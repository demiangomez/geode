"""
Interseismic constraint for ETM Stacker.
"""

from typing import List, Tuple
import numpy as np

from .base import BaseConstraint
from ..data_classes import Station
from ..types import ConstraintType


class InterseismicConstraint(BaseConstraint):
    """Constraints for interseismic velocities."""

    def __init__(self, h_sigma: float = 0.0001, v_sigma: float = 0.0003):
        super().__init__(ConstraintType.INTERSEISMIC, h_sigma, v_sigma)

    def select_stations(self, all_stations: List[Station],
                        **kwargs) -> Tuple[List[Station], List[Station]]:
        """
        Constraining: stations with interseismic component (no early earthquakes)
        To constrain: stations without interseismic component.
        """

        constraining = [stn for stn in all_stations if stn.is_interseismic]
        to_constrain = [stn for stn in all_stations if not stn.is_interseismic]

        return constraining, to_constrain

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
