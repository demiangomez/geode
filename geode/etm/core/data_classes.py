"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/21/25 4:58 PM
Author: Demian D. Gomez
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Any, Union, Callable
from datetime import datetime

# app
from geode.pyDate import Date
from geode.metadata.station_info import StationInfoRecord
from geode.etm.core.type_declarations import (JumpType, PeriodicStatus, FitStatus,
                                               AdjustmentModels, CovarianceFunction, SolutionType, EtmException)


@dataclass
class BaseDataClass:
    """
    base class for data manipulated by user preventing adding non-existent elements to the class
    """
    def __post_init__(self):
        # after initialization
        self._allowed_attributes = {item for item in dir(self) if item[0] != '_'}

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, '_allowed_attributes') and name not in self._allowed_attributes:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'. ")
        super().__setattr__(name, value)


@dataclass
class AdjustmentResults(BaseDataClass):
    """Results from least squares adjustment"""
    origin: str = ''
    parameters: np.ndarray = field(default_factory=lambda: np.array([]))
    parameter_sigmas: np.ndarray = field(default_factory=lambda: np.array([]))
    residuals: np.ndarray = field(default_factory=lambda: np.array([]))
    empirical_covariance: np.ndarray = field(default_factory=lambda: np.array([]))
    covariance_function_params: np.ndarray = field(default_factory=lambda: np.array([]))
    stochastic_signal: np.ndarray = 0
    spectral_index_random_noise: float = 0
    spectral_index_stochastic_noise: float = 0
    periodogram_frequencies: np.ndarray = field(default_factory=lambda: np.array([]))
    periodogram_power: np.ndarray = field(default_factory=lambda: np.array([]))
    variance_factor: float = 0
    wrms: float = 0
    obs_sigmas: np.ndarray = field(default_factory=lambda: np.array([]))
    covariance_matrix: np.ndarray = field(default_factory=lambda: np.array([]))
    outlier_flags: np.ndarray = field(default_factory=lambda: np.array([]))
    converged: bool = False
    iterations: int = 0

    def __post_init__(self):
        super().__post_init__()

        # Convert lists to numpy arrays if needed
        array_fields = ['parameters', 'parameter_sigmas', 'residuals', 'empirical_covariance',
                        'covariance_function_params', 'stochastic_signal', 'obs_sigmas', 'covariance_matrix',
                        'outlier_flags', 'periodogram_frequencies', 'periodogram_amplitudes']
        for field_name in array_fields:
            value = getattr(self, field_name)
            if isinstance(value, list):
                setattr(self, field_name, np.array(value))


@dataclass
class EtmFunctionParameterVector(BaseDataClass):
    # Parameter storage
    frequencies: np.ndarray = field(default_factory=lambda: np.array([]))
    relaxation: np.ndarray = field(default_factory=lambda: np.array([]))
    params: List[np.ndarray] = field(default_factory=lambda: [np.array([]), np.array([]), np.array([])])
    sigmas: List[np.ndarray] = field(default_factory=lambda: [np.array([]), np.array([]), np.array([])])
    covar: List[np.ndarray] = field(default_factory=lambda: [np.array([]), np.array([]), np.array([])])

    object: str = ''
    metadata: Optional[str] = None
    param_metadata: Optional[str] = None
    hash: int = 0
    jump_date: datetime = None
    jump_type: JumpType = None
    t_ref: float = None

    def __post_init__(self):
        super().__post_init__()

        # Convert dict to custom objects
        if isinstance(self.jump_type, dict):
            self.jump_type = JumpType(self.jump_type['value'])

        if isinstance(self.jump_date, dict):
            self.jump_date = Date(**self.jump_date).datetime()

        # Convert lists to numpy arrays if needed
        array_fields = ['relaxation', 'frequencies']
        for field_name in array_fields:
            value = getattr(self, field_name)
            if isinstance(value, list):
                setattr(self, field_name, np.array(value))

        for i, uj in enumerate(self.params):
            if isinstance(uj, list):
                self.params[i] = np.array(uj)

        for i, uj in enumerate(self.sigmas):
            if isinstance(uj, list):
                self.sigmas[i] = np.array(uj)

        for i, uj in enumerate(self.covar):
            if isinstance(uj, list):
                self.covar[i] = np.array(uj)


