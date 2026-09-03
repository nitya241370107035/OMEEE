"""
backend/api/routers/coverage.py
===============================
Coverage API Router — serves GeoJSON feature collection of all ingested AOI regions
directly from the PostgreSQL `ingestion_coverage` table.
"""

import json
import logging
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException
import psycopg2.extras

from backend.ingestion.db_writer import get_pg_connection

logger = logging.getLogger("coverage_router")
router = APIRouter(prefix="/api/v1/coverage", tags=["Coverage"])


@router.get("", response_model=Dict[str, Any])
def get_all_coverage():
    """
    Returns a GeoJSON FeatureCollection of all ingested regions in `ingestion_coverage`
    with their PostGIS polygons, tile counts, statuses, and timestamps.
    """
    try:
        conn = get_pg_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Query all coverage regions with GeoJSON geometry
            cur.execute("""
                SELECT 
                    region_id,
                    region_name,
                    status,
                    tile_count,
                    last_updated,
                    ST_AsGeoJSON(geometry) AS geom_json
                FROM ingestion_coverage
                ORDER BY last_updated DESC;
            """)
            rows = cur.fetchall()

            # Query total counts from tiles and scenes
            cur.execute("SELECT COUNT(*) AS total_tiles FROM tiles;")
            total_tiles_res = cur.fetchone()
            total_tiles = total_tiles_res["total_tiles"] if total_tiles_res else 0

            cur.execute("SELECT COUNT(*) AS total_scenes FROM scenes;")
            total_scenes_res = cur.fetchone()
            total_scenes = total_scenes_res["total_scenes"] if total_scenes_res else 0

        conn.close()

        features = []
        for r in rows:
            geom = json.loads(r["geom_json"]) if r.get("geom_json") else None
            if geom:
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "region_id": r["region_id"],
                        "region_name": r["region_name"] or r["region_id"],
                        "status": r["status"] or "done",
                        "tile_count": r["tile_count"] or 0,
                        "last_updated": r["last_updated"].isoformat() if r.get("last_updated") else None
                    }
                })

        return {
            "type": "FeatureCollection",
            "total_regions": len(features),
            "total_tiles": total_tiles,
            "total_scenes": total_scenes,
            "features": features
        }

    except Exception as e:
        logger.error(f"Error fetching coverage from database: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database coverage query failed: {str(e)}")
