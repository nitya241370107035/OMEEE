"""
backend/services/vector_search.py
==================================
Phase 1.8 & Semantic Retrieval: Vector Search Service & Agent-Ready Tool
========================================================================
PS Sections: 2.2.4 (Multimodal & Semantic Image Retrieval)

Retrieval service:
  1. Accepts text prompt OR image query + optional filters (date, sensor, min_quality, bbox)
  2. Encodes query using RemoteCLIP ViT-B-32
  3. Executes filtered kNN search in Qdrant `tile_embeddings`
  4. Joins results with PostgreSQL `tiles` & `scenes` for full metadata & geometry
  5. Returns clean, structured Pydantic SearchResponse
"""

import os
import sys
import time
import json
import logging
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
import psycopg2.extras
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchValue,
    Range,
)

from backend.services.encoder import get_encoder

log = logging.getLogger(__name__)

# Config & Connections
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "tile_embeddings")

PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = os.getenv("POSTGRES_PORT", "5434")
PG_DB = os.getenv("POSTGRES_DB", "eo_archive")
PG_USER = os.getenv("POSTGRES_USER", "eo_admin")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "eo_password")

PG_DSN = f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_USER} password={PG_PASSWORD}"


# ============================================================
# 1. Pydantic Contracts (Agent-Ready Input / Output Schemas)
# ============================================================

class SearchFilter(BaseModel):
    sensor: Optional[str] = Field(None, description="Filter by sensor, e.g. 'Sentinel-2' or 'sentinel2'")
    start_date: Optional[datetime] = Field(None, description="Start date for acquisition filtering")
    end_date: Optional[datetime] = Field(None, description="End date for acquisition filtering")
    min_quality: float = Field(0.0, ge=0.0, le=1.0, description="Minimum quality gate score (0.0 to 1.0)")
    cluster_id: Optional[str] = Field(None, description="Filter by HDBSCAN cluster ID")
    bbox: Optional[List[float]] = Field(
        None,
        description="Bounding box [min_lon, min_lat, max_lon, max_lat] in EPSG:4326"
    )


class SearchRequest(BaseModel):
    query_text: Optional[str] = Field(None, description="Natural language search prompt")
    query_image_path: Optional[str] = Field(None, description="Local path to reference image")
    query_image_bytes: Optional[bytes] = Field(None, description="Raw image bytes")
    filters: Optional[SearchFilter] = Field(default_factory=SearchFilter)
    limit: int = Field(10, ge=1, le=100, description="Number of top results to retrieve")
    analyst_id: str = Field("demo_analyst", description="Analyst identity for audit logging")


class SearchResultItem(BaseModel):
    tile_id: str
    score: float = Field(..., description="Cosine similarity score (0 to 1)")
    scene_id: Optional[str] = None
    site_key: Optional[str] = None
    acquisition_date: Optional[str] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    sensor: Optional[str] = None
    cloud_pct: Optional[float] = 0.0
    quality_confidence: Optional[float] = 1.0
    mean_ndvi: Optional[float] = None
    mean_ndwi: Optional[float] = None
    mean_ndbi: Optional[float] = None
    file_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_path: Optional[str] = None
    geometry_geojson: Optional[Dict[str, Any]] = None


class SearchResponse(BaseModel):
    query_type: str = Field(..., description="'text' or 'image'")
    query: str = Field(..., description="Query summary representation")
    total_found: int = Field(..., description="Number of results returned")
    filters_applied: Dict[str, Any] = Field(default_factory=dict)
    results: List[SearchResultItem] = Field(default_factory=list)
    execution_time_ms: float = Field(..., description="Latency in milliseconds")


# ============================================================
# 2. VectorSearchService Implementation
# ============================================================

