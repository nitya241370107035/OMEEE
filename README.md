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
> **Repository:** [`varun-ai69/SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analysis-`](https://github.com/varun-ai69/SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analy[Key Features](#-key-features--capabilities) • [System Architecture](#️-system-architecture--storage-strategy) • [Frontend Workspaces](#-dedicated-frontend-workspaces) • [Pipeline Flow](#-how-all-is-done-end-to-end-pipeline-execution-flow) • [API Specs](#-api-route-specification-apiv1) • [Quickstart Guide](#-quickstart--execution-guide-docker-compose)

</div>

---

## Executive Summary

Modern defense intelligence analysts require the capability to query multi-temporal satellite imagery archives **by semantic meaning** (e.g. *"find airstrips with visible hangars near river bends"*, *"military aircraft on apron"*, or *"agricultural fields with irrigation"*), rather than relying strictly on geographic coordinates or metadata dates. Additionally, the system must automatically flag **true multi-temporal change events** while suppressing false alarms caused by cloud coverage, seasonal variations, sun angles, or spatial misalignment.

This platform provides an end-to-end, **100% on-premises, network-isolated solution** utilizing fine-tuned RemoteCLIP vision-language encoders, Qdrant vector indexing, PostGIS spatial caching, multi-spectral index calculation (NDVI, NDWI, NDBI), and dedicated aerospace analytical workspaces.

---

## System Architecture & Storage Strategy

The system enforces a strict separation between ingestion-time heavy precomputation and query-time sub-second resolution:

```text
                            +-------------------------------------------------------------+
                            |               Aerospace Web Applications (UI)               |
                            |  [Map & Ingest (/)]  [Semantic Retrieval (/retrieval)]      |
                            |  [Change Detection (/change)]  [Clustering (/clustering)]   |
                            +------------------------------+------------------------------+
                                                           | REST (/api/v1/...)
                            +------------------------------v------------------------------+
                            |                   FastAPI Backend Service                   |
                            |         (Ingestion, RemoteCLIP Encoding, Semantic Search)   |
                            +----+-------------------------+-------------------------+----+
                                 |                         |                         |
            +--------------------+                         |                         +--------------------+
            | Vector Embeddings                            | Spatial SQL Queries                          | Tile & GeoTIFF Storage
    +-------v-------+                              +-------v-------+                              +-------v-------+
    |   Qdrant DB   |                              |  PostgreSQL / |                              |  Local Disk / |
    | (512-dim      |                              |    PostGIS    |                              | MinIO Storage |
    |  RemoteCLIP)  |                              | (Meta & Cache)|                              | (512x512 COGs)|
    +---------------+                              +---------------+                              +---------------+
```

### Three Storage Layers
1. **Vector Database (Qdrant `tile_embeddings`)**: Stores 512-dim RemoteCLIP embeddings with HNSW indexing for text-to-image semantic search, image-to-image retrieval, and compound payload filtering (date range, sensor, quality score, spatial bounds).
2. **Relational & Spatial Database (PostgreSQL 16 + PostGIS 3.4)**: Stores scene records (`scenes`), tile catalog (`tiles` with `GEOMETRY(Polygon, 4326)`), search history (`search_log`), and live region coverage boundaries (`ingestion_coverage`).
3. **File & Object Storage (Local Archive / MinIO S3)**: Holds multi-band GeoTIFF tiles with geotransforms (`.tif`), 8-bit visual previews (`.jpg`), and manifest metadata (`manifest.json`).

---

## 🖥️ Dedicated Frontend Workspaces

Instead of cramming distinct intelligence workflows into a single screen, AeroLens provides dedicated, high-performance web workspaces:

| Route | Workspace | Description |
|---|---|---|
| **`/`** | **Map & AOI Ingestion** | Global deep-zoom satellite map (Esri, Google, CartoDB Dark), interactive polygon drawing, multi-temporal timeline selector (1 to 10 years), and live pipeline progress visualization. |
| **`/retrieval`** | **Semantic Retrieval** | Multimodal search workspace with conversational prompt chips, compound filters (date range, sensor, quality gate, AOI boundary), sub-100ms cosine ranking, and tactical spot inspection modals. |
| **`/change`** | **Change Detection** | Bi-temporal $T_1$ vs $T_2$ multi-spectral change analysis, $\Delta\text{NDVI}$, $\Delta\text{NDWI}$, and $\Delta\text{NDBI}$ delta masks. |
| **`/clustering`** | **Spatial & Spectral Clustering** | Unsupervised terrain categorization and feature distribution analysis. |
| **`/review`** | **Analyst Review & Verification** | Quality assurance audit log and tactical verification feed. |

---

## 🚀 Key Features & Capabilities

### Phase 1 — Preprocessing & Ingestion Engine
* **Dual Ingestion Entry Points**:
  - **Entry Point A (AOI Draw / GeoJSON + Timeline)**: Validates arbitrary polygons, auto-closes open rings, computes geographic bounding box, and calculates safety buffer margins.
  - **Entry Point B (Offline Evaluation GeoTIFF)**: Reads local rasters, extracts CRS, bounding box in WGS84, dimensions, and band configurations with zero external network calls.
* **Multi-Temporal Scene Bucketing**: Automatically splits requested date ranges into discrete temporal intervals (`historical_T1` vs `recent_T2`), querying AWS Sentinel-2 L2A STAC catalogs for clear-sky imagery (`cloud_cover < 15%`).
* **5-Band Canvas Reprojection & TCI Streaming**: Warps scenes to EPSG:4326 using single-stage GDAL WarpedVRT bilinear reprojection. Simultaneously streams 10m True Color Image (`TCI.tif`) and 5 spectral bands: Blue (`B02`), Green (`B03`), Red (`B04`), NIR (`B08`), SWIR (`B11`).
* **Cloud & Directional Shadow Masking**: Integrates `s2cloudless` gradient-boosted trees and directional shadow ray-tracing to compute pixel-level bad masks (`bad_mask`).
* **Mask-Aware Percentile Normalization**: Applies 0.5%–99.5% dynamic contrast stretching strictly over clean ground pixels (`~bad_mask`).
* **Equidistant Non-Redundant Tiling**: Slices working canvas into 512×512 patches using equidistant coordinate offsets (`_get_grid_steps()`), ensuring complete 100% boundary coverage with zero duplicate slices.
* **Spectral Indices Computation**:
  - **NDVI** (Vegetation): $(\text{NIR} - \text{Red}) / (\text{NIR} + \text{Red})$
  - **NDWI** (Water): $(\text{Green} - \text{NIR}) / (\text{Green} + \text{NIR})$
  - **NDBI** (Built-Up / Urban): $(\text{SWIR} - \text{NIR}) / (\text{SWIR} + \text{NIR})$
* **PostGIS & Disk Persistence**: Saves 5-band GeoTIFFs (`.tif`), 8-bit RGB visual thumbnails (`.jpg`), and `manifest.json` in standardized directory layouts (`data/tiles/{region_id}/{date}/`).

---

### Phase 2 — Multimodal Semantic Retrieval & Filtered Search
* **Sovereign RemoteCLIP Vision-Language Encoder**: Employs fine-tuned RemoteCLIP ViT-B-32 weights (~605 MB) running locally on CPU or GPU with zero external cloud dependencies.
* **Aerial Prompt Template Ensembling**: Enhances natural language text queries using aerial prompt weighting (`"satellite imagery of {prompt}"`, `"aerial view of {prompt}"`), boosting zero-shot retrieval accuracy for military and civilian structures.
* **Sub-100ms Vector Search with Compound Filtering**: Executes cosine similarity matching in Qdrant with pre-filters:
  - **Spatial AOI Pre-filter**: Restricts results to a user-drawn polygon or bounding box.
  - **Temporal Range**: Filters by acquisition date range ($T_1$ to $T_2$).
  - **Sensor Type**: Filters by platform (e.g. `Sentinel-2A`, `Sentinel-2B`).
  - **Quality & Cloud Gates**: Filters by minimum quality confidence score ($0.0$ to $1.0$).
* **Spatial Proximity Deduplication**: Enforces spatial radius separation ($\sim 200\text{m}$) and distinct `site_key` filtering to guarantee that Top-K results are distinct physical ground locations.
* **Compound Multi-Spectral Intelligence Explanations**: Generates rule-based terrain and tactical spot assessments by evaluating interacting tri-index signatures ($\text{NDVI} \times \text{NDWI} \times \text{NDBI}$).
* **Audit Persistence**: Every search execution is automatically logged into the PostgreSQL `search_log` table with query text, result tile IDs, similarity scores, and execution latency.

---

## 🛠️ Quickstart & Execution Guide (Docker Compose)

The entire application stack (FastAPI Backend, Leaflet Web UI, PostgreSQL/PostGIS, Qdrant Vector DB, and MinIO S3) is fully containerized and runs with a single command.

### 1. Clone & Setup
```bash
# Clone the repository
git clone https://github.com/varun-ai69/SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analysis-.git
cd SIH-2026-PS26227-Semantic-Retrieval-and-Multi-Temporal-Analysis-

# Copy environment variables
cp .env.example .env
```

### 2. Download Pretrained RemoteCLIP Weights
Download the pre-trained `RemoteCLIP-ViT-B-32.pt` weights (~605 MB) and place them inside the `models/retrieval/` directory:

- **HuggingFace Repository**: [chendelong/RemoteCLIP](https://huggingface.co/chendelong/RemoteCLIP)
- **Direct Checkpoint Download**: [RemoteCLIP-ViT-B-32.pt](https://huggingface.co/chendelong/RemoteCLIP/resolve/main/RemoteCLIP-ViT-B-32.pt)

```bash
# Create models directory (if not exists)
mkdir -p models/retrieval

# Download using curl (Linux / macOS / PowerShell)
curl -L -o models/retrieval/RemoteCLIP-ViT-B-32.pt "https://huggingface.co/chendelong/RemoteCLIP/resolve/main/RemoteCLIP-ViT-B-32.pt"
```

### 3. Launch Full Docker Stack
```bash
# Launch the multi-container stack via infra compose
docker compose -f infra/docker-compose.yml up -d --build
```

### 4. Service Access Endpoints

| Service | Access URL | Port | Description |
|---|---|---|---|
| **AeroLens Map & Ingestion UI** | [http://localhost:8000](http://localhost:8000) | `8000` | Global Satellite Map, AOI Drawing & Ingestion |
| **AeroLens Retrieval Workspace** | [http://localhost:8000/retrieval](http://localhost:8000/retrieval) | `8000` | Dedicated Multimodal Semantic Search Feed |
| **FastAPI Swagger Docs** | [http://localhost:8000/docs](http://localhost:8000/docs) | `8000` | Interactive REST API Documentation |
| **Qdrant Vector Dashboard** | [http://localhost:6333/dashboard](http://localhost:6333/dashboard) | `6333` | Vector collection browser & points visualizer |
| **PostgreSQL / PostGIS** | `localhost:5434` | `5434` (mapped from 5432) | Database: `eo_archive`, User: `eo_admin`, Pass: `eo_password` |
| **MinIO S3 Web Console** | [http://localhost:9001](http://localhost:9001) | `9001` | Object Storage Console (User: `eo_admin`, Pass: `eo_password`) |

### 5. Run Automated Test Suite
```bash
# Run tests inside the running container
docker exec -it eo_backend pytest tests/ -v
```

---

## 🔄 How All Is Done (End-to-End Pipeline Execution Flow)

```text
1. User draws AOI polygon on Leaflet Map & selects timeline (e.g., 2023–2024, 2 buckets)
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
8. Tiler slices canvas into equidistant 512x512 tiles, clips to AOI & calculates NDVI, NDWI, NDBI
                             │
                             ▼
9. Storage Engine saves 5-band GeoTIFF (.tif), 8-bit RGB preview (.jpg), and manifest.json to disk
                             │
                             ▼
10. RemoteCLIP Encoder computes 512-dim L2-normalized embedding for each tile
                             │
                             ▼
11. DB Writer atomically upserts scenes & tiles into PostgreSQL and vectors into Qdrant
                             │
                             ▼
12. Analyst navigates to /retrieval and queries "airstrip runway with hangars"
                             │
                             ▼
13. Vector Search Engine embeds query, filters Qdrant vectors, deduplicates, and returns Top 5 matches with rule explanations in < 60ms
```

---

## 📁 Repository Directory Structure

```
.
├── README.md                               # Primary project documentation
├── PROVENANCE.md                           # Official Data & Model Lineage Audit Log
├── IngestionPipelin.md                     # Ingestion architecture specification
├── encoderPipeline.md                      # RemoteCLIP encoder pipeline reference
├── requirements.txt                        # Python dependencies
├── .env.example                            # Configuration environment variables template
├── backend/                                # Python FastAPI services, ML models & ingestion
│   ├── api/                                # REST API routers & main server
│   │   ├── main.py                         # FastAPI application entrypoint & static mounting
│   │   └── routers/                        # Endpoints (/ingest, /coverage, /search, /archive)
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
│   │   ├── tiler.py                        # Phase 1.5: Equidistant 512x512 tiling & spectral indices
│   │   ├── storage.py                      # Phase 1.5: GeoTIFF, JPEG & manifest disk writer
│   │   ├── db_writer.py                    # Phase 1.6: PostGIS atomic database persistence
│   │   └── pipeline.py                     # Unified Phase 1 end-to-end pipeline orchestrator
│   └── services/                           # AI / ML & Vector Store Services
│       ├── encoder.py                      # RemoteCLIP ViT-B-32 vision-language encoder (Phase 2.1)
│       ├── vector_store.py                 # Qdrant client wrapper & batch vector upsert
│       └── vector_search.py                # Semantic cosine search, filters & hydration (Phase 2.2 - 2.5)
├── frontend/                               # Interactive Aerospace Mapping Web Applications
│   ├── index.html                          # Map & AOI Ingestion workspace (/)
│   ├── retrieval.html                      # Semantic & Multimodal Retrieval workspace (/retrieval)
│   ├── change.html                         # Multi-Temporal Change Detection workspace (/change)
│   ├── clustering.html                     # Spatial & Spectral Clustering workspace (/clustering)
│   ├── review.html                         # Analyst Verification & Audit workspace (/review)
│   ├── index.css                           # Glassmorphic dark aerospace design system
│   └── app.js                              # Map drawing, layer switching, and API integration
├── infra/                                  # Infrastructure & Container Orchestration
│   ├── docker-compose.yml                  # Multi-container Docker Compose configuration
│   └── docker/                             # Docker container specification
│       ├── Dockerfile                      # Geospatial container specification with GDAL, PyTorch
│       └── entrypoint.sh                   # Automated database migration & startup entrypoint
├── models/                                 # Pretrained model weights (RemoteCLIP ViT-B-32)
├── data/                                   # Local archive storage (tiles, custom AOIs)
└── tests/                                  # Automated unit & integration test suite
    ├── test_phase1_0_input_handling.py     # Input validator tests
    ├── test_phase1_1_stac_search.py        # STAC query & bucketing tests
    ├── test_phase1_2_canvas.py             # Canvas assembly & reprojection tests
    ├── test_phase1_3_quality_masking.py    # Cloud & shadow masking tests
    ├── test_phase1_4_5_tiling_storage.py   # 512x512 tiling & disk writer tests
    ├── test_phase1_8_encoder_vectorstore.py # RemoteCLIP & Qdrant integration tests
    ├── test_phase1_e2e_pipeline.py         # End-to-end pipeline verification test
    └── test_phase2_retrieval.py            # Phase 2 Semantic Retrieval integration tests
```

---

## 🌐 API Route Specification (`/api/v1`)

| Endpoint | Method | Input Payload | Description |
|---|---|---|---|
| `/health` | `GET` | None | Service health status check |
| `/api/v1/ingest/aoi` | `POST` | GeoJSON Polygon + Date Range + Buckets | Ingests AOI polygon across time buckets (Phases 1.0 &ndash; 1.8) |
| `/api/v1/ingest/file` | `POST` | File path + Band order | Ingests offline evaluation GeoTIFF with custom bands |
| `/api/v1/coverage` | `GET` | None | Returns GeoJSON FeatureCollection of all ingested sectors |
| `/api/v1/search/semantic` | `POST` | `{ query, top_k, filter }` | Natural language text search against tile embeddings |
| `/api/v1/search/image` | `POST` | Multipart Image + Filters | Image-to-image semantic visual similarity retrieval |
| `/api/v1/search/health` | `GET` | None | RemoteCLIP encoder & Qdrant vector collection health status |
| `/api/v1/archive/stats`| `GET` | None | Returns total count of tiles, scenes, and regions online |
| `/api/v1/archive/tiles`| `GET` | Region ID / Date filter | Paged query of ingested tiles with spectral indices |

---

## 🛡️ License & Provenance
Logged in [`PROVENANCE.md`](PROVENANCE.md) per Ministry of Defence submission requirements.

