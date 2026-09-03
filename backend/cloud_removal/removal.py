"""
Core Cloud Removal & Surface Recovery Engine

Implements region-aware cloud matting and physical surface reflectance recovery:
1. Clear Pixels: Strictly preserved observed satellite measurements.
2. Thin Cloud Pixels: Cloud matting physical inversion: S = (I - alpha * C) / (1 - alpha).
3. Thick Cloud Pixels: Configuration-driven handling (safely marked as unresolved or reconstructed).

Produces: declouded_observed.tif, declouded_output.tif, reconstruction_mask.tif
"""

import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import numpy as np
import rasterio
from rasterio.transform import Affine

from backend.cloud_removal.config import (
    CloudRemovalConfig,
    CloudRegionClass,
    ProvenanceClass,
    DEFAULT_CONFIG
)
from backend.cloud_removal.cloud_regions import classify_cloud_regions
from backend.cloud_removal.trimap import generate_cloud_trimap
from backend.cloud_removal.opacity import estimate_cloud_opacity
from backend.cloud_removal.reconstruction_mask import create_reconstruction_mask

logger = logging.getLogger(__name__)


def remove_clouds(
    rgb_data: np.ndarray,
    cloud_prob: np.ndarray,
    cloud_mask: np.ndarray,
    shadow_mask: Optional[np.ndarray] = None,
    config: CloudRemovalConfig = DEFAULT_CONFIG,
    temporal_candidate: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Executes the full region-aware cloud removal pipeline.

    Args:
        rgb_data: Shape (3, H, W) uint8 observed optical canvas.
        cloud_prob: Shape (H, W) float32 cloud probability [0.0 - 1.0].
        cloud_mask: Shape (H, W) uint8 binary cloud mask.
        shadow_mask: Optional Shape (H, W) uint8 shadow mask.
        config: CloudRemovalConfig parameters.
        temporal_candidate: Optional co-registered cloud-free scene for temporal reconstruction.

    Returns:
        Tuple containing:
        - declouded_output: Shape (3, H, W) uint8 final de-clouded RGB canvas.
        - declouded_observed: Shape (3, H, W) uint8 observed clear pixels preserved.
        - reconstruction_mask: Shape (H, W) uint8 provenance codes.
        - region_map: Shape (H, W) uint8 cloud region categories.
        - opacity: Shape (H, W) float32 cloud opacity alpha map.
    """
    c, h, w = rgb_data.shape
    if shadow_mask is None:
        shadow_mask = np.zeros((h, w), dtype=np.uint8)

    # 1. Cloud Region Classification & Trimap
    region_map = classify_cloud_regions(cloud_prob, cloud_mask, config)
    trimap = generate_cloud_trimap(cloud_prob, cloud_mask, config)
    opacity = estimate_cloud_opacity(cloud_prob, trimap, rgb_data, config)

    # 2. Identify Pixel Subsets
    clear_mask = (region_map == CloudRegionClass.CLEAR)
    thin_mask = (region_map == CloudRegionClass.THIN_CLOUD) | (region_map == CloudRegionClass.UNCERTAIN_CLOUD)
    thick_mask = (region_map == CloudRegionClass.THICK_CLOUD)

    # 3. Estimate Cloud Radiance Vector (C_bar) from high-confidence thick clouds
    if thick_mask.any():
        c_cloud = np.zeros(3, dtype=np.float32)
        for b in range(3):
            thick_vals = rgb_data[b, thick_mask]
            # Use 90th percentile to capture pure cloud reflectance
            c_cloud[b] = float(np.percentile(thick_vals, 90.0)) if len(thick_vals) > 0 else 240.0
    else:
        # Standard white cloud default in uint8 RGB space
        c_cloud = np.array([235.0, 235.0, 245.0], dtype=np.float32)

    # 4. Initialize Outputs
    declouded_output = np.copy(rgb_data).astype(np.float32)
    declouded_observed = np.zeros_like(rgb_data)

    # Step A: Strictly Preserve Clear Observed Pixels
    for b in range(3):
        declouded_observed[b, clear_mask] = rgb_data[b, clear_mask]
        declouded_output[b, clear_mask] = rgb_data[b, clear_mask]

    # Step B: Thin Cloud Matting Physical Inversion: S = (I - alpha * C) / (1 - alpha + eps)
    if thin_mask.any():
        alpha_thin = opacity[thin_mask]
        eps = config.regularization_eps
        denom = np.clip(1.0 - alpha_thin + eps, 0.10, 1.0)
        for b in range(3):
            obs_b = rgb_data[b, thin_mask].astype(np.float32)
            c_b = c_cloud[b]
            # Invert physical cloud mixing equation
            s_recovered = (obs_b - alpha_thin * c_b) / denom
            declouded_output[b, thin_mask] = np.clip(s_recovered, 0.0, 255.0)

    # Step C: Thick Cloud Strategy
    thick_reconstructed_mask = np.zeros((h, w), dtype=bool)
    unresolved_mask = np.zeros((h, w), dtype=bool)

    if thick_mask.any():
        if config.thick_cloud_strategy == "temporal_reconstruction" and temporal_candidate is not None:
            # Use co-registered temporal candidate for thick cloud holes
            for b in range(3):
                declouded_output[b, thick_mask] = temporal_candidate[b, thick_mask]
            thick_reconstructed_mask[thick_mask] = True
        elif config.thick_cloud_strategy == "spatial_inpaint":
            # Simple boundary smooth interpolation for small thick cloud holes
            from scipy.ndimage import gaussian_filter
            for b in range(3):
                blurred = gaussian_filter(declouded_output[b], sigma=3.0)
                declouded_output[b, thick_mask] = blurred[thick_mask]
            thick_reconstructed_mask[thick_mask] = True
        else:
            # Scientifically safest default: mark as unresolved thick cloud
            unresolved_mask[thick_mask] = True
            # Retain original observed value in output raster for inspection

    # Final uint8 casting
    declouded_output_uint8 = np.clip(np.round(declouded_output), 0, 255).astype(np.uint8)

    # Step D: Construct Provenance / Reconstruction Mask
    rec_mask = create_reconstruction_mask(
        height=h,
        width=w,
        clear_mask=clear_mask,
        thin_corrected_mask=thin_mask,
        thick_reconstructed_mask=thick_reconstructed_mask,
        unresolved_mask=unresolved_mask
    )

    return declouded_output_uint8, declouded_observed, rec_mask, region_map, opacity


def save_cloud_removal_products(
    declouded_output: np.ndarray,
    declouded_observed: np.ndarray,
    reconstruction_mask: np.ndarray,
    cloud_region_map: np.ndarray,
    cloud_opacity: np.ndarray,
    output_dir: Path,
    crs: str,
    transform: Affine
) -> Dict[str, Path]:
    """
    Persists all generated cloud removal rasters into standardized directory structure.
    """
    regions_dir = output_dir / "cloud_regions"
    out_dir = output_dir / "output"
    regions_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    h, w = reconstruction_mask.shape
    paths = {}

    # 1. declouded_output.tif
    p_output = out_dir / "declouded_output.tif"
    with rasterio.open(
        p_output, "w", driver="GTiff",
        height=h, width=w, count=3,
        dtype="uint8", crs=crs, transform=transform
    ) as dst:
        dst.write(declouded_output)
    paths["declouded_output"] = p_output

    # 2. declouded_observed.tif
    p_obs = out_dir / "declouded_observed.tif"
    with rasterio.open(
        p_obs, "w", driver="GTiff",
        height=h, width=w, count=3,
        dtype="uint8", crs=crs, transform=transform
    ) as dst:
        dst.write(declouded_observed)
    paths["declouded_observed"] = p_obs

    # 3. reconstruction_mask.tif
    p_rec = out_dir / "reconstruction_mask.tif"
    with rasterio.open(
        p_rec, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="uint8", crs=crs, transform=transform
    ) as dst:
        dst.write(reconstruction_mask, 1)
    paths["reconstruction_mask"] = p_rec

    # 4. cloud_region_map.tif
    p_reg = regions_dir / "cloud_region_map.tif"
    with rasterio.open(
        p_reg, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="uint8", crs=crs, transform=transform
    ) as dst:
        dst.write(cloud_region_map, 1)
    paths["cloud_region_map"] = p_reg

    # 5. cloud_opacity.tif
    p_op = regions_dir / "cloud_opacity.tif"
    with rasterio.open(
        p_op, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="float32", crs=crs, transform=transform
    ) as dst:
        dst.write(cloud_opacity.astype(np.float32), 1)
    paths["cloud_opacity"] = p_op

    logger.info(f"Saved all cloud removal products in: {output_dir}")
    return paths
