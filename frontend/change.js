/**
 * AeroLens — Multi-Temporal Change Detection Studio
 * Controller: frontend/change.js (Full Multi-Temporal Asset Bridge)
 */

document.addEventListener("DOMContentLoaded", () => {
  initChangeStudio();
});

let changeMap = null;
let gridLayer = null;
let currentSelectedLayer = null;
let currentSiteTimeline = null;

async function initChangeStudio() {
  const mapElement = document.getElementById("change-map");
  if (!mapElement) return;

  // 1. Initialize Leaflet Map
  changeMap = L.map("change-map", {
    center: [23.0, 72.3],
    zoom: 11,
    zoomControl: false,
    attributionControl: false
  });

  // Custom Zoom Control
  L.control.zoom({ position: "topleft" }).addTo(changeMap);

  // 2. Base Tile Layers
  const esriSatellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "ESRI World Imagery" }
  );

  const cartoLabels = L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png",
    { subdomains: "abcd", maxZoom: 19, opacity: 0.85 }
  );

  const cartoDark = L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { subdomains: "abcd", maxZoom: 19, attribution: "CartoDB Dark Matter" }
  );

  // Default: Satellite + Labels
  esriSatellite.addTo(changeMap);
  cartoLabels.addTo(changeMap);

  // Base layer toggle & bounds reset
  setupLayerToggles(esriSatellite, cartoLabels, cartoDark);

  // Drawer Close Button
  setupDrawerControls();

  // 3. Fetch and Render Grid Footprints
  await loadChangeGrid();
}

function setupLayerToggles(satellite, labels, dark) {
  const toggleBtn = document.getElementById("btn-toggle-basemap");
  if (toggleBtn) {
    let isSatellite = true;
    toggleBtn.addEventListener("click", () => {
      if (isSatellite) {
        changeMap.removeLayer(satellite);
        changeMap.removeLayer(labels);
        dark.addTo(changeMap);
        toggleBtn.innerHTML = "<span>🛰️</span> Base: Dark Vector";
        isSatellite = false;
      } else {
        changeMap.removeLayer(dark);
        satellite.addTo(changeMap);
        labels.addTo(changeMap);
        toggleBtn.innerHTML = "<span>🗺️</span> Base: Satellite";
        isSatellite = true;
      }
    });
  }

  const resetBtn = document.getElementById("btn-reset-view");
  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      if (gridLayer && gridLayer.getBounds().isValid()) {
        changeMap.fitBounds(gridLayer.getBounds(), { padding: [60, 60], maxZoom: 14 });
      }
    });
  }
}

function setupDrawerControls() {
  const closeBtn = document.getElementById("btn-close-drawer");
  const drawer = document.getElementById("temporal-drawer");
  if (closeBtn && drawer) {
    closeBtn.addEventListener("click", () => {
      drawer.classList.remove("open");
      if (currentSelectedLayer && gridLayer) {
        gridLayer.resetStyle(currentSelectedLayer);
        currentSelectedLayer = null;
      }
      const selectedSiteContainer = document.getElementById("hud-selected-container");
      if (selectedSiteContainer) selectedSiteContainer.style.display = "none";
    });
  }

  // Studio Workbench Fullscreen / Side-Panel Toggle
  const expandBtn = document.getElementById("btn-expand-drawer");
  if (expandBtn && drawer) {
    expandBtn.addEventListener("click", () => {
      drawer.classList.toggle("side-mode");
      const isSide = drawer.classList.contains("side-mode");
      const icon = document.getElementById("expand-icon");
      const text = document.getElementById("expand-text");
      if (icon) icon.textContent = isSide ? "⤢" : "🗗";
      if (text) text.textContent = isSide ? "Full Studio" : "Side Panel";
      if (changeMap) changeMap.invalidateSize();
    });
  }

  // Setup Lightbox Modal Controls
  setupLightboxControls();

  // Date selection change listeners for the dual inspector
  const selectT1 = document.getElementById("select-t1-date");
  const selectT2 = document.getElementById("select-t2-date");

  if (selectT1 && selectT2) {
    selectT1.addEventListener("change", () => onInspectorDateChanged());
    selectT2.addEventListener("change", () => onInspectorDateChanged());
  }

  // Phase 5: Multi-Temporal Sequence Analysis Controls
  const runBtn = document.getElementById("btn-run-analysis");
  if (runBtn) {
    runBtn.addEventListener("click", () => runMultiTemporalSequenceAnalysis());
  }

  const tabOverall = document.getElementById("tab-mode-overall");
  if (tabOverall) {
    tabOverall.addEventListener("click", () => switchChangeViewMode("overall"));
  }

  const selectInterval = document.getElementById("select-pairwise-interval");
  if (selectInterval) {
    selectInterval.addEventListener("change", (e) => switchChangeViewMode(e.target.value));
  }
}

function setupLightboxControls() {
  const modal = document.getElementById("change-lightbox-modal");
  const closeBtn = document.getElementById("lightbox-close-btn");
  if (modal && closeBtn) {
    closeBtn.addEventListener("click", () => {
      modal.style.display = "none";
    });
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.style.display = "none";
    });
  }
}

window.openLightbox = function(url, title = "Band Map Inspection") {
  if (!url) return;
  const modal = document.getElementById("change-lightbox-modal");
  const img = document.getElementById("lightbox-img");
  const titleEl = document.getElementById("lightbox-title");
  if (modal && img) {
    img.src = url;
    if (titleEl) titleEl.textContent = title;
    modal.style.display = "flex";
  }
};


