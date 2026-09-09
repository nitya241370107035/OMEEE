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
let discoveryMapLayerGroup = null;
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

  // Setup Live Mouse Coordinates HUD
  setupMouseCoordsHud();

  // Load Database Ingestion Coverage Polygons
  loadCoverageRegions();
}

// ============================================================
// 2.5 COORDINATE TELEPORT & MOUSE HUD SYSTEM
// ============================================================
let activeTeleportMarker = null;
let activeTeleportBox = null;

function setupMouseCoordsHud() {
  if (!map) return;
  const hudLatLon = document.getElementById('hudLatLon');
  const hudZoom = document.getElementById('hudZoom');

  map.on('mousemove', function(e) {
    if (hudLatLon) {
      hudLatLon.innerText = `Lat: ${e.latlng.lat.toFixed(4)}° | Lon: ${e.latlng.lng.toFixed(4)}°`;
    }
  });

  map.on('zoomend', function() {
    if (hudZoom) {
      hudZoom.innerText = map.getZoom();
    }
  });
}

window.parseCoordinateInput = function(raw) {
  if (!raw) return null;
  const clean = raw.trim();

  // Check if JSON or bracket array e.g. [minLon, minLat, maxLon, maxLat]
  if (clean.startsWith('[') && clean.endsWith(']')) {
    try {
      const arr = JSON.parse(clean);
      if (Array.isArray(arr) && arr.length === 4) {
        return { type: 'bbox', bbox: arr.map(Number) };
      }
      if (Array.isArray(arr) && arr.length === 2) {
        return { type: 'point', lat: Number(arr[0]), lon: Number(arr[1]) };
      }
    } catch (e) {}
  }

  // Comma or space separated numbers
  const nums = clean.replace(/[^\d.\-+,]/g, ' ')
                    .split(/[\s,]+/)
                    .filter(Boolean)
                    .map(Number);

  if (nums.length === 4) {
    // BBOX: [minLon, minLat, maxLon, maxLat]
    return { type: 'bbox', bbox: nums };
  }

  if (nums.length >= 2) {
    let lat = nums[0];
    let lon = nums[1];

    // Smart detection: if first coordinate is > 90 or < -90, it's [lon, lat], swap them
    if ((lat > 90 || lat < -90) && (lon >= -90 && lon <= 90)) {
      const temp = lat;
      lat = lon;
      lon = temp;
    }

    return { type: 'point', lat, lon };
  }

  return null;
};

window.executeTeleport = function() {
  const topInput = document.getElementById('teleportCoordInputTop');
  const sideInput = document.getElementById('teleportCoordInput');
  const val = (topInput && topInput.value.trim()) || (sideInput && sideInput.value.trim());

  if (!val) {
    alert('Please enter coordinates (e.g. 34.455, 73.315 or [minLat, minLon, maxLat, maxLon])');
    return;
  }

  if (topInput) topInput.value = val;
  if (sideInput) sideInput.value = val;

  const parsed = parseCoordinateInput(val);
  if (!parsed) {
    alert('Invalid coordinate format.\n\nSupported examples:\n• "28.6139, 77.2090" (Lat, Lon)\n• "34.455 73.315"\n• "28°36\'50\\"N, 77°12\'32\\"E" (DMS)\n• "77.10, 28.50, 77.30, 28.70" (BBox)');
    return;
  }

  if (parsed.type === 'point') {
    teleportToPoint(parsed.lat, parsed.lon, 15, `Target Point: ${parsed.lat.toFixed(4)}°, ${parsed.lon.toFixed(4)}°`);
  } else if (parsed.type === 'bbox') {
    teleportToBbox(parsed.bbox);
  }
};

window.executeFastTravel = window.executeTeleport;

window.teleportToPreset = function(presetStr) {
  if (!presetStr) return;
  const parts = presetStr.split(',');
  if (parts.length < 3) return;

  const lat = parseFloat(parts[0]);
  const lon = parseFloat(parts[1]);
  const zoom = parseInt(parts[2], 10) || 15;
  const label = parts.slice(3).join(',') || `Preset (${lat.toFixed(4)}, ${lon.toFixed(4)})`;

  const coordText = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  const topInput = document.getElementById('teleportCoordInputTop');
  const sideInput = document.getElementById('teleportCoordInput');
  if (topInput) topInput.value = coordText;
  if (sideInput) sideInput.value = coordText;

  const topSelect = document.getElementById('teleportPresetSelectTop');
  const sideSelect = document.getElementById('teleportPresetSelect');
  if (topSelect) topSelect.value = presetStr;
  if (sideSelect) sideSelect.value = presetStr;

  teleportToPoint(lat, lon, zoom, label);
};

window.teleportToPoint = function(lat, lon, zoom = 15, label = '') {
  if (!map) return;

  // Fly animation
  map.flyTo([lat, lon], zoom, {
    animate: true,
    duration: 1.8
  });

  // Remove previous teleport marker & box
  if (activeTeleportMarker) map.removeLayer(activeTeleportMarker);
  if (activeTeleportBox) map.removeLayer(activeTeleportBox);

  // Custom pulsing beacon icon
  const pulseIcon = L.divIcon({
    className: 'teleport-pulse-icon',
    html: '<div class="teleport-pulse-dot"></div><div class="teleport-pulse-ring"></div>',
    iconSize: [24, 24],
    iconAnchor: [12, 12]
  });

  activeTeleportMarker = L.marker([lat, lon], { icon: pulseIcon }).addTo(map);

  const popupContent = `
    <div style="font-family: 'Inter', sans-serif; font-size: 12px; color: #fff; min-width: 200px; padding: 4px;">
      <div style="font-weight: 700; color: #38bdf8; margin-bottom: 4px; font-size: 13px;">🚀 ${label || 'Target Location'}</div>
      <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #94a3b8; margin-bottom: 10px;">
        Lat: ${lat.toFixed(5)}°<br>Lon: ${lon.toFixed(5)}°
      </div>
      <div style="display: flex; flex-direction: column; gap: 6px;">
        <button onclick="prepareIngestFromPoint(${lat}, ${lon})" style="background: #06b6d4; color: #000; border: none; padding: 6px 10px; border-radius: 4px; font-weight: 600; cursor: pointer; font-size: 11px;">
          📥 Ingest This Location (5km AOI)
        </button>
        <button onclick="clearTeleportMarker()" style="background: rgba(255,255,255,0.1); color: #cbd5e1; border: 1px solid rgba(255,255,255,0.2); padding: 5px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;">
          ❌ Clear Marker
        </button>
      </div>
    </div>
  `;

  activeTeleportMarker.bindPopup(popupContent).openPopup();
};

window.teleportToBbox = function(bbox) {
  if (!map || !bbox || bbox.length !== 4) return;
  const [minLon, minLat, maxLon, maxLat] = bbox;

  // Fly to BBOX bounds
  map.flyToBounds([[minLat, minLon], [maxLat, maxLon]], {
    animate: true,
    duration: 1.8,
    padding: [40, 40]
  });

  if (activeTeleportMarker) map.removeLayer(activeTeleportMarker);
  if (activeTeleportBox) map.removeLayer(activeTeleportBox);

  // Draw highlight polygon for the BBOX
  activeTeleportBox = L.rectangle([[minLat, minLon], [maxLat, maxLon]], {
    color: '#06b6d4',
    weight: 2,
    dashArray: '6, 6',
    fillColor: '#06b6d4',
    fillOpacity: 0.15
  }).addTo(map);

  activeTeleportBox.bindPopup(`
    <div style="font-family: 'Inter', sans-serif; font-size: 12px; color: #fff; min-width: 220px; padding: 4px;">
      <div style="font-weight: 700; color: #38bdf8; margin-bottom: 4px;">📦 Target Bounding Box</div>
      <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #94a3b8; margin-bottom: 8px;">
        SW: ${minLat.toFixed(4)}°, ${minLon.toFixed(4)}°<br>
        NE: ${maxLat.toFixed(4)}°, ${maxLon.toFixed(4)}°
      </div>
      <button onclick="prepareIngestFromBbox(${minLon}, ${minLat}, ${maxLon}, ${maxLat})" style="background: #06b6d4; color: #000; border: none; padding: 6px 10px; border-radius: 4px; font-weight: 600; cursor: pointer; font-size: 11px; width: 100%;">
        📥 Ingest This Bounding Box
      </button>
    </div>
  `).openPopup();
};

