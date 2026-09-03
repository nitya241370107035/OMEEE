"""
Reconstruction & Provenance Mask Management

Generates and saves the explicit provenance raster tracking whether each pixel is:
- 0 = OBSERVED_CLEAR
- 1 = THIN_CLOUD_CORRECTED
- 2 = THICK_CLOUD_RECONSTRUCTED
- 3 = UNRESOLVED_CLOUD
- 4 = NODATA

Produces: reconstruction_mask.tif
"""

import logging
from pathlib import Path
from typing import Dict, Any
import numpy as np
import rasterio
from rasterio.transform import Affine

from backend.cloud_removal.config import ProvenanceClass

logger = logging.getLogger(__name__)


def create_reconstruction_mask(
    height: int,
    width: int,
    clear_mask: np.ndarray,
    thin_corrected_mask: np.ndarray,
    thick_reconstructed_mask: np.ndarray = None,
    unresolved_mask: np.ndarray = None,
    nodata_mask: np.ndarray = None
) -> np.ndarray:
    """
    Assembles the multi-class pixel reconstruction provenance mask.
    """
    rec_mask = np.full((height, width), ProvenanceClass.UNRESOLVED_CLOUD, dtype=np.uint8)

    # 1. Clear observed pixels
    rec_mask[clear_mask] = ProvenanceClass.OBSERVED_CLEAR

    # 2. Thin cloud corrected pixels
    rec_mask[thin_corrected_mask] = ProvenanceClass.THIN_CLOUD_CORRECTED

    # 3. Thick cloud reconstructed pixels (if any)
    if thick_reconstructed_mask is not None:
        rec_mask[thick_reconstructed_mask] = ProvenanceClass.THICK_CLOUD_RECONSTRUCTED

    # 4. Explicit unresolved pixels
    if unresolved_mask is not None:
        rec_mask[unresolved_mask] = ProvenanceClass.UNRESOLVED_CLOUD

    # 5. NoData pixels
    if nodata_mask is not None:
        rec_mask[nodata_mask] = ProvenanceClass.NODATA

    return rec_mask


def save_reconstruction_mask(
    rec_mask: np.ndarray,
    output_path: Path,
    crs: str,
    transform: Affine
) -> Path:
    """Saves the reconstruction mask as a single-band GeoTIFF."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = rec_mask.shape
    with rasterio.open(
        output_path, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="uint8", crs=crs, transform=transform
    ) as dst:
        dst.write(rec_mask, 1)
    logger.info(f"Saved reconstruction provenance mask: {output_path}")
    return output_path


def compute_provenance_breakdown(rec_mask: np.ndarray) -> Dict[str, Any]:
    """Computes exact percentage breakdown of pixel provenance."""
    total_px = rec_mask.size
    obs_px = int(np.count_nonzero(rec_mask == ProvenanceClass.OBSERVED_CLEAR))
    thin_px = int(np.count_nonzero(rec_mask == ProvenanceClass.THIN_CLOUD_CORRECTED))
    thick_px = int(np.count_nonzero(rec_mask == ProvenanceClass.THICK_CLOUD_RECONSTRUCTED))
    unres_px = int(np.count_nonzero(rec_mask == ProvenanceClass.UNRESOLVED_CLOUD))
    nodata_px = int(np.count_nonzero(rec_mask == ProvenanceClass.NODATA))

    return {
        "total_pixels": total_px,
        "observed_pixel_percentage": round(obs_px / total_px * 100.0, 2),
        "thin_cloud_corrected_percentage": round(thin_px / total_px * 100.0, 2),
        "thick_cloud_reconstructed_percentage": round(thick_px / total_px * 100.0, 2),
        "unresolved_pixel_percentage": round(unres_px / total_px * 100.0, 2),
        "nodata_percentage": round(nodata_px / total_px * 100.0, 2),
        "counts": {
            "observed_clear": obs_px,
            "thin_cloud_corrected": thin_px,
            "thick_cloud_reconstructed": thick_px,
            "unresolved_cloud": unres_px,
            "nodata": nodata_px
        }
    }
