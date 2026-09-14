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

# User requirement: Only major anthropogenic / structural changes are marked
MAJOR_CHANGE_TYPES = {
    TYPE_CONSTRUCTION,
    TYPE_ROAD_DEVELOPMENT,
    TYPE_CLEARANCE,
    TYPE_WATER_SHRINKAGE,
    TYPE_WATER_EXPANSION,
    TYPE_DEMOLITION,
}

VEGETATION_CLASSES = {CLASS_DENSE_VEGETATION, CLASS_MODERATE_VEGETATION}


def lookup_change_type(before_class: str, after_class: str) -> str:
    """Evaluates the transition matrix for a single pixel or region.
    
    Strictly filters out non-major changes like seasonal grass sprouting or
    intra-vegetation shifts (dense <-> sparse vegetation).
    """
    if before_class == after_class:
        return "No Change"

    # User rule: "not mark the changes like spase vegetation dense vegetation like stuff"
    # 1. Intra-vegetation shifts (Dense <-> Moderate/Sparse) are seasonal phenology
    if before_class in VEGETATION_CLASSES and after_class in VEGETATION_CLASSES:
        return "No Change"

    # 2. Seasonal grass/sprouting on bare soil (Re-vegetation) is NOT a major change
    if before_class == CLASS_BARE_SOIL and after_class in VEGETATION_CLASSES:
        return "No Change"

    # 3. Transitions between unclassified and vegetation are non-major
    if (before_class == CLASS_UNCLASSIFIED and after_class in VEGETATION_CLASSES) or \
       (before_class in VEGETATION_CLASSES and after_class == CLASS_UNCLASSIFIED):
        return "No Change"

    # 4. Both unclassified is No Change
    if before_class == CLASS_UNCLASSIFIED and after_class == CLASS_UNCLASSIFIED:
        return "No Change"

    # --- MAJOR STRUCTURAL CHANGES ---
    # Construction: Land/Bare Soil or Vegetation -> Built-up
    if (before_class in VEGETATION_CLASSES or before_class == CLASS_BARE_SOIL) and after_class == CLASS_BUILT_UP:
        return TYPE_CONSTRUCTION

    # Clearance: Vegetation -> Bare Soil (excavation / site prep)
    if before_class in VEGETATION_CLASSES and after_class == CLASS_BARE_SOIL:
        return TYPE_CLEARANCE

    # Water Shrinkage: Water -> Land
    if before_class == CLASS_WATER and after_class in (CLASS_BARE_SOIL, CLASS_BUILT_UP):
        return TYPE_WATER_SHRINKAGE

    # Water Expansion: Land -> Water
    if before_class in (CLASS_BARE_SOIL, CLASS_BUILT_UP) and after_class == CLASS_WATER:
        return TYPE_WATER_EXPANSION

    # Demolition / Reversion: Built-up -> Bare Soil / Ground
    if before_class == CLASS_BUILT_UP and (after_class in VEGETATION_CLASSES or after_class == CLASS_BARE_SOIL):
        return TYPE_DEMOLITION

    # If transformed into built-up from any non-built class, classify as Construction
    if after_class == CLASS_BUILT_UP and before_class != CLASS_BUILT_UP:
        return TYPE_CONSTRUCTION

    # If previously built-up and disappeared, classify as Demolition
    if before_class == CLASS_BUILT_UP and after_class != CLASS_BUILT_UP:
        return TYPE_DEMOLITION

    # All other vegetation/subtle fluctuations are strictly No Change
    return "No Change"


def vectorized_change_type_lookup(
    before_classes: np.ndarray,
    after_classes: np.ndarray,
) -> np.ndarray:
    """Vectorized transition matrix assignment for 1D or 2D arrays.
    
    Restricted strictly to major structural / anthropogenic changes.
    """
    before = np.asarray(before_classes, dtype=object)
    after = np.asarray(after_classes, dtype=object)

    output = np.full(before.shape, "No Change", dtype=object)

    is_veg_before = (before == CLASS_DENSE_VEGETATION) | (before == CLASS_MODERATE_VEGETATION)
    is_veg_after = (after == CLASS_DENSE_VEGETATION) | (after == CLASS_MODERATE_VEGETATION)
    is_built_before = before == CLASS_BUILT_UP
    is_built_after = after == CLASS_BUILT_UP
    is_bare_before = before == CLASS_BARE_SOIL
    is_bare_after = after == CLASS_BARE_SOIL
    is_water_before = before == CLASS_WATER
    is_water_after = after == CLASS_WATER

    # 1. Construction: Land/Bare Soil or Vegetation -> Built-up
    output[(is_veg_before | is_bare_before) & is_built_after] = TYPE_CONSTRUCTION

    # 2. Clearance: Vegetation -> Bare Soil (major land clearing)
    output[is_veg_before & is_bare_after] = TYPE_CLEARANCE

    # 3. Water Variation
    output[is_water_before & (is_bare_after | is_built_after)] = TYPE_WATER_SHRINKAGE
    output[(is_bare_before | is_built_before) & is_water_after] = TYPE_WATER_EXPANSION

    # 4. Demolition: Built-up -> Land
    output[is_built_before & (is_bare_after | is_veg_after)] = TYPE_DEMOLITION

    # 5. Any other new built-up is Construction
    output[(output == "No Change") & is_built_after & (~is_built_before)] = TYPE_CONSTRUCTION

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
