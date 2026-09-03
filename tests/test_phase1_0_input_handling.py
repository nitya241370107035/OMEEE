"""
tests/test_phase1_0_input_handling.py
======================================
Unit Tests for Phase 1.0: Dual Entry Point Input Handling & Validation
"""

import os
import sys
import tempfile
import pytest
from pathlib import Path
from datetime import date, datetime

import numpy as np
import rasterio
from rasterio.transform import from_origin

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.input_validator import (
    validate_aoi,
    validate_aoi_input,
    validate_direct_file_input,
    ValidatedAOI,
    ValidatedAOIInput,
    ValidatedFileInput,
)


# ============================================================
# 1. Tests for Entry Point A (AOI + Date Range)
# ============================================================

def test_entry_point_a_valid_polygon():
    geojson_poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [72.5219491, 23.0494295],
                [72.6064632, 22.9964611],
                [72.6774966, 23.0890229],
                [72.5919455, 23.1276511],
                [72.5219491, 23.0494295]
            ]
        ]
    }

    validated = validate_aoi_input(
        geojson_input=geojson_poly,
        date_from="2024-01-01",
        date_to="2024-06-30",
        region_id="ahmedabad_test"
    )

    assert isinstance(validated, ValidatedAOIInput)
    assert validated.region_id == "ahmedabad_test"
    assert validated.date_from == date(2024, 1, 1)
    assert validated.date_to == date(2024, 6, 30)
    assert validated.aoi.area_km2 > 10.0
    assert validated.source_type == "aoi_search"
    assert validated.datetime_range_str == "2024-01-01/2024-06-30"


def test_entry_point_a_invalid_geometry():
    # Self-intersecting bowtie polygon
    bowtie_geojson = {
        "type": "Polygon",
        "coordinates": [
            [
                [72.5, 23.0],
                [72.6, 23.1],
                [72.5, 23.1],
                [72.6, 23.0],
                [72.5, 23.0]
            ]
        ]
    }

    with pytest.raises(ValueError, match="Invalid AOI geometry"):
        validate_aoi(bowtie_geojson)


def test_entry_point_a_invalid_dates():
    geojson_poly = {
        "type": "Polygon",
        "coordinates": [[[72.5, 23.0], [72.6, 23.0], [72.6, 23.1], [72.5, 23.1], [72.5, 23.0]]]
    }

    # date_from > date_to
    with pytest.raises(ValueError, match="cannot be later than"):
        validate_aoi_input(
            geojson_input=geojson_poly,
            date_from="2024-06-30",
            date_to="2024-01-01"
        )


# ============================================================
# 2. Tests for Entry Point B (Direct File Ingestion — Offline)
# ============================================================

def test_entry_point_b_with_synthetic_geotiff():
    with tempfile.TemporaryDirectory() as tmpdir:
        tif_path = Path(tmpdir) / "test_multiband.tif"
        
        # Create a synthetic 4-band GeoTIFF (Red, Green, Blue, NIR)
        width, height = 256, 256
        transform = from_origin(72.5, 23.1, 0.0001, 0.0001)
        data = (np.random.rand(4, height, width) * 10000).astype(np.uint16)

        with rasterio.open(
            str(tif_path),
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=4,
            dtype=data.dtype,
            crs="EPSG:4326",
            transform=transform,
        ) as dst:
            dst.write(data)
            dst.set_band_description(1, "Red")
            dst.set_band_description(2, "Green")
            dst.set_band_description(3, "Blue")
            dst.set_band_description(4, "NIR")

        # Test Entry Point B
        validated_file = validate_direct_file_input(
            file_path=tif_path,
            region_id="eval_dataset_01",
            acquisition_date="2024-04-15"
        )

        assert isinstance(validated_file, ValidatedFileInput)
        assert validated_file.file_path.resolve() == tif_path.resolve()
        assert validated_file.region_id == "eval_dataset_01"
        assert validated_file.band_count == 4
        assert validated_file.band_order == ["red", "green", "blue", "nir"]
        assert validated_file.is_offline_compliant is True
        assert validated_file.source_type == "organiser_provided"
        assert len(validated_file.bounds_wgs84) == 4
        assert validated_file.acquisition_date == datetime(2024, 4, 15)


def test_entry_point_b_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        validate_direct_file_input("non_existent_satellite_file.tif")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
