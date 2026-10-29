"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/15/25 8:56 AM
Author: Demian D. Gomez
"""

from abc import ABC, abstractmethod
from typing import List, Tuple

from scipy.optimize import curve_fit
from scipy.signal import lombscargle
from scipy.stats import chi2
from scipy.linalg import cho_factor, cho_solve, toeplitz
from time import time
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ..least_squares.design_matrix import DesignMatrix, DesignMatrixException
from ..core.etm_config import EtmConfig
from ..etm_functions.jumps import JumpFunction
from ..core.type_declarations import JumpType, AdjustmentModels, NoiseModels, FitStatus, CovarianceFunction
from ..core.data_classes import AdjustmentResults
from ..data.solution_data import SolutionData
from ..least_squares.ls_collocation import gaussian_func
from ..least_squares.noise_covariance import estimate_noise_model, NoiseCovarianceSettings, DETECTION_METHODS


class AdjustmentStrategyException(Exception):
    pass


class WeightBuilder(ABC):
    """Abstract base class for different weighting strategies"""
    def __init__(self, **kwargs):
        self.matrix = np.array([])
        pass

    @classmethod
    def create_instance(cls, noise_model: NoiseModels = NoiseModels.WHITE_ONLY,
                        observation_count: int = 0) -> 'WeightBuilder':
        """Determine the type of object needed and return it to the called"""

        if noise_model == NoiseModels.WHITE_ONLY:
            instance = WhiteNoise(observation_count)
        else:
            raise Exception('Noise model not implemented')

        return instance

    @abstractmethod
    def update_weights(self, new_weights):
        pass

    @abstractmethod
    def weight_observations(self, observations) -> np.ndarray:
        pass

    @abstractmethod
    def weight_design(self, design_matrix) -> np.ndarray:
        pass


class AdjustmentStrategy(ABC):
    """Abstract base class for different adjustment algorithms"""
    # Lomb-Scargle periodogram and spectral index of the residuals (and of the stochastic signal). TEMPORARILY OFF:
    # 0.3-0.7 s per call, up to 3 calls per component; nothing downstream uses them (they are only stored in the
    # results). When off, spectral_index_* stay 0 and periodogram_* stay empty
    compute_periodogram = False

    def __init__(self, config: EtmConfig):
        self.so = 1
        self.dof = 0
        self.x1 = 0
        self.x2 = 0
        self.config = config

    @abstractmethod
    def adjust(self, design_matrix: DesignMatrix, observations: np.ndarray,
               weights: WeightBuilder, **kwargs) -> AdjustmentResults:
        """Perform the least squares adjustment"""
        pass

    @staticmethod
    def component_label(kwargs: dict) -> str:
        """N, E or U from the component index passed by EtmFit.run_fit (- if not given)"""
        return 'NEU'[kwargs['component']] if kwargs.get('component') in (0, 1, 2) else '-'

    @classmethod
    def create_instance(cls, config: EtmConfig) -> 'AdjustmentStrategy':
        """Determine the type of object needed and return it to the called"""

        strategy = config.modeling.least_squares_strategy.adjustment_model
        # Determine which subclass to create based on solution type
        if strategy == AdjustmentModels.ROBUST_LEAST_SQUARES:
            instance = RobustLeastSquares(config)
        elif strategy == AdjustmentModels.LSQ_COLLOCATION:
            instance = LeastSquaresCollocation(config)
        elif strategy == AdjustmentModels.NOISE_COVARIANCE_GLS:
            instance = NoiseCovarianceGLS(config)
        else:
            raise ValueError(f"Adjustment strategy type "
                             f"{strategy.description} not implemented")

        return instance

    @staticmethod
    def compute_plomb(residuals, time_vector_mjd) -> float:
        # estimate the spectral index of the residuals
        T_total = np.max(time_vector_mjd) - np.min(time_vector_mjd)  # Total duration in days
        dt_min = 1  # Minimum sampling interval in days

        # Define frequency range
        f_min = 1 / T_total  # or 2/T_total for more conservative estimate
        f_max = 1 / (2 * dt_min)  # Nyquist frequency = 0.5 cycles/day

        # Create frequency vector
        fvec = np.linspace(f_min, f_max, 2000)  # 1000 frequency points
        angular_freqs = 2 * np.pi * fvec
        pxx = lombscargle(time_vector_mjd, residuals, angular_freqs)

        # Linear fit to the LogLog spectrum plot
        fit_psd = np.polyfit(np.log10(fvec), np.log10(pxx), 1)

        return fit_psd[0], fvec, pxx  # spectral index, frequency vector, amplitude vector


class WhiteNoise(WeightBuilder):
    """Implementation of WeightBuilder for different noise processes"""
    def __init__(self, observation_count: int = 0):
        super().__init__()
        self.matrix = np.diag(np.ones((observation_count,)))
        self._w = np.ones((observation_count,))

    def update_weights(self, new_weights):
        # check for weights <<< than eps, otherwise matrix may become singular
        new_weights[new_weights < np.finfo(float).eps] = np.finfo(float).eps
        if len(new_weights.shape) > 1:
            self.matrix = new_weights
            self._w = np.sqrt(np.diag(new_weights))
        else:
            self.matrix = np.diag(new_weights)
            self._w = np.sqrt(new_weights)

    def weight_observations(self, observations) -> np.ndarray:
        """
        function to multiply the observations and weights
        """
        return self._w * observations

    def weight_design(self, design_matrix):
        """
        function to multiply the design matrix and weights
        """
        return self._w[:, np.newaxis] * design_matrix


class EmpiricalCovariance:
    def __init__(self, time_vector_mjd: np.ndarray,
                 residuals: np.ndarray,
                 obs_sigmas: np.ndarray,
                 outlier_flags: np.ndarray,
                 function: CovarianceFunction = CovarianceFunction.GAUSSIAN,
                 arma_roots: int = 10,
                 arma_points: int = 500
                 ):

        logger.debug(f'EmpiricalCovariance: {function.description}')

        self.time_vector_mjd = time_vector_mjd
        self.obs_sigmas = obs_sigmas
        self.outlier_flags = outlier_flags

        # compute the empirical covariance values
        self.bins_mjd, self.bins_fyr, self.empirical_covariance = (
            self._compute_empirical_covariance(time_vector_mjd[outlier_flags], residuals[outlier_flags]))

        self.method = function
        self.params = np.array([])
        self.n = arma_points
        self.roots = arma_roots
        self.empirical_func = function.get_function
        self.arma_failed = False

        if self.method != CovarianceFunction.ARMA:
            self.params, _ = self.fit_empirical_covariance(self.empirical_func)

        self.crr = np.diag(obs_sigmas ** 2)

    @ staticmethod
    def _compute_empirical_covariance(time_vector_mjd: np.ndarray, data: np.ndarray):
        n = int(np.floor(time_vector_mjd.size / 2))

        # Normalize time indices
        tx = (time_vector_mjd - np.min(time_vector_mjd) + 1).astype(int) - 1  # Convert to 0-based indexing

        # Outer product with upper triangular
        dd = np.triu(np.outer(data, data))

        # Create time matrix
        time_range = np.max(time_vector_mjd) - np.min(time_vector_mjd) + 1
        tc = np.zeros((time_range, time_range))
        tc[np.ix_(tx, tx)] = dd

        # Calculate covariance along diagonals
        max_offset = min(n, tc.shape[0] - 1)
        cov = np.zeros(max_offset)

        for k in range(max_offset):
            diag_vals = np.diagonal(tc, offset=k)
            diag_vals = diag_vals[diag_vals != 0]  # Remove zeros
            if len(diag_vals) > 0:
                cov[k] = np.mean(diag_vals)

        # Create bins
        bins_mjd = np.arange(max_offset)
        bins_fyr = np.arange(max_offset) / 365.25

        return bins_mjd, bins_fyr, cov

    def get_covariance_matrix(self, time_vector_mjd: np.ndarray):

        if self.method == CovarianceFunction.ARMA and not self.arma_failed:
            roots = self.roots
            while roots > 3:
                try:
                    pk, Ak = self.arma_covariance(roots, self.n)
                    csz, empirical_cov_function = self.arma_compute(pk, Ak, time_vector_mjd)

                    np.linalg.cholesky(csz)
                    logger.debug(f'EmpiricalCovariance: Matrix is symmetric positive definite with {roots} roots '
                                 f'and {self.n} points')
                    # find which columns of csz to remove (because there are no observations on those positions)
                    # those columns also need to be removed as rows to form css
                    idx = np.isin(time_vector_mjd, self.time_vector_mjd)
                    # fill the params with the cov function
                    self.params = empirical_cov_function
                    # removing those columns and rows essentially gives the observation covariance matrix (css)
                    return csz[np.ix_(idx, idx)], csz[:, idx]
                except ValueError:
                    logger.debug(f'EmpiricalCovariance: Bad roots found for {roots} roots and {self.n} points, '
                                 f'reducing and trying again')
                    roots -= 1
                except np.linalg.LinAlgError:
                    # let cholesky raise an error if not positive definite
                    logger.debug(f'EmpiricalCovariance: Matrix is not symmetric positive definite for {roots} roots '
                                 f'and {self.n} points, reducing and trying again')
                    roots -= 1

            # if we got here is because we could not find a valid covariance matrix
            logger.debug('EmpiricalCovariance: Could not find symmetric positive definite matrix using ARMA, '
                         'switching to gaussian covariance')
            self.arma_failed = True
            self.params, _ = self.fit_empirical_covariance(self.empirical_func)

        if self.method in (CovarianceFunction.GAUSSIAN, CovarianceFunction.CAUCHY) or self.arma_failed:
            # get the lags of all the data to build the interpolation matrix (csz)
            lag = np.abs(time_vector_mjd - time_vector_mjd[:, np.newaxis])
            csz = self.empirical_func(lag, *self.params)
            # find which columns of csz to remove (because there are no observations on those positions)
            # those columns also need to be removed as rows to form css
            idx = np.isin(time_vector_mjd, self.time_vector_mjd)
            # removing those columns and rows essentially gives the observation covariance matrix (css)
            return csz[np.ix_(idx, idx)], csz[:, idx]
        else:
            raise ValueError('Method ' + self.method.description + ' not implemented')

    def arma_covariance(self, p: int = 10, n: int = 500):
        # equation 14c of
        # Schubert, T., Korte, J., Brockmann, J. M., & Schuh, W.-D. (2020). A Generic Approach to Covariance Function
        # Estimation Using ARMA-Models. Mathematics, 8(4), 591. https://doi.org/10.3390/math8040591
        # empirical_covariance[0] is the first point (with nugget effect) and needs to be
        # excluded

        e = self.empirical_covariance[1:n + 1]
        A = np.zeros((n - p, p))
        for i in range(p):
            A[:, i] = e[p - i - 1:n - i - 1]

        L = e[p:n]

        # alpha values
        alpha = np.linalg.lstsq(A, L, rcond=None)[0]

        # find the roots of eq 11
        coeffs = np.concatenate(([1], -alpha))
        pk = np.roots(coeffs)

        # all nks need to be < 1
        nk = np.abs(pk)

        if np.any(nk >= 1):
            raise ValueError('Bad roots in ARMA found!')

        # now build the matrix to find the Ap coefficients
        tau = np.arange(n + 1).reshape(-1, 1)
        Gk = pk.reshape(1, -1) ** tau
        Lk = self.empirical_covariance[0:n + 1]

        # find the Ak terms
        Ak = np.linalg.lstsq(Gk, Lk, rcond=None)[0]

        return pk, Ak

    @staticmethod
    def arma_compute(pk, Ak, time_vector_mjd: np.ndarray):
        tau = time_vector_mjd - np.min(time_vector_mjd)
        tau = tau.reshape(-1, 1)  # Make it a column vector
        Gk = pk.reshape(1, -1) ** tau

        # output the modeled covariance as a vector
        empirical_cov_function = np.real(Gk @ Ak)

        # output the matrix
        css = toeplitz(np.real(Gk @ Ak).flatten())

        return css, empirical_cov_function

    def fit_empirical_covariance(self, empirical_func=gaussian_func):
        """ Estimate the covariance of the signals.
            The given residuals are used to calculate the covariance of the signals.
        """

        init_scale = np.floor(np.log10(np.abs(self.bins_mjd).max()))

        if empirical_func.__name__ == 'gaussian_func':
            init_values = [self.empirical_covariance[0], 10 ** (init_scale - 1), 10 ** (init_scale - 1)]
        elif empirical_func.__name__ == 'exponential_func':
            init_values = [self.empirical_covariance[0], 10 ** (init_scale)]
        else:
            init_values = [self.empirical_covariance[0], 10 ** init_scale]

        params, _, opt = curve_fit(empirical_func, self.bins_mjd, self.empirical_covariance,
                                    p0=init_values, full_output=True)[0:3]

        rms_params = np.sqrt((opt['fvec'] ** 2).mean())

        return params, rms_params


class RobustLeastSquares(AdjustmentStrategy):
    """Robust least squares with iterative reweighting"""

    def adjust(self, design_matrix: DesignMatrix, observations: np.ndarray,
               weights: WeightBuilder, time_vector_mjd: np.ndarray = None,
               n_neq_left: np.ndarray = 0, c_neq_right: np.ndarray = 0, **kwargs) -> AdjustmentResults:
        """
        Robust least squares adjustment with outlier detection and reweighting
        """
        results = AdjustmentResults()
        # get design matrix once to avoid multiple computations
        a = design_matrix.matrix

        wrms = 1
        limit = self.config.modeling.least_squares_strategy.sigma_filter_limit
        iterations = self.config.modeling.least_squares_strategy.iterations

        self.dof = (a.shape[0] - a.shape[1]) + design_matrix.total_constraints

        if self.dof == 0:
            raise AdjustmentStrategyException('Degrees of freedom == 0. Cannot fit model.')

        self.x1 = chi2.ppf(0.05 / 2, self.dof)
        self.x2 = chi2.ppf(1 - 0.05 / 2, self.dof)

        s = np.array([])

        for j in range(iterations):
            # save iteration number
            results.iterations = j + 1

            aw = weights.weight_design(a)
            lw = weights.weight_observations(observations)

            results.parameters = np.linalg.solve(aw.T @ aw + n_neq_left, aw.T @ lw + c_neq_right)
            results.residuals = observations - a @ results.parameters

            v = results.residuals
            p = weights.matrix
            # unit variance
            self.so = float(np.sqrt((v @ p @ v) / self.dof))
            # save in results
            results.variance_factor = float(np.square(self.so))

            x = np.power(self.so, 2) * self.dof

            # obtain the overall uncertainty predicted by lsq
            wrms = wrms * self.so
            results.wrms = wrms

            # calculate the normalized sigmas
            s = np.abs(np.divide(v, wrms))

            logger.debug(f'RobustLeastSquares sqrt(so): {self.so:.4f} it: {j:2d} pass: {self.x1 <= x <= self.x2}')

            # DDG: took this condition out of the chi2 if so that weights get
            # updated using the latest so value
            # reweigh by Mike's method of equal weight until 2 sigma
            f = np.ones((v.shape[0],))
            # f[s > LIMIT] = 1. / (np.power(10, LIMIT - s[s > LIMIT]))
            # do not allow sigmas > 1/eps m, which is basically not putting
            # the observation in. This is to avoid unstable inversions
            sw = np.power(10, limit - s[s > limit])
            sw[sw < np.finfo(float).eps] = np.finfo(float).eps
            f[s > limit] = sw

            weights.update_weights(np.square(np.divide(f, wrms)))

            if self.x1 <= x <= self.x2:
                # if it falls in here it's because it did pass the Chi2 test
                results.converged = True
                break

        # iterations done, compute statistics
        try:
            if not results.converged:
                logger.info(f'RobustLeastSquares [{self.component_label(kwargs)}] did not converge! '
                            f'final wrms {wrms/1000:.1f} mm')

            results.covariance_matrix = np.linalg.inv(a.T @ weights.matrix @ a) * (self.so ** 2)
        except np.linalg.LinAlgError as e:
            logger.info('np.linalg.inv failed to obtain covariance matrix: %s' % str(e))
            results.covariance_matrix = np.ones((design_matrix.matrix.shape[1],
                                                 design_matrix.matrix.shape[1]))

        # extract the parameter sigmas
        if np.any(np.diag(results.covariance_matrix) <= 0):
            logger.warning(f'Invalid covariance matrix: '
                           f'design matrix condition number {design_matrix.condition_number:.1f}')
            results.parameter_sigmas = np.ones(design_matrix.matrix.shape[1])
        else:
            results.parameter_sigmas =  np.sqrt(np.diag(results.covariance_matrix))
        # mark observations with sigma <= LIMIT
        results.outlier_flags = s <= limit
        results.obs_sigmas = np.sqrt(1 / np.diag(weights.matrix))

        # compute spectral index of residuals
        if self.compute_periodogram:
            si, fvec, pxx = self.compute_plomb(results.residuals[results.outlier_flags],
                                               time_vector_mjd[results.outlier_flags])
            results.spectral_index_random_noise  = si
            results.periodogram_frequencies      = fvec
            results.periodogram_power            = pxx
            logger.info(f'Spectral index of residuals: {si:.4f}')

        # declare the origin of the fit
        results.origin = 'RobustLeastSquares'
        # log the results for inspection by user
        logger.debug(f'{" ":<28}: ' +
                     ' '.join([funct.short_name() for funct in design_matrix.functions if funct.fit]))
        logger.debug(f'{"RobustLeastSquares [mm]":>28}: ' +
                     ' '.join([f'{p*1000.:10.3e}' for p in results.parameters.tolist()]))

        logger.info(f'RobustLeastSquares [{self.component_label(kwargs)}] rejected outliers: '
                    f'{np.sum(~results.outlier_flags) / results.outlier_flags.shape[0] * 100:.1f}%')
        return results


class LeastSquaresCollocation(AdjustmentStrategy):

    def adjust(self, design_matrix: DesignMatrix, observations: np.ndarray,
               weights: WeightBuilder,
               time_vector_mjd: np.ndarray = None,
               time_vector_cont_mjd: np.ndarray = None,
               n_neq_left: np.ndarray = 0,
               c_neq_right: np.ndarray = 0,
               **kwargs) -> AdjustmentResults:

        tt = time()
        a = design_matrix.matrix
        limit = self.config.modeling.least_squares_strategy.sigma_filter_limit

        # first need to run the RobustAdjustment to get residuals
        lsq = RobustLeastSquares(self.config)
        white_noise = WhiteNoise(observations.size)

        results = lsq.adjust(design_matrix, observations, white_noise, time_vector_mjd, n_neq_left, c_neq_right,
                             component=kwargs.get('component'))
        wrms = results.wrms
        ###################################################################################################
        # now use residuals to run lsc
        self.dof = (a.shape[0] - a.shape[1]) + design_matrix.total_constraints

        if self.dof == 0:
            raise AdjustmentStrategyException('Degrees of freedom == 0. Cannot fit model.')

        # find the parameters
        results = self._least_squares(a, observations, results,
                                      time_vector_mjd, time_vector_cont_mjd,
                                      n_neq_left, c_neq_right)

        # save covariance parameters
        #results.covariance_function_params = empirical_cov.params
        #results.empirical_covariance = np.array([empirical_cov.bins_mjd, empirical_cov.empirical_covariance])
        results.wrms = wrms * self.so
        results.variance_factor = self.so ** 2
        #results = self._least_squares_chunks(a, observations, results, time_vector_mjd, time_vector_cont_mjd)

        #results.parameters = np.linalg.inv(a.T @ w @ a) @ (a.T @ w @ observations)
        logger.debug(f'LeastSquaresCollocation least squares in {time() - tt:.3f} s '
                     f'sigma_o: {self.so:.3f} '
                     f'wrms: {results.wrms * 1000.} mm')

        s = np.abs(np.divide(results.residuals, results.wrms))

        results.parameter_sigmas = np.sqrt(np.diag(results.covariance_matrix))

        logger.debug(f'LeastSquaresCollocation [mm]: ' +
                     ' '.join([f'{p*1000.:10.3e}' for p in results.parameters.tolist()]))

        # mark observations with sigma <= LIMIT
        results.outlier_flags = s <= limit

        # compute spectral index of residuals
        if self.compute_periodogram:
            si, fvec, pxx = self.compute_plomb(results.residuals[results.outlier_flags],
                                               time_vector_mjd[results.outlier_flags])
            results.spectral_index_random_noise  = si
            results.periodogram_frequencies      = fvec
            results.periodogram_power            = pxx
            logger.info(f'Spectral index of residuals: {si:.4f}')

            si, _, _ = self.compute_plomb(results.stochastic_signal, time_vector_cont_mjd)
            results.spectral_index_stochastic_noise = si
            logger.info(f'Spectral index of stochastic noise: {si:.4f}')

        # declare the origin of the fit
        results.origin = 'LeastSquaresCollocation'

        logger.debug(f'LeastSquaresCollocation in {time() - tt:.3f} s')
        logger.info(f'LeastSquaresCollocation [{self.component_label(kwargs)}] rejected outliers: '
                    f'{np.sum(~results.outlier_flags) / results.outlier_flags.shape[0] * 100:.1f}%')

        return results

    def _least_squares(self, a, observations,
                       rls,
                       time_vector_mjd,
                       time_vector_cont_mjd,
                       n_neq_left: np.ndarray = 0,
                       c_neq_right: np.ndarray = 0):
        """
        Compute all quantities that depend on w = inv(cov) efficiently
        """
        # find where the covariance drops below eps
        idx = int(a.shape[0] / 1)  # np.where(cov[0, :] <= (np.finfo(float).eps / 1000))[0].min()

        results = AdjustmentResults()

        # remainder
        r = np.mod(a.shape[0], idx)
        # idx parts to split the matrix
        parts = int(np.floor(a.shape[0] / idx))
        # actual chunks
        chunks = parts * [idx]
        if r > 0:
            # add the remainder to the last entry
            chunks[-1] += r

        n = []
        c = []
        p = []
        w = []
        z = []
        s = 0
        for i, e in enumerate(np.cumsum(chunks).tolist()):
            logger.debug(f'LeastSquaresCollocation: processing covariance block {i}')

            actual_end = e - 1
            block_size = actual_end - s + 1

            tv = time_vector_mjd[s:actual_end + 1]
            tc = time_vector_cont_mjd[np.logical_and(time_vector_cont_mjd >= tv.min(),
                                                     time_vector_cont_mjd <= tv.max())]
            empirical_cov = EmpiricalCovariance(tv,
                                                rls.residuals[s:actual_end + 1],
                                                rls.obs_sigmas[s:actual_end + 1],
                                                rls.outlier_flags[s:actual_end + 1],
                                                self.config.modeling.least_squares_strategy.covariance_function,
                                                arma_roots=self.config.modeling.least_squares_strategy.arma_roots,
                                                arma_points=self.config.modeling.least_squares_strategy.arma_points)

            css, csz = empirical_cov.get_covariance_matrix(tc)
            cov = css + empirical_cov.crr
            # cov = css + np.eye(block_size) * rls.wrms ** 2

            ap = a[s:actual_end + 1, :]
            # Solve with correct eye matrix size
            p.append(np.linalg.solve(cov, np.eye(block_size)))
            w.append(csz)
            z.append(css)

            n.append(ap.T @ p[-1] @ ap)
            c.append(ap.T @ p[-1] @ observations[s:actual_end + 1])

            # Next chunk starts after current chunk ends
            s = e  # or s = actual_end + 1

        # stack n and c
        n = np.sum(n, axis=0)
        c = np.sum(c, axis=0)

        results.parameters = np.linalg.solve(n + n_neq_left, c + c_neq_right)

        # residuals
        v = observations - a @ results.parameters

        # create the vector to store the stochastic signal
        results.stochastic_signal = np.zeros_like(time_vector_cont_mjd, dtype=float)
        results.obs_sigmas = np.zeros_like(time_vector_mjd, dtype=float)
        results.residuals = np.zeros_like(time_vector_mjd, dtype=float)
        # Solve for stochastic signal and variance factor
        s = 0
        omega = 0
        for i, e in enumerate(np.cumsum(chunks).tolist()):
            actual_end = e - 1
            tv = time_vector_mjd[s:actual_end + 1]
            idx = np.logical_and(time_vector_cont_mjd >= tv.min(),
                                 time_vector_cont_mjd <= tv.max())
            vp = v[s:actual_end + 1]
            results.obs_sigmas[s:actual_end + 1] = np.sqrt(1 / np.diag(p[i]))

            results.stochastic_signal[idx] = w[i] @ p[i] @ vp
            results.residuals[s:actual_end + 1] = vp - z[i] @ p[i] @ vp
            omega += vp.T @ p[i] @ vp
            s = e

        # wrms of the database
        self.so = np.sqrt(np.sum(omega) / self.dof)
        # results.wrms = np.sqrt((results.residuals.T @ results.residuals) / self.dof) * self.so

        results.covariance_matrix = np.linalg.solve(n, np.eye(results.parameters.shape[0])) * (self.so ** 2)

        return results


class NoiseCovarianceGLS(AdjustmentStrategy):
    """
    GLS adjustment with a noise covariance estimated from the residuals (see noise_covariance.py): a
    non-negative mix of power laws (alpha = 0.5 ... 1.5) and white noise fitted to the empirical covariance
    with projected kernels, plus automatically detected periodic terms. RobustLeastSquares flags the
    outliers first; outliers are excluded from the noise model and the GLS (treated as missing days).
    The stochastic signal is the collocation prediction of the detected periodic terms plus the power laws with
    alpha >= 1 (flicker and steeper); flatter power laws and the white noise stay in the residuals.
    """
    # GeoDE works in meters, the noise model in mm (the units of the method's spec)
    scale = 1000.

    def __init__(self, config: EtmConfig):
        super().__init__(config)
        lsq = config.modeling.least_squares_strategy
        if lsq.noise_detection not in DETECTION_METHODS:
            raise ValueError(f'NoiseCovarianceGLS: unknown detection {lsq.noise_detection!r}, '
                             f'use one of {DETECTION_METHODS}')
        # the noise model works in mm: the minimum white variance is the floor squared [mm^2]
        self.settings = NoiseCovarianceSettings(variance_factors=lsq.noise_variance_factors,
                                                white_min=lsq.noise_white_floor_mm ** 2,
                                                detection=lsq.noise_detection)
        if lsq.noise_snr_min is not None:
            if lsq.noise_snr_min <= 0:
                raise ValueError(f'NoiseCovarianceGLS: the SNR threshold must be positive ({lsq.noise_snr_min})')
            # only the threshold of the detection in use matters
            self.settings.jhf_snr_min = self.settings.sgs_snr_min = float(lsq.noise_snr_min)

    def adjust(self, design_matrix: DesignMatrix, observations: np.ndarray,
               weights: WeightBuilder,
               time_vector_mjd: np.ndarray = None,
               time_vector_cont_mjd: np.ndarray = None,
               n_neq_left: np.ndarray = 0,
               c_neq_right: np.ndarray = 0,
               **kwargs) -> AdjustmentResults:

        tt = time()
        a = design_matrix.matrix
        s = self.scale
        # component label for the screen output
        comp = self.component_label(kwargs)

        # robust fit to flag the outliers (and fallback if the noise model cannot be estimated)
        rls = RobustLeastSquares(self.config).adjust(design_matrix, observations, WhiteNoise(observations.size),
                                                     time_vector_mjd, n_neq_left, c_neq_right,
                                                     component=kwargs.get('component'))

        self.dof = (a.shape[0] - a.shape[1]) + design_matrix.total_constraints

        inliers = rls.outlier_flags
        if np.sum(inliers) <= a.shape[1]:
            logger.warning('NoiseCovarianceGLS: not enough inliers to estimate the noise model, '
                           'keeping the robust fit')
            return rls

        mjd = np.rint(time_vector_mjd).astype(int)
        mjd0 = mjd[inliers][0]
        if np.any(np.diff(mjd[inliers]) <= 0):
            logger.warning('NoiseCovarianceGLS: epochs are not unique days in increasing order, '
                           'keeping the robust fit')
            return rls

        has_constraints = bool(np.any(n_neq_left))
        try:
            # step-1 weights are those of the robust fit (equal weights for the inliers)
            nm = estimate_noise_model(mjd[inliers] - mjd0, observations[inliers] * s, a[inliers],
                                      p0=1 / (rls.obs_sigmas[inliers] * s) ** 2,
                                      irate=self._rate_column(design_matrix),
                                      settings=self.settings,
                                      n_neq=n_neq_left / s ** 2 if has_constraints else None,
                                      c_neq=c_neq_right / s if has_constraints else None)
        except (np.linalg.LinAlgError, ValueError) as e:
            logger.warning(f'NoiseCovarianceGLS: could not estimate the noise model ({e}), '
                           f'keeping the robust fit')
            return rls

        gls = nm.gls
        results = AdjustmentResults()
        results.iterations = rls.iterations
        results.converged = True
        results.parameters = gls.x / s
        # covariance of the GLS, not scaled by the variance factor (which is ~1 by construction)
        results.covariance_matrix = gls.qx / s ** 2
        results.parameter_sigmas = np.sqrt(np.diag(results.covariance_matrix))
        results.variance_factor = float(gls.factors[2])
        self.so = np.sqrt(results.variance_factor)

        # signal on the continuous time vector and at the observations (zero outside the span
        # of the inliers); residuals have the signal removed, as in LeastSquaresCollocation
        results.stochastic_signal = self._signal_at(nm.signal, np.rint(time_vector_cont_mjd).astype(int) - mjd0) / s
        signal_obs = self._signal_at(nm.signal, mjd - mjd0) / s
        results.residuals = observations - a @ results.parameters - signal_obs

        results.wrms = rls.wrms * self.so
        results.outlier_flags = inliers
        # total sigma of an observation under the noise model; robust-fit sigmas for the outliers
        results.obs_sigmas = rls.obs_sigmas.copy()
        results.obs_sigmas[inliers] = np.sqrt(nm.c_lag_final[0]) / s

        # report and diagnostic data (in mm: the JSON output rounds to 6 decimals)
        results.noise_model = nm.summary()
        results.noise_model['units'] = {'variance': 'mm^2', 'rate': 'mm/yr', 'frequency': 'cycles/yr'}

        # compute spectral index of residuals and of the stochastic signal
        if self.compute_periodogram:
            si, fvec, pxx = self.compute_plomb(results.residuals[inliers], time_vector_mjd[inliers])
            results.spectral_index_random_noise = si
            results.periodogram_frequencies = fvec
            results.periodogram_power = pxx
            si, _, _ = self.compute_plomb(results.stochastic_signal, time_vector_cont_mjd)
            results.spectral_index_stochastic_noise = si

        # every ETM parameter in the robust (white-noise) fit and in the GLS, for the screen output and the report
        results.noise_model['parameters'] = self._parameter_table(design_matrix, rls, results)

        results.origin = 'NoiseCovarianceGLS'
        self._log_summary(nm, comp)
        self._log_parameters(results.noise_model['parameters'], comp)
        logger.debug(f'NoiseCovarianceGLS [mm]: ' +
                     ' '.join([f'{p * 1000.:10.3e}' for p in results.parameters.tolist()]))
        logger.debug(f'NoiseCovarianceGLS in {time() - tt:.3f} s ' +
                     ' '.join(f'{k}: {v:.2f} s' for k, v in nm.timings.items()))
        logger.info(f'NoiseCovarianceGLS [{comp}] rejected outliers: '
                    f'{np.sum(~inliers) / inliers.shape[0] * 100:.1f}%')

        return results

    @staticmethod
    def _rate_column(design_matrix: DesignMatrix):
        """column of the velocity in the design matrix (None if the polynomial has no rate)"""
        poly = design_matrix.get_polynomial()
        if poly is not None and poly.fit and poly.param_count > 1:
            return int(poly.column_index[1])
        return None

    @staticmethod
    def _parameter_names(design_matrix: DesignMatrix) -> List[Tuple[str, str]]:
        """(name, unit) of each column of the design matrix, in column order"""
        names = []
        for f in design_matrix.functions:
            if not f.fit or f.param_count == 0:
                continue

            if f.p.object == 'polynomial':
                terms = [f'position ({f.p.t_ref:.3f})', 'velocity', 'acceleration']
                units = ['mm', 'mm/yr', 'mm/yr^2']
                fn = [(terms[p], units[p]) if p < len(terms) else (f'polynomial p{p}', f'mm/yr^{p}')
                      for p in range(f.param_count)]
            elif f.p.object == 'periodic':
                # sine columns first, then cosine columns; frequencies are in cycles/day
                fn = [(f'periodic {kind} {1 / fr:.2f} d', 'mm') for kind in ('sin', 'cos') for fr in f.p.frequencies]
            elif f.p.object == 'jump':
                date = f.date.yyyyddd()
                fn = []
                if f.p.jump_type != JumpType.POSTSEISMIC_ONLY:
                    fn.append((f'jump {date} offset', 'mm'))
                if f.p.jump_type in (JumpType.COSEISMIC_JUMP_DECAY, JumpType.POSTSEISMIC_ONLY):
                    fn += [(f'jump {date} relaxation {r:.2f} yr', 'mm') for r in f.p.relaxation]
            else:
                fn = []

            # generic names if the layout of the function is not the expected one
            if len(fn) != f.param_count:
                fn = [(f'{f.p.object} p{p}', '') for p in range(f.param_count)]
            names.extend(fn)

        return names

    def _parameter_table(self, design_matrix: DesignMatrix, rls: AdjustmentResults,
                         gls: AdjustmentResults) -> List[dict]:
        """value and sigma of each ETM parameter in the robust and the GLS fits [mm, years]"""
        names = self._parameter_names(design_matrix)
        if len(names) != gls.parameters.size:
            names = [(f'p{i}', '') for i in range(gls.parameters.size)]

        s = self.scale
        return [{'name': name, 'unit': unit,
                 'rls': float(rls.parameters[i] * s), 'rls_sigma': float(rls.parameter_sigmas[i] * s),
                 'gls': float(gls.parameters[i] * s), 'gls_sigma': float(gls.parameter_sigmas[i] * s)}
                for i, (name, unit) in enumerate(names)]

    @staticmethod
    def _log_parameters(table: List[dict], comp: str) -> None:
        """screen output of the ETM parameters, one per line: robust fit and GLS with the noise model"""
        tag = f'NoiseCovarianceGLS [{comp}]'
        logger.info(f'{tag}: {"parameter":<44} {"robust LSQ (white noise)":>24}   {"GLS (noise model)":>24}')
        for p in table:
            name = f"{p['name']} [{p['unit']}]" if p['unit'] else p['name']
            logger.info(f"{tag}:   {name:<42} {p['rls']:12.3f} +- {p['rls_sigma']:8.3f}   "
                        f"{p['gls']:12.3f} +- {p['gls_sigma']:8.3f}")

    @staticmethod
    def _signal_at(signal: np.ndarray, day: np.ndarray) -> np.ndarray:
        """signal on the daily grid evaluated at days from the first inlier (zero outside the grid)"""
        out = np.zeros(day.size)
        inside = (day >= 0) & (day < signal.size)
        out[inside] = signal[day[inside]]
        return out

    @staticmethod
    def _log_summary(nm, comp: str) -> None:
        """screen output of the noise model of one component (reference -> with periodic terms)"""
        ref, fin, det = nm.reference, nm.final, nm.detection
        a_ref, a_fin = nm.alpha_reference, nm.alpha_final
        gls = nm.gls
        joint = det.method == 'joint'
        method = 'joint harmonic fit' if joint else 'stepwise grid search'
        tag = f'NoiseCovarianceGLS [{comp}]'

        def white(cf) -> str:
            return f'{cf.nugget:.3f}' + (' (minimum)' if cf.white_at_min else '')

        logger.info(f'{tag}: colored {ref.colored_variance:.3f} -> {fin.colored_variance:.3f} '
                    f'white {white(ref)} -> {white(fin)} mm^2, chi2/dof '
                    f'{ref.chi2 / max(ref.dof, 1):.2f} -> {fin.chi2 / max(fin.dof, 1):.2f}')

        # periodic terms, one per line by increasing period: amplitude, SNR in the final fit, fraction of the variance
        # that survives the ETM; for the joint harmonic fit also the SNR in the harmonic fit (none for periods added
        # by the final check) and the action of the final check (passed, added; removed periods have a row without
        # values)
        removed = det.check_removed if joint else np.array([])
        weak = nm.weak_final_snr
        if not fin.f.size and not removed.size:
            logger.info(f'{tag}: periodic terms ({method}): none')
        else:
            logger.info(f'{tag}: {"periodic term (" + method + ")":<44} {"amplitude [mm]":>14} {"SNR final":>10}' +
                        (f' {"SNR harmonic":>13}' if joint else '') + f' {"survives ETM":>13}' +
                        (f' {"final check":>12}' if joint else ''))
            rows = []
            for i, (f, wp, q, c0) in enumerate(zip(fin.f, fin.w_p, det.snr, fin.survive)):
                row = f'{tag}:   {f"{1 / f:8.1f} d":<42} {np.sqrt(2 * wp):14.2f} {q:9.1f}{"*" if weak[i] else " "}'
                if joint:
                    q_h = det.snr_harmonic[i]
                    row += f' {f"{q_h:.1f}" if np.isfinite(q_h) else "-":>13}'
                row += f' {c0:13.2f}'
                if joint:
                    row += f' {det.check_action[i]:>12}'
                rows.append((1 / f, row))
            for f in removed:
                rows.append((1 / f, f'{tag}:   {f"{1 / f:8.1f} d":<42} {"-":>14} {"-":>9}  {"-":>13} {"-":>13} '
                                    f'{"removed":>12}'))
            for _, row in sorted(rows, key=lambda r: r[0]):
                logger.info(row)
            if weak.any():
                logger.info(f'{tag}:   * SNR below {nm.settings.jhf_snr_min:g} in the final fit (kept: the decision '
                            f'is made in the harmonic fit)')
        # the low final SNRs are marked in the table
        for kind, flag in nm.flag_items():
            if kind != 'final_snr':
                logger.info(f'{tag}: flag: {flag}')

        def alpha(ea) -> str:
            # band as periods [d], longest first (ea.f_low, ea.f_high in cycles/yr)
            if ea.band_empty:
                # the steep part never dominates the floor: alpha is the local slope at 1/N
                return f'{ea.alpha:.2f} (local at {365.25 / ea.f_low:.0f} d)'
            return f'{ea.alpha:.2f} ({365.25 / ea.f_low:.0f}-{365.25 / ea.f_high:.0f} d)'

        logger.info(f'{tag}: effective alpha {alpha(a_ref)} -> {alpha(a_fin)} with periodic terms')
        if np.isfinite(gls.factors[0]):
            # variance factors per part of the noise model, if requested (the white factor is undefined
            # without white noise)
            f_w = f'{gls.factors[1]:.3f}' if np.isfinite(gls.factors[1]) else 'n/a'
            logger.info(f'{tag}: variance factors colored {gls.factors[0]:.3f} white {f_w}')
        else:
            # variance of unit weight of the whole fit: v' C^-1 v / redundancy (always computed)
            logger.info(f'{tag}: variance of unit weight {gls.factors[2]:.3f}')


class EtmFit:
    def __init__(self, config: EtmConfig, design_matrix: DesignMatrix, solution_data_hash: int = None):

        self.config = config
        self.outlier_flags: np.ndarray = np.array([])
        self.results: List[AdjustmentResults] = [AdjustmentResults(), AdjustmentResults(), AdjustmentResults()]
        self.design_matrix = design_matrix
        self.covar = np.array([])
        # validate design matrix now to catch any changes in jumps (before loading from the db)
        # self._validate_function_design_matrix()
        # store solution hash to compare hash value in database save process
        self.hash = solution_data_hash

    def run_fit(self, solution_data: SolutionData, design_matrix: DesignMatrix = None,
                noise_model: NoiseModels = NoiseModels.WHITE_ONLY) -> float:
        """wrapper to run adjustment. Returns runtime of the operation"""
        run_time = time()

        # allow replacing the design matrix
        if design_matrix:
            self.design_matrix = design_matrix

        # between the initialization of EtmFit and run_adjustment (which calls run_fit) there can
        # be deactivated jumps or changes in the design matrix that require the recomputation of the
        # internal constraints (meant to stabilize the system of equations). Call _validate_function_design_matrix
        # again to make sure that we have the right dimensions in internal_constraints
        self._validate_function_design_matrix()

        # get mask for least squares collocation and prefit models
        mask = self.config.modeling.get_observation_mask(solution_data.time_vector)

        # Deactivate jumps that fall entirely outside the fit window before building the
        # design matrix. A jump at or before the first windowed observation produces an
        # all-ones column (collinear with the polynomial constant); a jump at or after the
        # last produces an all-zeros column. Both cause rank deficiency.
        windowed_mjd = solution_data.time_vector_mjd[mask]
        if windowed_mjd.size > 0:
            needs_reindex = False
            for func in [f for f in design_matrix.functions
                         if f.p.object == 'jump' and f.fit
                         and f.p.jump_type != JumpType.POSTSEISMIC_ONLY]:
                jump_mjd = func.date.mjd
                if jump_mjd <= windowed_mjd.min():
                    logger.warning(f'Jump at {func.date.yyyyddd()} is at or before the start of the '
                                   f'fit window — deactivating (effect absorbed by polynomial offset)')
                    func.fit = False
                    needs_reindex = True
                elif jump_mjd >= windowed_mjd.max():
                    logger.warning(f'Jump at {func.date.yyyyddd()} is at or after the end of the '
                                   f'fit window — deactivating (no observations to constrain it)')
                    func.fit = False
                    needs_reindex = True
            if needs_reindex:
                design_matrix._assign_column_indices()

        # transform solutions to NEU
        neu = solution_data.transform_to_local()

        lsq = AdjustmentStrategy.create_instance(self.config)

        try:
            while True:
                for i in range(3):
                    # select the noise model
                    noise = WeightBuilder.create_instance(noise_model, neu[i].shape[0])

                    n_neq_left, c_neq_right = design_matrix.get_constraints_normal_eq(i)

                    # DDG: validate design matrix here!
                    design_matrix.validate_matrix(n_neq_left)

                    self.results[i] = lsq.adjust(design_matrix=design_matrix,
                                                 observations=neu[i],
                                                 weights=noise,
                                                 time_vector_mjd=solution_data.time_vector_mjd[mask],
                                                 time_vector_cont_mjd=solution_data.time_vector_cont_mjd,
                                                 n_neq_left=n_neq_left,
                                                 c_neq_right=c_neq_right,
                                                 component=i)

                self.load_results_to_functions()

                # validate results and redo fit if needed
                validation = design_matrix.validate_parameters()
                if not validation:
                    # no issue found, break
                    break

                for f, message in validation:
                    logger.info(message)
                    if message.startswith('Unrealistic amplitude') and isinstance(f, JumpFunction):
                        if not f.was_modified:
                            f.configure_behavior({'jump_type': JumpType.POSTSEISMIC_ONLY})
                        else:
                            f.remove_from_fit()

            self.config.modeling.status = FitStatus.POSTFIT

        except (DesignMatrixException, AdjustmentStrategyException):
            # system was rank deficient! cannot fit model
            self.results = [AdjustmentResults()] * 3
            self.config.modeling.status = FitStatus.UNABLE_TO_FIT
            self.outlier_flags = np.ones(solution_data.solutions).astype(bool)

            logger.info(self.config.modeling.status.description + ' ' +
                        f'solutions: {solution_data.solutions} '
                        f'rank deficiency: {design_matrix.rank_deficient}')

            # deactivate all functions
            for funct in design_matrix.functions:
                funct.fit = False

        return time() - run_time

    def load_results_to_functions(self):
        """load the results to the design matrix functions"""

        # insert stochastic signal if it was estimates
        for f in self.design_matrix.functions:
            if f.p.object == 'stochastic':
                f.p.params = [rs.stochastic_signal for rs in self.results]

        # populate results onto etm_function
        for func in self.design_matrix.functions:
            if func.fit and func.param_count > 0:
                func.load_parameters(self.results)

        # single outlier flag
        self.outlier_flags = np.all((self.results[0].outlier_flags,
                                     self.results[1].outlier_flags,
                                     self.results[2].outlier_flags), axis=0)

        self.process_covariance()

    def _validate_function_design_matrix(self):

        check_again = False

        for i in range(3):
            check_again = False
            validation = self.design_matrix.validate_design()
            # initialize, this function can be called more than once
            self.design_matrix.internal_constraints = []

            for f, message in validation:
                logger.info(message)
                if message.startswith('Condition number too large') and isinstance(f, JumpFunction):
                    # try to set an inner constraint for this jump
                    if not self.config.modeling.check_jump_collisions:
                        # no jump collision check, DO NOT MODIFY ETM
                        # @todo: if user set max_cond number = 0 constraint jump also?
                        logger.info('Adding internal constraint = 0 for stabilizing system')
                        k = np.zeros((1, self.design_matrix.matrix.shape[1]))
                        k[:, f.get_relaxation_cols()] = 1
                        self.design_matrix.internal_constraints.append(k)
                        # no need to check again, if it didn't work the first time it won't work the next one
                    elif f.p.relaxation.size > 1:
                        # jump collisions enabled: modify the etm if needed
                        # @todo: evaluate the possibility of modifying the downstream jumps to remove smallest relax
                        # as long as the magnitude of the downstream jump is < to the one being evaluated here
                        logger.info('Removing smallest relaxation')
                        min_index = np.argmin(f.p.relaxation)
                        rlx = np.delete(f.p.relaxation, min_index)
                        f.configure_behavior({'relaxation': rlx})
                        # activate the check again flag
                        check_again = True
                    elif f.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
                        # single relaxation left and it is still ill-conditioned (e.g. jump sits
                        # right before a large data gap or the end of the series): drop the decay
                        # term entirely and keep only the coseismic offset, instead of leaving a
                        # column in the design matrix that makes the system rank deficient
                        logger.info('Single relaxation still ill-conditioned, '
                                    'downgrading jump to COSEISMIC ONLY')
                        f.configure_behavior({'jump_type': JumpType.COSEISMIC_ONLY})
                        check_again = True
                    else:
                        # POSTSEISMIC_ONLY with a single, ill-conditioned relaxation and no offset
                        # term to fall back on: nothing left to fit, remove the jump
                        logger.info('Function has a single relaxation, cannot remove it. '
                                    'Removing jump from fit.')
                        f.remove_from_fit()
                        check_again = True
            if not check_again:
                break
        # if we reach the max iteration level and still check again, create warning
        if check_again:
            logger.info('While checking the etm functions design matrices the max iteration '
                        'level was reached and check_again flag is still true!')

    def get_var_factor_db_fields(self):
        """method to return the var_factor object for storing solution in database"""
        return {
            'NetworkCode': self.config.network_code,
            'StationCode': self.config.station_code,
            'object': 'var_factor',
            'soln': self.config.solution.solution_type.code,
            'stack': self.config.solution.stack_name,
            'params': [result.parameters.tolist() for result in self.results],
            'sigmas': [[float(result.variance_factor) for result in self.results],
                       [float(result.wrms) for result in self.results]],
            'hash': self.hash
        }

    def get_time_continuous_model(self, time_vector_cont: np.ndarray) -> List[np.ndarray]:
        # spit the auto position by default, in case there is no model
        model = [np.array([0]),
                 np.array([0]),
                 np.array([0])]

        if self.config.modeling.status == FitStatus.POSTFIT:
            for i in range(3):
                model[i] = self.design_matrix.alternate_time_vector(time_vector_cont) @ self.results[i].parameters

        return model

    def load_from_json(self):
        """
        load ETM solution from json file
        """

    def _compute_covariances(self, results: 'AdjustmentResults') -> None:
        """Compute parameter covariances and correlations"""
        # Implementation for covariance computation
        pass

    def _detect_outliers(self, observations: np.ndarray,
                         results: 'AdjustmentResults') -> None:
        """Detect and flag statistical outliers"""
        # Implementation for outlier detection
        pass

    def process_covariance(self):

        cov = np.zeros((3, 1))

        self.covar = np.diag([r.wrms ** 2 for r in self.results])

        # save the covariance between N-E, E-U, N-U
        f = self.outlier_flags

        # load the covariances using the correlations
        for k, i, j in ((0, 0, 1), (1, 1, 2), (2, 0, 2)):
            cov[k] = np.corrcoef(self.results[i].residuals[f],
                                 self.results[j].residuals[f])[0, 1] * self.results[i].wrms * self.results[j].wrms

        # build a variance-covariance matrix
        self.covar[0, 1] = cov[0]
        self.covar[1, 0] = cov[0]
        self.covar[2, 1] = cov[1]
        self.covar[1, 2] = cov[1]
        self.covar[0, 2] = cov[2]
        self.covar[2, 0] = cov[2]

        if not self._is_pd(self.covar):
            self.covar = self._nearest_pd(self.covar)

    def _nearest_pd(self, a):
        """Find the nearest positive-definite matrix to input

        A Python/Numpy port of John D'Errico's `nearestSPD` MATLAB code [1], which
        credits [2].

        [1] https://www.mathworks.com/matlabcentral/fileexchange/42885-nearestspd

        [2] N.J. Higham, "Computing a nearest symmetric positive semidefinite
        matrix" (1988): https://doi.org/10.1016/0024-3795(88)90223-6
        """

        b = (a + a.T) / 2
        _, s, v = np.linalg.svd(b)

        h = np.dot(v.T, np.dot(np.diag(s), v))

        a2 = (b + h) / 2

        a3 = (a2 + a2.T) / 2

        if self._is_pd(a3):
            return a3

        spacing = np.spacing(np.linalg.norm(a))
        # The above is different from [1]. It appears that MATLAB's `chol` Cholesky
        # decomposition will accept matrixes with exactly 0-eigenvalue, whereas
        # Numpy's will not. So where [1] uses `eps(mineig)` (where `eps` is Matlab
        # for `np.spacing`), we use the above definition. CAVEAT: our `spacing`
        # will be much larger than [1]'s `eps(mineig)`, since `mineig` is usually on
        # the order of 1e-16, and `eps(1e-16)` is on the order of 1e-34, whereas
        # `spacing` will, for Gaussian random matrixes of small dimension, be on
        # othe order of 1e-16. In practice, both ways converge, as the unit test
        # below suggests.
        i = np.eye(a.shape[0])
        k = 1

        while not self._is_pd(a3):
            mineig = np.min(np.real(np.linalg.eigvals(a3)))
            a3 += i * (-mineig * k ** 2 + spacing)
            k += 1

        return a3

    @staticmethod
    def _is_pd(b):
        """Returns true when input is positive-definite, via Cholesky"""
        try:
            _ = np.linalg.cholesky(b)
            return True
        except np.linalg.LinAlgError:
            return False