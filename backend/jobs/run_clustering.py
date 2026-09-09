"""
backend/jobs/run_clustering.py
==============================
Phase 1 & 2: Standalone Vector Clustering Job (PS 2.2.4)
========================================================

Executes unsupervised clustering over all tile embeddings across all ingested regions
in Qdrant (both Sentinel-2 and Maxar collections).

Pipeline:
1. Pulls all vectors, point IDs, and tile IDs from Qdrant via paginated scroll.
2. Performs HDBSCAN clustering (with adaptive KMeans fallback for smaller archives).
3. Assigns cluster IDs and selects the medoid (centroid-closest) representative tile.
4. Generates an interpretable spectral/landscape label from medoid tile indices.
5. Updates Postgres 'tiles.cluster_id' and Qdrant 'payload.cluster_id'.
6. Refreshes the Postgres 'clusters' table.

Can be run:
- As a standalone script: python -m backend.jobs.run_clustering
- Programmatically / via API: run_clustering_job()
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import psycopg2
import psycopg2.extras
from qdrant_client import QdrantClient

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ClusteringJob]: %(message)s"
)
log = logging.getLogger("ClusteringJob")

# Configuration from Environment
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5434"))
PG_DB = os.getenv("POSTGRES_DB", "eo_archive")
PG_USER = os.getenv("POSTGRES_USER", "eo_admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "eo_password")

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_S2 = os.getenv("QDRANT_COLLECTION", "tile_embeddings")
COLLECTION_MAXAR = os.getenv("QDRANT_MAXAR_COLLECTION", "maxar_tile_embeddings")
MODEL_VERSION = "RemoteCLIP-ViT-B-32"


def get_pg_connection():
    """Establishes Postgres connection."""
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS
    )


def get_qdrant_client() -> QdrantClient:
    """Initializes Qdrant client."""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, check_compatibility=False)


def fetch_all_qdrant_points(client: QdrantClient) -> Tuple[List[Any], List[str], List[np.ndarray], List[str]]:
    """
    Scrolls across both Sentinel-2 and Maxar Qdrant collections to fetch:
    - point_ids (UUIDs or ints)
    - tile_ids (from payload.tile_id)
    - vectors (NumPy 1D float32 arrays)
    - collections (source collection name for payload writeback)
    """
    point_ids: List[Any] = []
    tile_ids: List[str] = []
    vectors: List[np.ndarray] = []
    collections: List[str] = []

    target_collections = [COLLECTION_S2, COLLECTION_MAXAR]

    for coll in target_collections:
        try:
            # Check if collection exists
            coll_info = client.get_collection(coll)
            if not coll_info or coll_info.points_count == 0:
                log.info(f"Collection '{coll}' is empty or does not exist. Skipping.")
                continue

            log.info(f"Scrolling collection '{coll}' (total points: {coll_info.points_count})...")
            offset = None
            batch_count = 0

            while True:
                points, next_page = client.scroll(
                    collection_name=coll,
                    limit=200,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True
                )

                for p in points:
                    if p.vector is None:
                        continue
                    
                    t_id = p.payload.get("tile_id") if p.payload else str(p.id)
                    vec = np.asarray(p.vector, dtype=np.float32)

                    point_ids.append(p.id)
                    tile_ids.append(t_id)
                    vectors.append(vec)
                    collections.append(coll)

                batch_count += len(points)
                if not next_page:
                    break
                offset = next_page

            log.info(f"Loaded {batch_count} vectors from collection '{coll}'.")
        except Exception as e:
            log.warning(f"Failed to scroll points from collection '{coll}': {e}")

    return point_ids, tile_ids, vectors, collections


def cluster_embeddings(vectors: List[np.ndarray]) -> np.ndarray:
    """
    Applies HDBSCAN or adaptive KMeans clustering to group high-dimensional tile vectors.
    Returns integer cluster labels array (label -1 indicates noise/outliers in HDBSCAN).
    """
    n_samples = len(vectors)
    if n_samples == 0:
        return np.array([], dtype=int)

    X = np.stack(vectors, axis=0)

    # Normalize vectors to unit length for cosine distance equivalence
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X_norm = X / norms

    if n_samples < 4:
        # Trivial cluster for very small archives
        log.info(f"Sample size {n_samples} is small. Assigning single cluster.")
        return np.zeros(n_samples, dtype=int)

    # Try HDBSCAN first
    try:
        from sklearn.cluster import HDBSCAN
        min_c = min(5, max(2, n_samples // 3))
        hdb = HDBSCAN(min_cluster_size=min_c, metric="euclidean")
        labels = hdb.fit_predict(X_norm)

        # Count non-noise clusters
        unique_clusters = set(labels) - {-1}
        log.info(f"HDBSCAN produced {len(unique_clusters)} clusters (noise points: {np.sum(labels == -1)}).")

        # If HDBSCAN classified everything as noise or single cluster, fallback to KMeans
        if len(unique_clusters) < 2 and n_samples >= 4:
            log.info("HDBSCAN produced fewer than 2 clusters. Using KMeans for clearer partitioning.")
            from sklearn.cluster import KMeans
            k = min(5, max(2, n_samples // 2))
            km = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = km.fit_predict(X_norm)

        return labels
    except Exception as e:
        log.warning(f"HDBSCAN clustering encountered an issue: {e}. Falling back to KMeans.")
        from sklearn.cluster import KMeans
        k = min(5, max(2, n_samples // 2))
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        return km.fit_predict(X_norm)


def derive_cluster_label(
    cluster_idx: int,
    medoid_tile_id: str,
    member_tile_ids: List[str],
    pg_conn
) -> str:
    """
    Queries Postgres for spectral indices of the cluster medoid and members
    to generate a descriptive human-readable label.
    """
    try:
        with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    AVG(mean_ndvi) as avg_ndvi,
                    AVG(mean_ndwi) as avg_ndwi,
                    AVG(mean_ndbi) as avg_ndbi,
                    MAX(sensor) as sensor
                FROM tiles
                WHERE tile_id = ANY(%s);
            """, (member_tile_ids,))
            stats = cur.fetchone()
            pg_conn.commit()

        ndvi = stats.get("avg_ndvi") if stats else None
        ndwi = stats.get("avg_ndwi") if stats else None
        ndbi = stats.get("avg_ndbi") if stats else None

        # Land cover interpretation heuristic
        if ndwi is not None and ndwi >= 0.15:
            terrain_type = "Aquatic & Coastal Water"
        elif ndvi is not None and ndvi >= 0.35:
            terrain_type = "Dense Woodland & Forest Canopy"
        elif ndvi is not None and ndvi >= 0.20:
            terrain_type = "Vegetative & Agricultural Fields"
        elif ndbi is not None and ndbi >= 0.03:
            terrain_type = "Urban Core & Built-up Infrastructure"
        elif ndbi is not None and ndbi >= 0.0:
            terrain_type = "Industrial / Tarmac Logistics"
        else:
            terrain_type = "Open Arid & Transition Terrain"

        idx_info = []
        if ndvi is not None: idx_info.append(f"NDVI {ndvi:.2f}")
        if ndwi is not None: idx_info.append(f"NDWI {ndwi:.2f}")
        if ndbi is not None: idx_info.append(f"NDBI {ndbi:.2f}")
        
        idx_str = f" ({', '.join(idx_info)})" if idx_info else ""
        return f"Cluster {cluster_idx + 1} — {terrain_type}{idx_str}"
    except Exception as e:
        try:
            pg_conn.rollback()
        except Exception:
            pass
        log.warning(f"Error deriving cluster label: {e}")
        return f"Cluster {cluster_idx + 1} (Partition Group)"