async function loadChangeGrid() {
  const hudStatus = document.getElementById("hud-status-text");
  const gridCountEl = document.getElementById("stat-total-grids");
  const multiCountEl = document.getElementById("stat-multi-grids");

  try {
    if (hudStatus) hudStatus.textContent = "Querying PostGIS grid footprints...";

    const res = await fetch("/api/v1/change/grid");
    if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch grid`);

    const geojsonData = await res.json();

    if (gridCountEl) gridCountEl.textContent = geojsonData.total_grids || 0;
    if (multiCountEl) multiCountEl.textContent = geojsonData.multi_temporal_count || 0;
    if (hudStatus) hudStatus.textContent = `${geojsonData.multi_temporal_count || 0} multi-temporal candidate grids active`;

    if (gridLayer) {
      changeMap.removeLayer(gridLayer);
    }

    gridLayer = L.geoJSON(geojsonData, {
      style: (feature) => {
        const isMulti = feature.properties && feature.properties.multi_temporal;
        return {
          color: isMulti ? "#06b6d4" : "#64748b",
          weight: isMulti ? 2.5 : 1.2,
          opacity: isMulti ? 0.95 : 0.6,
          fillColor: isMulti ? "#06b6d4" : "#334155",
          fillOpacity: isMulti ? 0.22 : 0.08,
          dashArray: isMulti ? null : "4, 4",
          className: isMulti ? "grid-cell-multi" : "grid-cell-single"
        };
      },
      onEachFeature: (feature, layer) => {
        setupFeatureInteractions(feature, layer);
      }
    }).addTo(changeMap);

    if (gridLayer.getBounds().isValid()) {
      changeMap.fitBounds(gridLayer.getBounds(), { padding: [70, 70], maxZoom: 14 });
    }

  } catch (err) {
    console.error("Error loading change grid:", err);
    if (hudStatus) hudStatus.textContent = "Error loading grid layer";
  }
}

function setupFeatureInteractions(feature, layer) {
  const props = feature.properties || {};
  const isMulti = props.multi_temporal;
  const obsCount = props.observation_count || 1;
  const earliest = props.earliest_date ? props.earliest_date.split("T")[0] : "N/A";
  const latest = props.latest_date ? props.latest_date.split("T")[0] : "N/A";
  const yearsStr = (props.available_years && props.available_years.length > 0)
    ? props.available_years.join(", ")
    : "N/A";

  // Hover Tooltip
  const tooltipContent = `
    <div class="change-grid-tooltip">
      <div class="tooltip-badge ${isMulti ? 'badge-multi' : 'badge-single'}">
        ${isMulti ? '⚡ MULTI-TEMPORAL SERIES' : 'SINGLE OBSERVATION'}
      </div>
      <div class="tooltip-title">${props.site_key || 'Tile Grid'}</div>
      <div class="tooltip-row">
        <span>Observations:</span> <b>${obsCount} epoch${obsCount > 1 ? 's' : ''}</b>
      </div>
      <div class="tooltip-row">
        <span>Epoch Span:</span> <b>${earliest} &rarr; ${latest}</b>
      </div>
      <div class="tooltip-row">
        <span>Capture Years:</span> <b>${yearsStr}</b>
      </div>
      <div class="tooltip-action">
        ${isMulti ? '👉 Click to inspect multi-temporal stack' : 'Single date only'}
      </div>
    </div>
  `;

  layer.bindTooltip(tooltipContent, {
    sticky: true,
    direction: "top",
    className: "leaflet-custom-tooltip",
    offset: [0, -10]
  });

  layer.on("mouseover", (e) => {
    const target = e.target;
    if (target !== currentSelectedLayer) {
      target.setStyle({
        weight: 3.5,
        fillOpacity: isMulti ? 0.38 : 0.18,
        color: isMulti ? "#38bdf8" : "#94a3b8"
      });
    }
  });

  layer.on("mouseout", (e) => {
    const target = e.target;
    if (target !== currentSelectedLayer) {
      gridLayer.resetStyle(target);
    }
  });

  layer.on("click", (e) => {
    handleGridClick(feature, layer);
  });
}

async function handleGridClick(feature, layer) {
  if (currentSelectedLayer && gridLayer) {
    gridLayer.resetStyle(currentSelectedLayer);
  }
  currentSelectedLayer = layer;
  layer.setStyle({
    color: "#f59e0b",
    weight: 4,
    fillOpacity: 0.38,
    fillColor: "#f59e0b"
  });

  changeMap.fitBounds(layer.getBounds(), { padding: [120, 120], maxZoom: 15 });

  const selectedSiteEl = document.getElementById("hud-selected-site");
  const selectedSiteContainer = document.getElementById("hud-selected-container");
  if (selectedSiteEl && selectedSiteContainer) {
    selectedSiteEl.textContent = feature.properties.site_key;
    selectedSiteContainer.style.display = "flex";
  }

  await openMultiTemporalDrawer(feature.properties.site_key);
}

/**
 * Loads and renders the FULL Multi-Temporal Asset Stack & Trajectory Transitions
 */
async function openMultiTemporalDrawer(siteKey) {
  const drawer = document.getElementById("temporal-drawer");
  const siteKeyEl = document.getElementById("drawer-site-key");
  const siteTitleEl = document.getElementById("drawer-site-title");
  const stackContainer = document.getElementById("multi-temporal-stack-container");
  const transitionsContainer = document.getElementById("sequential-transitions-container");
  const bridgeEpochCount = document.getElementById("bridge-epoch-count");

  drawer.classList.add("open");
  drawer.classList.remove("side-mode");
  const icon = document.getElementById("expand-icon");
  const text = document.getElementById("expand-text");
  if (icon) icon.textContent = "🗗";
  if (text) text.textContent = "Side Panel";
  if (siteKeyEl) siteKeyEl.textContent = siteKey;
  if (siteTitleEl) siteTitleEl.textContent = "Loading Multi-Temporal Series...";
  if (stackContainer) stackContainer.innerHTML = '<span style="color:#64748b; font-size:11px; padding:10px;">Loading epoch stack...</span>';

  try {
    const res = await fetch(`/api/v1/change/site/${encodeURIComponent(siteKey)}/timeline`);
    if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch site timeline`);

    const data = await res.json();
    currentSiteTimeline = data;

    const stack = data.multi_temporal_stack || [];
    const transitions = data.sequential_transitions || [];

    if (siteTitleEl) {
      siteTitleEl.textContent = `Ground Target (${stack.length} Multi-Temporal Epochs)`;
    }
    if (bridgeEpochCount) {
      bridgeEpochCount.textContent = `${stack.length} epochs`;
    }

    // 1. Render Multi-Temporal Observation Stack (Filmstrip Cards)
    renderMultiTemporalStack(stack);

    // 2. Render Step-Wise Sequential Transitions (T1 -> T2, T2 -> T3 ...)
    renderSequentialTransitions(transitions);

    // 3. Populate Sub-Interval Dual Inspector
    populateInspectorDropdowns(stack);

    // 4. Check for pre-existing or cached Multi-Temporal Sequence Analysis
    await checkAndLoadExistingAnalysis(siteKey);

  } catch (err) {
    console.error("Error loading multi-temporal timeline:", err);
    if (siteTitleEl) siteTitleEl.textContent = "Error loading series";
    if (stackContainer) stackContainer.innerHTML = `<span style="color:#f43f5e; font-size:11px;">Error: ${err.message}</span>`;
  }
}

function renderMultiTemporalStack(stack) {
  const container = document.getElementById("multi-temporal-stack-container");
  if (!container) return;

  container.innerHTML = "";
  stack.forEach((epoch) => {
    const card = document.createElement("div");
    card.className = "stack-epoch-card";

    const elapsedStr = epoch.elapsed_years_from_baseline > 0
      ? `+${epoch.elapsed_years_from_baseline} yrs`
      : "Baseline";

    const cloudPct = (epoch.cloud_pct * 100).toFixed(1);
    const ndviStr = epoch.mean_ndvi != null ? epoch.mean_ndvi.toFixed(3) : "--";
    const ndbiStr = epoch.mean_ndbi != null ? epoch.mean_ndbi.toFixed(3) : "--";
    const fileName = epoch.geotiff_path ? epoch.geotiff_path.split("/").pop() : epoch.tile_id;

    card.innerHTML = `
      <div class="stack-epoch-header">
        <span class="stack-epoch-tag">${epoch.epoch_label}</span>
        <span class="stack-epoch-elapsed">${elapsedStr}</span>
      </div>

      <div class="stack-thumb-box" title="Epoch ${epoch.epoch_label} (${epoch.date})">
        <img class="stack-thumb-img" src="${epoch.thumbnail_url || ''}" alt="${epoch.epoch_label}" />
      </div>

      <div class="stack-meta-grid">
        <div class="stack-meta-item"><span>Date:</span> <b>${epoch.date}</b></div>
        <div class="stack-meta-item"><span>Cloud:</span> <b>${cloudPct}%</b></div>
        <div class="stack-meta-item"><span>NDVI:</span> <b style="color:#10b981;">${ndviStr}</b></div>
        <div class="stack-meta-item"><span>NDBI:</span> <b style="color:#f59e0b;">${ndbiStr}</b></div>
        <div style="font-size:8px; font-family:'JetBrains Mono',monospace; color:#64748b; word-break:break-all; margin-top:2px;">
          ${fileName}
        </div>
      </div>
    `;

    // Click on stack card selects it as inspector Epoch B
    card.style.cursor = "pointer";
    card.addEventListener("click", () => {
      const selectT2 = document.getElementById("select-t2-date");
      if (selectT2) {
        selectT2.value = epoch.tile_id;
        onInspectorDateChanged();
      }
    });

    container.appendChild(card);
  });
}

function renderSequentialTransitions(transitions) {
  const container = document.getElementById("sequential-transitions-container");
  if (!container) return;

  container.innerHTML = "";
  if (transitions.length === 0) {
    container.innerHTML = '<span style="color:#64748b; font-size:11px;">Single observation only (no multi-temporal transitions available).</span>';
    return;
  }

  transitions.forEach((t) => {
    const row = document.createElement("div");
    row.className = "transition-row";

    const dNdviStr = t.delta_ndvi != null ? `${t.delta_ndvi >= 0 ? '+' : ''}${t.delta_ndvi.toFixed(3)}` : "--";
    const dNdbiStr = t.delta_ndbi != null ? `${t.delta_ndbi >= 0 ? '+' : ''}${t.delta_ndbi.toFixed(3)}` : "--";
    const ndviColor = t.delta_ndvi >= 0 ? "#10b981" : "#f43f5e";
    const ndbiColor = t.delta_ndbi >= 0.05 ? "#f59e0b" : "#38bdf8";

    row.innerHTML = `
      <div style="display:flex; align-items:center; gap:10px;">
        <span class="transition-label-badge">${t.transition_label}</span>
        <div style="display:flex; flex-direction:column;">
          <span class="transition-dates">${t.from_date} &rarr; ${t.to_date}</span>
          <span style="font-size:10px; color:#94a3b8;">&Delta; Time: +${t.elapsed_years} yrs (${t.elapsed_days} days)</span>
        </div>
      </div>

      <div class="transition-metrics-cluster">
        <div class="metric-pill" style="color:${ndviColor};" title="Change in NDVI">&Delta;NDVI: <b>${dNdviStr}</b></div>
        <div class="metric-pill" style="color:${ndbiColor};" title="Change in NDBI">&Delta;NDBI: <b>${dNdbiStr}</b></div>
        <button style="background:rgba(6,182,212,0.15); border:1px solid rgba(6,182,212,0.3); color:#38bdf8; font-size:10px; border-radius:4px; padding:3px 6px; cursor:pointer;" title="Inspect this step-wise transition">Inspect</button>
      </div>
    `;

    // Click handler to set this specific step in the inspector
    row.querySelector("button").addEventListener("click", () => {
      const selectT1 = document.getElementById("select-t1-date");
      const selectT2 = document.getElementById("select-t2-date");
      if (selectT1 && selectT2 && t.pair_payload) {
        selectT1.value = t.pair_payload.tile_before_id;
        selectT2.value = t.pair_payload.tile_after_id;
        onInspectorDateChanged();
      }
    });

    container.appendChild(row);
  });
}

