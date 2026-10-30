# base_function.py
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Dict, Any, Optional, List, Tuple
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ...Utils import load_json
from ..core.etm_config import EtmConfig
from ..core.data_classes import EtmFunctionParameterVector, AdjustmentResults


class EtmFunctionException(Exception):
    pass


class EtmFunction(ABC):
    """Enhanced base class for all ETM function objects"""

    def __init__(self, config: EtmConfig,
                 metadata: Optional[str] = '',
                 fit: bool = True,
                 **kwargs):

        self.p = EtmFunctionParameterVector()
        self.config = config
        # flag indicator for constraints
        self.constrained = [False] * 3

        self.p.metadata = metadata

        # Function properties
        self.param_count = 0
        self.column_index = np.array([])
        self.format_str = ''
        self.fit = fit
        self.design = np.array([])

        # time vector
        self._time_vector = np.array([])

        # Initialize specific parameters
        self.initialize(**kwargs)

        # Compute hash
        self.rehash()

    @abstractmethod
    def initialize(self, **kwargs) -> None:
        """Initialize function-specific parameters"""
        pass

    @abstractmethod
    def get_design_ts(self, ts: np.ndarray) -> np.ndarray:
        """Generate design matrix for given time series"""
        pass

    @abstractmethod
    def rehash(self) -> None:
        """Recompute hash value for change detection"""
        pass

    def eval(self, component: int,
             override_time_vector: np.ndarray = None,
             override_params: np.ndarray = None):

        if override_time_vector is not None:
            design = self.get_design_ts(override_time_vector)
        else:
            design = self.design

        if override_params is not None:
            return design @ override_params[component]
        else:
            return design @ self.p.params[component]

    def load_parameters(self, etm_results: List[AdjustmentResults]) -> None:
        """Load estimated parameters and their uncertainties"""
        # set to None, initialize before assigning values
        self.p.params = [np.array([])] * 3
        self.p.sigmas = [np.array([])] * 3
        self.p.covar = [np.array([])] * 3

        for i in range(3):
            self.p.params[i] = etm_results[i].parameters[self.column_index]
            self.p.sigmas[i] = etm_results[i].parameter_sigmas[self.column_index]
            if len(etm_results[i].covariance_matrix) > 0:
                # when an ETM is saved and loaded (from the db) the covariance is not transferred
                # avoid errors because of this
                self.p.covar[i] = etm_results[i].covariance_matrix[self.column_index][:, self.column_index]

    @abstractmethod
    def print_parameters(self, **kwargs) -> Tuple[list, list, list]:
        pass

    def validate_parameters(self) -> List[Tuple['EtmFunction', str]]:
        """Validate parameter values and return issues"""
        issues = []

        if self.p.params and len(self.p.params) > 0:
            if np.any(np.isnan(self.p.params[0])):
                issues.append((self, f"{self.__class__.__name__}: NaN values in parameters"))
            if np.any(np.isinf(self.p.params[0])):
                issues.append((self, f"{self.__class__.__name__}: Infinite values in parameters"))

        return issues

    def validate_design(self) -> List[Tuple['EtmFunction', str]]:
        issues = []
        return issues

    def get_parameter_dict(self) -> Dict[str, Any]:
        """Get parameters as dictionary for database"""
        parameter_dict = asdict(self.p)

        parameter_dict['frequencies'] = parameter_dict['frequencies'].tolist()
        parameter_dict['relaxation'] = parameter_dict['relaxation'].tolist()

        params0 = parameter_dict['params'][0]
        if isinstance(params0, np.ndarray) and params0.size:
            parameter_dict['params'] = [i.tolist() for i in parameter_dict['params']]
            parameter_dict['sigmas'] = [i.tolist() for i in parameter_dict['sigmas']]
            parameter_dict['covar'] = [i.tolist() for i in parameter_dict['covar']]
        else:
            parameter_dict['params'] = []
            parameter_dict['sigmas'] = []
            parameter_dict['covar'] = []

        parameter_dict['NetworkCode'] = self.config.network_code
        parameter_dict['StationCode'] = self.config.station_code
        parameter_dict['soln'] = self.config.solution.solution_type.code
        parameter_dict['stack'] = self.config.solution.stack_name

        return parameter_dict

    def configure_behavior(self, behavior_config: Dict[str, Any]) -> None:
        """Configure function behavior based on conditions"""
        # This method allows aggregating behavior-changing conditions
        # within each function object as requested

        for condition, value in behavior_config.items():
            if condition == 'fit_enabled':
                self.fit = bool(value)
            elif condition == 'custom_metadata':
                self.p.metadata = value
            elif hasattr(self, condition):
                setattr(self, condition, value)

    def to_json(self):
        return asdict(self.p)

    def load_from_json(self, json_file):
        data = load_json(json_file)

        if data['object'] == self.p.object:
            self.p = EtmFunctionParameterVector(**data)
        else:
            raise EtmFunctionException('object type mismatch when loading from json')

    @abstractmethod
    def short_name(self) -> str:
        pass

