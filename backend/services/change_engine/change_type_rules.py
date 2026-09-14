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
    CLASS_SPARSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_CONFUSION,
    CLASS_UNCLASSIFIED,
)

# Standardized Change Types
TYPE_CONSTRUCTION = "Construction"
TYPE_ROAD_DEVELOPMENT = "Road Development"
TYPE_CLEARANCE = "Clearance"
TYPE_WATER_SHRINKAGE = "Water-Extent Variation (shrinkage)"
TYPE_WATER_EXPANSION = "Water-Extent Variation (expansion)"
TYPE_DEMOLITION = "Demolition / Reversion"
TYPE_VEGETATION_LARGE_SCALE = "Vegetation Shift (Large Scale)"
TYPE_UNCLASSIFIED = "Unclassified Structural Change"

# User requirement: Only major anthropogenic / structural changes are marked
MAJOR_CHANGE_TYPES = {
    TYPE_CONSTRUCTION,
    TYPE_ROAD_DEVELOPMENT,
    TYPE_CLEARANCE,
    TYPE_WATER_SHRINKAGE,
    TYPE_WATER_EXPANSION,
    TYPE_DEMOLITION,
    TYPE_VEGETATION_LARGE_SCALE,
}

VEGETATION_CLASSES = {
    CLASS_DENSE_VEGETATION,
    CLASS_SPARSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    "Dense Vegetation",
    "Moderate / Sparse Vegetation",
    "Sparse Vegetation",
}

# Threshold for considering intra-vegetation changes: minimum 5 hectares (50,000 m2)
MIN_LARGE_VEG_AREA_M2 = 50000.0


def format_transition_label(before_class: str, after_class: str, change_type: str) -> str:
    """Returns a concise, standardized land-cover transition string.
    
    Examples:
    - 'Vegetation → Built-up'
    - 'Vegetation → Ground'
    - 'Ground → Built-up'
    - 'Land → Water (Extension)'
    - 'Water → Land (Shrinkage)'
    - 'Built-up → Ground'
    - 'Vegetation → Road'
    """
    b = str(before_class or "")
    a = str(after_class or "")
    t = str(change_type or "")

    if t == TYPE_ROAD_DEVELOPMENT:
        return "Vegetation → Road" if "Vegetation" in b else "Ground → Road"
    if t == TYPE_CONSTRUCTION:
        return "Vegetation → Built-up" if "Vegetation" in b else "Ground → Built-up"
    if t == TYPE_CLEARANCE:
        return "Vegetation → Ground"
    if "expansion" in t.lower() or "extension" in t.lower() or t == TYPE_WATER_EXPANSION:
        return "Land → Water (Extension)"
    if "shrinkage" in t.lower() or t == TYPE_WATER_SHRINKAGE:
        return "Water → Land (Shrinkage)"
    if t == TYPE_DEMOLITION:
        return "Built-up → Ground"
    if t == TYPE_VEGETATION_LARGE_SCALE:
        return "Vegetation Shift (Large Scale)"

    b_short = "Vegetation" if "Vegetation" in b else ("Ground" if ("Soil" in b or "Barren" in b or "Land" in b) else b)
    a_short = "Vegetation" if "Vegetation" in a else ("Ground" if ("Soil" in a or "Barren" in a or "Land" in a) else ("Built-up" if "Built" in a else a))
    return f"{b_short} → {a_short}"