window.clearTeleportMarker = function() {
  if (activeTeleportMarker && map) {
    map.removeLayer(activeTeleportMarker);
    activeTeleportMarker = null;
  }
  if (activeTeleportBox && map) {
    map.removeLayer(activeTeleportBox);
    activeTeleportBox = null;
  }
};

window.prepareIngestFromPoint = function(lat, lon) {
  // Generate ~5km x 5km box around point (approx 0.045 deg)
  const d = 0.0225;
  prepareIngestFromBbox(lon - d, lat - d, lon + d, lat + d);
};

window.prepareIngestFromBbox = function(minLon, minLat, maxLon, maxLat) {
  const geojsonPoly = {
    type: "Polygon",
    coordinates: [[
      [minLon, minLat],
      [maxLon, minLat],
      [maxLon, maxLat],
      [minLon, maxLat],
      [minLon, minLat]
    ]]
  };

  const geoInput = document.getElementById('aoiGeoJson');
  if (geoInput) {
    geoInput.value = JSON.stringify(geojsonPoly, null, 2);
  }

  const centerLat = (minLat + maxLat) / 2;
  const centerLon = (minLon + maxLon) / 2;
  const regionIdInput = document.getElementById('aoiRegionId');
  const regionNameInput = document.getElementById('aoiRegionName');
  if (regionIdInput) {
    regionIdInput.value = `sector_${centerLat.toFixed(2)}_${centerLon.toFixed(2)}`;
  }
  if (regionNameInput) {
    regionNameInput.value = `Sector (${centerLat.toFixed(2)}°N, ${centerLon.toFixed(2)}°E)`;
  }

  openIngestModal();
};

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
      select.innerHTML = '<option value="all">ð All Ingested Sectors</option>';
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
        opt.innerText = `ð ${props.region_name} (${props.tile_count} Tiles)`;
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
        <div class="popup-title">ð°ï¸ ${props.region_name}</div>
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
      regionNameInput.value = `Sector (${center.lat.toFixed(2)}Â°N, ${center.lng.toFixed(2)}Â°E)`;
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
let currentIngestionSensor = 'sentinel2'; // 'sentinel2' or 'maxar'
let selectedYears = 2;
let selectedGeoTiffFiles = [];

window.setIngestionSensor = function(sensor) {
  currentIngestionSensor = sensor;
  const cardSentinel = document.getElementById('sensorCardSentinel');
  const cardMaxar = document.getElementById('sensorCardMaxar');
  const sentinelGroup = document.getElementById('sentinelTimelineGroup');
  const maxarGroup = document.getElementById('maxarTimelineGroup');
  const descEl = document.getElementById('aoiDescriptionText');
  const bucketGroup = document.getElementById('bucketSelectGroup');

  if (sensor === 'maxar') {
    if (cardSentinel) {
      cardSentinel.style.border = '2px solid var(--border-color)';
      cardSentinel.style.background = 'rgba(255,255,255,0.02)';
    }
    if (cardMaxar) {
      cardMaxar.style.border = '2px solid var(--accent-cyan)';
      cardMaxar.style.background = 'rgba(6,182,212,0.15)';
    }
    if (sentinelGroup) sentinelGroup.style.display = 'none';
    if (maxarGroup) maxarGroup.style.display = 'block';
    if (bucketGroup) bucketGroup.style.display = 'none';
    if (descEl) {
      descEl.innerHTML = `<strong>Maxar High-Resolution Optical Ingestion:</strong> Fetches sub-meter orthorectified imagery from the Maxar/Esri Wayback archive (2020â2026), slices 512Ã512 georeferenced GeoTIFFs, computes the VARI index, and stores embeddings in dedicated Qdrant collection <code>maxar_tile_embeddings</code>.`;
    }
    setMaxarEpochPreset('2020_2026');
  } else {
    if (cardSentinel) {
      cardSentinel.style.border = '2px solid var(--accent-blue)';
      cardSentinel.style.background = 'rgba(56,189,248,0.12)';
    }
    if (cardMaxar) {
      cardMaxar.style.border = '2px solid var(--border-color)';
      cardMaxar.style.background = 'rgba(255,255,255,0.02)';
    }
    if (sentinelGroup) sentinelGroup.style.display = 'block';
    if (maxarGroup) maxarGroup.style.display = 'none';
    if (bucketGroup) bucketGroup.style.display = 'block';
    if (descEl) {
      descEl.innerHTML = `Define an AOI polygon and choose the historical timeline (e.g. 1â10 years). The pipeline fetches all covering Sentinel-2 scenes, cleans cloud/shadow masks, normalizes, slices 512x512 tiles, computes indices (NDVI/NDWI/NDBI), and embeds into Qdrant & PostgreSQL.`;
    }
    setTimelineYears(2);
  }
};

window.setMaxarEpochPreset = function(preset) {
  document.querySelectorAll('#maxarTimelineGroup .timeline-chip').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(
    preset === '2020_2026' ? 'chipMaxar2020_2026' :
    preset === '2022_2026' ? 'chipMaxar2022_2026' :
    preset === 'all' ? 'chipMaxarAll' : 'chipMaxarCustom'
  );
  if (activeBtn) activeBtn.classList.add('active');

  const dateFrom = document.getElementById('aoiDateFrom');
  const dateTo = document.getElementById('aoiDateTo');

  if (preset === '2020_2026') {
    if (dateFrom) dateFrom.value = '2020-08-12';
    if (dateTo) dateTo.value = '2026-08-05';
  } else if (preset === '2022_2026') {
    if (dateFrom) dateFrom.value = '2022-10-19';
    if (dateTo) dateTo.value = '2026-08-05';
  } else if (preset === 'all') {
    if (dateFrom) dateFrom.value = '2020-08-12';
    if (dateTo) dateTo.value = '2026-08-05';
  }
};

window.onDirectSensorChange = function(val) {
  const filterSensor = document.getElementById('filterSensor');
  if (filterSensor) filterSensor.value = val;
  const directSelects = document.querySelectorAll('#directSensorSelect');
  directSelects.forEach(s => s.value = val);
};

window.syncDirectSensor = function(val) {
  const directSelects = document.querySelectorAll('#directSensorSelect');
  directSelects.forEach(s => s.value = val || 'Sentinel-2');
};

window.handleGeoTiffFilesSelected = function(files) {
  if (!files || files.length === 0) return;
  selectedGeoTiffFiles = Array.from(files);
  const summaryEl = document.getElementById('selectedFilesSummary');
  if (summaryEl) {
    summaryEl.style.display = 'block';
    const names = selectedGeoTiffFiles.map(f => f.name).join(', ');
    summaryEl.innerHTML = `â <strong>${selectedGeoTiffFiles.length} file(s) selected:</strong> ${names}`;
  }
};

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
  document.querySelectorAll('#sentinelTimelineGroup .timeline-chip').forEach(btn => btn.classList.remove('active'));
  if (event && event.target && event.target.classList.contains('timeline-chip')) {
    event.target.classList.add('active');
  }

  const today = new Date();
  const startYear = today.getFullYear() - years;
  const fromDate = `${startYear}-01-01`;
  const toDate = today.toISOString().split('T')[0];

  document.getElementById('aoiDateFrom').value = fromDate;
  document.getElementById('aoiDateTo').value = toDate;

  // Auto-adjust suggested time buckets based on years
  const bucketSelect = document.getElementById('aoiBuckets');
  if (bucketSelect) {
    if (years === 1) bucketSelect.value = "2";
    else if (years <= 3) bucketSelect.value = "2";
    else if (years <= 5) bucketSelect.value = "4";
    else bucketSelect.value = "10";
  }
};

