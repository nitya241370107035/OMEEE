# Ingestion Pipeline Bugfix Plan
### Duplicate tiles, missing tiles, non-uniform grid, and the s2cloudless fallback issue

**Context:** this plan addresses four concrete issues found in code review of the completed ingestion pipeline (`canvas.py`, `input_validator.py`, `stac_search.py`, `tiler.py`, `storage.py`, the normalization/masking modules). Three are confirmed from the code directly; one (multi-scene handling) needs the orchestrator file to confirm fully — flagged accordingly below.

---

## Issue 1 — Duplicate tiles when AOI spans overlapping granules

**Root cause:** `search_multi_temporal_stac_scenes` in `stac_search.py` can return more than one scene per time bucket (its own docstring claims otherwise — that docstring is wrong and should be corrected too). When an AOI crosses the boundary between two adjacent Sentinel-2 granules, both get selected. If each scene is then tiled independently, ground in the overlap zone gets tiled twice.

**Fix, in order of preference:**

1. **Mosaic before tiling, not after.** Instead of calling `assemble_working_canvas` once per scene and tiling each separately, merge all scenes selected for a single bucket into **one combined canvas** first (e.g. `rasterio.merge.merge()`, or a manual max-of-non-nodata-pixel merge across the scene set), then run masking/normalization/tiling **once** on that merged canvas. This is the structurally correct fix — it also directly resolves Issue 2 below, since a mosaicked canvas has real data everywhere the polygon has any scene covering it, not just wherever one single scene happens to reach.
2. **If mosaicking is too much to build right now:** as a stopgap, deduplicate by `site_key` after tiling — when two tiles from different scenes in the same bucket produce the same (or very close) `site_key`, keep only the one with the better `quality_confidence` and discard the other before saving. Cheaper to build, but wastes the compute already spent tiling both, and doesn't fix Issue 2.
3. **Either way:** fix the misleading docstring on `search_multi_temporal_stac_scenes` so it accurately says "one or more scenes per bucket," not "one scene per bucket" — this matters because whoever wired the orchestrator likely wrote it assuming the docstring was correct.

---

## Issue 2 — Missing tiles inside the drawn polygon

**Root cause (most likely, pending orchestrator confirmation):** the nodata-discard check in `tiler.py`:
```python
if np.all(tile_multiband == 0) or float(np.mean(tile_multiband)) < 1e-3:
    continue
```
silently drops any tile window that reads back as black/zero — which happens whenever a tile falls inside your polygon but outside the *specific scene's* real coverage. If scenes aren't mosaicked (Issue 1), this is expected to happen constantly near granule boundaries.

**Fix:**
1. **Primary fix is the same as Issue 1's mosaicking fix** — once scenes are merged into one canvas per bucket before tiling, there's only "real data" vs "genuinely outside any available scene" to worry about, not "outside this one particular scene."
2. **Add logging to the discard check**, regardless: `logger.debug(f"Discarding tile at ({x},{y}): nodata/empty")` — right now this failure is completely silent, which is exactly why it looked like tiles were just vanishing with no explanation. Cheap to add, immediately useful for debugging.
3. **Accept as expected behavior, not a bug:** tiles right at the very edge of an irregular polygon that only partially overlap real image data (e.g. a polygon vertex sitting near the true edge of your imagery) will legitimately have no full tile to offer — this is a physical limitation, not something to "fix" away.

---

## Issue 3 — Replace the grid-step algorithm with a real fixed-stride sliding window

**Root cause:** `_get_grid_steps` uses `np.linspace` to spread a computed tile count evenly across the canvas, rather than stepping by your actual configured stride. This is why tiles don't form a clean, predictable adjacent grid.

**Fix — replace with a standard sliding-window step generator:**

```python
def _get_grid_steps(total_dim: int, window_size: int, stride: int) -> List[int]:
    """
    Standard fixed-stride sliding window. Every step (except possibly the
    last) is exactly `stride` pixels apart — a real, predictable grid,
    not a redistributed approximation.
    """
    if total_dim <= window_size:
        return [0]
    offsets = list(range(0, total_dim - window_size + 1, stride))
    # Ensure the far edge of the canvas is always covered, even if the
    # last regular step doesn't land exactly on it.
    if offsets[-1] != total_dim - window_size:
        offsets.append(total_dim - window_size)
    return offsets
```

**One thing to decide as a team before this fix goes in:** do you actually want the 15% overlap between tiles (useful for change-detection robustness against boundary artifacts), or a clean non-overlapping grid (simpler, avoids near-duplicate embeddings for the same ground twice)? Both are reasonable — the fix above still respects `overlap_pct`/`stride` either way, this is purely about making the *stepping itself* deterministic and literal, not about removing overlap. If you want zero overlap, just set `overlap_pct = 0.0` and this function will produce a genuinely edge-to-edge adjacent grid with no gaps and no overlap.

---

## Issue 4 — Correct the s2cloudless labeling, and decide whether to actually enable it

**Root cause:** your current 5-band ingestion (`blue, green, red, nir, swir`) can never satisfy `has_all_10_bands`, so `run_s2cloudless_detector` always silently uses `_spectral_fallback_detector` instead of the real trained model.

**Decision point for the team, not a code bug per se:**
- **Option A — keep the 5-band pipeline, accept the fallback detector as your actual cloud detection method.** If you go this route, **rename the file/module and its docstrings** to stop calling it "s2cloudless Integration" — call it what it actually is (e.g. `spectral_cloud_heuristic.py`), so nobody (including a judge reading your architecture note) is misled about which method is actually running. The fallback itself is a reasonable, defensible heuristic — the problem is purely the mislabeling, not the technique.
- **Option B — fetch the additional 5 bands** (B01, B05, B8A, B09, B10) needed for real s2cloudless, if there's still time. This increases per-scene download size and adds complexity to `canvas.py`'s band-fetching logic, so weigh this against remaining time before committing.

**Whichever is chosen, make the decision explicit and update `PROVENANCE.md`/architecture note accordingly** — this is exactly the kind of detail a judge testing 2.2.3 could reasonably ask about directly.

---

## Suggested order to actually fix these

1. Issue 3 (grid-step fix) — small, isolated, no dependencies, do first.
2. Issue 1 + 2 together (mosaicking) — the bigger structural fix, resolves both symptoms at once.
3. Issue 4 — Option - B (fetch 10 bands) 
4. Re-run ingestion on a test AOI known to span a granule boundary (if you can identify one), confirm no duplicate `site_key`s appear and no unexpected coverage gaps remain.