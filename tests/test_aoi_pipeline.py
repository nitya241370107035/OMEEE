"""
Phase 1.7: Comprehensive Test Suite for AOI Ingestion Pipeline

Validates:
  - Phase 1.0: GeoJSON validation, self-intersection rejection, area calculation.
  - Phase 1.1: STAC catalog query and metadata extraction.
  - Phase 1.2: Canvas assembly, buffer margins, and reprojection.
  - Phase 1.3: Cloud and shadow detection, masked percentile normalization.
  - Phase 1.4 & 1.5: Configurable ground crop tiling, polygon intersection filtering.
  - Phase 1.6: Directory layout, GeoTIFF creation, thumbnails, and manifest schema.
  - End-to-End pipeline execution.
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
from backend.ingestion.stac_search import search_stac_scene, STACSceneMetadata
from backend.ingestion.canvas import assemble_working_canvas, generate_synthetic_canvas
from backend.ingestion.masking import (
    detect_clouds,
    detect_shadows,
    normalize_rgb,
    clean_and_normalize_canvas
)
from backend.ingestion.tiler import (
    slice_and_filter_tiles,
    generate_site_key,
    DEFAULT_GROUND_CROP_SIZE
)
from backend.ingestion.storage import save_tiles_and_manifest
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
# Phase 1.1 Tests: STAC Scene Search & Metadata Extraction
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


# ============================================================
# Phase 1.2 Tests: Canvas Assembly & Buffering
# ============================================================

def test_canvas_assembly_synthetic():
    bbox = (77.10, 28.58, 77.15, 28.62)
    meta = search_stac_scene(bbox=bbox, offline_fallback=True)
    canvas = assemble_working_canvas(scene_meta=meta, aoi_bbox=bbox, buffer_pct=0.05)
    
    assert canvas.data.ndim == 3
    assert canvas.data.shape[0] >= 3  # At least RGB
    assert canvas.crs == "EPSG:4326"
    assert canvas.height > 0
    assert canvas.width > 0
    assert canvas.transform is not None


# ============================================================
# Phase 1.3 Tests: Cloud Masking & Radiometric Normalization
# ============================================================

def test_cloud_and_shadow_masking():
    h, w = 100, 100
    # Clear background
    red = np.full((h, w), 500.0, dtype=np.float32)
    green = np.full((h, w), 600.0, dtype=np.float32)
    blue = np.full((h, w), 450.0, dtype=np.float32)
    nir = np.full((h, w), 2000.0, dtype=np.float32)

    # Add bright cloud in top-left
    red[0:30, 0:30] = 6000.0
    green[0:30, 0:30] = 6000.0
    blue[0:30, 0:30] = 6500.0
    nir[0:30, 0:30] = 6000.0

    # Add dark shadow in bottom-right of cloud
    red[30:45, 30:45] = 100.0
    green[30:45, 30:45] = 100.0
    blue[30:45, 30:45] = 120.0
    nir[30:45, 30:45] = 150.0

    bands_dict = {"red": red, "green": green, "blue": blue, "nir": nir}
    cloud_prob, cloud_mask = detect_clouds(bands_dict, cloud_threshold=0.40)
    shadow_mask = detect_shadows(bands_dict, cloud_mask, shadow_darkness_threshold=300.0)

    assert np.any(cloud_mask[0:30, 0:30])
    assert not np.any(cloud_mask[70:100, 70:100])
    assert np.any(shadow_mask[30:45, 30:45])

    # Normalization test
    bad_mask = cloud_mask | shadow_mask
    rgb_stack = np.stack([red, green, blue], axis=0)
    norm = normalize_rgb(rgb_stack, bad_mask)
    assert norm.dtype == np.uint8
    assert norm.shape == (3, h, w)
    assert norm.min() >= 0
    assert norm.max() <= 255


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

    # Zoomed resolution test (GROUND_CROP_SIZE = 50)
    tiles_zoomed = slice_and_filter_tiles(
        canvas_data=canvas,
        cleaned_canvas=cleaned,
        aoi_polygon=aoi_poly,
        scene_id="TEST_SCENE",
        ground_crop_size=50,
        overlap_pct=0.10
    )
    assert len(tiles_zoomed) > len(tiles_native)
    for tile in tiles_zoomed:
        assert tile.rgb_data.shape == (3, 512, 512)
        assert tile.is_upsampled is True


# ============================================================
# Phase 1.6 Tests: Storage & Manifest Validation
# ============================================================

def test_tile_storage_and_manifest(tmp_path):
    bbox = (77.10, 28.58, 77.15, 28.62)
    aoi = validate_aoi({
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
    })
    meta = search_stac_scene(bbox=bbox, offline_fallback=True)
    canvas = generate_synthetic_canvas(buffered_bbox=bbox)
    cleaned = clean_and_normalize_canvas(canvas)
    tiles = slice_and_filter_tiles(
        canvas_data=canvas,
        cleaned_canvas=cleaned,
        aoi_polygon=aoi.polygon,
        scene_id=meta.scene_id,
        ground_crop_size=512
    )

    manifest_path = save_tiles_and_manifest(
        tiles=tiles,
        aoi=aoi,
        scene_meta=meta,
        region_id="unit_test_region",
        base_data_dir=tmp_path
    )

    assert manifest_path.is_file()
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_dict = json.load(f)

    assert manifest_dict["total_tiles"] == len(tiles)
    assert len(manifest_dict["tiles"]) == len(tiles)
    
    # Check that GeoTIFF and Thumbnail files were written to disk
    for t_record in manifest_dict["tiles"]:
        tif_path = Path(t_record["file_path"])
        jpg_path = Path(t_record["thumbnail_path"])
        assert tif_path.is_file()
        assert jpg_path.is_file()

        # Validate GeoTIFF
        with rasterio.open(tif_path) as src:
            assert src.count == 3
            assert src.crs.to_string() == "EPSG:4326"
            assert src.width == 512
            assert src.height == 512

        # Validate JPEG thumbnail
        with Image.open(jpg_path) as img:
            assert img.format == "JPEG"

        # Validate quality fields
        assert 0.0 <= t_record["cloud_pct"] <= 1.0
        assert 0.0 <= t_record["quality_confidence"] <= 1.0
        assert t_record["registration_residual_is_placeholder"] is True
        assert t_record["quality_is_placeholder"] is False


# ============================================================
# End-to-End Pipeline Run Test
# ============================================================

def test_full_pipeline_run(tmp_path):
    geojson_path = Path("tests/sample_aoi.geojson")
    result = run_aoi_pipeline(
        geojson_input=geojson_path,
        region_id="e2e_test_region",
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
