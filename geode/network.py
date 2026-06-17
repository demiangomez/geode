"""
Project: Geodesy Database Engine (GeoDE)
Date: Mar-31-2017
Author: Demian D. Gomez

pom170921: Added new global subnet methods.
pom210921:
- Added comments
- Changed the brackets around the dist variable, instead of checking the
  truth of dist it was checking that the list contained a value instead.
  Should fix far away stations from being added.
- Added a distance check to the tie station assignment. Only stations
  within 1 Earth radius linear distance will be added to the ties.
- When checking for stations that weren’t assigned to a subnet, I mistakenly
  included the backbone stations which caused duplicate entries in some
  subnets.
pom220921:
- Changed 'if not iterate or len(points) <= BACKBONE_NET:' to
  'if not iterate or len(points[n_mask]) <= BACKBONE_NET:'
  so only the masked stations are counted when comparing against the desired
  number of backbone stations.
- Added the geocenter to the backbone delaunay triangulation.
pom011021:
- Set a minimum number of subnets, the program will reduce the minimum number
  of stations per subnet until the minimum number of subnets is created. All
  changes are in the subnets_delaunay routine.
"""

from datetime import datetime
import time
import copy
# deps
from tqdm import tqdm 
import numpy as np
from scipy.spatial import Delaunay, distance

# app
from .gamit.gamit_session import GamitSession
from .gamit.station import StationCollection
from .clustering import (BisectingQMeans, overcluster, prune,
                         select_central_point, plot_global_network)

BACKBONE_NET = 45
NET_LIMIT = 40
SUBNET_LIMIT = 35
MAX_DIST = 5000
MIN_DIST = 20
MIN_STNS_PER_SUBNET = 1


def tic():
    global tt
    tt = time.time()


def toc(text):
    global tt
    tqdm.write(text + ': ' + str(time.time() - tt))


