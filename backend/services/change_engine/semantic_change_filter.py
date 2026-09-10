"""Semantic Change Filter module for AeroLens Change Engine.

Implements rule §2.4:
Semantic contradiction = false positive.
If binary_mask == 1 but before_class == after_class, the change is rejected
as seasonal phenology or illumination noise.
"""
from typing import Tuple
import numpy as np


def filter_semantic_changes(
    before_classes: np.ndarray,
    after_classes: np.ndarray,
    binary_mask: np.ndarray = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Filters out false-positive changes where before_class == after_class.

    Args:
        before_classes: 1D or 2D array of class strings for time T_before
        after_classes: 1D or 2D array of class strings for time T_after
        binary_mask: Optional boolean or uint8 mask indicating candidate pixels.
                     If None, all elements where before != after are kept.

    Returns:
        surviving_mask: Boolean array where change is confirmed (before != after AND binary_mask)
        stats: dict containing total candidates, rejected false positives, kept real changes.
    """
    before_arr = np.asarray(before_classes, dtype=object)
    after_arr = np.asarray(after_classes, dtype=object)

    if before_arr.shape != after_arr.shape:
        raise ValueError(
            f"Shape mismatch: before {before_arr.shape} vs after {after_arr.shape}"
        )

    # Real semantic change requires class transition
    is_class_change = before_arr != after_arr

    if binary_mask is not None:
        cand_mask = np.asarray(binary_mask) > 0
        surviving_mask = cand_mask & is_class_change
        total_candidates = int(np.sum(cand_mask))
    else:
        surviving_mask = is_class_change
        total_candidates = int(before_arr.size)

    kept_changes = int(np.sum(surviving_mask))
    false_positives = total_candidates - kept_changes

    stats = {
        "candidate_changes": total_candidates,
        "false_positives_rejected": false_positives,
        "verified_changes": kept_changes,
        "rejection_ratio": round(
            float(false_positives / total_candidates) if total_candidates > 0 else 0.0,
            4,
        ),
    }

    return surviving_mask, stats
