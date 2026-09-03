"""
Phase 1.1: Scene Search Against Imagery Catalog (STAC)

Queries public STAC catalogs (AWS Sentinel-2 L2A COGs) to find valid scenes covering
the specified AOI bounding box and date range with acceptable whole-scene cloud cover.
Extracts genuine scene metadata and band asset URLs. Includes offline/mock fallback.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

EARTH_SEARCH_STAC_URL = "https://earth-search.aws.element84.com/v1"
DEFAULT_COLLECTION = "sentinel-2-l2a"


@dataclass
class STACBandAssets:
    """
    Band asset URLs for visual and spectral analysis.
    Supports all 10 Sentinel-2 bands required by s2cloudless plus green (B03) and SCL.
    """
    # Core RGB
    red: str       # Band 04 (665 nm)
    green: str     # Band 03 (560 nm)
    blue: str      # Band 02 (490 nm)
    nir: Optional[str] = None    # Band 08 (842 nm)
    
    # Extended Sentinel-2 bands for s2cloudless
    b01: Optional[str] = None    # Coastal aerosol (443 nm)
    b05: Optional[str] = None    # Red Edge 1 (705 nm)
    b8a: Optional[str] = None    # Narrow NIR (865 nm)
    b09: Optional[str] = None    # Water vapour (945 nm)
    b10: Optional[str] = None    # Cirrus (1375 nm)
    b11: Optional[str] = None    # SWIR 1 (1610 nm)
    b12: Optional[str] = None    # SWIR 2 (2190 nm)
    scl: Optional[str] = None    # Scene Classification Layer

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
            "B11": self.b11 or "",
            "B12": self.b12 or "",
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
    source: str                    # e.g., 'aws_earth_search' or 'sentinel2_l2a'
    bbox: Tuple[float, float, float, float]
    assets: STACBandAssets
    properties: Dict[str, Any] = field(default_factory=dict)
    is_mock: bool = False


def search_stac_scene(
    bbox: Tuple[float, float, float, float],
    datetime_range: str = "2023-01-01/2023-12-31",
    max_cloud_cover: float = 30.0,
    stac_endpoint: str = EARTH_SEARCH_STAC_URL,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 20,
    offline_fallback: bool = True
) -> STACSceneMetadata:
    """
    Searches the STAC catalog for the best Sentinel-2 scene covering the AOI.

    Args:
        bbox: Bounding box tuple (min_lon, min_lat, max_lon, max_lat).
        datetime_range: ISO 8601 interval string 'YYYY-MM-DD/YYYY-MM-DD'.
        max_cloud_cover: Maximum acceptable scene cloud cover percentage (0 - 100).
        stac_endpoint: URL of the STAC catalog API.
        collection: STAC collection name.
        limit: Maximum candidate items to fetch.
        offline_fallback: If True, falls back to a mock scene when network is unreachable.

    Returns:
        STACSceneMetadata: Selected scene with lowest cloud cover and band asset URLs.

    Raises:
        RuntimeError: If search fails and offline_fallback is False, or no items match.
    """
    try:
        from pystac_client import Client
        logger.info(f"Connecting to STAC endpoint: {stac_endpoint}")
        client = Client.open(stac_endpoint)
        
        # Search STAC items
        search = client.search(
            collections=[collection],
            bbox=bbox,
            datetime=datetime_range,
            query={"eo:cloud_cover": {"lte": max_cloud_cover}},
            limit=limit
        )
        
        items = list(search.items())
        logger.info(f"STAC search returned {len(items)} matching items.")

        if items:
            from shapely.geometry import box, shape
            aoi_box = box(*bbox)
            aoi_area = aoi_box.area

            def score_item(item):
                try:
                    geom = shape(item.geometry) if item.geometry else box(*item.bbox)
                    inter_area = geom.intersection(aoi_box).area
                    overlap_ratio = inter_area / aoi_area if aoi_area > 0 else 1.0
                except Exception:
                    overlap_ratio = 1.0
                
                cloud = float(item.properties.get("eo:cloud_cover", 100.0))
                # Maximize overlap (primary: higher is better), minimize cloud (secondary: lower is better)
                # Bin overlap into tiers: >= 95% is tier 1 (value 1.0), else fractional tier
                overlap_tier = 1.0 if overlap_ratio >= 0.95 else overlap_ratio
                return (-overlap_tier, cloud)

            best_item = min(items, key=score_item)
            return _parse_stac_item(best_item, bbox, source="aws_earth_search")

        logger.warning(f"No STAC scenes found matching criteria: bbox={bbox}, date={datetime_range}, cloud<={max_cloud_cover}")
        
    except Exception as exc:
        logger.warning(f"STAC query failed: {exc}")
        if not offline_fallback:
            raise RuntimeError(f"STAC search failed: {exc}") from exc

    if offline_fallback:
        logger.info("Falling back to structured synthetic/offline STAC scene metadata.")
        return _create_offline_mock_scene(bbox, datetime_range)
    
    raise RuntimeError(f"No satellite scene found for bbox {bbox} in date range {datetime_range}")


def _get_asset_href(assets: Dict[str, Any], *keys: str) -> str:
    """Helper to safely fetch asset URL matching any candidate key."""
    for k in keys:
        if k in assets:
            asset = assets[k]
            if isinstance(asset, dict):
                return asset.get("href", "")
            if hasattr(asset, "href"):
                return asset.href
    return ""


def _parse_stac_item(item: Any, search_bbox: Tuple[float, float, float, float], source: str) -> STACSceneMetadata:
    """Extracts standardized metadata and band asset URLs from a PySTAC item."""
    item_dict = item.to_dict()
    props = item_dict.get("properties", {})
    assets_dict = item_dict.get("assets", {})

    # Extract all 10 Sentinel-2 bands + B03 + SCL
    b01_url = _get_asset_href(assets_dict, "B01", "b01", "coastal")
    b02_url = _get_asset_href(assets_dict, "B02", "b02", "blue")
    b03_url = _get_asset_href(assets_dict, "B03", "b03", "green")
    b04_url = _get_asset_href(assets_dict, "B04", "b04", "red", "visual")
    b05_url = _get_asset_href(assets_dict, "B05", "b05", "rededge1")
    b08_url = _get_asset_href(assets_dict, "B08", "b08", "nir", "nir08")
    b8a_url = _get_asset_href(assets_dict, "B8A", "b8a", "nir09", "rededge4")
    b09_url = _get_asset_href(assets_dict, "B09", "b09", "wvp", "water_vapour")
    b10_url = _get_asset_href(assets_dict, "B10", "b10", "cirrus")
    b11_url = _get_asset_href(assets_dict, "B11", "b11", "swir16", "swir1")
    b12_url = _get_asset_href(assets_dict, "B12", "b12", "swir22", "swir2")
    scl_url = _get_asset_href(assets_dict, "scl", "SCL", "scl_20m")

    scene_id = item_dict.get("id", "S2_UNKNOWN_SCENE")
    acq_date = props.get("datetime") or props.get("acquisition_date") or datetime.utcnow().isoformat()
    raw_sensor = props.get("platform") or props.get("instruments", ["Sentinel-2"])[0]
    
    # Standardize sensor name formatting (e.g. 'sentinel-2a' -> 'Sentinel-2A')
    if isinstance(raw_sensor, str):
        if "2a" in raw_sensor.lower():
            sensor = "Sentinel-2A"
        elif "2b" in raw_sensor.lower():
            sensor = "Sentinel-2B"
        else:
            sensor = raw_sensor.capitalize()
    else:
        sensor = "Sentinel-2"

    cloud_cover = float(props.get("eo:cloud_cover", 0.0))
    crs = props.get("proj:epsg")
    crs_str = f"EPSG:{crs}" if crs else "EPSG:4326"
    item_bbox = tuple(item_dict.get("bbox", search_bbox))

    return STACSceneMetadata(
        scene_id=scene_id,
        acquisition_date=acq_date,
        sensor=sensor,
        crs=crs_str,
        cloud_cover=round(cloud_cover, 2),
        source=source,
        bbox=item_bbox,
        assets=STACBandAssets(
            red=b04_url,
            green=b03_url,
            blue=b02_url,
            nir=b08_url,
            b01=b01_url,
            b05=b05_url,
            b8a=b8a_url,
            b09=b09_url,
            b10=b10_url,
            b11=b11_url,
            b12=b12_url,
            scl=scl_url
        ),
        properties=props,
        is_mock=False
    )


def _create_offline_mock_scene(
    bbox: Tuple[float, float, float, float],
    datetime_range: str
) -> STACSceneMetadata:
    """Generates deterministic mock scene metadata for offline testing."""
    start_date = datetime_range.split("/")[0] if "/" in datetime_range else "2023-06-15"
    scene_id = f"S2A_MSIL2A_{start_date.replace('-', '')}_OFFLINE_TEST"
    
    return STACSceneMetadata(
        scene_id=scene_id,
        acquisition_date=f"{start_date}T10:30:00Z",
        sensor="Sentinel-2A",
        crs="EPSG:4326",
        cloud_cover=12.5,
        source="offline_mock_generator",
        bbox=bbox,
        assets=STACBandAssets(
            red="mock://band_04_red.tif",
            green="mock://band_03_green.tif",
            blue="mock://band_02_blue.tif",
            nir="mock://band_08_nir.tif",
            b01="mock://band_01_coastal.tif",
            b05="mock://band_05_rededge.tif",
            b8a="mock://band_8a_narrownir.tif",
            b09="mock://band_09_watervapour.tif",
            b10="mock://band_10_cirrus.tif",
            b11="mock://band_11_swir1.tif",
            b12="mock://band_12_swir2.tif",
            scl="mock://band_scl.tif"
        ),
        properties={
            "platform": "Sentinel-2A",
            "constellation": "sentinel-2",
            "eo:cloud_cover": 12.5,
            "offline_mode": True
        },
        is_mock=True
    )
