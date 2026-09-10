"""Database Persistence for AeroLens Change Engine.

Implements §2.9 and §2.6:
Persists granular pairwise change events into `change_events`
and aggregated cross-pair regions with earliest-supported-date into `change_regions`.
"""
import json
import logging
from typing import Dict, Any, Optional, List
import psycopg2.extras

from backend.ingestion.db_writer import get_pg_connection

logger = logging.getLogger(__name__)


def save_sequence_results(site_key: str, analysis_result: Dict[str, Any]) -> Dict[str, int]:
    """Saves pairwise change events and overall change regions to PostgreSQL.

    Args:
        site_key: The unique site identifier.
        analysis_result: Output dict from SequenceOrchestrator.run_sequence.

    Returns:
        Dict with counts of inserted change_events and change_regions.
    """
    pairwise = analysis_result.get("pairwise", [])
    overall = analysis_result.get("overall", {})
    overall_geojson = overall.get("change_geojson", {})
    overall_features = overall_geojson.get("features", [])

    conn = get_pg_connection()
    events_inserted = 0
    regions_inserted = 0

    try:
        with conn.cursor() as cur:
            # 1. Clean up old runs for this site to keep latest fresh results
            cur.execute("DELETE FROM change_events WHERE site_key = %s;", (site_key,))
            cur.execute("DELETE FROM change_regions WHERE site_key = %s;", (site_key,))

            # 2. Insert pairwise change events
            for pair in pairwise:
                t_before = pair.get("tile_before_id", "")
                t_after = pair.get("tile_after_id", "")
                d_before = pair.get("date_before")
                d_after = pair.get("date_after")
                features = pair.get("change_geojson", {}).get("features", [])

                for f in features:
                    props = f.get("properties", {})
                    geom_json = json.dumps(f.get("geometry", {}))
                    c_type = props.get("change_type", "Unclassified Structural Change")
                    b_class = props.get("before_class", "Unclassified")
                    a_class = props.get("after_class", "Unclassified")
                    area_px = int(props.get("area_px", 0))
                    area_sq_m = float(props.get("area_sq_m", 0.0))

                    breakdown_payload = json.dumps({
                        "spectral_profile": props.get("spectral_profile"),
                        "binary_mask_url": pair.get("binary_mask_url"),
                        "filtered_mask_url": pair.get("filtered_mask_url"),
                        "rgb_before_url": pair.get("rgb_before_url"),
                        "rgb_after_url": pair.get("rgb_after_url"),
                        "ndvi_before_url": pair.get("ndvi_before_url"),
                        "ndvi_after_url": pair.get("ndvi_after_url"),
                        "candidate_pixels": pair.get("candidate_pixels"),
                        "false_positives_rejected": pair.get("false_positives_rejected"),
                        "changed_pixels": pair.get("changed_pixels"),
                        "change_pct": pair.get("change_pct"),
                        "cursor_sample_grid": pair.get("cursor_sample_grid"),
                        "pair_spectral_profile": pair.get("spectral_profile"),
                    })

                    cur.execute(
                        """
                        INSERT INTO change_events (
                            site_key, tile_before, tile_after, date_before, date_after,
                            change_type, before_class, after_class, area_px, area_sq_m,
                            confidence, detected_date, geom, confidence_breakdown
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            1.0, NOW(), ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s
                        );
                        """,
                        (
                            site_key, t_before, t_after, d_before, d_after,
                            c_type, b_class, a_class, area_px, area_sq_m,
                            geom_json, breakdown_payload
                        )
                    )
                    events_inserted += 1

            # 3. Insert overall change regions
            for f in overall_features:
                props = f.get("properties", {})
                geom_json = json.dumps(f.get("geometry", {}))
                c_type = props.get("change_type", "Unclassified Structural Change")
                earliest_date = props.get("earliest_supported_date")
                area_sq_m = float(props.get("area_sq_m", 0.0)) if props.get("area_sq_m") else None

                cur.execute(
                    """
                    INSERT INTO change_regions (
                        site_key, change_type, earliest_supported_date,
                        area_sq_m, geom, properties
                    ) VALUES (
                        %s, %s, %s,
                        %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s
                    );
                    """,
                    (
                        site_key, c_type, earliest_date,
                        area_sq_m, geom_json, json.dumps(props)
                    )
                )
                regions_inserted += 1

        conn.commit()
        logger.info(
            f"Saved change analysis for site '{site_key}': "
            f"{events_inserted} events, {regions_inserted} regions"
        )
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to persist change results for site '{site_key}': {e}", exc_info=True)
        raise
    finally:
        conn.close()

    return {
        "change_events_inserted": events_inserted,
        "change_regions_inserted": regions_inserted,
    }


