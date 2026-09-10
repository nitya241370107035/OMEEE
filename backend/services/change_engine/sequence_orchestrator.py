"""Sequence Orchestrator module for AeroLens Change Engine.

Implements §2.6:
Chains N-1 sequential pairs (T1, T2), (T2, T3), ..., (Tn-1, Tn) across chronological snapshots.
Applies:
1. Bad pixel / cloud gating via ~(mask_before | mask_after)
2. Binary change detection via MTKD-ChangeFormer (BinaryChangeAdapter)
3. Independent spectral classification via NDVI/NDWI/NDBI (SpectralClassifier)
4. Semantic false-positive rejection (SemanticChangeFilter)
5. Transition matrix and road morphology assignment (ChangeTypeRules)
6. GeoJSON vectorization (Vectorizer)
7. Chronological temporal aggregation (TemporalAggregator)
"""
import os
import io
import base64
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from PIL import Image
import rasterio

from backend.change_detector_module.detector import ChangeDetector
from backend.services.change_engine.binary_change_adapter import (
    BinaryChangeAdapter,
    load_tile_rgb,
    normalize_sentinel_rgb,
)
from backend.services.change_engine.spectral_classifier import (
    compute_spectral_indices,
    classify_pixels,
    CLASS_UNCLASSIFIED,
)
from backend.services.change_engine.semantic_change_filter import (
    filter_semantic_changes,
)
from backend.services.change_engine.change_type_rules import (
    vectorized_change_type_lookup,
)
from backend.services.change_engine.vectorizer import (
    polygonize_change_mask,
)
from backend.services.change_engine.temporal_aggregator import (
    aggregate_temporal_sequence,
)

logger = logging.getLogger(__name__)


