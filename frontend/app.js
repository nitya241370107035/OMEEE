/**
 * AeroLens Frontend Application Logic
 * ====================================
 * Manages:
 *  1. Top navigation switching across pages (Map, Semantic Retrieval, Change Detection, Clustering, Review Tool)
 *  2. Leaflet world map initialization with high-resolution satellite layers
 *  3. Ingestion coverage polygon overlay from PostgreSQL database
 *  4. Interactive Leaflet Draw polygon creation with auto-closure
 *  5. Multi-year timeline selection & dynamic ingestion pipeline execution
 */

const API_BASE = window.location.origin;

// Global Map State
let map = null;
let currentBasemap = null;
let basemapLayers = {};
let coverageLayerGroup = null;
let drawControl = null;
let currentDrawLayer = null;
let coverageData = null;

// ============================================================
// 1. PAGE NAVIGATION SWITCHER
// ============================================================

window.switchPage = function(pageId) {
  // Update Nav Buttons
  document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(`nav-${pageId}`);
  if (activeBtn) activeBtn.classList.add('active');

  // Update View Panels
  document.querySelectorAll('.view-panel').forEach(panel => panel.classList.remove('active'));
  const activePanel = document.getElementById(`view-${pageId}`);
  if (activePanel) {
    activePanel.classList.add('active');
    if (pageId === 'map' && map) {
      setTimeout(() => map.invalidateSize(), 200);
    }
  }
};

// ============================================================
// 2. LEAFLET MAP & HIGH-RES SATELLITE LAYERS
// ============================================================

function initMap() {
  // Define High-Resolution Basemaps
  basemapLayers = {
    satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Esri World Imagery, Maxar, Earthstar Geographics',
      maxZoom: 19
    }),
    hybrid: L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', {
      attribution: 'Google Satellite Hybrid Imagery',
      maxZoom: 20
    }),
    dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; CartoDB & OpenStreetMap',
      subdomains: 'abcd',
      maxZoom: 19
    }),
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19
    })
  };

  // Initialize Map with whole world view centered on India/Central Asia
  map = L.map('leafletMap', {
    center: [23.5, 75.0],
    zoom: 5,
    minZoom: 2,
    maxZoom: 20,
    worldCopyJump: true,
    zoomControl: true
  });

  // Add default satellite layer
  currentBasemap = basemapLayers.satellite;
  currentBasemap.addTo(map);

  // Initialize Coverage Layer Group
  coverageLayerGroup = L.featureGroup().addTo(map);

  // Setup Leaflet Draw Handler
  setupLeafletDraw();

  // Load Database Ingestion Coverage Polygons
  loadCoverageRegions();
}

window.switchBasemap = function(type) {
  if (!basemapLayers[type] || !map) return;

  // Switch Active Button Style
  document.querySelectorAll('.basemap-option').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(`bm-${type}`);
  if (activeBtn) activeBtn.classList.add('active');

  // Replace Layer
  map.removeLayer(currentBasemap);
  currentBasemap = basemapLayers[type];
  currentBasemap.addTo(map);
};

// ============================================================
// 3. DATABASE INGESTION COVERAGE OVERLAY
// ============================================================

