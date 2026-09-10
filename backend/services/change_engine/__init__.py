"""AeroLens Multi-Temporal Change Engine package.

Semantic-verified binary change detection engine implementing:
- Binary ChangeFormer inference (Phase B1)
- Spectral classification using independent per-tile NDVI/NDWI/NDBI (Phase B2)
- Semantic false-positive filtering (Phase B3)
- Transition matrix and road morphology rules (Phase B4)
- PostGIS/GeoJSON vectorization (Phase B5)
- Multi-temporal sequence orchestration and earliest-supported-date aggregation (Phases B6 & B7)
"""
