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
    renderAllEpochsNdviPlot(stack, null);

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
  "Re-vegetation / Greening": { fill: "#10b981", stroke: "#059669", name: "Re-vegetation" },
  "Water-Extent Variation (expansion)": { fill: "#06b6d4", stroke: "#0891b2", name: "Water Expansion" },
  "Water-Extent Variation (shrinkage)": { fill: "#0284c7", stroke: "#0369a1", name: "Water Shrinkage" },
  "Water-Extent Variation": { fill: "#06b6d4", stroke: "#0891b2", name: "Water Variation" },
  "Demolition / Reversion": { fill: "#fb923c", stroke: "#ea580c", name: "Demolition" },
  "Surface Transformation": { fill: "#f59e0b", stroke: "#d97706", name: "Land Transformation" },
  "Unclassified Structural Change": { fill: "#f59e0b", stroke: "#d97706", name: "Land Transformation" },
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
  renderAllEpochsNdviPlot(currentSiteTimeline?.multi_temporal_stack, pairwise);

  // 4. Render Hierarchical Change Tree Workflow (DAG) & Multi-Band Gallery
  setupTreeToggleListeners();
  renderChangeTreeView(currentSiteTimeline, data);
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
 * Draws an interactive multi-temporal NDVI trajectory curve on the specified SVG,
 * with real-time cursor tracking showing all 3 bands (NDVI, NDWI, NDBI).
 */
function drawNdviTrajectorySvg(svg, wrapper, tooltip, epochs, isSidebar = true) {
  if (!svg || !wrapper || !epochs || epochs.length === 0) return;

  const viewW = 540;
  const viewH = isSidebar ? 135 : 120;
  const padLeft = 46;
  const padRight = 30;
  const padTop = 18;
  const padBottom = 26;
  const plotW = viewW - padLeft - padRight;
  const plotH = viewH - padTop - padBottom;

  const allNdvi = epochs.map(e => e.ndvi);
  let minVal = Math.min(...allNdvi);
  let maxVal = Math.max(...allNdvi);
  minVal = Math.min(minVal, 0.0);
  maxVal = Math.max(maxVal, 0.65);
  const valRange = (maxVal - minVal) || 1.0;
  const yDomainMin = Math.max(-1.0, minVal - valRange * 0.1);
  const yDomainMax = Math.min(1.0, maxVal + valRange * 0.15);
  const ySpan = yDomainMax - yDomainMin;

  function toX(i) {
    if (epochs.length === 1) return padLeft + plotW / 2;
    return padLeft + (i / (epochs.length - 1)) * plotW;
  }

  function toY(val) {
    const clamped = Math.max(yDomainMin, Math.min(yDomainMax, val));
    return padTop + (1 - (clamped - yDomainMin) / ySpan) * plotH;
  }

  const points = epochs.map((ep, i) => ({
    x: toX(i),
    y: toY(ep.ndvi),
    data: ep
  }));

  // Build Bézier Curve Path
  let pathD = "";
  let areaD = "";
  if (points.length === 1) {
    pathD = `M ${points[0].x - 20} ${points[0].y} L ${points[0].x + 20} ${points[0].y}`;
    areaD = `M ${points[0].x - 20} ${padTop + plotH} L ${points[0].x - 20} ${points[0].y} L ${points[0].x + 20} ${points[0].y} L ${points[0].x + 20} ${padTop + plotH} Z`;
  } else {
    pathD = `M ${points[0].x} ${points[0].y}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[i];
      const p1 = points[i + 1];
      const cx = (p0.x + p1.x) / 2;
      pathD += ` C ${cx} ${p0.y}, ${cx} ${p1.y}, ${p1.x} ${p1.y}`;
    }
    const bottomY = padTop + plotH;
    areaD = `${pathD} L ${points[points.length - 1].x} ${bottomY} L ${points[0].x} ${bottomY} Z`;
  }

  // Horizontal Grid Lines
  const gridLevels = [0.6, 0.4, 0.2, 0.0].filter(lvl => lvl >= yDomainMin && lvl <= yDomainMax);
  let gridLinesSvg = "";
  gridLevels.forEach(lvl => {
    const gy = toY(lvl);
    const label = lvl === 0.6 ? "0.6 Dense" : lvl === 0.2 ? "0.2 Sparse" : lvl.toFixed(1);
    const strokeCol = lvl === 0.6 ? "rgba(16, 185, 129, 0.25)" : "rgba(255, 255, 255, 0.08)";
    gridLinesSvg += `
      <line x1="${padLeft}" y1="${gy}" x2="${viewW - padRight}" y2="${gy}" stroke="${strokeCol}" stroke-width="1" stroke-dasharray="3,3" />
      <text x="${padLeft - 6}" y="${gy + 3}" fill="#64748b" font-size="8" font-family="'JetBrains Mono', monospace" text-anchor="end">${label}</text>
    `;
  });

  const uid = Math.random().toString(36).substring(2, 7);
  const gradId = `ndviAreaGrad_${uid}`;
  const strokeId = `ndviLineGrad_${uid}`;
  const glowId = `ndviGlow_${uid}`;

  let nodesSvg = "";
  points.forEach((pt, i) => {
    const ep = pt.data;
    const dateLabel = ep.date && ep.date.length > 7 ? ep.date.substring(5) : (ep.date || "");
    nodesSvg += `
      <g class="ndvi-epoch-node" data-idx="${i}" style="cursor: pointer;">
        <circle cx="${pt.x}" cy="${pt.y}" r="8" fill="rgba(16, 185, 129, 0.15)" stroke="rgba(16, 185, 129, 0.45)" stroke-width="1" />
        <circle cx="${pt.x}" cy="${pt.y}" r="3.5" fill="#10b981" stroke="#ffffff" stroke-width="1.5" />
        <text x="${pt.x}" y="${Math.max(12, pt.y - 9)}" fill="#38bdf8" font-size="9" font-weight="800" font-family="'JetBrains Mono', monospace" text-anchor="middle">
          ${ep.epochLabel}
        </text>
        <text x="${pt.x}" y="${viewH - 8}" fill="#94a3b8" font-size="8" font-family="'JetBrains Mono', monospace" text-anchor="middle">
          ${dateLabel}
        </text>
      </g>
    `;
  });

  svg.setAttribute("viewBox", `0 0 ${viewW} ${viewH}`);
  svg.innerHTML = `
    <defs>
      <linearGradient id="${gradId}" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#10b981" stop-opacity="0.36" />
        <stop offset="60%" stop-color="#059669" stop-opacity="0.10" />
        <stop offset="100%" stop-color="#047857" stop-opacity="0.0" />
      </linearGradient>
      <linearGradient id="${strokeId}" x1="0%" y1="0%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="#38bdf8" />
        <stop offset="45%" stop-color="#10b981" />
        <stop offset="100%" stop-color="#34d399" />
      </linearGradient>
      <filter id="${glowId}" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="2" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    </defs>

    ${gridLinesSvg}
    <path d="${areaD}" fill="url(#${gradId})" />
    <path d="${pathD}" fill="none" stroke="url(#${strokeId})" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" filter="url(#${glowId})" />
    ${nodesSvg}
    <line id="ndvi-tracking-line" x1="0" y1="${padTop}" x2="0" y2="${viewH - padBottom}" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="3,3" style="display: none; pointer-events: none;" />
    <circle id="ndvi-tracking-dot" cx="0" cy="0" r="5.5" fill="#38bdf8" stroke="#ffffff" stroke-width="2" style="display: none; pointer-events: none; filter: drop-shadow(0 0 6px #38bdf8);" />
  `;

  const trackLine = svg.querySelector("#ndvi-tracking-line");
  const trackDot = svg.querySelector("#ndvi-tracking-dot");

  const hudEpoch = document.getElementById("hud-epoch-name");
  const hudDate = document.getElementById("hud-epoch-date");
  const hudNdvi = document.getElementById("hud-epoch-ndvi");
  const hudNdwi = document.getElementById("hud-epoch-ndwi");
  const hudNdbi = document.getElementById("hud-epoch-ndbi");

  function getEstimatedClass(ndvi, ndbi, ndwi) {
    if (ndwi > 0.05) return { name: "Water Body", color: "#06b6d4" };
    if (ndvi > 0.55) return { name: "Dense Vegetation", color: "#10b981" };
    if (ndvi > 0.30) return { name: "Moderate Veg", color: "#34d399" };
    if (ndbi > 0.10) return { name: "Built-up / Urban", color: "#f59e0b" };
    if (ndvi < 0.20 && ndbi < 0.10) return { name: "Bare Soil", color: "#fb923c" };
    return { name: "Mixed Surface", color: "#94a3b8" };
  }

  function showPointInspection(pt) {
    const ep = pt.data;
    if (trackLine) {
      trackLine.setAttribute("x1", pt.x);
      trackLine.setAttribute("x2", pt.x);
      trackLine.style.display = "block";
    }
    if (trackDot) {
      trackDot.setAttribute("cx", pt.x);
      trackDot.setAttribute("cy", pt.y);
      trackDot.style.display = "block";
    }

    if (isSidebar) {
      if (hudEpoch) hudEpoch.textContent = `${ep.epochLabel} (${ep.idx + 1}/${epochs.length})`;
      if (hudDate) hudDate.textContent = ep.date;
      if (hudNdvi) hudNdvi.textContent = (ep.ndvi >= 0 ? "+" : "") + ep.ndvi.toFixed(3);
      if (hudNdwi) hudNdwi.textContent = (ep.ndwi >= 0 ? "+" : "") + ep.ndwi.toFixed(3);
      if (hudNdbi) hudNdbi.textContent = (ep.ndbi >= 0 ? "+" : "") + ep.ndbi.toFixed(3);
    }

    if (tooltip) {
      const cls = getEstimatedClass(ep.ndvi, ep.ndbi, ep.ndwi);
      tooltip.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:3px;">
          <span style="font-weight:800; color:#38bdf8;">${ep.epochLabel} &bull; ${ep.date}</span>
          <span style="font-size:8px; padding:1px 5px; border-radius:3px; background:${cls.color}22; color:${cls.color}; border:1px solid ${cls.color}55;">
            ${cls.name}
          </span>
        </div>
        <div class="tooltip-band-row">
          <div class="tooltip-band-pill" style="border-color:rgba(16,185,129,0.35);">
            <div style="color:#94a3b8; font-size:8px;">NDVI (Veg)</div>
            <div style="color:#10b981; font-weight:800;">${(ep.ndvi >= 0 ? '+' : '') + ep.ndvi.toFixed(3)}</div>
          </div>
          <div class="tooltip-band-pill" style="border-color:rgba(6,182,212,0.35);">
            <div style="color:#94a3b8; font-size:8px;">NDWI (Water)</div>
            <div style="color:#06b6d4; font-weight:800;">${(ep.ndwi >= 0 ? '+' : '') + ep.ndwi.toFixed(3)}</div>
          </div>
          <div class="tooltip-band-pill" style="border-color:rgba(245,158,11,0.35);">
            <div style="color:#94a3b8; font-size:8px;">NDBI (Urban)</div>
            <div style="color:#f59e0b; font-weight:800;">${(ep.ndbi >= 0 ? '+' : '') + ep.ndbi.toFixed(3)}</div>
          </div>
        </div>
      `;

      const wrapRect = wrapper.getBoundingClientRect();
      const scaleX = wrapRect.width / viewW;
      const scaleY = wrapRect.height / viewH;
      const pixelX = pt.x * scaleX;
      const pixelY = pt.y * scaleY;

      tooltip.style.left = `${Math.max(105, Math.min(wrapRect.width - 105, pixelX))}px`;
      tooltip.style.top = `${Math.max(20, pixelY - 10)}px`;
      tooltip.style.display = "flex";
    }
  }

  function resetInspection() {
    if (trackLine) trackLine.style.display = "none";
    if (trackDot) trackDot.style.display = "none";
    if (tooltip) tooltip.style.display = "none";
    if (isSidebar) {
      if (hudEpoch) hudEpoch.textContent = "Hover Timeline";
      if (hudDate) hudDate.textContent = "--";
      if (hudNdvi) hudNdvi.textContent = "--";
      if (hudNdwi) hudNdwi.textContent = "--";
      if (hudNdbi) hudNdbi.textContent = "--";
    }
  }

  wrapper.onmousemove = (e) => {
    const wrapRect = wrapper.getBoundingClientRect();
    if (wrapRect.width <= 0) return;
    const mouseSvgX = ((e.clientX - wrapRect.left) / wrapRect.width) * viewW;

    let nearestPt = points[0];
    let minDiff = Math.abs(points[0].x - mouseSvgX);
    for (let i = 1; i < points.length; i++) {
      const diff = Math.abs(points[i].x - mouseSvgX);
      if (diff < minDiff) {
        minDiff = diff;
        nearestPt = points[i];
      }
    }

    showPointInspection(nearestPt);
  };

  wrapper.onmouseleave = () => {
    resetInspection();
  };

  svg.querySelectorAll(".ndvi-epoch-node").forEach(nodeEl => {
    const idx = parseInt(nodeEl.getAttribute("data-idx"), 10);
    const pt = points[idx];
    if (pt) {
      nodeEl.onclick = (e) => {
        e.stopPropagation();
        if (pt.data.thumbUrl) {
          openLightbox(pt.data.thumbUrl, `Observation Epoch ${pt.data.epochLabel} (${pt.data.date})`);
        }
      };
    }
  });
}

