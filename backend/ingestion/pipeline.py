"""
backend/ingestion/pipeline.py
=============================
Phase 1: End-to-End Multi-Temporal Ingestion & Preprocessing Pipeline
=====================================================================
PS Sections: 2.2.1, 2.2.2, 2.2.3, 2.2.6, 2.2.7

Master Pipeline Orchestrator connecting:
  - Phase 1.0: Dual Entry Point Validation (input_validator.py)
  - Phase 1.1: Multi-Temporal STAC Search (stac_search.py)
  - Phase 1.2: 5-Band Working Canvas Assembly (canvas.py)
  - Phase 1.3: Spectral Cloud/Shadow Masking & Normalization (masking)
  - Phase 1.4: 512x512 Tiling & Spectral Indices NDVI/NDWI/NDBI (tiler.py)
  - Phase 1.5: Sovereign Disk Storage & Manifest (storage.py)
  - Phase 1.6 & 1.7: Postgres Database Upserts (db_writer.py)
"""

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Union, Dict, Any, Optional, List
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.input_validator import (
    validate_aoi_input,
    validate_direct_file_input,
    ValidatedAOIInput,
    ValidatedFileInput,
)
from backend.ingestion.stac_search import (
    search_multi_temporal_stac_scenes,
    STACSceneMetadata,
)
from backend.ingestion.canvas import (
    assemble_canvas_from_stac,
    assemble_mosaicked_canvas_from_stac,
    assemble_canvas_from_file,
    CanvasData,
)
from backend.ingestion.masking import clean_and_normalize_canvas, CleanedCanvas
from backend.ingestion.tiler import slice_and_filter_tiles, TileCandidate, DEFAULT_GROUND_CROP_SIZE
from backend.ingestion.storage import save_tiles_and_manifest
from backend.ingestion.db_writer import (
    upsert_scene,
    upsert_tiles,
    update_coverage_status,
    ensure_schema_migrated,
)
from backend.services.vector_store import encode_and_upsert_tiles_to_qdrant

logger = logging.getLogger("ingestion_pipeline")


@dataclass
class SceneIngestionSummary:
    """Summary of a single scene processed through the pipeline."""
    scene_id: str
    time_bucket: Optional[str]
    acquisition_date: str
    tiles_count: int
    manifest_path: str
    cloud_pct: float


@dataclass
class PipelineResult:
    """Full execution summary across all processed scenes/files."""
    region_id: str
    source_type: str
    total_tiles_generated: int
    scenes_processed: List[SceneIngestionSummary]
    elapsed_seconds: float
    is_offline: bool


# ============================================================
# 1. Entry Point A Orchestrator (AOI + Multi-Temporal STAC)
# ============================================================

