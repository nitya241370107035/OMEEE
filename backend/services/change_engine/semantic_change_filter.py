"""Semantic Change Filter module for AeroLens Change Engine.

Implements rule §2.4:
Semantic contradiction = false positive.
If binary_mask == 1 but before_class == after_class, the change is rejected
as seasonal phenology or illumination noise.
"""
from typing import Tuple
import numpy as np

from backend.services.change_engine.spectral_classifier import (
    CLASS_WATER,
    CLASS_DENSE_VEGETATION,
    CLASS_SPARSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_CONFUSION,
    CLASS_UNCLASSIFIED,
)

VEGETATION_CLASSES = {
    CLASS_DENSE_VEGETATION,
    CLASS_SPARSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    "Dense Vegetation",
    "Moderate / Sparse Vegetation",
    "Sparse Vegetation",
}


def filter_semantic_changes(
    before_classes: np.ndarray,
    after_classes: np.ndarray,
    binary_mask: np.ndarray = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Filters out false-positive changes where before_class == after_class,
    intra-vegetation phenology, or ambiguous bare-soil / built-up confusion.

    Args:
        before_classes: 1D or 2D array of class strings for time T_before
        after_classes: 1D or 2D array of class strings for time T_after
        binary_mask: Optional boolean or uint8 mask indicating candidate pixels.
                     If None, all elements where genuine change exists are kept.

    Returns:
        surviving_mask: Boolean array where change is confirmed
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

    # 2. Reject intra-vegetation seasonal shifts (Dense <-> Sparse) as seasonal phenology
    is_veg_b = np.isin(before_arr, list(VEGETATION_CLASSES))
    is_veg_a = np.isin(after_arr, list(VEGETATION_CLASSES))
    is_veg_phenology = is_veg_b & is_veg_a

    # 3. Reject persistent unclassified/transitional surfaces
    is_both_unclassified = (
        (np.isin(before_arr, [CLASS_UNCLASSIFIED, "Unclassified / Transitional"]))
        & (np.isin(after_arr, [CLASS_UNCLASSIFIED, "Unclassified / Transitional"]))
    )

    # 4. Reject ambiguous transitions between Bare Soil, Built-up, and Confusion zone
    # Because NDBI [0.00, 0.15) cannot be confidently distinguished, fluctuations
    # to/from Bare Soil or Built-up are suppressed to eliminate false positives.
    is_soil_b = np.isin(before_arr, [CLASS_BARE_SOIL, "Bare Soil / Barren", "Bare Soil"])
    is_soil_a = np.isin(after_arr, [CLASS_BARE_SOIL, "Bare Soil / Barren", "Bare Soil"])
    is_built_b = np.isin(before_arr, [CLASS_BUILT_UP, "Built-up / Urban"])
    is_built_a = np.isin(after_arr, [CLASS_BUILT_UP, "Built-up / Urban"])
    is_conf_b = before_arr == CLASS_CONFUSION
    is_conf_a = after_arr == CLASS_CONFUSION

    is_confusion_noise = (
        (is_conf_b & is_conf_a)
        | (is_conf_b & is_soil_a)
        | (is_soil_b & is_conf_a)
    )

    # Unchanged or unconfirmed surface condition
    is_unchanged = is_same_class | is_veg_phenology | is_both_unclassified | is_confusion_noise
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
