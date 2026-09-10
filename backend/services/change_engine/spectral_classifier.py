"""Spectral Classifier module for AeroLens Change Engine.

Computes independent NDVI, NDWI, and NDBI indices and assigns semantic land-cover
classes based on §2.4 of implement_part2.md.
"""
from typing import Dict, Tuple, Union
import numpy as np

# Class Label Constants
CLASS_WATER = "Water"
CLASS_DENSE_VEGETATION = "Dense Vegetation"
CLASS_MODERATE_VEGETATION = "Moderate / Sparse Vegetation"
CLASS_BUILT_UP = "Built-up / Urban"
CLASS_BARE_SOIL = "Bare Soil / Barren"
CLASS_UNCLASSIFIED = "Unclassified / Transitional"

CLASS_NAMES = [
    CLASS_WATER,
    CLASS_DENSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_UNCLASSIFIED,
]


def compute_spectral_indices(
    b_green: np.ndarray,
    b_red: np.ndarray,
    b_nir: np.ndarray,
    b_swir: np.ndarray,
    eps: float = 1e-6,
) -> Dict[str, np.ndarray]:
    """Computes NDVI, NDWI, and NDBI from multi-spectral band reflectance arrays.

    All arrays must have identical shape and float values.

    Indices:
        NDVI = (NIR - Red) / (NIR + Red)
        NDWI = (Green - NIR) / (Green + NIR) (McFeeters, 1996)
        NDBI = (SWIR - NIR) / (SWIR + NIR)   (Zha et al., 2003)
    """
    nir = b_nir.astype(np.float32)
    red = b_red.astype(np.float32)
    green = b_green.astype(np.float32)
    swir = b_swir.astype(np.float32)

    ndvi = (nir - red) / (nir + red + eps)
    ndwi = (green - nir) / (green + nir + eps)
    ndbi = (swir - nir) / (swir + nir + eps)

    # Clip mathematically bounded to [-1.0, 1.0]
    return {
        "ndvi": np.clip(ndvi, -1.0, 1.0),
        "ndwi": np.clip(ndwi, -1.0, 1.0),
        "ndbi": np.clip(ndbi, -1.0, 1.0),
    }


def classify_pixels(
    ndvi: np.ndarray,
    ndwi: np.ndarray,
    ndbi: np.ndarray,
) -> np.ndarray:
    """Vectorized semantic classification using §2.4 rule table.

    Evaluated top-to-bottom (first match wins):
    1. Water: NDWI > 0.3 AND NDBI < -0.1
    2. Dense Vegetation: NDVI > 0.6 AND NDBI < 0
    3. Moderate / Sparse Vegetation: 0.2 <= NDVI <= 0.6 AND NDBI < 0
    4. Built-up / Urban: NDBI > 0 AND NDVI <= 0.2
    5. Bare Soil / Barren: NDVI <= 0.1 AND NDBI <= 0 AND NDWI <= 0
    6. Unclassified / Transitional: fallback

    Args:
        ndvi, ndwi, ndbi: 1D or 2D numpy arrays with values in [-1, 1]

    Returns:
        np.ndarray of string class labels with identical shape.
    """
    ndvi = np.asarray(ndvi, dtype=np.float32)
    ndwi = np.asarray(ndwi, dtype=np.float32)
    ndbi = np.asarray(ndbi, dtype=np.float32)

    # Default to Unclassified
    result = np.full(ndvi.shape, CLASS_UNCLASSIFIED, dtype=object)

    # Condition masks
    is_water = (ndwi > 0.3) & (ndbi < -0.1)
    is_dense_veg = (ndvi > 0.6) & (ndbi < 0.0)
    is_mod_veg = (ndvi >= 0.2) & (ndvi <= 0.6) & (ndbi < 0.0)
    is_built_up = (ndbi > 0.0) & (ndvi <= 0.2)
    is_bare_soil = (ndvi <= 0.1) & (ndbi <= 0.0) & (ndwi <= 0.0)

    # Apply in priority order: Priority 5 up to 1 so 1 overwrites 2, etc.,
    # or apply conditionally with unassigned mask
    unassigned = np.ones(ndvi.shape, dtype=bool)

    # Priority 1: Water
    mask1 = unassigned & is_water
    result[mask1] = CLASS_WATER
    unassigned[mask1] = False

    # Priority 2: Dense Vegetation
    mask2 = unassigned & is_dense_veg
    result[mask2] = CLASS_DENSE_VEGETATION
    unassigned[mask2] = False

    # Priority 3: Moderate / Sparse Vegetation
    mask3 = unassigned & is_mod_veg
    result[mask3] = CLASS_MODERATE_VEGETATION
    unassigned[mask3] = False

    # Priority 4: Built-up / Urban
    mask4 = unassigned & is_built_up
    result[mask4] = CLASS_BUILT_UP
    unassigned[mask4] = False

    # Priority 5: Bare Soil / Barren
    mask5 = unassigned & is_bare_soil
    result[mask5] = CLASS_BARE_SOIL
    unassigned[mask5] = False

    return result
