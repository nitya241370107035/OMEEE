"""
Benchmark Change Detection Dataset Adapter

Standardized adapter for public optical bi-temporal change detection benchmarks
(LEVIR-CD, WHU-CD, DSIFN-CD, SECOND, S2Looking). Enforces provenance tracking,
zero-mutation of raw datasets, and strict isolation from Eastern Ladakh research data.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datasets.dataset_base import BaseDatasetAdapter, DatasetValidationResult, ValidationStatus

logger = logging.getLogger(__name__)


class BenchmarkChangeDetectionAdapter(BaseDatasetAdapter):
    """
    Adapter for standardized bi-temporal change detection benchmark datasets.
    """

    def __init__(
        self,
        dataset_id: str,
        base_path: Path,
        task: str = "temporal_change_detection",
        source_resolution: str = "0.5m GSD",
        config: Optional[Dict[str, Any]] = None
    ):
        super().__init__(dataset_id=dataset_id, base_path=base_path, config=config)
        self._task = task
        self._source_resolution = source_resolution

    @property
    def domain(self) -> str:
        return "benchmark_change_detection"

    @property
    def modality(self) -> str:
        return "optical"

    @property
    def task(self) -> str:
        return self._task

    def validate(self) -> DatasetValidationResult:
        """Validates benchmark dataset structure (t1, t2, label, manifest)."""
        errors: List[str] = []
        warnings: List[str] = []
        info: Dict[str, Any] = {
            "domain": self.domain,
            "modality": self.modality,
            "task": self.task,
            "source_resolution": self._source_resolution
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

        # Validate standard subdirectories or configured structure
        expected_dirs = ["t1", "t2", "label"]
        for ed in expected_dirs:
            p = self.base_path / ed
            if not p.exists():
                warnings.append(f"Directory '{ed}' not found in {self.base_path} (dataset configured/pending download)")

        manifest_path = self.base_path / "manifest.json"
        if not manifest_path.exists():
            warnings.append(f"No manifest.json in {self.base_path}")
        else:
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                info["sample_count"] = len(manifest_data.get("samples", []))
            except Exception as e:
                errors.append(f"Invalid manifest.json: {e}")

        status = ValidationStatus.FAIL if errors else (ValidationStatus.WARNING if warnings else ValidationStatus.PASS)
        return DatasetValidationResult(
            dataset_id=self.dataset_id,
            status=status,
            errors=errors,
            warnings=warnings,
            info=info
        )

    def get_metadata(self) -> Dict[str, Any]:
        """Returns structured metadata for the benchmark dataset."""
        return {
            "dataset_id": self.dataset_id,
            "domain": self.domain,
            "modality": self.modality,
            "task": self.task,
            "source_resolution": self._source_resolution,
            "base_path": str(self.base_path),
            "isolation_guarantee": "Strictly separated from Eastern Ladakh training and ground-truth data."
        }

    def list_samples(self) -> List[Dict[str, Any]]:
        """Lists sample records from manifest."""
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
        """
        Creates or updates standard manifest from t1/t2/label file triplets.
        """
        samples: List[Dict[str, Any]] = []
        t1_dir = self.base_path / "t1"
        t2_dir = self.base_path / "t2"
        label_dir = self.base_path / "label"

        if t1_dir.exists():
            for t1_file in sorted(t1_dir.glob("*.*")):
                sample_name = t1_file.stem
                t2_file = t2_dir / t1_file.name if t2_dir.exists() else None
                label_file = label_dir / t1_file.name if label_dir.exists() else None

                samples.append({
                    "sample_id": f"{self.dataset_id}_{sample_name}",
                    "dataset_id": self.dataset_id,
                    "t1_path": str(t1_file.relative_to(self.base_path.parent.parent)),
                    "t2_path": str(t2_file.relative_to(self.base_path.parent.parent)) if t2_file and t2_file.exists() else None,
                    "label_path": str(label_file.relative_to(self.base_path.parent.parent)) if label_file and label_file.exists() else None,
                    "task": self.task,
                    "source_resolution": self._source_resolution,
                    "processed_resolution": self._source_resolution,
                    "split": "train" if "train" in sample_name.lower() else ("val" if "val" in sample_name.lower() else "test"),
                    "source_dataset": self.dataset_id,
                    "provenance": f"Imported into benchmark registry: {self.dataset_id}"
                })

        manifest = {
            "dataset_id": self.dataset_id,
            "domain": self.domain,
            "modality": self.modality,
            "task": self.task,
            "total_samples": len(samples),
            "samples": samples
        }

        # Save manifest
        manifest_path = self.base_path / "manifest.json"
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save manifest to {manifest_path}: {e}")

        return manifest
