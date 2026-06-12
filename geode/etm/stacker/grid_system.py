"""
ETM Stacker grid system for spatial interpolation.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Union, TYPE_CHECKING
import numpy as np

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from matplotlib.path import Path
from scipy.spatial import ConvexHull
from scipy.interpolate import griddata
from tqdm import tqdm
from shapely.geometry import Polygon

from ..core.data_classes import Earthquake
from ..visualization.plot_fields import mask_ocean_points
from ...elasticity.elastic_interpolation import get_qpw, get_radius, spline2dgreen
from ...elasticity.diskload import compute_diskload, load_love_numbers
from ...elasticity.rectloadhs import mrectloadhs_dif
from ...Utils import stationID, azimuthal_equidistant, inverse_azimuthal

if TYPE_CHECKING:
    from .data_classes import Station
    from .constraints.coseismic import CoseismicConstraint
    from .constraints.postseismic import PostseismicConstraint


def fill_region_with_grid(x_points, y_points, radius, apply_buffer=True) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Fill the convex hull of scattered points with a square grid.
    Circles centered on these points with the given radius will just touch.

    Parameters:
    -----------
    x_points : array-like
        X coordinates of scattered points defining the region
    y_points : array-like
        Y coordinates of scattered points defining the region
    radius : float
        Radius of circles to be placed on grid points

    Returns:
    --------
    grid_x, grid_y : arrays
        Coordinates of grid points inside the region
    hull_x, hull_y : arrays
        X and Y coordinates of the convex hull boundary
    """

    # Convert to numpy arrays
    x_points = np.asarray(x_points)
    y_points = np.asarray(y_points)

    # Stack points for convex hull computation
    points = np.column_stack([x_points, y_points])

    # Compute convex hull
    hull = ConvexHull(points)
    hull_points = points[hull.vertices]

    if apply_buffer:
        poly = Polygon(hull_points)
        offset_distance = radius * 4
        expanded_poly = poly.buffer(offset_distance, join_style='mitre')
        expanded_hull_points = np.array(expanded_poly.exterior.coords[:-1])

        hull_x = expanded_hull_points[:, 0]
        hull_y = expanded_hull_points[:, 1]
        # Create a path from convex hull boundary
        boundary_path = Path(expanded_hull_points)
    else:
        hull_x = hull_points[:, 0]
        hull_y = hull_points[:, 1]
        # Create a path from convex hull boundary
        boundary_path = Path(hull_points)

    # Grid spacing = 2 * radius (so circles just touch)
    spacing = 2 * radius

    # Get bounding box
    x_min, x_max = np.min(hull_x), np.max(hull_x)
    y_min, y_max = np.min(hull_y), np.max(hull_y)

    # Create square grid
    x_grid = np.arange(x_min, x_max + spacing, spacing)
    y_grid = np.arange(y_min, y_max + spacing, spacing)

    # Create meshgrid
    X, Y = np.meshgrid(x_grid, y_grid)

    # Flatten to get all grid points
    grid_points = np.column_stack([X.ravel(), Y.ravel()])

    # Filter points inside the region
    inside = boundary_path.contains_points(grid_points)

    grid_x = grid_points[inside, 0]
    grid_y = grid_points[inside, 1]

    return grid_x, grid_y, hull_x, hull_y