def run_aoi_ingestion_pipeline(
    geojson_input: Union[str, Path, Dict[str, Any]],
    date_from: str = "2023-01-01",
    date_to: str = "2024-06-30",
    region_id: str = "custom_region",
    region_name: Optional[str] = None,
    num_time_buckets: int = 2,
    max_cloud_cover: float = 15.0,
    ground_crop_size: int = DEFAULT_GROUND_CROP_SIZE,
    overlap_pct: float = 0.10,
    base_data_dir: str = "data",
    populate_db: bool = True,
    offline_fallback: bool = True,
    fetch_10_bands: bool = True
) -> PipelineResult:
    """
    Executes Entry Point A:
      1. Validates AOI GeoJSON + Date Range
      2. Runs Multi-Temporal STAC Search across time buckets (T1 vs T2)
      3. For each time bucket:
         - Streams 10 bands (or 5 core bands) & reprojects to EPSG:4326
         - Merges overlapping scene granules into a continuous working mosaic
         - Masks clouds/shadows & normalizes dynamic range
         - Slices 512x512 tiles & computes NDVI, NDWI, NDBI
         - Saves .tif, .jpg, and manifest.json to disk
         - Upserts scenes & tiles into Postgres (with mosaicked_scenes provenance)
    """
    t0 = time.time()
    logger.info("=" * 75)
    logger.info(f"STARTING ENTRY POINT A INGESTION: region='{region_id}' ({date_from} to {date_to})")
    logger.info(f"Time Buckets: {num_time_buckets} | Max Cloud: {max_cloud_cover}% | 10-Bands: {fetch_10_bands}")
    logger.info("=" * 75)

    # Phase 1.0: Validate AOI Input
    validated_input: ValidatedAOIInput = validate_aoi_input(
        geojson_input=geojson_input,
        date_from=date_from,
        date_to=date_to,
        region_id=region_id,
        max_cloud_cover=max_cloud_cover
    )
    aoi = validated_input.aoi

    # Phase 1.1: Multi-Temporal STAC Search
    scenes: List[STACSceneMetadata] = search_multi_temporal_stac_scenes(
        bbox=aoi.bbox,
        date_from=date_from,
        date_to=date_to,
        max_cloud_cover=max_cloud_cover,
        num_buckets=num_time_buckets,
        offline_fallback=offline_fallback
    )

    # Group scenes by time bucket for multi-scene mosaicking
    bucket_map: Dict[str, List[STACSceneMetadata]] = {}
    for scene in scenes:
        b_id = scene.time_bucket or "default_bucket"
        if b_id not in bucket_map:
            bucket_map[b_id] = []
        bucket_map[b_id].append(scene)

    scenes_summary: List[SceneIngestionSummary] = []
    total_tiles = 0

    for bucket_id, bucket_scenes in bucket_map.items():
        primary_scene = bucket_scenes[0]
        scene_ids_str = ", ".join([s.scene_id for s in bucket_scenes])
        logger.info(f"\n--- Processing Time Bucket: {bucket_id} ({len(bucket_scenes)} scene(s): {scene_ids_str}) ---")

        # Phase 1.2: Multi-Scene Mosaicked Canvas Assembly
        canvas: CanvasData = assemble_mosaicked_canvas_from_stac(
            scenes=bucket_scenes,
            aoi_bbox=aoi.bbox,
            buffer_pct=0.05,
            fetch_10_bands=fetch_10_bands
        )

        # Phase 1.3: Quality Handling & Masking
        cleaned: CleanedCanvas = clean_and_normalize_canvas(canvas)

        # Phase 1.4: Tiling & Indices
        tiles: List[TileCandidate] = slice_and_filter_tiles(
            canvas_data=canvas,
            cleaned_canvas=cleaned,
            scene_id=primary_scene.scene_id,
            aoi_polygon=aoi.polygon,
            ground_crop_size=ground_crop_size,
            overlap_pct=overlap_pct,
            source_type="aoi_search"
        )

        # Phase 1.5: Disk Storage & Manifest
        manifest_path = save_tiles_and_manifest(
            tiles=tiles,
            region_id=region_id,
            scene_id=primary_scene.scene_id,
            acquisition_date=primary_scene.acquisition_date,
            aoi_geojson=aoi.raw_geojson,
            base_data_dir=Path(base_data_dir),
            source_type="aoi_search",
            extra_properties={
                "time_bucket": bucket_id,
                "cloud_cover": primary_scene.cloud_cover,
                "mosaicked_scenes": [s.scene_id for s in bucket_scenes]
            }
        )

        # Phase 1.6, 1.7 & 1.8: Postgres & Qdrant Upserts
        if populate_db:
            try:
                for s_item in bucket_scenes:
                    # Compute scene's true geographic coverage footprint polygon
                    scene_poly = box(s_item.bbox[0], s_item.bbox[1], s_item.bbox[2], s_item.bbox[3]) if s_item.bbox else aoi.polygon
                    upsert_scene(
                        scene_id=s_item.scene_id,
                        source=s_item.source,
                        acquisition_date=s_item.acquisition_date,
                        crs=s_item.crs,
                        footprint_geom=scene_poly,
                        raw_file_path=s_item.assets.red or s_item.assets.b04,
                        provenance={"time_bucket": bucket_id, "sensor": s_item.sensor}
                    )
                upsert_tiles(
                    tiles=tiles,
                    scene_id=primary_scene.scene_id,
                    acquisition_date=primary_scene.acquisition_date,
                    sensor=primary_scene.sensor,
                    base_data_dir=base_data_dir,
                    region_id=region_id,
                    mosaicked_scenes=[s.scene_id for s in bucket_scenes]
                )

                # Phase 1.8: RemoteCLIP Embedding & Qdrant Vector Upsert
                try:
                    encode_and_upsert_tiles_to_qdrant(
                        tiles=tiles,
                        scene_id=primary_scene.scene_id,
                        acquisition_date=primary_scene.acquisition_date,
                        sensor=primary_scene.sensor,
                        region_id=region_id
                    )
                except Exception as q_err:
                    logger.warning(f"[Phase 1.8] Qdrant embedding upsert warning: {q_err}")
            except Exception as e:
                logger.warning(f"[Phase 1.7/1.8] Database/Vector upsert skipped or encountered error: {e}")

        total_tiles += len(tiles)
        scenes_summary.append(
            SceneIngestionSummary(
                scene_id=primary_scene.scene_id,
                time_bucket=bucket_id,
                acquisition_date=primary_scene.acquisition_date,
                tiles_count=len(tiles),
                manifest_path=str(manifest_path.resolve()),
                cloud_pct=cleaned.canvas_cloud_pct
            )
        )

    # Update Coverage
    if populate_db:
        try:
            update_coverage_status(
                region_id=region_id,
                region_name=region_name or f"Region {region_id}",
                aoi_geom=aoi.polygon,
                tile_count=total_tiles,
                status="done"
            )
        except Exception as e:
            logger.warning(f"[Phase 1.7] Coverage update warning: {e}")

    elapsed = round(time.time() - t0, 2)
    logger.info("=" * 75)
    logger.info(f"ENTRY POINT A INGESTION COMPLETE: {total_tiles} Tiles Across {len(scenes)} Scenes in {elapsed}s")
    logger.info("=" * 75)

    return PipelineResult(
        region_id=region_id,
        source_type="aoi_search",
        total_tiles_generated=total_tiles,
        scenes_processed=scenes_summary,
        elapsed_seconds=elapsed,
        is_offline=False
    )


