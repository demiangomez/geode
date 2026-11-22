#!/usr/bin/env python

"""
Project: Geodetic Database Engine (GeoDE)
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
    stationID,
    add_version_argument,
    SmartHelpFormatter,
)
from geode.reports.station_kmz import station_data_from_db, build_kmz


def main():
    parser = argparse.ArgumentParser(
        description='Generate GeoDE GNSS station KMZ files for Google Earth.',
        epilog=(
            SmartHelpFormatter.RAW_MARKER +
            'Examples:\n'
            '  StationKmz.py ars.at47 -o /tmp/kmz/\n'
            '  StationKmz.py ars.%    -o /tmp/kmz/\n'
            '  StationKmz.py ars.at47 ars.at48 --separate -o /tmp/kmz/'
        ),
        formatter_class=SmartHelpFormatter,
    )

    parser.add_argument(
        'stnlist', type=str, nargs='*', default=[],
        help=SmartHelpFormatter.RAW_MARKER + station_list_help(),
    )
    parser.add_argument(
        '-o', '--output', default='./',
        help='Output directory for generated KMZ files (default: current directory).',
    )
    parser.add_argument(
        '--separate', action='store_true',
        help='Write one KMZ per station instead of a single combined file.',
    )
    parser.add_argument(
        '-proj', '--project', type=str, default=None,
        metavar='project.cfg',
        help='GAMIT project config file. When provided the station list is read '
             'from the project; explicit stnlist further filters it. Stations not '
             'in the project are placed in an "other stations" folder.',
    )

    add_version_argument(parser)
    args = parser.parse_args()

    if not args.stnlist and not args.project:
        parser.error('Provide at least one station (stnlist) or a project file (--project).')

    # ── Database connection ───────────────────────────────────────────────────
    cnn = dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)

    cfg = configparser.ConfigParser()
    cfg.read('gnss_data.cfg')
    media_path = cfg.get('archive', 'media', fallback=None)

    # ── Project file (optional) ───────────────────────────────────────────────
    project_name    = None
    project_stn_set = set()   # stationID strings of stations in the project

    if args.project:
        from geode.gamit.gamit_config import GamitConfiguration
        gamit_cfg    = GamitConfiguration(args.project, check_config=False)
        project_name = gamit_cfg.NetworkConfig.network_id.lower()
        proj_raw     = gamit_cfg.NetworkConfig['stn_list'].split(',')
        project_stn_set = {
            stationID(s) for s in process_stnlist(cnn, proj_raw)
        }
        print(f' >> Project: {project_name} ({len(project_stn_set)} stations)',
              file=sys.stderr)

    # ── Station list ──────────────────────────────────────────────────────────
    if args.stnlist:
        # Explicit list provided — use it as-is (may include stations outside project)
        stnlist = process_stnlist(cnn, args.stnlist)
    elif project_stn_set:
        # No explicit list — use all project stations
        stnlist = process_stnlist(
            cnn, [f'{s.split(".")[0]}.{s.split(".")[1]}' for s in project_stn_set]
        )
    else:
        stnlist = []

    if not stnlist:
        print(' >> No stations matched the provided list.', file=sys.stderr)
        sys.exit(1)

    # ── Output directory ─────────────────────────────────────────────────────
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    errors = []

    def _load_station(nc, sc):
        station = station_data_from_db(cnn, nc, sc, media_path=media_path)
        if project_stn_set:
            station.in_project = (f'{nc}.{sc}' in project_stn_set)
        return station

    if args.separate:
        # ── One KMZ per station ───────────────────────────────────────────────
        for stn in stnlist:
            nc  = stn['NetworkCode']
            sc  = stn['StationCode']
            tag = f'{nc}.{sc}'
            print(f' >> Processing {tag}', file=sys.stderr)
            try:
                station = _load_station(nc, sc)
            except Exception as exc:
                print(f' !! {tag}: failed to build data — {exc}', file=sys.stderr)
                errors.append(tag)
                continue
            out_path = out_dir / f'{nc}.{sc}.kmz'
            try:
                build_kmz([station], str(out_path), project_name=project_name)
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
                stations.append(_load_station(nc, sc))
            except Exception as exc:
                print(f' !! {tag}: failed to build data — {exc}', file=sys.stderr)
                errors.append(tag)

        if stations:
            if project_name:
                out_name = f'{project_name}.kmz'
            elif len(stations) == 1:
                s        = stations[0]
                out_name = f'{s.network}.{s.station}.kmz'
            else:
                out_name = 'geode_stations.kmz'
            out_path = out_dir / out_name
            try:
                build_kmz(stations, str(out_path), project_name=project_name)
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
