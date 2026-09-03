"""
Temporal Cloud Reconstruction Interface (Phase 5.6)

Provides a standardized, provenance-preserving interface for optional multi-temporal
cloud reconstruction using registered temporal candidate pairs (e.g. from Phase 4).

Preserves:
- source_date & source_scene
- registration_quality & temporal_gap_days
- reconstruction_confidence
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
import numpy as np


@dataclass
class TemporalReconstructionMetadata:
    """Metadata tracking provenance and quality of temporal candidate data."""
    source_scene_id: str
    source_date: str
    target_date: str
    temporal_gap_days: int
    registration_residual_px: float
    confidence_score: float  # [0.0 - 1.0]
    is_enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_scene_id": self.source_scene_id,
            "source_date": self.source_date,
            "target_date": self.target_date,
            "temporal_gap_days": self.temporal_gap_days,
            "registration_residual_px": self.registration_residual_px,
            "confidence_score": self.confidence_score,
            "is_enabled": self.is_enabled
        }


class TemporalCloudReconstructor:
    """Interface for temporal reconstruction of thick cloud holes."""

    def __init__(self, metadata: Optional[TemporalReconstructionMetadata] = None):
        self.metadata = metadata or TemporalReconstructionMetadata(
            source_scene_id="NONE",
            source_date="NONE",
            target_date="NONE",
            temporal_gap_days=0,
            registration_residual_px=0.0,
            confidence_score=0.0,
            is_enabled=False
        )

    def reconstruct(
        self,
        cloudy_rgb: np.ndarray,
        thick_cloud_mask: np.ndarray,
        candidate_rgb: Optional[np.ndarray] = None
    ) -> np.ndarray:
        """
        Substitutes thick cloud regions with co-registered temporal candidate data
        only when explicitly enabled and valid candidate is supplied.
        """
        if not self.metadata.is_enabled or candidate_rgb is None:
            # Return original without modification
            return cloudy_rgb

        output = np.copy(cloudy_rgb)
        for b in range(3):
            output[b, thick_cloud_mask] = candidate_rgb[b, thick_cloud_mask]
        return output
