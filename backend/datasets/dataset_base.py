"""
Base Dataset Adapter Interface

Defines the abstract base class and common data models for all domain-specific
dataset adapters (Sentinel-2 Optical, Sentinel-1 SAR, Benchmark Change Detection, Maritime).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class ValidationStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


@dataclass
class DatasetValidationResult:
    """Structured validation response for a dataset."""
    dataset_id: str
    status: ValidationStatus
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.status != ValidationStatus.FAIL


class BaseDatasetAdapter(ABC):
    """
    Abstract Base Class for all project dataset adapters.
    
    Guarantees:
      1. Zero-mutation of raw source data
      2. Strict domain isolation (prevents cross-domain contamination)
      3. Standardized manifest schemas with source provenance
      4. Uniform validation and metadata inspection interface
    """

    def __init__(self, dataset_id: str, base_path: Path, config: Optional[Dict[str, Any]] = None):
        self.dataset_id = dataset_id
        self.base_path = Path(base_path)
        self.config = config or {}

    @property
    @abstractmethod
    def domain(self) -> str:
        """Domain name (e.g. 'pipeline_validation', 'eastern_ladakh', 'benchmark_change_detection', 'maritime')."""
        pass

    @property
    @abstractmethod
    def modality(self) -> str:
        """Modality (e.g. 'multispectral', 'optical', 'sar', 'multimodal')."""
        pass

    @property
    @abstractmethod
    def task(self) -> str:
        """Task (e.g. 'preprocessing_validation', 'temporal_change_detection', 'semantic_change_detection', 'ship_detection')."""
        pass

    @abstractmethod
    def validate(self) -> DatasetValidationResult:
        """
        Validates directory structure, manifests, files, and domain integrity.
        
        Returns:
            DatasetValidationResult: Structured validation outcome (PASS / WARNING / FAIL).
        """
        pass

    @abstractmethod
    def get_metadata(self) -> Dict[str, Any]:
        """Returns structured metadata dictionary for the dataset."""
        pass

    @abstractmethod
    def list_samples(self) -> List[Dict[str, Any]]:
        """Lists sample records indexed in the dataset manifests."""
        pass

    @abstractmethod
    def generate_manifest(self) -> Dict[str, Any]:
        """Compiles or validates the standardized dataset manifest."""
        pass

    def get_status(self) -> str:
        """Returns the operational status of the dataset adapter."""
        val = self.validate()
        return val.status.value