window.setCustomTimeline = function() {
  document.querySelectorAll('#sentinelTimelineGroup .timeline-chip').forEach(btn => btn.classList.remove('active'));
  if (event && event.target) event.target.classList.add('active');
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

  const isMaxar = currentIngestionSensor === 'maxar';
  label.innerText = isMaxar 
    ? 'ð 1/4: Connecting to Maxar Wayback Archive & Selecting Epochs...' 
    : 'â¡ 1/5: Querying STAC Catalog & Selecting Granules...';
  subtext.innerText = isMaxar
    ? 'Selecting 2020 baseline and contemporary sub-meter orthorectified releases...'
    : 'Scanning AWS Earth Search STAC for cloud-free Sentinel-2 scenes...';

  let seconds = 0;
  timer.innerText = '0s';

  const steps = isMaxar ? [
    { pct: 25, label: 'ð°ï¸ 2/4: Streaming High-Res Maxar WMTS Tiles & Stitching...', sub: 'Fetching sub-meter orthorectified image canvas across epochs...' },
    { pct: 50, label: 'âï¸ 3/4: Slicing 512Ã512 Georeferenced Tiles & Computing VARI...', sub: 'Generating EPSG:4326 GeoTIFFs with Visible Atmospherically Resistant Index...' },
    { pct: 75, label: 'ð§  4/4: RemoteCLIP ViT-B-32 Vector Embeddings & Indexing...', sub: 'Embedding optical features into Qdrant maxar_tile_embeddings & PostgreSQL...' },
    { pct: 90, label: 'ð¾ Finalizing Database Registration & Map Coverage...', sub: 'Writing georeferenced sector polygons to PostgreSQL...' }
  ] : [
    { pct: 25, label: 'ð°ï¸ 2/5: Streaming Cloud-Optimized GeoTIFFs (B2, B3, B4, B8, B11)...', sub: 'Reprojecting rasters to EPSG:4326 working canvas...' },
    { pct: 50, label: 'âï¸ 3/5: Computing Cloud & Shadow Masks + 2-98% Normalization...', sub: 'Filtering bad pixels and scaling dynamic range across 5 bands...' },
    { pct: 70, label: 'âï¸ 4/5: Slicing 512x512 Tiles & Calculating NDVI, NDWI, NDBI...', sub: 'Computing multi-spectral vegetation, water, and built-up indices...' },
    { pct: 90, label: 'ð§  5/5: Generating RemoteCLIP ViT-B-32 Vector Embeddings...', sub: 'Batch upserting 512-dim vectors into Qdrant and metadata into PostgreSQL...' }
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
    label.innerText = 'â Ingestion Pipeline Complete!';
    label.style.color = '#34d399';
    alertBox.style.display = 'block';
    alertBox.innerHTML = `<strong>SUCCESS:</strong> ${message}`;
  } else {
    label.innerText = 'â Ingestion Failed';
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

      const dateFromVal = document.getElementById('aoiDateFrom')?.value?.trim();
      const dateToVal = document.getElementById('aoiDateTo')?.value?.trim();

      if (currentIngestionSensor === 'maxar') {
        // -- Maxar Async Job: POST ? job_id ? poll GET /job/{id} --
        // Fixes ERR_EMPTY_RESPONSE for large AOIs that exceed browser timeout
        const startYr = dateFromVal ? parseInt(dateFromVal.slice(0, 4), 10) : 2020;
        const endYr = dateToVal ? parseInt(dateToVal.slice(0, 4), 10) : 2026;
        const yearsList = (startYr === endYr) ? [startYr] : [startYr, endYr];

        const maxarPayload = {
          geojson_polygon: parsedGeojson,
          region_id: document.getElementById('aoiRegionId')?.value?.trim() || undefined,
          region_name: document.getElementById('aoiRegionName')?.value?.trim() || undefined,
          years: yearsList,
          populate_db: true
        };

        console.log("[AeroLens] Submitting Maxar async job:", maxarPayload);

        // Step 1: Submit  returns job_id INSTANTLY (no timeout risk)
        const submitRes = await fetch(`${API_BASE}/api/v1/ingest/maxar`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(maxarPayload)
        });

        if (!submitRes.ok) {
          const errData = await submitRes.json().catch(() => ({ detail: `HTTP ${submitRes.status}` }));
          throw new Error(errData.detail || `Server returned error ${submitRes.status}`);
        }

        const submitResult = await submitRes.json();
        const jobId = submitResult.job_id;
        const regionId = submitResult.region_id;
        console.log(`[AeroLens] Maxar job queued: ${jobId} for region: ${regionId}`);

        if (!jobId) {
          throw new Error("No job_id received from server.");
        }

        // Step 2: Poll every 3 seconds for job completion
        const progressStages = [
          { minPct: 0,  label: '??? 1/4: Connecting to Maxar Wayback Archive...', sub: 'Resolving release IDs for selected epochs...' },
          { minPct: 10, label: '??? 2/4: Streaming WMTS Tiles & Stitching Canvas...', sub: 'Downloading sub-meter optical imagery from Esri Wayback...' },
          { minPct: 30, label: '?? 3/4: Slicing 512×512 GeoTIFF Tiles...', sub: 'Polygon intersection & VARI index computation...' },
          { minPct: 60, label: '?? 4/4: RemoteCLIP Embeddings ? Qdrant & PostgreSQL...', sub: 'Indexing tile vectors into maxar_tile_embeddings...' },
        ];

        const finalResult = await new Promise((resolve, reject) => {
          let fakeProgress = 5;
          const pollInterval = setInterval(async () => {
            try {
              const pollRes = await fetch(`${API_BASE}/api/v1/ingest/job/${jobId}`);
              if (!pollRes.ok) {
                clearInterval(pollInterval);
                reject(new Error(`Poll failed: HTTP ${pollRes.status}`));
                return;
              }
              const job = await pollRes.json();
              console.log(`[AeroLens] Job ${jobId}:`, job.status, job.progress + '%', job.message);

              // Animate progress bar based on server progress
              fakeProgress = Math.max(fakeProgress, job.progress || fakeProgress + 3);
              fakeProgress = Math.min(fakeProgress, 95); // cap at 95 until done
              const bar = document.getElementById('ingestProgressBar');
              const labelEl = document.getElementById('ingestStepLabel');
              const subEl = document.getElementById('ingestSubLabel');
              if (bar) bar.style.width = fakeProgress + '%';

              // Pick UI stage label
              const stage = progressStages.slice().reverse().find(s => fakeProgress >= s.minPct);
              if (stage) {
                if (labelEl) labelEl.innerText = stage.label;
                if (subEl) subEl.innerText = stage.sub;
              }

              if (job.status === 'done') {
                clearInterval(pollInterval);
                resolve(job.result || {});
              } else if (job.status === 'failed') {
                clearInterval(pollInterval);
                reject(new Error(job.message || 'Maxar pipeline failed'));
              }
            } catch (pollErr) {
              clearInterval(pollInterval);
              reject(pollErr);
            }
          }, 3000);
        });

        // Handle final result
        if (!finalResult || finalResult.total_tiles_generated === 0) {
          const errDetail = (finalResult && finalResult.errors && finalResult.errors.length > 0)
            ? finalResult.errors.join("; ")
            : "No tiles extracted. Check polygon coverage area.";
          stopProgressBar(false, `Maxar returned 0 tiles: ${errDetail}`);
          return;
        }

        const epochsCount = (finalResult.epochs_processed || []).length;
        stopProgressBar(true,
          `? Maxar Complete! ${finalResult.total_tiles_generated} sub-meter tiles, ` +
          `${epochsCount} epoch(s), ${finalResult.elapsed_seconds?.toFixed(1)}s. ` +
          `Indexed in 'maxar_tile_embeddings'.`
        );

        if (currentDrawLayer) { map.removeLayer(currentDrawLayer); currentDrawLayer = null; }
        await loadCoverageRegions();
        if (finalResult.region_id) {
          const sel = document.getElementById('regionSelect');
          if (sel) sel.value = finalResult.region_id;
          onSelectRegion(finalResult.region_id);
        }
        setTimeout(() => closeIngestModal(), 1800);

      } else {
        // Standard Sentinel-2 Multi-Spectral Ingestion
        const payload = {
          geojson_polygon: parsedGeojson,
          sensor: "sentinel2",
          region_id: document.getElementById('aoiRegionId')?.value?.trim() || undefined,
          region_name: document.getElementById('aoiRegionName')?.value?.trim() || undefined,
          date_from: dateFromVal && dateFromVal !== "" ? dateFromVal : undefined,
          date_to: dateToVal && dateToVal !== "" ? dateToVal : undefined,
          num_time_buckets: parseInt(document.getElementById('aoiBuckets')?.value, 10) || 2,
          populate_db: true
        };

        const res = await fetch(`${API_BASE}/api/v1/ingest/aoi`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
          throw new Error(errData.detail || `Server returned error ${res.status}`);
        }

        const result = await res.json();
        stopProgressBar(true, `Ingested ${result.total_tiles_generated} tiles across ${result.scenes_processed ? result.scenes_processed.length : 1} scenes in ${result.elapsed_seconds}s. Vector embeddings populated in Qdrant.`);

        if (currentDrawLayer) {
          map.removeLayer(currentDrawLayer);
          currentDrawLayer = null;
        }

        // Reload Coverage and Zoom to New Region
        await loadCoverageRegions();
        if (result.region_id) {
          const sel = document.getElementById('regionSelect');
          if (sel) sel.value = result.region_id;
          onSelectRegion(result.region_id);
        }

        setTimeout(() => {
          closeIngestModal();
        }, 1800);
      }

    } else {
      // Entry Point B: Offline File / Multi-File Ingestion
      const filePath = document.getElementById('filePathInput')?.value?.trim();
      const sensorVal = document.getElementById('fileSensor')?.value || 'auto';
      const bandOrderVal = document.getElementById('fileBandOrder')?.value;
      const customBandOrder = (bandOrderVal && bandOrderVal !== 'auto') ? bandOrderVal : undefined;
      const acqDate = document.getElementById('fileAcqDate')?.value || undefined;
      const regionId = document.getElementById('fileRegionId')?.value?.trim() || undefined;

      if (selectedGeoTiffFiles && selectedGeoTiffFiles.length > 0) {
        // Upload via FormData to /api/v1/ingest/upload
        const formData = new FormData();
        for (const f of selectedGeoTiffFiles) {
          formData.append('files', f);
        }
        if (sensorVal) formData.append('sensor', sensorVal);
        if (customBandOrder) formData.append('custom_band_order', customBandOrder);
        if (acqDate) formData.append('acquisition_date', acqDate);
        if (regionId) formData.append('region_id', regionId);

        const res = await fetch(`${API_BASE}/api/v1/ingest/upload`, {
          method: 'POST',
          body: formData
        });

        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || `Server returned error ${res.status}`);
        }

        const result = await res.json();
        stopProgressBar(true, `Uploaded and ingested ${result.total_tiles_generated} tiles from ${result.files_uploaded} file(s) in ${result.elapsed_seconds}s.`);
        await loadCoverageRegions();
        if (result.region_id) {
          document.getElementById('regionSelect').value = result.region_id;
          onSelectRegion(result.region_id);
        }
      } else if (filePath) {
        // Server Local Path / Directory
        const payload = {
          file_path: filePath,
          region_id: regionId,
          sensor: sensorVal,
          custom_band_order: (customBandOrder ? customBandOrder.split(',') : undefined),
          acquisition_date: acqDate,
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
        stopProgressBar(true, `Ingested ${result.total_tiles_generated} tiles from offline path in ${result.elapsed_seconds}s.`);
        await loadCoverageRegions();
        if (result.region_id) {
          document.getElementById('regionSelect').value = result.region_id;
          onSelectRegion(result.region_id);
        }
      } else {
        throw new Error("Please select one or more GeoTIFF files to upload, or specify a server local path.");
      }
    }

  } catch (err) {
    stopProgressBar(false, err.message);
  }
};

