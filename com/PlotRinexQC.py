#!/usr/bin/env python
"""
Project: GeoDE
Date: 11/18/26
Author: GeoDE RINEX QC module

PlotRinexQC
==============================================================================
Visualizations for RinexQC results (geode.rinex_qc.visualization): a daily
summary time series (completeness, MP RMS, SNR mean, slip rate) and two
elevation/azimuth-vs-time heatmaps, one constellation per figure. Equipment
changes (receiver/antenna/radome/firmware, from stationinfo) and site visits
(api_visits) are overlaid as vertical lines on every plot.

Writes one <net>.<stn>_<gnss>_<plot_type>.<format> file per station x
constellation x plot type under --out-dir. For base64/backend use instead of
files on disk, call geode.rinex_qc.visualization.plot_station() directly with
out_dir=None.
"""
import argparse
import sys

# app
from geode import dbConnection
from geode import Utils
from geode.Utils import add_version_argument, station_list_help
from geode.rinex_qc import visualization as viz


def main():
    parser = argparse.ArgumentParser(
        description='GeoDE RinexQC plots: daily summary time series and elevation/azimuth-vs-time '
                    'heatmaps, one constellation per figure.')

    parser.add_argument('stnlist', type=str, nargs='+', help=station_list_help())

    parser.add_argument('-gnss', '--gnss', nargs='+', choices=['G', 'R', 'E', 'C'], metavar='{G|R|E|C}',
                        help='Restrict to these GNSS systems (default: all the station has QC data for).')
    parser.add_argument('-plot', '--plot-types', nargs='+',
                        choices=['summary', 'elevation', 'azimuth'],
                        default=['summary', 'elevation', 'azimuth'],
                        help='Which plot(s) to generate (default: all three).')
    parser.add_argument('-elev', '--elev-range', nargs=2, type=float, metavar=('MIN', 'MAX'),
                        help='Azimuth-vs-time plot only: restrict to signal observed within this '
                             'elevation range in degrees (default: all elevations).')
    parser.add_argument('-az', '--az-range', nargs=2, type=float, metavar=('MIN', 'MAX'),
                        help='Elevation-vs-time plot only: restrict to signal observed within this '
                             'azimuth range in degrees, 0=north, clockwise (default: all azimuths).')
    parser.add_argument('-start', '--date-start', type=str, metavar='{date}',
                        help='First day to plot: fyear, yyyy_ddd, yyyy/mm/dd, or gpswk-wkday '
                             '(default: earliest rinex_qc_run for the station).')
    parser.add_argument('-end', '--date-end', type=str, metavar='{date}',
                        help='Last day to plot: same formats as --date-start '
                             '(default: latest rinex_qc_run for the station).')
    parser.add_argument('-out', '--out-dir', metavar='{path}', default='production/rinex_qc_plots/',
                        help="Output directory (default: 'production/rinex_qc_plots/').")
    parser.add_argument('-format', '--format', default='png', metavar='{png|pdf|svg}',
                        help="Image format (default: 'png').")

    add_version_argument(parser)

    args = parser.parse_args()

    if args.elev_range and args.elev_range[0] >= args.elev_range[1]:
        parser.error('--elev-range MIN must be less than MAX')
    if args.az_range and args.az_range[0] >= args.az_range[1]:
        parser.error('--az-range MIN must be less than MAX')

    try:
        date_start = Utils.process_date_str(args.date_start) if args.date_start else None
        date_end = Utils.process_date_str(args.date_end) if args.date_end else None
    except ValueError as e:
        parser.error(str(e))

    cnn = dbConnection.Cnn('gnss_data.cfg')
    station_list = Utils.process_stnlist(cnn, args.stnlist)

    exit_code = 0
    for stn in station_list:
        network, station = stn['NetworkCode'], stn['StationCode']
        try:
            gnss_present = viz.get_gnss_present(cnn, network, station)
        except Exception as e:
            print(f' -- {network}.{station}: {e}', file=sys.stderr)
            exit_code = 1
            continue

        if not gnss_present:
            print(f' -- {network}.{station}: no RinexQC data, skipping')
            continue

        gnss_list = [g for g in (args.gnss or gnss_present) if g in gnss_present]
        if not gnss_list:
            print(f' -- {network}.{station}: none of {args.gnss} present '
                 f'(has {gnss_present}), skipping')
            continue

        print(f' >> {network}.{station}: {gnss_list} x {args.plot_types}')
        viz.plot_station(cnn, network, station, plot_types=args.plot_types, gnss_list=gnss_list,
                         out_dir=args.out_dir, fmt=args.format, elev_range=args.elev_range,
                         az_range=args.az_range, date_start=date_start, date_end=date_end)

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
