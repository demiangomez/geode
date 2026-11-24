#!/usr/bin/env python

"""
Project: Geodetic Database Engine (GeoDE)
Author: Demian D. Gomez

Print the horizontal/vertical velocities (and basic time series stats) of every
station in a given solution (GAMIT stack or ppp), computed directly from the ETM
engine (no intermediate JSON files needed). Equivalent to looping over the JSON
files produced by PlotETM.py -json and extracting the polynomial velocity, except
the ETM is built and fit in-process and every station of the solution is included
by default (no station list needed), optionally narrowed down with -stn using the
standard GeoDE station selector syntax.

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
from geode.Utils import (add_version_argument, get_stack_stations, file_write, SmartHelpFormatter,
                         process_stnlist, station_list_help, stationID)


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
        error_msg, line, station_id, map_row = job.result
        if error_msg:
            tqdm.write(' -- Error processing %s:\n%s' % (station_id, error_msg))
        else:
            tqdm.write(' >> Successfully processed %s' % station_id)
    elif job.exception:
        tqdm.write(' -- Unhandled job exception:\n%s' % str(job.exception))


def process_etm_velocity(stn, args_dict):
    """
    Dispy worker: fully self-contained per-station ETM processing. Returns a
    (error_msg, output_line, station_id, map_row) tuple; output_line and map_row are
    None when the station could not be fit. map_row is a small dict of the numeric
    values (lat, lon, velocity, sigmas, N-E covariance) needed to plot this station on
    the velocity map (-map); it is always returned (cheap), whether or not -map was given.
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
    VERBOSITY_MAP = {
        'quiet': logging.CRITICAL,
        'info':  logging.INFO,
        'debug': logging.DEBUG
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

        config.modeling.postseismic_min_separability = args_dict['postseismic_min_separability']
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
        config.modeling.least_squares_strategy.noise_cross_covariance  = args_dict['cross_covariance']
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
        # force_computation=True: a DB-cached fit never repopulates .covar/.cross_covar (the noise
        # model and cross-covariance diagnostics aren't stored in the database -- see PlotETM.py's own
        # 'ellipses'/'covariance' force_recompute), and this script always reports Cov_ne/Cov_nu/Cov_eu,
        # so it must always recompute rather than silently printing NaN for any already-cached station.
        etm.run_adjustment(cnn=cnn,
                           try_save_to_db=not args_dict['no_save_database'],
                           try_loading_db=not args_dict['no_save_database'],
                           force_computation=True)

        if etm.config.modeling.status != FitStatus.POSTFIT:
            return ' -- unable to fit a model (insufficient or degenerate data)', None, station_id, None

        poly = etm.design_matrix.get_polynomial()
        if poly is None:
            return ' -- no polynomial (velocity) function found in design matrix', None, station_id, None

        vn, ve, vu = (p[1] * 1000 for p in poly.p.params)
        sn, se, su = (p[1] * 1000 for p in poly.p.sigmas)

        vh = np.sqrt(ve ** 2 + vn ** 2)

        # Velocity N-E, N-U, E-U covariances: the polynomial function's own cross-component
        # covariance block (EtmFit.process_cross_covariance, geode/etm/least_squares/
        # cross_covariance.py), populated for every fitted parameter by
        # EtmFunction.set_cross_covariance() after every adjustment -- the power-law cross-
        # spectrum model when available (-xcov with -lsq gls), otherwise the borrowed-residual-
        # correlation fallback. Same source and guard as the error-ellipse plot
        # (prepare_ellipse_data(), geode/etm/visualization/data_prep.py). Polynomial columns are
        # always [offset, velocity, acceleration], so index 1 is the velocity term.
        cov_ne = cov_nu = cov_eu = np.nan
        if poly.param_count > 1 and all(c.size for c in poly.p.covar):
            ne = poly.p.cross_covar.get('NE')
            nu = poly.p.cross_covar.get('NU')
            eu = poly.p.cross_covar.get('EU')
            if ne is not None:
                cov_ne = ne['matrix'][1][1] * 1000 ** 2
            if nu is not None:
                cov_nu = nu['matrix'][1][1] * 1000 ** 2
            if eu is not None:
                cov_eu = eu['matrix'][1][1] * 1000 ** 2

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

        map_row = {'stnm': stnm_out, 'lat': lat, 'lon': lon,
                  've': ve, 'vn': vn, 'se': se, 'sn': sn, 'cov_ne': cov_ne}

        return None, line, station_id, map_row

    except SolutionDataException as e:
        return str(e), None, station_id, None
    except Exception:
        return traceback.format_exc(), None, station_id, None


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


def plot_velocity_map(stations, output_file):
    """
    Plot a map of the horizontal station velocities (quiver arrows) and their 1-sigma N-E error
    ellipses, using the same per-station values already printed in the output table (ve, vn, se, sn,
    cov_ne -- the canonical N-E velocity cross-covariance, see process_etm_velocity()) and the same
    ellipse convention as EllipseTemplate (geode/etm/visualization/ellipse_template.py): eigh of the
    2x2 covariance matrix, 1-sigma full-axis lengths. Mercator projection centered on the data,
    unless the stations' mean latitude is beyond +-60 degrees, in which case a polar stereographic
    projection (north or south, matching the hemisphere) is used instead -- the usual choice for
    circumpolar GNSS networks, where Mercator's distortion near the poles makes the map unusable.

    Vectors and ellipses are each scaled independently to a legible on-screen size (velocity sigmas
    are routinely 1-2 orders of magnitude smaller than the velocities themselves, so a single,
    true-to-scale factor would make the ellipses invisible); the applied ellipse exaggeration factor
    is reported on stderr so the plot isn't mistaken for true-to-scale.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    import cartopy.crs as ccrs
    from matplotlib.patches import Ellipse

    from geode.etm.visualization.field_plot.map_renderer import CartopyRenderer
    from geode.etm.visualization.ellipse_template import _ellipse_from_covariance

    # 0.1 m/yr = 100 mm/yr: excludes ice/fast-moving stations (e.g. on ice sheets or across major
    # plate boundaries) whose vectors would otherwise dwarf the rest of the map and force every other
    # station's arrow down to an illegible size.
    max_vh_mm = 100.
    stations = [s for s in stations if np.isfinite(s['ve']) and np.isfinite(s['vn'])]
    if not stations:
        tqdm.write(' -- No stations with a valid velocity to plot on the map')
        return

    n_before = len(stations)
    stations = [s for s in stations if np.hypot(s['ve'], s['vn']) <= max_vh_mm]
    if len(stations) < n_before:
        tqdm.write(' -- Excluded %d station(s) with horizontal velocity > %g mm/yr from the map'
                  % (n_before - len(stations), max_vh_mm))
    if not stations:
        tqdm.write(' -- No stations left to plot on the map after excluding fast-moving stations')
        return

    lon = np.array([s['lon'] for s in stations])
    lat = np.array([s['lat'] for s in stations])
    ve  = np.array([s['ve'] for s in stations])
    vn  = np.array([s['vn'] for s in stations])

    central_lon = (lon.min() + lon.max()) / 2
    mean_lat    = lat.mean()

    if abs(mean_lat) > 60:
        projection = (ccrs.NorthPolarStereo if mean_lat > 0 else ccrs.SouthPolarStereo)(
            central_longitude=central_lon)
    else:
        projection = ccrs.Mercator(central_longitude=central_lon)

    fig = plt.figure(figsize=(12, 10))
    ax  = fig.add_subplot(1, 1, 1, projection=projection)

    lon_pad = max((lon.max() - lon.min()) * 0.15, 1.0)
    lat_pad = max((lat.max() - lat.min()) * 0.15, 1.0)
    ax.set_extent([lon.min() - lon_pad, lon.max() + lon_pad,
                  max(lat.min() - lat_pad, -90.), min(lat.max() + lat_pad, 90.)], crs=ccrs.PlateCarree())

    # Match the figure's aspect ratio to the plotted extent's: cartopy enforces equal aspect on the
    # map itself, so a mismatched figure (e.g. a wide lon/lat box in a square figure) would otherwise
    # pad the difference with blank space above/below (or left/right) the map.
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    data_aspect = (ylim[1] - ylim[0]) / (xlim[1] - xlim[0])
    if data_aspect <= 1.:
        fig_w, fig_h = 12., np.clip(12. * data_aspect, 4., 12.)
    else:
        fig_w, fig_h = np.clip(12. / data_aspect, 4., 12.), 12.
    fig.set_size_inches(fig_w, fig_h)

    CartopyRenderer(resolution='50m').add_features(ax)

    xy   = projection.transform_points(ccrs.PlateCarree(), lon, lat)
    x, y = xy[:, 0], xy[:, 1]

    # Local east/north do NOT correspond to constant screen x/y directions on an azimuthal
    # projection (e.g. polar stereographic): meridian convergence rotates "east"/"north" by a
    # different amount at every longitude (on a Mercator map this rotation happens to be ~0
    # everywhere, since standard cylindrical Mercator keeps every meridian vertical, but that's a
    # special case, not something to assume in general). Find that per-station rotation by
    # finite-differencing a tiny eastward lon/lat step and seeing which screen direction it lands
    # in, then rotate every vector (and, below, every ellipse) from local (east, north) into screen
    # (x, y) accordingly, before plotting.
    eps  = 0.01
    e0   = projection.transform_points(ccrs.PlateCarree(), lon - eps, lat)
    e1   = projection.transform_points(ccrs.PlateCarree(), lon + eps, lat)
    rot  = np.arctan2(e1[:, 1] - e0[:, 1], e1[:, 0] - e0[:, 0])
    cosr, sinr = np.cos(rot), np.sin(rot)
    ve_scr = ve * cosr - vn * sinr
    vn_scr = ve * sinr + vn * cosr

    ref_dim = min(xlim[1] - xlim[0], ylim[1] - ylim[0])

    # vectors: longest arrow spans ~8% of the shorter map dimension
    vh      = np.sqrt(ve ** 2 + vn ** 2)
    vh_max  = np.nanmax(vh) if np.any(np.isfinite(vh)) else 0.
    scale   = vh_max / (0.08 * ref_dim) if vh_max > 0 else 1.

    q = ax.quiver(x, y, ve_scr, vn_scr, scale=scale, scale_units='xy', color='red',
                 width=0.0022, edgecolor='black', linewidth=0.3, zorder=6)

    ref = round(vh_max / 2, 1) or 1.0
    ax.quiverkey(q, 0.85, 0.05, ref, '%g mm/yr' % ref, labelpos='E', coordinates='axes',
                fontproperties={'size': 8}, zorder=7)

    # ellipses: centered on the arrow tip (the vector's endpoint), not the station itself -- the
    # usual GNSS velocity-map convention (e.g. GMT's psvelo). Tip position uses the same quiver
    # 'scale' (scale_units='xy') and the same screen-rotated vector, so it lands exactly where the
    # plotted arrowhead is. The ellipse's own orientation (computed in the local east/north frame)
    # gets the same per-station rotation applied as the vectors, for the same reason.
    # Own independent scale for the ellipse size itself, so the largest one spans ~5% of the
    # shorter map dimension.
    ellipses = []
    for s, xi, yi, dve, dvn, rot_deg in zip(stations, x, y, ve_scr, vn_scr, np.degrees(rot)):
        cov_ne, se, sn = s['cov_ne'], s['se'], s['sn']
        if not (np.isfinite(cov_ne) and np.isfinite(se) and np.isfinite(sn)):
            continue
        width, height, angle = _ellipse_from_covariance(np.array([[se ** 2, cov_ne], [cov_ne, sn ** 2]]))
        tip_x, tip_y = xi + dve / scale, yi + dvn / scale
        ellipses.append((tip_x, tip_y, width, height, angle + rot_deg))

    if ellipses:
        major_max    = max(max(w, h) for _, _, w, h, _ in ellipses)
        ellipse_scale = major_max / (0.05 * ref_dim) if major_max > 0 else 1.
        for tip_x, tip_y, width, height, angle in ellipses:
            ax.add_patch(Ellipse((tip_x, tip_y), width / ellipse_scale, height / ellipse_scale, angle=angle,
                                 facecolor='none', edgecolor='black', linewidth=0.8, zorder=5))
        tqdm.write(' -- Error ellipses shown at %.0fx their true 1-sigma size (for visibility)'
                  % (1. / ellipse_scale))
    else:
        tqdm.write(' -- No station has a valid N-E velocity covariance; skipping error ellipses')

    ax.set_title('Station velocities and 1-sigma N-E error ellipses', fontsize=12, fontweight='bold')

    # GeoDE logo, bottom right -- same placement/approach as
    # geode.etm.visualization.etm_plotting.EtmPlotter._add_logo(). Reuse that module's own LOGO_PATH
    # (relative to the installed geode package, not to this script) rather than deriving a path from
    # this file's own location: PrintVelocities.py is installed as a standalone console script (see
    # setup.py), so it can end up anywhere (e.g. a conda env's bin/), with no fixed relationship to
    # where the geode package itself was installed.
    from geode.etm.visualization.etm_plotting import LOGO_PATH as logo_path
    if logo_path.exists():
        try:
            logo_img      = mpimg.imread(logo_path)
            width_frac    = 0.12
            aspect_ratio  = logo_img.shape[1] / logo_img.shape[0]
            height_frac   = width_frac / aspect_ratio
            logo_ax = fig.add_axes((1.0 - width_frac - 0.01, 0.01, width_frac, height_frac))
            logo_ax.imshow(logo_img, interpolation='lanczos', resample=True)
            logo_ax.axis('off')
        except Exception as e:
            tqdm.write(' -- Failed to add logo to velocity map: %s' % str(e))
    else:
        tqdm.write(' -- Logo file not found: %s' % logo_path)

    fig.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close(fig)
    tqdm.write(' >> Saved velocity map (%d station(s)) to %s' % (len(stations), output_file))


def main():
    # open (and immediately discard) a database connection before anything else, so that any
    # pending schema migrations run here, in the main process, before --parallel forks workers.
    dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)

    parser = argparse.ArgumentParser(description='Print the velocities (and basic time series '
                                                 'stats) of every station in a solution, computed '
                                                 'directly from the ETM engine.',
                                     formatter_class=SmartHelpFormatter)

    parser.add_argument('-sol', '--solution', type=str, metavar='{stack|ppp}', default='ppp',
                        help="Required: specify the GAMIT stack name or ppp to use GPSPACE solutions. "
                             "By default, every station found in this solution is processed (no station "
                             "list needed); narrow this down with -stn.")

    parser.add_argument('-stn', '--stations', nargs='+', type=str, metavar='{station list}', default=[],
                        help="Optional: restrict processing to a subset of the stations found in the "
                             "solution (-sol), using the standard GeoDE station selector syntax below. "
                             "If not specified, every station in the solution is processed." +
                             SmartHelpFormatter.RAW_MARKER + station_list_help())

    parser.add_argument('-o', '--output', type=str, default=None, metavar='file',
                        help="File to save the output table. If not specified, output goes to stdout")

    parser.add_argument('-map', '--plot_map', type=str, default=None, metavar='file',
                        help="Also plot a map of the station horizontal velocities (quiver arrows) and their "
                             "1-sigma N-E error ellipses (Cov_ne, Se, Sn -- same values as the output table), "
                             "saved to {file} (PNG). Mercator projection, unless the stations' mean latitude is "
                             "beyond +-60 degrees, in which case a polar stereographic projection (north or south, "
                             "matching the hemisphere) is used instead. Not plotted by default")

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

    parser.add_argument('-ps_separability', '--postseismic_min_separability', type=float,
                        default=ModelingParameters().postseismic_min_separability,
                        metavar='fraction',
                        help="Drop an earthquake's postseismic term when the station has no data from "
                             "before that event AND less than this fraction of the decay column's "
                             "variance survives removing a constant and a trend over the station's "
                             "own span -- i.e. the column is effectively a straight line, so fitting "
                             "it recovers no postseismic signal while taking an arbitrary share of "
                             "the velocity. An event the station actually observed is never dropped, "
                             "however collinear. Works alongside -post_back, which decides which "
                             "pre-observation events are candidates in the first place. Default "
                             f"{ModelingParameters().postseismic_min_separability:g}; 0 disables the filter.")

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

    parser.add_argument('-xcov', '--cross_covariance', action='store_true', default=False,
                        help="With -lsq gls, fit the power-law cross-spectrum model to the N-E/N-U/E-U velocity "
                             "cross-covariance (Cov_ne, Cov_nu, Cov_eu columns), fitted to the empirical cross-"
                             "covariance of the step-1 residuals at the epochs common to all three components. "
                             "Default: a cheaper borrowed-residual-correlation approximation "
                             "(Cov = r * sigma_i * sigma_j) is reported instead")

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

    if args.stations:
        selected = process_stnlist(cnn, args.stations,
                                   summary_title='Station selector matched (before intersecting with '
                                                 'the solution):')
        selected_ids = {stationID(s) for s in selected}
        stnlist = [s for s in stnlist if stationID(s) in selected_ids]

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

    results  = []
    map_rows = []

    def collect_and_report(job):
        callback_print_velocity(job)
        if job.result is not None:
            error_msg, line, station_id, map_row = job.result
            if line is not None:
                results.append(line)
            if map_row is not None:
                map_rows.append(map_row)

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

    if args.plot_map:
        plot_velocity_map(map_rows, args.plot_map)


if __name__ == '__main__':
    main()
