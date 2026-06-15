#!/usr/bin/env python

"""
Project: Geodesy Database Engine (GeoDE)
Date: 2025
Author: Demian D. Gomez

Generate HTML or PDF station reports from the GeoDE database.

Fetches station metadata, instrument history, visit records, contacts,
ETM time-series plots (PPP solution), and RINEX data availability plots
for one or more stations and writes self-contained HTML (or PDF) reports.

Usage examples
--------------
  # Single station
  StationReport ARS.AT47 -o /tmp/reports/

  # All stations in a network
  StationReport ARS.% -o /tmp/reports/

  # Multiple explicit stations
  StationReport ARS.AT47 ARS.AT48 -o /tmp/reports/

  # Render to PDF (requires weasyprint)
  StationReport ARS.AT47 --pdf -o /tmp/reports/

  # Skip map tile download (faster, no network needed)
  StationReport ARS.AT47 --no-maps -o /tmp/reports/
"""

import argparse
import configparser
import os
import sys
from pathlib import Path

from geode import dbConnection
from geode.Utils import (
    process_stnlist,
    station_list_help,
    add_version_argument,
)
from geode.reports.station_report import (
    StationReport,
    station_from_db,
    build_report,
    render_pdf,
)

try:
    from geode.reports.map_fetch import attach_maps_to_station
    _MAP_FETCH = True
except ImportError:
    _MAP_FETCH = False


def main():
    parser = argparse.ArgumentParser(
        prog='StationReport',
        description='Generate GeoDE GNSS station reports (HTML or PDF).',
        epilog=(
            'Examples:\n'
            '  StationReport ARS.AT47 -o /tmp/reports/\n'
            '  StationReport ARS.%    --pdf -o /tmp/reports/\n'
            '  StationReport ARS.AT47 --no-maps -o /tmp/reports/'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        'stnlist', type=str, nargs='+',
        help=station_list_help(),
    )
    parser.add_argument(
        '-o', '--output', default='./',
        help='Output directory for generated reports (default: current directory).',
    )
    parser.add_argument(
        '--pdf', action='store_true',
        help='Render reports to PDF instead of HTML (requires weasyprint).',
    )
    parser.add_argument(
        '--no-maps', action='store_true',
        help='Skip map tile fetching (reports will omit location maps).',
    )
    parser.add_argument(
        '--logo', default=None, metavar='PATH',
        help='Path to the GeoDE logo PNG to embed in the report header.',
    )

    add_version_argument(parser)
    args = parser.parse_args()

    # ── Database connection ───────────────────────────────────────────────────
    cnn = dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)

    # Media path from gnss_data.cfg [archive] media
    cfg = configparser.ConfigParser()
    cfg.read('gnss_data.cfg')
    media_path = cfg.get('archive', 'media', fallback=None)

    # ── Station list ─────────────────────────────────────────────────────────
    stnlist = process_stnlist(cnn, args.stnlist)
    if not stnlist:
        print(' >> No stations matched the provided list.', file=sys.stderr)
        sys.exit(1)

    # ── Output directory ─────────────────────────────────────────────────────
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Per-station report ────────────────────────────────────────────────────
    errors = []
    for stn in stnlist:
        nc = stn['NetworkCode']
        sc = stn['StationCode']
        tag = f'{nc}.{sc}'

        print(f' >> Processing {tag}', file=sys.stderr)

        # Build StationReport (queries DB, generates ETM + RINEX plots)
        try:
            station = station_from_db(cnn, nc, sc, media_path=media_path)
        except Exception as exc:
            print(f' !! {tag}: failed to build report — {exc}', file=sys.stderr)
            errors.append(tag)
            continue

        # Optionally set logo
        if args.logo and os.path.exists(args.logo):
            station.logo_path = args.logo

        # Fetch map tiles if not already on disk
        if not args.no_maps and _MAP_FETCH:
            needs_maps = not any([
                station.map_general_path and os.path.exists(station.map_general_path),
                station.map_detail_path  and os.path.exists(station.map_detail_path),
                station.satellite_path   and os.path.exists(station.satellite_path),
            ])
            if needs_maps:
                try:
                    attach_maps_to_station(station)
                except Exception as exc:
                    print(f' !! {tag}: map fetch failed — {exc}', file=sys.stderr)

        # Render report
        try:
            html = build_report(station)
        except Exception as exc:
            print(f' !! {tag}: render failed — {exc}', file=sys.stderr)
            errors.append(tag)
            continue

        # Write output
        if args.pdf:
            out_path = out_dir / f'{nc}.{sc}_report.pdf'
            try:
                render_pdf(html, str(out_path))
            except Exception as exc:
                print(f' !! {tag}: PDF render failed — {exc}', file=sys.stderr)
                errors.append(tag)
        else:
            out_path = out_dir / f'{nc}.{sc}_report.html'
            out_path.write_text(html, encoding='utf-8')
            print(f'    {tag} → {out_path}  ({len(html) // 1024} KB)')

    if errors:
        print(f'\n >> {len(errors)} station(s) failed: {", ".join(errors)}',
              file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
