"""
GeoTIFF Preview & Raster Inspection Package.
"""

from .geotiff_reader import read_geotiff, classify_geotiff_category
from .raster_metadata import RasterMetadata
from .statistics import compute_raster_statistics, RasterStatistics
from .rgb_preview import (
    generate_raster_preview,
    render_rgb_composite,
    render_mask_preview,
    render_probability_heatmap,
    render_mask_overlay
)
from .preview_service import (
    GeoTIFFPreviewService,
    validate_tile_dimensions,
    discover_scene_groups,
    discover_tile_directories,
    check_aoi_association
)

__all__ = [
    "read_geotiff",
    "classify_geotiff_category",
    "RasterMetadata",
    "compute_raster_statistics",
    "RasterStatistics",
    "generate_raster_preview",
    "render_rgb_composite",
    "render_mask_preview",
    "render_probability_heatmap",
    "render_mask_overlay",
    "GeoTIFFPreviewService",
    "validate_tile_dimensions",
    "discover_scene_groups",
    "discover_tile_directories",
    "check_aoi_association"
]
