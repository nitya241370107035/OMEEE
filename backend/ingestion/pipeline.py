"""
Phase 1: AOI-to-Tiles Pipeline Orchestrator

End-to-end execution of Phase 1:
  GeoJSON Polygon In -> Cloud/Shadow Cleaned, Normalized 512x512 Tiles Out.

Connects:
  - Phase 1.0: Input handling & strict geometry validation (input_validator.py)
  - Phase 1.1: STAC scene catalog search & metadata extraction (stac_search.py)
  - Phase 1.2: Working canvas download, buffering & reprojection (canvas.py)
  - Phase 1.3: Spectral cloud/shadow masking & masked percentile normalization (masking.py)
  - Phase 1.4 & 1.5: Configurable ground crop tiling & AOI polygon filtering (tiler.py)
  - Phase 1.6: Organized disk storage, GeoTIFFs, thumbnails, and manifest.json (storage.py)
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union, Dict, Any, Optional, List

# Ensure repository root is in sys.path for direct script execution
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.input_validator import validate_aoi, ValidatedAOI
from backend.ingestion.stac_search import search_stac_scene, STACSceneMetadata
from backend.ingestion.canvas import assemble_working_canvas, CanvasData
from backend.ingestion.masking import clean_and_normalize_canvas, CleanedCanvas
from backend.ingestion.tiler import slice_and_filter_tiles, TileCandidate, DEFAULT_GROUND_CROP_SIZE
from backend.ingestion.storage import save_tiles_and_manifest, save_intermediate_masks

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("aoi_pipeline")


@dataclass
class PipelineResult:
    """Summary of the completed AOI-to-Tiles ingestion run."""
    region_id: str
    manifest_path: Path
    total_tiles: int
    aoi: ValidatedAOI
    scene: STACSceneMetadata
    canvas_cloud_pct: float
    elapsed_seconds: float
    ground_crop_size: int
    is_upsampled: bool


def run_aoi_pipeline(
    geojson_input: Union[str, Path, Dict[str, Any]],
    region_id: str = "region_001",
    datetime_range: str = "2023-01-01/2023-12-31",
    max_cloud_cover: float = 30.0,
    ground_crop_size: int = DEFAULT_GROUND_CROP_SIZE,
    overlap_pct: float = 0.10,
    base_data_dir: Union[str, Path] = "data",
    offline_fallback: bool = True
) -> PipelineResult:
    """
    Executes the full Phase 1 AOI-to-Tiles Ingestion Pipeline.

    Args:
        geojson_input: File path, GeoJSON string, or dict defining the AOI.
        region_id: Unique string identifier for the region.
        datetime_range: Search interval 'YYYY-MM-DD/YYYY-MM-DD'.
        max_cloud_cover: Coarse catalog cloud cover filter (0 - 100).
        ground_crop_size: Window crop size (512 for native 10m, 25 for zoomed).
        overlap_pct: Tile overlap fraction (default 0.10).
        base_data_dir: Storage root directory.
        offline_fallback: If True, falls back to synthetic data when network is offline.

    Returns:
        PipelineResult containing metadata and path to manifest.json.
    """
    t0 = time.time()
    logger.info("=" * 70)
    logger.info(f"STARTING AOI INGESTION PIPELINE: region_id='{region_id}'")
    logger.info(f"Configuration: ground_crop_size={ground_crop_size}, overlap={overlap_pct*100:.0f}%")
    logger.info("=" * 70)

    # ---------------------------------------------------------
    # Phase 1.0: Input handling & validation
    # ---------------------------------------------------------
    logger.info("[Phase 1.0] Parsing and validating AOI geometry...")
    aoi = validate_aoi(geojson_input)
    logger.info(
        f"[Phase 1.0] AOI Validated: Bbox={aoi.bbox}, "
        f"Approx Area={aoi.area_km2:.2f} km²"
    )

    # ---------------------------------------------------------
    # Phase 1.1: STAC scene catalog search
    # ---------------------------------------------------------
    logger.info(f"[Phase 1.1] Searching STAC catalog for date range {datetime_range}...")
    scene_meta = search_stac_scene(
        bbox=aoi.bbox,
        datetime_range=datetime_range,
        max_cloud_cover=max_cloud_cover,
        offline_fallback=offline_fallback
    )
    logger.info(
        f"[Phase 1.1] Selected Scene: '{scene_meta.scene_id}' | "
        f"Date: {scene_meta.acquisition_date} | Sensor: {scene_meta.sensor} | "
        f"Scene Cloud: {scene_meta.cloud_cover}% | Offline/Mock: {scene_meta.is_mock}"
    )

    # ---------------------------------------------------------
    # Phase 1.2: Working canvas download & reprojection
    # ---------------------------------------------------------
    logger.info("[Phase 1.2] Downloading and assembling buffered EPSG:4326 working canvas...")
    canvas = assemble_working_canvas(
        scene_meta=scene_meta,
        aoi_bbox=aoi.bbox,
        buffer_pct=0.05
    )
    logger.info(
        f"[Phase 1.2] Canvas Assembled: {canvas.width}x{canvas.height} px, "
        f"Bands={canvas.band_names}, CRS={canvas.crs}"
    )

    # ---------------------------------------------------------
    # Phase 2: Cloud/shadow masking & radiometric normalization
    # ---------------------------------------------------------
    logger.info("[Phase 2] Executing s2cloudless cloud detection, shadow adapter, and masked percentile normalization...")
    cleaned = clean_and_normalize_canvas(canvas)
    logger.info(
        f"[Phase 2] Masking & Normalization complete: "
        f"Cloud/Shadow={cleaned.canvas_cloud_pct * 100:.2f}%, "
        f"Good={((1.0 - cleaned.canvas_cloud_pct) * 100):.2f}%"
    )

    # ---------------------------------------------------------
    # Phase 2.6: Preserve Intermediate Cloud Detection Outputs
    # ---------------------------------------------------------
    logger.info(f"[Phase 2.6] Preserving intermediate cloud probability and mask GeoTIFFs...")
    save_intermediate_masks(
        scene_id=scene_meta.scene_id,
        cloud_prob=cleaned.cloud_prob,
        cloud_mask=cleaned.cloud_mask,
        transform=canvas.transform,
        crs=canvas.crs,
        shadow_mask=cleaned.shadow_mask,
        bad_mask=cleaned.bad_mask,
        normalized_canvas=cleaned.rgb_normalized,
        raw_canvas=canvas.data,
        base_data_dir=Path(base_data_dir)
    )

    # ---------------------------------------------------------
    # Phase 1.4 & 1.5: Tiling & Exact Polygon Filtering
    # ---------------------------------------------------------
    logger.info(f"[Phase 1.4 & 1.5] Slicing into 512x512 tiles and filtering against AOI polygon...")
    tiles = slice_and_filter_tiles(
        canvas_data=canvas,
        cleaned_canvas=cleaned,
        aoi_polygon=aoi.polygon,
        scene_id=scene_meta.scene_id,
        ground_crop_size=ground_crop_size,
        overlap_pct=overlap_pct,
        target_size=512
    )

    if not tiles:
        logger.warning("No tiles intersected the AOI polygon footprint!")
    else:
        logger.info(f"[Phase 1.5] Filtered: {len(tiles)} tiles inside AOI polygon.")

    # ---------------------------------------------------------
    # Phase 1.6: Tile Storage & Manifest Generation
    # ---------------------------------------------------------
    logger.info(f"[Phase 1.6] Saving GeoTIFFs, thumbnails, and manifest to disk...")
    manifest_path = save_tiles_and_manifest(
        tiles=tiles,
        aoi=aoi,
        scene_meta=scene_meta,
        region_id=region_id,
        base_data_dir=Path(base_data_dir)
    )
    
    elapsed = time.time() - t0
    logger.info("=" * 70)
    logger.info(f"PIPELINE RUN SUCCESSFUL in {elapsed:.2f}s")
    logger.info(f"Region: {region_id} | Total Tiles: {len(tiles)}")
    logger.info(f"Manifest written to: {manifest_path.resolve()}")
    logger.info("=" * 70)

    return PipelineResult(
        region_id=region_id,
        manifest_path=manifest_path,
        total_tiles=len(tiles),
        aoi=aoi,
        scene=scene_meta,
        canvas_cloud_pct=cleaned.canvas_cloud_pct,
        elapsed_seconds=round(elapsed, 2),
        ground_crop_size=ground_crop_size,
        is_upsampled=(ground_crop_size != 512)
    )


def cli_main():
    """Command-line interface entry point."""
    parser = argparse.ArgumentParser(
        description="Phase 1: AOI-to-Tiles Pipeline (GeoJSON in -> Cloud-Cleaned 512x512 Tiles Out)"
    )
    parser.add_argument(
        "--geojson", "-g",
        required=True,
        help="Path to input GeoJSON file (FeatureCollection, Feature, or Polygon)"
    )
    parser.add_argument(
        "--region-id", "-r",
        default="default_region",
        help="Identifier for the geographic region (used for directory naming)"
    )
    parser.add_argument(
        "--date-range", "-d",
        default="2023-01-01/2023-12-31",
        help="ISO 8601 date search range: YYYY-MM-DD/YYYY-MM-DD"
    )
    parser.add_argument(
        "--max-cloud", "-c",
        type=float,
        default=30.0,
        help="Coarse catalog-level whole-scene maximum cloud cover percent (default 30.0)"
    )
    parser.add_argument(
        "--ground-crop-size", "-s",
        type=int,
        default=512,
        help="Ground crop size in canvas pixels (512 for native 10m res, 25 for zoomed patch)"
    )
    parser.add_argument(
        "--overlap", "-o",
        type=float,
        default=0.10,
        help="Overlap fraction between tiles (default 0.10 for 10%%)"
    )
    parser.add_argument(
        "--out-dir",
        default="data",
        help="Base output directory (default: data)"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="Enable offline fallback if network/STAC API is unreachable"
    )

    args = parser.parse_args()

    try:
        result = run_aoi_pipeline(
            geojson_input=args.geojson,
            region_id=args.region_id,
            datetime_range=args.date_range,
            max_cloud_cover=args.max_cloud,
            ground_crop_size=args.ground_crop_size,
            overlap_pct=args.overlap,
            base_data_dir=args.out_dir,
            offline_fallback=args.offline
        )
        print(f"\nPipeline finished: {result.total_tiles} tiles indexed. Manifest: {result.manifest_path}")
    except Exception as err:
        logger.error(f"Pipeline execution failed: {err}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
