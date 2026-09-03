# Semantic Retrieval & Multi-Temporal Change Analysis of Satellite Imagery

> **Problem Statement Number:** SIH-26227  
> **Organization:** Ministry of Defence (MoD) — Indian Army (DGIS)  
> **Category:** Software | **Theme:** Space Technology  
> **Repository:** [`varun-ai69/Semantic-Image-Retrieval-and-Multi-temporal-Stallelite-Image-Analysis`](https://github.com/varun-ai69/Semantic-Image-Retrieval-and-Multi-temporal-Stallelite-Image-Analysis)

---

## Executive Summary

Modern defense analysts require the capability to query satellite imagery archives **by semantic meaning** (e.g. *"find airstrips with visible hangars near river bends"*) rather than relying strictly on geographic coordinates or metadata dates. Additionally, the system must automatically flag **true multi-temporal changes** (such as new construction, land clearance, road development, or water-extent shifts) while suppressing false alarms caused by cloud coverage, seasonal variations, sun angles, or spatial misalignment.

This platform provides an end-to-end, **100% on-premises, network-isolated solution** utilizing fine-tuned RemoteCLIP vision-language encoders, Qdrant vector indexing, PostGIS spatial caching, Siamese/BIT change detection, and a unified intent-routing agent.

---

## 🛰️ Project Pipeline Architecture

```text
Raw Sentinel-2 Scene / AOI Polygon (Hyderabad & Eastern Ladakh LAC)
        ↓ [Phase 1]
STAC Scene Search & 10-Band Canvas Assembly (EPSG:4326)
        ↓ [Phase 2]
s2cloudless Machine Learning Cloud Probability (0.0 to 1.0)
        ↓
Spectral & Directional Cloud / Shadow Masking
        ↓
Combined Bad-Pixel Quality Mask
        ↓
Mask-Aware Percentile Radiometric Normalization (2nd–98.5th Percentile)
        ↓ [Phase 5]
Cloud-Matting Surface Reflectance Recovery (Clear vs Thin vs Thick Cloud)
        ↓
Reconstruction / Provenance Mask (0=Observed, 1=Recovered, 3=Unresolved)
        ↓
Strict 512 × 512 Ground Tile Slicing & Quality Gating
        ↓ [Phase 3]
Dataset Registry & Multi-Epoch Manifest Packaging
        ↓
Streamlit Defense Intelligence Mini UI/UX & QGIS Inspection
```

---

## 📋 Preprocessing Phases Overview

### Phase 0 — Foundation & Isolated Infrastructure
* Modular Docker Compose stack orchestrating local PostGIS 15+, Qdrant Vector DB, and MinIO storage.
* SQL schemas establishing strict spatial indices (`GEOMETRY(Polygon, 4326)`), audit lineage, and analyst decision logs.

### Phase 1 — AOI to Satellite Tiles (Hyderabad Baseline)
* Validates arbitrary GeoJSON polygons (e.g., [`data/custom_aoi.geojson`](data/custom_aoi.geojson)).
* Queries Microsoft Planetary Computer / AWS STAC catalogs with deterministic offline fallback.
* Assembles calibrated 10-band canvas and slices strict $512 	imes 512$ GeoTIFF tiles with exact polygon filtering.

### Phase 2 — Cloud Masking, Shadow Masking & Normalization
* **s2cloudless Integration**: Evaluates pixel-wise cloud probability across all 10 Sentinel-2 bands using trained gradient-boosted trees.
* **Directional Shadow Detection**: Casts cloud neighborhood rays and thresholds NIR dips to identify cloud shadows.
* **Combined Bad-Pixel Mask**: Unifies clouds and shadows into a single binary gating mask.
* **Mask-Aware Percentile Normalization**: Radiometrically stretches valid pixels between 2nd and 98.5th percentiles with gamma correction ($\gamma=0.85$), strictly excluding bad/cloud pixels from statistics calculation to prevent dynamic range skew.

### Phase 3 — Dataset Architecture, Registry & Mini UI/UX
* **Multi-Domain Dataset Registry**: Segmented catalog supporting Eastern Ladakh LAC sectors (Pangong Tso, Galwan Valley) and maritime anchorage zones.
* **GeoTIFF Inspection Engine**: Headless raster readers extracting exact dimensions, band counts, CRS, bounds, and NoData ratios.
* **Streamlit Defense Mini UI/UX**: Unified dashboard (`ui/geotiff_preview.py`) displaying multi-epoch operational timelines, 8-layer mask breakdowns, strict 512 tile validation, and deep raster inspection.

### Phase 5 — Cloud Removal / De-Clouding & Output Validation
* **Methodology**: Cloud-matting-inspired physical surface recovery (reference: DOI `10.3390/rs15040904`).
* **Region Classification**: Discretizes scene into `CLEAR` ($<0.20$ prob), `THIN_CLOUD` ($0.20-0.45$), `UNCERTAIN_CLOUD` ($0.45-0.70$), and `THICK_CLOUD` ($>0.70$).
* **Trimap & Opacity**: Produces discrete trimap and continuous alpha opacity $lpha \in [0.0, 1.0]$.
* **Surface Recovery**: Recovers underlying surface reflectance for thin transparent clouds via matting equations while preserving original observed pixels with **zero distortion** ($	ext{MAE} = 0.000000$).
* **Explicit Provenance Mask**: Retains pixel-level audit trail:
  * `Class 0 = OBSERVED_CLEAR` (genuine observed satellite measurement)
  * `Class 1 = THIN_CLOUD_CORRECTED` (matting recovered reflectance)
  * `Class 2 = THICK_CLOUD_RECONSTRUCTED` (reserved for temporal/model synthesis)
  * `Class 3 = UNRESOLVED_CLOUD` (opaque cloud masked without hallucinating data)
  * `Class 4 = NODATA`

---

## 🚀 Quickstart & Execution Guide

### 1. Environment Installation
```bash
# Clone the repository
git clone https://github.com/rezabadi31/Semantic-Image-Retrieval-and-Multi-temporal-Stallelite-Image-Analysis.git
cd Semantic-Image-Retrieval-and-Multi-temporal-Stallelite-Image-Analysis

# Install dependencies
pip install -r requirements.txt
```

### 2. Run Phase 1 AOI-to-Tiles Pipeline
```bash
python -m backend.ingestion.pipeline --geojson data/custom_aoi.geojson --region hyderabad_test
```

### 3. Run Phase 2 Cloud & Shadow Masking Validation
```bash
python -m pytest tests/test_cloud_validation.py -v
```

### 4. Run Phase 5 Cloud Removal CLI
```bash
# Process cloudy Sentinel-2 scene
python -m backend.cloud_removal.cli process --dataset-id cloud_masking_validation --scene-id S2A_43RGM_20230628_0_L2A

# Inspect generated products
python -m backend.cloud_removal.cli inspect --scene-id S2A_43RGM_20230628_0_L2A

# Validate scientific preservation & tile dimensions
python -m backend.cloud_removal.cli validate --scene-id S2A_43RGM_20230628_0_L2A

# Print full validation report
python -m backend.cloud_removal.cli report --dataset-id cloud_masking_validation --scene-id S2A_43RGM_20230628_0_L2A
```

### 5. Launch the Streamlit Defense Intelligence Mini UI/UX
```bash
streamlit run ui/geotiff_preview.py
```
*Access interactive inspection at `http://localhost:8501` featuring Section 1 (Timelines), Section 2 (Masks), Section 3 (512×512 Tiles), and Section 4 (Phase 5 De-Clouding).*

### 6. Run Automated Test Suite
```bash
python -m pytest tests/test_cloud_removal.py tests/test_cloud_validation.py tests/test_dataset_registry.py tests/test_preview_service.py -v
```

---

## 🔒 Data & Security Policy
* **Git Exclusions**: All heavy satellite rasters (`*.tif`, `*.tiff`, `*.jp2`), full-size visual previews (`*.png`, `*.jpg`), and temporary caches are strictly excluded by `.gitignore`.
* **Zero Secret Leakage**: No credentials, API tokens, or virtual environment binaries are committed.
* **Audit Lineage**: Every processed product is accompanied by a standardized JSON manifest capturing provenance, sensor lineage, and quality metrics.
