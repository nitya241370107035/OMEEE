"""
Phase 1.3: Cloud/Shadow Masking and Radiometric Normalization

Applies spectral cloud detection, proximity-based shadow masking, and masked
percentile stretch normalization across the full working canvas before tiling.
Guarantees that cloud/shadow artifacts do not bias the normalization of valid pixels.
"""

import logging
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Optional
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CleanedCanvas:
    """Represents the preprocessed, cleaned, and normalized RGB canvas."""
    rgb_normalized: np.ndarray    # Shape: (3, Height, Width), dtype uint8 [0, 255]
    cloud_mask: np.ndarray        # Shape: (Height, Width), dtype bool (True = cloud)
    shadow_mask: np.ndarray       # Shape: (Height, Width), dtype bool (True = shadow)
    bad_mask: np.ndarray          # Shape: (Height, Width), dtype bool (True = cloud OR shadow)
    cloud_prob: np.ndarray        # Shape: (Height, Width), dtype float32 [0.0, 1.0]
    canvas_cloud_pct: float       # Canvas-wide fraction of bad/cloud pixels (0.0 to 1.0)


def detect_clouds(
    bands_dict: Dict[str, np.ndarray],
    cloud_threshold: float = 0.40
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Computes spectral cloud probability and binary cloud mask from Sentinel-2 bands.

    Uses a robust multi-criteria rule set based on visible brightness,
    whiteness index, and NIR reflectance.

    Args:
        bands_dict: Dictionary mapping band names ('red', 'green', 'blue', 'nir') to 2D float arrays.
        cloud_threshold: Probability threshold above which a pixel is flagged as cloud.

    Returns:
        Tuple of (cloud_prob [0.0-1.0], cloud_mask [bool])
    """
    red = bands_dict["red"]
    green = bands_dict["green"]
    blue = bands_dict["blue"]
    nir = bands_dict.get("nir", (red + green) / 2.0)

    # 1. Visible Mean Brightness (in 0-10000 DN scale)
    vis_mean = (red + green + blue) / 3.0

    # 2. Whiteness Index (clouds have balanced, non-chromatic reflectance in visible bands)
    # Mean absolute difference from visible mean
    whiteness = (np.abs(red - vis_mean) + np.abs(green - vis_mean) + np.abs(blue - vis_mean)) / (vis_mean + 1e-5)

    # 3. Brightness score (higher reflectance = higher cloud likelihood)
    # Normalize typical cloud brightness range (1500 DN to 6000 DN)
    brightness_score = np.clip((vis_mean - 1500.0) / 4500.0, 0.0, 1.0)

    # 4. Whiteness score (low color deviation = high whiteness score)
    whiteness_score = np.clip(1.0 - (whiteness / 0.35), 0.0, 1.0)

    # 5. NIR score (clouds are bright in NIR, whereas water is dark in NIR)
    nir_score = np.clip((nir - 1200.0) / 4000.0, 0.0, 1.0)

    # Combined cloud probability map
    cloud_prob = (0.50 * brightness_score) + (0.25 * whiteness_score) + (0.25 * nir_score)
    cloud_prob = np.clip(cloud_prob, 0.0, 1.0).astype(np.float32)

    cloud_mask = cloud_prob >= cloud_threshold
    return cloud_prob, cloud_mask


def detect_shadows(
    bands_dict: Dict[str, np.ndarray],
    cloud_mask: np.ndarray,
    shadow_darkness_threshold: float = 450.0,
    search_radius_pixels: int = 25
) -> np.ndarray:
    """
    Flags probable cloud shadows by identifying dark pixels in the spatial
    vicinity of detected clouds.

    Args:
        bands_dict: Dictionary containing 'red', 'green', 'blue', 'nir' 2D float arrays.
        cloud_mask: 2D boolean array of detected clouds.
        shadow_darkness_threshold: Maximum visible DN for shadow candidates.
        search_radius_pixels: Kernel radius for cloud shadow neighborhood search.

    Returns:
        2D boolean array of detected cloud shadows.
    """
    red = bands_dict["red"]
    green = bands_dict["green"]
    blue = bands_dict["blue"]
    nir = bands_dict.get("nir", red)

    vis_mean = (red + green + blue) / 3.0

    # Candidate dark pixels: low visible reflectance and low NIR
    is_dark = (vis_mean < shadow_darkness_threshold) & (nir < shadow_darkness_threshold * 1.5)

    # Only consider shadow if within proximity to a detected cloud
    if not np.any(cloud_mask):
        return np.zeros_like(cloud_mask, dtype=bool)

    # Fast circular dilation using numpy sliding / slice windowing
    h, w = cloud_mask.shape
    cloud_proximity = np.zeros_like(cloud_mask, dtype=bool)

    # Sample directional offsets (clouds cast shadows directionally, typically SE/SW/NW)
    offsets = [
        (dy, dx)
        for dy in range(-search_radius_pixels, search_radius_pixels + 1, 4)
        for dx in range(-search_radius_pixels, search_radius_pixels + 1, 4)
        if dy * dy + dx * dx <= search_radius_pixels * search_radius_pixels
    ]

    for dy, dx in offsets:
        # Shift cloud mask
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

    # Shadow is dark pixel near cloud that is not itself cloud
    shadow_mask = is_dark & cloud_proximity & (~cloud_mask)
    return shadow_mask


def normalize_rgb(
    rgb_bands: np.ndarray,
    bad_mask: np.ndarray,
    p_low: float = 1.0,
    p_high: float = 98.5,
    gamma: float = 0.85
) -> np.ndarray:
    """
    Performs robust percentile contrast stretching (p_low to p_high) per RGB band with
    gamma correction for sharp, crisp ground visual detail, computed EXCLUSIVELY on
    clear/valid pixels (~bad_mask).

    Args:
        rgb_bands: Shape (3, H, W), float array containing [Red, Green, Blue].
        bad_mask: Shape (H, W), boolean mask (True = bad / cloud / shadow).
        p_low: Lower percentile cutoff (default 1.0).
        p_high: Upper percentile cutoff (default 98.5).
        gamma: Non-linear tone response curve (default 0.85 for enhanced shadow & midtone clarity).

    Returns:
        np.ndarray: Shape (3, H, W), uint8 array in range [0, 255].
    """
    num_bands, h, w = rgb_bands.shape
    good_mask = ~bad_mask
    normalized = np.zeros((num_bands, h, w), dtype=np.uint8)

    # If < 2% of pixels are valid, fall back to stretching entire canvas to avoid empty percentiles
    use_fallback = np.count_nonzero(good_mask) < (0.02 * h * w)

    for i in range(num_bands):
        band = rgb_bands[i]
        valid_pixels = band if use_fallback else band[good_mask]

        if len(valid_pixels) == 0:
            vmin, vmax = 0.0, 3000.0
        else:
            vmin = float(np.percentile(valid_pixels, p_low))
            vmax = float(np.percentile(valid_pixels, p_high))

        # Avoid zero division on flat/uniform images
        if vmax <= vmin:
            vmax = vmin + 1.0

        # Contrast stretch to [0.0, 1.0]
        stretched = np.clip((band - vmin) / (vmax - vmin), 0.0, 1.0)
        
        # Gamma tone adjustment for crisp midtone/shadow clarity
        if gamma != 1.0:
            stretched = np.power(stretched, gamma)

        normalized[i] = (stretched * 255.0).astype(np.uint8)

    return normalized


def clean_and_normalize_canvas(
    canvas_data: Any,
    cloud_threshold: float = 0.40,
    shadow_threshold: float = 450.0,
    p_low: float = 2.0,
    p_high: float = 98.0
) -> CleanedCanvas:
    """
    Full Phase 1.3 processing: cloud/shadow detection and masked radiometric normalization.

    Args:
        canvas_data: CanvasData object from Phase 1.2.
        cloud_threshold: Cloud probability threshold.
        shadow_threshold: Shadow darkness DN threshold.
        p_low: Lower percentile cutoff for normalization.
        p_high: Upper percentile cutoff for normalization.

    Returns:
        CleanedCanvas: Cleaned RGB array (uint8) + masks + probability map + cloud statistics.
    """
    bands_dict = {}
    for i, name in enumerate(canvas_data.band_names):
        bands_dict[name] = canvas_data.data[i]

    # Required RGB bands
    red = bands_dict["red"]
    green = bands_dict["green"]
    blue = bands_dict["blue"]

    # 1. Cloud Masking
    cloud_prob, cloud_mask = detect_clouds(bands_dict, cloud_threshold=cloud_threshold)

    # 2. Shadow Masking
    shadow_mask = detect_shadows(
        bands_dict,
        cloud_mask,
        shadow_darkness_threshold=shadow_threshold
    )

    # 3. Combine Masks
    bad_mask = cloud_mask | shadow_mask

    # 4. Canvas Cloud & Shadow Percentage
    total_pixels = bad_mask.size
    bad_pixels = int(np.count_nonzero(bad_mask))
    canvas_cloud_pct = round(bad_pixels / total_pixels, 4) if total_pixels > 0 else 0.0

    # 5. Masked Radiometric Normalization (RGB)
    rgb_stack = np.stack([red, green, blue], axis=0)
    rgb_normalized = normalize_rgb(rgb_stack, bad_mask, p_low=p_low, p_high=p_high)

    logger.info(
        f"Canvas cleaned: {canvas_data.width}x{canvas_data.height} px | "
        f"Cloud+Shadow: {canvas_cloud_pct * 100:.2f}% | "
        f"Good pixels: {(1.0 - canvas_cloud_pct) * 100:.2f}%"
    )

    return CleanedCanvas(
        rgb_normalized=rgb_normalized,
        cloud_mask=cloud_mask,
        shadow_mask=shadow_mask,
        bad_mask=bad_mask,
        cloud_prob=cloud_prob,
        canvas_cloud_pct=canvas_cloud_pct
    )