# ============================================================
# 2. Entry Point B Orchestrator (Direct Local GeoTIFF — Offline)
# ============================================================

def run_direct_file_ingestion_pipeline(
    file_path: Union[str, Path],
    region_id: Optional[str] = None,
    custom_band_order: Optional[List[str]] = None,
    acquisition_date: Optional[str] = None,
    ground_crop_size: int = DEFAULT_GROUND_CROP_SIZE,
    overlap_pct: float = 0.10,
    base_data_dir: str = "data",
    populate_db: bool = True
) -> PipelineResult:
    """
    Executes Entry Point B (Evaluation Ingestion — 100% Offline):
      1. Validates local GeoTIFF file metadata (CRS, bounds, bands)
      2. Reads bands directly into working canvas (reprojecting to EPSG:4326)
      3. Cleans, masks clouds/shadows, normalizes dynamic range
      4. Slices 512x512 tiles & computes NDVI, NDWI, NDBI
      5. Saves .tif, .jpg, and manifest.json
      6. Upserts into Postgres
    """
    t0 = time.time()
    logger.info("=" * 75)
    logger.info(f"STARTING ENTRY POINT B (OFFLINE FILE INGESTION): file='{file_path}'")
    logger.info("=" * 75)

    # Phase 1.0: Validate File Metadata
    file_input: ValidatedFileInput = validate_direct_file_input(
        file_path=file_path,
        region_id=region_id,
        custom_band_order=custom_band_order,
        acquisition_date=acquisition_date
    )

    scene_id = file_input.file_path.stem
    acq_date_str = (
        file_input.acquisition_date.isoformat()
        if file_input.acquisition_date
        else datetime.now(timezone.utc).isoformat()
    )

    # Phase 1.2: Canvas Assembly
    canvas: CanvasData = assemble_canvas_from_file(file_input)

    # Phase 1.3: Quality Handling & Masking
    cleaned: CleanedCanvas = clean_and_normalize_canvas(canvas)

    # Phase 1.4: Tiling & Indices
    tiles: List[TileCandidate] = slice_and_filter_tiles(
        canvas_data=canvas,
        cleaned_canvas=cleaned,
        scene_id=scene_id,
        aoi_polygon=None,  # Keep all valid tiles from evaluation file
        ground_crop_size=ground_crop_size,
        overlap_pct=overlap_pct,
        source_type="organiser_provided"
    )

    # Phase 1.5: Disk Storage & Manifest
    manifest_path = save_tiles_and_manifest(
        tiles=tiles,
        region_id=file_input.region_id,
        scene_id=scene_id,
        acquisition_date=acq_date_str,
        aoi_geojson=None,
        base_data_dir=Path(base_data_dir),
        source_type="organiser_provided",
        extra_properties={"original_file": str(file_input.file_path), "crs": file_input.crs}
    )

    # Phase 1.6 & 1.7: Postgres Upserts
    if populate_db:
        try:
            upsert_scene(
                scene_id=scene_id,
                source="organiser_evaluation_file",
                acquisition_date=acq_date_str,
                crs=file_input.crs,
                footprint_geom=file_input.extent_polygon,
                raw_file_path=str(file_input.file_path),
                provenance={"source_type": "organiser_provided", "bands": file_input.band_order}
            )
            upsert_tiles(
                tiles=tiles,
                scene_id=scene_id,
                acquisition_date=acq_date_str,
                sensor="Organiser Sensor",
                base_data_dir=base_data_dir,
                region_id=file_input.region_id
            )
            update_coverage_status(
                region_id=file_input.region_id,
                region_name=f"Evaluation File ({scene_id})",
                aoi_geom=file_input.extent_polygon,
                tile_count=len(tiles),
                status="done"
            )

            # Phase 1.8: RemoteCLIP Embedding & Qdrant Vector Upsert
            try:
                encode_and_upsert_tiles_to_qdrant(
                    tiles=tiles,
                    scene_id=scene_id,
                    acquisition_date=acq_date_str,
                    sensor="Organiser Sensor",
                    region_id=file_input.region_id
                )
            except Exception as q_err:
                logger.warning(f"[Phase 1.8] Qdrant embedding upsert warning for file ingestion: {q_err}")
        except Exception as e:
            logger.warning(f"[Phase 1.7/1.8] Database/Vector upsert warning for file ingestion: {e}")

    elapsed = round(time.time() - t0, 2)
    logger.info("=" * 75)
    logger.info(f"ENTRY POINT B INGESTION COMPLETE: {len(tiles)} Tiles Generated in {elapsed}s")
    logger.info("=" * 75)

    return PipelineResult(
        region_id=file_input.region_id,
        source_type="organiser_provided",
        total_tiles_generated=len(tiles),
        scenes_processed=[
            SceneIngestionSummary(
                scene_id=scene_id,
                time_bucket="evaluation_file",
                acquisition_date=acq_date_str,
                tiles_count=len(tiles),
                manifest_path=str(manifest_path.resolve()),
                cloud_pct=cleaned.canvas_cloud_pct
            )
        ],
        elapsed_seconds=elapsed,
        is_offline=True
    )


# Backward compatibility alias
def run_aoi_pipeline(*args, **kwargs):
    return run_aoi_ingestion_pipeline(*args, **kwargs)
