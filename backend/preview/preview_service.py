"""
GeoTIFF Preview Service & CLI Inspection Tool

Provides unified programmatic inspection and CLI commands for analyzing any
GeoTIFF file in the project pipeline without file mutation.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image

# Ensure repo root on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.preview.geotiff_reader import read_geotiff, classify_geotiff_category
from backend.preview.raster_metadata import RasterMetadata
from backend.preview.statistics import compute_raster_statistics, RasterStatistics
from backend.preview.rgb_preview import generate_raster_preview, render_mask_overlay

logger = logging.getLogger("preview_service")


def validate_tile_dimensions(width: int, height: int) -> Tuple[bool, str]:
    """
    Validates tile dimensions. Final output tiles MUST be exactly 512x512.
    """
    if width == 512 and height == 512:
        return True, "PASS"
    return False, f"FAIL (Expected: 512x512, Actual: {height}x{width})"


def discover_scene_groups(base_dir: Path) -> Dict[str, Dict[str, Optional[Path]]]:
    """
    Discovers intermediate scene directories and maps available pipeline layers.
    Layers: raw_canvas, cloud_probability, cloud_mask, shadow_mask, bad_mask, normalized_canvas.
    """
    scenes: Dict[str, Dict[str, Optional[Path]]] = {}
    search_dirs = [
        base_dir / "data" / "intermediate",
        base_dir / "datasets" / "pipeline_validation" / "current_aoi",
        base_dir / "datasets" / "pipeline_validation" / "cloud_masking_validation",
        base_dir / "datasets" / "eastern_ladakh" / "sentinel2",
        base_dir / "datasets" / "maritime"
    ]

    expected_layers = [
        "raw_canvas.tif",
        "cloud_probability.tif",
        "cloud_mask.tif",
        "shadow_mask.tif",
        "bad_mask.tif",
        "normalized_canvas.tif"
    ]

    for s_dir in search_dirs:
        if not s_dir.exists():
            continue
        # Find all directories that contain any tif or subdirectories with raw/intermediate/normalized
        for sub in s_dir.glob("**/epoch_*"):
            if sub.is_dir():
                layer_map: Dict[str, Optional[Path]] = {}
                for exp in expected_layers:
                    key = exp.replace(".tif", "")
                    found = list(sub.glob(f"**/{exp}"))
                    p = found[0] if found else None
                    if not p and key == "bad_mask":
                        alt = list(sub.glob("**/combined_mask.tif"))
                        p = alt[0] if alt else None
                    layer_map[key] = p
                rel_key = str(sub.relative_to(base_dir))
                scenes[rel_key] = layer_map

        # Also find direct subdirectories (e.g. data/intermediate/scene_id or cloud_masking_validation/scene_id)
        for sub in s_dir.iterdir():
            if sub.is_dir() and not sub.name.startswith("epoch_"):
                tifs = list(sub.glob("**/*.tif"))
                if tifs:
                    scene_name = sub.name
                    layer_map: Dict[str, Optional[Path]] = {}
                    for exp in expected_layers:
                        key = exp.replace(".tif", "")
                        found = list(sub.glob(f"**/{exp}"))
                        p = found[0] if found else None
                        if not p and key == "bad_mask":
                            alt = list(sub.glob("**/combined_mask.tif"))
                            p = alt[0] if alt else None
                        layer_map[key] = p
                    rel_key = str(sub.relative_to(base_dir))
                    scenes[rel_key] = layer_map

    return scenes


def discover_tile_directories(base_dir: Path) -> Dict[str, List[Path]]:
    """
    Discovers directories containing final output tiles (e.g. data/tiles/ or datasets/**/tiles).
    """
    tile_dirs: Dict[str, List[Path]] = {}
    search_dirs = [
        base_dir / "data" / "tiles",
        base_dir / "datasets" / "pipeline_validation",
        base_dir / "datasets" / "eastern_ladakh" / "sentinel2",
        base_dir / "datasets" / "maritime"
    ]

    for s_dir in search_dirs:
        if not s_dir.exists():
            continue
        for tif in sorted(s_dir.glob("**/*.tif")):
            if tif.name.startswith("tile_") or "tiles" in [p.lower() for p in tif.parts]:
                parent_key = str(tif.parent.relative_to(base_dir))
                tile_dirs.setdefault(parent_key, []).append(tif)

    return tile_dirs


def check_aoi_association(bounds: Tuple[float, float, float, float], repo_root: Path) -> Tuple[str, str]:
    """
    Checks if raster bounds intersect or match known AOIs from config.
    Returns (aoi_id, status: MATCH / OUTSIDE / UNKNOWN).
    """
    west, south, east, north = bounds
    # Known Delhi Cantonment AOI bounds (~77.10 to 77.15, 28.58 to 28.62)
    if (77.08 <= west <= 77.16) and (28.56 <= south <= 28.64):
        return "pipeline_validation_delhi_cantonment", "MATCH"

    # Known Pangong Tso AOI bounds (~78.4 to 79.1, 33.6 to 34.0)
    if (78.3 <= west <= 79.2) and (33.5 <= south <= 34.1):
        return "pangong_tso_north_bank", "MATCH"

    # Known Galwan Valley bounds (~77.9 to 78.5, 34.6 to 35.0)
    if (77.8 <= west <= 78.6) and (34.5 <= south <= 35.1):
        return "galwan_valley_confluence", "MATCH"

    # Known Depsang Plains bounds (~77.7 to 78.3, 35.1 to 35.6)
    if (77.6 <= west <= 78.4) and (35.0 <= south <= 35.7):
        return "depsang_plains_y_junction", "MATCH"

    # Known Mumbai Naval Dockyard bounds (~72.80 to 72.90, 18.88 to 18.98)
    if (72.80 <= west <= 72.90) and (18.88 <= south <= 18.98):
        return "mumbai_naval_anchorage", "MATCH"

    # Known Chushul bounds (~78.5 to 78.9, 33.4 to 33.8)
    if (78.4 <= west <= 79.0) and (33.3 <= south <= 33.9):
        return "chushul_rezang_la", "MATCH"

    return "UNKNOWN", "UNKNOWN"


class GeoTIFFPreviewService:
    """Service for safe inspection and rendering of GeoTIFF datasets."""

    @staticmethod
    def inspect(
        file_path: Path,
        render_preview: bool = True
    ) -> Tuple[RasterMetadata, RasterStatistics, Optional[Image.Image], str]:
        """
        Safely reads metadata, calculates NoData/raster statistics, and generates preview.

        Returns:
            Tuple: (RasterMetadata, RasterStatistics, PIL.Image or None, preview_mode_str)
        """
        metadata, data = read_geotiff(file_path=file_path, read_data=True)
        stats = compute_raster_statistics(data=data, metadata=metadata)
        preview_img, preview_mode = (generate_raster_preview(data, metadata) if render_preview else (None, "None"))
        return metadata, stats, preview_img, preview_mode


def main():
    parser = argparse.ArgumentParser(description="GeoTIFF Raster Inspection & Preview CLI")
    parser.add_argument("--file", "-f", required=True, help="Path to GeoTIFF file to inspect")
    parser.add_argument("--save-preview", "-s", help="Optional path to save rendered preview image (e.g. preview.png)")
    parser.add_argument("--json", "-j", action="store_true", help="Output inspection result as raw JSON")

    args = parser.parse_args()
    file_path = Path(args.file)

    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    try:
        metadata, stats, preview_img, preview_mode = GeoTIFFPreviewService.inspect(file_path)
    except Exception as exc:
        print(f"Error inspecting GeoTIFF: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.save_preview and preview_img:
        save_path = Path(args.save_preview)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        preview_img.save(save_path)
        print(f"Preview image saved to: {save_path}")

    aoi_id, aoi_status = check_aoi_association(metadata.bounds, REPO_ROOT)

    if args.json:
        result = {
            "metadata": metadata.to_dict(),
            "statistics": stats.to_dict(),
            "file_category": metadata.file_category,
            "aoi_association": {"aoi_id": aoi_id, "status": aoi_status},
            "preview_mode": preview_mode
        }
        print(json.dumps(result, indent=2))
        return

    # Structured CLI Output
    print("\n" + "=" * 80)
    print("GEOTIFF RASTER INSPECTION REPORT")
    print("=" * 80)
    print(f"  FILE               : {metadata.file_name}")
    print(f"  PATH               : {metadata.full_path}")
    print(f"  FILE CATEGORY      : {metadata.file_category}")
    print(f"  FILE SIZE          : {metadata.file_size_formatted} ({metadata.file_size_bytes} bytes)")
    print(f"  BANDS              : {metadata.count}")
    print(f"  BAND DESCRIPTIONS  : {', '.join(metadata.band_descriptions)}")
    print(f"  CRS                : {metadata.crs}")
    print(f"  RESOLUTION         : {metadata.resolution[0]:.8f}, {metadata.resolution[1]:.8f}")
    print(f"  DIMENSIONS         : {metadata.height} (Height) x {metadata.width} (Width)")

    if metadata.file_category == "WORKING_CANVAS":
        print("  TILE REQUIREMENT   : NOT APPLICABLE (Working canvas dimensions depend on AOI extent)")
    elif metadata.file_category == "FINAL_TILE":
        tile_valid, tile_msg = validate_tile_dimensions(metadata.width, metadata.height)
        print(f"  TILE REQUIREMENT   : {tile_msg}")

    print(f"  BOUNDS             : West={metadata.bounds[0]:.6f}, South={metadata.bounds[1]:.6f}, East={metadata.bounds[2]:.6f}, North={metadata.bounds[3]:.6f}")
    print(f"  AOI ASSOCIATION    : {aoi_id} ({aoi_status})")
    print(f"  DATATYPE           : {', '.join(metadata.dtypes)}")
    print(f"  NODATA VALUE       : {metadata.nodata}")
    print(f"  NODATA PERCENTAGE  : {stats.nodata_percentage:.2f}% ({stats.nodata_pixels:,} / {stats.total_pixels:,} px)")
    print(f"  VALID PIXELS       : {stats.valid_pixel_percentage:.2f}% ({stats.valid_pixels:,} px)")
    print(f"  PREVIEW MODE       : {preview_mode}")

    if stats.is_mask:
        print("\n  [BINARY MASK STATS]")
        print(f"    - Masked Pixels  : {stats.masked_pixel_count:,} ({stats.masked_percentage:.2f}%)")
        print(f"    - Valid Ground   : {stats.valid_ground_count:,} ({100.0 - stats.masked_percentage:.2f}%)")

    print("\n  [PER-BAND SUMMARY]")
    for b in stats.per_band_stats:
        print(f"    Band {b['band_index']} ({b['band_name']}): Min={b['min']}, Max={b['max']}, Mean={b['mean']}, Std={b['std']}, NoData={b['nodata_percentage']}%")

    if metadata.tags:
        print("\n  [RASTER TAGS]")
        for k, v in metadata.tags.items():
            print(f"    {k}: {v}")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
