"""
Postseismic constraint for ETM Stacker.
"""

from typing import List, Tuple
import numpy as np

from .base import BaseConstraint
from ..data_classes import Station
from ..types import ConstraintType
from ...core.data_classes import Earthquake
from ...core.type_declarations import JumpType

MISSING_DAYS_TOLERANCE = 3


class PostseismicConstraint(BaseConstraint):
    """Constraints for postseismic relaxation."""

    def __init__(self, event: Earthquake, relaxation: float,
                 h_sigma: float = 0.001, v_sigma: float = 0.003,
                 is_collision: bool = False):

        super().__init__(ConstraintType.POSTSEISMIC, h_sigma, v_sigma)
        self.event = event
        self.relaxation = relaxation
        # flag to set no constraining stations (add zero-tie)
        self.is_collision = is_collision

    def select_stations(self, all_stations: List[Station],
                        **kwargs) -> Tuple[List[Station], List[Station]]:
        """
        Constraining: stations with data and this relaxation
        To constrain: stations with relaxation but insufficient data.
        """

        constraining = []
        to_constrain = []
        for stn in all_stations:
            jump = stn.etm.jump_manager.get_geophysical_jump(self.event.id)

            if (jump and jump.p.jump_type != JumpType.COSEISMIC_ONLY and
                    jump.get_relaxation_cols(self.relaxation)):

                dates = np.array([date.mjd for date in stn.etm.solution_data.coordinates.dates])

                if np.min(dates[dates >= jump.date.mjd] - jump.date.mjd) <= MISSING_DAYS_TOLERANCE:
                    constraining.append(stn)
                else:
                    to_constrain.append(stn)

        if self.is_collision:
            return [], constraining + to_constrain
        return constraining, to_constrain

    def _get_target_cols(self, station: Station, constraining: List[Station]):

        target_idx = station.get_postseismic_column(self.event.id, self.relaxation)
        idx = np.array([stn.get_postseismic_column(self.event.id, self.relaxation)
                        for stn in constraining]).flatten()

        return target_idx, idx

    def short_description(self):
        return f"PostseismicConstraint({self.event.id} {self.relaxation:.3f})"

    def __str__(self) -> str:
        """String representation for debugging."""
        out_str = [f"{self.event.id}", f"relax {self.relaxation:.3f}",
                   f"equation count: {len(self.equations) * 3}",
                   f"h_sigma: {self.h_sigma:.6f}", f"v_sigma: {self.v_sigma:.6f}"]

        return '; '.join(out_str)

    def __repr__(self) -> str:
        return f"PostseismicConstraint({str(self)})"
