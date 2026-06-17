# Plotting and helper functions for ParallelGamit

# Author: Shane Grigsby (espg) <refuge@rocktalus.com>
# Date: August 2024

import time
import networkx as nx
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
from sklearn.neighbors import NearestNeighbors

from geode.Utils import ecef2lla


def plot_global_network(central_points, OC, labels, points,
                        output_path, lat_lon=False):
    """Plot global GNSS station clustering and subnetwork connections

    Plots six views of the GNSS station clustering segmentation: polar
    stereographic north & south, and four views of the goid disk centered
    on the equator and rotated in 90 degree increments. Label colors for
    clusters are stable between subplots, and always refer to the same
    subnetwork. Overlapping tie points plotted twice, so that whichever
    subnetwork was plotter first will have that tie station over-plotted
    when the later subnetwork is plotted.

    Parameters
    ----------
    central_points : ndarray of type int, and shape (n_clusters,)
        Indices of the central most point (station) for each subnetwork.
        This is the output from `utils.select_central_point`
    OC : bool array of shape (n_clusters, n_coordinates)
        The expanded cluster labels, one-hot encoded. Each row is a boolean
        index to extract cluster membership for a given label. This is the
        output from `utils.over_cluster`
    labels : ndarray of type int, and shape (n_samples,)
        Cluster labels for each point in the dataset from prior clustering.
    points : ndarray of shape (n_samples, n_features)
        Coordinates of the station data, as either ECEF or Lat / Lon
    output_path : str
        File path to write the output plot to. Note that matplotlib infers
        the image output format from the file path suffix, so a path ending
        in '.png' will produce a png, and a '.jpeg' suffix will produce a jpg.
    lat_lon : boolean, default=False
        If the input parameter `points` is already in lat / lon coordinates.
        When set to (the default) `False`, input ECEF coordinates are assumed
        for `points`, and these are transformed to lat / lon for plotting.
    """

    # determine lat/lon coordinates, if needed
    if lat_lon:
        LL = points
    else:
        LL = np.zeros((points.shape[0], 2))
        lat, lon, _ = ecef2lla(points)
        LL[:, 0], LL[:, 1] = lon, lat  # x,y ordering for plotting convention

    fig = plt.figure(figsize=(8, 12))

    # empty lists (to fill in loop)
    nodes = []
    pos1, pos2, pos3, pos4, pos5, pos6 = [], [], [], [], [], []
    positions = [pos1, pos2, pos3, pos4, pos5, pos6]

    # define number and layout of subplots
    subs = [321, 322, 323, 324, 325, 326]

    # define projections, stack into list for iteration
    ref_r = (6378137.00, 6356752.3142)
    m1 = Basemap(projection='npstere', boundinglat=10, lon_0=270,
                 resolution='l')
    m2 = Basemap(projection='geos', lon_0=0, resolution='l', rsphere=ref_r)
    m3 = Basemap(projection='geos', lon_0=90, resolution='l', rsphere=ref_r)
    m4 = Basemap(projection='geos', lon_0=180, resolution='l', rsphere=ref_r)
    m5 = Basemap(projection='geos', lon_0=-90, resolution='l', rsphere=ref_r)
    m6 = Basemap(projection='spstere', boundinglat=-10, lon_0=270,
                 resolution='l')
    projs = [m1, m2, m3, m4, m5, m6]

    # for estimating start of plot run-time...
    t0 = time.time()
    for label in np.unique(labels):
        nodes.append(nx.Graph())
        # Select Cluster
        points = np.where(OC[label])[0]
        # Flag centroid point
        remove = np.where(points == central_points[label])[0]
        points = points.tolist()
        try:
            # remove centroid point so it's not repeated
            points.pop(remove[0])
            # add same point to beginning of list
            points.insert(0, central_points[label])
        except IndexError:
            nbrs = NearestNeighbors(n_neighbors=1, algorithm='ball_tree',
                                    metric='haversine').fit(LL[points])
            idx = nbrs.kneighbors(LL[central_points[label]].reshape(1, -1),
                                  return_distance=False)
            # add central point to beginning as the central connection point
            points.insert(0, points.pop(idx.squeeze()))
            central_points[label] = points[0]
        nx.add_star(nodes[label], points)
        for position, proj in zip(positions, projs):
            mxy = np.zeros_like(LL[points])
            mxy[:, 0], mxy[:, 1] = proj(LL[points, 0], LL[points, 1])
            position.append(dict(zip(nodes[label].nodes, mxy)))

    colors = [plt.cm.prism(each) for each in np.linspace(0, 1, len(nodes))]
    for position, proj, sub in zip(positions, projs, subs):
        fig.add_subplot(sub)
        proj.drawcoastlines()
        proj.fillcontinents(color='grey', lake_color='aqua', alpha=0.3)
        for i, node in enumerate(nodes):
            # need reshape to squash warning
            r, g, b, a = colors[i]
            nx.draw(node, position[i], node_size=4, alpha=0.95, width=.2,
                    node_color=np.array([r, g, b, a]).reshape(1, 4))

    # end of plot run-time...
    t1 = time.time()
    fig.supxlabel("Figure runtime:  " + ("%.2fs" % (t1 - t0)).lstrip("0"))
    plt.savefig(output_path)
    plt.close()

    return central_points


