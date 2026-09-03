"""
tests/test_phase1_e2e_pipeline.py
==================================
End-to-End Tests for Phase 1 Ingestion Pipeline (Entry Point A & Entry Point B)
"""

import json
import sys
import tempfile
import pytest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.pipeline import (
    run_aoi_ingestion_pipeline,
    run_direct_file_ingestion_pipeline,
    PipelineResult,
)


def test_entry_point_a_end_to_end():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        geojson_poly = {
            "type": "Polygon",
            "coordinates": [
                [
                    [72.5219, 23.0494],
                    [72.6064, 22.9964],
                    [72.6774, 23.0890],
                    [72.5919, 23.1276],
                    [72.5219, 23.0494]
                ]
            ]
        }

        result = run_aoi_ingestion_pipeline(
            geojson_input=geojson_poly,
            date_from="2023-01-01",
            date_to="2024-06-30",
            region_id="test_ahmedabad_e2e",
            num_time_buckets=2,
            base_data_dir=str(tmp_path),
            populate_db=False,  # Avoid modifying live DB during unit test
            offline_fallback=True
        )

        assert isinstance(result, PipelineResult)
        assert result.source_type == "aoi_search"
        assert len(result.scenes_processed) >= 2
        assert result.total_tiles_generated >= 1

        for sc in result.scenes_processed:
            manifest_file = Path(sc.manifest_path)
            assert manifest_file.exists()
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["total_tiles"] == sc.tiles_count
            assert len(data["tiles"]) == sc.tiles_count
            
            # Verify tile fields
            first_tile = data["tiles"][0]
            assert "mean_ndvi" in first_tile["indices"]
            assert "mean_ndwi" in first_tile["indices"]
            assert "mean_ndbi" in first_tile["indices"]
            assert first_tile["quality"]["cloud_pct"] >= 0.0


def test_entry_point_b_end_to_end():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        eval_tif = tmp_path / "eval_sample_image.tif"

        # Create 5-band synthetic evaluation GeoTIFF
        width, height = 512, 512
        transform = from_origin(72.5, 23.1, 0.0001, 0.0001)
        data = (np.random.rand(5, height, width) * 5000).astype(np.uint16)

        with rasterio.open(
            str(eval_tif),
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=5,
            dtype=data.dtype,
            crs="EPSG:4326",
            transform=transform,
        ) as dst:
            dst.write(data)
            dst.set_band_description(1, "Blue")
            dst.set_band_description(2, "Green")
            dst.set_band_description(3, "Red")
            dst.set_band_description(4, "NIR")
            dst.set_band_description(5, "SWIR")

        result = run_direct_file_ingestion_pipeline(
            file_path=eval_tif,
            region_id="eval_run_01",
            acquisition_date="2024-05-10T00:00:00Z",
            base_data_dir=str(tmp_path),
            populate_db=False
        )

        assert isinstance(result, PipelineResult)
        assert result.source_type == "organiser_provided"
        assert result.is_offline is True
        assert result.total_tiles_generated >= 1

        manifest_file = Path(result.scenes_processed[0].manifest_path)
        assert manifest_file.exists()
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["source_type"] == "organiser_provided"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
