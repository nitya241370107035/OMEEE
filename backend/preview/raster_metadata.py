"""
GeoTIFF Raster Metadata Data Model

Typed representations for raster geometry, CRS, band descriptions, and tags.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RasterMetadata:
    """Structured container for GeoTIFF raster metadata."""
    file_name: str
    full_path: str
    file_size_bytes: int
    file_size_formatted: str
    count: int                          # Number of bands
    width: int                          # Width in pixels
    height: int                         # Height in pixels
    dimensions: Tuple[int, int]         # (height, width)
    crs: str                            # Coordinate reference system string (e.g. 'EPSG:4326')
    transform: Tuple[float, ...]        # Affine matrix elements
    bounds: Tuple[float, float, float, float]  # (west, south, east, north)
    resolution: Tuple[float, float]     # (pixel_width, pixel_height)
    dtypes: List[str]                   # Data types per band
    nodata: Optional[float] = None      # Raster NoData value
    file_category: str = "UNKNOWN"      # 'WORKING_CANVAS', 'QUALITY_LAYER', 'FINAL_TILE', 'UNKNOWN'
    tags: Dict[str, Any] = field(default_factory=dict)
    band_descriptions: List[str] = field(default_factory=list)
    colorinterp: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Converts metadata into serializable dictionary."""
        return {
            "file_name": self.file_name,
            "full_path": self.full_path,
            "file_category": self.file_category,
            "file_size_bytes": self.file_size_bytes,
            "file_size_formatted": self.file_size_formatted,
            "count": self.count,
            "width": self.width,
            "height": self.height,
            "dimensions": f"{self.height} x {self.width}",
            "crs": self.crs,
            "transform": [round(x, 8) for x in self.transform],
            "bounds": [round(b, 6) for b in self.bounds],
            "resolution": [round(r, 8) for r in self.resolution],
            "dtypes": self.dtypes,
            "nodata": self.nodata,
            "tags": self.tags,
            "band_descriptions": self.band_descriptions,
            "colorinterp": self.colorinterp
        }
