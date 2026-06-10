"""
SW-Okada leave-one-out interpolation mixin for coseismic and postseismic constraints.
"""

from abc import ABC, abstractmethod
from typing import List, Tuple, TYPE_CHECKING
import numpy as np
from tqdm import tqdm

from ..data_classes import Station
from ....Utils import stationID, azimuthal_equidistant
from ....elasticity.elastic_interpolation import get_qpw, get_radius, spline2dgreen

if TYPE_CHECKING:
    from ..grid_system import GridSystem


class SWOkadaMixin(ABC):
    """
    Mixin providing SW-Okada leave-one-out interpolation for coseismic
    and postseismic constraints.

    Host class must provide:
      self.event               : Earthquake
      self.grid                : GridSystem
      self.spline_tension      : float
      self.fault_geometry      : FaultGeometry
      self.dislocation_model   : tuple or None  (initialised lazily)
      self._constraint_coefficients : dict  (cache, initialised in __init__)
      self._sw_okada_h_weight  : float  (Okada horizontal weight)
      self._sw_okada_v_weight  : float  (Okada vertical weight)
      self._mask_index         : int  (0 for coseismic, 1 for postseismic)
      self._loo_context        : str  (appended to NaN warning, e.g. 'relax=0.050')
    """

    @abstractmethod
    def _ensure_dislocation_model(self, constraining_stations: List[Station],
                                   grids: 'GridSystem', mask: np.ndarray):
        """Initialise the dislocation model on first use. Must be implemented by host class."""
        pass

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
            self._ensure_dislocation_model(constraining_stations, grids, mask)
        station_id = stationID(target_station)
        if station_id in self._constraint_coefficients:
            return self._constraint_coefficients[station_id]
        ke, kn, ku = self._compute_interpolation_coefficients(
            target_station, constraining_stations, mask
        )
        self._constraint_coefficients[station_id] = (ke, kn, ku)
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
            self._sw_okada_h_weight, self._sw_okada_v_weight
        )

        # Pseudo-inverse
        a_dagger = np.linalg.solve(a.T @ a + p, a.T)

        # Project coordinates from epicenter (matches _compute_sw_okada_system)
        x_other, y_other = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat),
            other_lon, other_lat
        )
        x_target, y_target = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat),
            target_lon, target_lat
        )

        # Local offset from the N-1 other stations
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

        if np.any(np.isnan(ke)) or np.any(np.isnan(kn)) or np.any(np.isnan(ku)):
            ctx = f' ({self._loo_context})' if self._loo_context else ''
            tqdm.write(f'WARNING: NaN detected in SW-Okada interpolation coefficients for '
                       f'{stationID(target_station)} in event {self.event.id}'
                       f'{ctx}. N_other={N_other}, length_scale={length_scale:.4f}')

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
