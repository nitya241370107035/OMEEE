"""
Phase 2.2 - 2.5: s2cloudless Integration Adapter Layer

Adapter interfacing directly with the external `s2cloudless` repository / package
(https://github.com/sentinel-hub/sentinel2-cloud-detector).

Enforces:
1. Strict validation of the exact 10 Sentinel-2 bands expected by s2cloudless:
   ["B01", "B02", "B04", "B05", "B08", "B8A", "B09", "B10", "B11", "B12"]
2. Band order alignment and reflectance scaling (DN / 10000.0 -> [0.0, 1.0]).
3. Reshaping from Canvas (Bands, H, W) to s2cloudless 4D stack (1, H, W, 10).
4. Direct invocation of S2PixelCloudDetector for cloud probability and binary masks.
5. Conversion of outputs back into standard 2D (H, W) NumPy raster format.
"""

import logging
from typing import List, Tuple, Dict, Any, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Exact 10-band ordering required by the Sentinel Hub s2cloudless pixel detector
S2CLOUDLESS_BANDS: List[str] = [
    "B01",  # Coastal aerosol (443 nm)
    "B02",  # Blue (490 nm)
    "B04",  # Red (665 nm)
    "B05",  # Vegetation Red Edge (705 nm)
    "B08",  # NIR (842 nm)
    "B8A",  # Narrow NIR (865 nm)
    "B09",  # Water vapour (945 nm)
    "B10",  # Cirrus (1375 nm)
    "B11",  # SWIR 1 (1610 nm)
    "B12",  # SWIR 2 (2190 nm)
]


def validate_s2cloudless_bands(band_names: List[str]) -> None:
    """
    Validates that all 10 required Sentinel-2 bands are present for s2cloudless.

    Args:
        band_names: List of available band names in the raster canvas.

    Raises:
        ValueError: If any required band is missing.
    """
    available_set = set(band_names)
    missing_bands = [b for b in S2CLOUDLESS_BANDS if b not in available_set]
    if missing_bands:
        err_msg = (
            f"STOP PIPELINE ERROR: s2cloudless requires B01, B02, B04, B05, "
            f"B08, B8A, B09, B10, B11, B12. Missing: {missing_bands}"
        )
        logger.error(err_msg)
        raise ValueError(err_msg)


def prepare_s2cloudless_tensor(
    canvas_data: Any,
    required_bands: Optional[List[str]] = None
) -> np.ndarray:
    """
    Extracts the 10 s2cloudless bands, normalizes DN to reflectance [0.0, 1.0],
    and formats into the 4D NumPy array expected by s2cloudless: (1, Height, Width, 10).

    Args:
        canvas_data: CanvasData object with .data (Bands, H, W) and .band_names.
        required_bands: Ordered list of band names (defaults to S2CLOUDLESS_BANDS).

    Returns:
        np.ndarray: Shape (1, H, W, 10), dtype float32 in range [0.0, 1.0].
    """
    if required_bands is None:
        required_bands = S2CLOUDLESS_BANDS

    validate_s2cloudless_bands(canvas_data.band_names)

    band_index_map = {name: idx for idx, name in enumerate(canvas_data.band_names)}
    h, w = canvas_data.height, canvas_data.width

    # Extract bands in exact s2cloudless order
    ordered_slices = []
    for band_name in required_bands:
        idx = band_index_map[band_name]
        band_arr = canvas_data.data[idx].astype(np.float32)
        ordered_slices.append(band_arr)

    # Stack to (10, H, W)
    stacked = np.stack(ordered_slices, axis=0)

    # Reflectance scaling: Sentinel-2 L1C/L2A DN values are scaled by 10000 (reflectance = DN / 10000.0)
    # If values are already in [0, 1], do not divide again
    if np.nanmax(stacked) > 1.5:
        stacked = stacked / 10000.0
    
    stacked = np.clip(stacked, 0.0, 1.0)

    # Transpose from (10, H, W) -> (H, W, 10) -> (1, H, W, 10)
    hwc = np.transpose(stacked, (1, 2, 0))
    tensor_4d = np.expand_dims(hwc, axis=0).astype(np.float32)

    return tensor_4d


