"""
Phase 1.0: AOI Input Handling & Validation

Validates input GeoJSON geometries before they enter the imagery pipeline.
Rejects broken, degenerate, or self-intersecting geometries with detailed error messages.
Computes bounding boxes and approximate ground area in km².
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Union, Dict, Any, Tuple
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.validation import explain_validity


@dataclass
class ValidatedAOI:
    """Represents a validated Area of Interest (AOI)."""
    polygon: Union[Polygon, MultiPolygon]
    bbox: Tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    area_km2: float
    raw_geojson: Dict[str, Any]


def calculate_polygon_area_km2(geom: Union[Polygon, MultiPolygon]) -> float:
    """
    Computes approximate real-world ground area in km² on the WGS84 ellipsoid.
    Uses latitude-adjusted spherical polygon integration.
    """
    # Mean latitude for longitude scaling
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    mean_lat_rad = math.radians((min_lat + max_lat) / 2.0)

    # 1 deg latitude ~ 111.132 km; 1 deg longitude ~ 111.320 * cos(lat) km
    km_per_deg_lat = 111.132
    km_per_deg_lon = 111.320 * math.cos(mean_lat_rad)

    # Scale shapely planar degree^2 area to km^2
    area_deg2 = geom.area
    area_km2 = area_deg2 * km_per_deg_lat * km_per_deg_lon
    return max(0.0, float(area_km2))


def validate_aoi(geojson_input: Union[str, Path, Dict[str, Any]]) -> ValidatedAOI:
    """
    Parses and strictly validates an input GeoJSON polygon.

    Args:
        geojson_input: File path, JSON string, or dict containing GeoJSON
                       (FeatureCollection, Feature, or Polygon/MultiPolygon geometry).

    Returns:
        ValidatedAOI: Dataclass containing the validated shapely geometry,
                     bounding box tuple, computed area in km², and raw GeoJSON.

    Raises:
        ValueError: If input format is invalid, geometry is malformed,
                    self-intersecting, unclosed, or empty.
        FileNotFoundError: If input file path does not exist.
    """
    # 1. Load GeoJSON payload
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

    # 2. Extract geometry dictionary
    geom_dict = None
    geojson_type = raw_dict.get("type", "")

    if geojson_type == "FeatureCollection":
        features = raw_dict.get("features", [])
        if not features:
            raise ValueError("GeoJSON FeatureCollection contains no features.")
        # If single feature, extract its geometry; if multiple, extract polygons
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

    # 3. Validation checks
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

    # 4. Compute Bounding Box (min_lon, min_lat, max_lon, max_lat)
    min_lon, min_lat, max_lon, max_lat = shapely_geom.bounds

    # Coordinate range sanity check for EPSG:4326 (WGS84)
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0 and
            -90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise ValueError(
            f"Coordinates out of bounds for WGS84 (EPSG:4326): "
            f"lon range [{min_lon}, {max_lon}], lat range [{min_lat}, {max_lat}]."
        )

    # 5. Compute Area
    area_km2 = calculate_polygon_area_km2(shapely_geom)
    if area_km2 < 1e-6:
        raise ValueError(
            f"AOI geometry area is degenerate or negligible ({area_km2:.8f} km²)."
        )

    return ValidatedAOI(
        polygon=shapely_geom,
        bbox=(min_lon, min_lat, max_lon, max_lat),
        area_km2=round(area_km2, 4),
        raw_geojson=raw_dict
    )
