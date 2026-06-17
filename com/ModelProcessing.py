#!/usr/bin/env python
"""
Project: Geodesy Database Engine (GeoDE)
Date: 4/20/25 11:23 AM 
Author: Demian D. Gomez

Script to produce subnetworks and calculate GAMIT execution times based on Shane Grigsby's clustering algorithm

"""

import argparse
import simplekml
import numpy as np
from tqdm import tqdm
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
import json

from geode.pyDate import Date
from geode import dbConnection
from geode.pyDate import Date
from geode.clustering import BisectingQMeans, select_central_point, overcluster, prune, DeterministicClustering, plot_global_network, plot_geographic_cluster_graph
from geode.Utils import station_list_help, process_date, process_stnlist, stationID, add_version_argument, file_write


def generate_kmz(OC, lla, stations, central_points, filename):
    kml = simplekml.Kml()

    ICON_SQUARE = 'http://maps.google.com/mapfiles/kml/shapes/placemark_square.png'

    # define styles
    styles_stn = simplekml.StyleMap()
    styles_stn.normalstyle.iconstyle.icon.href = ICON_SQUARE
    styles_stn.normalstyle.iconstyle.color = 'ff00ff00'
    styles_stn.normalstyle.iconstyle.scale = 2
    styles_stn.normalstyle.labelstyle.scale = 0
    styles_stn.highlightstyle.iconstyle.icon.href = ICON_SQUARE
    styles_stn.highlightstyle.iconstyle.color = 'ff00ff00'
    styles_stn.highlightstyle.iconstyle.scale = 3
    styles_stn.highlightstyle.labelstyle.scale = 2

    styles_tie = simplekml.StyleMap()
    styles_tie.normalstyle.iconstyle.icon.href = ICON_SQUARE
    styles_tie.normalstyle.iconstyle.color = 'ff0000ff'
    styles_tie.normalstyle.iconstyle.scale = 2
    styles_tie.normalstyle.labelstyle.scale = 0
    styles_tie.highlightstyle.iconstyle.icon.href = ICON_SQUARE
    styles_tie.highlightstyle.iconstyle.color = 'ff0000ff'
    styles_tie.highlightstyle.iconstyle.scale = 3
    styles_tie.highlightstyle.labelstyle.scale = 3

    for i in np.arange(0, OC.shape[0], 1):
        folder_net = kml.newfolder(name='cluster %04i' % i)

        idx = np.where(OC[i])[0]
        clat = lla[central_points[i], 0]
        clon = lla[central_points[i], 1]
        cstn = stations[central_points[i]]

        for lat, lon, stnm, tie in zip(lla[idx, 0], lla[idx, 1], stations[idx], OC.sum(axis=0)[idx]):
            pt = folder_net.newpoint(name=stnm, coords=[(lon, lat)])

            if tie > 1:
                pt.stylemap = styles_tie
            else:
                pt.stylemap = styles_stn

            line = folder_net.newlinestring(name="%s - %s" % (cstn, stnm), coords=[(lon, lat), (clon, clat)])
            line.style.linestyle.width = 2
            line.style.linestyle.color = simplekml.Color.white

    # DDG Jun 17 2025: the wrong version of simplekml was being used, now using latest
    # to fix the issue from simple kml
    # AttributeError: module 'cgi' has no attribute 'escape'
    # see: https://github.com/tjlang/simplekml/issues/38
    # import cgi
    # import html
    # cgi.escape = html.escape

    kml.savekmz('%s.kmz' % filename)


