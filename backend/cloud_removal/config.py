"""
Configuration and Central Constants for Cloud Removal & De-Clouding Module

Defines:
- Standardized Cloud Region Categories (CLEAR, THIN_CLOUD, UNCERTAIN_CLOUD, THICK_CLOUD)
- Standardized Provenance / Reconstruction Codes (OBSERVED_CLEAR, THIN_CLOUD_CORRECTED, etc.)
- Configuration-Driven Cloud Removal Parameters & Strategies
"""

from enum import IntEnum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


class CloudRegionClass(IntEnum):
    """Classification of scene regions based on cloud density and probability."""
    CLEAR = 0             # Cloud probability < clear_threshold
    THIN_CLOUD = 1        # Semi-transparent / thin cloud with transmitted surface detail
    UNCERTAIN_CLOUD = 2   # Transition / edge region with uncertain opacity
    THICK_CLOUD = 3       # Opaque dense cloud completely occluding surface
    NODATA = 4            # Invalid or out-of-bounds pixels


class ProvenanceClass(IntEnum):
    """Explicit pixel-level provenance and reconstruction status."""
    OBSERVED_CLEAR = 0              # Genuine observed satellite pixel (unaltered)
    THIN_CLOUD_CORRECTED = 1        # Physical surface recovery via cloud matting
    THICK_CLOUD_RECONSTRUCTED = 2   # Reconstructed via temporal / model synthesis
    UNRESOLVED_CLOUD = 3            # Opaque cloud with unresolved surface (marked/flagged)
    NODATA = 4                      # NoData / background pixel


class TrimapClass(IntEnum):
    """Standardized discrete values for cloud trimap raster."""
    BACKGROUND_CLEAR = 0            # Definite clear background
    UNCERTAIN_TRANSPARENT = 128     # Semi-transparent / thin cloud / transition
    FOREGROUND_OPAQUE = 255         # Definite opaque cloud foreground


@dataclass
class CloudRemovalConfig:
    """Configuration parameters for cloud region classification and removal."""
    # Probability thresholds for region categorization
    clear_prob_threshold: float = 0.20       # Below this is definite clear
    thin_prob_threshold: float = 0.45        # Between clear and thin threshold is thin cloud
    thick_prob_threshold: float = 0.70       # Above this is definite thick cloud

    # Opacity (Alpha) estimation parameters
    min_opacity: float = 0.05                # Floor for cloud opacity
    max_opacity: float = 0.95                # Ceiling for thin cloud matting
    regularization_eps: float = 0.05         # Epsilon in denominator to avoid division by zero

    # Strategy for handling thick opaque clouds:
    # "mark_unresolved" (scientifically safest default), "temporal_reconstruction", "spatial_inpaint"
    thick_cloud_strategy: str = "mark_unresolved"

    # Cloud-free preservation tolerance (MAE must be within this tolerance, ideally 0.0)
    clear_preservation_tolerance: float = 1e-4

    # Target 512x512 tile dimension settings
    target_tile_size: int = 512
    tile_overlap_pct: float = 0.10

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clear_prob_threshold": self.clear_prob_threshold,
            "thin_prob_threshold": self.thin_prob_threshold,
            "thick_prob_threshold": self.thick_prob_threshold,
            "min_opacity": self.min_opacity,
            "max_opacity": self.max_opacity,
            "regularization_eps": self.regularization_eps,
            "thick_cloud_strategy": self.thick_cloud_strategy,
            "clear_preservation_tolerance": self.clear_preservation_tolerance,
            "target_tile_size": self.target_tile_size,
            "tile_overlap_pct": self.tile_overlap_pct
        }


DEFAULT_CONFIG = CloudRemovalConfig()
