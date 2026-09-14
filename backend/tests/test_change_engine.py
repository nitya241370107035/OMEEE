"""Unit tests for AeroLens Change Engine Pure Logic (Phase 1).

Validates:
- Spectral classification against exact user worked examples (§2.4)
- False-positive semantic contradiction discard
- Transition matrix classification (§2.5)
- Road development morphology sub-rule (elongation ratio > 4)
"""
import pytest
import numpy as np

from backend.services.change_engine.spectral_classifier import (
    compute_spectral_indices,
    classify_pixels,
    CLASS_WATER,
    CLASS_DENSE_VEGETATION,
    CLASS_SPARSE_VEGETATION,
    CLASS_MODERATE_VEGETATION,
    CLASS_BUILT_UP,
    CLASS_BARE_SOIL,
    CLASS_CONFUSION,
    CLASS_UNCLASSIFIED,
)
from backend.services.change_engine.semantic_change_filter import (
    filter_semantic_changes,
)
from backend.services.change_engine.change_type_rules import (
    lookup_change_type,
    vectorized_change_type_lookup,
    refine_road_morphology,
    TYPE_CONSTRUCTION,
    TYPE_ROAD_DEVELOPMENT,
    TYPE_CLEARANCE,
    TYPE_WATER_SHRINKAGE,
    TYPE_WATER_EXPANSION,
    TYPE_DEMOLITION,
)


class TestSpectralClassifier:
    def test_water_class(self):
        """💧 Water: NDVI [-0.50, 0.20], NDWI [0.20, 1.00], NDBI [-1.00, -0.10] -> Water."""
        ndvi = np.array([-0.20])
        ndwi = np.array([0.45])
        ndbi = np.array([-0.35])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_WATER

    def test_dense_vegetation(self):
        """🌳 Dense vegetation: NDVI [0.50, 0.90+], NDWI [-0.60, 0.10], NDBI [-0.60, -0.10] -> Dense Vegetation."""
        ndvi = np.array([0.70])
        ndwi = np.array([-0.20])
        ndbi = np.array([-0.30])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_DENSE_VEGETATION

    def test_sparse_vegetation(self):
        """🌱 Sparse vegetation: NDVI [0.20, <0.50), NDWI [-0.50, 0.10], NDBI [-0.40, 0.10] -> Sparse Vegetation."""
        ndvi = np.array([0.35])
        ndwi = np.array([-0.10])
        ndbi = np.array([-0.05])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_SPARSE_VEGETATION

    def test_built_up_strong_evidence(self):
        """🏢 Built-up: NDVI [-0.10, 0.30], NDWI [-0.50, 0.10], NDBI >= 0.15 to 0.50 -> Built-up."""
        ndvi = np.array([0.10])
        ndwi = np.array([-0.15])
        ndbi = np.array([0.25])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_BUILT_UP

    def test_bare_soil_open_land(self):
        """🟤 Bare soil / open land: NDVI [-0.20, <0.20), NDWI [-0.50, 0.10], NDBI [-0.20, <0.00) -> Bare Soil / Open Land."""
        ndvi = np.array([0.05])
        ndwi = np.array([-0.10])
        ndbi = np.array([-0.10])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_BARE_SOIL

    def test_built_up_bare_land_confusion(self):
        """⚠️ Built-up / Bare-land confusion: NDVI [-0.10, 0.20], NDWI [-0.50, 0.10], NDBI [0.00, <0.15) -> Confusion."""
        ndvi = np.array([-0.05])
        ndwi = np.array([-0.05])
        ndbi = np.array([0.08])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_CONFUSION

    def test_user_worked_example_before(self):
        """High canopy vegetation (NDVI 0.917, NDBI -0.898, NDWI -0.8) -> Dense Vegetation."""
        ndvi = np.array([0.917])
        ndbi = np.array([-0.898])
        ndwi = np.array([-0.8])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_DENSE_VEGETATION

    def test_user_worked_example_after(self):
        """After pixel with strong NDBI >= 0.15 -> Built-up."""
        ndvi = np.array([-0.091])
        ndbi = np.array([0.25])
        ndwi = np.array([-0.035])

        result = classify_pixels(ndvi, ndwi, ndbi)
        assert result[0] == CLASS_BUILT_UP

    def test_compute_indices_formulas(self):
        """Checks mathematical ratio bounds."""
        green = np.array([100.0])
        red = np.array([80.0])
        nir = np.array([500.0])
        swir = np.array([200.0])

        indices = compute_spectral_indices(green, red, nir, swir)
        # NDVI = (500 - 80) / (500 + 80) = 420 / 580 ~ 0.724
        assert 0.72 < indices["ndvi"][0] < 0.73
        # NDWI = (100 - 500) / (100 + 500) = -400 / 600 ~ -0.666
        assert -0.67 < indices["ndwi"][0] < -0.66
        # NDBI = (200 - 500) / (200 + 500) = -300 / 700 ~ -0.428
        assert -0.43 < indices["ndbi"][0] < -0.42


