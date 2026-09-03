"""
backend/ingestion/canvas.py
===========================
Phase 1.2: 5-Band Working Canvas Assembly (Shared by Both Entry Points)
=======================================================================
PS Sections: 2.2.1, 2.2.3, 2.2.6 (Ingestion), 2.2.7 (Evaluation Constraints)

Assembles a unified, reprojected EPSG:4326 multi-band canvas from either:
  1. Entry Point A (STAC Scene Metadata):
     - Streams 5 core bands: Blue (B02), Green (B03), Red (B04), NIR (B08), SWIR (B11).
     - Reprojects from native UTM to EPSG:4326 in a single stage.
     - Crops to the buffered AOI bounding box (+5% margin).

  2. Entry Point B (Direct Local GeoTIFF — 100% Offline):
     - Reads bands directly from local evaluation files without network.
     - Reprojects to EPSG:4326 if native CRS differs.
     - Uses file's own extent and detected band layout.

Both entry points produce the identical CanvasData representation, feeding directly
into Phase 1.3 (Quality Masking) and Phase 1.4 (Tiling & NDVI/NDWI/NDBI indices).
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Union

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds as window_from_bounds

from backend.ingestion.input_validator import ValidatedFileInput
from backend.ingestion.stac_search import STACSceneMetadata

logger = logging.getLogger(__name__)

# ~10 meters in EPSG:4326 degrees (at equator)
DEFAULT_PIXEL_RES_DEG = 0.00008983

# 5 Core Bands for Visuals, Vegetation (NDVI), Water (NDWI), and Built-Up (NDBI)
DEFAULT_5_BANDS = ["blue", "green", "red", "nir", "swir"]
STANDARD_CANVAS_BANDS = DEFAULT_5_BANDS


@dataclass
class CanvasData:
    """Represents a standardized multi-band satellite raster canvas in EPSG:4326."""
    data: np.ndarray             # Shape: (Bands, Height, Width), float32 or uint16
    band_names: List[str]        # e.g., ['blue', 'green', 'red', 'nir', 'swir']
    transform: rasterio.Affine   # Affine transform for pixel-to-geographic mapping
    crs: str                     # Standardized to 'EPSG:4326'
    bbox: Tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    height: int
    width: int
    source_type: str = "aoi_search"
    visual_rgb: Optional[np.ndarray] = None # Shape (3, H, W) pristine 10m True Color Image

    def has_band(self, name: str) -> bool:
        return name.lower() in [b.lower() for b in self.band_names]

    def get_band(self, name: str) -> np.ndarray:
        """Returns 2D array of the requested band (float32)."""
        lower_names = [b.lower() for b in self.band_names]
        target = name.lower()
        if target not in lower_names:
            raise KeyError(f"Band '{name}' not found in canvas. Available: {self.band_names}")
        idx = lower_names.index(target)
        return self.data[idx].astype(np.float32)

    def get_rgb(self) -> np.ndarray:
        """Returns 3-band RGB array of shape (3, H, W)."""
        if self.visual_rgb is not None and self.visual_rgb.shape[0] >= 3:
            return self.visual_rgb.astype(np.float32)
        red = self.get_band("red")
        green = self.get_band("green")
        blue = self.get_band("blue")
        return np.stack([red, green, blue], axis=0)


def calculate_buffered_bbox(
    bbox: Tuple[float, float, float, float],
    buffer_pct: float = 0.05
) -> Tuple[float, float, float, float]:
    """Expands bounding box by a safety margin percentage."""
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


# ============================================================
# 1. Entry Point A: Assemble Canvas from STAC COG URLs
# ============================================================

def assemble_canvas_from_stac(
    scene_meta: STACSceneMetadata,
    aoi_bbox: Tuple[float, float, float, float],
    buffer_pct: float = 0.05,
    pixel_res_deg: float = DEFAULT_PIXEL_RES_DEG
) -> CanvasData:
    """
    Entry Point A Canvas Assembler:
    Streams 5 bands (Blue, Green, Red, NIR, SWIR) from STAC COGs and reprojects to EPSG:4326.
    Also streams 10m True Color Image (TCI) for crystal-clear visual RGB rendering when available.
    """
    buffered_bbox = calculate_buffered_bbox(aoi_bbox, buffer_pct=buffer_pct)
    min_lon, min_lat, max_lon, max_lat = buffered_bbox

    # If mock scene or remote URLs are unavailable/offline, generate synthetic 5-band canvas
    if scene_meta.is_mock or not scene_meta.assets.red.startswith(("http://", "https://", "file://", "s3://")):
        logger.info("[Phase 1.2] Using synthetic 5-band canvas generator for offline/mock scene.")
        return generate_synthetic_canvas(
            buffered_bbox=buffered_bbox,
            pixel_res_deg=pixel_res_deg,
            cloud_pct_target=scene_meta.cloud_cover / 100.0
        )

    width = max(512, int(round((max_lon - min_lon) / pixel_res_deg)))
    height = max(512, int(round((max_lat - min_lat) / pixel_res_deg)))
    target_transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    # 5-Band targets: Blue (B02), Green (B03), Red (B04), NIR (B08), SWIR (B11)
    band_targets = [
        ("blue", scene_meta.assets.blue),
        ("green", scene_meta.assets.green),
        ("red", scene_meta.assets.red),
        ("nir", scene_meta.assets.nir),
        ("swir", scene_meta.assets.swir),
    ]

    env_params = {
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MAX_RETRY": "5",
        "GDAL_HTTP_RETRY_DELAY": "1",
    }

    canvas_layers = []
    loaded_band_names = []
    visual_rgb = None

    with rasterio.Env(**env_params):
        # 1. Stream True Color Image (TCI) COG if available
        if scene_meta.assets.visual:
            try:
                with rasterio.open(scene_meta.assets.visual) as src:
                    with WarpedVRT(src, crs="EPSG:4326", transform=target_transform,
                                   width=width, height=height, resampling=Resampling.bilinear) as vrt:
                        tci_raw = vrt.read(out_shape=(3, height, width), resampling=Resampling.bilinear)
                        visual_rgb = tci_raw.astype(np.float32)
                        logger.info(f"[Phase 1.2] Successfully streamed 10m True Color Image (TCI) ({width}x{height})")
            except Exception as e:
                logger.warning(f"[Phase 1.2] Could not stream visual TCI asset: {e}")

        # 2. Stream individual scientific bands
        for b_name, b_url in band_targets:
            if not b_url:
                continue

            try:
                with rasterio.open(b_url) as src:
                    # Single-stage WarpedVRT reprojection to EPSG:4326
                    with WarpedVRT(src, crs="EPSG:4326", transform=target_transform,
                                   width=width, height=height, resampling=Resampling.bilinear) as vrt:
                        data = vrt.read(1, out_shape=(height, width), resampling=Resampling.bilinear)
                        canvas_layers.append(data.astype(np.float32))
                        loaded_band_names.append(b_name)
            except Exception as e:
                logger.warning(f"[Phase 1.2] Failed to stream band '{b_name}' from {b_url}: {e}")

    if not canvas_layers:
        logger.warning("[Phase 1.2] STAC band streaming failed. Falling back to synthetic 5-band canvas.")
        return generate_synthetic_canvas(buffered_bbox, pixel_res_deg, scene_meta.cloud_cover / 100.0)

    canvas_array = np.stack(canvas_layers, axis=0)

    return CanvasData(
        data=canvas_array,
        band_names=loaded_band_names,
        transform=target_transform,
        crs="EPSG:4326",
        bbox=buffered_bbox,
        height=height,
        width=width,
        source_type="aoi_search",
        visual_rgb=visual_rgb
    )


# ============================================================
# 2. Entry Point B: Assemble Canvas from Local GeoTIFF (Offline)
# ============================================================

def assemble_canvas_from_file(
    file_input: ValidatedFileInput,
    target_crs: str = "EPSG:4326",
    pixel_res_deg: float = DEFAULT_PIXEL_RES_DEG
) -> CanvasData:
    """
    Entry Point B Canvas Assembler:
    Reads local evaluation GeoTIFF with ZERO network calls, reprojecting to EPSG:4326.
    """
    min_lon, min_lat, max_lon, max_lat = file_input.bounds_wgs84
    width = max(512, int(round((max_lon - min_lon) / pixel_res_deg)))
    height = max(512, int(round((max_lat - min_lat) / pixel_res_deg)))
    target_transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    with rasterio.open(str(file_input.file_path)) as src:
        # Reproject to EPSG:4326 grid
        with WarpedVRT(src, crs=target_crs, transform=target_transform,
                       width=width, height=height, resampling=Resampling.bilinear) as vrt:
            raw_data = vrt.read(out_shape=(src.count, height, width), resampling=Resampling.bilinear)

    return CanvasData(
        data=raw_data.astype(np.float32),
        band_names=file_input.band_order,
        transform=target_transform,
        crs="EPSG:4326",
        bbox=file_input.bounds_wgs84,
        height=height,
        width=width,
        source_type="organiser_provided"
    )


# ============================================================
# 3. Synthetic 5-Band Canvas Generator (Offline/Fallback)
# ============================================================

def generate_synthetic_canvas(
    buffered_bbox: Tuple[float, float, float, float],
    pixel_res_deg: float = DEFAULT_PIXEL_RES_DEG,
    cloud_pct_target: float = 0.05
) -> CanvasData:
    """Generates a realistic 5-band synthetic canvas (Blue, Green, Red, NIR, SWIR)."""
    min_lon, min_lat, max_lon, max_lat = buffered_bbox
    width = max(512, int(round((max_lon - min_lon) / pixel_res_deg)))
    height = max(512, int(round((max_lat - min_lat) / pixel_res_deg)))
    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    np.random.seed(42)

    # Base ground reflectances
    red = np.random.normal(loc=1200, scale=300, size=(height, width)).clip(200, 6000)
    green = np.random.normal(loc=1100, scale=250, size=(height, width)).clip(200, 5000)
    blue = np.random.normal(loc=900, scale=200, size=(height, width)).clip(100, 4000)
    nir = np.random.normal(loc=2500, scale=600, size=(height, width)).clip(300, 9000)
    swir = np.random.normal(loc=1800, scale=400, size=(height, width)).clip(200, 7000)

    # Add synthetic clouds if target > 0
    if cloud_pct_target > 0.01:
        num_clouds = max(1, int(cloud_pct_target * 20))
        for _ in range(num_clouds):
            cy, cx = np.random.randint(0, height), np.random.randint(0, width)
            radius = np.random.randint(20, 70)
            y, x = np.ogrid[:height, :width]
            dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            mask = dist <= radius
            # High visible + NIR reflectance for cloud
            red[mask] = 8000 + np.random.normal(0, 500, size=np.sum(mask))
            green[mask] = 8000 + np.random.normal(0, 500, size=np.sum(mask))
            blue[mask] = 8500 + np.random.normal(0, 500, size=np.sum(mask))
            nir[mask] = 8000 + np.random.normal(0, 500, size=np.sum(mask))
            swir[mask] = 3000 + np.random.normal(0, 300, size=np.sum(mask))

    data = np.stack([blue, green, red, nir, swir], axis=0).astype(np.float32)

    return CanvasData(
        data=data,
        band_names=["blue", "green", "red", "nir", "swir"],
        transform=transform,
        crs="EPSG:4326",
        bbox=buffered_bbox,
        height=height,
        width=width,
        source_type="synthetic"
    )


# Backward compatibility unified entry
def assemble_working_canvas(
    scene_meta_or_file: Union[STACSceneMetadata, ValidatedFileInput],
    aoi_bbox: Optional[Tuple[float, float, float, float]] = None,
    buffer_pct: float = 0.05,
    pixel_res_deg: float = DEFAULT_PIXEL_RES_DEG
) -> CanvasData:
    """Unified entry point dispatcher."""
    if isinstance(scene_meta_or_file, ValidatedFileInput):
        return assemble_canvas_from_file(scene_meta_or_file, pixel_res_deg=pixel_res_deg)
    elif isinstance(scene_meta_or_file, STACSceneMetadata):
        if aoi_bbox is None:
            aoi_bbox = scene_meta_or_file.bbox
        return assemble_canvas_from_stac(scene_meta_or_file, aoi_bbox, buffer_pct, pixel_res_deg)
    else:
        raise TypeError(f"Unsupported source for canvas assembly: {type(scene_meta_or_file)}")
