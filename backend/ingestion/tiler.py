"""
Phase 1.4 & Phase 1.5: Sliding Window Tiling, Ground-Crop Zooming, & Polygon Filtering

Slices the cleaned and normalized canvas into 512x512 tiles, computes exact geographic
transforms, calculates per-tile real cloud percentage from the bad pixel mask,
and strictly filters candidate tiles against the validated AOI polygon footprint.

========================================================================================
RESOLUTION & GROUND CROP SIZE CONFIGURATION:
- GROUND_CROP_SIZE = 512: True 5.12km x 5.12km tile at native Sentinel-2 10m/px resolution.
  No upsampling or interpolation. Recommended for ML embedding & change detection accuracy.
- GROUND_CROP_SIZE = 25 (approx 250m x 250m): Zoomed ground patch upsampled to 512x512
  via Lanczos resampling for close visual inspection. Note: upsampled tiles are interpolated
  and do not contain genuine high-resolution sensor detail beyond native 10m pixels.
========================================================================================
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import Affine, from_bounds
from shapely.geometry import box, Polygon, MultiPolygon

from backend.ingestion.canvas import CanvasData
from backend.ingestion.masking import CleanedCanvas

logger = logging.getLogger(__name__)

# Default ground crop size (pixels on the 10m canvas before resizing to 512x512)
# Set to 512 for native resolution, or 25-100 for zoomed view.
DEFAULT_GROUND_CROP_SIZE = 512
TARGET_TILE_SIZE = 512  # Final output dimension (512x512)


@dataclass
class TileCandidate:
    """Represents a generated 512x512 tile ready for indexing/storage."""
    tile_id: str
    scene_id: str
    site_key: str
    rgb_data: np.ndarray             # Shape: (3, 512, 512), uint8
    transform: Affine                # Affine geotransform in EPSG:4326
    bounds: Tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    centroid_lat: float
    centroid_lon: float
    cloud_pct: float                 # Real fraction of bad/cloud pixels (0.0 to 1.0)
    footprint_geom: Polygon          # Shapely geometry for spatial intersection
    ground_crop_size: int
    is_upsampled: bool


def generate_site_key(lat: float, lon: float, precision: int = 4) -> str:
    """
    Generates a stable, deterministic spatial key for a physical ground location.
    Allows multi-temporal tiles of the same spot across different dates to be paired.
    """
    lat_r = round(lat, precision)
    lon_r = round(lon, precision)
    coord_str = f"{lat_r:.4f}_{lon_r:.4f}"
    h = hashlib.sha256(coord_str.encode("utf-8")).hexdigest()[:10]
    return f"site_{lat_r:.4f}_{lon_r:.4f}_{h}"


def slice_and_filter_tiles(
    canvas_data: CanvasData,
    cleaned_canvas: CleanedCanvas,
    aoi_polygon: Polygon,
    scene_id: str,
    ground_crop_size: int = DEFAULT_GROUND_CROP_SIZE,
    overlap_pct: float = 0.10,
    target_size: int = TARGET_TILE_SIZE
) -> List[TileCandidate]:
    """
    Generates 512x512 tiles across the working canvas and filters against the AOI polygon.

    Args:
        canvas_data: Working canvas with geotransform and dimensions.
        cleaned_canvas: Cleaned RGB array and bad pixel mask from Phase 1.3.
        aoi_polygon: Validated AOI Shapely polygon from Phase 1.0.
        scene_id: Satellite scene identifier.
        ground_crop_size: Window size in canvas pixels (e.g. 512 for native, 25 for zoomed).
        overlap_pct: Overlap fraction between adjacent windows (default 10%).
        target_size: Target tile size in pixels (default 512).

    Returns:
        List[TileCandidate]: Kept tiles intersecting the AOI polygon with real cloud_pct.
    """
    c_height, c_width = canvas_data.height, canvas_data.width
    c_transform = canvas_data.transform
    rgb = cleaned_canvas.rgb_normalized
    bad_mask = cleaned_canvas.bad_mask

    # Step size between sliding windows
    stride = max(1, int(round(ground_crop_size * (1.0 - overlap_pct))))
    is_upsampled = (ground_crop_size != target_size)

    y_starts = list(range(0, c_height - ground_crop_size + 1, stride))
    if not y_starts or y_starts[-1] + ground_crop_size < c_height:
        y_starts.append(max(0, c_height - ground_crop_size))
    y_starts = sorted(list(set(y_starts)))

    x_starts = list(range(0, c_width - ground_crop_size + 1, stride))
    if not x_starts or x_starts[-1] + ground_crop_size < c_width:
        x_starts.append(max(0, c_width - ground_crop_size))
    x_starts = sorted(list(set(x_starts)))

    candidates: List[TileCandidate] = []
    total_windows = 0
    kept_windows = 0

    for r in y_starts:
        for c in x_starts:
            total_windows += 1
            r_end = min(c_height, r + ground_crop_size)
            c_end = min(c_width, c + ground_crop_size)

            # 1. Geographic Bounds calculation from canvas transform
            # Upper-left coordinate of window
            lon_ul = c_transform.c + c_transform.a * c + c_transform.b * r
            lat_ul = c_transform.f + c_transform.d * c + c_transform.e * r
            # Lower-right coordinate of window
            lon_lr = c_transform.c + c_transform.a * c_end + c_transform.b * r_end
            lat_lr = c_transform.f + c_transform.d * c_end + c_transform.e * r_end

            min_lon = min(lon_ul, lon_lr)
            max_lon = max(lon_ul, lon_lr)
            min_lat = min(lat_ul, lat_lr)
            max_lat = max(lat_ul, lat_lr)

            tile_box = box(min_lon, min_lat, max_lon, max_lat)

            # 2. Phase 1.5: Filter against exact AOI polygon
            if not tile_box.intersects(aoi_polygon):
                # Discard tile if outside drawn AOI polygon
                continue

            kept_windows += 1

            # 3. Real Per-Tile Cloud & Shadow Percentage from Phase 1.3 mask
            mask_slice = bad_mask[r:r_end, c:c_end]
            if mask_slice.size > 0:
                tile_cloud_pct = round(float(np.count_nonzero(mask_slice) / mask_slice.size), 4)
            else:
                tile_cloud_pct = 0.0

            # 4. Crop RGB data and resize to target_size (512x512)
            rgb_crop = rgb[:, r:r_end, c:c_end]  # Shape: (3, H_crop, W_crop)

            if rgb_crop.shape[1] != target_size or rgb_crop.shape[2] != target_size:
                # Transpose to (H, W, 3) for PIL image resizing
                img_pil = Image.fromarray(np.transpose(rgb_crop, (1, 2, 0)))
                resample_method = Image.Resampling.LANCZOS if (rgb_crop.shape[1] < target_size or rgb_crop.shape[2] < target_size) else Image.Resampling.BICUBIC
                img_resized = img_pil.resize((target_size, target_size), resample=resample_method)
                rgb_final = np.transpose(np.array(img_resized), (2, 0, 1))
            else:
                rgb_final = rgb_crop

            # Compute tile transform for target 512x512 dimensions
            tile_transform = from_bounds(min_lon, min_lat, max_lon, max_lat, target_size, target_size)
            centroid_lat = round((min_lat + max_lat) / 2.0, 6)
            centroid_lon = round((min_lon + max_lon) / 2.0, 6)

            site_key = generate_site_key(centroid_lat, centroid_lon)
            tile_id = f"{scene_id}_{site_key}"

            candidate = TileCandidate(
                tile_id=tile_id,
                scene_id=scene_id,
                site_key=site_key,
                rgb_data=rgb_final,
                transform=tile_transform,
                bounds=(min_lon, min_lat, max_lon, max_lat),
                centroid_lat=centroid_lat,
                centroid_lon=centroid_lon,
                cloud_pct=tile_cloud_pct,
                footprint_geom=tile_box,
                ground_crop_size=ground_crop_size,
                is_upsampled=is_upsampled
            )
            candidates.append(candidate)

    logger.info(
        f"Tiling complete: generated {total_windows} windows, "
        f"kept {len(candidates)} tiles intersecting the AOI polygon."
    )

    return candidates
