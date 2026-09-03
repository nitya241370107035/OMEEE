"""
Cloud Masking Validation Subsystem

Deliberately searches for, verifies, and processes Sentinel-2 scenes with visible
cloud coverage across configurable categories (clear_control, light_cloud,
medium_cloud, heavy_cloud). Processes scenes through the existing Phase 2 pipeline
and records distinct scene vs AOI vs tile cloud metrics.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from PIL import Image
import yaml

# Ensure repo root on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.input_validator import validate_aoi, ValidatedAOI
from backend.ingestion.stac_search import (
    STACSceneMetadata,
    _parse_stac_item,
    _create_offline_mock_scene,
    EARTH_SEARCH_STAC_URL,
    DEFAULT_COLLECTION
)
from backend.ingestion.canvas import assemble_working_canvas, CanvasData
from backend.ingestion.masking import clean_and_normalize_canvas, CleanedCanvas
from backend.ingestion.tiler import slice_and_filter_tiles, DEFAULT_GROUND_CROP_SIZE
from backend.preview.rgb_preview import (
    render_rgb_composite,
    render_mask_preview,
    render_probability_heatmap,
    render_mask_overlay
)
from backend.preview.geotiff_reader import read_geotiff
from backend.preview.statistics import compute_raster_statistics

logger = logging.getLogger("cloud_validation")

DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "cloud_validation.yaml"
VALIDATION_ROOT = REPO_ROOT / "datasets" / "pipeline_validation" / "cloud_masking_validation"


def load_cloud_validation_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Loads configuration for cloud masking validation."""
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_aoi(aoi_id_or_path: str, config: Optional[Dict[str, Any]] = None) -> Tuple[str, Path, ValidatedAOI]:
    """Resolves AOI geometry from ID or file path."""
    if config is None:
        config = load_cloud_validation_config()

    aoi_defs = config.get("aoi_definitions", {})
    if aoi_id_or_path in aoi_defs:
        aoi_id = aoi_id_or_path
        rel_path = aoi_defs[aoi_id]["geojson_path"]
        geom_path = REPO_ROOT / rel_path
    else:
        geom_path = Path(aoi_id_or_path)
        if not geom_path.is_absolute():
            geom_path = REPO_ROOT / geom_path
        aoi_id = geom_path.stem

    if not geom_path.exists():
        raise FileNotFoundError(f"AOI GeoJSON not found: {geom_path}")

    validated_aoi = validate_aoi(geom_path)
    return aoi_id, geom_path, validated_aoi