class TestSemanticFilter:
    def test_seasonal_false_positive_rejected(self):
        """Dense Veg -> Dense Veg marked as change by binary model must be discarded."""
        before = np.array([CLASS_DENSE_VEGETATION, CLASS_DENSE_VEGETATION])
        after = np.array([CLASS_DENSE_VEGETATION, CLASS_BUILT_UP])
        binary_mask = np.array([1, 1], dtype=np.uint8)

        surviving, stats = filter_semantic_changes(before, after, binary_mask)
        # Pixel 0 is seasonal noise (discarded)
        assert surviving[0] == False
        # Pixel 1 is genuine land-cover change (kept)
        assert surviving[1] == True
        assert stats["false_positives_rejected"] == 1
        assert stats["verified_changes"] == 1

    def test_unchanged_surface_and_phenology_removed_from_verified_mask(self):
        """Unchanged surface (before == after, intra-veg phenology, confusion noise) must be strictly discarded."""
        before = np.array([
            CLASS_BUILT_UP,            # unchanged
            CLASS_DENSE_VEGETATION,    # intra-veg phenology
            CLASS_UNCLASSIFIED,        # both unclassified
            CLASS_BARE_SOIL,           # ambiguous confusion transition (discarded)
            CLASS_SPARSE_VEGETATION,   # real change (kept)
        ])
        after = np.array([
            CLASS_BUILT_UP,
            CLASS_SPARSE_VEGETATION,
            CLASS_UNCLASSIFIED,
            CLASS_CONFUSION,
            CLASS_BUILT_UP,
        ])
        binary_mask = np.ones(5, dtype=np.uint8)

        surviving, stats = filter_semantic_changes(before, after, binary_mask)
        # Built-up -> Built-up: rejected
        assert surviving[0] == False
        # Dense Veg -> Sparse Veg: rejected as seasonal phenology
        assert surviving[1] == False
        # Unclassified -> Unclassified: rejected as unchanged
        assert surviving[2] == False
        # Bare Soil -> Confusion: rejected as unconfirmed / ambiguous
        assert surviving[3] == False
        # Sparse Veg -> Built-up: kept as real Construction
        assert surviving[4] == True
        assert stats["false_positives_rejected"] == 4
        assert stats["verified_changes"] == 1


class TestChangeTypeRules:
    def test_transitions(self):
        """Validates all key transition matrix pairings (§2.5)."""
        # Dense Veg -> Built-up
        assert lookup_change_type(CLASS_DENSE_VEGETATION, CLASS_BUILT_UP) == TYPE_CONSTRUCTION
        # Sparse Veg -> Built-up
        assert lookup_change_type(CLASS_SPARSE_VEGETATION, CLASS_BUILT_UP) == TYPE_CONSTRUCTION
        # Bare Soil -> Built-up
        assert lookup_change_type(CLASS_BARE_SOIL, CLASS_BUILT_UP) == TYPE_CONSTRUCTION
        # Dense Veg -> Bare Soil
        assert lookup_change_type(CLASS_DENSE_VEGETATION, CLASS_BARE_SOIL) == TYPE_CLEARANCE
        # Water -> Bare Soil (shrinkage)
        assert lookup_change_type(CLASS_WATER, CLASS_BARE_SOIL) == TYPE_WATER_SHRINKAGE
        # Bare Soil -> Water (expansion)
        assert lookup_change_type(CLASS_BARE_SOIL, CLASS_WATER) == TYPE_WATER_EXPANSION
        # Built-up -> Bare Soil (demolition / excavation)
        assert lookup_change_type(CLASS_BUILT_UP, CLASS_BARE_SOIL) == TYPE_DEMOLITION
        # Built-up -> Sparse Veg (revegetation / greening is suppressed from structural demolition)
        assert lookup_change_type(CLASS_BUILT_UP, CLASS_SPARSE_VEGETATION) == "No Change"
        # Bare Soil -> Confusion (suppressed as unconfirmed)
        assert lookup_change_type(CLASS_BARE_SOIL, CLASS_CONFUSION) == "No Change"
        # Confusion -> Built-up (suppressed as unconfirmed)
        assert lookup_change_type(CLASS_CONFUSION, CLASS_BUILT_UP) == "No Change"

    def test_road_morphology_elongation_subrule(self):
        """Elongated polygon (ratio > 4) reclassified from Construction to Road Development."""
        # 100x100 grid with a thin road strip (60 pixels long, 4 pixels wide -> ratio ~ 15 > 4)
        strip_mask = np.zeros((100, 100), dtype=bool)
        strip_mask[20:80, 48:52] = True

        result = refine_road_morphology(strip_mask, current_type=TYPE_CONSTRUCTION)
        assert result == TYPE_ROAD_DEVELOPMENT

        # A compact building blob (20x20 -> ratio ~ 1.0 < 4)
        building_mask = np.zeros((100, 100), dtype=bool)
        building_mask[20:40, 20:40] = True

        result_blob = refine_road_morphology(building_mask, current_type=TYPE_CONSTRUCTION)
        assert result_blob == TYPE_CONSTRUCTION