class VectorSearchService:
    def __init__(self):
        self.encoder = get_encoder()
        self.qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, check_compatibility=False)

    def _get_pg_conn(self):
        return psycopg2.connect(PG_DSN)

    def _build_qdrant_filter(self, filters: SearchFilter) -> Optional[Filter]:
        """Translates SearchFilter to Qdrant Payload Filter conditions."""
        conditions = []

        if filters.sensor:
            conditions.append(
                FieldCondition(key="sensor", match=MatchValue(value=filters.sensor))
            )

        if filters.cluster_id:
            conditions.append(
                FieldCondition(key="cluster_id", match=MatchValue(value=filters.cluster_id))
            )

        # Acquisition date range (stored in Qdrant as UNIX timestamp seconds)
        if filters.start_date or filters.end_date:
            date_range = {}
            if filters.start_date:
                start_ts = int(filters.start_date.replace(tzinfo=timezone.utc).timestamp())
                date_range["gte"] = start_ts
            if filters.end_date:
                end_ts = int(filters.end_date.replace(tzinfo=timezone.utc).timestamp())
                date_range["lte"] = end_ts
            conditions.append(
                FieldCondition(key="acquisition_date", range=Range(**date_range))
            )

        # Quality gate (minimum quality confidence threshold)
        if filters.min_quality > 0.0:
            conditions.append(
                FieldCondition(key="quality_confidence", range=Range(gte=filters.min_quality))
            )

        if not conditions:
            return None
        return Filter(must=conditions)

    def search(self, request: SearchRequest) -> SearchResponse:
        t0 = time.time()

        # 1. Validation & Encoding
        if request.query_text:
            query_type = "text"
            query_repr = request.query_text
            query_vector = self.encoder.encode_text(request.query_text)
        elif request.query_image_path:
            query_type = "image"
            query_repr = f"file:{os.path.basename(request.query_image_path)}"
            query_vector = self.encoder.encode_image(request.query_image_path)
        elif request.query_image_bytes:
            query_type = "image"
            query_repr = "raw_bytes_query"
            query_vector = self.encoder.encode_image(request.query_image_bytes)
        else:
            raise ValueError("Invalid SearchRequest: Must provide either query_text, query_image_path, or query_image_bytes.")

        # 2. Build Qdrant Search Filter
        q_filter = None
        if request.filters:
            q_filter = self._build_qdrant_filter(request.filters)

        # 3. Execute Vector kNN Search
        if hasattr(self.qdrant, "query_points"):
            query_response = self.qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=query_vector,
                query_filter=q_filter,
                limit=request.limit * 2,
                with_payload=True
            )
            q_results = query_response.points
        else:
            q_results = self.qdrant.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                query_filter=q_filter,
                limit=request.limit * 2,
                with_payload=True
            )

        if not q_results:
            elapsed_ms = (time.time() - t0) * 1000.0
            return SearchResponse(
                query_type=query_type,
                query=query_repr,
                total_found=0,
                filters_applied=request.filters.dict(exclude_none=True) if request.filters else {},
                results=[],
                execution_time_ms=round(elapsed_ms, 2)
            )

        # 4. Extract tile IDs & scores from Qdrant results
        tile_scores = {}
        for r in q_results:
            tile_id = r.payload.get("tile_id")
            if tile_id:
                tile_scores[tile_id] = float(r.score)

        tile_ids_list = list(tile_scores.keys())

        # 5. Hydrate with PostgreSQL Metadata & Geometry
        results = self._hydrate_from_postgres(tile_ids_list, tile_scores, request.filters)

        # Cut off at requested limit
        results = results[:request.limit]
        elapsed_ms = (time.time() - t0) * 1000.0

        return SearchResponse(
            query_type=query_type,
            query=query_repr,
            total_found=len(results),
            filters_applied=request.filters.dict(exclude_none=True) if request.filters else {},
            results=results,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def _hydrate_from_postgres(
        self,
        tile_ids: List[str],
        tile_scores: Dict[str, float],
        filters: Optional[SearchFilter]
    ) -> List[SearchResultItem]:
        """Queries PostgreSQL for authoritative tile metadata and geometry."""
        if not tile_ids:
            return []

        try:
            conn = self._get_pg_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # If bounding box filter supplied, apply spatial filter in SQL
                spatial_sql = ""
                params = [tuple(tile_ids)]
                if filters and filters.bbox and len(filters.bbox) == 4:
                    min_lon, min_lat, max_lon, max_lat = filters.bbox
                    spatial_sql = " AND ST_Intersects(t.geometry, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
                    params.extend([min_lon, min_lat, max_lon, max_lat])

                sql = f"""
                SELECT
                    t.tile_id,
                    t.scene_id,
                    t.site_key,
                    t.acquisition_date,
                    t.sensor,
                    t.cloud_pct,
                    t.quality_confidence,
                    t.mean_ndvi,
                    t.mean_ndwi,
                    t.mean_ndbi,
                    t.centroid_lat,
                    t.centroid_lon,
                    t.file_path,
                    t.thumbnail_path,
                    ST_AsGeoJSON(t.geometry)::json AS geometry_geojson
                FROM tiles t
                WHERE t.tile_id IN %s {spatial_sql};
                """
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.close()

            items = []
            for r in rows:
                t_id = r["tile_id"]
                score = tile_scores.get(t_id, 0.0)
                
                acq_str = r["acquisition_date"].isoformat() if isinstance(r["acquisition_date"], datetime) else str(r["acquisition_date"])
                thumb_path = r["thumbnail_path"]
                thumb_url = f"/data/tiles/{r['site_key']}/{t_id}_thumb.jpg" if thumb_path else None

                items.append(
                    SearchResultItem(
                        tile_id=t_id,
                        score=round(score, 4),
                        scene_id=r["scene_id"],
                        site_key=r["site_key"],
                        acquisition_date=acq_str,
                        centroid_lat=r["centroid_lat"],
                        centroid_lon=r["centroid_lon"],
                        sensor=r["sensor"],
                        cloud_pct=r["cloud_pct"],
                        quality_confidence=r["quality_confidence"],
                        mean_ndvi=r["mean_ndvi"],
                        mean_ndwi=r["mean_ndwi"],
                        mean_ndbi=r["mean_ndbi"],
                        file_path=r["file_path"],
                        thumbnail_url=thumb_url,
                        thumbnail_path=thumb_path,
                        geometry_geojson=r["geometry_geojson"]
                    )
                )

            # Sort descending by similarity score
            items.sort(key=lambda x: x.score, reverse=True)
            return items

        except Exception as e:
            log.error(f"Failed to hydrate search results from PostgreSQL: {e}", exc_info=True)
            # Fallback to returning items with available info
            fallback_items = []
            for t_id, score in tile_scores.items():
                fallback_items.append(
                    SearchResultItem(tile_id=t_id, score=round(score, 4))
                )
            fallback_items.sort(key=lambda x: x.score, reverse=True)
            return fallback_items
