"""
Per-station ETM construction for the stacker.

Every function here takes its inputs explicitly (station codes, a stacker config,
a db connection) and returns plain data -- none of them read or write stacker state.
This is deliberate: it is the unit of work that would be handed to a parallel job
server (see com/PlotETM.py's process_etm_station / JobServer pattern) if/when the
stacker is parallelized station-by-station. Folding a result back into EtmStacker's
state (self.stations, self.total_parameters, ...) is the caller's job, not this
module's.
"""

import os
import copy
import json
import logging
import math
from typing import List, Optional, Tuple

import numpy as np

from .data_classes import EtmStackerConfig, Station
from ..core.etm_engine import EtmEngine
from ..core.etm_config import EtmConfig
from ..core.type_declarations import SolutionType, FitStatus, JumpType, AdjustmentModels
from ..data.solution_data import SolutionDataException
from ..least_squares.design_matrix import DesignMatrixException
from ..etm_functions.jumps import JumpFunction
from ...dbConnection import Cnn
from ...pyDate import Date
from ...Utils import stationID

logger = logging.getLogger(__name__)

# Suffix for the sidecar file that pins a cached <net>.<stn>_ppp.json to the stacker
# parameters that were active when it was saved. See _signature_matches.
_SIGNATURE_SUFFIX = '_ppp.stacker_params.json'


def _signature_path(folder: str, network_code: str, station_code: str) -> str:
    return os.path.join(folder, f'{network_code}.{station_code}{_SIGNATURE_SUFFIX}')


def _stacker_signature(config: EtmStackerConfig) -> dict:
    """
    Snapshot of every EtmStackerConfig field that determines which earthquakes end up
    in a station's jump list (and, via relaxation, the shape of its postseismic
    columns): earthquake_magnitude_limit and post_seismic_back_lim decide the s-score
    scan in prepare_earthquake_list; postseismic_min_separability drops the ones the
    station missed whose decay column is flat over its span; relaxation decides the decay
    columns every surviving jump gets, and therefore also what that test is applied to;
    earthquakes_cherry_picked is that scan's resolved output (already final by
    the time a station is built, since prepare_earthquake_list runs first). A JSON
    cached under different values of any of these no longer matches what this run would
    build from the database.
    """
    back_lim = config.post_seismic_back_lim
    if isinstance(back_lim, Date):
        back_lim_sig = {'type': 'date', 'mjd': back_lim.mjd}
    else:
        back_lim_sig = {'type': 'years_back', 'value': float(back_lim)}

    return {
        'earthquake_magnitude_limit': float(config.earthquake_magnitude_limit),
        'post_seismic_back_lim': back_lim_sig,
        'postseismic_min_separability': float(config.postseismic_min_separability),
        'relaxation': sorted(float(r) for r in config.relaxation),
        'earthquakes_cherry_picked': sorted(str(e) for e in config.earthquakes_cherry_picked),
    }


