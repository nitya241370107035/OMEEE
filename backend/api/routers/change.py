"""
backend/api/routers/change.py
=============================
Change Detection API Router:
Resolves multi-temporal change pairs using per-pixel bad-mask exclusion
rather than coarse whole-tile discard.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException
import psycopg2.extras

from backend.ingestion.db_writer import get_pg_connection
from backend.services.change_pair import resolve_change_pair
from backend.services.change_engine.sequence_orchestrator import SequenceOrchestrator
from backend.services.change_engine.db_saver import (
    save_sequence_results,
    get_saved_sequence_analysis,
)

logger = logging.getLogger("change_router")
router = APIRouter(prefix="/api/v1/change", tags=["Change Detection"])


def sanitize_thumb_url(path: Optional[str]) -> Optional[str]:
    """Translates container or host disk paths into static web-accessible URLs."""
    if not path:
        return None
    p = path.replace("\\", "/")
    if "/data/" in p:
        return "/data/" + p.split("/data/", 1)[1]
    return p


@router.get("/grid", response_model=Dict[str, Any])
def get_change_grid():
    """
    Returns unique site grid footprints as GeoJSON for the interactive map.
    Cells with observation_count >= 2 are tagged with multi_temporal: true.
    """
    try:
        conn = get_pg_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    site_key,
                    ST_AsGeoJSON(geometry) AS footprint_json,
                    centroid_lat,
                    centroid_lon,
                    COUNT(*) AS observation_count,
                    MIN(acquisition_date) AS earliest_date,
                    MAX(acquisition_date) AS latest_date,
                    ARRAY_AGG(DISTINCT EXTRACT(YEAR FROM acquisition_date)::INT) AS available_years
                FROM tiles
                GROUP BY site_key, geometry, centroid_lat, centroid_lon
                ORDER BY observation_count DESC;
            """)
            rows = cur.fetchall()
        conn.close()

        features = []
        for r in rows:
            geom = json.loads(r["footprint_json"]) if r["footprint_json"] else None
            if not geom:
                continue

            obs_count = int(r["observation_count"])
            earliest_dt = r["earliest_date"].isoformat() if r["earliest_date"] else None
            latest_dt = r["latest_date"].isoformat() if r["latest_date"] else None
            years = [int(y) for y in (r["available_years"] or []) if y is not None]

            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "site_key": r["site_key"],
                    "centroid": [float(r["centroid_lat"]), float(r["centroid_lon"])] if r["centroid_lat"] is not None else None,
                    "observation_count": obs_count,
                    "multi_temporal": obs_count >= 2,
                    "earliest_date": earliest_dt,
                    "latest_date": latest_dt,
                    "available_years": years
                }
            })

        return {
            "type": "FeatureCollection",
            "features": features,
            "total_grids": len(features),
            "multi_temporal_count": sum(1 for f in features if f["properties"]["multi_temporal"])
        }
    except Exception as e:
        logger.error(f"Error generating change grid: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load change grid: {str(e)}")


