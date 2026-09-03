"""
Unit and Integration Tests for Phase 5 Cloud Removal Module (Phase 5.14)

Covers:
1. Clear pixels remain strictly unchanged within tolerance (MAE == 0.0).
2. Cloud region classification dimensions match source raster.
3. Trimap discrete classes are valid {0, 128, 255}.
4. Opacity values remain strictly in [0.0, 1.0].
5. Output GeoTIFF is readable by Rasterio.
6. CRS and affine transform are preserved.
7. Reconstruction mask dimensions and provenance codes match output.
8. 512x512 tiles are genuinely (3, 512, 512).
9. Missing cloud mask produces a clear validation error.
10. Cloud removal manifest schema and scientific metrics integrity.
"""

import json
from pathlib import Path
import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from backend.cloud_removal.config import (
    CloudRegionClass,
    ProvenanceClass,
    TrimapClass,
    CloudRemovalConfig,
    DEFAULT_CONFIG
)
from backend.cloud_removal.input_adapter import CloudRemovalInputAdapter
from backend.cloud_removal.cloud_regions import classify_cloud_regions, compute_region_statistics
from backend.cloud_removal.trimap import generate_cloud_trimap
from backend.cloud_removal.opacity import estimate_cloud_opacity
from backend.cloud_removal.reconstruction_mask import create_reconstruction_mask, compute_provenance_breakdown
from backend.cloud_removal.removal import remove_clouds, save_cloud_removal_products
from backend.cloud_removal.validation import (
    validate_clear_pixel_preservation,
    validate_cloud_removal_raster_integrity,
    validate_strict_512_tile_outputs
)
from backend.cloud_removal.manifest import create_cloud_removal_manifest


@pytest.fixture
def synthetic_scene_data():
    """Generates synthetic 512x512 multi-layer scene data for unit tests."""
    h, w = 512, 512
    np.random.seed(42)
    
    # 3-channel RGB canvas with structured terrain
    rgb = np.random.randint(50, 180, (3, h, w), dtype=np.uint8)

    # Cloud probability map with clear zone (left), thin cloud (middle), thick cloud (right)
    cloud_prob = np.zeros((h, w), dtype=np.float32)
    cloud_prob[:, :200] = 0.05                             # Definite clear
    cloud_prob[:, 200:350] = np.linspace(0.25, 0.55, 150)  # Thin cloud
    cloud_prob[:, 350:] = 0.85                             # Thick cloud

    # Binary cloud mask matching probability
    cloud_mask = (cloud_prob >= 0.40).astype(np.uint8)
    shadow_mask = np.zeros((h, w), dtype=np.uint8)
    bad_mask = cloud_mask.copy()

    transform = Affine(0.0001, 0, 78.5, 0, -0.0001, 34.0)
    crs = "EPSG:4326"

    return {
        "rgb": rgb,
        "cloud_prob": cloud_prob,
        "cloud_mask": cloud_mask,
        "shadow_mask": shadow_mask,
        "bad_mask": bad_mask,
        "transform": transform,
        "crs": crs,
        "height": h,
        "width": w
    }


def test_1_clear_pixels_remain_unchanged_zero_distortion(synthetic_scene_data):
    """Test 1: Verifies that genuine clear pixels are strictly preserved with 0.0 distortion."""
    data = synthetic_scene_data
    declouded_out, declouded_obs, rec_mask, region_map, opacity = remove_clouds(
        rgb_data=data["rgb"],
        cloud_prob=data["cloud_prob"],
        cloud_mask=data["cloud_mask"],
        config=DEFAULT_CONFIG
    )

    clear_mask = (region_map == CloudRegionClass.CLEAR)
    assert clear_mask.any(), "Expected clear pixels in synthetic test scene"

    # Evaluate preservation metrics
    metrics = validate_clear_pixel_preservation(
        observed_rgb=data["rgb"],
        declouded_rgb=declouded_out,
        clear_mask=clear_mask,
        tolerance=1e-4
    )

    assert metrics["clear_pixel_mae"] == 0.0, f"Expected 0.0 MAE on clear pixels, got {metrics['clear_pixel_mae']}"
    assert metrics["clear_pixel_rmse"] == 0.0, f"Expected 0.0 RMSE on clear pixels, got {metrics['clear_pixel_rmse']}"
    assert metrics["max_absolute_difference"] == 0.0
    assert metrics["modified_clear_pixels_percentage"] == 0.0
    assert metrics["preservation_status"] == "PASS"


