"""
Input Adapter for Cloud Removal Module

Reads, parses, and validates existing Phase 2 preprocessing products:
- Raw / Normalized optical RGB canvas
- s2cloudless cloud probability map
- Binary cloud mask & shadow mask
- Combined bad-pixel quality mask

Ensures strict shape, CRS, transform, and metadata compatibility across all rasters.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import numpy as np
import rasterio
from rasterio.transform import Affine

from backend.preview.geotiff_reader import read_geotiff
from backend.preview.raster_metadata import RasterMetadata


@dataclass
class SceneInputs:
    """Encapsulates validated multi-layer raster inputs for a satellite scene."""
    scene_id: str
    rgb_data: np.ndarray             # Shape: (3, H, W), uint8 or float32
    cloud_prob: np.ndarray           # Shape: (H, W), float32 [0.0 - 1.0]
    cloud_mask: np.ndarray           # Shape: (H, W), uint8 {0, 1}
    shadow_mask: np.ndarray          # Shape: (H, W), uint8 {0, 1}
    bad_mask: np.ndarray             # Shape: (H, W), uint8 {0, 1}
    height: int
    width: int
    crs: str
    transform: Affine
    metadata: Optional[RasterMetadata]
    source_paths: Dict[str, str]


class CloudRemovalInputAdapter:
    """Ingests and validates Phase 2 output directories for cloud removal."""

    @staticmethod
    def load_scene_directory(scene_dir: Path, scene_id: Optional[str] = None) -> SceneInputs:
        """
        Loads all required Phase 2 layers from a scene directory or dataset structure.
        """
        if not scene_dir.exists():
            raise FileNotFoundError(f"Scene directory does not exist: {scene_dir}")

        s_id = scene_id or scene_dir.name

        # 1. Locate layer files across standard layouts (flat or subdirectories)
        raw_path = CloudRemovalInputAdapter._find_file(scene_dir, ["raw/raw_canvas.tif", "raw_canvas.tif", "raw.tif"])
        norm_path = CloudRemovalInputAdapter._find_file(scene_dir, ["normalized/normalized_canvas.tif", "normalized_canvas.tif", "normalized.tif"])
        prob_path = CloudRemovalInputAdapter._find_file(scene_dir, ["intermediate/cloud_probability.tif", "cloud_probability.tif"])
        cmask_path = CloudRemovalInputAdapter._find_file(scene_dir, ["intermediate/cloud_mask.tif", "cloud_mask.tif"])
        smask_path = CloudRemovalInputAdapter._find_file(scene_dir, ["intermediate/shadow_mask.tif", "shadow_mask.tif"])
        bmask_path = CloudRemovalInputAdapter._find_file(scene_dir, ["intermediate/bad_mask.tif", "bad_mask.tif"])

        # RGB canvas preference: normalized_canvas if available, otherwise raw_canvas
        rgb_path = norm_path or raw_path
        if not rgb_path or not rgb_path.exists():
            raise FileNotFoundError(f"Neither normalized nor raw RGB canvas found in: {scene_dir}")

        if not prob_path or not prob_path.exists():
            raise FileNotFoundError(f"Missing required cloud probability raster in: {scene_dir}")

        if not cmask_path or not cmask_path.exists():
            raise FileNotFoundError(f"Missing required cloud mask raster in: {scene_dir}")

        # 2. Read Primary Metadata & RGB Canvas
        meta_rgb, _ = read_geotiff(rgb_path)
        with rasterio.open(rgb_path) as src_rgb:
            rgb_arr = src_rgb.read()  # (C, H, W)
            if rgb_arr.shape[0] > 3:
                # If 10-band canvas, take RGB bands (typically bands 3, 2, 1 for B04, B03, B02 or first 3)
                rgb_arr = rgb_arr[:3]
            elif rgb_arr.shape[0] == 1:
                # Replicate single band to 3 channels
                rgb_arr = np.repeat(rgb_arr, 3, axis=0)

            # Ensure uint8 [0..255]
            if rgb_arr.dtype != np.uint8:
                rgb_min, rgb_max = rgb_arr.min(), rgb_arr.max()
                if rgb_max > rgb_min:
                    rgb_arr = np.clip((rgb_arr - rgb_min) / (rgb_max - rgb_min) * 255.0, 0, 255).astype(np.uint8)
                else:
                    rgb_arr = np.zeros_like(rgb_arr, dtype=np.uint8)

            c_h, c_w = src_rgb.height, src_rgb.width
            crs_str = str(src_rgb.crs)
            aff_transform = src_rgb.transform

        # 3. Read Cloud Probability
        with rasterio.open(prob_path) as src_prob:
            CloudRemovalInputAdapter._validate_compatibility(src_prob, c_h, c_w, crs_str, "cloud_probability")
            prob_arr = src_prob.read(1).astype(np.float32)
            # Ensure prob is in [0.0, 1.0]
            if prob_arr.max() > 1.0:
                prob_arr = np.clip(prob_arr / 100.0 if prob_arr.max() <= 100.0 else prob_arr / 255.0, 0.0, 1.0)
            else:
                prob_arr = np.clip(prob_arr, 0.0, 1.0)

        # 4. Read Cloud Mask
        with rasterio.open(cmask_path) as src_cmask:
            CloudRemovalInputAdapter._validate_compatibility(src_cmask, c_h, c_w, crs_str, "cloud_mask")
            cmask_arr = (src_cmask.read(1) > 0).astype(np.uint8)

        # 5. Read Shadow Mask (or default to zeros)
        if smask_path and smask_path.exists():
            with rasterio.open(smask_path) as src_smask:
                CloudRemovalInputAdapter._validate_compatibility(src_smask, c_h, c_w, crs_str, "shadow_mask")
                smask_arr = (src_smask.read(1) > 0).astype(np.uint8)
        else:
            smask_arr = np.zeros((c_h, c_w), dtype=np.uint8)

        # 6. Read Bad Mask (or combine cloud + shadow)
        if bmask_path and bmask_path.exists():
            with rasterio.open(bmask_path) as src_bmask:
                CloudRemovalInputAdapter._validate_compatibility(src_bmask, c_h, c_w, crs_str, "bad_mask")
                bmask_arr = (src_bmask.read(1) > 0).astype(np.uint8)
        else:
            bmask_arr = np.clip(cmask_arr | smask_arr, 0, 1).astype(np.uint8)

        source_paths = {
            "rgb_canvas": str(rgb_path),
            "cloud_probability": str(prob_path),
            "cloud_mask": str(cmask_path),
            "shadow_mask": str(smask_path) if smask_path else "",
            "bad_mask": str(bmask_path) if bmask_path else ""
        }

        return SceneInputs(
            scene_id=s_id,
            rgb_data=rgb_arr,
            cloud_prob=prob_arr,
            cloud_mask=cmask_arr,
            shadow_mask=smask_arr,
            bad_mask=bmask_arr,
            height=c_h,
            width=c_w,
            crs=crs_str,
            transform=aff_transform,
            metadata=meta_rgb,
            source_paths=source_paths
        )

    @staticmethod
    def _find_file(base_dir: Path, candidates: list) -> Optional[Path]:
        for c in candidates:
            p = base_dir / c
            if p.exists():
                return p
        return None

    @staticmethod
    def _validate_compatibility(src, exp_h: int, exp_w: int, exp_crs: str, layer_name: str):
        if src.height != exp_h or src.width != exp_w:
            raise ValueError(
                f"Dimension mismatch in {layer_name}: expected ({exp_h}, {exp_w}), got ({src.height}, {src.width})"
            )
        if exp_crs and src.crs and str(src.crs) != exp_crs:
            raise ValueError(
                f"CRS mismatch in {layer_name}: expected '{exp_crs}', got '{src.crs}'"
            )