@dataclass
class Earthquake(BaseDataClass):
    id: str = None
    lat: float = None
    lon: float = None
    date: Date = None
    depth: int = 0
    magnitude: float = 0
    distance: float = 0
    location: str = None
    strike: List[float] = field(default_factory=lambda: [])
    dip: List[float] = field(default_factory=lambda: [])
    rake: List[float] = field(default_factory=lambda: [])
    jump_type: JumpType = None

    def __post_init__(self):
        super().__post_init__()

        # Convert dict to custom objects
        if isinstance(self.jump_type, dict):
            self.jump_type = JumpType(self.jump_type['value'])

        if isinstance(self.date, dict):
            self.date = Date(**self.date)

    def build_metadata(self) -> str:
        link = ('<a href="https://earthquake.usgs.gov/earthquakes/eventpage/%s" '
                'target="_blank">%s</a>'
                % (self.id, self.id))
        return f'{link}: M{self.magnitude:.1f} {self.location} -> {self.distance:.0f} km'

    def __eq__(self, other):
        """Compare earthquakes based on event_date"""
        if isinstance(other, Earthquake):
            return self.id == other.id
        return False

    def __lt__(self, other):
        """Less than comparison based on event_date"""
        if isinstance(other, Earthquake):
            return self.date < other.date
        return NotImplemented

    def __hash__(self):
        """Make the object hashable based on event_date"""
        return hash(self.date)

    def __str__(self):
        """return a human-readable string"""
        return f'{self.id} {self.date.yyyyddd()} Mw {self.magnitude}'

@dataclass
class JumpParameters(BaseDataClass):
    jump_type: JumpType = field(default_factory=lambda: JumpType)
    relaxation: np.ndarray = field(default_factory=lambda: np.array([0.5]))
    date: Date = None
    action: str = None

    def __post_init__(self):
        super().__post_init__()

        # Convert dict to custom objects
        if isinstance(self.jump_type, dict):
            self.jump_type = JumpType(self.jump_type['value'])

        if isinstance(self.date, dict):
            self.date = Date(**self.date)

        # Convert lists to numpy arrays if needed
        if isinstance(self.relaxation, list):
            self.relaxation = np.array(self.relaxation)

@dataclass
class LeastSquares(BaseDataClass):
    iterations: int = 10
    sigma_filter_limit: float = 2.5
    arma_roots: int = 49
    arma_points: int = 200
    covariance_function: CovarianceFunction = CovarianceFunction.GAUSSIAN
    adjustment_model: AdjustmentModels = AdjustmentModels.ROBUST_LEAST_SQUARES
    # constraints to apply to the fit
    constraints: List = field(default_factory=lambda: [])

    def __post_init__(self):
        super().__post_init__()

        # Convert dict to custom objects
        if isinstance(self.covariance_function, dict):
            self.covariance_function = CovarianceFunction(self.covariance_function['value'])

        if isinstance(self.adjustment_model, dict):
            self.adjustment_model = AdjustmentModels(self.adjustment_model['value'])


@dataclass
class ModelingParameters(BaseDataClass):
    """Configuration for modeling parameters"""
    # default configuration for running the EtmEngine
    relaxation: np.ndarray = field(
        default_factory=lambda: np.array([0.5])
    )
    poly_terms: int = 2
    reference_epoch: float = 0
    frequencies: np.ndarray = field(
        default_factory=lambda: np.array([1 / 365.25, 1 / (365.25 / 2)])
    )
    periodic_status: PeriodicStatus = PeriodicStatus.AUTOMATICALLY_ADDED

    user_jumps: List[JumpParameters] = field(default_factory=lambda: [])
    earthquake_jumps: List[Earthquake] = field(default_factory=lambda: [])
    # if not all data is to be fit, introduce a time window
    data_model_window: List[List[float]] = None
    # type of adjustment strategy to use
    least_squares_strategy: LeastSquares = field(default_factory=LeastSquares)
    # pre-fit models to apply to the data before doing a fit
    prefit_models: List = field(default_factory=lambda: [])

    # floor_sigmas for returning coordinate uncertainties
    sigma_floor_h: float = 0.10
    sigma_floor_v: float = 0.15
    # minimum number of days between earthquakes
    earthquake_min_days: int = 15
    # minimum magnitude to consider adding jumps
    earthquake_magnitude_limit: int = 6.0
    # earthquakes to add to the fit even if they fall outside of earthquake_magnitude_limit
    earthquakes_cherry_picked: List[str] = field(default_factory=lambda: [])
    # flag to activste / deactivate checks between jumps
    check_jump_collisions: bool = True
    # minimum number of days between jumps
    jump_min_days: int = 5
    # years to add postseismic decays from jumps back in time
    post_seismic_back_lim: Union[int, Date] = 365 * 5

    # General status of the fit process
    status: FitStatus = FitStatus.PREFIT

    # master switches activating certain components
    fit_earthquakes: bool = True
    fit_generic_jumps: bool = True
    fit_metadata_jumps: bool = True
    fit_auto_detected_jumps: bool = False
    fit_auto_detected_jumps_method: str = 'dbscan'

    def __post_init__(self):
        super().__post_init__()

        # Convert dict to custom objects
        if isinstance(self.periodic_status, dict):
            self.periodic_status = PeriodicStatus(self.periodic_status['value'])

        if isinstance(self.status, dict):
            self.status = FitStatus(self.status['value'])

        if isinstance(self.least_squares_strategy, dict):
            self.least_squares_strategy = LeastSquares(**self.least_squares_strategy)

        if isinstance(self.post_seismic_back_lim, dict):
            self.post_seismic_back_lim = Date(**self.post_seismic_back_lim)

        # Convert lists to numpy arrays if needed
        array_fields = ['relaxation', 'frequencies']
        for field_name in array_fields:
            value = getattr(self, field_name)
            if isinstance(value, list):
                setattr(self, field_name, np.array(value))

        for i, eq in enumerate(self.earthquake_jumps):
            if isinstance(eq, dict):
                self.earthquake_jumps[i] = Earthquake(**eq)

        for i, uj in enumerate(self.user_jumps):
            if isinstance(uj, dict):
                self.user_jumps[i] = JumpParameters(**uj)

    def get_observation_mask(self, time_vector):
        """apply the observation mask to know which observations to consider during fitting"""
        if self.data_model_window:
            mask = np.zeros(time_vector.shape).astype(bool)
            for win in self.data_model_window:
                mask[np.logical_and(time_vector > win[0], time_vector < win[1])] = True

            if ~np.any(mask):
                raise EtmException('Fit window yielded a null observation vector. Check your fit window dates '
                                   'and the available station data.')
        else:
            mask = np.ones(time_vector.shape).astype(bool)

        return mask

    def get_user_jump(self,
                      date: Union[Date, datetime],
                      jump_type: JumpType) -> Union[JumpParameters, None]:
        """obtain a jump from the database jump config using date and type"""

        for jump_params in self.user_jumps:
            if jump_params.date == date:
                # dates match, check types
                if (jump_params.jump_type == jump_type or
                        (jump_type == JumpType.REFERENCE_FRAME and
                         jump_params.jump_type == JumpType.MECHANICAL_MANUAL)):
                    # types match exactly, so it is the jump being looked for
                    return jump_params
                elif (jump_params.jump_type >= JumpType.COSEISMIC_JUMP_DECAY
                      and jump_type >= JumpType.COSEISMIC_JUMP_DECAY):
                    # a geophysical jump with a change in behavior, return it to the caller
                    return jump_params
        return None


