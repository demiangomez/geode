import heapq
import networkx as nx
import numpy as np

from scipy.spatial.distance import pdist, squareform
from sklearn.neighbors import NearestNeighbors


class DeterministicClustering(object):
    def __init__(self, target_size=15, tolerance=4,
                 num_tie_points=4, max_dist_to_centroid=5_000_000):

        self.target_size = target_size
        self.num_tie_points = num_tie_points
        self.tolerance = tolerance
        self.max_dist = max_dist_to_centroid
        self.points = np.array([])
        self.OC = np.array([])
        # variables to store results
        self.centroid_ids = []
        self.clustered_ids = []
        self.tie_ids = []

    def constrained_agglomerative(self, points, tie_clusters=True):
        """
        Spatially-constrained agglomerative clustering with centroid snapping.

        Parameters:
            points (np.ndarray): Nx3 ECEF coordinates.
            tie_clusters (bool): to tie clusters together using neighbors,
                                 pass True

        Returns:
            clustered_points (List[List[np.ndarray]]): Points per cluster.
            labels (np.ndarray): Cluster label for each point.
            final_centroids (List[np.ndarray]): Snapped centroids from points
                                                for each cluster.
        """
        target_size = self.target_size
        margin = self.tolerance
        max_dist_to_centroid = self.max_dist

        points = np.array(points)
        self.points = points

        min_size = target_size - margin
        max_size = target_size + margin

        clusters = {i: [i] for i in range(len(points))}
        centroids = {i: points[i] for i in range(len(points))}
        next_cluster_id = len(points)

        # Vectorized pairwise distance computation
        pairwise_dists = squareform(pdist(points))
        i_idx, j_idx = np.triu_indices(len(points), k=1)
        dists = pairwise_dists[i_idx, j_idx]
        valid_mask = dists <= max_dist_to_centroid * 2
        i_valid = i_idx[valid_mask]
        j_valid = j_idx[valid_mask]
        d_valid = dists[valid_mask]

        heap = list(zip(d_valid, i_valid, j_valid))
        heapq.heapify(heap)

        while heap:
            dist, ci, cj = heapq.heappop(heap)
            if ci not in clusters or cj not in clusters:
                continue

            merged_cluster = clusters[ci] + clusters[cj]
            if len(merged_cluster) > max_size:
                continue

            merged_points = points[merged_cluster]
            centroid = merged_points.mean(axis=0)
            # compare to square distance to avoid computing the sqrt
            # and save some computation time
            if np.any(np.einsum('ij,ij->i', merged_points - centroid,
                                merged_points -
                                centroid) > max_dist_to_centroid ** 2):
                continue

            clusters[next_cluster_id] = merged_cluster
            centroids[next_cluster_id] = centroid
            del clusters[ci], clusters[cj]
            del centroids[ci], centroids[cj]
            self.update_heap_vectorized(heap, clusters, centroids, centroid,
                                        merged_cluster, next_cluster_id,
                                        max_size, max_dist_to_centroid)

            next_cluster_id += 1

        # Precompute centroids and classify clusters
        cluster_items = list(clusters.values())
        cluster_lens = np.array([len(c) for c in cluster_items])
        is_final = cluster_lens >= min_size

        # Split clusters
        final_clusters = [cluster_items[i] for i in
                          range(len(cluster_items)) if is_final[i]]
        leftovers = [cluster_items[i] for i in
                     range(len(cluster_items)) if not is_final[i]]

        # Compute centroids for final clusters
        centroids_arr = np.array([points[c].mean(axis=0) for c in
                                  final_clusters])
        # Snap to closest input point in cluster
        snapped_idxs = [
            c[np.argmin(np.linalg.norm(points[c] - centroid, axis=1))]
            for c, centroid in zip(final_clusters, centroids_arr)
        ]
        final_centroids = list(points[snapped_idxs])
        centroid_ids = snapped_idxs

        for cluster in leftovers:
            if len(cluster) == 1:
                idx = cluster[0]
                point = points[idx]
                dists = np.linalg.norm(np.array(final_centroids) - point,
                                       axis=1)
                valid = [i for i in range(len(final_clusters)) if
                         len(final_clusters[i]) < max_size and
                         dists[i] <= max_dist_to_centroid]
                if valid:
                    best_fit = valid[np.argmin(dists[valid])]
                else:
                    best_fit = np.argmin(dists)
                final_clusters[best_fit].append(idx)
                cluster_points = points[final_clusters[best_fit]]
                centroid = cluster_points.mean(axis=0)
                snapped_idx = final_clusters[best_fit][np.argmin(
                    np.linalg.norm(cluster_points - centroid, axis=1))]
                final_centroids[best_fit] = points[snapped_idx]
                centroid_ids[best_fit] = snapped_idx
            else:
                centroid = points[cluster].mean(axis=0)
                dists = np.linalg.norm(np.array(final_centroids) - centroid,
                                       axis=1)
                best_fit = None
                for i in np.argsort(dists):
                    potential = final_clusters[i] + cluster
                    if len(potential) <= max_size:
                        test_points = points[potential]
                        test_centroid = test_points.mean(axis=0)
                        if np.all(np.linalg.norm(test_points -
                                                 test_centroid,
                                                 axis=1) <=
                                  max_dist_to_centroid):
                            best_fit = i
                            break
                if best_fit is not None:
                    final_clusters[best_fit].extend(cluster)
                    cluster_points = points[final_clusters[best_fit]]
                    centroid = cluster_points.mean(axis=0)
                    snapped_idx = final_clusters[best_fit][np.argmin(
                        np.linalg.norm(cluster_points - centroid, axis=1))]
                    final_centroids[best_fit] = points[snapped_idx]
                    centroid_ids[best_fit] = snapped_idx
                else:
                    final_clusters.append(cluster)
                    snapped_idx = cluster[np.argmin(np.linalg.norm(
                        points[cluster] - centroid, axis=1))]
                    final_centroids.append(points[snapped_idx])
                    centroid_ids.append(snapped_idx)

        labels = np.zeros(len(points), dtype=int)
        for idx, cluster in enumerate(final_clusters):
            for i in cluster:
                labels[i] = idx

        self.clustered_ids = final_clusters
        self.centroid_ids = centroid_ids

        # ties clusters together
        if tie_clusters:
            self.add_tie_points(labels, self.num_tie_points, self.max_dist)

        return final_clusters, labels, centroid_ids

    def update_heap_vectorized(self, heap, clusters, centroids, centroid,
                               merged_cluster, next_cluster_id, max_size,
                               max_dist_to_centroid):
        """
        Vectorized version to update heap with valid cluster pairs post merge

        Parameters:
            heap (List[Tuple[float, int, int]]): The heap to update.
            clusters (Dict[int, List[int]]): Dictionary of cluster_id -> list
                                             of point indices.
            centroids (Dict[int, np.ndarray]): Dictionary of cluster_id ->
                                               centroid coordinates.
            centroid (np.ndarray): The centroid of the newly merged cluster.
            merged_cluster (List[int]): Indices of points in the new cluster.
            next_cluster_id (int): The ID of the new cluster.
            max_size (int): Maximum allowed cluster size.
            max_dist_to_centroid (float): Maximum allowed distance for a valid
                                          connection.
        """
        # Extract existing cluster IDs and their centroids
        existing_ids = np.array(list(clusters.keys()))
        existing_centroids = np.array([centroids[k] for k in existing_ids])
        existing_sizes = np.array([len(clusters[k]) for k in existing_ids])

        # Compute distance from new centroid to all others
        dists = np.linalg.norm(existing_centroids - centroid, axis=1)

        # Evaluate which existing clusters are valid for merging
        size_sum = existing_sizes + len(merged_cluster)
        valid = ((existing_ids != next_cluster_id) &
                 (size_sum <= max_size) &
                 (dists <= max_dist_to_centroid * 2))

        # Push valid merge pairs into heap
        for other_id, dist in zip(existing_ids[valid], dists[valid]):
            heapq.heappush(heap, (dist, next_cluster_id, other_id))

    def add_tie_points(self, cluster_labels, num_neighbors=4,
                       max_tie_distance=5_000_000):
        """
        Add reciprocal tie points to each cluster from its nearest neighbors,
        then ensure every disconnected cluster component (island) is connected
        to at least `num_neighbors` external clusters.

        Parameters:
            cluster_labels (np.ndarray): Cluster index (0..K-1) for each
                                         station.
            num_neighbors (int): Minimum number of external links each island
                                 must have.
            max_tie_distance (float): Max allowable tie distance in meters.

        Returns:
            new_clusters (List[List[int]]): Cluster station indices including
                                            added tie points.
            tie_points (List[List[int]]): Tie point indices added to each
                                          cluster.
        """

        points = self.points
        labels = cluster_labels
        centroids = self.get_centroid_coordinates()

        n_clusters = len(centroids)
        n_stations = points.shape[0]

        # === Step 1: Initialize cluster structure ===
        clusters = [[] for _ in range(n_clusters)]
        for idx, label in enumerate(labels):
            clusters[label].append(idx)

        new_clusters = [list(cluster) for cluster in clusters]
        tie_points = [[] for _ in range(n_clusters)]
        used_points = set()
        tie_log = np.zeros((n_clusters, n_clusters), dtype=bool)

        # === Step 2: Initial connections to nearby neighbors using centroids
        nbrs = NearestNeighbors(n_neighbors=num_neighbors + 1).fit(centroids)
        _, neighbors = nbrs.kneighbors(centroids)

        def add_reciprocal_tie(i, j, pi_idx, pj_idx):
            new_clusters[i].append(pj_idx)
            new_clusters[j].append(pi_idx)
            tie_points[i].append(pj_idx)
            tie_points[j].append(pi_idx)
            used_points.update({pi_idx, pj_idx})
            tie_log[i, j] = True
            tie_log[j, i] = True

        for i in range(n_clusters):
            for j in neighbors[i][1:]:
                if tie_log[i, j] or tie_log[j, i]:
                    continue
                pi = [idx for idx in clusters[i] if idx not in used_points]
                pj = [idx for idx in clusters[j] if idx not in used_points]
                if not pi or not pj:
                    continue
                dist_matrix = np.linalg.norm(points[pi][:, None, :] -
                                             points[pj][None, :, :], axis=2)
                min_idx = np.unravel_index(np.argmin(dist_matrix), dist_matrix.shape)
                min_dist = dist_matrix[min_idx]
                if min_dist <= max_tie_distance:
                    add_reciprocal_tie(i, j, pi[min_idx[0]], pj[min_idx[1]])

        # === Step 3: Build the graph from current tie connections ===
        G = nx.Graph()
        G.add_nodes_from(range(n_clusters))
        for i in range(n_clusters):
            for j in range(i + 1, n_clusters):
                if set(tie_points[i]) & set(clusters[j]) or set(tie_points[j]) & set(clusters[i]):
                    G.add_edge(i, j)

        # === Step 4: Ensure every disconnected component connects to at least `num_neighbors` others ===
        components = sorted(list(nx.connected_components(G)), key=len)

        while len(components) > 1:
            updated = False  # Flag to break early if no valid connections were made

            for comp in components:
                external_nodes = set(range(n_clusters)) - comp
                connection_candidates = []

                for i in comp:
                    for j in external_nodes:
                        if tie_log[i, j] or tie_log[j, i]:
                            continue
                        pi = [idx for idx in clusters[i] if idx not in used_points]
                        pj = [idx for idx in clusters[j] if idx not in used_points]
                        if not pi or not pj:
                            continue
                        dist_matrix = np.linalg.norm(points[pi][:, None, :] -
                                                     points[pj][None, :, :],
                                                     axis=2)
                        min_idx = np.unravel_index(np.argmin(dist_matrix),
                                                   dist_matrix.shape)
                        dist = dist_matrix[min_idx]
                        if dist <= max_tie_distance:
                            connection_candidates.append((dist, i, j,
                                                          pi[min_idx[0]],
                                                          pj[min_idx[1]]))

                # Sort connections by shortest distance
                connection_candidates.sort()
                added = 0
                for conn in connection_candidates:
                    _, i, j, pi_idx, pj_idx = conn
                    if not G.has_edge(i, j):
                        add_reciprocal_tie(i, j, pi_idx, pj_idx)
                        G.add_edge(i, j)
                        added += 1
                        updated = True
                    if added >= num_neighbors:
                        break

                if added < num_neighbors:
                    pass
                    # tqdm.write(f" -- WARNING: Component containing clusters {sorted(comp)} "
                    #      f"was only connected to {added} external clusters under the distance constraint.")

            if not updated:
                # tqdm.write(" -- WARNING: Some disconnected components could not be joined with the rest of the graph.")
                break

            components = list(nx.connected_components(G))

        # create matrix with clusters and stations
        matrix = np.zeros((n_clusters, n_stations), dtype=bool)
        for i, cluster in enumerate(new_clusters):
            matrix[i, cluster] = True

        self.OC = np.array(matrix)

        self.clustered_ids = new_clusters
        self.tie_ids = tie_points

        return new_clusters, tie_points

    def get_cluster_coordinates(self):
        return [[self.points[i] for i in cluster]
                for cluster in self.clustered_ids]

    def get_centroid_coordinates(self):
        return [self.points[i] for i in self.centroid_ids]

    def get_tie_coordinates(self):
        return [[self.points[i] for i in cluster]
                for cluster in self.tie_ids]