def visualize_disks(x_points, y_points, hull_x, hull_y, grid_x, grid_y, radius):
    """
    Visualize the scattered points, convex hull, grid points, and circles.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Left plot: Scattered points, convex hull, and grid points
    ax1.plot(np.append(hull_x, hull_x[0]), np.append(hull_y, hull_y[0]),
             'b-', linewidth=2, label='Convex Hull')
    ax1.plot(grid_x, grid_y, 'ro', markersize=4, label='Grid Points')
    ax1.plot(x_points, y_points, 'bo', markersize=5, label='Scattered Points')
    ax1.set_aspect('equal')
    ax1.grid(True, alpha=0.3)
    ax1.legend()
    ax1.set_title('Region with Grid Points')
    ax1.set_xlabel('X')
    ax1.set_ylabel('Y')

    # Right plot: Convex hull with circles
    ax2.plot(np.append(hull_x, hull_x[0]), np.append(hull_y, hull_y[0]),
             'b-', linewidth=2, label='Convex Hull')

    # Draw circles at each grid point
    for x, y in zip(grid_x, grid_y):
        circle = Circle((x, y), radius, fill=False, edgecolor='red', alpha=0.6)
        ax2.add_patch(circle)

    # Plot centers
    ax2.plot(grid_x, grid_y, 'ko', markersize=2, alpha=0.5)
    ax2.set_aspect('equal')
    ax2.grid(True, alpha=0.3)
    ax2.legend()
    ax2.set_title(f'Circles with radius={radius:.3f} (just touching)')
    ax2.set_xlabel('X')
    ax2.set_ylabel('Y')

    # Set same limits for both plots
    x_margin = (np.max(hull_x) - np.min(hull_x)) * 0.1
    y_margin = (np.max(hull_y) - np.min(hull_y)) * 0.1
    ax1.set_xlim(np.min(hull_x) - x_margin, np.max(hull_x) + x_margin)
    ax1.set_ylim(np.min(hull_y) - y_margin, np.max(hull_y) + y_margin)
    ax2.set_xlim(np.min(hull_x) - x_margin, np.max(hull_x) + x_margin)
    ax2.set_ylim(np.min(hull_y) - y_margin, np.max(hull_y) + y_margin)

    plt.tight_layout()
    plt.show()
    return fig


def visualize_vectors(grid_x, grid_y, grid_vector):
    """
    Visualize the scattered points, convex hull, grid points, and circles.
    """
    fig = plt.figure(figsize=(14, 6))
    ax1 = fig.add_subplot(111)
    # Left plot: Scattered points, convex hull, and grid points
    ax1.plot(grid_x, grid_y, 'ro', markersize=4, label='Grid Points')
    ax1.quiver(grid_x, grid_y, grid_vector[0, :], grid_vector[1, :])
    ax1.set_aspect('equal')
    ax1.grid(True, alpha=0.3)
    ax1.legend()
    ax1.set_title('Region with Grid Points')
    ax1.set_xlabel('X')
    ax1.set_ylabel('Y')

    plt.tight_layout()
    plt.show()
    return fig


@dataclass
class GridSystem:
    """Encapsulates all grid-related computations."""
    origin: Tuple[float, float]  # (lon, lat)
    interpolation_grid: np.ndarray = field(
        default_factory=lambda: np.array([])
    )
    interpolation_geographic: np.ndarray = field(
        default_factory=lambda: np.array([])
    )
    grid_vertical_response: np.ndarray = field(
        default_factory=lambda: np.array([])
    )

    points: int = 0
    offset: float = 0.0
    poisson_ratio: float = 0.5

    # to store the earthquake responses for the grid
    earthquake_responses: Dict = field(default_factory=dict)
    earthquake_masks: Dict = field(default_factory=dict)

    @classmethod
    def create_from_stations(cls, stations: List['Station'],
                             grid_spacing: float = 20,
                             grid_load_radius: float = 50,
                             method='rectload',
                             tension: float = 0.10) -> 'GridSystem':
        """Factory method to create grid system from stations."""
        lat = np.array([stn.lat for stn in stations])
        lon = np.array([stn.lon for stn in stations])

        tqdm.write(f'Creating interpolation grids, station count {len(lat)}')
        tqdm.write(f'lon {np.array(lon).min()} {np.array(lon).max()} '
                   f'lat {np.array(lat).min()} {np.array(lat).max()}')

        # find an origin for the grid system
        grids_origin = (np.array(lon).mean(), np.array(lat).mean())

        ################################################################################
        # project the stations coordinates
        x, y = azimuthal_equidistant(grids_origin[0], grids_origin[1], lon, lat)

        # Compute median nearest-neighbour station spacing to set the SW regularization offset.
        # Matches MATLAB: reg = max(8, median_NN * 0.5)
        # The full N×N matrix includes N zeros on the diagonal; filling them with inf
        # before taking the column-wise minimum isolates each station's nearest neighbour.
        r, _, _ = get_radius(np.column_stack([x, y]),
                             np.column_stack([x, y]))
        np.fill_diagonal(r, np.inf)
        nn_dist = r.min(axis=1)

        tqdm.write(f'Station spacing statistics -> mean NN: {np.mean(nn_dist):.1f} km, '
                   f'median NN: {np.median(nn_dist):.1f} km')
        offset = max(8.0, float(np.median(nn_dist)) * 0.5)

        stations_projected = np.array([x, y])

        # save the station projected coordinates in the station object for convenience
        for i, stn in enumerate(stations):
            stn.projected_coords = (stations_projected[0, i], stations_projected[1, i])

        ################################################################################
        # create the grid and save it
        grid_x, grid_y, _, _ = fill_region_with_grid(x, y, grid_spacing / 2)

        # find the lat lon of points to apply the land/ocean mask
        glon, glat = inverse_azimuthal(grids_origin[0], grids_origin[1], grid_x, grid_y)
        mask = mask_ocean_points(glon, glat, grid_spacing * 4)
        grid_x = grid_x[mask]
        grid_y = grid_y[mask]

        points = grid_x.size
        # visualize_disks(x,y,hx,hy,grid_x,grid_y,grid_spacing / 2)
        interpolation_grid = np.array([grid_x, grid_y])
        interpolation_geographic = np.array(
            inverse_azimuthal(grids_origin[0], grids_origin[1], grid_x, grid_y))

        if method == 'diskload':
            # disk load as the preferred response
            disk_grid_x, disk_grid_y, _, _ = fill_region_with_grid(
                stations_projected[0], stations_projected[1], grid_load_radius
            )

            grid_vertical_response = cls._compute_disk_responses(
                stations, disk_grid_x, disk_grid_y, grid_load_radius, interpolation_grid
            )
        elif method == 'spline2d':

            tqdm.write(f'Computing spline grid response for interpolation '
                       f'grid (total points {grid_x.shape[0]})')

            grid_vertical_response, stations_vertical_response = cls._compute_spline_responses(
                stations_projected, grid_x, grid_y, tension
            )
            # assign the vertical response to each station
            for i, station in enumerate(stations):
                station.vertical_response = stations_vertical_response[i, :]

        elif method == 'rectload':
            disk_grid_x, disk_grid_y, _, _ = fill_region_with_grid(
                stations_projected[0], stations_projected[1], grid_load_radius
            )
            grid_vertical_response = cls._compute_rectload_responses(
                stations, stations_projected, disk_grid_x, disk_grid_y, grid_load_radius, interpolation_grid
            )
        else:
            raise ValueError(f'Method {method} not implemented')

        instance = GridSystem(grids_origin, interpolation_grid, interpolation_geographic,
                              grid_vertical_response, points, offset)

        tqdm.write(f'Created interpolation grids with size {grid_x.shape}')

        return instance

    @staticmethod
    def _compute_rectload_responses(stations: List['Station'], stations_projected: np.ndarray,
                                    disk_grid_x: np.ndarray, disk_grid_y: np.ndarray, disk_grid_spacing: float,
                                    interpolation_grid: np.ndarray):

        tqdm.write(f'Computing rectangular load grid response for interpolation '
                   f'grid (total points {interpolation_grid[0].shape[0]})')

        # average earth crust
        e = 50e6
        poisson_ratio = 0.27
        # get mu and lambda
        mu = e / (2 * (1 + poisson_ratio))
        lmb = e * poisson_ratio / ((1+poisson_ratio) * (1 - 2 * poisson_ratio))

        # Extract coordinates
        x, y = stations_projected

        [_, _, a] = mrectloadhs_dif(np.vstack((x, y, np.zeros_like(x))),
                                    np.vstack((disk_grid_x - disk_grid_spacing, disk_grid_x + disk_grid_spacing)).T,
                                    np.vstack((disk_grid_y - disk_grid_spacing, disk_grid_y + disk_grid_spacing)).T,
                                    lmb , mu)

        for i, station in enumerate(stations):
            station.vertical_response = a[i, :]

        grid_x, grid_y = interpolation_grid
        ########## now compute the response of the grid ##############
        [_, _, a] = mrectloadhs_dif(np.vstack((grid_x, grid_y, np.zeros_like(grid_x))),
                                    np.vstack((disk_grid_x - disk_grid_spacing, disk_grid_x + disk_grid_spacing)).T,
                                    np.vstack((disk_grid_y - disk_grid_spacing, disk_grid_y + disk_grid_spacing)).T,
                                    lmb, mu)

        tqdm.write(f'disk count: {disk_grid_x.shape} '
                   f'interpolation grid count: {interpolation_grid[0].shape} '
                   f'size of response matrix: {a.shape}')

        return a

    @staticmethod
    def _compute_spline_responses(stations_projected: np.ndarray,
                                  grid_x: np.ndarray, grid_y: np.ndarray, tension=0.10):

        length_scale = np.abs(np.max(grid_x.flatten()) - np.min(grid_x.flatten()) +
                              1j * (np.max(grid_y.flatten()) - np.min(grid_y.flatten()))) / 50

        p = np.sqrt(tension / (1 - tension))
        p = p / length_scale

        # Extract coordinates
        x, y = stations_projected

        # Compute all pairwise distances at once using broadcasting
        # Create n×n matrices of coordinate differences
        dx = x[:, np.newaxis] - x[np.newaxis, :]  # or: x[:, None] - x
        dy = y[:, np.newaxis] - y[np.newaxis, :]

        # Compute complex distances and take absolute value
        r = np.abs(dx + 1j * dy)

        # Apply spline2dgreen to entire distance matrix at once
        stations_vertical_response = spline2dgreen(r, p)

        ########## now compute the response of the grid ##############
        # Flatten and compute distances in fewer lines
        grid_x_flat = grid_x.ravel()
        grid_y_flat = grid_y.ravel()

        r = np.abs((grid_x_flat[:, None] - x) + 1j * (grid_y_flat[:, None] - y))
        grid_vertical_response = spline2dgreen(r, p)

        return grid_vertical_response, stations_vertical_response

    @staticmethod
    def compute_spline_greens(stations_projected: np.ndarray, tension=0.10) -> np.ndarray:
        """compute spline greens functions using projected coordinates"""

        # Extract coordinates
        x, y = stations_projected

        length_scale = np.abs(np.max(x.flatten()) - np.min(x.flatten()) +
                              1j * (np.max(y.flatten()) - np.min(y.flatten()))) / 50

        p = np.sqrt(tension / (1 - tension))
        p = p / length_scale

        # Compute all pairwise distances at once using broadcasting
        # Create n×n matrices of coordinate differences
        dx = x[:, np.newaxis] - x[np.newaxis, :]  # or: x[:, None] - x
        dy = y[:, np.newaxis] - y[np.newaxis, :]

        # Compute complex distances and take absolute value
        r = np.abs(dx + 1j * dy)

        # Apply spline2dgreen to entire distance matrix at once
        return spline2dgreen(r, p)

    @staticmethod
    def _compute_disk_responses(stations, disk_grid_x, disk_grid_y, disk_radius,
                                interpolation_grid, nmax_max=2500):
        # read love numbers now to accelerate the process later
        h_love, l_love, k_love = load_love_numbers()

        grid_vertical_response = []
        tqdm.write(f'Computing disk grid response for interpolation '
                   f'grid (total points {interpolation_grid[0].shape[0]})')

        bar = tqdm(total=interpolation_grid[0].shape[0], ncols=120)
        # compute the responses for the interpolation grid
        for i, (x, y) in enumerate(interpolation_grid.T):
            r, _, _ = get_radius(np.column_stack([disk_grid_x, disk_grid_y]),
                                 np.column_stack([x, y]))
            # add an offset to avoid singularities
            r = r + 8
            response = compute_diskload(alpha=disk_radius, theta_range=r, nmax_max=nmax_max,
                                        h_love=h_love, l_love=l_love, k_love=k_love)

            grid_vertical_response.append(response['U'])
            bar.update()

        bar.close()
        grid_vertical_response = np.array(grid_vertical_response)

        # compute the response for each station
        tqdm.write(f'Computing disk grid response for {len(stations)} stations')
        bar = tqdm(total=len(stations), ncols=120)

        for i, stn in enumerate(stations):
            r, _, _ = get_radius(np.column_stack([disk_grid_x, disk_grid_y]),
                                 np.column_stack([stn.projected_coords[0],
                                                  stn.projected_coords[1]]))
            # add an offset to avoid singularities
            r = r + 8
            response = compute_diskload(alpha=disk_radius, theta_range=r, nmax_max=nmax_max,
                                        h_love=h_love, l_love=l_love, k_love=k_love)

            # response on the site to all disks
            stn.vertical_response = response['U'].flatten()
            bar.update()

        bar.close()
        tqdm.write(f'disk count: {disk_grid_x.shape} '
                   f'interpolation grid count: {interpolation_grid[0].shape} '
                   f'size of response matrix: {grid_vertical_response.shape}')

        return grid_vertical_response

    def transform_coordinate(self, lon: float, lat: float):
        return azimuthal_equidistant(np.array(self.origin[0]), np.array(self.origin[1]),
                                     np.array(lon), np.array(lat))

    def compute_horizontal_interpolant_at_point(self, target_x: float, target_y: float,
                                                source_x: np.ndarray,
                                                source_y: np.ndarray,
                                                offset: float = None) -> Tuple[np.ndarray, np.ndarray]:
        """Sandwell-Wessel interpolation for a single point."""
        if offset is None:
            offset = self.offset

        # compute response between all stations
        a = self.compute_sw_design_matrix(source_x, source_y, offset)

        # compute response between current station (0, 0) and all stations
        q, p, w = get_qpw(np.column_stack([source_x, source_y]), np.array([[target_x, target_y]]),
                          offset, self.poisson_ratio)

        ke = np.linalg.solve(a, np.concatenate((q.flatten(), w.flatten())))
        kn = np.linalg.solve(a, np.concatenate((w.flatten(), p.flatten())))

        return ke, kn

    def compute_sw_design_matrix(self, source_x: np.ndarray, source_y: np.ndarray,
                                 offset: float = None) -> np.ndarray:
        """compute the design matrix of SW using provided coordinates."""
        if offset is None:
            offset = self.offset

        # compute response between all stations
        q, p, w = get_qpw(np.column_stack([source_x, source_y]),
                          np.column_stack([source_x, source_y]),
                          offset, self.poisson_ratio)

        return np.block([[q, w], [w, p]])  # type: ignore[arg-type]

    def compute_sw_forward_matrix(self,
                                  source_x: np.ndarray,
                                  source_y: np.ndarray,
                                  mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:

        q, p, w = get_qpw(np.column_stack([source_x, source_y]),
                          np.column_stack([self.interpolation_grid[0][mask],
                                           self.interpolation_grid[1][mask]]),
                          self.offset, self.poisson_ratio)

        return np.hstack((q, w)), np.hstack((w, p))

    @staticmethod
    def compute_vertical_interpolant_at_point(target_response: np.ndarray,
                                              source_responses: List[np.ndarray]) -> np.ndarray:
        """Elastic load interpolation for a single point."""

        # get the SVD decomposition for the design matrix
        u, s, vt = np.linalg.svd(np.array(source_responses), full_matrices=True)
        v = vt.T
        k = GridSystem._determine_svd_truncation(s)

        u_k = u[:, :k]
        s_k = s[:k]
        v_k = v[:, :k]

        ku = target_response @ (v_k / s_k) @ u_k.T

        return ku

    def compute_horizontal_grid_interpolant(self, source_x: np.ndarray,
                                            source_y: np.ndarray,
                                            mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Interpolate to full grid (for visualization)."""
        # now create the influence matrix for each grid point to use in the horizontal interpolation
        # compute response between all stations participating
        a = self.compute_sw_design_matrix(source_x, source_y)

        # compute response between current station (0, 0) and all stations
        q, p, w = get_qpw(np.column_stack([source_x, source_y]),
                          np.column_stack([self.interpolation_grid[0][mask],
                                           self.interpolation_grid[1][mask]]),
                          self.offset, self.poisson_ratio)

        # here, we do (A^-1 * B^t)^t so that we can multiply by v and get the answer at the interp points
        ke = np.linalg.solve(a, np.hstack((q, w)).T).T
        kn = np.linalg.solve(a, np.hstack((w, p)).T).T

        return ke, kn

    def compute_vertical_grid_interpolant(self, source_responses: List[np.ndarray],
                                          grid_vertical_response: np.ndarray) -> np.ndarray:
        """Interpolate vertical to full grid."""
        a = np.array(source_responses)
        g = grid_vertical_response

        # get the SVD decomposition for the design matrix
        u, s, vt = np.linalg.svd(a, full_matrices=True)
        v = vt.T

        k = self._determine_svd_truncation(s)

        u_k = u[:, :k]
        s_k = s[:k]
        v_k = v[:, :k]

        ku = g @ (v_k / s_k) @ u_k.T

        return ku

    @staticmethod
    def _determine_svd_truncation(s):
        """
        Determine the truncation to apply so that the answer has no more than 7 times more
        uncertainty than the input. This comes from the fact that
        uncertainty(solution) = Cond * uncertainty(data)
        """
        return int(len(s) * 4 / 5)

    def interpolate_field(self, stations: List['Station'],
                          enu: np.ndarray, covar: np.ndarray,
                          event: Earthquake = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Output enu_cov is en, e-u, n-u."""
        # get the number of parameters for this interpolation

        x = np.array([stn.projected_coords[0] for stn in stations])
        y = np.array([stn.projected_coords[1] for stn in stations])

        if event is not None:
            _, p_mask, _ = self.earthquake_masks[event.id]
            active_points = int(np.sum(p_mask))
        else:
            p_mask = np.array([True] * self.points)
            active_points = self.points

        grid_vertical_response = self.grid_vertical_response[p_mask, :]
        stations_vertical_response = [stn.vertical_response for stn in stations]
        xg, yg = self.interpolation_grid[0][p_mask], self.interpolation_grid[1][p_mask]

        # compute the grid velocities
        ae, an = self.compute_horizontal_grid_interpolant(x, y, p_mask)

        ah = np.vstack((ae, an))

        # find the ideal tension for this field
        pv, av = self._find_tension(x, y, xg, yg, np.zeros_like(xg), enu[2, :].flatten(), enu[2, :].flatten())

        # full design matrix for sigma values
        zeros = np.zeros((ae.shape[0], av.shape[1]))
        at = np.vstack((np.hstack((ae, zeros)),
                        np.hstack((an, zeros)),
                        np.hstack((zeros, zeros, av))))

        result = np.concatenate((ah @ enu[0:2, :].flatten(), pv))
        predict_cova = at @ covar @ at.T

        values = np.reshape(result, (3, active_points))
        enu_sigmas = np.reshape(np.sqrt(np.diag(predict_cova)), (3, active_points))
        # extract the ENU covariance
        en_cov = np.diag(predict_cova, k=active_points)[:active_points]  # Cov(E,N)
        eu_cov = np.diag(predict_cova, k=2 * active_points)[:active_points]  # Cov(E,U)
        nu_cov = np.diag(predict_cova, k=active_points)[active_points:]  # Cov(N,U)

        # Reshape to (3, n_points)
        enu_cov = np.reshape(np.concatenate((en_cov, nu_cov, eu_cov)), (3, active_points))

        return values, enu_sigmas, enu_cov

    def predict_seismic_deformation(self, event: Earthquake, stations: List['Station'],
                          observations: np.ndarray,
                          covar: np.ndarray,
                          constraint: Union['CoseismicConstraint',
                          'PostseismicConstraint']) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Output enu_cov is en, eu, nu."""
        tqdm.write(f'Predicting from {constraint.constraint_type} for event {event.id} {event.date.yyyyddd()}')

        sites = np.isin([stationID(s) for s in constraint.station_list],
                        [stationID(stn) for stn in stations])

        # grab the design matrix (dislocation_model is now a tuple (a, p))
        a_full, p_full = constraint.dislocation_model

        # Select rows corresponding to the stations we have
        # a_full is block_diag(ah, av) where ah is (2N x 2N) and av is (N x N)
        # Rows are ordered: [East_1..East_N, North_1..North_N, Up_1..Up_N]
        N = len(constraint.station_list)
        # Build index array for selected sites (E, N, U blocks)
        idx_e = np.where(sites)[0]
        idx_n = idx_e + N
        idx_u = idx_e + 2 * N
        row_idx = np.concatenate([idx_e, idx_n, idx_u])
        a = a_full[np.ix_(row_idx, row_idx)]

        # grab the grid response (already masked in CoseismicConstraint.compute_constraint_coefficients)
        ke, kn, ku = constraint.grid_prediction_kernels

        s_score = self.earthquake_masks[event.id][constraint._mask_index]

        # get how many points we actually need
        active_points = int(np.sum(s_score))

        c_field = np.full((3, active_points), np.nan)

        # ke, kn have shape (M_grid, 2*N) and expect horizontal observations [L_e, L_n]
        # ku has shape (M_grid, N) and expects vertical observations [L_u]
        obs_h = np.concatenate([observations[0], observations[1]])
        c_field[0, :] = ke @ obs_h
        c_field[1, :] = kn @ obs_h
        c_field[2, :] = ku @ observations[2]

        p = np.linalg.inv(covar)

        # compute uncertainties
        # Build combined kernel matrix that maps full observation vector to grid predictions
        # observations are structured as [L_e, L_n, L_u], kernel must match
        N_obs = len(observations[0])
        a_e = np.hstack([ke, np.zeros((active_points, N_obs))])  # ke maps [L_e, L_n], pad for L_u
        a_n = np.hstack([kn, np.zeros((active_points, N_obs))])  # kn maps [L_e, L_n], pad for L_u
        a_u = np.hstack([np.zeros((active_points, 2 * N_obs)), ku])  # ku maps L_u only, pad for L_e, L_n
        a_ = np.vstack((a_e, a_n, a_u))
        # extend covar
        covar_e = np.linalg.inv(a.T @ p @ a)
        predict_cova = a_ @ covar_e @ a_.T
        # reshape the result
        enu_sigma = np.reshape(np.sqrt(np.diag(predict_cova)), (3, active_points))

        # extract the ENU covariance
        en_cov = np.diag(predict_cova, k=active_points)[:active_points]  # Cov(E,N)
        eu_cov = np.diag(predict_cova, k=2 * active_points)[:active_points]  # Cov(E,U)
        nu_cov = np.diag(predict_cova, k=active_points)[active_points:]  # Cov(N,U)

        # Reshape to (3, n_points)
        enu_cov = np.reshape(np.concatenate((en_cov, eu_cov, nu_cov)), (3, active_points))

        return c_field, enu_sigma, enu_cov

    def _find_tension(self, xs, ys, xg, yg, f, vg, observations):
        i = 0
        rms_tension = np.zeros((99,))
        tensions = np.linspace(0.01, 0.99, 99)

        for tension in tensions:
            grid_response, stations_response = self._compute_spline_responses(
                np.array([xs, ys]), xg, yg, tension
            )
            # compute the interpolation matrix
            ar = self.compute_vertical_grid_interpolant(stations_response, grid_response)
            # compute residuals of this interpolation
            f_aux = griddata((xg, yg), f + ar @ vg, (xs, ys), method='cubic', fill_value=0)
            rms_tension[i] = np.sqrt(np.sum((observations - f_aux) ** 2) / len(observations))
            i += 1

        # choose the lowest rms
        tqdm.write(f'Lowest rms is {rms_tension[np.argmin(rms_tension)] * 1000.: 6.1f} mm '
                   f'tension={tensions[np.argmin(rms_tension)]}')

        grid_response, stations_response = self._compute_spline_responses(
            np.array([xs, ys]), xg, yg, tensions[np.argmin(rms_tension)]
        )
        # compute the interpolation matrix
        ar = self.compute_vertical_grid_interpolant(stations_response, grid_response)
        return ar @ vg, ar
