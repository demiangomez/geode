"""
ETM Stacker main class.
"""

import logging
from typing import List, Dict, Set
import numpy as np
from tqdm import tqdm

from . import console
from . import etm_builder
from .data_classes import (
    EtmStackerConfig, NormalEquations, Station, EtmStackerField
)
from .earthquake_catalog import EarthquakeCatalogMixin
from .exceptions import EtmStackerException
from .normal_equations import NormalEquationsMixin
from .solver import SolverMixin
from .diagnostics import DiagnosticsMixin
from .grid_system import GridSystem
from .types import ConstraintType
from .constraints import ConstraintRegistry
from .constraints import sparse as sparse_constraint
from ..core.etm_engine import EtmEngine
from ..core.data_classes import Earthquake
from ...dbConnection import Cnn
from ...Utils import stationID, print_yellow

logger = logging.getLogger(__name__)


class EtmStacker(EarthquakeCatalogMixin, NormalEquationsMixin, SolverMixin, DiagnosticsMixin):
    """Orchestrates station/earthquake bookkeeping, the stacking solve, and diagnostics.

    The actual work is split across sibling modules, each mixed in or called explicitly:
      - etm_builder.py: per-station ETM construction (called directly, not mixed in --
        it's a plain function of its inputs, with no `self` access, so it can be handed
        to a multiprocessing pool one station at a time -- see add_stations_parallel).
      - earthquake_catalog.py (EarthquakeCatalogMixin): which earthquakes are modeled and
        the constraints that follow from them.
      - normal_equations.py (NormalEquationsMixin): per-station normal equations and their
        assembly into the system.
      - solver.py (SolverMixin): the linear solve and result extraction.
      - diagnostics.py (DiagnosticsMixin): interactive/plotting helpers.
    """

    def __init__(self, config: EtmStackerConfig = None):

        # Core data
        self.stations: List[Station] = []
        self.sparse_stations: List[Station] = []
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
        # True when the station set has changed since build_system last ran, so the
        # grids and constraints no longer match self.stations. Every station's
        # vertical_response is produced by one SVD over the whole station set, so a
        # membership change invalidates all of them at once -- solving in that state
        # fails inside the vertical interpolant with a shape mismatch rather than
        # quietly carrying a dead site, which is why solve() refuses instead.
        self.system_stale: bool = False
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
        """Report the settings this run will use, as the run's opening phase."""
        from ...pyDate import Date

        console.phase('configuration')
        if self.filename:
            console.result('Session', self.filename)
        console.result('Earthquake magnitude limit', f'{self.config.earthquake_magnitude_limit}')
        if isinstance(self.config.post_seismic_back_lim, Date):
            console.result('Postseismic events considered from',
                           self.config.post_seismic_back_lim.yyyyddd())
        else:
            console.result('Postseismic events considered back',
                           f'{self.config.post_seismic_back_lim / 365:.1f} yr from station start')
        console.result('Postseismic separability limit',
                       f'{self.config.postseismic_min_separability:g}')
        console.result('Relaxation constants [yr]',
                       ', '.join(f'{r:.3f}' for r in self.config.relaxation))
        if self.config.earthquakes_cherry_picked:
            console.result('Cherry-picked events',
                           f'{len(self.config.earthquakes_cherry_picked)}')
        console.result('Max condition number [log10]', f'{self.config.max_condition_number}')

        console.result('Interseismic sigmas h/v [mm/yr]',
                       f'{self.config.interseismic_h_sigma * 1000.:g} / '
                       f'{self.config.interseismic_v_sigma * 1000.:g}')
        console.result('Coseismic sigmas h/v [mm]',
                       f'{self.config.coseismic_h_sigma * 1000.:g} / '
                       f'{self.config.coseismic_v_sigma * 1000.:g}')
        console.result('Postseismic sigmas h/v [mm]',
                       f'{self.config.postseismic_h_sigma * 1000.:g} / '
                       f'{self.config.postseismic_v_sigma * 1000.:g}')
        console.result('Station weight scale', f'{self.config.station_weight_scale:g}')

        console.result('Vertical interpolation', self.config.vertical_method)
        if self.config.vertical_method == 'spline2d':
            console.result('Spline2d tension', f'{self.config.tension:g}')
        else:
            console.result('Vertical load radius [km]', f'{self.config.vertical_load_radius:g}')

        console.result('Station inclusion',
                       f'>= {self.config.min_time_span:g} yr span, '
                       f'>= {self.config.min_span_completion:g}% complete')
        console.result('Sparse-station constraints',
                       'on' if self.config.sparse_constraints_enabled else 'off')
        console.result('Adaptive constraint weighting',
                       f'on, target ratio {self.config.adaptive_constraint_target_ratio:g}'
                       if self.config.adaptive_constraint_weighting_enabled else 'off')
        console.result('GLS for every station',
                       'on' if self.config.gls_for_all_stations else 'off')
        console.result('Parallel workers', f'{self.config.parallel_workers}')

    def add_station(self, cnn: Cnn, network_code: str, station_code: str,
                    json_folder: str = None,
                    save_json_folder: str = None):
        """Build one station's ETM and fold it into the stack.

        Leaves the system stale: the new station has no vertical_response yet (that is
        computed for the whole set at grid-build time) and no constraint references it.
        Call build_system again before solving -- EtmStackerShell.do_add_station does
        both.
        """
        # Build ETM
        # unconstrained_etm (Step 1's own fit, robust-least-squares) isn't used here: a
        # sparse station gets its own dedicated, GLS-fit free fit instead -- see below.
        etm, _, reason = etm_builder.build_etm(self.config, cnn, network_code, station_code,
                                               json_folder, save_json_folder)
        if etm is None:
            return {'station': f'{network_code}.{station_code}', 'role': 'dropped',
                    'reason': reason, 'span': 0., 'completion': 0., 'nobs': 0}
        record = self._add_built_station(etm, network_code, station_code)
        self.system_stale = True
        self.solved = False
        return record

    def add_stations_parallel(self, cnn: Cnn, stnlist: List[dict],
                              json_folder: str = None, save_json_folder: str = None):
        """Same end result as calling add_station for every entry in stnlist, but builds
        each station's ETM (etm_builder.build_etm -- the slow DB-fetch + least-squares
        step, especially with config.gls_for_all_stations) across
        config.parallel_workers worker processes instead of one at a time, using plain
        local multiprocessing (not com/PlotETM.py's dispy/pyJobServer cluster: that
        pattern dispatches to whatever gnss_data.cfg's node_list says, which here is a
        separate machine set up for something else entirely -- this is "all available
        cores" of the machine the stacker itself is running on).

        Folding a built ETM into self.stations/self.sparse_stations/
        self.normal_equations/self.total_parameters always happens here, serially, in
        this (the main) process -- see etm_builder's own module docstring on why that
        split (worker builds, caller folds) is deliberate. pool.imap (not
        imap_unordered) preserves stnlist's order, so which stations end up in
        self.stations and their parameter_range assignment is identical regardless of
        parallel_workers.

        cnn is used directly only when config.parallel_workers <= 1 (falls back to the
        ordinary serial add_station loop below); otherwise each worker opens its own
        fresh connection (see etm_builder.build_station_job) since a connection opened
        here, before forking, must never be reused by a worker process.
        """
        console.phase('station ETMs')
        console.info(f'Building {len(stnlist)} station ETM(s) on '
                     f'{max(1, self.config.parallel_workers)} process(es). '
                     f'Per-station detail: -etm_verbosity info')

        records = []

        if self.config.parallel_workers <= 1:
            for stn in tqdm(stnlist, ncols=console.WIDTH, desc='   building', unit='stn',
                            disable=None):
                records.append(self.add_station(cnn, stn['NetworkCode'], stn['StationCode'],
                                                json_folder, save_json_folder))
            self._report_station_builds(records)
            return

        import multiprocessing as mp

        jobs = [(self.config, stn['NetworkCode'], stn['StationCode'], json_folder, save_json_folder)
                for stn in stnlist]

        ctx = mp.get_context('fork')
        with ctx.Pool(processes=self.config.parallel_workers) as pool:
            for network_code, station_code, etm, error, reason in tqdm(
                    pool.imap(etm_builder.build_station_job, jobs), total=len(jobs),
                    ncols=console.WIDTH, desc='   building', unit='stn', disable=None):
                name = f'{network_code}.{station_code}'
                if error is not None:
                    tqdm.write(print_yellow(f'   ! worker exception building {name}:\n{error}'))
                if etm is None:
                    records.append({'station': name, 'role': 'dropped', 'reason': reason,
                                    'span': 0., 'completion': 0., 'nobs': 0})
                    continue
                records.append(self._add_built_station(etm, network_code, station_code,
                                                      note=reason))

        self._report_station_builds(records)

    def _report_station_builds(self, records: List[dict]) -> None:
        """Summarise a whole build phase in one place.

        Only stations that did something other than join the stack as an ordinary full
        station get a row: being included is the default and needs no line, while being
        set aside as sparse, dropped for a short span, or failing outright is the part
        worth reading. The counts above the table are what you actually check.
        """
        records = [r for r in records if r]
        roles = [r['role'] for r in records]
        interseismic = sum(1 for r in records
                           if r['role'] == 'full' and r.get('interseismic'))

        console.result('Full stations',
                       f'{roles.count("full")}  ({interseismic} constrain the '
                       f'interseismic field)')
        console.result('Sparse stations', f'{roles.count("sparse")}')
        console.result('Dropped', f'{roles.count("dropped") + roles.count("skipped")}')

        # A full station that still carries a note (a rank-deficient first attempt, a
        # zero-tie retry) belongs here too: it did join the stack, but not cleanly, and
        # that is exactly what gets lost when the only report is a count.
        notable = [r for r in records if r['role'] != 'full' or r['reason']]
        if notable:
            console.step('stations needing a second look')
            console.table(
                ['station', 'outcome', 'span[yr]', 'compl[%]', 'nobs', 'reason'],
                [[r['station'], r['role'], f'{r["span"]:.1f}', f'{r["completion"]:.0f}',
                  f'{r["nobs"]}', r['reason']] for r in notable],
                align='llrrrl')

    def _add_built_station(self, etm: EtmEngine, network_code: str, station_code: str,
                           note: str = '') -> dict:
        """Fold an already-built ETM (etm_builder.build_etm's result) into the stack:
        the min_time_span/sparse checks, Station creation, and (for a full station) its
        normal equations -- shared by add_station (serial) and add_stations_parallel.

        Returns a record of what happened to this station rather than printing it. With
        eight workers running, one line per station per decision produced an interleaved
        stream nobody could read; the caller collects these and reports them as one
        table once the phase is done.
        """
        span = float(etm.solution_data.time_vector[-1] - etm.solution_data.time_vector[0])
        completion = float(etm.solution_data.span_completion)
        record = {'station': f'{network_code}.{station_code}', 'span': span,
                  'completion': completion, 'nobs': int(etm.solution_data.solutions),
                  'role': 'full', 'reason': note}

        def add_reason(text: str) -> None:
            record['reason'] = f'{record["reason"]}; {text}' if record['reason'] else text

        if span < self.config.min_time_span:
            record['role'] = 'dropped'
            add_reason(f'span {span:.1f} yr < {self.config.min_time_span:g} yr')
            return record

        if completion < self.config.min_span_completion:
            record['role'] = 'sparse'
            add_reason(f'completion {completion:.1f}% '
                       f'< {self.config.min_span_completion:g}%')
        is_sparse = record['role'] == 'sparse'

        # Create station
        station = etm_builder.create_station(etm)
        record['interseismic'] = station.is_interseismic

        if is_sparse:
            # station.free_fit is NOT built here, even when sparse_constraints_enabled:
            # it needs a model-validity date (see constraints/sparse.overall_validity_date),
            # which needs every station added and constraints registered -- neither is
            # true yet at this point in the add_station loop. build_system() builds it
            # once that's available.
            self.sparse_stations.append(station)
            return record

        # Create normal equations
        neq = self._create_normal_equations(station)

        # Store
        self.stations.append(station)
        self.normal_equations.append(neq)

        return record

    def remove_station(self, station_id: str):
        """Drop a station from the stack, leaving the system stale.

        The parameter renumbering below is enough to make the normal equations
        consistent again, but the grids are not: each station's vertical_response comes
        from an SVD over the whole station set, so after a removal they are all the
        wrong length and the vertical interpolant fails. build_system has to run again
        before the next solve -- EtmStackerShell.do_remove_station does both.
        """
        # sparse stations are observation-only (no owned parameters), so removal
        # is immediate -- no normal_equations/parameter_range renumbering needed
        for i, station in enumerate(self.sparse_stations):
            if stationID(station) == station_id:
                self.sparse_stations.pop(i)
                tqdm.write(f'   Removed sparse station {station_id}')
                self.solved = False
                self.system_stale = True
                return

        for i, station in enumerate(self.stations):
            if stationID(station) == station_id:
                removed_param_count = station.normal_equations.parameter_count
                removed_eq_count = station.normal_equations.equation_count
                self.total_parameters -= removed_param_count
                self.total_equations -= removed_eq_count
                self.normal_equations.pop(i)
                self.stations.pop(i)
                # remove from the parameter range the station that has been removed;
                # this is applied to any stations that come after the removed site
                for station_after in self.stations[i:]:
                    station_after.normal_equations.parameter_range -= removed_param_count
                    station_after.normal_equations.parameter_start_idx -= removed_param_count
                break

        self.solved = False
        self.system_stale = True

    def build_system(self, cnn: Cnn = None):
        """Build (or rebuild) the shared system from the current station set.

        Safe to call again after add_station or remove_station, and much cheaper than a
        fresh run: the per-station ETMs are the expensive part and this does not touch
        them. What it redoes is everything that depends on WHICH stations are in the
        stack -- the interpolation grids and every station's vertical_response, the
        constraint objects, and each event's fault geometry.

        cnn is only required when self.config.sparse_constraints_enabled: it's needed to
        build each sparse station's model-validity-windowed free fit, which can only
        happen now (once constraints are registered below) -- see
        constraints.sparse.build_sparse_free_fits.
        """
        console.phase('shared system')

        console.step('interpolation grids')
        self.grids = GridSystem.create_from_stations(self.stations,
                                                     grid_spacing=self.config.grid_spacing,
                                                     grid_load_radius=self.config.vertical_load_radius,
                                                     method=self.config.vertical_method,
                                                     tension=self.config.tension,
                                                     extent_stations=self.sparse_stations)

        console.step('constraints')
        # Must run before the constraints are built: it changes which decay columns
        # exist, and therefore which stations each postseismic constraint can see.
        self.drop_unconstrainable_postseismic()
        self._register_constraints()

        # 3b. Apply any non-default Okada regularization weight to every constraint just
        # built, so config.*_sw_okada_h_weight/v_weight take effect from the start of a
        # run instead of needing the interactive okada_weights command afterward. Each
        # type (coseismic/postseismic) is applied separately and is a no-op (and silent)
        # at the SWOkada-internal default (1.0, 1.0) -- see data_classes.py for why they
        # aren't meant to share one value.
        if (self.config.coseismic_sw_okada_h_weight != 1.0 or
                self.config.coseismic_sw_okada_v_weight != 1.0):
            self.update_okada_weights(self.config.coseismic_sw_okada_h_weight,
                                      self.config.coseismic_sw_okada_v_weight,
                                      constraint_type='coseismic')
        if (self.config.postseismic_sw_okada_h_weight != 1.0 or
                self.config.postseismic_sw_okada_v_weight != 1.0):
            self.update_okada_weights(self.config.postseismic_sw_okada_h_weight,
                                      self.config.postseismic_sw_okada_v_weight,
                                      constraint_type='postseismic')

        # Sparse stations' validity-windowed free fits (needs station_list, populated
        # by _register_constraints above)
        if self.config.sparse_constraints_enabled:
            console.step('sparse-station free fits')
            if cnn is None:
                raise EtmStackerException(
                    'build_system: sparse_constraints_enabled is True but no cnn was given '
                    '(needed to build each sparse station\'s free fit)')
            sparse_constraint.build_sparse_free_fits(self, cnn)

        self.system_stale = False

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
                              event_id: str = None, relax: float = None,
                              constraint_type: str = None) -> int:
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
        constraint_type : str, optional
            'coseismic' or 'postseismic'. If given, restricts to that type only
            (independent of event_id/relax) -- e.g. constraint_type='postseismic'
            with no event_id updates every postseismic constraint, any event, any
            relaxation, without touching coseismic. If omitted, both types are
            candidates, same as before this parameter existed.

        Returns
        -------
        int
            Number of constraints updated.
        """
        if constraint_type:
            candidates = self.constraint_registry.constraints[constraint_type]
        else:
            candidates = (self.constraint_registry.constraints['coseismic'] +
                         self.constraint_registry.constraints['postseismic'])

        apply_to = []
        for constraint in candidates:
            if event_id and constraint.event.id != event_id:
                continue
            if relax is not None and getattr(constraint, 'relaxation', None) != relax:
                continue
            apply_to.append(constraint)

        for constraint in apply_to:
            logger.info(f'Okada weights for {constraint.short_description()}: '
                        f'h={constraint.sw_okada_h_weight}->{h_weight}  '
                        f'v={constraint.sw_okada_v_weight}->{v_weight}')
            constraint.sw_okada_h_weight = h_weight
            constraint.sw_okada_v_weight = v_weight
            constraint.dislocation_model = None

        if apply_to:
            console.info(f'Okada regularization h={h_weight:g} v={v_weight:g} applied to '
                         f'{len(apply_to)} '
                         f'{constraint_type or "co/postseismic"} constraint(s)')
        else:
            console.warn('update_okada_weights: no matching constraints found')

        self.solved = False
        return len(apply_to)

