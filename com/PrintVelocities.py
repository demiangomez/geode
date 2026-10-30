#!/usr/bin/env python

"""
Project: Geodetic Database Engine (GeoDE)
Author: Demian D. Gomez

Print the horizontal/vertical velocities (and basic time series stats) of every
station in a given solution (GAMIT stack or ppp), computed directly from the ETM
engine (no intermediate JSON files needed). Equivalent to looping over the JSON
files produced by PlotETM.py -json and extracting the polynomial velocity, except
the ETM is built and fit in-process and every station of the solution is included
(no station list needed).

Type python PrintVelocities.py -h for usage help
"""
import os, sys

# limit BLAS/OpenMP threading to 1 so this script can be run in parallel
# (multiple processes) without each process spawning its own thread pool
# and oversubscribing the CPU. Must be set before numpy/scipy are imported.
os.environ['OMP_NUM_THREADS'] = '1'           # OpenMP (general)
os.environ['MKL_NUM_THREADS'] = '1'           # Intel MKL
os.environ['OPENBLAS_NUM_THREADS'] = '1'      # OpenBLAS
os.environ['NUMEXPR_NUM_THREADS'] = '1'       # NumExpr
os.environ['VECLIB_MAXIMUM_THREADS'] = '1'    # macOS Accelerate framework

import argparse
import logging

# deps
import numpy as np
from tqdm import tqdm

# app
from geode import dbConnection
from geode.etm.core.logging_config import setup_etm_logging
from geode.etm.core.data_classes import ModelingParameters, ValidationRules
from geode.Utils import add_version_argument, get_stack_stations, file_write


VERBOSITY_MAP = {
    'quiet': logging.CRITICAL,
    'info':  logging.INFO,
    'debug': logging.DEBUG
}

HEADER = '%10s %10s %10s %10s %10s %10s %10s %10s %10s %10s %10s %10s %10s %10s %12s %12s %12s' \
         % ('stnm', 'lat', 'lon', 't_first', 't_last', 'tspan', 'nobs',
            'Ve', 'Vn', 'Vu', 'Vh', 'Se', 'Sn', 'Su', 'Cov_ne', 'Cov_nu', 'Cov_eu')


class TqdmLoggingHandler(logging.Handler):
    """Routes log records through tqdm.write so the progress bar isn't overwritten."""
    def emit(self, record):
        try:
            tqdm.write(self.format(record))
            self.flush()
        except Exception:
            self.handleError(record)


def get_solution_stations(cnn, solution):
    if solution == 'ppp':
        return cnn.query_float('SELECT DISTINCT "NetworkCode", "StationCode" FROM ppp_soln '
                               'ORDER BY "NetworkCode", "StationCode"', as_dict=True)
    else:
        return sorted(get_stack_stations(cnn, solution), key=lambda s: (s['NetworkCode'], s['StationCode']))


def callback_print_velocity(job):
    if job.result is not None:
        error_msg, line, station_id = job.result
        if error_msg:
            tqdm.write(' -- Error processing %s:\n%s' % (station_id, error_msg))
        else:
            tqdm.write(' >> Successfully processed %s' % station_id)
    elif job.exception:
        tqdm.write(' -- Unhandled job exception:\n%s' % str(job.exception))