function populateInspectorDropdowns(stack) {
  const selectT1 = document.getElementById("select-t1-date");
  const selectT2 = document.getElementById("select-t2-date");
  if (!selectT1 || !selectT2) return;

  selectT1.innerHTML = "";
  selectT2.innerHTML = "";

  stack.forEach((s) => {
    const opt1 = document.createElement("option");
    opt1.value = s.tile_id;
    opt1.textContent = `${s.epoch_label}: ${s.date} (Cloud: ${(s.cloud_pct * 100).toFixed(1)}%)`;
    selectT1.appendChild(opt1);

    const opt2 = document.createElement("option");
    opt2.value = s.tile_id;
    opt2.textContent = `${s.epoch_label}: ${s.date} (Cloud: ${(s.cloud_pct * 100).toFixed(1)}%)`;
    selectT2.appendChild(opt2);
  });

  if (stack.length >= 2) {
    selectT1.value = stack[0].tile_id;
    selectT2.value = stack[stack.length - 1].tile_id;
  } else if (stack.length === 1) {
    selectT1.value = stack[0].tile_id;
    selectT2.value = stack[0].tile_id;
  }

  onInspectorDateChanged();
}

function onInspectorDateChanged() {
  if (!currentSiteTimeline) return;
  const stack = currentSiteTimeline.multi_temporal_stack || [];
  const selectT1 = document.getElementById("select-t1-date");
  const selectT2 = document.getElementById("select-t2-date");
  if (!selectT1 || !selectT2) return;

  const t1Id = selectT1.value;
  const t2Id = selectT2.value;

  const snap1 = stack.find((s) => s.tile_id === t1Id) || stack[0];
  const snap2 = stack.find((s) => s.tile_id === t2Id) || stack[stack.length - 1];

  // Update T1 Card
  if (snap1) {
    const badgeDate = document.getElementById("t1-badge-date");
    const thumbImg = document.getElementById("t1-thumb-img");
    const cloudStat = document.getElementById("t1-cloud-stat");
    const ndviStat = document.getElementById("t1-ndvi-stat");

    if (badgeDate) badgeDate.textContent = `${snap1.epoch_label} • ${snap1.date}`;
    if (thumbImg) thumbImg.src = snap1.thumbnail_url || "";
    if (cloudStat) cloudStat.textContent = `${(snap1.cloud_pct * 100).toFixed(1)}%`;
    if (ndviStat) ndviStat.textContent = snap1.mean_ndvi != null ? snap1.mean_ndvi.toFixed(3) : "--";
  }

  // Update T2 Card
  if (snap2) {
    const badgeDate = document.getElementById("t2-badge-date");
    const thumbImg = document.getElementById("t2-thumb-img");
    const cloudStat = document.getElementById("t2-cloud-stat");
    const ndviStat = document.getElementById("t2-ndvi-stat");

    if (badgeDate) badgeDate.textContent = `${snap2.epoch_label} • ${snap2.date}`;
    if (thumbImg) thumbImg.src = snap2.thumbnail_url || "";
    if (cloudStat) cloudStat.textContent = `${(snap2.cloud_pct * 100).toFixed(1)}%`;
    if (ndviStat) ndviStat.textContent = snap2.mean_ndvi != null ? snap2.mean_ndvi.toFixed(3) : "--";
  }

  // Update Delta variance HUD
  if (snap1 && snap2) {
    const deltaTimeEl = document.getElementById("delta-time");
    const deltaNdviEl = document.getElementById("delta-ndvi");
    const deltaNdbiEl = document.getElementById("delta-ndbi");

    const d1 = new Date(snap1.date);
    const d2 = new Date(snap2.date);
    const diffDays = Math.round((d2 - d1) / (1000 * 60 * 60 * 24));
    if (deltaTimeEl) {
      if (diffDays >= 365) {
        deltaTimeEl.textContent = `+${(diffDays / 365.25).toFixed(1)} yrs`;
      } else {
        deltaTimeEl.textContent = `${diffDays >= 0 ? '+' : ''}${diffDays} d`;
      }
    }

    if (deltaNdviEl) {
      if (snap1.mean_ndvi != null && snap2.mean_ndvi != null) {
        const dNdvi = snap2.mean_ndvi - snap1.mean_ndvi;
        deltaNdviEl.textContent = `${dNdvi >= 0 ? '+' : ''}${dNdvi.toFixed(3)}`;
        deltaNdviEl.style.color = dNdvi >= 0 ? "#10b981" : "#f43f5e";
      } else {
        deltaNdviEl.textContent = "--";
      }
    }

    if (deltaNdbiEl) {
      if (snap1.mean_ndbi != null && snap2.mean_ndbi != null) {
        const dNdbi = snap2.mean_ndbi - snap1.mean_ndbi;
        deltaNdbiEl.textContent = `${dNdbi >= 0 ? '+' : ''}${dNdbi.toFixed(3)}`;
        deltaNdbiEl.style.color = dNdbi >= 0.05 ? "#f59e0b" : "#38bdf8";
      } else {
        deltaNdbiEl.textContent = "--";
      }
    }
  }
}

function updateMultiTemporalPayload(stack, transitions) {
  const payloadPreview = document.getElementById("part2-payload-preview");
  if (!payloadPreview || !currentSiteTimeline) return;

  const multiTemporalBridgePayload = {
    site_key: currentSiteTimeline.site_key,
    total_observation_epochs: stack.length,
    temporal_span: `${currentSiteTimeline.min_date} to ${currentSiteTimeline.max_date}`,
    multi_temporal_stack: stack.map((s) => ({
      epoch: s.epoch_label,
      tile_id: s.tile_id,
      date: s.date,
      elapsed_years: s.elapsed_years_from_baseline,
      geotiff_path: s.geotiff_path,
      bad_mask_path: s.bad_mask_path,
      ndvi: s.mean_ndvi,
      ndbi: s.mean_ndbi
    })),
    step_wise_transitions: transitions.map((t) => ({
      transition: t.transition_label,
      dates: `${t.from_date} -> ${t.to_date}`,
      elapsed_days: t.elapsed_days,
      delta_ndvi: t.delta_ndvi,
      delta_ndbi: t.delta_ndbi,
      pair_paths: {
        t_before_tif: t.pair_payload.tile_before_path,
        t_after_tif: t.pair_payload.tile_after_path,
        mask_before_tif: t.pair_payload.mask_before_path,
        mask_after_tif: t.pair_payload.mask_after_path
      }
    }))
  };

  payloadPreview.textContent = JSON.stringify(multiTemporalBridgePayload, null, 2);
}

/* ============================================================
   PHASE 5: MULTI-TEMPORAL CHANGE DETECTION ENGINE CONTROLLER
   ============================================================ */

let changeVectorLayer = null;
let currentSiteAnalysis = null;

const CHANGE_COLORS = {
  "Construction": { fill: "#f59e0b", stroke: "#d97706", name: "Construction" },
  "Clearance": { fill: "#f43f5e", stroke: "#e11d48", name: "Clearance" },
  "Road Development": { fill: "#a855f7", stroke: "#9333ea", name: "Road Development" },
  "Water-Extent Variation (expansion)": { fill: "#06b6d4", stroke: "#0891b2", name: "Water Expansion" },
  "Water-Extent Variation (shrinkage)": { fill: "#0284c7", stroke: "#0369a1", name: "Water Shrinkage" },
  "Water-Extent Variation": { fill: "#06b6d4", stroke: "#0891b2", name: "Water Variation" },
  "Demolition / Reversion": { fill: "#fb923c", stroke: "#ea580c", name: "Demolition" },
  "Unclassified Structural Change": { fill: "#6366f1", stroke: "#4f46e5", name: "Unclassified" },
  "No Change": { fill: "#475569", stroke: "#334155", name: "No Change" }
};

