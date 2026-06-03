"""
Coseismic constraint for ETM Stacker.

Uses SW-Okada methodology for interpolating coseismic displacement fields:
- Sandwell-Wessel elastic Green's functions for horizontal components
- Biharmonic spline interpolation for vertical component
- Okada dislocation model as physics-based regularization
"""

from typing import List, Tuple, Dict, TYPE_CHECKING
import numpy as np
from tqdm import tqdm

from .base import BaseConstraint
from .fault_geometry import FaultGeometry
from ..data_classes import Station
from ..types import ConstraintType
from ...core.data_classes import Earthquake
from ...core.type_declarations import JumpType
from ....Utils import stationID, azimuthal_equidistant
from ....elasticity.elastic_interpolation import get_qpw, get_radius, spline2dgreen

if TYPE_CHECKING:
    from ..grid_system import GridSystem


class CoseismicConstraint(BaseConstraint):
    """
    Constraints for coseismic displacements using SW-Okada interpolation.

    This class handles:
    - Station selection based on coseismic jumps
    - Constraint coefficient computation (leave-one-out cross-validation)
    - K-matrix building for the stacker system
    - Grid prediction kernel computation

    Fault geometry and Okada physics are delegated to FaultGeometry.
    """

    def __init__(self, event: Earthquake, stations: List[Station], grid: 'GridSystem',
                 h_sigma: float = 0.007, v_sigma: float = 0.01,
                 spline_tension: float = 0.10,
                 is_collision: bool = False):
        """
        Initialize coseismic constraint.

        Parameters
        ----------
        event : Earthquake
            Earthquake with magnitude, location, and focal mechanism
        stations : List[Station]
            Stations with potential coseismic observations
        grid : GridSystem
            Grid system for interpolation
        h_sigma : float
            A priori sigma for horizontal constraints [m]
        v_sigma : float
            A priori sigma for vertical constraints [m]
        spline_tension : float
            Spline tension parameter for vertical interpolation (0 < t < 1)
        """
        super().__init__(ConstraintType.COSEISMIC, h_sigma, v_sigma)

        self.event = event
        self.grid = grid
        self.spline_tension = spline_tension

        # Fault geometry handles patch grids, Okada responses, and plane selection
        self.fault_geometry = FaultGeometry(event, stations)

        # Will be set after plane determination
        self.dislocation_model = None  # (a, p) design and regularization matrices

        # Cache for leave-one-out constraint coefficients
        self._constraint_coefficients: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

        # flag to set no constraining stations (add zero-tie)
        self.is_collision = is_collision

    @property
    def plane(self):
        """Selected fault plane index (0 or 1)."""
        return self.fault_geometry.plane

    @property
    def station_list(self):
        """Station names in order used for dislocation matrices."""
        return self.fault_geometry.station_list

    def select_stations(self, all_stations: List[Station],
                        **kwargs) -> Tuple[List[Station], List[Station]]:
        """
        Select stations with coseismic jump for this event.

        All selected stations constrain each other (mutual constraint).

        Returns
        -------
        Tuple[List[Station], List[Station]]
            (target_stations, constraining_stations) - same list for coseismic
        """
        coseismic_stations = []
        for stn in all_stations:
            jump = stn.etm.jump_manager.get_geophysical_jump(self.event.id)
            if jump and jump.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
                if jump.fit:
                    coseismic_stations.append(stn)
                else:
                    tqdm.write(f'WARNING: station {stationID(stn)} is flagged as affected by '
                               f'{self.event.id} but the ETM jump is not activated. This may '
                               f'induce a bias in the model around this station.')

        return coseismic_stations if not self.is_collision else [], coseismic_stations

    def compute_constraint_coefficients(self, target_station: Station,
                                        constraining_stations: List[Station],
                                        grids: 'GridSystem') -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Compute SW interpolation coefficients for predicting a target station.

        The coefficients define how observations at the constraining stations
        (excluding the target) predict the displacement at the target station.
        These are used to build the constraint equations in the stacker.

        Parameters
        ----------
        target_station : Station
            Station whose displacement will be predicted
        constraining_stations : List[Station]
            All constraining stations (including target, which will be excluded
            when computing the interpolation weights)
        grids : GridSystem
            Grid system with masks and interpolation grids

        Returns
        -------
        Tuple[np.ndarray, np.ndarray, np.ndarray]
            (ke, kn, ku) interpolation weight vectors, each of length 3*N_other
            where N_other = len(constraining_stations) - 1
        """
        mask = grids.earthquake_masks[self.event.id][0]

        # First call: determine fault plane and compute grid prediction kernels
        if self.dislocation_model is None:
            self._initialize_dislocation_model(constraining_stations, grids, mask)

        # Check cache
        station_id = stationID(target_station)
        if station_id in self._constraint_coefficients:
            return self._constraint_coefficients[station_id]

        # Compute interpolation coefficients for this target
        ke, kn, ku = self._compute_interpolation_coefficients(
            target_station, constraining_stations, mask
        )

        self._constraint_coefficients[station_id] = (ke, kn, ku)
        return ke, kn, ku

    def _initialize_dislocation_model(self, stations: List[Station],
                                       grids: 'GridSystem', mask: np.ndarray):
        """
        Initialize dislocation model: determine plane and compute grid kernels.
        """
        tqdm.write(f'Initializing SW-Okada model for {self.event.id}')

        # Determine which fault plane fits better (uses FaultGeometry)
        a, p = self.fault_geometry.determine_plane(
            stations, self.grid, mask, self.spline_tension
        )
        self.dislocation_model = (a, p)

        # Compute grid prediction kernels
        tqdm.write('Computing earthquake response for the interpolation grid')
        ke, kn, ku = self._compute_grid_prediction_kernels(stations, mask)
        grids.earthquake_responses[self.event.id] = (ke, kn, ku)

    def _compute_grid_prediction_kernels(self, stations: List[Station],
                                          mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Compute kernels for predicting displacement at grid points from station observations.

        Returns
        -------
        Tuple[np.ndarray, np.ndarray, np.ndarray]
            (ke, kn, ku) where:
            - ke: (M_grid, 2*N) maps horizontal obs to grid east
            - kn: (M_grid, 2*N) maps horizontal obs to grid north
            - ku: (M_grid, N) maps vertical obs to grid up
        """
        a, p = self.dislocation_model
        N = len(stations)

        # Project stations from EPICENTER — must match the frame used in determine_plane
        sites_lon = np.array([stn.lon for stn in stations])
        sites_lat = np.array([stn.lat for stn in stations])
        x, y = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat),
            sites_lon, sites_lat
        )

        # Local offset: same formula as fault_geometry._compute_sw_okada_system
        r_ev, _, _ = get_radius(np.column_stack([x, y]), np.column_stack([x, y]))
        np.fill_diagonal(r_ev, np.inf)
        local_reg = max(8.0, float(np.median(r_ev.min(axis=1))) * 0.5)

        # Pseudo-inverse of the fitted system (a is block_diag(ah, av))
        a_dagger = np.linalg.solve(a.T @ a + p, a.T)
        a_dagger_h = a_dagger[:2*N, :2*N]  # maps [E_obs, N_obs] -> horizontal coefficients
        a_dagger_v = a_dagger[2*N:, 2*N:]  # maps  U_obs         -> vertical coefficients

        # Project grid points from EPICENTER (grid.interpolation_geographic stores lon/lat)
        grid_lon = self.grid.interpolation_geographic[0][mask]
        grid_lat = self.grid.interpolation_geographic[1][mask]
        grid_x_epi, grid_y_epi = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat), grid_lon, grid_lat
        )

        # SW forward matrix: station body forces -> grid horizontal displacements
        q, pp, w = get_qpw(
            np.column_stack([x, y]),
            np.column_stack([grid_x_epi, grid_y_epi]),
            local_reg, self.grid.poisson_ratio
        )
        ae = np.hstack((q, w))   # (M_grid, 2*N)
        an = np.hstack((w, pp))  # (M_grid, 2*N)

        # Spline forward matrix: station body forces -> grid vertical displacements
        length_scale = np.abs(
            (grid_x_epi.max() - grid_x_epi.min()) +
            1j * (grid_y_epi.max() - grid_y_epi.min())
        ) / 50
        if length_scale == 0:
            length_scale = 1.0
        p_tens = np.sqrt(self.spline_tension / (1 - self.spline_tension)) / length_scale
        r_grid_to_stn = np.abs((grid_x_epi[:, None] - x) + 1j * (grid_y_epi[:, None] - y))
        au = spline2dgreen(r_grid_to_stn, p_tens)  # (M_grid, N)

        # Compose: grid = forward @ pseudo_inverse @ observations
        ke = ae @ a_dagger_h
        kn = an @ a_dagger_h
        ku = au @ a_dagger_v

        # Check for NaN in grid kernels
        if np.any(np.isnan(ke)) or np.any(np.isnan(kn)) or np.any(np.isnan(ku)):
            tqdm.write(f'WARNING: NaN detected in grid prediction kernels for event {self.event.id}')

        return ke, kn, ku

    def _compute_interpolation_coefficients(self,
                                            target_station: Station,
                                            constraining_stations: List[Station],
                                            mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Compute SW interpolation coefficients for a target station.

        Builds the SW-Okada interpolation system using all constraining stations
        except the target, then computes the weights that predict the target's
        displacement from the other stations' observations.

        Parameters
        ----------
        target_station : Station
            Station whose displacement will be predicted
        constraining_stations : List[Station]
            All constraining stations (target will be excluded)
        mask : np.ndarray
            Boolean mask for active grid points

        Returns
        -------
        Tuple[np.ndarray, np.ndarray, np.ndarray]
            (ke, kn, ku) interpolation weight vectors
        """
        # Get other stations (excluding target)
        idx = constraining_stations.index(target_station)
        other_stations = [stn for i, stn in enumerate(constraining_stations) if i != idx]
        N_other = len(other_stations)

        other_lon = np.array([stn.lon for stn in other_stations])
        other_lat = np.array([stn.lat for stn in other_stations])
        target_lon = np.array([target_station.lon])
        target_lat = np.array([target_station.lat])

        # Compute SW-Okada system for N-1 stations
        strike, dip = self.fault_geometry.get_strike_dip()

        a, p = self.fault_geometry._compute_sw_okada_system(
            self.grid, other_lon, other_lat, strike, dip, mask, self.spline_tension
        )

        # Pseudo-inverse
        a_dagger = np.linalg.solve(a.T @ a + p, a.T)

        # Project coordinates from EPICENTER (matches _compute_sw_okada_system)
        x_other, y_other = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat),
            other_lon, other_lat
        )
        x_target, y_target = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat),
            target_lon, target_lat
        )

        # Local offset from the N-1 other stations (same formula as _compute_sw_okada_system)
        r_ev, _, _ = get_radius(np.column_stack([x_other, y_other]),
                                np.column_stack([x_other, y_other]))
        np.fill_diagonal(r_ev, np.inf)
        local_reg = max(8.0, float(np.median(r_ev.min(axis=1))) * 0.5)

        # Horizontal forward: SW Green's functions from other stations to target
        q, pp, w = get_qpw(
            np.column_stack([x_other, y_other]),
            np.column_stack([x_target, y_target]),
            local_reg, self.grid.poisson_ratio
        )
        ap_e = np.hstack((q.flatten(), w.flatten()))   # (2*N_other,)
        ap_n = np.hstack((w.flatten(), pp.flatten()))  # (2*N_other,)

        # Vertical forward: spline Green's functions
        grid_x_masked = self.grid.interpolation_grid[0][mask]
        grid_y_masked = self.grid.interpolation_grid[1][mask]
        length_scale = np.abs(
            np.max(grid_x_masked) - np.min(grid_x_masked) +
            1j * (np.max(grid_y_masked) - np.min(grid_y_masked))
        ) / 50
        # Protect against division by zero (single point or empty mask)
        if length_scale == 0:
            length_scale = 1.0  # fallback to unit length scale
        p_tens = np.sqrt(self.spline_tension / (1 - self.spline_tension)) / length_scale
        r_target_to_others = np.abs((x_target - x_other) + 1j * (y_target - y_other))
        ap_u = spline2dgreen(r_target_to_others, p_tens)

        # Build full forward vectors (structure: [horizontal 2*N, vertical N])
        ap_e_full = np.concatenate([ap_e, np.zeros(N_other)])
        ap_n_full = np.concatenate([ap_n, np.zeros(N_other)])
        ap_u_full = np.concatenate([np.zeros(2 * N_other), ap_u])

        # Compose: target = forward @ pseudo_inverse @ other_observations
        ke = ap_e_full @ a_dagger
        kn = ap_n_full @ a_dagger
        ku = ap_u_full @ a_dagger

        # Check for NaN in coefficients
        if np.any(np.isnan(ke)) or np.any(np.isnan(kn)) or np.any(np.isnan(ku)):
            tqdm.write(f'WARNING: NaN detected in interpolation coefficients for '
                       f'{stationID(target_station)} in event {self.event.id}. '
                       f'N_other={N_other}, length_scale={length_scale:.4f}')

        return ke, kn, ku

    def _get_target_cols(self, station: Station,
                         constraining: List[Station]) -> Tuple[int, np.ndarray]:
        """Get column indices for target and constraining stations."""
        target_idx = station.get_coseismic_column(self.event.id)
        idx = np.array([
            stn.get_coseismic_column(self.event.id)
            for stn in constraining if stn != station
        ])
        return target_idx, idx

    def _build_k_matrix(self, station: Station,
                        constraining: List[Station],
                        grids: 'GridSystem',
                        total_parameters: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Build the K matrix rows for a single station constraint.

        The constraint equation is: target - K @ others = 0
        where K weights the other stations' observations.
        """
        _ke, _kn, _ku = self.compute_constraint_coefficients(station, constraining, grids)

        ke = np.zeros((1, total_parameters * 3))
        kn = np.zeros((1, total_parameters * 3))
        ku = np.zeros((1, total_parameters * 3))

        target_idx, idx = self._get_target_cols(station, constraining)
        N_other = len(idx)

        # Target station coefficient (-1)
        ke[0, target_idx] = -1
        kn[0, target_idx + total_parameters] = -1
        ku[0, target_idx + total_parameters * 2] = -1

        # Constraint weights structure: [east_weights (N), north_weights (N), up_weights (N)]
        # Horizontal predictions depend on both east and north observations (SW coupling)
        # Vertical prediction depends only on up observations (block diagonal)

        # East prediction: from east and north observations
        ke[0, idx] = _ke[:N_other]
        ke[0, idx + total_parameters] = _ke[N_other:2*N_other]

        # North prediction: from east and north observations
        kn[0, idx] = _kn[:N_other]
        kn[0, idx + total_parameters] = _kn[N_other:2*N_other]

        # Up prediction: from up observations only
        ku[0, idx + total_parameters * 2] = _ku[2*N_other:]

        return ke, kn, ku

    def short_description(self) -> str:
        return f"CoseismicConstraint({self.event.id})"

    def __str__(self) -> str:
        """String representation for debugging."""
        parts = [
            f"{self.event.id}",
            f"plane: {self.plane}",
            f"equations: {len(self.equations) * 3}",
            f"h_sigma: {self.h_sigma:.6f}",
            f"v_sigma: {self.v_sigma:.6f}"
        ]
        return '; '.join(parts)

    def __repr__(self) -> str:
        return f"CoseismicConstraint({str(self)})"
