# 🛰️ AeroLens — Semantic Retrieval & Multi-Temporal Change Analysis of Satellite Imagery

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![Qdrant](https://img.shields.io/badge/Qdrant-Vector%20DB-DC2626?style=for-the-badge&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-316192?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![PostGIS](https://img.shields.io/badge/PostGIS-3.4-5B9BD5?style=for-the-badge&logo=postgis&logoColor=white)](https://postgis.net/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Leaflet](https://img.shields.io/badge/Leaflet-1.9.4-199900?style=for-the-badge&logo=leaflet&logoColor=white)](https://leafletjs.com/)
[![GDAL](https://img.shields.io/badge/GDAL-Geospatial-489849?style=for-the-badge&logo=osgeo&logoColor=white)](https://gdal.org/)
[![MinIO](https://img.shields.io/badge/MinIO-S3%20Storage-C72C48?style=for-the-badge&logo=minio&logoColor=white)](https://min.io/)
[![RemoteCLIP](https://img.shields.io/badge/RemoteCLIP-ViT--B%2F32-8A2BE2?style=for-the-badge)](https://huggingface.co/chendelong/RemoteCLIP)
[![Sentinel-2](https://img.shields.io/badge/Sentinel--2-10m%20Copernicus-003399?style=for-the-badge)](https://sentinel.esa.int/)

**AI-Powered Geospatial Intelligence, Zero-Shot Semantic Earth Search & Multi-Temporal Change Detection Platform**

> **Problem Statement Number:** SIH-26227  
> **Organization:** Ministry of Defence (MoD) — Indian Army (DGIS)  
> **Category:** Software | **Theme:** Space Technology  
> **Repository:** [`varun-ai69/SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analysis-`](https://github.com/varun-ai69/SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analysis-)

[Key Features](#-preprocessing--ingestion-phases-completed) • [System Architecture](#️-system-architecture--storage-strategy) • [Quickstart Guide](#-quickstart--execution-guide-docker-compose) • [Pipeline Flow](#-how-all-is-done-end-to-end-pipeline-execution-flow) • [API Specs](#-api-route-specification-apiv1)

</div>

---

## Executive Summary

Modern defense intelligence analysts require the capability to query multi-temporal satellite imagery archives **by semantic meaning** (e.g. *"find airstrips with visible hangars near river bends"* or *"detect newly cleared land or road construction"*) rather than relying strictly on geographic coordinates or metadata dates. Additionally, the system must automatically flag **true multi-temporal change events** while suppressing false alarms caused by cloud coverage, seasonal variations, sun angles, or spatial misalignment.

This platform provides an end-to-end, **100% on-premises, network-isolated solution** utilizing fine-tuned RemoteCLIP vision-language encoders, Qdrant vector indexing, PostGIS spatial caching, multi-spectral index calculation (NDVI, NDWI, NDBI), and an interactive aerospace Leaflet mapping workspace.

---

## System Architecture & Storage Strategy

The system enforces a strict separation between ingestion-time heavy precomputation and query-time rapid resolution:

```text
                            +---------------------------------+
                            |   Aerospace Web Application UI  |
                            |  (Leaflet + Esri + AOI Drawing) |
                            +----------------+----------------+
                                             | REST (/api/v1/...)
                            +----------------v----------------+
                            |     FastAPI Backend Service     |
                            |  (Ingestion, Coverage, Search)  |
                            +----+-----------+-----------+----+
                                 |           |           |
            +--------------------+           |           +--------------------+
            | Vector Embeddings              | Spatial SQL Queries            | Tile & GeoTIFF Storage
    +-------v-------+                +-------v-------+                +-------v-------+
    |   Qdrant DB   |                |  PostgreSQL / |                |  Local Disk / |
    | (512-dim      |                |    PostGIS    |                | MinIO Storage |
    |  RemoteCLIP)  |                | (Meta & Cache)|                | (512x512 COGs)|
    +---------------+                +---------------+                +---------------+
```

### Three Storage Layers
1. **Vector Database (Qdrant `tile_embeddings`)**: Stores 512-dim RemoteCLIP embeddings for text-to-image semantic search, image-to-image retrieval, and spatial-temporal payload filters.
2. **Relational & Spatial Database (PostgreSQL 16 + PostGIS 3.4)**: Stores scene records (`scenes`), tile catalog (`tiles` with `GEOMETRY(Polygon, 4326)`), and live region coverage boundaries (`ingestion_coverage`).
3. **File & Object Storage (Local Archive / MinIO S3)**: Holds multi-band GeoTIFF tiles with geotransforms (`.tif`), 8-bit visual previews (`.jpg`), and manifest metadata (`manifest.json`).

---

## Preprocessing & Ingestion Phases (COMPLETED)

### Phase 1.0 — Arbitrary Input Validation & Bounding Box Extraction
* **Dual Ingestion Entry Points**:
  - **Entry Point A (AOI Draw / GeoJSON + Timeline)**: Validates arbitrary polygons, auto-closes open rings, computes geographic bounding box, and calculates safety buffer margins.
  - **Entry Point B (Offline Evaluation GeoTIFF)**: Reads local rasters, extracts coordinate reference systems (CRS), bounding box in WGS84, dimensions, and band configurations with zero external network access.

### Phase 1.1 — Multi-Temporal Scene Search against STAC Catalog
* **Time-Window Bucketing**: Splits user-requested date ranges (e.g. 1 to 10 years) into discrete temporal buckets (T1 Historical vs T2 Recent).
* **Automated STAC Queries**: Queries AWS Earth Search Sentinel-2 L2A catalog for clear-sky imagery (`cloud_cover < 20%`), selecting optimal scenes per bucket.
* **Deterministic Offline Fallback**: Generates realistic synthetic 5-band fallback scenes when operating in air-gapped environments.

### Phase 1.2 — 5-Band Canvas Reprojection & High-Fidelity TCI Streaming
* **Standardized 10m Coordinate Space**: Warps scenes to EPSG:4326 using single-stage GDAL WarpedVRT bilinear reprojection.
* **Pristine True Color Image (TCI)**: Streams 10m ESA-calibrated True Color Image (`TCI.tif`) with Sen2Cor atmospheric balancing for ultra-crisp visual display and embedding extraction.
* **5 Core Scientific Bands**: Simultaneously streams Blue (`B02`), Green (`B03`), Red (`B04`), NIR (`B08`), and SWIR (`B11`) for multi-spectral analysis.

### Phase 1.3 — Cloud Masking, Directional Shadow Masking & Quality Gating
* **Machine Learning Cloud Detection**: Integrates `s2cloudless` gradient-boosted trees over Sentinel-2 bands to compute pixel-level cloud probabilities ($0.0$ to $1.0$).
* **Directional Shadow Detection**: Casts cloud neighborhood shadow projection rays and evaluates NIR dips.
* **Quality Mask Merging**: Combines clouds and shadows into a single binary bad-pixel mask (`bad_mask`).

### Phase 1.4 — Mask-Aware Radiometric Percentile Normalization
* **Sub-Percentile Contrast Stretch**: Applies 0.5%–99.5% dynamic range percentile stretching strictly over clean ground pixels (`~bad_mask`).
* **Radiometric Integrity**: Prevents cloud brightness or shadow darkness from skewing ground contrast, preserving natural color balance.

### Phase 1.5 — Strict 512×512 Tiling & Multi-Spectral Indices
* **Spatial Tiling**: Slices working canvas into 512×512 patches with configurable stride/overlap (default 10%).
* **Exact Polygon Intersection**: Filters out tiles falling outside the user's drawn AOI polygon.
* **Spectral Indices Computation**:
  - **NDVI** (Vegetation): $(\text{NIR} - \text{Red}) / (\text{NIR} + \text{Red})$
  - **NDWI** (Water): $(\text{Green} - \text{NIR}) / (\text{Green} + \text{NIR})$
  - **NDBI** (Built-Up / Urban): $(\text{SWIR} - \text{NIR}) / (\text{SWIR} + \text{NIR})$
* **Deterministic Site Keys**: Generates stable spatial hashes for tracking physical ground locations across multi-temporal epochs.

### Phase 1.6 — PostGIS Database Storage & Ingestion Coverage
* **Schema Upsert**: Atomically records scenes in `scenes`, tiles in `tiles`, and updates `ingestion_coverage` with sector polygon geometries.
* **Coverage Visualizer**: Exposes `GET /api/v1/coverage` returning GeoJSON FeatureCollections for map rendering.

### Phase 1.8 — RemoteCLIP ViT-B-32 Vector Embeddings & Qdrant Upsert
* **Vision-Language Encoder**: Passes preprocessed 512x512 tile visual arrays through fine-tuned RemoteCLIP ViT-B-32, generating L2-normalized 512-dimensional feature embeddings.
* **Vector Store Indexing**: Performs batch upserts into Qdrant `tile_embeddings` collection with payload metadata (`tile_id`, `scene_id`, `site_key`, `acquisition_date`, `cloud_pct`, `ndvi`, `ndwi`, `ndbi`).

### Frontend — Interactive Leaflet Map & Ingestion UI
* **Global Satellite Map**: Deep zoom capability (up to level 20) with 4-layer basemap switcher (Esri World Imagery, Google Satellite Hybrid, CartoDB Dark Matter, OSM).
* **Interactive AOI Draw Tool**: Draw polygons on map with auto-closure snapping.
* **Multi-Year Timeline Selector**: 1 Year, 2 Years (T1 vs T2), 3 Years, 5 Years, 10 Years, or Custom Date Ranges.
* **Live Step-by-Step Progress Tracking Bar**: Animates each pipeline stage (STAC search &rarr; Canvas &rarr; Cloud Masking &rarr; Tiling &rarr; RemoteCLIP Embedding &rarr; DB Upsert).
* **Live Ingested Coverage Overlay**: Queries PostgreSQL to render all ingested regions with neon glowing polygon borders, tooltips, and fly-to sector controls.

---

## Quickstart & Execution Guide (Docker Compose)

The entire application stack (FastAPI Backend, Leaflet Web UI, PostgreSQL/PostGIS, Qdrant Vector DB, and MinIO S3) is fully containerized and runs with a single command.

### 1. Clone & Setup
```bash
# Clone the repository
git clone https://github.com/varun-ai69/SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analysis-.git
cd SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analysis-

# Copy environment variables
cp .env.example .env
```

### 2. Launch Full Docker Stack
```bash
# Launch the multi-container stack via infra compose
docker compose -f infra/docker-compose.yml up -d --build

# Or navigate to infra/ and start
cd infra && docker compose up -d --build
```

### 3. Service Access Endpoints

| Service | Access URL | Port | Description |
|---|---|---|---|
| **AeroLens Web UI** | [http://localhost:8000](http://localhost:8000) | `8000` | Interactive Global Map, AOI Ingestion & Search Workspace |
| **FastAPI Swagger Docs** | [http://localhost:8000/docs](http://localhost:8000/docs) | `8000` | Interactive REST API Documentation |
| **Qdrant Vector Dashboard** | [http://localhost:6333/dashboard](http://localhost:6333/dashboard) | `6333` | Vector collection browser & points visualizer |
| **PostgreSQL / PostGIS** | `localhost:5434` | `5434` (mapped from 5432) | Database: `eo_archive`, User: `eo_admin`, Pass: `eo_password` |
| **MinIO S3 Web Console** | [http://localhost:9001](http://localhost:9001) | `9001` | Object Storage Console (User: `eo_admin`, Pass: `eo_password`) |

### 4. Run Automated Test Suite
```bash
# Run tests inside the running container
docker exec -it eo_backend pytest tests/ -v

# Or run tests locally
pytest tests/ -v
```

---

## How All Is Done (End-to-End Pipeline Execution Flow)

```text
1. User draws AOI polygon on Leaflet Map & selects timeline (e.g., 2024, 2 buckets)
                             │
                             ▼
2. Frontend sends POST /api/v1/ingest/aoi to FastAPI Backend
                             │
                             ▼
3. Input Validator checks polygon topology & computes EPSG:4326 bounding box
                             │
                             ▼
4. STAC Search queries AWS Sentinel-2 catalog for lowest-cloud scenes in each time bucket
                             │
                             ▼
5. Canvas Assembler streams 10m True Color (TCI) COG + 5 Multi-Spectral bands (B02, B03, B04, B08, B11)
                             │
                             ▼
6. Masking Module executes s2cloudless ML model + shadow detector to build bad_mask
                             │
                             ▼
7. Normalizer applies 0.5%–99.5% dynamic percentile contrast stretch on clean ground pixels
                             │
                             ▼
8. Tiler slices canvas into 512x512 tiles, clips to AOI polygon & calculates NDVI, NDWI, NDBI
                             │
                             ▼
9. Storage Engine saves 5-band GeoTIFF (.tif) and 8-bit RGB preview (.jpg) to disk
                             │
                             ▼
10. RemoteCLIP Encoder computes 512-dim L2-normalized embedding for each tile
                             │
                             ▼
11. DB Writer atomically upserts scenes & tiles into PostgreSQL and vectors into Qdrant
                             │
                             ▼
12. Frontend receives success report & renders glowing coverage polygon on map
```

---

## Repository Directory Structure

```
.
├── README.md                               # Primary project documentation
├── PROVENANCE.md                           # Official Data & Model Lineage Audit Log
├── IngestionPipelin.md                     # Ingestion architecture specification
├── requirements.txt                        # Python dependencies
├── .env.example                            # Configuration environment variables template
├── backend/                                # Python FastAPI services, ML models & ingestion
│   ├── api/                                # REST API routers & main server
│   │   ├── main.py                         # FastAPI application entrypoint & static mounting
│   │   └── routers/                        # Endpoints (/ingest, /coverage, /archive)
│   ├── db/                                 # PostgreSQL / PostGIS schemas & migrations
│   │   ├── schema.sql                      # Spatial database DDL & indexes
│   │   ├── seed.sql                        # Initial seed data
│   │   └── models.py                       # SQLAlchemy models
│   ├── ingestion/                          # Preprocessing & Ingestion Engine (Phases 1.0 - 1.8)
│   │   ├── input_validator.py              # Phase 1.0: GeoJSON & GeoTIFF input validation
│   │   ├── stac_search.py                  # Phase 1.1: Multi-temporal STAC catalog search
│   │   ├── canvas.py                       # Phase 1.2: 5-band canvas reprojection & TCI streaming
│   │   ├── masking/                        # Phase 1.3: s2cloudless & shadow masking
│   │   ├── normalization/                  # Phase 1.4: Mask-aware percentile normalization
│   │   ├── tiler.py                        # Phase 1.5: 512x512 tiling & spectral indices
│   │   ├── storage.py                      # Phase 1.5: GeoTIFF, JPEG & manifest disk writer
│   │   ├── db_writer.py                    # Phase 1.6: PostGIS atomic database persistence
│   │   └── pipeline.py                     # Unified Phase 1 end-to-end pipeline orchestrator
│   └── services/                           # AI / ML & Vector Store Services
│       ├── encoder.py                      # RemoteCLIP ViT-B-32 vision-language encoder
│       ├── vector_store.py                 # Qdrant client wrapper & batch vector upsert
│       └── vector_search.py                # Semantic cosine search & payload filter engine
├── frontend/                               # Interactive Aerospace Mapping Web Application
│   ├── index.html                          # Leaflet map UI with multi-view navigation
│   ├── index.css                           # Glassmorphic dark aerospace styling
│   └── app.js                              # Map logic, AOI draw tools & progress tracking
├── infra/                                  # Infrastructure & Container Orchestration
│   ├── docker-compose.yml                  # Full-stack Multi-Container Docker Compose configuration
│   ├── docker/                             # Docker container specification
│   │   ├── Dockerfile                      # Geospatial container specification with GDAL, PyTorch
│   │   └── entrypoint.sh                   # Automated database migration & startup entrypoint
│   └── scripts/                            # Offline staging scripts
├── models/                                 # Pretrained model weights (RemoteCLIP ViT-B-32)
├── data/                                   # Local archive storage (tiles, custom AOIs)
└── tests/                                  # Automated unit & integration test suite
    ├── test_phase1_0_input_handling.py     # Input validator tests
    ├── test_phase1_1_stac_search.py        # STAC query & bucketing tests
    ├── test_phase1_2_canvas.py             # Canvas assembly & reprojection tests
    ├── test_phase1_3_quality_masking.py    # Cloud & shadow masking tests
    ├── test_phase1_4_5_tiling_storage.py   # 512x512 tiling & disk writer tests
    ├── test_phase1_8_encoder_vectorstore.py # RemoteCLIP & Qdrant integration tests
    └── test_phase1_e2e_pipeline.py         # End-to-end pipeline verification test
```

---

## API Route Specification (`/api/v1`)

| Endpoint | Method | Input | Description |
|---|---|---|---|
| `/health` | `GET` | None | Service health status check |
| `/api/v1/ingest/aoi` | `POST` | GeoJSON Polygon + Timeline | Ingests AOI polygon across time buckets (Phases 1.0 &ndash; 1.8) |
| `/api/v1/ingest/file` | `POST` | File path + Band order | Ingests offline evaluation GeoTIFF with custom bands |
| `/api/v1/coverage` | `GET` | None | Returns GeoJSON FeatureCollection of all ingested sectors for map rendering |
| `/api/v1/archive/stats`| `GET` | None | Returns total count of tiles, scenes, and regions online |
| `/api/v1/archive/tiles`| `GET` | Region ID / Date filter | Paged query of ingested tiles with spectral indices |

---

## 🛡️ License & Provenance
Logged in [`PROVENANCE.md`](PROVENANCE.md) per Ministry of Defence submission requirements.
