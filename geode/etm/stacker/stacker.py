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
from ..core.etm_engine import EtmEngine
from ..core.etm_config import EtmConfig
from ..core.data_classes import Earthquake
from ..core.type_declarations import SolutionType, FitStatus
from ..data.solution_data import SolutionDataException
from ..least_squares.design_matrix import DesignMatrixException
from ..etm_functions.jumps import JumpFunction
from ..visualization.plot_fields import plot_velocity_field
from ...dbConnection import Cnn
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

        etm = None
        loaded_from_json = False

        if json_folder is not None:
            if os.path.isfile(os.path.join(json_folder, f'{network_code}.{station_code}_ppp.json')):
                tqdm.write(f'Loading etm for {network_code}.{station_code} from json file')
                config = EtmConfig(json_file=os.path.join(json_folder, f'{network_code}.{station_code}_ppp.json'))
                # remove any prefit models from the json (should be applied when we did the model in the first place)
                etm = EtmEngine(config)
                loaded_from_json = True
            else:
                tqdm.write(f'Could not find etm json for {network_code}.{station_code}, '
                           f'will try to use the database')

        try:
            if etm is None:
                tqdm.write(f'Estimating etm for {network_code}.{station_code}')
                config = EtmConfig(network_code, station_code, cnn=cnn)
                config.solution.solution_type = SolutionType.PPP

                config = self._apply_config(config, cnn)

                etm = EtmEngine(config, cnn=cnn, silent=True)
                etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_stacker'
                etm.plot()
            if etm.solution_data.time_vector[-1] - etm.solution_data.time_vector[0] <= 1.5:
                tqdm.write(print_yellow(f' -- Station {network_code}.{station_code} has less than 1.5 '
                                        f'years of data, skipping'))
                return None

            etm.run_adjustment(cnn=cnn)
        except (DesignMatrixException, numpy.linalg.linalg.LinAlgError):
            tqdm.write(print_yellow(f' -- Unable to fit {network_code}.{station_code} -> system is rank deficient. '
                                    f'Will redo ETM with only 10 years of postseismic events.'))
            # default back to max condition number = 3
            config.validation.max_condition_number = 3
            etm = EtmEngine(config, cnn=cnn, silent=True)
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
            try:
                etm.run_adjustment(cnn=cnn, try_loading_db=False, force_computation=True, try_save_to_db=False)
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

        # gather any mechanical jumps to remove
        mechanical = etm.jump_manager.get_active_mechanical_jumps()
        if len(mechanical):
            if not self._correct_mechanical_jumps(network_code, station_code, etm, mechanical, cnn):
                return None

        if save_json_folder is not None and not loaded_from_json:
            if not os.path.exists(save_json_folder):
                os.makedirs(save_json_folder)
            # let the etm build the filename of the station
            etm.save_etm(save_json_folder + '/', dump_functions=True, dump_observations=True,
                         dump_raw_results=True, dump_design_matrix=True, dump_model=True)

        return etm

    def _apply_config(self, config: EtmConfig, cnn: Cnn):
        config.validation.max_condition_number = self.config.max_condition_number
        config.modeling.check_jump_collisions = False  # turn off jump collision check. Add all jumps.
        config.modeling.earthquake_magnitude_limit = self.config.earthquake_magnitude_limit
        config.modeling.post_seismic_back_lim = self.config.post_seismic_back_lim
        config.modeling.relaxation = self.config.relaxation
        config.modeling.earthquakes_cherry_picked = self.config.earthquakes_cherry_picked
        config.refresh_config(cnn)
        return config

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

    def _correct_mechanical_jumps(self, network_code: str, station_code: str,
                                  station_etm: EtmEngine,
                                  mechanical: List[JumpFunction],
                                  cnn: Cnn):

        # need to rerun the model but without letting it be unconstrained
        # create a deep copy of the etm
        config = EtmConfig(network_code, station_code, cnn=cnn)
        config.modeling.relaxation = np.array([np.max(self.config.relaxation)])

        if not os.path.exists('./production'):
            os.makedirs('./production')

        etm = EtmEngine(config, cnn=cnn)
        etm.run_adjustment(try_loading_db=False, force_computation=True, try_save_to_db=False)
        etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_before_correction'
        etm.plot()

        prefit: List[JumpFunction] = []
        for jump in etm.jump_manager.get_active_mechanical_jumps():
            prefit.append(jump)

        # assign prefit models to remove
        station_etm.config.modeling.status = FitStatus.PREFIT
        station_etm.config.modeling.prefit_models = copy.deepcopy(prefit)
        # deactivate these jumps from the design matrix
        for j in mechanical:
            j.fit = False
        # rerun adjustment without the mechanical jumps
        try:
            station_etm.run_adjustment(try_loading_db=False, force_computation=True, try_save_to_db=False)
            tqdm.write(f' -- Found and corrected {len(mechanical)} mechanical jumps in {network_code}.{station_code}')
            station_etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_corrected'
            station_etm.plot()
        except (DesignMatrixException, numpy.linalg.linalg.LinAlgError):
            tqdm.write(print_yellow(f' -- Unable to fit {network_code}.{station_code} -> system is rank deficient.'))
            return False

        return True

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
                sigma = 1 # self.config.postseismic_v_sigma if j == 2 else self.config.postseismic_h_sigma
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

        # collect constraints. Changes to smoothing and weight will be applied here
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

            # Coseismic
            stations = [stn for stn in self.stations if stn.get_coseismic_column(event.id) is not None]

            if len(stations):
                self.constraint_registry.add_constraint(
                    CoseismicConstraint(
                        event, stations, self.grids,
                        self.config.coseismic_h_sigma,
                        self.config.coseismic_v_sigma,
                        is_collision=event.id in self.collided_earthquakes
                    )
                )
            else:
                tqdm.write(f'No stations observed coseismic event {event.id}. '
                           f'A coseismic constraint for this event will not be added.')

            # Postseismic for each relaxation
            for relax in self.config.relaxation:
                self.constraint_registry.add_constraint(
                    PostseismicConstraint(
                        event, relax,
                        self.config.postseismic_h_sigma,
                        self.config.postseismic_v_sigma,
                        is_collision=event.id in self.collided_earthquakes
                    )
                )

    def _record_earthquakes(self):

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

                    # open connection to database
                    cnn = Cnn('gnss_data.cfg')
                    lon, lat = self.grids.interpolation_geographic

                    # save a mask for the event
                    mask = Mask(cnn, jump.earthquake.id)
                    s_score, p_score = mask.score(lat, lon)

                    tqdm.write(f'Getting mask for event {jump.earthquake.id}')
                    s_score = s_score > 0
                    p_score = p_score > 0
                    # save the actual object to query it
                    self.grids.earthquake_masks[jump.earthquake.id] = (s_score, p_score, mask)

        # Check for earthquake collisions (events within 10 days of each other)
        # Only the largest magnitude event survives; others get zero-tie constraints
        collision_window_days = 10
        for i, event_i in enumerate(self.earthquakes):
            for j, event_j in enumerate(self.earthquakes):
                if i >= j:
                    continue
                # Check if events are within collision window
                days_apart = abs(event_i.date.fyear - event_j.date.fyear) * 365.25
                if days_apart <= collision_window_days:
                    # Collision detected - keep the larger magnitude event
                    if event_i.magnitude >= event_j.magnitude:
                        loser = event_j
                        winner = event_i
                    else:
                        loser = event_i
                        winner = event_j

                    if loser.date >= winner.date:
                        # Loser fires after (or simultaneously with) winner:
                        # its coseismic is buried in the winner's postseismic → zero-tie
                        if loser.id not in self.collided_earthquakes:
                            tqdm.write(f'WARNING: Earthquake collision detected between '
                                       f'{event_i.id} (M{event_i.magnitude:.1f}) and '
                                       f'{event_j.id} (M{event_j.magnitude:.1f}) '
                                       f'({days_apart:.1f} days apart). '
                                       f'Keeping {winner.id}, constraining {loser.id} to zero.')
                            self.collided_earthquakes.add(loser.id)
                    else:
                        # Loser fires before winner: its coseismic is observable;
                        # the stacker postseismic constraints handle the overlap.
                        tqdm.write(f'NOTE: Event {loser.id} (M{loser.magnitude:.1f}) fires '
                                   f'{days_apart:.1f} days before {winner.id} (M{winner.magnitude:.1f}); '
                                   f'coseismic is observable, not constraining to zero.')

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
        stations = [stn for stn in self.stations if stn.get_coseismic_column(event.id) is not None]
        if len(stations):
            self.constraint_registry.add_constraint(
                CoseismicConstraint(
                    event, stations,
                    self.config.coseismic_h_sigma,
                    self.config.coseismic_v_sigma
                )
            )
        else:
            tqdm.write(f'No stations observed coseismic event {event.id}. '
                       f'A coseismic constraint for this event will not be added.')

        # Postseismic for each relaxation
        for relax in self.config.relaxation:
            self.constraint_registry.add_constraint(
                PostseismicConstraint(
                    event, relax,
                    self.config.postseismic_h_sigma,
                    self.config.postseismic_v_sigma
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

    def update_smoothing(self, event_id: str, new_smoothing: float):
        pass
        #for const in self.constraint_registry.constraints['coseismic']:
        #    if const.event.id == event_id:
        #        tqdm.write(f'Found event {event_id} with current smoothing {const.smoothing:.3e}')
        #        const.smoothing = new_smoothing
        #        self.solved = False

    def update_smoothing_start_stop(self, event_id: str, new_smoothing_start: float,
                                    new_smoothing_stop: float):
        for const in self.constraint_registry.constraints['coseismic']:
            if const.event.id == event_id:
                tqdm.write(f'Found event {event_id} with current smoothing start {const.search_start_smoothing:.3e} '
                           f'stop {const.search_stop_smoothing:.3e}')
                const.search_start_smoothing = new_smoothing_start
                const.search_stop_smoothing = new_smoothing_stop
                # reset fields
                const.start_smoothing = [None, None, None]
                const.stop_smoothing = [None, None, None]

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
            constraint.update_weights(h_sigma, v_sigma)

        self.solved = False

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

            self.fields.append(
                EtmStackerField.create_field(
                    self.stations, self.solution, self.covariance, self.grids)
            )

            for event in self.earthquakes:
                # find the constraint for this event
                coseismic_constraint = None
                for const in self.constraint_registry.constraints['coseismic']:
                    if const.event == event:
                        coseismic_constraint = const
                        break

                if coseismic_constraint is None:
                    tqdm.write(f'Could not find coseismic constraint for {event.id}')
                    continue

                if coseismic_constraint.is_collision:
                    tqdm.write(f'Skipping field interpolation for collided event {event.id} '
                               f'(parameters are zero-tied)')
                    continue

                fields = EtmStackerField.create_field(
                    self.stations, self.solution, self.covariance, self.grids, event,
                    self.config.relaxation, coseismic_constraint)

                self.fields += fields

        else:
            tqdm.write('System has not been solved! Invoke solve first')

    def get_trajectory_functions_at_point(self, lon: float, lat: float, etm: EtmEngine):
        pass
