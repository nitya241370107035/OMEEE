"""Semantic Change Filter module for AeroLens Change Engine.

Implements rule §2.4:
Semantic contradiction = false positive.
If binary_mask == 1 but before_class == after_class, the change is rejected
as seasonal phenology or illumination noise.
"""
from typing import Tuple
import numpy as np

VEGETATION_CLASSES = {"Dense Vegetation", "Moderate / Sparse Vegetation"}
CLASS_UNCLASSIFIED = "Unclassified / Transitional"


def filter_semantic_changes(
    before_classes: np.ndarray,
    after_classes: np.ndarray,
    binary_mask: np.ndarray = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Filters out false-positive changes where before_class == after_class or surface is unchanged.

    Args:
        before_classes: 1D or 2D array of class strings for time T_before
        after_classes: 1D or 2D array of class strings for time T_after
        binary_mask: Optional boolean or uint8 mask indicating candidate pixels.
                     If None, all elements where before != after are kept.

    Returns:
        surviving_mask: Boolean array where change is confirmed (genuine transition AND binary_mask)
        stats: dict containing total candidates, rejected false positives, kept real changes.
    """
    before_arr = np.asarray(before_classes, dtype=object)
    after_arr = np.asarray(after_classes, dtype=object)

    if before_arr.shape != after_arr.shape:
        raise ValueError(
            f"Shape mismatch: before {before_arr.shape} vs after {after_arr.shape}"
        )

    # 1. Reject identical classes (unchanged surface)
    is_same_class = before_arr == after_arr

    # 2. Reject intra-vegetation seasonal shifts (Dense <-> Moderate) as seasonal phenology
    is_veg_b = np.isin(before_arr, list(VEGETATION_CLASSES))
    is_veg_a = np.isin(after_arr, list(VEGETATION_CLASSES))
    is_veg_phenology = is_veg_b & is_veg_a

    # 3. Reject persistent unclassified/transitional surfaces
    is_both_unclassified = (before_arr == CLASS_UNCLASSIFIED) & (after_arr == CLASS_UNCLASSIFIED)

    # Unchanged surface condition
    is_unchanged = is_same_class | is_veg_phenology | is_both_unclassified
    is_class_change = ~is_unchanged

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