def process_etm_velocity(stn, args_dict):
    """
    Dispy worker: fully self-contained per-station ETM processing. Returns a
    (error_msg, output_line, station_id) tuple; output_line is None when the
    station could not be fit.
    """
    import traceback, logging
    import numpy as np

    from geode import dbConnection
    from geode.etm.core.logging_config import setup_etm_logging
    from geode.etm.core.etm_engine import EtmEngine
    from geode.etm.core.etm_config import EtmConfig
    from geode.etm.core.type_declarations import JumpType, FitStatus
    from geode.etm.data.solution_data import SolutionDataException
    from geode.etm.core.data_classes import (AdjustmentModels, SolutionType, CovarianceFunction,
                                              ModelingParameters, SolutionOptions,
                                              JumpParameters, ValidationRules)
    from geode.etm.etm_functions.polynomial import PolynomialFunction
    from geode.etm.etm_functions.periodic import PeriodicFunction
    from geode.etm.etm_functions.jumps import JumpFunction
    from geode.pyDate import Date
    from geode.Utils import process_date_str, load_json

    ADJUSTMENT_MODEL_MAP = {
        'rls': AdjustmentModels.ROBUST_LEAST_SQUARES,
        'lsc': AdjustmentModels.LSQ_COLLOCATION,
        'gls': AdjustmentModels.NOISE_COVARIANCE_GLS,
    }
    COVARIANCE_MAP = {
        'arma': CovarianceFunction.ARMA,
        'gaussian': CovarianceFunction.GAUSSIAN,
    }

    setup_etm_logging(level=VERBOSITY_MAP[args_dict['verbosity']])

    net  = stn['NetworkCode']
    stnm = stn['StationCode']
    station_id = '%s.%s' % (net, stnm)

    try:
        cnn = dbConnection.Cnn('gnss_data.cfg')

        solution_options = SolutionOptions()
        solution_options.solution_type = SolutionType.from_code(args_dict['solution'])
        solution_options.stack_name    = args_dict['solution']

        psb = args_dict['post_seismic_back']
        if psb is None:
            time_back = None
        elif any(c in psb for c in ('_', '.', '/')):
            time_back = process_date_str(psb)
        else:
            time_back = int(psb) * 365

        config = EtmConfig(net, stnm, cnn=cnn,
                           solution_options=solution_options,
                           post_seismic_back_lim=time_back,
                           earthquake_magnitude_limit=args_dict['s_score_mag_limit'],
                           earthquakes_cherry_picked=args_dict['force_earthquakes'])

        config.modeling.relaxation        = np.array(args_dict['default_relax'])
        config.modeling.data_model_window = args_dict['_fit_dates']

        # --- prefit models (inlined from PlotETM.get_prefit_models) ---
        user_prefit_models = []
        prefit_args = args_dict['prefit_model']
        if prefit_args:
            fmap_str = {'poly': 'polynomial', 'per': 'periodic', 'jump': 'jump'}
            fmap     = {'poly': PolynomialFunction, 'per': PeriodicFunction, 'jump': JumpFunction}
            funcs    = load_json(prefit_args[0])['functions']
            cur      = prefit_args[1]
            i = 2
            while i < len(prefit_args):
                if cur == 'jump':
                    mjd = process_date_str(prefit_args[i]).mjd
                    jf  = [f for f in funcs if f['object'] == fmap_str[cur]
                           and f['jump_date']['mjd'] == mjd]
                    fn  = fmap[cur](config, time_vector=np.array([0]),
                                   date=Date(**jf[0]['jump_date']),
                                   jump_type=JumpType.UNDETERMINED, fit=True)
                    i += 1
                else:
                    jf = [f for f in funcs if f['object'] == fmap_str[cur]]
                    fn = fmap[cur](config)
                fn.load_from_json(jf[0])
                user_prefit_models.append(fn)
                i += 1
        config.modeling.prefit_models = user_prefit_models

        config.modeling.check_jump_collisions = not args_dict['no_jump_check']
        config.modeling.least_squares_strategy.adjustment_model = \
            ADJUSTMENT_MODEL_MAP[args_dict['least_squares_strategy']]
        config.modeling.fit_auto_detected_jumps        = args_dict['fit_auto_jumps'][0] is not None
        config.modeling.fit_auto_detected_jumps_method = args_dict['fit_auto_jumps'][0]
        config.modeling.least_squares_strategy.iterations             = args_dict['max_iterations']
        config.modeling.least_squares_strategy.noise_variance_factors  = args_dict['variance_factors']
        config.modeling.least_squares_strategy.noise_detection         = args_dict['periodic_detection']
        config.modeling.least_squares_strategy.noise_snr_min           = args_dict['snr_min']
        config.modeling.least_squares_strategy.sigma_filter_limit      = args_dict['sigma_limit']
        config.modeling.least_squares_strategy.covariance_function = \
            COVARIANCE_MAP[args_dict['covariance'][0]]

        # --- constraints (inlined from PlotETM.get_constraints) ---
        user_constraints = []
        const_args = args_dict['constraints']
        if const_args:
            fmap_str = {'poly': 'polynomial', 'per': 'periodic', 'jump': 'jump'}
            fmap     = {'poly': PolynomialFunction, 'per': PeriodicFunction, 'jump': JumpFunction}
            funcs    = load_json(const_args[0])['functions']
            cur      = const_args[1]
            arg_count = 0
            i = 2
            while i < len(const_args):
                if arg_count == 0:
                    if cur == 'jump':
                        mjd = process_date_str(const_args[i]).mjd
                        jf  = [f for f in funcs if f['object'] == fmap_str[cur]
                               and f['jump_date']['mjd'] == mjd]
                        fn  = fmap[cur](config, time_vector=np.array([0]),
                                       date=Date(**jf[0]['jump_date']),
                                       jump_type=JumpType.UNDETERMINED, fit=False)
                        i += 1
                    else:
                        jf = [f for f in funcs if f['object'] == fmap_str[cur]]
                        fn = fmap[cur](config)
                    fn.load_from_json(jf[0])
                    params = np.zeros_like(fn.p.params[0], dtype=bool)
                    user_constraints.append(fn)
                    arg_count += 1
                else:
                    if const_args[i] in fmap_str:
                        for j in range(3):
                            user_constraints[-1].p.params[j][~params] = np.nan
                            user_constraints[-1].p.sigmas[j][~params] = np.nan
                        arg_count = 0
                        cur = const_args[i]
                    else:
                        params[int(const_args[i][1])] = True
                        arg_count += 1
                    i += 1
            for j in range(3):
                user_constraints[-1].p.params[j][~params] = np.nan
                user_constraints[-1].p.sigmas[j][~params] = np.nan
        config.modeling.least_squares_strategy.constraints = user_constraints

        cov = args_dict['covariance']
        if cov[0] == 'arma' and len(cov) > 1:
            config.modeling.least_squares_strategy.arma_roots  = cov[1]
        if cov[0] == 'arma' and len(cov) > 2:
            config.modeling.least_squares_strategy.arma_points = cov[2]

        config.validation.max_condition_number = args_dict['max_condition_number']

        # --- custom relaxations (inlined from PlotETM.process_custom_relaxations) ---
        events, cur_event, cur_relax = [], None, []
        for arg in args_dict['event_relax']:
            try:
                cur_relax.append(float(arg))
            except ValueError:
                if cur_event is not None:
                    events.append((cur_event, cur_relax))
                cur_event, cur_relax = arg, []
        if cur_event is not None:
            events.append((cur_event, cur_relax))
        for event, relax in events:
            rs   = cnn.query_float("SELECT * FROM earthquakes WHERE id = '%s'" % event,
                                   as_dict=True)[0]
            date = Date(datetime=rs['date'])
            config.modeling.user_jumps.append(
                JumpParameters(jump_type=JumpType.COSEISMIC_JUMP_DECAY,
                               relaxation=relax, date=date, action='+'))

        etm = EtmEngine(config, cnn=cnn)
        etm.run_adjustment(cnn=cnn,
                           try_save_to_db=not args_dict['no_save_database'],
                           try_loading_db=not args_dict['no_save_database'])

        if etm.config.modeling.status != FitStatus.POSTFIT:
            return ' -- unable to fit a model (insufficient or degenerate data)', None, station_id

        ve = vn = vu = se = sn = su = None
        for x in etm.design_matrix.functions:
            if x.p.object == 'polynomial':
                vn, ve, vu = (p[1] * 1000 for p in x.p.params)
                sn, se, su = (p[1] * 1000 for p in x.p.sigmas)

        if ve is None:
            return ' -- no polynomial (velocity) function found in design matrix', None, station_id

        vh = np.sqrt(ve ** 2 + vn ** 2)

        # Velocity N-E, N-U, E-U covariances, simplistic/separable-case approximation
        # (cross_component_covariance.pdf, Section 3.4): Cov(x_i, x_j) = r_ij * sigma_i * sigma_j,
        # where r_ij is the correlation of the whole (N, E, U) residual time series already computed
        # by EtmFit.process_covariance (etm/least_squares/least_squares.py) and stored in etm.fit.covar
        # (indices 0=N, 1=E, 2=U, matching the polynomial params/sigmas row order used above). This
        # assumes the N/E/U noise shares the same temporal shape (only scaled per component), so the
        # single overall residual correlation can be reused for any parameter pair, including the
        # velocities; it is the same approximation GeoDE's residual-ellipse scaling already relies on.
        covar = etm.fit.covar
        cov_ne = cov_nu = cov_eu = np.nan
        if covar.size and covar[0, 0] > 0 and covar[1, 1] > 0 and covar[2, 2] > 0:
            r_ne = covar[0, 1] / np.sqrt(covar[0, 0] * covar[1, 1])
            r_nu = covar[0, 2] / np.sqrt(covar[0, 0] * covar[2, 2])
            r_eu = covar[1, 2] / np.sqrt(covar[1, 1] * covar[2, 2])
            cov_ne = r_ne * sn * se
            cov_nu = r_nu * sn * su
            cov_eu = r_eu * se * su

        tvec    = etm.solution_data.time_vector
        t_first = tvec[0]
        t_last  = tvec[-1]
        tspan   = t_last - t_first
        nobs    = etm.solution_data.solutions

        stnm_out = ('%s_%s' % (net, stnm)).upper()
        lat = etm.config.metadata.lat[0]
        lon = etm.config.metadata.lon[0]

        line = '%10s %10.6f %11.6f %10.3f %10.3f %10.3f %10d %10.3f %10.3f %10.3f %10.3f %10.3f %10.3f %10.3f ' \
              '%12.4f %12.4f %12.4f' \
              % (stnm_out, lat, lon, t_first, t_last, tspan, nobs, ve, vn, vu, vh, se, sn, su,
                 cov_ne, cov_nu, cov_eu)

        return None, line, station_id

    except SolutionDataException as e:
        return str(e), None, station_id
    except Exception:
        return traceback.format_exc(), None, station_id


