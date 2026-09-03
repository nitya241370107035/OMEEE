"""
tests/test_phase1_1_stac_search.py
===================================
Unit Tests for Phase 1.1: Multi-Temporal STAC Search & Time Bucketing
"""

import sys
import pytest
from pathlib import Path
from datetime import date

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.stac_search import (
    split_date_range_into_buckets,
    search_multi_temporal_stac_scenes,
    STACSceneMetadata,
)


def test_split_date_range_into_two_buckets():
    buckets = split_date_range_into_buckets("2023-01-01", "2024-06-30", num_buckets=2)
    assert len(buckets) == 2
    assert buckets[0][0] == "historical_T1"
    assert buckets[0][1] == "2023-01-01"
    assert buckets[1][0] == "recent_T2"
    assert buckets[1][2] == "2024-06-30"


def test_split_date_range_into_four_buckets():
    buckets = split_date_range_into_buckets("2023-01-01", "2023-12-31", num_buckets=4)
    assert len(buckets) == 4
    for i, b in enumerate(buckets):
        assert b[0] == f"time_bucket_{i + 1}"


def test_multi_temporal_stac_search_ahmedabad():
    # Ahmedabad Bounding Box
    ahmedabad_bbox = (72.5219, 22.9964, 72.6775, 23.1276)
    
    scenes = search_multi_temporal_stac_scenes(
        bbox=ahmedabad_bbox,
        date_from="2023-01-01",
        date_to="2024-06-30",
        max_cloud_cover=15.0,
        num_buckets=2,
        offline_fallback=True
    )

    assert len(scenes) >= 2
    for s in scenes:
        assert isinstance(s, STACSceneMetadata)
        assert s.scene_id != ""
        assert s.acquisition_date != ""
        assert s.crs != ""
        assert s.cloud_cover <= 35.0
        assert s.assets.red != ""
        assert s.assets.green != ""
        assert s.assets.blue != ""
        assert s.assets.nir is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
