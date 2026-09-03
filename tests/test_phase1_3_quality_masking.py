"""
tests/test_phase1_3_quality_masking.py
======================================
Unit Tests for Phase 1.3: Quality Handling, Cloud/Shadow Masking & Normalization
"""

import sys
import pytest
from pathlib import Path
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.canvas import generate_synthetic_canvas
from backend.ingestion.masking import clean_and_normalize_canvas, CleanedCanvas


def test_quality_masking_and_normalization():
    bbox = (72.5, 23.0, 72.6, 23.1)
    # Generate synthetic canvas with ~20% cloud cover
    canvas = generate_synthetic_canvas(bbox, cloud_pct_target=0.20)

    cleaned = clean_and_normalize_canvas(
        canvas_data=canvas,
        cloud_threshold=0.35,
        p_low=2.0,
        p_high=98.0
    )

    assert isinstance(cleaned, CleanedCanvas)
    assert cleaned.rgb_normalized.shape == (3, canvas.height, canvas.width)
    assert cleaned.rgb_normalized.dtype == np.uint8
    assert cleaned.cloud_mask.shape == (canvas.height, canvas.width)
    assert cleaned.shadow_mask.shape == (canvas.height, canvas.width)
    assert cleaned.bad_mask.shape == (canvas.height, canvas.width)
    assert 0.0 <= cleaned.canvas_cloud_pct <= 1.0

    # Ensure bad mask combines cloud and shadow
    assert np.array_equal(cleaned.bad_mask, (cleaned.cloud_mask | cleaned.shadow_mask))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
