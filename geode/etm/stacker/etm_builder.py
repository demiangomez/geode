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
from typing import List, Optional, Tuple

import numpy as np
import numpy.linalg.linalg
from tqdm import tqdm

from .data_classes import EtmStackerConfig, Station
from ..core.etm_engine import EtmEngine
from ..core.etm_config import EtmConfig
from ..core.type_declarations import SolutionType, FitStatus, JumpType
from ..data.solution_data import SolutionDataException
from ..least_squares.design_matrix import DesignMatrixException
from ..etm_functions.jumps import JumpFunction
from ...dbConnection import Cnn
from ...Utils import stationID, print_yellow


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
    etm_config.plotting_config.plot_show_outliers = True

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


def build_etm(config: EtmStackerConfig, cnn: Cnn, network_code: str, station_code: str,
             json_folder: str = None, save_json_folder: str = None
             ) -> Optional[Tuple[EtmEngine, Optional[EtmEngine]]]:
    """
    Build (or load) a station's ETM.

    Returns a (etm, unconstrained_etm) pair, where unconstrained_etm is the plain
    "Step 1" free fit (no cherry-picked earthquakes) used to derive unbiased obs_sigmas
    and detect mechanical jumps -- or None when that step was skipped (JSON-loaded
    station, or a station the unconstrained fit itself could not fit). Callers that need
    a free fit for a sparse station should reuse this object when present rather than
    calling build_free_fit again.

    Returns None if the station cannot be used at all.
    """
    loaded_from_json = False
    saved_obs_sigmas = None
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

    if json_path is not None and os.path.isfile(json_path):
        tqdm.write(f'Loading etm for {network_code}.{station_code} from json file')
        etm_config = EtmConfig(json_file=json_path)
        etm = EtmEngine(etm_config)
        loaded_from_json = True
    else:
        if json_folder is not None:
            tqdm.write(f'Could not find etm json for {network_code}.{station_code}, '
                       f'will try to use the database')

        # @todo: check why stations not getting the default relaxation from the etm_stacker config
        # STEP 1: Always run a plain unconstrained ETM (no cherry-picked events) first.
        # This gives unbiased obs_sigmas and detects any mechanical jumps for correction.
        try:
            unconstrained_config = EtmConfig(network_code, station_code, cnn=cnn)
            unconstrained_config.modeling.relaxation = np.array([np.max(config.relaxation)])
            etm_unconstrained = EtmEngine(unconstrained_config, cnn=cnn, silent=True)

            if (etm_unconstrained.solution_data.time_vector[-1] -
                    etm_unconstrained.solution_data.time_vector[0] <= 1.5):
                tqdm.write(print_yellow(f' -- Station {network_code}.{station_code} has less than 1.5 '
                                        f'years of data, skipping'))
                return None

            etm_unconstrained.run_adjustment(try_loading_db=False, force_computation=True,
                                             try_save_to_db=False)
            etm_unconstrained.config.plotting_config.filename = \
                f'./production/{network_code}.{station_code}_unconstrained'
            etm_unconstrained.config.plotting_config.plot_show_outliers = True
            etm_unconstrained.plot()
        except SolutionDataException as e:
            tqdm.write(print_yellow(str(e)))
            return None

        if etm_unconstrained.config.modeling.status == FitStatus.UNABLE_TO_FIT:
            tqdm.write(print_yellow(f' -- Unconstrained ETM for {network_code}.{station_code} '
                                    f'could not fit; skipping obs_sigmas restoration and '
                                    f'mechanical jump detection'))
        else:
            unconstrained_etm = etm_unconstrained
            saved_obs_sigmas = [r.obs_sigmas.copy() for r in etm_unconstrained.fit.results]
            prefit = list(etm_unconstrained.jump_manager.get_active_mechanical_jumps())
            if prefit:
                tqdm.write(f' -- Found {len(prefit)} mechanical jump(s) in {network_code}.{station_code}; '
                           f'will correct via prefit')

    try:
        if etm is None:
            tqdm.write(f'Estimating etm for {network_code}.{station_code}')
            etm_config = apply_config(config, network_code, station_code, cnn)
            etm_config.solution.solution_type = SolutionType.PPP
            etm = EtmEngine(etm_config, cnn=cnn, silent=True)

        # Apply mechanical jump prefit before running the adjustment
        apply_prefit(etm, prefit)

        etm.run_adjustment(cnn=cnn, force_computation=not loaded_from_json,
                           try_loading_db=False, try_save_to_db=False)
        etm.config.plotting_config.filename = f'./production/{network_code}.{station_code}_stacker'
        etm.plot()
    except (DesignMatrixException, numpy.linalg.linalg.LinAlgError):
        tqdm.write(print_yellow(f' -- Unable to fit {network_code}.{station_code} -> system is rank deficient. '
                                f'Will redo ETM with only 10 years of postseismic events.'))
        etm_config.validation.max_condition_number = 3
        etm = EtmEngine(etm_config, cnn=cnn, silent=True)
        apply_prefit(etm, prefit)
        try:
            etm.run_adjustment(cnn=cnn)
        except Exception:
            tqdm.write(print_yellow(f' -- Unable to fit {network_code}.{station_code}. '
                                    f'Station will not be added.'))
            return None
    except SolutionDataException as e:
        tqdm.write(print_yellow(str(e)))
        return None

    if etm.config.modeling.status == etm.config.modeling.status.UNABLE_TO_FIT:
        tqdm.write(print_yellow(f' -- Unable to fit station {network_code}.{station_code}. '
                                f'Retrying with zero-tied coseismic/postseismic constraints.'))
        # UNABLE_TO_FIT deactivates all design matrix functions (fit=False), so we inspect
        # jump_manager directly (ignoring fit state) to build the constraint list, then
        # recreate a fresh EtmEngine so the design matrix is rebuilt with everything active.
        zero_constraints = build_zero_tie_constraints(etm)
        if not zero_constraints:
            tqdm.write(print_yellow(f' -- No geophysical jumps to constrain for '
                                    f'{network_code}.{station_code}. Station will not be added.'))
            return None
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
            tqdm.write(print_yellow(f' -- Still unable to fit {network_code}.{station_code} with constraints. '
                                    f'Station will not be added.'))
            return None
        if etm.config.modeling.status == etm.config.modeling.status.UNABLE_TO_FIT:
            tqdm.write(print_yellow(f' -- Still unable to fit {network_code}.{station_code} after constraints. '
                                    f'Station will not be added.'))
            return None

    if np.any([np.isnan(r.parameters) for r in etm.fit.results]):
        tqdm.write(print_yellow(f' -- Station {network_code}.{station_code} combined with the list of earthquakes '
                                f'yielded a singular solution, station cannot be used'))
        return None

    # STEP 3: Restore obs_sigmas from the unconstrained run (when available).
    # The cherry-picked ETM may produce large residuals near geophysical events,
    # spuriously downweighting those observations in the stacker's normal equations.
    # The unconstrained run provides unbiased per-observation weights.
    if saved_obs_sigmas is not None:
        for i, r in enumerate(etm.fit.results):
            r.obs_sigmas = saved_obs_sigmas[i]

    if save_json_folder is not None and not loaded_from_json:
        if not os.path.exists(save_json_folder):
            os.makedirs(save_json_folder)
        etm.save_etm(
            save_json_folder + '/',
            dump_functions=True,
            dump_observations=True,
            dump_raw_results=True,
            dump_design_matrix=True
        )

    return etm, unconstrained_etm


