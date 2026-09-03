"""
Maritime Object Detection Dataset Adapter

Maintains isolated object detection and semantic retrieval dataset architecture
for maritime SAR benchmarks (xView3, OpenSARShip, SAR-Ship). Guarantees that
vessel detection data is never mixed with terrestrial temporal change detection data.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datasets.dataset_base import BaseDatasetAdapter, DatasetValidationResult, ValidationStatus

logger = logging.getLogger(__name__)


class MaritimeDatasetAdapter(BaseDatasetAdapter):
    """
    Adapter for maritime vessel detection and SAR chip datasets.
    """

    def __init__(
        self,
        dataset_id: str,
        base_path: Path,
        sensor: str = "Sentinel-1 C-SAR",
        object_classes: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        super().__init__(dataset_id=dataset_id, base_path=base_path, config=config)
        self._sensor = sensor
        self._object_classes = object_classes or ["vessel", "fishing", "non-fishing", "dark_vessel", "infrastructure"]

    @property
    def domain(self) -> str:
        return "maritime"

    @property
    def modality(self) -> str:
        return "sar"

    @property
    def task(self) -> str:
        return "ship_detection"

    def validate(self) -> DatasetValidationResult:
        """Validates maritime dataset directory structure and object detection schema."""
        errors: List[str] = []
        warnings: List[str] = []
        info: Dict[str, Any] = {
            "domain": self.domain,
            "modality": self.modality,
            "task": self.task,
            "sensor": self._sensor,
            "object_classes": self._object_classes
        }

        if not self.base_path.exists():
            errors.append(f"Base path does not exist: {self.base_path}")
            return DatasetValidationResult(
                dataset_id=self.dataset_id,
                status=ValidationStatus.FAIL,
                errors=errors,
                warnings=warnings,
                info=info
            )

        manifest_path = self.base_path / "manifest.json"
        if not manifest_path.exists():
            warnings.append(f"No manifest.json found in {self.base_path} (dataset configured/placeholder)")
        else:
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                info["sample_count"] = len(data.get("samples", []))
            except Exception as e:
                errors.append(f"Invalid manifest.json in {self.base_path}: {e}")

        status = ValidationStatus.FAIL if errors else (ValidationStatus.WARNING if warnings else ValidationStatus.PASS)
        return DatasetValidationResult(
            dataset_id=self.dataset_id,
            status=status,
            errors=errors,
            warnings=warnings,
            info=info
        )

    def get_metadata(self) -> Dict[str, Any]:
        """Returns structured metadata for maritime dataset."""
        return {
            "dataset_id": self.dataset_id,
            "domain": self.domain,
            "sensor": self._sensor,
            "modality": self.modality,
            "task": self.task,
            "object_classes": self._object_classes,
            "base_path": str(self.base_path),
            "isolation_guarantee": "Strict domain isolation: Maritime data is segregated from terrestrial temporal change analysis."
        }

    def list_samples(self) -> List[Dict[str, Any]]:
        """Lists maritime object detection samples."""
        manifest_path = self.base_path / "manifest.json"
        if not manifest_path.exists():
            return []
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("samples", [])
        except Exception as e:
            logger.warning(f"Error reading manifest {manifest_path}: {e}")
            return []

    def generate_manifest(self) -> Dict[str, Any]:
        """Generates standard maritime object detection manifest."""
        samples = self.list_samples()
        manifest = {
            "dataset_id": self.dataset_id,
            "domain": self.domain,
            "sensor": self._sensor,
            "modality": self.modality,
            "task": self.task,
            "object_classes": self._object_classes,
            "total_samples": len(samples),
            "samples": samples
        }
        manifest_path = self.base_path / "manifest.json"
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save manifest to {manifest_path}: {e}")
        return manifest
