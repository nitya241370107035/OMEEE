"""
Cloud Region Classification Stage

Categorizes scene pixels into clear, thin cloud, uncertain cloud, and thick opaque cloud
using s2cloudless continuous probability maps and binary quality masks.

Produces: cloud_region_map.tif
"""

import logging
from pathlib import Path
from typing import Dict, Any, Tuple
import numpy as np
import rasterio
from rasterio.transform import Affine

from backend.cloud_removal.config import CloudRegionClass, CloudRemovalConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def classify_cloud_regions(
    cloud_prob: np.ndarray,
    cloud_mask: np.ndarray,
    config: CloudRemovalConfig = DEFAULT_CONFIG,
    nodata_mask: np.ndarray = None
) -> np.ndarray:
    """
    Classifies pixels into discrete cloud density categories.

    Args:
        cloud_prob: Shape (H, W) float32 array in [0.0, 1.0].
        cloud_mask: Shape (H, W) uint8 binary mask from Phase 2.
        config: Configuration parameters with thresholds.
        nodata_mask: Optional boolean mask where True = NoData.

    Returns:
        np.ndarray: Shape (H, W) uint8 array of CloudRegionClass enum values.
    """
    h, w = cloud_prob.shape
    region_map = np.zeros((h, w), dtype=np.uint8)  # Default CLEAR = 0

    # 1. Definite Clear: cloud_prob < clear_prob_threshold and not in cloud_mask
    clear_mask = (cloud_prob < config.clear_prob_threshold) & (cloud_mask == 0)
    region_map[clear_mask] = CloudRegionClass.CLEAR

    # 2. Thin Cloud: cloud_prob between clear_prob_threshold and thin_prob_threshold (or thin portion of cloud_mask)
    thin_mask = (
        ((cloud_prob >= config.clear_prob_threshold) & (cloud_prob < config.thin_prob_threshold)) |
        ((cloud_mask == 1) & (cloud_prob < config.thin_prob_threshold))
    )
    region_map[thin_mask] = CloudRegionClass.THIN_CLOUD

    # 3. Uncertain / Transition Cloud: cloud_prob between thin_prob_threshold and thick_prob_threshold
    uncertain_mask = (cloud_prob >= config.thin_prob_threshold) & (cloud_prob < config.thick_prob_threshold)
    region_map[uncertain_mask] = CloudRegionClass.UNCERTAIN_CLOUD

    # 4. Thick Opaque Cloud: cloud_prob >= thick_prob_threshold or high-confidence cloud_mask core
    thick_mask = (cloud_prob >= config.thick_prob_threshold)
    region_map[thick_mask] = CloudRegionClass.THICK_CLOUD

    # 5. Handle NoData
    if nodata_mask is not None:
        region_map[nodata_mask] = CloudRegionClass.NODATA

    return region_map


def save_cloud_region_map(
    region_map: np.ndarray,
    output_path: Path,
    crs: str,
    transform: Affine
) -> Path:
    """Saves the cloud region classification map as a single-band GeoTIFF."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = region_map.shape
    with rasterio.open(
        output_path, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="uint8", crs=crs, transform=transform
    ) as dst:
        dst.write(region_map, 1)
    logger.info(f"Saved cloud region map: {output_path}")
    return output_path


def compute_region_statistics(region_map: np.ndarray) -> Dict[str, Any]:
    """Computes percentage breakdown for each cloud region category."""
    total_px = region_map.size
    clear_px = int(np.count_nonzero(region_map == CloudRegionClass.CLEAR))
    thin_px = int(np.count_nonzero(region_map == CloudRegionClass.THIN_CLOUD))
    uncertain_px = int(np.count_nonzero(region_map == CloudRegionClass.UNCERTAIN_CLOUD))
    thick_px = int(np.count_nonzero(region_map == CloudRegionClass.THICK_CLOUD))
    nodata_px = int(np.count_nonzero(region_map == CloudRegionClass.NODATA))

    return {
        "total_pixels": total_px,
        "clear_percentage": round(clear_px / total_px * 100.0, 2),
        "thin_cloud_percentage": round(thin_px / total_px * 100.0, 2),
        "uncertain_cloud_percentage": round(uncertain_px / total_px * 100.0, 2),
        "thick_cloud_percentage": round(thick_px / total_px * 100.0, 2),
        "nodata_percentage": round(nodata_px / total_px * 100.0, 2),
        "pixel_counts": {
            "clear": clear_px,
            "thin": thin_px,
            "uncertain": uncertain_px,
            "thick": thick_px,
            "nodata": nodata_px
        }
    }
