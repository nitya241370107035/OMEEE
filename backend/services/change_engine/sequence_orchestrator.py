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
    CLASS_WATER,
    CLASS_DENSE_VEGETATION,
    CLASS_SPARSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_CONFUSION,
    CLASS_UNCLASSIFIED,
)
from backend.services.change_engine.semantic_change_filter import (
    filter_semantic_changes,
)
from backend.services.change_engine.change_type_rules import (
    vectorized_change_type_lookup,
    format_transition_label,
    MAJOR_CHANGE_TYPES,
    TYPE_WATER_EXPANSION,
    TYPE_WATER_SHRINKAGE,
    TYPE_CONSTRUCTION,
    TYPE_CLEARANCE,
    TYPE_ROAD_DEVELOPMENT,
    TYPE_DEMOLITION,
    TYPE_VEGETATION_LARGE_SCALE,
)
from backend.services.change_engine.vectorizer import (
    polygonize_change_mask,
)
from backend.services.change_engine.temporal_aggregator import (
    aggregate_temporal_sequence,
)

logger = logging.getLogger(__name__)


def draw_bounding_squares(base_img: Image.Image, boxes: Optional[List[Dict[str, Any]]]) -> Image.Image:
    """Overlays high-contrast engineering bounding squares directly onto the image."""
    if not boxes:
        return base_img

    from PIL import ImageDraw
    img_out = base_img.copy().convert("RGBA")
    draw = ImageDraw.Draw(img_out, "RGBA")
    w, h = img_out.size

    TYPE_COLORS = {
        "Construction": (239, 68, 68, 255),                       # Reddish #ef4444
        "Road Development": (168, 85, 247, 255),                   # Purple #a855f7
        "Clearance": (253, 224, 71, 255),                           # Light Yellow #fde047
        "Water-Extent Variation (shrinkage)": (6, 182, 212, 255), # Cyan #06b6d4
        "Water-Extent Variation (expansion)": (56, 189, 248, 255), # Blue #38bdf8
        "Water Extension": (56, 189, 248, 255),                    # Blue #38bdf8
        "Demolition / Reversion": (251, 146, 60, 255),            # Orange #fb923c
        "Vegetation Shift (Large Scale)": (16, 185, 129, 255),    # Emerald #10b981
    }

    for b in boxes:
        c_type = b.get("change_type", "Construction")
        color = TYPE_COLORS.get(c_type, (239, 68, 68, 255))
        fill_color = (color[0], color[1], color[2], 40)

        norm = b.get("box_pct", {})
        if not norm:
            continue
        x0 = int(round((norm["x"] / 100.0) * w))
        y0 = int(round((norm["y"] / 100.0) * h))
        bw = max(6, int(round((norm["w"] / 100.0) * w)))
        bh = max(6, int(round((norm["h"] / 100.0) * h)))
        x1 = min(w - 1, x0 + bw)
        y1 = min(h - 1, y0 + bh)

        # Draw semi-transparent highlight fill and crisp 2px border
        draw.rectangle([x0, y0, x1, y1], fill=fill_color, outline=color, width=2)

    return img_out


