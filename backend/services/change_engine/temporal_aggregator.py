"""Temporal Aggregator module for AeroLens Change Engine.

Implements §2.10:
- Year-wise pair preservation
- Cross-pair spatial union of surviving change polygons
- Earliest-supported-date tracking per changed region
- Change type breakdown statistics
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import defaultdict
import numpy as np
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import unary_union


def aggregate_temporal_sequence(
    pairwise_results: List[Dict[str, Any]],
    site_key: str = "",
) -> Dict[str, Any]:
    """Aggregates chronological sequential pair results into an overall multi-temporal change map.

    Args:
        pairwise_results: Chronologically ordered list of pair change results:
            [
                {
                    "pair_index": 0,
                    "date_before": "2022-02-26",
                    "date_after": "2023-02-06",
                    "change_pct": 3.4,
                    "change_geojson": { "type": "FeatureCollection", "features": [...] }
                },
                ...
            ]
        site_key: Identifier of the monitored spatial grid site

    Returns:
        Dict matching §2.8 response specification:
        {
            "site_key": site_key,
            "pairwise": pairwise_results,
            "overall": {
                "change_geojson": { "type": "FeatureCollection", "features": [...] },
                "change_type_breakdown": { "Construction": 62.0, ... },
                "earliest_change_regions": [
                    { "region_id": "r1", "earliest_supported_date": "2023-02-06", "change_type": "Construction", ... }
                ],
                "total_changed_area_m2": 15420.0
            }
        }
    """
    if not pairwise_results:
        return {
            "site_key": site_key,
            "pairwise": [],
            "overall": {
                "change_geojson": {"type": "FeatureCollection", "features": []},
                "change_type_breakdown": {},
                "earliest_change_regions": [],
                "total_changed_area_m2": 0.0,
            }
        }

    # Step 1: Collect all valid polygon features across pairs with metadata
    all_pair_features = []
    for pair in pairwise_results:
        date_after = pair.get("date_after")
        geojson = pair.get("change_geojson", {})
        for feat in geojson.get("features", []):
            poly = shape(feat["geometry"])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                all_pair_features.append({
                    "polygon": poly,
                    "date_after": str(date_after),
                    "change_type": feat.get("properties", {}).get("change_type", "Unclassified Structural Change"),
                    "area_m2": feat.get("properties", {}).get("area_m2", 0.0),
                    "area_px": feat.get("properties", {}).get("area_px", 0),
                    "properties": feat.get("properties", {}),
                })

    if not all_pair_features:
        return {
            "site_key": site_key,
            "pairwise": pairwise_results,
            "overall": {
                "change_geojson": {"type": "FeatureCollection", "features": []},
                "change_type_breakdown": {},
                "earliest_change_regions": [],
                "total_changed_area_m2": 0.0,
            }
        }

    # Step 2: Group polygons by change type or cluster spatially
    # Group by change_type for clean thematic union
    type_to_features = defaultdict(list)
    for item in all_pair_features:
        type_to_features[item["change_type"]].append(item)

    overall_features = []
    earliest_change_regions = []
    type_area_totals = defaultdict(float)
    region_counter = 1

    for c_type, items in type_to_features.items():
        polys = [it["polygon"] for it in items]
        unioned = unary_union(polys)

        # Separate individual polygons if unioned geometry is a MultiPolygon
        single_polys = []
        if isinstance(unioned, Polygon):
            single_polys = [unioned]
        elif isinstance(unioned, MultiPolygon):
            single_polys = list(unioned.geoms)

        for poly in single_polys:
            if poly.is_empty:
                continue

            # Earliest supported observation tracking:
            # Walk pairwise features in chronological order to find the first pair intersecting this polygon
            earliest_date = None
            total_m2 = 0.0

            for it in items:
                if poly.intersects(it["polygon"]):
                    if earliest_date is None or it["date_after"] < earliest_date:
                        earliest_date = it["date_after"]
                    total_m2 += it["area_m2"]

            if earliest_date is None:
                earliest_date = items[0]["date_after"]

            # Compute actual polygon geometric area in m2
            bounds = poly.bounds
            centroid_lat = (bounds[1] + bounds[3]) / 2.0
            deg_to_m_lat = 111320.0
            deg_to_m_lon = 111320.0 * np.cos(np.radians(centroid_lat))
            real_m2 = round(float(poly.area * deg_to_m_lat * deg_to_m_lon), 1)

            region_id = f"region_{region_counter}"
            region_counter += 1
            type_area_totals[c_type] += real_m2

            region_entry = {
                "region_id": region_id,
                "change_type": c_type,
                "earliest_supported_date": earliest_date,
                "area_m2": real_m2,
            }
            earliest_change_regions.append(region_entry)

            feat = {
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "region_id": region_id,
                    "change_type": c_type,
                    "earliest_supported_date": earliest_date,
                    "area_m2": real_m2,
                }
            }
            overall_features.append(feat)

    # Step 3: Compute percentage breakdown
    grand_total_m2 = sum(type_area_totals.values())
    breakdown = {}
    if grand_total_m2 > 0:
        for ct, a_m2 in type_area_totals.items():
            breakdown[ct] = round((a_m2 / grand_total_m2) * 100.0, 1)

    return {
        "site_key": site_key,
        "pairwise": pairwise_results,
        "overall": {
            "change_geojson": {
                "type": "FeatureCollection",
                "features": overall_features,
            },
            "change_type_breakdown": breakdown,
            "earliest_change_regions": earliest_change_regions,
            "total_changed_area_m2": round(grand_total_m2, 1),
        }
    }
