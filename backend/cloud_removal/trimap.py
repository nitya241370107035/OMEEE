"""
Cloud Trimap Generation Stage (Methodology Reference: MDPI Remote Sensing 10.3390/rs15040904)

Generates standardized 3-zone cloud trimaps:
- Background / Clear: 0
- Uncertain / Transparent Cloud: 128
- Foreground / Opaque Cloud: 255

Produces: cloud_trimap.tif & cloud_trimap_preview.png
"""

import logging
from pathlib import Path
from typing import Tuple, Dict, Any
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import Affine

from backend.cloud_removal.config import TrimapClass, CloudRemovalConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def generate_cloud_trimap(
    cloud_prob: np.ndarray,
    cloud_mask: np.ndarray,
    config: CloudRemovalConfig = DEFAULT_CONFIG,
    erosion_iterations: int = 1,
    dilation_iterations: int = 2
) -> np.ndarray:
    """
    Constructs a 3-zone trimap for cloud matting.

    Args:
        cloud_prob: Continuous cloud probability array (H, W).
        cloud_mask: Binary cloud mask (H, W).
        config: Threshold configuration.
        erosion_iterations: Iterations to find definite opaque cloud core.
        dilation_iterations: Iterations to find transition zone.

    Returns:
        np.ndarray: uint8 trimap with values {0, 128, 255}.
    """
    from scipy.ndimage import binary_erosion, binary_dilation

    h, w = cloud_prob.shape
    trimap = np.zeros((h, w), dtype=np.uint8)  # Default: BACKGROUND_CLEAR = 0

    # Definite foreground: very high probability or eroded cloud mask core
    opaque_seed = (cloud_prob >= config.thick_prob_threshold) | (cloud_mask == 1)
    if opaque_seed.any() and erosion_iterations > 0:
        struct = np.ones((3, 3), dtype=bool)
        opaque_core = binary_erosion(opaque_seed, structure=struct, iterations=erosion_iterations)
    else:
        opaque_core = (cloud_prob >= config.thick_prob_threshold)

    # Expanded cloud zone for transition region
    if opaque_seed.any() and dilation_iterations > 0:
        struct = np.ones((3, 3), dtype=bool)
        expanded_zone = binary_dilation(opaque_seed, structure=struct, iterations=dilation_iterations)
    else:
        expanded_zone = (cloud_prob >= config.clear_prob_threshold)

    # Intermediate / uncertain region
    uncertain_zone = expanded_zone & (~opaque_core) & (cloud_prob >= config.clear_prob_threshold)

    # Assign discrete trimap values
    trimap[uncertain_zone] = TrimapClass.UNCERTAIN_TRANSPARENT
    trimap[opaque_core] = TrimapClass.FOREGROUND_OPAQUE

    # Ensure clear region is strictly 0
    clear_zone = (cloud_prob < config.clear_prob_threshold) & (~expanded_zone)
    trimap[clear_zone] = TrimapClass.BACKGROUND_CLEAR

    return trimap


def save_cloud_trimap(
    trimap: np.ndarray,
    output_tif: Path,
    output_preview_png: Path,
    crs: str,
    transform: Affine
) -> Tuple[Path, Path]:
    """Saves both the GeoTIFF trimap and the PNG preview visualization."""
    output_tif.parent.mkdir(parents=True, exist_ok=True)
    output_preview_png.parent.mkdir(parents=True, exist_ok=True)
    h, w = trimap.shape

    # 1. GeoTIFF
    with rasterio.open(
        output_tif, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="uint8", crs=crs, transform=transform
    ) as dst:
        dst.write(trimap, 1)

    # 2. RGB Visual Preview (Black = Clear, Gray = Uncertain/Thin, White = Opaque)
    rgb_vis = np.zeros((h, w, 3), dtype=np.uint8)
    rgb_vis[trimap == TrimapClass.BACKGROUND_CLEAR] = [20, 24, 33]           # Dark slate for background
    rgb_vis[trimap == TrimapClass.UNCERTAIN_TRANSPARENT] = [128, 170, 220]   # Light blue-gray for transparent
    rgb_vis[trimap == TrimapClass.FOREGROUND_OPAQUE] = [245, 245, 255]       # Pure white for opaque

    img_pil = Image.fromarray(rgb_vis, mode="RGB")
    img_pil.save(output_preview_png)

    logger.info(f"Saved cloud trimap: {output_tif} & {output_preview_png}")
    return output_tif, output_preview_png
