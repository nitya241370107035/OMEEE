"""
tests/test_phase1_8_encoder_vectorstore.py
==========================================
Unit & Integration Tests for Phase 1.8: RemoteCLIP Encoder & Qdrant Vector Store
"""

import sys
import numpy as np
import pytest
from pathlib import Path
from rasterio.transform import from_bounds
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.encoder import get_encoder, RemoteCLIPEncoder
from backend.services.vector_store import (
    ensure_collection_exists,
    encode_and_upsert_tiles_to_qdrant,
    get_qdrant_client,
    COLLECTION_NAME,
)
from backend.services.vector_search import VectorSearchService, SearchRequest
from backend.ingestion.tiler import TileCandidate


def test_remoteclip_encoder():
    encoder = get_encoder()
    assert isinstance(encoder, RemoteCLIPEncoder)

    # 1. Text Encoding
    text_emb = encoder.encode_text("military airfield with hangars and fighter aircraft")
    assert isinstance(text_emb, list)
    assert len(text_emb) == 512
    # Verify L2 normalization: sum of squares ≈ 1.0
    norm = np.linalg.norm(text_emb)
    assert pytest.approx(norm, abs=1e-3) == 1.0

    # 2. Image Array Encoding ((3, 512, 512) RGB)
    rgb_arr = np.random.randint(0, 255, (3, 512, 512), dtype=np.uint8)
    img_emb = encoder.encode_image(rgb_arr)
    assert isinstance(img_emb, list)
    assert len(img_emb) == 512
    norm_img = np.linalg.norm(img_emb)
    assert pytest.approx(norm_img, abs=1e-3) == 1.0


def test_qdrant_tile_upsert_and_retrieval():
    # 1. Ensure Qdrant collection exists
    client = get_qdrant_client()
    ensure_collection_exists(client)

    # 2. Create sample tile
    bounds = (72.50, 23.00, 72.55, 23.05)
    transform = from_bounds(*bounds, 512, 512)
    footprint = box(*bounds)

    sample_tile = TileCandidate(
        tile_id="test_remoteclip_tile_001",
        scene_id="S2_TEST_REMOTECLIP_001",
        site_key="site_23.0250_72.5250_test",
        multiband_data=np.random.rand(5, 512, 512).astype(np.float32) * 3000,
        rgb_data=np.random.randint(0, 255, (3, 512, 512), dtype=np.uint8),
        band_order=["blue", "green", "red", "nir", "swir"],
        band_stats={"blue": {"min": 0, "max": 255, "mean": 128}},
        transform=transform,
        bounds=bounds,
        centroid_lat=23.025,
        centroid_lon=72.525,
        cloud_pct=0.01,
        quality_confidence=0.99,
        mean_ndvi=0.45,
        mean_ndwi=-0.25,
        mean_ndbi=0.05,
        footprint_geom=footprint,
        source_type="aoi_search"
    )

    # 3. Upsert to Qdrant
    upserted_count = encode_and_upsert_tiles_to_qdrant(
        tiles=[sample_tile],
        scene_id="S2_TEST_REMOTECLIP_001",
        acquisition_date="2024-03-22T05:30:00Z",
        sensor="Sentinel-2",
        region_id="test_region"
    )
    assert upserted_count == 1

    # 4. Search via VectorSearchService
    search_service = VectorSearchService()
    req = SearchRequest(
        query_text="urban roads and buildings",
        limit=5
    )
    resp = search_service.search(req)

    assert resp.total_found >= 1
    assert resp.query_type == "text"
    assert resp.results[0].score > 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
