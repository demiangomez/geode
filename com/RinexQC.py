#!/usr/bin/env python
"""
Project: GeoDE
Date: 11/16/26
Author: GeoDE RINEX QC module

RinexQC
==============================================================================
Computes per-signal/per-bin RINEX quality-control statistics (code
multipath, SNR and cycle-slip metrics) via gnssmultipath, with full
provenance (tool, version, config hash).

Standalone mode (this phase): runs on one or more RINEX files, writing one
<file>_qc.json + <file>_qc_bins.csv per file. No database involved yet.
TODO(db): backfill/incremental modes against the whole archive, writing to
rinex_qc_run/rinex_qc_signal/rinex_qc_signal_bins instead of JSON/CSV.
"""
import argparse
import os
import sys

from tqdm import tqdm

# app
from geode import pyOptions
from geode.Utils import add_version_argument
from geode.rinex_qc import pipeline, writer
from geode.rinex_qc.config import RinexQCConfig, RinexQCConfigError


def process_file(path, config, Config, out_dir):
    result = pipeline.process_one_file(path, config, Config)
    if result.status != pipeline.STATUS_FAILED:
        basename = os.path.splitext(os.path.basename(path))[0]
        writer.write_json_csv(out_dir, basename, result.run, result.signals)
    return result


def main():
    parser = argparse.ArgumentParser(
        description='GeoDE RINEX QC: per-signal/per-bin multipath, SNR and cycle-slip '
                    'statistics via gnssmultipath.')

    parser.add_argument('files', nargs='+', metavar='rinex_file',
                        help='One or more RINEX/CRINEX(Z) file paths.')
    parser.add_argument('-config', '--config', metavar='{json}',
                        help='JSON config file. CLI switches below override its values.')
    parser.add_argument('-elev', '--elev-cutoff', type=float, metavar='{deg}',
                        help='Elevation cutoff in degrees (default 10).')
    parser.add_argument('-sys', '--systems', nargs='+', metavar='{G|R|E|C}',
                        help='Restrict to these GNSS systems (default: all observed).')
    parser.add_argument('-nav', '--nav', nargs='+', metavar='{path}',
                        help='Explicit broadcast navigation file(s) (up to 4), overriding '
                             'archive lookup.')
    parser.add_argument('-sp3', '--sp3', nargs='+', metavar='{path}',
                        help='Explicit SP3 file(s) (up to 3), overriding archive lookup.')
    parser.add_argument('-out', '--out-dir', metavar='{path}', default='.',
                        help='Output directory for *_qc.json/*_qc_bins.csv (default: '
                             'current directory).')
    parser.add_argument('-keep', '--keep-tool-outputs', action='store_true',
                        help="Don't delete gnssmultipath's own temporary output directory.")
    parser.add_argument('-dry', '--dry-run', action='store_true',
                        help='Resolve inputs and print the plan only; no processing.')

    add_version_argument(parser)

    args = parser.parse_args()

    try:
        config = RinexQCConfig.load(
            json_path=args.config,
            elev_cutoff_deg=args.elev_cutoff,
            systems=tuple(args.systems) if args.systems else None,
            nav_paths=tuple(args.nav) if args.nav else None,
            sp3_paths=tuple(args.sp3) if args.sp3 else None,
            keep_tool_outputs=True if args.keep_tool_outputs else None,
        )
    except RinexQCConfigError as e:
        print(' >> Configuration error: %s' % e)
        sys.exit(1)

    if args.dry_run:
        print(' >> Would process %d file(s) with config_hash %s, output to %s:'
              % (len(args.files), config.config_hash, args.out_dir))
        for f in args.files:
            print('   -', f)
        return

    Config = pyOptions.ReadOptions('gnss_data.cfg')

    exit_code = 0
    for path in tqdm(args.files, desc='%-30s' % ' >> RinexQC', ncols=160, disable=None):
        result = process_file(path, config, Config, args.out_dir)

        status_line = ' -- %-40s %s' % (os.path.basename(path), result.status)
        if result.message:
            status_line += ': %s' % result.message
        tqdm.write(status_line)

        if result.status == pipeline.STATUS_FAILED:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