function getChangeColor(changeType) {
  return CHANGE_COLORS[changeType] || { fill: "#6366f1", stroke: "#4f46e5", name: changeType };
}

/**
 * Checks if a precomputed multi-temporal analysis exists in PostgreSQL for this site.
 */
async function checkAndLoadExistingAnalysis(siteKey) {
  const statusBadge = document.getElementById("analysis-status-badge");
  const resultsContainer = document.getElementById("analysis-results-container");
  const legend = document.getElementById("change-map-legend");

  if (changeVectorLayer) {
    changeMap.removeLayer(changeVectorLayer);
    changeVectorLayer = null;
  }
  if (resultsContainer) resultsContainer.style.display = "none";
  if (legend) legend.style.display = "none";

  if (statusBadge) {
    statusBadge.textContent = "Checking DB cache...";
    statusBadge.style.color = "#38bdf8";
    statusBadge.style.borderColor = "rgba(6, 182, 212, 0.4)";
  }

  try {
    const res = await fetch(`/api/v1/change/analysis/${encodeURIComponent(siteKey)}`);
    if (res.ok) {
      const data = await res.json();
      if (data && data.overall && data.overall.total_regions > 0) {
        // If cached analysis is missing band maps or cursor grids, re-run sequence once so user sees full maps
        const p0 = data.pairwise && data.pairwise[0];
        if (!p0 || !p0.ndvi_before_url || !p0.binary_mask_url) {
          console.log("Cached analysis is missing multi-band maps. Auto-running sequence...");
          runMultiTemporalSequenceAnalysis();
          return;
        }

        currentSiteAnalysis = data;
        if (statusBadge) {
          statusBadge.textContent = "Cached (PostgreSQL)";
          statusBadge.style.color = "#10b981";
          statusBadge.style.borderColor = "rgba(16, 185, 129, 0.4)";
        }
        renderAnalysisUI(data);
        renderChangeVectorLayer(data.overall.change_geojson, "Overall Aggregated");

        // Ensure workbench is full studio mode
        const drawer = document.getElementById("temporal-drawer");
        if (drawer) {
          drawer.classList.remove("side-mode");
          const icon = document.getElementById("expand-icon");
          const text = document.getElementById("expand-text");
          if (icon) icon.textContent = "🗗";
          if (text) text.textContent = "Side Panel";
        }
        return;
      }
    }
  } catch (e) {
    console.debug("No cached analysis found for site:", siteKey);
  }

  if (statusBadge) {
    statusBadge.textContent = "Ready to Analyze";
    statusBadge.style.color = "#94a3b8";
    statusBadge.style.borderColor = "rgba(255, 255, 255, 0.15)";
  }
}

/**
 * Triggers the end-to-end multi-temporal sequence analysis via the REST API.
 */
async function runMultiTemporalSequenceAnalysis() {
  if (!currentSiteTimeline || !currentSiteTimeline.site_key) return;

  const siteKey = currentSiteTimeline.site_key;
  const runBtn = document.getElementById("btn-run-analysis");
  const runIcon = document.getElementById("run-btn-icon");
  const runText = document.getElementById("run-btn-text");
  const statusBadge = document.getElementById("analysis-status-badge");
  const chkForce = document.getElementById("chk-force-refresh");

  const forceRefresh = chkForce ? chkForce.checked : false;

  if (runBtn) {
    runBtn.disabled = true;
    if (runIcon) runIcon.textContent = "⏳";
    if (runText) runText.textContent = "Running ML Engine...";
  }
  if (statusBadge) {
    statusBadge.textContent = "Chaining N-1 Pairs...";
    statusBadge.style.color = "#f59e0b";
    statusBadge.style.borderColor = "rgba(245, 158, 11, 0.4)";
  }

  try {
    const res = await fetch("/api/v1/change/analyze/sequence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_key: siteKey,
        force_refresh: forceRefresh,
        save_to_db: true
      })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Analysis failed" }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    currentSiteAnalysis = data;

    if (statusBadge) {
      statusBadge.textContent = "Analysis Complete & Saved";
      statusBadge.style.color = "#10b981";
      statusBadge.style.borderColor = "rgba(16, 185, 129, 0.5)";
    }

    renderAnalysisUI(data);
    renderChangeVectorLayer(data.overall.change_geojson, "Overall Aggregated");

  } catch (err) {
    console.error("Multi-temporal analysis error:", err);
    if (statusBadge) {
      statusBadge.textContent = `Error: ${err.message}`;
      statusBadge.style.color = "#f43f5e";
      statusBadge.style.borderColor = "rgba(244, 63, 94, 0.4)";
    }
    alert(`Change analysis failed: ${err.message}`);
  } finally {
    if (runBtn) {
      runBtn.disabled = false;
      if (runIcon) runIcon.textContent = "⚡";
      if (runText) runText.textContent = "Run Multi-Temporal Analysis";
    }
  }
}

/**
 * Renders the analysis metrics, transition breakdown bar, and region list.
 */
function renderAnalysisUI(data) {
  const container = document.getElementById("analysis-results-container");
  if (!container) return;

  container.style.display = "flex";

  const overall = data.overall || {};
  const geojson = overall.change_geojson || {};
  const features = geojson.features || [];
  const breakdown = overall.change_type_breakdown || {};
  const pairwise = data.pairwise || [];

  // 1. Key Metrics
  const statCount = document.getElementById("stat-region-count");
  const statDate = document.getElementById("stat-earliest-date");
  const statArea = document.getElementById("stat-total-area");
  const statDominant = document.getElementById("stat-dominant-type");

  if (statCount) statCount.textContent = features.length;

  // Earliest supported date across regions (excluding pure No Change)
  const realChangeDates = features
    .filter(f => (f.properties?.change_type !== "No Change"))
    .map(f => f.properties?.earliest_supported_date)
    .filter(Boolean);

  const earliestDate = realChangeDates.length > 0 ? realChangeDates.sort()[0] : "--";
  if (statDate) statDate.textContent = earliestDate;

  // Total area in m2 or hectares
  let totalAreaM2 = 0;
  features.forEach(f => {
    totalAreaM2 += (f.properties?.area_sq_m || f.properties?.area_m2 || 0);
  });
  if (statArea) {
    if (totalAreaM2 >= 10000) {
      statArea.textContent = `${(totalAreaM2 / 10000).toFixed(1)} ha`;
    } else {
      statArea.textContent = `${Math.round(totalAreaM2).toLocaleString()} m²`;
    }
  }

  // Dominant transition type
  const topTypeEntry = Object.entries(breakdown)
    .filter(([k]) => k !== "No Change")
    .sort((a, b) => b[1] - a[1])[0];
  if (statDominant) {
    statDominant.textContent = topTypeEntry ? `${topTypeEntry[0]} (${topTypeEntry[1]}%)` : "--";
  }

  // 2. Stacked Breakdown Bar
  const stackedBar = document.getElementById("breakdown-stacked-bar");
  const pillsContainer = document.getElementById("breakdown-pills-container");

  if (stackedBar) stackedBar.innerHTML = "";
  if (pillsContainer) pillsContainer.innerHTML = "";

  Object.entries(breakdown).forEach(([cType, pct]) => {
    if (pct <= 0) return;
    const colorInfo = getChangeColor(cType);

    // Segment in stacked bar
    if (stackedBar) {
      const seg = document.createElement("div");
      seg.className = "stacked-segment";
      seg.style.width = `${pct}%`;
      seg.style.backgroundColor = colorInfo.fill;
      seg.title = `${cType}: ${pct}%`;
      stackedBar.appendChild(seg);
    }

    // Legend pill
    if (pillsContainer) {
      const pill = document.createElement("div");
      pill.className = "breakdown-pill";
      pill.innerHTML = `
        <div class="breakdown-pill-dot" style="background-color: ${colorInfo.fill};"></div>
        <span>${colorInfo.name || cType}: <b>${pct}%</b></span>
      `;
      pillsContainer.appendChild(pill);
    }
  });

  // 3. Render Plotted Spectral Graph (NDVI, NDBI, NDWI)
  const initialProfile = pairwise[0]?.spectral_profile || {
    before: { ndvi: 0.65, ndbi: -0.42, ndwi: -0.25 },
    after: { ndvi: 0.12, ndbi: 0.38, ndwi: -0.10 },
    delta: { ndvi: -0.53, ndbi: 0.80, ndwi: 0.15 }
  };
  updateSpectralGraph(initialProfile, "Cumulative Shift");

  // 4. Render Year-Wise Changes & Binary Mask Quad Gallery
  renderYearwiseMaskGallery(pairwise);

  // 5. Populate Pair Interval Dropdown
  const selectInterval = document.getElementById("select-pairwise-interval");
  if (selectInterval) {
    selectInterval.innerHTML = `<option value="overall">All Pairs Combined (${features.length} regions)</option>`;
    pairwise.forEach((p, idx) => {
      const count = p.change_geojson?.features?.length || 0;
      const opt = document.createElement("option");
      opt.value = idx.toString();
      opt.textContent = `Pair ${idx + 1}: ${p.date_before} → ${p.date_after} (${count} polys)`;
      selectInterval.appendChild(opt);
    });
    selectInterval.value = "overall";
  }

  // 6. Populate Regions List
  renderRegionsList(features);
}

