"""
ETM Stacker main class.
"""

import os
import copy
from typing import List, Tuple, Dict, Union, Set
import numpy as np
import numpy.linalg.linalg
from tqdm import tqdm

from .data_classes import (
    EtmStackerConfig, NormalEquations, Station, EtmStackerField
)
from .grid_system import GridSystem
from .types import ConstraintType
from .constraints import (
    InterseismicConstraint, CoseismicConstraint,
    PostseismicConstraint, ConstraintRegistry
)
from .constraints.fault_geometry import FaultGeometry
from ..core.etm_engine import EtmEngine
from ..core.etm_config import EtmConfig
from ..core.data_classes import Earthquake
from ..core.type_declarations import SolutionType, FitStatus, JumpType
from ..data.solution_data import SolutionDataException
from ..least_squares.design_matrix import DesignMatrixException
from ..etm_functions.jumps import JumpFunction
from ..visualization.plot_fields import plot_velocity_field
from ...dbConnection import Cnn
from ...pyDate import Date
from ...pyOkada import Mask
from ...Utils import stationID, print_yellow


class EtmStackerException(Exception):
    """Exception raised for ETM Stacker errors."""
    pass


class EtmStacker:
    """Simplified main class focusing on orchestration."""

    def __init__(self, config: EtmStackerConfig = None):

        # Core data
        self.stations: List[Station] = []
        self.normal_equations: List[NormalEquations] = []
        self.earthquakes: List[Earthquake] = []
        self.collided_earthquakes: Set[str] = set()  # IDs of earthquakes that lost collision check

        # Constraint management
        self.constraint_registry: ConstraintRegistry = ConstraintRegistry()

        # Grid system
        self.grids: GridSystem = GridSystem((0, 0))

        # Configuration
        if config is None:
            self.config = EtmStackerConfig()
        else:
            self.config = config

        # System normal equations
        self.total_parameters: int = 0
        self.total_equations: int = 0
        self.total_constraints: int = 0
        self.variance: float = 0.

        # Results
        self.solved: bool = False
        self.solution: np.ndarray = np.array([])
        self.covariance: np.ndarray = np.array([])

        # interpolated fields
        self.fields: List[EtmStackerField] = []

        # to save the command history applied to the stacker instance
        self.command_history: List[str] = []
        # to store the name of the current pickle (without extension)
        self.filename: str = ''

        self.print_config()

    def print_config(self):
        sr = ','.join(['%.3f' % r for r in self.config.relaxation])
        cp = ','.join(['%s' % r for r in self.config.earthquakes_cherry_picked])
        tqdm.write(f' -- Initialized EtmStacker with max cond number: {self.config.max_condition_number}; '
                   f'relaxations: {sr}')
        tqdm.write(f' -- Earthquake mag limit: {self.config.earthquake_magnitude_limit}; '
                   f'Cherry picked earthquakes: {cp};')
        from ...pyDate import Date
        if isinstance(self.config.post_seismic_back_lim, Date):
            tqdm.write(f' -- Considering events starting from {self.config.post_seismic_back_lim.yyyyddd()}')
        else:
            tqdm.write(f' -- Considering events up to {self.config.post_seismic_back_lim / 365} '
                       f'years back from station start')

        tqdm.write(f" -- Interseismic sigmas: {self.config.interseismic_h_sigma * 1000.} mm/yr "
                   f"{self.config.interseismic_v_sigma * 1000.} mm/yr")
        tqdm.write(f" -- Coseismic sigmas: {self.config.coseismic_h_sigma * 1000.} mm "
                   f"{self.config.coseismic_v_sigma * 1000.} mm")
        tqdm.write(f" -- Postseismic sigmas: {self.config.postseismic_h_sigma * 1000.} mm "
                   f"{self.config.postseismic_v_sigma * 1000.} mm")
        tqdm.write(f" -- Station weight scale factor: {self.config.station_weight_scale}")
        tqdm.write(f" -- Vertical interpolation method: {self.config.vertical_method}")
        if self.config.vertical_method != 'spline2d':
            tqdm.write(f" -- Vertical load radius (for diskload or rectload): {self.config.vertical_load_radius} km")
        else:
            tqdm.write(f" -- Spline2d tension: {self.config.tension}")

        tqdm.write(f" -- ETM stacker model filename: {self.filename}")

    def add_station(self, cnn: Cnn, network_code: str, station_code: str,
                    json_folder: str = None,
                    save_json_folder: str = None):
        """Add a station to the stack."""
        # Build ETM
        etm = self._build_etm(cnn, network_code, station_code, json_folder, save_json_folder)
        if etm is None:
            return

        # Create station
        station = self._create_station(etm)

        # Create normal equations
        neq = self._create_normal_equations(station)

        # Store
        self.stations.append(station)
        self.normal_equations.append(neq)

    def remove_station(self, station_id: str):
        remove_from_index = 0
        for i, station in enumerate(self.stations):
            # remove from the parameter range the station that has been removed
            # this is applied to any stations that come after the removed site
            station.normal_equations.parameter_range -= remove_from_index
            station.normal_equations.parameter_start_idx -= remove_from_index
            if stationID(station) == station_id:
                self.total_parameters -= station.normal_equations.parameter_count
                self.total_equations -= station.normal_equations.equation_count
                self.normal_equations.pop(i)
                self.stations.pop(i)
                remove_from_index = station.normal_equations.parameter_count

        # no need to rebuild the registry or the grids
        # they will contain a couple dead sites but they don't affect the calculations
        self.solved = False

    def _build_etm(self, cnn: Cnn, network_code: str, station_code: str,
                   json_folder: str = None, save_json_folder: str = None):

        loaded_from_json = False
        saved_obs_sigmas = None
        prefit: List[JumpFunction] = []
        etm = None

        # Check for a saved JSON first. The JSON was written after obs_sigmas restoration
        # (step 3 below), so it already carries the correct weights. Running the
        # unconstrained ETM against current DB data would produce an observation count
        # that may not match the JSON snapshot, causing a size mismatch in
        # _create_normal_equations. Skip steps 1 and 3 entirely for the JSON path.
        json_path = (os.path.join(json_folder, f'{network_code}.{station_code}_ppp.json')
                     if json_folder is not None else None)

        if json_path is not None and os.path.isfile(json_path):
            tqdm.write(f'Loading etm for {network_code}.{station_code} from json file')
            config = EtmConfig(json_file=json_path)
            etm = EtmEngine(config)
            loaded_from_json = True
        else:
            if json_folder is not None:
                tqdm.write(f'Could not find etm json for {network_code}.{station_code}, '
                           f'will try to use the database')

            # @todo: check why stations not getting the default relaxation from the etm_stacker config
            # STEP 1: Always run a plain unconstrained ETM (no cherry-picked events) first.
            # This gives unbiased obs_sigmas and detects any mechanical jumps for correction.
            try:
                unconstrained_config = EtmConfig(network_code, station_code, cnn=cnn)
                unconstrained_config.modeling.relaxation = np.array([np.max(self.config.relaxation)])
                etm_unconstrained = EtmEngine(unconstrained_config, cnn=cnn, silent=True)

                if (etm_unconstrained.solution_data.time_vector[-1] -
                        etm_unconstrained.solution_data.time_vector[0] <= 1.5):
                    tqdm.write(print_yellow(f' -- Station {network_code}.{station_code} has less than 1.5 '
                                            f'years of data, skipping'))
                    return None

                etm_unconstrained.run_adjustment(try_loading_db=False, force_computation=True,
                                                 try_save_to_db=False)
                etm_unconstrained.config.plotting_config.filename = \
                    f'./production/{network_code}.{station_code}_unconstrained'
                etm_unconstrained.config.plotting_config.plot_show_outliers = True
                etm_unconstrained.plot()
            except SolutionDataException as e:
                tqdm.write(print_yellow(str(e)))
                return None

            if etm_unconstrained.config.modeling.status == FitStatus.UNABLE_TO_FIT:
                tqdm.write(print_yellow(f' -- Unconstrained ETM for {network_code}.{station_code} '
                                        f'could not fit; skipping obs_sigmas restoration and '
                                        f'mechanical jump detection'))
            else:
                saved_obs_sigmas = [r.obs_sigmas.copy() for r in etm_unconstrained.fit.results]
                prefit = list(etm_unconstrained.jump_manager.get_active_mechanical_jumps())
                if prefit:
                    tqdm.write(f' -- Found {len(prefit)} mechanical jump(s) in {network_code}.{station_code}; '
                               f'will correct via prefit')

        try:
            if etm is None:
                tqdm.write(f'Estimating etm for {network_code}.{station_code}')
                config = EtmConfig(network_code, station_code, cnn=cnn)
                config.solution.solution_type = SolutionType.PPP
                config = self._apply_config(config, cnn)
                etm = EtmEngine(config, cnn=cnn, silent=True)

            # Apply mechanical jump prefit before running the adjustment
            if prefit:
                etm.config.modeling.status = FitStatus.PREFIT
                etm.config.modeling.prefit_models = copy.deepcopy(prefit)
                for j in etm.jump_manager.get_active_mechanical_jumps():
                    j.fit = False

            etm.run_adjustment(cnn=cnn, force_computation=not loaded_from_json,
                               try_loading_db=False, try_save_to_db=False)
            etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_stacker'
            etm.plot()
        except (DesignMatrixException, numpy.linalg.linalg.LinAlgError):
            tqdm.write(print_yellow(f' -- Unable to fit {network_code}.{station_code} -> system is rank deficient. '
                                    f'Will redo ETM with only 10 years of postseismic events.'))
            config.validation.max_condition_number = 3
            etm = EtmEngine(config, cnn=cnn, silent=True)
            if prefit:
                etm.config.modeling.status = FitStatus.PREFIT
                etm.config.modeling.prefit_models = copy.deepcopy(prefit)
                for j in etm.jump_manager.get_active_mechanical_jumps():
                    j.fit = False
            try:
                etm.run_adjustment(cnn=cnn)
            except Exception:
                tqdm.write(print_yellow(f' -- Unable to fit {network_code}.{station_code}. '
                                        f'Station will not be added.'))
                return None
        except SolutionDataException as e:
            tqdm.write(print_yellow(str(e)))
            return None

        if etm.config.modeling.status == etm.config.modeling.status.UNABLE_TO_FIT:
            tqdm.write(print_yellow(f' -- Unable to fit station {network_code}.{station_code}. '
                                    f'Retrying with zero-tied coseismic/postseismic constraints.'))
            # UNABLE_TO_FIT deactivates all design matrix functions (fit=False), so we inspect
            # jump_manager directly (ignoring fit state) to build the constraint list, then
            # recreate a fresh EtmEngine so the design matrix is rebuilt with everything active.
            zero_constraints = self._build_zero_tie_constraints(etm)
            if not zero_constraints:
                tqdm.write(print_yellow(f' -- No geophysical jumps to constrain for '
                                        f'{network_code}.{station_code}. Station will not be added.'))
                return None
            etm.config.modeling.least_squares_strategy.constraints = zero_constraints
            etm = EtmEngine(etm.config, cnn=cnn, silent=True)
            # prefit status and models are already carried in etm.config; only need to
            # deactivate the mechanical jumps in the freshly built jump_manager
            if prefit:
                for j in etm.jump_manager.get_active_mechanical_jumps():
                    j.fit = False
            try:
                etm.run_adjustment(cnn=cnn, try_loading_db=False, force_computation=True, try_save_to_db=False)
                etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_stacker'
                etm.plot()
            except Exception:
                tqdm.write(print_yellow(f' -- Still unable to fit {network_code}.{station_code} with constraints. '
                                        f'Station will not be added.'))
                return None
            if etm.config.modeling.status == etm.config.modeling.status.UNABLE_TO_FIT:
                tqdm.write(print_yellow(f' -- Still unable to fit {network_code}.{station_code} after constraints. '
                                        f'Station will not be added.'))
                return None

        if np.any([np.isnan(r.parameters) for r in etm.fit.results]):
            tqdm.write(print_yellow(f' -- Station {network_code}.{station_code} combined with the list of earthquakes '
                                    f'yielded a singular solution, station cannot be used'))
            return None

        # STEP 3: Restore obs_sigmas from the unconstrained run (when available).
        # The cherry-picked ETM may produce large residuals near geophysical events,
        # spuriously downweighting those observations in the stacker's normal equations.
        # The unconstrained run provides unbiased per-observation weights.
        if saved_obs_sigmas is not None:
            for i, r in enumerate(etm.fit.results):
                r.obs_sigmas = saved_obs_sigmas[i]

        if save_json_folder is not None and not loaded_from_json:
            if not os.path.exists(save_json_folder):
                os.makedirs(save_json_folder)
            etm.save_etm(
                save_json_folder + '/',
                dump_functions=True,
                dump_observations=True,
                dump_raw_results=True,
                dump_design_matrix=True
            )

        return etm

    def _apply_config(self, config: EtmConfig, cnn: Cnn):
        config.validation.max_condition_number = self.config.max_condition_number
        config.modeling.check_jump_collisions = False  # turn off jump collision check. Add all jumps.
        # Set an impossibly high magnitude limit so the ScoreTable SQL "mag >= limit" branch
        # never fires. Only events in earthquakes_cherry_picked (the pre-computed canonical
        # list) will be admitted via the "id IN cherry_picked" branch of the query.
        config.modeling.earthquake_magnitude_limit = 10
        config.modeling.post_seismic_back_lim = self.config.post_seismic_back_lim
        config.modeling.relaxation = self.config.relaxation
        config.modeling.earthquakes_cherry_picked = self.config.earthquakes_cherry_picked
        config.plotting_config.plot_show_outliers = True
        config.refresh_config(cnn)

        # @todo: implement a permanent fix for this. The clean solution is Option A:
        #   add a `force_relaxation: bool = False` flag to ModelingParameters
        #   (data_classes.py) and check it first in JumpFunction._setup_relaxation()
        #   (jumps.py) to skip the user_jumps lookup entirely. This would make the
        #   stacker's relaxation authoritative without mutating the loaded user_jumps.
        #
        # Workaround: refresh_config reloads per-jump relaxation from the DB via
        # user_jumps, which silently overrides config.modeling.relaxation in
        # JumpFunction._setup_relaxation(). Overwrite the relaxation field on all
        # geophysical user_jumps here so the stacker's values are actually used.
        for jp in config.modeling.user_jumps:
            if jp.jump_type >= JumpType.COSEISMIC_JUMP_DECAY:
                jp.relaxation = self.config.relaxation.copy()

        return config

    def prepare_earthquake_list(self, cnn: Cnn, stnlist: list) -> None:
        """
        Pre-compute the canonical earthquake list before any station ETMs are fitted.

        For each station in stnlist, queries ScoreTable with the configured
        magnitude_limit to find all events with s-score > 0.  Events provided
        via config.earthquakes_cherry_picked (--force) are merged on top without
        a magnitude-limit check (identical to per-station behaviour).

        The union is deduplicated by event ID, sorted, and collision-windowed using
        the same 10-day rule as _record_earthquakes.  Collision losers are excluded
        from cherry_picked entirely (they will not appear in any station ETM).
        Results are written to:
          - self.config.earthquakes_cherry_picked  (surviving event IDs only)
          - self.collided_earthquakes              (IDs of excluded losers, for bookkeeping)

        _record_earthquakes will therefore skip the collision step.
        """
        import math
        from datetime import datetime as _dt
        from ..core.s_score import ScoreTable
        from ..core.type_declarations import JumpType
        # date range for the scan
        if isinstance(self.config.post_seismic_back_lim, Date):
            sdate = self.config.post_seismic_back_lim
        else:
            # float = years back from station start; use conservative fallback
            sdate = Date(year=1975, doy=1)
        edate = Date(datetime=_dt.utcnow())

        seen_ids: set = set()
        candidate_events: List[Earthquake] = []
        # Maps event_id -> set of station IDs (net.code) that see the event.
        # Used by the collision check: two close events only collide if they
        # share at least one common station.
        event_stations: dict = {}

        tqdm.write(f'Pre-computing earthquake list for {len(stnlist)} stations '
                   f'(mag >= {self.config.earthquake_magnitude_limit})...')

        # s-score scan for every station
        for stn in stnlist:
            net, code = stn['NetworkCode'], stn['StationCode']
            rs = cnn.query_float(
                'SELECT lat, lon FROM stations '
                f'WHERE "NetworkCode" = \'{net}\' AND "StationCode" = \'{code}\'',
                as_dict=True
            )
            if not rs:
                tqdm.write(f'  WARNING: Could not get coordinates for {net}.{code}, skipping')
                continue
            lat, lon = float(rs[0]['lat']), float(rs[0]['lon'])

            score = ScoreTable(cnn, net, code, lat, lon, sdate, edate,
                               magnitude_limit=self.config.earthquake_magnitude_limit,
                               include_all_events=True)
            for eq in score.table:
                if eq.id not in seen_ids:
                    seen_ids.add(eq.id)
                    candidate_events.append(eq)
                event_stations.setdefault(eq.id, set()).add(f'{net}.{code}')

        tqdm.write(f'  s-score scan: {len(candidate_events)} unique events from '
                   f'{len(stnlist)} stations')

        # merge force (cherry-picked) events on top
        if self.config.earthquakes_cherry_picked:
            for eid in self.config.earthquakes_cherry_picked:
                if eid in seen_ids:
                    continue
                rows = cnn.query_float(
                    f"SELECT * FROM earthquakes WHERE id = '{eid}'", as_dict=True
                )
                if not rows:
                    tqdm.write(f'  WARNING: Cherry-picked event {eid} not found in database')
                    continue
                j = rows[0]
                has_fm = not math.isnan(float(j['strike1']))
                strike = [float(j['strike1']), float(j['strike2'])] if has_fm else []
                dip    = [float(j['dip1']),    float(j['dip2'])]    if has_fm else []
                rake   = [float(j['rake1']),   float(j['rake2'])]   if has_fm else []
                candidate_events.append(Earthquake(
                    id=j['id'],
                    lat=float(j['lat']), lon=float(j['lon']),
                    date=Date(datetime=j['date']),
                    depth=int(j['depth']),
                    magnitude=float(j['mag']),
                    location=j['location'],
                    strike=strike, dip=dip, rake=rake,
                    jump_type=JumpType.COSEISMIC_JUMP_DECAY
                ))
                seen_ids.add(eid)
                tqdm.write(f'  Added cherry-picked event {eid}')

        candidate_events.sort()

        # Exclude events with no focal mechanism — SW-Okada requires strike/dip/rake
        no_fm = [e for e in candidate_events if not e.strike]
        if no_fm:
            tqdm.write(f'  Excluding {len(no_fm)} event(s) with no focal mechanism: '
                       + ', '.join(e.id for e in no_fm))
            candidate_events = [e for e in candidate_events if e.strike]

        tqdm.write(f'  Total: {len(candidate_events)} candidate events')

        # collision detection: exclude the smaller event whenever two events are
        # within 15 days of each other, regardless of ordering.
        collision_window_days = 15
        for i, event_i in enumerate(candidate_events):
            for j, event_j in enumerate(candidate_events):
                if i >= j:
                    continue
                days_apart = abs(event_i.date.fyear - event_j.date.fyear) * 365.25
                if days_apart <= collision_window_days:
                    # Only a true collision when both events affect at least one
                    # common station.  Two events close in time but in different
                    # regions (no shared stations) are independent and should
                    # remain in the stacker separately.
                    shared = (event_stations.get(event_i.id, set()) &
                              event_stations.get(event_j.id, set()))
                    if not shared:
                        tqdm.write(
                            f'  Events {event_i.id} and {event_j.id} are '
                            f'{days_apart:.1f} days apart but have no common '
                            f'stations — not a collision'
                        )
                        continue

                    if event_i.magnitude >= event_j.magnitude:
                        loser, winner = event_j, event_i
                    else:
                        loser, winner = event_i, event_j

                    if loser.id not in self.collided_earthquakes:
                        tqdm.write(
                            f'WARNING: Earthquake collision detected between '
                            f'{event_i.id} (M{event_i.magnitude:.1f}, {event_i.date.yyyyddd():s}) and '
                            f'{event_j.id} (M{event_j.magnitude:.1f}, {event_j.date.yyyyddd():s}) '
                            f'({days_apart:.1f} days apart). '
                            f'Keeping {winner.id}, excluding {loser.id}.'
                        )
                        self.collided_earthquakes.add(loser.id)

        if self.collided_earthquakes:
            tqdm.write(f'  Collisions: {len(self.collided_earthquakes)} event(s) excluded: '
                       + ', '.join(sorted(self.collided_earthquakes)))

        # publish results — collision losers are excluded entirely, not zero-tied
        kept = [e.id for e in candidate_events if e.id not in self.collided_earthquakes]
        self.config.earthquakes_cherry_picked = kept
        tqdm.write(f'Earthquake list ready: {len(kept)} events '
                   f'({len(self.collided_earthquakes)} excluded due to collisions)')

    @staticmethod
    def _create_station(etm: EtmEngine):

        station = Station(
            etm.config.network_code,
            etm.config.station_code,
            etm.config.metadata.lon[0],
            etm.config.metadata.lat[0],
            etm.solution_data.coordinates.dates[0],
            etm
        )

        # figure out if station can participate on interseismic model
        jump = etm.jump_manager.get_first_geophysical()
        if jump is None or (jump is not None and jump.p.jump_date > station.first_obs):
            station.is_interseismic = True
            tqdm.write(f' -- Station {stationID(station)} is interseismic')

        return station

    def _build_zero_tie_constraints(self, etm) -> List:
        """Build soft zero-tie constraints for all geophysical jumps (coseismic and postseismic).

        Used when a station ETM returns UNABLE_TO_FIT to regularize the system so it can be
        solved. Parameters are tied to zero with the stacker's postseismic sigmas.
        Note: fit state is ignored because UNABLE_TO_FIT deactivates all design matrix functions.
        """
        constraints = []

        for jump in etm.jump_manager.jumps:
            if not jump.is_geophysical():
                continue

            jc = JumpFunction(etm.config, time_vector=np.array([0]),
                              date=jump.date, jump_type=jump.p.jump_type, fit=False)

            for j in range(3):
                sigma = 10 # self.config.postseismic_v_sigma if j == 2 else self.config.postseismic_h_sigma
                jc.p.params[j] = np.zeros(jc.param_count)
                jc.p.sigmas[j] = np.full(jc.param_count, sigma)

            constraints.append(jc)

        return constraints

    def _create_normal_equations(self, station: Station):
        """Analyze and save the relevant information."""

        # get the observations without the jumps
        l = station.etm.solution_data.transform_to_local()
        a = station.etm.design_matrix.matrix

        n = []
        c = []

        if station.etm.solution_data.solutions < 100:
            tqdm.write(f' -- Upweighting {stationID(station)} because observations count is < 100')
            weight_scale = self.config.station_weight_scale * 100.
        else:
            weight_scale = self.config.station_weight_scale

        lpl = []
        observation_weights = []
        prior_wrms = []
        # rearrange the NEU to ENU
        for i in [1, 0, 2]:
            p = np.diag(1 / station.etm.fit.results[i].obs_sigmas ** 2) * weight_scale
            n.append(a.T @ p @ a)
            c.append(a.T @ p @ l[i])
            lpl.append(l[i].T @ p @ l[i])
            observation_weights.append(1 / station.etm.fit.results[i].obs_sigmas ** 2)
            prior_wrms.append(station.etm.fit.results[i].wrms)

        neq = NormalEquations(
            station=stationID(station),
            neq=n, ceq=c,
            design_matrix=a,
            observation_vector=[l[1], l[0], l[2]],
            weighted_observations=lpl,
            observation_weights=observation_weights,
            weight_scale=weight_scale, dof=a.shape[0] - a.shape[1],
            parameter_count=a.shape[1],
            equation_count=a.shape[0],
            parameter_start_idx=self.total_parameters,
            parameter_range=np.arange(a.shape[1]) + self.total_parameters,
            prior_wrms=prior_wrms
        )
        # save both vectors to the station
        station.normal_equations = neq

        self.total_parameters += a.shape[1]
        self.total_equations += a.shape[0]

        return neq

    def build_system(self):
        """Build the complete stacking system."""
        # 1. Create grids
        self.grids = GridSystem.create_from_stations(self.stations,
                                                     grid_spacing=self.config.grid_spacing,
                                                     grid_load_radius=self.config.vertical_load_radius,
                                                     method=self.config.vertical_method,
                                                     tension=self.config.tension)

        # 3. Register all constraints
        self._register_constraints()

    def change_station_weight(self, station_id: str, new_weight: float, silent=False):

        found = False
        for neq in self.normal_equations:
            if neq.station == station_id:
                if not silent:
                    tqdm.write(f'Found {station_id} with weight {neq.weight_scale}, '
                               f'updating to {new_weight}')
                for i in range(3):
                    neq.ceq[i] = neq.ceq[i] / neq.weight_scale * new_weight
                    neq.neq[i] = neq.neq[i] / neq.weight_scale * new_weight
                    neq.weighted_observations[i] = neq.weighted_observations[i] / neq.weight_scale * new_weight

                neq.weight_scale = new_weight

                found = True

        if found and not silent:
            tqdm.write('Do not forget to invoke solve again!')

    def solve(self, interpolate_fields=True) -> Tuple[List, List]:
        """
        Solve the stacking system.
        """
        # rebuild normal equations before solving
        system_neq, system_ceq = self._build_base_normal_equations()

        # collect constraints. Weight changes take effect here
        self.constraint_registry.collect_all_constraints(
            self.stations, self.total_parameters, self.grids,
            earthquakes=self.earthquakes
        )

        # Solve: Apply constraints to system and do not modify original NEQs
        self.total_constraints = self.constraint_registry.add_all_constraints(
            system_neq, self.total_parameters
        )

        tqdm.write('Solving system...')

        x = np.linalg.solve(system_neq, system_ceq)
        self.solution = np.reshape(x, (3, self.total_parameters))
        # compute covariance for the entire system
        self.covariance = np.linalg.inv(system_neq)

        # compute the variance of unit weight
        lpl = sum(stn.normal_equations.weighted_observations[0] +
                  stn.normal_equations.weighted_observations[1] +
                  stn.normal_equations.weighted_observations[2] for stn in self.stations)

        dof = self.total_equations * 3 + self.total_constraints - (self.total_parameters * 3)
        c_vpv = self._sum_constraint_weighted_residuals()
        o_vpv = lpl - system_ceq.T @ x

        # see Kyle Snow eq 6.36 and 6.37a (y^T P y − c^T N^−1 c)
        self.variance = (o_vpv + c_vpv) / dof
        # update the covariance
        self.covariance *= self.variance
        # compute variance of unit weight for each stations
        increment = []
        for stn in self.stations:
            # add constraints to each station
            stn.extract_etm_constraints(self.earthquakes, self.config.relaxation,
                                        self.solution, self.covariance)
            # access normal equations
            neq = stn.normal_equations
            stn.posterior_wrms = []
            wrms_increment = []
            for i in range(3):
                # compute residuals for station
                x = self.solution[i, stn.normal_equations.parameter_range]
                v = neq.observation_vector[i] - neq.design_matrix @ x
                wrms_increment.append(np.sqrt(
                    v.T @ np.diag(neq.observation_weights[i]) @ v / stn.normal_equations.dof)
                )
                stn.posterior_wrms.append(
                    neq.prior_wrms[i] * wrms_increment[i]
                )
            wrms_increment = np.array(wrms_increment)

            increment.append([stationID(stn), np.mean(wrms_increment), wrms_increment])

        from operator import itemgetter
        tqdm.write('WRMS increment for each station:')
        for stn, wrmsi, wrms in increment:
            tqdm.write(f'{stn} WRMS increment: (total={wrmsi:.2f}) {wrms[0]:.2f} {wrms[1]:.2f} {wrms[2]:.2f}')

        tqdm.write('First five largest WRMS increments:')
        c = 0
        for stn, wrmsi, wrms in sorted(increment, key=itemgetter(1), reverse=True):
            if c == 6:
                break
            tqdm.write(f'{stn} WRMS increment: (total={wrmsi:.2f}) {wrms[0]:.2f} {wrms[1]:.2f} {wrms[2]:.2f}')
            c += 1

        tqdm.write(f'Equations: {self.total_equations * 3}')
        tqdm.write(f'Constraints: {self.total_constraints}')
        tqdm.write(f'Parameters: {self.total_parameters * 3}')
        tqdm.write(f'Sum of squared residuals (obs): {o_vpv:.3f}')
        tqdm.write(f'Sum of squared residuals (con): {c_vpv:.3f}')
        tqdm.write(f'Model redundancy: {dof}')
        tqdm.write(f'SQRT(var) for the stacked system: {np.sqrt(self.variance):.3f} ')
        tqdm.write(f'1/var for the stacked system: {1/self.variance:.4f} ')

        self.solved = True

        if interpolate_fields:
            self.interpolate_fields_to_grid()

        # Extract results
        return self._extract_results()

    def _sum_constraint_weighted_residuals(self):

        # Count active equations first
        n_active = self.total_constraints
        # Pre-allocate
        k = np.zeros((n_active, self.total_parameters * 3))
        idx = 0

        for constraint_type in self.constraint_registry.constraints.keys():
            for const in self.constraint_registry.constraints[constraint_type]:
                for eq in [e for e in const.equations if e.is_active]:
                    # Build K matrix for this constraint
                    ke, kn, ku = eq.constraint_design
                    se, sn, su = eq.constraint_sigma
                    # do not square! will get squared when doing v.T @ v
                    k[idx:idx + 3, :] = np.vstack((ke * (1 / se), kn * (1 / sn), ku * (1 / su)))
                    idx += 3
        # the - comes from z0 − K ξ̂ but in this case, z0 = 0 (see Snow 6.38)
        v = -k @ self.solution.flatten()

        return v.T @ v

    def constraints_rms(self):
        """
        Take the registered constraints and find their rms values.
        """
        from operator import itemgetter

        wrms = []
        for constraint_type in self.constraint_registry.constraints.keys():
            for const in self.constraint_registry.constraints[constraint_type]:
                v = np.array([])
                v_eq = []
                for eq in [e for e in const.equations if e.is_active]:
                    # get design and weight matrix
                    ke, kn, ku = eq.constraint_design
                    # do not square! will get squared when doing v.T @ v
                    r = np.vstack((ke, kn, ku)) @ self.solution.flatten()
                    v_eq.append([stationID(eq.station), r, np.sqrt((r.T @ r) / 2)])
                    v = np.concatenate((v, r))

                n = len(const.equations)
                # compute the wrms residuals
                if n > 0:
                    wrms.append([const, n, np.sqrt((v.T @ v) / (n - 1)),
                                 sorted(v_eq, key=itemgetter(2), reverse=True)])

        return sorted(wrms, key=itemgetter(2), reverse=True)

    def _register_constraints(self):
        """Register all constraint types."""
        # Interseismic
        self.constraint_registry.add_constraint(
            InterseismicConstraint(
                self.stations,
                self.config.interseismic_h_sigma,
                self.config.interseismic_v_sigma
            )
        )

        # record all earthquakes that might affect the ETMs
        self._record_earthquakes()

        # Coseismic and postseismic for each earthquake
        for event in self.earthquakes:
            # Skip collided earthquakes - they will be constrained to zero
            if event.id in self.collided_earthquakes:
                tqdm.write(f'Setting number of stations for collided earthquake {event.id} to zero.')

            # coseismic stations
            stations = [stn for stn in self.stations if stn.get_coseismic_column(event.id) is not None]
            # compute fault_geometry for this event (will use co or postseismic stations)
            fault_geometry = FaultGeometry(event, self.stations, np.max(self.config.relaxation), self.grids)

            if len(stations):
                coseis = CoseismicConstraint(
                    event, fault_geometry, stations, self.grids,
                    self.config.coseismic_h_sigma,
                    self.config.coseismic_v_sigma,
                    is_collision=event.id in self.collided_earthquakes
                )
                self.constraint_registry.add_constraint(coseis)
            else:
                tqdm.write(f'No stations observed coseismic event {event.id}. '
                           f'A coseismic constraint for this event will not be added.')

            # Postseismic for each relaxation
            for relax in self.config.relaxation:
                # Build a FaultGeometry from postseismic stations if coseismic had none
                stations = [stn for stn in self.stations if stn.get_postseismic_column(event.id, relax) is not None]

                if not stations:
                    tqdm.write(f'No stations for postseismic event {event.id} relax={relax:.3f}. '
                               f'Skipping postseismic constraint.')
                    continue

                self.constraint_registry.add_constraint(
                    PostseismicConstraint(
                        event, fault_geometry, stations, relax, self.grids,
                        self.config.postseismic_h_sigma,
                        self.config.postseismic_v_sigma,
                        is_collision=event.id in self.collided_earthquakes
                    )
                )

    def _record_earthquakes(self):

        # open connection to database
        cnn = Cnn('gnss_data.cfg')

        for stn in self.stations:
            for jump in [jump for jump in stn.etm.jump_manager.jumps
                         if jump.is_geophysical() and jump.fit]:

                if jump.earthquake is None:
                    tqdm.write(f'Could not identify earthquake ID for station '
                               f'{stationID(stn)} for jump date {jump.date}')
                    continue

                if jump.earthquake not in self.earthquakes:
                    tqdm.write('Recording event ' + repr(jump))
                    self.earthquakes.append(jump.earthquake)
                    self.earthquakes.sort()

                    lon, lat = self.grids.interpolation_geographic

                    # save a mask for the event
                    mask = Mask(cnn, jump.earthquake.id)
                    s_score, p_score = mask.score(lat, lon)

                    # tqdm.write(f'Getting mask for event {jump.earthquake.id}')
                    s_score = s_score > 0
                    p_score = p_score > 0
                    # save the actual object to query it
                    self.grids.earthquake_masks[jump.earthquake.id] = (s_score, p_score, mask)

        # Collision detection was already performed in prepare_earthquake_list.
        # self.collided_earthquakes is pre-populated; no action needed here.

    def _build_base_normal_equations(self) -> Tuple[np.ndarray, np.ndarray]:
        """Build the base NEQ from individual stations."""

        tqdm.write('Building station system of normal equations')

        tp = self.total_parameters

        system_neq = np.zeros((tp * 3, tp * 3))
        system_ceq = np.zeros((tp * 3,))

        offset = 0
        for neq in self.normal_equations:
            n_params = neq.parameter_count

            for i in range(3):
                neq_comp = neq.neq[i]
                ceq_comp = neq.ceq[i]

                system_neq[
                    i * tp + offset:i * tp + offset + n_params,
                    i * tp + offset:i * tp + offset + n_params] = neq_comp

                system_ceq[
                    i * tp + offset:i * tp + offset + n_params] = ceq_comp

            offset += n_params

        self.solved = False

        return system_neq, system_ceq

    def add_earthquake(self, event: Earthquake, json_folder: str = None, save_json_folder: str = None):
        """Add event to the list of modeled earthquakes."""

        # check the event is not already in the list
        if event in self.earthquakes:
            tqdm.write(f'Event {event.id} is already in the list of modeled events')
            return

        tqdm.write('Adding event ' + str(event))
        self.earthquakes.append(event)
        self.config.earthquakes_cherry_picked.append(f'{event.id}')
        self.earthquakes.sort()

        # get the mask
        # open connection to database
        cnn = Cnn('gnss_data.cfg')
        lon, lat = self.grids.interpolation_geographic

        # save a mask for the event
        mask = Mask(cnn, event.id)
        s_score, p_score = mask.score(lat, lon)

        tqdm.write(f'Getting mask for event {event.id}')
        s_score = s_score > 0
        p_score = p_score > 0
        # save the actual object to query it
        self.grids.earthquake_masks[event.id] = (s_score, p_score, mask)

        # figure out which stations need to be recomputed
        for i, stn in enumerate(self.stations):
            s_score, p_score = mask.score(stn.lat, stn.lon)
            if p_score > 0 or s_score > 0:
                tqdm.write(f'Recomputing etm for {stationID(stn)}')
                # replace old etm
                stn.etm = self._build_etm(cnn, stn.network_code, stn.station_code, json_folder, save_json_folder)
                # get dimensions of neq
                new_par_count = stn.etm.design_matrix.matrix.shape[1]
                # assume number of unknowns will change, so update the rest of the stations down from current
                remove_from_index = 0
                # will be added when calling _create_normal_equations
                self.total_parameters = 0
                self.total_equations = 0

                for station in self.stations:
                    if stationID(station) == stationID(stn):
                        # add the number of new parameters to remove_from_index
                        remove_from_index = station.normal_equations.parameter_count - new_par_count
                        # create a new normal equations object and replace current
                        self.normal_equations[i] = self._create_normal_equations(stn)
                    else:
                        # remove from the parameter range the station that has been removed
                        # this is applied to any stations that come after the removed site
                        station.normal_equations.parameter_range -= remove_from_index
                        station.normal_equations.parameter_start_idx -= remove_from_index
                        self.total_parameters += station.normal_equations.parameter_count
                        self.total_equations += station.normal_equations.equation_count

        # now add the constraint
        fault_geometry = FaultGeometry(event, self.stations, np.max(self.config.relaxation), self.grids)

        stations = [stn for stn in self.stations if stn.get_coseismic_column(event.id) is not None]
        if len(stations):
            coseis = CoseismicConstraint(
                event, fault_geometry, stations, self.grids,
                self.config.coseismic_h_sigma,
                self.config.coseismic_v_sigma,
                is_collision=event.id in self.collided_earthquakes
            )
            self.constraint_registry.add_constraint(coseis)
        else:
            tqdm.write(f'No stations observed coseismic event {event.id}. '
                       f'A coseismic constraint for this event will not be added.')

        # Postseismic for each relaxation
        for relax in self.config.relaxation:
            stations = [stn for stn in self.stations if stn.get_postseismic_column(event.id, relax) is not None]

            if not stations:
                tqdm.write(f'No stations for postseismic event {event.id} relax={relax:.3f}. '
                           f'Skipping postseismic constraint.')
                continue

            self.constraint_registry.add_constraint(
                PostseismicConstraint(
                    event, fault_geometry, stations, relax, self.grids,
                    self.config.postseismic_h_sigma,
                    self.config.postseismic_v_sigma,
                    is_collision=event.id in self.collided_earthquakes
                )
            )

    def remove_earthquake(self, event: Earthquake, json_folder: str = None, save_json_folder: str = None):
        """Remove event from the list of modeled earthquakes."""

        # check the event is not already in the list
        if event in self.earthquakes:
            tqdm.write('Removing event ' + str(event))

            self.earthquakes.pop(self.earthquakes.index(event))
            self.config.earthquakes_cherry_picked.pop(self.config.earthquakes_cherry_picked.index(f'{event.id}'))
            self.earthquakes.sort()

            cnn = Cnn('gnss_data.cfg')

            # remove mask
            _, _, mask = self.grids.earthquake_masks[event.id]
            self.grids.earthquake_masks.pop(event.id)

            # figure out which stations need to be recomputed
            for i, stn in enumerate(self.stations):
                s_score, p_score = mask.score(stn.lat, stn.lon)
                if p_score > 0 or s_score > 0:
                    # replace old etm
                    stn.etm = self._build_etm(cnn, stn.network_code, stn.station_code, json_folder, save_json_folder)
                    # get dimensions of neq
                    new_par_count = stn.etm.design_matrix.matrix.shape[1]
                    # assume number of unknowns will change, so update the rest of the stations down from current
                    remove_from_index = 0
                    # will be added when calling _create_normal_equations
                    self.total_parameters = 0
                    self.total_equations = 0

                    for station in self.stations:
                        if stationID(station) == stationID(stn):
                            # add the number of new parameters to remove_from_index
                            remove_from_index = station.normal_equations.parameter_count - new_par_count
                            # create a new normal equations object and replace current
                            self.normal_equations[i] = self._create_normal_equations(stn)
                        else:
                            # remove from the parameter range the station that has been removed
                            # this is applied to any stations that come after the removed site
                            station.normal_equations.parameter_range -= remove_from_index
                            station.normal_equations.parameter_start_idx -= remove_from_index
                            self.total_parameters += station.normal_equations.parameter_count
                            self.total_equations += station.normal_equations.equation_count

            # now remove the constraints
            for i, constraint in enumerate(self.constraint_registry.constraints['coseismic']):
                if constraint.event.id == event.id:
                    tqdm.write(f'Removed {constraint}')
                    self.constraint_registry.constraints['coseismic'].pop(i)
                    break

            # remove the as many postseismic constraints as we have
            while event.id in [c.event.id for c in self.constraint_registry.constraints['postseismic']]:
                for i, constraint in enumerate(self.constraint_registry.constraints['postseismic']):
                    if constraint.event.id == event.id:
                        tqdm.write(f'Removed {constraint}')
                        self.constraint_registry.constraints['postseismic'].pop(i)
                        break

        else:
            tqdm.write(f'Event {event.id} is not in the list of modeled events')

    def get_constraint_summary(self) -> Dict:
        """Get summary of constraint system."""
        return self.constraint_registry.get_constraint_summary()

    def update_weights(self, event_id: str = None, relax: float = None, constraint_type: str = None,
                       h_sigma: float = None, v_sigma: float = None):
        """
        Update weights for specific constraint type or all constraints.
        """
        apply_to = []

        if constraint_type and not event_id:
            # only constraint type was given
            apply_to = self.constraint_registry.constraints[constraint_type]
        elif constraint_type and event_id and relax:
            # constraint type, event and relax
            for constraint in self.constraint_registry.constraints[constraint_type]:
                if constraint.constraint_type in (ConstraintType.COSEISMIC, ConstraintType.POSTSEISMIC):
                    if constraint.event.id == event_id and constraint.relaxation == relax:
                        apply_to += [constraint]
        elif constraint_type and event_id and not relax:
            # no relax
            for constraint in self.constraint_registry.constraints[constraint_type]:
                if constraint.constraint_type in (ConstraintType.COSEISMIC, ConstraintType.POSTSEISMIC):
                    if constraint.event.id == event_id:
                        apply_to += [constraint]
        elif event_id and relax and not constraint_type:
            # event and relax but no constraint type (but implicitly is postseismic)
            for constraint in self.constraint_registry.constraints['postseismic']:
                if constraint.event.id == event_id and constraint.relaxation == relax:
                    apply_to += [constraint]
        elif event_id and not constraint_type and not relax:
            # only event id
            for constraint in (self.constraint_registry.constraints['coseismic'] +
                               self.constraint_registry.constraints['postseismic']):
                if constraint.event.id == event_id:
                    apply_to += [constraint]
        else:
            # nothing given, apply to all
            apply_to = self.constraint_registry.constraints.values()

        # do the thing
        for constraint in apply_to:
            tqdm.write(f'Updating sigma for {constraint.short_description()}: '
                       f'h={constraint.h_sigma*1000:.2f}mm -> {h_sigma*1000:.2f}mm  '
                       f'v={constraint.v_sigma*1000:.2f}mm -> {v_sigma*1000:.2f}mm')
            constraint.update_weights(h_sigma, v_sigma)

        if not apply_to:
            tqdm.write('update_weights: no matching constraints found')

        self.solved = False

    def update_okada_weights(self, h_weight: float, v_weight: float,
                              event_id: str = None, relax: float = None) -> int:
        """
        Update SW-Okada regularization weights on coseismic/postseismic constraints.

        Sets _sw_okada_h_weight and _sw_okada_v_weight on all matching constraints.
        The LOO coefficient cache is cleared automatically on the next solve() call.

        Parameters
        ----------
        h_weight : float
            New horizontal Okada regularization weight.
        v_weight : float
            New vertical Okada regularization weight.
        event_id : str, optional
            If given, only constraints for this earthquake are updated.
        relax : float, optional
            If given together with event_id, only the postseismic constraint with
            this relaxation constant is updated.

        Returns
        -------
        int
            Number of constraints updated.
        """
        apply_to = []
        for constraint in (self.constraint_registry.constraints['coseismic'] +
                           self.constraint_registry.constraints['postseismic']):
            if event_id and constraint.event.id != event_id:
                continue
            if relax is not None and getattr(constraint, 'relaxation', None) != relax:
                continue
            apply_to.append(constraint)

        for constraint in apply_to:
            tqdm.write(f'Updating Okada weights for {constraint.short_description()}: '
                       f'h={constraint.sw_okada_h_weight}->{h_weight}  '
                       f'v={constraint.sw_okada_v_weight}->{v_weight}')
            constraint.sw_okada_h_weight = h_weight
            constraint.sw_okada_v_weight = v_weight
            constraint.dislocation_model = None

        if not apply_to:
            tqdm.write('update_okada_weights: no matching constraints found')

        self.solved = False
        return len(apply_to)

    def plot_grid_result(self, sigmas=False):

        input_names = [stationID(stn) for stn in self.stations]
        input_lon = [stn.lon for stn in self.stations]
        input_lat = [stn.lat for stn in self.stations]

        available_fields, station_data, grid_lon, grid_lat, fields, fcovar = [], [], [], [], [], []

        postseismic = []
        for ifield in self.fields:
            if ifield.base_type == ConstraintType.POSTSEISMIC:
                if ifield.event.id in postseismic:
                    # field already in
                    continue

            parameters = np.zeros((3, len(self.stations)))
            station_data.append(parameters)
            available_fields.append(ifield.description)
            lon, lat = ifield.get_interpolation_grid_geographic()
            grid_lon.append(lon)
            grid_lat.append(lat)
            if sigmas:
                # do not plot data from stations in uncertainty mode
                fields.append(ifield.enu_sigma)
                fcovar.append(ifield.enu_covar)
            else:
                if ifield.base_type == ConstraintType.POSTSEISMIC:
                    r_field = 0
                    for f in [ff for ff in self.fields if ff.base_type == ConstraintType.POSTSEISMIC]:
                        if ifield.event == f.event:
                            idx = np.isin(np.array([stationID(stn) for stn in self.stations]),
                                          np.array([stationID(stn) for stn in f.constrain_stations]))
                            # pick the max relaxation and use it a dt
                            r_field += f.enu_field * np.log10(1 + self.config.relaxation.max()/f.relaxation)
                            # assign values to where they belong
                            parameters[:, idx] += (f.constrained_parameters *
                                                   np.log10(1 + self.config.relaxation.max()/f.relaxation))
                    # append postseismic field to keep track of which earthquakes were processed already
                    postseismic.append(ifield.event.id)
                    fields.append(r_field)
                else:
                    idx = np.isin(np.array([stationID(stn) for stn in self.stations]),
                                  np.array([stationID(stn) for stn in ifield.constrain_stations]))
                    # assign values to where they belong
                    parameters[:, idx] = ifield.constrained_parameters
                    fields.append(ifield.enu_field)

        # do the thing
        return plot_velocity_field(grid_lon, grid_lat, fields,
                                   np.array(input_lon), np.array(input_lat), station_data, input_names,
                                   self.plot_constrained_etm, available_fields, plot_sigmas=sigmas,
                                   covar=fcovar)

    def plot_constrained_etm(self, station_index, folder=None):
        cnn = Cnn('gnss_data.cfg')

        stn = self.stations[station_index]

        tqdm.write(f'Estimating constrained etm for {stationID(stn)}')

        config = EtmConfig(stn.network_code, stn.station_code, cnn=cnn)
        config = self._apply_config(config, cnn)
        config.solution.solution_type = SolutionType.PPP
        config.modeling.least_squares_strategy.constraints = stn.etm_constraints
        # add the prefit models that got removed from the ETM when we did the stack
        config.modeling.prefit_models = copy.deepcopy(stn.etm.config.modeling.prefit_models)

        for const in config.modeling.least_squares_strategy.constraints:
            par = ''
            for p in const.p.params:
                par += '[' + ' '.join([f'{a * 1000.:.2f}' for a in p.tolist()]) + '] '

            tqdm.write(f' -- Etm constrain: {const} {par}')

        if folder is None:
            config.plotting_config.interactive = True
        else:
            config.plotting_config.filename = folder

        config.plotting_config.plot_show_outliers = True
        config.plotting_config.plot_residuals_mode = True
        etm = EtmEngine(config, cnn=cnn, silent=True)

        # deactivate mechanical jumps jumps
        mechanical = etm.jump_manager.get_active_mechanical_jumps()
        for jump in mechanical:
            jump.fit = False

        etm.run_adjustment(cnn=cnn, try_save_to_db=False, try_loading_db=False)
        etm.plot()
        print('(EtmStacker) > ', end='', flush=True)

    def _extract_results(self) -> Tuple[List, List]:

        interseismic = []
        earthquakes = []
        for stn in self.stations:
            ap = stn.etm.design_matrix.get_polynomial().p.params
            idx = stn.get_velocity_column()
            interseismic.append({
                'station': stationID(stn),
                'lon': stn.lon,
                'lat': stn.lat,
                'a_priori': [ap[1][1], ap[0][1], ap[2][1]],
                'constrained': self.solution[:, idx].tolist(),
                'is_interseismic': stn.is_interseismic
            })
            for event in self.earthquakes:
                idx = stn.get_coseismic_column(event.id)
                if idx:
                    ap = stn.etm.jump_manager.get_geophysical_jump(event.id).p.params
                    earthquakes.append({
                        'station': stationID(stn),
                        'lon': stn.lon,
                        'lat': stn.lat,
                        'event_id': event.id,
                        'relax': 0.0,
                        'a_priori': [ap[1], ap[0], ap[2]],
                        'constrained': self.solution[:, idx].tolist(),
                    })
                for relax in self.config.relaxation:
                    idx = stn.get_postseismic_column(event.id, relax)
                    if idx is not None:
                        jump = stn.etm.jump_manager.get_geophysical_jump(event.id)
                        ap = jump.p.params
                        col = jump.get_relaxation_cols(relax, False)
                        earthquakes.append({
                            'station': stationID(stn),
                            'lon': stn.lon,
                            'lat': stn.lat,
                            'event_id': event.id,
                            'relax': relax,
                            'a_priori': [ap[1][col], ap[0][col], ap[2][col]],
                            'constrained': self.solution[:, idx].tolist(),
                        })

        return interseismic, earthquakes

    def interpolate_fields_to_grid(self):

        if self.solved:
            # clean any previous runs
            self.fields = []

            # iterate through the constraints
            constraint_types = self.constraint_registry.constraints.values()
            for constraint_type in constraint_types:
                for constraint in constraint_type:
                    if constraint.is_collision:
                        tqdm.write(f'Skipping field interpolation for collided event {constraint.event.id} '
                                   f'(parameters are zero-tied)')
                        continue

                    if (constraint.constraint_type != ConstraintType.INTERSEISMIC and
                            getattr(constraint, 'dislocation_model', None) is None):
                        tqdm.write(f'Skipping field interpolation for {constraint.short_description()} '
                                   f'(zero-constrained: no SW-Okada model was built)')
                        continue

                    self.fields.append(
                        EtmStackerField.create_field(
                            self.stations, self.solution, self.covariance, self.grids, constraint)
                    )

        else:
            tqdm.write('System has not been solved! Invoke solve first')

    def get_trajectory_functions_at_point(self, lon: float, lat: float, etm: EtmEngine):
        pass
