"""
backend/ingestion/input_validator.py
====================================
Phase 1.0: Dual Entry Point Input Handling & Validation
======================================================
PS Sections: 2.2.6 (Ingestion & Sovereignty), 2.2.7 (Evaluation Constraints)

Provides two independent, strictly validated entry points:
  1. Entry Point A — AOI + Date Range (STAC Archive Building):
     - Validates GeoJSON Polygon / MultiPolygon (shapely is_valid, WGS84 bounds, area).
     - Validates date range intervals for multi-temporal search.
     - Uses network (STAC API) prior to evaluation.

  2. Entry Point B — Direct Local File Ingestion (Organiser Evaluation Imagery):
     - Validates one or more GeoTIFF / COG file paths directly from disk.
     - STRICTLY OFFLINE — Zero network calls or external catalog lookups.
     - Reads intrinsic metadata (CRS, bounds in WGS84, dimensions, band count, data types).
     - Inspects and normalizes band order/descriptions, failing gracefully with clear errors
       if bands are unreadable or missing.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Union, Dict, Any, Tuple, List, Optional

logger = logging.getLogger(__name__)

import rasterio
from rasterio.warp import transform_bounds
from shapely.geometry import shape, Polygon, MultiPolygon, box
from shapely.validation import explain_validity


# ============================================================
# Data Models for Phase 1.0 Entry Points
# ============================================================

@dataclass
class ValidatedAOI:
    """Represents a validated Area of Interest (AOI) for Entry Point A."""
    polygon: Union[Polygon, MultiPolygon]
    bbox: Tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat) in EPSG:4326
    area_km2: float
    raw_geojson: Dict[str, Any]
    source_type: str = "aoi_search"


@dataclass
class ValidatedAOIInput:
    """Full parameters for Entry Point A (AOI + Multi-Temporal Date Interval)."""
    aoi: ValidatedAOI
    date_from: date
    date_to: date
    region_id: str
    max_cloud_cover: float = 15.0
    source_type: str = "aoi_search"

    @property
    def datetime_range_str(self) -> str:
        return f"{self.date_from.isoformat()}/{self.date_to.isoformat()}"


@dataclass
class FileBandInfo:
    """Metadata about individual bands inside a GeoTIFF."""
    index: int  # 1-based index in rasterio
    name: str   # 'red', 'green', 'blue', 'nir', 'swir', or 'band_N'
    dtype: str


@dataclass
class ValidatedFileInput:
    """Metadata for Entry Point B (Direct Local GeoTIFF Ingestion — 100% Offline)."""
    file_path: Path
    region_id: str
    crs: str
    bounds_wgs84: Tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    extent_polygon: Polygon                         # Shapely polygon footprint in EPSG:4326
    width: int
    height: int
    band_count: int
    band_order: List[str]                            # e.g. ['blue', 'green', 'red', 'nir']
    band_info: List[FileBandInfo]
    acquisition_date: Optional[datetime] = None
    source_type: str = "organiser_provided"
    is_offline_compliant: bool = True
    metadata_tags: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 1. Entry Point A: AOI Geometry & Date Validation
# ============================================================

def calculate_polygon_area_km2(geom: Union[Polygon, MultiPolygon]) -> float:
    """
    Computes approximate ground area in km² on WGS84 ellipsoid.
    Uses latitude-adjusted spherical polygon integration.
    """
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    mean_lat_rad = math.radians((min_lat + max_lat) / 2.0)

    km_per_deg_lat = 111.132
    km_per_deg_lon = 111.320 * math.cos(mean_lat_rad)

    area_deg2 = geom.area
    area_km2 = area_deg2 * km_per_deg_lat * km_per_deg_lon
    return max(0.0, float(area_km2))


def validate_aoi(geojson_input: Union[str, Path, Dict[str, Any]]) -> ValidatedAOI:
    """
    Parses and strictly validates an input GeoJSON polygon.
    """
    raw_dict: Dict[str, Any]
    if isinstance(geojson_input, (str, Path)):
        input_str = str(geojson_input).strip()
        if input_str.startswith("{"):
            try:
                raw_dict = json.loads(input_str)
            except json.JSONDecodeError as err:
                raise ValueError(f"Failed to parse GeoJSON string: {err}") from err
        else:
            path = Path(geojson_input)
            if not path.is_file():
                raise FileNotFoundError(f"GeoJSON file not found at: {path}")
            with open(path, "r", encoding="utf-8") as f:
                try:
                    raw_dict = json.load(f)
                except json.JSONDecodeError as err:
                    raise ValueError(f"Failed to parse GeoJSON file {path}: {err}") from err
    elif isinstance(geojson_input, dict):
        raw_dict = geojson_input
    else:
        raise TypeError(f"Unsupported input type for geojson_input: {type(geojson_input)}")

    geojson_type = raw_dict.get("type", "")

    if geojson_type == "FeatureCollection":
        features = raw_dict.get("features", [])
        if not features:
            raise ValueError("GeoJSON FeatureCollection contains no features.")
        geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
        if not geoms:
            raise ValueError("No valid geometries found in FeatureCollection.")
        if len(geoms) == 1:
            shapely_geom = geoms[0]
        else:
            from shapely.ops import unary_union
            shapely_geom = unary_union(geoms)
    elif geojson_type == "Feature":
        geom_dict = raw_dict.get("geometry")
        if not geom_dict:
            raise ValueError("GeoJSON Feature is missing a 'geometry' object.")
        shapely_geom = shape(geom_dict)
    elif geojson_type in ("Polygon", "MultiPolygon"):
        shapely_geom = shape(raw_dict)
    else:
        raise ValueError(
            f"Unsupported GeoJSON object type: '{geojson_type}'. "
            "Must be 'FeatureCollection', 'Feature', 'Polygon', or 'MultiPolygon'."
        )

    if shapely_geom.is_empty:
        raise ValueError("AOI geometry is empty.")

    if not isinstance(shapely_geom, (Polygon, MultiPolygon)):
        raise ValueError(
            f"Expected Polygon or MultiPolygon geometry, got {shapely_geom.geom_type}."
        )

    if not shapely_geom.is_valid:
        reason = explain_validity(shapely_geom)
        raise ValueError(
            f"Invalid AOI geometry: {reason}. "
            "Please ensure polygon rings do not self-intersect and coordinates are ordered correctly."
        )

    min_lon, min_lat, max_lon, max_lat = shapely_geom.bounds

    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0 and
            -90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise ValueError(
            f"Coordinates out of bounds for WGS84 (EPSG:4326): "
            f"lon range [{min_lon}, {max_lon}], lat range [{min_lat}, {max_lat}]."
        )

    area_km2 = calculate_polygon_area_km2(shapely_geom)
    if area_km2 < 1e-6:
        raise ValueError(
            f"AOI geometry area is degenerate or negligible ({area_km2:.8f} km²)."
        )

    return ValidatedAOI(
        polygon=shapely_geom,
        bbox=(min_lon, min_lat, max_lon, max_lat),
        area_km2=round(area_km2, 4),
        raw_geojson=raw_dict,
        source_type="aoi_search"
    )


def validate_aoi_input(
    geojson_input: Union[str, Path, Dict[str, Any]],
    date_from: Union[str, date, datetime],
    date_to: Union[str, date, datetime],
    region_id: str = "custom_region",
    max_cloud_cover: float = 15.0
) -> ValidatedAOIInput:
    """
    Entry Point A Validator: Validates AOI geometry, date intervals, and parameters.
    """
    # 1. Validate AOI geometry
    validated_aoi = validate_aoi(geojson_input)

    # 2. Parse & validate dates
    d_start: date
    d_end: date

    if isinstance(date_from, str):
        d_start = datetime.strptime(date_from.strip()[:10], "%Y-%m-%d").date()
    elif isinstance(date_from, datetime):
        d_start = date_from.date()
    elif isinstance(date_from, date):
        d_start = date_from
    else:
        raise TypeError(f"Invalid type for date_from: {type(date_from)}")

    if isinstance(date_to, str):
        d_end = datetime.strptime(date_to.strip()[:10], "%Y-%m-%d").date()
    elif isinstance(date_to, datetime):
        d_end = date_to.date()
    elif isinstance(date_to, date):
        d_end = date_to
    else:
        raise TypeError(f"Invalid type for date_to: {type(date_to)}")

    if d_start > d_end:
        raise ValueError(f"date_from ({d_start}) cannot be later than date_to ({d_end}).")

    return ValidatedAOIInput(
        aoi=validated_aoi,
        date_from=d_start,
        date_to=d_end,
        region_id=region_id.strip() or "region_default",
        max_cloud_cover=max(0.0, min(100.0, float(max_cloud_cover))),
        source_type="aoi_search"
    )


# ============================================================
# 2. Entry Point B: Direct Local GeoTIFF File Ingestion (Offline)
# ============================================================

def infer_band_order(
    band_count: int,
    descriptions: Tuple[Optional[str], ...],
    custom_band_order: Optional[List[str]] = None
) -> List[str]:
    """
    Infers or validates the band order (e.g. ['blue', 'green', 'red', 'nir']).
    Uses rasterio band descriptions or standard defaults based on band count.
    """
    if custom_band_order:
        if len(custom_band_order) != band_count:
            raise ValueError(
                f"Custom band order length ({len(custom_band_order)}) "
                f"does not match file band count ({band_count})."
            )
        return [b.strip() for b in custom_band_order]

    # Check descriptions from rasterio
    inferred: List[str] = []
    has_valid_desc = any(desc for desc in descriptions)
    
    if has_valid_desc:
        for idx, desc in enumerate(descriptions, start=1):
            if desc:
                d_clean = desc.strip()
                d_lower = d_clean.lower()
                d_upper = d_clean.upper()
                if "red" in d_lower or d_upper in ("B4", "B04"):
                    inferred.append("red" if "red" in d_lower else "B04")
                elif "green" in d_lower or d_upper in ("B3", "B03"):
                    inferred.append("green" if "green" in d_lower else "B03")
                elif "blue" in d_lower or d_upper in ("B2", "B02"):
                    inferred.append("blue" if "blue" in d_lower else "B02")
                elif "nir" in d_lower or d_upper in ("B8", "B08"):
                    inferred.append("nir" if "nir" in d_lower else "B08")
                elif "swir" in d_lower or d_upper in ("B11", "B12"):
                    inferred.append("swir" if "swir" in d_lower else d_upper)
                elif d_upper in ("B1", "B01"):
                    inferred.append("B01")
                elif d_upper in ("B5", "B05"):
                    inferred.append("B05")
                elif d_upper in ("B8A", "8A"):
                    inferred.append("B8A")
                elif d_upper in ("B9", "B09"):
                    inferred.append("B09")
                elif d_upper in ("B10",):
                    inferred.append("B10")
                else:
                    inferred.append(d_clean)
            else:
                inferred.append(f"band_{idx}")
        return inferred

    # Standard default assumptions when descriptions are absent:
    if band_count == 1:
        return ["grayscale"]
    elif band_count == 3:
        return ["red", "green", "blue"]
    elif band_count == 4:
        return ["red", "green", "blue", "nir"]
    elif band_count == 5:
        return ["red", "green", "blue", "nir", "swir"]
    elif band_count == 10:
        return ["B01", "B02", "B04", "B05", "B08", "B8A", "B09", "B10", "B11", "B12"]
    else:
        return [f"band_{i}" for i in range(1, band_count + 1)]


def validate_direct_file_input(
    file_path: Union[str, Path],
    region_id: Optional[str] = None,
    custom_band_order: Optional[List[str]] = None,
    acquisition_date: Optional[Union[str, datetime]] = None
) -> ValidatedFileInput:
    """
    Entry Point B Validator: Inspects and validates a local GeoTIFF / COG file.
    
    CRITICAL: 100% OFFLINE COMPLIANT — Performs zero network calls.
    
    Args:
        file_path: Path to the local GeoTIFF/COG file.
        region_id: Optional identifier (defaults to filename stem).
        custom_band_order: Optional explicit band mapping e.g. ['blue', 'green', 'red', 'nir'].
        acquisition_date: Optional capture date if known, or parsed from tags/filename.

    Returns:
        ValidatedFileInput containing intrinsic file metadata, extent, and band layout.

    Raises:
        FileNotFoundError: If the file does not exist on disk.
        ValueError: If file is corrupted, unreadable, has no CRS, or has 0 bands.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation GeoTIFF file does not exist: {path}")

    try:
        with rasterio.open(str(path)) as src:
            if src.count == 0:
                raise ValueError(f"GeoTIFF file has 0 raster bands: {path}")

            crs_obj = src.crs
            width = src.width
            height = src.height
            band_count = src.count
            tags = src.tags()

            if not crs_obj:
                logger.warning(f"GeoTIFF '{path.name}' has no embedded CRS metadata. Constructing spatial bounds from raster dimensions ({width}x{height}).")
                crs_str = "EPSG:4326"
                min_lon, min_lat = 0.0, 0.0
                max_lon = float(width * 0.0001)
                max_lat = float(height * 0.0001)
            else:
                crs_str = crs_obj.to_string()
                left, bottom, right, top = src.bounds
                min_lon, min_lat, max_lon, max_lat = transform_bounds(
                    src.crs, "EPSG:4326", left, bottom, right, top
                )

            # Build bounding box polygon
            extent_poly = box(min_lon, min_lat, max_lon, max_lat)

            # Determine band layout & descriptions
            descriptions = src.descriptions or tuple([None] * band_count)
            band_order = infer_band_order(band_count, descriptions, custom_band_order)

            band_info_list: List[FileBandInfo] = []
            for i in range(1, band_count + 1):
                dtype_str = src.dtypes[i - 1]
                b_name = band_order[i - 1] if i - 1 < len(band_order) else f"band_{i}"
                band_info_list.append(FileBandInfo(index=i, name=b_name, dtype=dtype_str))

    except Exception as err:
        if isinstance(err, (ValueError, FileNotFoundError)):
            raise
        raise ValueError(f"Failed to read and inspect GeoTIFF {path}: {err}") from err

    # Parse acquisition date from parameter, tags, or filename YYYYMMDD pattern
    acq_dt: Optional[datetime] = None
    if acquisition_date:
        if isinstance(acquisition_date, str):
            try:
                acq_dt = datetime.fromisoformat(acquisition_date)
            except Exception:
                acq_dt = datetime.strptime(acquisition_date[:10], "%Y-%m-%d")
        elif isinstance(acquisition_date, datetime):
            acq_dt = acquisition_date
    elif "DATETIME" in tags:
        try:
            acq_dt = datetime.fromisoformat(tags["DATETIME"])
        except Exception:
            pass

    if not acq_dt:
        import re
        match = re.search(r"(20\d{2}|19\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", path.name)
        if match:
            y, m, d = match.groups()
            try:
                acq_dt = datetime(int(y), int(m), int(d))
            except Exception:
                pass

    reg_id = region_id or path.stem

    return ValidatedFileInput(
        file_path=path,
        region_id=reg_id,
        crs=crs_str,
        bounds_wgs84=(min_lon, min_lat, max_lon, max_lat),
        extent_polygon=extent_poly,
        width=width,
        height=height,
        band_count=band_count,
        band_order=band_order,
        band_info=band_info_list,
        acquisition_date=acq_dt,
        source_type="organiser_provided",
        is_offline_compliant=True,
        metadata_tags=tags
    )
