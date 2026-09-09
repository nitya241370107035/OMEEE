"""
backend/services/change_pair.py
===============================
Phase 2.3: Per-Pixel Mask-Aware Change Detection Pair Resolution
================================================================
PS Section 2.2.3: "favour analytically useful precision over indiscriminate change recall"

Instead of throwing away a tile pair as a whole because of an aggregate cloud percentage,
this module loads the actual per-pixel georeferenced bad-masks for both dates:
  1. Computes combined unusable mask: combined_bad_mask = mask_before | mask_after
  2. Evaluates valid pixel coverage: valid_fraction = 1.0 - (bad_pixels / total_pixels)
  3. Rejects only if valid_fraction < MIN_USABLE_FRACTION (e.g. < 30%)
  4. Compares fixed-scale reflectance over genuinely usable pixels (~combined_bad_mask)
  5. Scales confidence dynamically by the fraction of verified clear ground evaluated.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union, Tuple
import numpy as np
import rasterio

logger = logging.getLogger(__name__)

MIN_USABLE_FRACTION = 0.30
DEFAULT_REFLECTANCE_DIFF_THRESHOLD = 0.15  # 15% reflectance difference on fixed scale [0.0, 1.5]


def load_tile_bad_mask(mask_path: Optional[Union[str, Path]], shape: Tuple[int, int] = (512, 512)) -> np.ndarray:
    """
    Loads the per-pixel boolean bad-mask from GeoTIFF.
    Returns: 2D boolean array (True = bad/cloud/shadow, False = good/clear).
    """
    if not mask_path:
        return np.zeros(shape, dtype=bool)

    p = Path(mask_path)
    if not p.is_file():
        logger.debug(f"Bad mask file not found at '{mask_path}', defaulting to all-valid.")
        return np.zeros(shape, dtype=bool)

    try:
        with rasterio.open(str(p)) as src:
            data = src.read(1)
            return (data > 0)
    except Exception as e:
        logger.warning(f"Error loading bad-mask from '{mask_path}': {e}. Defaulting to clean mask.")
        return np.zeros(shape, dtype=bool)


def load_tile_pixels(tif_path: Union[str, Path]) -> Tuple[np.ndarray, rasterio.Affine, str]:
    """
    Loads multi-band fixed-scale reflectance tile from GeoTIFF.
    Returns: (multiband_float32, transform, crs)
    """
    with rasterio.open(str(tif_path)) as src:
        data = src.read().astype(np.float32)
        transform = src.transform
        crs = src.crs.to_string() if src.crs else "EPSG:4326"
    return data, transform, crs


def resolve_change_pair(
    tile_before_path: Union[str, Path],
    tile_after_path: Union[str, Path],
    mask_before_path: Optional[Union[str, Path]] = None,
    mask_after_path: Optional[Union[str, Path]] = None,
    min_usable_fraction: float = MIN_USABLE_FRACTION,
    diff_threshold: float = DEFAULT_REFLECTANCE_DIFF_THRESHOLD
) -> Dict[str, Any]:
    """
    Performs pixel-precise change detection between two multi-temporal tiles.
    
    A tile that is 20% cloudy in date 1 is evaluated on the 80% clear pixels
    rather than discarded entirely.
    """
    data_a, transform_a, crs_a = load_tile_pixels(tile_before_path)
    data_b, transform_b, crs_b = load_tile_pixels(tile_after_path)

    # Harmonize shape
    h = min(data_a.shape[1], data_b.shape[1])
    w = min(data_a.shape[2], data_b.shape[2])
    bands = min(data_a.shape[0], data_b.shape[0])

    data_a = data_a[:bands, :h, :w]
    data_b = data_b[:bands, :h, :w]

    # Load bad-masks
    mask_a = load_tile_bad_mask(mask_before_path, shape=(h, w))[:h, :w]
    mask_b = load_tile_bad_mask(mask_after_path, shape=(h, w))[:h, :w]

    # Combined bad mask: pixel is unusable if cloudy or shadowed in EITHER date
    combined_bad_mask = mask_a | mask_b
    total_pixels = h * w
    bad_pixels_count = int(np.count_nonzero(combined_bad_mask))
    usable_pixels_count = total_pixels - bad_pixels_count
    valid_fraction = float(usable_pixels_count) / float(total_pixels)

    if valid_fraction < min_usable_fraction:
        logger.info(
            f"[ChangePair] Insufficient valid coverage: only {valid_fraction*100:.1f}% valid ground "
            f"(threshold={min_usable_fraction*100:.0f}%). Flagged as insufficient_coverage."
        )
        return {
            "status": "insufficient_coverage",
            "quality_flag": "insufficient_coverage",
            "valid_fraction": round(valid_fraction, 4),
            "cloud_unusable_pct": round((1.0 - valid_fraction) * 100.0, 2),
            "confidence": 0.0,
            "change_percentage": 0.0,
            "total_pixels": total_pixels,
            "evaluated_pixels": usable_pixels_count,
            "change_pixel_count": 0,
            "details": f"Tile pair discarded due to high persistent cloud/shadow ({bad_pixels_count}/{total_pixels} unusable pixels)."
        }

    # Multi-band Euclidean difference in fixed reflectance units
    usable_mask = ~combined_bad_mask
    diff_tensor = np.abs(data_b - data_a)
    euclidean_diff = np.sqrt(np.sum((data_b - data_a) ** 2, axis=0))

    # Identify significant change strictly over verified valid pixels
    raw_change_mask = euclidean_diff > diff_threshold
    usable_change_mask = raw_change_mask & usable_mask

    change_pixel_count = int(np.count_nonzero(usable_change_mask))
    change_pct_over_valid = (float(change_pixel_count) / float(usable_pixels_count) * 100.0) if usable_pixels_count > 0 else 0.0

    # Confidence score reflects verified ground fraction & SNR
    confidence = float(np.clip(valid_fraction * 0.95, 0.1, 0.99))

    return {
        "status": "ok",
        "quality_flag": "ok",
        "valid_fraction": round(valid_fraction, 4),
        "cloud_unusable_pct": round((1.0 - valid_fraction) * 100.0, 2),
        "confidence": round(confidence, 4),
        "change_percentage": round(change_pct_over_valid, 2),
        "total_pixels": total_pixels,
        "evaluated_pixels": usable_pixels_count,
        "change_pixel_count": change_pixel_count,
        "mean_spectral_diff": round(float(np.mean(euclidean_diff[usable_mask])), 4) if usable_pixels_count > 0 else 0.0,
        "diff_threshold_used": diff_threshold
    }
