"""
Orchestrates one RINEX file through prepare -> orbits -> runner -> signals ->
aggregate -> writer. A single entry point (process_one_file) used by both
com/RinexQC.py and the ArchiveService.py QC hook, so there is exactly one
place that decides what a failed/partial file looks like.
"""
import dataclasses
import os
import time

from geode import pyProducts
from geode.rinex_qc import aggregate, orbits, prepare, runner, signals

STATUS_OK      = 'ok'
STATUS_PARTIAL = 'partial'
STATUS_FAILED  = 'failed'

TOOL_NAME = 'gnssmultipath'
# Keep in lockstep with the pin in pyproject.toml/environment.yml (ADR-0003)
# -- a version bump here without bumping those pins would silently make
# runs claim a version that isn't actually what's installed.
TOOL_VERSION = '2.2.0'


class QCRunResult:
    def __init__(self, status, message=None, run=None, signals=None):
        self.status  = status
        self.message = message
        self.run     = run        # dict of run-level fields -- always set (see process_one_file)
        self.signals = signals or []  # list of {summary, bins, grid_id} dicts


def process_one_file(origin_file, config, Config, NetworkCode=None, StationCode=None,
                     ObservationYear=None, ObservationDOY=None, rinex_id=None):
    """
    Run the full RinexQC pipeline on one archived RINEX file.

    :param config: a rinex_qc.config.RinexQCConfig.
    :param Config: a pyOptions.ReadOptions (for orbit-archive paths).
    :param ObservationYear/ObservationDOY/rinex_id: pass these when already known (e.g. from the
        rinex table during a DB-backed batch/backfill run) -- they seed the run record's identity
        up front, so even a file that fails before pyRinex can determine its own date still
        produces a complete, storable 'failed' run row rather than an unidentifiable one.
    :return: a QCRunResult whose .run is always a usable dict (never None) -- any failure in any
        stage becomes status='failed' with .run still populated from whatever was known plus the
        failure message, so a batch of files never aborts on, or silently drops, one bad file.
    """
    t0 = time.time()
    run = {
        'NetworkCode':             NetworkCode,
        'StationCode':             StationCode,
        'ObservationYear':         ObservationYear,
        'ObservationDOY':          ObservationDOY,
        'rinex_id':                rinex_id,
        'tool':                    TOOL_NAME,
        'tool_version':            TOOL_VERSION,
        'config_hash':             config.config_hash,
        'config':                  dataclasses.asdict(config),
        'elev_cutoff_deg':         config.elev_cutoff_deg,
        'orbit_source':            config.orbit_source,
        'orbit_files':             [],
        'systems_processed':      [],
        'systems_without_orbits':  [],
        'rinex_version':           None,
        'receiver_type':           None,
        'antenna_type':            None,
        'interval_s':              None,
        'epochs_expected':         None,
        'epochs_observed':         None,
        'n_clock_jumps':           None,
        'header_fixed':            False,
        'flags':                   [],
        'status':                  STATUS_FAILED,
        'message':                 None,
        'runtime_s':               None,
    }

    def failed(message):
        run['message']   = message
        run['runtime_s'] = time.time() - t0
        return QCRunResult(STATUS_FAILED, message=message, run=run)

    try:
        with prepare.prepare(origin_file, NetworkCode, StationCode) as rinexobj:
            run['NetworkCode']     = rinexobj.NetworkCode
            run['StationCode']     = rinexobj.StationCode
            run['ObservationYear'] = rinexobj.date.year
            run['ObservationDOY']  = rinexobj.date.doy
            run['rinex_version']   = rinexobj.rinex_version
            run['receiver_type']   = rinexobj.recType
            run['antenna_type']    = rinexobj.antType
            run['interval_s']      = rinexobj.interval
            run['header_fixed']    = rinexobj.header_fixed
            if rinexobj.header_fixed:
                run['flags'].append('header_fixed')

            observed_systems = set(rinexobj.satsys)
            desired_systems = set(config.systems) if config.systems else observed_systems

            with orbits.resolve_orbits(Config, rinexobj.date, desired_systems,
                                       orbit_source=config.orbit_source,
                                       work_dir=rinexobj.rootdir,
                                       explicit_nav_paths=config.nav_paths,
                                       explicit_sp3_paths=config.sp3_paths) as orbit_res:

                run['orbit_source'] = orbit_res.orbit_source
                run['orbit_files'] = [os.path.basename(p)
                                      for p in (*orbit_res.nav_paths, *orbit_res.sp3_paths)]
                run['systems_without_orbits'] = orbit_res.systems_without_orbits
                if orbit_res.systems_without_orbits:
                    run['flags'].append('systems_without_orbits')

                systems_to_process = sorted(desired_systems - set(orbit_res.systems_without_orbits))
                run['systems_processed'] = systems_to_process

                if not systems_to_process:
                    return failed(
                        'No system observed in %s has usable orbit coverage (requested %s, '
                        'no orbits for %s)' % (origin_file, sorted(desired_systems),
                                               orbit_res.systems_without_orbits))

                try:
                    run_result = runner.run(
                        rinexobj.rinex_path,
                        nav_paths=orbit_res.nav_paths,
                        sp3_paths=orbit_res.sp3_paths,
                        systems=systems_to_process,
                        elev_cutoff_deg=config.elev_cutoff_deg,
                        work_dir=rinexobj.rootdir,
                        keep_tool_outputs=config.keep_tool_outputs)
                except Exception as e:
                    return failed('gnssmultipath failed: %s' % e)

                run['runtime_s'] = run_result.runtime_s

                res = run_result.res
                grid = aggregate.make_equal_area_grid()

                signal_results = []
                for sys_name in res['GNSSsystems']:
                    sys_code = _sys_code(res, sys_name)
                    sys_dict = res[sys_name]
                    azimuth = res['Sat_position'][sys_code]['azimuth']
                    snr_codes = sys_dict.get('SNR', {})

                    for band_name in sys_dict['Bands']:
                        for obs_code in sys_dict[band_name]['Codes']:
                            stats = sys_dict[band_name].get(obs_code)
                            if not isinstance(stats, dict):
                                continue  # code had no usable combination (see runner.py)

                            mapping = signals.normalize(sys_code, obs_code, rinexobj.rinex_version)
                            snr_code, snr_band_only = signals.find_snr_code(
                                snr_codes.keys(), mapping.band_attr, rinexobj.rinex_version)
                            snr_array = snr_codes.get(snr_code) if snr_code else None

                            summary, bins = aggregate.aggregate_signal(stats, azimuth, grid,
                                                                       snr_array=snr_array)
                            summary['gnss']          = mapping.gnss
                            summary['band_attr']     = mapping.band_attr
                            summary['obs_code_orig'] = mapping.obs_code_orig
                            summary['snr_band_only'] = snr_band_only or mapping.snr_band_only
                            summary['snr_unit'] = (
                                'receiver'
                                if summary['snr_mean'] is not None
                                and config.snr_unit_policy.is_receiver_units(rinexobj.recType,
                                                                             summary['snr_mean'])
                                else 'dBHz')
                            signal_results.append({'summary': summary, 'bins': bins,
                                                   'grid_id': config.default_grid_id})

                extra = res['ExtraOutputInfo']
                run['epochs_expected'] = round(86400 / rinexobj.interval) if rinexobj.interval else None
                run['epochs_observed'] = extra['nEpochs']
                run['n_clock_jumps']   = extra['nClockJumps']
                if extra['nClockJumps'] and extra['nClockJumps'] > extra['nEpochs'] * 0.01:
                    run['flags'].append('clock_jumps')

                run['status']     = STATUS_PARTIAL if orbit_res.systems_without_orbits else STATUS_OK
                run['runtime_s']  = time.time() - t0
                return QCRunResult(run['status'], run=run, signals=signal_results)

    except (pyProducts.pyProductsException, orbits.OrbitGuardError,
           orbits.OrbitResolutionError) as e:
        return failed('Orbit resolution failed: %s' % e)
    except Exception as e:
        return failed(str(e))


def _sys_code(res, sys_name):
    _NAME2CODE = {'GPS': 'G', 'GLONASS': 'R', 'Galileo': 'E', 'BeiDou': 'C'}
    return _NAME2CODE[sys_name]
