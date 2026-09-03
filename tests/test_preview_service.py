"""
Unit & Integration Tests for GeoTIFF Preview Service, Statistics, Classification, and Mask Overlay
"""

from pathlib import Path
import numpy as np
from PIL import Image
import pytest
import rasterio
from rasterio.transform import from_bounds

from backend.preview.geotiff_reader import read_geotiff, classify_geotiff_category
from backend.preview.raster_metadata import RasterMetadata
from backend.preview.statistics import compute_raster_statistics
from backend.preview.rgb_preview import (
    find_rgb_band_indices,
    render_rgb_composite,
    render_mask_preview,
    render_probability_heatmap,
    render_mask_overlay,
    generate_raster_preview
)
from backend.preview.preview_service import (
    GeoTIFFPreviewService,
    validate_tile_dimensions,
    discover_scene_groups,
    check_aoi_association
)


@pytest.fixture
def synthetic_canvas_512x612(tmp_path) -> Path:
    """Creates a working canvas with variable dimensions (512 height x 612 width)."""
    intermediate_dir = tmp_path / "data" / "intermediate" / "test_scene"
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    file_path = intermediate_dir / "raw_canvas.tif"
    height, width = 512, 612
    transform = from_bounds(77.10, 28.58, 77.15, 28.62, width, height)
    
    arr = np.full((3, height, width), 120, dtype=np.uint8)
    with rasterio.open(
        file_path, "w", driver="GTiff", height=height, width=width, count=3,
        dtype=np.uint8, crs="EPSG:4326", transform=transform
    ) as dst:
        dst.write(arr)
        dst.set_band_description(1, "B04")
        dst.set_band_description(2, "B03")
        dst.set_band_description(3, "B02")

    return file_path


@pytest.fixture
def synthetic_tile_512x512(tmp_path) -> Path:
    """Creates a valid 512x512 final output tile."""
    tile_dir = tmp_path / "data" / "tiles" / "test_aoi" / "2023-10-06"
    tile_dir.mkdir(parents=True, exist_ok=True)
    file_path = tile_dir / "tile_0000.tif"
    height, width = 512, 512
    transform = from_bounds(77.10, 28.58, 77.12, 28.60, width, height)
    
    arr = np.full((3, height, width), 90, dtype=np.uint8)
    with rasterio.open(
        file_path, "w", driver="GTiff", height=height, width=width, count=3,
        dtype=np.uint8, crs="EPSG:4326", transform=transform
    ) as dst:
        dst.write(arr)

    return file_path


@pytest.fixture
def synthetic_invalid_tile(tmp_path) -> Path:
    """Creates an invalid non-512x512 tile (e.g. 256x256)."""
    tile_dir = tmp_path / "data" / "tiles" / "test_aoi" / "2023-10-06"
    tile_dir.mkdir(parents=True, exist_ok=True)
    file_path = tile_dir / "tile_invalid.tif"
    height, width = 256, 256
    transform = from_bounds(77.10, 28.58, 77.11, 28.59, width, height)
    
    arr = np.full((3, height, width), 90, dtype=np.uint8)
    with rasterio.open(
        file_path, "w", driver="GTiff", height=height, width=width, count=3,
        dtype=np.uint8, crs="EPSG:4326", transform=transform
    ) as dst:
        dst.write(arr)

    return file_path


def test_working_canvas_classification_and_dimensions(synthetic_canvas_512x612):
    """Test 1: Working canvas 512x612 classified as WORKING_CANVAS and does not fail 512x512 requirement."""
    metadata, data = read_geotiff(synthetic_canvas_512x612)
    assert metadata.file_category == "WORKING_CANVAS"
    assert metadata.height == 512
    assert metadata.width == 612
    # Verify AOI association
    aoi_id, aoi_status = check_aoi_association(metadata.bounds, synthetic_canvas_512x612.parent.parent.parent.parent)
    assert aoi_status == "MATCH"


def test_final_tile_dimension_validation(synthetic_tile_512x512, synthetic_invalid_tile):
    """Test 2 & 3: Final tile 512x512 passes validation; non-512x512 fails validation."""
    meta_valid, _ = read_geotiff(synthetic_tile_512x512)
    assert meta_valid.file_category == "FINAL_TILE"
    is_valid, msg = validate_tile_dimensions(meta_valid.width, meta_valid.height)
    assert is_valid is True
    assert msg == "PASS"

    meta_invalid, _ = read_geotiff(synthetic_invalid_tile)
    assert meta_invalid.file_category == "FINAL_TILE"
    is_valid_inv, msg_inv = validate_tile_dimensions(meta_invalid.width, meta_invalid.height)
    assert is_valid_inv is False
    assert "FAIL" in msg_inv


def test_scene_group_discovery_and_missing_layer_handling(tmp_path):
    """Test 4 & 5: Scene grouping and missing layers returning None without crashing."""
    scene_dir = tmp_path / "data" / "intermediate" / "scene_demo"
    scene_dir.mkdir(parents=True, exist_ok=True)
    
    # Create only raw_canvas.tif and cloud_mask.tif
    with rasterio.open(scene_dir / "raw_canvas.tif", "w", driver="GTiff", height=64, width=64, count=3, dtype=np.uint8) as dst:
        dst.write(np.zeros((3, 64, 64), dtype=np.uint8))
    with rasterio.open(scene_dir / "cloud_mask.tif", "w", driver="GTiff", height=64, width=64, count=1, dtype=np.uint8) as dst:
        dst.write(np.zeros((1, 64, 64), dtype=np.uint8))

    scenes = discover_scene_groups(tmp_path)
    assert len(scenes) >= 1
    key = [k for k in scenes.keys() if "scene_demo" in k][0]
    layers = scenes[key]

    assert layers["raw_canvas"] is not None
    assert layers["cloud_mask"] is not None
    # Missing layers should be None (NOT AVAILABLE)
    assert layers["cloud_probability"] is None
    assert layers["shadow_mask"] is None
    assert layers["normalized_canvas"] is None


def test_mask_overlay_rendering(synthetic_canvas_512x612):
    """Test 6 & 7: Real mask statistics and true mask overlay composite generation."""
    meta, data = read_geotiff(synthetic_canvas_512x612)
    rgb_img = render_rgb_composite(data, meta)
    
    # Synthetic cloud mask (100 px) & shadow mask (50 px)
    c_mask = np.zeros((1, 512, 612), dtype=np.uint8)
    c_mask[0, 10:20, 10:20] = 1
    s_mask = np.zeros((1, 512, 612), dtype=np.uint8)
    s_mask[0, 30:35, 30:40] = 1

    stats_c = compute_raster_statistics(c_mask, meta)
    assert stats_c.is_mask is True
    assert stats_c.masked_pixel_count == 100

    overlay = render_mask_overlay(rgb_img, cloud_mask=c_mask, shadow_mask=s_mask, alpha=0.5)
    assert isinstance(overlay, Image.Image)
    assert overlay.size == (612, 512)