// ============================================================
// 8. PHASE 2.7 â SEMANTIC RETRIEVAL CHAT & TILE INSPECTOR
// ============================================================

let attachedSearchFile = null;
let currentSearchResults = [];
let currentInspectingTile = null;
let mapTileHighlightLayer = null;

window.toggleSearchFilterPopover = function() {
  const popover = document.getElementById('searchFilterPopover');
  if (!popover) return;
  const isHidden = popover.style.display === 'none' || popover.style.display === '';
  popover.style.display = isHidden ? 'block' : 'none';
};

window.resetSearchFilters = function() {
  document.getElementById('filterTopK').value = 5;
  document.getElementById('topKValueLabel').innerText = '5';
  document.getElementById('filterSensor').value = 'Sentinel-2';
  document.getElementById('filterStartDate').value = '';
  document.getElementById('filterEndDate').value = '';
  document.getElementById('filterMinQuality').value = '0.0';
  document.getElementById('filterMaxCloud').value = '100';
  document.getElementById('filterCountBadge').style.display = 'none';
};

window.onSearchImageSelected = function(event) {
  const file = event.target.files[0];
  if (!file) return;

  attachedSearchFile = file;
  const preview = document.getElementById('searchImageAttachmentPreview');
  const nameLabel = document.getElementById('attachedImageName');
  if (preview && nameLabel) {
    nameLabel.innerText = `ð· ${file.name}`;
    preview.style.display = 'inline-flex';
  }
};

window.clearAttachedSearchImage = function() {
  attachedSearchFile = null;
  const fileInput = document.getElementById('searchImageFileInput');
  if (fileInput) fileInput.value = '';
  const preview = document.getElementById('searchImageAttachmentPreview');
  if (preview) preview.style.display = 'none';
};

window.applyQuickPrompt = function(promptText) {
  const input = document.getElementById('searchPromptInput');
  if (input) {
    input.value = promptText;
    submitSemanticSearch();
  }
};