def test_2_cloud_region_classification_dimensions(synthetic_scene_data):
    """Test 2: Verifies cloud region classification shapes and class values."""
    data = synthetic_scene_data
    region_map = classify_cloud_regions(data["cloud_prob"], data["cloud_mask"], DEFAULT_CONFIG)

    assert region_map.shape == (data["height"], data["width"])
    unique_classes = set(np.unique(region_map).tolist())
    valid_classes = {int(c) for c in CloudRegionClass}
    assert unique_classes.issubset(valid_classes)
    assert CloudRegionClass.CLEAR in unique_classes
    assert CloudRegionClass.THICK_CLOUD in unique_classes


def test_3_trimap_discrete_classes(synthetic_scene_data):
    """Test 3: Verifies trimap contains only standardized discrete values {0, 128, 255}."""
    data = synthetic_scene_data
    trimap = generate_cloud_trimap(data["cloud_prob"], data["cloud_mask"], DEFAULT_CONFIG)

    assert trimap.shape == (data["height"], data["width"])
    unique_vals = set(np.unique(trimap).tolist())
    allowed_vals = {TrimapClass.BACKGROUND_CLEAR, TrimapClass.UNCERTAIN_TRANSPARENT, TrimapClass.FOREGROUND_OPAQUE}
    assert unique_vals.issubset(allowed_vals)


def test_4_opacity_range(synthetic_scene_data):
    """Test 4: Verifies opacity alpha map strictly remains within [0.0, 1.0]."""
    data = synthetic_scene_data
    trimap = generate_cloud_trimap(data["cloud_prob"], data["cloud_mask"], DEFAULT_CONFIG)
    opacity = estimate_cloud_opacity(data["cloud_prob"], trimap, data["rgb"], DEFAULT_CONFIG)

    assert opacity.shape == (data["height"], data["width"])
    assert opacity.min() >= 0.0
    assert opacity.max() <= 1.0
    # Clear region must have exact alpha = 0.0
    assert (opacity[trimap == TrimapClass.BACKGROUND_CLEAR] == 0.0).all()
    # Opaque region must have exact alpha = 1.0
    assert (opacity[trimap == TrimapClass.FOREGROUND_OPAQUE] == 1.0).all()


def test_5_output_geotiff_is_readable(tmp_path, synthetic_scene_data):
    """Test 5: Verifies generated cloud removal GeoTIFF is readable and valid."""
    data = synthetic_scene_data
    declouded_out, declouded_obs, rec_mask, region_map, opacity = remove_clouds(
        rgb_data=data["rgb"],
        cloud_prob=data["cloud_prob"],
        cloud_mask=data["cloud_mask"],
        config=DEFAULT_CONFIG
    )

    paths = save_cloud_removal_products(
        declouded_output=declouded_out,
        declouded_observed=declouded_obs,
        reconstruction_mask=rec_mask,
        cloud_region_map=region_map,
        cloud_opacity=opacity,
        output_dir=tmp_path,
        crs=data["crs"],
        transform=data["transform"]
    )

    with rasterio.open(paths["declouded_output"]) as src:
        assert src.count == 3
        assert src.height == data["height"]
        assert src.width == data["width"]
        arr = src.read()
        assert not np.isnan(arr).any()


def test_6_crs_and_transform_preservation(tmp_path, synthetic_scene_data):
    """Test 6: Verifies exact coordinate reference system and affine transform preservation."""
    data = synthetic_scene_data
    declouded_out, declouded_obs, rec_mask, region_map, opacity = remove_clouds(
        rgb_data=data["rgb"],
        cloud_prob=data["cloud_prob"],
        cloud_mask=data["cloud_mask"],
        config=DEFAULT_CONFIG
    )

    paths = save_cloud_removal_products(
        declouded_output=declouded_out,
        declouded_observed=declouded_obs,
        reconstruction_mask=rec_mask,
        cloud_region_map=region_map,
        cloud_opacity=opacity,
        output_dir=tmp_path,
        crs=data["crs"],
        transform=data["transform"]
    )

    with rasterio.open(paths["declouded_output"]) as src:
        assert str(src.crs) == data["crs"]
        assert src.transform == data["transform"]


