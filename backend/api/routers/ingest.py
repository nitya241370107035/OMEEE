"""
backend/api/routers/ingest.py
=============================
Ingestion API Router — provides endpoints for:
  1. Entry Point A: AOI Polygon with timeline (e.g. 1-10 years or date ranges)
  2. Entry Point B: Offline GeoTIFF / Evaluation File Ingestion
"""

import os
import shutil
import logging
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from backend.ingestion.pipeline import (
    run_aoi_ingestion_pipeline,
    run_direct_file_ingestion_pipeline,
    PipelineResult
)

logger = logging.getLogger("ingest_router")
router = APIRouter(prefix="/api/v1/ingest", tags=["Ingestion"])


class AOIIngestRequest(BaseModel):
    geojson_polygon: Dict[str, Any] = Field(..., description="GeoJSON Polygon geometry or Feature")
    region_id: Optional[str] = Field(None, description="Unique region identifier")
    region_name: Optional[str] = Field(None, description="Human-readable region name")
    date_from: Optional[str] = Field(None, description="Start date (YYYY-MM-DD)")
    date_to: Optional[str] = Field(None, description="End date (YYYY-MM-DD)")
    years_timeline: Optional[int] = Field(None, description="Years of historical timeline to query (e.g. 1 to 10)")
    num_time_buckets: int = Field(2, ge=1, le=10, description="Number of temporal buckets across the range")
    max_cloud_cover: float = Field(20.0, ge=0.0, le=100.0, description="Maximum cloud cover percentage")
    ground_crop_size: int = Field(512, description="Ground crop size in pixels")
    overlap_pct: float = Field(0.10, ge=0.0, le=0.5, description="Overlap percentage between tiles")
    populate_db: bool = Field(True, description="Whether to upsert into Postgres and Qdrant")


class FileIngestRequest(BaseModel):
    file_path: str = Field(..., description="Absolute or relative path to local GeoTIFF file")
    region_id: Optional[str] = Field(None, description="Region ID")
    custom_band_order: Optional[List[str]] = Field(None, description="Custom band order e.g. ['blue', 'green', 'red', 'nir', 'swir']")
    acquisition_date: Optional[str] = Field(None, description="Acquisition date (YYYY-MM-DD)")
    ground_crop_size: int = Field(512, description="Ground crop size in pixels")
    overlap_pct: float = Field(0.10, ge=0.0, le=0.5, description="Overlap percentage between tiles")
    populate_db: bool = Field(True, description="Whether to upsert into Postgres and Qdrant")


@router.post("/aoi", response_model=Dict[str, Any])
def ingest_aoi(request: AOIIngestRequest):
    """
    Entry Point A: Ingests an AOI polygon across the requested timeline.
    Queries STAC for clear Sentinel-2 scenes, processes 5-band canvas,
    masks clouds/shadows, normalizes, slices 512x512 tiles, computes RemoteCLIP embeddings,
    and updates PostgreSQL and Qdrant.
    """
    try:
        # Determine date range
        today = date.today()
        if request.years_timeline and request.years_timeline > 0:
            start_year = today.year - request.years_timeline
            date_from = f"{start_year}-01-01"
            date_to = today.isoformat()
            # If multi-year, scale time buckets appropriately
            num_buckets = max(request.num_time_buckets, min(request.years_timeline, 8))
        else:
            date_from = request.date_from or "2023-01-01"
            date_to = request.date_to or today.isoformat()
            num_buckets = request.num_time_buckets

        region_id = request.region_id or f"region_aoi_{uuid.uuid4().hex[:8]}"

        logger.info(f"Starting AOI ingestion for region '{region_id}' from {date_from} to {date_to} ({num_buckets} buckets)")

        result: PipelineResult = run_aoi_ingestion_pipeline(
            geojson_input=request.geojson_polygon,
            date_from=date_from,
            date_to=date_to,
            region_id=region_id,
            region_name=request.region_name,
            num_time_buckets=num_buckets,
            max_cloud_cover=request.max_cloud_cover,
            ground_crop_size=request.ground_crop_size,
            overlap_pct=request.overlap_pct,
            populate_db=request.populate_db,
            offline_fallback=True
        )

        return {
            "status": "success",
            "region_id": result.region_id,
            "source_type": result.source_type,
            "total_tiles_generated": result.total_tiles_generated,
            "scenes_processed": [
                {
                    "scene_id": s.scene_id,
                    "time_bucket": s.time_bucket,
                    "acquisition_date": s.acquisition_date,
                    "tiles_count": s.tiles_count,
                    "cloud_pct": s.cloud_pct,
                    "manifest_path": s.manifest_path
                }
                for s in result.scenes_processed
            ],
            "elapsed_seconds": result.elapsed_seconds,
            "is_offline": result.is_offline
        }

    except Exception as e:
        logger.error(f"AOI Ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AOI Ingestion failed: {str(e)}")


@router.post("/file", response_model=Dict[str, Any])
def ingest_file(request: FileIngestRequest):
    """
    Entry Point B: Ingests an evaluation / organiser GeoTIFF file directly (100% offline).
    """
    try:
        result: PipelineResult = run_direct_file_ingestion_pipeline(
            file_path=request.file_path,
            region_id=request.region_id,
            custom_band_order=request.custom_band_order,
            acquisition_date=request.acquisition_date,
            ground_crop_size=request.ground_crop_size,
            overlap_pct=request.overlap_pct,
            populate_db=request.populate_db
        )

        return {
            "status": "success",
            "region_id": result.region_id,
            "source_type": result.source_type,
            "total_tiles_generated": result.total_tiles_generated,
            "scenes_processed": [
                {
                    "scene_id": s.scene_id,
                    "time_bucket": s.time_bucket,
                    "acquisition_date": s.acquisition_date,
                    "tiles_count": s.tiles_count,
                    "cloud_pct": s.cloud_pct,
                    "manifest_path": s.manifest_path
                }
                for s in result.scenes_processed
            ],
            "elapsed_seconds": result.elapsed_seconds,
            "is_offline": result.is_offline
        }
    except Exception as e:
        logger.error(f"File Ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"File Ingestion failed: {str(e)}")


@router.post("/upload", response_model=Dict[str, Any])
async def upload_and_ingest_file(
    file: UploadFile = File(...),
    region_id: Optional[str] = Form(None),
    custom_band_order: Optional[str] = Form(None),
    acquisition_date: Optional[str] = Form(None)
):
    """
    Uploads a local GeoTIFF file to temporary storage and runs Entry Point B ingestion.
    """
    try:
        temp_dir = Path("data/uploads")
        temp_dir.mkdir(parents=True, exist_ok=True)
        saved_file_path = temp_dir / file.filename

        with open(saved_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        band_order_list = [b.strip() for b in custom_band_order.split(",")] if custom_band_order else None

        result = run_direct_file_ingestion_pipeline(
            file_path=saved_file_path,
            region_id=region_id or f"upload_{Path(file.filename).stem}",
            custom_band_order=band_order_list,
            acquisition_date=acquisition_date,
            populate_db=True
        )

        return {
            "status": "success",
            "region_id": result.region_id,
            "source_type": "file_upload",
            "total_tiles_generated": result.total_tiles_generated,
            "elapsed_seconds": result.elapsed_seconds
        }
    except Exception as e:
        logger.error(f"Upload Ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload Ingestion failed: {str(e)}")
