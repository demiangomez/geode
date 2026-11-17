import logging
import numpy as np
from typing import List

logger = logging.getLogger(__name__)

from ...dbConnection import Cnn
from ..core.etm_config import EtmConfig
from ..least_squares.least_squares import EtmFit
from ..core.type_declarations import FitStatus


def save_parameters_db(cnn: Cnn, etm_results: EtmFit) -> None:
    """function to save an etm to the database"""

    # check that the components in the database have the same signature as those in the results
    dm = etm_results.design_matrix

    query = '''
            SELECT * FROM etms WHERE "NetworkCode" = '{}' AND "StationCode" = '{}' AND soln = '{}' AND  
            object = '{}' AND stack = '{}'
            '''

    # begin by finding the variance factor field to compare hash. If hash is different,
    # then invalidate the entire etm solution
    var_factor = cnn.query_float(query.format(etm_results.config.network_code,
                                              etm_results.config.station_code,
                                              etm_results.config.solution.solution_type.code,
                                              'var_factor',
                                              etm_results.config.solution.stack_name), as_dict=True)
    if var_factor:
        # delete solution from database and replace with new one
        cnn.delete(
            'etms',
            StationCode=etm_results.config.station_code,
            NetworkCode=etm_results.config.network_code,
            soln=etm_results.config.solution.solution_type.code,
            stack=etm_results.config.solution.stack_name)

    logger.debug(f'insert new ver_factor hash {etm_results.hash}')
    cnn.insert('etms', **etm_results.get_var_factor_db_fields())

    for funct in dm.functions:
        # no values, need to insert them
        logger.debug(f'insert {repr(funct)}')
        cnn.insert('etms', **funct.get_parameter_dict())

def load_parameters_db(config: EtmConfig,
                       cnn: Cnn, etm_results: EtmFit,
                       observations: List[np.ndarray],
                       time_vector_mjd: np.ndarray = None) -> bool:

    dm = etm_results.design_matrix
    # shorthand notation
    limit = etm_results.config.modeling.least_squares_strategy.sigma_filter_limit

    hash_sum_db = cnn.query_float(f'''
    SELECT sum(hash) FROM etms WHERE "NetworkCode" = '{etm_results.config.network_code}' AND 
    "StationCode" = '{etm_results.config.station_code}' 
    AND soln = '{etm_results.config.solution.solution_type.code}' AND 
    stack = '{etm_results.config.solution.stack_name}'
    ''')

    hash_sum_etm = sum([funct.p.hash for funct in dm.functions]) + etm_results.hash

    if hash_sum_db and hash_sum_db[0][0] != hash_sum_etm:
        # invalidate solution, return false so that caller knows that information in database is different
        return False

    query = '''
            SELECT * FROM etms WHERE "NetworkCode" = '{}' AND "StationCode" = '{}' AND soln = '{}' AND stack = '{}'
            '''

    etms = cnn.query_float(query.format(etm_results.config.network_code,
                                        etm_results.config.station_code,
                                        etm_results.config.solution.solution_type.code,
                                        etm_results.config.solution.stack_name), as_dict=True)
    # placeholder to save stochastic signal object
    stochastic_signal = None

    for funct in dm.functions:
        obj = next((item for item in etms if item.get('object') == funct.p.object
                    and item.get('jump_date') == funct.p.jump_date
                    and item.get('jump_type') == funct.p.jump_type), None)

        if obj and obj['params']:
            # skip parameters that are none
            funct.p.params = [np.array([])] * 3
            funct.p.sigmas = [np.array([])] * 3
            for i in range(3):
                funct.p.params[i] = np.array(obj['params'][i]).astype(float)
                funct.p.sigmas[i] = np.array(obj['sigmas'][i]).astype(float)
                # save the sigmas in continuous form - only for fitted functions
                # (parameters vector only contains fitted function params)
                if funct.p.object != 'stochastic' and funct.fit:
                    etm_results.results[i].parameter_sigmas = (
                        np.concatenate((etm_results.results[i].parameter_sigmas, funct.p.sigmas[i])))

                if funct.p.object == 'stochastic':
                    arr = funct.p.params[i]
                    if arr.ndim == 2 and arr.shape[0] == 2:
                        # current format: row 0 signal, row 1 noise (Stochastic.set_params())
                        signal_i, noise_i = arr[0], arr[1]
                    else:
                        # legacy row, saved before the signal/noise split existed: signal only
                        signal_i, noise_i = arr, np.zeros_like(arr)
                        funct.p.params[i] = np.vstack([signal_i, noise_i])
                    etm_results.results[i].stochastic_signal = signal_i
                    etm_results.results[i].stochastic_noise = noise_i
                    stochastic_signal = funct

    # load the parameters vector
    var_factor = next((item for item in etms if item.get('object') == 'var_factor'), None)

    # guard: if the saved parameter count no longer matches the current design matrix (e.g. because
    # a jump was deactivated inside run_fit() after the solution was saved, changing the column count
    # without affecting the hash), force recomputation rather than crashing on the matmul below.
    dm_cols = dm.matrix.shape[1]
    if any(len(var_factor['params'][i]) != dm_cols for i in range(3)):
        logger.warning(
            f'Cached parameter vector length ({len(var_factor["params"][0])}) does not match '
            f'current design matrix column count ({dm_cols}) — discarding cached solution')
        return False

    for i in range(3):
        etm_results.results[i].origin = cnn.options['hostname'] + ':' + cnn.options['database']
        etm_results.results[i].parameters = np.array(var_factor['params'][i]).astype(float)
        etm_results.results[i].wrms = float(var_factor['sigmas'][1][i])
        etm_results.results[i].variance_factor = float(var_factor['sigmas'][0][i])

        # compute outliers for this component
        v = observations[i] - dm.matrix @ np.array(var_factor['params'][i]).astype(float)

        # check if stochastic signal present in the functions
        if stochastic_signal is not None:
            # remove stochastic signal from the residuals!
            v -= stochastic_signal.eval(i, time_vector_mjd)

        etm_results.results[i].residuals = v
        # stochastic_noise was already loaded straight from the DB row above (object='stochastic'),
        # alongside stochastic_signal -- nothing to recompute here
        s = np.abs(np.divide(v, etm_results.results[i].wrms))
        etm_results.results[i].outlier_flags = s <= limit
        # compute the observation sigmas
        etm_results.results[i].obs_sigmas = np.ones(observations[i].shape[0]) * etm_results.results[i].wrms
        # normalized residuals
        s = np.abs(v / etm_results.results[i].wrms)
        # compute downweighted outliers
        sw = np.power(10, limit - s[s > limit])
        # limit the lowest value
        sw[sw < np.finfo(float).eps] = np.finfo(float).eps

        etm_results.results[i].obs_sigmas[s > limit] = etm_results.results[i].wrms / sw

    etm_results.outlier_flags = np.all((etm_results.results[0].outlier_flags,
                                        etm_results.results[1].outlier_flags,
                                        etm_results.results[2].outlier_flags), axis=0)

    # after loading all function, estimate covariance
    etm_results.process_covariance()

    # update status to reflect that ETM is ready to compute solutions
    etm_results.config.modeling.status = FitStatus.POSTFIT

    return True