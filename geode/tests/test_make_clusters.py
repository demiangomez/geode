# Author: Shane Grigsby (espg) <refuge@rocktalus.com>
# Created: September 2024

import pytest
import numpy as np

from .common import gen_variable_density_clusters, generate_clustered_data
from ..clustering import BisectingQMeans, overcluster


@pytest.mark.parametrize(
    ("qmax", "clust_size"),
    [
        [5, 10],
        [7, 10],
        [10, 50],
        [15, 50],
        [10, 250],
        [20, 250],
        [30, 250],
    ],
)
def test_ceiling_variable_density(qmax, clust_size):
    """Test algorithmic guarantee of BisectingQMeans on variable density data
    
    Verify that when `min_size=2`, that the max per cluster membership is
    under (<, less than) what parameter `opt_cluster_size` is set to"""

    data = gen_variable_density_clusters(clust_size)
    clust = BisectingQMeans(qmax=qmax,
                            init='random', n_init=50, algorithm='lloyd',
                            max_iter=8000, random_state=42)
    clust.fit(data)

    _, counts = np.unique(clust.labels_, return_counts=True)
    assert np.max(counts) <= qmax


@pytest.mark.parametrize(
    ("qmax", "overlap", "nmax"),
    [
        [5, 5, 2],
        [10, 2, 5],
        [10, 5, 2],
        [15, 2, 5],
        [20, 1, 10],
        [17, 10, 1],
    ],
)
def test_max_clust_expansion(qmax, overlap, nmax):
    """Test algorithmic guarantee of `overcluster`

    Verify that expanded cluster size is under (<=, less than or equal to):
    [initial cluster size + (neighbors * overlap)]"""
    
    data = gen_variable_density_clusters()
    clust = BisectingQMeans(qmax=qmax,
                            init='random', n_init=50, algorithm='lloyd',
                            max_iter=8000, random_state=42)
    clust.fit(data)
 
    OC = overcluster(clust.labels_, data, metric='euclidean', 
                      overlap=overlap, nmax=nmax,
                      method='dynamic')

    expanded_sizes = np.sum(OC, axis=1)
    _, original_sizes = np.unique(clust.labels_, return_counts=True)
    assert np.all((expanded_sizes - original_sizes) <= overlap*nmax)

