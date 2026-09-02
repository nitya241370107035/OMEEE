"""
Phase 1.2: Download and Assemble Working Canvas

Pulls multi-band raster data (RGB + NIR) for the selected scene, crops to the AOI
bounding box with a safety buffer margin (+5% to +10%), and reprojects the canvas
once into EPSG:4326. Supports real remote COGs (via rasterio/GDAL HTTP) and synthetic
offline canvas generation for offline environments and unit testing.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds as window_from_bounds

from backend.ingestion.stac_search import STACSceneMetadata

logger = logging.getLogger(__name__)

# Default ~10m resolution in degrees at equator (~0.0001 deg)
DEFAULT_PIXEL_RES_DEG = 0.00008983  # ~10 meters in EPSG:4326


@dataclass
class CanvasData:
    """Represents a working multi-band satellite raster canvas."""
    data: np.ndarray             # Shape: (Bands, Height, Width), float32 or uint16
    band_names: List[str]        # e.g., ['red', 'green', 'blue', 'nir']
    transform: rasterio.Affine   # Affine transform for pixel-to-geographic mapping
    crs: str                     # Standardized to 'EPSG:4326'
    bbox: Tuple[float, float, float, float]  # Buffered canvas bounds (min_lon, min_lat, max_lon, max_lat)
    height: int
    width: int


def calculate_buffered_bbox(
    bbox: Tuple[float, float, float, float],
    buffer_pct: float = 0.05
) -> Tuple[float, float, float, float]:
    """
    Expands a bounding box by a percentage margin on all sides.
    Ensures edge tiles have surrounding pixel context.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    width = max_lon - min_lon
    height = max_lat - min_lat
    
    buf_x = width * buffer_pct
    buf_y = height * buffer_pct
    
    return (
        max(-180.0, min_lon - buf_x),
        max(-90.0, min_lat - buf_y),
        min(180.0, max_lon + buf_x),
        min(90.0, max_lat + buf_y)
    )


def assemble_working_canvas(
    scene_meta: STACSceneMetadata,
    aoi_bbox: Tuple[float, float, float, float],
    buffer_pct: float = 0.05,
    target_crs: str = "EPSG:4326",
    pixel_res_deg: float = DEFAULT_PIXEL_RES_DEG
) -> CanvasData:
    """
    Downloads and reprojects scene bands into a unified EPSG:4326 working canvas.

    Args:
        scene_meta: Metadata with band URLs from Phase 1.1.
        aoi_bbox: Unbuffered AOI bounding box (min_lon, min_lat, max_lon, max_lat).
        buffer_pct: Safety margin fraction (default 5%).
        target_crs: Common CRS (always 'EPSG:4326').
        pixel_res_deg: Target resolution in degrees (~10m).

    Returns:
        CanvasData: Multi-band raster canvas cropped to buffered bbox in EPSG:4326.
    """
    buffered_bbox = calculate_buffered_bbox(aoi_bbox, buffer_pct=buffer_pct)
    min_lon, min_lat, max_lon, max_lat = buffered_bbox

    # If mock scene or remote URLs are unavailable/offline, generate deterministic synthetic canvas
    if scene_meta.is_mock or not scene_meta.assets.red.startswith(("http://", "https://", "file://", "s3://")):
        logger.info("Using synthetic canvas generator for offline/mock scene.")
        return generate_synthetic_canvas(
            buffered_bbox=buffered_bbox,
            pixel_res_deg=pixel_res_deg,
            cloud_pct_target=scene_meta.cloud_cover / 100.0
        )

    # Real remote/local raster read via Rasterio
    band_urls = {
        "red": scene_meta.assets.red,
        "green": scene_meta.assets.green,
        "blue": scene_meta.assets.blue,
    }
    if scene_meta.assets.nir:
        band_urls["nir"] = scene_meta.assets.nir

    width = max(512, int(round((max_lon - min_lon) / pixel_res_deg)))
    height = max(512, int(round((max_lat - min_lat) / pixel_res_deg)))
    final_transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    canvas_bands = []
    band_names = []

    try:
        for b_name, url in band_urls.items():
            if not url:
                continue
            logger.info(f"Reading band {b_name} from: {url}")
            with rasterio.open(url) as src:
                dst_arr = np.zeros((height, width), dtype=np.float32)
                reproject(
                    source=rasterio.band(src, 1),
                    destination=dst_arr,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=final_transform,
                    dst_crs=target_crs,
                    resampling=Resampling.bilinear
                )
                canvas_bands.append(dst_arr)
                band_names.append(b_name)

        if not canvas_bands:
            raise ValueError("No valid bands could be loaded from scene assets.")

        stacked_data = np.stack(canvas_bands, axis=0)

        return CanvasData(
            data=stacked_data,
            band_names=band_names,
            transform=final_transform,
            crs=target_crs,
            bbox=buffered_bbox,
            height=height,
            width=width
        )

    except Exception as exc:
        logger.warning(f"Failed to fetch live raster bands ({exc}). Falling back to synthetic canvas.")
        return generate_synthetic_canvas(
            buffered_bbox=buffered_bbox,
            pixel_res_deg=pixel_res_deg,
            cloud_pct_target=scene_meta.cloud_cover / 100.0
        )