def build_free_fit(cnn: Cnn, network_code: str, station_code: str) -> Optional[EtmEngine]:
    """
    Lazily compute the "Step 1" unconstrained ETM fit (polynomial + periodic + whatever
    mechanical/default-detected jumps it finds on its own, no cherry-picked earthquakes)
    for a sparse station whose main build skipped it (e.g. loaded from JSON via -load_json).

    This is the "free, uncontaminated fit" the sparse_station_constraints.md spec requires
    before a sparse station's data can be cleaned of nuisance terms -- see that file for why
    the station's own cherry-picked ETM (station.etm) must not be used for this. Only called
    for stations that have already turned out to be sparse, never unconditionally, so it does
    not slow down -load_json runs for full stations.
    """
    try:
        unconstrained_config = EtmConfig(network_code, station_code, cnn=cnn)
        etm_unconstrained = EtmEngine(unconstrained_config, cnn=cnn, silent=True)
        etm_unconstrained.run_adjustment(try_loading_db=False, force_computation=True,
                                         try_save_to_db=False)
        return etm_unconstrained
    except SolutionDataException as e:
        tqdm.write(print_yellow(f' -- Could not compute free fit for sparse station '
                                f'{network_code}.{station_code}: {e}'))
        return None


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
        tqdm.write(f' -- Station {stationID(station)} is interseismic')

    return station