window.loadCoverageRegions = async function() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/coverage`);
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    coverageData = await res.json();

    // Clear previous coverage
    coverageLayerGroup.clearLayers();

    // Populate Region Dropdown
    const select = document.getElementById('regionSelect');
    if (select) {
      select.innerHTML = '<option value="all">🌐 All Ingested Sectors</option>';
    }

    // Update Header Tile Counter
    const archiveCountEl = document.getElementById('archiveTileCount');
    if (archiveCountEl) {
      archiveCountEl.innerText = `${coverageData.total_tiles || 0} TILES ONLINE`;
    }

    if (!coverageData.features || coverageData.features.length === 0) {
      return;
    }

    // Colors for distinct regions
    const sectorColors = ['#06b6d4', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#3b82f6'];

    coverageData.features.forEach((feat, idx) => {
      const color = sectorColors[idx % sectorColors.length];
      const props = feat.properties;

      // Add to Region Select Dropdown
      if (select) {
        const opt = document.createElement('option');
        opt.value = props.region_id;
        opt.innerText = `📍 ${props.region_name} (${props.tile_count} Tiles)`;
        select.appendChild(opt);
      }

      // Render GeoJSON Polygon
      const geoLayer = L.geoJSON(feat.geometry, {
        style: {
          color: color,
          weight: 2.5,
          opacity: 0.9,
          fillColor: color,
          fillOpacity: 0.18,
          dashArray: '4, 4'
        }
      });

      // Hover Tooltip
      geoLayer.bindTooltip(`
        <strong>${props.region_name}</strong><br/>
        <span style="font-family: monospace; font-size: 11px;">Tiles: ${props.tile_count} | Status: ${props.status}</span>
      `, { sticky: true });

      // Click Popup
      geoLayer.bindPopup(`
        <div class="popup-title">🛰️ ${props.region_name}</div>
        <div class="popup-stat"><strong>Region ID:</strong> ${props.region_id}</div>
        <div class="popup-stat"><strong>Embedded Tiles:</strong> ${props.tile_count}</div>
        <div class="popup-stat"><strong>Database Status:</strong> <span style="color: #34d399; font-weight: 600;">${props.status.toUpperCase()}</span></div>
        <div class="popup-stat"><strong>Updated:</strong> ${props.last_updated ? props.last_updated.substring(0, 10) : 'N/A'}</div>
      `);

      coverageLayerGroup.addLayer(geoLayer);
    });

    // Fit Map to Ingested Regions on initial load
    if (coverageLayerGroup.getLayers().length > 0) {
      map.fitBounds(coverageLayerGroup.getBounds(), { padding: [40, 40], maxZoom: 13 });
    }

  } catch (err) {
    console.error("Failed to load coverage regions:", err);
  }
};

window.onSelectRegion = function(regionId) {
  if (!coverageData || !coverageData.features) return;

  if (regionId === 'all') {
    fitAllAois();
    return;
  }

  const targetFeat = coverageData.features.find(f => f.properties.region_id === regionId);
  if (targetFeat) {
    const tempLayer = L.geoJSON(targetFeat.geometry);
    map.fitBounds(tempLayer.getBounds(), { padding: [60, 60], maxZoom: 14 });
  }
};

window.fitAllAois = function() {
  if (coverageLayerGroup && coverageLayerGroup.getLayers().length > 0) {
    map.fitBounds(coverageLayerGroup.getBounds(), { padding: [40, 40], maxZoom: 13 });
  }
};

// ============================================================
// 4. LEAFLET DRAW INTERACTIVE POLYGON CREATION
// ============================================================

function setupLeafletDraw() {
  map.on(L.Draw.Event.CREATED, function(e) {
    const layer = e.layer;
    if (currentDrawLayer) {
      map.removeLayer(currentDrawLayer);
    }
    currentDrawLayer = layer;
    map.addLayer(currentDrawLayer);

    const geojson = layer.toGeoJSON();
    
    // Auto-populate GeoJSON Input in Ingest Modal
    const geoInput = document.getElementById('geojsonInput');
    if (geoInput) {
      geoInput.value = JSON.stringify(geojson.geometry, null, 2);
    }

    // Auto-generate suggested Region ID
    const center = layer.getBounds().getCenter();
    const regionIdInput = document.getElementById('aoiRegionId');
    const regionNameInput = document.getElementById('aoiRegionName');
    if (regionIdInput && !regionIdInput.value) {
      regionIdInput.value = `region_${center.lat.toFixed(2)}_${center.lng.toFixed(2)}`;
    }
    if (regionNameInput && !regionNameInput.value) {
      regionNameInput.value = `Sector (${center.lat.toFixed(2)}°N, ${center.lng.toFixed(2)}°E)`;
    }

    // Open Ingest Modal for Confirmation
    openIngestModal();
  });
}

window.startLeafletDraw = function() {
  closeIngestModal();
  const polygonDrawer = new L.Draw.Polygon(map, {
    shapeOptions: {
      color: '#06b6d4',
      weight: 3,
      fillColor: '#06b6d4',
      fillOpacity: 0.25
    },
    allowIntersection: false,
    showArea: true
  });
  polygonDrawer.enable();
};

window.triggerDrawFromModal = function() {
  closeIngestModal();
  startLeafletDraw();
};

// ============================================================
// 5. INGESTION MODAL & TIMELINE CONTROLS
// ============================================================

let currentIngestMode = 'aoi'; // 'aoi' or 'file'
let selectedYears = 2;

window.openIngestModal = function() {
  document.getElementById('ingestModal').classList.add('open');
};

window.closeIngestModal = function() {
  document.getElementById('ingestModal').classList.remove('open');
};

window.switchIngestMode = function(mode) {
  currentIngestMode = mode;
  document.getElementById('tabAoiBtn').classList.toggle('active', mode === 'aoi');
  document.getElementById('tabFileBtn').classList.toggle('active', mode === 'file');
  document.getElementById('aoiFormSection').style.display = (mode === 'aoi') ? 'flex' : 'none';
  document.getElementById('fileFormSection').style.display = (mode === 'file') ? 'flex' : 'none';
};

window.setTimelineYears = function(years) {
  selectedYears = years;
  document.querySelectorAll('.timeline-chip').forEach(btn => btn.classList.remove('active'));
  event.target.classList.add('active');

  const today = new Date();
  const startYear = today.getFullYear() - years;
  const fromDate = `${startYear}-01-01`;
  const toDate = today.toISOString().split('T')[0];

  document.getElementById('aoiDateFrom').value = fromDate;
  document.getElementById('aoiDateTo').value = toDate;

  // Auto-adjust suggested time buckets based on years
  const bucketSelect = document.getElementById('aoiBuckets');
  if (years === 1) bucketSelect.value = "2";
  else if (years <= 3) bucketSelect.value = "2";
  else if (years <= 5) bucketSelect.value = "4";
  else bucketSelect.value = "10";
};

window.setCustomTimeline = function() {
  document.querySelectorAll('.timeline-chip').forEach(btn => btn.classList.remove('active'));
  event.target.classList.add('active');
};

// ============================================================
// 6. PIPELINE EXECUTION & REAL-TIME PROGRESS BAR
// ============================================================

let progressInterval = null;

function animateProgressBar() {
  const card = document.getElementById('ingestProgressCard');
  const bar = document.getElementById('ingestProgressBar');
  const label = document.getElementById('ingestStepLabel');
  const timer = document.getElementById('ingestTimer');
  const subtext = document.getElementById('ingestSubtext');

  card.style.display = 'flex';
  bar.style.width = '10%';
  label.innerText = '⚡ 1/5: Querying STAC Catalog & Selecting Granules...';
  subtext.innerText = 'Scanning AWS Earth Search STAC for cloud-free Sentinel-2 scenes...';

  let seconds = 0;
  timer.innerText = '0s';

  const steps = [
    { pct: 25, label: '🛰️ 2/5: Streaming Cloud-Optimized GeoTIFFs (B2, B3, B4, B8, B11)...', sub: 'Reprojecting rasters to EPSG:4326 working canvas...' },
    { pct: 50, label: '☁️ 3/5: Computing Cloud & Shadow Masks + 2-98% Normalization...', sub: 'Filtering bad pixels and scaling dynamic range across 5 bands...' },
    { pct: 70, label: '✂️ 4/5: Slicing 512x512 Tiles & Calculating NDVI, NDWI, NDBI...', sub: 'Computing multi-spectral vegetation, water, and built-up indices...' },
    { pct: 90, label: '🧠 5/5: Generating RemoteCLIP ViT-B-32 Vector Embeddings...', sub: 'Batch upserting 512-dim vectors into Qdrant and metadata into PostgreSQL...' }
  ];

  let stepIdx = 0;
  progressInterval = setInterval(() => {
    seconds++;
    timer.innerText = `${seconds}s`;

    if (seconds % 4 === 0 && stepIdx < steps.length) {
      bar.style.width = `${steps[stepIdx].pct}%`;
      label.innerText = steps[stepIdx].label;
      subtext.innerText = steps[stepIdx].sub;
      stepIdx++;
    }
  }, 1000);
}

function stopProgressBar(success, message) {
  if (progressInterval) clearInterval(progressInterval);
  const bar = document.getElementById('ingestProgressBar');
  const label = document.getElementById('ingestStepLabel');
  const alertBox = document.getElementById('ingestSuccessAlert');
  const submitBtn = document.getElementById('submitIngestBtn');

  submitBtn.disabled = false;

  if (success) {
    bar.style.width = '100%';
    label.innerText = '✅ Ingestion Pipeline Complete!';
    label.style.color = '#34d399';
    alertBox.style.display = 'block';
    alertBox.innerHTML = `<strong>SUCCESS:</strong> ${message}`;
  } else {
    label.innerText = '❌ Ingestion Failed';
    label.style.color = '#f43f5e';
    alertBox.style.display = 'block';
    alertBox.style.background = 'rgba(244, 63, 94, 0.15)';
    alertBox.style.borderColor = 'rgba(244, 63, 94, 0.4)';
    alertBox.style.color = '#fb7185';
    alertBox.innerHTML = `<strong>ERROR:</strong> ${message}`;
  }
}

window.startIngestion = async function() {
  const submitBtn = document.getElementById('submitIngestBtn');
  const alertBox = document.getElementById('ingestSuccessAlert');
  alertBox.style.display = 'none';
  submitBtn.disabled = true;

  animateProgressBar();

  try {
    if (currentIngestMode === 'aoi') {
      // Entry Point A: AOI Polygon
      const geojsonStr = document.getElementById('geojsonInput').value.trim();
      if (!geojsonStr) {
        throw new Error("Please draw a region on the map or paste GeoJSON polygon coordinates.");
      }

      let parsedGeojson;
      try {
        parsedGeojson = JSON.parse(geojsonStr);
      } catch (e) {
        throw new Error("Invalid GeoJSON JSON syntax. Please verify coordinates format.");
      }

      const payload = {
        geojson_polygon: parsedGeojson,
        region_id: document.getElementById('aoiRegionId').value.trim() || undefined,
        region_name: document.getElementById('aoiRegionName').value.trim() || undefined,
        date_from: document.getElementById('aoiDateFrom').value,
        date_to: document.getElementById('aoiDateTo').value,
        num_time_buckets: parseInt(document.getElementById('aoiBuckets').value, 10) || 2,
        populate_db: true
      };

      const res = await fetch(`${API_BASE}/api/v1/ingest/aoi`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || `Server returned error ${res.status}`);
      }

      const result = await res.json();
      stopProgressBar(true, `Ingested ${result.total_tiles_generated} tiles across ${result.scenes_processed.length} scenes in ${result.elapsed_seconds}s. Vector embeddings populated in Qdrant.`);

      // Reload Coverage and Zoom to New Region
      await loadCoverageRegions();
      if (result.region_id) {
        document.getElementById('regionSelect').value = result.region_id;
        onSelectRegion(result.region_id);
      }

    } else {
      // Entry Point B: Offline File Ingestion
      const filePath = document.getElementById('filePathInput').value.trim();
      if (!filePath) {
        throw new Error("Please provide the local file path to the GeoTIFF file.");
      }

      const bandOrder = document.getElementById('fileBandOrder').value.split(',');
      const payload = {
        file_path: filePath,
        region_id: document.getElementById('fileRegionId').value.trim() || undefined,
        custom_band_order: bandOrder,
        acquisition_date: document.getElementById('fileAcqDate').value,
        populate_db: true
      };

      const res = await fetch(`${API_BASE}/api/v1/ingest/file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || `Server returned error ${res.status}`);
      }

      const result = await res.json();
      stopProgressBar(true, `Ingested ${result.total_tiles_generated} tiles from offline GeoTIFF file in ${result.elapsed_seconds}s.`);

      await loadCoverageRegions();
    }

  } catch (err) {
    stopProgressBar(false, err.message);
  }
};

// ============================================================
// 7. INITIALIZE ON DOM LOAD
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
  initMap();
});
