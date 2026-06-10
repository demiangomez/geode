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
from .sw_okada_mixin import SWOkadaMixin
from ..data_classes import Station
from ..types import ConstraintType
from ...core.data_classes import Earthquake
from ...core.type_declarations import JumpType
from ....Utils import stationID, azimuthal_equidistant
from ....elasticity.elastic_interpolation import get_qpw, get_radius, spline2dgreen

if TYPE_CHECKING:
    from ..grid_system import GridSystem


class CoseismicConstraint(BaseConstraint, SWOkadaMixin):
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

        # Snapshot of station_list at the time the dislocation model was built.
        # Frozen here so that a later call to fault_geometry.determine_plane
        # (from a shared PostseismicConstraint) cannot corrupt the N used in
        # predict_coseismic index arithmetic.
        self._station_list: list = None

        # Cache for leave-one-out constraint coefficients
        self._constraint_coefficients: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

        # flag to set no constraining stations (add zero-tie)
        self.is_collision = is_collision

        # SWOkadaMixin configuration
        # With the normalised scaling (weight=1 → Okada term = data term),
        # the useful range is ~0.01 (data-dominated) to ~10 (Okada-dominated).
        # Values above ~10 are saturated: incremental changes have no effect.
        self._sw_okada_h_weight = 1.0
        self._sw_okada_v_weight = 1.0
        self._mask_index = 0
        self._loo_context = ''

    @property
    def plane(self):
        """Selected fault plane index (0 or 1)."""
        return self.fault_geometry.plane

    @property
    def station_list(self):
        """Station names in order used for dislocation matrices.

        Returns the snapshot taken when the coseismic dislocation model was
        built.  Falls back to fault_geometry.station_list before that happens,
        but after _ensure_dislocation_model runs the snapshot is immutable
        even if the shared FaultGeometry is later updated by a PostseismicConstraint.
        """
        if self._station_list is not None:
            return self._station_list
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

    def _ensure_dislocation_model(self, stations: List[Station],
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
        # Freeze the station list now.  Any subsequent call to determine_plane on
        # the shared FaultGeometry (e.g. from PostseismicConstraint) will overwrite
        # fault_geometry.station_list, but this constraint's property returns the
        # snapshot and remains unaffected.
        self._station_list = list(self.fault_geometry.station_list)

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

    def _get_target_cols(self, station: Station,
                         constraining: List[Station]) -> Tuple[int, np.ndarray]:
        """Get column indices for target and constraining stations."""
        target_idx = station.get_coseismic_column(self.event.id)
        idx = np.array([
            stn.get_coseismic_column(self.event.id)
            for stn in constraining if stn != station
        ])
        return target_idx, idx

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