def plot_geographic_cluster_graph(centroids, cluster_ids, tie_labels, stnm, points_xyz):
    """
    Plot a geographic network of clusters with nodes at centroid locations and edges
    representing reciprocal tie point connections.

    Parameters:
        centroids (List[np.ndarray]): ECEF coordinates of snapped cluster centroids.
        cluster_ids (List[List[int]]): List of station indices (original + tie) per cluster.
        tie_labels (List[List[int]]): List of tie point indices per cluster.
        stnm (np.ndarray): Station name array aligned with points_xyz.
        points_xyz (np.ndarray): Nx3 array of ECEF station coordinates.
    """

    # Convert centroid coordinates to lat/lon
    centroids_array = np.array(centroids)
    lat_cent, lon_cent, _ = ecef2lla(centroids_array)

    # Determine cluster sizes and nearest station names
    cluster_sizes = [len(cluster) for cluster in cluster_ids]
    centroid_station_names = []
    for centroid in centroids:
        distances = np.linalg.norm(points_xyz - centroid, axis=1)
        closest_index = np.argmin(distances)
        centroid_station_names.append(stnm[closest_index])

    # Construct edges based on shared tie points
    edges = []
    for i in range(len(cluster_ids)):
        for j in range(i + 1, len(cluster_ids)):
            shared = set(tie_labels[i]) & set(cluster_ids[j]) | set(tie_labels[j]) & set(cluster_ids[i])
            if shared:
                edges.append((i, j, len(shared)))

    # Plot
    plt.figure(figsize=(14, 10))
    plt.scatter(lon_cent, lat_cent, s=np.array(cluster_sizes) * 10,
                color='cornflowerblue', edgecolor='black', alpha=0.8, zorder=3, marker='o')

    for i, j, w in edges:
        plt.plot([lon_cent[i], lon_cent[j]], [lat_cent[i], lat_cent[j]],
                 'k-', lw=0.5 + 0.2 * w, alpha=0.4, zorder=2)

    for lon, lat, label in zip(lon_cent, lat_cent, centroid_station_names):
        plt.text(lon, lat, label, fontsize=7, ha='center', va='center', zorder=4)

    plt.title("Geographic Cluster Graph (Labeled by Centroid Station)")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.grid(True)
    plt.axis('equal')  # Ensure equal scaling for longitude and latitude
    plt.tight_layout()
    plt.savefig('map.png', dpi=150, bbox_inches='tight')
    # plt.show()
