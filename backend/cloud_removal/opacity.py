"""
Cloud Opacity / Transparency (Alpha Matte) Estimation

Estimates continuous cloud opacity alpha in [0.0, 1.0] across the scene.
- 0.0: Fully transparent / clear
- 1.0: Fully opaque thick cloud

Produces: cloud_opacity.tif
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
import numpy as np
import rasterio
from rasterio.transform import Affine

from backend.cloud_removal.config import CloudRemovalConfig, TrimapClass, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def estimate_cloud_opacity(
    cloud_prob: np.ndarray,
    trimap: np.ndarray,
    rgb_data: Optional[np.ndarray] = None,
    config: CloudRemovalConfig = DEFAULT_CONFIG,
    method: str = "cloud_matting_heuristic"
) -> np.ndarray:
    """
    Computes continuous cloud opacity alpha map in range [0.0, 1.0].

    Methodology:
    - Background (trimap == 0): Strictly alpha = 0.0
    - Foreground (trimap == 255): Strictly alpha = 1.0
    - Uncertain (trimap == 128): Smooth physical mapping from cloud probability and local luminance.
    """
    h, w = cloud_prob.shape
    alpha = np.zeros((h, w), dtype=np.float32)

    # 1. Definite foreground = 1.0
    alpha[trimap == TrimapClass.FOREGROUND_OPAQUE] = 1.0

    # 2. Definite background = 0.0
    alpha[trimap == TrimapClass.BACKGROUND_CLEAR] = 0.0

    # 3. Uncertain / thin cloud zone estimation
    uncertain_mask = (trimap == TrimapClass.UNCERTAIN_TRANSPARENT)
    if uncertain_mask.any():
        # Scale probability between clear_threshold and thick_threshold into [min_opacity, max_opacity]
        prob_span = max(1e-4, config.thick_prob_threshold - config.clear_prob_threshold)
        norm_prob = np.clip((cloud_prob[uncertain_mask] - config.clear_prob_threshold) / prob_span, 0.0, 1.0)
        
        # Smooth cosine/S-curve transition
        alpha_uncertain = config.min_opacity + (config.max_opacity - config.min_opacity) * (
            0.5 * (1.0 - np.cos(np.pi * norm_prob))
        )
        alpha[uncertain_mask] = alpha_uncertain

    # Global bounds clamp [0.0, 1.0]
    alpha = np.clip(alpha, 0.0, 1.0)
    return alpha


def save_cloud_opacity(
    opacity: np.ndarray,
    output_path: Path,
    crs: str,
    transform: Affine
) -> Path:
    """Saves the continuous cloud opacity raster as a float32 GeoTIFF."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = opacity.shape
    with rasterio.open(
        output_path, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="float32", crs=crs, transform=transform
    ) as dst:
        dst.write(opacity.astype(np.float32), 1)
    logger.info(f"Saved cloud opacity map: {output_path}")
    return output_path
