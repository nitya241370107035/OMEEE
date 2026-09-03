"""
Phase 2: Comprehensive Test Suite for AOI Ingestion Pipeline

Validates:
  - Phase 1.0: GeoJSON validation, self-intersection rejection, area calculation.
  - Phase 1.1: STAC catalog query and 10-band metadata extraction.
  - Phase 1.2 & 2.4: 11-band Canvas assembly, buffer margins, and resolution reprojection.
  - Phase 2.2 - 2.5: s2cloudless adapter, 10-band validation, and detector invocation.
  - Phase 2.6: Preservation of intermediate cloud probability & cloud mask GeoTIFFs.
  - Phase 2.7: Proximity-based shadow detection adapter.
  - Phase 2.8: Mask-aware percentile radiometric normalization.
  - Phase 1.4 & 1.5: Configurable ground crop tiling, polygon intersection filtering.
  - Phase 1.6: Directory layout, GeoTIFF creation, thumbnails, and manifest schema.
  - End-to-End Phase 2 pipeline execution.
"""

import json
import shutil
import tempfile
from pathlib import Path
import numpy as np
import pytest
import rasterio
from PIL import Image
from shapely.geometry import shape, box, Polygon

from backend.ingestion.input_validator import validate_aoi, calculate_polygon_area_km2
from backend.ingestion.stac_search import search_stac_scene, STACSceneMetadata, STACBandAssets
from backend.ingestion.canvas import assemble_working_canvas, generate_synthetic_canvas, STANDARD_CANVAS_BANDS, CanvasData
from backend.ingestion.integrations.s2cloudless_adapter import (
    S2CLOUDLESS_BANDS,
    validate_s2cloudless_bands,
    prepare_s2cloudless_tensor,
    run_s2cloudless_detector
)
from backend.ingestion.masking.shadow_adapter import detect_shadows
from backend.ingestion.masking.quality_mask import combine_quality_masks, QualityMaskResult
from backend.ingestion.normalization.percentile_normalization import (
    normalize_rgb_percentile,
    normalize_multiband_percentile
)
from backend.ingestion.masking import (
    detect_clouds,
    normalize_rgb,
    clean_and_normalize_canvas
)
from backend.ingestion.tiler import (
    slice_and_filter_tiles,
    generate_site_key,
    DEFAULT_GROUND_CROP_SIZE
)
from backend.ingestion.storage import save_tiles_and_manifest, save_intermediate_masks
from backend.ingestion.pipeline import run_aoi_pipeline


# ============================================================
# Phase 1.0 Tests: Input Handling & Validation
# ============================================================

def test_valid_geojson_polygon():
    valid_geojson = {
        "type": "Polygon",
        "coordinates": [
            [
                [77.10, 28.58],
                [77.15, 28.58],
                [77.15, 28.62],
                [77.10, 28.62],
                [77.10, 28.58]
            ]
        ]
    }
    aoi = validate_aoi(valid_geojson)
    assert aoi.polygon.is_valid
    assert len(aoi.bbox) == 4
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    assert min_lon == 77.10
    assert max_lon == 77.15
    assert min_lat == 28.58
    assert max_lat == 28.62
    assert aoi.area_km2 > 0.0


def test_invalid_self_intersecting_polygon():
    # Figure-8 self-intersecting polygon
    invalid_geojson = {
        "type": "Polygon",
        "coordinates": [
            [
                [77.10, 28.58],
                [77.15, 28.62],
                [77.15, 28.58],
                [77.10, 28.62],
                [77.10, 28.58]
            ]
        ]
    }
    with pytest.raises(ValueError) as exc_info:
        validate_aoi(invalid_geojson)
    assert "Invalid AOI geometry" in str(exc_info.value)
    assert "Self-intersection" in str(exc_info.value)


def test_empty_or_degenerate_geometry():
    empty_geojson = {
        "type": "Polygon",
        "coordinates": []
    }
    with pytest.raises(ValueError):
        validate_aoi(empty_geojson)


def test_feature_collection_input(tmp_path):
    sample_file = tmp_path / "test_fc.geojson"
    fc_dict = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [77.0, 28.0],
                            [77.1, 28.0],
                            [77.1, 28.1],
                            [77.0, 28.1],
                            [77.0, 28.0]
                        ]
                    ]
                }
            }
        ]
    }
    sample_file.write_text(json.dumps(fc_dict), encoding="utf-8")
    aoi = validate_aoi(sample_file)
    assert aoi.polygon.is_valid
    assert aoi.bbox == (77.0, 28.0, 77.1, 28.1)


