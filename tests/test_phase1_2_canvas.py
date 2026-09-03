"""
tests/test_phase1_2_canvas.py
==============================
Unit Tests for Phase 1.2: 5-Band Working Canvas Assembly (Shared by Both Entry Points)
"""

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

from backend.ingestion.input_validator import validate_direct_file_input
from backend.ingestion.stac_search import STACSceneMetadata, STACBandAssets
from backend.ingestion.canvas import (
    assemble_canvas_from_stac,
    assemble_canvas_from_file,
    generate_synthetic_canvas,
    CanvasData,
)


def test_synthetic_5_band_canvas():
    bbox = (72.5, 23.0, 72.6, 23.1)
    canvas = generate_synthetic_canvas(bbox, cloud_pct_target=0.1)

    assert isinstance(canvas, CanvasData)
    assert canvas.crs == "EPSG:4326"
    assert canvas.data.shape[0] == 5  # 5 bands
    assert canvas.band_names == ["blue", "green", "red", "nir", "swir"]

    # Verify helper getters
    red = canvas.get_band("red")
    nir = canvas.get_band("nir")
    swir = canvas.get_band("swir")
    rgb = canvas.get_rgb()

    assert red.shape == (canvas.height, canvas.width)
    assert nir.shape == (canvas.height, canvas.width)
    assert swir.shape == (canvas.height, canvas.width)
    assert rgb.shape == (3, canvas.height, canvas.width)


def test_entry_point_b_canvas_assembly():
    with tempfile.TemporaryDirectory() as tmpdir:
        tif_path = Path(tmpdir) / "eval_5band.tif"
        width, height = 128, 128
        transform = from_origin(72.5, 23.1, 0.0001, 0.0001)
        data = (np.random.rand(5, height, width) * 5000).astype(np.uint16)

        with rasterio.open(
            str(tif_path),
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

        # 1. Validate file (Phase 1.0)
        file_input = validate_direct_file_input(tif_path, region_id="eval_test")
        
        # 2. Assemble canvas (Phase 1.2)
        canvas = assemble_canvas_from_file(file_input)

        assert isinstance(canvas, CanvasData)
        assert canvas.crs == "EPSG:4326"
        assert canvas.data.shape[0] == 5
        assert canvas.band_names == ["blue", "green", "red", "nir", "swir"]
        assert canvas.source_type == "organiser_provided"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