/**
 * Plots the Before vs After NDVI, NDBI, and NDWI comparison bar chart.
 */
function updateSpectralGraph(profile, targetLabel = "Site Mean") {
  const badge = document.getElementById("spectral-target-badge");
  if (badge) badge.textContent = `Target: ${targetLabel}`;

  if (!profile) {
    profile = {
      before: { ndvi: 0.0, ndbi: 0.0, ndwi: 0.0 },
      after: { ndvi: 0.0, ndbi: 0.0, ndwi: 0.0 },
      delta: { ndvi: 0.0, ndbi: 0.0, ndwi: 0.0 }
    };
  }

  const b = profile.before || {};
  const a = profile.after || {};
  const d = profile.delta || {
    ndvi: (a.ndvi || 0) - (b.ndvi || 0),
    ndbi: (a.ndbi || 0) - (b.ndbi || 0),
    ndwi: (a.ndwi || 0) - (b.ndwi || 0)
  };

  // Convert index [-1, +1] to visual bar percentage [0%, 100%]
  function toWidth(val) {
    if (val == null || isNaN(val)) return "0%";
    const clamped = Math.max(-1, Math.min(1, val));
    return `${Math.round(((clamped + 1) / 2) * 100)}%`;
  }

  function fmtVal(val) {
    if (val == null || isNaN(val)) return "--";
    return (val >= 0 ? "+" : "") + Number(val).toFixed(2);
  }

  function updateIndexCol(name, color) {
    const barB = document.getElementById(`${name}-bar-before`);
    const valB = document.getElementById(`${name}-val-before`);
    const barA = document.getElementById(`${name}-bar-after`);
    const valA = document.getElementById(`${name}-val-after`);
    const deltaEl = document.getElementById(`${name}-delta-badge`);

    const vB = b[name] != null ? b[name] : 0;
    const vA = a[name] != null ? a[name] : 0;
    const vD = d[name] != null ? d[name] : (vA - vB);

    if (barB) barB.style.width = toWidth(vB);
    if (valB) valB.textContent = fmtVal(vB);
    if (barA) {
      barA.style.width = toWidth(vA);
      barA.style.backgroundColor = color;
    }
    if (valA) valA.textContent = fmtVal(vA);

    if (deltaEl) {
      deltaEl.textContent = `Δ ${fmtVal(vD)}`;
      if (Math.abs(vD) < 0.05) {
        deltaEl.style.color = "#94a3b8";
        deltaEl.style.background = "rgba(255,255,255,0.06)";
        deltaEl.style.borderColor = "rgba(255,255,255,0.15)";
      } else if (vD > 0) {
        deltaEl.style.color = color;
        deltaEl.style.background = `${color}22`;
        deltaEl.style.borderColor = `${color}66`;
      } else {
        deltaEl.style.color = "#f43f5e";
        deltaEl.style.background = "rgba(244, 63, 94, 0.15)";
        deltaEl.style.borderColor = "rgba(244, 63, 94, 0.4)";
      }
    }
  }

  updateIndexCol("ndvi", "#10b981");
  updateIndexCol("ndbi", "#f59e0b");
  updateIndexCol("ndwi", "#06b6d4");
}

/**
 * Renders the Year-Wise Changes & Binary Mask Quad Gallery.
 */
