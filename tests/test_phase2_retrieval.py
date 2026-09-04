"""
tests/test_phase2_retrieval.py
===============================
Comprehensive Unit and Integration Tests for Phase 2: Semantic & Multimodal Retrieval
Covers:
  - Phase 2.1: Query text and image encoding
  - Phase 2.2: Parameterized kNN vector search & filter construction
  - Phase 2.3: Postgres batch result hydration & ranking preservation
  - Phase 2.4: Rule-based 3-index spot description generation (with defensive null checks)
  - Phase 2.5: SemanticSearchTool end-to-end integration & audit logging
"""

import os
import sys
import numpy as np
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.encoder import get_encoder, encode_query_text, encode_query_image
from backend.services.vector_search import (
    SearchFilter,
    SearchRequest,
    VectorSearchService,
    generate_tile_description,
    get_vector_search_service,
    search_semantic
)


# ============================================================
# Phase 2.1 — Query Encoding Unit Tests
# ============================================================

def test_phase2_1_encode_query_text_shape_and_norm():
    """Verify encode_query_text produces 512-dim L2-normalized vector."""
    prompt = "Airstrip with hangars near river bend"
    vec = encode_query_text(prompt)

    assert isinstance(vec, list), "Output must be a list of floats"
    assert len(vec) == 512, f"Expected 512-dim embedding, got {len(vec)}"

    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    assert np.isclose(norm, 1.0, atol=1e-3), f"Vector is not L2 normalized: norm={norm}"


def test_phase2_1_encode_query_image_array_and_norm():
    """Verify encode_query_image handles RGB numpy arrays correctly."""
    rgb_arr = np.random.randint(0, 256, (512, 512, 3), dtype=np.uint8)
    vec = encode_query_image(rgb_arr)

    assert isinstance(vec, list), "Output must be a list of floats"
    assert len(vec) == 512, f"Expected 512-dim embedding, got {len(vec)}"

    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    assert np.isclose(norm, 1.0, atol=1e-3), f"Vector is not L2 normalized: norm={norm}"


def test_phase2_1_encoder_singleton_identity():
    """Verify encoder instance is reused and never loaded twice."""
    enc1 = get_encoder()
    enc2 = get_encoder()
    assert enc1 is enc2, "get_encoder() must return the exact same singleton instance"


# ============================================================
# Phase 2.2 — Vector Search & Filter Construction Tests
# ============================================================

def test_phase2_2_build_qdrant_filter_conditions():
    """Verify Qdrant Filter correctly maps date-range, sensor, quality, and candidate IDs."""
    service = VectorSearchService(qdrant_client=MagicMock())

    filters = SearchFilter(
        sensor="Sentinel-2",
        start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
        min_quality=0.8,
        max_cloud_pct=15.0
    )
    candidate_ids = ["tile_ahmedabad_001", "tile_ahmedabad_002"]

    q_filter = service.build_qdrant_filter(filters, candidate_tile_ids=candidate_ids)
    assert q_filter is not None
    assert len(q_filter.must) >= 5, "Filter must contain candidate_ids, sensor, date, quality, and cloud conditions"


def test_phase2_2_search_vectors_empty_candidate_tiles_short_circuit():
    """Verify that when AOI spatial pre-filter finds 0 tiles, search immediately returns empty list."""
    service = VectorSearchService(qdrant_client=MagicMock())

    with patch.object(service, "resolve_aoi_to_tile_ids", return_value=[]):
        dummy_vec = [0.0] * 512
        filters = SearchFilter(bbox=[72.0, 23.0, 72.1, 23.1])
        results = service.search_vectors(dummy_vec, top_k=5, filters=filters)
        assert results == [], "Must return empty results when spatial filter intersects 0 tiles"


# ============================================================
# Phase 2.4 — Spot Description Generator Unit Tests
# ============================================================

def test_phase2_4_description_all_three_indices():
    """Verify rule-based description formatting with all 3 indices."""
    desc = generate_tile_description(mean_ndvi=0.45, mean_ndwi=0.35, mean_ndbi=-0.12)
    assert "dense vegetation (NDVI 0.45)" in desc
    assert "prominent water presence (NDWI 0.35)" in desc
    assert "non-built natural surface (NDBI -0.12)" in desc


def test_phase2_4_description_sparse_builtup_case():
    """Verify built-up urban description generation."""
    desc = generate_tile_description(mean_ndvi=0.18, mean_ndwi=-0.05, mean_ndbi=0.22)
    assert "sparse vegetation (NDVI 0.18)" in desc
    assert "moderate moisture (NDWI -0.05)" in desc
    assert "high built-up dominance (NDBI 0.22)" in desc


def test_phase2_4_description_defensive_null_check_missing_ndbi():
    """Verify graceful handling when NDBI is None (legacy tiles)."""
    desc = generate_tile_description(mean_ndvi=0.05, mean_ndwi=0.15, mean_ndbi=None)
    assert "minimal vegetation (NDVI 0.05)" in desc
    assert "moderate moisture (NDWI 0.15)" in desc
    assert "NDBI" not in desc
    assert "None" not in desc


def test_phase2_4_description_all_none_fallback():
    """Verify fallback when all indices are None."""
    desc = generate_tile_description(mean_ndvi=None, mean_ndwi=None, mean_ndbi=None)
    assert "Spectral indices unavailable" in desc


# ============================================================
# Phase 2.3 & 2.5 — Hydration, Logging & Search Tool Tests
# ============================================================

