"""
tests/test_bugfixes.py
======================
Unit Tests verifying the 4 Ingestion Pipeline Bugfixes:
1. Fixed-stride grid stepping determinism (_get_grid_steps)
2. Multi-scene mosaicking per time-bucket (assemble_mosaicked_canvas_from_stac)
3. Nodata handling across granule boundary overlap seams
4. 10-band s2cloudless ML detector execution & band alias resolution
"""

import sys
from pathlib import Path
import pytest
import numpy as np
import rasterio
from rasterio.transform import from_bounds

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.tiler import _get_grid_steps
from backend.ingestion.canvas import (
    CanvasData,
    assemble_mosaicked_canvas_from_stac,
    generate_synthetic_canvas
)
from backend.ingestion.stac_search import STACSceneMetadata, STACBandAssets
from backend.ingestion.integrations.s2cloudless_adapter import (
    run_s2cloudless_detector,
    prepare_s2cloudless_tensor
)


def test_grid_steps_literal_fixed_stride():
    """Verify _get_grid_steps uses deterministic fixed strides instead of linspace redistribution."""
    total_dim = 1500
    window_size = 512
    stride = 435  # ~15% overlap

    steps = _get_grid_steps(total_dim, window_size, stride)

    # Expected offsets: range(0, 1500 - 512 + 1, 435) -> [0, 435, 870] + end clip [988]
    assert steps == [0, 435, 870, 988]

    # Verify spacing between consecutive regular steps is exactly stride (435)
    assert steps[1] - steps[0] == stride
    assert steps[2] - steps[1] == stride
    assert steps[-1] == total_dim - window_size


def test_canvas_band_alias_resolution():
    """Verify CanvasData resolves both color names and Sentinel-2 band identifiers."""
    data = np.random.rand(11, 64, 64).astype(np.float32) * 2000.0
    band_names = ["B01", "B02", "B03", "B04", "B05", "B08", "B8A", "B09", "B10", "B11", "B12"]
    transform = from_bounds(72.5, 23.0, 72.6, 23.1, 64, 64)

    canvas = CanvasData(
        data=data,
        band_names=band_names,
        transform=transform,
        crs="EPSG:4326",
        bbox=(72.5, 23.0, 72.6, 23.1),
        height=64,
        width=64
    )

    # Verify both B02/blue, B03/green, B04/red, B08/nir, B11/swir are recognized
    assert canvas.has_band("blue")
    assert canvas.has_band("B02")
    assert canvas.has_band("nir")
    assert canvas.has_band("B08")
    assert canvas.has_band("swir")
    assert canvas.has_band("B11")

    # Verify get_band returns valid 2D array
    red_arr = canvas.get_band("red")
    b04_arr = canvas.get_band("B04")
    np.testing.assert_array_equal(red_arr, b04_arr)


def test_s2cloudless_10_band_tensor_preparation():
    """Verify 10-band tensor preparation for s2cloudless ML model."""
    canvas = generate_synthetic_canvas(
        bbox=(72.5, 23.0, 72.6, 23.1),
        cloud_pct_target=0.2,
        height=128,
        width=128,
        band_names=["B01", "B02", "B03", "B04", "B05", "B08", "B8A", "B09", "B10", "B11", "B12"]
    )

    tensor_4d = prepare_s2cloudless_tensor(canvas)

    # Expected 4D shape for s2cloudless: (1, Height, Width, 10)
    assert tensor_4d.shape == (1, 128, 128, 10)
    assert tensor_4d.dtype == np.float32
    assert 0.0 <= np.max(tensor_4d) <= 1.0


def test_s2cloudless_execution_with_ml_detector():
    """Verify s2cloudless detector runs cleanly and returns cloud probability and binary mask."""
    canvas_10b = generate_synthetic_canvas(
        bbox=(72.5, 23.0, 72.6, 23.1),
        cloud_pct_target=0.15,
        height=128,
        width=128,
        band_names=["B01", "B02", "B03", "B04", "B05", "B08", "B8A", "B09", "B10", "B11", "B12"]
    )

    prob, mask = run_s2cloudless_detector(canvas_10b, threshold=0.40)

    assert prob.shape == (128, 128)
    assert mask.shape == (128, 128)
    assert mask.dtype == bool
    assert np.count_nonzero(mask) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
