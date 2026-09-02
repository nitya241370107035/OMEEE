"""
Ingestion Package for AOI Processing, Tiling, and Preprocessing.
"""

from backend.ingestion.input_validator import validate_aoi, ValidatedAOI
from backend.ingestion.stac_search import search_stac_scene, STACSceneMetadata
from backend.ingestion.canvas import assemble_working_canvas, CanvasData
from backend.ingestion.masking import clean_and_normalize_canvas, CleanedCanvas
from backend.ingestion.tiler import slice_and_filter_tiles, TileCandidate, DEFAULT_GROUND_CROP_SIZE
from backend.ingestion.storage import save_tiles_and_manifest
from backend.ingestion.pipeline import run_aoi_pipeline, PipelineResult

__all__ = [
    "validate_aoi",
    "ValidatedAOI",
    "search_stac_scene",
    "STACSceneMetadata",
    "assemble_working_canvas",
    "CanvasData",
    "clean_and_normalize_canvas",
    "CleanedCanvas",
    "slice_and_filter_tiles",
    "TileCandidate",
    "DEFAULT_GROUND_CROP_SIZE",
    "save_tiles_and_manifest",
    "run_aoi_pipeline",
    "PipelineResult",
]
