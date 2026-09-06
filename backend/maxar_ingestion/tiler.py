"""
backend/maxar_ingestion/tiler.py
================================
Tiling and Feature Extraction Engine for High-Resolution Maxar RGB Imagery.
Generates 512x512 georeferenced crops with exact EPSG:4326 affine transforms,
computes per-band RGB statistics, and calculates the Visible Atmospherically Resistant Index (VARI).
"""

import math
import hashlib
import logging
from typing import List, Tuple, Dict, Any, Optional
import numpy as np
from PIL import Image
from rasterio.transform import from_bounds as tf_from_bounds
from shapely.geometry import box, Polygon

from backend.ingestion.tiler import TileCandidate

logger = logging.getLogger("MaxarTiler")

TILE_SIZE = 512


def calculate_vari_index(rgb_array: np.ndarray) -> float:
    """
    Computes mean Visible Atmospherically Resistant Index (VARI):
    VARI = (Green - Red) / (Green + Red - Blue + epsilon)
    Range: [-1.0, 1.0]. Highlights vegetative contrast from True Color RGB.
    rgb_array: shape (3, H, W), float or uint8
    """
    try:
        r = rgb_array[0].astype(np.float32)
        g = rgb_array[1].astype(np.float32)
        b = rgb_array[2].astype(np.float32)

        denominator = g + r - b
        denominator = np.where(denominator == 0, 1e-6, denominator)

        vari = (g - r) / denominator
        vari = np.clip(vari, -1.0, 1.0)
        mean_vari = float(np.nanmean(vari))
        return round(mean_vari, 4)
    except Exception:
        return 0.0


def _get_grid_steps(total_dim: int, window_size: int, stride: int) -> List[int]:
    """
    Computes sliding window start offsets ensuring full boundary coverage.
    If the final step does not reach total_dim - window_size, appends that boundary offset.
    """
    if total_dim <= window_size:
        return [0]
    stride = max(1, int(stride))
    offsets = list(range(0, total_dim - window_size + 1, stride))
    if offsets[-1] != total_dim - window_size:
        offsets.append(total_dim - window_size)
    return offsets


def slice_maxar_canvas_into_tiles(
    stitched_img: Image.Image,
    bbox: Tuple[float, float, float, float],
    scene_id: str,
    region_id: str = "custom_region",
    aoi_polygon: Optional[Any] = None,
    overlap_pct: float = 0.10,
    tile_size: int = TILE_SIZE
) -> List[TileCandidate]:
    """
    Slices the stitched Maxar PIL image into 512x512 TileCandidate objects with real
    geographic coordinates in EPSG:4326.
    Ensures 100% full coverage of the drawn polygon, including boundary edges,
    and filters against the input AOI polygon if provided.
    """
    img_w, img_h = stitched_img.size
    min_lon, min_lat, max_lon, max_lat = bbox

    # Geographic degrees per pixel
    lon_res = (max_lon - min_lon) / float(img_w)
    lat_res = (max_lat - min_lat) / float(img_h)

    tiles: List[TileCandidate] = []
    
    # If the stitched image is smaller than tile_size in either dimension, resize/pad to at least tile_size
    if img_w < tile_size or img_h < tile_size:
        target_w = max(tile_size, img_w)
        target_h = max(tile_size, img_h)
        resized = stitched_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        stitched_img = resized
        img_w, img_h = target_w, target_h
        lon_res = (max_lon - min_lon) / float(img_w)
        lat_res = (max_lat - min_lat) / float(img_h)

    # Compute deterministic sliding-window steps with boundary remainder coverage
    stride = max(1, int(round(tile_size * (1.0 - overlap_pct))))
    x_steps = _get_grid_steps(img_w, tile_size, stride)
    y_steps = _get_grid_steps(img_h, tile_size, stride)

    for tile_idx_y, y in enumerate(y_steps):
        for tile_idx_x, x in enumerate(x_steps):
            crop_img = stitched_img.crop((x, y, x + tile_size, y + tile_size))
            arr = np.transpose(np.array(crop_img.convert("RGB")), (2, 0, 1))

            # Geographic bounds of this crop in EPSG:4326
            t_min_lon = min_lon + (x * lon_res)
            t_max_lon = min_lon + ((x + tile_size) * lon_res)
            t_max_lat = max_lat - (y * lat_res)
            t_min_lat = max_lat - ((y + tile_size) * lat_res)

            poly = box(t_min_lon, t_min_lat, t_max_lon, t_max_lat)

            # Spatial filtering: Keep every tile that intersects the AOI polygon
            if aoi_polygon is not None and not poly.intersects(aoi_polygon):
                continue

            t_bbox = (t_min_lon, t_min_lat, t_max_lon, t_max_lat)
            c_lat = (t_min_lat + t_max_lat) / 2.0
            c_lon = (t_min_lon + t_max_lon) / 2.0
            tf = tf_from_bounds(t_min_lon, t_min_lat, t_max_lon, t_max_lat, tile_size, tile_size)

            band_stats = {
                "red": {"min": float(arr[0].min()), "max": float(arr[0].max()), "mean": round(float(arr[0].mean()), 2)},
                "green": {"min": float(arr[1].min()), "max": float(arr[1].max()), "mean": round(float(arr[1].mean()), 2)},
                "blue": {"min": float(arr[2].min()), "max": float(arr[2].max()), "mean": round(float(arr[2].mean()), 2)},
            }
            vari = calculate_vari_index(arr)

            tile_id = f"maxar_{region_id}_{scene_id}_{tile_idx_x}_{tile_idx_y}"
            site_key = f"maxar_site_{round(c_lat, 4)}_{round(c_lon, 4)}"

            tile = TileCandidate(
                tile_id=tile_id,
                scene_id=scene_id,
                site_key=site_key,
                multiband_data=arr.astype(np.float32) / 255.0,
                rgb_data=arr,
                band_order=["red", "green", "blue"],
                band_stats=band_stats,
                transform=tf,
                bounds=t_bbox,
                centroid_lat=c_lat,
                centroid_lon=c_lon,
                cloud_pct=0.0,
                quality_confidence=1.0,
                mean_ndvi=vari,
                mean_ndwi=None,
                mean_ndbi=None,
                footprint_geom=poly,
                source_type="maxar_wayback",
                bad_mask_data=None,
                bad_mask_path=None,
                pixel_scale="rgb_uint8"
            )
            tiles.append(tile)

    logger.info(f"Sliced Maxar canvas ({img_w}x{img_h}) into {len(tiles)} tiles of {tile_size}x{tile_size} (covering AOI).")
    return tiles
