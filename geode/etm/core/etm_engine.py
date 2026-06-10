from enum import IntEnum
from dataclasses import asdict
from datetime import datetime
from typing import Dict, Optional, Union, List
import logging
import json
import numpy as np
import copy
import platform
import os

from importlib.metadata import version, PackageNotFoundError

try:
    VERSION = str(version("geode"))
except PackageNotFoundError:
    # package is not installed
    VERSION = 'NOT_AVAIL'
    pass

logger = logging.getLogger(__name__)

# app
from ...dbConnection import Cnn
from ...Utils import file_write, load_json
from ...pyDate import Date

from ...metadata.station_info import StationInfoRecord
from ..data.solution_data import SolutionData
from ..data.etm_database import load_parameters_db, save_parameters_db
from ..data.etm_params import EtmParams
from ..core.etm_config import EtmConfig, SolutionOptions
from ..core.type_declarations import EtmSolutionType, FitStatus, SolutionType, JumpType
from ..core.data_classes import LeastSquares, AdjustmentResults
from ..least_squares.design_matrix import DesignMatrix
from ..least_squares.least_squares import EtmFit, AdjustmentModels
from ..etm_functions.polynomial import PolynomialFunction
from ..etm_functions.jumps import JumpFunction
from ..etm_functions.periodic import PeriodicFunction
from ..etm_functions.stochastic_signal import StochasticSignal
from ..core.jump_manager import JumpManager
from ..core.logging_config import setup_etm_logging
from ..visualization.data_prep import PlotDataPreparer
from ..visualization.etm_plotting import EtmPlotter


def enum_dict_factory(field_list):
    """Custom dict factory that handles enums with descriptions"""
    result = {}
    for key, value in field_list:
        # Check if value is an IntEnum with a description property
        if isinstance(value, IntEnum) and hasattr(value, 'description'):
            result[key] = {
                'value': value.value,
                'description': value.description
            }
        # Handle lists that might contain enums
        elif isinstance(value, list):
            result[key] = [
                {'value': v.value, 'description': v.description}
                if isinstance(v, IntEnum) and hasattr(v, 'description')
                else v
                for v in value
            ]
        elif isinstance(value, (LeastSquares, SolutionOptions)):
            for k, v in value:
                result[key] = {}
                # Check if value is an IntEnum with a description property
                if isinstance(v, IntEnum) and hasattr(v, 'description'):
                    result[key][k] = {
                        'value': v.value,
                        'description': v.description
                    }
        else:
            result[key] = value
    return result


class EtmEncoder(json.JSONEncoder):
    """Custom JSON encoder for numpy arrays and float rounding"""

    def __init__(self, *args, round_digits=6, no_round_fields=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.round_digits = round_digits
        self.no_round_fields = no_round_fields or []
        self._in_rounding_phase = False

    def default(self, obj):
        if isinstance(obj, np.ndarray):
            # Just convert to list, don't round here
            return obj.tolist()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, StationInfoRecord):
            return obj.to_json()
        if isinstance(obj, datetime):
            date = Date(datetime=obj)
            return {'year': date.year,
                    'month': date.month,
                    'day': date.day,
                    'mjd': date.mjd,
                    'doy': date.doy,
                    'hour': date.hour,
                    'minute': date.minute,
                    'second': date.second}
        if isinstance(obj, Date):
            if obj.from_stninfo:
                return {'stninfo': str(self)}
            else:
                return {'year': obj.year,
                        'month': obj.month,
                        'day': obj.day,
                        'mjd': obj.mjd,
                        'doy': obj.doy,
                        'hour': obj.hour,
                        'minute': obj.minute,
                        'second': obj.second}
        return super().default(obj)

    def iterencode(self, obj, _one_shot=False):
        """Override iterencode to apply rounding after numpy conversion"""
        if not self._in_rounding_phase:
            # First pass: convert all numpy types to native Python types
            self._in_rounding_phase = True
            temp_json = ''.join(super().iterencode(obj, _one_shot))
            self._in_rounding_phase = False

            # Parse back to Python objects
            intermediate = json.loads(temp_json)

            # Apply rounding with path tracking
            rounded = self._round_floats(intermediate, path=[])

            # Encode the rounded result
            for chunk in super().iterencode(rounded, _one_shot):
                yield chunk
        else:
            # Already in rounding phase, just do normal encoding
            for chunk in super().iterencode(obj, _one_shot):
                yield chunk

    def _round_floats(self, obj, path):
        """Recursively round all floats in nested structures, except excluded fields"""
        # Check if any key in the current path is in no_round_fields
        if any(key in self.no_round_fields for key in path):
            return obj

        if isinstance(obj, float):
            return round(obj, self.round_digits)
        elif isinstance(obj, dict):
            return {key: self._round_floats(value, path=path + [key]) for key, value in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._round_floats(item, path=path) for item in obj]
        return obj


