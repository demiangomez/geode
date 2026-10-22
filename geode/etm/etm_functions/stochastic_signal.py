from typing import List, Tuple
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ...Utils import crc32
from ...pyDate import Date
from ..etm_functions.etm_function import EtmFunction
from ..core.etm_config import EtmConfig
from ..core.data_classes import AdjustmentResults
from ..core.type_declarations import AdjustmentModels


class StochasticSignal(EtmFunction):
    """Special EtmFunction to store stochastic signal values"""
    def __init__(self, config: EtmConfig,
                 time_vector_cont_mjd: np.ndarray = np.array([0]),
                 stochastic_signal: List[np.ndarray] = np.array([0]), **kwargs):

        super().__init__(config, **kwargs)

        self.p.params = stochastic_signal
        self._time_vector_cont_mjd = time_vector_cont_mjd
        self.fit = False

        self.rehash()

    def initialize(self, **kwargs) -> None:
        self.p.object = 'stochastic'
        self.p.sigmas = [np.array([0.])] * 3

    def rehash(self) -> None:
        """Recompute hash value for change detection"""
        if hasattr(self, '_time_vector_cont_mjd'):
            lsq = self.config.modeling.least_squares_strategy
            # the noise covariance GLS does not use covariance_function: identify it by the adjustment model
            if lsq.adjustment_model == AdjustmentModels.NOISE_COVARIANCE_GLS:
                model = lsq.adjustment_model.description
            else:
                model = lsq.covariance_function.description
            self.p.hash = crc32(f'stochastic_signal'
                                f'{self._time_vector_cont_mjd.min()}'
                                f'{self._time_vector_cont_mjd.min()}'
                                f'{model}')
        else:
            self.p.hash = crc32(f'stochastic_signal')

    def get_design_ts(self, ts: np.ndarray) -> np.ndarray:
        """Generate design matrix for given time series"""
        # when a new time vector is introduced, figure out the overlap between the ts
        # function parameter and as _time_vector_mjd to select the values from the "design matrix"

        mjd = np.array([Date(fyear=date).mjd for date in ts])
        mask = np.isin(self._time_vector_cont_mjd, mjd)

        return self.p.params[:][mask]

    def eval(self, component: int,
             override_time_vector: np.ndarray = None,
             override_params: np.ndarray = None):

        mask = np.isin(self._time_vector_cont_mjd, override_time_vector)

        return self.p.params[component][mask]

    def load_parameters(self, etm_results: List[AdjustmentResults]) -> None:
        """Load estimated parameters and their uncertainties"""
        pass

    def print_parameters(self, **kwargs) -> Tuple[list, list, list]:
        pass

    def short_name(self) -> str:
        return 'STOCHASTIC'

    def __str__(self) -> str:
        """String representation for debugging"""
        return f'length: {self._time_vector_cont_mjd.size}'

    def __repr__(self) -> str:
        return f"StochasticSignal({str(self)})"