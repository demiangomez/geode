"""
ETM Stacker main class.
"""

from typing import List, Dict, Set
import numpy as np
from tqdm import tqdm

from . import etm_builder
from .data_classes import (
    EtmStackerConfig, NormalEquations, Station, EtmStackerField
)
from .earthquake_catalog import EarthquakeCatalogMixin
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


class EtmStackerException(Exception):
    """Exception raised for ETM Stacker errors."""
    pass


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
        result = etm_builder.build_etm(self.config, cnn, network_code, station_code,
                                       json_folder, save_json_folder)
        if result is None:
            return
        # unconstrained_etm (Step 1's own fit, robust-least-squares) isn't used here: a
        # sparse station gets its own dedicated, GLS-fit free fit instead -- see below.
        etm, _ = result
        self._add_built_station(etm, network_code, station_code)

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
        if self.config.parallel_workers <= 1:
            for stn in stnlist:
                self.add_station(cnn, stn['NetworkCode'], stn['StationCode'],
                                 json_folder, save_json_folder)
            return

        import multiprocessing as mp

        jobs = [(self.config, stn['NetworkCode'], stn['StationCode'], json_folder, save_json_folder)
                for stn in stnlist]

        tqdm.write(f' -- Building {len(jobs)} station(s) across '
                  f'{self.config.parallel_workers} worker processes')

        ctx = mp.get_context('fork')
        with ctx.Pool(processes=self.config.parallel_workers) as pool:
            for network_code, station_code, etm, error in pool.imap(etm_builder.build_station_job, jobs):
                if error is not None:
                    tqdm.write(print_yellow(
                        f' -- Worker exception building {network_code}.{station_code}, skipping:\n{error}'))
                    continue
                if etm is None:
                    # build_etm/add_station's own skip conditions already explained
                    # themselves via tqdm.write from inside the worker process
                    continue
                self._add_built_station(etm, network_code, station_code)

    def _add_built_station(self, etm: EtmEngine, network_code: str, station_code: str):
        """Fold an already-built ETM (etm_builder.build_etm's result) into the stack:
        the min_time_span/sparse checks, Station creation, and (for a full station) its
        normal equations -- shared by add_station (serial) and add_stations_parallel.
        """
        if (etm.solution_data.time_vector[-1] -
                etm.solution_data.time_vector[0]) < self.config.min_time_span:
            tqdm.write(print_yellow(
                f' -- Station {network_code}.{station_code} has less than '
                f'{self.config.min_time_span} years of data, skipping'))
            return

        is_sparse = etm.solution_data.span_completion < self.config.min_span_completion

        if is_sparse:
            tqdm.write(print_yellow(
                f' -- Station {network_code}.{station_code} span completion '
                f'{etm.solution_data.span_completion:.1f}% < {self.config.min_span_completion:.1f}%, '
                f'adding as sparse station'))

        # Create station
        station = etm_builder.create_station(etm)

        if is_sparse:
            # station.free_fit is NOT built here, even when sparse_constraints_enabled:
            # it needs a model-validity date (see constraints/sparse.overall_validity_date),
            # which needs every station added and constraints registered -- neither is
            # true yet at this point in the add_station loop. build_system() builds it
            # once that's available.
            self.sparse_stations.append(station)
            return

        # Create normal equations
        neq = self._create_normal_equations(station)

        # Store
        self.stations.append(station)
        self.normal_equations.append(neq)

    def remove_station(self, station_id: str):
        # sparse stations are observation-only (no owned parameters), so removal
        # is immediate -- no normal_equations/parameter_range renumbering needed
        for i, station in enumerate(self.sparse_stations):
            if stationID(station) == station_id:
                self.sparse_stations.pop(i)
                tqdm.write(f' -- Removed sparse station {station_id}')
                self.solved = False
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

        # no need to rebuild the registry or the grids
        # they will contain a couple dead sites but they don't affect the calculations
        self.solved = False

    def build_system(self, cnn: Cnn = None):
        """Build the complete stacking system.

        cnn is only required when self.config.sparse_constraints_enabled: it's needed to
        build each sparse station's model-validity-windowed free fit, which can only
        happen now (once constraints are registered below) -- see
        constraints.sparse.build_sparse_free_fits.
        """
        # 1. Create grids
        self.grids = GridSystem.create_from_stations(self.stations,
                                                     grid_spacing=self.config.grid_spacing,
                                                     grid_load_radius=self.config.vertical_load_radius,
                                                     method=self.config.vertical_method,
                                                     tension=self.config.tension,
                                                     extent_stations=self.sparse_stations)

        # 3. Register all constraints
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

        # 4. Sparse stations' validity-windowed free fits (needs station_list, populated
        # by _register_constraints above)
        if self.config.sparse_constraints_enabled:
            if cnn is None:
                raise EtmStackerException(
                    'build_system: sparse_constraints_enabled is True but no cnn was given '
                    '(needed to build each sparse station\'s free fit)')
            sparse_constraint.build_sparse_free_fits(self, cnn)

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

    def get_trajectory_functions_at_point(self, lon: float, lat: float, etm: EtmEngine):
        pass
