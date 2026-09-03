"""
GeoTIFF Rasterio Reader

Safely inspects and reads multi-spectral, RGB, single-band, and mask GeoTIFFs
without modifying any source files.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
import rasterio

from backend.preview.raster_metadata import RasterMetadata

logger = logging.getLogger(__name__)


def format_file_size(size_bytes: int) -> str:
    """Formats file size in bytes into human-readable string (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def classify_geotiff_category(path_obj: Path) -> str:
    """
    Classifies a GeoTIFF into logical processing tier:
      - 'WORKING_CANVAS': AOI/Scene processing space (dimensions vary, 512x512 not required)
      - 'QUALITY_LAYER': Cloud probability, cloud mask, shadow mask, bad pixel mask
      - 'FINAL_TILE': Final cropped output tile (must be exactly 512x512)
      - 'UNKNOWN': Unclassified raster
    """
    parts = [p.lower() for p in path_obj.parts]
    name_lower = path_obj.name.lower()

    if "tiles" in parts or name_lower.startswith("tile_"):
        return "FINAL_TILE"
    if "intermediate" in parts:
        if "canvas" in name_lower or name_lower in ["raw_canvas.tif", "normalized_canvas.tif"]:
            return "WORKING_CANVAS"
        if any(k in name_lower for k in ["probability", "mask", "cloud", "shadow", "bad"]):
            return "QUALITY_LAYER"
        return "WORKING_CANVAS"
    if "canvas" in name_lower:
        return "WORKING_CANVAS"
    if any(k in name_lower for k in ["mask", "probability"]):
        return "QUALITY_LAYER"
    if name_lower.startswith("tile_"):
        return "FINAL_TILE"
    return "UNKNOWN"


def read_geotiff(
    file_path: Path,
    read_data: bool = True
) -> Tuple[RasterMetadata, Optional[np.ndarray]]:
    """
    Reads a GeoTIFF file using Rasterio and returns structured metadata and array data.

    Args:
        file_path: Absolute or relative Path to GeoTIFF file.
        read_data: If True, reads array into memory; if False, metadata only.

    Returns:
        Tuple[RasterMetadata, Optional[np.ndarray]]: (metadata, numpy array with shape (Bands, H, W))
    """
    path_obj = Path(file_path).resolve()
    if not path_obj.exists():
        raise FileNotFoundError(f"GeoTIFF file not found: {path_obj}")

    size_bytes = path_obj.stat().st_size
    formatted_size = format_file_size(size_bytes)
    category = classify_geotiff_category(path_obj)

    with rasterio.open(path_obj) as src:
        crs_str = str(src.crs) if src.crs else "MISSING / UNPROJECTED"
        res_x, res_y = src.res
        bounds = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)
        
        # Band descriptions and color interpretation
        descriptions = [src.descriptions[i] or f"Band_{i+1}" for i in range(src.count)]
        colorinterp = [ci.name for ci in src.colorinterp] if src.colorinterp else []
        dtypes = [src.dtypes[i] for i in range(src.count)]
        tags = dict(src.tags())

        metadata = RasterMetadata(
            file_name=path_obj.name,
            full_path=str(path_obj),
            file_category=category,
            file_size_bytes=size_bytes,
            file_size_formatted=formatted_size,
            count=src.count,
            width=src.width,
            height=src.height,
            dimensions=(src.height, src.width),
            crs=crs_str,
            transform=tuple(src.transform),
            bounds=bounds,
            resolution=(res_x, res_y),
            dtypes=dtypes,
            nodata=src.nodata,
            tags=tags,
            band_descriptions=descriptions,
            colorinterp=colorinterp
        )

        data = src.read() if read_data else None

    return metadata, data