window.submitSemanticSearch = async function() {
  const textInput = document.getElementById('searchPromptInput');
  const promptText = textInput ? textInput.value.trim() : '';

  if (!promptText && !attachedSearchFile) {
    alert("Please enter a search prompt or upload a reference image.");
    return;
  }

  const chatFeed = document.getElementById('retrievalChatFeed');
  if (!chatFeed) return;

  // 1. Gather Filters
  const topK = parseInt(document.getElementById('filterTopK')?.value || '5', 10);
  const directSensor = document.getElementById('directSensorSelect')?.value;
  const popoverSensor = document.getElementById('filterSensor')?.value?.trim();
  const rawSensor = (directSensor && directSensor !== "") ? directSensor : popoverSensor;
  const sensor = (rawSensor && rawSensor !== "" && rawSensor !== "Any") ? rawSensor : undefined;
  const startDate = document.getElementById('filterStartDate')?.value || undefined;
  const endDate = document.getElementById('filterEndDate')?.value || undefined;
  const minQuality = parseFloat(document.getElementById('filterMinQuality')?.value || '0.0');
  const maxCloud = parseFloat(document.getElementById('filterMaxCloud')?.value || '100.0');

  // 2. Render User Message in Chat
  renderUserChatMessage(promptText, attachedSearchFile);

  // Clear inputs
  if (textInput) textInput.value = '';
  const searchFileToUpload = attachedSearchFile;
  clearAttachedSearchImage();

  // 3. Render Assistant Loading Message
  const loadingMsgId = `loading_${Date.now()}`;
  renderAssistantLoadingBubble(loadingMsgId);
  chatFeed.scrollTop = chatFeed.scrollHeight;

  try {
    let responseData = null;

    if (searchFileToUpload) {
      // Image-to-Image Query (Multipart)
      const formData = new FormData();
      formData.append('file', searchFileToUpload);
      formData.append('top_k', topK.toString());
      if (sensor) formData.append('sensor', sensor);
      if (startDate) formData.append('start_date', startDate);
      if (endDate) formData.append('end_date', endDate);
      formData.append('min_quality', minQuality.toString());
      formData.append('max_cloud_pct', maxCloud.toString());
      formData.append('min_similarity', '0.65');

      const res = await fetch(`${API_BASE}/api/v1/search/image`, {
        method: 'POST',
        body: formData
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `Server returned error ${res.status}`);
      }
      responseData = await res.json();

    } else {
      // Text Query (JSON)
      const payload = {
        query_text: promptText,
        top_k: topK,
        filters: {
          sensor: sensor || undefined,
          start_date: startDate ? new Date(startDate).toISOString() : undefined,
          end_date: endDate ? new Date(endDate).toISOString() : undefined,
          min_quality: minQuality > 0 ? minQuality : undefined,
          max_cloud_pct: maxCloud < 100 ? maxCloud : undefined
        }
      };

      const res = await fetch(`${API_BASE}/api/v1/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `Server returned error ${res.status}`);
      }
      responseData = await res.json();
    }

    // Remove Loading Bubble
    const loadingElem = document.getElementById(loadingMsgId);
    if (loadingElem) loadingElem.remove();

    // Render Search Results
    renderAssistantResultsBubble(responseData);
    chatFeed.scrollTop = chatFeed.scrollHeight;

  } catch (err) {
    const loadingElem = document.getElementById(loadingMsgId);
    if (loadingElem) loadingElem.remove();
    renderAssistantErrorBubble(err.message);
    chatFeed.scrollTop = chatFeed.scrollHeight;
  }
};

function renderUserChatMessage(text, file) {
  const chatFeed = document.getElementById('retrievalChatFeed');
  const msg = document.createElement('div');
  msg.className = 'chat-message user';

  let contentHtml = '';
  if (file) {
    const previewUrl = URL.createObjectURL(file);
    contentHtml += `<div style="margin-bottom: 6px;"><img src="${previewUrl}" style="max-height: 120px; border-radius: var(--radius-sm); border: 1px solid var(--border-color);" alt="Uploaded Query"></div>`;
  }
  if (text) {
    contentHtml += `<div class="chat-text" style="font-weight: 500;">${escapeHtml(text)}</div>`;
  }

  msg.innerHTML = `
    <div class="chat-avatar">ð¤</div>
    <div class="chat-bubble">
      ${contentHtml}
    </div>
  `;
  chatFeed.appendChild(msg);
}

function renderAssistantLoadingBubble(id) {
  const chatFeed = document.getElementById('retrievalChatFeed');
  const msg = document.createElement('div');
  msg.className = 'chat-message assistant';
  msg.id = id;
  msg.innerHTML = `
    <div class="chat-avatar">ð°ï¸</div>
    <div class="chat-bubble">
      <div style="display: flex; align-items: center; gap: 10px; color: var(--accent-cyan); font-size: 13px;">
        <span class="status-dot"></span>
        <span>Computing RemoteCLIP 512-dim embedding & searching Qdrant archive...</span>
      </div>
    </div>
  `;
  chatFeed.appendChild(msg);
}

function renderAssistantErrorBubble(errMsg) {
  const chatFeed = document.getElementById('retrievalChatFeed');
  const msg = document.createElement('div');
  msg.className = 'chat-message assistant';
  msg.innerHTML = `
    <div class="chat-avatar">â ï¸</div>
    <div class="chat-bubble" style="border-color: rgba(244, 63, 94, 0.4); background: rgba(244, 63, 94, 0.1);">
      <div style="color: var(--accent-rose); font-weight: 600; font-size: 13px; margin-bottom: 4px;">Search Failed</div>
      <div style="font-size: 12px; color: var(--text-secondary);">${escapeHtml(errMsg)}</div>
    </div>
  `;
  chatFeed.appendChild(msg);
}

function renderAssistantResultsBubble(response) {
  const chatFeed = document.getElementById('retrievalChatFeed');
  const msg = document.createElement('div');
  msg.className = 'chat-message assistant';

  currentSearchResults = response.results || [];

  let headerHtml = `
    <div class="chat-bubble-header">
      <span class="assistant-name">Search Results (${response.total_found} Matches)</span>
      <span class="assistant-meta">â¡ ${response.execution_time_ms} ms &bull; Cosine Similarity</span>
    </div>
  `;

  if (response.total_found === 0) {
    msg.innerHTML = `
      <div class="chat-avatar">ð°ï¸</div>
      <div class="chat-bubble">
        ${headerHtml}
        <div class="chat-text" style="color: var(--text-secondary);">
          ${response.query_type === 'image' ? 'No matching tiles found with similarity >= 65% for this reference image. Try broadening your filters or uploading a different scene.' : 'No matching tiles found for this query within the selected filter constraints. Try broadening your date range or adjusting quality filters.'}
        </div>
      </div>
    `;
    chatFeed.appendChild(msg);
    return;
  }

  // Build Results Grid
  let gridHtml = `<div class="retrieval-results-grid">`;
  response.results.forEach((item, idx) => {
    const scorePct = (item.score * 100).toFixed(1);
    const thumbUrl = getTileThumbnailUrl(item);
    const dateStr = item.acquisition_date ? item.acquisition_date.split('T')[0] : 'Unknown Date';

    const ndviVal = item.mean_ndvi !== null && item.mean_ndvi !== undefined ? item.mean_ndvi.toFixed(2) : '-';
    const ndwiVal = item.mean_ndwi !== null && item.mean_ndwi !== undefined ? item.mean_ndwi.toFixed(2) : '-';
    const ndbiVal = item.mean_ndbi !== null && item.mean_ndbi !== undefined ? item.mean_ndbi.toFixed(2) : '-';

    gridHtml += `
      <div class="result-card">
        <div class="result-thumb-wrap">
          <img class="result-thumb-img" src="${thumbUrl}" alt="${item.tile_id}" onerror="handleTileThumbError(this, '${item.tile_id}')">
          <div class="result-score-badge">${scorePct}% Match</div>
        </div>
        <div class="result-body">
          <div class="result-title-row">
            <span class="result-tile-id">#${idx + 1} &bull; ${item.tile_id}</span>
            <span class="result-date">ð ${dateStr}</span>
          </div>

          <!-- Spot Description (Phase 2.4) -->
          <div class="result-spot-desc">
            ${escapeHtml(item.spot_description || 'Analysis complete.')}
          </div>

          <!-- Spectral Index Mini-Pills -->
          <div class="result-spectral-chips">
            <span class="spec-chip ndvi">NDVI ${ndviVal}</span>
            <span class="spec-chip ndwi">NDWI ${ndwiVal}</span>
            <span class="spec-chip ndbi">NDBI ${ndbiVal}</span>
          </div>

          <!-- Actions -->
          <div class="result-card-actions">
            <button class="result-action-btn" onclick="openTileInspect('${item.tile_id}')">
              🔍 Inspect
            </button>
            <button class="result-action-btn" onclick="openTileInMap('${item.tile_id}', ${item.centroid_lat || 'null'}, ${item.centroid_lon || 'null'}, ${JSON.stringify(item.geometry_geojson || null).replace(/"/g, '&quot;')})">
              🗺️ Open in Map
            </button>
            <button class="result-action-btn" onclick="discoverSimilarFromTile('${item.tile_id}')" style="color: #38bdf8; font-weight: 600;" title="Discover similar sites across the entire archive">
              ⚡ Find Similar
            </button>
          </div>
        </div>
      </div>
    `;
  });
  gridHtml += `</div>`;

  msg.innerHTML = `
    <div class="chat-avatar">ð°ï¸</div>
    <div class="chat-bubble" style="width: 100%;">
      ${headerHtml}
      ${gridHtml}
    </div>
  `;
  chatFeed.appendChild(msg);
}

// Fallback image generator for missing/mock tile previews
window.getTileThumbnailUrl = function(item) {
  if (item.thumbnail_url && !item.thumbnail_url.includes('/null/')) {
    let t = item.thumbnail_url.trim();
    if (t.startsWith('//')) t = t.replace(/^\/+/, '/');
    if (t.startsWith('/app/')) t = t.replace('/app/', '/');
    else if (t.startsWith('app/')) t = t.replace('app/', '/');
    return t.startsWith('http') ? t : `${API_BASE}${t.startsWith('/') ? '' : '/'}${t}`;
  }
  if (item.site_key && item.site_key !== 'null') {
    return `${API_BASE}/data/tiles/${item.site_key}/${item.tile_id}_preview.jpg`;
  }
  return createTileSvgDataUri(item.tile_id);
};

window.handleTileThumbError = function(img, tileId) {
  img.onerror = null;
  img.src = createTileSvgDataUri(tileId);
};

function createTileSvgDataUri(tileId) {
  const cleanId = escapeHtml(tileId || 'Sentinel-2 Tile');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
    <rect width="512" height="512" fill="#0d1424"/>
    <defs>
      <radialGradient id="grad" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="#1e293b"/>
        <stop offset="100%" stop-color="#070a12"/>
      </radialGradient>
      <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
        <path d="M 32 0 L 0 0 0 32" fill="none" stroke="rgba(6,182,212,0.12)" stroke-width="1"/>
      </pattern>
    </defs>
    <rect width="512" height="512" fill="url(#grad)"/>
    <rect width="512" height="512" fill="url(#grid)"/>
    <circle cx="256" cy="256" r="140" fill="none" stroke="rgba(6,182,212,0.25)" stroke-width="2" stroke-dasharray="6,6"/>
    <line x1="256" y1="80" x2="256" y2="432" stroke="rgba(6,182,212,0.3)" stroke-width="1.5"/>
    <line x1="80" y1="256" x2="432" y2="256" stroke="rgba(6,182,212,0.3)" stroke-width="1.5"/>
    <text x="256" y="240" font-family="monospace" font-size="28" fill="#38bdf8" text-anchor="middle" font-weight="bold">ð°ï¸ SENTINEL-2</text>
    <text x="256" y="275" font-family="monospace" font-size="14" fill="#94a3b8" text-anchor="middle">512x512 MULTI-SPECTRAL</text>
    <text x="256" y="300" font-family="monospace" font-size="12" fill="#06b6d4" text-anchor="middle">${cleanId}</text>
  </svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

// ============================================================
// 9. TILE INSPECTION MODAL & MAP HANDOFF
// ============================================================

window.openTileInspect = function(tileId) {
  const item = currentSearchResults.find(t => t.tile_id === tileId);
  if (!item) return;

  currentInspectingTile = item;
  const modal = document.getElementById('tileInspectModal');
  if (!modal) return;

  // Set Title & Subtitle
  document.getElementById('inspectTileTitle').innerText = `Tile: ${item.tile_id}`;
  document.getElementById('inspectTileSubtitle').innerText = `${item.sensor || 'Sentinel-2'} &bull; Scene: ${item.scene_id || 'N/A'}`;

  // Image & Score
  const thumbUrl = getTileThumbnailUrl(item);
  document.getElementById('inspectTileImage').src = thumbUrl;
  document.getElementById('inspectScoreBadge').innerText = `${(item.score * 100).toFixed(1)}% Match`;

  // Description
  document.getElementById('inspectDescriptionBox').innerText = item.spot_description || "Detailed spectral indices calculated.";

  // Download GeoTIFF link
  const tifBtn = document.getElementById('inspectDownloadTifBtn');
  if (tifBtn) {
    tifBtn.href = `${API_BASE}/data/tiles/${item.site_key || 'default'}/${item.tile_id}.tif`;
  }

  // Spectral Meters
  const ndvi = item.mean_ndvi !== null && item.mean_ndvi !== undefined ? item.mean_ndvi : 0;
  const ndwi = item.mean_ndwi !== null && item.mean_ndwi !== undefined ? item.mean_ndwi : 0;
  const ndbi = item.mean_ndbi !== null && item.mean_ndbi !== undefined ? item.mean_ndbi : 0;

  document.getElementById('inspectValNdvi').innerText = item.mean_ndvi !== null && item.mean_ndvi !== undefined ? ndvi.toFixed(3) : 'N/A';
  document.getElementById('inspectValNdwi').innerText = item.mean_ndwi !== null && item.mean_ndwi !== undefined ? ndwi.toFixed(3) : 'N/A';
  document.getElementById('inspectValNdbi').innerText = item.mean_ndbi !== null && item.mean_ndbi !== undefined ? ndbi.toFixed(3) : 'N/A';

  // Scale -1 to +1 into 0% to 100%
  document.getElementById('inspectBarNdvi').style.width = `${Math.min(100, Math.max(0, ((ndvi + 1) / 2) * 100))}%`;
  document.getElementById('inspectBarNdwi').style.width = `${Math.min(100, Math.max(0, ((ndwi + 1) / 2) * 100))}%`;
  document.getElementById('inspectBarNdbi').style.width = `${Math.min(100, Math.max(0, ((ndbi + 1) / 2) * 100))}%`;

  // Metadata Table
  document.getElementById('inspectMetaTileId').innerText = item.tile_id;
  document.getElementById('inspectMetaSceneId').innerText = item.scene_id || 'N/A';
  document.getElementById('inspectMetaDate').innerText = item.acquisition_date || 'N/A';
  document.getElementById('inspectMetaCoords').innerText = (item.centroid_lat && item.centroid_lon) 
    ? `${item.centroid_lat.toFixed(4)}Â° N, ${item.centroid_lon.toFixed(4)}Â° E` 
    : 'N/A';
  document.getElementById('inspectMetaCloud').innerText = `${item.cloud_pct !== null && item.cloud_pct !== undefined ? item.cloud_pct.toFixed(1) : '0.0'}%`;
  document.getElementById('inspectMetaQuality').innerText = `${item.quality_confidence !== null && item.quality_confidence !== undefined ? item.quality_confidence.toFixed(2) : '1.00'} (Gated)`;

  modal.style.display = 'flex';
};

window.closeTileInspect = function() {
  const modal = document.getElementById('tileInspectModal');
  if (modal) modal.style.display = 'none';
  currentInspectingTile = null;
};

window.inspectOpenInMapClicked = function() {
  if (!currentInspectingTile) return;
  const item = currentInspectingTile;
  closeTileInspect();
  openTileInMap(item.tile_id, item.centroid_lat, item.centroid_lon, item.geometry_geojson);
};

window.inspectFindSimilarClicked = function() {
  if (!currentInspectingTile) return;
  const tileId = currentInspectingTile.tile_id;
  closeTileInspect();
  discoverSimilarFromTile(tileId);
};

// ============================================================
// 9.5 GENERIC SIMILARITY DISCOVERY (PS 2.2.4)
// ============================================================
window.__lastDiscoveredTiles = [];

window.discoverSimilarFromTile = async function(tileId) {
  if (!tileId) return;

  const chatFeed = document.getElementById('chatFeed');
  if (chatFeed) {
    // 1. Post user prompt in chat
    const userMsg = document.createElement('div');
    userMsg.className = 'chat-message user';
    userMsg.innerHTML = `
      <div class="chat-bubble">
        ⚡ Find visually and semantically similar sites like <code>${tileId}</code> across the entire archive
      </div>
    `;
    chatFeed.appendChild(userMsg);

    // 2. Post loading placeholder
    const loadingMsg = document.createElement('div');
    loadingMsg.className = 'chat-message assistant';
    loadingMsg.id = 'discoveryLoadingIndicator';
    loadingMsg.innerHTML = `
      <div class="chat-avatar">🛰️</div>
      <div class="chat-bubble" style="display: flex; align-items: center; gap: 10px;">
        <div class="status-dot"></div>
        <span>Searching Qdrant multi-region vector space for twins of <strong>${tileId}</strong> (zero re-encoding)...</span>
      </div>
    `;
    chatFeed.appendChild(loadingMsg);
    chatFeed.scrollTop = chatFeed.scrollHeight;
  }

  try {
    const res = await fetch(`${API_BASE}/api/v1/discover/${encodeURIComponent(tileId)}?top_k=10`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Discovery HTTP error ${res.status}`);
    }

    const results = await res.json();
    window.__lastDiscoveredTiles = results;

    // Cache currentSearchResults so inspect modal finds them
    results.forEach(r => {
      if (!currentSearchResults.some(t => t.tile_id === r.tile_id)) {
        currentSearchResults.push(r);
      }
    });

    // Remove loading indicator
    const loader = document.getElementById('discoveryLoadingIndicator');
    if (loader) loader.remove();

    // ALWAYS open the rich interactive pop-up window showing the top 5 similar tiles
    showDiscoveryResultsModal(tileId, results.slice(0, 5));

    if (!chatFeed) {
      return;
    }

    // Render Discovery Response in Chat (as history)
    const msg = document.createElement('div');
    msg.className = 'chat-message assistant';

    const headerHtml = `
      <div class="retrieval-summary-header" style="background: linear-gradient(135deg, rgba(6,182,212,0.15), rgba(99,102,241,0.15)); border: 1px solid var(--border-active); border-radius: var(--radius-md); padding: 14px; margin-bottom: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
          <div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="font-size: 16px;">⚡</span>
              <h4 style="font-size: 14px; font-weight: 700; color: #38bdf8; margin: 0;">Discovery: Similar Sites Found</h4>
              <span class="tag-badge" style="background: rgba(6,182,212,0.2); color: var(--accent-cyan);">PS 2.2.4</span>
            </div>
            <p style="font-size: 12px; color: var(--text-secondary); margin: 4px 0 0 0;">
              Seed: <code>${tileId}</code> &bull; <strong>${results.length} matches</strong> across multiple geographic sectors (Zero Re-Encoding)
            </p>
          </div>
          <button class="map-btn primary" onclick="openAllOnMap(window.__lastDiscoveredTiles)" style="padding: 8px 16px; font-weight: 700; background: #06b6d4; color: #000;">
            🗺️ See All Locations on Map
          </button>
        </div>
      </div>
    `;

    let gridHtml = `<div class="retrieval-results-grid">`;
    results.forEach((item, idx) => {
      const scorePct = (item.score * 100).toFixed(1);
      const thumbUrl = getTileThumbnailUrl(item);
      const dateStr = item.acquisition_date ? item.acquisition_date.split('T')[0] : 'Unknown Date';

      const ndviVal = item.mean_ndvi !== null && item.mean_ndvi !== undefined ? item.mean_ndvi.toFixed(2) : '-';
      const ndwiVal = item.mean_ndwi !== null && item.mean_ndwi !== undefined ? item.mean_ndwi.toFixed(2) : '-';
      const ndbiVal = item.mean_ndbi !== null && item.mean_ndbi !== undefined ? item.mean_ndbi.toFixed(2) : '-';

      gridHtml += `
        <div class="result-card" style="border-color: rgba(6,182,212,0.3);">
          <div class="result-thumb-wrap">
            <img class="result-thumb-img" src="${thumbUrl}" alt="${item.tile_id}" onerror="handleTileThumbError(this, '${item.tile_id}')">
            <div class="result-score-badge" style="background: rgba(6,182,212,0.9);">${scorePct}% Match</div>
          </div>
          <div class="result-body">
            <div class="result-title-row">
              <span class="result-tile-id">#${idx + 1} &bull; ${item.tile_id}</span>
              <span class="result-date">📅 ${dateStr}</span>
            </div>
            <div class="result-spot-desc">
              ${escapeHtml(item.spot_description || 'Visually & semantically similar terrain signature.')}
            </div>
            <div class="result-spectral-chips">
              <span class="spec-chip ndvi">NDVI ${ndviVal}</span>
              <span class="spec-chip ndwi">NDWI ${ndwiVal}</span>
              <span class="spec-chip ndbi">NDBI ${ndbiVal}</span>
            </div>
            <div class="result-card-actions">
              <button class="result-action-btn" onclick="openTileInspect('${item.tile_id}')">
                🔍 Inspect
              </button>
              <button class="result-action-btn" onclick="openTileInMap('${item.tile_id}', ${item.centroid_lat || 'null'}, ${item.centroid_lon || 'null'}, ${JSON.stringify(item.geometry_geojson || null).replace(/"/g, '&quot;')})">
                🗺️ Map
              </button>
              <button class="result-action-btn" onclick="discoverSimilarFromTile('${item.tile_id}')" style="color: #38bdf8; font-weight: 600;" title="Recursive discovery from this match">
                ⚡ Find Similar
              </button>
            </div>
          </div>
        </div>
      `;
    });
    gridHtml += `</div>`;

    msg.innerHTML = `
      <div class="chat-avatar">🛰️</div>
      <div class="chat-bubble" style="width: 100%;">
        ${headerHtml}
        ${gridHtml}
      </div>
    `;

    chatFeed.appendChild(msg);
    chatFeed.scrollTop = chatFeed.scrollHeight;

  } catch (err) {
    const loader = document.getElementById('discoveryLoadingIndicator');
    if (loader) loader.remove();

    if (chatFeed) {
      const errMsg = document.createElement('div');
      errMsg.className = 'chat-message assistant';
      errMsg.innerHTML = `
        <div class="chat-avatar">⚠️</div>
        <div class="chat-bubble" style="color: #f87171;">
          Discovery Error: ${escapeHtml(err.message)}
        </div>
      `;
      chatFeed.appendChild(errMsg);
    } else {
      alert(`Discovery Error: ${err.message}`);
    }
  }
};

