"""
Per-station normal equations and their assembly into the stacker's system.
"""

from typing import Tuple

import logging

import numpy as np
from tqdm import tqdm

from .data_classes import NormalEquations, Station
from ..least_squares.noise_covariance import build_toeplitz_covariance, cholesky_whiten_design
from ...Utils import stationID

logger = logging.getLogger(__name__)

# NEU (fit.results/l index) -> ENU label, matching the [1, 0, 2] iteration order below.
_NEU_TO_ENU_LABEL = {1: 'E', 0: 'N', 2: 'U'}

# Ceiling on the variance inflation _outlier_inflated_covariance applies, so that a single
# hard-rejected epoch (the robust weight can fall far enough to put its sigma 10 orders of
# magnitude above the noise floor) cannot leave the Toeplitz matrix too ill-conditioned for
# the Cholesky. At 1e6 the epoch already carries a millionth of a good epoch's weight.
_OUTLIER_MAX_INFLATION = 1e6


def _outlier_inflated_covariance(t_mjd: np.ndarray, noise_lag: np.ndarray,
                                 results) -> np.ndarray:
    """
    Expand a lag-indexed colored-noise covariance into its Toeplitz matrix over t_mjd, with
    the diagonal inflated at the epochs the station's own robust fit rejected.

    The inflation is needed because noise_lag is estimated from inlier residuals only
    (least_squares fits the noise model on rls.outlier_flags), so applying it unmodified to
    every epoch weights a rejected epoch exactly like a good one. The diagonal branch in
    _create_normal_equations does not have that problem: obs_sigmas keeps the robust fit's
    inflated sigma at those epochs, so they are already downweighted there. This carries the
    same downweighting into the colored-noise branch, scaling each rejected epoch's variance
    up to the robust fit's own obs_sigma for it.

    Inflating rather than deleting the rows keeps the epoch spacing intact, which a
    lag-indexed covariance depends on.
    """
    c_mat = build_toeplitz_covariance(t_mjd, noise_lag)

    inliers = results.outlier_flags.astype(bool)  # True == kept by the robust fit
    if inliers.all():
        return c_mat

    idx = np.flatnonzero(~inliers)
    # Clipped at 1 so an epoch whose robust sigma came out below the noise floor keeps the
    # noise floor: rejected epochs may be downweighted, never upweighted.
    inflation = np.clip(results.obs_sigmas[idx] ** 2 / noise_lag[0],
                        1.0, _OUTLIER_MAX_INFLATION)
    c_mat[idx, idx] += (inflation - 1.0) * noise_lag[0]
    return c_mat


class NormalEquationsMixin:
    """Mixed into EtmStacker: owns self.normal_equations / self.total_parameters."""

    def _create_normal_equations(self, station: Station) -> NormalEquations:
        """Analyze and save the relevant information."""

        # get the observations without the jumps
        l = station.etm.solution_data.transform_to_local()
        a = station.etm.design_matrix.matrix
        t_mjd = station.etm.solution_data.time_vector_mjd

        n = []
        c = []

        if station.etm.solution_data.solutions < 100:
            logger.info(f'Upweighting {stationID(station)}: fewer than 100 observations')
            weight_scale = self.config.station_weight_scale * 100.
        else:
            weight_scale = self.config.station_weight_scale

        lpl = []
        observation_weights = []
        prior_wrms = []
        # rearrange the NEU to ENU
        for i in [1, 0, 2]:
            noise_lag = station.etm.fit.results[i].noise_covariance_lag
            if noise_lag.size:
                # This component was fit with NoiseCovarianceGLS (see
                # EtmStackerConfig.gls_for_all_stations) and carries a real colored-noise
                # covariance -- whiten the design against the full covariance instead of
                # treating epochs as independent, the same technique
                # constraints/sparse.py already uses for a sparse station's own data.
                # weight_scale multiplies the weight, i.e. divides the covariance.
                c_mat = _outlier_inflated_covariance(
                    t_mjd, noise_lag, station.etm.fit.results[i]) / weight_scale
                n_i, c_i, lpl_i = cholesky_whiten_design(
                    a, l[i], c_mat, f'{stationID(station)} {_NEU_TO_ENU_LABEL[i]}')
                n.append(n_i)
                c.append(c_i)
                lpl.append(lpl_i)
                # The actual weighting used above is the full (non-diagonal) covariance,
                # not this. This diagonal, white-noise-equivalent stand-in (the
                # reciprocal of the zero-lag/same-epoch variance) exists only so
                # downstream diagnostics that assume a per-epoch diagonal weight (the
                # per-station WRMS report in solver.py, and the legacy do_vce command in
                # etm_stacker_vce.py, which also rescales this array directly) still
                # have something to work with -- it is not what the solve itself used.
                # Taken from the diagonal so it reflects the outlier inflation above.
                observation_weights.append(1.0 / np.diag(c_mat))
            else:
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

    def _build_base_normal_equations(self) -> Tuple[np.ndarray, np.ndarray]:
        """Build the base NEQ from individual stations."""

        logger.info('Building station system of normal equations')

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