def search_cloud_validation_scenes(
    aoi_id: str = "current_aoi",
    category: str = "medium_cloud",
    config_path: Path = DEFAULT_CONFIG_PATH,
    limit: int = 25
) -> List[Dict[str, Any]]:
    """
    Searches STAC catalog for candidate Sentinel-2 scenes covering the AOI with
    cloud cover matching the specified category.
    """
    config = load_cloud_validation_config(config_path)
    categories = config.get("categories", {})
    if category not in categories:
        raise ValueError(f"Unknown category '{category}'. Available: {list(categories.keys())}")

    cat_cfg = categories[category]
    min_cloud = float(cat_cfg.get("minimum_cloud_cover", 30.0))
    max_cloud = float(cat_cfg.get("maximum_cloud_cover", 60.0))
    pref_cloud = float(cat_cfg.get("preferred_cloud_cover", 45.0))

    aoi_key, aoi_path, validated_aoi = resolve_aoi(aoi_id, config)
    bbox = validated_aoi.bbox

    scene_sel = config.get("scene_selection", {})
    stac_endpoint = scene_sel.get("stac_endpoint", EARTH_SEARCH_STAC_URL)
    collection = scene_sel.get("collection", DEFAULT_COLLECTION)
    dt_range = scene_sel.get("datetime_range", "2023-01-01/2023-12-31")

    candidates = []

    try:
        from pystac_client import Client
        from shapely.geometry import box, shape

        logger.info(f"Connecting to STAC endpoint {stac_endpoint} for cloudy scene search ({category}: {min_cloud}% - {max_cloud}%)...")
        client = Client.open(stac_endpoint)
        
        search = client.search(
            collections=[collection],
            bbox=bbox,
            datetime=dt_range,
            query={"eo:cloud_cover": {"gte": min_cloud, "lte": max_cloud}},
            limit=limit
        )

        aoi_box = box(*bbox)
        aoi_area = aoi_box.area

        items = list(search.items())
        logger.info(f"Found {len(items)} STAC items matching cloud cover [{min_cloud}%, {max_cloud}%].")

        for item in items:
            try:
                geom = shape(item.geometry) if item.geometry else box(*item.bbox)
                inter_area = geom.intersection(aoi_box).area
                overlap_pct = (inter_area / aoi_area * 100.0) if aoi_area > 0 else 100.0
            except Exception:
                overlap_pct = 100.0

            if overlap_pct < 90.0:
                continue  # Must cover at least 90% of AOI

            c_cover = float(item.properties.get("eo:cloud_cover", 0.0))
            score = abs(c_cover - pref_cloud)
            scene_meta = _parse_stac_item(item, bbox, source="aws_earth_search")

            candidates.append({
                "scene_id": scene_meta.scene_id,
                "acquisition_date": scene_meta.acquisition_date,
                "sensor": scene_meta.sensor,
                "catalog_cloud_cover": c_cover,
                "preferred_cloud_proximity": round(score, 2),
                "aoi_coverage_overlap_pct": round(overlap_pct, 2),
                "category": category,
                "aoi_id": aoi_key,
                "scene_meta": scene_meta,
                "status": "cloud_masking_candidate"
            })

        # Rank by proximity to preferred cloud cover
        candidates.sort(key=lambda x: (x["preferred_cloud_proximity"], -x["aoi_coverage_overlap_pct"]))

    except Exception as exc:
        logger.warning(f"STAC search encountered error: {exc}. Using offline candidate generation.")
        mock_meta = _create_offline_mock_scene(bbox, dt_range)
        mock_meta.cloud_cover = pref_cloud
        candidates.append({
            "scene_id": f"OFFLINE_CLOUD_SCENE_{category.upper()}",
            "acquisition_date": mock_meta.acquisition_date,
            "sensor": mock_meta.sensor,
            "catalog_cloud_cover": pref_cloud,
            "preferred_cloud_proximity": 0.0,
            "aoi_coverage_overlap_pct": 100.0,
            "category": category,
            "aoi_id": aoi_key,
            "scene_meta": mock_meta,
            "status": "cloud_masking_candidate"
        })

    return candidates


