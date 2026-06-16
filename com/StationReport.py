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
  StationReport.py ars.at47 -o /tmp/reports/

  # All stations in a network
  StationReport.py ars.% -o /tmp/reports/

  # Multiple explicit stations
  StationReport.py ars.at47 ars.at48 -o /tmp/reports/

  # Render to PDF (requires weasyprint)
  StationReport.py ars.at47 --pdf -o /tmp/reports/

  # Omit specific sections
  StationReport.py ars.at47 --no-instruments --no-timeseries -o /tmp/reports/
"""

import argparse
import configparser
import sys
from pathlib import Path

from geode import dbConnection
from geode.Utils import (
    process_stnlist,
    station_list_help,
    add_version_argument,
)
from geode.reports.station_report import (
    station_from_db,
    build_report,
    render_pdf,
)


def main():
    parser = argparse.ArgumentParser(
        description='Generate GeoDE GNSS station reports (HTML or PDF).',
        epilog=(
            'Examples:\n'
            '  StationReport.py ars.at47 -o /tmp/reports/\n'
            '  StationReport.py ars.%    --pdf -o /tmp/reports/\n'
            '  StationReport.py ars.at47 ars.at48 -o /tmp/reports/'
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

    # ── Section visibility ────────────────────────────────────────────────────
    parser.add_argument(
        '--no-instruments', action='store_true',
        help='Omit the Instrument History section.',
    )
    parser.add_argument(
        '--no-timeseries', action='store_true',
        help='Omit the Position Time Series (ETM) section.',
    )
    parser.add_argument(
        '--no-rinex', action='store_true',
        help='Omit the RINEX Data Availability section.',
    )
    parser.add_argument(
        '--no-contacts', action='store_true',
        help='Omit the Contact Information section.',
    )
    parser.add_argument(
        '--no-visits', action='store_true',
        help='Omit the Visit History section.',
    )
    parser.add_argument(
        '--no-geodynamics', action='store_true',
        help='Omit the Geodynamic Events section.',
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
            station = station_from_db(
                cnn, nc, sc,
                media_path        = media_path,
                show_instruments  = not args.no_instruments,
                show_timeseries   = not args.no_timeseries,
                show_rinex        = not args.no_rinex,
                show_contacts     = not args.no_contacts,
                show_visits       = not args.no_visits,
                show_geodynamics  = not args.no_geodynamics,
            )
        except Exception as exc:
            print(f' !! {tag}: failed to build report — {exc}', file=sys.stderr)
            errors.append(tag)
            continue

        # Render report
        try:
            html = build_report(
                station,
                show_instruments  = not args.no_instruments,
                show_timeseries   = not args.no_timeseries,
                show_rinex        = not args.no_rinex,
                show_contacts     = not args.no_contacts,
                show_visits       = not args.no_visits,
                show_geodynamics  = not args.no_geodynamics,
            )
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
