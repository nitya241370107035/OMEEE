"""
backend/ingestion/storage.py
============================
Phase 1.5: Disk Storage & Manifest Generation (Multi-Band GeoTIFFs + RGB Thumbnails)
====================================================================================
PS Sections: 2.2.3, 2.2.6 (Sovereign Storage Architecture)

Saves tiles to the standardized directory layout:
  data/tiles/{region_id}/{date}/
    ├── {tile_id}.tif          (Full multi-band GeoTIFF with EPSG:4326 transform)
    ├── {tile_id}_thumb.jpg    (8-bit RGB visual thumbnail for UI)
    └── manifest.json          (Full metadata, quality signals, and spectral indices)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
import numpy as np
from PIL import Image
import rasterio
from rasterio.crs import CRS
from shapely.geometry import mapping

from backend.ingestion.input_validator import ValidatedAOI
from backend.ingestion.stac_search import STACSceneMetadata
from backend.ingestion.tiler import TileCandidate

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")


def format_date_dir(date_iso: Optional[Union[str, datetime]]) -> str:
    """Formats an ISO-8601 date string to YYYY-MM-DD for folder naming."""
    if not date_iso:
        return datetime.utcnow().strftime("%Y-%m-%d")
    if isinstance(date_iso, datetime):
        return date_iso.strftime("%Y-%m-%d")
    try:
        dt = datetime.fromisoformat(str(date_iso).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(date_iso)[:10].replace(":", "-")


def save_tile_geotiff(
    tile: TileCandidate,
    output_path: Path,
    crs: str = "EPSG:4326"
) -> str:
    """
    Saves multi-band tile as full-precision GeoTIFF with EPSG:4326 geotransform.
    """
    num_bands, height, width = tile.multiband_data.shape
    dtype = tile.multiband_data.dtype

    with rasterio.open(
        str(output_path),
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=num_bands,
        dtype=dtype,
        crs=CRS.from_string(crs),
        transform=tile.transform,
        compress="lzw"
    ) as dst:
        dst.write(tile.multiband_data)
        for idx, b_name in enumerate(tile.band_order, start=1):
            dst.set_band_description(idx, b_name)

    return str(output_path.as_posix())


def save_tile_thumbnail(
    tile: TileCandidate,
    output_path: Path,
    thumb_size: Tuple[int, int] = (512, 512),
    quality: int = 95
) -> str:
    """
    Generates and saves a high-quality 512x512 JPEG thumbnail (RGB only) for UI rendering.
    """
    rgb_hwc = np.transpose(tile.rgb_data, (1, 2, 0))
    img = Image.fromarray(rgb_hwc, mode="RGB")
    
    if img.size != thumb_size:
        img = img.resize(thumb_size, Image.Resampling.BILINEAR)
    
    img.save(str(output_path), format="JPEG", quality=quality, optimize=True)
    return str(output_path.as_posix())


def save_tiles_and_manifest(
    tiles: List[TileCandidate],
    region_id: str,
    scene_id: str,
    acquisition_date: str,
    aoi_geojson: Optional[Dict[str, Any]] = None,
    base_data_dir: Path = DEFAULT_DATA_DIR,
    source_type: str = "aoi_search",
    extra_properties: Optional[Dict[str, Any]] = None
) -> Path:
    """
    Persists all tiles to disk in the structured archive hierarchy and writes manifest.json.
    """
    date_str = format_date_dir(acquisition_date)
    out_dir = Path(base_data_dir) / "tiles" / region_id / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    tile_records = []

    for tile in tiles:
        tif_filename = f"{tile.tile_id}.tif"
        jpg_filename = f"{tile.tile_id}_thumb.jpg"
        tif_path = out_dir / tif_filename
        jpg_path = out_dir / jpg_filename

        save_tile_geotiff(tile, tif_path)
        save_tile_thumbnail(tile, jpg_path)

        tile_record = {
            "tile_id": tile.tile_id,
            "scene_id": tile.scene_id,
            "site_key": tile.site_key,
            "source_type": tile.source_type or source_type,
            "acquisition_date": acquisition_date,
            "centroid": {
                "latitude": tile.centroid_lat,
                "longitude": tile.centroid_lon
            },
            "bounds_epsg4326": list(tile.bounds),
            "geometry": mapping(tile.footprint_geom),
            "quality": {
                "cloud_pct": tile.cloud_pct,
                "quality_confidence": tile.quality_confidence,
                "passed_gate": bool(tile.quality_confidence >= 0.70)
            },
            "indices": {
                "mean_ndvi": tile.mean_ndvi,
                "mean_ndwi": tile.mean_ndwi,
                "mean_ndbi": tile.mean_ndbi
            },
            "bands": {
                "band_order": tile.band_order,
                "band_stats": tile.band_stats
            },
            "storage": {
                "geotiff_path": str(tif_path.resolve()),
                "thumbnail_path": str(jpg_path.resolve())
            }
        }
        tile_records.append(tile_record)

    manifest_payload = {
        "manifest_version": "2.0.0",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "region_id": region_id,
        "scene_id": scene_id,
        "acquisition_date": acquisition_date,
        "source_type": source_type,
        "total_tiles": len(tiles),
        "aoi_geometry": aoi_geojson,
        "metadata": extra_properties or {},
        "tiles": tile_records
    }

    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2)

    logger.info(f"[Phase 1.5] Manifest and {len(tiles)} tiles saved successfully to: {manifest_path}")
    return manifest_path


def save_intermediate_masks(
    cleaned_canvas: Any,
    region_id: str,
    scene_id: str,
    base_data_dir: Path = DEFAULT_DATA_DIR
) -> Dict[str, str]:
    """Saves full-canvas debug masks for quality audits."""
    out_dir = Path(base_data_dir) / "masks" / region_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cloud_path = out_dir / f"{scene_id}_cloud_mask.png"
    shadow_path = out_dir / f"{scene_id}_shadow_mask.png"
    bad_path = out_dir / f"{scene_id}_bad_mask.png"

    if hasattr(cleaned_canvas, "cloud_mask"):
        Image.fromarray((cleaned_canvas.cloud_mask * 255).astype(np.uint8)).save(cloud_path)
    if hasattr(cleaned_canvas, "shadow_mask"):
        Image.fromarray((cleaned_canvas.shadow_mask * 255).astype(np.uint8)).save(shadow_path)
    if hasattr(cleaned_canvas, "bad_mask"):
        Image.fromarray((cleaned_canvas.bad_mask * 255).astype(np.uint8)).save(bad_path)

    return {
        "cloud_mask": str(cloud_path.as_posix()),
        "shadow_mask": str(shadow_path.as_posix()),
        "bad_mask": str(bad_path.as_posix())
    }