def _signatures_equal(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    if not math.isclose(a['earthquake_magnitude_limit'], b['earthquake_magnitude_limit'], rel_tol=1e-9):
        return False
    if a['post_seismic_back_lim']['type'] != b['post_seismic_back_lim']['type']:
        return False
    a_key = 'mjd' if a['post_seismic_back_lim']['type'] == 'date' else 'value'
    if not math.isclose(a['post_seismic_back_lim'][a_key], b['post_seismic_back_lim'][a_key], rel_tol=1e-9):
        return False
    if len(a['relaxation']) != len(b['relaxation']) or not all(
            math.isclose(x, y, rel_tol=1e-9) for x, y in zip(a['relaxation'], b['relaxation'])):
        return False
    if not math.isclose(a['postseismic_min_separability'], b['postseismic_min_separability'],
                        rel_tol=1e-9):
        return False
    if a['earthquakes_cherry_picked'] != b['earthquakes_cherry_picked']:
        return False
    return True


def _signature_matches(json_folder: str, network_code: str, station_code: str,
                       config: EtmStackerConfig) -> bool:
    """
    True only if a stacker-parameter signature file exists next to the cached JSON and
    matches the current run's config. Missing (e.g. a JSON saved before this mechanism
    existed) or mismatched both return False, so the caller falls back to the database
    exactly as if the JSON were absent -- never silently loads a stale cache.
    """
    sig_path = _signature_path(json_folder, network_code, station_code)
    if not os.path.isfile(sig_path):
        logger.info(f'No stacker-parameter signature next to the cached json for '
                    f'{network_code}.{station_code} (cached before this check existed); '
                    f'rebuilding from the database to be safe')
        return False

    try:
        with open(sig_path) as f:
            cached = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.info(f'Could not read signature file {sig_path}: {e}')
        return False

    current = _stacker_signature(config)
    if not _signatures_equal(cached, current):
        logger.info(f'Cached json for {network_code}.{station_code} was saved with '
                    f'different stacker parameters (cached={cached}, current={current}); '
                    f'rebuilding from the database')
        return False

    return True


def apply_config(config: EtmStackerConfig, network_code: str, station_code: str, cnn: Cnn) -> EtmConfig:
    """Build the EtmConfig for a station's cherry-picked (constrained) ETM fit."""
    # Set an impossibly high magnitude limit so the ScoreTable SQL "mag >= limit" branch
    # never fires. Only events in earthquakes_cherry_picked (the pre-computed canonical
    # list) will be admitted via the "id IN cherry_picked" branch of the query.
    # Passed at construction (rather than set afterwards + refresh_config) so the
    # earthquake jump query in EtmConfig.__init__ runs once with the final values.
    etm_config = EtmConfig(network_code, station_code, cnn=cnn,
                           post_seismic_back_lim=config.post_seismic_back_lim,
                           earthquake_magnitude_limit=10,
                           earthquakes_cherry_picked=config.earthquakes_cherry_picked)
    etm_config.validation.max_condition_number = config.max_condition_number
    etm_config.modeling.check_jump_collisions = False  # turn off jump collision check. Add all jumps.
    etm_config.modeling.relaxation = config.relaxation
    etm_config.modeling.postseismic_min_separability = config.postseismic_min_separability
    etm_config.plotting_config.plot_show_outliers = True
    if config.gls_for_all_stations:
        etm_config.modeling.least_squares_strategy.adjustment_model = AdjustmentModels.NOISE_COVARIANCE_GLS

    # @todo: implement a permanent fix for this. The clean solution is Option A:
    #   add a `force_relaxation: bool = False` flag to ModelingParameters
    #   (data_classes.py) and check it first in JumpFunction._setup_relaxation()
    #   (jumps.py) to skip the user_jumps lookup entirely. This would make the
    #   stacker's relaxation authoritative without mutating the loaded user_jumps.
    #
    # Workaround: _load_jump_config (run during EtmConfig construction above) reloads
    # per-jump relaxation from the DB via user_jumps, which silently overrides
    # config.modeling.relaxation in JumpFunction._setup_relaxation(). Overwrite the
    # relaxation field on all geophysical user_jumps here so the stacker's values
    # are actually used.
    for jp in etm_config.modeling.user_jumps:
        if jp.jump_type >= JumpType.COSEISMIC_JUMP_DECAY:
            jp.relaxation = config.relaxation.copy()

    return etm_config


def deactivate_mechanical_jumps(etm: EtmEngine) -> None:
    """Turn off fit=True on every mechanical jump already active in an ETM's jump manager."""
    for j in etm.jump_manager.get_active_mechanical_jumps():
        j.fit = False


def sync_geophysical_jumps(etm: EtmEngine, constraints: List[JumpFunction]) -> None:
    """Make `etm`'s geophysical jumps match what `constraints` actually says something about.

    An ETM rebuilt from an EtmStackerConfig derives its jump table afresh from
    earthquakes_cherry_picked, which holds every event any station in the network scored
    and admits them per-station without a magnitude check, back to post_seismic_back_lim
    (a Date, so an absolute epoch rather than a window before the station's own start).
    earthquake_catalog.drop_unconstrainable_postseismic then removes the events no station
    can resolve -- but a rebuild re-admits every one of them, and a sparse station's own
    free fit, which is not subject to the drop, carries them as fit=True as well. Neither
    is a usable reference; the constraint list is, because it is built from the events the
    stack still models.

    So: a geophysical jump with no constraint is deactivated, and one whose constraint
    describes a different jump type adopts that type (the stack is the authority on what is
    modelled, and leaving a decay column the stack does not constrain is the whole problem).

    This matters because a station whose data begins years after an event gets a
    log(1 + dt/tau) column that is flat over its own span, and several such columns are
    collinear with each other and with the intercept. The resulting design is rank
    deficient, and least_squares catches DesignMatrixException by deactivating *every*
    function -- so the station loses its trend and seasonals too, and reports
    UNABLE_TO_FIT despite having a usable time series.
    """
    jump_constraints = [c for c in constraints if c.p.object == 'jump']

    for jump in etm.jump_manager.jumps:
        if not jump.is_geophysical():
            continue

        match = next((c for c in jump_constraints
                      if Date(datetime=c.p.jump_date) == Date(datetime=jump.p.jump_date)), None)
        if match is None:
            jump.fit = False
        elif match.p.jump_type != jump.p.jump_type:
            jump.configure_behavior({'jump_type': match.p.jump_type})


def apply_prefit(etm: EtmEngine, prefit: List[JumpFunction]) -> None:
    """Install mechanical-jump prefit models on an ETM and deactivate their own fit flags.

    No-op if prefit is empty.
    """
    if not prefit:
        return
    etm.config.modeling.status = FitStatus.PREFIT
    etm.config.modeling.prefit_models = copy.deepcopy(prefit)
    deactivate_mechanical_jumps(etm)


def build_zero_tie_constraints(etm: EtmEngine) -> List[JumpFunction]:
    """Build soft zero-tie constraints for all geophysical jumps (coseismic and postseismic).

    Used when a station ETM returns UNABLE_TO_FIT to regularize the system so it can be
    solved. Parameters are tied to zero with the stacker's postseismic sigmas.
    Note: fit state is ignored because UNABLE_TO_FIT deactivates all design matrix functions.
    """
    constraints = []

    for jump in etm.jump_manager.jumps:
        if not jump.is_geophysical():
            continue

        jc = JumpFunction(etm.config, time_vector=np.array([0]),
                          date=jump.date, jump_type=jump.p.jump_type, fit=False)

        for j in range(3):
            sigma = 10  # self.config.postseismic_v_sigma if j == 2 else self.config.postseismic_h_sigma
            jc.p.params[j] = np.zeros(jc.param_count)
            jc.p.sigmas[j] = np.full(jc.param_count, sigma)

        constraints.append(jc)

    return constraints


def _note(notes: List[str], reason: str) -> str:
    """Join whatever happened on the way with why the station was finally dropped."""
    return '; '.join(notes + [reason])


def build_etm(config: EtmStackerConfig, cnn: Cnn, network_code: str, station_code: str,
             json_folder: str = None, save_json_folder: str = None
             ) -> Tuple[Optional[EtmEngine], Optional[EtmEngine], str]:
    """
    Build (or load) a station's ETM.

    Returns a (etm, unconstrained_etm) pair, where unconstrained_etm is the plain
    "Step 1" free fit (no cherry-picked earthquakes) used to derive unbiased obs_sigmas
    and detect mechanical jumps -- or None when that step was skipped (JSON-loaded
    station, or a station the unconstrained fit itself could not fit). Callers that need
    a free fit for a sparse station should reuse this object when present rather than
    calling build_free_fit again.

    Returns (etm, unconstrained_etm, note). etm is None when the station cannot be used
    at all, and note then says why in a few words; when the ETM was built but something
    happened on the way (a rank-deficient first attempt, a zero-tie retry), note records
    that instead.

    Nothing in here prints. This runs in a worker process, and a worker cannot clear the
    parent's progress bar -- tqdm.write only knows about bars in its own process -- so
    anything written from here lands on top of the bar, detached from the seven other
    stations building alongside it. The caller collects these notes into one summary
    table (stacker._report_station_builds); detail goes to the logger.
    """
    notes: List[str] = []
    loaded_from_json = False
    saved_obs_sigmas = None
    saved_noise_covariance_lag = None
    prefit: List[JumpFunction] = []
    etm = None
    unconstrained_etm: Optional[EtmEngine] = None

    # Check for a saved JSON first. The JSON was written after obs_sigmas restoration
    # (step 3 below), so it already carries the correct weights. Running the
    # unconstrained ETM against current DB data would produce an observation count
    # that may not match the JSON snapshot, causing a size mismatch in
    # _create_normal_equations. Skip steps 1 and 3 entirely for the JSON path.
    json_path = (os.path.join(json_folder, f'{network_code}.{station_code}_ppp.json')
                 if json_folder is not None else None)
    json_usable = (json_path is not None and os.path.isfile(json_path) and
                  _signature_matches(json_folder, network_code, station_code, config))

    if json_usable:
        logger.info(f'Loading etm for {network_code}.{station_code} from json file')
        etm_config = EtmConfig(json_file=json_path)
        etm = EtmEngine(etm_config)
        loaded_from_json = True
    else:
        if json_folder is not None and json_path is not None and not os.path.isfile(json_path):
            logger.info(f'Could not find etm json for {network_code}.{station_code}, '
                        f'will try to use the database')
        # else: json_path exists but _signature_matches already explained why it was rejected

        # @todo: check why stations not getting the default relaxation from the etm_stacker config
        # STEP 1: Always run a plain unconstrained ETM (no cherry-picked events) first.
        # This gives unbiased obs_sigmas and detects any mechanical jumps for correction.
        try:
            unconstrained_config = EtmConfig(network_code, station_code, cnn=cnn)
            unconstrained_config.modeling.relaxation = np.array([np.max(config.relaxation)])
            unconstrained_config.modeling.postseismic_min_separability = \
                config.postseismic_min_separability
            if config.gls_for_all_stations:
                unconstrained_config.modeling.least_squares_strategy.adjustment_model = \
                    AdjustmentModels.NOISE_COVARIANCE_GLS
            etm_unconstrained = EtmEngine(unconstrained_config, cnn=cnn, silent=True)

            if (etm_unconstrained.solution_data.time_vector[-1] -
                    etm_unconstrained.solution_data.time_vector[0] <= config.min_time_span):
                # Same threshold stacker.add_station checks again once build_etm returns
                # (that second check is what actually catches a JSON-loaded station, since
                # Step 1 here never runs for one) -- using config.min_time_span here too,
                # rather than a separate hardcoded cutoff, means a too-short station is
                # rejected for the same reason and the same message regardless of which
                # of the two code paths (database vs. JSON) it came through.
                logger.info(f'Station {network_code}.{station_code} has less than '
                            f'{config.min_time_span} years of data, skipping')
                return None, None, _note(notes, f'span < {config.min_time_span:g} yr')

            etm_unconstrained.run_adjustment(try_loading_db=False, force_computation=True,
                                             try_save_to_db=False)
            etm_unconstrained.config.plotting_config.filename = \
                f'./production/{network_code}.{station_code}_unconstrained'
            etm_unconstrained.config.plotting_config.plot_show_outliers = True
            etm_unconstrained.plot()
        except SolutionDataException as e:
            logger.info(f'{network_code}.{station_code}: {e}')
            return None, None, _note(notes, 'no usable solutions')

        if etm_unconstrained.config.modeling.status == FitStatus.UNABLE_TO_FIT:
            logger.info(f'Unconstrained ETM for {network_code}.{station_code} could not '
                        f'fit; skipping obs_sigmas restoration and mechanical jump '
                        f'detection')
            notes.append('step-1 fit failed')
        else:
            unconstrained_etm = etm_unconstrained
            saved_obs_sigmas = [r.obs_sigmas.copy() for r in etm_unconstrained.fit.results]
            # Only meaningful when gls_for_all_stations fit this unconstrained run with
            # NoiseCovarianceGLS; empty (size 0) for the default robust-least-squares fit,
            # same as AdjustmentResults.noise_covariance_lag's own default.
            saved_noise_covariance_lag = [r.noise_covariance_lag.copy() for r in etm_unconstrained.fit.results]
            prefit = list(etm_unconstrained.jump_manager.get_active_mechanical_jumps())
            if prefit:
                logger.info(f'Found {len(prefit)} mechanical jump(s) in '
                            f'{network_code}.{station_code}; will correct via prefit')

    try:
        if etm is None:
            logger.info(f'Estimating etm for {network_code}.{station_code}')
            etm_config = apply_config(config, network_code, station_code, cnn)
            etm_config.solution.solution_type = SolutionType.PPP
            etm = EtmEngine(etm_config, cnn=cnn, silent=True)

        # Apply mechanical jump prefit before running the adjustment
        apply_prefit(etm, prefit)

        etm.run_adjustment(cnn=cnn, force_computation=not loaded_from_json,
                           try_loading_db=False, try_save_to_db=False)
        etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_stacker'
        etm.plot()
    except (DesignMatrixException, np.linalg.LinAlgError):
        logger.info(f'{network_code}.{station_code} rank deficient; redoing ETM with '
                    f'only 10 years of postseismic events')
        notes.append('rank deficient, retried')
        etm_config.validation.max_condition_number = 3
        etm = EtmEngine(etm_config, cnn=cnn, silent=True)
        apply_prefit(etm, prefit)
        try:
            etm.run_adjustment(cnn=cnn)
        except Exception:
            logger.info(f'Unable to fit {network_code}.{station_code}; station not added')
            return None, None, _note(notes, 'rank deficient, retry failed')
    except SolutionDataException as e:
        logger.info(f'{network_code}.{station_code}: {e}')
        return None, None, _note(notes, 'no usable solutions')

    if etm.config.modeling.status == etm.config.modeling.status.UNABLE_TO_FIT:
        logger.info(f'Unable to fit {network_code}.{station_code}; retrying with '
                    f'zero-tied coseismic/postseismic constraints')
        notes.append('zero-tied retry')
        # UNABLE_TO_FIT deactivates all design matrix functions (fit=False), so we inspect
        # jump_manager directly (ignoring fit state) to build the constraint list, then
        # recreate a fresh EtmEngine so the design matrix is rebuilt with everything active.
        zero_constraints = build_zero_tie_constraints(etm)
        if not zero_constraints:
            logger.info(f'No geophysical jumps to constrain for '
                        f'{network_code}.{station_code}; station not added')
            return None, None, _note(notes, 'no jumps to zero-tie')
        etm.config.modeling.least_squares_strategy.constraints = zero_constraints
        etm = EtmEngine(etm.config, cnn=cnn, silent=True)
        # prefit status and models are already carried in etm.config; only need to
        # deactivate the mechanical jumps in the freshly built jump_manager
        if prefit:
            deactivate_mechanical_jumps(etm)
        try:
            etm.run_adjustment(cnn=cnn, try_loading_db=False, force_computation=True, try_save_to_db=False)
            etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_stacker'
            etm.plot()
        except Exception:
            logger.info(f'Still unable to fit {network_code}.{station_code} with '
                        f'constraints; station not added')
            return None, None, _note(notes, 'unfittable with zero ties')
        if etm.config.modeling.status == etm.config.modeling.status.UNABLE_TO_FIT:
            logger.info(f'Still unable to fit {network_code}.{station_code} after '
                        f'constraints; station not added')
            return None, None, _note(notes, 'unfittable with zero ties')

    if np.any([np.isnan(r.parameters) for r in etm.fit.results]):
        logger.info(f'Station {network_code}.{station_code} combined with the list of '
                    f'earthquakes yielded a singular solution; station not added')
        return None, None, _note(notes, 'singular solution')

    # STEP 3: Restore obs_sigmas (and, when gls_for_all_stations fit both runs with
    # NoiseCovarianceGLS, noise_covariance_lag too) from the unconstrained run, when
    # available. The cherry-picked ETM may produce large residuals near geophysical
    # events, spuriously downweighting those observations in the stacker's normal
    # equations. The unconstrained run provides unbiased per-observation weights -- and,
    # for the same reason, an unbiased colored-noise covariance estimate: that estimate
    # is itself fit from this run's own residuals, so a badly-fit constrained model near
    # an event would distort it the same way it would distort obs_sigmas.
    if saved_obs_sigmas is not None:
        for i, r in enumerate(etm.fit.results):
            r.obs_sigmas = saved_obs_sigmas[i]
    if saved_noise_covariance_lag is not None:
        for i, r in enumerate(etm.fit.results):
            if saved_noise_covariance_lag[i].size:
                r.noise_covariance_lag = saved_noise_covariance_lag[i]

    if not loaded_from_json:
        # Always re-save into json_folder too (not just save_json_folder) when a
        # rebuild happened: this is what makes the signature check self-healing.
        # Without this, a -load_json-only run (no -save_json) that hits a stale/missing
        # signature rebuilds from the database correctly, but then leaves the same
        # stale signature on disk -- so every subsequent run with the same (new,
        # correct) parameters keeps rebuilding from the database forever, even though
        # nothing is actually changing run to run.
        save_folders = set()
        if save_json_folder is not None:
            save_folders.add(save_json_folder)
        if json_folder is not None:
            save_folders.add(json_folder)

        for folder in save_folders:
            # exist_ok=True (not an exists-then-create check): with parallel_workers > 1,
            # multiple worker processes can race to create the same folder.
            os.makedirs(folder, exist_ok=True)
            etm.save_etm(
                folder + '/',
                dump_functions=True,
                dump_observations=True,
                dump_raw_results=True,
                dump_design_matrix=True
            )
            with open(_signature_path(folder, network_code, station_code), 'w') as f:
                json.dump(_stacker_signature(config), f, indent=2)

    return etm, unconstrained_etm, '; '.join(notes)


def build_station_job(job: Tuple[EtmStackerConfig, str, str, Optional[str], Optional[str]]
                      ) -> Tuple[str, str, Optional[EtmEngine], Optional[str], str]:
    """
    multiprocessing worker for stacker.add_stations_parallel (local cores only -- see
    that method's docstring for why this isn't dispy/pyJobServer). job is
    (config, network_code, station_code, json_folder, save_json_folder), matching
    build_etm's own arguments bundled into one tuple since Pool.imap passes a single
    item per call.

    Always opens its own, fresh Cnn rather than taking one as an argument: a connection
    opened in the parent before forking must never be reused by a worker process.

    Limits this process's own BLAS thread pool to 1 via threadpoolctl, which (unlike
    OMP_NUM_THREADS/MKL_NUM_THREADS/OPENBLAS_NUM_THREADS) takes effect at runtime rather
    than only at BLAS-library-load time -- the parent process (com/EtmStacker.py) already
    set those env vars to 8 for its own single-process use before numpy/BLAS ever loaded,
    and a forked child inherits that already-initialized thread pool, so only a runtime
    override here actually stops each of parallel_workers processes from also spawning
    an 8-wide BLAS pool and oversubscribing the machine.

    Also forces the Agg backend: build_etm always calls etm.plot(), and an interactive
    GUI backend selected in the parent before forking is not safe to reuse in a forked
    child.

    Returns (network_code, station_code, etm, error). etm is None both for build_etm's
    own ordinary None return (its note says why) and for an
    exception build_etm doesn't itself handle, in which case error carries the
    traceback -- so one bad station can't take the whole pool down.
    """
    config, network_code, station_code, json_folder, save_json_folder = job

    import matplotlib
    matplotlib.use('Agg', force=True)
    from threadpoolctl import threadpool_limits

    try:
        with threadpool_limits(limits=1):
            cnn = Cnn('gnss_data.cfg')
            etm, _, reason = build_etm(config, cnn, network_code, station_code,
                                       json_folder, save_json_folder)
    except Exception:
        import traceback
        return network_code, station_code, None, traceback.format_exc(), 'worker exception'

    return network_code, station_code, etm, None, reason


def build_free_fit(cnn: Cnn, network_code: str, station_code: str,
                   data_model_window: Optional[List[List[float]]] = None,
                   postseismic_min_separability: Optional[float] = None) -> Optional[EtmEngine]:
    """
    Compute the same "Step 1" unconstrained ETM fit (polynomial + periodic + whatever
    mechanical/default-detected jumps it finds on its own, no cherry-picked earthquakes)
    that build_etm above already computes for every non-JSON station -- for a sparse
    station, which needs one of its own regardless of whether build_etm's own Step 1
    ran, since this one is fit with NOISE_COVARIANCE_GLS rather than the default robust
    least squares (see below), which build_etm's Step 1 is not.

    A sparse station's own cherry-picked ETM (station.etm) is not a safe stand-in for
    this fit: it jointly estimates nuisance terms (constant/periodic/mechanical jumps)
    alongside geophysical jumps, so using its output to clean the station's data would
    reintroduce that bias -- this free fit is the uncontaminated one.

    Fit with NOISE_COVARIANCE_GLS (not the default robust least squares) so that
    AdjustmentResults.noise_covariance_lag is populated: the sparse-constraint weighting
    (constraints/sparse.py) needs the station's own colored-noise covariance, not just a
    white-noise wrms, to avoid treating thousands of temporally-correlated daily epochs
    as independent observations.

    data_model_window (fyear windows, same format as ModelingParameters.data_model_window)
    restricts which of the station's own epochs this fit uses -- see
    constraints.sparse.build_sparse_free_fits for why this needs to match whatever window
    the fit's own result is later used over, not just be applied as a mask afterward.
    """
    try:
        unconstrained_config = EtmConfig(network_code, station_code, cnn=cnn)
        unconstrained_config.modeling.least_squares_strategy.adjustment_model = \
            AdjustmentModels.NOISE_COVARIANCE_GLS
        if postseismic_min_separability is not None:
            unconstrained_config.modeling.postseismic_min_separability = postseismic_min_separability
        if data_model_window is not None:
            unconstrained_config.modeling.data_model_window = data_model_window
            # EtmConfig.__init__ may have already loaded a station-specific
            # reference_epoch from the database (etm_params), which would otherwise
            # override the polynomial's own "reference_epoch == 0 -> use min(time_vector)"
            # default and anchor this fit's constant/velocity terms at that (possibly
            # long-predates-the-window) epoch regardless of data_model_window. Anchoring
            # explicitly at the window's own start keeps the fit's constant+velocity terms
            # (and therefore what compute_sparse_system_contribution subtracts as nuisance)
            # consistent with the span this fit is actually built from.
            unconstrained_config.modeling.reference_epoch = data_model_window[0][0]
        etm_unconstrained = EtmEngine(unconstrained_config, cnn=cnn, silent=True)
        etm_unconstrained.run_adjustment(try_loading_db=False, force_computation=True,
                                         try_save_to_db=False)
        return etm_unconstrained
    except SolutionDataException as e:
        logger.info(f'Could not compute free fit for sparse station '
                    f'{network_code}.{station_code}: {e}')
        return None


def build_free_fit_job(job: Tuple[str, str, Optional[List[List[float]]], Optional[float]]
                       ) -> Tuple[str, str, Optional[EtmEngine], Optional[str]]:
    """
    multiprocessing worker for constraints.sparse.build_sparse_free_fits -- same
    local-cores-only, fork-based pattern as build_station_job above (see its docstring
    for why), reused here because a sparse station's free fit is exactly the same kind
    of slow, independent-per-station unit of work (always NOISE_COVARIANCE_GLS -- see
    build_free_fit) that motivated parallelizing the full-station build loop in the
    first place. job is (network_code, station_code, data_model_window,
    postseismic_min_separability), matching build_free_fit's own arguments bundled into
    one tuple.

    Returns (network_code, station_code, etm, error), same convention as
    build_station_job: etm is None both for build_free_fit's own ordinary None return
    and for an exception it doesn't itself handle, in which case error carries the
    traceback.
    """
    network_code, station_code, data_model_window, postseismic_min_separability = job

    import matplotlib
    matplotlib.use('Agg', force=True)
    from threadpoolctl import threadpool_limits

    try:
        with threadpool_limits(limits=1):
            cnn = Cnn('gnss_data.cfg')
            etm = build_free_fit(cnn, network_code, station_code,
                                 data_model_window=data_model_window,
                                 postseismic_min_separability=postseismic_min_separability)
    except Exception:
        import traceback
        return network_code, station_code, None, traceback.format_exc()

    return network_code, station_code, etm, None


def create_station(etm: EtmEngine) -> Station:

    station = Station(
        etm.config.network_code,
        etm.config.station_code,
        etm.config.metadata.lon[0],
        etm.config.metadata.lat[0],
        etm.solution_data.coordinates.dates[0],
        etm
    )

    # figure out if station can participate on interseismic model
    jump = etm.jump_manager.get_first_geophysical()
    if jump is None or (jump is not None and jump.p.jump_date > station.first_obs):
        station.is_interseismic = True
        logger.info(f'Station {stationID(station)} is interseismic')

    return station
