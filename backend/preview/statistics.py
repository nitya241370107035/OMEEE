"""
Raster Statistics & NoData Calculator

Calculates comprehensive per-band and overall raster statistics, honest NoData
percentage (handling NaN values, infinite floats, and explicit NoData markers),
and mask coverage statistics.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import numpy as np

from backend.preview.raster_metadata import RasterMetadata


@dataclass
class RasterStatistics:
    """Comprehensive statistical metrics for a raster dataset."""
    total_pixels: int
    valid_pixels: int
    nodata_pixels: int
    nodata_percentage: float
    valid_pixel_percentage: float
    per_band_stats: List[Dict[str, Any]] = field(default_factory=list)
    is_mask: bool = False
    masked_pixel_count: Optional[int] = None
    valid_ground_count: Optional[int] = None
    masked_percentage: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Converts statistics into dictionary for inspection output."""
        return {
            "total_pixels": self.total_pixels,
            "valid_pixels": self.valid_pixels,
            "nodata_pixels": self.nodata_pixels,
            "nodata_percentage": round(self.nodata_percentage, 4),
            "valid_pixel_percentage": round(self.valid_pixel_percentage, 4),
            "is_mask": self.is_mask,
            "masked_pixel_count": self.masked_pixel_count,
            "valid_ground_count": self.valid_ground_count,
            "masked_percentage": round(self.masked_percentage, 4) if self.masked_percentage is not None else None,
            "per_band_stats": self.per_band_stats
        }


def compute_raster_statistics(
    data: np.ndarray,
    metadata: RasterMetadata
) -> RasterStatistics:
    """
    Computes rigorous per-band and overall statistics from raster data array.

    Args:
        data: Numpy array with shape (Bands, Height, Width) or (Height, Width).
        metadata: RasterMetadata instance for nodata and band context.

    Returns:
        RasterStatistics: Structured summary of valid pixels, NoData metrics, and distributions.
    """
    if data.ndim == 2:
        data = np.expand_dims(data, axis=0)

    num_bands, height, width = data.shape
    total_pixels_per_band = height * width

    # Determine overall NoData mask across all bands
    # Overall NoData definition: A spatial pixel is considered NoData if ANY band is NoData (NaN, Inf, or explicit nodata value)
    overall_nodata_mask = np.zeros((height, width), dtype=bool)
    per_band_stats: List[Dict[str, Any]] = []

    for b_idx in range(num_bands):
        band_arr = data[b_idx]
        band_name = metadata.band_descriptions[b_idx] if b_idx < len(metadata.band_descriptions) else f"Band_{b_idx+1}"

        # Build band NoData mask
        band_nodata = np.zeros((height, width), dtype=bool)

        # 1. Floating-point NaNs / Infs
        if np.issubdtype(band_arr.dtype, np.floating):
            band_nodata |= np.isnan(band_arr) | np.isinf(band_arr)

        # 2. Explicit NoData value check
        if metadata.nodata is not None:
            if np.isnan(metadata.nodata):
                band_nodata |= np.isnan(band_arr)
            else:
                band_nodata |= np.isclose(band_arr, metadata.nodata)

        overall_nodata_mask |= band_nodata

        # Valid band pixels
        valid_band_data = band_arr[~band_nodata]
        b_nodata_count = int(np.sum(band_nodata))
        b_valid_count = int(len(valid_band_data))
        b_nodata_pct = float((b_nodata_count / total_pixels_per_band) * 100.0)

        if b_valid_count > 0:
            b_min = float(np.min(valid_band_data))
            b_max = float(np.max(valid_band_data))
            b_mean = float(np.mean(valid_band_data))
            b_std = float(np.std(valid_band_data))
            b_p1 = float(np.percentile(valid_band_data, 1))
            b_p50 = float(np.percentile(valid_band_data, 50))
            b_p99 = float(np.percentile(valid_band_data, 99))
        else:
            b_min = b_max = b_mean = b_std = b_p1 = b_p50 = b_p99 = 0.0

        per_band_stats.append({
            "band_index": b_idx + 1,
            "band_name": band_name,
            "dtype": str(band_arr.dtype),
            "valid_pixels": b_valid_count,
            "nodata_pixels": b_nodata_count,
            "nodata_percentage": round(b_nodata_pct, 4),
            "min": round(b_min, 4),
            "max": round(b_max, 4),
            "mean": round(b_mean, 4),
            "std": round(b_std, 4),
            "p1": round(b_p1, 4),
            "p50": round(b_p50, 4),
            "p99": round(b_p99, 4)
        })

    total_spatial_pixels = total_pixels_per_band
    overall_nodata_pixels = int(np.sum(overall_nodata_mask))
    overall_valid_pixels = total_spatial_pixels - overall_nodata_pixels
    overall_nodata_pct = float((overall_nodata_pixels / total_spatial_pixels) * 100.0)
    overall_valid_pct = float(100.0 - overall_nodata_pct)

    # Check if single-band binary mask (e.g. cloud_mask.tif, shadow_mask.tif, bad_mask.tif)
    is_mask = False
    masked_count = None
    valid_ground_count = None
    masked_pct = None

    if num_bands == 1 and (
        data.dtype == bool or 
        "mask" in metadata.file_name.lower() or 
        (set(np.unique(data)).issubset({0, 1}))
    ):
        is_mask = True
        masked_count = int(np.sum(data[0] > 0))
        valid_ground_count = total_spatial_pixels - masked_count
        masked_pct = float((masked_count / total_spatial_pixels) * 100.0)

    return RasterStatistics(
        total_pixels=total_spatial_pixels,
        valid_pixels=overall_valid_pixels,
        nodata_pixels=overall_nodata_pixels,
        nodata_percentage=overall_nodata_pct,
        valid_pixel_percentage=overall_valid_pct,
        per_band_stats=per_band_stats,
        is_mask=is_mask,
        masked_pixel_count=masked_count,
        valid_ground_count=valid_ground_count,
        masked_percentage=masked_pct
    )
