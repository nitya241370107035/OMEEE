"""
Phase 2.8: Mask-Aware Percentile Normalization Module

Performs robust radiometric contrast stretching strictly over valid, unmasked ground pixels
(~bad_mask) to eliminate radiometric bias caused by cloud brightness or shadow darkness.
Uses scientific NumPy array operations and non-linear gamma response for crisp ground clarity.
"""

import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


def normalize_rgb_percentile(
    rgb_bands: np.ndarray,
    bad_mask: np.ndarray,
    p_low: float = 1.0,
    p_high: float = 98.5,
    gamma: float = 0.85
) -> np.ndarray:
    """
    Normalizes a 3-band (Red, Green, Blue) satellite raster to uint8 [0, 255] using
    percentile stretching computed exclusively over clean/valid pixels (~bad_mask).

    Args:
        rgb_bands: Shape (3, H, W), float32 array in [0, 10000] DN or [0.0, 1.0] reflectance.
        bad_mask: Shape (H, W), boolean mask (True = bad / cloud / shadow).
        p_low: Lower percentile cutoff (default 1.0%).
        p_high: Upper percentile cutoff (default 98.5%).
        gamma: Gamma tone curve factor (default 0.85 for midtone/shadow contrast boost).

    Returns:
        np.ndarray: Shape (3, H, W), dtype uint8 in range [0, 255].
    """
    return normalize_multiband_percentile(
        bands_data=rgb_bands,
        bad_mask=bad_mask,
        p_low=p_low,
        p_high=p_high,
        gamma=gamma
    )


def normalize_multiband_percentile(
    bands_data: np.ndarray,
    bad_mask: np.ndarray,
    p_low: float = 1.0,
    p_high: float = 98.5,
    gamma: float = 0.85
) -> np.ndarray:
    """
    Performs mask-aware percentile contrast stretching across any multi-band raster stack.

    Args:
        bands_data: Shape (Bands, H, W), float32/float64 array.
        bad_mask: Shape (H, W), boolean mask where True indicates invalid pixels.
        p_low: Lower percentile cutoff.
        p_high: Upper percentile cutoff.
        gamma: Non-linear tone response curve.

    Returns:
        np.ndarray: Shape (Bands, H, W), dtype uint8 in range [0, 255].
    """
    num_bands, h, w = bands_data.shape
    good_mask = ~bad_mask
    normalized = np.zeros((num_bands, h, w), dtype=np.uint8)

    # If < 2% of pixels are valid, fall back to stretching the entire canvas
    valid_count = np.count_nonzero(good_mask)
    use_fallback = valid_count < (0.02 * h * w)

    if use_fallback:
        logger.warning(
            f"Only {valid_count} / {h * w} ({valid_count / (h * w) * 100:.1f}%) valid pixels. "
            "Falling back to unmasked canvas percentiles for radiometric stability."
        )

    for i in range(num_bands):
        band = bands_data[i].astype(np.float32)
        valid_pixels = band if use_fallback else band[good_mask]

        # Filter NaNs or Infs if any
        valid_finite = valid_pixels[np.isfinite(valid_pixels)]

        if len(valid_finite) == 0:
            vmin, vmax = 0.0, 3000.0 if np.nanmax(band) > 1.5 else 0.3
        else:
            vmin = float(np.percentile(valid_finite, p_low))
            vmax = float(np.percentile(valid_finite, p_high))

        # Avoid zero division on uniform or flat regions
        if vmax <= vmin:
            vmax = vmin + (1.0 if vmin > 1.5 else 0.01)

        # Contrast stretch to [0.0, 1.0]
        stretched = np.clip((band - vmin) / (vmax - vmin), 0.0, 1.0)

        # Non-linear gamma tone mapping for clear visual contrast
        if gamma != 1.0:
            stretched = np.power(stretched, gamma)

        normalized[i] = (stretched * 255.0).astype(np.uint8)

    return normalized
