# PS 2.2.4 — Discovery and Clustering, Fully Explained

## The core confusion to clear up first: this is NOT the same as semantic search

You already understand semantic retrieval (2.2.1): analyst has something in mind — a description, or a reference photo — that **isn't yet a specific known tile**, types/uploads it, the system embeds *that new input*, and finds the closest matching tiles. The starting point is something **external to the archive**.

**2.2.4 starts from the opposite direction.** The starting point is a tile that's **already sitting in your archive, already embedded, already has a stable identity** — the analyst isn't describing anything new, they're pointing at something that already exists in your system and saying *"more like this one."*

This is the whole reason it's a separate PS requirement, not just "search again" — it's a fundamentally different action: **zero new description, zero new embedding computation, just "expand outward from this exact thing I'm already looking at."**

---

## "Analyst who identifies one location of interest" — where does this actually come from?

This was your exact confusion, and the honest answer is: **it's just whatever tile is currently on their screen, from any part of the system they were already using.** There's no special separate step where an analyst "identifies a location" in the abstract — it happens naturally as a byproduct of using the rest of your system. Concretely, in your actual app, it can come from any of these:

1. **A semantic search result.** Analyst searches "industrial storage yard," gets 5 ranked results, clicks into one that looks interesting. **That clicked tile is now the "location of interest."** This is the most natural, most common entry point.
2. **A change-detection result.** Analyst is reviewing a flagged construction event in the queue, looks at the "after" tile, and thinks "are there other sites like this?" — that tile becomes the seed.
3. **The review queue itself.** Browsing pending items, any tile they click becomes a candidate seed.
4. **Directly clicking a tile on the map/coverage view**, just exploring.

**The key realization:** you don't need to build a *new* screen or workflow for "identifying a location of interest" — you just need to add a **"Find similar sites" button** everywhere a single tile is already being displayed (your tile inspect modal already has an "Open in map" button sitting right there — this is the natural place to add a second button right next to it).

---

## "Across a wider area" — what this phrase is actually telling you

This is the part the diagram above is meant to make concrete. **"Wider area" does not mean a bigger AOI radius around the seed tile.** It means: **similarity search here is not spatial at all — it ignores geography entirely and searches your whole ingested archive, wherever those regions physically are.**

Look at the diagram: the seed tile (coral) is in the Ahmedabad archive. It connects to one similar tile in the *same* region — but it *also* connects to two similar-looking tiles sitting in a **completely different, geographically distant ingested region**. Meanwhile, tiles sitting *right next to* the seed, geographically, stay gray and unconnected because they don't look similar, despite being close.

**This is the entire point of the feature:** if an analyst finds one interesting industrial storage yard in Ahmedabad, and your archive also happens to include imagery from, say, a completely different city or border sector, discovery should surface visually/semantically similar storage yards there too — something a purely location-based search could never do, since it has no concept of "search everywhere I've ever ingested," only "search near this point."

---

## The actual technical mechanism, step by step

**Step 1 — happens continuously in the background, not per-click:**
A periodic batch job (HDBSCAN clustering) runs over **every embedding currently in Qdrant, across every region you've ever ingested**, and groups tiles that are visually/semantically similar into clusters, writing a `cluster_id` onto each tile. This runs on a schedule (e.g., after each ingestion batch, or nightly) — never live, per-query, since clustering the whole archive is too slow to do on demand.

**Step 2 — happens the instant the analyst clicks "Find similar sites":**
1. Look up the seed tile's `cluster_id` (already stored, from Step 1 — an instant database lookup, `SELECT * FROM tiles WHERE cluster_id = ...`).
2. Return every other tile sharing that same `cluster_id`.
3. Rank them by how close their embedding is to the seed tile's own stored embedding (optional refinement — the cluster gives you the candidate set, the distance ranks them).

**Notice what never happens here:** no text gets typed, no image gets uploaded, **no new call to the encoder model at all.** Everything used already existed — the seed tile's embedding, its cluster membership, the other tiles' data. This is exactly why this operation is near-instant compared to a fresh semantic search, and it's the technical reason this is a genuinely separate tool (`similarity_discovery`) from `semantic_search` in your agent's design, not just the same tool called again.

---

## "Without manually constructing a new query for each site" — the payoff explained

Without this feature, if an analyst wanted to find 10 similar storage yards across your whole archive, they'd have to: think of the right descriptive words, type a text search, look through results, repeat that guessing process again for each variation they think of. That's slow, and it relies on the analyst being able to *describe in words* what they're looking for — which is often harder than it sounds ("industrial storage yard" might miss ones that look slightly different but are functionally the same).

**With discovery:** one click, using the *visual/semantic fingerprint the system already computed*, instantly surfaces everything comparable — no guessing words, no repeated typing, and critically, it can find things that are hard to describe in text but visually/embedding-wise clearly similar.

---

## Where this fits into your actual system, concretely

| Piece | What to build |
|---|---|
| Background clustering job | A script that runs HDBSCAN over all Qdrant embeddings periodically, writes `cluster_id` back to each tile (Postgres + Qdrant payload) |
| UI trigger | A "Find similar sites" button, placed exactly where "Open in map" already sits — in your tile inspect modal, and optionally directly on result cards too |
| Backend tool | A `similarity_discovery` function/route: given a `tile_id`, look up its `cluster_id`, fetch cluster members, rank, return — reuses your existing tile-enrichment logic from semantic search (Phase 2.3) for pulling full metadata on each result |
| Display | Same result-grid/card UI you already built for semantic search results — this output can render in the exact same component, since the shape of the result (ranked list of tiles with metadata) is identical either way |
| Map | Since results can be geographically scattered (the whole point), showing them all pinned on the map at once — possibly zoomed out further than a normal AOI view — is a strong way to visually sell "wider area" to a judge |

**The honest summary in one line:** 2.2.4 isn't a new pipeline stage — it's a background clustering job plus one button that reuses infrastructure you've already built for 2.2.1, applied to a tile the analyst is already looking at instead of a fresh query. 

