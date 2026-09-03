"""
Defense Preprocessing & Multi-Temporal Analysis Runner

Executes cloud/shadow masking, mask-aware normalization, 512x512 tiling,
and multi-temporal change detection across key defense priority sectors:
1. Eastern Ladakh Border Corridor (LAC): Pangong Tso, Galwan, Depsang (2017 vs 2020 vs 2023)
2. Maritime Domain: Naval dockyards, anchorages, and marine vessel monitoring
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
import rasterio

# Ensure repo root on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.input_validator import validate_aoi, ValidatedAOI
from backend.ingestion.stac_search import search_stac_scene, STACSceneMetadata
from backend.ingestion.canvas import assemble_working_canvas, CanvasData, DEFAULT_PIXEL_RES_DEG
from backend.ingestion.masking import clean_and_normalize_canvas, CleanedCanvas
from backend.ingestion.tiler import slice_and_filter_tiles, DEFAULT_GROUND_CROP_SIZE
from backend.preview.rgb_preview import (
    render_rgb_composite,
    render_mask_preview,
    render_probability_heatmap,
    render_mask_overlay
)
from backend.preview.geotiff_reader import read_geotiff

logger = logging.getLogger("defense_runner")

EASTERN_LADAKH_REGISTRY = REPO_ROOT / "config" / "eastern_ladakh_aois" / "registry.json"
MARITIME_REGISTRY = REPO_ROOT / "config" / "maritime_aois" / "registry.json"


def load_registry(registry_path: Path) -> Dict[str, Any]:
    """Loads AOI definitions from registry JSON."""
    if not registry_path.exists():
        raise FileNotFoundError(f"Registry file not found: {registry_path}")
    with open(registry_path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_multitemporal_difference(
    canvas_a_path: Path,
    canvas_b_path: Path,
    output_tif: Path,
    output_png: Path,
    threshold: float = 35.0
) -> Dict[str, Any]:
    """
    Computes absolute multi-temporal difference between two co-registered normalized canvases
    and saves both GeoTIFF difference layer and high-contrast change heatmap PNG.
    """
    meta_a, data_a = read_geotiff(canvas_a_path)
    meta_b, data_b = read_geotiff(canvas_b_path)

    # Align shapes if minor edge discrepancy exists
    h = min(data_a.shape[1], data_b.shape[1])
    w = min(data_a.shape[2], data_b.shape[2])

    a_crop = data_a[:, :h, :w].astype(np.float32)
    b_crop = data_b[:, :h, :w].astype(np.float32)

    # Multiband Euclidean spectral difference
    diff = np.sqrt(np.sum((b_crop - a_crop) ** 2, axis=0))  # Shape (H, W)
    diff_uint8 = np.clip(diff, 0, 255).astype(np.uint8)

    # Significant change mask (exceeding threshold)
    change_mask = diff > threshold
    change_pct = float(np.sum(change_mask) / change_mask.size * 100.0)

    # Save difference GeoTIFF
    output_tif.parent.mkdir(parents=True, exist_ok=True)
    out_crs = meta_a.crs if meta_a.crs else "EPSG:4326"
    with rasterio.open(
        output_tif, "w", driver="GTiff",
        height=h, width=w, count=1,
        dtype="uint8", crs=out_crs, transform=meta_a.transform
    ) as dst:
        dst.write(diff_uint8, 1)

    # Save Change Heatmap PNG (Red overlay on grayscale baseline)
    gray_bg = (np.mean(a_crop, axis=0) * 0.5).astype(np.uint8)
    rgb_vis = np.stack([gray_bg, gray_bg, gray_bg], axis=-1)
    # Bright Red highlight on detected change
    rgb_vis[change_mask] = [255, 50, 50]
    img = Image.fromarray(rgb_vis, mode="RGB")
    img.save(output_png)

    try:
        rel_tif = str(output_tif.relative_to(REPO_ROOT))
    except ValueError:
        rel_tif = str(output_tif)

    try:
        rel_png = str(output_png.relative_to(REPO_ROOT))
    except ValueError:
        rel_png = str(output_png)

    return {
        "change_pixels": int(np.sum(change_mask)),
        "total_pixels": change_mask.size,
        "change_percentage": round(change_pct, 2),
        "difference_tif": rel_tif,
        "heatmap_png": rel_png
    }


def process_defense_scene_epoch(
    aoi: ValidatedAOI,
    aoi_id: str,
    epoch_year: int,
    output_base_dir: Path,
    seasonal_window: str = "07-01/10-31",
    max_cloud_cover: float = 20.0
) -> Dict[str, Any]:
    """
    Ingests and preprocesses a single epoch scene for a defense AOI:
    STAC Search -> 10-Band Canvas -> s2cloudless & Shadow Detection ->
    Mask-Aware Normalization -> 512x512 Tiling -> PNG Previews -> Manifest.
    """
    dt_range = f"{epoch_year}-{seasonal_window.split('/')[0]}/{epoch_year}-{seasonal_window.split('/')[1]}"
    logger.info(f"[{aoi_id}] Searching STAC for Epoch {epoch_year} in {dt_range}...")

    scene_meta = search_stac_scene(
        bbox=aoi.bbox,
        datetime_range=dt_range,
        max_cloud_cover=max_cloud_cover,
        offline_fallback=True
    )

    epoch_dir = output_base_dir / f"epoch_{epoch_year}"
    raw_dir = epoch_dir / "raw"
    inter_dir = epoch_dir / "intermediate"
    norm_dir = epoch_dir / "normalized"
    tile_dir = epoch_dir / "tiles"
    prev_dir = epoch_dir / "previews"

    for d in [raw_dir, inter_dir, norm_dir, tile_dir, prev_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 1. Assemble Working Canvas
    canvas = assemble_working_canvas(
        scene_meta=scene_meta,
        aoi_bbox=aoi.bbox,
        buffer_pct=0.05
    )

    # 2. Run s2cloudless, Shadow Detection, and Normalization
    cleaned = clean_and_normalize_canvas(canvas)

    # 3. Slices into 512x512 Tiles
    tiles = slice_and_filter_tiles(
        canvas_data=canvas,
        cleaned_canvas=cleaned,
        aoi_polygon=aoi.polygon,
        scene_id=f"{aoi_id}_{epoch_year}",
        ground_crop_size=DEFAULT_GROUND_CROP_SIZE,
        overlap_pct=0.10
    )

    # Metrics
    total_px = cleaned.cloud_mask.size
    cloud_px = int(np.sum(cleaned.cloud_mask))
    shadow_px = int(np.sum(cleaned.shadow_mask))
    bad_px = int(np.sum(cleaned.bad_mask))

    aoi_cloud_pct = float(cloud_px / total_px * 100.0)
    aoi_shadow_pct = float(shadow_px / total_px * 100.0)
    aoi_bad_pct = float(bad_px / total_px * 100.0)

    # 4. Save GeoTIFFs
    raw_path = raw_dir / "raw_canvas.tif"
    with rasterio.open(
        raw_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=3,
        dtype=canvas.data.dtype, crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(canvas.data[[2, 1, 0]])

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

    norm_path = norm_dir / "normalized_canvas.tif"
    with rasterio.open(
        norm_path, "w", driver="GTiff",
        height=canvas.height, width=canvas.width, count=3,
        dtype="uint8", crs=canvas.crs, transform=canvas.transform
    ) as dst:
        dst.write(cleaned.rgb_normalized)

    # Save 512x512 Tiles
    tile_entries = []
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
        tile_entries.append({
            "tile_id": f"{aoi_id}_{epoch_year}_tile_{idx:04d}",
            "file_name": t_fname,
            "dimensions": f"{t_h}x{t_w}",
            "cloud_percentage": round(t.cloud_pct * 100.0, 2),
            "bounds": [round(b, 6) for b in t.bounds]
        })

    # Previews
    meta_raw, _ = read_geotiff(raw_path)
    meta_norm, _ = read_geotiff(norm_path)

    raw_img = render_rgb_composite(canvas.data[[2, 1, 0]], meta_raw)
    raw_img.save(prev_dir / "raw_rgb_preview.png")

    prob_img = render_probability_heatmap(np.expand_dims(cleaned.cloud_prob, axis=0))
    prob_img.save(prev_dir / "cloud_probability_preview.png")

    cmask_img = render_mask_preview(np.expand_dims(cleaned.cloud_mask, axis=0), color="cyan")
    cmask_img.save(prev_dir / "cloud_mask_preview.png")

    smask_img = render_mask_preview(np.expand_dims(cleaned.shadow_mask, axis=0), color="yellow")
    smask_img.save(prev_dir / "shadow_mask_preview.png")

    norm_img = render_rgb_composite(cleaned.rgb_normalized, meta_norm)
    norm_img.save(prev_dir / "normalized_rgb_preview.png")

    ov_img = render_mask_overlay(raw_img, cloud_mask=cleaned.cloud_mask, shadow_mask=cleaned.shadow_mask, alpha=0.45)
    ov_img.save(prev_dir / "mask_overlay_preview.png")

    if tiles:
        tile_0_img = Image.fromarray(np.transpose(tiles[0].rgb_data, (1, 2, 0)), mode="RGB")
        tile_0_img.save(prev_dir / "final_512_tile_preview.png")

    manifest = {
        "aoi_id": aoi_id,
        "epoch_year": epoch_year,
        "scene_id": scene_meta.scene_id,
        "acquisition_date": scene_meta.acquisition_date,
        "sensor": scene_meta.sensor,
        "crs": canvas.crs,
        "dimensions": f"{canvas.height}x{canvas.width}",
        "cloud_metrics": {
            "catalog_scene_cloud_cover": round(float(scene_meta.cloud_cover), 2),
            "AOI_cloud_pct": round(aoi_cloud_pct, 2),
            "AOI_shadow_pct": round(aoi_shadow_pct, 2),
            "AOI_bad_pixel_pct": round(aoi_bad_pct, 2),
            "tile_cloud_percentages": [t["cloud_percentage"] for t in tile_entries]
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
        "tiles": tile_entries
    }

    with open(epoch_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest


def run_eastern_ladakh_pipeline(
    aoi_id: str = "all",
    target_epochs: List[int] = [2017, 2020, 2023]
) -> Dict[str, Any]:
    """
    Executes full multi-temporal analysis across epochs (2017, 2020, 2023) for Eastern Ladakh AOIs
    and computes multi-temporal change detection difference maps.
    """
    reg = load_registry(EASTERN_LADAKH_REGISTRY)
    
    if aoi_id == "all":
        target_aois = reg["aois"]
    else:
        target_aois = [a for a in reg["aois"] if a["aoi_id"] == aoi_id]
        if not target_aois:
            raise ValueError(f"AOI '{aoi_id}' not found in Eastern Ladakh registry.")

    all_results = {}
    for aoi_entry in target_aois:
        current_id = aoi_entry["aoi_id"]
        geom_path = REPO_ROOT / aoi_entry["geometry_file"]
        validated_aoi = validate_aoi(geom_path)
        output_base = REPO_ROOT / "datasets" / "eastern_ladakh" / "sentinel2" / current_id

        epoch_results = {}
        for year in target_epochs:
            logger.info(f"=== Processing Eastern Ladakh [{current_id}] Epoch {year} ===")
            res = process_defense_scene_epoch(
                aoi=validated_aoi,
                aoi_id=current_id,
                epoch_year=year,
                output_base_dir=output_base,
                seasonal_window=aoi_entry.get("seasonal_window", "07-01/10-31")
            )
            epoch_results[year] = res

        # Multi-temporal Change Detection between 2017 baseline vs 2020 vs 2023
        change_analysis_dir = output_base / "change_analysis"
        change_analysis_dir.mkdir(parents=True, exist_ok=True)
        change_reports = {}

        if 2017 in epoch_results and 2023 in epoch_results:
            norm_2017 = output_base / "epoch_2017" / "normalized" / "normalized_canvas.tif"
            norm_2023 = output_base / "epoch_2023" / "normalized" / "normalized_canvas.tif"
            chg_2017_2023 = compute_multitemporal_difference(
                canvas_a_path=norm_2017,
                canvas_b_path=norm_2023,
                output_tif=change_analysis_dir / "difference_2017_2023.tif",
                output_png=change_analysis_dir / "change_heatmap_2017_2023.png",
                threshold=35.0
            )
            change_reports["2017_vs_2023"] = chg_2017_2023
            logger.info(f"[{current_id}] Border Change Detected (2017 vs 2023): {chg_2017_2023['change_percentage']}% of area")

        if 2017 in epoch_results and 2020 in epoch_results:
            norm_2017 = output_base / "epoch_2017" / "normalized" / "normalized_canvas.tif"
            norm_2020 = output_base / "epoch_2020" / "normalized" / "normalized_canvas.tif"
            chg_2017_2020 = compute_multitemporal_difference(
                canvas_a_path=norm_2017,
                canvas_b_path=norm_2020,
                output_tif=change_analysis_dir / "difference_2017_2020.tif",
                output_png=change_analysis_dir / "change_heatmap_2017_2020.png",
                threshold=35.0
            )
            change_reports["2017_vs_2020"] = chg_2017_2020

        summary_manifest = {
            "region": "Eastern Ladakh High-Altitude Border Corridor",
            "aoi_id": current_id,
            "target_epochs": target_epochs,
            "epochs": epoch_results,
            "change_analysis": change_reports,
            "last_updated": datetime.utcnow().isoformat()
        }

        with open(output_base / "master_manifest.json", "w", encoding="utf-8") as f:
            json.dump(summary_manifest, f, indent=2)

        all_results[current_id] = summary_manifest

    return all_results if aoi_id == "all" else all_results.get(aoi_id, summary_manifest)


def run_maritime_pipeline(
    aoi_id: str = "mumbai_naval_anchorage",
    epoch_year: int = 2023
) -> Dict[str, Any]:
    """
    Executes maritime vessel monitoring preprocessing:
    Sea-surface cloud masking, dark-water vs bright-vessel contrast normalization,
    and standardized 512x512 maritime tile creation.
    """
    reg = load_registry(MARITIME_REGISTRY)
    aoi_entry = next((a for a in reg["aois"] if a["aoi_id"] == aoi_id), None)
    if not aoi_entry:
        raise ValueError(f"AOI '{aoi_id}' not found in Maritime registry.")

    geom_path = REPO_ROOT / aoi_entry["geometry_file"]
    validated_aoi = validate_aoi(geom_path)
    output_base = REPO_ROOT / "datasets" / "maritime" / aoi_id

    logger.info(f"=== Processing Maritime Naval & Marine Vessel Domain [{aoi_id}] ===")
    res = process_defense_scene_epoch(
        aoi=validated_aoi,
        aoi_id=aoi_id,
        epoch_year=epoch_year,
        output_base_dir=output_base,
        seasonal_window="01-01/12-31",
        max_cloud_cover=15.0
    )

    with open(output_base / "master_manifest.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)

    return res


def main():
    parser = argparse.ArgumentParser(description="Defense Preprocessing & Multi-Temporal Runner CLI")
    parser.add_argument("--domain", choices=["eastern_ladakh", "maritime", "all"], default="all", help="Defense domain to process")
    parser.add_argument("--aoi-id", help="Optional specific AOI identifier")
    parser.add_argument("--epochs", default="2017,2020,2023", help="Comma-separated epoch years for Eastern Ladakh")

    args = parser.parse_args()
    target_epochs = [int(y.strip()) for y in args.epochs.split(",")]

    print("\n" + "=" * 85)
    print("DEFENSE PREPROCESSING & MULTI-TEMPORAL PIPELINE EXECUTION")
    print("=" * 85)

    if args.domain in ("eastern_ladakh", "all"):
        target_aoi = args.aoi_id or "pangong_tso_north_bank"
        print(f"\n>> Executing Eastern Ladakh Multi-Temporal Analysis: {target_aoi} (Epochs: {target_epochs})")
        res_el = run_eastern_ladakh_pipeline(aoi_id=target_aoi, target_epochs=target_epochs)
        print(f"   [SUCCESS] Processed {len(res_el['epochs'])} epochs over {target_aoi}")
        if "2017_vs_2023" in res_el.get("change_analysis", {}):
            print(f"   [CHANGE ANALYSIS] 2017 vs 2023 Border Change: {res_el['change_analysis']['2017_vs_2023']['change_percentage']}% of area")

    if args.domain in ("maritime", "all"):
        target_m_aoi = args.aoi_id if (args.domain == "maritime" and args.aoi_id) else "mumbai_naval_anchorage"
        print(f"\n>> Executing Maritime Vessel Monitoring Preprocessing: {target_m_aoi}")
        res_m = run_maritime_pipeline(aoi_id=target_m_aoi, epoch_year=2023)
        print(f"   [SUCCESS] Processed Maritime Domain over {target_m_aoi} (Total Tiles: {res_m['total_tiles']})")

    print("\n" + "=" * 85)
    print("DEFENSE PIPELINE EXECUTION COMPLETED SUCCESSFULLY")
    print("=" * 85 + "\n")


if __name__ == "__main__":
    main()
