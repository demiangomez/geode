#!/usr/bin/env python
"""
Project: GeoDE
Date: 11/16/26
Author: GeoDE RINEX QC module

RinexQC
==============================================================================
Computes per-signal/per-bin RINEX quality-control statistics (code
multipath, SNR and cycle-slip metrics) via RINGO (see
scripts/install_ringo.sh), with full provenance (tool, version, config
hash).

Standalone mode (default): runs on one or more RINEX files directly,
writing one <file>_qc.json + <file>_qc_bins.csv per file. No database
involved.

Database modes (--incremental / --backfill): selects rinex rows that have
no QC run yet under the current config (optionally restricted to a
station list), and writes results straight to
rinex_qc_run/rinex_qc_signal/rinex_qc_signal_bins via GeoDE's existing
parallel-dispatch mechanism (pyJobServer). ArchiveService.py's QC hook
calls the same geode.rinex_qc.batch functions this uses, so there is one
implementation of "which files need a QC run and how to run them", not
two.
"""
import argparse
import os
import sys

from tqdm import tqdm

# app
from geode import dbConnection
from geode import pyJobServer
from geode import pyOptions
from geode import Utils
from geode.Utils import add_version_argument, station_list_help
from geode.rinex_qc import batch, pipeline, writer
from geode.rinex_qc.config import RinexQCConfig, RinexQCConfigError


def process_file(path, config, Config, out_dir):
    result = pipeline.process_one_file(path, config, Config)
    basename = os.path.splitext(os.path.basename(path))[0]
    writer.write_json_csv(out_dir, basename, result.run, result.signals)
    if config.keep_tool_outputs and result.run_result is not None:
        _write_tool_outputs(out_dir, basename, result.run_result)
    return result


def _write_tool_outputs(out_dir, basename, run_result):
    if run_result.qc_csv is not None:
        with open(os.path.join(out_dir, '%s_ringo_qcmode.csv' % basename), 'w') as f:
            f.write(run_result.qc_csv)
    if run_result.obs_csv is not None:
        with open(os.path.join(out_dir, '%s_ringo_obs.csv' % basename), 'w') as f:
            f.write(run_result.obs_csv)


def run_standalone(args, config):
    Config = pyOptions.ReadOptions('gnss_data.cfg')

    exit_code = 0
    for path in tqdm(args.targets, desc='%-30s' % ' >> RinexQC', ncols=160, disable=None):
        result = process_file(path, config, Config, args.out_dir)

        status_line = ' -- %-40s %s' % (os.path.basename(path), result.status)
        if result.message:
            status_line += ': %s' % result.message
        tqdm.write(status_line)

        if result.status == pipeline.STATUS_FAILED:
            exit_code = 1

    return exit_code


def run_db_mode(args, config):
    cnn = dbConnection.Cnn('gnss_data.cfg')
    Config = pyOptions.ReadOptions('gnss_data.cfg')

    station_list = Utils.process_stnlist(cnn, args.targets) if args.targets else None

    candidates = batch.find_pending(cnn, config, station_list=station_list,
                                    force=args.force, newest_first=args.backfill,
                                    limit=args.limit)
    tqdm.write(' >> Found %d file(s) pending QC' % len(candidates))

    if not candidates:
        return 0

    JobServer = pyJobServer.JobServer(Config, run_parallel=not args.noparallel,
                                      software_sync=[Config.options['ppp_remote_local']])

    desc = 'RinexQC backfill' if args.backfill else 'RinexQC incremental'
    counts = batch.process_batch(JobServer, candidates, config, force=args.force, desc=desc)

    tqdm.write(' >> ok=%d partial=%d failed=%d' % (counts.get(pipeline.STATUS_OK, 0),
                                                    counts.get(pipeline.STATUS_PARTIAL, 0),
                                                    counts.get(pipeline.STATUS_FAILED, 0)))
    return 1 if counts.get(pipeline.STATUS_FAILED) else 0