def lookup_change_type(before_class: str, after_class: str, area_m2: float = 0.0) -> str:
    """Evaluates the transition matrix for a single pixel or region.
    
    Filters out non-major changes like seasonal grass sprouting,
    minor intra-vegetation shifts, and ambiguous bare-soil/built-up confusion.
    """
    if before_class == after_class:
        return "No Change"

    # User rule: "not mark the changes like spase vegetation dense vegetation like stuff"
    # 1. Intra-vegetation shifts (Dense <-> Moderate/Sparse):
    # Only considered if the area is large (>= 5 ha / 50,000 m2)
    if before_class in VEGETATION_CLASSES and after_class in VEGETATION_CLASSES:
        if area_m2 >= MIN_LARGE_VEG_AREA_M2:
            return TYPE_VEGETATION_LARGE_SCALE
        return "No Change"

    # 2. Ambiguous transitions between Bare Soil and Confusion are No Change
    is_conf_b = before_class == CLASS_CONFUSION or "Confusion" in str(before_class)
    is_conf_a = after_class == CLASS_CONFUSION or "Confusion" in str(after_class)
    is_soil_b = before_class in (CLASS_BARE_SOIL, "Bare Soil / Barren", "Bare Soil / Open Land", "Bare Soil")
    is_soil_a = after_class in (CLASS_BARE_SOIL, "Bare Soil / Barren", "Bare Soil / Open Land", "Bare Soil")
    is_built_b = before_class in (CLASS_BUILT_UP, "Built-up / Urban", "Built-up")
    is_built_a = after_class in (CLASS_BUILT_UP, "Built-up / Urban", "Built-up")

    if is_conf_b and is_conf_a:
        return "No Change"
    if (is_conf_b and is_soil_a) or (is_soil_b and is_conf_a):
        return "No Change"
    if is_conf_b and is_built_a and area_m2 < 1000.0:
        return "No Change"
    if is_built_b and is_conf_a:
        return "No Change"

    # 3. Seasonal grass/sprouting on bare soil (Re-vegetation) is NOT a major structural change
    if is_soil_b and after_class in VEGETATION_CLASSES:
        return "No Change"

    # 4. Transitions between unclassified and vegetation are non-major
    if (before_class in (CLASS_UNCLASSIFIED, "Unclassified / Transitional") and after_class in VEGETATION_CLASSES) or \
       (before_class in VEGETATION_CLASSES and after_class in (CLASS_UNCLASSIFIED, "Unclassified / Transitional")):
        return "No Change"

    # 5. Both unclassified is No Change
    if before_class in (CLASS_UNCLASSIFIED, "Unclassified / Transitional") and after_class in (CLASS_UNCLASSIFIED, "Unclassified / Transitional"):
        return "No Change"

    # --- MAJOR ANTHROPOGENIC / STRUCTURAL CHANGES ---
    # 1. Construction: Land/Bare Soil, Confusion, or Vegetation -> Built-up
    if (before_class in VEGETATION_CLASSES or is_soil_b or is_conf_b) and is_built_a:
        return TYPE_CONSTRUCTION

    # 2. Clearance: Genuine deforestation / excavation
    # Dense Vegetation -> Bare Soil / Confusion is Deforestation/Clearance
    if before_class == CLASS_DENSE_VEGETATION and (is_soil_a or is_conf_a):
        return TYPE_CLEARANCE
    # Large contiguous bare clearance (>= 5,000 m2 / 50 px)
    if before_class in VEGETATION_CLASSES and is_soil_a and area_m2 >= 5000.0:
        return TYPE_CLEARANCE
    # Routine drying of sparse crops or fallow is seasonal phenology (No Change)
    if before_class in VEGETATION_CLASSES and is_soil_a:
        return "No Change"

    # 3. Water Variation (confirmed by spectral thresholds)
    if before_class == CLASS_WATER and (is_soil_a or is_built_a or is_conf_a):
        return TYPE_WATER_SHRINKAGE
    if (is_soil_b or is_built_b or is_conf_b or before_class in VEGETATION_CLASSES) and after_class == CLASS_WATER:
        return TYPE_WATER_EXPANSION

    # 4. Demolition: Built-up -> Bare Soil ONLY (site excavation/rubble)
    # Built-up -> Vegetation is natural greening/revegetation (No Change)
    if is_built_b and is_soil_a:
        return TYPE_DEMOLITION
    if is_built_b and after_class in VEGETATION_CLASSES:
        return "No Change"

    # If transformed into built-up from any non-built class, classify as Construction
    if is_built_a and not is_built_b and not (is_conf_b and area_m2 < 1000.0):
        return TYPE_CONSTRUCTION

    # All other subtle fluctuations are strictly No Change
    return "No Change"


def vectorized_change_type_lookup(
    before_classes: np.ndarray,
    after_classes: np.ndarray,
) -> np.ndarray:
    """Vectorized transition matrix mapping before_classes and after_classes arrays.

    Returns np.ndarray of change type strings matching the shapes of input arrays.
    """
    before = np.asarray(before_classes, dtype=object)
    after = np.asarray(after_classes, dtype=object)

    output = np.full(before.shape, "No Change", dtype=object)

    is_veg_before = np.isin(before, list(VEGETATION_CLASSES))
    is_dense_before = before == CLASS_DENSE_VEGETATION
    is_sparse_before = np.isin(before, [CLASS_SPARSE_VEGETATION, CLASS_MODERATE_VEGETATION, "Sparse Vegetation", "Moderate / Sparse Vegetation"])
    is_veg_after = np.isin(after, list(VEGETATION_CLASSES))

    is_built_before = np.isin(before, [CLASS_BUILT_UP, "Built-up / Urban", "Built-up"])
    is_built_after = np.isin(after, [CLASS_BUILT_UP, "Built-up / Urban", "Built-up"])
    is_bare_before = np.isin(before, [CLASS_BARE_SOIL, "Bare Soil / Barren", "Bare Soil / Open Land", "Bare Soil"])
    is_bare_after = np.isin(after, [CLASS_BARE_SOIL, "Bare Soil / Barren", "Bare Soil / Open Land", "Bare Soil"])
    is_water_before = before == CLASS_WATER
    is_water_after = after == CLASS_WATER
    is_conf_before = (before == CLASS_CONFUSION) | (before == "Built-up / Bare-land Confusion")
    is_conf_after = (after == CLASS_CONFUSION) | (after == "Built-up / Bare-land Confusion")

    # 1. Construction: Land/Bare Soil, Confusion, or Vegetation -> Built-up
    output[(is_veg_before | is_bare_before | is_conf_before) & is_built_after] = TYPE_CONSTRUCTION

    # 2. Clearance: Dense Vegetation -> Bare Soil / Confusion (deforestation/clearing)
    output[is_dense_before & (is_bare_after | is_conf_after)] = TYPE_CLEARANCE

    # 3. Water Variation
    output[is_water_before & (is_bare_after | is_built_after | is_conf_after)] = TYPE_WATER_SHRINKAGE
    output[(is_bare_before | is_built_before | is_conf_before | is_veg_before) & is_water_after] = TYPE_WATER_EXPANSION

    # 4. Demolition: Built-up -> Bare Soil ONLY (rubble/excavation)
    output[is_built_before & is_bare_after] = TYPE_DEMOLITION

    # 5. Suppress seasonal vegetation shifts (Greening, Crop harvest/fallow, Sprouting)
    output[is_built_before & is_veg_after] = "No Change"
    output[is_bare_before & is_veg_after] = "No Change"
    output[is_sparse_before & is_bare_after] = "No Change"

    # 6. Suppress subtle confusion fluctuations
    is_conf_noise = (
        (is_conf_before & is_conf_after)
        | (is_conf_before & is_bare_after)
        | (is_bare_before & is_conf_after)
    )
    output[is_conf_noise] = "No Change"

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
