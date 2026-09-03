"""
Cloud Removal Manifest Generator (Phase 5.12)

Generates and persists the structured JSON manifest documenting all inputs, outputs,
algorithmic parameters, provenance percentages, and scientific validation metrics.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional


def create_cloud_removal_manifest(
    scene_id: str,
    dataset_id: str,
    input_paths: Dict[str, str],
    product_paths: Dict[str, str],
    method_name: str,
    method_version: str,
    provenance_stats: Dict[str, Any],
    region_stats: Dict[str, Any],
    validation_metrics: Dict[str, Any],
    tile_validation: Dict[str, Any],
    output_manifest_path: Path
) -> Dict[str, Any]:
    """
    Constructs and writes the complete cloud removal manifest.
    """
    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_data = {
        "manifest_schema_version": "1.0.0",
        "phase": "PHASE_5_CLOUD_REMOVAL",
        "scene_id": scene_id,
        "dataset_id": dataset_id,
        "processing_timestamp": datetime.now(timezone.utc).isoformat(),
        
        "methodology": {
            "cloud_removal_method": method_name,
            "method_version": method_version,
            "research_reference": "DOI: 10.3390/rs15040904 (MDPI Remote Sensing)",
            "implementation_type": "cloud-matting-inspired physical surface recovery",
            "official_repository_used": None,
            "model_weights_used": None,
            "source_provenance": "Observed Sentinel-2 L2A optical bands with s2cloudless probability inputs"
        },
        
        "raster_paths": {
            "input_rgb_path": input_paths.get("rgb_canvas"),
            "cloud_probability_path": input_paths.get("cloud_probability"),
            "cloud_mask_path": input_paths.get("cloud_mask"),
            "shadow_mask_path": input_paths.get("shadow_mask"),
            "cloud_region_map_path": str(product_paths.get("cloud_region_map", "")),
            "cloud_trimap_path": str(product_paths.get("cloud_trimap", "")),
            "cloud_opacity_path": str(product_paths.get("cloud_opacity", "")),
            "declouded_output_path": str(product_paths.get("declouded_output", "")),
            "declouded_observed_path": str(product_paths.get("declouded_observed", "")),
            "reconstruction_mask_path": str(product_paths.get("reconstruction_mask", ""))
        },
        
        "cloud_statistics": {
            "clear_percentage": region_stats.get("clear_percentage", 0.0),
            "thin_cloud_pixel_percentage": region_stats.get("thin_cloud_percentage", 0.0),
            "uncertain_cloud_pixel_percentage": region_stats.get("uncertain_cloud_percentage", 0.0),
            "thick_cloud_pixel_percentage": region_stats.get("thick_cloud_percentage", 0.0),
            "nodata_percentage": region_stats.get("nodata_percentage", 0.0)
        },
        
        "provenance_breakdown": {
            "observed_pixel_percentage": provenance_stats.get("observed_pixel_percentage", 0.0),
            "thin_cloud_corrected_percentage": provenance_stats.get("thin_cloud_corrected_percentage", 0.0),
            "thick_cloud_reconstructed_percentage": provenance_stats.get("thick_cloud_reconstructed_percentage", 0.0),
            "unresolved_pixel_percentage": provenance_stats.get("unresolved_pixel_percentage", 0.0),
            "nodata_percentage": provenance_stats.get("nodata_percentage", 0.0)
        },
        
        "scientific_validation": {
            "clear_pixel_mae": validation_metrics.get("clear_pixel_mae", 0.0),
            "clear_pixel_rmse": validation_metrics.get("clear_pixel_rmse", 0.0),
            "max_absolute_difference": validation_metrics.get("max_absolute_difference", 0.0),
            "modified_clear_pixels_percentage": validation_metrics.get("modified_clear_pixels_percentage", 0.0),
            "clear_pixel_preservation_status": validation_metrics.get("preservation_status", "UNKNOWN"),
            "raster_integrity_status": validation_metrics.get("raster_integrity_status", "UNKNOWN"),
            "total_512_tiles": tile_validation.get("total_tiles", 0),
            "valid_512_tiles": tile_validation.get("valid_512_count", 0),
            "tile_dimension_status": tile_validation.get("status", "UNKNOWN"),
            "validation_status": "PASS" if (validation_metrics.get("preservation_status") == "PASS" and tile_validation.get("status") == "PASS") else "FAIL",
            "validation_errors": validation_metrics.get("errors", [])
        }
    }

    with open(output_manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return manifest_data
