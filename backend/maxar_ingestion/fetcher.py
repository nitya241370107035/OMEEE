"""
backend/maxar_ingestion/fetcher.py
==================================
Sub-meter Optical Satellite Imagery Fetcher for Maxar WorldView via Wayback WMTS.
Handles coordinate conversions, multi-threaded tile fetching, mosaic stitching,
and georeferenced bounding box cropping.
"""

import io
import math
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Dict, Optional, Any
from pathlib import Path
import numpy as np
import requests
from PIL import Image

logger = logging.getLogger("MaxarFetcher")

# Curated ESRI / Maxar Wayback Release Mapping (Archival -> Contemporary)
WAYBACK_RELEASES: Dict[int, Dict[str, str]] = {
    2020: {"release_id": "6049", "date": "2020-08-12", "label": "August 2020 Baseline"},
    2021: {"release_id": "10020", "date": "2021-06-16", "label": "Mid-2021 Observation"},
    2022: {"release_id": "14458", "date": "2022-10-19", "label": "Late-2022 Snapshot"},
    2023: {"release_id": "18092", "date": "2023-09-13", "label": "Late-2023 Observation"},
    2024: {"release_id": "20112", "date": "2024-03-20", "label": "Spring 2024 Snapshot"},
    2025: {"release_id": "24200", "date": "2025-05-14", "label": "Mid-2025 Snapshot"},
    2026: {"release_id": "26334", "date": "2026-08-05", "label": "August 2026 Contemporary"},
}

DEFAULT_ZOOM = 17  # Sub-meter / high-res optical imagery (~1.19m/px at equator; ~0.6m at zoom 18)

# Safety cap: max WMTS tiles to download in one call (prevents OOM for city-scale AOIs)
MAX_SAFE_WMTS_TILES = 800