class TestBinaryChangeAdapter:
    def test_normalize_sentinel_rgb(self):
        """Validates robust percentile RGB normalization to uint8 [0, 255]."""
        from backend.services.change_engine.binary_change_adapter import normalize_sentinel_rgb
        red = np.random.uniform(100.0, 3000.0, (512, 512)).astype(np.float32)
        green = np.random.uniform(100.0, 3000.0, (512, 512)).astype(np.float32)
        blue = np.random.uniform(100.0, 3000.0, (512, 512)).astype(np.float32)

        rgb = normalize_sentinel_rgb(red, green, blue)
        assert rgb.shape == (512, 512, 3)
        assert rgb.dtype == np.uint8
        assert 0 <= rgb.min() <= 255
        assert 0 <= rgb.max() <= 255

    def test_load_real_tile_rgb(self):
        """Loads a real GeoTIFF tile from data/tiles and verifies 3-channel RGB."""
        import os
        import glob
        from backend.services.change_engine.binary_change_adapter import load_tile_rgb

        tiles = [t for t in glob.glob("/app/data/tiles/**/*.tif", recursive=True) if not t.endswith("_mask.tif")]
        if not tiles:
            pytest.skip("No sample GeoTIFF tiles available")

        rgb = load_tile_rgb(tiles[0])
        assert rgb.shape == (512, 512, 3)
        assert rgb.dtype == np.uint8


class TestVectorizer:
    def test_polygonize_change_mask(self):
        """Validates rasterio polygonization into GeoJSON features with area calculations."""
        from affine import Affine
        from backend.services.change_engine.vectorizer import polygonize_change_mask

        mask = np.zeros((512, 512), dtype=np.uint8)
        # Create a 40x40 square of change
        mask[100:140, 100:140] = 1

        geotransform = Affine(0.0001, 0.0, 72.0, 0.0, -0.0001, 23.0)
        features = polygonize_change_mask(mask, geotransform)

        assert len(features) >= 1
        feat = features[0]
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Polygon"
        assert "properties" in feat
        assert feat["properties"]["area_px"] > 0
        assert feat["properties"]["area_m2"] > 0


class TestTemporalAggregator:
    def test_earliest_supported_date_aggregation(self):
        """Verifies spatial union and earliest-supported-date tracking (§2.10)."""
        from backend.services.change_engine.temporal_aggregator import aggregate_temporal_sequence

        # Pair 1: 2022 -> 2023 detected change in region A
        poly_a = {
            "type": "Polygon",
            "coordinates": [[[72.0, 23.0], [72.01, 23.0], [72.01, 23.01], [72.0, 23.01], [72.0, 23.0]]]
        }
        pair1 = {
            "pair_index": 0,
            "date_before": "2022-02-26",
            "date_after": "2023-02-06",
            "change_pct": 2.5,
            "change_geojson": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": poly_a,
                    "properties": {"change_type": "Construction", "area_m2": 1000.0, "area_px": 50}
                }]
            }
        }

        # Pair 2: 2023 -> 2025 detected change in region B
        poly_b = {
            "type": "Polygon",
            "coordinates": [[[72.02, 23.0], [72.03, 23.0], [72.03, 23.01], [72.02, 23.01], [72.02, 23.0]]]
        }
        pair2 = {
            "pair_index": 1,
            "date_before": "2023-02-06",
            "date_after": "2025-03-12",
            "change_pct": 1.5,
            "change_geojson": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": poly_b,
                    "properties": {"change_type": "Clearance", "area_m2": 500.0, "area_px": 25}
                }]
            }
        }

        result = aggregate_temporal_sequence([pair1, pair2], site_key="test_site")
        assert len(result["pairwise"]) == 2
        assert len(result["overall"]["earliest_change_regions"]) == 2

        # Check earliest dates
        regions = result["overall"]["earliest_change_regions"]
        dates = [r["earliest_supported_date"] for r in regions]
        assert "2023-02-06" in dates
        assert "2025-03-12" in dates

        # Breakdown should have Construction and Clearance
        breakdown = result["overall"]["change_type_breakdown"]
        assert "Construction" in breakdown
        assert "Clearance" in breakdown
        assert sum(breakdown.values()) == pytest.approx(100.0, abs=0.5)