window.showDiscoveryResultsModal = function(seedTileId, results) {
  let modal = document.getElementById('discoveryModalOverlay');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'discoveryModalOverlay';
    modal.className = 'discovery-modal-overlay';
    document.body.appendChild(modal);
  }

  const top5 = (results || []).slice(0, 5);

  const resultCardsHtml = top5.map((item, idx) => {
    const hasScore = item.score !== undefined && item.score !== null;
    const scorePct = hasScore ? (item.score * 100).toFixed(1) + '% Match' : 'Cluster Member';
    const thumbUrl = getTileThumbnailUrl(item);
    const dateStr = item.acquisition_date ? item.acquisition_date.split('T')[0] : 'Unknown Date';
    const ndviVal = item.mean_ndvi !== null && item.mean_ndvi !== undefined ? item.mean_ndvi.toFixed(2) : '-';
    const ndwiVal = item.mean_ndwi !== null && item.mean_ndwi !== undefined ? item.mean_ndwi.toFixed(2) : '-';
    const ndbiVal = item.mean_ndbi !== null && item.mean_ndbi !== undefined ? item.mean_ndbi.toFixed(2) : '-';
    const lat = item.centroid_lat ? item.centroid_lat.toFixed(4) : null;
    const lon = item.centroid_lon ? item.centroid_lon.toFixed(4) : null;

    return `
      <div class="result-card" style="border-color: rgba(6,182,212,0.35); background: rgba(13, 18, 31, 0.95); display: flex; flex-direction: column; min-width: 0;">
        <div class="result-thumb-wrap" style="height: 160px; position: relative;">
          <img class="result-thumb-img" src="${thumbUrl}" alt="${item.tile_id}" onerror="handleTileThumbError(this, '${item.tile_id}')">
          <div class="result-score-badge" style="background: rgba(6,182,212,0.95); color: #000; font-weight: 800;">${scorePct}</div>
          <div style="position: absolute; bottom: 8px; left: 8px; font-size: 10px; font-family: 'JetBrains Mono', monospace; background: rgba(0,0,0,0.75); padding: 2px 6px; border-radius: 4px; color: #38bdf8;">
            ${lat ? lat + '° N, ' + lon + '° E' : 'WGS84 Coordinates'}
          </div>
        </div>
        <div class="result-body" style="display: flex; flex-direction: column; flex: 1; padding: 14px;">
          <div class="result-title-row">
            <span class="result-tile-id" style="font-size: 12px; font-weight: 700;" title="${item.tile_id}">#${idx + 1} &bull; ${item.tile_id.length > 22 ? item.tile_id.slice(0, 20) + '...' : item.tile_id}</span>
            <span class="result-date">📅 ${dateStr}</span>
          </div>
          <div class="result-spot-desc" style="font-size: 11px; margin: 6px 0; color: var(--text-secondary); line-height: 1.4;">
            ${escapeHtml(item.spot_description || 'High-dimensional visual & semantic twin.')}
          </div>
          <div class="result-spectral-chips" style="margin-bottom: 10px;">
            <span class="spec-chip ndvi">NDVI ${ndviVal}</span>
            <span class="spec-chip ndwi">NDWI ${ndwiVal}</span>
            <span class="spec-chip ndbi">NDBI ${ndbiVal}</span>
          </div>
          <div class="result-card-actions" style="margin-top: auto; display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
            <button class="map-btn primary" onclick="openTileInMap('${item.tile_id}', ${item.centroid_lat || 'null'}, ${item.centroid_lon || 'null'}, ${JSON.stringify(item.geometry_geojson || null).replace(/"/g, '&quot;')})" style="padding: 7px 8px; font-size: 11px; font-weight: 700; background: linear-gradient(135deg, #06b6d4, #6366f1); color: #fff;">
              🗺️ See on Map
            </button>
            <button class="map-btn" onclick="openTileInspect('${item.tile_id}')" style="padding: 7px 8px; font-size: 11px; font-weight: 600;">
              🔍 Inspect Tile
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');

  modal.innerHTML = `
    <div class="discovery-modal-content">
      <div class="discovery-modal-header">
        <div style="display: flex; align-items: center; gap: 10px;">
          <div style="width: 34px; height: 34px; border-radius: 8px; background: rgba(6,182,212,0.2); border: 1px solid var(--accent-cyan); display: flex; align-items: center; justify-content: center; font-size: 18px;">
            ⚡
          </div>
          <div>
            <h3 style="font-size: 16px; font-weight: 800; color: #fff; margin: 0;">Top 5 Discovered Similar Sites</h3>
            <span style="font-size: 12px; color: var(--text-muted);">Unsupervised High-Dimensional Similarity Search (PS 2.2.4 &bull; Zero GPU Re-Encoding)</span>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 12px;">
          <button class="map-btn primary" onclick="openAllOnMap(window.__lastDiscoveredTiles)" style="padding: 8px 16px; font-weight: 700; background: #06b6d4; color: #000;">
            🗺️ See All ${top5.length} on Map
          </button>
          <button class="modal-close-btn" onclick="closeDiscoveryModal()">&times;</button>
        </div>
      </div>
      <div class="discovery-modal-body">
        <div class="discovery-seed-box">
          <span style="font-size: 22px;">🎯</span>
          <div>
            <div style="font-size: 13px; font-weight: 700; color: #38bdf8;">Seed Tile: <code>${escapeHtml(seedTileId)}</code></div>
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 2px;">
              Directly queried 512-dim RemoteCLIP embedding in Qdrant &bull; Identified top ${top5.length} visual and terrain signature twins across archive sectors.
            </div>
          </div>
        </div>
        <div class="discovery-results-grid">
          ${resultCardsHtml}
        </div>
      </div>
    </div>
  `;

  modal.style.display = 'flex';
};

window.closeDiscoveryModal = function() {
  const modal = document.getElementById('discoveryModalOverlay');
  if (modal) modal.style.display = 'none';
};

window.openAllOnMap = function(tiles) {
  if (!tiles || tiles.length === 0) {
    alert('No tiles available to plot on map.');
    return;
  }

  // Store discovery pins for map handoff
  localStorage.setItem('discovery_pins', JSON.stringify(tiles));

  // If on map page already:
  if (document.getElementById('leafletMap')) {
    renderDiscoveryPinsOnMap(tiles);
    return;
  }

  // Navigate to map view
  window.location.href = `/?discovery=true`;
};

window.renderDiscoveryPinsOnMap = function(tiles) {
  if (!map) {
    // Retry if map is still initializing
    setTimeout(() => window.renderDiscoveryPinsOnMap(tiles), 300);
    return;
  }
  if (!tiles || tiles.length === 0) return;

  if (discoveryMapLayerGroup) {
    try { map.removeLayer(discoveryMapLayerGroup); } catch (e) {}
  }
  discoveryMapLayerGroup = L.featureGroup().addTo(map);

  const bounds = L.latLngBounds();
  let validCount = 0;

  tiles.forEach((item, idx) => {
    const lat = parseFloat(item.centroid_lat);
    const lon = parseFloat(item.centroid_lon);
    if (isNaN(lat) || isNaN(lon)) return;

    validCount++;
    bounds.extend([lat, lon]);

    const hasScore = item.score !== undefined && item.score !== null;
    const scoreVal = hasScore ? (item.score * 100).toFixed(0) + '%' : 'Member';
    const scoreDisplay = hasScore ? (item.score * 100).toFixed(1) + '%' : 'Cluster Medoid / Member';

    const markerIcon = L.divIcon({
      className: 'discovery-map-pin',
      html: `
        <div style="position: relative; cursor: pointer; text-align: center;">
          <div style="background: linear-gradient(135deg, #06b6d4, #6366f1); color: #fff; font-weight: 700; font-size: 11px; padding: 3px 8px; border-radius: 12px; border: 1px solid #fff; box-shadow: 0 0 14px rgba(6,182,212,0.85); white-space: nowrap;">
            #${idx + 1} (${scoreVal})
          </div>
          <div style="width: 2px; height: 10px; background: #06b6d4; margin: 0 auto;"></div>
        </div>
      `,
      iconSize: [80, 32],
      iconAnchor: [40, 32]
    });

    const marker = L.marker([lat, lon], { icon: markerIcon }).addTo(discoveryMapLayerGroup);
    const thumbUrl = getTileThumbnailUrl(item);

    marker.bindPopup(`
      <div style="font-family: 'Inter', sans-serif; font-size: 12px; color: #fff; width: 240px; padding: 4px;">
        <div style="font-weight: 700; color: #38bdf8; margin-bottom: 4px;">🎯 Match #${idx + 1} &bull; ${scoreVal}</div>
        <img src="${thumbUrl}" style="width: 100%; height: 120px; object-fit: cover; border-radius: 4px; border: 1px solid rgba(255,255,255,0.1); margin-bottom: 6px;" onerror="handleTileThumbError(this, '${item.tile_id}')">
        <div style="font-size: 11px; color: #cbd5e1; margin-bottom: 8px;">
          <strong>Tile:</strong> <code>${item.tile_id}</code><br>
          <strong>Match:</strong> ${scoreDisplay}<br>
          <strong>Centroid:</strong> ${lat.toFixed(4)}° N, ${lon.toFixed(4)}° E
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px;">
          <button onclick="openTileInspect('${item.tile_id}')" style="background: rgba(255,255,255,0.1); color: #fff; border: 1px solid rgba(255,255,255,0.2); padding: 5px 8px; border-radius: 4px; font-weight: 600; cursor: pointer; font-size: 11px;">
            🔍 Inspect
          </button>
          <button onclick="discoverSimilarFromTile('${item.tile_id}')" style="background: #06b6d4; color: #000; border: none; padding: 5px 8px; border-radius: 4px; font-weight: 700; cursor: pointer; font-size: 11px;">
            ⚡ Find Similar
          </button>
        </div>
      </div>
    `);

    // Draw footprint rectangle if available
    if (item.geometry_geojson) {
      try {
        const geom = typeof item.geometry_geojson === 'string' ? JSON.parse(item.geometry_geojson) : item.geometry_geojson;
        if (geom && (geom.coordinates || geom.type)) {
          L.geoJSON(geom, {
            style: {
              color: '#06b6d4',
              weight: 2,
              fillColor: '#06b6d4',
              fillOpacity: 0.15,
              dashArray: '4, 4'
            }
          }).addTo(discoveryMapLayerGroup);
        }
      } catch (e) {}
    }
  });

  if (validCount > 0 && bounds.isValid()) {
    map.fitBounds(bounds, { padding: [80, 80], maxZoom: 15 });
  }
};

window.openTileInMap = function(tileId, centroidLat, centroidLon, geometryGeoJson) {
  // If not on map page, navigate to map page with query params
  if (!document.getElementById('leafletMap')) {
    const params = new URLSearchParams();
    params.set('tile_id', tileId);
    if (centroidLat) params.set('lat', centroidLat);
    if (centroidLon) params.set('lon', centroidLon);
    window.location.href = `/?${params.toString()}`;
    return;
  }

  // If on map page with view switching:
  if (typeof switchPage === 'function') {
    switchPage('map');
  }

  if (!map) return;

  // Remove previous highlight
  if (mapTileHighlightLayer) {
    map.removeLayer(mapTileHighlightLayer);
    mapTileHighlightLayer = null;
  }

  // Draw highlighted tile polygon
  if (geometryGeoJson && geometryGeoJson.coordinates) {
    mapTileHighlightLayer = L.geoJSON(geometryGeoJson, {
      style: {
        color: '#f59e0b',
        weight: 3,
        opacity: 1,
        fillColor: '#f59e0b',
        fillOpacity: 0.35,
        dashArray: '6, 6'
      }
    }).addTo(map);

    mapTileHighlightLayer.bindPopup(`
      <div class="popup-title">🎯 Retrieved Tile: ${tileId}</div>
      <div class="popup-stat">Centroid: ${centroidLat ? centroidLat.toFixed(4) : ''}° N, ${centroidLon ? centroidLon.toFixed(4) : ''}° E</div>
    `).openPopup();

    map.fitBounds(mapTileHighlightLayer.getBounds(), { maxZoom: 15, padding: [50, 50] });
  } else if (centroidLat && centroidLon) {
    map.setView([centroidLat, centroidLon], 14);
    mapTileHighlightLayer = L.circleMarker([centroidLat, centroidLon], {
      radius: 10,
      color: '#f59e0b',
      fillColor: '#f59e0b',
      fillOpacity: 0.8
    }).addTo(map);
    mapTileHighlightLayer.bindPopup(`<b>Retrieved Tile:</b> ${tileId}`).openPopup();
  }
};

function checkUrlParamsForTileHighlight() {
  const params = new URLSearchParams(window.location.search);
  const isDiscovery = params.get('discovery');
  const tileId = params.get('tile_id');
  const lat = parseFloat(params.get('lat'));
  const lon = parseFloat(params.get('lon'));

  if (isDiscovery) {
    try {
      const cached = localStorage.getItem('discovery_pins');
      if (cached) {
        const tiles = JSON.parse(cached);
        setTimeout(() => {
          renderDiscoveryPinsOnMap(tiles);
        }, 500);
        return;
      }
    } catch (e) {}
  }

  if (tileId && lat && lon && map) {
    setTimeout(() => {
      openTileInMap(tileId, lat, lon, null);
    }, 500);
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str.toString().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

// ============================================================
// 10. INITIALIZE ON DOM LOAD
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('leafletMap')) {
    initMap();
    checkUrlParamsForTileHighlight();
  }
});

