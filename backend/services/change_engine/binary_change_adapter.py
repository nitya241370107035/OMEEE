"""Binary Change Adapter for AeroLens Change Engine.

Phase B1: Thin pass-through wrapper around MTKD-ChangeFormer (change_detector_module).
Extracts and normalizes RGB channels from Sentinel-2 GeoTIFF tiles and runs
binary change detection.
"""
import os
import logging
from typing import Optional, Union
import numpy as np
from PIL import Image
import rasterio

logger = logging.getLogger(__name__)

_DETECTOR_INSTANCE = None


def get_detector():
    """Singleton getter for ChangeDetector model."""
    global _DETECTOR_INSTANCE
    if _DETECTOR_INSTANCE is None:
        from backend.change_detector_module.detector import ChangeDetector
        logger.info("Initializing MTKD-ChangeFormer ChangeDetector...")
        _DETECTOR_INSTANCE = ChangeDetector()
        logger.info("MTKD-ChangeFormer ChangeDetector successfully initialized.")
    return _DETECTOR_INSTANCE


def normalize_sentinel_rgb(
    band_red: np.ndarray,
    band_green: np.ndarray,
    band_blue: np.ndarray,
    p_min: float = 2.0,
    p_max: float = 98.0,
) -> np.ndarray:
    """Normalizes multi-spectral Red, Green, Blue bands to (H, W, 3) uint8 RGB array.

    Handles Sentinel-2 surface reflectance (e.g. 0-10000 range or 0.0-1.0 float).
    """
    rgb = np.stack([band_red, band_green, band_blue], axis=-1).astype(np.float32)

    # Compute robust percentiles per channel
    out_rgb = np.zeros_like(rgb, dtype=np.uint8)
    for c in range(3):
        channel = rgb[:, :, c]
        valid = channel[np.isfinite(channel)]
        if valid.size == 0:
            continue
        v_min = np.percentile(valid, p_min)
        v_max = np.percentile(valid, p_max)
        if v_max <= v_min:
            v_max = v_min + 1e-3
        scaled = (channel - v_min) / (v_max - v_min) * 255.0
        out_rgb[:, :, c] = np.clip(scaled, 0, 255).astype(np.uint8)

    return out_rgb


def load_tile_rgb(tile_path: str) -> np.ndarray:
    """Loads a GeoTIFF tile and extracts (H, W, 3) uint8 RGB.

    Sentinel-2 AeroLens band order:
    B01, B02 (Blue=2), B03 (Green=3), B04 (Red=4), B05, B08 (NIR=6), B8A, B11 (SWIR=8), B12
    """
    if not os.path.exists(tile_path):
        raise FileNotFoundError(f"Tile file not found: {tile_path}")

    with rasterio.open(tile_path) as ds:
        # If standard 3-band RGB
        if ds.count == 3:
            r = ds.read(1)
            g = ds.read(2)
            b = ds.read(3)
        elif ds.count >= 4:
            # 9-band Sentinel-2 tile
            r = ds.read(4)  # B04 Red
            g = ds.read(3)  # B03 Green
            b = ds.read(2)  # B02 Blue
        else:
            data = ds.read(1)
            r, g, b = data, data, data

    return normalize_sentinel_rgb(r, g, b)


class BinaryChangeAdapter:
    """Pass-through adapter to execute binary change detection."""

    def __init__(self, detector=None):
        self._detector = detector

    @property
    def detector(self):
        if self._detector is None:
            self._detector = get_detector()
        return self._detector

    def predict_binary_mask(
        self,
        tile_before: Union[str, np.ndarray, Image.Image],
        tile_after: Union[str, np.ndarray, Image.Image],
    ) -> np.ndarray:
        """Runs ChangeFormer model to obtain raw (H, W) binary change mask.

        Args:
            tile_before: File path to GeoTIFF / image, or uint8 (H, W, 3) array
            tile_after: File path to GeoTIFF / image, or uint8 (H, W, 3) array

        Returns:
            np.ndarray: Binary mask (H, W) where 0 = No Change, 1 = Change
        """
        # Load before RGB if file path
        if isinstance(tile_before, str):
            if tile_before.lower().endswith((".tif", ".tiff")):
                rgb_before = load_tile_rgb(tile_before)
            else:
                rgb_before = np.array(Image.open(tile_before).convert("RGB"))
        else:
            rgb_before = tile_before

        # Load after RGB if file path
        if isinstance(tile_after, str):
            if tile_after.lower().endswith((".tif", ".tiff")):
                rgb_after = load_tile_rgb(tile_after)
            else:
                rgb_after = np.array(Image.open(tile_after).convert("RGB"))
        else:
            rgb_after = tile_after

        # Execute ChangeDetector inference
        mask = self.detector.get_binary_mask(rgb_before, rgb_after)
        return (mask > 0).astype(np.uint8)
