"""Various utilities and functions to help ParallelGamit."""

# Author: Shane Grigsby (espg) <refuge@rocktalus.com>
# Created: August 2024 (clustering functions)

import warnings
import numpy as np
import pandas as pd
import scipy.sparse as sp

from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import pairwise_distances
from sklearn.base import _fit_context
from sklearn.utils._openmp_helpers import _openmp_effective_n_threads
from sklearn.utils._param_validation import Integral, Interval, StrOptions
from sklearn.utils.extmath import row_norms
from sklearn.utils.validation import (_check_sample_weight, check_random_state)
from sklearn.cluster._k_means_common import _inertia_dense, _inertia_sparse
from sklearn.cluster._kmeans import (_BaseKMeans, _kmeans_single_elkan,
                                     _kmeans_single_lloyd)


def prune(OC, central_points, method='minsize'):
    """Prune redundant clusters from overcluster (OC) and other arrays

    Parameters
    ----------

    OC : bool array of shape (n_clusters, n_coordinates)
    method : ["linear", "minsize:]; "linear" is a row-by-row scan through the
        cluster matrix, "minsize" will sort matrix rows (i.e., the clusters)
        according to size and prioritize pruning the smallest clusters first.

    Returns

    OC : Pruned bool array of shape (n_clusters - N, n_coordinates)
    central_points : Pruned int array of shape (n_clusters -N,)
    """
    subset = []
    rowlength = len(OC[0, :])
    if method == "linear":
        indices = list(range(len(OC)))
    elif method == "minsize":
        indices = np.argsort(OC.sum(axis=1))
    else:
        raise ValueError("Unknown method '" + method + "'")
    for i in indices:
        mod = OC.copy()
        mod[i, :] = np.zeros(rowlength)
        counts = mod.sum(axis=0)
        problems = np.sum(counts == 0)
        if problems == 0:
            subset.append(i)
            OC[i, :] = np.zeros(rowlength)
    # Cast subset list to pandas index
    dfIndex = pd.Index(subset)
    # Cast OC to pandas dataframe
    dfOC = pd.DataFrame(OC)
    # Apply the 'inverse' index; pruned is boolean numpy index array
    pruned = ~dfOC.index.isin(dfIndex)
    return OC[pruned], central_points[pruned]


def select_central_point(coordinates, centroids, metric='euclidean'):
    """Select the nearest central point in a given neighborhood

    Note this code explicitly assumes that centroids are passed from a
    sklearn clustering result (i.e., kmeans, or bisecting kmeans); those
    centroids are ordered as monotonically increasing labels. In other words,
    the output indices will match the labeling order of the input centroids.
    Note that `n_features` refers to the dimensionality of coordinate system,
    i.e., 2 for lat/lon, 3 for ECEF (Earth-Centered Earth-Fixed), etc.

    Parameters
    ----------

    coordinates : ndarray of shape (n_samples, n_features)
        Coordinates do not need to match what was used for the prior
        clustering; i.e., if 'Euclidean' was used to calculate the prior
        clustering in an X,Y,Z projection, those coordinates can be provided in
        spherical coordinates, provided that 'haversine' is selected for the
        `metric` parameter.
    centroids : ndarray of shape (n_clusters, n_features)
        Coordinates of the cluster centroids; distance metric for centroids
        should match both `coordinates` and the `metric` parameter.
    metric : str or callable, default='euclidean'
        Metric to use for distance computation. Any metric from scikit-learn or
        scipy.spatial.distance can be used. If metric is a callable function,
        it is called on each pair of instances (rows) and the resulting value
        recorded. See scikit-learn documentation for additional details.

    Returns
    -------
    central_points_idxs : int array of shape (n_clusters,)
        Indices of the central most point for each cluster; indices match the
        `labels` ordering.
    """
    nbrs = NearestNeighbors(n_neighbors=1, algorithm='ball_tree',
                            metric=metric).fit(coordinates)
    idxs = nbrs.kneighbors(centroids, return_distance=False)
    return idxs.squeeze()


