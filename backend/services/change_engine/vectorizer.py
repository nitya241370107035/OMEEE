"""GeoJSON Vectorizer for AeroLens Change Engine.

Phase B5: Converts 2D labeled pixel change masks into valid GeoJSON features
in EPSG:4326 using rasterio.features.shapes and shapely.
"""
from typing import Dict, List, Any, Optional
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape, mapping, Polygon, MultiPolygon
from affine import Affine

from backend.services.change_engine.change_type_rules import (
    refine_road_morphology,
    TYPE_CONSTRUCTION,
    TYPE_ROAD_DEVELOPMENT,
)


def polygonize_change_mask(
    mask: np.ndarray,
    geotransform: Affine,
    change_type_map: Optional[np.ndarray] = None,
    before_class_map: Optional[np.ndarray] = None,
    after_class_map: Optional[np.ndarray] = None,
    min_area_px: int = 4,
    simplify_tolerance: float = 1e-6,
    indices_before: Optional[Dict[str, np.ndarray]] = None,
    indices_after: Optional[Dict[str, np.ndarray]] = None,
) -> List[Dict[str, Any]]:
    """Polygonizes a binary or integer labeled change mask into GeoJSON features.

    Args:
        mask: 2D uint8 or bool array (H, W) where >0 indicates change
        geotransform: Affine transform mapping pixel (col, row) to (lon, lat)
        change_type_map: Optional (H, W) array of change type strings
        before_class_map: Optional (H, W) array of before class strings
        after_class_map: Optional (H, W) array of after class strings
        min_area_px: Minimum pixel count required to keep a polygon
        simplify_tolerance: Shapely simplification tolerance in degrees

    Returns:
        List of GeoJSON Feature dicts with geometry and properties.
    """
    binary_mask = (mask > 0).astype(np.uint8)
    if not np.any(binary_mask):
        return []

    features = []
    # rasterio.features.shapes yields (geojson_dict, value)
    for geom_dict, val in shapes(binary_mask, mask=(binary_mask > 0), transform=geotransform):
        if val == 0:
            continue

        poly = shape(geom_dict)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue

        if simplify_tolerance > 0:
            poly = poly.simplify(simplify_tolerance, preserve_topology=True)

        # Approximate area in square meters (1 deg lat ~ 111,320m)
        bounds = poly.bounds
        centroid_lat = (bounds[1] + bounds[3]) / 2.0
        deg_to_m_lat = 111320.0
        deg_to_m_lon = 111320.0 * np.cos(np.radians(centroid_lat))
        area_m2 = round(float(poly.area * deg_to_m_lat * deg_to_m_lon), 1)

        # Estimate pixel count from bounding box / area
        pixel_width_m = abs(geotransform.a * deg_to_m_lon)
        pixel_height_m = abs(geotransform.e * deg_to_m_lat)
        px_area_m2 = max(pixel_width_m * pixel_height_m, 1.0)
        area_px = max(int(round(area_m2 / px_area_m2)), 1)

        if area_px < min_area_px:
            continue

        # Extract dominant change type and classes if maps are supplied
        change_type = TYPE_CONSTRUCTION
        before_class = "Unknown"
        after_class = "Unknown"

        if change_type_map is not None:
            # Sample point or representative pixel
            c_col = int(np.clip((poly.centroid.x - geotransform.c) / geotransform.a, 0, mask.shape[1] - 1))
            c_row = int(np.clip((poly.centroid.y - geotransform.f) / geotransform.e, 0, mask.shape[0] - 1))
            change_type = str(change_type_map[c_row, c_col])
            if before_class_map is not None:
                before_class = str(before_class_map[c_row, c_col])
            if after_class_map is not None:
                after_class = str(after_class_map[c_row, c_col])

        # Morphology check for Road Development if construction-like
        if change_type in (TYPE_CONSTRUCTION, "Built-up / Urban"):
            # Crop local component for morphology test
            min_c = int(np.clip((bounds[0] - geotransform.c) / geotransform.a, 0, mask.shape[1] - 1))
            max_c = int(np.clip((bounds[2] - geotransform.c) / geotransform.a, 0, mask.shape[1] - 1))
            min_r = int(np.clip((bounds[3] - geotransform.f) / geotransform.e, 0, mask.shape[0] - 1))
            max_r = int(np.clip((bounds[1] - geotransform.f) / geotransform.e, 0, mask.shape[0] - 1))
            r_start, r_end = min(min_r, max_r), max(min_r, max_r) + 1
            c_start, c_end = min(min_c, max_c), max(min_c, max_c) + 1

            sub_mask = binary_mask[r_start:r_end, c_start:c_end]
            change_type = refine_road_morphology(sub_mask, current_type=change_type)

        # Extract spectral indices for this region if maps are provided
        spectral_profile = None
        if indices_before is not None and indices_after is not None:
            c_col = int(np.clip((poly.centroid.x - geotransform.c) / geotransform.a, 0, mask.shape[1] - 1))
            c_row = int(np.clip((poly.centroid.y - geotransform.f) / geotransform.e, 0, mask.shape[0] - 1))
            b_ndvi = round(float(indices_before.get("ndvi", np.zeros((1, 1)))[c_row, c_col]), 3)
            b_ndbi = round(float(indices_before.get("ndbi", np.zeros((1, 1)))[c_row, c_col]), 3)
            b_ndwi = round(float(indices_before.get("ndwi", np.zeros((1, 1)))[c_row, c_col]), 3)
            a_ndvi = round(float(indices_after.get("ndvi", np.zeros((1, 1)))[c_row, c_col]), 3)
            a_ndbi = round(float(indices_after.get("ndbi", np.zeros((1, 1)))[c_row, c_col]), 3)
            a_ndwi = round(float(indices_after.get("ndwi", np.zeros((1, 1)))[c_row, c_col]), 3)
            spectral_profile = {
                "before": {"ndvi": b_ndvi, "ndbi": b_ndbi, "ndwi": b_ndwi},
                "after": {"ndvi": a_ndvi, "ndbi": a_ndbi, "ndwi": a_ndwi},
                "delta": {
                    "ndvi": round(a_ndvi - b_ndvi, 3),
                    "ndbi": round(a_ndbi - b_ndbi, 3),
                    "ndwi": round(a_ndwi - b_ndwi, 3),
                }
            }

        feature = {
            "type": "Feature",
            "geometry": mapping(poly),
            "properties": {
                "change_type": change_type,
                "before_class": before_class,
                "after_class": after_class,
                "area_px": area_px,
                "area_m2": area_m2,
                "area_sq_m": area_m2,
                "spectral_profile": spectral_profile,
            }
        }
        features.append(feature)

    return features
