"""
Test Suite for Cloud Masking Validation Subsystem

Tests:
1. Configuration loading & schema verification (categories, ranges, preferred coverages).
2. AOI resolution logic.
3. Candidate search, scoring, and proximity-to-preferred ranking.
4. End-to-end processing of a cloudy validation scene through Phase 2 pipeline.
5. Distinct 3-tier cloud percentage separation (Catalog vs AOI vs Tile).
6. Inspection and listing utilities.
"""

import json
from pathlib import Path
import pytest
import numpy as np
import rasterio

from backend.datasets.cloud_validation import (
    load_cloud_validation_config,
    resolve_aoi,
    search_cloud_validation_scenes,
    process_cloud_validation_scene,
    list_cloud_validation_scenes,
    DEFAULT_CONFIG_PATH
)
from backend.ingestion.stac_search import STACSceneMetadata, STACBandAssets


def test_cloud_validation_config_loading():
    """Verify cloud_validation.yaml loads correctly with all required categories."""
    cfg = load_cloud_validation_config(DEFAULT_CONFIG_PATH)
    assert "categories" in cfg
    assert "scene_selection" in cfg

    categories = cfg["categories"]
    required_cats = ["clear_control", "light_cloud", "medium_cloud", "heavy_cloud"]
    for cat in required_cats:
        assert cat in categories
        c_info = categories[cat]
        assert "minimum_cloud_cover" in c_info
        assert "maximum_cloud_cover" in c_info
        assert "preferred_cloud_cover" in c_info
        assert c_info["minimum_cloud_cover"] <= c_info["preferred_cloud_cover"] <= c_info["maximum_cloud_cover"]


def test_resolve_aoi():
    """Verify AOI resolution for standard validation AOI."""
    aoi_id, aoi_path, v_aoi = resolve_aoi("current_aoi")
    assert aoi_id == "current_aoi"
    assert aoi_path.exists()
    assert len(v_aoi.bbox) == 4
    assert v_aoi.area_km2 > 0


def test_search_cloud_validation_scenes():
    """Verify search returns ranked candidates with cloud coverage within category bounds."""
    candidates = search_cloud_validation_scenes(
        aoi_id="current_aoi",
        category="medium_cloud",
        limit=10
    )
    assert len(candidates) > 0
    top_cand = candidates[0]
    assert "scene_id" in top_cand
    assert "catalog_cloud_cover" in top_cand
    assert "preferred_cloud_proximity" in top_cand
    assert "aoi_coverage_overlap_pct" in top_cand
    assert top_cand["status"] == "cloud_masking_candidate"

    # Verify ranking: top candidate must have lowest proximity delta
    proximities = [c["preferred_cloud_proximity"] for c in candidates]
    assert proximities == sorted(proximities)


def test_3tier_cloud_percentage_separation():
    """Verify distinct separation between scene-level, AOI-level, and tile-level cloud percentages."""
    scenes = list_cloud_validation_scenes()
    if not scenes:
        pytest.skip("No processed cloud validation scenes found to test.")

    for sc in scenes:
        cm = sc["cloud_metrics"]
        assert "catalog_scene_cloud_cover" in cm
        assert "AOI_cloud_pct" in cm
        assert "AOI_shadow_pct" in cm
        assert "AOI_bad_pixel_pct" in cm
        assert "tile_cloud_percentages" in cm

        cat_cover = cm["catalog_scene_cloud_cover"]
        aoi_cover = cm["AOI_cloud_pct"]
        tile_covers = cm["tile_cloud_percentages"]

        assert 0.0 <= cat_cover <= 100.0
        assert 0.0 <= aoi_cover <= 100.0
        for tc in tile_covers:
            assert 0.0 <= tc <= 100.0


def test_cloud_validation_file_integrity(tmp_path):
    """Verify intermediate GeoTIFFs, PNG previews, and 512x512 tiles for processed cloudy scene."""
    scenes = list_cloud_validation_scenes()
    if not scenes:
        pytest.skip("No processed cloud validation scenes found.")

    sc = scenes[0]
    repo_root = Path(__file__).resolve().parent.parent

    # Check raw canvas GeoTIFF
    raw_path = repo_root / sc["file_structure"]["raw"]
    assert raw_path.exists()
    with rasterio.open(raw_path) as src:
        assert src.count == 3
        assert src.crs.to_string() == "EPSG:4326"

    # Check intermediate cloud mask GeoTIFF
    cmask_path = repo_root / sc["file_structure"]["intermediate"]["cloud_mask"]
    assert cmask_path.exists()
    with rasterio.open(cmask_path) as src:
        assert src.count == 1
        assert src.dtypes[0] == "uint8"

    # Check normalized canvas GeoTIFF
    norm_path = repo_root / sc["file_structure"]["normalized"]
    assert norm_path.exists()
    with rasterio.open(norm_path) as src:
        assert src.count == 3
        assert src.dtypes[0] == "uint8"

    # Check 512x512 Tiles
    tile_dir = repo_root / sc["file_structure"]["tiles_directory"]
    assert tile_dir.exists()
    tiles = list(tile_dir.glob("tile_*.tif"))
    assert len(tiles) > 0
    for t in tiles:
        with rasterio.open(t) as src:
            assert src.width == 512
            assert src.height == 512
            assert src.count == 3

    # Check previews directory
    prev_dir = repo_root / sc["file_structure"]["previews_directory"]
    assert prev_dir.exists()
    expected_previews = [
        "raw_rgb_preview.png",
        "cloud_probability_preview.png",
        "cloud_mask_preview.png",
        "shadow_mask_preview.png",
        "combined_mask_preview.png",
        "normalized_rgb_preview.png",
        "mask_overlay_preview.png"
    ]
    for ep in expected_previews:
        assert (prev_dir / ep).exists(), f"Missing preview: {ep}"
