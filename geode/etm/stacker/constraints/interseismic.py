"""
Interseismic constraint for ETM Stacker.
"""

from typing import List, Tuple, TYPE_CHECKING
import numpy as np

from .base import BaseConstraint
from ..data_classes import Station
from ..types import ConstraintType
from ....Utils import azimuthal_equidistant
from ....elasticity.elastic_interpolation import get_radius

if TYPE_CHECKING:
    from ..grid_system import GridSystem


class InterseismicConstraint(BaseConstraint):
    """Constraints for interseismic velocities."""

    def __init__(self, stations: List[Station], h_sigma: float = 0.0001, v_sigma: float = 0.0003):
        super().__init__(ConstraintType.INTERSEISMIC, h_sigma, v_sigma)

        self._station_list = stations

    def select_stations(self, all_stations: List[Station],
                        **kwargs) -> Tuple[List[Station], List[Station]]:
        """
        Constraining: stations with interseismic component (no early earthquakes)
        To constrain: stations without interseismic component.
        """

        constraining = [stn for stn in all_stations if stn.is_interseismic]
        to_constrain = [stn for stn in all_stations if not stn.is_interseismic]

        return constraining, to_constrain

    def get_parameters_and_covariance(self, solution: np.ndarray, covariance: np.ndarray):
        """retrieve solution parameters and covariance for this constraint"""
        # total parameters
        tp = solution.shape[1]

        idx = np.array([stn.get_velocity_column() for stn in self._station_list])
        v = solution[:, idx]
        # create array with indices of stations for covariance
        idx_ = np.concatenate((idx, idx + tp, idx + tp * 2))
        c = covariance[idx_][:, idx_]

        return v, c

    def compute_constraint_coefficients(self, target_station: Station,
                                        constraining_stations: List[Station],
                                        grids: 'GridSystem') -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Use Sandwell-Wessel for horizontal, elastic loads for vertical (Convention A)."""
        lat = np.array([stn.lat for stn in constraining_stations])
        lon = np.array([stn.lon for stn in constraining_stations])

        x, y = azimuthal_equidistant(np.array([target_station.lon]),
                                     np.array([target_station.lat]), lon, lat)

        if len(x) > 1:
            r, _, _ = get_radius(np.column_stack([x, y]), np.column_stack([x, y]))
            np.fill_diagonal(r, np.inf)
            local_offset = max(8.0, float(np.median(r.min(axis=1))) * 0.5)
        else:
            local_offset = grids.offset

        ke, kn = grids.compute_horizontal_interpolant_at_point(0, 0, x, y, offset=local_offset)
        ku = grids.compute_vertical_interpolant_at_point(
            target_station.vertical_response,
            [stn.vertical_response for stn in constraining_stations]
        )

        return ke, kn, ku

    def _build_k_matrix(self, station: Station,
                        constraining: List[Station],
                        grids: 'GridSystem',
                        total_parameters: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Build K matrix for interseismic constraint (Convention A)."""
        _ke, _kn, _ku = self.compute_constraint_coefficients(station, constraining, grids)

        ke = np.zeros((1, total_parameters * 3))
        kn = np.zeros((1, total_parameters * 3))
        ku = np.zeros((1, total_parameters * 3))

        target_idx, idx = self._get_target_cols(station, constraining)

        ke[0, target_idx] = -1
        kn[0, target_idx + total_parameters] = -1
        ku[0, target_idx + total_parameters * 2] = -1
        # at the velocity position of the site, place constraint
        ke[0, np.concatenate((idx, idx + total_parameters))] = _ke
        kn[0, np.concatenate((idx, idx + total_parameters))] = _kn
        # vertical component
        ku[0, idx + total_parameters * 2] = _ku

        return ke, kn, ku

    def _get_target_cols(self, station: Station, constraining: List[Station]):
        # Target station gets -1
        target_idx = station.get_velocity_column()

        # Constraining stations get interpolation weights
        idx = np.array([stn.get_velocity_column() for stn in constraining])

        return target_idx, idx

    def short_description(self):
        return f"InterseismicConstraint()"

    def __str__(self) -> str:
        """String representation for debugging."""
        out_str = [f"eq count: {len(self.equations) * 3}",
                   f"h_sig: {self.h_sigma:.6f}", f"v_sig: {self.v_sigma:.6f}"]

        return '; '.join(out_str)

    def __repr__(self) -> str:
        return f"InterseismicConstraint({str(self)})"