def test_7_reconstruction_mask_dimensions_and_provenance_codes(synthetic_scene_data):
    """Test 7: Verifies provenance mask dimensions match output and contains valid codes."""
    data = synthetic_scene_data
    declouded_out, declouded_obs, rec_mask, region_map, opacity = remove_clouds(
        rgb_data=data["rgb"],
        cloud_prob=data["cloud_prob"],
        cloud_mask=data["cloud_mask"],
        config=DEFAULT_CONFIG
    )

    assert rec_mask.shape == (data["height"], data["width"])
    valid_codes = {int(p) for p in ProvenanceClass}
    found_codes = set(np.unique(rec_mask).tolist())
    assert found_codes.issubset(valid_codes)
    assert ProvenanceClass.OBSERVED_CLEAR in found_codes


def test_8_strict_512_tile_outputs(tmp_path, synthetic_scene_data):
    """Test 8: Verifies that all generated ground tiles are strictly (3, 512, 512)."""
    data = synthetic_scene_data
    tiles_dir = tmp_path / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # Save a test 512x512 tile
    p_tile = tiles_dir / "test_tile_0000.tif"
    with rasterio.open(
        p_tile, "w", driver="GTiff",
        height=512, width=512, count=3,
        dtype="uint8", crs=data["crs"], transform=data["transform"]
    ) as dst:
        dst.write(data["rgb"])

    val = validate_strict_512_tile_outputs(tiles_dir)
    assert val["status"] == "PASS"
    assert val["total_tiles"] == 1
    assert val["valid_512_count"] == 1


def test_9_missing_cloud_mask_produces_validation_error(tmp_path, synthetic_scene_data):
    """Test 9: Verifies that missing required cloud mask raises a clear FileNotFoundError."""
    data = synthetic_scene_data
    # Write raw_canvas and cloud_probability, but omit cloud_mask.tif
    raw_path = tmp_path / "raw_canvas.tif"
    with rasterio.open(
        raw_path, "w", driver="GTiff",
        height=data["height"], width=data["width"], count=3,
        dtype="uint8", crs=data["crs"], transform=data["transform"]
    ) as dst:
        dst.write(data["rgb"])

    prob_path = tmp_path / "cloud_probability.tif"
    with rasterio.open(
        prob_path, "w", driver="GTiff",
        height=data["height"], width=data["width"], count=1,
        dtype="float32", crs=data["crs"], transform=data["transform"]
    ) as dst:
        dst.write(data["cloud_prob"], 1)

    with pytest.raises(FileNotFoundError, match="Missing required cloud mask raster in:"):
        CloudRemovalInputAdapter.load_scene_directory(tmp_path, scene_id="missing_mask_scene")


def test_10_cloud_removal_manifest_schema_and_metrics(tmp_path, synthetic_scene_data):
    """Test 10: Verifies cloud removal manifest generation and scientific metric schema."""
    p_man = tmp_path / "manifest.json"
    
    manifest = create_cloud_removal_manifest(
        scene_id="TEST_SCENE",
        dataset_id="cloud_masking_validation",
        input_paths={"rgb_canvas": "fake/rgb.tif", "cloud_probability": "fake/prob.tif", "cloud_mask": "fake/cmask.tif"},
        product_paths={"declouded_output": "fake/dec.tif", "reconstruction_mask": "fake/rec.tif"},
        method_name="cloud_matting_surface_recovery",
        method_version="1.0.0",
        provenance_stats={"observed_pixel_percentage": 65.0, "thin_cloud_corrected_percentage": 20.0, "unresolved_pixel_percentage": 15.0},
        region_stats={"clear_percentage": 65.0, "thin_cloud_percentage": 20.0, "thick_cloud_percentage": 15.0},
        validation_metrics={"clear_pixel_mae": 0.0, "clear_pixel_rmse": 0.0, "max_absolute_difference": 0.0, "preservation_status": "PASS"},
        tile_validation={"status": "PASS", "total_tiles": 4, "valid_512_count": 4},
        output_manifest_path=p_man
    )

    assert p_man.exists()
    assert manifest["scientific_validation"]["validation_status"] == "PASS"
    assert manifest["scientific_validation"]["clear_pixel_mae"] == 0.0
    assert manifest["methodology"]["research_reference"] == "DOI: 10.3390/rs15040904 (MDPI Remote Sensing)"