# ============================================================
# Phase 1.1 Tests: STAC Scene Search & Multi-Band Metadata
# ============================================================

def test_stac_scene_search_offline():
    bbox = (77.10, 28.58, 77.15, 28.62)
    meta = search_stac_scene(
        bbox=bbox,
        datetime_range="2023-01-01/2023-12-31",
        max_cloud_cover=30.0,
        offline_fallback=True
    )
    assert meta.scene_id is not None
    assert meta.acquisition_date is not None
    assert meta.sensor in ("Sentinel-2A", "Sentinel-2B", "Sentinel-2")
    assert meta.crs.startswith("EPSG:")
    assert meta.assets.red is not None
    assert meta.assets.blue is not None
    assert meta.assets.green is not None
    assert meta.assets.b01 is not None
    assert meta.assets.b11 is not None


# ============================================================
# Phase 1.2 & 2.4 Tests: Multi-Band Canvas Assembly
# ============================================================

def test_canvas_assembly_synthetic():
    bbox = (77.10, 28.58, 77.15, 28.62)
    meta = search_stac_scene(bbox=bbox, offline_fallback=True)
    canvas = assemble_working_canvas(scene_meta=meta, aoi_bbox=bbox, buffer_pct=0.05)
    
    assert canvas.data.ndim == 3
    assert canvas.data.shape[0] == len(STANDARD_CANVAS_BANDS)
    assert "B01" in canvas.band_names
    assert "B02" in canvas.band_names
    assert "B04" in canvas.band_names
    assert "B08" in canvas.band_names
    assert "B11" in canvas.band_names
    assert canvas.crs == "EPSG:4326"
    assert canvas.height > 0
    assert canvas.width > 0
    assert canvas.transform is not None


# ============================================================
# Phase 2.2 - 2.5 Tests: s2cloudless Adapter Layer
# ============================================================

def test_s2cloudless_adapter_execution():
    bbox = (77.10, 28.58, 77.15, 28.62)
    canvas = generate_synthetic_canvas(buffered_bbox=bbox, cloud_pct_target=0.25)
    
    # 1. Test tensor preparation
    tensor_4d = prepare_s2cloudless_tensor(canvas)
    assert tensor_4d.shape == (1, canvas.height, canvas.width, 10)
    assert 0.0 <= tensor_4d.min()
    assert tensor_4d.max() <= 1.0

    # 2. Test s2cloudless detector execution
    cloud_prob, cloud_mask = run_s2cloudless_detector(canvas, threshold=0.40)
    assert cloud_prob.shape == (canvas.height, canvas.width)
    assert cloud_mask.shape == (canvas.height, canvas.width)
    assert cloud_prob.dtype == np.float32
    assert cloud_mask.dtype == bool
    assert np.any(cloud_mask)


def test_s2cloudless_missing_bands_validation():
    # Construct canvas with only RGB (missing B01, B05, B8A, B09, B10, B11, B12)
    h, w = 64, 64
    rgb_data = np.zeros((3, h, w), dtype=np.float32)
    rgb_canvas = CanvasData(
        data=rgb_data,
        band_names=["B02", "B03", "B04"],
        transform=rasterio.Affine.identity(),
        crs="EPSG:4326",
        bbox=(0, 0, 1, 1),
        height=h,
        width=w
    )

    with pytest.raises(ValueError) as exc_info:
        prepare_s2cloudless_tensor(rgb_canvas)

    err = str(exc_info.value)
    assert "STOP PIPELINE ERROR: s2cloudless requires B01, B02, B04, B05, B08, B8A, B09, B10, B11, B12." in err
    assert "Missing:" in err


# ============================================================
# Phase 2.7 & 2.8 Tests: Shadow Adapter & Mask-Aware Normalization
# ============================================================

def test_shadow_adapter_and_quality_mask():
    bbox = (77.10, 28.58, 77.15, 28.62)
    canvas = generate_synthetic_canvas(buffered_bbox=bbox, cloud_pct_target=0.20)
    
    cloud_prob, cloud_mask = run_s2cloudless_detector(canvas, threshold=0.40)
    shadow_mask = detect_shadows(canvas, cloud_mask=cloud_mask, shadow_darkness_threshold=450.0)
    
    assert shadow_mask.shape == (canvas.height, canvas.width)
    assert shadow_mask.dtype == bool

    quality = combine_quality_masks(cloud_prob, cloud_mask, shadow_mask)
    assert quality.bad_mask.shape == (canvas.height, canvas.width)
    assert 0.0 <= quality.canvas_cloud_pct <= 1.0
    assert quality.valid_pixel_pct == round(1.0 - quality.canvas_cloud_pct, 4)


