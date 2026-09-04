"""
backend/ingestion/stac_search.py
================================
Phase 1.1: Multi-Temporal Scene Search Against STAC Catalog
===========================================================
PS Sections: 2.2.1 / 2.2.2 (Multi-Temporal Earth Observation Foundation)

Queries public STAC catalogs (AWS Sentinel-2 L2A COGs) to find MULTIPLE scenes
spread across time buckets for a given AOI bounding box and date interval.

Key Features:
  - Time-Window Bucketing: Splits date interval into sub-windows (e.g. T1 Historical vs T2 Recent).
  - Separate STAC query per time bucket with cloud cover filter (< 15%).
  - Extracts genuine scene metadata straight from API response (scene_id, acquisition_date, crs, assets).
  - Supplies 4 core bands (Red, Green, Blue, NIR B08) + extended bands (SWIR B11, SCL).
  - Includes offline fallback simulation for isolated environments.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

EARTH_SEARCH_STAC_URL = "https://earth-search.aws.element84.com/v1"
DEFAULT_COLLECTION = "sentinel-2-l2a"


@dataclass
class STACBandAssets:
    """
    Band asset URLs for visual and spectral analysis.
    Supports 4-band core (RGB + NIR) + extended bands (SWIR B11, SCL, Visual TCI).
    """
    red: str       # Band 04 (665 nm)
    green: str     # Band 03 (560 nm)
    blue: str      # Band 02 (490 nm)
    visual: Optional[str] = None # 10m True Color Image (TCI)
    nir: Optional[str] = None    # Band 08 (842 nm)
    swir: Optional[str] = None   # Band 11 (1610 nm)
    scl: Optional[str] = None    # Scene Classification Layer

    # Additional bands for advanced cloud detection
    b01: Optional[str] = None    # Coastal aerosol (443 nm)
    b05: Optional[str] = None    # Red Edge 1 (705 nm)
    b8a: Optional[str] = None    # Narrow NIR (865 nm)
    b09: Optional[str] = None    # Water vapour (945 nm)
    b10: Optional[str] = None    # Cirrus (1375 nm)
    b12: Optional[str] = None    # SWIR 2 (2190 nm)

    def to_band_dict(self) -> Dict[str, str]:
        """Returns mapping of standard band names to asset URLs."""
        mapping = {
            "B01": self.b01 or "",
            "B02": self.blue or "",
            "B03": self.green or "",
            "B04": self.red or "",
            "B05": self.b05 or "",
            "B08": self.nir or "",
            "B8A": self.b8a or "",
            "B09": self.b09 or "",
            "B10": self.b10 or "",
            "B11": self.swir or "",
            "B12": self.b12 or "",
            "SCL": self.scl or "",
            "visual": self.visual or "",
        }
        return {k: v for k, v in mapping.items() if v}


@dataclass
class STACSceneMetadata:
    """Metadata representing a selected satellite scene."""
    scene_id: str
    acquisition_date: str          # ISO-8601 string
    sensor: str                    # e.g., 'Sentinel-2A', 'Sentinel-2B'
    crs: str                       # e.g., 'EPSG:32643' or 'EPSG:4326'
    cloud_cover: float             # Whole-scene cloud cover % (0 - 100)
    source: str                    # e.g., 'sentinel-2-l2a'
    bbox: Tuple[float, float, float, float]
    assets: STACBandAssets
    time_bucket: Optional[str] = None  # e.g. 'bucket_1_historical', 'bucket_2_recent'
    properties: Dict[str, Any] = field(default_factory=dict)
    is_mock: bool = False


# ============================================================
# 1. Time-Window Sub-Bucket Generator
# ============================================================

def split_date_range_into_buckets(
    date_from: Union[str, date, datetime],
    date_to: Union[str, date, datetime],
    num_buckets: int = 2
) -> List[Tuple[str, str, str]]:
    """
    Splits a date interval into meaningful sub-window buckets.
    
    Returns:
        List of (bucket_label, start_date_str, end_date_str) tuples.
        e.g. [('bucket_1_historical', '2023-01-01', '2023-09-30'),
              ('bucket_2_recent', '2023-10-01', '2024-06-30')]
    """
    d_start = datetime.strptime(str(date_from)[:10], "%Y-%m-%d").date() if isinstance(date_from, str) else (date_from.date() if isinstance(date_from, datetime) else date_from)
    d_end = datetime.strptime(str(date_to)[:10], "%Y-%m-%d").date() if isinstance(date_to, str) else (date_to.date() if isinstance(date_to, datetime) else date_to)

    total_days = (d_end - d_start).days
    if total_days <= 0:
        return [("bucket_single", d_start.isoformat(), d_end.isoformat())]

    num_buckets = max(1, min(num_buckets, total_days))
    step_days = total_days / num_buckets

    buckets = []
    for i in range(num_buckets):
        b_start = d_start + timedelta(days=int(i * step_days))
        if i == num_buckets - 1:
            b_end = d_end
        else:
            b_end = d_start + timedelta(days=int((i + 1) * step_days) - 1)

        if num_buckets == 2:
            label = "historical_T1" if i == 0 else "recent_T2"
        else:
            label = f"time_bucket_{i + 1}"

        buckets.append((label, b_start.isoformat(), b_end.isoformat()))

    return buckets


# ============================================================
# 2. Single Bucket STAC Scene Search
# ============================================================

def _search_single_bucket(
    bbox: Tuple[float, float, float, float],
    datetime_range: str,
    max_cloud_cover: float = 15.0,
    stac_endpoint: str = EARTH_SEARCH_STAC_URL,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 15,
    time_bucket_label: Optional[str] = None
) -> List[STACSceneMetadata]:
    """Internal helper to execute a STAC query and return all covering COG scenes for a single time bucket."""
    try:
        from pystac_client import Client
    except ImportError as err:
        logger.warning(f"pystac_client not installed: {err}")
        return []

    try:
        client = Client.open(stac_endpoint, timeout=3.0)
    except Exception as c_err:
        logger.warning(f"Could not connect to STAC endpoint {stac_endpoint}: {c_err}")
        return []

    try:
        search = client.search(
            collections=[collection],
            bbox=list(bbox),
            datetime=datetime_range,
            query={"eo:cloud_cover": {"lt": max_cloud_cover}},
            limit=limit,
        )
        items = list(search.items())
    except Exception as s_err:
        logger.warning(f"STAC search query failed: {s_err}")
        return []
    if not items:
        # Retry with slightly relaxed cloud cover
        search_relaxed = client.search(
            collections=[collection],
            bbox=list(bbox),
            datetime=datetime_range,
            query={"eo:cloud_cover": {"lt": min(100.0, max_cloud_cover + 20.0)}},
            limit=limit,
        )
        items = list(search_relaxed.items())

    # Filter only items with valid public HTTPS COG URLs
    valid_items = [
        it for it in items
        if it.assets.get("red") and str(it.assets["red"].href).startswith(("http://", "https://"))
    ]

    if not valid_items:
        return []

    # Group by closest acquisition date / orbit pass
    valid_items.sort(key=lambda x: x.properties.get("eo:cloud_cover", 100.0))
    best_date = valid_items[0].datetime.date() if valid_items[0].datetime else None

    # Pick all overlapping granules from the best date / same 3-day orbit pass
    selected_items = [
        it for it in valid_items
        if it.datetime and abs((it.datetime.date() - best_date).days) <= 2
    ] if best_date else valid_items[:2]

    return [_parse_stac_item(it, collection, time_bucket_label, bbox) for it in selected_items]


def _parse_stac_item(
    item: Any,
    collection: str = DEFAULT_COLLECTION,
    time_bucket_label: Optional[str] = None,
    default_bbox: Optional[Tuple[float, float, float, float]] = None
) -> STACSceneMetadata:
    """Helper to parse a PySTAC item into STACSceneMetadata."""
    assets_dict = item.assets

    def get_href(key_options: List[str]) -> Optional[str]:
        for k in key_options:
            if k in assets_dict and assets_dict[k].href:
                return assets_dict[k].href
        return None

    band_assets = STACBandAssets(
        red=get_href(["red", "B04", "b04"]) or "",
        green=get_href(["green", "B03", "b03"]) or "",
        blue=get_href(["blue", "B02", "b02"]) or "",
        visual=get_href(["visual", "visual-tci", "TCI"]),
        nir=get_href(["nir", "B08", "b08", "nir08"]),
        swir=get_href(["swir16", "B11", "b11"]),
        scl=get_href(["scl", "SCL"]),
        b01=get_href(["coastal", "B01", "b01"]),
        b05=get_href(["rededge1", "B05", "b05"]),
        b8a=get_href(["nir09", "B8A", "b8a"]),
        b09=get_href(["watervapour", "B09", "b09"]),
        b10=get_href(["cirrus", "B10", "b10"]),
        b12=get_href(["swir22", "B12", "b12"]),
    )

    crs_str = (
        item.properties.get("proj:epsg")
        or item.properties.get("proj:crs")
        or "EPSG:4326"
    )
    if isinstance(crs_str, int):
        crs_str = f"EPSG:{crs_str}"

    return STACSceneMetadata(
        scene_id=item.id,
        acquisition_date=item.datetime.isoformat() if item.datetime else "",
        sensor=item.properties.get("platform", "Sentinel-2"),
        crs=crs_str,
        cloud_cover=float(item.properties.get("eo:cloud_cover", 0.0)),
        source=collection,
        bbox=tuple(item.bbox) if item.bbox else (default_bbox or (0.0, 0.0, 0.0, 0.0)),
        assets=band_assets,
        time_bucket=time_bucket_label,
        properties=item.properties,
        is_mock=False
    )




# ============================================================
# 3. Multi-Temporal Scene Search Orchestrator (Phase 1.1)
# ============================================================

def search_multi_temporal_stac_scenes(
    bbox: Tuple[float, float, float, float],
    date_from: Union[str, date, datetime] = "2023-01-01",
    date_to: Union[str, date, datetime] = "2024-06-30",
    max_cloud_cover: float = 15.0,
    num_buckets: int = 2,
    stac_endpoint: str = EARTH_SEARCH_STAC_URL,
    collection: str = DEFAULT_COLLECTION,
    offline_fallback: bool = True
) -> List[STACSceneMetadata]:
    """
    Phase 1.1 Multi-Temporal STAC Search:
    Splits date interval into sub-windows and retrieves the best low-cloud Sentinel-2 scene per bucket.

    Args:
        bbox: Bounding box tuple (min_lon, min_lat, max_lon, max_lat).
        date_from: Start date.
        date_to: End date.
        max_cloud_cover: Max whole-scene cloud cover %.
        num_buckets: Number of time-series buckets (default 2 for T1/T2 multi-temporal pairs).
        stac_endpoint: STAC API URL.
        collection: STAC collection.
        offline_fallback: If True, falls back to simulated offline scenes when network is down.

    Returns:
        List[STACSceneMetadata]: One selected scene per time bucket.
    """
    buckets = split_date_range_into_buckets(date_from, date_to, num_buckets=num_buckets)
    logger.info(f"[Phase 1.1] Executing multi-temporal search across {len(buckets)} time bucket(s): {buckets}")

    selected_scenes: List[STACSceneMetadata] = []

    for label, b_start, b_end in buckets:
        dt_range = f"{b_start}/{b_end}"
        logger.info(f"[Phase 1.1] Searching STAC bucket '{label}': {dt_range} (Cloud < {max_cloud_cover}%)")
        
        bucket_scenes: List[STACSceneMetadata] = []
        try:
            bucket_scenes = _search_single_bucket(
                bbox=bbox,
                datetime_range=dt_range,
                max_cloud_cover=max_cloud_cover,
                stac_endpoint=stac_endpoint,
                collection=collection,
                time_bucket_label=label
            )
        except Exception as e:
            logger.warning(f"[Phase 1.1] STAC query for bucket '{label}' failed: {e}")

        if bucket_scenes:
            for sc in bucket_scenes:
                logger.info(
                    f"[Phase 1.1] ✅ Found Scene for '{label}': {sc.scene_id} "
                    f"(Captured: {sc.acquisition_date[:10]}, Cloud: {sc.cloud_cover:.2f}%)"
                )
            selected_scenes.extend(bucket_scenes)
        elif offline_fallback:
            logger.info(f"[Phase 1.1] Using offline mock scene for bucket '{label}'.")
            mock_scene = _build_mock_scene(bbox, dt_range, label)
            selected_scenes.append(mock_scene)
        else:
            logger.warning(f"[Phase 1.1] No scene found for bucket '{label}' in range {dt_range}.")

    if not selected_scenes:
        raise RuntimeError(
            f"Phase 1.1 failed: Zero Sentinel-2 scenes found for bbox={bbox} between {date_from} and {date_to}."
        )

    return selected_scenes


def _build_mock_scene(
    bbox: Tuple[float, float, float, float],
    datetime_range: str,
    time_bucket: str
) -> STACSceneMetadata:
    """Builds a mock scene for offline testing."""
    dt_str = datetime_range.split("/")[0] + "T05:30:00Z"
    mock_id = f"S2_OFFLINE_{time_bucket.upper()}_{dt_str[:10].replace('-', '')}"
    
    return STACSceneMetadata(
        scene_id=mock_id,
        acquisition_date=dt_str,
        sensor="Sentinel-2B",
        crs="EPSG:4326",
        cloud_cover=2.5,
        source="offline_mock",
        bbox=bbox,
        assets=STACBandAssets(
            red="mock://B04.tif",
            green="mock://B03.tif",
            blue="mock://B02.tif",
            nir="mock://B08.tif",
            swir="mock://B11.tif",
            scl="mock://SCL.tif"
        ),
        time_bucket=time_bucket,
        is_mock=True
    )


# Backward compatibility alias
def search_stac_scene(
    bbox: Tuple[float, float, float, float],
    datetime_range: str = "2023-01-01/2023-12-31",
    max_cloud_cover: float = 30.0,
    stac_endpoint: str = EARTH_SEARCH_STAC_URL,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 20,
    offline_fallback: bool = True
) -> STACSceneMetadata:
    """Single scene search wrapper for backward compatibility."""
    parts = datetime_range.split("/")
    d_start = parts[0] if len(parts) > 0 else "2023-01-01"
    d_end = parts[1] if len(parts) > 1 else "2023-12-31"

    scenes = search_multi_temporal_stac_scenes(
        bbox=bbox,
        date_from=d_start,
        date_to=d_end,
        max_cloud_cover=max_cloud_cover,
        num_buckets=1,
        stac_endpoint=stac_endpoint,
        collection=collection,
        offline_fallback=offline_fallback
    )
    return scenes[0]
