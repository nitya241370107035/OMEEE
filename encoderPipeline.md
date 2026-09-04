# Implementation Plan — Phase 2: Semantic & Multimodal Retrieval (PS 2.2.1)
### (Updated: NDBI/SWIR band confirmed and included — no longer a pending decision)

**Scope:** the actual query-time retrieval tool — text or image in, ranked results with full metadata and an auto-generated spot description out — plus the chat-style UI on top of it. Consumes exactly what Phase 1 produced (embedded, quality-scored tiles in Qdrant + Postgres); no ingestion logic lives here.

**What changed from the previous version of this plan:** NDBI is now a confirmed, always-computed index — the team has added the 5th SWIR band. This removes the "decision pending / graceful degradation" framing from the earlier draft. `mean_ndbi` is now expected to be populated on every tile, the same way `mean_ndvi`/`mean_ndwi` already are.

**One thing this does affect, worth a quick check before moving on:** since this changes the band count from 4 to 5, confirm Phase 1's `band_order`/`band_stats` JSONB fields and the `.tif` files themselves were actually re-ingested (or ingested fresh) with the SWIR band included — if any tiles were embedded before this change, they'll have `mean_ndbi = null` and should either be re-processed or clearly excluded from anything that assumes NDBI is always present.

---

## Phase 2.0 — Filters remain the one thing to confirm before building

The NDBI dependency from before is resolved. The other open item stands: the PS explicitly says results "may be refined using area-of-interest, date-range, sensor or other metadata filters" — confirm the team is building at least backend support for this now (Phase 2.2 below), even if full UI filter controls come in a later pass. Cheap to include now given Phase 1's payload indexes already exist; expensive to retrofit later.

---

## Phase 2.1 — Query encoding (reuse, don't rebuild)

**Goal:** turn the analyst's text or uploaded image into a vector, using the exact same encoder instance from Phase 1 — never a second copy of the model.