def get_saved_sequence_analysis(site_key: str) -> Optional[Dict[str, Any]]:
    """Retrieves previously saved change analysis for a site_key from PostgreSQL."""
    conn = get_pg_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Query overall regions
            cur.execute(
                """
                SELECT 
                    region_id,
                    change_type,
                    earliest_supported_date,
                    area_sq_m,
                    properties,
                    ST_AsGeoJSON(geom) AS geom_json
                FROM change_regions
                WHERE site_key = %s
                ORDER BY region_id ASC;
                """,
                (site_key,)
            )
            region_rows = cur.fetchall()

            if not region_rows:
                return None

            # Query pairwise events grouped by (date_before, date_after)
            cur.execute(
                """
                SELECT 
                    event_id,
                    tile_before,
                    tile_after,
                    date_before,
                    date_after,
                    change_type,
                    before_class,
                    after_class,
                    area_px,
                    area_sq_m,
                    confidence_breakdown,
                    ST_AsGeoJSON(geom) AS geom_json
                FROM change_events
                WHERE site_key = %s AND geom IS NOT NULL
                ORDER BY date_before ASC, event_id ASC;
                """,
                (site_key,)
            )
            event_rows = cur.fetchall()

        # Build overall GeoJSON
        overall_features = []
        breakdown_counts: Dict[str, float] = {}
        total_region_area = 0.0

        for r in region_rows:
            geom = json.loads(r["geom_json"]) if r["geom_json"] else None
            props = r["properties"] or {}
            props["region_id"] = f"region_{r['region_id']}"
            props["change_type"] = r["change_type"]
            props["earliest_supported_date"] = (
                r["earliest_supported_date"].strftime("%Y-%m-%d")
                if r["earliest_supported_date"] else None
            )
            area = float(r["area_sq_m"]) if r["area_sq_m"] is not None else 0.0
            props["area_sq_m"] = round(area, 1)

            c_type = r["change_type"]
            breakdown_counts[c_type] = breakdown_counts.get(c_type, 0.0) + (area or 1.0)
            total_region_area += (area or 1.0)

            overall_features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": props
            })

        breakdown = {}
        if total_region_area > 0:
            for c_name, c_val in breakdown_counts.items():
                breakdown[c_name] = round((c_val / total_region_area) * 100.0, 1)

        # Group pairwise events
        pair_groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for er in event_rows:
            d_b = er["date_before"].strftime("%Y-%m-%d") if er["date_before"] else ""
            d_a = er["date_after"].strftime("%Y-%m-%d") if er["date_after"] else ""
            key = (d_b, d_a, er["tile_before"], er["tile_after"])
            if key not in pair_groups:
                pair_groups[key] = []
            
            geom = json.loads(er["geom_json"]) if er["geom_json"] else None
            cb = er.get("confidence_breakdown") or {}
            if isinstance(cb, str):
                try: cb = json.loads(cb)
                except Exception: cb = {}

            pair_groups[key].append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "event_id": er["event_id"],
                    "change_type": er["change_type"],
                    "before_class": er["before_class"],
                    "after_class": er["after_class"],
                    "area_px": er["area_px"],
                    "area_sq_m": er["area_sq_m"],
                    "spectral_profile": cb.get("spectral_profile"),
                },
                "_meta": cb
            })

        pairwise = []
        for idx, ((d_b, d_a, t_b, t_a), feat_list) in enumerate(pair_groups.items()):
            meta = feat_list[0].get("_meta", {}) if feat_list else {}
            # Clean up internal meta
            cleaned_feats = []
            for f in feat_list:
                cleaned_feats.append({
                    "type": f["type"],
                    "geometry": f["geometry"],
                    "properties": f["properties"]
                })

            pairwise.append({
                "pair_index": idx,
                "tile_before_id": t_b,
                "tile_after_id": t_a,
                "date_before": d_b,
                "date_after": d_a,
                "change_pct": meta.get("change_pct"),
                "candidate_pixels": meta.get("candidate_pixels"),
                "false_positives_rejected": meta.get("false_positives_rejected"),
                "changed_pixels": meta.get("changed_pixels"),
                "rgb_before_url": meta.get("rgb_before_url"),
                "rgb_after_url": meta.get("rgb_after_url"),
                "ndvi_before_url": meta.get("ndvi_before_url"),
                "ndvi_after_url": meta.get("ndvi_after_url"),
                "binary_mask_url": meta.get("binary_mask_url"),
                "filtered_mask_url": meta.get("filtered_mask_url"),
                "spectral_profile": meta.get("pair_spectral_profile"),
                "cursor_sample_grid": meta.get("cursor_sample_grid"),
                "change_geojson": {
                    "type": "FeatureCollection",
                    "features": cleaned_feats
                }
            })

        return {
            "site_key": site_key,
            "is_cached": True,
            "pairwise": pairwise,
            "overall": {
                "change_geojson": {
                    "type": "FeatureCollection",
                    "features": overall_features
                },
                "change_type_breakdown": breakdown,
                "total_regions": len(overall_features)
            }
        }
    except Exception as e:
        logger.error(f"Error reading saved change analysis for site '{site_key}': {e}", exc_info=True)
        return None
    finally:
        conn.close()
