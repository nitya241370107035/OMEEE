"""
backend/services/vector_store.py
================================
Phase 1.8: Qdrant Vector Store Ingestion Engine
===============================================
PS Sections: 2.2.4 (Multimodal & Semantic Image Retrieval)

Manages Qdrant collection `tile_embeddings` and handles batch upserts of
512-dimensional RemoteCLIP embeddings along with multi-temporal metadata.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
)

if TYPE_CHECKING:
    from backend.ingestion.tiler import TileCandidate

from backend.services.encoder import get_encoder

logger = logging.getLogger("VectorStore")

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "tile_embeddings")
MAXAR_COLLECTION_NAME = os.getenv("QDRANT_MAXAR_COLLECTION", "maxar_tile_embeddings")
VECTOR_DIM = 512


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, check_compatibility=False)


def ensure_collection_exists(client: Optional[QdrantClient] = None, collection_name: Optional[str] = None) -> None:
    """Ensures the specified collection exists with Cosine distance and 512 dimensions."""
    target_coll = collection_name or COLLECTION_NAME
    if client is None:
        client = get_qdrant_client()

    collections = client.get_collections().collections
    exists = any(c.name == target_coll for c in collections)

    if not exists:
        logger.info(f"Creating Qdrant collection '{target_coll}' (dim={VECTOR_DIM}, metric=Cosine)...")
        client.create_collection(
            collection_name=target_coll,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info(f"Qdrant collection '{target_coll}' created successfully.")


def tile_id_to_uuid(tile_id: str) -> str:
    """Generates a deterministic UUID from a tile_id string for Qdrant Point ID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, tile_id))


def encode_and_upsert_tiles_to_qdrant(
    tiles: List[TileCandidate],
    scene_id: str,
    acquisition_date: str,
    sensor: str = "Sentinel-2",
    region_id: str = "custom_region",
    collection_name: Optional[str] = None,
    client: Optional[QdrantClient] = None,
    batch_size: int = 64
) -> int:
    """
    Phase 1.8:
    1. Extracts normalized RGB arrays from TileCandidate list.
    2. Encodes RGB tiles into 512-dim RemoteCLIP embeddings in batches.
    3. Prepares PointStruct with rich metadata payload.
    4. Upserts points into target Qdrant collection (tile_embeddings or maxar_tile_embeddings).
    """
    if not tiles:
        return 0

    target_coll = collection_name or (MAXAR_COLLECTION_NAME if "maxar" in sensor.lower() else COLLECTION_NAME)

    if client is None:
        client = get_qdrant_client()

    ensure_collection_exists(client, target_coll)
    encoder = get_encoder()

    # Parse timestamp
    try:
        dt = datetime.fromisoformat(str(acquisition_date).replace("Z", "+00:00"))
        acq_ts = int(dt.timestamp())
    except Exception:
        acq_ts = int(datetime.utcnow().timestamp())

    total_upserted = 0

    for i in range(0, len(tiles), batch_size):
        chunk = tiles[i:i + batch_size]
        
        # Batch encode RGB arrays
        rgb_list = [t.rgb_data for t in chunk]
        embeddings = encoder.encode_image(rgb_list)

        points = []
        for tile, emb in zip(chunk, embeddings):
            point_id = tile_id_to_uuid(tile.tile_id)
            payload = {
                "tile_id": tile.tile_id,
                "scene_id": scene_id,
                "site_key": tile.site_key,
                "region_id": region_id,
                "sensor": sensor,
                "acquisition_date": acq_ts,
                "acquisition_date_iso": acquisition_date,
                "centroid": {
                    "lat": tile.centroid_lat,
                    "lon": tile.centroid_lon
                },
                "centroid_lat": tile.centroid_lat,
                "centroid_lon": tile.centroid_lon,
                "cloud_pct": tile.cloud_pct,
                "quality_confidence": tile.quality_confidence,
                "mean_ndvi": tile.mean_ndvi,
                "mean_ndwi": tile.mean_ndwi,
                "mean_ndbi": tile.mean_ndbi,
                "source_type": tile.source_type,
                "band_order": tile.band_order
            }
            points.append(
                PointStruct(
                    id=point_id,
                    vector=emb,
                    payload=payload
                )
            )

        client.upsert(
            collection_name=target_coll,
            points=points
        )
        total_upserted += len(points)

    logger.info(f"[VectorStore] Successfully encoded & upserted {total_upserted} tile embeddings into Qdrant '{target_coll}'.")
    return total_upserted
