"""
Unit & Integration Tests for Dataset Architecture & Registry
"""

import json
from pathlib import Path
import pytest
import yaml

from backend.datasets.dataset_base import ValidationStatus
from backend.datasets.registry import DatasetRegistry
from backend.datasets.sentinel2_adapter import Sentinel2DatasetAdapter
from backend.datasets.sentinel1_adapter import Sentinel1DatasetAdapter
from backend.datasets.change_detection_adapter import BenchmarkChangeDetectionAdapter
from backend.datasets.maritime_adapter import MaritimeDatasetAdapter
from backend.datasets.validation import validate_registry_schema, validate_all_datasets


def test_registry_schema_and_loading():
    """Verifies that dataset_registry.json is valid against schema rules."""
    registry = DatasetRegistry()
    assert len(registry.datasets) >= 11

    schema_errors = validate_registry_schema({"datasets": registry.datasets})
    assert len(schema_errors) == 0, f"Schema validation errors: {schema_errors}"


def test_domain_isolation_rules():
    """Ensures domain-task constraints are enforced and invalid mixing is blocked."""
    # Maritime domain with temporal change task should fail validation
    invalid_registry = {
        "datasets": [
            {
                "dataset_id": "invalid_dataset_test",
                "display_name": "Invalid Domain Test",
                "domain": "maritime",
                "sensor": "Sentinel-1",
                "modality": "sar",
                "task": "temporal_change_detection",  # Disallowed for maritime
                "geographic_scope": "Global",
                "time_range": "2023",
                "spatial_resolution": "10m",
                "source": "Test",
                "license": "Test",
                "local_path": "datasets/maritime",
                "status": "configured",
                "notes": "Testing invalid domain mixing"
            }
        ]
    }
    errors = validate_registry_schema(invalid_registry)
    assert len(errors) > 0
    assert any("Domain isolation error" in e for e in errors)


def test_dataset_adapters_instantiation():
    """Verifies that all domain adapters instantiate correctly with distinct properties."""
    registry = DatasetRegistry()

    # Sentinel-2 Adapter
    s2_adapter = registry.get_adapter("pipeline_validation_current_aoi")
    assert isinstance(s2_adapter, Sentinel2DatasetAdapter)
    assert s2_adapter.domain == "pipeline_validation"
    assert s2_adapter.modality == "multispectral"

    # Sentinel-1 Adapter
    s1_adapter = registry.get_adapter("eastern_ladakh_s1")
    assert isinstance(s1_adapter, Sentinel1DatasetAdapter)
    assert s1_adapter.domain == "eastern_ladakh"
    assert s1_adapter.modality == "sar"

    # Benchmark Change Detection Adapter
    bm_adapter = registry.get_adapter("benchmark_levir_cd")
    assert isinstance(bm_adapter, BenchmarkChangeDetectionAdapter)
    assert bm_adapter.domain == "benchmark_change_detection"

    # Maritime Adapter
    mar_adapter = registry.get_adapter("maritime_xview3")
    assert isinstance(mar_adapter, MaritimeDatasetAdapter)
    assert mar_adapter.domain == "maritime"


def test_eastern_ladakh_aoi_registry_and_geometries():
    """Verifies that all Eastern Ladakh AOI configurations and GeoJSON files exist and are valid."""
    reg_path = Path("config/eastern_ladakh_aois/registry.json")
    assert reg_path.exists()

    with open(reg_path, "r", encoding="utf-8") as f:
        aoi_reg = json.load(f)

    assert "aois" in aoi_reg
    assert len(aoi_reg["aois"]) >= 4

    for aoi in aoi_reg["aois"]:
        assert "aoi_id" in aoi
        assert "geometry_file" in aoi
        geom_path = Path(aoi["geometry_file"])
        assert geom_path.exists(), f"AOI geometry file missing: {geom_path}"
        with open(geom_path, "r", encoding="utf-8") as gf:
            geom_data = json.load(gf)
        assert geom_data.get("type") in ["Feature", "FeatureCollection"]


def test_dataset_collection_yaml_config():
    """Verifies dataset_collection.yaml structure."""
    cfg_path = Path("config/dataset_collection.yaml")
    assert cfg_path.exists()

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    assert "temporal_scope" in cfg
    assert 2017 in cfg["temporal_scope"]["target_years"]
    assert 2026 in cfg["temporal_scope"]["target_years"]
    assert "stac_search_policy" in cfg
    assert cfg["stac_search_policy"]["auto_download"] is False


def test_full_dataset_validation():
    """Runs full dataset validation check."""
    registry = DatasetRegistry()
    status, results, schema_errors = registry.validate()
    assert len(schema_errors) == 0
    assert status in [ValidationStatus.PASS, ValidationStatus.WARNING]
    assert len(results) >= 11