def process_fit_dates(args):
    dates = []
    if args.fit_window is not None:
        if np.mod(len(args.fit_window), 2):
            raise ValueError('Fit dates must be given in start/end pairs')

        from geode.Utils import process_date
        for i in range(0, len(args.fit_window), 2):
            try:
                dates.append([d.fyear for d in process_date(args.fit_window[i:i + 2])])
            except ValueError:
                raise ValueError('Invalid fit dates')

    return None if not dates else dates


def main():
    # open (and immediately discard) a database connection before anything else, so that any
    # pending schema migrations run here, in the main process, before --parallel forks workers.
    dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)

    parser = argparse.ArgumentParser(description='Print the velocities (and basic time series '
                                                 'stats) of every station in a solution, computed '
                                                 'directly from the ETM engine.',
                                     formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('-sol', '--solution', type=str, metavar='{stack|ppp}', default='ppp',
                        help="Required: specify the GAMIT stack name or ppp to use GPSPACE solutions. "
                             "All stations found in this solution are processed (no station list needed).")

    parser.add_argument('-o', '--output', type=str, default=None, metavar='file',
                        help="File to save the output table. If not specified, output goes to stdout")

    parser.add_argument('-lsq', '--least_squares_strategy',
                        choices=['lsc', 'rls', 'gls'], default='rls',
                        help="Adjustment strategy to fit the time series. Choose between least squares collocation "
                             "(lsc), robust least squares (rls) and generalized least squares with a power-law + "
                             "white + periodic noise covariance estimated from the residuals (gls)")

    parser.add_argument('-const', '--constraints', nargs='+', default=None, metavar='{json} {function}',
                        help="Constrain fit using json function (ETM) file. See PlotETM.py -h for the full syntax")

    parser.add_argument('-prefit', '--prefit_model', nargs='+', default=None,
                        metavar='{json} {function}',
                        help="Remove model from data using json function (ETM) file. See PlotETM.py -h for the "
                             "full syntax")

    parser.add_argument('-cova', '--covariance', nargs='+',
                        choices=['arma', 'gaussian'], default=['arma', 49, 200],
                        help="To use with strategy lsc, provide the method to estimate the covariance matrix. "
                             "Choose between Autoregressive Moving Average (arma, default) and gaussian covariance "
                             "function. If arma is used, optionally provide the number of roots for the decomposition "
                             "(49 by default) and the number of points to estimate the AR process (200 by default)")

    parser.add_argument('-fit_win', '--fit_window', nargs='+', metavar='interval',
                        help='Date range to window data fit. Can be specified in yyyy/mm/dd, yyyy_doy '
                             'in pairs for data ranges to be included in the ETM fit. Data outside '
                             'the range will not be included in the fit')

    parser.add_argument('-no_jump_check', '--no_jump_check', action='store_true', default=False,
                        help="Avoid checking for possible collisions between jumps which can lead to "
                             "unstable systems of equations. Default is to check jump collisions")

    parser.add_argument('-s_score', '--s_score_mag_limit', type=float, default=6.0, metavar='magnitude',
                        help="Limit the s-score search to earthquakes with magnitude >= {magnitude}. Default is 6.0")

    parser.add_argument('-post_back', '--post_seismic_back', type=str, default=None, metavar='years|date',
                        help="How many years (or date) before the start of the time series should the ETM fit "
                             "postseismic transients of M>=7 earthquakes. Default is "
                             f"{ModelingParameters().post_seismic_back_lim // 365} years (the ETM module default)")

    parser.add_argument('-force', '--force_earthquakes', nargs='+', default=[], metavar='event_id',
                        help="Add cherry-picked seismic earthquake (that fall outside of s_score_mag_limit) to the "
                             "list of jump functions to fit (using the USGS event id). Event needs to have an "
                             "s-score > 0 to be considered, even if it has been cherry-picked")

    parser.add_argument('-cond', '--max_condition_number', type=float,
                        default=ValidationRules().max_condition_number, metavar='log10(cond)',
                        help="Maximum acceptable log10 condition number for a jump function. Jump parameters with "
                             "log10(cond) > max_condition_number will get their smallest relaxation value removed. If "
                             "high condition number persists, then the jump will be reduced to POSTSEISMIC-ONLY if "
                             "jump is CO+POSTSEISMIC")

    parser.add_argument('-pdet', '--periodic_detection', choices=['joint', 'stepwise'], default='joint',
                        help="With -lsq gls, how periodic terms are detected in the noise model: joint harmonic fit "
                             "(joint, default) or stepwise grid search (stepwise, the earlier method, kept for "
                             "comparison)")

    parser.add_argument('-snr', '--snr_min', type=float, default=None, metavar='threshold',
                        help="With -lsq gls, SNR threshold of the detection of periodic terms. Default is 8 for the "
                             "joint harmonic fit (where it is also the misfit-ratio threshold of the final check) "
                             "and 7 for the stepwise grid search")

    parser.add_argument('-vfac', '--variance_factors', action='store_true', default=False,
                        help="With -lsq gls, also compute the variance factors of the colored and white parts of "
                             "the noise model (costs one n x n triangular inverse per component)")

    parser.add_argument('-sigma', '--sigma_limit', type=float, default=2.5,
                        help="Number of sigmas for the limit of outlier detection. Default is 2.5 sigma")

    parser.add_argument('-iter', '--max_iterations', type=int, default=10,
                        help="Maximum number of iterations during outlier detection. Default is 10")

    parser.add_argument('-relax', '--default_relax', type=float, nargs='+',
                        default=ModelingParameters().relaxation,
                        help="Relaxation value(s) to use during the fit. Default as defined by the station in "
                             "the database or in the ETM module (0.05 and 1 years)")

    parser.add_argument('-event_relax', '--event_relax', type=str, nargs='+',
                        default=[], metavar='event_id relax1 relax2',
                        help="Override default relaxation values with custom relaxation for specific events without "
                             "modifying the database. If custom database entry exists, this parameter overrides "
                             "the existing configuration")

    parser.add_argument('-fit_auto', '--fit_auto_jumps', nargs=1,
                        choices=['angry', 'dbscan'], default=[None],
                        help="Fit unmodeled but automatically detected jumps. "
                             "Choose between two algorithms: angry (used and provided by JPL) and dbscan")

    parser.add_argument('-no_save', '--no_save_database', action='store_true', default=False,
                        help="Do not fetch / save ETM solution from / to database")

    parser.add_argument('-p', '--parallel', action='store_true', default=False,
                        help="Run in parallel using available cluster nodes. "
                             "Requires gnss_data.cfg in the working directory.")

    parser.add_argument('-verbosity', '--verbosity',
                        choices=['quiet', 'info', 'debug'], default='info',
                        help="Determine how detailed the execution messages should be. "
                             "Default is 'info'")

    add_version_argument(parser)

    args = parser.parse_args()

    cnn = dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)

    setup_etm_logging(level=VERBOSITY_MAP[args.verbosity])
    _etm_logger = logging.getLogger('geode.etm')
    _etm_logger.handlers.clear()
    _h = TqdmLoggingHandler()
    _h.setFormatter(logging.Formatter(' -- %(message)s'))
    _etm_logger.addHandler(_h)

    stnlist = get_solution_stations(cnn, args.solution)

    if not stnlist:
        print(' >> ERROR: no stations found for solution "%s"' % args.solution, file=sys.stderr)
        sys.exit(1)

    print(' >> Found %d station(s) in solution "%s"' % (len(stnlist), args.solution), file=sys.stderr)

    from geode import pyJobServer, pyOptions

    Config = pyOptions.ReadOptions('gnss_data.cfg')
    Config.run_parallel = args.parallel

    args_dict = vars(args).copy()
    args_dict['_fit_dates'] = process_fit_dates(args)

    JobServer = pyJobServer.JobServer(Config,
                                     check_archive=False,
                                     check_executables=False,
                                     check_atx=False)

    pbar = tqdm(desc='%-30s' % ' >> Processing ETM',
               total=len(stnlist), ncols=160, disable=None)

    results = []

    def collect_and_report(job):
        callback_print_velocity(job)
        if job.result is not None:
            error_msg, line, station_id = job.result
            if line is not None:
                results.append(line)

    JobServer.create_cluster(process_etm_velocity, (), collect_and_report, pbar,
                             modules=('geode.dbConnection',
                                      'geode.etm.core.etm_engine',
                                      'geode.etm.core.etm_config',
                                      'geode.etm.core.type_declarations',
                                      'geode.etm.data.solution_data',
                                      'geode.etm.core.data_classes',
                                      'geode.etm.etm_functions.polynomial',
                                      'geode.etm.etm_functions.periodic',
                                      'geode.etm.etm_functions.jumps',
                                      'geode.etm.core.logging_config',
                                      'geode.Utils',
                                      'geode.pyDate',
                                      'numpy'))

    for stn in stnlist:
        tqdm.write(' >> About to process %s.%s' % (stn['NetworkCode'], stn['StationCode']))
        JobServer.submit(stn, args_dict)

    JobServer.wait()
    pbar.close()

    JobServer.close_cluster()

    results.sort()
    output = HEADER + '\n' + '\n'.join(results) + '\n'

    if args.output:
        file_write(args.output, output)
        print(' >> Wrote %d station(s) to %s' % (len(results), args.output), file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
