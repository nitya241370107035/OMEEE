# Ingestion Pipeline (`backend/ingestion`)

Full Phase 1 AOI-to-Tiles pipeline that converts an input GeoJSON polygon into cloud/shadow-cleaned, normalized 512×512 tiles with georeferencing and metadata manifests.

---

## Architecture & Sub-Phases

- **Phase 1.0 (`input_validator.py`)**: Strict GeoJSON validation using Shapely (`is_valid`), rejection of self-intersecting / unclosed geometries, bounding box calculation, and ground area calculation in km².
- **Phase 1.1 (`stac_search.py`)**: Queries STAC catalog (AWS Sentinel-2 L2A COGs) with bbox, date range, and coarse cloud filtering. Extracts real scene metadata (`scene_id`, `acquisition_date`, `sensor`, `crs`, `assets`).
- **Phase 1.2 (`canvas.py`)**: Downloads RGB + NIR bands, reprojects to `EPSG:4326`, and clips to AOI bounding box with a 5% safety buffer margin.
- **Phase 1.3 (`masking.py`)**: Executes multi-spectral cloud detection (brightness, whiteness, NIR) and proximity-based shadow masking on the full canvas. Normalizes good pixels using 2nd–98th percentile stretch excluding masked bad pixels.
- **Phase 1.4 & 1.5 (`tiler.py`)**:
  - Sliding window tiling with configurable `GROUND_CROP_SIZE`:
    - `GROUND_CROP_SIZE = 512`: True 10m/px resolution (5.12km footprint, native ML accuracy).
    - `GROUND_CROP_SIZE = 25`: Zoomed view (≈250m patch, upsampled to 512×512 via Lanczos).
  - Exact polygon filtering (`tile_footprint.intersects(aoi_polygon)`).
  - Calculates real per-tile `cloud_pct` from the bad pixel mask.
- **Phase 1.6 (`storage.py`)**: Writes GeoTIFFs (`.tif`), companion preview thumbnails (`.jpg`), and `manifest.json` under `data/tiles/{region_id}/{date}/` with honest `quality_confidence = 1.0 - cloud_pct`.
- **Phase 1 Orchestrator (`pipeline.py`)**: CLI and Python API (`run_aoi_pipeline`) coordinating all phases.

---

## CLI Usage

```powershell
# Standard 512x512 native resolution
python backend/ingestion/pipeline.py --geojson tests/sample_aoi.geojson --region-id delhi_ cantonment --ground-crop-size 512

# Zoomed mode (25px ground crop upsampled to 512x512)
python backend/ingestion/pipeline.py --geojson tests/sample_aoi.geojson --region-id delhi_zoomed --ground-crop-size 25

# Offline mode with synthetic scene fallback
python backend/ingestion/pipeline.py --geojson tests/sample_aoi.geojson --region-id test_region --offline
```

---

## Testing

Run the automated test suite:
```powershell
python -m pytest tests/test_aoi_pipeline.py -v
```
