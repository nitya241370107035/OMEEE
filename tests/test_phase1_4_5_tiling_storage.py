"""
tests/test_phase1_4_5_tiling_storage.py
========================================
Unit Tests for Phase 1.4 & 1.5: Tiling, Spectral Indices (NDVI, NDWI, NDBI) & Storage
"""

import json
import sys
import tempfile
import pytest
from pathlib import Path
from PIL import Image
import rasterio

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.canvas import generate_synthetic_canvas
from backend.ingestion.masking import clean_and_normalize_canvas
from backend.ingestion.tiler import slice_and_filter_tiles, TileCandidate
from backend.ingestion.storage import save_tiles_and_manifest


def test_tiling_indices_and_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        bbox = (72.5, 23.0, 72.6, 23.1)

        # 1. Canvas (5 bands)
        canvas = generate_synthetic_canvas(bbox, cloud_pct_target=0.05)
        
        # 2. Masking & Normalization
        cleaned = clean_and_normalize_canvas(canvas)

        # 3. Tiling (Phase 1.4)
        tiles = slice_and_filter_tiles(
            canvas_data=canvas,
            cleaned_canvas=cleaned,
            scene_id="S2_TEST_SCENE_001",
            ground_crop_size=512,
            overlap_pct=0.10,
            source_type="aoi_search"
        )

        assert len(tiles) >= 1
        tile = tiles[0]
        assert isinstance(tile, TileCandidate)
        assert tile.multiband_data.shape == (5, 512, 512)
        assert tile.rgb_data.shape == (3, 512, 512)
        assert tile.band_order == ["blue", "green", "red", "nir", "swir"]
        
        # Verify computed indices
        assert tile.mean_ndvi is not None
        assert -1.0 <= tile.mean_ndvi <= 1.0
        assert tile.mean_ndwi is not None
        assert -1.0 <= tile.mean_ndwi <= 1.0
        assert tile.mean_ndbi is not None
        assert -1.0 <= tile.mean_ndbi <= 1.0
        assert "blue" in tile.band_stats

        # 4. Storage & Manifest (Phase 1.5)
        manifest_path = save_tiles_and_manifest(
            tiles=tiles,
            region_id="test_region_01",
            scene_id="S2_TEST_SCENE_001",
            acquisition_date="2024-03-22T05:30:00Z",
            base_data_dir=tmp_path,
            source_type="aoi_search"
        )

        assert manifest_path.exists()
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        assert manifest_data["total_tiles"] == len(tiles)
        assert len(manifest_data["tiles"]) == len(tiles)

        # Check saved .tif and .jpg files
        first_record = manifest_data["tiles"][0]
        tif_file = Path(first_record["storage"]["geotiff_path"])
        thumb_file = Path(first_record["storage"]["thumbnail_path"])

        assert tif_file.exists()
        assert thumb_file.exists()

        # Check GeoTIFF count and CRS
        with rasterio.open(tif_file) as src:
            assert src.count == 5
            assert src.crs.to_string() == "EPSG:4326"

        # Check thumbnail
        with Image.open(thumb_file) as img:
            assert img.size == (512, 512)
            assert img.mode == "RGB"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
