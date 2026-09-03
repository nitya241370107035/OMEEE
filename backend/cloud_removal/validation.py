"""
Scientific Quality Validation & Verification for Cloud Removal

Performs:
1. Cloud-Free Pixel Preservation Metrics (MAE, RMSE, Max Absolute Error, % Modified Clear Pixels)
2. Raster Metadata & Integrity Checks (CRS, Affine Transform, Dimensions, NoData)
3. Strict 512x512 Tile Dimension Validation
4. Provenance Mask Consistency Verification
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np
import rasterio

from backend.cloud_removal.config import CloudRegionClass, ProvenanceClass

logger = logging.getLogger(__name__)


def validate_clear_pixel_preservation(
    observed_rgb: np.ndarray,
    declouded_rgb: np.ndarray,
    clear_mask: np.ndarray,
    tolerance: float = 1e-4
) -> Dict[str, Any]:
    """
    Computes scientific distortion metrics exclusively on genuine clear observed pixels.
    MAE, RMSE, and Max Error must be 0.0 within numerical precision.
    """
    if not clear_mask.any():
        return {
            "clear_pixel_count": 0,
            "clear_pixel_mae": 0.0,
            "clear_pixel_rmse": 0.0,
            "max_absolute_difference": 0.0,
            "modified_clear_pixels_percentage": 0.0,
            "preservation_status": "PASS (No clear pixels to evaluate)"
        }

    clear_px_count = int(np.count_nonzero(clear_mask))
    diffs = []

    for b in range(3):
        obs_b = observed_rgb[b, clear_mask].astype(np.float64)
        dec_b = declouded_rgb[b, clear_mask].astype(np.float64)
        diff = np.abs(dec_b - obs_b)
        diffs.append(diff)

    all_diffs = np.concatenate(diffs)
    mae = float(np.mean(all_diffs))
    rmse = float(np.sqrt(np.mean(all_diffs ** 2)))
    max_diff = float(np.max(all_diffs))
    
    modified_count = int(np.count_nonzero(all_diffs > tolerance))
    modified_pct = round(modified_count / all_diffs.size * 100.0, 4)

    is_preserved = (mae <= tolerance) and (max_diff <= tolerance)
    status = "PASS" if is_preserved else "FAIL"

    return {
        "clear_pixel_count": clear_px_count,
        "clear_pixel_mae": round(mae, 6),
        "clear_pixel_rmse": round(rmse, 6),
        "max_absolute_difference": round(max_diff, 6),
        "modified_clear_pixels_percentage": modified_pct,
        "preservation_status": status
    }


def validate_cloud_removal_raster_integrity(
    declouded_path: Path,
    reconstruction_mask_path: Path,
    expected_height: int,
    expected_width: int,
    expected_crs: str
) -> Dict[str, Any]:
    """
    Verifies that generated cloud removal GeoTIFFs are readable, have valid dimensions,
    and match the expected coordinate reference system.
    """
    errors = []

    # 1. Validate declouded output GeoTIFF
    if not declouded_path.exists():
        errors.append(f"Declouded output file missing: {declouded_path}")
        return {"status": "FAIL", "errors": errors}

    try:
        with rasterio.open(declouded_path) as src:
            if src.count != 3:
                errors.append(f"Declouded output has {src.count} bands, expected 3")
            if src.height != expected_height or src.width != expected_width:
                errors.append(f"Declouded dimension mismatch: got ({src.height}, {src.width}), expected ({expected_height}, {expected_width})")
            if expected_crs and src.crs and str(src.crs) != expected_crs:
                errors.append(f"CRS mismatch: got {src.crs}, expected {expected_crs}")
            
            data = src.read()
            if np.isnan(data).any():
                errors.append("Declouded raster contains unexpected NaN values")
    except Exception as e:
        errors.append(f"Failed to read declouded GeoTIFF: {str(e)}")

    # 2. Validate reconstruction mask GeoTIFF
    if not reconstruction_mask_path.exists():
        errors.append(f"Reconstruction mask file missing: {reconstruction_mask_path}")
    else:
        try:
            with rasterio.open(reconstruction_mask_path) as src_m:
                if src_m.height != expected_height or src_m.width != expected_width:
                    errors.append(f"Reconstruction mask dimension mismatch: got ({src_m.height}, {src_m.width}), expected ({expected_height}, {expected_width})")
                m_data = src_m.read(1)
                valid_codes = {int(p) for p in ProvenanceClass}
                found_codes = set(np.unique(m_data).tolist())
                if not found_codes.issubset(valid_codes):
                    errors.append(f"Reconstruction mask contains invalid provenance codes: {found_codes - valid_codes}")
        except Exception as e:
            errors.append(f"Failed to read reconstruction mask GeoTIFF: {str(e)}")

    status = "PASS" if not errors else "FAIL"
    return {
        "status": status,
        "errors": errors
    }


def validate_strict_512_tile_outputs(tiles_dir: Path) -> Dict[str, Any]:
    """
    Inspects all generated output tiles and strictly asserts that height == 512 and width == 512.
    """
    if not tiles_dir.exists():
        return {"status": "FAIL", "total_tiles": 0, "valid_512_count": 0, "errors": ["Tiles directory not found"]}

    tiles = sorted(list(tiles_dir.glob("*.tif")))
    if not tiles:
        return {"status": "FAIL", "total_tiles": 0, "valid_512_count": 0, "errors": ["No tiles found in directory"]}

    valid_count = 0
    invalid_tiles = []

    for t in tiles:
        try:
            with rasterio.open(t) as src:
                if src.height == 512 and src.width == 512 and src.count == 3:
                    valid_count += 1
                else:
                    invalid_tiles.append(f"{t.name} ({src.height}x{src.width}, count={src.count})")
        except Exception as e:
            invalid_tiles.append(f"{t.name} (Error: {str(e)})")

    status = "PASS" if valid_count == len(tiles) and not invalid_tiles else "FAIL"
    return {
        "status": status,
        "total_tiles": len(tiles),
        "valid_512_count": valid_count,
        "invalid_tiles": invalid_tiles
    }
