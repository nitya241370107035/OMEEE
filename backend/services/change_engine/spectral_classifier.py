"""Spectral Classifier module for AeroLens Change Engine.

Computes independent NDVI, NDWI, and NDBI indices and assigns semantic land-cover
classes based on §2.4 of implement_part2.md.
"""
from typing import Dict, Tuple, Union, Optional
import numpy as np

# Class Label Constants
CLASS_WATER = "Water"
CLASS_DENSE_VEGETATION = "Dense Vegetation"
CLASS_SPARSE_VEGETATION = "Sparse Vegetation"
CLASS_MODERATE_VEGETATION = CLASS_SPARSE_VEGETATION  # Backward-compatible alias
CLASS_BUILT_UP = "Built-up"
CLASS_BARE_SOIL = "Bare Soil / Open Land"
CLASS_CONFUSION = "Built-up / Bare-land Confusion"
CLASS_UNCLASSIFIED = "Unclassified / Other"

CLASS_NAMES = [
    CLASS_WATER,
    CLASS_DENSE_VEGETATION,
    CLASS_SPARSE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_CONFUSION,
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
    b_nir: Optional[np.ndarray] = None,
    b_red: Optional[np.ndarray] = None,
    b_swir: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Vectorized semantic classification using the 6-class spectral rules:

    Class                             NDVI              NDWI              NDBI              Interpretation
    💧 Water                          -0.50 to 0.20     0.20 to 1.00      -1.00 to -0.10    Strong water evidence
    🌳 Dense vegetation               0.50 to 0.90+     -0.60 to 0.10     -0.60 to -0.10    Strong vegetation evidence
    🌱 Sparse vegetation              0.20 to <0.50     -0.50 to 0.10     -0.40 to 0.10     Moderate vegetation evidence
    🏢 Built-up                       -0.10 to 0.30     -0.50 to 0.10     >=0.15 to 0.50    Stronger built-up evidence
    🟤 Bare soil / open land          -0.20 to <0.20    -0.50 to 0.10     -0.20 to <0.00    Stronger bare-land evidence
    ⚠️ Built-up / Bare-land confusion  -0.10 to 0.20     -0.50 to 0.10     0.00 to <0.15     Cannot confidently distinguish

    Args:
        ndvi, ndwi, ndbi: 1D or 2D numpy arrays with values in [-1, 1]
        b_nir, b_red, b_swir: Optional surface reflectance arrays [0.0, 1.5] for radiometric dark water detection.

    Returns:
        np.ndarray of string class labels with identical shape.
    """
    ndvi = np.asarray(ndvi, dtype=np.float32)
    ndwi = np.asarray(ndwi, dtype=np.float32)
    ndbi = np.asarray(ndbi, dtype=np.float32)

    result = np.full(ndvi.shape, CLASS_UNCLASSIFIED, dtype=object)
    unassigned = np.ones(ndvi.shape, dtype=bool)

    # Priority 1: 💧 Water
    # Strong water evidence: NDWI >= 0.20, NDBI <= -0.10, NDVI in [-0.50, 0.20]
    is_water = (
        (ndwi >= 0.20)
        & (ndbi <= -0.10)
        & (ndvi >= -0.50)
        & (ndvi <= 0.20)
    )
    # Radiometric dark water body detection: deep/sedimented water absorbs >95% light across NIR & SWIR
    if b_nir is not None and b_red is not None and b_swir is not None:
        dark_water = (
            (b_nir < 0.045)
            & (b_red < 0.040)
            & (b_swir < 0.045)
            & (ndvi < 0.15)
        )
        is_water = is_water | dark_water

    mask_water = unassigned & is_water
    result[mask_water] = CLASS_WATER
    unassigned[mask_water] = False

    # Priority 2: 🌳 Dense vegetation
    # Strong vegetation evidence: NDVI >= 0.50, NDWI <= 0.10, NDBI <= -0.10
    is_dense_veg = (
        (ndvi >= 0.50)
        & (ndwi >= -0.60)
        & (ndwi <= 0.10)
        & (ndbi <= -0.10)
    )
    mask_dense = unassigned & is_dense_veg
    result[mask_dense] = CLASS_DENSE_VEGETATION
    unassigned[mask_dense] = False

    # Priority 3: 🌱 Sparse vegetation
    # Moderate vegetation evidence: 0.20 <= NDVI < 0.50, NDWI in [-0.50, 0.10], NDBI in [-0.40, 0.10]
    is_sparse_veg = (
        (ndvi >= 0.20)
        & (ndvi < 0.50)
        & (ndwi >= -0.50)
        & (ndwi <= 0.10)
        & (ndbi >= -0.40)
        & (ndbi <= 0.10)
    )
    mask_sparse = unassigned & is_sparse_veg
    result[mask_sparse] = CLASS_SPARSE_VEGETATION
    unassigned[mask_sparse] = False

    # Priority 4: 🏢 Built-up
    # Stronger built-up evidence: NDBI >= 0.15, NDVI in [-0.10, 0.30], NDWI in [-0.50, 0.10]
    is_built_up = (
        (ndvi >= -0.10)
        & (ndvi <= 0.30)
        & (ndwi >= -0.50)
        & (ndwi <= 0.10)
        & (ndbi >= 0.15)
    )
    mask_built = unassigned & is_built_up
    result[mask_built] = CLASS_BUILT_UP
    unassigned[mask_built] = False

    # Priority 5: 🟤 Bare soil / open land
    # Stronger bare-land evidence: -0.20 <= NDBI < 0.00, -0.20 <= NDVI < 0.20, NDWI in [-0.50, 0.10]
    is_bare_soil = (
        (ndvi >= -0.20)
        & (ndvi < 0.20)
        & (ndwi >= -0.50)
        & (ndwi <= 0.10)
        & (ndbi >= -0.20)
        & (ndbi < 0.00)
    )
    mask_soil = unassigned & is_bare_soil
    result[mask_soil] = CLASS_BARE_SOIL
    unassigned[mask_soil] = False

    # Priority 6: ⚠️ Built-up / Bare-land confusion
    # Cannot confidently distinguish: 0.00 <= NDBI < 0.15, -0.10 <= NDVI <= 0.20, NDWI in [-0.50, 0.10]
    is_confusion = (
        (ndvi >= -0.10)
        & (ndvi <= 0.20)
        & (ndwi >= -0.50)
        & (ndwi <= 0.10)
        & (ndbi >= 0.00)
        & (ndbi < 0.15)
    )
    mask_confusion = unassigned & is_confusion
    result[mask_confusion] = CLASS_CONFUSION
    unassigned[mask_confusion] = False

    # Priority 7: Contextual residual fallback for out-of-box/edge index values
    if np.any(unassigned):
        # Out-of-box Water (e.g. NDWI > 0.20 even if NDVI slightly outside [-0.50, 0.20], or negative NDVI with near-zero NDWI)
        fb_water = unassigned & (
            (ndwi >= 0.20)
            | ((ndwi > 0.05) & (ndvi < -0.10))
            | ((ndvi < 0.00) & (ndwi > -0.10) & (ndbi < 0.10))
        )
        result[fb_water] = CLASS_WATER
        unassigned[fb_water] = False

        # Out-of-box Dense Vegetation (NDVI >= 0.50)
        fb_dense = unassigned & (ndvi >= 0.50)
        result[fb_dense] = CLASS_DENSE_VEGETATION
        unassigned[fb_dense] = False

        # Out-of-box Sparse Vegetation (0.20 <= NDVI < 0.50 & NDVI > NDBI)
        fb_sparse = unassigned & (ndvi >= 0.20) & (ndvi > ndbi)
        result[fb_sparse] = CLASS_SPARSE_VEGETATION
        unassigned[fb_sparse] = False

        # Out-of-box Built-up (NDBI >= 0.15)
        fb_built = unassigned & (ndbi >= 0.15)
        result[fb_built] = CLASS_BUILT_UP
        unassigned[fb_built] = False

        # Out-of-box Bare Soil (negative NDBI and low NDVI)
        fb_soil = unassigned & (ndbi < 0.00) & (ndvi < 0.20)
        result[fb_soil] = CLASS_BARE_SOIL
        unassigned[fb_soil] = False

        # Remaining falls into the Ambiguous / Confusion zone
        result[unassigned] = CLASS_CONFUSION
        unassigned[:] = False

    return result