def overcluster(labels, coordinates, metric='euclidean', overlap=4,
                nmax=2, rejection_threshold=5e6, method='static'):
    """Expand cluster membership to include edge points of neighbor clusters

    Expands an existing clustering to create overlapping membership between
    clusters. Existing clusters are processed sequentially by looking up
    nearest neighbors as an intersection of the current cluster membership and
    all cluster's point membership.  Once the `nmax` for a given
    neighbor cluster have been determined and added to current cluster, the
    remainder of that neighboring cluster is removed from consideration and
    distance query is rerun, with the process repeating until a number of
    clusters equal to the `overlap` parameter is reached. For stability,
    only original points are included for subsequent neighborhood searches;
    that is, Nearest neighbor distances are run as the shortest distance from
    all **original** members of the current cluster.

    Function requires an initial vector of cluster labels from a prior
    clustering, and coordinates in an ordering that matches the labels. This
    function also assumes that all points have been assigned a label (i.e.,
    there are no unlabeled points, or points labeled as 'noise').

    Parameters
    ----------

    labels : ndarray of type int, and shape (n_samples,)
        Cluster labels for each point in the dataset from prior clustering.
    coordinates : ndarray of shape (n_samples, n_features)
        Coordinates do not need to match what was used for the prior
        clustering; i.e., if 'Euclidean' was used to calculate the prior
        clustering in an X,Y,Z projection, those coordinates can be provided in
        spherical coordinates, provided that 'haversine' is selected for the
        `metric` parameter.
    metric : str or callable, default='euclidean'
        Metric to use for distance computation. Any metric from scikit-learn or
        scipy.spatial.distance can be used.

        Note that latitude and longitude values will need to be converted to
        radians if using the default 'haversine' distance metric.

        If metric is a callable function, it is called on each pair of
        instances (rows) and the resulting value recorded. The callable should
        take two arrays as input and return one value indicating the distance
        between them. This works for Scipy's metrics, but is less efficient
        than passing the metric name as a string.

        Use of "precomputed", i.e., a N-by-N distance matrix, has not been
        tested, nor have sparse matrices. These may or may not work.

        Valid values for metric are:

        - from scikit-learn: ['cityblock', 'cosine', 'euclidean', 'l1', 'l2',
          'manhattan']

        - from scipy.spatial.distance: ['braycurtis', 'canberra', 'chebyshev',
          'correlation', 'dice', 'hamming', 'jaccard', 'mahalanobis',
          'minkowski', 'rogerstanimoto', 'russellrao', 'seuclidean',
          'sokalmichener', 'sokalsneath', 'sqeuclidean', 'yule']

        Sparse matrices are only supported by scikit-learn metrics.  See the
        documentation for scipy.spatial.distance for details on these metrics.

    overlap: int greater than or equal to 1, default=3
        For method='static', this is total number of points that will be added
        to the seed clusters during cluster expansion.
        For method='paired', this is the number of cluster that are used to
        tie, with each cluster contributing exactly 2 points.
        For method='dynamic', this is the (zero-indexed) number of adjacent
        clusters to include when adding cluster membership overlap. Should be
        less than the number of unique cluster labels - 1.

    nmax : int greater than or equal to 1, default=2
        Should not exceed the size of the smallest cluster in `labels`. Note
        that this parameter has no effect for method='paired'.

    rejection_threshold : float, default=5e6
        Determines if any potential overlapping points should be rejected for
        being too far (from source centroid or nearest source edge point).
        Value of 'None' is equivalent to setting the threshold to infinity.
        Note that if value other than 'None' is used, there is no guarantee
        that all clusters will have overlap points added. This parameter value
        is required to be set when using method='paired'.

    method : 'static' (default), 'paired', or 'dynamic'
        The 'static' method will always produce an overcluster equal to the
        `overlap` parameter; 'dynamic' will produce an overcluster ceiling
        of (overlap - 1) * overlap_points, with a floor of overlap. The
        'paired' method will add 2 * `nieghbors` points per cluster, one
        of which is the closest nieghbor and one which is the farthest point
        within that same nieghboring cluster.

    Returns
    -------
    expanded_clusters : bool array of shape (n_clusters, n_coordinates)
        The updated labels, one-hot encoded. Each row is a boolean index to
        extract cluster membership for a given label. If labels are continuous
        integers starting at 0, then the row number will match the cluster
        label; if not, rows are ordered to monotonically increase from the
        smallest cluster label.
"""

    # Returns already sorted
    clusters = np.unique(labels)
    n_clusters = len(clusters)

    if (n_clusters - 1) < overlap:
        overlap = (n_clusters - 1)

    # reference index for reverse lookups
    ridx = np.array(list(range(len(labels))))
    output = np.zeros((n_clusters, len(labels)), dtype=np.bool_)

    for cluster in clusters:
        # Define current cluster membership (and non-membership)
        members = labels == cluster
        output[cluster, members] = True
        nonmembers = ~members

        # Implements 'edge' method of overlap expansion
        # Build index tree on members
        nbrs = NearestNeighbors(n_neighbors=1, algorithm='ball_tree',
                                metric=metric).fit(coordinates[members])
        if method == 'dynamic':
            coverage = len(np.unique(labels[output[cluster, :]]))
        else:  # method == 'static' or 'paired'
            coverage = 1

        while coverage <= overlap:
            # intersect search tree with non-members
            D, indx = nbrs.kneighbors(coordinates[nonmembers, :])
            mindex = np.argmin(D)
            # Select closest external point to add to member cluster
            new_member = ridx[nonmembers][mindex]
            # Grab label of new member for overlap and other checks
            nm_label = labels[new_member]
            # Paired method removes full cluster from consideration
            if method == 'paired':
                # 'remove' is the captured cluster, from which we select pairs
                remove = nm_label == labels
                # For simplicity, we use the single point defined my 'mindex'
                # as the 'member point' to calculate max eligible distance
                rdists = pairwise_distances(coordinates[members][indx[mindex]],
                                            coordinates[remove])
                # Filter too far points from argmax eligibility
                rdists[rdists >= rejection_threshold] = 0
                far_member = ridx[remove][np.argmax(rdists)]
                # Add near / far points to cluster for overlap
                output[cluster, new_member] = 1
                output[cluster, far_member] = 1
                # Remove captured cluster from further consideration
                nonmembers[remove] = False
                # Continue
                coverage += 1
            else:
                # Rejection threshold is lightly tested...
                if rejection_threshold:
                    if np.min(D) > rejection_threshold:
                        break
                # Remove point from future coordinate distance queries
                nonmembers[new_member] = 0
                # Add to member label array
                output[cluster, new_member] = 1
                if method == 'dynamic':
                    # Update current count of overclustered neighbors
                    coverage = len(np.unique(labels[output[cluster, :]]))
                elif method == 'static':
                    # Update current point expansion count
                    coverage += 1
                # Check if we've exceeded our overlap allotment...
                if sum(labels[output[cluster, :]] == nm_label) >= nmax:
                    # ...if so, remove entire neighboring cluster
                    remove = nm_label == labels
                    nonmembers[remove] = False
    return output