@dataclass
class StationMetadata(BaseDataClass):
    # @ todo: add station monument and station type to the metadata
    name: str = ''
    country_code: str = ''
    lat: np.ndarray = field(default_factory=lambda: np.array([0]))
    lon: np.ndarray = field(default_factory=lambda: np.array([0]))
    height: np.ndarray = field(default_factory=lambda: np.array([0]))
    auto_x: np.ndarray = field(default_factory=lambda: np.array([6378137.]))
    auto_y: np.ndarray = field(default_factory=lambda: np.array([0]))
    auto_z: np.ndarray = field(default_factory=lambda: np.array([0]))
    first_obs: Date = Date(year=1980, doy=1)
    last_obs: Date = Date(datetime=datetime.now())
    max_dist: float = 20.0
    station_information: list = field(default_factory=lambda: [])

    def __post_init__(self):
        super().__post_init__()

        # Convert dict to Date objects if needed
        if isinstance(self.first_obs, dict):
            self.first_obs = Date(**self.first_obs)

        if isinstance(self.last_obs, dict):
            self.last_obs = Date(**self.last_obs)

        if isinstance(self.station_information, list):
            for i, stn in enumerate(self.station_information):
                self.station_information[i] = StationInfoRecord(stn['NetworkCode'], stn['StationCode'], _record=stn)

        # Convert lists to numpy arrays if needed
        array_fields = ['lat', 'lon', 'height', 'auto_x', 'auto_y', 'auto_z']
        for field_name in array_fields:
            value = getattr(self, field_name)
            if isinstance(value, list) or isinstance(value, tuple):
                setattr(self, field_name, np.array(value))


@dataclass
class SolutionOptions(BaseDataClass):
    solution_type: SolutionType = SolutionType.PPP
    stack_name: str = 'ppp'
    project: str = '' # to store the project name for GAMIT solutions
    filename: str = '' # to store the filename location if SolutionType is NGL
    format: str = ('fyear', 'x', 'y', 'z') # default format reader for the filename

    def __post_init__(self):
        super().__post_init__()

        # Convert dict to custom objects
        if isinstance(self.solution_type, dict):
            self.solution_type = SolutionType(self.solution_type['value'])

# @ todo: analyze if this class belongs inside of modeling
@dataclass
class ValidationRules(BaseDataClass):
    """Configuration for validation rules"""
    max_relaxation_amplitude: float = 100.0  # meters (inflated after tests from 4 to 100)
    min_solutions_for_etm: int = 4
    min_data_for_jump: int = 50  # data points
    max_condition_number: float = 8 # log10 of the condition number