@router.get("/site/{site_key}/timeline", response_model=Dict[str, Any])
def get_site_timeline(site_key: str):
    """
    Returns chronological observations for a given site_key, strictly locking the timeline
    to verified database timestamps and resolving all physical paths for Part 2.
    """
    try:
        conn = get_pg_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    tile_id,
                    scene_id,
                    site_key,
                    acquisition_date,
                    cloud_pct,
                    quality_confidence,
                    mean_ndvi,
                    mean_ndwi,
                    mean_ndbi,
                    thumbnail_path,
                    file_path,
                    bad_mask_path
                FROM tiles
                WHERE site_key = %s
                ORDER BY acquisition_date ASC;
            """, (site_key,))
            rows = cur.fetchall()
        conn.close()

        if not rows:
            raise HTTPException(status_code=404, detail=f"No observations found for site_key '{site_key}'")

        snapshots = []
        years_set = set()
        for r in rows:
            dt = r["acquisition_date"]
            iso_date = dt.isoformat() if dt else None
            date_str = dt.strftime("%Y-%m-%d") if dt else "Unknown"
            if dt:
                years_set.add(dt.year)

            snapshots.append({
                "tile_id": r["tile_id"],
                "scene_id": r["scene_id"],
                "acquisition_date": iso_date,
                "date": date_str,
                "cloud_pct": round(float(r["cloud_pct"]), 4) if r["cloud_pct"] is not None else 0.0,
                "quality_confidence": round(float(r["quality_confidence"]), 4) if r["quality_confidence"] is not None else 1.0,
                "mean_ndvi": round(float(r["mean_ndvi"]), 4) if r["mean_ndvi"] is not None else None,
                "mean_ndwi": round(float(r["mean_ndwi"]), 4) if r["mean_ndwi"] is not None else None,
                "mean_ndbi": round(float(r["mean_ndbi"]), 4) if r["mean_ndbi"] is not None else None,
                "thumbnail_url": sanitize_thumb_url(r["thumbnail_path"]),
                "file_path": r["file_path"],
                "bad_mask_path": r["bad_mask_path"]
            })

        # Multi-Temporal Sequence Stack Building
        multi_temporal_stack = []
        base_dt = snapshots[0]["acquisition_date"] if snapshots and snapshots[0]["acquisition_date"] else None
        base_dt_obj = rows[0]["acquisition_date"] if rows and rows[0]["acquisition_date"] else None
        
        for idx, s in enumerate(snapshots):
            curr_dt_obj = rows[idx]["acquisition_date"]
            elapsed_days = (curr_dt_obj - base_dt_obj).days if curr_dt_obj and base_dt_obj else 0

            multi_temporal_stack.append({
                "epoch_index": idx,
                "epoch_label": f"T{idx + 1}",
                "tile_id": s["tile_id"],
                "scene_id": s["scene_id"],
                "date": s["date"],
                "acquisition_date": s["acquisition_date"],
                "elapsed_days_from_baseline": elapsed_days,
                "elapsed_years_from_baseline": round(elapsed_days / 365.25, 2) if elapsed_days > 0 else 0.0,
                "cloud_pct": s["cloud_pct"],
                "quality_confidence": s["quality_confidence"],
                "mean_ndvi": s["mean_ndvi"],
                "mean_ndbi": s["mean_ndbi"],
                "mean_ndwi": s["mean_ndwi"],
                "thumbnail_url": s["thumbnail_url"],
                "geotiff_path": s["file_path"],
                "bad_mask_path": s["bad_mask_path"]
            })

        # Step-wise Sequential Transitions (T1 -> T2, T2 -> T3, ..., T(N-1) -> TN)
        sequential_transitions = []
        for i in range(len(multi_temporal_stack) - 1):
            e1 = multi_temporal_stack[i]
            e2 = multi_temporal_stack[i + 1]
            dt1 = rows[i]["acquisition_date"]
            dt2 = rows[i + 1]["acquisition_date"]
            step_days = (dt2 - dt1).days if dt2 and dt1 else 0

            d_ndvi = round(e2["mean_ndvi"] - e1["mean_ndvi"], 4) if e1["mean_ndvi"] is not None and e2["mean_ndvi"] is not None else None
            d_ndbi = round(e2["mean_ndbi"] - e1["mean_ndbi"], 4) if e1["mean_ndbi"] is not None and e2["mean_ndbi"] is not None else None

            sequential_transitions.append({
                "step_index": i,
                "transition_label": f"{e1['epoch_label']} → {e2['epoch_label']}",
                "from_epoch": e1["epoch_label"],
                "to_epoch": e2["epoch_label"],
                "from_date": e1["date"],
                "to_date": e2["date"],
                "elapsed_days": step_days,
                "elapsed_years": round(step_days / 365.25, 2),
                "delta_ndvi": d_ndvi,
                "delta_ndbi": d_ndbi,
                "pair_payload": {
                    "site_key": site_key,
                    "tile_before_id": e1["tile_id"],
                    "tile_after_id": e2["tile_id"],
                    "tile_before_path": e1["geotiff_path"],
                    "tile_after_path": e2["geotiff_path"],
                    "mask_before_path": e1["bad_mask_path"],
                    "mask_after_path": e2["bad_mask_path"]
                }
            })

        sorted_years = sorted(list(years_set))
        min_date = snapshots[0]["date"] if snapshots else None
        max_date = snapshots[-1]["date"] if snapshots else None

        return {
            "site_key": site_key,
            "observation_count": len(snapshots),
            "available_years": sorted_years,
            "min_date": min_date,
            "max_date": max_date,
            "multi_temporal_stack": multi_temporal_stack,
            "sequential_transitions": sequential_transitions,
            "snapshots": snapshots
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching timeline for site '{site_key}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch site timeline: {str(e)}")



class TilePairChangeRequest(BaseModel):
    tile_id_before: Optional[str] = Field(None, description="Database tile_id for baseline (t1)")
    tile_id_after: Optional[str] = Field(None, description="Database tile_id for subsequent (t2)")
    tile_before_path: Optional[str] = Field(None, description="Direct file path to baseline GeoTIFF")
    tile_after_path: Optional[str] = Field(None, description="Direct file path to subsequent GeoTIFF")
    mask_before_path: Optional[str] = Field(None, description="Direct file path to baseline bad-mask GeoTIFF")
    mask_after_path: Optional[str] = Field(None, description="Direct file path to subsequent bad-mask GeoTIFF")
    min_usable_fraction: float = Field(0.30, ge=0.05, le=0.95, description="Minimum clear ground fraction required")
    diff_threshold: float = Field(0.15, ge=0.01, le=1.0, description="Fixed-scale reflectance change threshold")


@router.post("/pair", response_model=Dict[str, Any])
def compute_pair_change(request: TilePairChangeRequest):
    """
    Computes pixel-precise multi-temporal change between two tiles using per-pixel bad-mask exclusion.
    """
    try:
        tif_before = request.tile_before_path
        tif_after = request.tile_after_path
        mask_before = request.mask_before_path
        mask_after = request.mask_after_path

        # If tile IDs are supplied, look up paths from PostgreSQL
        if request.tile_id_before and request.tile_id_after:
            conn = get_pg_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT tile_id, file_path, bad_mask_path FROM tiles WHERE tile_id IN (%s, %s);",
                    (request.tile_id_before, request.tile_id_after)
                )
                rows = {r["tile_id"]: r for r in cur.fetchall()}
            conn.close()

            if request.tile_id_before not in rows or request.tile_id_after not in rows:
                raise HTTPException(status_code=404, detail="One or both tile IDs not found in database.")

            tif_before = rows[request.tile_id_before]["file_path"]
            mask_before = rows[request.tile_id_before].get("bad_mask_path")
            tif_after = rows[request.tile_id_after]["file_path"]
            mask_after = rows[request.tile_id_after].get("bad_mask_path")

        if not tif_before or not tif_after:
            raise HTTPException(
                status_code=400,
                detail="Must provide either (tile_id_before and tile_id_after) or (tile_before_path and tile_after_path)."
            )

        report = resolve_change_pair(
            tile_before_path=tif_before,
            tile_after_path=tif_after,
            mask_before_path=mask_before,
            mask_after_path=mask_after,
            min_usable_fraction=request.min_usable_fraction,
            diff_threshold=request.diff_threshold
        )

        return {
            "status": "success",
            "tile_before": request.tile_id_before or str(tif_before),
            "tile_after": request.tile_id_after or str(tif_after),
            "result": report
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Change pair evaluation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Change detection failed: {str(e)}")


class SequenceAnalysisRequest(BaseModel):
    site_key: str = Field(..., description="Unique site key identifier to run multi-temporal sequence analysis for")
    tile_ids: Optional[List[str]] = Field(None, description="Optional subset of tile IDs in chronological order")
    force_refresh: bool = Field(False, description="If True, forces full model re-inference even if DB cache exists")
    save_to_db: bool = Field(True, description="Whether to persist results to change_events and change_regions")


@router.post("/analyze/sequence", response_model=Dict[str, Any])
def analyze_sequence(request: SequenceAnalysisRequest):
    """
    Executes Part 2 Multi-Temporal Semantic-Verified Change Detection across N chronological snapshots.
    Chains N-1 consecutive pairs:
    1. Gating with cloud/bad pixel masks ~(mask_before | mask_after)
    2. Binary change detection via MTKD-ChangeFormer
    3. Independent spectral classification via NDVI/NDWI/NDBI
    4. Semantic noise rejection (before_class == after_class discarded)
    5. Transition matrix & road elongation morphology classification
    6. Vectorization to GeoJSON polygons with area and geometry
    7. Multi-temporal aggregation resolving earliest-supported-date per region
    """
    try:
        # Check cache if not forcing refresh
        if not request.force_refresh and not request.tile_ids:
            cached = get_saved_sequence_analysis(request.site_key)
            if cached and cached.get("overall", {}).get("total_regions", 0) > 0:
                logger.info(f"Returning cached sequence analysis for site '{request.site_key}'")
                return cached

        # Fetch ordered snapshots from database
        conn = get_pg_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if request.tile_ids and len(request.tile_ids) > 0:
                cur.execute(
                    """
                    SELECT tile_id, scene_id, site_key, acquisition_date, file_path, bad_mask_path
                    FROM tiles
                    WHERE site_key = %s AND tile_id = ANY(%s)
                    ORDER BY acquisition_date ASC;
                    """,
                    (request.site_key, request.tile_ids)
                )
            else:
                cur.execute(
                    """
                    SELECT tile_id, scene_id, site_key, acquisition_date, file_path, bad_mask_path
                    FROM tiles
                    WHERE site_key = %s
                    ORDER BY acquisition_date ASC;
                    """,
                    (request.site_key,)
                )
            rows = cur.fetchall()
        conn.close()

        if len(rows) < 2:
            raise HTTPException(
                status_code=400,
                detail=f"At least 2 chronological snapshots are required for change analysis on site '{request.site_key}', found {len(rows)}"
            )

        snapshots = []
        for r in rows:
            dt = r["acquisition_date"]
            snapshots.append({
                "tile_id": r["tile_id"],
                "scene_id": r["scene_id"],
                "site_key": r["site_key"],
                "acquisition_date": dt.isoformat() if dt else None,
                "date": dt.strftime("%Y-%m-%d") if dt else "Unknown",
                "file_path": r["file_path"],
                "bad_mask_path": r["bad_mask_path"]
            })

        missing_files = [s["file_path"] for s in snapshots if s.get("file_path") and not os.path.exists(s["file_path"])]
        if missing_files:
            raise HTTPException(
                status_code=404,
                detail=f"GeoTIFF tile files missing on disk: {missing_files[:2]}"
            )

        logger.info(f"Running multi-temporal change orchestrator for site '{request.site_key}' with {len(snapshots)} snapshots")
        orchestrator = SequenceOrchestrator()
        result = orchestrator.run_sequence(request.site_key, snapshots)

        # Save to database if requested
        if request.save_to_db:
            try:
                db_stats = save_sequence_results(request.site_key, result)
                result["db_saved"] = True
                result["db_stats"] = db_stats
            except Exception as e:
                logger.warning(f"Failed to save results to DB: {e}")
                result["db_saved"] = False

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Multi-temporal sequence analysis error for site '{request.site_key}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Sequence analysis failed: {str(e)}")


@router.get("/analysis/{site_key}", response_model=Dict[str, Any])
def get_site_analysis(site_key: str):
    """
    Fetches previously persisted multi-temporal change analysis for a site_key,
    including earliest supported observation dates and vector polygons.
    """
    try:
        cached = get_saved_sequence_analysis(site_key)
        if not cached:
            raise HTTPException(status_code=404, detail=f"No change analysis found for site_key '{site_key}'")
        return cached
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching analysis for site '{site_key}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve site analysis: {str(e)}")