class EtmEngineException(Exception):
    pass


class EtmEngine:
    """Core mathematical engine for ETM processing, separated from business logic"""

    def __init__(self, config: EtmConfig,
                 cnn: Cnn = None, silent: bool = False, **kwargs):

        setup_etm_logging(level=logging.CRITICAL if silent else logging.INFO)

        self.config = config

        if config.solution.solution_type == SolutionType.DRA:
            # DRA, turn off models
            self.config.modeling.fit_metadata_jumps = False
            self.config.modeling.fit_generic_jumps = False
            self.config.modeling.fit_earthquakes = False
            self.config.modeling.fit_auto_detected_jumps = False
            # force zero auto coordinates in DRA mode
            self.config.metadata.auto_x = np.array([0])
            self.config.metadata.auto_y = np.array([0])
            self.config.metadata.auto_z = np.array([0])

        # determine the type of object within the SolutionData class
        self.solution_data = SolutionData.create_instance(config)

        # load the data from connection or json file
        self.solution_data.load_data(cnn, **kwargs)

        # check how many solutions we have available. If less than
        # 100 (most likely campaign) disable metadata jumps
        logger.info(f'Observation count is {self.solution_data.solutions}')
        if self.solution_data.solutions < 100:
            logger.info('Disabling metadata jumps because solution count < 100')
            self.config.modeling.fit_metadata_jumps = False

        # @todo: evaluate if mask should be applied here or inside solution_data
        mask = self.config.modeling.get_observation_mask(self.solution_data.time_vector)

        # set the basic functions
        polynomial = PolynomialFunction(config, time_vector=self.solution_data.time_vector)
        periodic = PeriodicFunction(config, time_vector=self.solution_data.time_vector)

        self.jump_manager = JumpManager(self.solution_data, config)
        # handle reference frame changes in PPP solutions
        if config.solution.solution_type == SolutionType.PPP:
            # check for reference frame changes
            self._check_reference_frame_jumps()

        self.jump_manager.build_jump_table(self.solution_data.time_vector[mask],
                                           self.solution_data.transform_to_local())

        if config.solution.solution_type == SolutionType.DRA:
            # no periodic for DRA
            self.design_matrix = DesignMatrix(config,
                                              self.solution_data.time_vector,
                                              [polynomial] + self.jump_manager.get_jump_functions())
        else:
            self.design_matrix = DesignMatrix(config,
                                              self.solution_data.time_vector,
                                              [polynomial, periodic] + self.jump_manager.get_jump_functions())

        # if adjustment strategy is LSQ_COLLOCATION, add the stochastic function to the design matrix
        # (although it is not used during the fit)
        if self.config.modeling.least_squares_strategy.adjustment_model == AdjustmentModels.LSQ_COLLOCATION:
            self.design_matrix.functions.append(
                StochasticSignal(self.config, self.solution_data.time_vector_cont_mjd))

        self.fit = EtmFit(self.config, self.design_matrix, self.solution_data.hash)

    def _check_reference_frame_jumps(self):
        # check for reference frame jumps
        if len(self.solution_data.frames) > 1:
            # more than one frame, add a jump
            prev_frame = self.solution_data.frames[0]['ReferenceFrame']

            for frame in self.solution_data.frames[1:]:
                jump = JumpFunction(
                    config=self.config,
                    time_vector=self.solution_data.time_vector,
                    date=Date(Year=int(frame['Year']), doy=int(frame['DOY'])),
                    jump_type=JumpType.REFERENCE_FRAME,
                    user_action='A',
                    metadata=f'{prev_frame}->{frame["ReferenceFrame"]}',
                    fit=self.config.modeling.fit_generic_jumps,
                )
                prev_frame = frame["ReferenceFrame"]
                self.jump_manager.add_jump(jump)

    def run_adjustment(self, try_loading_db=True, try_save_to_db=True,
                       cnn: Optional[Cnn] = None, force_computation=False) -> None:
        """Run the iterative least squares adjustment"""
        if self.config.json_file is None:
            if try_loading_db and cnn and not force_computation:
                # get mask for time vector: only needed for stochastic signal in load_parameters_db!
                mask = self.config.modeling.get_observation_mask(self.solution_data.time_vector)

                success = load_parameters_db(self.config, cnn, self.fit, self.solution_data.transform_to_local(),
                                             self.solution_data.time_vector_mjd[mask])
                logger.info(f'Loading parameters from database: {success}')
            else:
                success = False
        else:
            if not force_computation:
                # information in the json, see if required data is present
                success = self.load_from_json(self.config.json_file)
            else:
                success = False

        if not success:
            run_time = self.fit.run_fit(self.solution_data, self.design_matrix)
            if cnn and try_save_to_db and self.config.modeling.status == FitStatus.POSTFIT:
                save_parameters_db(cnn, self.fit)
            logger.info(f'Estimated parameters in {run_time} seconds')

    def load_from_json(self, json_: Union[str, dict] = None) -> bool:
        """
        load ETM solution from json file
        """
        logger.info('Loading etm from json file')
        data = load_json(json_)

        if 'raw_results' in data.keys():
            self.fit.results = []
            for i in range(3):
                self.fit.results.append(AdjustmentResults(**data['raw_results'][i]))

            # need to check if the design matrix that the etm built is compatible with the design matrix
            # in the json file. If the json has a deactivated function, it will be in the design matrix
            # but not in the json file
            for f in [ff for ff in self.design_matrix.functions if ff.p.object == 'jump']:
                found = False
                for func in [ff for ff in data['functions'] if ff['object'] == 'jump']:
                    if Date(**func['jump_date']) == f.p.jump_date:
                        found = True
                # if we did not find the jump in the json, deactivate it because it
                # was deactivated before saving the file
                if not found:
                    f.fit = False
                    # reassign the column indices
                    self.design_matrix._assign_column_indices()

            self.fit.load_results_to_functions()

            return True
        else:
            return False

    def save_etm(self,
                 filename: str = None,
                 dump_functions: bool = True,
                 dump_raw_results: bool = False,
                 dump_observations: bool = False,
                 dump_design_matrix: bool = False,
                 dump_model: bool = False) -> Dict:

        dm = self.design_matrix
        mask = self.config.modeling.get_observation_mask(self.solution_data.time_vector)
        # create a station_meta copy to replace the StationInfoRecord instances
        station_meta = copy.deepcopy(self.config.metadata)
        station_meta.station_information = [item.to_json() for item in station_meta.station_information]

        # dump continuous model if requested
        if dump_model:
            model_values = self.fit.get_time_continuous_model(self.solution_data.time_vector_cont)
        else:
            model_values = None

        functions = [asdict(funct.p, dict_factory=enum_dict_factory) for funct in dm.functions if funct.fit]

        etm_dump = {
            "network_code": self.config.network_code,
            "station_code": self.config.station_code,
            "station_meta": asdict(station_meta),
            "solution_options": asdict(self.config.solution, dict_factory=enum_dict_factory),
            "modeling_params": asdict(self.config.modeling, dict_factory=enum_dict_factory),
            "raw_results": [asdict(self.fit.results[0]),
                            asdict(self.fit.results[1]),
                            asdict(self.fit.results[2])] if dump_raw_results else None,
            "functions": functions if dump_functions else None,
            "observations": asdict(self.solution_data.coordinates) if dump_observations else None,
            "frames": self.solution_data.frames,
            "model": {'model_cont': model_values,
                      'time_vector_fyear': self.solution_data.time_vector_cont,
                      'time_vector_mjd': self.solution_data.time_vector_cont_mjd
                      } if dump_model else None,
            "covariance": self.fit.covar.tolist(),
            "design_matrix": dm.matrix if dump_design_matrix else None,
            "data_model_window": mask if dump_design_matrix else None,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "localhost": platform.node(),
            "version": VERSION
        }

        if filename:
            if not os.path.basename(filename):
                filename = os.path.join(filename, self.config.build_filename()) + '.json'
            else:
                dirs = os.path.dirname(filename)
                file = os.path.basename(filename).split('.json')
                filename = os.path.join(dirs, file[0]) + '.json'

            #import bson

            file_write(filename,
                       json.dumps(etm_dump, indent=4, sort_keys=False, cls=EtmEncoder,
                                  round_digits=6, no_round_fields=['covariance', 'parameter_sigmas',
                                                                   'parameters', 'covariance_matrix',
                                                                   'lat', 'lon', 'params', 'sigmas']))

            #binary_data = bson.encode(etm_dump, cls)
            #with open(filename + '.bson', 'wb') as f:
            #    f.write(binary_data)

        return etm_dump

    def plot(self) -> Union[str, None]:

        plotter = EtmPlotter(self.config)
        plot_data = PlotDataPreparer(self.config).prepare_time_series_data(self.solution_data, self.fit)

        return plotter.plot_time_series(plot_data)

    def plot_hist(self) -> Union[str, None]:

        if self.config.modeling.status == FitStatus.POSTFIT:
            plotter = EtmPlotter(self.config)
            plot_data = PlotDataPreparer(self.config).prepare_time_series_data(self.solution_data, self.fit)

            return plotter.plot_histogram(plot_data)
        else:
            return None

    def get_position(self, dates: Union[Date, list],
                     etm_solution: EtmSolutionType = EtmSolutionType.MODEL) -> Dict:
        """
        use computed model to obtain a station position
        """

        if isinstance(dates, Date):
            dates = [dates]

        sigma_h = self.config.modeling.sigma_floor_h
        sigma_v = self.config.modeling.sigma_floor_v
        limit = self.config.modeling.least_squares_strategy.sigma_filter_limit

        model = [np.array([]), np.array([]), np.array([])]
        model_ecef = [np.array([]), np.array([]), np.array([])]
        stack_name = self.solution_data.stack_name.upper()

        # find the model value, if fit done
        if self.config.modeling.status == self.config.modeling.status.POSTFIT:
            for i in range(3):
                model[i] = (self.design_matrix.alternate_time_vector(
                    np.array([date.fyear for date in dates])) @ self.fit.results[i].parameters)

            # transform back to ecef
            model_ecef = np.array(self.solution_data.transform_to_ecef(model))

        if etm_solution == EtmSolutionType.OBSERVATION:
            # find the date of the request
            mask = np.isin(self.solution_data.time_vector_mjd, [date.mjd for date in dates])
            i = np.where(mask)[0]

            if i.size:
                # solution found, save it
                position = np.array([self.solution_data.x[i], self.solution_data.y[i], self.solution_data.z[i]])
                sigmas = np.array([result.residuals[i] for result in self.fit.results])

                # check if model is not empty
                if model_ecef[0].size:
                    if np.all(self.fit.outlier_flags[i]):
                        source = stack_name + ' with ETM solution: good'
                    else:
                        # filter coordinate because it was an outlier
                        position = model_ecef
                        sigmas = sigmas * limit
                        source = stack_name + ' with ETM solution: filtered'
                else:
                    source = stack_name + ' solution, but no ETM'
            else:
                if model_ecef[0].size:
                    position = model_ecef
                    sigmas = np.array([[result.wrms] * len(dates) for result in self.fit.results]) * limit
                    source = 'No ' + stack_name + ' solution for requested dates: ETM'
                else:
                    source = 'No ' + stack_name + ' solution for requested dates, no ETM: mean coordinate'
                    position = np.array([[self.solution_data.x.mean()] * len(dates),
                                         [self.solution_data.y.mean()] * len(dates),
                                         [self.solution_data.z.mean()] * len(dates)])

                    sigmas = np.array([[self.solution_data.x.std()] * len(dates),
                                       [self.solution_data.y.std()] * len(dates),
                                       [self.solution_data.z.std()] * len(dates)])
        else:
            if model_ecef.size:
                source = 'ETM solution requested'
                position = model_ecef
                sigmas = np.array([[result.wrms] * len(dates) for result in self.fit.results])
            else:
                raise Exception('Model requested by no ETM was found for ' + self.config.get_station_id())

        if self.config.modeling.status == self.config.modeling.status.POSTFIT:
            # get the velocity of the site
            for funct in self.design_matrix.functions:
                if funct.p.object == 'polynomial':
                    if np.sqrt(np.sum(np.square([p[1] for p in funct.p.params]))) > 0.2:
                        # fast moving station! bump up the sigma floor
                        sigma_h = 99.9
                        sigma_v = 99.9
                        source += '. fast moving station, bumping up sigmas'
                    break

        # apply floor sigmas
        sigmas = np.sqrt(np.square(sigmas) +
                         np.square(np.array(
                             [[sigma_h] * len(dates), [sigma_h] * len(dates), [sigma_v] * len(dates)])))

        return {'position': position.tolist(), 'source': source, 'sigmas': sigmas.tolist()}

    def query_jump(self, date: Date):
        """return any jumps that may have occurred in the date being requested"""
        for jump in self.design_matrix.functions:
            if jump.p.object == 'jump' and Date(datetime=jump.p.jump_date) == date and jump.fit:
                return Date(datetime=jump.p.jump_date)

        return None

    def pull_params(self) -> Dict:
        """
        Obtain the current parameters of the station (in json format).

        Returns a dictionary with 'polynomial', 'periodic', and 'jumps' keys.
        Periodic returns a dictionary with the periods as keys and a str following
        the description in periodic_status_dict.

        Returns:
            Dictionary with polynomial, periodic, and jumps parameters
        """
        return EtmParams.from_etm(self).pull_params()

    def push_params(self, cnn: Cnn,
                    params=None,
                    reset_polynomial=False,
                    reset_periodic=False,
                    reset_jumps=False,
                    copy_params: bool = None):
        """
        Push parameters to the etm_params table.

        Args:
            cnn: Database connection object
            params: Dictionary containing parameter objects. Supported formats:
                {'object': 'polynomial', 'terms': int, 'Year': int, 'DOY': int}
                    - terms: number of polynomial terms (e.g., 2, 3, 4)
                    - Year/DOY: reference date (both required if either is set)
                {'object': 'periodic', 'frequencies': list[float]}
                    - frequencies: list of periods in days to fit (empty list = none)
                {'object': 'jump', 'Year': int, 'DOY': int, 'action': str,
                 'jump_type': int, 'relaxation': list}
                    - Year/DOY: discontinuity date (mandatory)
                    - action: '+' to add, '-' to remove
                    - jump_type: 0=mechanical, 1=co+postseismic, 2=postseismic only
                    - relaxation: required when jump_type > 0
            reset_polynomial: If True, delete existing polynomial params
            reset_periodic: If True, delete existing periodic params
            reset_jumps: If True, delete existing jump params
            copy_params: If True, copy parameters to all solution types.
                        If False, remove the copy_params flag.
                        If None, don't modify the copy_params setting.

        Raises:
            EtmParamsException: If validation fails
        """
        etm_params = EtmParams.from_etm(self, cnn)
        etm_params.push_params(
            params=params,
            reset_polynomial=reset_polynomial,
            reset_periodic=reset_periodic,
            reset_jumps=reset_jumps,
            copy_params=copy_params
        )
