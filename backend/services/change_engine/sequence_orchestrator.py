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


def mask_to_png_base64(mask: np.ndarray, color=(6, 182, 212)) -> str:
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
    if h > 256 or w > 256:
        img = img.resize((256, 256), Image.Resampling.NEAREST)
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


class SequenceOrchestrator:
    """Orchestrates end-to-end multi-temporal sequence analysis."""

    def __init__(self, adapter: Optional[BinaryChangeAdapter] = None):
        self.adapter = adapter or BinaryChangeAdapter()

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

        # Step 3: Run Binary ChangeFormer model
        raw_binary_mask = self.adapter.predict_binary_mask(data_b["rgb"], data_a["rgb"])
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

        # Step 10: Generate Web-ready base64 PNG previews of the masks
        raw_mask_png = mask_to_png_base64(raw_binary_mask, color=(6, 182, 212))
        filtered_mask_png = mask_to_png_base64(surviving_mask, color=(245, 158, 11))

        return {
            "pair_index": pair_index,
            "tile_before_id": snapshot_before.get("tile_id", f"t_{date_before}"),
            "tile_after_id": snapshot_after.get("tile_id", f"t_{date_after}"),
            "date_before": date_before,
            "date_after": date_after,
            "change_pct": change_pct,
            "changed_pixels": changed_pixels,
            "binary_mask_url": raw_mask_png,
            "filtered_mask_url": filtered_mask_png,
            "spectral_profile": spectral_profile,
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
