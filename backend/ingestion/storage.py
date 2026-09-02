"""
Phase 1.6: Tile Storage & Manifest Generation

Saves tiles to the standardized directory layout:
  data/tiles/{region_id}/{date}/
    ├── {tile_id}.tif          (GeoTIFF with EPSG:4326 metadata)
    ├── {tile_id}_thumb.jpg    (Fast visual preview thumbnail)
    └── manifest.json          (Full metadata & quality manifest)

Computes honest quality_confidence scores based on real cloud/shadow percentages,
while tagging registration_residual as an explicit placeholder for Phase 2 change pairing.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
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


def format_date_dir(date_iso: str) -> str:
    """Formats an ISO-8601 date string to YYYY-MM-DD for folder naming."""
    try:
        dt = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        # Fallback if already date or unusual format
        return date_iso[:10].replace(":", "-")


def save_tile_geotiff(
    tile: TileCandidate,
    output_path: Path,
    crs: str = "EPSG:4326"
) -> str:
    """
    Saves RGB tile as a 3-band GeoTIFF with georeferencing metadata.
    """
    num_bands, height, width = tile.rgb_data.shape
    
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=num_bands,
        dtype=tile.rgb_data.dtype,
        crs=CRS.from_string(crs),
        transform=tile.transform,
        compress="lzw"
    ) as dst:
        dst.write(tile.rgb_data)

    return str(output_path.as_posix())


def save_tile_thumbnail(
    tile: TileCandidate,
    output_path: Path,
    thumb_size: Tuple[int, int] = (512, 512),
    quality: int = 95
) -> str:
    """
    Generates and saves a high-quality 512x512 JPEG thumbnail for map rendering & UI display.
    """
    # Transpose (3, H, W) to (H, W, 3)
    rgb_hwc = np.transpose(tile.rgb_data, (1, 2, 0))
    img = Image.fromarray(rgb_hwc, mode="RGB")
    
    if img.size != thumb_size:
        img = img.resize(thumb_size, Image.Resampling.LANCZOS)
    
    img.save(output_path, format="JPEG", quality=quality, optimize=True)
    return str(output_path.as_posix())


def save_tiles_and_manifest(
    tiles: List[TileCandidate],
    aoi: ValidatedAOI,
    scene_meta: STACSceneMetadata,
    region_id: str,
    base_data_dir: Path = DEFAULT_DATA_DIR
) -> Path:
    """
    Persists all tiles to disk in the structured archive hierarchy and writes manifest.json.

    Args:
        tiles: List of TileCandidate objects from Phase 1.5.
        aoi: ValidatedAOI from Phase 1.0.
        scene_meta: STACSceneMetadata from Phase 1.1.
        region_id: Unique string identifier for the geographical region / project.
        base_data_dir: Root storage path (default 'data/').

    Returns:
        Path: Absolute path to the generated manifest.json file.
    """
    date_str = format_date_dir(scene_meta.acquisition_date)
    out_dir = Path(base_data_dir) / "tiles" / region_id / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    tiles_records: List[Dict[str, Any]] = []

    for tile in tiles:
        tif_filename = f"{tile.tile_id}.tif"
        jpg_filename = f"{tile.tile_id}_thumb.jpg"
        
        tif_path = out_dir / tif_filename
        jpg_path = out_dir / jpg_filename

        # Write GeoTIFF & Thumbnail
        save_tile_geotiff(tile, tif_path, crs="EPSG:4326")
        save_tile_thumbnail(tile, jpg_path)

        # Honest quality confidence calculation based on real cloud percentage
        # Gates downstream retrieval & change detection
        quality_confidence = round(max(0.0, 1.0 - tile.cloud_pct), 4)

        tile_record = {
            "tile_id": tile.tile_id,
            "scene_id": tile.scene_id,
            "site_key": tile.site_key,
            "geometry": mapping(tile.footprint_geom),
            "centroid_lat": tile.centroid_lat,
            "centroid_lon": tile.centroid_lon,
            "acquisition_date": scene_meta.acquisition_date,
            "sensor": scene_meta.sensor,
            "cloud_pct": tile.cloud_pct,
            "registration_residual": 0.0,  # Explicit placeholder until 2-date pairing
            "registration_residual_is_placeholder": True,
            "quality_confidence": quality_confidence,
            "quality_is_placeholder": False,  # Cloud & quality confidence are genuine
            "ground_crop_size": tile.ground_crop_size,
            "is_upsampled": tile.is_upsampled,
            "file_path": str(tif_path.as_posix()),
            "thumbnail_path": str(jpg_path.as_posix())
        }
        tiles_records.append(tile_record)

    # Manifest dictionary
    manifest_data = {
        "manifest_version": "1.0",
        "region_id": region_id,
        "date": date_str,
        "ingested_at": datetime.utcnow().isoformat() + "Z",
        "total_tiles": len(tiles_records),
        "ground_crop_size": tiles[0].ground_crop_size if tiles else 512,
        "is_upsampled": tiles[0].is_upsampled if tiles else False,
        "aoi": {
            "bbox": aoi.bbox,
            "area_km2": aoi.area_km2,
            "geometry": mapping(aoi.polygon)
        },
        "scene": {
            "scene_id": scene_meta.scene_id,
            "acquisition_date": scene_meta.acquisition_date,
            "sensor": scene_meta.sensor,
            "crs": scene_meta.crs,
            "cloud_cover": scene_meta.cloud_cover,
            "source": scene_meta.source,
            "is_mock": scene_meta.is_mock
        },
        "tiles": tiles_records
    }

    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    logger.info(
        f"Archive saved: {len(tiles_records)} tiles written to {out_dir} "
        f"(manifest: {manifest_path.name})"
    )

    return manifest_path