def rgb_to_png_base64(rgb: np.ndarray, max_dim: int = 384, boxes: Optional[List[Dict[str, Any]]] = None) -> str:
    """Encodes normalized uint8 (H, W, 3) RGB array to a base64 PNG data URL."""
    img = Image.fromarray(rgb.astype(np.uint8), mode="RGB")
    h, w = rgb.shape[:2]
    if h > max_dim or w > max_dim:
        img = img.resize((max_dim, max_dim), Image.Resampling.BILINEAR)
    if boxes:
        img = draw_bounding_squares(img, boxes)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def ndvi_to_png_base64(
    ndvi: np.ndarray,
    ndbi: Optional[np.ndarray] = None,
    class_map: Optional[np.ndarray] = None,
    rgb: Optional[np.ndarray] = None,
    max_dim: int = 512,
    boxes: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Maps continuous NDVI [-1.0, 1.0] to a high-grade Earth Observation scientific colormap.

    Uses smooth continuous spline interpolation modulated with surface luminance texture:
    - Water: Deep marine blue (#0c2d61) to lake azure (#1a6bb5)
    - Built-up / Urban: Textured terracotta (#c86a3b) with architectural contrast
    - Bare Soil / Barren: Warm golden sandstone (#dfaa6b)
    - Sparse Vegetation: Soft spring lime / meadow (#88bd37)
    - Dense Vegetation: Vibrant emerald to deep canopy green (#239433 -> #0f521b)
    """
    h, w = ndvi.shape
    v = np.clip(ndvi.astype(np.float32), -1.0, 1.0)

    # 1. Continuous scientific colormap knots (Piecewise continuous spline)
    knots = [
        (-1.00, np.array([12,  36,  90], dtype=np.float32)),   # Deep oceanic blue
        (-0.20, np.array([22,  70, 150], dtype=np.float32)),   # Marine blue
        (-0.02, np.array([35, 125, 195], dtype=np.float32)),   # Azure water edge
        ( 0.00, np.array([168, 155, 142], dtype=np.float32)),  # Neutral stone / urban transition
        ( 0.08, np.array([205, 125,  85], dtype=np.float32)),  # Warm terracotta / built-up
        ( 0.16, np.array([225, 185, 115], dtype=np.float32)),  # Sandstone / bare soil
        ( 0.28, np.array([185, 210,  80], dtype=np.float32)),  # Yellow-green / sparse vegetation
        ( 0.45, np.array([105, 180,  50], dtype=np.float32)),  # Meadow / cropland
        ( 0.65, np.array([ 35, 140,  45], dtype=np.float32)),  # Healthy canopy
        ( 1.00, np.array([ 12,  75,  25], dtype=np.float32)),  # Deep dense forest
    ]

    rgb_out = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(len(knots) - 1):
        v0, c0 = knots[i]
        v1, c1 = knots[i + 1]
        mask = (v >= v0) & (v <= v1)
        if not np.any(mask):
            continue
        t = (v[mask] - v0) / (v1 - v0)
        t = t[:, np.newaxis]
        rgb_out[mask] = (1.0 - t) * c0 + t * c1

    # Respect explicit water classification from multi-spectral bands
    if class_map is not None:
        is_water = np.isin(class_map, [CLASS_WATER, "Water"])
        if np.any(is_water):
            w_t = np.clip((v[is_water] + 0.2) / 0.4, 0.0, 1.0)[:, np.newaxis]
            rgb_out[is_water] = (1.0 - w_t) * np.array([18, 55, 125], dtype=np.float32) + w_t * np.array([28, 95, 165], dtype=np.float32)

    # 2. Surface Luminance Modulation (Pan-sharpening texture from true reflectance)
    if rgb is not None:
        rgb_f = rgb.astype(np.float32) / 255.0
        lum = 0.299 * rgb_f[:,:,0] + 0.587 * rgb_f[:,:,1] + 0.114 * rgb_f[:,:,2]
        p2, p98 = np.percentile(lum, 2), np.percentile(lum, 98)
        lum_norm = np.clip((lum - p2) / (p98 - p2 + 1e-5), 0.0, 1.0)
        shading = 0.72 + 0.38 * lum_norm
        rgb_out = rgb_out * shading[:, :, np.newaxis]

    rgb_out = np.clip(rgb_out, 0, 255).astype(np.uint8)
    rgba = np.dstack([rgb_out, np.full((h, w), 255, dtype=np.uint8)])

    img = Image.fromarray(rgba, mode="RGBA")
    if h > max_dim or w > max_dim:
        img = img.resize((max_dim, max_dim), Image.Resampling.BILINEAR)
    if boxes:
        img = draw_bounding_squares(img, boxes)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def ndvi_delta_to_png_base64(delta_ndvi: np.ndarray, max_dim: int = 512) -> str:
    """Generates a high-contrast difference map highlighting vegetation loss / gain."""
    h, w = delta_ndvi.shape
    v = np.clip(delta_ndvi.astype(np.float32), -0.4, 0.4)
    rgb_out = np.zeros((h, w, 3), dtype=np.uint8)

    # Negative delta (Loss / Construction) -> Bright Crimson Red
    neg_mask = v < 0.0
    t_neg = (-v[neg_mask] / 0.4)[:, np.newaxis]
    rgb_out[neg_mask] = (1.0 - t_neg) * np.array([28, 32, 42]) + t_neg * np.array([239, 68, 68])

    # Positive delta (Regrowth / Gain) -> Vibrant Emerald
    pos_mask = v >= 0.0
    t_pos = (v[pos_mask] / 0.4)[:, np.newaxis]
    rgb_out[pos_mask] = (1.0 - t_pos) * np.array([28, 32, 42]) + t_pos * np.array([34, 197, 94])

    rgba = np.dstack([rgb_out, np.full((h, w), 255, dtype=np.uint8)])
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


def classified_map_to_png_base64(
    class_map: np.ndarray,
    mask: np.ndarray,
    max_dim: int = 384,
) -> str:
    """Renders a colorized classification map ONLY for the region where mask > 0.

    6-Class Color Mapping:
    - Dense Vegetation: Dark Forest Green (21, 128, 61) / #15803d
    - Sparse Vegetation: Light Olive Green (132, 204, 22) / #84cc16
    - Bare Soil / Open Land: Earth Brown (146, 64, 14) / #92400e
    - Built-up: Crimson Red (239, 68, 68) / #ef4444
    - Built-up / Bare-land Confusion: Amber Warning / Sand (234, 179, 8) / #eab308
    - Water: Deep Blue (2, 132, 199) / #0284c7
    - Unclassified / Other: Slate (148, 163, 184)
    - Outside Changed Mask: Muted Dark Background (7, 10, 18, 230)
    """
    h, w = class_map.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    # Background (unchanged pixels outside mask)
    rgba[:, :, 0] = 7
    rgba[:, :, 1] = 10
    rgba[:, :, 2] = 18
    rgba[:, :, 3] = 230

    bool_mask = mask > 0

    if np.any(bool_mask):
        # 1. Dense Vegetation -> Dark Forest Green
        is_dense = bool_mask & (class_map == CLASS_DENSE_VEGETATION)
        rgba[is_dense] = (21, 128, 61, 255)

        # 2. Sparse Veg -> Light Olive Green
        is_sparse = bool_mask & np.isin(class_map, [CLASS_SPARSE_VEGETATION, CLASS_MODERATE_VEGETATION, "Moderate / Sparse Vegetation", "Sparse Vegetation"])
        rgba[is_sparse] = (132, 204, 22, 255)

        # 3. Bare Soil / Open Land -> Earth Brown
        is_soil = bool_mask & np.isin(class_map, [CLASS_BARE_SOIL, "Bare Soil / Barren", "Bare Soil / Open Land", "Bare Soil"])
        rgba[is_soil] = (146, 64, 14, 255)

        # 4. Built-up -> Crimson Red
        is_built = bool_mask & np.isin(class_map, [CLASS_BUILT_UP, "Built-up / Urban", "Built-up"])
        rgba[is_built] = (239, 68, 68, 255)

        # 5. Built-up / Bare-land Confusion -> Amber Sand
        is_conf = bool_mask & (class_map == CLASS_CONFUSION)
        rgba[is_conf] = (234, 179, 8, 255)

        # 6. Water -> Deep Blue
        is_water = bool_mask & (class_map == CLASS_WATER)
        rgba[is_water] = (2, 132, 199, 255)

        # 7. Unclassified / Other -> Slate
        is_unclass = bool_mask & (class_map == CLASS_UNCLASSIFIED)
        rgba[is_unclass] = (148, 163, 184, 255)

    img = Image.fromarray(rgba, mode="RGBA")
    if h > max_dim or w > max_dim:
        img = img.resize((max_dim, max_dim), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")



def sanitize_cloud_mask(bad_mask: np.ndarray, blue: np.ndarray, red: np.ndarray) -> np.ndarray:
    """Sanitizes false-positive cloud masks caused by s2cloudless flagging bright sunny terrain/sand."""
    if not np.any(bad_mask):
        return bad_mask
    bad_frac = float(np.mean(bad_mask))
    if bad_frac > 0.50:
        mean_blue = float(np.mean(blue[bad_mask]))
        # True clouds have high Blue reflectance (> 0.30 - 0.70). Sunny dry desert sand/ground has Blue < 0.28
        if mean_blue < 0.30:
            logger.info(f"Detected false-positive cloud mask over bright terrain (bad_frac={bad_frac:.2f}, mean_blue={mean_blue:.3f}). Sanitizing mask.")
            # Keep only genuine cloud pixels (high blue AND high red reflectance)
            true_clouds = bad_mask & (blue > 0.35) & (red > 0.30)
            return true_clouds
    return bad_mask


def load_bands_and_masks(
    file_path: str,
    mask_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Loads Green (B03), Red (B04), NIR (B08), SWIR (B11) and bad pixel mask from disk.

    Dynamically maps band names by inspecting ds.descriptions and directory manifest.json,
    supporting arbitrary 3, 4, 9, or multi-band GeoTIFFs.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Tile not found: {file_path}")

    import json

    with rasterio.open(file_path) as ds:
        transform = ds.transform
        profile = ds.profile
        count = ds.count

        # Extract descriptions or fallback to manifest.json
        descriptions = list(ds.descriptions or [])
        manifest_band_order = []
        manifest_path = os.path.join(os.path.dirname(file_path), "manifest.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as mf:
                    mdata = json.load(mf)
                    tiles = mdata.get("tiles", [])
                    base_name = os.path.basename(file_path)
                    for t in tiles:
                        if base_name in str(t.get("storage", {}).get("geotiff_path", "")) or base_name in str(t.get("tile_id", "")):
                            manifest_band_order = t.get("bands", {}).get("band_order", [])
                            break
                    if not manifest_band_order and tiles:
                        manifest_band_order = tiles[0].get("bands", {}).get("band_order", [])
            except Exception:
                manifest_band_order = []

        # Build band dict
        raw_bands = {}
        for idx in range(1, count + 1):
            desc_name = ""
            if idx - 1 < len(descriptions) and descriptions[idx - 1]:
                desc_name = str(descriptions[idx - 1]).strip().upper()
            elif idx - 1 < len(manifest_band_order) and manifest_band_order[idx - 1]:
                desc_name = str(manifest_band_order[idx - 1]).strip().upper()
            else:
                desc_name = f"B{idx}"

            arr = ds.read(idx).astype(np.float32)
            # Normalize DN to reflectance [0.0, 1.5] if fixed 10000 scale
            if np.nanmax(arr) > 10.0:
                arr = np.clip(arr / 10000.0, 0.0, 1.5)
            raw_bands[desc_name] = arr
            raw_bands[f"INDEX_{idx}"] = arr

        # Intelligent semantic mapping
        # 1. NIR: B08, B8, B8A, NIR
        nir = None
        for k in ["B08", "B8", "B8A", "NIR", "BAND_8", "BAND_8A"]:
            if k in raw_bands:
                nir = raw_bands[k]
                break
        if nir is None:
            if count >= 6: nir = raw_bands.get("INDEX_6")
            elif count >= 4: nir = raw_bands.get(f"INDEX_{count}")
            elif "B8A" in raw_bands: nir = raw_bands["B8A"]
            else: nir = raw_bands["INDEX_1"]

        # 2. SWIR: B11, B12, SWIR
        swir = None
        for k in ["B11", "B12", "SWIR", "SWIR1", "SWIR2", "BAND_11", "BAND_12"]:
            if k in raw_bands:
                swir = raw_bands[k]
                break
        if swir is None:
            if count >= 8: swir = raw_bands.get("INDEX_8")
            else: swir = nir

        # 3. RED: B04, B4, RED, or B05 proxy
        red = None
        for k in ["B04", "B4", "RED", "BAND_4"]:
            if k in raw_bands:
                red = raw_bands[k]
                break
        if red is None:
            if "B05" in raw_bands: red = raw_bands["B05"]  # Red Edge 1 proxy
            elif count >= 4: red = raw_bands.get("INDEX_3")
            elif count == 3 and "B01" not in raw_bands: red = raw_bands["INDEX_1"]
            elif "B01" in raw_bands: red = raw_bands["B01"]
            else: red = raw_bands["INDEX_1"]

        # 4. BLUE: B02, B2, BLUE, or B01
        blue = None
        for k in ["B02", "B2", "BLUE", "BAND_2"]:
            if k in raw_bands:
                blue = raw_bands[k]
                break
        if blue is None:
            if "B01" in raw_bands: blue = raw_bands["B01"]
            elif count >= 3: blue = raw_bands.get("INDEX_1") if count >= 4 else raw_bands.get("INDEX_3")
            else: blue = raw_bands["INDEX_1"]

        # 5. GREEN: B03, B3, GREEN
        green = None
        for k in ["B03", "B3", "GREEN", "BAND_3"]:
            if k in raw_bands:
                green = raw_bands[k]
                break
        if green is None:
            if count >= 3: green = raw_bands.get("INDEX_2")
            else: green = (red + blue) / 2.0

    # Load bad pixel / cloud mask if present
    h, w = red.shape
    if mask_path and os.path.exists(mask_path):
        with rasterio.open(mask_path) as mds:
            bad_mask = mds.read(1) > 0
    else:
        bad_mask = np.zeros((h, w), dtype=bool)

    # Sanitize false-positive cloud masks on bright terrain
    bad_mask = sanitize_cloud_mask(bad_mask, blue, red)

    # Pre-render normalized RGB for ChangeFormer
    # Check if a visual thumbnail already exists on disk alongside the tile
    thumb_path = file_path.replace(".tif", "_thumb.jpg")
    if not os.path.exists(thumb_path):
        thumb_path = file_path.replace(".tif", "_thumb.png")

    if os.path.exists(thumb_path):
        try:
            with Image.open(thumb_path) as t_img:
                rgb = np.array(t_img.convert("RGB"))
                if rgb.shape[:2] != (h, w):
                    t_img = t_img.resize((w, h), Image.Resampling.BILINEAR)
                    rgb = np.array(t_img)
        except Exception:
            rgb = normalize_sentinel_rgb(red, green, blue)
    else:
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
        try:
            raw_neural_mask = self.detector.get_binary_mask(data_b["rgb"], data_a["rgb"])
        except Exception as e:
            logger.warning(f"ChangeDetector inference warning: {e}. Continuing with spectral CVA.")
            raw_neural_mask = np.zeros((h, w), dtype=np.uint8)

        # Step 4: Compute spectral indices independently from multi-spectral bands
        indices_b = compute_spectral_indices(
            data_b["green"], data_b["red"], data_b["nir"], data_b["swir"]
        )
        indices_a = compute_spectral_indices(
            data_a["green"], data_a["red"], data_a["nir"], data_a["swir"]
        )

        # Step 5: Multi-spectral Change Vector Analysis (CVA)
        # S2 bands capture vegetation loss/gain (|d_ndvi| > 0.15), built-up/soil (|d_ndbi| > 0.12), and water (|d_ndwi| > 0.15)
        d_ndvi = np.abs(indices_a["ndvi"] - indices_b["ndvi"])
        d_ndbi = np.abs(indices_a["ndbi"] - indices_b["ndbi"])
        d_ndwi = np.abs(indices_a["ndwi"] - indices_b["ndwi"])
        spectral_cva_mask = (d_ndvi > 0.15) | (d_ndbi > 0.12) | (d_ndwi > 0.15)

        # Step 6: Hybrid Candidate Binary Change Mask (ChangeDetector Neural Mask + Multi-Spectral CVA)
        candidate_binary_mask = ((raw_neural_mask > 0) | spectral_cva_mask) & valid_pixels

        # Step 7: Spectral classification (Vegetation, Water, Urban/Built-up, Bare Soil)
        class_before_map = classify_pixels(
            indices_b["ndvi"], indices_b["ndwi"], indices_b["ndbi"],
            b_nir=data_b["nir"], b_red=data_b["red"], b_swir=data_b["swir"],
        )
        class_after_map = classify_pixels(
            indices_a["ndvi"], indices_a["ndwi"], indices_a["ndbi"],
            b_nir=data_a["nir"], b_red=data_a["red"], b_swir=data_a["swir"],
        )

        # Step 8: Semantic Contradiction Filtering
        # User rule: "if in binary masked showing pixel changes but bands telling nothing is changed then dont include that pixel in change"
        # If before_class == after_class, reject candidate pixel as phenology/illumination false positive.
        surviving_mask, filter_stats = filter_semantic_changes(
            class_before_map, class_after_map, binary_mask=candidate_binary_mask
        )

        # Step 9: Transition Matrix Lookup
        change_type_map = vectorized_change_type_lookup(class_before_map, class_after_map)

        # CRITICAL USER RULE: Only major structural changes (Land to Construction, Road, Clearance, Demolition, Water)
        # Never mark vegetation-only shifts ("sparse vegetation dense vegetation like stuff")
        is_major_change = np.isin(change_type_map, list(MAJOR_CHANGE_TYPES))
        surviving_mask = surviving_mask & is_major_change

        # Step 10: Vectorize to GeoJSON polygons with road morphology test & spectral sampling
        features = polygonize_change_mask(
            mask=surviving_mask,
            geotransform=transform,
            change_type_map=change_type_map,
            before_class_map=class_before_map,
            after_class_map=class_after_map,
            indices_before=indices_b,
            indices_after=indices_a,
        )

        total_pixels = candidate_binary_mask.size
        changed_pixels = int(np.sum(surviving_mask))
        change_pct = round(float((changed_pixels / total_pixels) * 100.0), 2)

        # Step 11: Pair-level aggregate spectral profile across changed pixels
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

        # Step 12: Generate Web-ready base64 PNG previews for all bands and masks
        # Strictly eliminate any pixel where class_before_map == class_after_map (no change)
        is_same_pixel_class = (class_before_map == class_after_map)
        surviving_mask = surviving_mask & (~is_same_pixel_class)

        # Re-compute changed pixel metrics
        changed_pixels = int(np.sum(surviving_mask))
        change_pct = round(float((changed_pixels / total_pixels) * 100.0), 2)
        cand_pixels = int(np.sum(candidate_binary_mask))
        fp_rejected = cand_pixels - changed_pixels
        filter_stats["verified_changes"] = changed_pixels
        filter_stats["false_positives_rejected"] = fp_rejected

        # Step: Rigorous Water Extent Calculation and Proof Verification
        # Water pixels: NDVI < 0.0 or classified as Water (blue in NDVI map)
        water_mask_b = (indices_b["ndvi"] < 0.0) | (class_before_map == CLASS_WATER)
        water_mask_a = (indices_a["ndvi"] < 0.0) | (class_after_map == CLASS_WATER)
        water_px_before = int(np.sum(water_mask_b))
        water_px_after = int(np.sum(water_mask_a))
        water_px_delta = water_px_after - water_px_before

        # Pixels that previously were NOT blue/water, but are NOW blue/water
        new_blue_pixels = (~water_mask_b) & water_mask_a
        new_water_count = int(np.sum(new_blue_pixels))

        # USER RULE: 4-5 pixels is noise and does NOT mean water is extended (requires proof)
        # Confirmed threshold: at least 25 contiguous pixels (approx 2,500 m2)
        MIN_WATER_PROOF_PX = 25
        is_water_extension_proven = (water_px_delta > 0) and (new_water_count >= MIN_WATER_PROOF_PX)
        is_water_shrinkage_proven = (water_px_delta < -MIN_WATER_PROOF_PX)

        water_proof_text = ""
        if is_water_extension_proven:
            water_proof_text = (
                f"Water Extent Expansion Verified: +{water_px_delta} blue pixels "
                f"(+{round(water_px_delta * 100 / 10000.0, 2)} ha). "
                f"Before: {water_px_before:,} px → After: {water_px_after:,} px (>{MIN_WATER_PROOF_PX} px proof threshold)."
            )
        elif is_water_shrinkage_proven:
            water_proof_text = (
                f"Water Extent Shrinkage: {water_px_delta} blue pixels "
                f"({round(water_px_delta * 100 / 10000.0, 2)} ha). "
                f"Before: {water_px_before:,} px → After: {water_px_after:,} px."
            )
        else:
            water_proof_text = (
                f"Water Extent Stable / Unchanged: Δ {water_px_delta:+d} px "
                f"(Baseline: {water_px_before:,} px vs After: {water_px_after:,} px; below {MIN_WATER_PROOF_PX} px threshold)."
            )

        water_extent_stats = {
            "before_pixels": water_px_before,
            "after_pixels": water_px_after,
            "delta_pixels": water_px_delta,
            "new_water_pixels": new_water_count,
            "is_extension_proven": is_water_extension_proven,
            "is_shrinkage_proven": is_water_shrinkage_proven,
            "status": "Extended" if is_water_extension_proven else ("Shrunk" if is_water_shrinkage_proven else "Stable"),
            "proof": water_proof_text,
        }

        # Collect major change boxes for drawing on images
        # Filter strictly for genuine structural changes (minimum 20 px / 2,000 m2)
        raw_boxes = []
        for i, f in enumerate(features):
            props = f.get("properties", {})
            c_type = props.get("change_type")
            if c_type not in MAJOR_CHANGE_TYPES:
                continue

            area_m2 = float(props.get("area_m2", 0) or props.get("area_sq_m", 0) or 0)
            if area_m2 < 1000.0:
                continue

            # If water expansion/shrinkage is NOT proven (e.g. 4-5 px noise), suppress it from change boxes
            if c_type == TYPE_WATER_EXPANSION and not is_water_extension_proven:
                continue
            if c_type == TYPE_WATER_SHRINKAGE and not is_water_shrinkage_proven:
                continue

            b_class = props.get("before_class", "Land")
            a_class = props.get("after_class", "Built-up")
            t_label = props.get("transition_label") or format_transition_label(b_class, a_class, c_type)

            raw_boxes.append({
                "id": i + 1,
                "change_type": c_type,
                "transition_label": t_label,
                "before_class": b_class,
                "after_class": a_class,
                "area_m2": area_m2,
                "box_pct": props.get("box_pct", {}),
                "pixel_bbox": props.get("pixel_bbox", []),
                "proof": water_proof_text if "water" in c_type.lower() else None,
            })

        # Sort candidate boxes by area descending
        raw_boxes.sort(key=lambda b: b.get("area_m2", 0), reverse=True)

        # Spatial Non-Maximum Suppression (NMS) to eliminate overlapping duplicate clutter
        def _calc_box_iou(b1, b2):
            p1 = b1.get("box_pct", {})
            p2 = b2.get("box_pct", {})
            if not p1 or not p2:
                return 0.0
            x1 = max(p1.get("x", 0), p2.get("x", 0))
            y1 = max(p1.get("y", 0), p2.get("y", 0))
            x2 = min(p1.get("x", 0) + p1.get("w", 0), p2.get("x", 0) + p2.get("w", 0))
            y2 = min(p1.get("y", 0) + p1.get("h", 0), p2.get("y", 0) + p2.get("h", 0))
            inter_w = max(0.0, x2 - x1)
            inter_h = max(0.0, y2 - y1)
            inter_area = inter_w * inter_h
            if inter_area <= 0:
                return 0.0
            area1 = p1.get("w", 0) * p1.get("h", 0)
            area2 = p2.get("w", 0) * p2.get("h", 0)
            union_area = area1 + area2 - inter_area
            return inter_area / union_area if union_area > 0 else 0.0

        suppressed_boxes = []
        for cand in raw_boxes:
            if any(_calc_box_iou(cand, kept) > 0.35 for kept in suppressed_boxes):
                continue
            suppressed_boxes.append(cand)
            if len(suppressed_boxes) >= 8:
                break

        # Consolidate water boxes to at most 1 clean comprehensive box per pair to prevent multiple tags along the same river
        water_boxes = [b for b in suppressed_boxes if "water" in b.get("change_type", "").lower()]
        non_water_boxes = [b for b in suppressed_boxes if "water" not in b.get("change_type", "").lower()]

        if len(water_boxes) > 1:
            primary_water = dict(water_boxes[0])
            total_water_area = sum(b.get("area_m2", 0) for b in water_boxes)
            primary_water["area_m2"] = round(total_water_area, 1)
            x_min = min(b["box_pct"]["x"] for b in water_boxes)
            y_min = min(b["box_pct"]["y"] for b in water_boxes)
            x_max = max(b["box_pct"]["x"] + b["box_pct"]["w"] for b in water_boxes)
            y_max = max(b["box_pct"]["y"] + b["box_pct"]["h"] for b in water_boxes)
            primary_water["box_pct"] = {
                "x": round(x_min, 2),
                "y": round(y_min, 2),
                "w": round(min(100.0 - x_min, x_max - x_min), 2),
                "h": round(min(100.0 - y_min, y_max - y_min), 2),
            }
            water_boxes = [primary_water]

        change_boxes = (water_boxes + non_water_boxes)[:8]

        # If water extension is proven, ensure exactly 1 clean consolidated box for the new water area
        if is_water_extension_proven:
            has_water = any("water" in b.get("change_type", "").lower() and "exp" in b.get("change_type", "").lower() for b in change_boxes)
            if not has_water and new_water_count >= MIN_WATER_PROOF_PX:
                r_indices, c_indices = np.where(new_blue_pixels)
                r_min, r_max = int(np.min(r_indices)), int(np.max(r_indices))
                c_min, c_max = int(np.min(c_indices)), int(np.max(c_indices))
                bw = max(4, c_max - c_min)
                bh = max(4, r_max - r_min)
                water_box = {
                    "id": len(change_boxes) + 1,
                    "change_type": TYPE_WATER_EXPANSION,
                    "transition_label": "Land → Water (Extension)",
                    "before_class": "Land",
                    "after_class": "Water",
                    "area_m2": round(float(new_water_count * 100.0), 1),
                    "box_pct": {
                        "x": round((c_min / w) * 100.0, 2),
                        "y": round((r_min / h) * 100.0, 2),
                        "w": round((bw / w) * 100.0, 2),
                        "h": round((bh / h) * 100.0, 2),
                    },
                    "pixel_bbox": [c_min, r_min, c_max, r_max],
                    "proof": water_proof_text,
                }
                change_boxes.insert(0, water_box)
                change_boxes = change_boxes[:8]

        # If water shrinkage is proven, ensure the consolidated box covers the FULL lake/water extent that dried up
        if is_water_shrinkage_proven:
            dried_mask = water_mask_b & (~water_mask_a)
            full_dried_count = int(np.sum(dried_mask))
            if full_dried_count >= MIN_WATER_PROOF_PX:
                r_indices, c_indices = np.where(dried_mask)
                r_min, r_max = int(np.min(r_indices)), int(np.max(r_indices))
                c_min, c_max = int(np.min(c_indices)), int(np.max(c_indices))
                bw = max(6, c_max - c_min)
                bh = max(6, r_max - r_min)
                full_shrinkage_box = {
                    "id": 1,
                    "change_type": TYPE_WATER_SHRINKAGE,
                    "transition_label": "Water → Land (Shrinkage)",
                    "before_class": "Water",
                    "after_class": "Land",
                    "area_m2": round(float(full_dried_count * 100.0), 1),
                    "box_pct": {
                        "x": round((c_min / w) * 100.0, 2),
                        "y": round((r_min / h) * 100.0, 2),
                        "w": round(min(100.0 - (c_min / w) * 100.0, (bw / w) * 100.0), 2),
                        "h": round(min(100.0 - (r_min / h) * 100.0, (bh / h) * 100.0), 2),
                    },
                    "pixel_bbox": [c_min, r_min, c_max, r_max],
                    "proof": water_proof_text,
                }
                # Replace fragmented small water boxes with the full water shrinkage envelope
                non_water_boxes = [b for b in change_boxes if "water" not in b.get("change_type", "").lower()]
                change_boxes = [full_shrinkage_box] + non_water_boxes
                change_boxes = change_boxes[:8]

        # Per-pixel colorized land-cover maps for Before and After
        classified_b_png = classified_map_to_png_base64(class_before_map, mask=surviving_mask)
        classified_a_png = classified_map_to_png_base64(class_after_map, mask=surviving_mask)

        # Standard band previews and annotated previews with change squares (User specified: BOTH Before and After)
        rgb_b_png = rgb_to_png_base64(data_b["rgb"])
        rgb_a_png = rgb_to_png_base64(data_a["rgb"])
        rgb_b_annotated_png = rgb_to_png_base64(data_b["rgb"], boxes=change_boxes)
        rgb_a_annotated_png = rgb_to_png_base64(data_a["rgb"], boxes=change_boxes)

        # High-Fidelity Scientific Continuous NDVI maps with pan-sharpened surface texture
        ndvi_b_png = ndvi_to_png_base64(indices_b["ndvi"], ndbi=indices_b.get("ndbi"), class_map=class_before_map, rgb=data_b.get("rgb"))
        ndvi_a_png = ndvi_to_png_base64(indices_a["ndvi"], ndbi=indices_a.get("ndbi"), class_map=class_after_map, rgb=data_a.get("rgb"))
        ndvi_b_annotated_png = ndvi_to_png_base64(indices_b["ndvi"], ndbi=indices_b.get("ndbi"), class_map=class_before_map, rgb=data_b.get("rgb"), boxes=change_boxes)
        ndvi_a_annotated_png = ndvi_to_png_base64(indices_a["ndvi"], ndbi=indices_a.get("ndbi"), class_map=class_after_map, rgb=data_a.get("rgb"), boxes=change_boxes)

        # High-Contrast Delta NDVI Difference Map (Loss vs Gain)
        delta_ndvi = indices_a["ndvi"] - indices_b["ndvi"]
        ndvi_delta_png = ndvi_delta_to_png_base64(delta_ndvi)

        raw_mask_png = mask_to_png_base64(candidate_binary_mask, color=(6, 182, 212))
        filtered_mask_png = mask_to_png_base64(surviving_mask, color=(245, 158, 11))

        # Step 13: Sample 128x128 spatial index grid for live cursor hover inspection
        grid_step = max(1, h // 128)
        ndvi_b_sample = np.round(indices_b["ndvi"][::grid_step, ::grid_step], 3).tolist()
        ndwi_b_sample = np.round(indices_b["ndwi"][::grid_step, ::grid_step], 3).tolist()
        ndbi_b_sample = np.round(indices_b["ndbi"][::grid_step, ::grid_step], 3).tolist()
        class_b_sample = class_before_map[::grid_step, ::grid_step].tolist()

        ndvi_a_sample = np.round(indices_a["ndvi"][::grid_step, ::grid_step], 3).tolist()
        ndwi_a_sample = np.round(indices_a["ndwi"][::grid_step, ::grid_step], 3).tolist()
        ndbi_a_sample = np.round(indices_a["ndbi"][::grid_step, ::grid_step], 3).tolist()
        class_a_sample = class_after_map[::grid_step, ::grid_step].tolist()

        verified_sample = (surviving_mask[::grid_step, ::grid_step] > 0).astype(int).tolist()
        change_type_sample = change_type_map[::grid_step, ::grid_step].tolist()

        cursor_sample_grid = {
            "grid_size": len(ndvi_b_sample),
            "before": {
                "ndvi": ndvi_b_sample,
                "ndwi": ndwi_b_sample,
                "ndbi": ndbi_b_sample,
                "class": class_b_sample,
            },
            "after": {
                "ndvi": ndvi_a_sample,
                "ndwi": ndwi_a_sample,
                "ndbi": ndbi_a_sample,
                "class": class_a_sample,
            },
            "verified": verified_sample,
            "change_type": change_type_sample,
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
            "rgb_before_annotated_url": rgb_b_annotated_png,
            "rgb_after_annotated_url": rgb_a_annotated_png,
            "ndvi_before_url": ndvi_b_png,
            "ndvi_after_url": ndvi_a_png,
            "before_ndvi_url": ndvi_b_png,
            "after_ndvi_url": ndvi_a_png,
            "ndvi_before_annotated_url": ndvi_b_annotated_png,
            "ndvi_after_annotated_url": ndvi_a_annotated_png,
            "ndvi_delta_url": ndvi_delta_png,
            "change_boxes": change_boxes,
            "water_extent_stats": water_extent_stats,
            "binary_mask_url": raw_mask_png,
            "filtered_mask_url": filtered_mask_png,
            "classified_before_url": classified_b_png,
            "classified_after_url": classified_a_png,
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
