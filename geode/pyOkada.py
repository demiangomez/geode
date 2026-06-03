"""
Project: Geodesy Database Engine (GeoDE)
Date: 5/15/24 9:08 AM 
Author: Demian D. Gomez

Seismic-score (s-score) class that allows testing if a latitude longitude locations requires co-seismic
displacement parameters on its ETM. The class includes the formulations from Wells and Coppersmith (1994) to
estimate the fault dimensions of the event and calculate the level-2 s-score. If the nodal planes of the event
are not available, then the class returns the level-1 (isotropic) s-score.

##########################################################################################
Okada code Translated from Matlab to Python. Original by Michael Bevis based on Okada 1985
##########################################################################################

OKADA surface displacements due to dislocation in an elastic half-space This is a matlab implementation of Okada's
fortran code SRECTF except that only surface displacements are computed, not strains or tilts. See document
OkadaCoordSystem.pdf by M. Bevis for graphics describing of Okada's (1985) coordinate systems and sign conventions

USAGE:    [ux,uy,uz] = okada(alpha,x,y,d,L1,L2,W1,W2,snd,csd,B1,B2,B3)

A rectangular dislocation is located in an elastic halfspace. The right-handed coordinate system {X,Y,Z} has Z positive
upwards, and the X and Y axes lie on the surface of the halfspace. The upper and lower edges of the rectangle are
horizontal and parallel to the X axis, which constitutes the 'strike direction'. The rectangular dislocation is located
within a dipping plane which intersects the Z axis at -d (so d is a positive number coinciding with 'depth'). The dip
of this plane is the angle delta which is measured positive anticlockwise from horizontal looking in the -X direction,
as seen in Figure 1 of Okada's 1985 BSSA paper. The user must specify sd = sin(delta) and cd = cos(delta) rather than
delta itself, since this convention allows a general treatment of the vertical fault (switching sd between +1 and -1
when cd = 0, changes which side of the fault is going up). The position of the dislocation within the dipping plane is
specified using a cartesian axis system {L,W} confined to the plane of the dislocation, with the L axis being parallel
to and located beneath the X axis. The origin of the {L,W} system is at X=Y=0, Z=-d. The actual dislocation is located
in the rectangle L1 <= L <= L2, W1 <= W <= W2.

With reference to Okada (1985) Figure 2, the fault outlined with a solid line has L1=0,L2=L, W1=0, W2=W. To specify the
extended fault shown with the dashed line, change L1 to -L.

The Burgers vector B for the dislocation has three components B1,B2 and B3 where B1 is the L component, B2 is the W
component, and B3 is the component normal to the dislocation surface.

The elastic half-space (Z<0) is uniform and isotropic. The displacement field produced by the dislocation depends only
on scalar alpha, where alpha = mu/(lambda+mu). When the Lamé parameters lambda and mu are equal, the elastic is said to
be a Poisson solid and alpha=0.5

The stations where displacements are to be computed have coordinates x and y (which can be scalars, vectors or
matrices, but must have the same sizes. THe output arguments ux, uy and uz, which have the sames sizes as input
arguments x and y, are the X, Y and Z components of displacement at each station.

Okada.m uses internal matlab function okadakernel.m to peform the indefinite integrals.

version 1.0               Michael Bevis                  4 Nov 99
version 1.1                                             26 Feb 04
version 1.2  (change argument names, new header)        11 Oct 13
version 1.3  (changed sd,cd too)                         3 Apr 14
version 1.0  (PYTHON) translated by Demian Gomez        15 May 24

"""
import numpy as np
import math

from scipy.spatial     import KDTree
from datetime          import timedelta
import simplekml
import io
import zipfile
import base64
from skimage.measure import find_contours
from shapely.geometry import Polygon
from obspy.imaging.beachball import beachball

from .pyDate import Date
from . import pyETM as etm
from .pyETM import CO_SEISMIC_JUMP, CO_SEISMIC_JUMP_DECAY
from . import dbConnection

cosd  = lambda x : np.cos(np.deg2rad(x))
sind  = lambda x : np.sin(np.deg2rad(x))
acosd = lambda x : np.rad2deg(np.arccos(x))
asind = lambda x : np.rad2deg(np.arcsin(x))
atand = lambda x : np.rad2deg(np.arctan(x))

# from Gómez et al 2024
a = 0.5261
b = -1.1478

POST_SEISMIC_SCALE_FACTOR = 1.5