function renderYearwiseMaskGallery(pairwise) {
  const container = document.getElementById("yearwise-masks-container");
  const countLbl = document.getElementById("mask-gallery-count-lbl");

  if (countLbl) countLbl.textContent = `${pairwise.length} intervals`;
  if (!container) return;

  container.innerHTML = "";

  pairwise.forEach((pair, idx) => {
    const card = document.createElement("div");
    card.className = "mask-pair-card";

    const pFeatures = pair.change_geojson?.features || [];
    const changePct = pair.change_pct != null ? `${pair.change_pct}%` : "--%";

    // Verification stats
    const candPx = pair.candidate_pixels ?? (pair.filter_stats?.candidate_changes || 0);
    const fpRejected = pair.false_positives_rejected ?? (pair.filter_stats?.false_positives_rejected || 0);
    const verifiedPx = pair.changed_pixels ?? (pair.filter_stats?.verified_changes || 0);
    const rejRate = candPx > 0 ? ((fpRejected / candPx) * 100).toFixed(1) : "0.0";

    // Spectral shift deltas
    const dNdvi = pair.spectral_profile?.delta?.ndvi != null ? (pair.spectral_profile.delta.ndvi >= 0 ? "+" : "") + pair.spectral_profile.delta.ndvi.toFixed(2) : "--";
    const dNdbi = pair.spectral_profile?.delta?.ndbi != null ? (pair.spectral_profile.delta.ndbi >= 0 ? "+" : "") + pair.spectral_profile.delta.ndbi.toFixed(2) : "--";
    const dNdwi = pair.spectral_profile?.delta?.ndwi != null ? (pair.spectral_profile.delta.ndwi >= 0 ? "+" : "") + pair.spectral_profile.delta.ndwi.toFixed(2) : "--";

    // Image URLs (fallback to timeline thumbnails if necessary)
    const snapB = currentSiteTimeline?.snapshots?.find(s => s.tile_id === pair.tile_before_id || s.date === pair.date_before);
    const snapA = currentSiteTimeline?.snapshots?.find(s => s.tile_id === pair.tile_after_id || s.date === pair.date_after);
    const rgbB = pair.rgb_before_url || snapB?.thumbnail_url || "";
    const rgbA = pair.rgb_after_url || snapA?.thumbnail_url || "";
    const ndviB = pair.ndvi_before_url || "";
    const ndviA = pair.ndvi_after_url || "";
    const rawMaskUrl = pair.binary_mask_url || "";
    const filteredMaskUrl = pair.filtered_mask_url || "";

    // Total area
    let pairAreaM2 = 0;
    pFeatures.forEach(f => pairAreaM2 += (f.properties?.area_sq_m || f.properties?.area_m2 || 0));
    const pairAreaStr = pairAreaM2 >= 10000 ? `${(pairAreaM2 / 10000).toFixed(2)} ha` : `${Math.round(pairAreaM2).toLocaleString()} m²`;

    card.innerHTML = `
      <div class="mask-pair-header">
        <div style="display:flex; align-items:center; gap:10px;">
          <span class="transition-label-badge" style="font-size:11px; padding:4px 8px;">Pair ${idx + 1}</span>
          <span style="font-size:13px; font-family:'JetBrains Mono',monospace; color:#f8fafc; font-weight:700;">
            ${pair.date_before} &rarr; ${pair.date_after}
          </span>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <button class="hud-btn btn-theater-toggle" style="padding:4px 10px; font-size:11px; background:rgba(6,182,212,0.15); border:1px solid rgba(6,182,212,0.4); color:#38bdf8;" title="Maximize this pair to full screen theater mode">
            ⤢ Fullscreen Pair
          </button>
          <span class="metric-pill" style="color:#f59e0b; font-weight:800; font-size:11px;" title="Surface changed">${changePct} Changed</span>
          <span class="metric-pill" style="color:#38bdf8; font-size:11px;" title="Surviving polygons">${pFeatures.length} polys (${pairAreaStr})</span>
        </div>
      </div>

      <!-- Live Cursor Spectral HUD Bar -->
      <div class="pair-cursor-hud" id="cursor-hud-${idx}">
        <div class="hud-cursor-lead">
          <span class="pulse-dot"></span>
          <span>LIVE CURSOR PIXEL INSPECTOR:</span>
          <span class="hud-coords" id="coords-${idx}">Move cursor over any band map below to inspect pixel values</span>
        </div>
        <div class="hud-spectral-readouts" id="readouts-${idx}">
          <div class="readout-group before">
            <span class="readout-tag">Before (${pair.date_before})</span>
            <span class="readout-item">NDVI: <b id="val-ndvi-b-${idx}" style="color:#34d399;">--</b></span>
            <span class="readout-item">NDWI: <b id="val-ndwi-b-${idx}" style="color:#38bdf8;">--</b></span>
            <span class="readout-item">NDBI: <b id="val-ndbi-b-${idx}" style="color:#fbbf24;">--</b></span>
            <span class="readout-class" id="class-b-${idx}">--</span>
          </div>
          <div class="readout-arrow" style="color:#64748b; font-size:16px;">&rarr;</div>
          <div class="readout-group after">
            <span class="readout-tag">After (${pair.date_after})</span>
            <span class="readout-item">NDVI: <b id="val-ndvi-a-${idx}" style="color:#34d399;">--</b></span>
            <span class="readout-item">NDWI: <b id="val-ndwi-a-${idx}" style="color:#38bdf8;">--</b></span>
            <span class="readout-item">NDBI: <b id="val-ndbi-a-${idx}" style="color:#fbbf24;">--</b></span>
            <span class="readout-class" id="class-a-${idx}">--</span>
          </div>
          <div class="readout-transition" id="trans-${idx}">Hover over map</div>
        </div>
      </div>

      <!-- 6-Panel Multi-Band Matrix -->
      <div class="mask-hex-grid">
        <!-- 1. Before RGB -->
        <div class="mask-box" onclick="openLightbox('${rgbB}', 'Before RGB (${pair.date_before})')">
          <span class="mask-box-badge rgb">T1 RGB</span>
          <div class="mask-img-wrap" title="Click to enlarge Before RGB (${pair.date_before})">
            ${rgbB ? `<img src="${rgbB}" alt="Before RGB" loading="eager" />` : `<span style="font-size:10px; color:#94a3b8;">No RGB</span>`}
            <div class="sync-crosshair"></div>
          </div>
          <span class="mask-box-label">Before RGB (${pair.date_before})</span>
        </div>

        <!-- 2. After RGB -->
        <div class="mask-box" onclick="openLightbox('${rgbA}', 'After RGB (${pair.date_after})')">
          <span class="mask-box-badge rgb">T2 RGB</span>
          <div class="mask-img-wrap" title="Click to enlarge After RGB (${pair.date_after})">
            ${rgbA ? `<img src="${rgbA}" alt="After RGB" loading="eager" />` : `<span style="font-size:10px; color:#94a3b8;">No RGB</span>`}
            <div class="sync-crosshair"></div>
          </div>
          <span class="mask-box-label">After RGB (${pair.date_after})</span>
        </div>

        <!-- 3. Before NDVI Band Map -->
        <div class="mask-box" onclick="openLightbox('${ndviB}', 'Before NDVI Band Map (${pair.date_before})')">
          <span class="mask-box-badge ndvi">T1 NDVI</span>
          <div class="mask-img-wrap" title="Click to enlarge Before NDVI Band Map (${pair.date_before})">
            ${ndviB ? `<img src="${ndviB}" alt="Before NDVI" loading="eager" />` : `<span style="font-size:10px; color:#94a3b8;">NDVI Map</span>`}
            <div class="sync-crosshair"></div>
          </div>
          <span class="mask-box-label" style="color:#6ee7b7;">Before NDVI (${pair.date_before})</span>
        </div>

        <!-- 4. After NDVI Band Map -->
        <div class="mask-box" onclick="openLightbox('${ndviA}', 'After NDVI Band Map (${pair.date_after})')">
          <span class="mask-box-badge ndvi">T2 NDVI</span>
          <div class="mask-img-wrap" title="Click to enlarge After NDVI Band Map (${pair.date_after})">
            ${ndviA ? `<img src="${ndviA}" alt="After NDVI" loading="eager" />` : `<span style="font-size:10px; color:#94a3b8;">NDVI Map</span>`}
            <div class="sync-crosshair"></div>
          </div>
          <span class="mask-box-label" style="color:#6ee7b7;">After NDVI (${pair.date_after})</span>
        </div>

        <!-- 5. Raw ChangeFormer Binary Mask -->
        <div class="mask-box" onclick="openLightbox('${rawMaskUrl}', 'Raw ChangeFormer Binary Prediction (0/1)')">
          <span class="mask-box-badge binary">Neural Net</span>
          <div class="mask-img-wrap" style="border-color: rgba(6, 182, 212, 0.4);" title="Raw MTKD-ChangeFormer Binary Prediction (0/1)">
            ${rawMaskUrl ? `<img src="${rawMaskUrl}" alt="ChangeFormer Mask" loading="eager" />` : `<span style="font-size:10px; color:#94a3b8;">Binary Mask</span>`}
            <div class="sync-crosshair"></div>
          </div>
          <span class="mask-box-label" style="color:#38bdf8;">Binary Mask (Where)</span>
        </div>

        <!-- 6. Semantic-Verified Change Mask -->
        <div class="mask-box" onclick="openLightbox('${filteredMaskUrl}', 'Semantic-Verified True Changes (Contradictions Removed)')">
          <span class="mask-box-badge verified">Verified</span>
          <div class="mask-img-wrap" style="border-color: rgba(245, 158, 11, 0.4);" title="Semantically Verified Mask (Seasonal noise rejected)">
            ${filteredMaskUrl ? `<img src="${filteredMaskUrl}" alt="Verified Mask" loading="eager" />` : `<span style="font-size:10px; color:#94a3b8;">Verified Mask</span>`}
            <div class="sync-crosshair"></div>
          </div>
          <span class="mask-box-label" style="color:#f59e0b;">Verified Mask (What)</span>
        </div>
      </div>

      <!-- Verification Audit & False-Positive Rejection Summary -->
      <div class="filter-audit-bar">
        <div class="filter-audit-stat" title="Raw changed pixels identified by binary ChangeFormer">
          <span>⚡ Candidate Pixels:</span>
          <b style="color:#38bdf8;">${candPx.toLocaleString()}</b>
        </div>
        <div class="filter-audit-stat" title="Pixels discarded because before/after bands showed no actual land-cover transition">
          <span>🚫 False-Positives Discarded:</span>
          <b style="color:#f43f5e;">${fpRejected.toLocaleString()} (${rejRate}%)</b>
        </div>
        <div class="filter-audit-stat" title="Verified true land-cover changes confirmed by spectral bands">
          <span>🎯 Real Changes Kept:</span>
          <b style="color:#10b981;">${verifiedPx.toLocaleString()}</b>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <div style="display:flex; gap:8px; font-family:'JetBrains Mono',monospace; font-size:10px;">
            <span title="NDVI Delta">ΔNDVI: <b style="color:${parseFloat(dNdvi) < 0 ? '#f43f5e' : '#10b981'}">${dNdvi}</b></span>
            <span title="NDBI Delta">ΔNDBI: <b style="color:${parseFloat(dNdbi) > 0 ? '#f59e0b' : '#38bdf8'}">${dNdbi}</b></span>
          </div>
          <button class="hud-btn btn-focus-pair" style="padding:4px 10px; font-size:11px;" title="Isolate and inspect this interval's polygons on map">
            🔍 Focus on Map
          </button>
        </div>
      </div>
    `;

    // Theater Mode toggle handler
    const theaterBtn = card.querySelector(".btn-theater-toggle");
    if (theaterBtn) {
      theaterBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        card.classList.toggle("theater-mode");
        const isTheater = card.classList.contains("theater-mode");
        theaterBtn.textContent = isTheater ? "🗗 Exit Fullscreen" : "⤢ Fullscreen Pair";
        theaterBtn.style.color = isTheater ? "#fbbf24" : "#38bdf8";
      });
    }

    // Focus on map handler
    const focusBtn = card.querySelector(".btn-focus-pair");
    if (focusBtn) {
      focusBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        switchChangeViewMode(idx.toString());
        document.querySelectorAll(".mask-pair-card").forEach(c => c.classList.remove("active-pair-card"));
        card.classList.add("active-pair-card");
      });
    }

    // Card click updates the plotted graph
    card.addEventListener("click", () => {
      if (pair.spectral_profile) {
        updateSpectralGraph(pair.spectral_profile, `Pair ${idx + 1} (${pair.date_before} → ${pair.date_after})`);
      }
    });

    // Attach interactive cursor inspector to this card's image tiles
    attachCursorInspector(card, pair, idx);

    container.appendChild(card);
  });
}