def process_cloud_validation_scene(
    scene_id: Optional[str] = None,
    scene_meta: Optional[STACSceneMetadata] = None,
    aoi_id: str = "current_aoi",
    category: str = "medium_cloud",
    config_path: Path = DEFAULT_CONFIG_PATH
) -> Dict[str, Any]:
    """
    Executes the existing Phase 2 pipeline on a cloudy Sentinel-2 scene and persists
    all visual comparison GeoTIFFs, PNG previews, and manifests.
    """
    config = load_cloud_validation_config(config_path)
    aoi_key, aoi_path, validated_aoi = resolve_aoi(aoi_id, config)

    # If scene_meta not provided, search candidate
    if scene_meta is None:
        candidates = search_cloud_validation_scenes(aoi_id=aoi_key, category=category, config_path=config_path)
        if not candidates:
            raise RuntimeError(f"No candidate scenes found for AOI '{aoi_key}' and category '{category}'")
        if scene_id:
            matched = [c for c in candidates if c["scene_id"] == scene_id]
            if not matched:
                raise RuntimeError(f"Scene '{scene_id}' not found in candidate search results.")
            target_candidate = matched[0]
        else:
            target_candidate = candidates[0]
        scene_meta = target_candidate["scene_meta"]

    target_scene_id = scene_meta.scene_id
    scene_dir = VALIDATION_ROOT / target_scene_id
    raw_dir = scene_dir / "raw"
    inter_dir = scene_dir / "intermediate"
    norm_dir = scene_dir / "normalized"
    tile_dir = scene_dir / "tiles"
    prev_dir = scene_dir / "previews"

    for d in [raw_dir, inter_dir, norm_dir, tile_dir, prev_dir]:
        d.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing cloudy validation scene: {target_scene_id}")

    # 1. Assemble Working Canvas
    canvas: CanvasData = assemble_working_canvas(
        scene_meta=scene_meta,
        aoi_bbox=validated_aoi.bbox,
        buffer_pct=0.05
    )

    # 2. Run s2cloudless, shadow detector, and normalization
    cleaned: CleanedCanvas = clean_and_normalize_canvas(canvas)

    # 3. Generate 512x512 Tiles
    tiles = slice_and_filter_tiles(
        canvas_data=canvas,
        cleaned_canvas=cleaned,
        aoi_polygon=validated_aoi.polygon,
        scene_id=target_scene_id,
        ground_crop_size=DEFAULT_GROUND_CROP_SIZE,
        overlap_pct=0.10
    )

    # Calculate the 3 Distinct Cloud Cover Metrics
    catalog_scene_cloud_cover = float(scene_meta.cloud_cover)
    total_canvas_pixels = cleaned.cloud_mask.size
    aoi_cloud_pixels = int(np.sum(cleaned.cloud_mask))
    aoi_shadow_pixels = int(np.sum(cleaned.shadow_mask))
    aoi_bad_pixels = int(np.sum(cleaned.bad_mask))
    aoi_cloud_pct = float((aoi_cloud_pixels / total_canvas_pixels) * 100.0)
    aoi_shadow_pct = float((aoi_shadow_pixels / total_canvas_pixels) * 100.0)
    aoi_bad_pct = float((aoi_bad_pixels / total_canvas_pixels) * 100.0)

    # 4. Save GeoTIFF Outputs
    import rasterio

    # Save raw_canvas.tif
    raw_path = raw_dir / "raw_canvas.tif"
    with rasterio.open(
        raw_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=3,
        dtype=canvas.data.dtype, crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(canvas.data[[2, 1, 0]])  # B04, B03, B02
        dst.set_band_description(1, "B04 - Red")
        dst.set_band_description(2, "B03 - Green")
        dst.set_band_description(3, "B02 - Blue")

    # Save intermediate GeoTIFFs
    prob_path = inter_dir / "cloud_probability.tif"
    with rasterio.open(
        prob_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=1,
        dtype="float32", crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(cleaned.cloud_prob, 1)

    cmask_path = inter_dir / "cloud_mask.tif"
    with rasterio.open(
        cmask_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=1,
        dtype="uint8", crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(cleaned.cloud_mask.astype(np.uint8), 1)

    smask_path = inter_dir / "shadow_mask.tif"
    with rasterio.open(
        smask_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=1,
        dtype="uint8", crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(cleaned.shadow_mask.astype(np.uint8), 1)

    bmask_path = inter_dir / "bad_mask.tif"
    with rasterio.open(
        bmask_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=1,
        dtype="uint8", crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(cleaned.bad_mask.astype(np.uint8), 1)

    # Save normalized_canvas.tif
    norm_path = norm_dir / "normalized_canvas.tif"
    with rasterio.open(
        norm_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=3,
        dtype="uint8", crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(cleaned.rgb_normalized)
        dst.set_band_description(1, "Normalized Red (B04)")
        dst.set_band_description(2, "Normalized Green (B03)")
        dst.set_band_description(3, "Normalized Blue (B02)")

    # Save final 512x512 tiles
    tile_manifest_entries = []
    for idx, t in enumerate(tiles):
        t_fname = f"tile_{idx:04d}.tif"
        t_path = tile_dir / t_fname
        t_h, t_w = t.rgb_data.shape[1], t.rgb_data.shape[2]
        with rasterio.open(
            t_path, "w", driver="GTiff",
            height=t_h, width=t_w, count=3,
            dtype=t.rgb_data.dtype, crs=canvas.crs, transform=t.transform
        ) as dst:
            dst.write(t.rgb_data)
        tile_manifest_entries.append({
            "tile_id": f"{target_scene_id}_tile_{idx:04d}",
            "file_name": t_fname,
            "dimensions": f"{t_h}x{t_w}",
            "cloud_percentage": round(t.cloud_pct * 100.0, 2),
            "valid_ground_percentage": round(100.0 - (t.cloud_pct * 100.0), 2),
            "bounds": [round(b, 6) for b in t.bounds]
        })

    # 5. Generate PNG Previews
    meta_raw, _ = read_geotiff(raw_path)
    meta_norm, _ = read_geotiff(norm_path)

    raw_rgb_img = render_rgb_composite(canvas.data[[2, 1, 0]], meta_raw)
    raw_rgb_img.save(prev_dir / "raw_rgb_preview.png")

    prob_img = render_probability_heatmap(np.expand_dims(cleaned.cloud_prob, axis=0))
    prob_img.save(prev_dir / "cloud_probability_preview.png")

    cmask_img = render_mask_preview(np.expand_dims(cleaned.cloud_mask, axis=0), color="cyan")
    cmask_img.save(prev_dir / "cloud_mask_preview.png")

    smask_img = render_mask_preview(np.expand_dims(cleaned.shadow_mask, axis=0), color="yellow")
    smask_img.save(prev_dir / "shadow_mask_preview.png")

    bmask_img = render_mask_preview(np.expand_dims(cleaned.bad_mask, axis=0), color="red")
    bmask_img.save(prev_dir / "combined_mask_preview.png")

    norm_rgb_img = render_rgb_composite(cleaned.rgb_normalized, meta_norm)
    norm_rgb_img.save(prev_dir / "normalized_rgb_preview.png")

    overlay_img = render_mask_overlay(raw_rgb_img, cloud_mask=cleaned.cloud_mask, shadow_mask=cleaned.shadow_mask, alpha=0.45)
    overlay_img.save(prev_dir / "mask_overlay_preview.png")

    if tiles:
        tile_0_img = Image.fromarray(np.transpose(tiles[0].rgb_data, (1, 2, 0)), mode="RGB")
        tile_0_img.save(prev_dir / "final_512_tile_preview.png")

    # 6. Save Comprehensive Manifest
    manifest_data = {
        "scene_id": target_scene_id,
        "acquisition_date": scene_meta.acquisition_date,
        "sensor": scene_meta.sensor,
        "aoi_id": aoi_key,
        "category": category,
        "crs": canvas.crs,
        "dimensions": f"{canvas.height}x{canvas.width}",
        "cloud_metrics": {
            "catalog_scene_cloud_cover": round(catalog_scene_cloud_cover, 2),
            "AOI_cloud_pct": round(aoi_cloud_pct, 2),
            "AOI_shadow_pct": round(aoi_shadow_pct, 2),
            "AOI_bad_pixel_pct": round(aoi_bad_pct, 2),
            "tile_cloud_percentages": [t["cloud_percentage"] for t in tile_manifest_entries]
        },
        "pixel_counts": {
            "total_canvas_pixels": total_canvas_pixels,
            "aoi_cloud_pixels": aoi_cloud_pixels,
            "aoi_shadow_pixels": aoi_shadow_pixels,
            "aoi_bad_pixels": aoi_bad_pixels
        },
        "file_structure": {
            "raw": str(raw_path.relative_to(REPO_ROOT)),
            "intermediate": {
                "cloud_probability": str(prob_path.relative_to(REPO_ROOT)),
                "cloud_mask": str(cmask_path.relative_to(REPO_ROOT)),
                "shadow_mask": str(smask_path.relative_to(REPO_ROOT)),
                "bad_mask": str(bmask_path.relative_to(REPO_ROOT))
            },
            "normalized": str(norm_path.relative_to(REPO_ROOT)),
            "tiles_directory": str(tile_dir.relative_to(REPO_ROOT)),
            "previews_directory": str(prev_dir.relative_to(REPO_ROOT))
        },
        "total_tiles": len(tiles),
        "tiles": tile_manifest_entries
    }

    manifest_path = scene_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    logger.info(f"Cloud validation processing complete for '{target_scene_id}'. Manifest saved to {manifest_path}")
    return manifest_data


def list_cloud_validation_scenes() -> List[Dict[str, Any]]:
    """Lists all processed cloud validation scenes and their manifests."""
    if not VALIDATION_ROOT.exists():
        return []

    scenes = []
    for m_path in VALIDATION_ROOT.glob("*/manifest.json"):
        try:
            with open(m_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            scenes.append(data)
        except Exception as e:
            logger.warning(f"Error reading manifest {m_path}: {e}")
    return scenes


def main():
    parser = argparse.ArgumentParser(description="Cloud Masking Validation Subsystem CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: search
    s_parser = subparsers.add_parser("search", help="Search STAC catalog for cloudy Sentinel-2 scenes")
    s_parser.add_argument("--aoi-id", default="current_aoi", help="Target AOI identifier or path")
    s_parser.add_argument("--category", default="medium_cloud", choices=["clear_control", "light_cloud", "medium_cloud", "heavy_cloud"], help="Cloud coverage test category")

    # Command: process
    p_parser = subparsers.add_parser("process", help="Process a cloudy validation scene through Phase 2 pipeline")
    p_parser.add_argument("--scene-id", help="Optional specific scene ID to process (defaults to best candidate)")
    p_parser.add_argument("--aoi-id", default="current_aoi", help="Target AOI identifier")
    p_parser.add_argument("--category", default="medium_cloud", help="Cloud category")

    # Command: inspect
    i_parser = subparsers.add_parser("inspect", help="Inspect a processed cloud validation scene manifest")
    i_parser.add_argument("--scene-id", required=True, help="Scene ID to inspect")

    # Command: list
    subparsers.add_parser("list", help="List all processed cloud validation scenes")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "search":
        candidates = search_cloud_validation_scenes(aoi_id=args.aoi_id, category=args.category)
        print("\n" + "=" * 95)
        print(f"CLOUDY SCENE CANDIDATES FOR AOI '{args.aoi_id}' (Category: {args.category.upper()})")
        print("=" * 95)
        print(f"{'SCENE ID':<36} {'DATE':<12} {'CATALOG CLOUD':<16} {'PROXIMITY':<12} {'AOI OVERLAP'}")
        print("-" * 95)
        for c in candidates:
            print(f"{c['scene_id']:<36} {c['acquisition_date'][:10]:<12} {c['catalog_cloud_cover']:>6.2f}%         {c['preferred_cloud_proximity']:>6.2f}%       {c['aoi_coverage_overlap_pct']:>6.2f}%")
        print("=" * 95 + "\n")

    elif args.command == "process":
        res = process_cloud_validation_scene(scene_id=args.scene_id, aoi_id=args.aoi_id, category=args.category)
        print("\n" + "=" * 80)
        print(f"CLOUD VALIDATION PROCESSING COMPLETE: {res['scene_id']}")
        print("=" * 80)
        print(f"  Acquisition Date       : {res['acquisition_date']}")
        print(f"  Catalog Scene Cloud    : {res['cloud_metrics']['catalog_scene_cloud_cover']:.2f}%")
        print(f"  AOI Cloud (s2cloudless): {res['cloud_metrics']['AOI_cloud_pct']:.2f}%")
        print(f"  AOI Shadow Coverage    : {res['cloud_metrics']['AOI_shadow_pct']:.2f}%")
        print(f"  AOI Combined Bad Mask  : {res['cloud_metrics']['AOI_bad_pixel_pct']:.2f}%")
        print(f"  Total 512x512 Tiles    : {res['total_tiles']}")
        print(f"  Output Directory       : datasets/pipeline_validation/cloud_masking_validation/{res['scene_id']}")
        print("=" * 80 + "\n")

    elif args.command == "inspect":
        target_path = VALIDATION_ROOT / args.scene_id / "manifest.json"
        if not target_path.exists():
            print(f"Error: Manifest not found for scene '{args.scene_id}' in {target_path}", file=sys.stderr)
            sys.exit(1)
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print("\n" + "=" * 80)
        print(f"CLOUD VALIDATION SCENE INSPECTION: {data['scene_id']}")
        print("=" * 80)
        print(json.dumps(data, indent=2))
        print("=" * 80 + "\n")

    elif args.command == "list":
        scenes = list_cloud_validation_scenes()
        print("\n" + "=" * 95)
        print("PROCESSED CLOUD MASKING VALIDATION SCENES")
        print("=" * 95)
        if not scenes:
            print("  No cloud validation scenes processed yet.")
        else:
            print(f"{'SCENE ID':<36} {'DATE':<12} {'CATALOG CLOUD':<15} {'AOI CLOUD (s2cloudless)':<24} {'TILES'}")
            print("-" * 95)
            for sc in scenes:
                cat_c = sc.get("cloud_metrics", {}).get("catalog_scene_cloud_cover", 0.0)
                aoi_c = sc.get("cloud_metrics", {}).get("AOI_cloud_pct", 0.0)
                print(f"{sc['scene_id']:<36} {sc['acquisition_date'][:10]:<12} {cat_c:>6.2f}%         {aoi_c:>6.2f}%                  {sc['total_tiles']}")
        print("=" * 95 + "\n")


if __name__ == "__main__":
    main()
