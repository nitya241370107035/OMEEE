"""
Automated Test Suite for Defense Multi-Temporal & Maritime Pipeline

Tests:
1. Eastern Ladakh and Maritime AOI Registries & Geometry Validation
2. Multi-temporal difference computation and change map generation
3. Strict 512x512 tile dimension verification across all defense output tiles
4. Manifest structure and 3-tier cloud metric presence
"""

import json
from pathlib import Path
import numpy as np
import pytest
import rasterio

from backend.ingestion.input_validator import validate_aoi
from backend.datasets.defense_runner import (
    load_registry,
    compute_multitemporal_difference,
    EASTERN_LADAKH_REGISTRY,
    MARITIME_REGISTRY
)
from backend.preview.preview_service import validate_tile_dimensions, check_aoi_association

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_eastern_ladakh_registry_and_geometries():
    """Verifies all 5 Eastern Ladakh border sectors load and validate."""
    reg = load_registry(EASTERN_LADAKH_REGISTRY)
    assert len(reg["aois"]) >= 5
    
    aoi_ids = [a["aoi_id"] for a in reg["aois"]]
    assert "pangong_tso_north_bank" in aoi_ids
    assert "galwan_valley_confluence" in aoi_ids
    assert "depsang_plains_y_junction" in aoi_ids
    assert "hot_springs_gogra" in aoi_ids
    assert "chushul_rezang_la" in aoi_ids

    for a in reg["aois"]:
        geom_file = REPO_ROOT / a["geometry_file"]
        assert geom_file.exists(), f"Geometry file missing: {geom_file}"
        val = validate_aoi(geom_file)
        assert len(val.bbox) == 4
        assert val.polygon.is_valid


def test_maritime_registry_and_geometry():
    """Verifies Mumbai Naval Anchorage AOI loads and validates."""
    reg = load_registry(MARITIME_REGISTRY)
    assert len(reg["aois"]) >= 1
    
    m_entry = reg["aois"][0]
    assert m_entry["aoi_id"] == "mumbai_naval_anchorage"
    
    geom_file = REPO_ROOT / m_entry["geometry_file"]
    assert geom_file.exists()
    val = validate_aoi(geom_file)
    assert val.polygon.is_valid


def test_multitemporal_difference_logic(tmp_path):
    """Verifies multi-temporal spectral difference computation and change metrics."""
    # Synthetic canvases
    w, h = 100, 100
    transform = rasterio.transform.from_bounds(78.5, 33.7, 78.6, 33.8, w, h)
    
    # Canvas A (Baseline)
    arr_a = np.ones((3, h, w), dtype=np.uint8) * 100
    path_a = tmp_path / "canvas_a.tif"
    with rasterio.open(path_a, "w", driver="GTiff", height=h, width=w, count=3, dtype="uint8", crs="EPSG:4326", transform=transform) as dst:
        dst.write(arr_a)
        
    # Canvas B (New road/infrastructure construction in 25% of pixels)
    arr_b = np.ones((3, h, w), dtype=np.uint8) * 100
    arr_b[:, :50, :50] = 200  # 2500 pixels changed with large spectral delta
    path_b = tmp_path / "canvas_b.tif"
    with rasterio.open(path_b, "w", driver="GTiff", height=h, width=w, count=3, dtype="uint8", crs="EPSG:4326", transform=transform) as dst:
        dst.write(arr_b)

    out_tif = tmp_path / "diff.tif"
    out_png = tmp_path / "diff.png"
    
    result = compute_multitemporal_difference(path_a, path_b, out_tif, out_png, threshold=35.0)
    
    assert result["total_pixels"] == 10000
    assert result["change_pixels"] == 2500
    assert result["change_percentage"] == 25.0
    assert out_tif.exists()
    assert out_png.exists()


def test_defense_generated_tiles_512_dimensions():
    """Verifies all generated defense tiles are strictly 512x512 with PASS status."""
    el_tiles_dir = REPO_ROOT / "datasets" / "eastern_ladakh" / "sentinel2" / "pangong_tso_north_bank"
    if el_tiles_dir.exists():
        for ep in ["epoch_2017", "epoch_2020", "epoch_2023"]:
            td = el_tiles_dir / ep / "tiles"
            if td.exists():
                tiles = list(td.glob("*.tif"))
                assert len(tiles) > 0, f"No tiles in {td}"
                for t in tiles:
                    with rasterio.open(t) as src:
                        assert src.width == 512, f"Tile {t.name} width is {src.width}"
                        assert src.height == 512, f"Tile {t.name} height is {src.height}"
                        is_valid, msg = validate_tile_dimensions(src.width, src.height)
                        assert is_valid is True
                        assert msg == "PASS"

    m_tiles_dir = REPO_ROOT / "datasets" / "maritime" / "mumbai_naval_anchorage" / "epoch_2023" / "tiles"
    if m_tiles_dir.exists():
        tiles = list(m_tiles_dir.glob("*.tif"))
        assert len(tiles) == 4
        for t in tiles:
            with rasterio.open(t) as src:
                assert src.width == 512
                assert src.height == 512
                is_valid, msg = validate_tile_dimensions(src.width, src.height)
                assert is_valid is True
                assert msg == "PASS"


def test_defense_manifests_integrity():
    """Verifies structured JSON manifests contain 3-tier cloud metrics."""
    el_manifest = REPO_ROOT / "datasets" / "eastern_ladakh" / "sentinel2" / "pangong_tso_north_bank" / "master_manifest.json"
    if el_manifest.exists():
        with open(el_manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["region"] == "Eastern Ladakh High-Altitude Border Corridor"
            assert data["aoi_id"] == "pangong_tso_north_bank"
            assert "change_analysis" in data
            assert "2017_vs_2023" in data["change_analysis"]
            assert data["change_analysis"]["2017_vs_2023"]["change_percentage"] > 0

    m_manifest = REPO_ROOT / "datasets" / "maritime" / "mumbai_naval_anchorage" / "master_manifest.json"
    if m_manifest.exists():
        with open(m_manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["aoi_id"] == "mumbai_naval_anchorage"
            assert "cloud_metrics" in data
            assert "catalog_scene_cloud_cover" in data["cloud_metrics"]
            assert "AOI_cloud_pct" in data["cloud_metrics"]
            assert "tile_cloud_percentages" in data["cloud_metrics"]