def test_phase2_3_hydrate_preserves_qdrant_ranking():
    """Verify hydration maintains Qdrant's similarity score order and attaches descriptions."""
    service = VectorSearchService(qdrant_client=MagicMock())

    ranked_pairs = [("tile_high", 0.95), ("tile_mid", 0.80), ("tile_low", 0.65)]

    # Mock DB return with distinct sites
    mock_db_rows = [
        {
            "tile_id": "tile_low",
            "scene_id": "scene_01",
            "site_key": "site_01",
            "acquisition_date": datetime(2023, 6, 1),
            "sensor": "Sentinel-2",
            "cloud_pct": 2.5,
            "quality_confidence": 0.95,
            "mean_ndvi": 0.05,
            "mean_ndwi": -0.02,
            "mean_ndbi": 0.30,
            "centroid_lat": 23.0,
            "centroid_lon": 72.5,
            "file_path": "/data/tiles/tile_low.tif",
            "thumbnail_path": "/data/tiles/tile_low_thumb.jpg",
            "geometry_geojson": {"type": "Polygon", "coordinates": []}
        },
        {
            "tile_id": "tile_high",
            "scene_id": "scene_01",
            "site_key": "site_02",
            "acquisition_date": datetime(2023, 6, 1),
            "sensor": "Sentinel-2",
            "cloud_pct": 0.5,
            "quality_confidence": 0.98,
            "mean_ndvi": 0.55,
            "mean_ndwi": 0.40,
            "mean_ndbi": -0.20,
            "centroid_lat": 23.1,
            "centroid_lon": 72.6,
            "file_path": "/data/tiles/tile_high.tif",
            "thumbnail_path": "/data/tiles/tile_high_thumb.jpg",
            "geometry_geojson": {"type": "Polygon", "coordinates": []}
        },
        {
            "tile_id": "tile_mid",
            "scene_id": "scene_01",
            "site_key": "site_03",
            "acquisition_date": datetime(2023, 6, 1),
            "sensor": "Sentinel-2",
            "cloud_pct": 1.0,
            "quality_confidence": 0.96,
            "mean_ndvi": 0.25,
            "mean_ndwi": 0.10,
            "mean_ndbi": 0.10,
            "centroid_lat": 23.05,
            "centroid_lon": 72.55,
            "file_path": "/data/tiles/tile_mid.tif",
            "thumbnail_path": "/data/tiles/tile_mid_thumb.jpg",
            "geometry_geojson": {"type": "Polygon", "coordinates": []}
        }
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = mock_db_rows
    mock_cursor.__enter__.return_value = mock_cursor

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(service, "_get_pg_conn", return_value=mock_conn):
        items = service.hydrate_from_postgres(ranked_pairs)

        assert len(items) == 3
        # Must strictly match Qdrant rank order: tile_high (0.95), tile_mid (0.80), tile_low (0.65)
        assert items[0].tile_id == "tile_high"
        assert items[0].score == 0.95
        assert "dense vegetation" in items[0].spot_description

        assert items[1].tile_id == "tile_mid"
        assert items[1].score == 0.80

        assert items[2].tile_id == "tile_low"
        assert items[2].score == 0.65
        assert "built-up dominance" in items[2].spot_description


# ============================================================
# Phase 2.6 — FastAPI Endpoint Integration Tests
# ============================================================

def test_phase2_6_api_search_text_endpoint():
    """Verify POST /api/v1/search endpoint processes JSON text search requests."""
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.services.vector_search import SearchResponse, SearchResultItem

    client = TestClient(app)

    mock_resp = SearchResponse(
        query_type="text",
        query="river bank bridge",
        total_found=1,
        results=[
            SearchResultItem(
                tile_id="tile_001",
                score=0.91,
                scene_id="scene_001",
                mean_ndvi=0.20,
                mean_ndwi=0.40,
                mean_ndbi=-0.10,
                spot_description="This location shows sparse vegetation and significant water."
            )
        ],
        execution_time_ms=12.5
    )

    with patch("backend.api.routers.search.get_vector_search_service") as mock_get_srv:
        mock_srv = MagicMock()
        mock_srv.search.return_value = mock_resp
        mock_get_srv.return_value = mock_srv

        payload = {
            "query_text": "river bank bridge",
            "top_k": 5,
            "filters": {
                "sensor": "Sentinel-2",
                "min_quality": 0.5
            }
        }

        res = client.post("/api/v1/search", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["total_found"] == 1
        assert data["query_type"] == "text"
        assert len(data["results"]) == 1
        assert data["results"][0]["tile_id"] == "tile_001"
        assert data["results"][0]["score"] == 0.91


def test_phase2_6_api_search_image_upload_endpoint():
    """Verify POST /api/v1/search/image endpoint accepts multipart file uploads."""
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.services.vector_search import SearchResponse, SearchResultItem
    import io

    client = TestClient(app)

    mock_resp = SearchResponse(
        query_type="image",
        query="raw_image_bytes",
        total_found=1,
        results=[
            SearchResultItem(
                tile_id="tile_002",
                score=0.88,
                spot_description="This location shows dense vegetation."
            )
        ],
        execution_time_ms=15.0
    )

    with patch("backend.api.routers.search.get_vector_search_service") as mock_get_srv:
        mock_srv = MagicMock()
        mock_srv.search.return_value = mock_resp
        mock_get_srv.return_value = mock_srv

        # Create dummy image bytes
        from PIL import Image
        img = Image.new("RGB", (64, 64), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        files = {"file": ("query_sample.jpg", buf, "image/jpeg")}
        data = {"top_k": "5", "sensor": "Sentinel-2"}

        res = client.post("/api/v1/search/image", files=files, data=data)
        assert res.status_code == 200
        res_data = res.json()
        assert res_data["total_found"] == 1
        assert res_data["query_type"] == "image"
        assert res_data["results"][0]["tile_id"] == "tile_002"