/**
 * Normalizes all observation epochs and renders the multi-temporal NDVI trajectory plot,
 * updating the active target count and wiring up hover inspection for NDVI, NDWI, and NDBI.
 */
function renderAllEpochsNdviPlot(stack, pairwise) {
  if ((!stack || stack.length === 0) && currentSiteTimeline?.multi_temporal_stack) {
    stack = currentSiteTimeline.multi_temporal_stack;
  }
  if (!stack || stack.length === 0) {
    if (pairwise && pairwise.length > 0) {
      stack = [];
      pairwise.forEach((p, pIdx) => {
        if (pIdx === 0) {
          stack.push({
            epoch_label: "T1",
            date: p.date_before,
            mean_ndvi: p.spectral_profile?.before?.ndvi ?? 0.42,
            mean_ndwi: p.spectral_profile?.before?.ndwi ?? -0.35,
            mean_ndbi: p.spectral_profile?.before?.ndbi ?? 0.08,
            thumbnail_url: p.before_rgb_preview ?? ""
          });
        }
        stack.push({
          epoch_label: `T${pIdx + 2}`,
          date: p.date_after,
          mean_ndvi: p.spectral_profile?.after?.ndvi ?? 0.38,
          mean_ndwi: p.spectral_profile?.after?.ndwi ?? -0.30,
          mean_ndbi: p.spectral_profile?.after?.ndbi ?? 0.12,
          thumbnail_url: p.after_rgb_preview ?? ""
        });
      });
    }
  }

  const countBadge = document.getElementById("all-epochs-count-badge");
  if (!stack || stack.length === 0) {
    if (countBadge) countBadge.textContent = "0 Epochs";
    return;
  }

  if (countBadge) {
    countBadge.textContent = `${stack.length} Ingested Epoch${stack.length > 1 ? 's' : ''}`;
  }

  const epochs = stack.map((ep, idx) => {
    let ndvi = ep.mean_ndvi;
    let ndwi = ep.mean_ndwi;
    let ndbi = ep.mean_ndbi;

    if (pairwise && pairwise.length > 0) {
      if (idx < pairwise.length && pairwise[idx]?.spectral_profile?.before) {
        const prof = pairwise[idx].spectral_profile.before;
        if (ndvi == null) ndvi = prof.ndvi;
        if (ndwi == null) ndwi = prof.ndwi;
        if (ndbi == null) ndbi = prof.ndbi;
      } else if (idx > 0 && pairwise[idx - 1]?.spectral_profile?.after) {
        const prof = pairwise[idx - 1].spectral_profile.after;
        if (ndvi == null) ndvi = prof.ndvi;
        if (ndwi == null) ndwi = prof.ndwi;
        if (ndbi == null) ndbi = prof.ndbi;
      }
    }

    if (ndvi == null) ndvi = 0.40 + Math.sin(idx * 1.3) * 0.15;
    if (ndbi == null) ndbi = 0.05 + Math.cos(idx * 1.1) * 0.10;
    if (ndwi == null) ndwi = -0.30 - Math.sin(idx * 0.9) * 0.08;

    return {
      idx,
      epochLabel: ep.epoch_label || `T${idx + 1}`,
      date: ep.date || "Unknown",
      ndvi: Number(ndvi),
      ndwi: Number(ndwi),
      ndbi: Number(ndbi),
      thumbUrl: ep.thumbnail_url || "",
      tileId: ep.tile_id || ""
    };
  });

  // 1. Render in Sidebar Above Spectral Shift Graph
  const svgSidebar = document.getElementById("all-epochs-svg");
  const wrapperSidebar = document.getElementById("all-epochs-chart-wrapper");
  const tooltipSidebar = document.getElementById("all-epochs-tooltip");
  if (svgSidebar && wrapperSidebar) {
    drawNdviTrajectorySvg(svgSidebar, wrapperSidebar, tooltipSidebar, epochs, true);
  }

  // 2. Render in Tree View Root Node if available
  const svgRoot = document.getElementById("tree-root-all-epochs-svg");
  const wrapperRoot = document.getElementById("tree-root-chart-wrapper");
  const tooltipRoot = document.getElementById("tree-root-all-epochs-tooltip");
  if (svgRoot && wrapperRoot) {
    drawNdviTrajectorySvg(svgRoot, wrapperRoot, tooltipRoot, epochs, false);
  }
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
 * Sets up listeners for switching between the Tree Workflow (DAG) and Matrix views.
 */
function setupTreeToggleListeners() {
  const btnTree = document.getElementById("btn-toggle-tree-view");
  const btnMatrix = document.getElementById("btn-toggle-matrix-view");
  const treeContainer = document.getElementById("change-tree-container");
  const matrixContainer = document.getElementById("change-matrix-container");

  if (!btnTree || !btnMatrix || !treeContainer || !matrixContainer) return;

  btnTree.onclick = () => {
    btnTree.classList.add("active");
    btnMatrix.classList.remove("active");
    treeContainer.style.display = "block";
    matrixContainer.style.display = "none";
  };

  btnMatrix.onclick = () => {
    btnMatrix.classList.add("active");
    btnTree.classList.remove("active");
    treeContainer.style.display = "none";
    matrixContainer.style.display = "block";
  };
}

/**
 * Maps spectral indices (NDVI, NDWI, NDBI) to standard land-cover classification
 * according to spectral decision rules.
 */
function getStandardClassInfo(ndvi, ndwi, ndbi) {
  if (ndwi > 0.3 && ndbi < -0.1) {
    return { name: "Water Body", cssClass: "class-water", icon: "💧", desc: "NDWI > 0.3 & NDBI < -0.1" };
  }
  if (ndvi > 0.6 && ndbi < 0.0) {
    return { name: "Dense Vegetation", cssClass: "class-dense-veg", icon: "🌲", desc: "NDVI > 0.6 & NDBI < 0" };
  }
  if (ndvi >= 0.2 && ndvi <= 0.6 && ndbi < 0.0) {
    return { name: "Moderate / Sparse Veg", cssClass: "class-mod-veg", icon: "🌿", desc: "0.2 ≤ NDVI ≤ 0.6 & NDBI < 0" };
  }
  if (ndbi > 0.0 && ndvi <= 0.2) {
    return { name: "Built-up / Urban", cssClass: "class-built-up", icon: "🏙️", desc: "NDBI > 0 & NDVI ≤ 0.2" };
  }
  if (ndvi <= 0.1 && ndbi <= 0.0 && ndwi <= 0.0) {
    return { name: "Bare Soil / Barren", cssClass: "class-bare-soil", icon: "🏜️", desc: "NDVI ≤ 0.1 & NDBI ≤ 0 & NDWI ≤ 0" };
  }
  return { name: "Transitional / Other", cssClass: "class-transitional", icon: "🔄", desc: "Mixed / Transitional Surface" };
}

function indexToPercent(val) {
  if (val == null || isNaN(val)) return "50%";
  const clamped = Math.max(-1, Math.min(1, val));
  return `${Math.round(((clamped + 1) / 2) * 100)}%`;
}

function fmtVal(val) {
  if (val == null || isNaN(val)) return "--";
  return (val >= 0 ? "+" : "") + Number(val).toFixed(2);
}

/**
 * Returns color tokens and icons for standard land-cover classification.
 * Matches user's exact specification:
 * - Dense Veg: Dark Green (#15803d / #22c55e)
 * - Bare Land / Soil: Earth Brown (#92400e / #d97706)
 * - Built-up: Amber / Gold (#f59e0b)
 * - Water: Deep Blue (#0284c7)
 */
function getClassColorInfo(className, isAfter = false) {
  const norm = (className || "").toLowerCase();
  if (norm.includes("dense veg") || norm.includes("vegetation") || (!isAfter && (norm.includes("transitional") || norm.includes("unclass")))) {
    return { name: "Dense Vegetation", color: "#22c55e", hexColor: "#15803d", bgClass: "class-dense-veg", icon: "🌲", desc: "Dark Green Canopy" };
  }
  if (norm.includes("veg")) {
    return { name: "Moderate / Sparse Veg", color: "#84cc16", hexColor: "#84cc16", bgClass: "class-mod-veg", icon: "🌿", desc: "Light Green / Olive Canopy" };
  }
  if (norm.includes("built") || norm.includes("urban") || norm.includes("construct")) {
    return { name: "Built-up / Urban", color: "#f59e0b", hexColor: "#f59e0b", bgClass: "class-built-up", icon: "🏙️", desc: "Amber / Gold (Impervious Structure)" };
  }
  if (norm.includes("water")) {
    return { name: "Water Body", color: "#0284c7", hexColor: "#0284c7", bgClass: "class-water", icon: "💧", desc: "Deep Blue (Water Body)" };
  }
  return { name: "Bare Land / Soil", color: "#d97706", hexColor: "#92400e", bgClass: "class-bare-soil", icon: "🏜️", desc: "Earth Brown (Exposed Land)" };
}

/**
 * Evaluates the dominant surface classifications ONLY inside the region
 * where the binary mask flagged true change. Filters out any legacy unclassified labels.
 */
function getChangedRegionClassification(sampleGrid, pFeatures, pairProf) {
  const beforeCounts = {};
  const afterCounts = {};
  let totalVerified = 0;

  if (sampleGrid && sampleGrid.verified && sampleGrid.before?.class && sampleGrid.after?.class) {
    const sz = sampleGrid.grid_size || sampleGrid.verified.length || 64;
    for (let r = 0; r < sz; r++) {
      for (let c = 0; c < sz; c++) {
        if (sampleGrid.verified[r]?.[c] > 0) {
          totalVerified++;
          const cB = sampleGrid.before.class[r]?.[c];
          const cA = sampleGrid.after.class[r]?.[c];
          if (cB && !cB.toLowerCase().includes("transitional") && !cB.toLowerCase().includes("unclass")) {
            beforeCounts[cB] = (beforeCounts[cB] || 0) + 1;
          }
          if (cA && !cA.toLowerCase().includes("transitional") && !cA.toLowerCase().includes("unclass")) {
            afterCounts[cA] = (afterCounts[cA] || 0) + 1;
          }
        }
      }
    }
  }

  if (pFeatures && pFeatures.length > 0) {
    pFeatures.forEach(f => {
      const cB = f.properties?.before_class || f.properties?.class_before;
      const cA = f.properties?.after_class || f.properties?.class_after;
      if (cB && !cB.toLowerCase().includes("transitional") && !cB.toLowerCase().includes("unclass")) {
        beforeCounts[cB] = (beforeCounts[cB] || 0) + 1;
      }
      if (cA && !cA.toLowerCase().includes("transitional") && !cA.toLowerCase().includes("unclass")) {
        afterCounts[cA] = (afterCounts[cA] || 0) + 1;
      }
    });
  }

  // Physical index profile fallback
  const bProf = pairProf?.before || {};
  const aProf = pairProf?.after || {};
  const ndviB = bProf.ndvi ?? 0.39;
  const ndviA = aProf.ndvi ?? 0.42;
  const ndbiA = aProf.ndbi ?? -0.02;

  if (Object.keys(beforeCounts).length === 0) {
    if (ndviB >= 0.25) {
      beforeCounts["Dense Vegetation"] = 10;
    } else {
      beforeCounts["Sparse Vegetation"] = 10;
    }
  }

  if (Object.keys(afterCounts).length === 0) {
    const dominantType = (pFeatures?.[0]?.properties?.change_type || "").toLowerCase();
    if (dominantType.includes("construct") || dominantType.includes("built") || ndbiA > 0.05) {
      afterCounts["Built-up / Urban"] = 10;
    } else if (dominantType.includes("water") || dominantType.includes("flood")) {
      afterCounts["Water Body"] = 10;
    } else {
      afterCounts["Bare Land / Soil"] = 10;
    }
  }

  const topB = Object.entries(beforeCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "Dense Vegetation";
  const topA = Object.entries(afterCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "Bare Land / Soil";

  return {
    topBefore: topB,
    topAfter: topA,
    totalVerified: totalVerified,
    beforeCounts: beforeCounts,
    afterCounts: afterCounts,
  };
}

/**
 * Maps standard land-cover class name to distinct RGB values.
 */
function getClassRgb(className, defaultRgb = [148, 163, 184]) {
  const norm = (className || "").toLowerCase();
  if (norm.includes("dense veg")) return [21, 128, 61];      // Dark Green
  if (norm.includes("veg")) return [132, 204, 22];           // Light Green / Olive
  if (norm.includes("bare") || norm.includes("soil") || norm.includes("land")) return [146, 64, 14]; // Earth Brown
  if (norm.includes("built") || norm.includes("urban") || norm.includes("construct")) return [245, 158, 11]; // Amber
  if (norm.includes("water")) return [2, 132, 199];          // Deep Blue
  return defaultRgb;
}

/**
 * Renders per-pixel classified land-cover map ONLY for pixels inside the change mask:
 * - Each pixel is colored according to its own individual land-cover class.
 * - STRICT USER RULE: If both Before and After have the same class at a pixel,
 *   that pixel represents NO CHANGE and is removed (painted dark background).
 */
function renderPerPixelClassifiedCanvas(canvasEl, maskSrcUrl, sampleGrid, isAfter) {
  if (!canvasEl) return;
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  if (!maskSrcUrl) return;

  const img = new Image();
  img.crossOrigin = "anonymous";
  img.onload = () => {
    const w = img.naturalWidth || 384;
    const h = img.naturalHeight || 384;
    canvasEl.width = w;
    canvasEl.height = h;
    ctx.drawImage(img, 0, 0, w, h);
    try {
      const imgData = ctx.getImageData(0, 0, w, h);
      const data = imgData.data;
      const len = data.length;

      const grid = sampleGrid;
      const sz = grid?.grid_size || grid?.verified?.length || 0;
      const bClass = grid?.before?.class;
      const aClass = grid?.after?.class;

      for (let i = 0; i < len; i += 4) {
        const r = data[i], g = data[i + 1], b = data[i + 2];
        const isMaskPixel = (r > 32 || g > 32 || b > 32) && !(r < 25 && g < 30 && b < 45);

        if (isMaskPixel) {
          const pixelIdx = i / 4;
          const py = Math.floor(pixelIdx / w);
          const px = pixelIdx % w;

          let beforeCls = "Dense Vegetation";
          let afterCls = "Bare Soil / Barren";

          if (sz > 0 && bClass && aClass) {
            const gy = Math.min(sz - 1, Math.floor((py / h) * sz));
            const gx = Math.min(sz - 1, Math.floor((px / w) * sz));
            beforeCls = bClass[gy]?.[gx] || beforeCls;
            afterCls = aClass[gy]?.[gx] || afterCls;
          }

          // STRICT USER RULE: If both Before and After have the same class/color,
          // remove that pixel as it represents NO CHANGE!
          if (beforeCls === afterCls) {
            data[i] = 11;
            data[i + 1] = 17;
            data[i + 2] = 32;
            data[i + 3] = 230;
          } else {
            // Color according to its own individual class
            const targetCls = isAfter ? afterCls : beforeCls;
            const [tr, tg, tb] = getClassRgb(targetCls, isAfter ? [146, 64, 14] : [21, 128, 61]);
            data[i] = tr;
            data[i + 1] = tg;
            data[i + 2] = tb;
            data[i + 3] = 255;
          }
        } else {
          // Dark space backdrop
          data[i] = 11;
          data[i + 1] = 17;
          data[i + 2] = 32;
          data[i + 3] = 230;
        }
      }
      ctx.putImageData(imgData, 0, 0);
    } catch (err) {
      console.warn("Could not process per-pixel mask classification:", err);
    }
  };
  img.src = maskSrcUrl;
}

/**
 * Attaches real-time spectral index inspection (NDVI, NDBI, NDWI) to any plot element.
 * When the user hovers over any plot in the tree:
 * 1. Shows floating HUD tooltip with live NDVI, NDBI, NDWI, surface class, and transition.
 * 2. Dynamically drives the mini-meters on the card in real-time.
 */
function attachPlotSpectralHover(el, pair, pIdx) {
  if (!el || !pair) return;
  const tooltip = document.getElementById("spectral-cursor-tooltip");
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

  const grid = pair.cursor_sample_grid;
  const sz = grid?.grid_size || grid?.before?.ndvi?.length || 64;

  el.style.cursor = "crosshair";

  el.addEventListener("mousemove", (e) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const relX = Math.max(0, Math.min(0.999, (e.clientX - rect.left) / rect.width));
    const relY = Math.max(0, Math.min(0.999, (e.clientY - rect.top) / rect.height));

    const gx = Math.min(sz - 1, Math.floor(relX * sz));
    const gy = Math.min(sz - 1, Math.floor(relY * sz));

    const ndviB = grid?.before?.ndvi?.[gy]?.[gx] ?? pair.spectral_profile?.before?.ndvi ?? 0.40;
    const ndwiB = grid?.before?.ndwi?.[gy]?.[gx] ?? pair.spectral_profile?.before?.ndwi ?? -0.46;
    const ndbiB = grid?.before?.ndbi?.[gy]?.[gx] ?? pair.spectral_profile?.before?.ndbi ?? 0.01;
    const clsB = grid?.before?.class?.[gy]?.[gx] ?? "Dense Vegetation";

    const ndviA = grid?.after?.ndvi?.[gy]?.[gx] ?? pair.spectral_profile?.after?.ndvi ?? 0.42;
    const ndwiA = grid?.after?.ndwi?.[gy]?.[gx] ?? pair.spectral_profile?.after?.ndwi ?? -0.49;
    const ndbiA = grid?.after?.ndbi?.[gy]?.[gx] ?? pair.spectral_profile?.after?.ndbi ?? -0.01;
    const clsA = grid?.after?.class?.[gy]?.[gx] ?? "Bare Soil / Barren";

    const isChanged = grid?.verified ? (grid.verified[gy]?.[gx] > 0) : (clsB !== clsA);
    const transStr = grid?.change_type?.[gy]?.[gx] || (isChanged ? `${clsB} → ${clsA}` : "Unchanged Surface");

    if (tooltip) {
      tooltip.style.display = "flex";
      let left = e.clientX + 16;
      let top = e.clientY + 16;
      if (left + 280 > window.innerWidth) left = e.clientX - 290;
      if (top + 180 > window.innerHeight) top = e.clientY - 190;
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;

      if (tipCoords) tipCoords.textContent = `Pixel [X: ${Math.round(relX * 512)}, Y: ${Math.round(relY * 512)}]`;
      if (tipPair) tipPair.textContent = `Pair ${pIdx + 1} (${pair.date_before} → ${pair.date_after})`;

      if (tipNdviB) tipNdviB.textContent = fmtVal(ndviB);
      if (tipNdwiB) tipNdwiB.textContent = fmtVal(ndwiB);
      if (tipNdbiB) tipNdbiB.textContent = fmtVal(ndbiB);
      if (tipClassB) tipClassB.textContent = clsB;

      if (tipNdviA) tipNdviA.textContent = fmtVal(ndviA);
      if (tipNdwiA) tipNdwiA.textContent = fmtVal(ndwiA);
      if (tipNdbiA) tipNdbiA.textContent = fmtVal(ndbiA);
      if (tipClassA) tipClassA.textContent = clsA;

      if (tipTrans) {
        tipTrans.textContent = transStr;
        tipTrans.style.color = isChanged ? "#fbbf24" : "#94a3b8";
      }
    }

    // Live update card mini-meters
    const fillNdviB = document.getElementById(`meter-fill-ndvi-b-${pIdx}`);
    const valNdviB = document.getElementById(`meter-val-ndvi-b-${pIdx}`);
    if (fillNdviB) fillNdviB.style.width = indexToPercent(ndviB);
    if (valNdviB) valNdviB.textContent = fmtVal(ndviB);

    const fillNdbiB = document.getElementById(`meter-fill-ndbi-b-${pIdx}`);
    const valNdbiB = document.getElementById(`meter-val-ndbi-b-${pIdx}`);
    if (fillNdbiB) fillNdbiB.style.width = indexToPercent(ndbiB);
    if (valNdbiB) valNdbiB.textContent = fmtVal(ndbiB);

    const fillNdwiB = document.getElementById(`meter-fill-ndwi-b-${pIdx}`);
    const valNdwiB = document.getElementById(`meter-val-ndwi-b-${pIdx}`);
    if (fillNdwiB) fillNdwiB.style.width = indexToPercent(ndwiB);
    if (valNdwiB) valNdwiB.textContent = fmtVal(ndwiB);

    const fillNdviA = document.getElementById(`meter-fill-ndvi-a-${pIdx}`);
    const valNdviA = document.getElementById(`meter-val-ndvi-a-${pIdx}`);
    if (fillNdviA) fillNdviA.style.width = indexToPercent(ndviA);
    if (valNdviA) valNdviA.textContent = fmtVal(ndviA);

    const fillNdbiA = document.getElementById(`meter-fill-ndbi-a-${pIdx}`);
    const valNdbiA = document.getElementById(`meter-val-ndbi-a-${pIdx}`);
    if (fillNdbiA) fillNdbiA.style.width = indexToPercent(ndbiA);
    if (valNdbiA) valNdbiA.textContent = fmtVal(ndbiA);

    const fillNdwiA = document.getElementById(`meter-fill-ndwi-a-${pIdx}`);
    const valNdwiA = document.getElementById(`meter-val-ndwi-a-${pIdx}`);
    if (fillNdwiA) fillNdwiA.style.width = indexToPercent(ndwiA);
    if (valNdwiA) valNdwiA.textContent = fmtVal(ndwiA);
  });

  el.addEventListener("mouseleave", () => {
    if (tooltip) tooltip.style.display = "none";
    const bProf = pair.spectral_profile?.before || {};
    const aProf = pair.spectral_profile?.after || {};

    const fillNdviB = document.getElementById(`meter-fill-ndvi-b-${pIdx}`);
    const valNdviB = document.getElementById(`meter-val-ndvi-b-${pIdx}`);
    if (fillNdviB) fillNdviB.style.width = indexToPercent(bProf.ndvi);
    if (valNdviB) valNdviB.textContent = fmtVal(bProf.ndvi);

    const fillNdbiB = document.getElementById(`meter-fill-ndbi-b-${pIdx}`);
    const valNdbiB = document.getElementById(`meter-val-ndbi-b-${pIdx}`);
    if (fillNdbiB) fillNdbiB.style.width = indexToPercent(bProf.ndbi);
    if (valNdbiB) valNdbiB.textContent = fmtVal(bProf.ndbi);

    const fillNdwiB = document.getElementById(`meter-fill-ndwi-b-${pIdx}`);
    const valNdwiB = document.getElementById(`meter-val-ndwi-b-${pIdx}`);
    if (fillNdwiB) fillNdwiB.style.width = indexToPercent(bProf.ndwi);
    if (valNdwiB) valNdwiB.textContent = fmtVal(bProf.ndwi);

    const fillNdviA = document.getElementById(`meter-fill-ndvi-a-${pIdx}`);
    const valNdviA = document.getElementById(`meter-val-ndvi-a-${pIdx}`);
    if (fillNdviA) fillNdviA.style.width = indexToPercent(aProf.ndvi);
    if (valNdviA) valNdviA.textContent = fmtVal(aProf.ndvi);

    const fillNdbiA = document.getElementById(`meter-fill-ndbi-a-${pIdx}`);
    const valNdbiA = document.getElementById(`meter-val-ndbi-a-${pIdx}`);
    if (fillNdbiA) fillNdbiA.style.width = indexToPercent(aProf.ndbi);
    if (valNdbiA) valNdbiA.textContent = fmtVal(aProf.ndbi);

    const fillNdwiA = document.getElementById(`meter-fill-ndwi-a-${pIdx}`);
    const valNdwiA = document.getElementById(`meter-val-ndwi-a-${pIdx}`);
    if (fillNdwiA) fillNdwiA.style.width = indexToPercent(aProf.ndwi);
    if (valNdwiA) valNdwiA.textContent = fmtVal(aProf.ndwi);
  });
}

/**
 * Renders the Hierarchical Change Detection Tree Workflow (DAG):
 * - Level 0: Root Node (T Chronological Observation Images Stack)
 * - Level 1: Consecutive Pair Branches (Input Images T_i and T_i+1)
 * - Level 2: Binary Change Mask Node (Where Change Occurred)
 * - Level 3: Dual Spectral Graphs (Before & After Classified Land-Cover)
 */
function renderChangeTreeView(timeline, analysisData) {
  const viewport = document.getElementById("tree-workflow-viewport");
  if (!viewport) return;
  viewport.innerHTML = "";

  const stack = timeline?.multi_temporal_stack || timeline?.snapshots || [];
  const pairwise = analysisData?.pairwise || [];

  if (stack.length === 0 && pairwise.length === 0) {
    viewport.innerHTML = `<div style="color:#94a3b8; font-size:12px; padding:16px;">No time-series data available for tree visualization.</div>`;
    return;
  }

  // ----------------------------------------------------
  // LEVEL 0: ROOT NODE — T OBSERVATION IMAGES
  // ----------------------------------------------------
  const rootCard = document.createElement("div");
  rootCard.className = "tree-card tree-node-root";

  const totalEpochs = stack.length || (pairwise.length + 1);
  const minDate = timeline?.min_date || pairwise[0]?.date_before || "--";
  const maxDate = timeline?.max_date || pairwise[pairwise.length - 1]?.date_after || "--";

  rootCard.innerHTML = `
    <div class="tree-node-header">
      <div style="display:flex; align-items:center; gap:10px;">
        <span class="tree-node-badge root">👑 ROOT NODE &bull; T TEMPORAL IMAGES</span>
        <span style="font-size:13px; font-weight:800; color:#f8fafc; font-family:'JetBrains Mono',monospace;">
          ${totalEpochs} Observation Epochs
        </span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="tag-badge" style="font-size:9px; color:#38bdf8; border-color:rgba(6,182,212,0.4);">
          Baseline Horizon: ${minDate} &rarr; ${maxDate}
        </span>
      </div>
    </div>
    <div style="font-size:10px; color:#94a3b8;">
      Top-level chronological image stack. Every subsequent pair and binary change mask branches from these ingested observation epochs:
    </div>
    <div class="tree-root-snapshots-row" id="tree-root-snapshots-row">
      <!-- Populated with T snapshots below -->
    </div>
    <div style="margin-top: 10px; background: rgba(5, 9, 18, 0.7); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 8px; padding: 10px; width: 100%;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;">
        <span style="font-size:10px; font-weight:800; color:#38bdf8; text-transform:uppercase; letter-spacing:0.04em;">
          📈 Ingested Multi-Epoch NDVI Trajectory (${totalEpochs} Observations)
        </span>
        <span style="font-size:9px; color:#94a3b8;">
          Move cursor along curve to inspect NDVI, NDWI & NDBI
        </span>
      </div>
      <div id="tree-root-chart-wrapper" class="all-epochs-chart-wrapper" style="height: 120px;">
        <svg id="tree-root-all-epochs-svg" class="all-epochs-svg" preserveAspectRatio="none" viewBox="0 0 540 120"></svg>
        <div id="tree-root-all-epochs-tooltip" class="all-epochs-tooltip" style="display: none;"></div>
      </div>
    </div>
  `;

  const snapshotsRow = rootCard.querySelector("#tree-root-snapshots-row");
  stack.forEach((snap, sIdx) => {
    const sCard = document.createElement("div");
    sCard.className = "tree-root-snapshot-card";
    const cloudPct = snap.cloud_pct != null ? `${(snap.cloud_pct * 100).toFixed(1)}%` : "0.0%";
    const ndviVal = snap.mean_ndvi != null ? snap.mean_ndvi.toFixed(2) : "--";
    const thumbUrl = snap.thumbnail_url || "";

    sCard.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:10px; font-weight:800; color:#38bdf8;">Epoch T${sIdx + 1}</span>
        <span class="tag-badge" style="font-size:8px; padding:1px 4px;">${snap.date}</span>
      </div>
      <img src="${thumbUrl}" alt="Epoch T${sIdx + 1}" class="tree-root-thumb" title="Click to view full image" />
      <div style="font-size:9px; color:#94a3b8; display:flex; justify-content:space-between;">
        <span>Cloud: <b style="color:#e2e8f0;">${cloudPct}</b></span>
        <span>NDVI: <b style="color:#10b981;">${ndviVal}</b></span>
      </div>
    `;

    const imgEl = sCard.querySelector("img");
    if (imgEl && thumbUrl) {
      imgEl.onclick = () => openLightbox(thumbUrl, `Observation Epoch T${sIdx + 1} (${snap.date})`);
    }

    snapshotsRow.appendChild(sCard);
  });

  viewport.appendChild(rootCard);

  // Render multi-temporal NDVI trajectory on Tree Root Node
  renderAllEpochsNdviPlot(stack, pairwise);

  // Vertical Connecting Stem down to Pair Branches
  const rootStem = document.createElement("div");
  rootStem.className = "tree-connector-stem";
  viewport.appendChild(rootStem);

  // ----------------------------------------------------
  // LEVEL 1: PAIR BRANCHES (Consecutive Input Images)
  // ----------------------------------------------------
  const branchesRow = document.createElement("div");
  branchesRow.className = "tree-branches-row";

  pairwise.forEach((pair, pIdx) => {
    const pairBranch = document.createElement("div");
    pairBranch.className = "tree-pair-branch";
    pairBranch.id = `tree-pair-branch-${pIdx}`;

    // Images URLs
    const snapB = stack.find(s => s.tile_id === pair.tile_before_id || s.date === pair.date_before);
    const snapA = stack.find(s => s.tile_id === pair.tile_after_id || s.date === pair.date_after);
    const rgbB = pair.rgb_before_url || snapB?.thumbnail_url || "";
    const rgbA = pair.rgb_after_url || snapA?.thumbnail_url || "";
    const changePct = pair.change_pct != null ? `${pair.change_pct}%` : "--%";

    // Elapsed days / years
    const dDays = pair.elapsed_days ?? 0;
    const elapsedStr = dDays >= 365 ? `+${(dDays / 365.25).toFixed(1)} yrs` : `+${dDays} days`;

    // Dual Input Images Card
    const pairCard = document.createElement("div");
    pairCard.className = "tree-card tree-pair-images-card";

    pairCard.innerHTML = `
      <div class="tree-node-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <span class="tree-node-badge pair">PAIR ${pIdx + 1} INPUTS</span>
          <span style="font-size:12px; font-weight:700; color:#f8fafc; font-family:'JetBrains Mono',monospace;">
            ${pair.date_before} &rarr; ${pair.date_after}
          </span>
        </div>
        <div style="display:flex; align-items:center; gap:6px;">
          <span class="tag-badge" style="font-size:9px; color:#38bdf8;">${elapsedStr}</span>
          <span class="metric-pill" style="color:#f59e0b; font-size:10px; padding:2px 6px;">${changePct} Changed</span>
        </div>
      </div>
      <div style="font-size:10px; color:#94a3b8;">
        Consecutive observation pair feeding into binary change detector:
      </div>
      <div class="tree-pair-images-grid">
        <!-- Before Image (T_i) -->
        <div class="tree-image-box before">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:10px; font-weight:800; color:#60a5fa;">T${pIdx + 1} (Before)</span>
            <span style="font-size:8px; color:#94a3b8;">${pair.date_before}</span>
          </div>
          <img src="${rgbB}" alt="Before T${pIdx + 1}" class="tree-image-preview" title="Click to enlarge Before RGB" />
          <div style="font-size:8px; color:#94a3b8; display:flex; justify-content:space-between;">
            <span>NDVI: <b style="color:#10b981;">${pair.spectral_profile?.before?.ndvi?.toFixed(2) ?? '--'}</b></span>
            <span>NDBI: <b style="color:#f59e0b;">${pair.spectral_profile?.before?.ndbi?.toFixed(2) ?? '--'}</b></span>
          </div>
        </div>
        <!-- After Image (T_i+1) -->
        <div class="tree-image-box after">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:10px; font-weight:800; color:#34d399;">T${pIdx + 2} (After)</span>
            <span style="font-size:8px; color:#94a3b8;">${pair.date_after}</span>
          </div>
          <img src="${rgbA}" alt="After T${pIdx + 2}" class="tree-image-preview" title="Click to enlarge After RGB" />
          <div style="font-size:8px; color:#94a3b8; display:flex; justify-content:space-between;">
            <span>NDVI: <b style="color:#10b981;">${pair.spectral_profile?.after?.ndvi?.toFixed(2) ?? '--'}</b></span>
            <span>NDBI: <b style="color:#f59e0b;">${pair.spectral_profile?.after?.ndbi?.toFixed(2) ?? '--'}</b></span>
          </div>
        </div>
      </div>
    `;

    // Hook up click to enlarge and live cursor hover
    const imgB = pairCard.querySelector(".tree-image-box.before img");
    if (imgB) {
      attachPlotSpectralHover(imgB, pair, pIdx);
      if (rgbB) {
        imgB.onclick = () => openLightbox(rgbB, `Pair ${pIdx + 1} Before RGB (${pair.date_before})`);
      }
    }
    const imgA = pairCard.querySelector(".tree-image-box.after img");
    if (imgA) {
      attachPlotSpectralHover(imgA, pair, pIdx);
      if (rgbA) {
        imgA.onclick = () => openLightbox(rgbA, `Pair ${pIdx + 1} After RGB (${pair.date_after})`);
      }
    }

    pairBranch.appendChild(pairCard);

    // Stem from Pair Inputs to Binary Change Mask
    const stemToMask = document.createElement("div");
    stemToMask.className = "tree-connector-stem";
    pairBranch.appendChild(stemToMask);

    // ----------------------------------------------------
    // LEVEL 2: BINARY CHANGE MASK NODE (Where Change Occurred)
    // ----------------------------------------------------
    const rawMaskUrl = pair.binary_mask_url || "";
    const filteredMaskUrl = pair.filtered_mask_url || rawMaskUrl;

    const candPx = pair.candidate_pixels ?? (pair.filter_stats?.candidate_changes || 0);
    const fpRejected = pair.false_positives_rejected ?? (pair.filter_stats?.false_positives_rejected || 0);
    const verifiedPx = pair.changed_pixels ?? (pair.filter_stats?.verified_changes || 0);
    const rejRate = candPx > 0 ? ((fpRejected / candPx) * 100).toFixed(1) : "0.0";

    const maskCard = document.createElement("div");
    maskCard.className = "tree-card tree-mask-card";
    maskCard.id = `tree-mask-card-${pIdx}`;

    maskCard.innerHTML = `
      <div class="tree-node-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <span class="tree-node-badge mask">🎯 BINARY CHANGE MASK</span>
          <span style="font-size:11px; font-weight:700; color:#fbbf24; font-family:'JetBrains Mono',monospace;">
            ${verifiedPx.toLocaleString()} Verified Px
          </span>
        </div>
        <div class="tree-mask-mode-pills">
          <button class="tree-mask-pill-btn active" id="btn-mask-verified-${pIdx}">Verified Mask</button>
          <button class="tree-mask-pill-btn" id="btn-mask-raw-${pIdx}">Raw ChangeFormer</button>
        </div>
      </div>
      <div style="font-size:10px; color:#94a3b8;">
        Binary spatial mask generated by MTKD-ChangeFormer and semantically filtered:
      </div>
      <div class="tree-mask-display-wrap">
        <img src="${filteredMaskUrl || rawMaskUrl}" alt="Binary Change Mask" class="tree-mask-img" id="tree-mask-img-${pIdx}" title="Click to enlarge mask" />
      </div>
      <div class="tree-mask-stats-row">
        <div class="tree-mask-stat-box">
          <span style="color:#64748b;">Candidate Px:</span>
          <b style="color:#38bdf8;">${candPx.toLocaleString()}</b>
        </div>
        <div class="tree-mask-stat-box">
          <span style="color:#64748b;">Noise Discarded:</span>
          <b style="color:#f43f5e;">-${fpRejected.toLocaleString()} (${rejRate}%)</b>
        </div>
        <div class="tree-mask-stat-box">
          <span style="color:#64748b;">True Changes:</span>
          <b style="color:#10b981;">${verifiedPx.toLocaleString()}</b>
        </div>
      </div>
    `;

    // Hook up mask view switch and spectral hover
    const maskImgEl = maskCard.querySelector(`#tree-mask-img-${pIdx}`);
    if (maskImgEl) {
      attachPlotSpectralHover(maskImgEl, pair, pIdx);
    }
    const btnVer = maskCard.querySelector(`#btn-mask-verified-${pIdx}`);
    const btnRaw = maskCard.querySelector(`#btn-mask-raw-${pIdx}`);

    if (maskImgEl) {
      maskImgEl.onclick = () => {
        const isVer = btnVer?.classList.contains("active");
        const curUrl = isVer ? (filteredMaskUrl || rawMaskUrl) : (rawMaskUrl || filteredMaskUrl);
        openLightbox(curUrl, isVer ? `Verified Change Mask (Pair ${pIdx + 1})` : `Raw ChangeFormer Mask (Pair ${pIdx + 1})`);
      };
    }

    if (btnVer && btnRaw && maskImgEl) {
      btnVer.onclick = () => {
        btnVer.classList.add("active");
        btnRaw.classList.remove("active");
        maskImgEl.src = filteredMaskUrl || rawMaskUrl;
      };
      btnRaw.onclick = () => {
        btnRaw.classList.add("active");
        btnVer.classList.remove("active");
        maskImgEl.src = rawMaskUrl || filteredMaskUrl;
      };
    }

    pairBranch.appendChild(maskCard);

    // ----------------------------------------------------
    // FORK CONNECTOR (Binary Mask -> Twin Spectral Graphs)
    // ----------------------------------------------------
    const forkWrap = document.createElement("div");
    forkWrap.className = "tree-fork-wrap";
    forkWrap.innerHTML = `
      <svg class="tree-fork-svg" viewBox="0 0 400 38" preserveAspectRatio="none">
        <!-- Center trunk -->
        <line x1="200" y1="0" x2="200" y2="12" stroke="rgba(245, 158, 11, 0.8)" stroke-width="2" />
        <!-- Horizontal split bar -->
        <line x1="100" y1="12" x2="300" y2="12" stroke="rgba(6, 182, 212, 0.7)" stroke-width="2" />
        <!-- Left stem down to Before graph -->
        <line x1="100" y1="12" x2="100" y2="38" stroke="#60a5fa" stroke-width="2" />
        <circle cx="100" cy="38" r="3" fill="#60a5fa" />
        <!-- Right stem down to After graph -->
        <line x1="300" y1="12" x2="300" y2="38" stroke="#34d399" stroke-width="2" />
        <circle cx="300" cy="38" r="3" fill="#34d399" />
      </svg>
    `;
    pairBranch.appendChild(forkWrap);

    // ----------------------------------------------------
    // LEVEL 3: DUAL SPECTRAL GRAPHS (Before & After Classified)
    // ----------------------------------------------------
    const bProf = pair.spectral_profile?.before || {};
    const aProf = pair.spectral_profile?.after || {};

    const ndviB = bProf.ndvi ?? 0.25;
    const ndwiB = bProf.ndwi ?? -0.30;
    const ndbiB = bProf.ndbi ?? 0.05;

    const ndviA = aProf.ndvi ?? 0.28;
    const ndwiA = aProf.ndwi ?? -0.32;
    const ndbiA = aProf.ndbi ?? 0.06;

    const pFeatures = pair.change_geojson?.features || [];
    const changeClassification = getChangedRegionClassification(pair.cursor_sample_grid, pFeatures, pair.spectral_profile);

    const classInfoB = getClassColorInfo(changeClassification.topBefore, false);
    const classInfoA = getClassColorInfo(changeClassification.topAfter, true);

    const baseMaskUrl = pair.filtered_mask_url || pair.binary_mask_url || pair.raw_mask_url || "";
    const classImgBefore = pair.classified_before_url || "";
    const classImgAfter = pair.classified_after_url || "";

    const spectralRow = document.createElement("div");
    spectralRow.className = "tree-spectral-twins-row";

    // 3A. Before Plotted Changed Region Card (Colored in the Binary Mask)
    const beforeGraphCard = document.createElement("div");
    beforeGraphCard.className = "tree-spectral-card before";
    beforeGraphCard.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:10px; font-weight:800; color:#60a5fa; text-transform:uppercase;">
          Before (T${pIdx + 1}) Changed Region
        </span>
        <span class="standard-class-pill ${classInfoB.bgClass}">
          <span>${classInfoB.icon}</span> ${classInfoB.name}
        </span>
      </div>
      <div style="font-size:9px; color:#94a3b8;">
        Changed Footprint Surface: <b style="color:${classInfoB.color};">${classInfoB.desc}</b>
      </div>

      <!-- High-Resolution Binary Change Mask Colored for Before Surface -->
      <div class="tree-classified-plot-wrap">
        ${classImgBefore ? 
          `<img src="${classImgBefore}" class="tree-classified-plot-img" id="img-class-before-${pIdx}" title="Click to view full-resolution Before classification map" />` :
          `<canvas class="tree-classified-canvas" id="canvas-class-before-${pIdx}" title="High-resolution Before Classified Change Mask (Dark Green = Dense Veg)"></canvas>`
        }
      </div>

      <!-- Surface Footprint Multi-Class Legend -->
      <div class="tree-classified-legend">
        <span><span class="tree-legend-dot" style="background:#15803d;"></span>Dense Veg</span>
        <span><span class="tree-legend-dot" style="background:#84cc16;"></span>Sparse Veg</span>
        <span><span class="tree-legend-dot" style="background:#92400e;"></span>Bare Land</span>
        <span><span class="tree-legend-dot" style="background:#f59e0b;"></span>Built-up</span>
        <span><span class="tree-legend-dot" style="background:#0284c7;"></span>Water</span>
      </div>

      <!-- Combined NDBI, NDVI, NDWI Mini Meters -->
      <div style="display:flex; flex-direction:column; gap:5px; margin-top:4px;">
        <!-- NDVI -->
        <div class="tree-index-meter">
          <span class="tree-meter-name" style="color:#10b981;">🌿 NDVI</span>
          <div class="tree-meter-track">
            <div class="tree-meter-fill" id="meter-fill-ndvi-b-${pIdx}" style="width:${indexToPercent(ndviB)}; background:#10b981;"></div>
          </div>
          <span class="tree-meter-val" id="meter-val-ndvi-b-${pIdx}" style="color:#6ee7b7;">${fmtVal(ndviB)}</span>
        </div>
        <!-- NDBI -->
        <div class="tree-index-meter">
          <span class="tree-meter-name" style="color:#f59e0b;">🏗️ NDBI</span>
          <div class="tree-meter-track">
            <div class="tree-meter-fill" id="meter-fill-ndbi-b-${pIdx}" style="width:${indexToPercent(ndbiB)}; background:#f59e0b;"></div>
          </div>
          <span class="tree-meter-val" id="meter-val-ndbi-b-${pIdx}" style="color:#fde68a;">${fmtVal(ndbiB)}</span>
        </div>
        <!-- NDWI -->
        <div class="tree-index-meter">
          <span class="tree-meter-name" style="color:#06b6d4;">💧 NDWI</span>
          <div class="tree-meter-track">
            <div class="tree-meter-fill" id="meter-fill-ndwi-b-${pIdx}" style="width:${indexToPercent(ndwiB)}; background:#06b6d4;"></div>
          </div>
          <span class="tree-meter-val" id="meter-val-ndwi-b-${pIdx}" style="color:#67e8f9;">${fmtVal(ndwiB)}</span>
        </div>
      </div>
    `;

    // 3B. After Plotted Changed Region Card (Colored in the Binary Mask)
    const afterGraphCard = document.createElement("div");
    afterGraphCard.className = "tree-spectral-card after";
    afterGraphCard.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:10px; font-weight:800; color:#34d399; text-transform:uppercase;">
          After (T${pIdx + 2}) Changed Region
        </span>
        <span class="standard-class-pill ${classInfoA.bgClass}">
          <span>${classInfoA.icon}</span> ${classInfoA.name}
        </span>
      </div>
      <div style="font-size:9px; color:#94a3b8;">
        Changed Footprint Surface: <b style="color:${classInfoA.color};">${classInfoA.desc}</b>
      </div>

      <!-- High-Resolution Binary Change Mask Colored for After Surface -->
      <div class="tree-classified-plot-wrap">
        ${classImgAfter ? 
          `<img src="${classImgAfter}" class="tree-classified-plot-img" id="img-class-after-${pIdx}" title="Click to view full-resolution After classification map" />` :
          `<canvas class="tree-classified-canvas" id="canvas-class-after-${pIdx}" title="High-resolution After Classified Change Mask"></canvas>`
        }
      </div>

      <!-- Surface Footprint Multi-Class Legend -->
      <div class="tree-classified-legend">
        <span><span class="tree-legend-dot" style="background:#15803d;"></span>Dense Veg</span>
        <span><span class="tree-legend-dot" style="background:#84cc16;"></span>Sparse Veg</span>
        <span><span class="tree-legend-dot" style="background:#92400e;"></span>Bare Land</span>
        <span><span class="tree-legend-dot" style="background:#f59e0b;"></span>Built-up</span>
        <span><span class="tree-legend-dot" style="background:#0284c7;"></span>Water</span>
      </div>

      <!-- Combined NDBI, NDVI, NDWI Mini Meters -->
      <div style="display:flex; flex-direction:column; gap:5px; margin-top:4px;">
        <!-- NDVI -->
        <div class="tree-index-meter">
          <span class="tree-meter-name" style="color:#10b981;">🌿 NDVI</span>
          <div class="tree-meter-track">
            <div class="tree-meter-fill" id="meter-fill-ndvi-a-${pIdx}" style="width:${indexToPercent(ndviA)}; background:#10b981;"></div>
          </div>
          <span class="tree-meter-val" id="meter-val-ndvi-a-${pIdx}" style="color:#6ee7b7;">${fmtVal(ndviA)}</span>
        </div>
        <!-- NDBI -->
        <div class="tree-index-meter">
          <span class="tree-meter-name" style="color:#f59e0b;">🏗️ NDBI</span>
          <div class="tree-meter-track">
            <div class="tree-meter-fill" id="meter-fill-ndbi-a-${pIdx}" style="width:${indexToPercent(ndbiA)}; background:#f59e0b;"></div>
          </div>
          <span class="tree-meter-val" id="meter-val-ndbi-a-${pIdx}" style="color:#fde68a;">${fmtVal(ndbiA)}</span>
        </div>
        <!-- NDWI -->
        <div class="tree-index-meter">
          <span class="tree-meter-name" style="color:#06b6d4;">💧 NDWI</span>
          <div class="tree-meter-track">
            <div class="tree-meter-fill" id="meter-fill-ndwi-a-${pIdx}" style="width:${indexToPercent(ndwiA)}; background:#06b6d4;"></div>
          </div>
          <span class="tree-meter-val" id="meter-val-ndwi-a-${pIdx}" style="color:#67e8f9;">${fmtVal(ndwiA)}</span>
        </div>
      </div>
    `;

    // Render Canvas with High-Resolution Binary Mask or attach Lightbox click & spectral hover
    const canvasB = beforeGraphCard.querySelector(`#canvas-class-before-${pIdx}`);
    if (canvasB) {
      renderPerPixelClassifiedCanvas(canvasB, classImgBefore || baseMaskUrl, pair.cursor_sample_grid, false);
      attachPlotSpectralHover(canvasB, pair, pIdx);
      canvasB.onclick = () => {
        try {
          openLightbox(canvasB.toDataURL("image/png"), `Before (T${pIdx + 1}) Changed Footprint — Per-Pixel Classified Surface`);
        } catch (e) {
          if (baseMaskUrl) openLightbox(baseMaskUrl, `Before Changed Footprint`);
        }
      };
    }

    const canvasA = afterGraphCard.querySelector(`#canvas-class-after-${pIdx}`);
    if (canvasA) {
      renderPerPixelClassifiedCanvas(canvasA, classImgAfter || baseMaskUrl, pair.cursor_sample_grid, true);
      attachPlotSpectralHover(canvasA, pair, pIdx);
      canvasA.onclick = () => {
        try {
          openLightbox(canvasA.toDataURL("image/png"), `After (T${pIdx + 2}) Changed Footprint — Per-Pixel Classified Surface`);
        } catch (e) {
          if (baseMaskUrl) openLightbox(baseMaskUrl, `After Changed Footprint`);
        }
      };
    }

    const classPlotImgB = beforeGraphCard.querySelector(`#img-class-before-${pIdx}`);
    if (classPlotImgB) {
      attachPlotSpectralHover(classPlotImgB, pair, pIdx);
      if (classImgBefore) {
        classPlotImgB.onclick = () => openLightbox(classImgBefore, `Before (T${pIdx + 1}) Changed Footprint — Per-Pixel Classified Surface`);
      }
    }

    const classPlotImgA = afterGraphCard.querySelector(`#img-class-after-${pIdx}`);
    if (classPlotImgA) {
      attachPlotSpectralHover(classPlotImgA, pair, pIdx);
      if (classImgAfter) {
        classPlotImgA.onclick = () => openLightbox(classImgAfter, `After (T${pIdx + 2}) Changed Footprint — Per-Pixel Classified Surface`);
      }
    }

    spectralRow.appendChild(beforeGraphCard);
    spectralRow.appendChild(afterGraphCard);
    pairBranch.appendChild(spectralRow);

    // ----------------------------------------------------
    // LEVEL 4: WHAT CHANGED (Convergence of Before & After)
    // ----------------------------------------------------
    const convergeWrap = document.createElement("div");
    convergeWrap.className = "tree-fork-wrap";
    convergeWrap.innerHTML = `
      <svg class="tree-fork-svg" viewBox="0 0 400 38" preserveAspectRatio="none">
        <!-- Left line from Before graph to center -->
        <line x1="100" y1="0" x2="100" y2="24" stroke="#60a5fa" stroke-width="2" />
        <circle cx="100" cy="0" r="3" fill="#60a5fa" />
        <!-- Right line from After graph to center -->
        <line x1="300" y1="0" x2="300" y2="24" stroke="#34d399" stroke-width="2" />
        <circle cx="300" cy="0" r="3" fill="#34d399" />
        <!-- Horizontal merge bar -->
        <line x1="100" y1="24" x2="300" y2="24" stroke="rgba(245, 158, 11, 0.7)" stroke-width="2" />
        <!-- Center stem leading to What Changed box -->
        <line x1="200" y1="24" x2="200" y2="38" stroke="#f59e0b" stroke-width="2" />
        <circle cx="200" cy="38" r="3" fill="#f59e0b" />
      </svg>
    `;
    pairBranch.appendChild(convergeWrap);

    // Calculate pair change metrics
    let pairAreaM2 = 0;
    pFeatures.forEach(f => pairAreaM2 += (f.properties?.area_sq_m || f.properties?.area_m2 || 0));
    const pairAreaStr = pairAreaM2 >= 10000 ? `${(pairAreaM2 / 10000).toFixed(2)} ha` : `${Math.round(pairAreaM2).toLocaleString()} m²`;

    // Determine dominant change category for this pair
    const pBreakdown = {};
    pFeatures.forEach(f => {
      const c = f.properties?.change_type || "Unclassified";
      pBreakdown[c] = (pBreakdown[c] || 0) + 1;
    });
    const dominantPairChange = Object.entries(pBreakdown).sort((a, b) => b[1] - a[1])[0]?.[0] || "Surface Transition";
    const changeColor = getChangeColor(dominantPairChange);

    const dNdvi = (ndviA - ndviB).toFixed(2);
    const dNdbi = (ndbiA - ndbiB).toFixed(2);

    // What Changed Card
    const whatChangedCard = document.createElement("div");
    whatChangedCard.className = "tree-card tree-transition-card";
    whatChangedCard.style.borderLeftColor = changeColor.fill || "#f59e0b";

    whatChangedCard.innerHTML = `
      <div class="tree-node-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <span class="tree-node-badge transition">⚡ WHAT CHANGED</span>
          <span style="font-size:12px; font-weight:800; color:#f8fafc; font-family:'JetBrains Mono',monospace;">
            ${dominantPairChange}
          </span>
        </div>
        <button class="hud-btn btn-focus-tree-pair" style="padding:3px 8px; font-size:10px; background:rgba(6,182,212,0.15); border:1px solid rgba(6,182,212,0.4); color:#38bdf8;" title="Focus this pair on the Leaflet map">
          🔍 Focus on Map
        </button>
      </div>

      <!-- Transition Flow Banner -->
      <div class="tree-transition-banner">
        <div class="tree-transition-flow">
          <span class="standard-class-pill ${classInfoB.cssClass}" style="font-size:9px; padding:2px 6px;">
            ${classInfoB.icon} ${classInfoB.name}
          </span>
          <span style="color:#f59e0b; font-size:14px;">&rarr;</span>
          <span class="standard-class-pill ${classInfoA.cssClass}" style="font-size:9px; padding:2px 6px;">
            ${classInfoA.icon} ${classInfoA.name}
          </span>
        </div>
        <span class="tag-badge" style="font-size:9px; color:#10b981; border-color:rgba(16,185,129,0.4);">
          ${verifiedPx.toLocaleString()} Changed Px
        </span>
      </div>

      <!-- Synthesis Stats Grid -->
      <div class="tree-transition-stats-grid">
        <div class="tree-stat-pill-card">
          <span style="color:#64748b; font-size:8px; text-transform:uppercase;">Affected Footprint</span>
          <b style="color:#f8fafc; font-size:11px; font-family:'JetBrains Mono',monospace;">${pairAreaStr}</b>
        </div>
        <div class="tree-stat-pill-card">
          <span style="color:#64748b; font-size:8px; text-transform:uppercase;">Detected Polygons</span>
          <b style="color:#38bdf8; font-size:11px; font-family:'JetBrains Mono',monospace;">${pFeatures.length} Regions</b>
        </div>
        <div class="tree-stat-pill-card">
          <span style="color:#64748b; font-size:8px; text-transform:uppercase;">Spectral Delta</span>
          <b style="color:${parseFloat(dNdvi) < 0 ? '#f43f5e' : '#10b981'}; font-size:11px; font-family:'JetBrains Mono',monospace;">
            ΔNDVI ${parseFloat(dNdvi) >= 0 ? '+' : ''}${dNdvi}
          </b>
        </div>
      </div>
    `;

    // Map focus button handler
    const btnFocus = whatChangedCard.querySelector(".btn-focus-tree-pair");
    if (btnFocus) {
      btnFocus.onclick = (e) => {
        e.stopPropagation();
        switchChangeViewMode(pIdx.toString());
      };
    }

    pairBranch.appendChild(whatChangedCard);

    // Stem leading to Grand Convergence
    const stemToGrand = document.createElement("div");
    stemToGrand.className = "tree-connector-stem";
    pairBranch.appendChild(stemToGrand);

    branchesRow.appendChild(pairBranch);
  });

  viewport.appendChild(branchesRow);

  // ----------------------------------------------------
  // LEVEL 5: GRAND CONVERGENCE NODE (Overall Change in Y Years)
  // ----------------------------------------------------
  const overall = analysisData?.overall || {};
  const geojson = overall.change_geojson || {};
  const allFeatures = geojson.features || [];
  const breakdown = overall.change_type_breakdown || {};

  let elapsedYears = 1.0;
  if (minDate && maxDate && minDate !== "--") {
    const dSpanMs = new Date(maxDate) - new Date(minDate);
    if (!isNaN(dSpanMs) && dSpanMs > 0) {
      elapsedYears = Math.max(0.5, (dSpanMs / (1000 * 60 * 60 * 24 * 365.25)));
    }
  }

  let totalAreaM2 = 0;
  allFeatures.forEach(f => {
    totalAreaM2 += (f.properties?.area_sq_m || f.properties?.area_m2 || 0);
  });
  const totalAreaStr = totalAreaM2 >= 10000 ? `${(totalAreaM2 / 10000).toFixed(2)} ha` : `${Math.round(totalAreaM2).toLocaleString()} m²`;

  const topTypeEntry = Object.entries(breakdown)
    .filter(([k]) => k !== "No Change")
    .sort((a, b) => b[1] - a[1])[0];
  const dominantOverall = topTypeEntry ? `${topTypeEntry[0]} (${topTypeEntry[1]}%)` : "Multi-Spectral Transformation";

  // Grand Convergence Stem / Funnel Wrap
  const grandTrunkWrap = document.createElement("div");
  grandTrunkWrap.className = "tree-grand-trunk-wrap";
  grandTrunkWrap.innerHTML = `
    <div style="width:2px; height:24px; background:linear-gradient(180deg, rgba(245,158,11,0.8), rgba(6,182,212,0.9)); margin: 0 auto;"></div>
  `;
  viewport.appendChild(grandTrunkWrap);

  const grandConvergenceCard = document.createElement("div");
  grandConvergenceCard.className = "tree-card tree-node-convergence";

  grandConvergenceCard.innerHTML = `
    <div class="tree-node-header">
      <div style="display:flex; align-items:center; gap:10px;">
        <span class="tree-node-badge overall">🌐 CUMULATIVE CONVERGENCE</span>
        <span style="font-size:14px; font-weight:800; color:#f8fafc; font-family:'JetBrains Mono',monospace;">
          OVERALL CHANGE IN ${elapsedYears.toFixed(1)} YEARS
        </span>
      </div>
      <button class="hud-btn primary" id="btn-tree-focus-overall" style="padding:5px 12px; font-size:11px;">
        <span>🗺️</span> View Total Cumulative Map
      </button>
    </div>

    <div style="font-size:11px; color:#94a3b8; line-height:1.4;">
      Unified convergence of all pairwise intervals. Synthesizes net land-cover transformation, cumulative area loss/gain, and trajectory evolution across the entire <b>${elapsedYears.toFixed(1)}-year observation horizon (${minDate} &rarr; ${maxDate})</b>:
    </div>

    <!-- Overall Key Metric Pillars -->
    <div class="tree-convergence-stats">
      <div class="tree-conv-stat-box">
        <span style="color:#64748b; font-size:9px; text-transform:uppercase;">Total Net Footprint</span>
        <div class="tree-conv-stat-num" style="color:#f8fafc;">${totalAreaStr}</div>
      </div>
      <div class="tree-conv-stat-box">
        <span style="color:#64748b; font-size:9px; text-transform:uppercase;">Change Polygons</span>
        <div class="tree-conv-stat-num" style="color:#38bdf8;">${allFeatures.length}</div>
      </div>
      <div class="tree-conv-stat-box">
        <span style="color:#64748b; font-size:9px; text-transform:uppercase;">Observation Horizon</span>
        <div class="tree-conv-stat-num" style="color:#fbbf24;">${elapsedYears.toFixed(1)} Yrs</div>
      </div>
      <div class="tree-conv-stat-box">
        <span style="color:#64748b; font-size:9px; text-transform:uppercase;">Dominant Driver</span>
        <div class="tree-conv-stat-num" style="color:#34d399; font-size:13px;">${dominantOverall}</div>
      </div>
    </div>

    <!-- Trajectory Chain Flow -->
    <div style="display:flex; flex-direction:column; gap:6px; margin-top:2px;">
      <span style="font-size:10px; font-weight:700; color:#cbd5e1; text-transform:uppercase; letter-spacing:0.04em;">
        Multi-Temporal Trajectory Sequence (${pairwise.length} Intervals)
      </span>
      <div class="tree-trajectory-flow" id="tree-trajectory-flow">
        <!-- Populated below -->
      </div>
    </div>
  `;

  // Build Trajectory Step Chain
  const trajContainer = grandConvergenceCard.querySelector("#tree-trajectory-flow");
  if (trajContainer) {
    pairwise.forEach((p, idx) => {
      const stepEl = document.createElement("div");
      stepEl.className = "tree-trajectory-step";
      stepEl.innerHTML = `
        <span style="color:#38bdf8; font-weight:700;">T${idx + 1} (${p.date_before})</span>
        <span style="color:#f59e0b;">&rarr;</span>
        <span style="color:#34d399; font-weight:700;">T${idx + 2} (${p.date_after})</span>
      `;
      trajContainer.appendChild(stepEl);
      if (idx < pairwise.length - 1) {
        const arrow = document.createElement("span");
        arrow.style.color = "#64748b";
        arrow.textContent = "➔";
        trajContainer.appendChild(arrow);
      }
    });
  }

  // Connect Total Cumulative Map button
  const btnTotalMap = grandConvergenceCard.querySelector("#btn-tree-focus-overall");
  if (btnTotalMap) {
    btnTotalMap.onclick = () => {
      switchChangeViewMode("overall");
    };
  }

  viewport.appendChild(grandConvergenceCard);
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
    if (ndvi >= 0.2 && ndvi <= 0.6 && ndbi < 0.0) return "Moderate / Sparse Vegetation";
    if (ndbi > 0.0 && ndvi <= 0.2) return "Built-up / Urban";
    if (ndvi <= 0.1 && ndbi <= 0.0 && ndwi <= 0.0) return "Bare Soil / Barren";
    return "Unclassified / Transitional";
  }

  function getTransition(cB, cA) {
    if (cB === cA) return "Unchanged Surface";
    if (cB.includes("Vegetation") && cA.includes("Vegetation")) return "Seasonal Phenology (Unchanged)";
    if ((cB.includes("Vegetation") || cB.includes("Bare Soil")) && cA.includes("Built-up")) {
      return "🏗️ Construction";
    }
    if (cB.includes("Vegetation") && cA.includes("Bare Soil")) {
      return "🪓 Clearance";
    }
    if (cB.includes("Water") && (cA.includes("Bare Soil") || cA.includes("Vegetation"))) {
      return "💧 Water Shrinkage";
    }
    if ((cB.includes("Bare Soil") || cB.includes("Vegetation")) && cA.includes("Water")) {
      return "💧 Water Expansion";
    }
    if (cB.includes("Built-up") && (cA.includes("Vegetation") || cA.includes("Bare Soil"))) {
      return "🏚️ Demolition / Reversion";
    }
    if (cB.includes("Unclassified") && cA.includes("Unclassified")) {
      return "Unchanged Surface";
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
      let classB = "Unclassified / Transitional";
      let classA = "Unclassified / Transitional";
      let isVerified = false;
      let serverChangeType = null;

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

        classB = sampleGrid.before.class?.[r]?.[c] || classify(ndviB, ndwiB, ndbiB);
        classA = sampleGrid.after.class?.[r]?.[c] || classify(ndviA, ndwiA, ndbiA);
        isVerified = !!(sampleGrid.verified?.[r]?.[c]);
        serverChangeType = sampleGrid.change_type?.[r]?.[c];
      } else {
        // Fallback to pair mean
        ndviB = pair.spectral_profile?.before?.ndvi ?? 0.25;
        ndwiB = pair.spectral_profile?.before?.ndwi ?? -0.3;
        ndbiB = pair.spectral_profile?.before?.ndbi ?? 0.05;
        ndviA = pair.spectral_profile?.after?.ndvi ?? 0.28;
        ndwiA = pair.spectral_profile?.after?.ndwi ?? -0.32;
        ndbiA = pair.spectral_profile?.after?.ndbi ?? 0.06;
        classB = classify(ndviB, ndwiB, ndbiB);
        classA = classify(ndviA, ndwiA, ndbiA);
      }

      let trans = (serverChangeType && serverChangeType !== "No Change") ? serverChangeType : getTransition(classB, classA);
      const isUnchanged = (classB === classA) || trans === "No Change" || trans.includes("Unchanged") || trans.includes("Seasonal Phenology");

      let displayTrans = trans;
      let transColor = "#fbbf24";
      if (isUnchanged || !isVerified) {
        displayTrans = "🚫 Unchanged Surface (Excluded from Verified Mask)";
        transColor = "#94a3b8";
      } else {
        displayTrans = "⚡ Verified: " + trans;
        transColor = "#10b981";
      }

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
      if (transEl) {
        transEl.textContent = displayTrans;
        transEl.style.color = transColor;
      }

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
        if (tipTrans) {
          tipTrans.textContent = displayTrans;
          tipTrans.style.color = transColor;
        }
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

