"""
Postseismic constraint for ETM Stacker.

Uses SW-Okada methodology for interpolating postseismic relaxation amplitude fields:
- Sandwell-Wessel elastic Green's functions for horizontal components
- Biharmonic spline interpolation for vertical component
- Okada dislocation model as physics-based regularization (shared from coseismic constraint)
"""

from datetime import datetime as _dt
from typing import List, Tuple, Dict, TYPE_CHECKING
import numpy as np
from tqdm import tqdm

from .fault_geometry import FaultGeometry
from .sw_okada import SWOkada
from ..data_classes import Station
from ..types import ConstraintType
from ...core.data_classes import Earthquake
from ...core.type_declarations import JumpType
from ....pyDate import Date
if TYPE_CHECKING:
    from ..grid_system import GridSystem


MISSING_DAYS_TOLERANCE = 5
# Minimum years of post-event data required for a station to be classified as
# constraining.  Stations that started observing shortly after the earthquake
# but whose time series ended before this threshold are demoted to to_constrain
# because their relaxation amplitude estimate is poorly determined.
# Exception: if the earthquake itself is younger than this threshold (relative
# to today's date), no station can meet the bar yet, so all stations with early
# post-event data are kept as constraining.
MIN_CONSTRAINING_YEARS = 2.0


class PostseismicConstraint(SWOkada):
    """
    Constraints for postseismic relaxation using SW-Okada interpolation.

    Uses the same fault geometry as the corresponding CoseismicConstraint to
    regularize the spatial interpolation of relaxation amplitude fields.
    The FaultGeometry instance is shared (passed in) to avoid recomputing the
    plane determination.
    """

    def __init__(self, event: Earthquake, fault_geometry: FaultGeometry,
                stations: List[Station], relaxation: float, grid: 'GridSystem',
                 h_sigma: float = 0.001, v_sigma: float = 0.003,
                 spline_tension: float = 0.10, is_collision: bool = False):

        super().__init__(event, fault_geometry, stations, grid,
                         h_sigma, v_sigma, ConstraintType.POSTSEISMIC, spline_tension, is_collision)

        # postseismic-specific state variable
        self.relaxation = relaxation

    def select_stations(self, all_stations: List[Station],
                        **kwargs) -> Tuple[List[Station], List[Station]]:
        """
        All stations with this postseismic relaxation constrain each other (mutual
        constraint, same as coseismic). This ensures a leave-one-out cross-validation
        is applied uniformly, regardless of whether a station has early post-seismic
        data or not.

        When no coseismic constraint has already determined the fault plane AND no
        station has well-determined parameters (all three component sigmas >= 1 mm),
        FaultGeometry.determine_plane would fall back to noisy data and produce a
        meaningless constraint.  In that case the postseismic amplitudes are
        zero-tied instead, exactly as for collisions.
        """
        constraining = []
        to_constrain = []

        # If the earthquake is younger than MIN_CONSTRAINING_YEARS (relative to today),
        # no station can have accumulated enough post-event data to meet the coverage
        # threshold — all qualifying stations are kept as constraining.
        today_fyear = Date(datetime=_dt.now()).fyear

        event_is_recent = (today_fyear - self.event.date.fyear) < MIN_CONSTRAINING_YEARS

        for stn in all_stations:
            jump = stn.etm.jump_manager.get_geophysical_jump(self.event.id)

            if (jump and jump.p.jump_type != JumpType.COSEISMIC_ONLY and
                    jump.get_relaxation_cols(self.relaxation)):

                dates = np.array([date.mjd for date in stn.etm.solution_data.coordinates.dates])
                post_event = dates[dates >= jump.date.mjd]

                has_early_data = np.min(post_event - jump.date.mjd) <= MISSING_DAYS_TOLERANCE

                if has_early_data:
                    post_event_span_years = (np.max(post_event) - jump.date.mjd) / 365.25
                    if event_is_recent or post_event_span_years >= MIN_CONSTRAINING_YEARS:
                        constraining.append(stn)
                    else:
                        tqdm.write(f' -- {stn.network_code}.{stn.station_code}: moved to to_constrain '
                                   f'for {self.event.id} (post-event span {post_event_span_years:.1f} yr '
                                   f'< {MIN_CONSTRAINING_YEARS:.1f} yr)')
                        to_constrain.append(stn)
                else:
                    to_constrain.append(stn)

        if self.is_collision:
            return [], constraining + to_constrain

        # No coseismic plane available.  Determine_plane will be called fresh using
        # these stations' jump parameters.  Apply the same sigma filter it uses
        # (all three component sigmas < 1 mm) to check whether at least one station
        # is well-determined enough to anchor the plane selection.
        sigma_threshold = 0.001
        for stn in constraining:
            jump = stn.etm.jump_manager.get_geophysical_jump(self.event.id)
            if jump is None:
                continue
            s_n = jump.p.sigmas[0][0] if len(jump.p.sigmas[0]) > 0 else np.inf
            s_e = jump.p.sigmas[1][0] if len(jump.p.sigmas[1]) > 0 else np.inf
            s_u = jump.p.sigmas[2][0] if len(jump.p.sigmas[2]) > 0 else np.inf
            if s_n < sigma_threshold and s_e < sigma_threshold and s_u < sigma_threshold:
                # At least one anchor station exists — proceed normally.
                return constraining, to_constrain

        # All stations have poorly determined parameters: zero-tie to prevent excursion.
        tqdm.write(f'PostseismicConstraint({self.event.id} {self.relaxation:.3f}): '
                   f'no station with all component sigmas < {sigma_threshold * 1000:.0f} mm '
                   f'and no coseismic plane available; zero-tying all parameters')
        return [], constraining + to_constrain

    def get_parameters_and_covariance(self, solution: np.ndarray, covariance: np.ndarray):
        """Return postseismic relaxation amplitudes and their covariance sub-matrix."""
        tp = solution.shape[1]
        idx = []
        relax_params = {}

        for stn in self._station_list:
            par, _ = stn.get_constrained_relax(self.event, self.relaxation, solution, covariance)
            if par is not None:
                idx.append(stn.get_postseismic_column(self.event, self.relaxation))
                relax_params[stn] = par

        if len(idx) > 1:
            v = np.array(list(relax_params.values())).T
            idx = np.array(idx).flatten()
            idx_ = np.concatenate((idx, idx + tp, idx + tp * 2))
            c = covariance[idx_][:, idx_]
            return v, c
        else:
            return np.array([]), np.array([])

    def _get_target_cols(self, station: Station, constraining: List[Station]):

        target_idx = station.get_postseismic_column(self.event.id, self.relaxation)
        idx = np.array([stn.get_postseismic_column(self.event.id, self.relaxation)
                        for stn in constraining if stn != station]).flatten()

        return target_idx, idx

    def short_description(self):
        return f"PostseismicConstraint({self.event.id} {self.relaxation:.3f})"

    def __str__(self) -> str:
        """String representation for debugging."""
        out_str = [f"{self.event.id}",
                   f"plane: {self.plane}",
                   f"relax: {self.relaxation:.3f}",
                   f"equation count: {len(self.equations) * 3}",
                   f"h_sigma: {self.h_sigma:.6f}", f"v_sigma: {self.v_sigma:.6f}"]

        return '; '.join(out_str)

    def __repr__(self) -> str:
        return f"PostseismicConstraint({str(self)})"
