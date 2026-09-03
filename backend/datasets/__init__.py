"""
Multi-Domain Dataset Architecture & Registry Package.
"""

from .dataset_base import BaseDatasetAdapter, DatasetValidationResult, ValidationStatus
from .sentinel2_adapter import Sentinel2DatasetAdapter
from .sentinel1_adapter import Sentinel1DatasetAdapter
from .change_detection_adapter import BenchmarkChangeDetectionAdapter
from .maritime_adapter import MaritimeDatasetAdapter
from .registry import DatasetRegistry
from .validation import validate_all_datasets, validate_dataset_entry

__all__ = [
    "BaseDatasetAdapter",
    "DatasetValidationResult",
    "ValidationStatus",
    "Sentinel2DatasetAdapter",
    "Sentinel1DatasetAdapter",
    "BenchmarkChangeDetectionAdapter",
    "MaritimeDatasetAdapter",
    "DatasetRegistry",
    "validate_all_datasets",
    "validate_dataset_entry",
]