/**
 * Attaches real-time mousemove cursor inspector to all 6 band images of a pair.
 */
function attachCursorInspector(card, pair, idx) {
  const sampleGrid = pair.cursor_sample_grid;
  const tooltip = document.getElementById("spectral-cursor-tooltip");
  const maskBoxes = card.querySelectorAll(".mask-box");
  const crosshairs = card.querySelectorAll(".sync-crosshair");

  function classify(ndvi, ndwi, ndbi) {
    if (ndwi > 0.3 && ndbi < -0.1) return "Water";
    if (ndvi > 0.6 && ndbi < 0.0) return "Dense Vegetation";
    if (ndvi >= 0.2 && ndvi <= 0.6 && ndbi < 0.0) return "Moderate Veg";
    if (ndbi > 0.0 && ndvi <= 0.2) return "Built-up / Urban";
    if (ndvi <= 0.1 && ndbi <= 0.0 && ndwi <= 0.0) return "Bare Soil";
    return "Transitional";
  }

  function getTransition(cB, cA) {
    if (cB === cA) return "Unchanged Surface";
    if ((cB.includes("Vegetation") || cB.includes("Bare Soil")) && cA.includes("Built-up")) {
      return "🏗️ Construction";
    }
    if (cB.includes("Vegetation") && cA.includes("Bare Soil")) {
      return "🪓 Clearance";
    }
    if (cB.includes("Water") || cA.includes("Water")) {
      return "💧 Water Variation";
    }
    if (cB.includes("Built-up") && (cA.includes("Vegetation") || cA.includes("Bare Soil"))) {
      return "🏚️ Demolition";
    }
    return `${cB} → ${cA}`;
  }

  maskBoxes.forEach((box) => {
    box.addEventListener("mousemove", (e) => {
      const rect = box.getBoundingClientRect();
      const relX = Math.max(0, Math.min(0.999, (e.clientX - rect.left) / rect.width));
      const relY = Math.max(0, Math.min(0.999, (e.clientY - rect.top) / rect.height));

      const pxX = Math.floor(relX * 512);
      const pxY = Math.floor(relY * 512);

      // Synchronize glowing crosshair across all 6 plots in this pair
      crosshairs.forEach(ch => {
        ch.style.left = `${relX * 100}%`;
        ch.style.top = `${relY * 100}%`;
        ch.style.display = "block";
      });

      let ndviB = 0, ndwiB = 0, ndbiB = 0;
      let ndviA = 0, ndwiA = 0, ndbiA = 0;

      if (sampleGrid && sampleGrid.before && sampleGrid.before.ndvi) {
        const sz = sampleGrid.grid_size || 64;
        const r = Math.min(sz - 1, Math.floor(relY * sz));
        const c = Math.min(sz - 1, Math.floor(relX * sz));
        ndviB = sampleGrid.before.ndvi[r]?.[c] ?? 0;
        ndwiB = sampleGrid.before.ndwi[r]?.[c] ?? 0;
        ndbiB = sampleGrid.before.ndbi[r]?.[c] ?? 0;
        ndviA = sampleGrid.after.ndvi[r]?.[c] ?? 0;
        ndwiA = sampleGrid.after.ndwi[r]?.[c] ?? 0;
        ndbiA = sampleGrid.after.ndbi[r]?.[c] ?? 0;
      } else {
        // Fallback to pair mean
        ndviB = pair.spectral_profile?.before?.ndvi ?? 0.25;
        ndwiB = pair.spectral_profile?.before?.ndwi ?? -0.3;
        ndbiB = pair.spectral_profile?.before?.ndbi ?? 0.05;
        ndviA = pair.spectral_profile?.after?.ndvi ?? 0.28;
        ndwiA = pair.spectral_profile?.after?.ndwi ?? -0.32;
        ndbiA = pair.spectral_profile?.after?.ndbi ?? 0.06;
      }

      const classB = classify(ndviB, ndwiB, ndbiB);
      const classA = classify(ndviA, ndwiA, ndbiA);
      const trans = getTransition(classB, classA);

      // Update Card Live HUD Bar
      const coordsEl = document.getElementById(`coords-${idx}`);
      if (coordsEl) coordsEl.textContent = `Pixel [X: ${pxX}, Y: ${pxY}] (${Math.round(relX*100)}%, ${Math.round(relY*100)}%)`;

      const vNdviB = document.getElementById(`val-ndvi-b-${idx}`);
      const vNdwiB = document.getElementById(`val-ndwi-b-${idx}`);
      const vNdbiB = document.getElementById(`val-ndbi-b-${idx}`);
      const cB = document.getElementById(`class-b-${idx}`);

      if (vNdviB) vNdviB.textContent = (ndviB >= 0 ? "+" : "") + Number(ndviB).toFixed(2);
      if (vNdwiB) vNdwiB.textContent = (ndwiB >= 0 ? "+" : "") + Number(ndwiB).toFixed(2);
      if (vNdbiB) vNdbiB.textContent = (ndbiB >= 0 ? "+" : "") + Number(ndbiB).toFixed(2);
      if (cB) cB.textContent = classB;

      const vNdviA = document.getElementById(`val-ndvi-a-${idx}`);
      const vNdwiA = document.getElementById(`val-ndwi-a-${idx}`);
      const vNdbiA = document.getElementById(`val-ndbi-a-${idx}`);
      const cA = document.getElementById(`class-a-${idx}`);

      if (vNdviA) vNdviA.textContent = (ndviA >= 0 ? "+" : "") + Number(ndviA).toFixed(2);
      if (vNdwiA) vNdwiA.textContent = (ndwiA >= 0 ? "+" : "") + Number(ndwiA).toFixed(2);
      if (vNdbiA) vNdbiA.textContent = (ndbiA >= 0 ? "+" : "") + Number(ndbiA).toFixed(2);
      if (cA) cA.textContent = classA;

      const transEl = document.getElementById(`trans-${idx}`);
      if (transEl) transEl.textContent = trans;

      // Update Floating Tooltip
      if (tooltip) {
        tooltip.style.display = "flex";
        tooltip.style.left = `${e.clientX + 16}px`;
        tooltip.style.top = `${e.clientY + 16}px`;

        const tipCoords = document.getElementById("tip-coords");
        const tipPair = document.getElementById("tip-pair");
        const tipNdviB = document.getElementById("tip-ndvi-b");
        const tipNdwiB = document.getElementById("tip-ndwi-b");
        const tipNdbiB = document.getElementById("tip-ndbi-b");
        const tipClassB = document.getElementById("tip-class-b");

        const tipNdviA = document.getElementById("tip-ndvi-a");
        const tipNdwiA = document.getElementById("tip-ndwi-a");
        const tipNdbiA = document.getElementById("tip-ndbi-a");
        const tipClassA = document.getElementById("tip-class-a");
        const tipTrans = document.getElementById("tip-trans");

        if (tipCoords) tipCoords.textContent = `Pixel [X: ${pxX}, Y: ${pxY}]`;
        if (tipPair) tipPair.textContent = `Pair ${idx + 1}`;
        if (tipNdviB) tipNdviB.textContent = (ndviB >= 0 ? "+" : "") + Number(ndviB).toFixed(2);
        if (tipNdwiB) tipNdwiB.textContent = (ndwiB >= 0 ? "+" : "") + Number(ndwiB).toFixed(2);
        if (tipNdbiB) tipNdbiB.textContent = (ndbiB >= 0 ? "+" : "") + Number(ndbiB).toFixed(2);
        if (tipClassB) tipClassB.textContent = classB;

        if (tipNdviA) tipNdviA.textContent = (ndviA >= 0 ? "+" : "") + Number(ndviA).toFixed(2);
        if (tipNdwiA) tipNdwiA.textContent = (ndwiA >= 0 ? "+" : "") + Number(ndwiA).toFixed(2);
        if (tipNdbiA) tipNdbiA.textContent = (ndbiA >= 0 ? "+" : "") + Number(ndbiA).toFixed(2);
        if (tipClassA) tipClassA.textContent = classA;
        if (tipTrans) tipTrans.textContent = trans;
      }
    });

    box.addEventListener("mouseleave", () => {
      crosshairs.forEach(ch => ch.style.display = "none");
      if (tooltip) tooltip.style.display = "none";
    });
  });
}