def rgb_to_png_base64(rgb: np.ndarray, max_dim: int = 384) -> str:
    """Encodes normalized uint8 (H, W, 3) RGB array to a base64 PNG data URL."""
    img = Image.fromarray(rgb.astype(np.uint8), mode="RGB")
    h, w = rgb.shape[:2]
    if h > max_dim or w > max_dim:
        img = img.resize((max_dim, max_dim), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def ndvi_to_png_base64(ndvi: np.ndarray, max_dim: int = 384) -> str:
    """Maps continuous NDVI [-1.0, 1.0] to a standard colorized band map and base64 PNG.
    
    Ramp:
    < 0.0: Water / Shadow (Deep Blue: #1e3a8a)
    0.0 - 0.2: Built-up / Barren (Tan / Ochre: #d97706)
    0.2 - 0.5: Moderate / Sparse Vegetation (Lime / Yellow-Green: #84cc16)
    > 0.5: Dense Healthy Canopy (Vibrant Green: #16a34a)
    """
    h, w = ndvi.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255

    val = np.clip(ndvi, -1.0, 1.0)

    # Segment 1: Water (< 0.0) -> navy to sky blue
    w_mask = val < 0.0
    w_ratio = np.clip((val + 1.0), 0.0, 1.0)
    rgba[w_mask, 0] = (20 + w_ratio[w_mask] * 36).astype(np.uint8)
    rgba[w_mask, 1] = (40 + w_ratio[w_mask] * 149).astype(np.uint8)
    rgba[w_mask, 2] = (100 + w_ratio[w_mask] * 148).astype(np.uint8)

    # Segment 2: Bare Soil / Built-up (0.0 to 0.2) -> Tan to Sand/Gold
    s_mask = (val >= 0.0) & (val < 0.2)
    s_ratio = val[s_mask] / 0.2
    rgba[s_mask, 0] = (180 + s_ratio * 54).astype(np.uint8)
    rgba[s_mask, 1] = (120 + s_ratio * 59).astype(np.uint8)
    rgba[s_mask, 2] = (50 - s_ratio * 42).astype(np.uint8)

    # Segment 3: Sparse to Moderate Veg (0.2 to 0.5) -> Yellow-Green
    m_mask = (val >= 0.2) & (val < 0.5)
    m_ratio = (val[m_mask] - 0.2) / 0.3
    rgba[m_mask, 0] = (163 - m_ratio * 129).astype(np.uint8)
    rgba[m_mask, 1] = (230 - m_ratio * 33).astype(np.uint8)
    rgba[m_mask, 2] = (53 + m_ratio * 41).astype(np.uint8)

    # Segment 4: Dense Veg (>= 0.5) -> Deep Lush Forest
    d_mask = val >= 0.5
    d_ratio = np.clip((val[d_mask] - 0.5) / 0.5, 0.0, 1.0)
    rgba[d_mask, 0] = (34 - d_ratio * 30).astype(np.uint8)
    rgba[d_mask, 1] = (197 - d_ratio * 77).astype(np.uint8)
    rgba[d_mask, 2] = (94 - d_ratio * 7).astype(np.uint8)

    img = Image.fromarray(rgba, mode="RGBA")
    if h > max_dim or w > max_dim:
        img = img.resize((max_dim, max_dim), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def mask_to_png_base64(mask: np.ndarray, color=(6, 182, 212), max_dim: int = 384) -> str:
    """Encodes a boolean or uint8 mask to a base64 PNG data URL."""
    h, w = mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    bool_mask = mask > 0
    rgba[bool_mask, 0] = color[0]
    rgba[bool_mask, 1] = color[1]
    rgba[bool_mask, 2] = color[2]
    rgba[bool_mask, 3] = 230
    rgba[~bool_mask, :3] = (11, 17, 32)
    rgba[~bool_mask, 3] = 200

    img = Image.fromarray(rgba, mode="RGBA")
    if h > max_dim or w > max_dim:
        img = img.resize((max_dim, max_dim), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")



def load_bands_and_masks(
    file_path: str,
    mask_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Loads Green (B03), Red (B04), NIR (B08), SWIR (B11) and bad pixel mask from disk."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Tile not found: {file_path}")

    with rasterio.open(file_path) as ds:
        transform = ds.transform
        profile = ds.profile
        count = ds.count

        if count >= 9:
            # Standard AeroLens Sentinel-2 9-band stack
            # ('B01', 'B02', 'B03', 'B04', 'B05', 'B08', 'B8A', 'B11', 'B12')
            blue = ds.read(2)
            green = ds.read(3)
            red = ds.read(4)
            nir = ds.read(6)
            swir = ds.read(8)
        elif count >= 4:
            blue = ds.read(1)
            green = ds.read(2)
            red = ds.read(3)
            nir = ds.read(4)
            swir = nir
        else:
            b1 = ds.read(1)
            blue, green, red, nir, swir = b1, b1, b1, b1, b1

    # Load bad pixel / cloud mask if present
    h, w = red.shape
    if mask_path and os.path.exists(mask_path):
        with rasterio.open(mask_path) as mds:
            bad_mask = mds.read(1) > 0
    else:
        bad_mask = np.zeros((h, w), dtype=bool)

    # Pre-render normalized RGB for ChangeFormer
    rgb = normalize_sentinel_rgb(red, green, blue)

    return {
        "blue": blue,
        "green": green,
        "red": red,
        "nir": nir,
        "swir": swir,
        "bad_mask": bad_mask,
        "rgb": rgb,
        "transform": transform,
        "profile": profile,
    }


_CHANGE_DETECTOR_SINGLETON: Optional[ChangeDetector] = None


def get_change_detector() -> ChangeDetector:
    """Returns the singleton MTKD-ChangeFormer ChangeDetector instance from change_detector_module."""
    global _CHANGE_DETECTOR_SINGLETON
    if _CHANGE_DETECTOR_SINGLETON is None:
        logger.info("Initializing binary mask model directly from backend.change_detector_module.detector.ChangeDetector...")
        _CHANGE_DETECTOR_SINGLETON = ChangeDetector()
        logger.info("Binary mask model (change_detector_module) initialized successfully.")
    return _CHANGE_DETECTOR_SINGLETON


class SequenceOrchestrator:
    """Orchestrates end-to-end multi-temporal sequence analysis using change_detector_module."""

    def __init__(self, detector: Optional[ChangeDetector] = None, adapter: Optional[BinaryChangeAdapter] = None):
        if detector is not None:
            self.detector = detector
        elif adapter is not None and hasattr(adapter, "detector"):
            self.detector = adapter.detector
        else:
            self.detector = get_change_detector()

    def process_pair(
        self,
        snapshot_before: Dict[str, Any],
        snapshot_after: Dict[str, Any],
        pair_index: int = 0,
    ) -> Dict[str, Any]:
        """Runs the semantic-verified change detection pipeline for a single consecutive pair."""
        date_before = str(snapshot_before.get("acquisition_date") or snapshot_before.get("date", ""))[:10]
        date_after = str(snapshot_after.get("acquisition_date") or snapshot_after.get("date", ""))[:10]

        path_before = snapshot_before["file_path"]
        path_after = snapshot_after["file_path"]
        mask_before_path = snapshot_before.get("bad_mask_path")
        mask_after_path = snapshot_after.get("bad_mask_path")

        # Step 1: Load bands and masks
        data_b = load_bands_and_masks(path_before, mask_before_path)
        data_a = load_bands_and_masks(path_after, mask_after_path)

        transform = data_b["transform"]
        h, w = data_b["red"].shape

        # Step 2: Valid pixels (exclude clouds/nodata in either snapshot)
        valid_pixels = ~(data_b["bad_mask"] | data_a["bad_mask"])

        # Step 3: Run Binary ChangeFormer model directly from change_detector_module
        raw_binary_mask = self.detector.get_binary_mask(data_b["rgb"], data_a["rgb"])
        binary_mask = (raw_binary_mask > 0) & valid_pixels

        # Step 4: Compute spectral indices independently
        indices_b = compute_spectral_indices(
            data_b["green"], data_b["red"], data_b["nir"], data_b["swir"]
        )
        indices_a = compute_spectral_indices(
            data_a["green"], data_a["red"], data_a["nir"], data_a["swir"]
        )

        # Step 5: Spectral classification
        class_before_map = classify_pixels(indices_b["ndvi"], indices_b["ndwi"], indices_b["ndbi"])
        class_after_map = classify_pixels(indices_a["ndvi"], indices_a["ndwi"], indices_a["ndbi"])

        # Step 6: Semantic False-Positive Filter (before_class == after_class is discarded)
        surviving_mask, filter_stats = filter_semantic_changes(
            class_before_map, class_after_map, binary_mask=binary_mask
        )

        # Step 7: Transition Matrix Lookup
        change_type_map = vectorized_change_type_lookup(class_before_map, class_after_map)

        # Step 8: Vectorize to GeoJSON polygons with road morphology test & spectral sampling
        features = polygonize_change_mask(
            mask=surviving_mask,
            geotransform=transform,
            change_type_map=change_type_map,
            before_class_map=class_before_map,
            after_class_map=class_after_map,
            indices_before=indices_b,
            indices_after=indices_a,
        )

        total_pixels = binary_mask.size
        changed_pixels = int(np.sum(surviving_mask))
        change_pct = round(float((changed_pixels / total_pixels) * 100.0), 2)

        # Step 9: Pair-level aggregate spectral profile across changed pixels
        if changed_pixels > 0:
            b_ndvi = round(float(np.nanmean(indices_b["ndvi"][surviving_mask])), 3)
            b_ndbi = round(float(np.nanmean(indices_b["ndbi"][surviving_mask])), 3)
            b_ndwi = round(float(np.nanmean(indices_b["ndwi"][surviving_mask])), 3)
            a_ndvi = round(float(np.nanmean(indices_a["ndvi"][surviving_mask])), 3)
            a_ndbi = round(float(np.nanmean(indices_a["ndbi"][surviving_mask])), 3)
            a_ndwi = round(float(np.nanmean(indices_a["ndwi"][surviving_mask])), 3)
        else:
            b_ndvi, b_ndbi, b_ndwi = 0.0, 0.0, 0.0
            a_ndvi, a_ndbi, a_ndwi = 0.0, 0.0, 0.0

        spectral_profile = {
            "before": {"ndvi": b_ndvi, "ndbi": b_ndbi, "ndwi": b_ndwi},
            "after": {"ndvi": a_ndvi, "ndbi": a_ndbi, "ndwi": a_ndwi},
            "delta": {
                "ndvi": round(a_ndvi - b_ndvi, 3),
                "ndbi": round(a_ndbi - b_ndbi, 3),
                "ndwi": round(a_ndwi - b_ndwi, 3),
            }
        }

        # Step 10: Generate Web-ready base64 PNG previews for all bands and masks
        rgb_b_png = rgb_to_png_base64(data_b["rgb"])
        rgb_a_png = rgb_to_png_base64(data_a["rgb"])
        ndvi_b_png = ndvi_to_png_base64(indices_b["ndvi"])
        ndvi_a_png = ndvi_to_png_base64(indices_a["ndvi"])
        raw_mask_png = mask_to_png_base64(raw_binary_mask, color=(6, 182, 212))
        filtered_mask_png = mask_to_png_base64(surviving_mask, color=(245, 158, 11))

        cand_pixels = int(np.sum(binary_mask))
        fp_rejected = int(filter_stats.get("false_positives_rejected", 0))

        # Step 11: Sample 64x64 spatial index grid for live cursor hover inspection
        grid_step = max(1, h // 64)
        ndvi_b_sample = np.round(indices_b["ndvi"][::grid_step, ::grid_step], 3).tolist()
        ndwi_b_sample = np.round(indices_b["ndwi"][::grid_step, ::grid_step], 3).tolist()
        ndbi_b_sample = np.round(indices_b["ndbi"][::grid_step, ::grid_step], 3).tolist()

        ndvi_a_sample = np.round(indices_a["ndvi"][::grid_step, ::grid_step], 3).tolist()
        ndwi_a_sample = np.round(indices_a["ndwi"][::grid_step, ::grid_step], 3).tolist()
        ndbi_a_sample = np.round(indices_a["ndbi"][::grid_step, ::grid_step], 3).tolist()

        cursor_sample_grid = {
            "grid_size": len(ndvi_b_sample),
            "before": {
                "ndvi": ndvi_b_sample,
                "ndwi": ndwi_b_sample,
                "ndbi": ndbi_b_sample,
            },
            "after": {
                "ndvi": ndvi_a_sample,
                "ndwi": ndwi_a_sample,
                "ndbi": ndbi_a_sample,
            }
        }

        return {
            "pair_index": pair_index,
            "tile_before_id": snapshot_before.get("tile_id", f"t_{date_before}"),
            "tile_after_id": snapshot_after.get("tile_id", f"t_{date_after}"),
            "date_before": date_before,
            "date_after": date_after,
            "change_pct": change_pct,
            "candidate_pixels": cand_pixels,
            "false_positives_rejected": fp_rejected,
            "changed_pixels": changed_pixels,
            "rgb_before_url": rgb_b_png,
            "rgb_after_url": rgb_a_png,
            "ndvi_before_url": ndvi_b_png,
            "ndvi_after_url": ndvi_a_png,
            "binary_mask_url": raw_mask_png,
            "filtered_mask_url": filtered_mask_png,
            "spectral_profile": spectral_profile,
            "cursor_sample_grid": cursor_sample_grid,
            "filter_stats": filter_stats,
            "change_geojson": {
                "type": "FeatureCollection",
                "features": features,
            },
        }

    def run_sequence(
        self,
        site_key: str,
        ordered_snapshots: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Chains all N-1 sequential pairs and aggregates multi-temporal change."""
        if len(ordered_snapshots) < 2:
            raise ValueError(f"At least 2 snapshots required for change analysis, got {len(ordered_snapshots)}")

        # Sort chronologically
        snapshots = sorted(
            ordered_snapshots,
            key=lambda s: str(s.get("acquisition_date") or s.get("date", ""))
        )

        pairwise_results = []
        for i in range(len(snapshots) - 1):
            snap_before = snapshots[i]
            snap_after = snapshots[i + 1]
            pair_res = self.process_pair(snap_before, snap_after, pair_index=i)
            pairwise_results.append(pair_res)

        # Aggregate across all pairs for earliest supported date and overall map
        aggregated = aggregate_temporal_sequence(pairwise_results, site_key=site_key)
        return aggregated
