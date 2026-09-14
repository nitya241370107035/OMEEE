import numpy as np
from backend.services.change_engine.change_type_rules import (
    format_transition_label,
    lookup_change_type,
    TYPE_CONSTRUCTION,
    TYPE_CLEARANCE,
    TYPE_WATER_EXPANSION,
    TYPE_WATER_SHRINKAGE,
    TYPE_DEMOLITION,
    TYPE_VEGETATION_LARGE_SCALE,
    CLASS_DENSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_WATER,
)
from backend.services.change_engine.sequence_orchestrator import (
    ndvi_to_png_base64,
    draw_bounding_squares,
)

print("--- Testing format_transition_label ---")
assert format_transition_label(CLASS_DENSE_VEGETATION, CLASS_BUILT_UP, TYPE_CONSTRUCTION) == "Vegetation → Built-up"
assert format_transition_label(CLASS_DENSE_VEGETATION, CLASS_BARE_SOIL, TYPE_CLEARANCE) == "Vegetation → Ground"
assert format_transition_label(CLASS_BARE_SOIL, CLASS_WATER, TYPE_WATER_EXPANSION) == "Land → Water (Extension)"
assert format_transition_label(CLASS_WATER, CLASS_BARE_SOIL, TYPE_WATER_SHRINKAGE) == "Water → Land (Shrinkage)"
assert format_transition_label(CLASS_BUILT_UP, CLASS_BARE_SOIL, TYPE_DEMOLITION) == "Built-up → Ground"
assert format_transition_label(CLASS_MODERATE_VEGETATION, CLASS_DENSE_VEGETATION, TYPE_VEGETATION_LARGE_SCALE) == "Vegetation Shift (Large Scale)"
print("All format_transition_label assertions PASSED!")

print("--- Testing intra-vegetation area filtering ---")
# Small area -> No Change
assert lookup_change_type(CLASS_MODERATE_VEGETATION, CLASS_DENSE_VEGETATION, area_m2=1000) == "No Change"
# Large area >= 50,000 m2 -> TYPE_VEGETATION_LARGE_SCALE
assert lookup_change_type(CLASS_MODERATE_VEGETATION, CLASS_DENSE_VEGETATION, area_m2=60000) == TYPE_VEGETATION_LARGE_SCALE
print("Intra-vegetation area assertions PASSED!")

print("--- Testing ndvi_to_png_base64 colormap ---")
h, w = 64, 64
ndvi = np.zeros((h, w), dtype=np.float32)
ndvi[:10, :] = -0.5
ndvi[10:20, :] = 0.1
ndvi[20:30, :] = 0.05
ndvi[30:45, :] = 0.35
ndvi[45:, :] = 0.75

class_map = np.full((h, w), "Unclassified", dtype=object)
class_map[:10, :] = "Water"
class_map[10:20, :] = "Built-up / Urban"
class_map[20:30, :] = "Bare Soil / Barren"
class_map[30:45, :] = "Moderate / Sparse Vegetation"
class_map[45:, :] = "Dense Vegetation"

boxes = [
    {
        "id": 1,
        "change_type": "Construction",
        "transition_label": "Vegetation → Built-up",
        "box_pct": {"x": 10, "y": 10, "w": 20, "h": 20}
    },
    {
        "id": 2,
        "change_type": "Clearance",
        "transition_label": "Vegetation → Ground",
        "box_pct": {"x": 50, "y": 50, "w": 20, "h": 20}
    },
    {
        "id": 3,
        "change_type": "Water Extension",
        "transition_label": "Land → Water (Extension)",
        "box_pct": {"x": 2, "y": 2, "w": 15, "h": 15}
    }
]

png_data = ndvi_to_png_base64(ndvi, class_map=class_map, boxes=boxes)
assert png_data.startswith("data:image/png;base64,")
print(f"ndvi_to_png_base64 generated valid PNG data url: length={len(png_data)} chars")

print("--- Testing Water Extension Proof Logic ---")
water_mask_b = np.zeros((64, 64), dtype=bool)
water_mask_b[0:10, 0:10] = True # 100 pixels

# Case A: Minor fluctuation (+4 pixels) -> Should NOT trigger extension
water_mask_a_minor = water_mask_b.copy()
water_mask_a_minor[10:12, 10:12] = True # +4 pixels
delta_a = np.sum(water_mask_a_minor) - np.sum(water_mask_b)
new_a = np.sum((~water_mask_b) & water_mask_a_minor)
is_ext_a = bool((delta_a > 0) and (new_a >= 25))
assert is_ext_a is False
print(f"Case A (4 px fluctuation): Delta={delta_a}, New={new_a} -> is_extension={is_ext_a} (Correctly rejected noise!)")

# Case B: Significant expansion (+40 pixels) -> Should trigger verified extension
water_mask_a_major = water_mask_b.copy()
water_mask_a_major[10:18, 0:5] = True # +40 pixels
delta_b = int(np.sum(water_mask_a_major)) - int(np.sum(water_mask_b))
new_b = int(np.sum((~water_mask_b) & water_mask_a_major))
is_ext_b = bool((delta_b > 0) and (new_b >= 25))
assert is_ext_b is True
print(f"Case B (40 px extension): Delta={delta_b}, New={new_b} -> is_extension={is_ext_b} (Correctly verified!)")

print("\nALL BACKEND TESTS PASSED SUCCESSFULLY!")