"""Bisecting Q-means clustering."""

# Modified from sklearn _bisecting_k_means.py
# Original bisecting_k_means author: Michal Krawczyk <mkrwczyk.1@gmail.com>
# Modifications by: Shane Grigsby <refuge@rocktalus.com>


class BisectingQMeans(_BaseKMeans):
    """Bisecting Q-Means clustering; modified from sklearn Bisecting K-means.

    In contrast to Bisecting K-Means, Bisecting Q-Means clustering will infer
    the number of clusters based on a termination condition. For this
    implementation the bisecting termination occurs according to the minimum
    and optimum cluster sizes, which are set by the `opt_size` and
    `min_size` parameters respectively. The child cluster of the bisected
    root cluster with the biggest inertia as determined by SSE (Sum of Squared
    Errors) will be selected bisection-- provided that the child cluster
    exceeds the set `*clust_size` boundary conditions. Cluster bisection
    terminates when there are no child clusters remaining that fulfill the user
    set boundary conditions.

    Parameters
    ----------
    qmax: int, default=25
        Hard cutoff to bypass the heuristic when bisecting clusters; no
        clusters greater than this size will be produced.

    init : {'k-means++', 'random'} or callable, default='random'
        Method for initialization:

        'k-means++' : selects initial cluster centers for k-mean
        clustering in a smart way to speed up convergence. See section
        Notes in k_init for more details.

        'random': choose `n_clusters` observations (rows) at random from data
        for the initial centroids.

        If a callable is passed, it should take arguments X, n_clusters and a
        random state and return an initialization.

    n_init : int, default=1
        Number of time the inner k-means algorithm will be run with different
        centroid seeds in each bisection.
        That will result producing for each bisection best output of n_init
        consecutive runs in terms of inertia.

    random_state : int, RandomState instance or None, default=None
        Determines random number generation for centroid initialization
        in inner K-Means. Use an int to make the randomness deterministic.
        See :term:`Glossary <random_state>`.

    max_iter : int, default=300
        Maximum number of iterations of the inner k-means algorithm at each
        bisection.

    verbose : int, default=0
        Verbosity mode.

    tol : float, default=1e-4
        Relative tolerance with regards to Frobenius norm of the difference
        in the cluster centers of two consecutive iterations  to declare
        convergence. Used in inner k-means algorithm at each bisection to pick
        best possible clusters.

    copy_x : bool, default=True
        When pre-computing distances it is more numerically accurate to center
        the data first. If copy_x is True (default), then the original data is
        not modified. If False, the original data is modified, and put back
        before the function returns, but small numerical differences may be
        introduced by subtracting and then adding the data mean. Note that if
        the original data is not C-contiguous, a copy will be made even if
        copy_x is False. If the original data is sparse, but not in CSR format,
        a copy will be made even if copy_x is False.

    algorithm : {"lloyd", "elkan"}, default="lloyd"
        Inner K-means algorithm used in bisection.
        The classical EM-style algorithm is `"lloyd"`.
        The `"elkan"` variation can be more efficient on some datasets with
        well-defined clusters, by using the triangle inequality. However it's
        more memory intensive due to the allocation of an extra array of shape
        `(n_samples, n_clusters)`.

    n_clusters : int, default=2
        The number of clusters to seed prior to recursively bisecting. This
        parameter is updated at inference time and will reflect the number of
        clusters identified after calling `fit`.

    Attributes
    ----------
    cluster_centers_ : ndarray of shape (n_clusters, n_features)
        Coordinates of cluster centers. If the algorithm stops before fully
        converging (see ``tol`` and ``max_iter``), these will not be
        consistent with ``labels_``.

    labels_ : ndarray of shape (n_samples,)
        Labels of each point.

    inertia_ : float
        Sum of squared distances of samples to their closest cluster center,
        weighted by the sample weights if provided.

    n_features_in_ : int
        Number of features seen during :term:`fit`.

    feature_names_in_ : ndarray of shape (`n_features_in_`,)
        Names of features seen during :term:`fit`. Defined only when `X`
        has feature names that are all strings.

    See Also
    --------
    KMeans : Original implementation of K-Means algorithm.

    """

    _parameter_constraints: dict = {
        **_BaseKMeans._parameter_constraints,
        "init": [StrOptions({"k-means++", "random"}), callable],
        "n_init": [Interval(Integral, 1, None, closed="left")],
        "copy_x": ["boolean"],
        "algorithm": [StrOptions({"lloyd", "elkan"})], }

    def __init__(
        self,
        qmax=25,
        *,
        init="random",
        n_init=1,
        random_state=None,
        max_iter=300,
        verbose=0,
        tol=1e-4,
        copy_x=True,
        algorithm="elkan",
        n_clusters=2,      # needed for base class, do not remove
    ):
        super().__init__(
            init=init,
            n_clusters=n_clusters,
            max_iter=max_iter,
            verbose=verbose,
            random_state=random_state,
            tol=tol,
            n_init=n_init,
        )

        self.qmax = qmax
        self.copy_x = copy_x
        self.algorithm = algorithm
        self.bisect = True

    def _warn_mkl_vcomp(self, n_active_threads):
        """Warn when vcomp and mkl are both present"""
        warnings.warn(
            "BisectingKMeans is known to have a memory leak on Windows "
            "with MKL, when there are less chunks than available "
            "threads. You can avoid it by setting the environment"
            f" variable OMP_NUM_THREADS={n_active_threads}."
        )

    def _bisect(self, X, x_squared_norms, sample_weight, cluster_to_bisect):
        """Split a cluster into 2 subsclusters.

        Parameters
        ----------
        X : {ndarray, csr_matrix} of shape (n_samples, n_features)
            Training instances to cluster.

        x_squared_norms : ndarray of shape (n_samples,)
            Squared euclidean norm of each data point.

        sample_weight : ndarray of shape (n_samples,)
            The weights for each observation in X.

        cluster_to_bisect : _BisectingTree node object
            The cluster node to split.
        """
        X = X[cluster_to_bisect.indices]
        x_squared_norms = x_squared_norms[cluster_to_bisect.indices]
        sample_weight = sample_weight[cluster_to_bisect.indices]

        best_inertia = None

        # Split samples in X into 2 clusters.
        # Repeating `n_init` times to obtain best clusters
        for _ in range(self.n_init):
            centers_init = self._init_centroids(
                X,
                x_squared_norms=x_squared_norms,
                init=self.init,
                random_state=self._random_state,
                n_centroids=2,
                sample_weight=sample_weight,
            )

            labels, inertia, centers, _ = self._kmeans_single(
                X,
                sample_weight,
                centers_init,
                max_iter=self.max_iter,
                verbose=self.verbose,
                tol=self.tol,
                n_threads=self._n_threads,
            )

            # allow small tolerance on the inertia to accommodate for
            # non-deterministic rounding errors due to parallel computation
            if best_inertia is None or inertia < best_inertia * (1 - 1e-6):
                best_labels = labels
                best_centers = centers
                best_inertia = inertia

        if self.verbose:
            print(f"New centroids from bisection: {best_centers}")

        counts = np.bincount(best_labels, minlength=2)
        scores = counts
        if (counts[0] + counts[1] >= self.qmax):
            cluster_to_bisect.split(best_labels, best_centers, scores)
        else:
            self.bisect = False

    @_fit_context(prefer_skip_nested_validation=True)
    def fit(self, X, y=None, sample_weight=None):
        """Compute bisecting k-means clustering.

        Parameters
        ----------
        X : {array-like, sparse matrix} of shape (n_samples, n_features)

            Training instances to cluster.

            .. note:: The data will be converted to C ordering,
                which will cause a memory copy
                if the given data is not C-contiguous.

        y : Ignored
            Not used, present here for API consistency by convention.

        sample_weight : array-like of shape (n_samples,), default=None
            The weights for each observation in X. If None, all observations
            are assigned equal weight. `sample_weight` is not used during
            initialization if `init` is a callable.

        Returns
        -------
        self
            Fitted estimator.
        """
        #DDG: commented since it does not appear to be implemented
        #X = self._validate_data(
        #    X,
        #    accept_sparse="csr",
        #    dtype=[np.float64, np.float32],
        #    order="C",
        #    copy=self.copy_x,
        #    accept_large_sparse=False,
        #)

        self._check_params_vs_input(X)

        self._random_state = check_random_state(self.random_state)
        sample_weight = _check_sample_weight(sample_weight, X, dtype=X.dtype)
        self._n_threads = _openmp_effective_n_threads()

        if self.algorithm == "lloyd" or self.n_clusters == 1:
            self._kmeans_single = _kmeans_single_lloyd
            self._check_mkl_vcomp(X, X.shape[0])
        else:
            self._kmeans_single = _kmeans_single_elkan

        # Subtract of mean of X for more accurate distance computations
        if not sp.issparse(X):
            self._X_mean = X.mean(axis=0)
            X -= self._X_mean

        # Initialize the hierarchical clusters tree
        self._bisecting_tree = _BisectingTree(
            indices=np.arange(X.shape[0]),
            center=X.mean(axis=0),
            score=0,
        )

        x_squared_norms = row_norms(X, squared=True)

        # run first bisection out of loop to avoid 0-count early termination
        cluster_to_bisect = self._bisecting_tree.get_cluster_to_bisect()
        self._bisect(X, x_squared_norms, sample_weight, cluster_to_bisect)
        while self.bisect:
            # Chose cluster to bisect
            cluster_to_bisect = self._bisecting_tree.get_cluster_to_bisect()

            # Split this cluster into 2 subclusters
            # if cluster_to_bisect is not None:
            if cluster_to_bisect.score > self.qmax:
                self._bisect(X, x_squared_norms, sample_weight,
                             cluster_to_bisect)
            else:
                self.bisect = False
                break

        # Aggregate final labels and centers from the bisecting tree
        self.labels_ = np.full(X.shape[0], -1, dtype=np.int32)
        self.cluster_centers_ = []

        for i, cluster_node in enumerate(self._bisecting_tree.iter_leaves()):
            self.labels_[cluster_node.indices] = i
            self.cluster_centers_.append(cluster_node.center)
            # label final clusters for future prediction
            cluster_node.label = i
            cluster_node.indices = None  # release memory

        self.n_clusters = len(self.cluster_centers_)
        cluster_centers_ = np.empty((self.n_clusters, X.shape[1]),
                                    dtype=X.dtype)
        for i, center in enumerate(self.cluster_centers_):
            cluster_centers_[i] = center[:]
        self.cluster_centers_ = cluster_centers_

        # Restore original data
        if not sp.issparse(X):
            X += self._X_mean
            self.cluster_centers_ += self._X_mean

        _inertia = _inertia_sparse if sp.issparse(X) else _inertia_dense
        self.inertia_ = _inertia(X, sample_weight, self.cluster_centers_,
                                 self.labels_, self._n_threads)

        self._n_features_out = self.cluster_centers_.shape[0]

        return self

    def _more_tags(self):
        return {"preserves_dtype": [np.float64, np.float32]}