def run_clustering_job() -> Dict[str, Any]:
    """
    Full Phase 1 execution function:
    Pulls embeddings, clusters, writes back to Postgres & Qdrant.
    """
    t0 = datetime.utcnow()
    log.info("=" * 70)
    log.info("STARTING STANDALONE TILE CLUSTERING JOB (PS 2.2.4)")
    log.info("=" * 70)

    q_client = get_qdrant_client()
    point_ids, tile_ids, vectors, collections = fetch_all_qdrant_points(q_client)

    if not vectors:
        log.warning("No vectors found in Qdrant collections. Exiting clustering job.")
        return {
            "status": "empty",
            "message": "No tile embeddings found in Qdrant.",
            "total_points": 0,
            "clusters_count": 0
        }

    log.info(f"Loaded {len(vectors)} total embeddings from archive. Running clustering algorithm...")
    labels = cluster_embeddings(vectors)

    run_stamp = t0.strftime("%Y%m%d%H%M%S")
    unique_labels = sorted(list(set(labels)))

    conn = get_pg_connection()

    # Map each tile to its new cluster_id
    tile_cluster_map: Dict[str, Optional[str]] = {}
    cluster_records: List[Dict[str, Any]] = []

    for label in unique_labels:
        # Group points belonging to this cluster
        indices = [i for i, l in enumerate(labels) if l == label]
        c_tile_ids = [tile_ids[i] for i in indices]

        if label == -1:
            # HDBSCAN Noise / Outlier points get NULL cluster_id
            for t_id in c_tile_ids:
                tile_cluster_map[t_id] = None
            log.info(f"Found {len(c_tile_ids)} outlier / noise points (assigned NULL cluster_id).")
            continue

        c_vectors = [vectors[i] for i in indices]
        c_id = f"cluster_{run_stamp}_{label + 1}"

        for t_id in c_tile_ids:
            tile_cluster_map[t_id] = c_id

        # Calculate geometric centroid vector
        centroid = np.mean(c_vectors, axis=0)

        # Select medoid (tile closest to centroid)
        dists = [np.linalg.norm(vec - centroid) for vec in c_vectors]
        medoid_idx = int(np.argmin(dists))
        medoid_tile_id = c_tile_ids[medoid_idx]

        # Generate descriptive label
        human_label = derive_cluster_label(
            cluster_idx=label,
            medoid_tile_id=medoid_tile_id,
            member_tile_ids=c_tile_ids,
            pg_conn=conn
        )

        cluster_records.append({
            "cluster_id": c_id,
            "label": human_label,
            "representative_tile_id": medoid_tile_id,
            "tile_count": len(c_tile_ids),
            "computed_at": t0,
            "model_version": MODEL_VERSION
        })

    log.info(f"Generated {len(cluster_records)} coherent clusters.")

    # 1. Update Postgres 'tiles' table with new cluster_ids
    try:
        with conn.cursor() as cur:
            # Batch update tiles in chunks of 500
            update_data = [(c_id, t_id) for t_id, c_id in tile_cluster_map.items()]
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE tiles SET cluster_id = %s WHERE tile_id = %s;",
                update_data,
                page_size=500
            )

            # 2. Refresh Postgres 'clusters' table
            cur.execute("DELETE FROM clusters;")
            for c in cluster_records:
                cur.execute("""
                    INSERT INTO clusters (
                        cluster_id, label, representative_tile_id, tile_count, computed_at, model_version
                    ) VALUES (%s, %s, %s, %s, %s, %s);
                """, (
                    c["cluster_id"],
                    c["label"],
                    c["representative_tile_id"],
                    c["tile_count"],
                    c["computed_at"],
                    c["model_version"]
                ))
            conn.commit()
        log.info(f"Successfully updated {len(update_data)} rows in Postgres 'tiles' and refreshed 'clusters' table.")
    except Exception as e:
        conn.rollback()
        log.error(f"Postgres update failed: {e}", exc_info=True)
    finally:
        conn.close()

    # 3. Update Qdrant payloads with cluster_id
    qdrant_updated = 0
    for p_id, t_id, coll in zip(point_ids, tile_ids, collections):
        c_id = tile_cluster_map.get(t_id)
        try:
            q_client.set_payload(
                collection_name=coll,
                payload={"cluster_id": c_id},
                points=[p_id]
            )
            qdrant_updated += 1
        except Exception as e:
            log.warning(f"Failed to set payload for Qdrant point {p_id} in {coll}: {e}")

    log.info(f"Updated cluster payload for {qdrant_updated} Qdrant points.")

    elapsed = round((datetime.utcnow() - t0).total_seconds(), 2)
    log.info("=" * 70)
    log.info(f"CLUSTERING JOB COMPLETE: {len(cluster_records)} Clusters Across {len(vectors)} Tiles in {elapsed}s")
    log.info("=" * 70)

    return {
        "status": "success",
        "total_points": len(vectors),
        "clusters_count": len(cluster_records),
        "outliers_count": sum(1 for c_id in tile_cluster_map.values() if c_id is None),
        "clusters": cluster_records,
        "elapsed_seconds": elapsed,
        "computed_at": t0.isoformat()
    }


if __name__ == "__main__":
    run_clustering_job()