def main():
    parser = argparse.ArgumentParser(
        description='GeoDE RINEX QC: per-signal/per-bin multipath, SNR and cycle-slip '
                    'statistics via RINGO.')

    parser.add_argument('targets', nargs='*', metavar='rinex_file|station',
                        help='Standalone mode: one or more RINEX/CRINEX(Z) file paths. With '
                             '--backfill/--incremental: a station list instead (see below); '
                             'omit entirely to select the whole archive.\n\n' + station_list_help())
    parser.add_argument('-config', '--config', metavar='{json}',
                        help='JSON config file. CLI switches below override its values.')
    parser.add_argument('-elev', '--elev-cutoff', type=float, metavar='{deg}',
                        help='Elevation cutoff in degrees (default 10).')
    parser.add_argument('-sys', '--systems', nargs='+', metavar='{G|R|E|C}',
                        help='Restrict to these GNSS systems (default: all observed).')
    parser.add_argument('-nav', '--nav', nargs='+', metavar='{path}',
                        help='Explicit broadcast navigation file(s) (up to 4), overriding '
                             'archive lookup. Standalone mode only.')
    parser.add_argument('-sp3', '--sp3', nargs='+', metavar='{path}',
                        help='Explicit SP3 file(s) (up to 3), overriding archive lookup. '
                             'Standalone mode only.')
    parser.add_argument('-ringo', '--ringo-bin', metavar='{path}',
                        help="Path to the ringo binary (default: 'ringo', assumed on PATH -- "
                             'see scripts/install_ringo.sh).')
    parser.add_argument('-out', '--out-dir', metavar='{path}', default='.',
                        help='Standalone mode: output directory for *_qc.json/*_qc_bins.csv '
                             '(default: current directory).')
    parser.add_argument('-keep', '--keep-tool-outputs', action='store_true',
                        help='Standalone mode: also write *_ringo_qcmode.csv/*_ringo_obs.csv '
                             "(RINGO's raw per-epoch output) alongside *_qc.json.")
    parser.add_argument('-dry', '--dry-run', action='store_true',
                        help='Resolve inputs and print the plan only; no processing.')

    parser.add_argument('-backfill', '--backfill', action='store_true',
                        help='Database mode: process every pending file for the given station '
                             'list (or the whole archive), newest-first.')
    parser.add_argument('-incr', '--incremental', action='store_true',
                        help='Database mode: same selection as --backfill, but processed in '
                             'station/date order rather than newest-first -- for catching up a '
                             'small number of recently-archived files (see ArchiveService.py).')
    parser.add_argument('-force', '--force', action='store_true',
                        help='With --backfill/--incremental: reprocess files even if a run '
                             'already exists under the current config, replacing it.')
    parser.add_argument('-limit', '--limit', type=int, metavar='{n}',
                        help='With --backfill/--incremental: process at most n pending files '
                             '(e.g. to try a handful before committing to a large batch).')
    parser.add_argument('-np', '--noparallel', action='store_true',
                        help='Execute without parallelization.')

    add_version_argument(parser)

    args = parser.parse_args()

    if args.backfill and args.incremental:
        parser.error('--backfill and --incremental are mutually exclusive.')

    db_mode = args.backfill or args.incremental

    if not db_mode and not args.targets:
        parser.error('At least one RINEX file is required in standalone mode (or pass '
                     '--backfill/--incremental for database mode).')

    try:
        config = RinexQCConfig.load(
            json_path=args.config,
            elev_cutoff_deg=args.elev_cutoff,
            systems=tuple(args.systems) if args.systems else None,
            nav_paths=tuple(args.nav) if args.nav else None,
            sp3_paths=tuple(args.sp3) if args.sp3 else None,
            keep_tool_outputs=True if args.keep_tool_outputs else None,
            ringo_bin=args.ringo_bin,
        )
    except RinexQCConfigError as e:
        print(' >> Configuration error: %s' % e)
        sys.exit(1)

    if args.dry_run:
        if db_mode:
            print(' >> Would select pending files under config_hash %s for station(s): %s'
                  % (config.config_hash, ', '.join(args.targets) if args.targets else 'all'))
        else:
            print(' >> Would process %d file(s) with config_hash %s, output to %s:'
                  % (len(args.targets), config.config_hash, args.out_dir))
            for f in args.targets:
                print('   -', f)
        return

    if db_mode:
        sys.exit(run_db_mode(args, config))
    else:
        sys.exit(run_standalone(args, config))


if __name__ == '__main__':
    main()
