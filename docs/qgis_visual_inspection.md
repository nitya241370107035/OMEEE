# QGIS Visual Inspection & Manual Validation Guide

This document provides step-by-step instructions for inspecting and validating the intermediate and tiled GeoTIFF outputs produced by the preprocessing pipeline using [QGIS](https://qgis.org/) (Quantum GIS).

> [!NOTE]
> QGIS is an external desktop GIS tool used exclusively for developer visual validation and spatial inspection. It is **not** a Python runtime dependency and does **not** replace automated pytest suites.

---

## 1. Prerequisites & Installation

1. Download and install **QGIS Desktop 3.28+ (LTR)** from the official repository: [https://qgis.org/en/site/forusers/download.html](https://qgis.org/en/site/forusers/download.html).
2. Launch **QGIS Desktop**.

---

## 2. Loading Pipeline Outputs

The pipeline produces structured GeoTIFFs under `data/intermediate/` and `data/tiles/`:

```
data/
├── intermediate/
│   └── scene_2023/
│       ├── raw_canvas.tif
│       ├── cloud_probability.tif
│       ├── cloud_mask.tif
│       ├── shadow_mask.tif
│       ├── bad_mask.tif
│       └── normalized_canvas.tif
│
└── tiles/
    └── delhi_test_phase2/
        └── 2023-10-06/
            ├── tile_0000.tif
            ├── tile_0001.tif
            └── manifest.json
```

### Loading Steps:
1. Open a new project in QGIS (`Project` → `New`).
2. Set the Project CRS to **WGS 84 / EPSG:4326** (bottom-right status bar).
3. Open your file explorer and drag-and-drop the `.tif` files directly into the **Layers Panel** in QGIS (or use `Layer` → `Add Layer` → `Add Raster Layer`).
4. Drag-and-drop your AOI GeoJSON file (e.g. `tests/sample_aoi.geojson` or `data/custom_aoi.geojson`) to overlay vector boundaries.

---

## 3. Visual Comparison & Layer Stack

To validate data quality, stack the layers in the following vertical order (top to bottom):

```
[Top]     1. AOI Vector Boundary (Sample AOI GeoJSON - Red Outline, Transparent Fill)
          2. Cloud Mask (cloud_mask.tif - Singleband Pseudocolor: Cyan)
          3. Shadow Mask (shadow_mask.tif - Singleband Pseudocolor: Amber / Yellow)
          4. Cloud Probability (cloud_probability.tif - Heatmap: Viridis / Magma)
          5. Normalized RGB Canvas (normalized_canvas.tif - Multiband Color: Bands 1, 2, 3)
[Bottom]  6. Raw RGB Canvas (raw_canvas.tif - Multiband Color: Bands 1, 2, 3)
```

---

## 4. What to Inspect & Validate

| Validation Check | Expected Observation | What to Watch For |
| :--- | :--- | :--- |
| **AOI Alignment** | Canvas extends smoothly across the full AOI boundary with ~5% safety buffer. | Canvas cropping clipping inside the AOI boundary. |
| **Pixel Resolution** | ~10m ground sampling distance (`~0.00008983°` in EPSG:4326). | Blurry resampling or pixel aspect distortion. |
| **Cloud Mask Accuracy** | Cloud mask triggers sharply over white convective cloud cores. | False alarms over bright urban concrete, airport runways, or desert sand. |
| **Shadow Masking** | Shadow mask triggers in dark ground patches adjacent to detected clouds. | False alarms over dark asphalt or water bodies distant from clouds. |
| **Percentile Normalization** | Ground features (roads, buildings, agriculture) show rich contrast and tonal balance without oversaturation. | Washed out ground or crushed dark tones caused by cloud pixels skewing histogram. |
| **Tile Seamlessness** | Adjacent 512×512 tiles match seamlessly across borders with consistent radiometric tone. | Tile-to-tile brightness jumps or seamline artifacts. |

---

## 5. Inspecting Layer Metadata & GeoTIFF Tags in QGIS

For any loaded GeoTIFF:
1. Right-click the layer in the **Layers Panel** → select **Properties** (or press `Alt + Enter`).
2. Navigate to the **Information** tab to inspect:
   - **CRS**: `EPSG:4326 - WGS 84`
   - **Extent**: Bounding coordinates in degrees
   - **Width / Height**: Pixel dimensions
   - **Data Type**: `Byte` (uint8) or `Float32`
   - **Band Count**: 1 (mask/probability) or 3 (RGB composite) or 11 (full canvas stack)
   - **NoData Value**: `nan` or `None`
3. Navigate to the **Symbology** tab to adjust contrast stretching, gamma, or colormaps.
4. Navigate to the **Metadata** tab to view embedded GDAL tags and driver information.

---

## 6. Summary

Visual validation in QGIS confirms geometric alignment, radiometric normalization quality, and masking accuracy before initiating downstream model training or feature retrieval.