class _BisectingTree:
    """Tree structure representing the hierarchical clusters from bisecting"""

    def __init__(self, center, indices, score):
        """Create a new cluster node in the tree.

        The node holds the center of this cluster and the indices of the data
        points that belong to it.
        """
        self.center = center
        self.indices = indices
        self.score = score

        self.left = None
        self.right = None

    def split(self, labels, centers, scores):
        """Split the cluster node into two subclusters."""
        self.left = _BisectingTree(indices=self.indices[labels == 0],
                                   center=centers[0], score=scores[0])
        self.right = _BisectingTree(indices=self.indices[labels == 1],
                                    center=centers[1], score=scores[1])

        # reset the indices attribute to save memory
        self.indices = None

    def get_cluster_to_bisect(self):
        """Return the cluster node to bisect next.

        It's based on the score of the cluster, which can be either the number
        of data points assigned to that cluster or the inertia of that cluster.
        """
        max_score = None

        for cluster_leaf in self.iter_leaves():
            if max_score is None or cluster_leaf.score > max_score:
                max_score = cluster_leaf.score
                best_cluster_leaf = cluster_leaf

        # if max_score >= self.opt_size:
        if np.isneginf(max_score):
            self.bisect = False
        else:
            return best_cluster_leaf

    def iter_leaves(self):
        """Iterate over all the cluster leaves in the tree."""
        if self.left is None:
            yield self
        else:
            yield from self.left.iter_leaves()
            yield from self.right.iter_leaves()
