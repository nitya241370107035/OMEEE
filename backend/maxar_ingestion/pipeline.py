"""
backend/maxar_ingestion/pipeline.py
===================================
End-to-End Ingestion Pipeline for Sub-Meter Maxar High-Resolution Satellite Imagery.
Bypasses multi-spectral masks, fetches Wayback orthorectified mosaics across historical epochs,
slices 512x512 georeferenced tiles, computes VARI index, saves to PostgreSQL tiles table,
and indexes into dedicated Qdrant collection 'maxar_tile_embeddings'.
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
import numpy as np
from PIL import Image
import rasterio
from shapely.geometry import shape, box, Polygon, mapping

from backend.maxar_ingestion.fetcher import (
    fetch_stitched_bbox_imagery,
    get_closest_release,
    auto_select_zoom,
    WAYBACK_RELEASES
)
from backend.maxar_ingestion.tiler import (
    slice_maxar_canvas_into_tiles,
    calculate_vari_index
)
from backend.ingestion.db_writer import (
    upsert_scene,
    upsert_tiles,
    get_pg_connection,
    update_coverage
)
from backend.services.vector_store import (
    encode_and_upsert_tiles_to_qdrant,
    ensure_collection_exists,
    MAXAR_COLLECTION_NAME
)
from backend.ingestion.storage import (
    save_tile_thumbnail,
    format_date_dir
)

logger = logging.getLogger("MaxarPipeline")


@dataclass
class MaxarPipelineResult:
    region_id: str
    epochs_processed: List[Dict[str, Any]] = field(default_factory=list)
    total_tiles_generated: int = 0
    tiles_upserted_postgres: int = 0
    vectors_upserted_qdrant: int = 0
    execution_time_seconds: float = 0.0
    status: str = "success"
    errors: List[str] = field(default_factory=list)


def save_maxar_geotiff(
    tile_candidate,
    output_path: Path
) -> str:
    """Saves a 3-band uint8 Maxar RGB tile with EPSG:4326 transform."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    arr = tile_candidate.rgb_data  # (3, 512, 512) uint8
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=tile_candidate.multiband_data.shape[1],
        width=tile_candidate.multiband_data.shape[2],
        count=3,
        dtype=arr.dtype,
        crs="EPSG:4326",
        transform=tile_candidate.transform,
        compress="lzw"
    ) as dst:
        dst.write(arr)
    return str(output_path)


