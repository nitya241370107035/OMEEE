"""
backend/ingestion/tiler.py
==========================
Phase 1.4: Multi-Band Sliding Window Tiling & Index Computation (NDVI, NDWI, NDBI)
==================================================================================
PS Sections: 2.2.1, 2.2.3 (Quality Filtering), 2.2.6 (Tiling Foundation)

Slices the working canvas into 512x512 multi-band tiles, computes exact geographic
transforms, calculates real per-tile cloud percentage from the bad pixel mask,
computes mean spectral indices (NDVI, NDWI, NDBI), and performs spatial filtering.

Derived Indices:
  - NDVI = (NIR - Red) / (NIR + Red)           [Vegetation Health & Crops]
  - NDWI = (Green - NIR) / (Green + NIR)       [Water Bodies & Inundation]
  - NDBI = (SWIR - NIR) / (SWIR + NIR)         [Built-Up, Concrete & Structures]
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional, Union
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import Affine, from_bounds
from shapely.geometry import box, Polygon, MultiPolygon

from backend.ingestion.canvas import CanvasData
from backend.ingestion.masking import CleanedCanvas

logger = logging.getLogger(__name__)

DEFAULT_GROUND_CROP_SIZE = 512
TARGET_TILE_SIZE = 512


@dataclass
class TileCandidate:
    """Represents a generated 512x512 tile with full multi-band data, RGB, and spectral indices."""
    tile_id: str
    scene_id: str
    site_key: str
    multiband_data: np.ndarray       # Shape: (Bands, 512, 512), float32 (Raw bit depth)
    rgb_data: np.ndarray             # Shape: (3, 512, 512), uint8 (Normalized RGB)
    band_order: List[str]            # e.g. ['blue', 'green', 'red', 'nir', 'swir']
    band_stats: Dict[str, Dict[str, float]]  # per-band min, max, mean
    transform: Affine                # Affine geotransform in EPSG:4326
    bounds: Tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    centroid_lat: float
    centroid_lon: float
    cloud_pct: float                 # Real fraction of bad/cloud pixels (0.0 to 1.0)
    quality_confidence: float        # Quality gate (1.0 - cloud_pct)
    mean_ndvi: Optional[float]       # (NIR - Red) / (NIR + Red)
    mean_ndwi: Optional[float]       # (Green - NIR) / (Green + NIR)
    mean_ndbi: Optional[float]       # (SWIR - NIR) / (SWIR + NIR)
    footprint_geom: Polygon          # Shapely polygon footprint in EPSG:4326
    source_type: str = "aoi_search"


def generate_site_key(lat: float, lon: float, precision: int = 4) -> str:
    """
    Generates a stable, deterministic spatial key for a physical ground location.
    Shared across multi-temporal tiles covering the same spot on Earth.
    """
    lat_r = round(lat, precision)
    lon_r = round(lon, precision)
    coord_str = f"{lat_r:.4f}_{lon_r:.4f}"
    h = hashlib.sha256(coord_str.encode("utf-8")).hexdigest()[:8]
    return f"site_{lat_r:.4f}_{lon_r:.4f}_{h}"


def compute_spectral_indices(
    tile_multiband: np.ndarray,
    band_order: List[str],
    bad_mask: np.ndarray
) -> Tuple[Optional[float], Optional[float], Optional[float], Dict[str, Dict[str, float]]]:
    """
    Computes scalar mean NDVI, NDWI, NDBI and per-band statistics for a tile.
    Excludes bad/cloud pixels when computing means to avoid skewing indices.
    """
    band_map = {name.lower(): tile_multiband[i].astype(np.float32) for i, name in enumerate(band_order)}
    valid_mask = ~bad_mask
    if not np.any(valid_mask):
        valid_mask = np.ones_like(bad_mask, dtype=bool)

    red = band_map.get("red")
    green = band_map.get("green")
    nir = band_map.get("nir")
    swir = band_map.get("swir")

    mean_ndvi: Optional[float] = None
    mean_ndwi: Optional[float] = None
    mean_ndbi: Optional[float] = None

    # NDVI = (NIR - Red) / (NIR + Red)
    if nir is not None and red is not None:
        denom = nir + red
        denom[denom == 0] = 1e-6
        ndvi_arr = (nir - red) / denom
        mean_ndvi = float(np.clip(np.mean(ndvi_arr[valid_mask]), -1.0, 1.0))

    # NDWI = (Green - NIR) / (Green + NIR)
    if green is not None and nir is not None:
        denom = green + nir
        denom[denom == 0] = 1e-6
        ndwi_arr = (green - nir) / denom
        mean_ndwi = float(np.clip(np.mean(ndwi_arr[valid_mask]), -1.0, 1.0))

    # NDBI = (SWIR - NIR) / (SWIR + NIR)
    if swir is not None and nir is not None:
        denom = swir + nir
        denom[denom == 0] = 1e-6
        ndbi_arr = (swir - nir) / denom
        mean_ndbi = float(np.clip(np.mean(ndbi_arr[valid_mask]), -1.0, 1.0))

    # Per-band summary statistics
    band_stats = {}
    for name, arr in band_map.items():
        v_pixels = arr[valid_mask]
        band_stats[name] = {
            "min": round(float(np.min(v_pixels)), 2),
            "max": round(float(np.max(v_pixels)), 2),
            "mean": round(float(np.mean(v_pixels)), 2)
        }

    return mean_ndvi, mean_ndwi, mean_ndbi, band_stats


def _get_grid_steps(total_dim: int, window_size: int, stride: int) -> List[int]:
    """
    Computes equidistant sliding window start offsets ensuring full 100% boundary coverage
    with uniform spacing and ZERO identical/duplicate tile slices.
    """
    if total_dim <= window_size:
        return [0]
    
    num_steps = max(2, int(np.ceil((total_dim - window_size) / float(stride))) + 1)
    offsets = np.linspace(0, total_dim - window_size, num_steps, dtype=int)
    return sorted(list(set(offsets.tolist())))


def slice_and_filter_tiles(
    canvas_data: CanvasData,
    cleaned_canvas: CleanedCanvas,
    scene_id: str,
    aoi_polygon: Optional[Union[Polygon, MultiPolygon]] = None,
    ground_crop_size: int = DEFAULT_GROUND_CROP_SIZE,
    overlap_pct: float = 0.15,
    target_size: int = TARGET_TILE_SIZE,
    source_type: str = "aoi_search"
) -> List[TileCandidate]:
    """
    Slices the multi-band working canvas into distinct 512x512 tiles, computes indices,
    and filters against the AOI polygon (if provided) ensuring complete, non-duplicated coverage.
    """
    c_height, c_width = canvas_data.height, canvas_data.width
    c_transform = canvas_data.transform
    raw_multiband = canvas_data.data
    band_order = canvas_data.band_names
    rgb = cleaned_canvas.rgb_normalized
    bad_mask = cleaned_canvas.bad_mask

    # Adapt effective crop size if the canvas itself is smaller than 512x512
    eff_crop_size = min(ground_crop_size, min(c_height, c_width))
    stride = max(1, int(round(eff_crop_size * (1.0 - overlap_pct))))

    y_steps = _get_grid_steps(c_height, eff_crop_size, stride)
    x_steps = _get_grid_steps(c_width, eff_crop_size, stride)

    tiles: List[TileCandidate] = []
    tile_index = 1

    for y in y_steps:
        for x in x_steps:
            # Compute tile bounds in EPSG:4326
            min_lon, max_lat = c_transform * (x, y)
            max_lon, min_lat = c_transform * (x + eff_crop_size, y + eff_crop_size)
            tile_bounds = (min_lon, min_lat, max_lon, max_lat)
            tile_poly = box(min_lon, min_lat, max_lon, max_lat)

            # Spatial filtering against AOI polygon (Entry Point A only)
            if aoi_polygon is not None and not tile_poly.intersects(aoi_polygon):
                continue

            centroid_lon = (min_lon + max_lon) / 2.0
            centroid_lat = (min_lat + max_lat) / 2.0
            site_key = generate_site_key(centroid_lat, centroid_lon)

            # Extract window arrays with exact effective crop dimensions
            tile_bad_mask = bad_mask[y:y + eff_crop_size, x:x + eff_crop_size]
            tile_cloud_pct = float(np.count_nonzero(tile_bad_mask)) / float(tile_bad_mask.size)
            quality_conf = max(0.0, min(1.0, 1.0 - tile_cloud_pct))

            tile_multiband = raw_multiband[:, y:y + eff_crop_size, x:x + eff_crop_size]
            tile_rgb = rgb[:, y:y + eff_crop_size, x:x + eff_crop_size]

            # Discard completely empty / nodata tiles located outside the satellite swath
            if np.all(tile_multiband == 0) or float(np.mean(tile_multiband)) < 1e-3:
                continue

            # Resize to target 512x512 if effective crop size differs from target size
            if eff_crop_size != target_size:
                # Resize RGB
                pil_rgb = Image.fromarray(np.transpose(tile_rgb, (1, 2, 0)))
                pil_rgb_resized = pil_rgb.resize((target_size, target_size), Image.Resampling.BILINEAR)
                final_rgb = np.transpose(np.array(pil_rgb_resized), (2, 0, 1))

                # Resize multiband
                resized_bands = []
                for b_idx in range(tile_multiband.shape[0]):
                    pil_b = Image.fromarray(tile_multiband[b_idx])
                    pil_b_resized = pil_b.resize((target_size, target_size), Image.Resampling.BILINEAR)
                    resized_bands.append(np.array(pil_b_resized, dtype=np.float32))
                final_multiband = np.stack(resized_bands, axis=0)

                # Resize mask
                pil_m = Image.fromarray(tile_bad_mask.astype(np.uint8))
                pil_m_resized = pil_m.resize((target_size, target_size), Image.Resampling.NEAREST)
                final_bad_mask = np.array(pil_m_resized, dtype=bool)
            else:
                final_rgb = tile_rgb
                final_multiband = tile_multiband
                final_bad_mask = tile_bad_mask

            tile_transform = from_bounds(min_lon, min_lat, max_lon, max_lat, target_size, target_size)

            # Compute spectral indices
            mean_ndvi, mean_ndwi, mean_ndbi, band_stats = compute_spectral_indices(
                final_multiband, band_order, final_bad_mask
            )

            clean_scene_id = scene_id.replace(":", "_").replace("/", "_")
            tile_id = f"{clean_scene_id}_tile_{tile_index:05d}"
            tile_index += 1

            tiles.append(
                TileCandidate(
                    tile_id=tile_id,
                    scene_id=scene_id,
                    site_key=site_key,
                    multiband_data=final_multiband,
                    rgb_data=final_rgb,
                    band_order=band_order,
                    band_stats=band_stats,
                    transform=tile_transform,
                    bounds=tile_bounds,
                    centroid_lat=round(centroid_lat, 6),
                    centroid_lon=round(centroid_lon, 6),
                    cloud_pct=round(tile_cloud_pct, 4),
                    quality_confidence=round(quality_conf, 4),
                    mean_ndvi=mean_ndvi,
                    mean_ndwi=mean_ndwi,
                    mean_ndbi=mean_ndbi,
                    footprint_geom=tile_poly,
                    source_type=source_type
                )
            )

    logger.info(
        f"[Phase 1.4] Tiling completed for scene '{scene_id}': "
        f"Generated {len(tiles)} tiles (Crop={ground_crop_size}px, Overlap={overlap_pct*100:.0f}%)"
    )
    return tiles
