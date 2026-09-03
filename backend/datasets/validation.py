"""
Dataset Registry & Multi-Domain Validation Subsystem

Validates dataset architecture, schema compliance, domain separation, manifest integrity,
and raster reference health. Returns structured PASS / WARNING / FAIL reports.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.datasets.dataset_base import DatasetValidationResult, ValidationStatus

logger = logging.getLogger(__name__)

VALID_DOMAINS = {"pipeline_validation", "eastern_ladakh", "benchmark_change_detection", "maritime"}
VALID_MODALITIES = {"multispectral", "optical", "sar", "multimodal"}
VALID_TASKS = {
    "preprocessing_validation",
    "temporal_change_detection",
    "semantic_change_detection",
    "ship_detection",
    "semantic_retrieval"
}

# Domain-specific task constraints to enforce domain isolation
DOMAIN_TASK_RULES = {
    "pipeline_validation": {"preprocessing_validation", "temporal_change_detection"},
    "eastern_ladakh": {"temporal_change_detection", "semantic_change_detection"},
    "benchmark_change_detection": {"temporal_change_detection", "semantic_change_detection"},
    "maritime": {"ship_detection", "semantic_retrieval"}
}


def validate_registry_schema(registry_dict: Dict[str, Any]) -> List[str]:
    """Validates top-level registry dictionary structure."""
    errors = []
    if "datasets" not in registry_dict or not isinstance(registry_dict["datasets"], list):
        errors.append("Registry must contain a top-level 'datasets' list.")
        return errors

    required_fields = [
        "dataset_id", "display_name", "domain", "sensor", "modality",
        "task", "geographic_scope", "time_range", "spatial_resolution",
        "source", "license", "local_path", "status", "notes"
    ]

    seen_ids = set()
    for idx, d in enumerate(registry_dict["datasets"]):
        if not isinstance(d, dict):
            errors.append(f"Item #{idx} is not a valid JSON object.")
            continue

        for rf in required_fields:
            if rf not in d or not str(d[rf]).strip():
                errors.append(f"Dataset #{idx} missing required field: '{rf}'")

        d_id = d.get("dataset_id")
        if d_id:
            if d_id in seen_ids:
                errors.append(f"Duplicate dataset_id found: '{d_id}'")
            seen_ids.add(d_id)

        # Domain validity
        domain = d.get("domain")
        if domain and domain not in VALID_DOMAINS:
            errors.append(f"Dataset '{d_id}' has invalid domain '{domain}'. Must be one of {VALID_DOMAINS}")

        # Modality validity
        modality = d.get("modality")
        if modality and modality not in VALID_MODALITIES:
            errors.append(f"Dataset '{d_id}' has invalid modality '{modality}'. Must be one of {VALID_MODALITIES}")

        # Task validity
        task = d.get("task")
        if task and task not in VALID_TASKS:
            errors.append(f"Dataset '{d_id}' has invalid task '{task}'. Must be one of {VALID_TASKS}")

        # Domain Isolation constraint check
        if domain in DOMAIN_TASK_RULES and task:
            allowed_tasks = DOMAIN_TASK_RULES[domain]
            if task not in allowed_tasks:
                errors.append(
                    f"Domain isolation error in '{d_id}': Domain '{domain}' cannot have task '{task}'. "
                    f"Allowed tasks for {domain}: {allowed_tasks}"
                )

    return errors


def validate_dataset_entry(
    dataset_entry: Dict[str, Any],
    repo_root: Path
) -> DatasetValidationResult:
    """
    Validates an individual dataset entry and its local storage structure.
    """
    d_id = dataset_entry.get("dataset_id", "unknown")
    errors: List[str] = []
    warnings: List[str] = []
    info: Dict[str, Any] = {
        "dataset_id": d_id,
        "domain": dataset_entry.get("domain"),
        "modality": dataset_entry.get("modality"),
        "task": dataset_entry.get("task"),
        "status": dataset_entry.get("status")
    }

    local_rel_path = dataset_entry.get("local_path", "")
    full_path = repo_root / local_rel_path

    if not full_path.exists():
        if dataset_entry.get("status") in ["active", "configured"]:
            warnings.append(f"Local storage path does not exist yet: {full_path}")
        else:
            info["storage_status"] = "placeholder_path_pending"
    else:
        info["storage_path"] = str(full_path)
        # Check for manifests
        manifests = list(full_path.glob("**/manifest*.json"))
        info["manifest_count"] = len(manifests)
        if not manifests and dataset_entry.get("status") == "active":
            warnings.append(f"Active dataset '{d_id}' has no manifest.json files in {full_path}")

    status = ValidationStatus.FAIL if errors else (ValidationStatus.WARNING if warnings else ValidationStatus.PASS)
    return DatasetValidationResult(
        dataset_id=d_id,
        status=status,
        errors=errors,
        warnings=warnings,
        info=info
    )


def validate_all_datasets(
    registry_path: Path,
    repo_root: Path
) -> Tuple[ValidationStatus, List[DatasetValidationResult], List[str]]:
    """
    Runs full project dataset registry validation.
    
    Returns:
        overall_status: PASS / WARNING / FAIL
        results: List of per-dataset validation results
        schema_errors: List of top-level schema validation errors
    """
    if not registry_path.exists():
        return ValidationStatus.FAIL, [], [f"Registry file not found: {registry_path}"]

    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            reg_data = json.load(f)
    except Exception as e:
        return ValidationStatus.FAIL, [], [f"Failed to parse registry JSON: {e}"]

    schema_errors = validate_registry_schema(reg_data)
    if schema_errors:
        return ValidationStatus.FAIL, [], schema_errors

    results: List[DatasetValidationResult] = []
    has_warning = False

    for entry in reg_data.get("datasets", []):
        res = validate_dataset_entry(entry, repo_root)
        results.append(res)
        if res.status == ValidationStatus.FAIL:
            return ValidationStatus.FAIL, results, []
        if res.status == ValidationStatus.WARNING:
            has_warning = True

    overall = ValidationStatus.WARNING if has_warning else ValidationStatus.PASS
    return overall, results, []