def auto_select_zoom(bbox: Tuple[float, float, float, float], requested_zoom: Optional[int] = None) -> int:
    """
    Automatically selects the best zoom level balancing high optical clarity and memory safety.
    Ensures crisp sub-meter / near-meter Maxar imagery without blurry downscaling:

      - Tactical / Base areas (<= ~5km / 0.05deg): zoom 18 (~0.6m/px high-res)
      - City sectors / Ports (<= ~15km / 0.15deg):  zoom 17 (~1.2m/px crisp optical)
      - Regional metropolitan (<= ~35km / 0.35deg): zoom 16 (~2.4m/px standard)
      - Very large multi-city AOIs (> 0.35deg):      zoom 15 (~4.8m/px)
    """
    if requested_zoom and requested_zoom > 0 and requested_zoom != DEFAULT_ZOOM:
        candidate = max(15, min(18, requested_zoom))
    else:
        min_lon, min_lat, max_lon, max_lat = bbox
        span = max(abs(max_lon - min_lon), abs(max_lat - min_lat))
        if span <= 0.05:
            candidate = 18
        elif span <= 0.15:
            candidate = 17
        elif span <= 0.35:
            candidate = 16
        else:
            candidate = 15

    # Safety check: ensure total WMTS tiles stays within memory limit
    while candidate > 15:
        x_min, y_max = deg2num(bbox[1], bbox[0], candidate)
        x_max, y_min = deg2num(bbox[3], bbox[2], candidate)
        total_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
        if total_tiles <= MAX_SAFE_WMTS_TILES:
            break
        logger.info(f"Zoom {candidate} needs {total_tiles} WMTS tiles (>{MAX_SAFE_WMTS_TILES}), stepping down to zoom {candidate-1}")
        candidate -= 1

    logger.info(f"auto_select_zoom: bbox span={max(abs(bbox[2]-bbox[0]), abs(bbox[3]-bbox[1])):.3f}deg → zoom {candidate}")
    return candidate


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
    """Converts latitude and longitude in degrees to Web Mercator tile x, y."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def num2deg(xtile: int, ytile: int, zoom: int) -> Tuple[float, float]:
    """Converts Web Mercator tile x, y to latitude, longitude (NW corner)."""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def get_closest_release(year_or_date: Any) -> Dict[str, str]:
    """Matches a requested year or ISO date string to the nearest available Wayback release."""
    target_year = 2026
    if isinstance(year_or_date, int):
        target_year = year_or_date
    elif isinstance(year_or_date, str):
        try:
            target_year = int(year_or_date.split("-")[0])
        except Exception:
            target_year = 2026

    available_years = sorted(WAYBACK_RELEASES.keys())
    closest_year = min(available_years, key=lambda y: abs(y - target_year))
    return WAYBACK_RELEASES[closest_year]


def fetch_single_wmts_tile(
    release_id: str,
    zoom: int,
    x: int,
    y: int,
    session: Optional[requests.Session] = None,
    max_retries: int = 3
) -> Optional[Tuple[int, int, Image.Image]]:
    """Fetches a single 256x256 WMTS tile from the Wayback endpoint."""
    url = f"https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{release_id}/{zoom}/{y}/{x}"
    s = session or requests.Session()
    headers = {"User-Agent": "Antigravity-Satellite-Analysis-Platform/2.0"}

    for attempt in range(max_retries):
        try:
            resp = s.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                return (x, y, img)
            elif resp.status_code == 404:
                return (x, y, Image.new("RGB", (256, 256), color=(20, 20, 20)))
        except Exception as e:
            if attempt == max_retries - 1:
                logger.warning(f"Failed to fetch tile {zoom}/{y}/{x} after {max_retries} attempts: {e}")
            time.sleep(0.3 * (attempt + 1))
    return None


def fetch_stitched_bbox_imagery(
    bbox: Tuple[float, float, float, float],
    release_id: str,
    zoom: int = DEFAULT_ZOOM,
    max_workers: int = 8
) -> Image.Image:
    """
    Fetches and stitches all WMTS tiles covering the bounding box (min_lon, min_lat, max_lon, max_lat),
    then precisely crops to the bbox boundaries.
    """
    min_lon, min_lat, max_lon, max_lat = bbox

    x_min, y_max = deg2num(min_lat, min_lon, zoom)
    x_max, y_min = deg2num(max_lat, max_lon, zoom)

    cols = x_max - x_min + 1
    rows = y_max - y_min + 1

    logger.info(f"Fetching Wayback release {release_id} at zoom {zoom} for bbox {bbox} ({cols} cols x {rows} rows = {cols*rows} tiles)")

    canvas = Image.new("RGB", (cols * 256, rows * 256), color=(15, 23, 42))

    # Multi-threaded download
    tile_coords = [
        (x, y)
        for x in range(x_min, x_max + 1)
        for y in range(y_min, y_max + 1)
    ]

    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_coord = {
                executor.submit(fetch_single_wmts_tile, release_id, zoom, x, y, session): (x, y)
                for x, y in tile_coords
            }
            for future in as_completed(future_to_coord):
                result = future.result()
                if result:
                    x, y, tile_img = result
                    c = x - x_min
                    r = y - y_min
                    canvas.paste(tile_img, (c * 256, r * 256))

    # Precise crop to bounding box
    nw_lat, nw_lon = num2deg(x_min, y_min, zoom)
    se_lat, se_lon = num2deg(x_max + 1, y_max + 1, zoom)

    canvas_w = canvas.width
    canvas_h = canvas.height
    left = int((min_lon - nw_lon) / (se_lon - nw_lon) * canvas_w)
    right = int((max_lon - nw_lon) / (se_lon - nw_lon) * canvas_w)
    top = int((nw_lat - max_lat) / (nw_lat - se_lat) * canvas_h)
    bottom = int((nw_lat - min_lat) / (nw_lat - se_lat) * canvas_h)

    left = max(0, min(canvas_w - 1, left))
    right = max(left + 1, min(canvas_w, right))
    top = max(0, min(canvas_h - 1, top))
    bottom = max(top + 1, min(canvas_h, bottom))

    cropped = canvas.crop((left, top, right, bottom))
    return cropped