def _spectral_fallback_detector(
    canvas_data: Any,
    threshold: float = 0.40
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fallback spectral cloud detector if external s2cloudless model weights / LightGBM
    are unavailable or encountering binary incompatibility.
    """
    logger.warning("Using built-in spectral multi-band fallback cloud detector.")
    band_map = {name: canvas_data.data[idx].astype(np.float32) for idx, name in enumerate(canvas_data.band_names)}
    
    blue = band_map.get("B02", band_map.get("blue"))
    red = band_map.get("B04", band_map.get("red"))
    green = band_map.get("B03", band_map.get("green", (blue + red) / 2.0))
    nir = band_map.get("B08", band_map.get("nir", red))
    swir = band_map.get("B11", red * 0.5)

    vis_mean = (blue + green + red) / 3.0
    whiteness = (np.abs(red - vis_mean) + np.abs(green - vis_mean) + np.abs(blue - vis_mean)) / (vis_mean + 1e-5)
    
    brightness_score = np.clip((vis_mean - 1500.0) / 4500.0, 0.0, 1.0)
    whiteness_score = np.clip(1.0 - (whiteness / 0.35), 0.0, 1.0)
    nir_score = np.clip((nir - 1200.0) / 4000.0, 0.0, 1.0)
    # Clouds have low NDSI or low SWIR relative to visible brightness
    swir_factor = np.clip(1.0 - (swir / (vis_mean + 1e-5)), 0.0, 1.0)

    prob = (0.40 * brightness_score) + (0.25 * whiteness_score) + (0.20 * nir_score) + (0.15 * swir_factor)
    prob = np.clip(prob, 0.0, 1.0).astype(np.float32)
    mask = prob >= threshold

    return prob, mask


def run_s2cloudless_detector(
    canvas_data: Any,
    threshold: float = 0.40,
    average_over: int = 4,
    dilation_size: int = 2
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Executes the official s2cloudless S2PixelCloudDetector on the prepared 10-band canvas.

    Args:
        canvas_data: CanvasData object containing the 10 required Sentinel-2 bands.
        threshold: Cloud probability cutoff threshold (0.0 to 1.0, default 0.40).
        average_over: Spatial window averaging for cloud smoothing (default 4).
        dilation_size: Morphological dilation radius for cloud buffer (default 2).

    Returns:
        Tuple of:
          - cloud_probability: (Height, Width) float32 array in range [0.0, 1.0]
          - cloud_mask: (Height, Width) boolean array (True = cloud)
    """
    # 1. Prepare 4D tensor (1, H, W, 10) in reflectance scale [0, 1]
    tensor_4d = prepare_s2cloudless_tensor(canvas_data)

    try:
        from s2cloudless import S2PixelCloudDetector

        logger.info(
            f"Invoking s2cloudless S2PixelCloudDetector "
            f"(threshold={threshold}, average_over={average_over}, dilation_size={dilation_size})..."
        )

        detector = S2PixelCloudDetector(
            threshold=threshold,
            average_over=average_over,
            dilation_size=dilation_size,
            all_bands=False  # 10-band model
        )

        # Get cloud probability map: returns shape (1, H, W) float64
        prob_stack = detector.get_cloud_probability_maps(tensor_4d)
        cloud_prob = prob_stack[0].astype(np.float32)

        # Get binary cloud mask: returns shape (1, H, W) uint8 (0 or 1)
        mask_stack = detector.get_cloud_masks(tensor_4d)
        cloud_mask = mask_stack[0].astype(bool)

        logger.info(
            f"s2cloudless detector execution successful: "
            f"Cloud Pixels: {int(np.count_nonzero(cloud_mask))} / {cloud_mask.size} "
            f"({(np.count_nonzero(cloud_mask) / cloud_mask.size) * 100:.2f}%)"
        )

        return cloud_prob, cloud_mask

    except ImportError as imp_err:
        logger.warning(f"s2cloudless package not imported ({imp_err}). Using fallback detector.")
        return _spectral_fallback_detector(canvas_data, threshold=threshold)
    except Exception as exc:
        logger.warning(f"s2cloudless execution encountered error ({exc}). Falling back to spectral detector.")
        return _spectral_fallback_detector(canvas_data, threshold=threshold)