def run_maxar_ingestion_pipeline(
    geojson_polygon: Dict[str, Any],
    region_id: str = "maxar_aoi",
    region_name: Optional[str] = None,
    years: Optional[List[int]] = None,
    zoom_level: int = 16,
    overlap_pct: float = 0.10,
    base_data_dir: Union[str, Path] = "data",
    populate_db: bool = True
) -> MaxarPipelineResult:
    """
    Executes high-resolution Maxar optical ingestion across requested historical epochs.
    1. Extracts polygon bounding box.
    2. Iterates over years (default: 2020 and 2026 for multi-temporal baseline vs contemporary).
    3. Fetches stitched sub-meter imagery from Wayback WMTS with adaptive zoom.
    4. Slices georeferenced 512x512 tiles with VARI vegetation index and full polygon coverage.
    5. Saves 3-band GeoTIFF + RGB thumbnail previews.
    6. Persists scene and tile metadata in PostgreSQL 'scenes' and 'tiles' tables.
    7. Encodes tiles with RemoteCLIP and indexes into Qdrant 'maxar_tile_embeddings'.
    8. Updates 'ingestion_coverage' so the region appears immediately on the frontend map.
    """
    start_time = datetime.utcnow()
    result = MaxarPipelineResult(region_id=region_id)
    target_years = years or [2020, 2026]
    base_dir = Path(base_data_dir)

    # 1. Parse AOI Polygon and Bounding Box
    try:
        if isinstance(geojson_polygon, str):
            import json
            geojson_polygon = json.loads(geojson_polygon)

        if isinstance(geojson_polygon, dict):
            if geojson_polygon.get("type") == "FeatureCollection":
                features = geojson_polygon.get("features", [])
                if not features:
                    raise ValueError("GeoJSON FeatureCollection contains no features.")
                geom_dict = features[0].get("geometry", features[0])
            elif geojson_polygon.get("type") == "Feature":
                geom_dict = geojson_polygon.get("geometry", geojson_polygon)
            else:
                geom_dict = geojson_polygon
        else:
            geom_dict = geojson_polygon

        geom = shape(geom_dict)
        min_lon, min_lat, max_lon, max_lat = geom.bounds

        # Check for flipped coordinates [lat, lon]
        if min_lat < -90 or max_lat > 90 or min_lon < -180 or max_lon > 180:
            from shapely.ops import transform
            geom = transform(lambda x, y: (y, x), geom)
            min_lon, min_lat, max_lon, max_lat = geom.bounds

        bbox = (float(min_lon), float(min_lat), float(max_lon), float(max_lat))
    except Exception as e:
        err = f"Invalid GeoJSON polygon: {e}"
        logger.error(err, exc_info=True)
        result.status = "failed"
        result.errors.append(err)
        return result

    # Automatically adapt zoom level for high resolution if small/tactical area
    eff_zoom = auto_select_zoom(bbox, requested_zoom=zoom_level)
    logger.info(f"Starting Maxar Ingestion Pipeline for region '{region_id}' over bbox {bbox} at zoom {eff_zoom} for epochs {target_years}")

    # Ensure Qdrant Maxar collection exists if DB population is active
    if populate_db:
        try:
            ensure_collection_exists(collection_name=MAXAR_COLLECTION_NAME)
        except Exception as qe:
            logger.warning(f"Could not connect to Qdrant or ensure collection: {qe}")

    # 2. Iterate through each epoch
    for year in target_years:
        rel_info = get_closest_release(year)
        release_id = rel_info["release_id"]
        acq_date = rel_info["date"]
        label = rel_info["label"]
        scene_id = f"maxar_wayback_{release_id}_{region_id}_{year}"

        logger.info(f"Processing Maxar epoch {year} (Release {release_id} - {label}) for region {region_id}")

        try:
            # Fetch stitched optical imagery
            stitched_img = fetch_stitched_bbox_imagery(bbox, release_id=release_id, zoom=eff_zoom)

            # Slice into 512x512 tiles with full coverage and spatial polygon intersection
            tiles = slice_maxar_canvas_into_tiles(
                stitched_img=stitched_img,
                bbox=bbox,
                scene_id=scene_id,
                region_id=region_id,
                aoi_polygon=geom,
                overlap_pct=overlap_pct
            )
            result.total_tiles_generated += len(tiles)

            # Date folder for disk storage
            date_folder = format_date_dir(acq_date)
            tiles_dir = base_dir / "tiles" / region_id / date_folder
            tiles_dir.mkdir(parents=True, exist_ok=True)

            # Save GeoTIFFs and Thumbnails
            for tile in tiles:
                tif_path = tiles_dir / f"{tile.tile_id}.tif"
                thumb_path = tiles_dir / f"{tile.tile_id}_thumb.jpg"
                save_maxar_geotiff(tile, tif_path)
                save_tile_thumbnail(tile, thumb_path)
                tile.file_path = str(tif_path)
                tile.thumbnail_path = str(thumb_path)

            # Database & Vector Store Upsert
            if populate_db and tiles:
                # 1. Upsert Scene
                try:
                    upsert_scene(
                        scene_id=scene_id,
                        source="maxar_wayback",
                        acquisition_date=acq_date,
                        crs="EPSG:4326",
                        footprint_geom=box(*bbox),
                        raw_file_path=str(tiles_dir),
                        provenance={
                            "provider": "Maxar / Esri Wayback",
                            "release_id": release_id,
                            "release_label": label,
                            "zoom_level": zoom_level,
                            "resolution": "sub-meter optical"
                        }
                    )
                except Exception as se:
                    logger.error(f"Scene upsert error: {se}", exc_info=True)
                    result.errors.append(f"Scene upsert error: {se}")

                # 2. Upsert Tiles into Postgres
                try:
                    upserted_count = upsert_tiles(
                        tiles=tiles,
                        scene_id=scene_id,
                        acquisition_date=acq_date,
                        sensor="Maxar WorldView / Wayback",
                        base_data_dir=str(base_dir),
                        region_id=region_id
                    )
                    result.tiles_upserted_postgres += upserted_count
                except Exception as te:
                    logger.error(f"Failed to upsert tiles into Postgres: {te}")
                    result.errors.append(f"Postgres upsert error: {te}")

                # 3. Vector Embeddings into Qdrant 'maxar_tile_embeddings'
                try:
                    q_count = encode_and_upsert_tiles_to_qdrant(
                        tiles=tiles,
                        scene_id=scene_id,
                        acquisition_date=acq_date,
                        sensor="Maxar WorldView",
                        region_id=region_id,
                        collection_name=MAXAR_COLLECTION_NAME
                    )
                    result.vectors_upserted_qdrant += q_count
                except Exception as ve:
                    logger.error(f"Failed to upsert embeddings into Qdrant '{MAXAR_COLLECTION_NAME}': {ve}")
                    result.errors.append(f"Qdrant upsert error: {ve}")

            result.epochs_processed.append({
                "year": year,
                "release_id": release_id,
                "label": label,
                "acquisition_date": acq_date,
                "tiles_count": len(tiles)
            })

        except Exception as epoch_err:
            logger.error(f"Error processing epoch {year}: {epoch_err}", exc_info=True)
    # 3. Register Region into Ingestion Coverage Table for Map & UI Visibility
    if populate_db and result.tiles_upserted_postgres > 0:
        try:
            disp_name = region_name or f"Maxar {region_id}"
            update_coverage(
                region_id=region_id,
                region_name=disp_name,
                aoi_geom=geom,
                tile_count=result.tiles_upserted_postgres,
                status="done"
            )
            logger.info(f"Successfully updated ingestion_coverage for Maxar region '{region_id}' ({result.tiles_upserted_postgres} tiles).")
        except Exception as ce:
            logger.error(f"Failed to update ingestion_coverage: {ce}", exc_info=True)
            result.errors.append(f"Coverage update error: {ce}")

    end_time = datetime.utcnow()
    result.execution_time_seconds = round((end_time - start_time).total_seconds(), 2)
    logger.info(
        f"Maxar Ingestion completed for '{region_id}' in {result.execution_time_seconds}s. "
        f"Tiles generated: {result.total_tiles_generated}, "
        f"Postgres: {result.tiles_upserted_postgres}, "
        f"Qdrant '{MAXAR_COLLECTION_NAME}': {result.vectors_upserted_qdrant}"
    )
    return result
