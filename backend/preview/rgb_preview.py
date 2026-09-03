"""
Metadata-Aware RGB & Mask Preview Renderer

Generates visual previews (PIL Images) for:
  1. Multi-band Sentinel-2 & RGB GeoTIFFs (with metadata-aware band discovery)
  2. Normalized uint8 composites
  3. Binary masks (cloud_mask, shadow_mask, bad_mask)
  4. Float32 cloud probability heatmaps
"""

import logging
from typing import Optional, Tuple
import numpy as np
from PIL import Image

from backend.preview.raster_metadata import RasterMetadata

logger = logging.getLogger(__name__)


def find_rgb_band_indices(metadata: RasterMetadata) -> Optional[Tuple[int, int, int]]:
    """
    Discovers 0-indexed (Red, Green, Blue) band indices based on metadata descriptions and tags.
    """
    num_bands = metadata.count
    if num_bands < 3:
        return None

    # Exact 3-band raster (standard RGB / normalized canvas)
    if num_bands == 3:
        return (0, 1, 2)

    # Multi-band raster: search band descriptions for B04 (Red), B03 (Green), B02 (Blue)
    descriptions_lower = [d.lower() for d in metadata.band_descriptions]
    r_idx = None
    g_idx = None
    b_idx = None

    for i, desc in enumerate(descriptions_lower):
        if "b04" in desc or "red" in desc:
            r_idx = i
        elif "b03" in desc or "green" in desc:
            g_idx = i
        elif "b02" in desc or "blue" in desc:
            b_idx = i

    if r_idx is not None and g_idx is not None and b_idx is not None:
        return (r_idx, g_idx, b_idx)

    # If 4-band (e.g. BGRN or RGBN)
    if num_bands >= 4:
        return (0, 1, 2)

    return None


def render_rgb_composite(
    data: np.ndarray,
    metadata: RasterMetadata,
    r_idx: int = 0,
    g_idx: int = 1,
    b_idx: int = 2
) -> Image.Image:
    """
    Renders an RGB composite PIL Image with automatic dynamic range handling.
    """
    r = data[r_idx].astype(np.float32)
    g = data[g_idx].astype(np.float32)
    b = data[b_idx].astype(np.float32)

    # Handle NaNs
    r = np.nan_to_num(r, nan=0.0)
    g = np.nan_to_num(g, nan=0.0)
    b = np.nan_to_num(b, nan=0.0)

    # Determine data range
    max_val = max(np.max(r), np.max(g), np.max(b))
    min_val = min(np.min(r), np.min(g), np.min(b))

    if max_val <= 1.0 and min_val >= 0.0:
        # Float [0.0, 1.0]
        r_u8 = np.clip(r * 255.0, 0, 255).astype(np.uint8)
        g_u8 = np.clip(g * 255.0, 0, 255).astype(np.uint8)
        b_u8 = np.clip(b * 255.0, 0, 255).astype(np.uint8)
    elif max_val <= 255.0 and data.dtype == np.uint8:
        # Standard uint8 [0, 255]
        r_u8 = r.astype(np.uint8)
        g_u8 = g.astype(np.uint8)
        b_u8 = b.astype(np.uint8)
    else:
        # Raw Digital Numbers (e.g. 0-10000): Percentile stretch per channel
        def _stretch(ch):
            p1, p99 = np.percentile(ch[ch > 0], 1) if np.any(ch > 0) else 0, np.percentile(ch[ch > 0], 99) if np.any(ch > 0) else 10000
            if p99 <= p1:
                p99 = p1 + 1.0
            return np.clip((ch - p1) / (p99 - p1) * 255.0, 0, 255).astype(np.uint8)

        r_u8 = _stretch(r)
        g_u8 = _stretch(g)
        b_u8 = _stretch(b)

    rgb_stack = np.stack([r_u8, g_u8, b_u8], axis=-1)
    return Image.fromarray(rgb_stack, mode="RGB")


def render_mask_preview(data: np.ndarray, color: str = "cyan") -> Image.Image:
    """
    Renders a single-band binary mask into high-contrast RGB visual.
    """
    mask_2d = data[0] if data.ndim == 3 else data
    mask_bool = mask_2d > 0
    h, w = mask_bool.shape

    out = np.zeros((h, w, 3), dtype=np.uint8)
    # Dark slate background
    out[:, :] = [25, 30, 42]

    # Overlay color
    if color == "cyan":
        out[mask_bool] = [0, 230, 255]  # Bright Cyan for cloud
    elif color == "yellow":
        out[mask_bool] = [255, 204, 0]  # Bright Amber for shadow
    elif color == "red":
        out[mask_bool] = [255, 65, 54]  # Bright Coral for bad pixels
    else:
        out[mask_bool] = [255, 255, 255]

    return Image.fromarray(out, mode="RGB")


