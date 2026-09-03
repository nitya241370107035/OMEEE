"""
Sentinel-2 Optical Dataset Adapter

Orchestrates multi-temporal Sentinel-2 optical data for Eastern Ladakh and
Pipeline Validation domains. Reuses existing Phase 1 & Phase 2 preprocessing
pipelines without duplicating STAC, canvas, cloud masking, or tiling code.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datasets.dataset_base import BaseDatasetAdapter, DatasetValidationResult, ValidationStatus
from backend.ingestion.pipeline import run_aoi_pipeline, PipelineResult
from backend.ingestion.input_validator import validate_aoi

logger = logging.getLogger(__name__)


class Sentinel2DatasetAdapter(BaseDatasetAdapter):
    """
    Adapter for Sentinel-2 MSI multi-temporal optical imagery datasets.
    """

    def __init__(
        self,
        dataset_id: str,
        base_path: Path,
        domain: str = "eastern_ladakh",
        config: Optional[Dict[str, Any]] = None
    ):
        super().__init__(dataset_id=dataset_id, base_path=base_path, config=config)
        self._domain = domain

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def modality(self) -> str:
        return "multispectral"

    @property
    def task(self) -> str:
        if self._domain == "pipeline_validation":
            return "preprocessing_validation"
        return "temporal_change_detection"

    def validate(self) -> DatasetValidationResult:
        """Validates Sentinel-2 dataset directory structure, metadata, and files."""
        errors: List[str] = []
        warnings: List[str] = []
        info: Dict[str, Any] = {"domain": self.domain, "modality": self.modality, "task": self.task}

        if not self.base_path.exists():
            errors.append(f"Base path does not exist: {self.base_path}")
            return DatasetValidationResult(
                dataset_id=self.dataset_id,
                status=ValidationStatus.FAIL,
                errors=errors,
                warnings=warnings,
                info=info
            )

        # Expected subdirectories for Eastern Ladakh
        if self._domain == "eastern_ladakh":
            subdirs = ["raw", "intermediate", "normalized", "tiles", "manifests"]
            for s in subdirs:
                s_path = self.base_path / s
                if not s_path.exists():
                    warnings.append(f"Subdirectory '{s}' not yet created under {self.base_path}")

        # Check existing manifests or validation files
        manifest_files = list(self.base_path.glob("**/manifest.json"))
        info["manifest_count"] = len(manifest_files)
        if not manifest_files:
            warnings.append(f"No manifest.json found in {self.base_path} (dataset configured/placeholder)")

        status = ValidationStatus.FAIL if errors else (ValidationStatus.WARNING if warnings else ValidationStatus.PASS)
        return DatasetValidationResult(
            dataset_id=self.dataset_id,
            status=status,
            errors=errors,
            warnings=warnings,
            info=info
        )

    def get_metadata(self) -> Dict[str, Any]:
        """Returns structured metadata for the dataset."""
        return {
            "dataset_id": self.dataset_id,
            "domain": self.domain,
            "sensor": "Sentinel-2 MSI",
            "modality": self.modality,
            "task": self.task,
            "bands_required": ["B01", "B02", "B03", "B04", "B05", "B08", "B8A", "B09", "B10", "B11", "B12"],
            "base_path": str(self.base_path),
            "config": self.config
        }

    def list_samples(self) -> List[Dict[str, Any]]:
        """Lists samples registered in manifest files."""
        samples: List[Dict[str, Any]] = []
        for mf in self.base_path.glob("**/manifest.json"):
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "tiles" in data:
                        samples.extend(data["tiles"])
                    elif "temporal_pairs" in data:
                        samples.extend(data["temporal_pairs"])
                    else:
                        samples.append(data)
            except Exception as e:
                logger.warning(f"Error reading manifest {mf}: {e}")
        return samples

    def generate_manifest(self) -> Dict[str, Any]:
        """Compiles standard manifest from existing sub-manifests."""
        samples = self.list_samples()
        return {
            "dataset_id": self.dataset_id,
            "domain": self.domain,
            "modality": self.modality,
            "task": self.task,
            "total_samples": len(samples),
            "samples": samples
        }

    def process_aoi_temporal_scene(
        self,
        aoi_geometry_path: Path,
        region_id: str,
        year: int,
        seasonal_window: str = "07-01/10-31",
        max_cloud_cover: float = 20.0,
        offline_fallback: bool = True
    ) -> PipelineResult:
        """
        Orchestrates an ingestion run for a specific AOI and Year using the existing Phase 1 & 2 pipeline.
        Reuses run_aoi_pipeline directly.
        """
        date_range = f"{year}-{seasonal_window.split('/')[0]}/{year}-{seasonal_window.split('/')[1]}"
        logger.info(f"Orchestrating Sentinel-2 ingestion for {region_id} ({year}): {date_range}")

        return run_aoi_pipeline(
            geojson_input=aoi_geometry_path,
            region_id=region_id,
            datetime_range=date_range,
            max_cloud_cover=max_cloud_cover,
            ground_crop_size=512,
            overlap_ratio=0.10,
            base_data_dir=self.base_path,
            offline_fallback=offline_fallback
        )