**Tasks:**
1. Reuse Phase 1's loaded RemoteCLIP encoder object directly — import it, don't reinitialize. Loading it twice wastes memory and risks a version mismatch between ingestion-time and query-time embeddings, which would silently degrade search quality.
2. Two entry functions: `encode_query_text(text)` and `encode_query_image(image)` — both return the same shape vector (512-dim, matching what's stored in Qdrant).

**Output:** one query vector, ready for search.

---

## Phase 2.2 — Vector search with optional filters

**Goal:** find the closest matches in Qdrant, applying AOI/date/sensor filters when the analyst provides them.

**Tasks:**
1. Accept: the query vector, `top_k` (default 5, but a parameter — never hardcode 5 inside the search call itself, so a future "show more" action or filter refinement doesn't need a code change).
2. Accept optional filters: `aoi_polygon`, `date_from`/`date_to`, `sensor`, `min_quality`.
3. **AOI filtering:** Qdrant's payload only holds `centroid_lat/lon`, not full tile geometry — it can't do a true polygon-intersection filter itself. Handle this by pre-resolving the AOI to a list of `tile_id`s via Postgres (`ST_Intersects`) first, then passing those IDs into the Qdrant filter alongside the vector search.
4. Date-range, sensor, and quality filters map directly onto the payload indexes already built in Phase 1.
5. Run the filtered kNN search, get back `top_k` `tile_id`s with similarity scores — this ordering already satisfies the "rank ordered" requirement, since Qdrant returns results sorted by similarity by construction.

**Output:** a ranked list of `(tile_id, score)` pairs.

---

## Phase 2.3 — Result enrichment (join back to Postgres)

**Goal:** for each returned `tile_id`, pull everything the PS and your UI need to display.

**Tasks:**
1. Batch-fetch all `top_k` tiles from Postgres in one query (`WHERE tile_id = ANY(...)`) — not one query per result.
2. Pull every field the result card and inspect view need: `geometry`, `centroid_lat/lon`, `acquisition_date`, `sensor`, `quality_confidence`, `cloud_pct`, `scene_id`, `mean_ndvi`, `mean_ndwi`, `mean_ndbi`, `thumbnail_path`, `file_path`.
3. Preserve the Qdrant-provided ranking order when merging in the Postgres data — don't accidentally re-sort by tile_id or insertion order.

**Output:** ranked list of fully-populated tile records, all three indices present.

---

## Phase 2.4 — Per-result description generation (NDVI/NDWI/NDBI → plain-language summary)

**Goal:** a short, auto-generated description of the spot's character, using all three index values computed at ingestion. Rule-based, not another model.

**Tasks:**
1. Threshold-based classification per index:
   - **NDVI:** `> 0.3` → "dense vegetation", `0.1–0.3` → "sparse vegetation", `< 0.1` → "minimal vegetation".
   - **NDWI:** `> 0.3` → "significant water presence", `0–0.3` → "some moisture/water", `< 0` → "no significant water".
   - **NDBI:** `> 0` → "built-up areas likely dominant", `< 0` → "non-built-up (vegetation/water) likely dominant".
2. Combine all three into one sentence, e.g.: *"This location shows sparse vegetation (NDVI 0.18), no significant water (NDWI -0.05), and built-up areas likely dominant (NDBI 0.22)"* — all three clauses now included by default, since all three values are expected to exist.
3. **Keep one defensive null-check in the function regardless** — if a specific tile happens to have `mean_ndbi = null` (e.g. an older tile from before the SWIR band was added, per the note at the top of this plan), omit that clause gracefully rather than erroring or printing "None". Cheap insurance, costs nothing, avoids a crash on old data during the transition.
4. Keep this function pure and standalone — `(mean_ndvi, mean_ndwi, mean_ndbi)` in, description string out — easy to unit-test with a handful of known value combinations before wiring it into the result pipeline.

**Output:** a short, three-index description string, attached to each result.

---

## Phase 2.5 — Finalize the `SemanticSearchTool` contract (agent-ready, as designed from the start)

**Tasks:**
1. Combine Phases 2.1-2.4 into one callable class/function — this is what the agent calls directly later, unchanged.
2. Input: `query_text` OR `query_image`, `filters` (AOI/date/sensor/min_quality), `top_k`.
3. Output: ranked list of result objects — score, full tile metadata, generated description.
4. **Log every search** to the `search_log` table — raw query, query type, filters used, resulting `tile_id`s. This is what makes future feedback-driven reranking (2.2.5) possible; skipping this now loses that history permanently for every query made before it's added.

**Output:** one tested, standalone tool function.

---

## Phase 2.6 — API route

**Tasks:**
1. One endpoint accepting text or uploaded image, plus optional filter parameters — thin wrapper around Phase 2.5's tool.
2. Return the ranked result list as JSON, including each result's description and full metadata.

---

## Phase 2.7 — Chat-style UI

**Tasks:**
1. **Input bar:** text field + image-attach button, single submit either way.
2. **Result cards, rendered inline in the thread:** thumbnail, score, date, sensor, plus the generated description shown directly on the card.
3. **Inspect view** (click a card → modal or side panel): every metadata field — location, acquisition date, sensor, quality_confidence, cloud_pct, NDVI/NDWI/NDBI, scene_id, full description text.
4. **"Open this tile in map" button**, inside the inspect view (and optionally on the card too): passes the tile's `geometry`/`centroid` to the map component, which pans/zooms and draws the tile's footprint outline.
5. Loading state while a search runs, empty state for zero results.

**Output:** the actual demoable feature for 2.2.1 — chat-style search, real ranked results, real metadata, real three-index spot descriptions, and a working map hand-off.

---

## Phase 2.8 — Validation pass

- Run the PS's own example queries ("newly built structures near a river," "large vehicle concentrations on open ground") — check that the description on river-adjacent results (high NDWI) and open-ground results (low NDVI, low NDBI) actually line up with what the query asked for.
- Test at least one filtered query (date range) end-to-end, confirming the AOI pre-filter approach in Phase 2.2 narrows results correctly.
- Confirm `search_log` is actually being written on every query — check the table directly.
- Confirm the "open in map" action correctly pans to the right tile across a few different results, not just the first one in a list.
- **Specifically check a handful of tiles for `mean_ndbi = null`** (pre-SWIR-band tiles, if any exist in the archive) and confirm the description generator's defensive null-check actually handles them without breaking the demo.

---

## What's still explicitly deferred, for later phases

- Confirm/reject buttons on results (2.2.5's review queue — though the inspect view built here is a natural place to add them later without redesigning anything).
- Full reranking using `search_log` feedback history (2.2.5) — this phase only logs the data.
- Discovery/clustering "find similar" action (2.2.4) — separate tool, reuses this phase's tile-fetching/enrichment logic but with a different starting point.