def main():

    parser = argparse.ArgumentParser(
        description='Script to estimate run times from clustering. Two algorithms are '
                    'available: qmeans and deterministic.'
    )

    parser.add_argument('stnlist', type=str, nargs='+', metavar='all|net.stnm',
                        help=station_list_help())

    parser.add_argument('-d', '--date_filter', nargs='+', metavar='date',
                        help='''Date range filter. Can be specified in
                            yyyy/mm/dd yyyy_doy  wwww-d format''')

    parser.add_argument('-yr', '--year', nargs=1, metavar='year', type=int,
                        help='''Instead of date range, provide year to filter, from the first to the last day.''')

    parser.add_argument('-tol', '--tolerance', nargs=1, type=int,
                        metavar='{max size}', default=[4],
                        help="The value of maximum network size. This is without overlap (tie) stations. "
                             "Only applies for deterministic clustering. Default value is 4.")

    parser.add_argument('-target', '--target_size', nargs=1, type=int,
                        metavar='{optimum size}', default=[25],
                        help="The value of optimum network size. This is without overlap (tie) stations. "
                             "Default value is 25.")

    parser.add_argument('-ties', '--tie_count', nargs=1, type=int,
                        metavar='{tie count}', default=[4],
                        help="The value for the overlap (tie) stations. "
                             "For deterministic clustering, ties are reciprocal, so total ties is double the value "
                             "set here. Default value is 4.")

    parser.add_argument('-cores', '--core_count', nargs=1, type=int,
                        metavar='{core count}', default=[1],
                        help="The number of cores to estimate clock time. Default is 1.")

    parser.add_argument('-kmz', '--kmz_days', nargs='*', metavar='argument', default=None,
                        help='List of days to output the clusters as kmz files')

    parser.add_argument('-det', '--deterministic', action='store_true', default=False,
                        help="Switch to deterministic clustering. Default is qmeans.")

    parser.add_argument('-plots', '--plots', action='store_true', default=False,
                        help="Produce plots with stats at the end of the run.")

    parser.add_argument('-json', '--export_json', nargs=1, type=str,
                        metavar='{filename}', default=None,
                        help="Export results (station count per cluster per day) in json format.")

    parser.add_argument('-tc', '--ties_classic', action='store_true', default=False,
                        help="Classic ties. Default is qmeans.")

    parser.add_argument('-vb', '--verbose', action='store_true', default=False,
                        help="Verbose mode.")

    add_version_argument(parser)

    args = parser.parse_args()

    cnn = dbConnection.Cnn("gnss_data.cfg")

    stnlist = process_stnlist(cnn, args.stnlist, print_summary=False)

    try:
        if args.date_filter:
            dates = process_date(args.date_filter)
        elif args.year:
            dates = [Date(year=args.year[0], doy=1), Date(year=args.year[0]+1, doy=1) - 1]
        else:
            parser.error('At least date range or year need to be provided.')
    except ValueError as e:
        parser.error(str(e))

    try:
        if args.kmz_days:
            plot_dates = list(process_date(args.kmz_days, 'no_fill'))
            if len(plot_dates) > len(args.kmz_days):
                plot_dates.pop()
        else:
            plot_dates = []
    except ValueError as e:
        parser.error(str(e))

    print(' -- Gathering active stations for the requested days...')

    rs = cnn.query_float('''
    SELECT auto_x, auto_y, auto_z, rp."ObservationYear", rp."ObservationDOY", 
        rp."NetworkCode" || '.' || rp."StationCode" as station, lat, lon
    FROM stations LEFT JOIN rinex_proc as rp USING 
        ("StationCode", "NetworkCode") 
    WHERE rp."NetworkCode" || '.' || rp."StationCode" IN (%s) 
        AND (rp."ObservationYear", rp."ObservationDOY") BETWEEN (%i, %i) AND (%i, %i)
    ORDER BY rp."ObservationYear", rp."ObservationDOY", rp."NetworkCode" || '.' || rp."StationCode"
    ''' % ('\'' + '\',\''.join([stationID(stn) for stn in stnlist]) + '\'',
           dates[0].year, dates[0].doy, dates[1].year, dates[1].doy), as_dict=True)

    mjds = np.arange(dates[0].mjd, dates[1].mjd + 1, 1)

    x = []  # day of year
    y = []  # stations per cluster
    t = []  # for run time
    clusters = []
    stations = []
    u_stations = []
    ties_count = []
    cluster_count = []

    for mjd in tqdm(mjds, ncols=120):
        date = Date(mjd=mjd)

        points = np.array([(stn['auto_x'], stn['auto_y'], stn['auto_z']) for stn in rs
                           if stn['ObservationYear'] == date.year and stn['ObservationDOY'] == date.doy])

        lla = np.array([(stn['lat'], stn['lon']) for stn in rs
                       if stn['ObservationYear'] == date.year and stn['ObservationDOY'] == date.doy])

        stnm = np.array([stn['station'] for stn in rs
                        if stn['ObservationYear'] == date.year and stn['ObservationDOY'] == date.doy])

        if points.shape[0] > 50:
            if not args.deterministic:
                qmean = BisectingQMeans(qmax=args.target_size[0], random_state=42)
                qmean.fit(points)
                # snap centroids to closest station coordinate
                central_points_ids = select_central_point(points, qmean.cluster_centers_)

                dc = DeterministicClustering(target_size=args.target_size[0], tolerance=args.tolerance[0],
                                             num_tie_points=args.tie_count[0])
                if args.ties_classic:
                    dc.points = points
                    dc.centroid_ids = central_points_ids
                    dc.add_tie_points(qmean.labels_, args.tie_count[0])
                    OC = dc.OC
                else:
                    #  expand the initial clusters to overlap stations with neighbors
                    OC = overcluster(qmean.labels_, points, metric='euclidean', overlap=args.tie_count[0], nmax=2)
                #  set 'method=None' to disable
                OC, central_points_ids = prune(OC, central_points_ids, method='minsize')

                cluster_ids = [[] for _ in np.arange(OC.shape[0])]
                tie_ids    = [[] for _ in np.arange(OC.shape[0])]
                ties        = np.where(np.sum(OC, axis=0) > 1)[0]
                for i in np.arange(OC.shape[0]):
                    for j in np.arange(OC.shape[1]):
                        if OC[i, j]:
                            cluster_ids[i].append(j)
                            if np.isin(j, ties):
                                tie_ids[i].append(j)
            else:
                dc = DeterministicClustering(target_size=args.target_size[0], tolerance=args.tolerance[0],
                                             num_tie_points=args.tie_count[0])
                dc.constrained_agglomerative(points)
                central_points_ids = dc.centroid_ids
                cluster_ids = dc.clustered_ids
                tie_ids = dc.tie_ids
                OC = dc.OC
        else:
            OC = np.ones((1, points.shape[0]), dtype=np.bool_)
            tie_ids = []
            central_points_ids = []
            cluster_ids = []

        cluster_sizes = OC.sum(axis=1)  # array of shape (num_clusters,)
        x.extend([int(mjd)] * len(cluster_sizes))  # repeat day for each cluster
        y.extend(cluster_sizes.tolist())  # add all cluster sizes for that day

        # compute the time that would take these clusters to run
        A = np.column_stack((np.ones_like(cluster_sizes), cluster_sizes, cluster_sizes**2))
        # from Grigsby et al 2025
        T = A @ [6.04370855910554, -0.102313555237178, 0.0206316702864554]
        t.append(T.sum())

        # save the number of clusters
        clusters.append(OC.shape[0])
        stations.append(np.sum(OC.flatten()))
        u_stations.append(OC.shape[1])

        tc = [len(t) for t in tie_ids]
        ties_count += tc
        cs = [i for i in cluster_sizes]
        cluster_count += cs

        if args.verbose:
            R = (int(np.sum(OC.flatten())) - OC.shape[1]) / OC.shape[1]

            tqdm.write(' -- %s C %3i - S: %4i - US: %4i - avg R: %5.3f - ties: [%2i %4.1f %2i] - T: %.1f'
                       % (date.yyyyddd(), OC.shape[0], int(np.sum(OC.flatten())), OC.shape[1], R,
                          np.min(tc), np.mean(tc, dtype=float), np.max(tc), T.sum()))

        # Plot each cluster with a different color
        if date in plot_dates and points.shape[0] > 50:
            generate_kmz(OC, lla, stnm, central_points_ids, date.yyyyddd().replace(' ', '_'))
            plot_geographic_cluster_graph(points[central_points_ids], cluster_ids, tie_ids, stnm, points)

    # estimate the runtime
    tqdm.write(' >> Total estimated time:')
    tqdm.write(' -- %s - %s processing time: %.1f hours'
               % (dates[0].yyyyddd(), dates[1].yyyyddd(), np.sum(np.array(t)) / 60.))
    tqdm.write(' -- Wall time using %i cores: %.1f hours' % (args.core_count[0],
                                                             np.sum(np.array(t))/args.core_count[0] / 60.))

    if args.export_json:
        file_write(args.export_json[0] + '.json',
                   json.dumps({'mjd': x, 'station_per_clusters': y}, indent=4, sort_keys=False))

    if args.plots:
        fig, axs = plt.subplots(nrows=2, ncols=2, figsize=(14, 8))

        counts, xedges, yedges, im = axs[0, 0].hist2d(x, y, bins=[np.arange(dates[0].mjd, dates[1].mjd + 2, 1),
                                                                  np.arange(5, 60)], cmap='viridis')
        cbar = fig.colorbar(im, ax=axs[0, 0])
        cbar.set_label('Cluster count')

        axs[0, 0].set_ylabel('Stations per cluster')
        axs[0, 0].set_title(r'(a) Cluster size per day', fontsize=14, fontweight='bold')

        # --- 2. Plot the runtime below ---
        # Assuming you have `runtime_per_day` and `day_indices`:
        axs[0, 1].plot(mjds, t, color='tab:red')
        axs[0, 1].grid(True, linestyle='--', linewidth=0.5, alpha=0.7)
        axs[0, 1].set_xlabel('Modified Julian Date')
        axs[0, 1].set_ylabel('Runtime (minutes)')
        axs[0, 1].set_title('(b) Total runtime per day', fontsize=14, fontweight='bold')

        axs[1, 0].plot(mjds, clusters, color='tab:red')
        axs[1, 0].grid(True, linestyle='--', linewidth=0.5, alpha=0.7)
        axs[1, 0].set_xlabel('Modified Julian Date')
        axs[1, 0].set_ylabel('Clusters')
        axs[1, 0].set_title('(c) Number of clusters', fontsize=14, fontweight='bold')

        axs[1, 1].plot(mjds, stations, color='tab:red')
        axs[1, 1].plot(mjds, u_stations, color='tab:blue')
        axs[1, 1].grid(True, linestyle='--', linewidth=0.5, alpha=0.7)
        axs[1, 1].set_xlabel('Modified Julian Date')
        axs[1, 1].set_ylabel('Stations')
        axs[1, 1].set_title('(d) Number of stations / unique', fontsize=14, fontweight='bold')

        # Layout
        plt.suptitle('Target size: %i ties: %i' % (args.target_size[0], args.tie_count[0]))
        plt.tight_layout()
        plt.show()

        fig, axs = plt.subplots(nrows=1, ncols=2, figsize=(14, 8))
        axs[1].hist(ties_count)
        axs[1].grid(True, linestyle='--', linewidth=0.5, alpha=0.7)
        axs[1].set_xlabel('Overlaps per cluster')
        axs[1].set_ylabel('Frequency')
        axs[1].set_title('Overlap histogram', fontsize=14, fontweight='bold')

        axs[0].hist(cluster_count)
        axs[0].grid(True, linestyle='--', linewidth=0.5, alpha=0.7)
        axs[0].set_xlabel('Stations per cluster')
        axs[0].set_ylabel('Frequency')
        axs[0].set_title('Cluster size histogram', fontsize=14, fontweight='bold')
        plt.tight_layout()
        plt.savefig('output_figure.png', dpi=150, bbox_inches='tight')
        # plot_global_network(central_points_ids, OC, np.arange(0, OC.shape[0] - 1, 1), points, './map.png')


if __name__ == '__main__':
    main()
