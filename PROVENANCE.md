# Data & Model Provenance Log (`PROVENANCE.md`)

## Overview & Purpose

This document serves as the official **Data and Model Lineage Audit Log** for the *Semantic Retrieval & Multi-Temporal Change Analysis of Satellite Imagery* project (PS SIH-26227 for Indian Army DGIS / Ministry of Defence).

To ensure complete transparency, security compliance, and licensing auditability in a **fully on-premises and air-gapped environment**, every dataset, pre-trained model checkpoint, and open-source dependency ingested into the platform is logged in this document.

---

## 📋 Tracked Metadata Fields

For every ingested component, the following metadata fields are tracked:

1. **Item Name**: Descriptive identifier of the dataset or model checkpoint.
2. **Component Type**: Categorized into `Training Dataset`, `Evaluation Dataset`, `Pre-trained Weights`, `Third-Party Dependency`, or `Earth Observation Data`.
3. **Source Provider / URL**: Official public URL or data provider repository.
4. **License & Usage Terms**: Legal license governing offline usage (e.g. CC BY 4.0, MIT, Apache 2.0, Open Data).
5. **Storage Location**: Local on-premises path within the workspace or container.
6. **Status**: Deployment state (`In-Use`, `Staged`, `Planned`).

---

## 📊 Ingestion Audit Table

| Item Name | Component Type | Source Provider / URL | License | Storage Location | Status |
|---|---|---|---|---|---|
| **RemoteCLIP (ViT-B/32)** | Pre-trained Vision-Language Weights | [HuggingFace / RemoteCLIP](https://huggingface.co/chendelong/RemoteCLIP) | MIT License | `models/retrieval/RemoteCLIP-ViT-B-32.pt` | **In-Use** 🟢 |
| **Sentinel-2 L2A COG Archive** | Earth Observation Satellite Imagery (10m) | [AWS Earth Search STAC / Copernicus](https://earth-search.aws.element84.com/v1) | Open Data (Copernicus) | `data/tiles/{region_id}/{date}/` | **In-Use** 🟢 |
| **s2cloudless Model** | ML Gradient-Boosted Cloud Detector | [Sentinel Hub / Sinergise](https://github.com/sentinel-hub/sentinel2-cloud-detector) | MIT License | `s2cloudless` pip package | **In-Use** 🟢 |
| **Qdrant Vector Database** | Vector Search Engine (512-dim Cosine) | [Qdrant Repository](https://github.com/qdrant/qdrant) | Apache 2.0 | Docker (`eo_qdrant:6333`) | **In-Use** 🟢 |
| **PostgreSQL 16 + PostGIS 3.4** | Sovereign Spatial Database | [PostGIS Official](https://postgis.net/) | GPL v2 | Docker (`eo_postgres:5434`) | **In-Use** 🟢 |
| **MinIO S3 Object Store** | S3-Compatible Local Object Store | [MinIO Official](https://min.io/) | AGPL v3.0 | Docker (`eo_minio:9000`) | **In-Use** 🟢 |
| **OpenCLIP Framework** | Vision-Language Model Library | [OpenCLIP Torch](https://github.com/mlfoundations/open_clip) | MIT License | Python dependency | **In-Use** 🟢 |
| **Leaflet & Esri World Imagery** | Interactive Geospatial Web Map | [Leaflet](https://leafletjs.com/) / Esri ArcGIS | BSD-2-Clause / Open Map Data | `frontend/` Web UI | **In-Use** 🟢 |
| **LEVIR-CD / OSCD** | Bi-Temporal Change Detection Datasets | LEVIR-CD & IEEE DataPort | CC BY 4.0 / CC BY-SA 4.0 | `data/training/` | **Planned** ⏳ |
| **RSICD Remote Sensing Captions** | Multi-Modal Caption Dataset | GitHub / RSICD | Open Access | `data/training/` | **Planned** ⏳ |

---

## 🛡️ Offline Air-Gap Verification & Sovereign Compliance

Before deploying the platform in an offline / air-gapped sovereign defense environment:
1. **Zero External API Calls**: Ingestion engine uses local COGs or pre-staged local files when network access is restricted.
2. **Deterministic Fallback**: Automatic offline mock generator available for unit testing and CI/CD without internet access.
3. **Isolated Docker Network**: All microservices (`eo_backend`, `eo_postgres`, `eo_qdrant`, `eo_minio`) communicate exclusively across the local Docker bridge network (`0.0.0.0:8000`).
