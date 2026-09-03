"""
Phase 2.7: Shadow Detection Module (Option A)

Identifies cloud shadow candidates using dark spectral response in visible & NIR bands
combined with spatial neighborhood dilation around s2cloudless-detected clouds.
"""

import logging
from typing import Dict, Any, Optional, Union
import numpy as np

logger = logging.getLogger(__name__)


def detect_shadows(
    bands_source: Union[Dict[str, np.ndarray], Any],
    cloud_mask: np.ndarray,
    shadow_darkness_threshold: float = 450.0,
    search_radius_pixels: int = 25
) -> np.ndarray:
    """
    Identifies cloud shadows by finding dark spectral pixels in the spatial
    vicinity of detected clouds.

    Args:
        bands_source: Either a Dict mapping band names ('red', 'green', 'blue', 'nir' or 'B04', 'B03', 'B02', 'B08')
                      to 2D arrays, or a CanvasData object.
        cloud_mask: 2D boolean array of detected clouds from s2cloudless.
        shadow_darkness_threshold: Maximum visible DN (0-10000 scale) for shadow candidates.
        search_radius_pixels: Directional search radius in pixels around cloud boundaries.

    Returns:
        np.ndarray: 2D boolean mask of detected cloud shadows (True = shadow).
    """
    if not np.any(cloud_mask):
        return np.zeros_like(cloud_mask, dtype=bool)

    # Extract band arrays
    if hasattr(bands_source, "band_names") and hasattr(bands_source, "data"):
        band_map = {name: bands_source.data[idx].astype(np.float32) for idx, name in enumerate(bands_source.band_names)}
    elif isinstance(bands_source, dict):
        band_map = {k: np.asarray(v, dtype=np.float32) for k, v in bands_source.items()}
    else:
        raise TypeError("bands_source must be a CanvasData instance or Dict[str, np.ndarray].")

    red = band_map.get("B04", band_map.get("red"))
    green = band_map.get("B03", band_map.get("green", red))
    blue = band_map.get("B02", band_map.get("blue", red))
    nir = band_map.get("B08", band_map.get("nir", red))

    if red is None or blue is None:
        logger.warning("Missing visible bands for shadow detection. Returning empty shadow mask.")
        return np.zeros_like(cloud_mask, dtype=bool)

    vis_mean = (red + green + blue) / 3.0

    # Handle reflectance scaled to [0, 1] vs DN scale [0, 10000]
    thresh = shadow_darkness_threshold
    if np.nanmax(vis_mean) <= 1.5:
        thresh = shadow_darkness_threshold / 10000.0

    # 1. Dark pixel candidates: low visible reflectance and low NIR
    is_dark = (vis_mean < thresh) & (nir < thresh * 1.5)

    # 2. Fast directional dilation for cloud shadow neighborhood search
    h, w = cloud_mask.shape
    cloud_proximity = np.zeros_like(cloud_mask, dtype=bool)

    offsets = [
        (dy, dx)
        for dy in range(-search_radius_pixels, search_radius_pixels + 1, 4)
        for dx in range(-search_radius_pixels, search_radius_pixels + 1, 4)
        if dy * dy + dx * dx <= search_radius_pixels * search_radius_pixels
    ]

    for dy, dx in offsets:
        src_y_start = max(0, -dy)
        src_y_end = min(h, h - dy)
        src_x_start = max(0, -dx)
        src_x_end = min(w, w - dx)

        dst_y_start = max(0, dy)
        dst_y_end = min(h, h + dy)
        dst_x_start = max(0, dx)
        dst_x_end = min(w, w + dx)

        cloud_proximity[dst_y_start:dst_y_end, dst_x_start:dst_x_end] |= (
            cloud_mask[src_y_start:src_y_end, src_x_start:src_x_end]
        )

    # 3. Shadow is a dark pixel near cloud that is not cloud itself
    shadow_mask = is_dark & cloud_proximity & (~cloud_mask)

    shadow_count = int(np.count_nonzero(shadow_mask))
    logger.info(
        f"Shadow detection complete: {shadow_count} pixels "
        f"({(shadow_count / shadow_mask.size) * 100:.2f}%)"
    )

    return shadow_mask