def generate_synthetic_canvas(
    buffered_bbox: Tuple[float, float, float, float],
    pixel_res_deg: float = DEFAULT_PIXEL_RES_DEG,
    cloud_pct_target: float = 0.15,
    seed: int = 42
) -> CanvasData:
    """
    Generates a deterministic synthetic 4-band (R, G, B, NIR) canvas with realistic
    ground features (fields, urban, water) and synthetic cloud + shadow patches.
    Useful for offline testing and pipeline validation.
    """
    rng = np.random.default_rng(seed)
    min_lon, min_lat, max_lon, max_lat = buffered_bbox

    width = max(512, int(round((max_lon - min_lon) / pixel_res_deg)))
    height = max(512, int(round((max_lat - min_lat) / pixel_res_deg)))

    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    # Base terrain reflectance in Sentinel-2 scale (0 - 10000 DN)
    # Ground background: vegetation / soil / urban
    red = np.full((height, width), 600.0, dtype=np.float32)
    green = np.full((height, width), 800.0, dtype=np.float32)
    blue = np.full((height, width), 500.0, dtype=np.float32)
    nir = np.full((height, width), 2400.0, dtype=np.float32)

    # Add landscape textures
    y_coords, x_coords = np.mgrid[0:height, 0:width]
    texture = np.sin(x_coords / 15.0) * np.cos(y_coords / 15.0) * 150.0
    red += texture
    green += texture * 1.2
    blue += texture * 0.8
    nir += texture * 2.0

    # Add random agricultural/urban variations
    noise = rng.normal(0, 40, (height, width)).astype(np.float32)
    red += noise
    green += noise
    blue += noise
    nir += noise * 1.5

    # Inject synthetic cloud patches if cloud_pct_target > 0
    if cloud_pct_target > 0.01:
        num_clouds = max(1, int(cloud_pct_target * 8))
        for _ in range(num_clouds):
            cy = rng.integers(int(height * 0.2), int(height * 0.8))
            cx = rng.integers(int(width * 0.2), int(width * 0.8))
            r = rng.integers(int(min(height, width) * 0.08), int(min(height, width) * 0.20))
            
            # Cloud footprint
            dist_sq = (y_coords - cy) ** 2 + (x_coords - cx) ** 2
            cloud_mask = dist_sq < (r ** 2)
            
            # Bright cloud reflectance (high across all bands)
            red[cloud_mask] = rng.uniform(4000, 8000)
            green[cloud_mask] = rng.uniform(4000, 8000)
            blue[cloud_mask] = rng.uniform(4500, 8500)
            nir[cloud_mask] = rng.uniform(4000, 8000)

            # Shadow footprint offset (e.g. South-East shadow offset)
            sy = min(height - 1, cy + int(r * 0.6))
            sx = min(width - 1, cx + int(r * 0.6))
            shadow_dist_sq = (y_coords - sy) ** 2 + (x_coords - sx) ** 2
            shadow_mask = (shadow_dist_sq < (r ** 2 * 0.7)) & (~cloud_mask)

            # Dark shadow reflectance
            red[shadow_mask] = np.clip(red[shadow_mask] * 0.25, 50, 200)
            green[shadow_mask] = np.clip(green[shadow_mask] * 0.25, 50, 200)
            blue[shadow_mask] = np.clip(blue[shadow_mask] * 0.35, 60, 250)
            nir[shadow_mask] = np.clip(nir[shadow_mask] * 0.20, 50, 300)

    # Clip values to valid positive reflectance range
    red = np.clip(red, 0, 10000)
    green = np.clip(green, 0, 10000)
    blue = np.clip(blue, 0, 10000)
    nir = np.clip(nir, 0, 10000)

    data = np.stack([red, green, blue, nir], axis=0)

    return CanvasData(
        data=data,
        band_names=["red", "green", "blue", "nir"],
        transform=transform,
        crs="EPSG:4326",
        bbox=buffered_bbox,
        height=height,
        width=width
    )
