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

            cur.execute("SELECT tile_id FROM tiles;")
            valid_tiles = {r[0] for r in cur.fetchall()}

            # 2. Insert pairwise change events
            event_records = []
            for pair in pairwise:
                t_before = pair.get("tile_before_id")
                if t_before not in valid_tiles:
                    t_before = None
                t_after = pair.get("tile_after_id")
                if t_after not in valid_tiles:
                    t_after = None
                d_before = pair.get("date_before")
                d_after = pair.get("date_after")
                features = pair.get("change_geojson", {}).get("features", [])

                pair_meta = json.dumps({
                    "binary_mask_url": pair.get("binary_mask_url"),
                    "filtered_mask_url": pair.get("filtered_mask_url"),
                    "rgb_before_url": pair.get("rgb_before_url"),
                    "rgb_after_url": pair.get("rgb_after_url"),
                    "rgb_before_annotated_url": pair.get("rgb_before_annotated_url"),
                    "rgb_after_annotated_url": pair.get("rgb_after_annotated_url"),
                    "ndvi_before_url": pair.get("before_ndvi_url") or pair.get("ndvi_before_url"),
                    "ndvi_after_url": pair.get("after_ndvi_url") or pair.get("ndvi_after_url"),
                    "before_ndvi_url": pair.get("before_ndvi_url") or pair.get("ndvi_before_url"),
                    "after_ndvi_url": pair.get("after_ndvi_url") or pair.get("ndvi_after_url"),
                    "ndvi_delta_url": pair.get("ndvi_delta_url"),
                    "ndvi_before_annotated_url": pair.get("ndvi_before_annotated_url"),
                    "ndvi_after_annotated_url": pair.get("ndvi_after_annotated_url"),
                    "candidate_pixels": pair.get("candidate_pixels"),
                    "false_positives_rejected": pair.get("false_positives_rejected"),
                    "changed_pixels": pair.get("changed_pixels"),
                    "change_pct": pair.get("change_pct"),
                    "change_boxes": pair.get("change_boxes", []),
                    "water_extent_stats": pair.get("water_extent_stats"),
                    "cursor_sample_grid": pair.get("cursor_sample_grid"),
                    "pair_spectral_profile": pair.get("spectral_profile"),
                })

                for f_idx, f in enumerate(features):
                    props = f.get("properties", {})
                    geom_json = json.dumps(f.get("geometry", {}))
                    c_type = props.get("change_type", "Unclassified Structural Change")
                    b_class = props.get("before_class", "Unclassified")
                    a_class = props.get("after_class", "Unclassified")
                    area_px = int(props.get("area_px", 0))
                    area_sq_m = float(props.get("area_sq_m", 0.0))

                    f_meta = {
                        "spectral_profile": props.get("spectral_profile"),
                        "box_pct": props.get("box_pct"),
                        "transition_label": props.get("transition_label"),
                        "pixel_bbox": props.get("pixel_bbox"),
                    }
                    if f_idx == 0:
                        payload = json.dumps({
                            **json.loads(pair_meta),
                            **f_meta,
                        })
                    else:
                        payload = json.dumps(f_meta)

                    event_records.append((
                        site_key, t_before, t_after, d_before, d_after,
                        c_type, b_class, a_class, area_px, area_sq_m,
                        geom_json, payload
                    ))
                else:
                    event_records.append((
                        site_key, t_before, t_after, d_before, d_after,
                        "No Change", "Stable", "Stable", 0, 0.0,
                        json.dumps({"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.0, 0.000001], [0.000001, 0.000001], [0.000001, 0.0], [0.0, 0.0]]]}),
                        pair_meta
                    ))

            if event_records:
                psycopg2.extras.execute_batch(
                    cur,
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
                    event_records,
                    page_size=200
                )
            events_inserted = len(event_records)

            # 3. Insert overall change regions
            region_records = []
            for f in overall_features:
                props = f.get("properties", {})
                geom_json = json.dumps(f.get("geometry", {}))
                c_type = props.get("change_type", "Unclassified Structural Change")
                earliest_date = props.get("earliest_supported_date")
                area_sq_m = float(props.get("area_sq_m", 0.0)) if props.get("area_sq_m") else None

                region_records.append((
                    site_key, c_type, earliest_date,
                    area_sq_m, geom_json, json.dumps(props)
                ))

            if region_records:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO change_regions (
                        site_key, change_type, earliest_supported_date,
                        area_sq_m, geom, properties
                    ) VALUES (
                        %s, %s, %s,
                        %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s
                    );
                    """,
                    region_records,
                    page_size=200
                )
            regions_inserted = len(region_records)

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
                    "box_pct": cb.get("box_pct"),
                    "transition_label": cb.get("transition_label"),
                    "pixel_bbox": cb.get("pixel_bbox"),
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

            # Reconstruct or auto-derive change_boxes for cached sites
            raw_change_boxes = meta.get("change_boxes") or []
            # Sanitize historical change boxes to eliminate false demolition on normal land
            change_boxes = []
            for box in raw_change_boxes:
                b_type = str(box.get("change_type", ""))
                b_trans = str(box.get("transition_label", ""))
                if "Demo" in b_type or "Built-up → Ground" in b_trans:
                    b_cls = str(box.get("before_class", ""))
                    b_prof = box.get("spectral_profile", {}).get("before", {}) if isinstance(box.get("spectral_profile"), dict) else {}
                    b_ndbi = b_prof.get("ndbi")
                    b_ndvi = b_prof.get("ndvi")
                    if "Soil" in b_cls or "Ground" in b_cls or "Barren" in b_cls or "Land" in b_cls:
                        continue
                    if b_ndbi is not None and (b_ndbi < 0.10 or (b_ndvi is not None and b_ndvi >= b_ndbi)):
                        continue
                change_boxes.append(box)

            if not change_boxes and cleaned_feats:
                # Compute site bounding envelope across all features
                all_lons = []
                all_lats = []
                for cf in cleaned_feats:
                    g = cf.get("geometry") or {}
                    def _ext(coords):
                        for item in coords:
                            if isinstance(item[0], list): _ext(item)
                            else:
                                all_lons.append(item[0])
                                all_lats.append(item[1])
                    if g.get("coordinates"):
                        _ext(g["coordinates"])
                
                if all_lons and all_lats:
                    s_min_lon, s_max_lon = min(all_lons), max(all_lons)
                    s_min_lat, s_max_lat = min(all_lats), max(all_lats)
                    lon_span = max(s_max_lon - s_min_lon, 1e-6)
                    lat_span = max(s_max_lat - s_min_lat, 1e-6)

                    major_candidates = []
                    for c_idx, cf in enumerate(cleaned_feats):
                        cp = cf.get("properties", {})
                        ct = cp.get("change_type", "")
                        b_cls = cp.get("before_class", "")
                        a_cls = cp.get("after_class", "")
                        area = cp.get("area_sq_m") or 0.0

                        if ct == "No Change" or (b_cls == a_cls and "Veg" in b_cls and area < 50000):
                            continue
                        if "Demo" in ct and ("Soil" in b_cls or "Ground" in b_cls or "Barren" in b_cls or "Land" in b_cls):
                            continue

                        f_lons, f_lats = [], []
                        def _fl(coords):
                            for item in coords:
                                if isinstance(item[0], list): _fl(item)
                                else:
                                    f_lons.append(item[0])
                                    f_lats.append(item[1])
                        g = cf.get("geometry") or {}
                        if g.get("coordinates"):
                            _fl(g["coordinates"])

                        if not f_lons:
                            continue

                        f_min_lon, f_max_lon = min(f_lons), max(f_lons)
                        f_min_lat, f_max_lat = min(f_lats), max(f_lats)

                        x = round(((f_min_lon - s_min_lon) / lon_span) * 100.0, 2)
                        y = round(((s_max_lat - f_max_lat) / lat_span) * 100.0, 2)
                        w = round(max(3.5, ((f_max_lon - f_min_lon) / lon_span) * 100.0), 2)
                        h = round(max(3.5, ((f_max_lat - f_min_lat) / lat_span) * 100.0), 2)

                        # Set box_pct back on the feature properties as well
                        cp["box_pct"] = {"x": x, "y": y, "w": w, "h": h}

                        t_lbl = cp.get("transition_label")
                        if not t_lbl:
                            if "Construct" in ct or "Built" in a_cls: t_lbl = "Vegetation → Built-up"
                            elif "Clear" in ct or "Ground" in a_cls or "Soil" in a_cls: t_lbl = "Vegetation → Ground"
                            elif "Road" in ct: t_lbl = "Vegetation → Road"
                            elif "Water" in ct: t_lbl = "Land → Water (Extension)"
                            elif "Demo" in ct: t_lbl = "Built-up → Ground"
                            else: t_lbl = f"{b_cls} → {a_cls}"
                            cp["transition_label"] = t_lbl

                        major_candidates.append({
                            "id": c_idx + 1,
                            "change_type": ct,
                            "transition_label": t_lbl,
                            "before_class": b_cls,
                            "after_class": a_cls,
                            "area_m2": area,
                            "box_pct": {"x": x, "y": y, "w": w, "h": h},
                        })

                    # Filter: keep Construction, Road, Clearance, Demolition (valid), Water, or area >= 2000 m2
                    major_filtered = [
                        m for m in major_candidates
                        if any(k in m["change_type"] for k in ["Construct", "Road", "Clear", "Demo", "Water"])
                        or m["area_m2"] >= 2000.0
                    ]
                    major_filtered.sort(key=lambda item: item.get("area_m2", 0), reverse=True)
                    change_boxes = major_filtered[:25]

            # Reconstruct or compute water extent stats if missing
            water_stats = meta.get("water_extent_stats")
            if not water_stats:
                # Estimate from cursor_sample_grid if available
                csg = meta.get("cursor_sample_grid") or {}
                b_grid = csg.get("before", {}).get("class", [])
                a_grid = csg.get("after", {}).get("class", [])
                if b_grid and a_grid:
                    w_before = sum(row.count("Water") for row in b_grid) * 64
                    w_after = sum(row.count("Water") for row in a_grid) * 64
                    delta = w_after - w_before
                    is_ext = (delta >= 25)
                    is_shk = (delta <= -25)
                    water_stats = {
                        "before_pixels": w_before,
                        "after_pixels": w_after,
                        "delta_pixels": delta,
                        "new_water_pixels": max(0, delta),
                        "is_extension_proven": is_ext,
                        "is_shrinkage_proven": is_shk,
                        "status": "Extended" if is_ext else ("Shrunk" if is_shk else "Stable"),
                        "proof": f"Water Extent {'Expansion Verified' if is_ext else 'Stable'}: Δ {delta:+d} px."
                    }

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
                "rgb_before_annotated_url": meta.get("rgb_before_annotated_url"),
                "rgb_after_annotated_url": meta.get("rgb_after_annotated_url"),
                "ndvi_before_url": meta.get("before_ndvi_url") or meta.get("ndvi_before_url"),
                "ndvi_after_url": meta.get("after_ndvi_url") or meta.get("ndvi_after_url"),
                "before_ndvi_url": meta.get("before_ndvi_url") or meta.get("ndvi_before_url"),
                "after_ndvi_url": meta.get("after_ndvi_url") or meta.get("ndvi_after_url"),
                "ndvi_delta_url": meta.get("ndvi_delta_url"),
                "ndvi_before_annotated_url": meta.get("ndvi_before_annotated_url"),
                "ndvi_after_annotated_url": meta.get("ndvi_after_annotated_url"),
                "change_boxes": change_boxes,
                "water_extent_stats": water_stats,
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
