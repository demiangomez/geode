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
from .sw_okada import SWOkada
from ..data_classes import Station
from ..types import ConstraintType
from ...core.data_classes import Earthquake
from ...core.type_declarations import JumpType
from ....Utils import stationID, azimuthal_equidistant
from ....elasticity.elastic_interpolation import get_qpw, get_radius, spline2dgreen

if TYPE_CHECKING:
    from ..grid_system import GridSystem


class CoseismicConstraint(SWOkada):
    """
    Constraints for coseismic displacements using SW-Okada interpolation.

    This class handles:
    - Station selection based on coseismic jumps
    - Constraint coefficient computation (leave-one-out cross-validation)
    - K-matrix building for the stacker system
    - Grid prediction kernel computation

    Fault geometry and Okada physics are delegated to FaultGeometry.
    """

    def __init__(self, event: Earthquake, fault_geometry: FaultGeometry,
                 stations: List[Station], grid: 'GridSystem',
                 h_sigma: float = 1, v_sigma: float = 1,
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
        super().__init__(event, fault_geometry, stations, grid,
                         h_sigma, v_sigma, ConstraintType.COSEISMIC, spline_tension, is_collision)

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
