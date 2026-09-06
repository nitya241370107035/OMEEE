"""
backend/maxar_ingestion
=======================
Dedicated High-Resolution Optical Ingestion Pipeline for Maxar WorldView imagery.
Provides Wayback WMTS fetching, 512x512 tiling, VARI visible index calculation,
PostgreSQL metadata persistence, and isolated Qdrant vector indexing.
"""

from backend.maxar_ingestion.fetcher import (
    fetch_stitched_bbox_imagery,
    get_closest_release,
    WAYBACK_RELEASES
)
from backend.maxar_ingestion.tiler import (
    slice_maxar_canvas_into_tiles,
    calculate_vari_index
)
from backend.maxar_ingestion.pipeline import (
    run_maxar_ingestion_pipeline,
    MaxarPipelineResult
)

__all__ = [
    "fetch_stitched_bbox_imagery",
    "get_closest_release",
    "WAYBACK_RELEASES",
    "slice_maxar_canvas_into_tiles",
    "calculate_vari_index",
    "run_maxar_ingestion_pipeline",
    "MaxarPipelineResult"
]
