"""
Orchestrates one RINEX file through prepare -> orbits -> runner -> aggregate
-> writer. A single entry point (process_one_file) used by both
com/RinexQC.py and the ArchiveService.py QC hook, so there is exactly one
place that decides what a failed/partial file looks like.

prepare.prepare() (pyRinex.ReadRinex) still runs for every file, but only
for provenance metadata (header_fixed, rinex_version, receiver/antenna
type, interval) -- RINGO itself reads the original archived file directly
(see runner.py), not pyRinex's sanitized temp copy, since it handles raw
.Z/.gz/Hatanaka files and the header issues pyRinex fixes without any
pre-processing (knowledge/decisions/0005-switch-to-ringo.md).
"""
import dataclasses
import os
import time
import traceback

from geode import pyProducts
from geode.rinex_qc import aggregate, orbits, prepare, runner

STATUS_OK      = 'ok'
STATUS_PARTIAL = 'partial'
STATUS_FAILED  = 'failed'

TOOL_NAME = 'ringo'
# Keep in lockstep with the version scripts/install_ringo.sh installs by
# default (ADR-0005) -- a version bump here without bumping that default
# would silently make runs claim a version that isn't actually installed.
TOOL_VERSION = '0.9.6'


class QCRunResult:
    def __init__(self, status, message=None, run=None, signals=None, run_result=None):
        self.status     = status
        self.message    = message
        self.run         = run        # dict of run-level fields -- always set (see process_one_file)
        self.signals     = signals or []  # list of {summary, bins, grid_id} dicts
        self.run_result  = run_result     # runner.RunnerResult, set on success when
                                          # config.keep_tool_outputs -- com/RinexQC.py's standalone
                                          # mode uses it to write RINGO's raw CSVs alongside *_qc.json


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
                        origin_file,
                        nav_paths=orbit_res.nav_paths,
                        sp3_paths=orbit_res.sp3_paths,
                        elev_cutoff_deg=config.elev_cutoff_deg,
                        ringo_bin=config.ringo_bin,
                        keep_tool_outputs=config.keep_tool_outputs)
                except Exception:
                    # the bare exception message alone is useless for
                    # diagnosing a failure inside RINGO's own parsing --
                    # keep the full traceback so rinex_qc_run.message is
                    # enough to debug from later, without needing to
                    # reproduce it live.
                    return failed('ringo failed:\n%s' % traceback.format_exc())

                run['runtime_s']       = run_result.runtime_s
                run['epochs_observed'] = run_result.nepochs
                run['epochs_expected'] = round(86400 / rinexobj.interval) if rinexobj.interval else None

                grid = aggregate.make_equal_area_grid()

                signal_results = []
                for sys_code in systems_to_process:
                    bands = run_result.systems.get(sys_code, {})
                    for band, band_data in bands.items():
                        summary, bins = aggregate.aggregate_signal(band_data, grid)
                        summary['gnss']          = sys_code
                        summary['band_attr']     = band + '?'  # exact attribute unknown, see runner.py
                        summary['obs_code_orig'] = 'MP' + band
                        summary['snr_band_only'] = True
                        summary['snr_unit'] = (
                            'receiver'
                            if summary['snr_mean'] is not None
                            and config.snr_unit_policy.is_receiver_units(rinexobj.recType,
                                                                         summary['snr_mean'])
                            else 'dBHz')
                        signal_results.append({'summary': summary, 'bins': bins,
                                               'grid_id': config.default_grid_id})

                run['status']     = STATUS_PARTIAL if orbit_res.systems_without_orbits else STATUS_OK
                run['runtime_s']  = time.time() - t0
                return QCRunResult(run['status'], run=run, signals=signal_results,
                                  run_result=run_result if config.keep_tool_outputs else None)

    except (pyProducts.pyProductsException, orbits.OrbitGuardError,
           orbits.OrbitResolutionError) as e:
        return failed('Orbit resolution failed: %s' % e)
    except Exception:
        return failed(traceback.format_exc())
