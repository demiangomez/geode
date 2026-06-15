"""
SW-Okada leave-one-out interpolation mixin for coseismic and postseismic constraints.
"""

from abc import ABC, abstractmethod
from typing import List, Tuple, TYPE_CHECKING, Dict
import numpy as np
from tqdm import tqdm

from . import fault_geometry
from ..data_classes import Station
from ...core.data_classes import Earthquake
from .fault_geometry import FaultGeometry
from .base import BaseConstraint
from ..types import ConstraintType
from ....Utils import stationID, azimuthal_equidistant
from ....elasticity.elastic_interpolation import get_qpw, get_radius, spline2dgreen

if TYPE_CHECKING:
    from ..grid_system import GridSystem


class SWOkada(BaseConstraint):
    """
    SW-Okada interpolation for coseismic
    and postseismic constraints.
    """

    def __init__(self, event: Earthquake,
                 fault_geometry: FaultGeometry,
                 stations: List[Station], grid: 'GridSystem',
                 h_sigma: float = 0.001, v_sigma: float = 0.001,
                 constraint_type: ConstraintType = ConstraintType.COSEISMIC,
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
        super().__init__(constraint_type, h_sigma, v_sigma)

        self.event = event
        self.grid = grid
        self.spline_tension = spline_tension

        # Fault geometry handles patch grids, Okada responses, and plane selection
        self.fault_geometry = fault_geometry

        self.dislocation_model = None  # (a, p) design and regularization matrices

        # Snapshot of station_list at the time the dislocation model was built.
        # Frozen here so that a later call to fault_geometry.determine_plane
        # (from a shared PostseismicConstraint) cannot corrupt the N used in
        # predict_coseismic index arithmetic.
        self._station_list: list = stations

        # Cache for target-station constraint coefficients
        self._constraint_coefficients: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        # cache for prediction kernels
        self.grid_prediction_kernels = None

        # flag to set no constraining stations (add zero-tie)
        self.is_collision = is_collision

        # SWOkada configuration
        # With the normalized scaling (weight=1 → Okada term = data term),
        # the useful range is ~0.01 (data-dominated) to ~10 (Okada-dominated).
        # Values above ~10 are saturated: incremental changes have no effect.
        self.sw_okada_h_weight = 1.0
        self.sw_okada_v_weight = 1.0

        if constraint_type == ConstraintType.COSEISMIC:
            self._mask_index = 0
        else:
            self._mask_index = 1

    @property
    def plane(self):
        """Selected fault plane index (0 or 1)."""
        return self.fault_geometry.plane

    def get_parameters_and_covariance(self, solution: np.ndarray, covariance: np.ndarray):
        """retrieve solution parameters and covariance for this constraint"""

        # total parameters
        tp = solution.shape[1]

        return_fields, idx = [], []
        seismic = {}

        # find stations affected by event
        for stn in self._station_list:
            par, _ = stn.get_constrained_jump(self.event, solution, covariance)
            if par is not None:
                if self.constraint_type == ConstraintType.COSEISMIC:
                    idx.append(stn.get_coseismic_column(self.event))
                else:
                    idx.append(stn.get_postseismic_column(self.event, self.relaxation))
                seismic[stn] = par

        # if there is something to process, add an EtmStackerField to the return list
        if len(idx) > 1:
            v = np.array(list(seismic.values())).T
            idx = np.array(idx).flatten()
            idx_ = np.concatenate((idx, idx + tp, idx + tp * 2))
            c = covariance[idx_][:, idx_]

            return v, c
        else:
            return np.array([]), np.array([])

    def _compute_dislocation_model(self, grids: 'GridSystem', mask: np.ndarray):
        """
        Initialize dislocation model: determine plane and compute grid kernels.

        self._station_list (all stations: constraining + to_constrain) so that
        predict_seismic_deformation and the grid interpolation use every
        available observation.  Cross-validation coefficients
        (_compute_interpolation_coefficients) build their own per-station
        systems and are not affected by this choice.
        """
        tqdm.write(f'Initializing SW-Okada model for {self.event.id}')

        strike, dip = self.fault_geometry.get_strike_dip()

        # Use ALL stations (constraining + to_constrain): both sets have fitted
        # parameters from the joint stack and their spatial distribution improves
        # the SW-Okada regularization and the grid prediction coverage.
        all_stations = self._station_list
        sites_lon = np.array([stn.lon for stn in all_stations])
        sites_lat = np.array([stn.lat for stn in all_stations])

        self.dislocation_model = self.fault_geometry._compute_sw_okada_system(
            grids, sites_lon, sites_lat, strike, dip, mask,
            self.spline_tension, self.sw_okada_h_weight, self.sw_okada_v_weight
        )

        # Compute grid prediction kernels using the same all-station set so that
        # ke/kn/ku dimensions are consistent with dislocation_model and
        # station_list (N_all × N_all matrices throughout).
        tqdm.write('Computing earthquake response for the interpolation grid')
        ke, kn, ku = self._compute_grid_prediction_kernels(all_stations, mask)

        self.grid_prediction_kernels = (ke, kn, ku)

    def compute_constraint_coefficients(self, target_station: Station,
                                        constraining_stations: List[Station],
                                        grids: 'GridSystem') -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Compute SW-Okada leave-one-out coefficients for predicting a target station.

        Returns (ke, kn, ku), each of length 3*N_other where
        N_other = len(constraining_stations) - 1.
        """
        mask = grids.earthquake_masks[self.event.id][self._mask_index]

        if self.dislocation_model is None:
            self._compute_dislocation_model(grids, mask)

        station_id = stationID(target_station)

        if station_id in self._constraint_coefficients:
            return self._constraint_coefficients[station_id]

        ke, kn, ku = self._compute_interpolation_coefficients(
            target_station, constraining_stations, mask
        )

        self._constraint_coefficients[station_id] = (ke, kn, ku)

        return ke, kn, ku

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

        # Local offset: same formula as fault_geometry._compute_sw_okada_system.
        # Guard for N < 2 to avoid median([inf]) = inf.
        if N >= 2:
            r_ev, _, _ = get_radius(np.column_stack([x, y]), np.column_stack([x, y]))
            np.fill_diagonal(r_ev, np.inf)
            local_reg = max(8.0, float(np.median(r_ev.min(axis=1))) * 0.5)
        else:
            local_reg = 8.0

        # Pseudo-inverse of the fitted system (a is block_diag(ah, av)).
        # Use lstsq for robustness (handles rank-deficient edge cases).
        a_dagger, _, _, _ = np.linalg.lstsq(a.T @ a + p, a.T, rcond=None)
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
        Compute SW-Okada interpolation coefficients for a target station.

        If the target is in constraining_stations (leave-one-out, coseismic case),
        it is excluded and the system is built for N-1 stations.
        If the target is not in constraining_stations (postseismic case where the
        target lacks early data), all N constraining stations are used.
        Returns (ke, kn, ku) each of length 3*N_other.
        """
        try:
            idx = constraining_stations.index(target_station)
        except ValueError:
            idx = -1

        other_stations = [stn for i, stn in enumerate(constraining_stations) if i != idx]
        N_other = len(other_stations)

        other_lon = np.array([stn.lon for stn in other_stations])
        other_lat = np.array([stn.lat for stn in other_stations])
        target_lon = np.array([target_station.lon])
        target_lat = np.array([target_station.lat])

        # Build SW-Okada system for N-1 stations
        strike, dip = self.fault_geometry.get_strike_dip()
        a, p = self.fault_geometry._compute_sw_okada_system(
            self.grid, other_lon, other_lat, strike, dip, mask, self.spline_tension,
            self.sw_okada_h_weight, self.sw_okada_v_weight
        )

        # Pseudo-inverse.  Use lstsq instead of solve so that rank-deficient
        # systems (e.g. N_other=1 where the spline vertical block av=0 makes
        # the normal matrix singular) are handled gracefully rather than
        # crashing.  For full-rank systems (N_other >= 2) the result is
        # identical to solve.
        a_dagger, _, _, _ = np.linalg.lstsq(a.T @ a + p, a.T, rcond=None)

        # Project coordinates from epicenter (matches _compute_sw_okada_system)
        x_other, y_other = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat),
            other_lon, other_lat
        )
        x_target, y_target = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat),
            target_lon, target_lat
        )

        # Local offset from the N-1 other stations.  Guard for N_other < 2.
        if N_other >= 2:
            r_ev, _, _ = get_radius(np.column_stack([x_other, y_other]),
                                    np.column_stack([x_other, y_other]))
            np.fill_diagonal(r_ev, np.inf)
            local_reg = max(8.0, float(np.median(r_ev.min(axis=1))) * 0.5)
        else:
            local_reg = 8.0

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
        if length_scale == 0:
            length_scale = 1.0
        p_tens = np.sqrt(self.spline_tension / (1 - self.spline_tension)) / length_scale
        r_target_to_others = np.abs((x_target - x_other) + 1j * (y_target - y_other))
        ap_u = spline2dgreen(r_target_to_others, p_tens)

        # Build full forward vectors (structure: [horizontal 2*N_other, vertical N_other])
        ap_e_full = np.concatenate([ap_e, np.zeros(N_other)])
        ap_n_full = np.concatenate([ap_n, np.zeros(N_other)])
        ap_u_full = np.concatenate([np.zeros(2 * N_other), ap_u])

        # Compose: target = forward @ pseudo_inverse @ other_observations
        ke = ap_e_full @ a_dagger
        kn = ap_n_full @ a_dagger
        ku = ap_u_full @ a_dagger

        return ke, kn, ku

    def _build_k_matrix(self, station: Station,
                        constraining: List[Station],
                        grids: 'GridSystem',
                        total_parameters: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Build K matrix rows for a single station constraint (SW-Okada Convention B).

        Coefficient structure: ke/kn/ku each have length 3*N_other.
        Layout: [E-from-E | E-from-N | zeros] for ke,
                [N-from-E | N-from-N | zeros] for kn,
                [zeros    | zeros    | U-from-U] for ku.
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

        # East prediction: from east and north observations (SW coupling)
        ke[0, idx] = _ke[:N_other]
        ke[0, idx + total_parameters] = _ke[N_other:2*N_other]

        # North prediction: from east and north observations (SW coupling)
        kn[0, idx] = _kn[:N_other]
        kn[0, idx + total_parameters] = _kn[N_other:2*N_other]

        # Up prediction: from up observations only
        ku[0, idx + total_parameters * 2] = _ku[2*N_other:]

        return ke, kn, ku