/**
 * Renders the scrollable list of detected change regions.
 */
function renderRegionsList(features) {
  const listEl = document.getElementById("regions-scroll-list");
  const countPill = document.getElementById("regions-count-pill");

  if (countPill) countPill.textContent = `${features.length} regions`;
  if (!listEl) return;

  listEl.innerHTML = "";

  if (features.length === 0) {
    listEl.innerHTML = `<div style="font-size:11px; color:#94a3b8; text-align:center; padding:12px;">No significant change polygons survived semantic filtering.</div>`;
    return;
  }

  features.forEach((feat, idx) => {
    const props = feat.properties || {};
    const regId = props.region_id || `Region #${idx + 1}`;
    const cType = props.change_type || "Unclassified";
    const earliestDate = props.earliest_supported_date || "--";
    const areaM2 = Math.round(props.area_sq_m || props.area_m2 || 0);
    const colorInfo = getChangeColor(cType);

    const row = document.createElement("div");
    row.className = "region-row-item";
    row.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:2px;">
        <div style="display:flex; align-items:center; gap:6px;">
          <span style="font-size:11px; font-weight:700; font-family:'JetBrains Mono',monospace; color:#f8fafc;">${regId}</span>
          <span class="region-type-badge" style="background:${colorInfo.fill}22; color:${colorInfo.fill}; border-color:${colorInfo.fill}66;">
            ${cType}
          </span>
        </div>
        <div style="font-size:10px; color:#94a3b8;">
          Area: <b style="color:#cbd5e1;">${areaM2.toLocaleString()} m²</b>
        </div>
      </div>
      <div style="display:flex; flex-direction:column; align-items:flex-end; gap:2px;">
        <span class="badge-earliest" title="Earliest observation date where this change was supported">
          📅 ${earliestDate}
        </span>
        <span style="font-size:9px; color:#38bdf8;">Graph 📊</span>
      </div>
    `;

    // Click focuses that polygon on Leaflet map & updates plotted spectral graph!
    row.addEventListener("click", () => {
      focusChangePolygon(feat);
    });

    listEl.appendChild(row);
  });
}

/**
 * Focuses map on a specific change polygon and updates plotted spectral graph.
 */
function focusChangePolygon(feature) {
  if (!changeVectorLayer || !feature || !feature.geometry) return;

  if (feature.properties?.spectral_profile) {
    updateSpectralGraph(feature.properties.spectral_profile, feature.properties.region_id || "Selected Region");
  }

  const tempLayer = L.geoJSON(feature);
  const bounds = tempLayer.getBounds();
  if (bounds.isValid()) {
    changeMap.fitBounds(bounds, { maxZoom: 17, padding: [100, 100] });

    // Find corresponding leaflet layer and fire click
    changeVectorLayer.eachLayer((layer) => {
      if (layer.feature && (layer.feature.properties?.region_id === feature.properties?.region_id)) {
        layer.fire("click");
      }
    });
  }
}

/**
 * Renders GeoJSON vector features onto the Leaflet map with interactive tooltips and popups.
 */
function renderChangeVectorLayer(geojson, layerTitle = "Change Polygons") {
  const legend = document.getElementById("change-map-legend");

  if (changeVectorLayer) {
    changeMap.removeLayer(changeVectorLayer);
    changeVectorLayer = null;
  }

  if (!geojson || !geojson.features || geojson.features.length === 0) {
    if (legend) legend.style.display = "none";
    return;
  }

  if (legend) legend.style.display = "flex";

  changeVectorLayer = L.geoJSON(geojson, {
    style: (feature) => {
      const cType = feature.properties?.change_type || "Unclassified Structural Change";
      const colors = getChangeColor(cType);
      return {
        color: colors.stroke,
        weight: 2,
        fillColor: colors.fill,
        fillOpacity: 0.45,
        dashArray: cType === "No Change" ? "4, 4" : null
      };
    },
    onEachFeature: (feature, layer) => {
      const props = feature.properties || {};
      const cType = props.change_type || "Change Detected";
      const colors = getChangeColor(cType);
      const earliestDate = props.earliest_supported_date || props.date_after || "N/A";
      const areaM2 = Math.round(props.area_sq_m || props.area_m2 || 0);
      const beforeClass = props.before_class || "N/A";
      const afterClass = props.after_class || "N/A";
      const regId = props.region_id || "Change Polygon";

      // Hover Tooltip
      layer.bindTooltip(`
        <div style="font-family:'Inter',sans-serif; font-size:11px;">
          <b style="color:${colors.fill};">${cType}</b>
          <div style="font-size:10px; color:#94a3b8; margin-top:2px;">
            Earliest Date: <span style="color:#fbbf24; font-family:'JetBrains Mono',monospace;">${earliestDate}</span>
          </div>
        </div>
      `, {
        sticky: true,
        className: "leaflet-custom-tooltip"
      });

      // Click Popup
      layer.bindPopup(`
        <div style="font-family:'Inter',sans-serif; min-width:220px; font-size:11px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <span style="font-weight:800; font-family:'JetBrains Mono',monospace; color:#38bdf8;">${regId}</span>
            <span style="font-size:9px; padding:2px 6px; border-radius:4px; font-weight:700; background:${colors.fill}22; color:${colors.fill}; border:1px solid ${colors.fill}55;">
              ${cType}
            </span>
          </div>

          <div style="background:rgba(255,255,255,0.06); padding:8px; border-radius:6px; margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
              <span style="color:#94a3b8;">Earliest Date:</span>
              <span style="color:#fbbf24; font-family:'JetBrains Mono',monospace; font-weight:800;">${earliestDate}</span>
            </div>
            <div style="display:flex; justify-content:space-between; margin-bottom:3px;">
              <span style="color:#94a3b8;">Surface Area:</span>
              <span style="color:#f8fafc; font-family:'JetBrains Mono',monospace; font-weight:700;">${areaM2.toLocaleString()} m²</span>
            </div>
            ${beforeClass !== "N/A" ? `
            <div style="display:flex; justify-content:space-between; border-top:1px solid rgba(255,255,255,0.08); padding-top:4px; margin-top:4px;">
              <span style="color:#94a3b8;">Land-Cover:</span>
              <span style="color:#cbd5e1;">${beforeClass} → ${afterClass}</span>
            </div>` : ""}
          </div>
        </div>
      `, {
        className: "leaflet-custom-tooltip"
      });

      layer.on("mouseover", () => {
        layer.setStyle({ weight: 3.5, fillOpacity: 0.7 });
      });

      layer.on("click", () => {
        if (props.spectral_profile) {
          updateSpectralGraph(props.spectral_profile, props.region_id || "Selected Polygon");
        }
      });

      layer.on("mouseout", () => {
        if (changeVectorLayer) {
          changeVectorLayer.resetStyle(layer);
        }
      });
    }
  });

  changeVectorLayer.addTo(changeMap);

  const bounds = changeVectorLayer.getBounds();
  if (bounds.isValid()) {
    changeMap.fitBounds(bounds, { padding: [100, 100], maxZoom: 16 });
  }
}

/**
 * Switches the displayed layer between Overall Aggregated and individual consecutive pairs.
 */
function switchChangeViewMode(mode) {
  if (!currentSiteAnalysis) return;

  const tabOverall = document.getElementById("tab-mode-overall");
  const selectInterval = document.getElementById("select-pairwise-interval");

  if (mode === "overall") {
    if (tabOverall) tabOverall.classList.add("active");
    if (selectInterval) selectInterval.value = "overall";

    const features = currentSiteAnalysis.overall?.change_geojson?.features || [];
    renderChangeVectorLayer(currentSiteAnalysis.overall?.change_geojson, "Overall Aggregated");
    renderRegionsList(features);

  } else {
    if (tabOverall) tabOverall.classList.remove("active");
    const pairIdx = parseInt(mode, 10);
    const pairwise = currentSiteAnalysis.pairwise || [];
    const pair = pairwise[pairIdx];

    if (pair && pair.change_geojson) {
      renderChangeVectorLayer(pair.change_geojson, `Pair ${pairIdx + 1}`);
      renderRegionsList(pair.change_geojson.features || []);
    }
  }
}

