#!/usr/bin/env python

"""
Project: Geodesy Database Engine (GeoDE)
Date: 2025
Author: Demian D. Gomez

Generate Google Earth KMZ files for one or more GeoDE GNSS stations.

Each station gets a colour-coded placemark (based on its operational status)
with an HTML balloon popup showing coordinates, station metadata, site imagery,
visit history, and general comments.  Visit folders with navigation KMZ files
are embedded automatically when they exist in the media store.

Usage examples
--------------
  # Single station
  StationKmz.py ars.at47 -o /tmp/kmz/

  # All stations in a network
  StationKmz.py ars.% -o /tmp/kmz/

  # Multiple explicit stations — combined into one KMZ
  StationKmz.py ars.at47 ars.at48 -o /tmp/kmz/

  # One KMZ per station (--separate)
  StationKmz.py ars.% --separate -o /tmp/kmz/
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
from geode.reports.station_kmz import station_data_from_db, build_kmz


def main():
    parser = argparse.ArgumentParser(
        description='Generate GeoDE GNSS station KMZ files for Google Earth.',
        epilog=(
            'Examples:\n'
            '  StationKmz.py ars.at47 -o /tmp/kmz/\n'
            '  StationKmz.py ars.%    -o /tmp/kmz/\n'
            '  StationKmz.py ars.at47 ars.at48 --separate -o /tmp/kmz/'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        'stnlist', type=str, nargs='+',
        help=station_list_help(),
    )
    parser.add_argument(
        '-o', '--output', default='./',
        help='Output directory for generated KMZ files (default: current directory).',
    )
    parser.add_argument(
        '--separate', action='store_true',
        help='Write one KMZ per station instead of a single combined file.',
    )

    add_version_argument(parser)
    args = parser.parse_args()

    # ── Database connection ───────────────────────────────────────────────────
    cnn = dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)

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

    errors = []

    if args.separate:
        # ── One KMZ per station ───────────────────────────────────────────────
        for stn in stnlist:
            nc  = stn['NetworkCode']
            sc  = stn['StationCode']
            tag = f'{nc}.{sc}'
            print(f' >> Processing {tag}', file=sys.stderr)
            try:
                station = station_data_from_db(cnn, nc, sc, media_path=media_path)
            except Exception as exc:
                print(f' !! {tag}: failed to build data — {exc}', file=sys.stderr)
                errors.append(tag)
                continue
            out_path = out_dir / f'{nc}.{sc}.kmz'
            try:
                build_kmz([station], str(out_path))
                print(f'    {tag} → {out_path}')
            except Exception as exc:
                print(f' !! {tag}: KMZ build failed — {exc}', file=sys.stderr)
                errors.append(tag)

    else:
        # ── Combined KMZ ─────────────────────────────────────────────────────
        stations = []
        for stn in stnlist:
            nc  = stn['NetworkCode']
            sc  = stn['StationCode']
            tag = f'{nc}.{sc}'
            print(f' >> Processing {tag}', file=sys.stderr)
            try:
                station = station_data_from_db(cnn, nc, sc, media_path=media_path)
                stations.append(station)
            except Exception as exc:
                print(f' !! {tag}: failed to build data — {exc}', file=sys.stderr)
                errors.append(tag)

        if stations:
            if len(stations) == 1:
                s        = stations[0]
                out_name = f'{s.network}.{s.station}.kmz'
            else:
                out_name = 'geode_stations.kmz'
            out_path = out_dir / out_name
            try:
                build_kmz(stations, str(out_path))
                print(f'    {len(stations)} station(s) → {out_path}')
            except Exception as exc:
                print(f' !! KMZ build failed — {exc}', file=sys.stderr)
                sys.exit(1)

    if errors:
        print(f'\n >> {len(errors)} station(s) failed: {", ".join(errors)}',
              file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