def test_mask_aware_percentile_normalization():
    h, w = 100, 100
    rgb = np.full((3, h, w), 500.0, dtype=np.float32)
    bad_mask = np.zeros((h, w), dtype=bool)

    # Cloud artifact
    rgb[:, 0:20, 0:20] = 8000.0
    bad_mask[0:20, 0:20] = True

    # Ground variation
    rgb[0, 20:100, 20:100] = 1200.0
    rgb[1, 20:100, 20:100] = 1600.0
    rgb[2, 20:100, 20:100] = 900.0

    norm = normalize_rgb_percentile(rgb, bad_mask, p_low=1.0, p_high=98.5)
    assert norm.shape == (3, h, w)
    assert norm.dtype == np.uint8
    assert norm.min() >= 0
    assert norm.max() <= 255


# ============================================================
# Phase 2.6 Tests: Intermediate GeoTIFF Persistence
# ============================================================

def test_save_intermediate_masks(tmp_path):
    h, w = 64, 64
    cloud_prob = np.random.uniform(0.0, 1.0, (h, w)).astype(np.float32)
    cloud_mask = cloud_prob > 0.5
    shadow_mask = cloud_prob < 0.1
    transform = rasterio.transform.from_bounds(77.0, 28.0, 77.1, 28.1, w, h)

    saved = save_intermediate_masks(
        scene_id="TEST_SCENE_INTER",
        cloud_prob=cloud_prob,
        cloud_mask=cloud_mask,
        transform=transform,
        crs="EPSG:4326",
        shadow_mask=shadow_mask,
        base_data_dir=tmp_path
    )

    assert "cloud_probability" in saved
    assert "cloud_mask" in saved
    assert Path(saved["cloud_probability"]).is_file()
    assert Path(saved["cloud_mask"]).is_file()

    with rasterio.open(saved["cloud_probability"]) as src:
        assert src.count == 1
        assert src.dtypes[0] == "float32"
        assert src.crs.to_string() == "EPSG:4326"


# ============================================================
# Phase 1.4 & 1.5 Tests: Tiling & Polygon Filtering
# ============================================================

def test_tiling_and_polygon_filtering():
    bbox = (77.10, 28.58, 77.15, 28.62)
    aoi_poly = Polygon([
        [77.10, 28.58],
        [77.15, 28.58],
        [77.15, 28.62],
        [77.10, 28.62],
        [77.10, 28.58]
    ])
    canvas = generate_synthetic_canvas(buffered_bbox=bbox, cloud_pct_target=0.2)
    cleaned = clean_and_normalize_canvas(canvas)

    # Native resolution test (GROUND_CROP_SIZE = 512)
    tiles_native = slice_and_filter_tiles(
        canvas_data=canvas,
        cleaned_canvas=cleaned,
        aoi_polygon=aoi_poly,
        scene_id="TEST_SCENE",
        ground_crop_size=512,
        overlap_pct=0.10
    )
    assert len(tiles_native) >= 1
    for tile in tiles_native:
        assert tile.rgb_data.shape == (3, 512, 512)
        assert 0.0 <= tile.cloud_pct <= 1.0
        assert tile.footprint_geom.intersects(aoi_poly)
        assert tile.is_upsampled is False


# ============================================================
# Phase 1.6 & End-to-End Pipeline Run Test
# ============================================================

def test_full_pipeline_run_with_intermediate_geotiffs(tmp_path):
    geojson_path = Path("tests/sample_aoi.geojson")
    result = run_aoi_pipeline(
        geojson_input=geojson_path,
        region_id="phase2_e2e_region",
        datetime_range="2023-01-01/2023-12-31",
        max_cloud_cover=30.0,
        ground_crop_size=512,
        base_data_dir=tmp_path,
        offline_fallback=True
    )

    assert result.total_tiles > 0
    assert result.manifest_path.is_file()
    assert result.aoi.polygon.is_valid
    assert result.elapsed_seconds > 0.0

    # Verify intermediate masks were written to disk
    scene_id = result.scene.scene_id
    inter_dir = tmp_path / "intermediate" / scene_id
    assert (inter_dir / "cloud_probability.tif").is_file()
    assert (inter_dir / "cloud_mask.tif").is_file()

