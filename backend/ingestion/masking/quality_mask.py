"""
Phase 2 Quality Mask Combiner

Combines s2cloudless binary cloud mask with shadow adapter mask to produce
the unified bad pixel mask for radiometric normalization and tile quality gating.
"""

import logging
from dataclasses import dataclass
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class QualityMaskResult:
    """Encapsulates all quality and cloud masking outputs for a canvas."""
    cloud_prob: np.ndarray       # Shape (H, W), float32 [0.0, 1.0]
    cloud_mask: np.ndarray       # Shape (H, W), bool (True = cloud)
    shadow_mask: np.ndarray      # Shape (H, W), bool (True = shadow)
    bad_mask: np.ndarray         # Shape (H, W), bool (True = cloud OR shadow)
    canvas_cloud_pct: float      # Fraction of bad pixels across canvas (0.0 to 1.0)
    valid_pixel_pct: float       # Fraction of clear/valid pixels across canvas (0.0 to 1.0)


def combine_quality_masks(
    cloud_prob: np.ndarray,
    cloud_mask: np.ndarray,
    shadow_mask: np.ndarray
) -> QualityMaskResult:
    """
    Merges cloud and shadow masks and computes overall canvas quality statistics.

    Args:
        cloud_prob: 2D float32 cloud probability map [0.0, 1.0].
        cloud_mask: 2D boolean cloud mask.
        shadow_mask: 2D boolean shadow mask.

    Returns:
        QualityMaskResult containing individual and combined masks with statistics.
    """
    bad_mask = cloud_mask | shadow_mask

    total_pixels = bad_mask.size
    bad_pixels = int(np.count_nonzero(bad_mask))

    canvas_cloud_pct = round(bad_pixels / total_pixels, 4) if total_pixels > 0 else 0.0
    valid_pixel_pct = round(1.0 - canvas_cloud_pct, 4)

    logger.info(
        f"Quality mask combined: Total={total_pixels} px, "
        f"Cloud+Shadow={bad_pixels} ({canvas_cloud_pct * 100:.2f}%), "
        f"Valid={total_pixels - bad_pixels} ({valid_pixel_pct * 100:.2f}%)"
    )

    return QualityMaskResult(
        cloud_prob=cloud_prob,
        cloud_mask=cloud_mask,
        shadow_mask=shadow_mask,
        bad_mask=bad_mask,
        canvas_cloud_pct=canvas_cloud_pct,
        valid_pixel_pct=valid_pixel_pct
    )
