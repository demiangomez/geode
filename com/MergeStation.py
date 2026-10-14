#!/usr/bin/env python
"""
Project: Geodetic Database Engine (GeoDE)

Renames (merges) a station into another one: moves its RINEX files in the archive and its data in the database.
Use --dry-run to see what would be done without changing anything.
"""

import argparse

from geode import dbConnection
from geode import pyArchiveStruct
from geode.Utils import process_date_str, add_version_argument

CONFIG_FILE = 'gnss_data.cfg'


def main():
    parser = argparse.ArgumentParser(
        description='Take the data of the origin station and merge it into the destiny station. The RINEX files '
                    'are renamed in the archive to match the destiny station, and the station information, '
                    'time series tables and subnets are moved with them. The whole change of the database is '
                    'done in a single transaction.')

    parser.add_argument('origin', type=str, metavar='net.stnm',
                        help='Station that gives its data')

    parser.add_argument('destination', type=str, metavar='net.stnm',
                        help='Station that receives the data. It must exist in the database')

    date_help = 'Can be specified in wwww-d, yyyy_ddd, yyyy/mm/dd or fyear format. '

    parser.add_argument('-df', '--date_from', metavar='date',
                        help='First day of the data to move. ' + date_help +
                             'Default: since the beginning of the data')

    parser.add_argument('-dt', '--date_to', metavar='date',
                        help='Last day of the data to move. ' + date_help +
                             'Default: until the end of the data')

    parser.add_argument('-del_stn', '--delete_station', action='store_true', default=False,
                        help='Delete the origin station at the end if it is left without RINEX files.')

    parser.add_argument('--dry-run', action='store_true',
                        help='Preview mode: show what would be moved, skipped or deleted, but do not change '
                             'the database or the archive.')

    add_version_argument(parser)

    args = parser.parse_args()

    stations = []
    for arg in (args.origin, args.destination):
        if arg.count('.') != 1 or not all(arg.split('.')):
            parser.error('Format for the stations should be net.stnm')
        stations.append(tuple(arg.split('.')))

    try:
        date_from = process_date_str(args.date_from) if args.date_from else None
        date_to = process_date_str(args.date_to) if args.date_to else None
    except ValueError as e:
        parser.error(str(e))

    cnn = dbConnection.Cnn(CONFIG_FILE)

    pyArchiveStruct.RinexStruct(cnn).rename_station(*stations[0], *stations[1], date_from, date_to,
                                                    args.delete_station, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