class NetworkException(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return str(self.value)


class Network(object):

    def __init__(self, cnn, archive, GamitConfig, stations, date,
                 check_stations=None, ignore_missing=False):

        self.name = GamitConfig.NetworkConfig.network_id.lower()
        self.org = GamitConfig.gamitopt['org']
        self.GamitConfig = GamitConfig
        self.date = date
        self.cluster_size = int(self.GamitConfig.NetworkConfig['cluster_size'])
        self.ties = int(self.GamitConfig.NetworkConfig['ties'])

        # find out if this project-day has been processed before
        db_subnets = cnn.query_float('SELECT * FROM gamit_subnets '
                                     'WHERE "Project" = \'%s\' AND "Year" = %i AND '
                                     '"DOY" = %i ORDER BY "subnet"'
                                     % (self.name, date.year, date.doy), as_dict=True)

        stn_active = stations.get_active_stations(date)
        chk_active = check_stations.get_active_stations(date)

        if len(db_subnets) > 0:
            tqdm.write(' >> %s %s %s -> Processing already exists' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                          self.name, date.yyyyddd()))

            # sub-network already exist, put information in lists
            dba_stn = [stn for net in db_subnets for stn in net['stations']]
            # DDG: deprecated, aliases are now fixed and kept constant
            # dba_alias = [alias for net in db_subnets for alias in net['alias']]

            # DDG: deprecated, aliases are now fixed and kept constant
            # make the necessary changes to the stations aliases (so they
            # match those in the database)
            # stations.replace_alias(dba_stn, dba_alias)

            # build the sub-networks using the information in the database
            clusters, backbone, ties = self.recover_subnets(db_subnets,
                                                            stn_active)

            if check_stations:
                for stn in chk_active:
                    if stn.netstn not in dba_stn:
                        # add station to StationCollection to make sure there
                        # is no name collisions
                        stations.append(stn)
                        # determine the closest sub-network (cluster) to this
                        # station and add it
                        clusters = self.add_missing_station(cnn, clusters, stn)

            # find if there are any incomplete sub-networks or stations
            # without solution
            for subnet in db_subnets:
                stat = cnn.query_float('SELECT * FROM gamit_stats WHERE "Project" = \'%s\' AND "Year" = %i AND '
                                       '"DOY" = %i AND "subnet" = %i'
                                       % (self.name, date.year, date.doy,
                                          subnet['subnet']), as_dict=True)

                if not len(stat):
                    # sub-network didn't finish properly, GamitSession will
                    # detect this condition and flag session for reprocessing.
                    # Generate message alerting the user
                    # DDG: support up to 999 subnetworks
                    tqdm.write(' -- Sub-network %s%03i did not finish successfully and will be reprocessed'
                               % (self.org, subnet['subnet']))
                else:
                    # loop through the stations in this sub-network and find
                    # it in the StationsCollection. If exists and there is no
                    # gamit_soln, trigger reprocessing
                    for stn in subnet['stations']:
                        # logic here is that, if the station in the database
                        # is still in the list to be processed and
                        # ignore_missing is turned off OR station is part of
                        # the check stations, then verify the solution is in
                        # the database. Otherwise, skip the station
                        if stn in stn_active and \
                           (not ignore_missing or stn in chk_active) and \
                           not stations[stn].check_gamit_soln(cnn,
                                                              self.name,
                                                              date):
                            # stations is in the database but there was no
                            # solution for this day, rerun

                            for table in ('gamit_stats', 'gamit_subnets'):
                                cnn.query('DELETE FROM %s WHERE '
                                          '"Project" = \'%s\' AND '
                                          '"Year"    = %i AND '
                                          '"DOY"     = %i AND '
                                          'subnet    = %i' %
                                          (table, self.name, self.date.year, self.date.doy, subnet['subnet']))

                            # DDG: support up to 999 subnetworks
                            tqdm.write(' -- %s in sub-network %s%03i did not produce a solution and will be '
                            'reprocessed' % (stn, self.org, subnet['subnet']))
        else:
            tqdm.write(' >> %s %s %s -> Creating network clusters' %
                       (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        self.name, date.yyyyddd()))
            tqdm.write(' --  Processing type is %s with %i active stations'
                       % (GamitConfig.NetworkConfig['type'], len(stn_active)))

            if len(stn_active) > BACKBONE_NET + 5:
                backbone = self.backbone_delauney(stations.get_active_coordinates(date), stn_active)
                clusters, ties = self.make_clusters(stations.get_active_coordinates(date), stn_active)
            else:
                # no need to create a set of clusters, just use them all
                clusters = {'stations': [stn_active]}
                backbone = []
                ties = []

        self.sessions = self.create_gamit_sessions(cnn, archive, clusters,
                                                   backbone, ties, date)

    def make_clusters(self, points, stations, net_limit=NET_LIMIT):
        # Run initial clustering using bisecting 'q-means'
        qmean = BisectingQMeans(qmax=self.cluster_size, random_state=42)
        qmean.fit(points)
        # snap centroids to closest station coordinate
        central_points = select_central_point(points, qmean.cluster_centers_)
        # expand the initial clusters to overlap stations with neighbors
        OC = overcluster(qmean.labels_, points, metric='euclidean',
                         overlap=self.ties, nmax=2)
        # set 'method=None' to disable
        OC, central_points = prune(OC, central_points, method='minsize')
        # calculate all 'tie' stations
        ties = np.where(np.sum(OC, axis=0) > 1)[0]

        # monotonic labels, compatible with previous data structure / api
        cluster_labels = []
        station_labels = []
        cluster_ties = []

        # init'ed outside of the loop for efficiency...
        stat_labs = stations.labels_array()
        for row, cluster in enumerate(OC):
            # Create 'station collections' for compat
            my_stations = StationCollection()
            my_cluster_ties = StationCollection()
            # strip out station id's per cluster...
            for station in stat_labs[cluster]:
                # rebuild as a 'station collection list'
                my_stations.append(stations[str(station)])
            # append to a regular list for integer indexing at line ~400
            station_labels.append(my_stations)
            cluster_labels.append(np.ones((1, np.sum(cluster)),
                                           dtype=np.int_).squeeze()*row)
            # strip out station id's for tie points....
            for statn in stat_labs[ties[np.isin(ties, np.where(cluster)[0])]]:
                # rebuild as a 'station collection list'
                my_cluster_ties.append(stations[str(statn)])
            # append to a regular list for integer indexing at line ~400
            cluster_ties.append(my_cluster_ties)

        # define output path for plot
        solution_base = self.GamitConfig.gamitopt['solutions_dir'].rstrip('/')
        end_path = '/%s/%s/%s' % (self.date.yyyy(), self.date.ddd(), self.name)
        path = solution_base + end_path + '_cluster.png'

        # generate plot of the network segmentation
        # central_points = plot_global_network(central_points, OC, qmean.labels_,
        #                                     points, output_path=path)

        # put everything in a dictionary
        clusters = {'centroids': points[central_points],
                    'labels': cluster_labels,
                    'stations': station_labels}

        return clusters, cluster_ties

    @staticmethod
    def backbone_delauney(points, stations):
        stations2 = [stn for stn in stations]
        geocenter = copy.deepcopy(stations2[0])
        geocenter.NetworkCode = 'nan'
        geocenter.StationCode = 'geoc'
        stations2.append(geocenter)
        points = np.vstack((points, np.zeros((1, 3))))
        dt = Delaunay(points)

        # start distance to remove stations
        max_dist = 5

        # create a mask with all the stations
        mask = np.ones(points.shape[0], dtype=bool)

        while len(points[mask]) > BACKBONE_NET:
            # make a copy of the mask
            n_mask = mask.copy()

            while True:
                # create variable to check if should iterate
                iterate = False

                # loop through each simplex
                for v in dt.simplices:
                    # get the distance of each edge
                    d = distance.cdist(points[mask][v], points[mask][v]) / 1e3
                    # make the zeros infinite
                    d[d == 0] = np.inf
                    # if any pair is closer than max_dist, remove point
                    if np.any(d <= max_dist):
                        rp = v[np.where((d <= max_dist))[0]][0]

                        n_mask[np.where(mask)[0][rp]] = False
                        # if mask was updated, then iterate
                        iterate = True

                        if len(points[n_mask]) <= BACKBONE_NET:
                            break

                mask = n_mask.copy()
                dt = Delaunay(points[mask])

                if not iterate or len(points[mask]) <= BACKBONE_NET:
                    break

            # make the distance double from the last run
            max_dist = max_dist * 2

        backbone = [s for i, s in enumerate(stations) if mask[i]]
        if geocenter in backbone:
            backbone.pop(geocenter)
        return backbone

    @staticmethod
    def recover_subnets(db_subnets, stations):
        # this method does not update the labels because they are not used
        # labels are only used to tie station
        clusters = {'centroids': [],
                    'labels': [],
                    'stations': []}

        backbone = []
        ties = []

        # check if there is more than one sub-network
        if len(db_subnets) == 1:
            # single network reported as sub-network zero
            # no backbone and no ties, single network processing
            clusters = {'centroids': np.array([db_subnets[0]['centroid']]),
                        'labels': np.zeros(len(stations)),
                        'stations': [stations]
                        }
        else:
            # multiple sub-networks: 0 contains the backbone;
            # 1 contains cluster 1; 2 contains...
            for subnet in db_subnets[1:]:
                clusters['centroids'].append(subnet['centroid'])
                # labels start at zero, but zero subnet is backbone
                # clusters['labels'] += np.ones(len(subnet['stations'])) * (subnet['subnet'] - 1)
                # DDG: clusters['stations'] should not have the ties!
                # This is because ties are merged to each sub-network in
                # GamitSession
                clusters['stations'].append([stations[stn] for stn in
                                             subnet['stations'] if stn not in
                                             subnet['ties']])
                # add the corresponding ties
                ties.append([stations[stn] for stn in subnet['ties']])

            clusters['centroids'] = np.array(clusters['centroids'])

            # now recover the backbone
            backbone = [stations[stn] for stn in db_subnets[0]['stations']]

        return clusters, backbone, ties

    def add_missing_station(self, cnn, clusters, add_station):

        # this method does not update the labels because they are not used
        # labels are only used to tie station

        if len(clusters['centroids']) == 1:
            # single station network, just add the missing station
            clusters['stations'][0].append(add_station)
            clusters['labels'] = np.zeros(len(clusters['stations'][0]))

            # because a station was added, delete the gamit_stats record to
            # force reprocessing
            for table in ('gamit_stats', 'gamit_subnets'):
                # DDG: change to query statement to delete all systems (GNSS support)
                cnn.query(f'DELETE from {table} WHERE "Project"=\'{self.name}\' AND "Year"={self.date.year} AND '
                          f'"DOY"={self.date.doy} AND subnet=0')
                # cnn.delete(table, Project=self.name, Year=self.date.year, DOY=self.date.doy, subnet=0)

            tqdm.write(' -- %s was not originally in the processing, will be added to network %s'
                       % (add_station.netstn, self.org))
        else:
            # find the closest centroid to this station
            xyz = np.zeros((1, 3))
            xyz[0] = np.array([add_station.X, add_station.Y, add_station.Z])
            # find distances between stations and clusters
            dist = distance.cdist(xyz, clusters['centroids']) / 1e3
            # sort distances
            min_i = np.argsort(dist)[0][0]

            # can add the station to this sub-network
            clusters['stations'][min_i].append(add_station)
            # because a station was added, delete the gamit_stats and subnets
            # record to force reprocessing
            for table in ('gamit_stats', 'gamit_subnets'):
                # DDG: change to query statement to delete all systems (GNSS support)
                cnn.query(f'DELETE from {table} WHERE "Project"=\'{self.name}\' AND "Year"={self.date.year} AND '
                          f'"DOY"={self.date.doy} AND subnet={min_i + 1}')
                # cnn.delete(table, Project=self.name, Year=self.date.year, DOY=self.date.doy, subnet=min_i + 1)

            tqdm.write(' -- %s was not originally in the processing, will be added to sub-network %s%03i'
                       % (add_station.netstn, self.org, min_i + 1))
        return clusters

    def create_gamit_sessions(self, cnn, archive, clusters,
                              backbone, ties, date):

        sessions = []

        if len(backbone):
            # a backbone network was created: at least two or more clusters
            # backbone if always network 00
            sessions.append(GamitSession(cnn, archive, self.name, self.org,
                                         0, date, self.GamitConfig, backbone))

            for c in range(len(clusters['centroids'])):
                # create a session for each cluster
                sessions.append(GamitSession(cnn, archive, self.name,
                                             self.org, c + 1, date,
                                             self.GamitConfig,
                                             clusters['stations'][c], ties[c],
                                             clusters['centroids'][c].tolist()))

        else:
            sessions.append(GamitSession(cnn, archive, self.name, self.org,
                                         None, date, self.GamitConfig,
                                         clusters['stations'][0]))

        return sessions