def render_probability_heatmap(data: np.ndarray) -> Image.Image:
    """
    Renders float32 cloud probability map [0.0, 1.0] into a smooth heatmap.
    """
    prob_2d = data[0] if data.ndim == 3 else data
    prob_clean = np.clip(np.nan_to_num(prob_2d, nan=0.0), 0.0, 1.0)
    h, w = prob_clean.shape

    # Apply 3-point color gradient: Dark Navy (0.0) -> Amber/Orange (0.5) -> Bright White (1.0)
    out = np.zeros((h, w, 3), dtype=np.uint8)
    
    # Low probability (0.0 to 0.4): Navy Blue to Purple
    low_mask = prob_clean <= 0.4
    t_low = prob_clean[low_mask] / 0.4
    out[low_mask, 0] = (t_low * 120).astype(np.uint8)
    out[low_mask, 1] = (t_low * 40).astype(np.uint8)
    out[low_mask, 2] = (140 - t_low * 40).astype(np.uint8)

    # Mid to High probability (0.4 to 1.0): Orange to Pure White
    high_mask = prob_clean > 0.4
    t_high = (prob_clean[high_mask] - 0.4) / 0.6
    out[high_mask, 0] = (120 + t_high * 135).astype(np.uint8)
    out[high_mask, 1] = (40 + t_high * 215).astype(np.uint8)
    out[high_mask, 2] = (100 + t_high * 155).astype(np.uint8)

    return Image.fromarray(out, mode="RGB")


def render_mask_overlay(
    rgb_img: Image.Image,
    cloud_mask: Optional[np.ndarray] = None,
    shadow_mask: Optional[np.ndarray] = None,
    alpha: float = 0.45
) -> Image.Image:
    """
    Overlays cloud (Cyan) and shadow (Amber) masks directly onto the base RGB image.
    Visualization only; does not mutate any underlying raster arrays.
    """
    base_arr = np.array(rgb_img.convert("RGB")).astype(np.float32)
    h, w, _ = base_arr.shape
    overlay_arr = base_arr.copy()

    # Process shadow mask (Amber: 255, 204, 0)
    if shadow_mask is not None:
        s_2d = shadow_mask[0] if shadow_mask.ndim == 3 else shadow_mask
        if s_2d.shape == (h, w):
            s_bool = s_2d > 0
            amber = np.array([255, 204, 0], dtype=np.float32)
            overlay_arr[s_bool] = (1.0 - alpha) * overlay_arr[s_bool] + alpha * amber

    # Process cloud mask (Cyan: 0, 230, 255)
    if cloud_mask is not None:
        c_2d = cloud_mask[0] if cloud_mask.ndim == 3 else cloud_mask
        if c_2d.shape == (h, w):
            c_bool = c_2d > 0
            cyan = np.array([0, 230, 255], dtype=np.float32)
            overlay_arr[c_bool] = (1.0 - alpha) * overlay_arr[c_bool] + alpha * cyan

    out_u8 = np.clip(overlay_arr, 0, 255).astype(np.uint8)
    return Image.fromarray(out_u8, mode="RGB")



def generate_raster_preview(
    data: np.ndarray,
    metadata: RasterMetadata
) -> Tuple[Image.Image, str]:
    """
    Automated preview generator selecting the best visual strategy for the GeoTIFF.

    Returns:
        Tuple[PIL.Image, str]: (Rendered Image, Description of preview mode)
    """
    fname_lower = metadata.file_name.lower()

    # 1. Cloud probability map
    if "probability" in fname_lower or (metadata.count == 1 and np.issubdtype(data.dtype, np.floating) and np.max(data) <= 1.0):
        return render_probability_heatmap(data), "Cloud Probability Map (Heatmap [0.0, 1.0])"

    # 2. Binary masks
    if "shadow_mask" in fname_lower:
        return render_mask_preview(data, color="yellow"), "Shadow Mask Preview (Amber = Shadow)"
    elif "cloud_mask" in fname_lower:
        return render_mask_preview(data, color="cyan"), "Cloud Mask Preview (Cyan = Cloud)"
    elif "bad_mask" in fname_lower or "mask" in fname_lower:
        return render_mask_preview(data, color="red"), "Combined Quality Mask (Red = Invalid/Cloud/Shadow)"

    # 3. Single-band grayscale
    if metadata.count == 1:
        arr_2d = data[0] if data.ndim == 3 else data
        arr_clean = np.nan_to_num(arr_2d, nan=0.0)
        p1, p99 = np.percentile(arr_clean, 1), np.percentile(arr_clean, 99)
        if p99 <= p1:
            p99 = p1 + 1.0
        norm_gray = np.clip((arr_clean - p1) / (p99 - p1) * 255.0, 0, 255).astype(np.uint8)
        return Image.fromarray(norm_gray, mode="L").convert("RGB"), "Single-Band Grayscale Preview"

    # 4. Multi-band RGB composite
    rgb_indices = find_rgb_band_indices(metadata)
    if rgb_indices:
        r_i, g_i, b_i = rgb_indices
        img = render_rgb_composite(data, metadata, r_idx=r_i, g_idx=g_i, b_idx=b_i)
        r_name = metadata.band_descriptions[r_i] if r_i < len(metadata.band_descriptions) else f"Band_{r_i+1}"
        g_name = metadata.band_descriptions[g_i] if g_i < len(metadata.band_descriptions) else f"Band_{g_i+1}"
        b_name = metadata.band_descriptions[b_i] if b_i < len(metadata.band_descriptions) else f"Band_{b_i+1}"
        return img, f"RGB Composite (Red={r_name}, Green={g_name}, Blue={b_name})"

    # Fallback to first band grayscale
    arr_2d = data[0]
    p1, p99 = np.percentile(arr_2d, 1), np.percentile(arr_2d, 99)
    norm_gray = np.clip((arr_2d - p1) / (max(p99, p1 + 1.0) - p1) * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(norm_gray, mode="L").convert("RGB"), "Fallback Grayscale Preview (Band 1)"
