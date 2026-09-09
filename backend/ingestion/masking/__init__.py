"""
Phase 2: Masking and Quality Assessment Package

Orchestrates:
1. External s2cloudless detector adapter (integrations/s2cloudless_adapter.py)
2. Directional shadow detector adapter (masking/shadow_adapter.py)
3. Quality mask combination (masking/quality_mask.py)
4. Mask-aware percentile contrast normalization (normalization/percentile_normalization.py)
"""

import logging
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Optional
import numpy as np

from backend.ingestion.integrations.s2cloudless_adapter import (
    run_s2cloudless_detector,
    validate_s2cloudless_bands,
    prepare_s2cloudless_tensor,
    S2CLOUDLESS_BANDS
)
from .shadow_adapter import detect_shadows
from .quality_mask import combine_quality_masks, QualityMaskResult
from backend.ingestion.normalization.percentile_normalization import (
    normalize_rgb_percentile,
    normalize_multiband_percentile
)

logger = logging.getLogger(__name__)


@dataclass
class CleanedCanvas:
    """Represents the preprocessed, cleaned, and normalized RGB canvas with quality masks."""
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
    Computes cloud probability and binary cloud mask from Sentinel-2 bands dictionary.
    Wrapper for backward compatibility.
    """
    band_names = list(bands_dict.keys())
    data = np.stack([bands_dict[name] for name in band_names], axis=0)
    
    class _MockCanvas:
        def __init__(self, data, names):
            self.data = data
            self.band_names = names
            self.height = data.shape[1]
            self.width = data.shape[2]

    mock_canvas = _MockCanvas(data, band_names)
    return run_s2cloudless_detector(mock_canvas, threshold=cloud_threshold)


def normalize_rgb(
    rgb_bands: np.ndarray,
    bad_mask: np.ndarray,
    p_low: float = 1.0,
    p_high: float = 98.5,
    gamma: float = 0.85
) -> np.ndarray:
    """Backward compatible wrapper for percentile RGB normalization."""
    return normalize_rgb_percentile(
        rgb_bands=rgb_bands,
        bad_mask=bad_mask,
        p_low=p_low,
        p_high=p_high,
        gamma=gamma
    )


def clean_and_normalize_canvas(
    canvas_data: Any,
    cloud_threshold: float = 0.40,
    shadow_threshold: float = 450.0,
    p_low: float = 1.0,
    p_high: float = 98.5,
    gamma: float = 0.85
) -> CleanedCanvas:
    """
    Full Phase 2 processing pipeline:
      1. s2cloudless detector execution on 10-band canvas
      2. Shadow adapter detection near clouds
      3. Quality mask merging
      4. Mask-aware radiometric percentile normalization

    Args:
        canvas_data: CanvasData object containing Sentinel-2 bands.
        cloud_threshold: s2cloudless cloud probability cutoff threshold.
        shadow_threshold: Shadow darkness DN threshold.
        p_low: Lower percentile cutoff for normalization.
        p_high: Upper percentile cutoff for normalization.
        gamma: Gamma tone curve factor.

    Returns:
        CleanedCanvas: Cleaned RGB array (uint8) + masks + probability map + cloud statistics.
    """
    # 1. Cloud Masking via external s2cloudless adapter
    cloud_prob, cloud_mask = run_s2cloudless_detector(
        canvas_data=canvas_data,
        threshold=cloud_threshold
    )

    # 2. Shadow Masking via shadow adapter
    shadow_mask = detect_shadows(
        bands_source=canvas_data,
        cloud_mask=cloud_mask,
        shadow_darkness_threshold=shadow_threshold
    )

    # 3. Combine Masks into QualityMaskResult
    quality = combine_quality_masks(
        cloud_prob=cloud_prob,
        cloud_mask=cloud_mask,
        shadow_mask=shadow_mask
    )

    # 4. Extract RGB bands for visual display and tile generation
    if hasattr(canvas_data, "visual_rgb") and canvas_data.visual_rgb is not None and canvas_data.visual_rgb.shape[0] >= 3:
        # Use pristine 10m True Color Image with ESA Sen2Cor atmospheric balancing
        rgb_stack = canvas_data.visual_rgb
        rgb_normalized = normalize_rgb_percentile(
            rgb_bands=rgb_stack,
            bad_mask=quality.bad_mask,
            p_low=0.5,
            p_high=99.5,
            gamma=1.0
        )
    else:
        band_map = {name.lower(): canvas_data.data[idx] for idx, name in enumerate(canvas_data.band_names)}
        red = band_map.get("b04", band_map.get("red", band_map.get("b4")))
        green = band_map.get("b03", band_map.get("green", band_map.get("b3")))
        blue = band_map.get("b02", band_map.get("blue", band_map.get("b2")))

        if red is None or green is None or blue is None:
            # Flexible positional fallback for generic or unlabelled rasters
            if canvas_data.data.shape[0] >= 3:
                red = canvas_data.data[0]
                green = canvas_data.data[1]
                blue = canvas_data.data[2]
            elif canvas_data.data.shape[0] == 1:
                red = canvas_data.data[0]
                green = canvas_data.data[0]
                blue = canvas_data.data[0]
            else:
                raise ValueError(
                    f"Canvas is missing RGB bands (B04, B03, B02). Available: {canvas_data.band_names}"
                )

        rgb_stack = np.stack([red, green, blue], axis=0)
        rgb_normalized = normalize_rgb_percentile(
            rgb_bands=rgb_stack,
            bad_mask=quality.bad_mask,
            p_low=p_low,
            p_high=p_high,
            gamma=gamma
        )

    logger.info(
        f"Canvas cleaned & normalized: {canvas_data.width}x{canvas_data.height} px | "
        f"Cloud+Shadow: {quality.canvas_cloud_pct * 100:.2f}% | "
        f"Valid pixels: {quality.valid_pixel_pct * 100:.2f}%"
    )

    return CleanedCanvas(
        rgb_normalized=rgb_normalized,
        cloud_mask=quality.cloud_mask,
        shadow_mask=quality.shadow_mask,
        bad_mask=quality.bad_mask,
        cloud_prob=quality.cloud_prob,
        canvas_cloud_pct=quality.canvas_cloud_pct
    )


__all__ = [
    "CleanedCanvas",
    "clean_and_normalize_canvas",
    "detect_clouds",
    "detect_shadows",
    "normalize_rgb",
    "QualityMaskResult",
    "combine_quality_masks",
]
