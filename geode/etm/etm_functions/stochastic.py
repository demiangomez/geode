from typing import List, Tuple
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ...Utils import crc32
from ..etm_functions.etm_function import EtmFunction
from ..core.etm_config import EtmConfig
from ..core.data_classes import AdjustmentResults
from ..core.type_declarations import AdjustmentModels

# row index of each part within self.p.params[component] (shape (2, n): see Stochastic)
_SIGNAL_ROW = 0
_NOISE_ROW = 1


class Stochastic(EtmFunction):
    """
    Special EtmFunction to store the stochastic component of a LSQ_COLLOCATION / NOISE_COVARIANCE_GLS
    fit, split into two independently-removable parts. self.p.params[component] is a (2, n) array,
    n = time_vector_cont_mjd.size: row _SIGNAL_ROW is the signal, row _NOISE_ROW is the noise. Both
    rows are saved to/restored from the DB together (object='stochastic'), like any other function's
    params.
      - signal (eval()): the collocation-predicted, correlated part (periodic terms plus power laws
        with alpha >= noise_signal_alpha_min for GLS; the entire collocation prediction for
        LSQ_COLLOCATION). Defined on every day of time_vector_cont_mjd.
      - noise (eval_noise()): for GLS, the collocation-predicted power laws with alpha <
        noise_signal_alpha_min only -- no periodic terms, and deliberately excluding white noise
        (see noise_covariance.estimate_noise_model: white noise has no meaningful predictor away
        from the data it was observed at). For LSQ_COLLOCATION there is no such band below the
        single colored kernel, so this is always zero.
    """
    def __init__(self, config: EtmConfig,
                 time_vector_cont_mjd: np.ndarray = np.array([0]),
                 stochastic_signal: List[np.ndarray] = None,
                 stochastic_noise: List[np.ndarray] = None, **kwargs):

        super().__init__(config, **kwargs)

        self._time_vector_cont_mjd = time_vector_cont_mjd
        self.fit = False

        self.set_params(stochastic_signal, stochastic_noise)

        self.rehash()

    def set_params(self, stochastic_signal: List[np.ndarray] = None,
                   stochastic_noise: List[np.ndarray] = None) -> None:
        """
        Store signal and noise together as self.p.params[component], a (2, n) array per
        component. Normalizes missing/stale entries (e.g. an older cached result, or a DB row
        saved before the signal/noise split existed) to zero, so eval()/eval_noise() always get a
        real (2, n) array to index into.
        """
        n = np.zeros_like(self._time_vector_cont_mjd, dtype=float)

        def row(values: np.ndarray) -> np.ndarray:
            return values if isinstance(values, np.ndarray) and values.shape == n.shape else n

        signal = [None] * 3 if stochastic_signal is None else stochastic_signal
        noise = [None] * 3 if stochastic_noise is None else stochastic_noise
        self.p.params = [np.vstack([row(signal[i]), row(noise[i])]) for i in range(3)]

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
        """Unreachable: self.fit is always False, so DesignMatrix.matrix (design_matrix.py) never
        calls this -- Stochastic overrides eval() directly instead. Kept only to satisfy the
        abstract base class; returns the same "no columns" shape DesignMatrix uses for an inactive
        function, rather than reproducing the original implementation here (which predated this
        class overriding eval(), and could not have worked: it indexed the plain list self.p.params
        with a boolean array)."""
        return np.empty((len(ts), 0))

    def eval(self, component: int,
             override_time_vector: np.ndarray = None,
             override_params: np.ndarray = None):

        mask = np.isin(self._time_vector_cont_mjd, override_time_vector)

        return self.p.params[component][_SIGNAL_ROW][mask]

    def eval_noise(self, component: int,
                   override_time_vector: np.ndarray = None):
        """Evaluate the noise complement at the given epochs (zero where there was no observation)"""
        mask = np.isin(self._time_vector_cont_mjd, override_time_vector)

        return self.p.params[component][_NOISE_ROW][mask]

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
        return f"Stochastic({str(self)})"
