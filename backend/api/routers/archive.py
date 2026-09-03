"""
backend/api/routers/archive.py
==============================
Archive & Tile Discovery API Router — provides summary stats, region lists,
and tile metadata from PostgreSQL.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Query
import psycopg2.extras

from backend.ingestion.db_writer import get_pg_connection

logger = logging.getLogger("archive_router")
router = APIRouter(prefix="/api/v1/archive", tags=["Archive"])


@router.get("/stats", response_model=Dict[str, Any])
def get_archive_stats():
    """
    Returns global database archive statistics:
      - Total Tiles
      - Total Scenes
      - Total Ingested Regions
    """
    try:
        conn = get_pg_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total_tiles FROM tiles;")
            total_tiles = cur.fetchone()["total_tiles"]

            cur.execute("SELECT COUNT(*) AS total_scenes FROM scenes;")
            total_scenes = cur.fetchone()["total_scenes"]

            cur.execute("SELECT COUNT(*) AS total_regions FROM ingestion_coverage WHERE status = 'done';")
            total_regions = cur.fetchone()["total_regions"]

            cur.execute("""
                SELECT region_id, region_name, tile_count, status, last_updated 
                FROM ingestion_coverage 
                ORDER BY last_updated DESC;
            """)
            regions = cur.fetchall()

        conn.close()

        return {
            "total_tiles": total_tiles,
            "total_scenes": total_scenes,
            "total_regions": total_regions,
            "regions": [
                {
                    "region_id": r["region_id"],
                    "region_name": r["region_name"] or r["region_id"],
                    "tile_count": r["tile_count"],
                    "status": r["status"],
                    "last_updated": r["last_updated"].isoformat() if r["last_updated"] else None
                }
                for r in regions
            ]
        }
    except Exception as e:
        logger.error(f"Failed to fetch archive stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch archive stats: {str(e)}")


@router.get("/tiles", response_model=Dict[str, Any])
def get_tiles(
    region_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """
    Returns paginated list of tiles with their geometry, centroid, and multi-spectral indices.
    """
    try:
        conn = get_pg_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            where_clauses = []
            params = []

            if region_id and region_id != "all":
                where_clauses.append("t.tile_id LIKE %s OR t.scene_id LIKE %s")
                params.extend([f"%{region_id}%", f"%{region_id}%"])

            where_str = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            query = f"""
                SELECT 
                    t.tile_id,
                    t.scene_id,
                    t.site_key,
                    t.centroid_lat,
                    t.centroid_lon,
                    t.cloud_pct,
                    t.quality_confidence,
                    t.mean_ndvi,
                    t.mean_ndwi,
                    t.mean_ndbi,
                    t.thumbnail_path,
                    t.source_type,
                    t.created_at,
                    ST_AsGeoJSON(t.footprint_geom) AS footprint_json
                FROM tiles t
                {where_str}
                ORDER BY t.created_at DESC
                LIMIT %s OFFSET %s;
            """
            params.extend([limit, offset])
            cur.execute(query, params)
            rows = cur.fetchall()

            cur.execute(f"SELECT COUNT(*) AS total FROM tiles t {where_str};", params[:-2] if len(params) > 2 else [])
            total_count = cur.fetchone()["total"]

        conn.close()

        features = []
        for r in rows:
            geom = json.loads(r["footprint_json"]) if r.get("footprint_json") else None
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "tile_id": r["tile_id"],
                    "scene_id": r["scene_id"],
                    "site_key": r["site_key"],
                    "centroid_lat": r["centroid_lat"],
                    "centroid_lon": r["centroid_lon"],
                    "cloud_pct": r["cloud_pct"],
                    "quality_confidence": r["quality_confidence"],
                    "mean_ndvi": r["mean_ndvi"],
                    "mean_ndwi": r["mean_ndwi"],
                    "mean_ndbi": r["mean_ndbi"],
                    "thumbnail_path": r["thumbnail_path"],
                    "source_type": r["source_type"]
                }
            })

        return {
            "total": total_count,
            "count": len(features),
            "features": features
        }

    except Exception as e:
        logger.error(f"Failed to fetch tiles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch tiles: {str(e)}")