def azimuth(lon1, lat1, lon2, lat2):
    """
    Calculates the initial bearing (azimuth) in degrees between two points
    on the Earth's surface (latitude and longitude).
    """
    # convert decimal degrees to radians
    lon1 = lon1 * np.pi / 180
    lat1 = lat1 * np.pi / 180
    lon2 = lon2 * np.pi / 180
    lat2 = lat2 * np.pi / 180

    delta_lon = lon2 - lon1

    y = np.sin(delta_lon) * np.cos(lat2)
    x = np.cos(lat1) * np.sin(lat2) - \
        np.sin(lat1) * np.cos(lat2) * np.cos(delta_lon)

    az_rad = np.arctan2(y, x)
    az_deg = np.rad2deg(az_rad)

    # Normalize to 0-360 degrees
    return (az_deg + 360) % 360

def distance(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees)
    """

    # convert decimal degrees to radians
    lon1 = lon1 * np.pi / 180
    lat1 = lat1 * np.pi / 180
    lon2 = lon2 * np.pi / 180
    lat2 = lat2 * np.pi / 180
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    d = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(d))
    km = 6371 * c
    return km


def inv_azimuthal(x, y, lon, lat):
    # inverse azimuthal equidistant
    r = np.sqrt(np.square(x) + np.square(y)).flatten()
    c = r / 6371.

    i_lat = asind(np.cos(c) * sind(lat) + y.flatten() * np.sin(c) * cosd(lat) / r)
    i_lon = lon + atand((x.flatten() * np.sin(c)) / (r * cosd(lat) * np.cos(c) - y.flatten() * sind(lat) * np.sin(c)))

    return i_lon, i_lat


class EarthquakeTable(object):
    """
    Given a connection to the database and an earthquake id, find all stations affected by the given event
    """
    def __init__(self, cnn, earthquake_id, include_postseismic=True):
        self.earthquake_id = earthquake_id
        self.c_stations = []
        self.p_stations = []
        self.cnn = cnn

        # get the earthquakes based on Mike's expression
        # earthquakes before the start data: only magnitude 7+
        eq   = cnn.get('earthquakes', {'id': earthquake_id})
        stns = cnn.query_float('SELECT "NetworkCode", "StationCode", lat, lon, height, auto_x, auto_y, auto_z '
                               'FROM stations WHERE "NetworkCode" NOT LIKE \'?%\' AND '
                               'lat IS NOT NULL AND lon IS NOT NULL', as_dict=True)

        strike = [float(eq['strike1']), float(eq['strike2'])] if not math.isnan(eq['strike1']) else []
        dip    = [float(eq['dip1']), float(eq['dip2'])]       if not math.isnan(eq['strike1']) else []
        rake   = [float(eq['rake1']), float(eq['rake2'])]     if not math.isnan(eq['strike1']) else []

        score = Score(float(eq['lat']), float(eq['lon']), float(eq['depth']), float(eq['mag']),
                      strike, dip, rake, eq['date'], location=eq['location'], event_id=eq['id'])

        for stn in stns:

            dist = distance(stn['lon'], stn['lat'], eq['lon'], eq['lat'])
            # Obtain level-1 s-score to make the process faster: do not use events outside of level-1 s-score
            # inflate the score to also include postseismic events if include_postseismic=True
            sp = a * eq['mag'] - np.log10(dist) + b + np.log10(POST_SEISMIC_SCALE_FACTOR)
            sc = a * eq['mag'] - np.log10(dist) + b

            # prepare a dictionary for this station
            stn_dict = dict(stn)
            stn_dict['distance'] = dist
            stn_dict['azimuth']  = azimuth(stn['lon'], stn['lat'], eq['lon'], eq['lat'])

            if (sc > 0 or sp > 0) and rake:
                # check the actual score if rake, otherwise no need to check
                sc, sp = score.score(stn['lat'], stn['lon'])
                if sc > 0:
                    self.c_stations.append(stn_dict)
                elif sp > 0 and include_postseismic:
                    self.p_stations.append(stn_dict)
            elif sc > 0:
                # append the station because no L2 s-score
                self.c_stations.append(stn_dict)
            elif sp > 0 and include_postseismic:
                self.p_stations.append(stn_dict)

        self.c_stations = sorted(self.c_stations, key=lambda x: x['StationCode'])
        self.p_stations = sorted(self.p_stations, key=lambda x: x['StationCode'])

    def get_coseismic_displacements(self, stack='ppp'):
        # find co-seismic only and co+post-seismic
        etms = self.cnn.query_float(f"SELECT \"NetworkCode\", \"StationCode\", params, relaxation, "
                                    f"ARRAY_LENGTH(relaxation,1) as len, jump_type FROM etms "
                                    f"WHERE jump_type in ({CO_SEISMIC_JUMP_DECAY}, {CO_SEISMIC_JUMP}) "
                                    f"AND metadata like '%{self.earthquake_id}%' and stack = '{stack}'", as_dict=True)

        displacements = []

        for etm in etms:
            if etm['params'] is None:
                continue

            if isinstance(etm['params'][0], list):
                etm['params'] = [item for sublist in etm['params'] for item in sublist]

            if etm['jump_type'] == CO_SEISMIC_JUMP:
                displacements.append({"NetworkCode": etm['NetworkCode'],
                                      "StationCode": etm['StationCode'],
                                      "n": etm['params'][0],
                                      "e": etm['params'][1],
                                      "u": etm['params'][2]})
            else:
                displacements.append({"NetworkCode": etm['NetworkCode'],
                                      "StationCode": etm['StationCode'],
                                      "n": etm['params'][0],
                                      "e": etm['params'][1 + etm['len']],
                                      "u": etm['params'][2 + 2 * etm['len']]})

        return sorted(displacements, key=lambda x: x['StationCode'])


class ScoreTable(object):
    """
    Given a connection to the database, lat and lon of point of interest, and date range, find all the seismic events
    with level-2 s-score = 1. If no strike, dip, and rake parameters available, return events with level-1 s-score > 0
    Returns a list with [mag, date, lon, lat] ordered by ascending date and descending magnitude.
    """
    def __init__(self, cnn, lat, lon, sdate, edate):
        self.table = []

        # get the earthquakes based on Mike's expression
        # earthquakes before the start data: only magnitude 7+
        jumps = cnn.query_float('SELECT * FROM earthquakes '
                                'WHERE date BETWEEN \'%s\' AND \'%s\' UNION '
                                'SELECT * FROM earthquakes '
                                'WHERE date BETWEEN \'%s\' AND \'%s\' AND mag >= 7 '
                                'ORDER BY date ASC, mag DESC'
                                % (sdate.yyyymmdd(), edate.yyyymmdd(),
                                   (sdate.datetime() - timedelta(days=5*365)), sdate.yyyymmdd()), as_dict=True)

        for j in jumps:
            strike = [float(j['strike1']), float(j['strike2'])] if not math.isnan(j['strike1']) else []
            dip    = [float(j['dip1']), float(j['dip2'])]       if not math.isnan(j['strike1']) else []
            rake   = [float(j['rake1']), float(j['rake2'])]     if not math.isnan(j['strike1']) else []

            dist = distance(lon, lat, j['lon'], j['lat'])
            # Obtain level-1 s-score to make the process faster: do not use events outside of level-1 s-score
            # inflate the score to also include postseismic events
            s = a * j['mag'] - np.log10(dist) + b + np.log10(POST_SEISMIC_SCALE_FACTOR)

            if s > 0:

                score = Score(float(j['lat']), float(j['lon']), float(j['depth']), float(j['mag']),
                              strike, dip, rake, j['date'], location=j['location'], event_id=j['id'])

                # capture co-seismic and post-seismic scores
                s_score, p_score = score.score(lat, lon)
                # print(j['date'], s, s_score, p_score, dist)
                link = ('<a href="https://earthquake.usgs.gov/earthquakes/eventpage/%s" target="_blank">%s</a>'
                        % (j['id'], j['id']))
                if s_score > 0:
                    # seismic score came back > 0, add jump
                    self.table.append([j['mag'], Date(datetime=j['date']), j['lon'], j['lat'],
                                       etm.CO_SEISMIC_JUMP_DECAY,
                                       link + ': M%.1f' % j['mag'] + ' ' + j['location'] + ' -> %.0f km' % dist])
                elif p_score > 0:
                    # seismic score came back == 0, but post-seismic score > 0 add jump
                    self.table.append([j['mag'], Date(datetime=j['date']), j['lon'], j['lat'],
                                       etm.CO_SEISMIC_DECAY,
                                       link + ': M%.1f' % j['mag'] + ' ' + j['location'] + ' -> %.0f km' % dist])


class Score(object):
    def __init__(self, event_lat, event_lon, depth_km, magnitude, strike=(), dip=(), rake=(), event_date=None,
                 density=250, location='', event_id='Unknown'):
        """
        Seismic-score (s-score) class that allows testing if a latitude longitude locations requires co-seismic
        displacement parameters on its ETM. The class includes the formulations from Wells and Coppersmith (1994) to
        estimate the fault dimensions of the event and calculate the level-2 s-score. If the nodal planes of the event
        are not available, then the class returns the level-1 (isotropic) s-score. Input data is as follows:
        event_lat: latitude (in decimal degrees) of the event's hypocenter.
        event_lon: longitude (in decimal degrees) of the event's hypocenter.
        depth_km : depth of the hypocenter, expressed in km.
        magnitude: moment-magnitude of the event.
        strike   : one or two value vector with the nodal planes of the event. Can be empty in which case the clss will
                   return the level-1 (isotropic) s-score.
        dip      : one or two value vector with the nodal planes of the event. Can be empty in which case the clss will
                   return the level-1 (isotropic) s-score.
        rake     : one or two value vector with the nodal planes of the event. Can be empty in which case the clss will
                   return the level-1 (isotropic) s-score.
        """
        self.lat    = float(event_lat)
        self.lon    = float(event_lon)
        self.depth  = [0, float(depth_km)*1000]
        self.mag    = float(magnitude)
        self.strike = strike if type(strike) in (list, tuple) else [strike]
        self.dip    = dip    if type(dip)    in (list, tuple) else [dip]
        self.rake   = rake   if type(rake)   in (list, tuple) else [rake]
        # compute dmax based on parameters
        self.dmax   = 10. ** (a * self.mag + b)
        self.date   = event_date
        # for the kml information
        self.location = location
        self.event_id = event_id

        # init variables
        self.along_strike_l = None
        self.downdip_l      = None
        self.avg_disp       = None
        self.rupture_area   = None
        self.maximum_disp   = None

        # compute fault dimensions from Wells and Coppersmith 1994
        # all lengths and displacements reported in m
        self.fault_dims()

        # to compute okada
        self.c_mx = np.array([])
        self.c_my = np.array([])
        self.c_mask = np.array([])

        self.p_mx = np.array([])
        self.p_my = np.array([])
        self.p_mask = np.array([])

        far_field_scale = 25
        xmax = np.ceil(self.along_strike_l) * far_field_scale
        self.gx, self.gy = np.meshgrid(np.linspace(-xmax, xmax, density), np.linspace(-xmax, xmax, density))

        if len(self.strike):
            self.c_mx, self.c_my, self.c_mask = self.compute_disp_field()
            self.p_mx, self.p_my, self.p_mask = self.compute_disp_field(POST_SEISMIC_SCALE_FACTOR)
        else:
            # if not strike information, produce a mask using the L1 S-score only
            self.c_mask = np.sqrt(np.square(self.gx) + np.square(self.gy)) < (self.dmax * 1000.)
            self.p_mask = np.sqrt(np.square(self.gx) + np.square(self.gy)) < (POST_SEISMIC_SCALE_FACTOR * (self.dmax * 1000.))

            self.c_mx = self.gx / 1000.
            self.c_my = self.gy / 1000.
            self.p_mx = self.gx / 1000.
            self.p_my = self.gy / 1000.

        # save the interpolator to make the score response faster
        self.kd_c = KDTree(np.column_stack((self.c_mx.flatten(), self.c_my.flatten())))
        self.kd_p = KDTree(np.column_stack((self.p_mx.flatten(), self.p_my.flatten())))

    def fault_dims(self):
        # compute fault dimensions from Wells and Coppersmith 1994
        # all lengths and displacements reported in m
        self.along_strike_l = 10. ** (-3.22 + 0.69 * self.mag) * 1000  # [m]
        self.downdip_l      = 10. ** (-1.01 + 0.32 * self.mag) * 1000  # [m]
        self.avg_disp       = 10. ** (-4.80 + 0.69 * self.mag)         # [m]
        self.rupture_area   = 10. ** (-3.49 + 0.91 * self.mag)         # [km ** 2]
        self.maximum_disp   = 10. ** (-5.46 + 0.82 * self.mag)         # [m]

    def compute_disp_field(self, scale_factor=1., limit=1e-3):
        # source dimensions L is horizontal, and W is depth
        L1 = -self.along_strike_l / 2
        L2 =  -L1
        W1 = -self.downdip_l / 2
        W2 = -W1
        ad = self.avg_disp

        ref_scale = []
        U = np.zeros_like(self.gx, dtype=bool)

        for depth in self.depth:
            # no need to save the mask for the zero depth, since it is only for the reference scale
            U = np.zeros_like(self.gx, dtype=bool)

            for strike, dip, rake in zip(self.strike, self.dip, self.rake):
                # check depth of fault edge (add 500 meters for security factor)
                d2 = depth - (W2 * sind(dip) + 500)

                if d2 < 0:
                    # fault is sticking out of the ground! reduce depth
                    depth = depth - d2

                # clockwise rotation
                R = np.array([[cosd(90 - strike), sind(90 - strike)],
                             [-sind(90 - strike), cosd(90 - strike)]])

                # compute the transformed station coordinates
                T = R @ np.array([self.gx.flatten(), self.gy.flatten()])
                tx = T[0, :]
                ty = T[1, :]
                n, e, u = okada(0.5, tx, ty, depth, L1, L2, W1, W2,
                                sind(dip), cosd(dip), ad*cosd(rake), ad*sind(rake), 0)

                n = np.reshape(n, self.gx.shape)
                e = np.reshape(e, self.gx.shape)
                u = np.reshape(u, self.gx.shape)

                # create the mask
                U = np.logical_or((np.sqrt(np.square(n) + np.square(e) + np.square(u)) >= limit), U)
            # print(U)
            # print(np.max(np.sqrt(np.square(self.gx[U]) + np.square(self.gy[U]))))
            # compute the deformation field scale
            try:
                ref_scale.append(np.max(np.sqrt(np.square(self.gx[U]) + np.square(self.gy[U]))))
            except ValueError:
                # no True values in the mask
                ref_scale.append(0)

        mx = self.gx / np.max(ref_scale) * self.dmax * scale_factor
        my = self.gy / np.max(ref_scale) * self.dmax * scale_factor
        # print(ref_scale)
        return mx, my, U

    def score(self, lat, lon):
        # determine if lat lon within the mask, or determine score for station
        # convert lat lon to mask coordinates
        c = np.arccos(sind(self.lat) * sind(lat) + cosd(self.lat) * cosd(lat) * cosd(lon - self.lon))
        k = np.zeros_like(c)
        k[c != 0] = c[c != 0] / np.sin(c[c != 0]) * 6371
        x = k * cosd(lat) * sind(lon - self.lon)
        y = k * (cosd(self.lat) * sind(lat) - sind(self.lat) * cosd(lat) * cosd(lon - self.lon))

        qp = np.column_stack((x, y))

        if self.c_mask.size > 0:
            # if mask is available, use mask
            _, i = self.kd_c.query(qp)
            s_score = self.c_mask.flatten()[i] + 0

            # repeat, this time inflating the level-2 mask to get the postseismic
            _, i = self.kd_p.query(qp)
            p_score = self.p_mask.flatten()[i] + 0
        else:
            s_score = a * self.mag - np.log10(np.sqrt(np.square(x) + np.square(y))) + b
            p_score = 0

        return s_score, p_score

    def save_masks(self, txt_file=None, kmz_file=None, include_postseismic=False):
        """
        Exports coseismic mask as a KMZ file with proper polygon separation.
        """
        # DDG Jun 17 2025: the wrong version of simplekml was being used, now using latest
        # import cgi
        # import html
        # cgi.escape = html.escape

        # First, transform the scalar field coordinates using inv_azimuthal
        c_lon, c_lat = inv_azimuthal(self.c_mx, self.c_my, self.lon, self.lat)
        p_lon, p_lat = inv_azimuthal(self.p_mx, self.p_my, self.lon, self.lat)

        # Reshape to match mask shape
        c_lon = np.reshape(c_lon, self.c_mask.shape)
        c_lat = np.reshape(c_lat, self.c_mask.shape)
        p_lon = np.reshape(p_lon, self.p_mask.shape)
        p_lat = np.reshape(p_lat, self.p_mask.shape)

        # Find contours on transformed scalar field coordinates
        coseismic_contours = find_contours(self.c_mask, 1e-16)
        postseismic_contours = find_contours(self.p_mask, 1e-16) if include_postseismic else []

        # Create KML
        kml = simplekml.Kml()

        epicenter = kml.newpoint(name=self.location, coords=[(self.lon, self.lat)])
        epicenter.style.iconstyle.icon.href = 'https://maps.google.com/mapfiles/kml/shapes/star.png'
        epicenter.style.iconstyle.scale = 1.5
        epicenter.style.iconstyle.color = simplekml.Color.yellow
        epicenter.style.labelstyle.scale = 0

        if self.strike:
            img_buffer = io.BytesIO()
            beachball([self.strike[0], self.dip[0], self.rake[0]], width=100, linewidth=1,
                      facecolor='k', outfile=img_buffer)
            img_buffer.seek(0)
            img_base64 = base64.b64encode(img_buffer.read()).decode("utf-8")

            # Add overlay to KML
            epicenter.description = """<table style="border-collapse: collapse; width: 100%%; text-align: left;">
            <tr><td style="text-align: left; font-weight: bold;">ID:</td><td>%s</td></tr>
            <tr><td style="text-align: left; font-weight: bold;">Date:</td><td>%s</td></tr>
            <tr><td style="text-align: left; font-weight: bold;">Mag:</td><td>%.1f</td></tr>
            <tr><td style="text-align: left; font-weight: bold;">Depth:</td><td>%.0f km</td></tr>
            <tr>
                <td colspan="2" style="text-align: left;">
                    <a href="https://earthquake.usgs.gov/earthquakes/eventpage/%s" target="_blank"
                    style="display: inline-block; text-align: left;">
                        USGS event page
                    </a>
                </td>
            </tr>
            <tr>
                <td colspan="2"">
                    <strong>Focal Mechanism</strong><br>
                    <img src="data:image/png;base64,%s" alt="Focal Mechanism" 
                    style="display: block; margin: 0 auto; max-width: 100%%;" />
                </td>
            </tr>
            </table>""" % (self.event_id, self.date.strftime('%Y-%m-%d %H:%M:%S'),
                           self.mag, self.depth[1] / 1000, self.event_id, img_base64)
        else:
            epicenter.description = """<table style="border-collapse: collapse;">
            <tr><td style="text-align: left; font-weight: bold;">ID:</td><td>%s</td></tr>
            <tr><td style="text-align: left; font-weight: bold;">Date:</td><td>%s</td></tr>
            <tr><td style="text-align: left; font-weight: bold;">Mag:</td><td>%.1f</td></tr>
            <tr><td style="text-align: left; font-weight: bold;">Depth:</td><td>%.0f km</td></tr>
            <tr>
                <td colspan="2" style="text-align: left;">
                    <a href="https://earthquake.usgs.gov/earthquakes/eventpage/%s" target="_blank"
                    style="display: inline-block; text-align: left;">
                        USGS event page
                    </a>
                </td>
            </tr>
            </table>
            """ % (self.event_id, self.date.strftime('%Y-%m-%d %H:%M:%S'), self.mag,
                   self.depth[1] / 1000, self.event_id)

        def process_contours(contours, lon_grid, lat_grid, color, name):
            """
            Converts contour paths into properly formatted KML polygons.
            """
            polygons = []
            for contour in contours:
                # Convert contour indices to geographic coordinates
                row_indices = contour[:, 0].astype(int)
                col_indices = contour[:, 1].astype(int)
                lon = lon_grid[row_indices, col_indices]
                lat = lat_grid[row_indices, col_indices]

                poly = Polygon(np.column_stack((lon, lat)))  # Convert to a Shapely polygon
                if poly.area > 0.0025:
                    # ignore polygons with areas smaller than 10 km**2 (expressed in deg**2)
                    polygons.append(poly)
                    # print('added %f' % poly.area)
                # else:
                    # print('not added %f' % poly.area)

            # Separate outer polygons from inner holes
            outer_polys = []
            holes = []
            for poly in polygons:
                is_hole = False
                for outer in outer_polys:
                    if outer.contains(poly):  # If it's inside another, it's a hole
                        holes.append(poly)
                        is_hole = True
                        break
                if not is_hole:
                    outer_polys.append(poly)
                    # print('outer detected')

            # Add to KML
            for outer in outer_polys:
                kml_poly = kml.newpolygon(name=name, outerboundaryis=outer.exterior.coords)
                kml_poly.style.linestyle.color = color
                kml_poly.style.linestyle.width = 3
                kml_poly.style.polystyle.color = simplekml.Color.changealphaint(0, simplekml.Color.white)

                # todo: add the inner boundary, which needs to be tested
                # Add inner boundaries (holes)
                # for hole in holes:
                #    if outer.contains(hole):  # Ensure hole belongs to this polygon
                #        kml_poly.innerboundaryis = hole.exterior.coords

        # Process coseismic and postseismic separately
        process_contours(coseismic_contours, c_lon, c_lat, simplekml.Color.blue, "Coseismic mask")
        if include_postseismic:
            process_contours(postseismic_contours, p_lon, p_lat, simplekml.Color.orange, "Postseismic mask")

        if kmz_file:
            kml.savekmz(kmz_file)

        if txt_file is not None:
            # inverse azimuthal equidistant (coseismic)
            if include_postseismic:
                np.savetxt(txt_file, np.column_stack((c_lon, c_lat, self.c_mask.flatten(), self.p_mask.flatten())))
            else:
                np.savetxt(txt_file, np.column_stack((c_lon, c_lat, self.c_mask.flatten())))

        return kml.kml()


class Mask(Score):
    """
    A mask object takes an earthquake id as a parameter and calculates the mask or loads it from the database
    """

    def __init__(self, cnn: dbConnection.Cnn, earthquake_id: str, density=750):

        eq = cnn.get('earthquakes', {'id': earthquake_id})

        strike = [float(eq['strike1']), float(eq['strike2'])] if not math.isnan(eq['strike1']) else []
        dip = [float(eq['dip1']), float(eq['dip2'])] if not math.isnan(eq['strike1']) else []
        rake = [float(eq['rake1']), float(eq['rake2'])] if not math.isnan(eq['strike1']) else []

        super().__init__(float(eq['lat']), float(eq['lon']), float(eq['depth']), float(eq['mag']),
                         strike, dip, rake, eq['date'], density=density, location=eq['location'], event_id=eq['id'])

        if eq['density'] is None:
            # need to call super
            self.c_kml = super().save_masks(include_postseismic=False)
            self.cp_kml = super().save_masks(include_postseismic=True)

            # save values to database only if density is 750 or above
            if density >= 750:
                cnn.update('earthquakes',
                           {'density': density, 'c_kml'  : self.c_kml, 'cp_kml' : self.cp_kml},
                           id=eq['id'])
        else:
            self.c_kml = eq['c_kml']
            self.cp_kml = eq['cp_kml']

    def save_masks(self, txt_file=None, kmz_file=None, include_postseismic=False):
        """
        Completely override the parent's save_mask method
        """
        if kmz_file:
            if not include_postseismic:
                kml_stream = io.BytesIO(self.c_kml.encode('utf-8'))
            else:
                kml_stream = io.BytesIO(self.cp_kml.encode('utf-8'))

            # Create the KMZ file (a zip archive)
            with zipfile.ZipFile(kmz_file, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
                # Write the KML string into the KMZ file
                zf.writestr('doc.kml', kml_stream.getvalue())

        if txt_file:
            # First, transform the scalar field coordinates using inv_azimuthal
            c_lon, c_lat = inv_azimuthal(self.c_mx, self.c_my, self.lon, self.lat)
            # inverse azimuthal equidistant (coseismic)
            if include_postseismic:
                np.savetxt(txt_file, np.column_stack((c_lon, c_lat, self.c_mask.flatten(), self.p_mask.flatten())))
            else:
                np.savetxt(txt_file, np.column_stack((c_lon, c_lat, self.c_mask.flatten())))

        return self.cp_kml if include_postseismic else self.c_kml


def okada(alpha, x, y, d, L1, L2, W1, W2, snd, csd, B1, B2, B3):
    # A rectangular dislocation is located in an elastic halfspace.
    # Right-handed coordinate system {X,Y,Z} has Z positive upwards.
    # Upper and lower edges of the rectangle are horizontal and parallel to the X axis.
    # The rectangular dislocation is located within a dipping plane.
    # The elastic half-space (Z<0) is uniform and isotropic.

    ####################################################################
    # switch between our variable names and those used in Okada's code
    alp = alpha
    al1 = L1
    al2 = L2
    aw1 = W1
    aw2 = W2
    dep = d
    disl1 = B1
    disl2 = B2
    disl3 = B3
    sd = snd
    cd = csd

    # check if input is np array, if not convert
    if not isinstance(x, np.ndarray):
        x = np.array([x])
        y = np.array([y])

    p = y * cd + dep * sd
    q = y * sd - dep * cd

    et = p - aw1  # K=1   J=1   JK=2
    xi = x - al1
    ux, uy, uz = okadakernel(alp, xi, et, q, sd, cd, disl1, disl2, disl3)

    xi = x - al2  # K=1   J=2   JK=3
    u1, u2, u3 = okadakernel(alp, xi, et, q, sd, cd, disl1, disl2, disl3)
    ux -= u1
    uy -= u2
    uz -= u3

    et = p - aw2  # K=2   J=1   JK=3
    xi = x - al1
    u1, u2, u3 = okadakernel(alp, xi, et, q, sd, cd, disl1, disl2, disl3)
    ux -= u1
    uy -= u2
    uz -= u3

    xi = x - al2  # K=2   J=2   JK=4
    u1, u2, u3 = okadakernel(alp, xi, et, q, sd, cd, disl1, disl2, disl3)
    ux += u1
    uy += u2
    uz += u3

    return ux, uy, uz


def okadakernel(alp, xi, et, q, sd, cd, disl1, disl2, disl3):

    pi2 = 2 * np.pi

    f2 = 2 * np.ones_like(xi)
    xi2 = xi ** 2
    et2 = et ** 2
    q2 = q ** 2
    r2 = xi2 + et2 + q2
    r = np.sqrt(r2)
    d = et * sd - q * cd
    y = et * cd + q * sd
    ret = r + et
    ret[ret < 0] = 0
    rd = r + d
    tt = np.arctan(xi * et / (q * r))
    re = np.zeros_like(ret)
    re[ret != 0] = 1 / ret[ret != 0]
    dle = np.zeros_like(ret)
    dle[ret == 0] = -np.log(r[ret == 0] - et[ret == 0])
    dle[ret != 0] = np.log(ret[ret != 0])
    rrx = 1 / (r * (r + xi))
    rre = re / r

    u1 = np.zeros_like(xi)
    u2 = np.zeros_like(xi)
    u3 = np.zeros_like(xi)

    if abs(cd) > 1e-6:  # if not vertical fault
        td = sd / cd
        x = np.sqrt(xi2 + q2)
        a5 = np.zeros_like(xi)
        a5[xi != 0] = alp * f2[xi != 0] / cd * np.arctan(
            (et[xi != 0] * (x[xi != 0] + q[xi != 0] * cd) + x[xi != 0] * (r[xi != 0] + x[xi != 0]) * sd) /
            (xi[xi != 0] * (r[xi != 0] + x[xi != 0]) * cd)
        )
        a4 = alp / cd * (np.log(rd) - sd * dle)
        a3 = alp * (y / rd / cd - dle) + td * a4
        a1 = -alp / cd * xi / rd - td * a5
    else:  # if vertical fault
        a1 = -alp / f2 * xi * q / rd ** 2
        a3 = alp / f2 * (et / rd + y * q / rd ** 2 - dle)
        if disl1 != 0:
            a4 = -alp * q / rd
        a5 = -alp * xi * sd / rd

    if disl1 != 0:
        a2 = -alp * dle - a3

    if disl1 != 0:
        un = disl1 / pi2
        req = rre * q
        u1 -= un * (req * xi + tt + a1 * sd)
        u2 -= un * (req * y + q * cd * re + a2 * sd)
        u3 -= un * (req * d + q * sd * re + a4 * sd)

    if disl2 != 0:
        un = disl2 / pi2
        sdcd = sd * cd
        u1 -= un * (q / r - a3 * sdcd)
        u2 -= un * (y * q * rrx + cd * tt - a1 * sdcd)
        u3 -= un * (d * q * rrx + sd * tt - a5 * sdcd)

    if disl3 != 0:
        un = disl3 / pi2
        sdsd = sd * sd
        u1 += un * (q2 * rre - a3 * sdsd)
        u2 += un * (-d * q * rrx - sd * (xi * q * rre - tt) - a1 * sdsd)
        u3 += un * (y * q * rrx + cd * (xi * q * rre - tt) - a5 * sdsd)

    return u1, u2, u3


if __name__ == '__main__':
    from geode import dbConnection
    conn = dbConnection.Cnn('gnss_data.cfg')
    st = ScoreTable(conn, -34, -58, Date(year=1995, doy=1), Date(year=2024, doy=1))
    print(' -- Done')
    for e in st.table:
        print(e)

    _score = Score(-3.6122000e+01, -7.2898000e+01, 22.9, 8.8, [178, 17], [77, 14],
                   [86, 108], density=1000)
    print(_score.save_masks(kmz_file='test.kmz', include_postseismic=True))

    et = EarthquakeTable(conn, 'us20003k7a')
    print(et.c_stations)

