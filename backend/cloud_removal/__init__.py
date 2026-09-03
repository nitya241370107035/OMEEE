"""
Cloud Removal & De-Clouding Module (Phase 5)

Provides:
- Input adaptation and layer validation
- Cloud region classification (CLEAR, THIN_CLOUD, UNCERTAIN_CLOUD, THICK_CLOUD)
- Cloud trimap generation (0, 128, 255)
- Continuous cloud opacity alpha estimation [0.0, 1.0]
- Region-aware cloud matting physical surface recovery
- Provenance and reconstruction mask tracking
- Scientific quality validation & strict 512x512 tile generation
"""

from backend.cloud_removal.config import (
    CloudRegionClass,
    ProvenanceClass,
    TrimapClass,
    CloudRemovalConfig,
    DEFAULT_CONFIG
)
from backend.cloud_removal.input_adapter import CloudRemovalInputAdapter, SceneInputs
from backend.cloud_removal.cloud_regions import classify_cloud_regions, compute_region_statistics
from backend.cloud_removal.trimap import generate_cloud_trimap
from backend.cloud_removal.opacity import estimate_cloud_opacity
from backend.cloud_removal.reconstruction_mask import create_reconstruction_mask, compute_provenance_breakdown
from backend.cloud_removal.removal import remove_clouds, save_cloud_removal_products
from backend.cloud_removal.validation import (
    validate_clear_pixel_preservation,
    validate_cloud_removal_raster_integrity,
    validate_strict_512_tile_outputs
)

__all__ = [
    "CloudRegionClass",
    "ProvenanceClass",
    "TrimapClass",
    "CloudRemovalConfig",
    "DEFAULT_CONFIG",
    "CloudRemovalInputAdapter",
    "SceneInputs",
    "classify_cloud_regions",
    "compute_region_statistics",
    "generate_cloud_trimap",
    "estimate_cloud_opacity",
    "create_reconstruction_mask",
    "compute_provenance_breakdown",
    "remove_clouds",
    "save_cloud_removal_products",
    "validate_clear_pixel_preservation",
    "validate_cloud_removal_raster_integrity",
    "validate_strict_512_tile_outputs"
]
