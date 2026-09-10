"""Change Type Transition Matrix and Morphology Rules for AeroLens Change Engine.

Implements §2.5:
- Transition Matrix lookup (Vegetation -> Built-up = Construction, etc.)
- Morphology sub-rule (§2.5.1) for Road Development using elongation ratio > 4.
"""
from typing import List, Union
import numpy as np
from skimage.measure import label, regionprops

from backend.services.change_engine.spectral_classifier import (
    CLASS_WATER,
    CLASS_DENSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_UNCLASSIFIED,
)

# Standardized Change Types
TYPE_CONSTRUCTION = "Construction"
TYPE_ROAD_DEVELOPMENT = "Road Development"
TYPE_CLEARANCE = "Clearance"
TYPE_WATER_SHRINKAGE = "Water-Extent Variation (shrinkage)"
TYPE_WATER_EXPANSION = "Water-Extent Variation (expansion)"
TYPE_DEMOLITION = "Demolition / Reversion"
TYPE_UNCLASSIFIED = "Unclassified Structural Change"

VEGETATION_CLASSES = {CLASS_DENSE_VEGETATION, CLASS_MODERATE_VEGETATION}


def lookup_change_type(before_class: str, after_class: str) -> str:
    """Evaluates the transition matrix for a single pixel or region."""
    if before_class == after_class:
        return "No Change"

    # Construction: Vegetation -> Built-up or Bare Soil -> Built-up
    if (before_class in VEGETATION_CLASSES or before_class == CLASS_BARE_SOIL) and after_class == CLASS_BUILT_UP:
        return TYPE_CONSTRUCTION

    # Clearance: Vegetation -> Bare Soil
    if before_class in VEGETATION_CLASSES and after_class == CLASS_BARE_SOIL:
        return TYPE_CLEARANCE

    # Water Shrinkage: Water -> Bare Soil or Vegetation
    if before_class == CLASS_WATER and (after_class == CLASS_BARE_SOIL or after_class in VEGETATION_CLASSES):
        return TYPE_WATER_SHRINKAGE

    # Water Expansion: Bare Soil or Vegetation -> Water
    if (before_class == CLASS_BARE_SOIL or before_class in VEGETATION_CLASSES) and after_class == CLASS_WATER:
        return TYPE_WATER_EXPANSION

    # Demolition / Reversion: Built-up -> Vegetation or Bare Soil
    if before_class == CLASS_BUILT_UP and (after_class in VEGETATION_CLASSES or after_class == CLASS_BARE_SOIL):
        return TYPE_DEMOLITION

    return TYPE_UNCLASSIFIED


def vectorized_change_type_lookup(
    before_classes: np.ndarray,
    after_classes: np.ndarray,
) -> np.ndarray:
    """Vectorized transition matrix assignment for 1D or 2D arrays."""
    before = np.asarray(before_classes, dtype=object)
    after = np.asarray(after_classes, dtype=object)

    output = np.full(before.shape, TYPE_UNCLASSIFIED, dtype=object)

    is_veg_before = (before == CLASS_DENSE_VEGETATION) | (before == CLASS_MODERATE_VEGETATION)
    is_veg_after = (after == CLASS_DENSE_VEGETATION) | (after == CLASS_MODERATE_VEGETATION)
    is_built_before = before == CLASS_BUILT_UP
    is_built_after = after == CLASS_BUILT_UP
    is_bare_before = before == CLASS_BARE_SOIL
    is_bare_after = after == CLASS_BARE_SOIL
    is_water_before = before == CLASS_WATER
    is_water_after = after == CLASS_WATER

    # Construction
    output[(is_veg_before | is_bare_before) & is_built_after] = TYPE_CONSTRUCTION

    # Clearance
    output[is_veg_before & is_bare_after] = TYPE_CLEARANCE

    # Water Shrinkage
    output[is_water_before & (is_bare_after | is_veg_after)] = TYPE_WATER_SHRINKAGE

    # Water Expansion
    output[(is_bare_before | is_veg_before) & is_water_after] = TYPE_WATER_EXPANSION

    # Demolition
    output[is_built_before & (is_veg_after | is_bare_after)] = TYPE_DEMOLITION

    # Identical classes remain No Change
    output[before == after] = "No Change"

    return output


def refine_road_morphology(
    component_mask: np.ndarray,
    current_type: str = TYPE_CONSTRUCTION,
    elongation_threshold: float = 4.0,
    min_area_px: int = 15,
) -> Tuple_Type if False else str:
    """Evaluates the elongation ratio of a binary connected component.

    §2.5.1:
    elongation_ratio = major_axis_length / minor_axis_length
    If elongation_ratio > 4 (thin and long, not blob-shaped), re-label that
    specific polygon Road Development instead of generic Construction.
    """
    if current_type not in (TYPE_CONSTRUCTION, TYPE_UNCLASSIFIED):
        return current_type

    mask_bool = np.asarray(component_mask, dtype=bool)
    if not np.any(mask_bool) or np.sum(mask_bool) < min_area_px:
        return current_type

    labeled = label(mask_bool)
    props = regionprops(labeled)
    if not props:
        return current_type

    # Take the largest component in the mask
    largest = max(props, key=lambda p: p.area)
    # Handle skimage 0.26+ axis length property names with backwards compatibility
    major = float(getattr(largest, "axis_major_length", None) or largest.major_axis_length)
    minor = float(getattr(largest, "axis_minor_length", None) or largest.minor_axis_length)

    if minor > 1e-3:
        ratio = major / minor
        if ratio >= elongation_threshold:
            return TYPE_ROAD_DEVELOPMENT

    return current_type
