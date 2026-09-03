"""
Dataset Registry Manager & CLI Interface

Provides programmatic and CLI access to registered multi-domain datasets,
dataset inspection, adapter instantiation, and comprehensive validation.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repo root on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.datasets.dataset_base import BaseDatasetAdapter, DatasetValidationResult, ValidationStatus
from backend.datasets.sentinel2_adapter import Sentinel2DatasetAdapter
from backend.datasets.sentinel1_adapter import Sentinel1DatasetAdapter
from backend.datasets.change_detection_adapter import BenchmarkChangeDetectionAdapter
from backend.datasets.maritime_adapter import MaritimeDatasetAdapter
from backend.datasets.validation import validate_all_datasets, validate_dataset_entry

DEFAULT_REGISTRY_PATH = REPO_ROOT / "datasets" / "registry" / "dataset_registry.json"

logger = logging.getLogger("dataset_registry")


class DatasetRegistry:
    """
    Manages dataset catalog, validation, and adapter instantiation.
    """

    def __init__(self, registry_path: Path = DEFAULT_REGISTRY_PATH, repo_root: Path = REPO_ROOT):
        self.registry_path = Path(registry_path)
        self.repo_root = Path(repo_root)
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.registry_path.exists():
            raise FileNotFoundError(f"Dataset registry file not found: {self.registry_path}")
        with open(self.registry_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    @property
    def datasets(self) -> List[Dict[str, Any]]:
        return self._data.get("datasets", [])

    def get_dataset_entry(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        for d in self.datasets:
            if d.get("dataset_id") == dataset_id:
                return d
        return None

    def list_datasets(
        self,
        domain: Optional[str] = None,
        modality: Optional[str] = None,
        task: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Filters datasets by domain, modality, and task."""
        filtered = self.datasets
        if domain:
            filtered = [d for d in filtered if d.get("domain") == domain]
        if modality:
            filtered = [d for d in filtered if d.get("modality") == modality]
        if task:
            filtered = [d for d in filtered if d.get("task") == task]
        return filtered

    def get_adapter(self, dataset_id: str) -> BaseDatasetAdapter:
        """Instantiates the appropriate domain adapter for the dataset."""
        entry = self.get_dataset_entry(dataset_id)
        if not entry:
            raise KeyError(f"Dataset '{dataset_id}' not found in registry.")

        domain = entry.get("domain")
        local_path = self.repo_root / entry.get("local_path", "")
        modality = entry.get("modality")

        if domain in ["pipeline_validation", "eastern_ladakh"] and modality in ["multispectral", "optical"]:
            return Sentinel2DatasetAdapter(dataset_id=dataset_id, base_path=local_path, domain=domain)
        elif domain == "eastern_ladakh" and modality == "sar":
            return Sentinel1DatasetAdapter(dataset_id=dataset_id, base_path=local_path, domain=domain)
        elif domain == "benchmark_change_detection":
            return BenchmarkChangeDetectionAdapter(
                dataset_id=dataset_id,
                base_path=local_path,
                task=entry.get("task", "temporal_change_detection"),
                source_resolution=entry.get("spatial_resolution", "0.5m GSD")
            )
        elif domain == "maritime":
            return MaritimeDatasetAdapter(
                dataset_id=dataset_id,
                base_path=local_path,
                sensor=entry.get("sensor", "Sentinel-1 C-SAR")
            )
        else:
            raise ValueError(f"No specific adapter implemented for domain '{domain}', modality '{modality}'")

    def validate(self, dataset_id: Optional[str] = None) -> Tuple[ValidationStatus, List[DatasetValidationResult], List[str]]:
        """Validates all datasets or a specific dataset."""
        if dataset_id:
            entry = self.get_dataset_entry(dataset_id)
            if not entry:
                return ValidationStatus.FAIL, [], [f"Dataset '{dataset_id}' not found in registry."]
            res = validate_dataset_entry(entry, self.repo_root)
            return res.status, [res], []
        return validate_all_datasets(self.registry_path, self.repo_root)


def main():
    parser = argparse.ArgumentParser(description="Multi-Domain Dataset Registry CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: list
    list_parser = subparsers.add_parser("list", help="List registered datasets")
    list_parser.add_argument("--domain", help="Filter by domain")
    list_parser.add_argument("--modality", help="Filter by modality")
    list_parser.add_argument("--task", help="Filter by task")

    # Command: validate
    val_parser = subparsers.add_parser("validate", help="Validate dataset registry and storage paths")
    val_parser.add_argument("--dataset-id", help="Validate a specific dataset ID")

    # Command: inspect
    insp_parser = subparsers.add_parser("inspect", help="Inspect detailed dataset metadata and adapter")
    insp_parser.add_argument("--dataset-id", required=True, help="Dataset ID to inspect")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    registry = DatasetRegistry()

    if args.command == "list":
        items = registry.list_datasets(domain=args.domain, modality=args.modality, task=args.task)
        print(f"\n{'DATASET ID':<32} {'DOMAIN':<28} {'MODALITY':<14} {'STATUS':<12} {'TASK'}")
        print("=" * 115)
        for item in items:
            print(f"{item['dataset_id']:<32} {item['domain']:<28} {item['modality']:<14} {item['status']:<12} {item['task']}")
        print(f"\nTotal Registered Datasets: {len(items)}\n")

    elif args.command == "validate":
        status, results, schema_errors = registry.validate(dataset_id=args.dataset_id)
        print("\n" + "=" * 80)
        print(f"DATASET REGISTRY VALIDATION RESULT: {status.value}")
        print("=" * 80)
        if schema_errors:
            print("\nSchema Errors:")
            for err in schema_errors:
                print(f"  [ERROR] {err}")

        for res in results:
            prefix = "[PASS]" if res.status == ValidationStatus.PASS else ("[WARN]" if res.status == ValidationStatus.WARNING else "[FAIL]")
            print(f"{prefix} {res.dataset_id:<32} (Domain: {res.info.get('domain')}, Task: {res.info.get('task')})")
            for e in res.errors:
                print(f"    - Error: {e}")
            for w in res.warnings:
                print(f"    - Warning: {w}")

        print("=" * 80 + "\n")
        if status == ValidationStatus.FAIL:
            sys.exit(1)

    elif args.command == "inspect":
        entry = registry.get_dataset_entry(args.dataset_id)
        if not entry:
            print(f"Error: Dataset '{args.dataset_id}' not found.")
            sys.exit(1)

        print(f"\nDATASET INSPECTION: {entry['dataset_id']}")
        print("=" * 80)
        for k, v in entry.items():
            print(f"  {k:<22}: {v}")

        try:
            adapter = registry.get_adapter(args.dataset_id)
            print("\nAdapter Details:")
            print(f"  Class                 : {adapter.__class__.__name__}")
            print(f"  Samples Registered    : {len(adapter.list_samples())}")
        except Exception as exc:
            print(f"\nAdapter Note: {exc}")
        print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
