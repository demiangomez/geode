# -*- coding: utf-8 -*-
"""
Project: Geodetic Database Engine (GeoDE)
Date: 3/3/17 11:27 AM
Author: Demian D. Gomez
"""
import datetime
import traceback
import warnings
import sys
import os
from time import time
from io import BytesIO
import base64
import logging
from logging import INFO, ERROR, WARNING, DEBUG, StreamHandler, Formatter
import copy
import platform

from importlib.metadata import version, PackageNotFoundError

import numpy.linalg

try:
    VERSION = str(version("geode-gnss"))
except PackageNotFoundError:
    # package is not installed
    VERSION = 'NOT_AVAIL'
    pass

# add to supper WARNING message: Warning: Ignoring XDG_SESSION_TYPE=wayland on Gnome.
# Use QT_QPA_PLATFORM=wayland to run on Wayland anyway.
# see https://stackoverflow.com/questions/69828508/warning-ignoring-xdg-session-type-wayland-on-gnome-use-qt-qpa-
# platform-wayland
os.environ["XDG_SESSION_TYPE"] = "xcb"

# deps
import numpy as np
from numpy import sin, cos, pi
from scipy.stats import chi2
from sklearn.cluster import DBSCAN
import matplotlib

if not os.environ.get('DISPLAY', None):
    matplotlib.use('Agg')

from matplotlib.widgets import Button

# app
from .metadata.station_info import StationInfo, StationInfoException
from . import pyDate
from . import pyEvents
from .Utils import ct2lg, lg2ct, rotlg2ct, crc32, stationID, lla2ecef
from .pyBunch import Bunch

LIMIT = 2.5


type_dict = {-1: 'UNDETERMINED',
              1: 'MECHANICAL (MANUAL)',
              2: 'MECHANICAL (ANTENNA CHANGE)',
              5: 'REFERENCE FRAME CHANGE',
             10: 'CO+POSTSEISMIC',
             15: 'COSEISMIC ONLY',
             20: 'POSTSEISMIC ONLY'}

type_dict_user = {0: 'MECHANICAL',
                  1: 'CO+POSTSEISMIC',
                  2: 'POSTSEISMIC'}

periodic_status_dict = {'A': 'AUTOMATICALLY ADDED',
                        'R': 'ADDED BY USER',
                        'D': 'ADDED BY USER, UNABLE TO FIT'}

# unknown jump
UNDETERMINED = -1
# no effect: display purposes
GENERIC_JUMP = 1
# antenna change jump
ANTENNA_CHANGE = 2
# reference frame jump
REFERENCE_FRAME_JUMP = 5
# co-seismic jump and decay
CO_SEISMIC_JUMP_DECAY = 10
# co-seismic jump only, no decay
CO_SEISMIC_JUMP = 15
# co-seismic decay only
CO_SEISMIC_DECAY = 20

EQ_MIN_DAYS = 15
JP_MIN_DAYS = 5

DEFAULT_RELAXATION = np.array([0.5])
DEFAULT_POL_TERMS = 2
DEFAULT_FREQUENCIES = np.array(
    (1 / 365.25, 1 / (365.25 / 2)))  # (1 yr, 6 months) expressed in 1/days (one year = 365.25)

SIGMA_FLOOR_H = 0.10
SIGMA_FLOOR_V = 0.15

ESTIMATION = 0
DATABASE = 1

from . import pyOkada
from . import dbConnection

language = {
    'eng': {
        "station": "Station",
        "north": "North",
        "east": "East",
        "up": "Up",
        "table_title": "Year Day Relx    [mm] Mag   D [km]",
        "periodic": "Periodic amp",
        "velocity": "Velocity",
        "from_model": "from model",
        "acceleration": "Acceleration",
        "position": "Conventional Epoch Pos.",
        "completion": "Completion",
        "other": "other polynomial terms",
        "not_enough": "Not enough solutions to fit an ETM.",
        "table_too_long": "Table too long to print!",
        "frequency": "Frequency",
        "N residuals": "N Residuals",
        "E residuals": "E Residuals",
        "U residuals": "U Residuals",
        "histogram plot": "Histogram",
        "residual plot": "Residual Plot",
        "jumps removed": "Jumps Removed",
        "polynomial removed": "Polynomial Removed"
    },
    'spa': {
        "station": "Estación",
        "north": "Norte",
        "east": "Este",
        "up": "Arriba",
        "table_title": "Año  Día Relx    [mm] Mag   D [km]",
        "periodic": "Amp. Periódica",
        "velocity": "Velocidad",
        "from_model": "de modelo",
        "acceleration": "Aceleración",
        "position": "Posición Época Conv.",
        "completion": "Completitud",
        "other": "otros términos polinómicos",
        "not_enough": "No hay suficientes soluciones para ajustar trayectorias.",
        "table_too_long": "Tabla demasiado larga!",
        "frequency": "Frecuencia",
        "N residuals": "Residuos N",
        "E residuals": "Residuos E",
        "U residuals": "Residuos U",
        "histogram plot": "Histograma",
        "residual plot": "Gráfico de Residuos",
        "jumps removed": "Saltos Removidos",
        "polynomial removed": "Polinomio Removido"
    }}

if 'LANG' not in globals():
    LANG = 'eng'


def LABEL(msg):
    global LANG
    return language[LANG][msg]


# logger information and setup
logger = logging.getLogger('pyETM')
stream = StreamHandler()
stream.setFormatter(Formatter(' -- %(message)s'))
logger.addHandler(stream)


def tic():
    global tt
    tt = time()


def toc(text):
    global tt
    print(text + ': ' + str(time() - tt))


def prYellow(skk):
    if os.fstat(0) == os.fstat(1):
        return "\033[93m{}\033[00m" .format(skk)
    else:
        return skk


class Model(object):
    VEL = 1
    LOG = 2

    def __init__(self, m_type, **kwargs):
        """
        Interface to remove pre-determined model from time series. Currently only velocity (VEL) and postseismic
        deformation (LOG) implemented. For velocity, pass m_type = Model.VEL, date = reference date of velocity, and
        velocity = ndarray((3,1)). For postseismic, poss m_type = Model.LOG, date = jump datetime, relaxation =
        ndarray((n,1)), log_amplitude = ndarray((n,3)). To eval the model, call eval with the t vector corresponding to
        the time series.
        """
        self.type = m_type
        self.date = None

        # parse args
        for key in kwargs:
            arg = kwargs[key]
            key = key.lower()

            if key == 'relaxation':
                if isinstance(arg, list):
                    self.relaxation = np.array(arg)
                elif isinstance(arg, np.ndarray):
                    self.relaxation = arg
                elif isinstance(arg, float):
                    self.relaxation = np.array(arg)
                else:
                    raise pyETMException_Model('\'relaxation\' must be list, numpy.ndarray, or float')
            elif key == 'velocity':
                if isinstance(arg, list):
                    self.velocity = np.array(arg)
                elif isinstance(arg, np.ndarray):
                    self.velocity = arg
                else:
                    raise pyETMException_Model('\'velocity\' must be list or numpy.ndarray')
            elif key == 'log_amplitude':
                if isinstance(arg, list):
                    self.log_amplitude = np.array(arg)
                elif isinstance(arg, np.ndarray):
                    self.log_amplitude = arg
                else:
                    raise pyETMException_Model('\'log_amplitude\' must be list or numpy.ndarray')
            elif key == 'date':
                if isinstance(arg, pyDate.Date):
                    self.date = arg
                elif isinstance(arg, datetime.datetime):
                    self.date = pyDate.Date(datetime=arg)
                else:
                    raise pyETMException_Model('\'date\' must be pyDate.Date or datetime')
            elif key == 'fit':
                if isinstance(arg, bool):
                    self.fit = arg
                else:
                    raise pyETMException_Model('\'fit\' must be boolean')

        if m_type == self.LOG:
            # validate the dimensions of relaxation and amplitude
            if self.log_amplitude.shape[0] != self.relaxation.shape[0]:
                raise pyETMException_Model('\'log_amplitude\' dimension 0 must match the elements in relaxation')

    def eval(self, t):
        model = np.zeros((3, t.shape[0]))
        if self.date is None:
            # use the minimum date as the ref date
            self.date = pyDate.Date(fyear=t.min())

        for i in range(3):
            if self.type == self.VEL:
                logger.info('Applying velocity for reference date %s' % self.date.yyyyddd())
                model[i] = (t - self.date.fyear) * self.velocity[i]

            elif self.type == self.LOG:
                logger.info('Applying log model for event %s' % self.date.yyyyddd())
                # log parameters passed, check each relaxation to see if one has to be removed
                for j, r in enumerate(self.relaxation):
                    # for each relaxation, evaluate the model to subtract it from self.l
                    hl = np.zeros((t.shape[0],))
                    pmodel = np.zeros((3, t.shape[0]))
                    hl[t > self.date.fyear] = np.log10(1. + (t[t > self.date.fyear] - self.date.fyear) / r)
                    # apply the amplitudes
                    amp = self.log_amplitude[j][i]
                    model[i] += amp * hl

        return model


class pyETMException(Exception):

    def __init__(self, value):
        self.value = value
        self.event = pyEvents.Event(Description=value, EventType='error')

    def __str__(self):
        return str(self.value)


class pyETMException_NoDesignMatrix(pyETMException):
    pass


class pyETMException_Model(pyETMException):
    pass


def distance(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees)
    """

    # convert decimal degrees to radians
    lon1 = lon1 * pi / 180
    lat1 = lat1 * pi / 180
    lon2 = lon2 * pi / 180
    lat2 = lat2 * pi / 180
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    km = 6371 * c
    return km


def to_postgres(dictionary):
    if isinstance(dictionary, dict):
        for key, val in list(dictionary.items()):
            if isinstance(val, np.ndarray):
                dictionary[key] = str(val.flatten().tolist()).replace('[', '{').replace(']', '}')
            if key in 'covar':
                # remove covar key which is not in the database
                dictionary.pop(key, None)
    else:
        dictionary = str(dictionary.flatten().tolist()).replace('[', '{').replace(']', '}')

    return dictionary


def to_list(dictionary):
    for key, val in list(dictionary.items()):
        if isinstance(val, np.ndarray):
            dictionary[key] = val.tolist()

        elif isinstance(val, pyDate.datetime):
            date = pyDate.Date(datetime=val)
            dictionary[key] = {'date': val.strftime('%Y-%m-%d %H:%M:%S'),
                               'year': val.year,
                               'doy': date.doy,
                               'mjd': date.mjd,
                               'fyear': date.fyear}

    return dictionary


def find_sets_with_tolerance(numbers, tolerance=2/365.25):
    """
    Finds sets of numbers within a given tolerance.

    Args:
        numbers (list): List of numbers to group into sets.
        tolerance (float): The maximum allowed difference between two numbers in a set (default 2 days).

    Returns:
        list: set of unique values.
    """

    set = []
    for number in numbers:
        found_set = False
        for i, set_ in enumerate(set):
            if abs(number - set_) <= tolerance:
                found_set = True
                break
        if not found_set:
            set.append(number)

    return set


class PppSoln:
    """"class to extract the PPP solutions from the database"""

    def __init__(self, cnn, NetworkCode, StationCode):

        self.NetworkCode = NetworkCode
        self.StationCode = StationCode
        self.hash = 0

        stn_id = stationID(self)

        self.type = 'ppp'
        self.stack_name = 'ppp'
        self.project = 'from_ppp'

        # get the station from the stations table
        stn = cnn.query('SELECT * FROM stations WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                        % (NetworkCode, StationCode))

        stn = stn.dictresult()[0]

        if stn['lat'] is None:
            raise pyETMException('Station %s has no valid metadata in the stations table.' % stn_id)

        self.lat = np.array([float(stn['lat'])])
        self.lon = np.array([float(stn['lon'])])
        self.height = np.array([float(stn['height'])])
        self.auto_x = np.array([float(stn['auto_x'])])
        self.auto_y = np.array([float(stn['auto_y'])])
        self.auto_z = np.array([float(stn['auto_z'])])

        x = np.array([float(stn['auto_x'])])
        y = np.array([float(stn['auto_y'])])
        z = np.array([float(stn['auto_z'])])

        if stn['max_dist'] is not None:
            self.max_dist = stn['max_dist']
        else:
            self.max_dist = 20

        # load all the PPP coordinates available for this station
        # exclude ppp solutions in the exclude table and any solution that is more than 20 meters from the simple
        # linear trend calculated above

        self.excluded = cnn.query_float('SELECT "Year", "DOY" FROM ppp_soln_excl '
                                        'WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                                        % (NetworkCode, StationCode))

        self.table = cnn.query_float(
            'SELECT "X", "Y", "Z", "Year", "DOY" FROM ppp_soln p1 '
            'WHERE p1."NetworkCode" = \'%s\' AND p1."StationCode" = \'%s\' ORDER BY "Year", "DOY"'
            % (NetworkCode, StationCode))

        self.table = [item for item in self.table
                      if np.sqrt(np.square(item[0] - x) + np.square(item[1] - y) + np.square(item[2] - z)) <=
                      self.max_dist and item[3:] not in self.excluded]

        self.blunders = [item for item in self.table
                         if np.sqrt(np.square(item[0] - x) + np.square(item[1] - y) + np.square(item[2] - z)) >
                         self.max_dist and item[3:] not in self.excluded]

        self.solutions = len(self.table)

        self.ts_blu = np.array([pyDate.Date(year=item[3], doy=item[4]).fyear for item in self.blunders])

        if self.solutions >= 1:
            a = np.array(self.table)

            self.x = a[:, 0]
            self.y = a[:, 1]
            self.z = a[:, 2]
            self.t = np.array([pyDate.Date(year=item[0], doy=item[1]).fyear for item in a[:, 3:5]])
            self.mjd = np.array([pyDate.Date(year=item[0], doy=item[1]).mjd for item in a[:, 3:5]])

            self.date = [pyDate.Date(year=item[0], doy=item[1]) for item in a[:, 3:5]]

            # continuous time vector for plots
            ts = np.arange(np.min(self.mjd), np.max(self.mjd) + 1, 1)
            self.mjds = ts
            self.ts = np.array([pyDate.Date(mjd=tts).fyear for tts in ts])

            # gaps: find all days without solutions which can be considered gaps in data
            self.gaps = np.setdiff1d(self.mjds, self.mjd)
            self.span_completion = (1.0 - len(self.gaps) / len(self.mjds)) * 100.0

        elif len(self.blunders) >= 1:
            raise pyETMException('No viable PPP solutions available for %s (all blunders!)\n'
                                 '  -> min distance to station coordinate is %.1f meters'
                                 % (stn_id, np.array([item[5]
                                                      for item in self.blunders]).min()))
        else:
            raise pyETMException('No PPP solutions available for %s' % stn_id)

        # get a list of the epochs with files but no solutions.
        # This will be shown in the outliers plot as a special marker

        rnx = cnn.query(
            'SELECT r."ObservationFYear" FROM rinex_proc as r '
            'LEFT JOIN ppp_soln as p ON '
            'r."NetworkCode" = p."NetworkCode" AND '
            'r."StationCode" = p."StationCode" AND '
            'r."ObservationYear" = p."Year"    AND '
            'r."ObservationDOY"  = p."DOY"'
            'WHERE r."NetworkCode" = \'%s\' AND r."StationCode" = \'%s\' AND '
            'p."NetworkCode" IS NULL' % (NetworkCode, StationCode))

        self.rnx_no_ppp = rnx.getresult()

        self.ts_ns = np.array([item for item in self.rnx_no_ppp])

        self.completion = 100. - float(len(self.ts_ns)) / float(len(self.ts_ns) + len(self.t)) * 100.

        ppp_hash = cnn.query_float('SELECT sum(hash) FROM ppp_soln p1 '
                                   'WHERE p1."NetworkCode" = \'%s\' AND p1."StationCode" = \'%s\''
                                   % (NetworkCode, StationCode))

        self.hash = crc32(str(len(self.t) + len(self.blunders)) + ' ' +
                          str(self.auto_x) +
                          str(self.auto_y) +
                          str(self.auto_z) +
                          str(ts[0]) + ' ' +
                          str(ts[-1]) + ' ' +
                          str(ppp_hash[0][0]) +
                          VERSION)


class GamitSoln:
    """"class to extract the GAMIT polyhedrons from the database"""

    def __init__(self, cnn, polyhedrons, NetworkCode, StationCode, stack_name):

        self.NetworkCode = NetworkCode
        self.StationCode = StationCode

        stn_id = stationID(self)

        # get the project name that initiated the stack
        prj = cnn.query_float(
            'SELECT "Project" FROM stacks WHERE name = \'%s\' AND '
            '"NetworkCode" = \'%s\' AND "StationCode" = \'%s\' LIMIT 1'
            % (stack_name, NetworkCode, StationCode), as_dict=True)
        # check if len > 0, sometimes some stations don't have any data!
        if len(prj) > 0:
            self.project = prj[0]['Project']

        self.stack_name = stack_name
        self.hash = 0

        self.type = 'gamit'

        # get the station from the stations table
        stn = cnn.query_float('SELECT * FROM stations WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                              % (NetworkCode, StationCode), as_dict=True)[0]

        if stn['lat'] is not None:
            self.lat = np.array([float(stn['lat'])])
            self.lon = np.array([float(stn['lon'])])
            self.height = np.array([stn['height']])
            self.auto_x = np.array([float(stn['auto_x'])])
            self.auto_y = np.array([float(stn['auto_y'])])
            self.auto_z = np.array([float(stn['auto_z'])])

            if stn['max_dist'] is not None:
                self.max_dist = stn['max_dist']
            else:
                self.max_dist = 20

            self.solutions = len(polyhedrons)

            # blunders
            self.blunders = []
            self.ts_blu = np.array([])

            if self.solutions >= 1:
                a = np.array(polyhedrons, dtype=float)

                if np.sqrt(np.sum(np.square(a[0, 0:3]))) > 6.3e3:
                    # coordinates given in XYZ
                    nb = np.sqrt(np.sum(
                        np.square(a[:, 0:3] - np.array([stn['auto_x'], stn['auto_y'], stn['auto_z']])), axis=1)) \
                         <= self.max_dist
                else:
                    # coordinates are differences
                    nb = np.sqrt(np.sum(np.square(a[:, 0:3]), axis=1)) <= self.max_dist

                if np.any(nb):
                    self.x = a[nb, 0]
                    self.y = a[nb, 1]
                    self.z = a[nb, 2]
                    self.t = np.array([pyDate.Date(year=item[0], doy=item[1]).fyear for item in a[nb, 3:5]])
                    self.mjd = np.array([pyDate.Date(year=item[0], doy=item[1]).mjd for item in a[nb, 3:5]])

                    self.date = [pyDate.Date(year=item[0], doy=item[1]) for item in a[nb, 3:5]]

                    # continuous time vector for plots
                    ts = np.arange(np.min(self.mjd), np.max(self.mjd) + 1, 1)
                    self.mjds = ts
                    self.ts = np.array([pyDate.Date(mjd=tts).fyear for tts in ts])

                    # gaps: find all days without solutions which can be considered gaps in data
                    self.gaps = np.setdiff1d(self.mjds, self.mjd)
                    self.span_completion = (1.0 - len(self.gaps) / len(self.mjds)) * 100.0
                else:
                    dd = np.sqrt(np.sum(
                        np.square(a[:, 0:3] - np.array([stn['auto_x'], stn['auto_y'], stn['auto_z']])), axis=1))

                    raise pyETMException('No viable GAMIT solutions available for %s (all blunders!)\n'
                                         '  -> min distance to station coordinate is %.1f meters'
                                         % (stn_id, dd.min()))
            else:
                raise pyETMException('No GAMIT polyhedrons vertices available for %s' % stn_id)

            # get a list of the epochs with files but no solutions.
            # This will be shown in the outliers plot as a special marker
            rnx = cnn.query(
                'SELECT r.* FROM rinex_proc as r '
                'LEFT JOIN stacks as p ON '
                'r."NetworkCode" = p."NetworkCode" AND '
                'r."StationCode" = p."StationCode" AND '
                'r."ObservationYear" = p."Year"    AND '
                'r."ObservationDOY"  = p."DOY"     AND '
                'p."name" = \'%s\''
                'WHERE r."NetworkCode" = \'%s\' AND r."StationCode" = \'%s\' AND '
                'p."NetworkCode" IS NULL' % (stack_name, NetworkCode, StationCode))

            # new feature: to avoid problems with realignment of the frame. A change in coordinates was not triggering
            # a recalculation of the ETMs
            crd = cnn.query_float(
                'SELECT avg("X") + avg("Y") + avg("Z") AS hash FROM stacks WHERE '
                'name = \'%s\' AND "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                % (stack_name, NetworkCode, StationCode), as_dict=True)

            self.rnx_no_ppp = rnx.dictresult()
            self.ts_ns = np.array([float(item['ObservationFYear']) for item in self.rnx_no_ppp])

            self.completion = 100. - float(len(self.ts_ns)) / float(len(self.ts_ns) + len(self.t)) * 100.

            self.hash = crc32(str(len(self.t) + len(self.blunders)) + ' ' +
                              str(ts[0]) + ' ' +
                              str(ts[-1]) +
                              str(crd[0]['hash']) +
                              VERSION)

        else:
            raise pyETMException('Station %s has no valid metadata in the stations table.' % stn_id)


class ListSoln(GamitSoln):
    """"class to extract the polyhedrons from a list"""

    def __init__(self, cnn, polyhedrons, NetworkCode, StationCode, stack_name='file-unknown', station_metadata=None):

        if not station_metadata:
            # instance can use the database to initialize the object
            logger.info('Solution from file for station %s.%s' % (NetworkCode, StationCode))
            super(ListSoln, self).__init__(cnn=cnn, polyhedrons=polyhedrons, NetworkCode=NetworkCode,
                                           StationCode=StationCode, stack_name=stack_name)
        else:
            logger.info('Solution from file, station metadata for %s.%s from external source'
                        % (NetworkCode, StationCode))
            # station metadata comes from a kmz/kml file
            stnm, lla = station_metadata
            self.NetworkCode = stnm.split('.')[0]
            self.StationCode = stnm.split('.')[1]
            self.stack_name = stack_name

            self.lat = np.array([lla[1]])
            self.lon = np.array([lla[0]])
            self.height = np.array([lla[2]])
            ecef = lla2ecef(np.array([self.lat[0], self.lon[0], self.height[0]]))
            self.auto_x = ecef[0]
            self.auto_y = ecef[1]
            self.auto_z = ecef[2]
            self.max_dist = 3000
            self.solutions = len(polyhedrons)

            # blunders
            self.blunders = []
            self.ts_blu = np.array([])

            if self.solutions >= 1:
                a = np.array(polyhedrons, dtype=float)

                if np.sqrt(np.sum(np.square(a[0, 0:3]))) > 6.3e3:
                    # coordinates given in XYZ
                    nb = np.sqrt(np.sum(np.square(a[:, 0:3] -
                                                  np.array([self.auto_x[0],
                                                            self.auto_y[0],
                                                            self.auto_z[0]])), axis=1)) \
                         <= self.max_dist
                else:
                    # coordinates are differences
                    nb = np.sqrt(np.sum(np.square(a[:, 0:3]), axis=1)) <= self.max_dist

                if np.any(nb):
                    self.x = a[nb, 0]
                    self.y = a[nb, 1]
                    self.z = a[nb, 2]
                    self.t = np.array([pyDate.Date(year=item[0], doy=item[1]).fyear for item in a[nb, 3:5]])
                    self.mjd = np.array([pyDate.Date(year=item[0], doy=item[1]).mjd for item in a[nb, 3:5]])

                    self.date = [pyDate.Date(year=item[0], doy=item[1]) for item in a[nb, 3:5]]

                    # continuous time vector for plots
                    ts = np.arange(np.min(self.mjd), np.max(self.mjd) + 1, 1)
                    self.mjds = ts
                    self.ts = np.array([pyDate.Date(mjd=tts).fyear for tts in ts])

                    # gaps: find all days without solutions which can be considered gaps in data
                    self.gaps = np.setdiff1d(self.mjds, self.mjd)
                    self.span_completion = (1.0 - len(self.gaps) / len(self.mjds)) * 100.0
                else:
                    dd = np.sqrt(np.sum(np.square(a[:, 0:3] -
                                                  np.array([self.auto_x[0],
                                                            self.auto_y[0],
                                                            self.auto_z[0]])), axis=1))

                    raise pyETMException('No viable GAMIT solutions available for %s (all blunders!)\n'
                                         '  -> min distance to station coordinate is %.1f meters'
                                         % (StationCode, dd.min()))
            else:
                raise pyETMException('No GAMIT polyhedrons vertices available for %s' % StationCode)

            self.rnx_no_ppp = []
            self.ts_ns = np.array([])

            self.completion = 100.

            self.hash = crc32(str(len(self.t) + len(self.blunders)) + ' ' +
                              str(ts[0]) + ' ' +
                              str(ts[-1]) +
                              str(0) +
                              VERSION)

        self.rnx_no_ppp = []
        self.type = 'file'


class JumpTable:

    def __init__(self, cnn, NetworkCode, StationCode, soln, t, FitEarthquakes=True, FitGenericJumps=True, models=()):

        self.table = []

        # get earthquakes for this station
        self.earthquakes = Earthquakes(cnn, NetworkCode, StationCode, soln, t, FitEarthquakes, models)
        self.generic_jumps = GenericJumps(cnn, NetworkCode, StationCode, soln, t, FitGenericJumps)

        jumps = self.earthquakes.table + self.generic_jumps.table

        jumps.sort()

        # add the relevant jumps, make sure none are incompatible
        for jump in jumps:
            self.insert_jump(jump)

        # verify last jump to make sure there's enough data
        if len(self.table) > 0:
            jump = None
            # find last active jump
            for j in self.table[-1::-1]:
                # find the previous active jump
                if j.fit:
                    jump = j
                    break

            if jump:
                dt = np.max(t[jump.design[:, -1] != 0]) - \
                     np.min(t[jump.design[:, -1] != 0])
                # check for minimum data of coseismic jumps + decays
                if (jump.p.jump_type == CO_SEISMIC_JUMP_DECAY and
                        (dt < 1 and np.count_nonzero(jump.design[:, -1]) / 365.25 < 0.5)):
                    # was a jump and decay, leave the jump
                    jump.p.jump_type = CO_SEISMIC_JUMP

                    jump.param_count -= jump.nr  # subtract from param count the number of relaxations
                    jump.p.params = np.zeros((3, 1))
                    jump.p.sigmas = np.zeros((3, 1))

                    # reevaluate the design matrix!
                    jump.design = jump.eval(t)
                    jump.rehash()

        # get the coseismic and coseismic decay jumps
        jcs = [j for j in self.table if (j.p.jump_type == CO_SEISMIC_JUMP_DECAY
                                         or j.p.jump_type == CO_SEISMIC_DECAY) and j.fit is True]
        if len(jcs) > 1:
            for j, i in zip(jcs[0:], jcs[1:]):
                j.constrain_years = (i.min_date - j.min_date)
                j.constrain_data_points = np.count_nonzero(t[np.logical_and(t > j.min_date, t < i.min_date)])
            jcs[-1].constrain_years = t.max() - jcs[-1].min_date
            jcs[-1].constrain_data_points = np.count_nonzero(t[np.logical_and(t > jcs[-1].min_date, t < t.max())])
        elif len(jcs) == 1:
            jcs[0].constrain_years = (t.max() - jcs[0].min_date)
            jcs[0].constrain_data_points = np.count_nonzero(t[np.logical_and(t > jcs[0].min_date, t < t.max())])

        self.constrains = np.array([])

        # automatic jump detection section
        self.auto_jumps = []
        auto_jumps = self.detect_jumps(soln)

        for ja in auto_jumps:
            present = False
            for jt in self.table:
                # check if this jump is in the table +- 2 days
                # check that this is not a jump created by a data gap (-1 to make sure we fall in the gap)
                if abs(jt.date.fyear - ja) < 2/365.25 or pyDate.Date(fyear=ja).mjd-1 in soln.gaps:
                    present = True
                    break
            if not present:
                date = pyDate.Date(fyear=ja)
                logger.info(prYellow('Possible unmodeled jump detected on %.3f (%s / %s)'
                            % (ja, date.datetime().strftime('%Y-%m-%d'), date.ddd())))
                self.auto_jumps.append(Jump(NetworkCode, StationCode, soln, soln.t, date,
                                            'auto-jump', silent=True))

    def param_count(self):
        return sum([jump.param_count for jump in self.table if jump.fit])

    def insert_jump(self, jump):

        if len(self.table) == 0:
            self.table.append(jump)
        else:
            # take last jump and compare to adding jump
            jj = None
            for j in self.table[-1::-1]:
                # find the previous active jump
                if j.fit:
                    jj = j
                    break

            if not jj:
                # no active jumps in the table!
                self.table.append(jump)
                return

            elif jump.fit:
                # this operation determines if jumps are equivalent
                # result is true if equivalent, decision is which one survives
                result, decision = jj.__eq__(jump)

                if result:
                    # jumps are  equivalent
                    # decision branches:
                    # 1) decision == jump, remove previous; add jump
                    # 2) decision == jj  , do not add jump (i.e. do nothing)
                    if decision is jump:
                        jj.remove_from_fit()
                    else:
                        jump.remove_from_fit()

            self.table.append(jump)

    def get_design_ts(self, t):
        # if function call NOT for inversion, return the columns even if the design matrix is unstable

        A = np.array([])

        # get the design matrix for the jump table
        for jump in self.table:
            if jump.fit:
                a = jump.eval(t)

                if a.size:
                    if A.size:
                        # if A is not empty, verify that this jump will not make the matrix singular
                        tA = np.column_stack((A, a))
                        # getting the condition number might trigger divide_zero warning => turn off
                        np.seterr(divide='ignore', invalid='ignore')
                        if np.linalg.cond(tA) < 1e10:
                            # adding this jumps doesn't make the matrix singular
                            A = tA
                        else:
                            # if matrix becomes singular, remove from fit!
                            jump.remove_from_fit()
                            warnings.warn('%s had to be removed due to high condition number' % str(jump))
                    else:
                        A = a

        return A

    def load_parameters(self, params, sigmas):

        for jump in self.table:
            if jump.fit:
                jump.load_parameters(params=params, sigmas=sigmas)

    def print_parameters(self):

        output_n = [LABEL('table_title')]
        output_e = [LABEL('table_title')]
        output_u = [LABEL('table_title')]

        for jump in self.table:

            # relaxation counter
            rx = 0
            m = '  -' if np.isnan(jump.magnitude) else jump.magnitude
            epi_dist = '{:>6.1f}'.format(jump.epi_distance) if jump.epi_distance != 0 else ''

            if jump.fit:
                for j, p in enumerate(np.arange(jump.param_count)):
                    psc = jump.p.params[:, p]

                    if j == 0 and jump.p.jump_type is not CO_SEISMIC_DECAY:
                        fmt_str = '{}      {:>7.1f} {} {} {}'
                        output_n.append(fmt_str.format(jump.date.yyyyddd(), psc[0] * 1000.0, m, jump.action, epi_dist))
                        output_e.append(fmt_str.format(jump.date.yyyyddd(), psc[1] * 1000.0, m, jump.action, epi_dist))
                        output_u.append(fmt_str.format(jump.date.yyyyddd(), psc[2] * 1000.0, m, jump.action, epi_dist))
                    else:
                        fmt_str = '{} {:4.2f} {:>7.1f} {} {} {}'
                        output_n.append(fmt_str.format(jump.date.yyyyddd(), jump.p.relaxation[rx], psc[0] * 1000.0, m,
                                                       jump.action, epi_dist))
                        output_e.append(fmt_str.format(jump.date.yyyyddd(), jump.p.relaxation[rx], psc[1] * 1000.0, m,
                                                       jump.action, epi_dist))
                        output_u.append(fmt_str.format(jump.date.yyyyddd(), jump.p.relaxation[rx], psc[2] * 1000.0, m,
                                                       jump.action, epi_dist))
                        # relaxation counter
                        rx += 1
            else:
                for j, _ in enumerate(np.arange(jump.param_count)):
                    if j == 0 and jump.p.jump_type is not CO_SEISMIC_DECAY and jump.action != 'M':
                        fmt_str = '{}            - {} {} {}'
                        # the only type of jump that does not show the jump is a co-seismic decay
                        output_n.append(fmt_str.format(jump.date.yyyyddd(), m, jump.action, epi_dist))
                        output_e.append(fmt_str.format(jump.date.yyyyddd(), m, jump.action, epi_dist))
                        output_u.append(fmt_str.format(jump.date.yyyyddd(), m, jump.action, epi_dist))
                    else:
                        fmt_str = '{} {:4.2f}       - {} {} {}'
                        output_n.append(fmt_str.format(jump.date.yyyyddd(), jump.p.relaxation[rx],
                                                       m, jump.action, epi_dist))
                        output_e.append(fmt_str.format(jump.date.yyyyddd(), jump.p.relaxation[rx],
                                                       m, jump.action, epi_dist))
                        output_u.append(fmt_str.format(jump.date.yyyyddd(), jump.p.relaxation[rx],
                                                       m, jump.action, epi_dist))
                        # relaxation counter
                        rx += 1

        if len(output_n) > 22:
            output_n = output_n[0:22] + [LABEL('table_too_long')]
            output_e = output_e[0:22] + [LABEL('table_too_long')]
            output_u = output_u[0:22] + [LABEL('table_too_long')]

        return '\n'.join(output_n), '\n'.join(output_e), '\n'.join(output_u)

    def find_jumps(self, cluster_labels, time):

        jump_times = []
        p_cluster = 0
        p_time = 0
        for i in range(1, len(cluster_labels)):
            if cluster_labels[i] != -1:
                if cluster_labels[i] != p_cluster:
                    if time[i] - p_time > 20 / 365.25:
                        # only allow jump if more than 20 days
                        jump_times.append(time[i])
                    p_cluster = cluster_labels[i]
                    p_time = time[i]

        return jump_times

    def detect_jumps(self, soln):
        """
        this method uses dbscan to automatically look for jumps in the time series
        """
        # scale the time to match the scale of the gnss positions
        time_scaled = (soln.t - soln.t.min()) / (1 / 365.25) * 0.0001

        ecef = np.array([soln.x - soln.auto_x, soln.y - soln.auto_y, soln.z - soln.auto_z])
        l = ct2lg(ecef[0], ecef[1], ecef[2], soln.lat, soln.lon)

        E_data = np.column_stack((time_scaled, l[1]))
        N_data = np.column_stack((time_scaled, l[0]))

        # Apply DBSCAN clustering with tuned parameters
        eps_value = 0.0028      # Adjust as needed to detect prominent clusters
        min_samples_value = 15  # value from tests

        dbscan = DBSCAN(eps=eps_value, min_samples=min_samples_value)
        e_cluster_labels = dbscan.fit_predict(E_data)
        n_cluster_labels = dbscan.fit_predict(N_data)

        # from the detected jumps, remove duplicates with a +- 2 day difference
        jump_times = find_sets_with_tolerance(self.find_jumps(e_cluster_labels, soln.t) +
                                              self.find_jumps(n_cluster_labels, soln.t))
        jump_times.sort()

        return jump_times


class EtmFunction:

    def __init__(self, **kwargs):

        self.p = Bunch()

        self.p.NetworkCode = kwargs['NetworkCode']
        self.p.StationCode = kwargs['StationCode']
        self.p.soln = kwargs['soln'].type
        self.p.stack = kwargs['soln'].stack_name

        self.p.params = np.array([])
        self.p.sigmas = np.array([])
        self.p.object = ''
        self.p.metadata = None
        self.p.hash = 0
        self.p.covar  = np.array([])

        self.param_count = 0
        self.column_index = np.array([])
        self.format_str = ''
        self.fit = True

    def load_parameters(self, **kwargs):

        params = kwargs['params']
        sigmas = kwargs['sigmas']

        if params.ndim == 1:
            # parameters coming from the database, reshape
            params = params.reshape((3, params.shape[0] // 3))

        if sigmas.ndim == 1:
            # parameters coming from the database, reshape
            sigmas = sigmas.reshape((3, sigmas.shape[0] // 3))

        # determine if parameters are coming from the X vector (LSQ) or from the database (solution for self only)
        if params.shape[1] > self.param_count:
            # X vector
            self.p.params = params[:, self.column_index]
            self.p.sigmas = sigmas[:, self.column_index]
        else:
            # database (solution for self only; no need for column_index)
            self.p.params = params
            self.p.sigmas = sigmas


class Jump(EtmFunction):
    """
    generic jump (mechanic jump, frame change, etc) class
    :argument NetworkCode
    :argument StationCode
    """

    def __init__(self, NetworkCode, StationCode, soln, t, date, metadata, dtype=GENERIC_JUMP, action='A', fit=True,
                 silent=False):

        super(Jump, self).__init__(NetworkCode=NetworkCode, StationCode=StationCode, soln=soln)

        # in the future, can load parameters from the db
        self.p.object = 'jump'

        # define initial state variables
        self.date = date

        self.p.jump_date = date.datetime()
        self.p.metadata = metadata
        self.p.jump_type = dtype
        self.p.relaxation = []

        # new property to identify manually added (or removed) jumps
        self.action = action
        # new property indicating if jump should be adjusted or not
        self.fit = fit

        # add the magnitude property to allow transformation from CO_SEISMIC_JUMP_DECAY to CO_SEISMIC_JUMP and still
        # print the magnitude of the event and the distance to epicenter in the jump table
        self.magnitude = np.nan
        self.epi_distance = 0.
        # the param count of a jump is one!
        self.param_count = 1

        if self.fit:
            # evaluate only if the jump is not flagged as NO EFFECT
            self.design = Jump.eval(self, t)
        else:
            self.design = np.array([])

        if not np.any(self.design) or np.all(self.design):
            # a valid jump only has some rows == 1 in the design matrix,
            # not all rows (all rows produces a singular matrix)
            self.design = np.array([])
            self.fit = False

        # new silent option: do not show a message when adding an automatic jump
        if dtype not in (CO_SEISMIC_JUMP,
                         CO_SEISMIC_DECAY,
                         CO_SEISMIC_JUMP_DECAY) and not silent:
            logger.info('Mec jump -> Adding jump on %s type: %s; Action: %s; Fit: %s'
                        % (self.date.yyyyddd(), type_dict[dtype], action, 'T' if self.fit else 'F'))
        Jump.rehash(self)

    def rehash(self):
        self.p.hash = crc32(str(self.date) + str(self.fit) + VERSION)

    def remove_from_fit(self):
        # this method will make this jump type = NO_EFFECT and adjust its params
        self.fit = False
        self.design = np.array([])
        self.rehash()

    def eval(self, t):
        # given a time vector t, return the design matrix column vector(s)
        if not self.fit:
            return np.array([])

        ht = np.zeros((t.shape[0], 1))

        ht[t > self.date.fyear] = 1.

        return ht

    def load_parameters(self, **kwargs):
        if self.fit:
            EtmFunction.load_parameters(self, **kwargs)

    def __eq__(self, jump):

        if not isinstance(jump, Jump):
            raise pyETMException('type: ' + str(type(jump)) + ' invalid. Can compare two Jump objects')

        if not self.fit and jump.fit:
            # if comparing to a self that has NO_EFFECT, remove and keep jump
            return True, jump
        elif self.fit and not jump.fit:
            # if comparing against a jump that has NO_EFFECT, remove jump keep self
            return True, self
        elif not self.fit and not jump.fit:
            # no jump has an effect, return None. This will be interpreted as False (if not result)
            return None, None

        # if we got here, then both jumps have fit == True
        # compare two jumps together and make sure they will not generate a singular (or near singular) system of eq
        c = np.sum(np.logical_xor(self.design[:, 0], jump.design[:, 0]))
        dt = jump.date - self.date
        # print '  ', jump.date, self.date, dt, c
        if self.p.jump_type >= 10 and jump.p.jump_type >= 10:
            # jump type > 10 => co-seismic jump
            # if self is a co-seismic jump and next jump is also co-seismic
            # and there are more than two weeks of data to constrain params, return false (not equal)
            # otherwise, decide based on the magnitude of events
            if c < self.param_count + 1 or (dt < 365 and c / 365.25 < 0.1):
                if self.magnitude < jump.magnitude:
                    return True, jump
                else:
                    return True, self
            else:
                return False, None

        elif self.p.jump_type >= 10 and 0 < jump.p.jump_type < 10:

            if c < self.param_count + 1 or (dt < 365 and c / 365.25 < 0.1):
                # can't fit the co-seismic or generic jump AND the generic jump after, remove generic jump
                return True, self
            else:
                return False, None

        elif 0 < self.p.jump_type < 10:

            if jump.p.jump_type >= 10:
                if c < self.param_count + 1 or (dt < 365 and c / 365.25 < 0.1):
                    # if generic jump before an earthquake jump and less than 5 days, co-seismic prevails
                    return True, jump
                else:
                    return False, None

            elif 0 < jump.p.jump_type < 10:

                # two generic jumps. As long as they can be constrained, we are fine
                if c < self.param_count + 1 or (dt < 365 and c / 365.25 < 0.1):
                    return True, jump
                else:
                    return False, None
            else:
                # @todo possible bug when falling in this case?
                return False, None
        else:
            # @todo possible bug when falling in this case?
            return False, None

    def __str__(self):
        return 'date=' + str(self.date) + \
               ', type=' + type_dict[self.p.jump_type] + \
               ', metadata="' + self.p.metadata + \
               '", action="' + str(self.action) + \
               '", fit=' + str(self.fit) + \
               '", column_index=' + str(self.column_index)

    def __repr__(self):
        return 'pyPPPETM.Jump(%s)' % str(self)

    def __check_cmp(self, jump):
        if not isinstance(jump, Jump):
            raise pyETMException('type: ' + str(type(jump)) + ' invalid.  Can only compare Jump objects')

    def __lt__(self, jump):
        self.__check_cmp(jump)
        return self.date.fyear < jump.date.fyear

    def __le__(self, jump):
        self.__check_cmp(jump)
        return self.date.fyear <= jump.date.fyear

    def __gt__(self, jump):
        self.__check_cmp(jump)
        return self.date.fyear > jump.date.fyear

    def __ge__(self, jump):
        self.__check_cmp(jump)
        return self.date.fyear >= jump.date.fyear

    def __hash__(self):
        # to make the object hashable
        return hash(self.date.fyear)


class CoSeisJump(Jump):

    def __init__(self, NetworkCode, StationCode, soln, t, date, relaxation, metadata,
                 dtype=CO_SEISMIC_JUMP_DECAY, magnitude=0., action='A', fit=True, models=(), epi_distance=0.):
        # model input is a list of objects of type pyETM.Model (see definition)

        # super-class initialization
        Jump.__init__(self, NetworkCode, StationCode, soln, t, date, metadata, dtype, action, fit)

        # if t.min() > date, change to CO_SEISMIC_DECAY
        # if jump / decay manually deactivated, fit == False and it's not changed below

        if date.fyear < t.min():
            self.p.jump_type = CO_SEISMIC_DECAY
            # save the minimum date validity
            self.min_date = t.min()
        else:
            self.p.jump_type = dtype
            # save the minimum date validity
            self.min_date = date.fyear

        # new feature informs the magnitude of the event in the plot
        self.magnitude = magnitude
        self.epi_distance = epi_distance
        # constrain_years saves how many years of data constrains this jump
        # filled by JumpTable
        self.constrain_years = None

        if not self.fit and fit:
            # came back from init with empty design matrix (self.fit = false) and originally fit was True.
            # Maybe a jump before t.min()
            # assign just the decay
            self.p.jump_type = CO_SEISMIC_DECAY
            # put fit back to original state
            self.fit = fit

        # if T is an array, it contains the corresponding decays
        # otherwise, it is a single decay
        if not isinstance(relaxation, np.ndarray):
            relaxation = np.array([relaxation])

        self.param_count += relaxation.shape[0]
        if self.p.jump_type == CO_SEISMIC_DECAY:
            # if CO_SEISMIC_DECAY, subtract one from parameters
            self.param_count -= 1

        self.nr = relaxation.shape[0]
        self.p.relaxation = relaxation

        logger.info('Geo jump -> Adding jump on %s type: %s; relax: %s; Mag: %.1f; Action: %s; Fit: %s'
                    % (self.date.yyyyddd(), type_dict[dtype], str(relaxation),
                       magnitude, action, 'T' if self.fit else 'F'))

        # DDG: New feature -> include a postseismic relaxation to remove from the data before performing the fit
        # if post-seismic component is passed, then subtract from the data (this step is done in __init__).

        postseismic = [m for m in models if m.type == Model.LOG]

        if postseismic is not None and self.p.jump_type in (CO_SEISMIC_JUMP_DECAY, CO_SEISMIC_DECAY, CO_SEISMIC_JUMP):
            # only run the code if fit == True
            for event in postseismic:
                if event.date == self.date and self.fit:
                    # postseismic parameters passed, check each relaxation to see if one has to be removed
                    for i, r in enumerate(event.relaxation):
                        if r in self.p.relaxation:
                            # this relaxation was supposed to be adjusted, remove
                            self.p.relaxation = np.array([rr for rr in self.p.relaxation if rr != r])
                            self.nr = self.p.relaxation.shape[0]
                            # remove one from the parameter count
                            self.param_count -= 1
                            logger.info('Geophysical Jump -> Modifying %s: removing log decay with T=%.3f '
                                        'and NEU amplitudes of %6.3f %6.3f %6.3f '
                                        '(model provided)' % (self.date.yyyyddd(), r,
                                                              event.log_amplitude[i][0],
                                                              event.log_amplitude[i][1],
                                                              event.log_amplitude[i][2]))

                            # check if any relaxation components were left: if none, then turn to
                            # CO_SEISMIC_JUMP_DECAY into CO_SEISMIC_JUMP
                            if self.nr == 0 and self.p.jump_type == CO_SEISMIC_JUMP_DECAY:
                                self.p.jump_type = CO_SEISMIC_JUMP

                            if self.nr == 0 and self.p.jump_type == CO_SEISMIC_DECAY:
                                # we ended up with an "empty" jump, we need to deactivate it
                                self.fit = False

        if self.fit:
            self.design = self.eval(t)
        else:
            self.design = np.array([])

        self.rehash()

    def rehash(self):
        # co-seismic jump already has the version hash value from Jump object
        self.p.hash = crc32(str(self.date) + str(self.fit) + str(self.param_count) + str(self.p.jump_type) +
                            str(self.p.relaxation) + str(self.fit) + VERSION)

    def eval(self, t):

        ht = Jump.eval(self, t)

        # if there is nothing in ht, then there is no expected output, return none
        if not np.any(ht):
            return np.array([])

        # if it was determined that this is just a co-seismic jump (no decay), return ht
        elif self.p.jump_type == CO_SEISMIC_JUMP:
            return ht

        # support more than one decay
        hl = np.zeros((t.shape[0], self.nr))

        for i, T in enumerate(self.p.relaxation):
            hl[t > self.date.fyear, i] = np.log10(1. + (t[t > self.date.fyear] - self.date.fyear) / T)

        # if it's both jump and decay, return ht + hl

        if np.any(hl):
            if self.p.jump_type == CO_SEISMIC_JUMP_DECAY:
                # get condition number
                A = np.column_stack((ht, hl))
                cond_num = np.log10(np.linalg.cond(A.transpose() @ A))
                if cond_num > 5:
                    logger.info(prYellow('CO_SEISMIC_JUMP_DECAY condition number: %.1f' % cond_num))
                else:
                    logger.info('CO_SEISMIC_JUMP_DECAY condition number: %.1f' % cond_num)
                return np.column_stack((ht, hl))

            elif self.p.jump_type == CO_SEISMIC_DECAY:
                # if decay only, return hl
                return hl
            else:
                return np.array([])
        else:
            return np.array([])

    def __str__(self):
        return Jump.__str__(self) + ', relax=' + str(self.p.relaxation)

    def __repr__(self):
        return 'pyETM.CoSeisJump(%s)' % str(self)


class Earthquakes:

    def __init__(self, cnn, NetworkCode, StationCode, soln, t, FitEarthquakes=True, models=()):

        self.StationCode = StationCode
        self.NetworkCode = NetworkCode

        # load metadata
        lat = soln.lat[0]
        lon = soln.lon[0]

        # establish the limit dates. Ignore jumps before 5 years from the earthquake
        # sdate = pyDate.Date(fyear=t.min() - 5)
        # DDG 30/04/2020: now do not treat the earthquakes before the start date
        # the same as those happening after the start date
        sdate = pyDate.Date(fyear=t.min())
        edate = pyDate.Date(fyear=t.max())

        # get all possible events using the
        s_events = pyOkada.ScoreTable(cnn, lat, lon, sdate, edate)

        # check if data range returned any jumps
        if FitEarthquakes:

            eq_jumps = s_events.table

            # open the jumps table
            jp = cnn.query_float('SELECT * FROM etm_params WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                                 'AND soln = \'%s\' AND jump_type <> 0 AND object = \'jump\''
                                 % (NetworkCode, StationCode, 'gamit' if soln.type == 'file' else soln.type),
                                 as_dict=True)

            # start by collapsing all earthquakes for the same day.
            # Do not allow more than one earthquake on the same day
            f_jumps = []
            next_date = None

            for mag, date, epi_lon, epi_lat, j_type, metadata in eq_jumps:

                # jumps are analyzed in windows that are EQ_MIN_DAYS long
                # a date should not be analyzed if it's < next_date
                if next_date is not None:
                    if date < next_date:
                        continue

                # obtain jumps in a EQ_MIN_DAYS window
                jumps = [(m, d, elon, elat, j_type, meta) for m, d, elon, elat, j_type, meta in eq_jumps
                         if date <= d < date + EQ_MIN_DAYS]

                if len(jumps) > 1:
                    # if more than one jump, get the max magnitude
                    mmag = max(m for m, _, _ , _, _, _ in jumps)

                    # only keep the earthquake with the largest magnitude
                    for m, d, epi_lon, epi_lat, j_type, meta in jumps:

                        epi_dist = float(distance(lon, lat, epi_lon, epi_lat))

                        table = {j['action'] for j in jp if j['Year'] == d.year and j['DOY'] == d.doy}
                        # get a different relaxation for this date
                        relax = [j['relaxation'] for j in jp if j['Year'] == d.year and j['DOY'] == d.doy]

                        if relax and relax[0] is not None:
                            relaxation = np.array(relax[0])
                        else:
                            relaxation = DEFAULT_RELAXATION

                        # if present in jump table, with either + of -, don't use default decay
                        if m == mmag and '-' not in table:
                            f_jumps.append(CoSeisJump(NetworkCode, StationCode, soln, t, d, relaxation,
                                                      meta, magnitude=m, action='+' if '+' in table else 'A',
                                                      models=models, epi_distance=epi_dist, dtype=j_type))
                            # once the jump was added, exit for loop
                            break
                        elif '+' in table:
                            # add only if in jump list with a '+'

                            f_jumps.append(CoSeisJump(NetworkCode, StationCode, soln, t, d,
                                                      relaxation, meta, magnitude=m, action='+',
                                                      models=models, epi_distance=epi_dist, dtype=j_type))
                            # once the jump was added, exit for loop
                            break
                        else:
                            f_jumps.append(CoSeisJump(NetworkCode, StationCode, soln, t, d,
                                                      relaxation, meta, action='-', fit=False,
                                                      models=models, epi_distance=epi_dist, dtype=j_type))
                else:
                    epi_dist = float(distance(lon, lat, epi_lon, epi_lat))

                    # add, unless marked in table with '-'
                    table = {j['action'] for j in jp if j['Year'] == date.year and j['DOY'] == date.doy}
                    # get a different relaxation for this date
                    relax = [j['relaxation'] for j in jp if j['Year'] == date.year and j['DOY'] == date.doy]

                    if relax and relax[0] is not None:
                        relaxation = np.array(relax[0])
                    else:
                        relaxation = DEFAULT_RELAXATION

                    if '-' not in table:
                        f_jumps.append(CoSeisJump(NetworkCode, StationCode, soln, t, date,
                                                  relaxation, metadata, magnitude=mag,
                                                  action='+' if '+' in table else 'A',
                                                  models=models, epi_distance=epi_dist, dtype=j_type))
                    else:
                        # add it with NO_EFFECT for display purposes
                        f_jumps.append(CoSeisJump(NetworkCode, StationCode, soln, t, date,
                                                  relaxation, metadata, magnitude=mag, action='-', fit=False,
                                                  models=models, epi_distance=epi_dist, dtype=j_type))

                next_date = date + EQ_MIN_DAYS

            # now consider events outside of the event table that might be M<6
            notin = ["(%i, %i)" % (j.date.year, j.date.doy) for j in f_jumps]
            notin = ' AND ("Year", "DOY") NOT IN (' + ','.join(notin) + ')' if notin else ''
            # open the jumps table but only consider event with action=+, and dates > last data point of station
            jp = cnn.query_float('SELECT * FROM etm_params WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                                 'AND soln = \'%s\' AND jump_type <> 0 AND object = \'jump\' AND action = \'+\' '
                                 'AND ("Year", "DOY") < (%i, %i)'
                                 % (NetworkCode, StationCode, 'gamit' if soln.type == 'file' else soln.type,
                                    edate.year, edate.doy)
                                 + notin,
                                 as_dict=True)
            for j in jp:
                f_jumps.append(CoSeisJump(NetworkCode, StationCode, soln, t,
                                          pyDate.Date(year=int(j['Year']), doy=int(j['DOY'])),
                                          np.array(j['relaxation']), 'manual geophysical jump', magnitude=0.0,
                                          action='+', models=models, epi_distance=0.0,
                                          dtype=CO_SEISMIC_JUMP_DECAY if j['jump_type'] == 1 else CO_SEISMIC_DECAY))
            # final jump table
            self.table = f_jumps
        else:
            self.table = []


class GenericJumps:

    def __init__(self, cnn, NetworkCode, StationCode, soln, t, FitGenericJumps=True):

        # DDG Nov-30: change the solution type to GAMIT if soln.type == 'file'. This is because otherwise the ETM
        #             done with files will not read the updated parameters from the database
        self.solution_type = 'gamit' if soln.type == 'file' else soln.type
        self.table = []

        if t.size >= 2:
            # analyze if it is possible to add the jumps (based on the available data)
            wt = np.sort(np.unique(t - np.fix(t)))
            # analyze the gaps in the data
            dt = np.diff(wt)
            # max dt (internal)
            dtmax = np.max(dt)
            # dt wrapped around
            dt_interyr = 1 - wt[-1] + wt[0]

            if dt_interyr > dtmax:
                dtmax = dt_interyr

            if dtmax <= 0.2465 and FitGenericJumps:
                # put jumps in
                self.add_metadata_jumps = True
            else:
                # no jumps
                self.add_metadata_jumps = False
        else:
            self.add_metadata_jumps = False

        # open the jumps table
        jp = cnn.query('SELECT * FROM etm_params WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                       'AND soln = \'%s\' AND jump_type = 0 AND object = \'jump\''
                       % (NetworkCode, StationCode, self.solution_type))

        jp = jp.dictresult()

        # get station information
        try:
            self.stninfo = StationInfo(cnn, NetworkCode, StationCode)

            # DDG new behavior: do not add a jump if the equipment is the same (only if antenna or equipment are different)
            prev_red = self.stninfo.records[0]
            for stninfo in self.stninfo.records[1:]:
                date = stninfo['DateStart']

                table = [j['action'] for j in jp if j['Year'] == date.year and j['DOY'] == date.doy]
                # there is an action, or action is automatic
                action = table[0] if table else 'A'

                if (prev_red['AntennaCode'] != stninfo['AntennaCode'] or prev_red['RadomeCode'] != stninfo['RadomeCode']
                    or action != 'A'):

                    # add to list only if:
                    # 1) add_meta = True AND there is no '-' OR
                    # 2) add_meta = False AND there is a '+'

                    self.table.append(Jump(NetworkCode, StationCode, soln, t, date,
                                           'Antenna: %s->%s' % (prev_red['AntennaCode'] + ' ' +
                                                                prev_red['RadomeCode'] if prev_red['RadomeCode'] else
                                                                prev_red['AntennaCode'] + ' NONE',
                                                                stninfo['AntennaCode']  + ' ' +
                                                                stninfo['RadomeCode'] if stninfo['RadomeCode'] else
                                                                stninfo['RadomeCode'] + ' NONE'),
                                           dtype=ANTENNA_CHANGE,
                                           action=action,
                                           fit=('+' in table or (self.add_metadata_jumps and '-' not in table))
                                           ))
                prev_red = stninfo
        except StationInfoException:
            logger.info('No station information found for %s.%s, mechanical jumps may be incomplete'
                        % (NetworkCode, StationCode))

        # frame changes if ppp
        if self.solution_type == 'ppp':
            frames = cnn.query(
                'SELECT distinct on ("ReferenceFrame") "ReferenceFrame", "Year", "DOY" from ppp_soln WHERE '
                '"NetworkCode" = \'%s\' AND "StationCode" = \'%s\' order by "ReferenceFrame", "Year", "DOY"' %
                (NetworkCode, StationCode))

            frames = frames.dictresult()

            if len(frames) > 1:
                # more than one frame, add a jump
                frames.sort(key=lambda k: k['Year'])

                for frame in frames[1:]:
                    date = pyDate.Date(Year=int(frame['Year']), doy=int(frame['DOY']))

                    table = [j['action'] for j in jp if j['Year'] == date.year and j['DOY'] == date.doy]

                    self.table.append(Jump(NetworkCode, StationCode, soln, t, date,
                                           'Frame Change: %s' % frame['ReferenceFrame'],
                                           dtype=REFERENCE_FRAME_JUMP,
                                           action=table[0] if table else 'A',
                                           fit=('-' not in table)))

        # now check the jump table to add specific jumps
        jp = cnn.query('SELECT * FROM etm_params WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                       'AND soln = \'%s\' AND jump_type = 0 AND object = \'jump\' '
                       'AND action = \'+\'' % (NetworkCode, StationCode, self.solution_type)).dictresult()

        table = {j.date for j in self.table}

        for j in jp:
            date = pyDate.Date(Year=int(j['Year']), doy=int(j['DOY']))

            if date not in table:
                self.table.append(Jump(NetworkCode, StationCode, soln, t, date, 'mechanic-jump',
                                       dtype=GENERIC_JUMP,
                                       action='+'))


class Periodic(EtmFunction):
    """"class to determine the periodic terms to be included in the ETM"""

    def __init__(self, cnn, NetworkCode, StationCode, soln, t, FitPeriodic=True):

        super(Periodic, self).__init__(NetworkCode=NetworkCode, StationCode=StationCode, soln=soln)

        try:
            # load the frequencies from the database
            etm_param = cnn.get('etm_params',
                                {'NetworkCode': NetworkCode,
                                 'StationCode': StationCode,
                                 'soln': 'gamit' if soln.type == 'file' else soln.type,
                                 'object': 'periodic'
                                 },
                                ['NetworkCode', 'StationCode', 'soln', 'object', 'frequencies'])

            self.p.frequencies = np.array([float(p) for p in etm_param['frequencies']])
            self.requested_frequencies = self.p.frequencies
        except dbConnection.DatabaseError:
            self.p.frequencies = DEFAULT_FREQUENCIES
            self.requested_frequencies = np.array([])

        self.p.object = 'periodic'

        if t.size > 1 and FitPeriodic:
            # wrap around the solutions
            wt = np.sort(np.unique(t - np.fix(t)))

            # analyze the gaps in the data
            dt = np.diff(wt)

            # max dt (internal)
            dtmax = np.max(dt)

            # dt wrapped around
            dt_interyr = 1 - wt[-1] + wt[0]

            if dt_interyr > dtmax:
                dtmax = dt_interyr

            # save the value of the max wrapped delta time
            self.dt_max = dtmax

            # get the 50 % of Nyquist for each component (and convert to average fyear)
            self.nyquist = ((1 / self.p.frequencies) / 2.) * 0.5 * 1 / 365.25

            # frequency count
            self.frequency_count = int(np.sum(self.dt_max <= self.nyquist))

            # redefine the frequencies vector to accommodate only the frequencies that can be fit
            self.p.frequencies = self.p.frequencies[self.dt_max <= self.nyquist]

        else:
            # no periodic terms
            self.frequency_count = 0
            self.p.frequencies = np.array([])
            self.dt_max = 1  # one year of delta t

        logger.info('Periodic -> Frequency count: %i; FitPeriodic: %s' % (self.frequency_count, str(FitPeriodic)))

        # build the metadata description for the json string
        self.p.metadata = '['
        for k in ('n', 'e', 'u'):
            self.p.metadata += '['
            meta = []
            for i in ('sin', 'cos'):
                for f in (1 / (self.p.frequencies * 365.25)).tolist():
                    meta.append('%s:%s(%.1f yr)' % (k, i, f))

            self.p.metadata += ','.join(meta) + '],'

        self.p.metadata = self.p.metadata + ']'

        self.design = self.get_design_ts(t)
        self.param_count = self.frequency_count * 2
        # declare the location of the answer (to be filled by Design object)
        self.column_index = np.array([])

        self.format_str = LABEL('periodic') + ' (' + \
                          ', '.join('%.1f yr' % i for i in (1 / (self.p.frequencies * 365.25)).tolist()) + \
                          ') N: %s E: %s U: %s [mm]'

        self.p.hash = crc32(str(self.p.frequencies) + VERSION)

    def get_design_ts(self, ts):
        # if dtmax < 3 months (90 days = 0.1232), then we can fit the annual
        # if dtmax < 1.5 months (45 days = 0.24657), then we can fit the semi-annual too

        if self.frequency_count > 0:
            f = self.p.frequencies
            f = np.tile(f, (ts.shape[0], 1))

            As = np.array(sin(2 * pi * f * 365.25 * np.tile(ts[:, np.newaxis], (1, f.shape[1]))))
            Ac = np.array(cos(2 * pi * f * 365.25 * np.tile(ts[:, np.newaxis], (1, f.shape[1]))))

            A = np.column_stack((As, Ac))
        else:
            # no periodic terms
            A = np.array([])

        return A

    def print_parameters(self):

        n = np.array([])
        e = np.array([])
        u = np.array([])

        for p in np.arange(self.param_count):
            psc = self.p.params[:, p]

            sn = psc[0]
            se = psc[1]
            su = psc[2]

            n = np.append(n, sn)
            e = np.append(e, se)
            u = np.append(u, su)

        shape = (2, self.param_count // 2)
        n = n.reshape(shape)
        e = e.reshape(shape)
        u = u.reshape(shape)

        # calculate the amplitude of the components
        an = np.sqrt(np.square(n[0, :]) + np.square(n[1, :]))
        ae = np.sqrt(np.square(e[0, :]) + np.square(e[1, :]))
        au = np.sqrt(np.square(u[0, :]) + np.square(u[1, :]))

        return self.format_str % (np.array_str(an * 1000.0, precision=1),
                                  np.array_str(ae * 1000.0, precision=1),
                                  np.array_str(au * 1000.0, precision=1))


class Polynomial(EtmFunction):
    """"class to build the linear portion of the design matrix"""

    def __init__(self, cnn, NetworkCode, StationCode, soln, t, t_ref=0, models=()):

        super(Polynomial, self).__init__(NetworkCode=NetworkCode, StationCode=StationCode, soln=soln)

        # t ref (just the beginning of t vector)
        if t_ref == 0:
            t_ref = np.min(t)

        self.ce_pos = np.array([[0.0], [0.0], [0.0]])

        self.p.object = 'polynomial'
        self.p.t_ref = t_ref

        # see if any model invoked VEL
        model = [m for m in models if m.type == Model.VEL]

        if len(model) > 0 and not model[0].fit:
            logger.info('Polynomial -> Interseismic velocity provided: removing velocity from fit')
            # interseismic model provided, do not fit linear (remove trend)

            inter = model[0].velocity

            self.terms = 1
            self.format_str = (LABEL('position') + ' (%.3f' % t_ref + ') X: {:.3f} Y: {:.3f} Z: {:.3f} [m]\n'
                               + LABEL('velocity') + ' (' + LABEL('from_model') + ')' +
                               ' N: {:.2f} E: {:.2f} U: {:.2f} [mm/yr]'.format(inter[0, 0] * 1000,
                                                                               inter[1, 0] * 1000,
                                                                               inter[2, 0] * 1000))
            self.p.metadata = '[[n:pos, n:vel],[e:pos, e:vel],[u:pos, u:vel]]'
        else:
            try:
                # load the number of terms from the database
                etm_param = cnn.get('etm_params',
                                    {'NetworkCode': NetworkCode,
                                     'StationCode': StationCode,
                                     'soln': 'gamit' if soln.type == 'file' else soln.type,
                                     'object': 'polynomial'},
                                    ['NetworkCode', 'StationCode', 'soln', 'object', 'terms', 'Year', 'DOY'])

                self.terms = int(etm_param['terms'])
                # check if Year and DOY not null
                if etm_param['Year'] and etm_param['DOY']:
                    self.p.t_ref = pyDate.Date(year=etm_param['Year'], doy=etm_param['DOY']).fyear
                    t_ref = self.p.t_ref

            except dbConnection.DatabaseError:
                self.terms = DEFAULT_POL_TERMS

            logger.info('Polynomial -> Fitting %i term(s), conventional epoch %.3f' % (self.terms, self.p.t_ref))

            if self.terms == 1:
                self.format_str = LABEL('position') + ' (%.3f' % t_ref + \
                                  ') X: {:.3f} Y: {:.3f} Z: {:.3f} [m]'
                self.p.metadata = '[[n:pos],[e:pos],[u:pos]]'

            elif self.terms == 2:
                self.format_str = LABEL('position') + ' (%.3f' % t_ref + \
                                  ') X: {:.3f} Y: {:.3f} Z: {:.3f} [m]\n' \
                                  + LABEL('velocity') + (r' N: {:.2f} $\pm$ {:.2f} E: {:.2f} $\pm$ {:.2f} '
                                                         r'U: {:.2f} $\pm$ {:.2f} [mm/yr]')
                self.p.metadata = '[[n:pos, n:vel],[e:pos, e:vel],[u:pos, u:vel]]'

            elif self.terms == 3:
                self.format_str = LABEL('position') + ' (%.3f' % t_ref + \
                                  ') X: {:.3f} Y: {:.3f} Z: {:.3f} [m]\n' \
                                  + LABEL('velocity') + (r' N: {:.3f} $\pm$ {:.2f} E: {:.3f} $\pm$ {:.2f} '
                                                         r'U: {:.3f} $\pm$ {:.2f} [mm/yr]' + '\n') \
                                  + LABEL('acceleration') + (r' N: {:.2f} $\pm$ {:.2f} E: {:.2f} $\pm$ {:.2f} '
                                                             r'U: {:.2f} $\pm$ {:.2f} [mm/yr**2]')
                self.p.metadata = '[[n:pos, n:vel, n:acc],[e:pos, e:vel, e:acc],[u:pos, u:vel, u:acc]]'

            elif self.terms > 3:
                self.format_str = LABEL('position') + ' (%.3f' % t_ref + \
                                  ') X: {:.3f} Y: {:.3f} Z: {:.3f} [m]\n' \
                                  + LABEL('velocity') + (r' N: {:.3f} $\pm$ {:.2f} E: {:.3f} $\pm$ {:.2f} '
                                                         r'U: {:.3f} $\pm$ {:.2f} [mm/yr]' + '\n') \
                                  + LABEL('acceleration') + (r' N: {:.2f} $\pm$ {:.2f} E: {:.2f} $\pm$ {:.2f} '
                                                             r'U: {:.2f} $\pm$ {:.2f} [mm/yr**2] + ') \
                                  + '%i ' % (self.terms - 3) + LABEL('other')
                self.p.metadata = '[[n:pos, n:vel, n:acc, n:tx...],' \
                                  '[e:pos, e:vel, e:acc, e:tx...],' \
                                  '[u:pos, u:vel, u:acc, u:tx...]]'

        self.design = self.get_design_ts(t)

        # always first in the list of A, index columns are fixed
        self.column_index = np.arange(self.terms)
        # param count is the same as terms
        self.param_count = self.terms
        # save the hash of the object
        self.p.hash = crc32(str(self.terms) + str(self.p.t_ref) + VERSION)

    def load_parameters(self, params, sigmas, t_ref):

        super(Polynomial, self).load_parameters(params=params, sigmas=sigmas)

        self.p.t_ref = t_ref

    def print_parameters(self, ref_xyz, lat, lon):

        params = np.zeros((3,))

        for p in np.arange(self.terms):
            if p == 0:
                params[0], params[1], params[2] = lg2ct(self.p.params[0, 0],
                                                        self.p.params[1, 0],
                                                        self.p.params[2, 0], lat, lon)
                params += ref_xyz.flatten()

            elif p > 0:
                n = self.p.params[0, p]
                e = self.p.params[1, p]
                u = self.p.params[2, p]

                sn = self.p.sigmas[0, p]
                se = self.p.sigmas[1, p]
                su = self.p.sigmas[2, p]

                params = np.append(params, (n * 1000, sn * 1000, e * 1000, se * 1000, u * 1000, su * 1000))

        return self.format_str.format(*params.tolist())

    def get_design_ts(self, ts):

        A = np.zeros((ts.size, self.terms))

        for p in np.arange(self.terms):
            A[:, p] = np.power(ts - self.p.t_ref, p)

        return A


class Design(np.ndarray):

    def __new__(subtype, Linear, Jumps, Periodic, dtype=float, buffer=None, offset=0, strides=None, order=None):
        # Create the ndarray instance of our type, given the usual
        # ndarray input arguments.  This will call the standard
        # ndarray constructor, but return an object of our type.
        # It also triggers a call to InfoArray.__array_finalize__

        shape = (Linear.design.shape[0], Linear.param_count + Jumps.param_count() + Periodic.param_count)
        A = super(Design, subtype).__new__(subtype, shape, dtype, buffer, offset, strides, order)

        A[:, Linear.column_index] = Linear.design

        # determine the column_index for all objects
        col_index = Linear.param_count

        for jump in Jumps.table:
            # save the column index
            if jump.fit:
                jump.column_index = np.arange(col_index, col_index + jump.param_count)
                # print(jump)
                # assign the portion of the design matrix
                A[:, jump.column_index] = jump.design
                # increment the col_index
                col_index += jump.param_count

        Periodic.column_index = np.arange(col_index, col_index + Periodic.param_count)

        A[:, Periodic.column_index] = Periodic.design

        # save the object list
        A.objects = (Linear, Jumps, Periodic)

        # save the number of total parameters
        A.linear_params = Linear.param_count
        A.jump_params = Jumps.param_count()
        A.periodic_params = Periodic.param_count

        A.params = Linear.param_count + Jumps.param_count() + Periodic.param_count

        # save the constraints matrix
        A.constrains = Jumps.constrains

        # Finally, we must return the newly created object:
        return A

    def __call__(self, ts=None, constrains=False):

        if ts is None:
            if constrains and self.constrains.size:
                A = self.copy()
                # resize matrix (use A.resize so that it fills with zeros)
                A.resize((self.shape[0] + self.constrains.shape[0], self.shape[1]), refcheck=False)
                # apply constrains
                A[-self.constrains.shape[0]:, self.jump_params] = self.constrains
                return A
            else:
                return self
        else:
            A = np.array([])

            for obj in self.objects:
                tA = obj.get_design_ts(ts)
                if A.size:
                    A = np.column_stack((A, tA)) if tA.size else A
                else:
                    A = tA

            return A

    def get_l(self, L, constrains=False):

        if constrains and self.constrains.size:
            tL = L.copy()
            tL.resize((L.shape[0] + self.constrains.shape[0]), refcheck=False)
            return tL

        return L

    def get_p(self, constrains=False):
        # return a weight matrix full of ones with or without the extra elements for the constrains
        return np.ones(self.shape[0] if not constrains else \
                           (self.shape[0] + self.constrains.shape[0]))

    def remove_constrains(self, v):
        # remove the constrains to whatever vector is passed
        if self.constrains.size:
            return v[0:-self.constrains.shape[0]]
        else:
            return v


class ETM:

    def __init__(self, cnn, soln, no_model=False, FitEarthquakes=True, FitGenericJumps=True, FitPeriodic=True,
                 plotit=False, ignore_db_params=False, models=(), plot_remove_jumps=False,
                 plot_polynomial_removed=False):

        # to display more verbose warnings
        # warnings.showwarning = self.warn_with_traceback

        self.C = np.array([])
        self.S = np.array([])
        self.F = np.array([])
        self.R = np.array([])
        self.P = np.array([])
        self.factor = np.array([])
        self.covar = np.zeros((3, 3))
        self.A  = None
        # DDG: was missing to initialize As if A ends up being None
        self.As = None
        self.param_origin = ESTIMATION
        self.soln = soln
        self.no_model = no_model
        self.FitEarthquakes = FitEarthquakes
        self.FitGenericJumps = FitGenericJumps
        self.FitPeriodic = FitPeriodic
        self.plot_jumps_removed = plot_remove_jumps
        self.plot_polynomial_removed = plot_polynomial_removed

        self.NetworkCode = soln.NetworkCode
        self.StationCode = soln.StationCode

        self.models = models
        self.ce_pos = np.array([[0.0], [0.0], [0.0]])

        stn_id = stationID(self)

        logger.info('Creating ETM object for %s' % stn_id)
        logger.info('First obs %.3f last obs %.3f nobs %i' % (np.min(soln.t), np.max(soln.t), soln.t.size))

        # save the function objects
        self.Linear = Polynomial(cnn, soln.NetworkCode, soln.StationCode, self.soln, self.soln.t, models=models)
        self.Periodic = Periodic(cnn, soln.NetworkCode, soln.StationCode, self.soln, self.soln.t, FitPeriodic)
        self.Jumps = JumpTable(cnn, soln.NetworkCode, soln.StationCode, self.soln, self.soln.t, FitEarthquakes,
                               FitGenericJumps, models)

        # calculate the hash value for this station
        # now hash also includes the timestamp of the last time pyETM was modified.
        self.hash = soln.hash

        # anything less than four is not worth it
        if soln.solutions > 4 and not no_model:

            # to obtain the parameters
            self.A = Design(self.Linear, self.Jumps, self.Periodic)

            # check if problem can be solved!
            if self.A.shape[1] >= soln.solutions:
                self.A = None
            else:
                self.As = self.A(soln.ts)
        else:
            logger.info('Less than 4 solutions, cannot calculate ETM')

        # no offset applied
        self.L = np.array([self.soln.x, self.soln.y, self.soln.z])

        # reduced to x y z coordinate of the station
        self.l = self.rotate_2neu(np.array([self.soln.x - self.soln.auto_x,
                                            self.soln.y - self.soln.auto_y,
                                            self.soln.z - self.soln.auto_z]))

        # remove the interseismic component if passed (here, objects already initialized)
        if len(models) > 0:
            # reading data from the database and invoking postseismic or interseismic parameters can cause problems
            logger.info('Ignoring database parameters because models were invoked')
            ignore_db_params = True
            for model in models:
                self.l -= model.eval(self.soln.t)
            # for consistency, transform the XYZ coordinates as well
            self.L = self.rotate_2xyz(self.l) + np.array([self.soln.auto_x, self.soln.auto_y, self.soln.auto_z])

        self.run_adjustment(cnn, self.l, self.soln, ignore_db_params=ignore_db_params)

        # save the parameters to the db
        if self.A is not None and not ignore_db_params:
            self.save_parameters(cnn)

        # after running the adjustment, introduce jump parameters removed (if postseismic passed)
        # if postseismic is not None:
        #    self.display_postseismic_params(postseismic)

        if plotit:
            self.plot()

    def display_postseismic_params(self, postseismic):

        for event in postseismic:
            jump = None
            added = False
            for j in self.Jumps.table:
                # applied to the first jump with a matching date
                if j.date == event['date'] and j.p.jump_type in (CO_SEISMIC_JUMP_DECAY,
                                                                 CO_SEISMIC_DECAY, CO_SEISMIC_JUMP):
                    jump = j
                    # force M (for Manual, i.e. passing postseismic as an argument to pyETM) in action
                    jump.action = 'M'
                    if not jump.fit and j.p.jump_type in (CO_SEISMIC_JUMP_DECAY, CO_SEISMIC_JUMP):
                        # if this jump was not set to be adjusted, then apply dummy parameters for the jump if
                        # type is CO_SEISMIC_JUMP or CO_SEISMIC_JUMP_DECAY (the rest of the params filled later)
                        jump.p.params = np.zeros((3, 1))

                    if j.p.jump_type is CO_SEISMIC_JUMP:
                        # force back CO_SEISMIC_JUMP_DECAY
                        jump.p.jump_type = CO_SEISMIC_JUMP_DECAY

                    # just for display purposes
                    jump.fit = True
                    # stop iterating the jump table, we found a matching jump
                    break

            if jump is None:
                # if event was not found, then add it manually
                added = True
                jump = CoSeisJump(self.NetworkCode, self.StationCode, self.soln, self.soln.t, event['date'],
                                  np.array(event['relaxation']), 'mag=??',
                                  CO_SEISMIC_JUMP_DECAY if self.soln.t.min() < event['date'].fyear else
                                  CO_SEISMIC_DECAY,
                                  0., 'M', fit=True)

            for i, r in enumerate(event['relaxation']):
                if r not in jump.p.relaxation:
                    # add the relaxation back
                    jump.p.relaxation = np.append(jump.p.relaxation, r)
                    jump.nr = jump.p.relaxation.shape[0]
                    # add it to the param count
                    jump.param_count += 1
                # put the parameter that comes from the model
                params = np.array(event['amplitude']).reshape((3, 1))
                if jump.p.params.size > 0:
                    jump.p.params = np.append(jump.p.params, params, axis=1)
                else:
                    jump.p.params = params

                if added:
                    self.Jumps.table.append(jump)
                    self.Jumps.table.sort()

    def apply_postseismic_model(self, postseismic):

        # evaluate the model

        t = self.soln.t

        for event in postseismic:
            date = event['date']
            logger.info('Applying postseisic model for event %s' % date.yyyyddd())
            # postseismic parameters passed, check each relaxation to see if one has to be removed
            for i, r in enumerate(event['relaxation']):
                # for each relaxation, evaluate the model to subtract it from self.l
                hl = np.zeros((t.shape[0],))
                pmodel = np.zeros((3, t.shape[0]))
                hl[t > date.fyear] = np.log10(1. + (t[t > date.fyear] - date.fyear) / r)
                # apply the amplitudes
                for j in range(3):
                    amp = event['amplitude'][i][j]
                    pmodel[j] += amp * hl

                self.l -= pmodel

    def run_adjustment(self, cnn, l, soln, ignore_db_params=False):
        import pandas as pd
        if self.A is not None:
            # try to load the last ETM solution from the database

            etm_objects = cnn.query_float('SELECT * FROM etms WHERE "NetworkCode" = \'%s\' '
                                          'AND "StationCode" = \'%s\' AND soln = \'%s\' AND stack = \'%s\''
                                          % (self.NetworkCode, self.StationCode, self.soln.type,
                                             self.soln.stack_name), as_dict=True)

            # DDG: Attention: it is not always possible to retrieve the parameters from the database using the hash
            # strategy. The jump table is determined and their hash values calculated. The fit attribute goes into the
            # hash value. When an unrealistic jump is detected, the jump is removed from the fit and the final
            # parameters are saved without this jump. Thus, when loading the object, the jump will be added to fit but
            # it will not be present in the database.
            db_hash_sum = sum(obj['hash'] for obj in etm_objects)
            jumps_hash = sum(o.p.hash for o in self.Jumps.table if o.fit)
            ob_hash_sum = self.Periodic.p.hash + self.Linear.p.hash + self.hash + jumps_hash
            cn_object_sum = len([o.p.hash for o in self.Jumps.table if o.fit]) + 2

            # -1 to account for the var_factor entry
            if len(etm_objects) - 1 == cn_object_sum and db_hash_sum == ob_hash_sum and not ignore_db_params:
                logger.info('ETM -> Loading parameters from database (db hash %i; ob hash %i)'
                            % (db_hash_sum, ob_hash_sum))
                # load the parameters from th db
                self.load_parameters(etm_objects, l)
                # signal the outside world that the parameters were loaded from the database (no need to save them)
                self.param_origin = DATABASE
            else:
                logger.info('ETM -> Estimating parameters (db hash %i; ob hash %i)'
                            % (db_hash_sum, ob_hash_sum))
                # signal the outside world that the parameters were estimated (and need to be saves)
                self.param_origin = ESTIMATION
                # purge table and recompute (only if MODELS not invoked!)
                if len(self.models) == 0:
                    cnn.query('DELETE FROM etms WHERE "NetworkCode" = \'%s\' AND '
                              '"StationCode" = \'%s\' AND soln = \'%s\' AND stack = \'%s\''
                              % (self.NetworkCode, self.StationCode, self.soln.type, self.soln.stack_name))

                if self.soln.type == 'dra':
                    # if the solution is of type 'dra', delete the excluded solutions
                    cnn.query('DELETE FROM gamit_soln_excl WHERE "NetworkCode" = \'%s\' AND '
                              '"StationCode" = \'%s\'' % (self.NetworkCode, self.StationCode))

                # use the default parameters from the objects
                t_ref = self.Linear.p.t_ref
                j = 0
                do_again = False
                while j < 10:
                    c = []
                    f = []
                    s = []
                    r = []
                    p = []
                    factor = []
                    for i in range(3):
                        x, sigma, index, residuals, fact, w, cova = self.adjust_lsq(self.A, l[i])

                        c.append(x)
                        s.append(sigma)
                        f.append(index)
                        r.append(residuals)
                        factor.append(fact)
                        p.append(w)

                        # DDG: uncomment to see covariance information on the screen
                        # dinv = np.diag(1 / np.sqrt(np.diag(cova)))
                        # corr = dinv @ cova @ dinv
                        # print('%i CORR ==============' % i)
                        # df = pd.DataFrame(corr, columns=range(x.size), index=range(x.size))
                        # print(df)
                        # print('%i COVA ==============' % i)
                        # df = pd.DataFrame(cova, columns=range(x.size), index=range(x.size))
                        # print(df)

                    self.C = np.array(c)
                    self.S = np.array(s)
                    self.F = np.array(f)
                    self.R = np.array(r)
                    self.factor = np.array(factor)
                    self.P = np.array(p)

                    # load_parameters to the objects
                    self.Linear.load_parameters(self.C, self.S, t_ref)
                    self.Jumps.load_parameters(self.C, self.S)
                    self.Periodic.load_parameters(params=self.C, sigmas=self.S)

                    # determine if any jumps are unrealistic
                    # DDG Feb-7-2022: to determine if a jump is unrealistic, we check that the postseismic deformation
                    # is > 1 meter in amplitude. This value is a priori and a study should be done to determine a better
                    # estimate of what this value should be.
                    for jump in self.Jumps.table:
                        if jump.fit and \
                                jump.p.jump_type in (CO_SEISMIC_JUMP_DECAY,
                                                     CO_SEISMIC_DECAY) and \
                                np.any(np.abs(jump.p.params[:, -jump.nr:]) > 4):
                            # unrealistic, remove
                            jump.remove_from_fit()
                            do_again = True
                            logger.info('ETM -> Unrealistic jump detected (%s : %s) on %s, removing and redoing fit'
                                        % (np.array_str(jump.p.params[:, -jump.nr:].flatten(), precision=1),
                                           type_dict[jump.p.jump_type], jump.date.yyyyddd()))

                    if not do_again:
                        break
                    else:
                        self.A = Design(self.Linear, self.Jumps, self.Periodic)
                        if soln:
                            self.As = self.A(soln.ts)
                        j += 1

            # DDG: new method to compute the minimum-entropy sigma for constant velocity
            entropy_sigmas = self.entropy_sigma()
            # do not replace values if sigmas come back with 0
            # DDG: if self.Linear.p.sigmas.shape[1] is not > 1, then interseismic model applied, do not use sigmas
            if np.all(entropy_sigmas > 0) and self.Linear.p.sigmas.shape[1] > 1:
                self.Linear.p.sigmas[:, 1] = entropy_sigmas
            # load the covariances using the correlations
            self.process_covariance()
            # get conventional epoch coordinates
            # calculate the conventional epoch position
            date = pyDate.Date(fyear=self.Linear.p.t_ref)
            # save the CE postion
            self.ce_pos, _, _, _ = self.get_xyz_s(date.year, date.doy, force_model=True)

        else:
            logger.info('ETM -> Empty design matrix')

    def get_data_segments(self, tolerance):
        # find the indices of start of the data gaps
        gaps = np.where(np.diff(self.soln.mjd) > tolerance)[0]

        segments = []
        previous = 0
        # loop though the gaps and produce an array of the data segments
        # if no gaps, this loop does not do anything and the results is a segment from 0 to -1 (end)
        for gap in gaps:
            segments.append([previous, gap])
            previous = gap + 1

        # the last segment of data goes from previous to the end of the array
        segments.append([previous, self.soln.mjd.size - 1])

        return segments

    def entropy_sigma(self):
        # DDG: new method to compute the minimum-entropy sigma
        # see Saleh, J. (2024). Minimum-entropy velocity estimation from GPS position time series.

        # first get the time series gaps using
        segments = self.get_data_segments(60)
        # total duration of the time series
        T = np.max(self.soln.t) - np.min(self.soln.t)

        # print(segments)
        sv = np.zeros((3, ))
        # loop three times: one for N-E-U
        for i, r in enumerate(self.R):
            # assume residuals already have minimum-entropy
            # element in array that are not outliers (M = 1)
            H = np.zeros(len(segments))
            P = np.zeros(len(segments))
            for j, s in enumerate(segments):
                # select the segment of data
                dT = self.soln.t[s[1]] - self.soln.t[s[0]]
                # do not process this segment if less than 50 points
                if s[1] - s[0] < 50:
                    continue
                # loop through each segment and get the outliers out
                f = self.F[i][s[0]:s[1]]
                x = r[s[0]:s[1]][f]
                # elements being considered
                N = np.sum(f)
                # to build eq 9 from Saleh, J. (2024)
                i_p1 = np.arange(N) + 1
                i_m1 = np.arange(N) - 1
                i_p1[i_p1 > N - 1] = N - 1
                i_m1[i_m1 < 0] = 0

                # sort the residual vector
                xr = np.sort(x)
                # compute the entropy
                H[j] = 1 / N * np.sum(np.log(N / 2 * (xr[i_p1] - xr[i_m1]))) / np.log(2)
                P[j] = dT/T

            if len(segments) > 1:
                H = np.sum(H * P)

            if H != 0:
                sp = np.power(2, H - 2.0471)
                sv[i] = sp / T
            else:
                sv[i] = 0

            # print(' entropy 3 sigma: %f mm/yr entropy: %f' % (float(sv[i]) * 1000 * 3, H))
            # print(' regular 3 sigma: %f mm/yr' % (self.Linear.p.sigmas[i][1] * 1000 * 3))

        return sv

    def process_covariance(self):

        cov = np.zeros((3, 1))

        # save the covariance between N-E, E-U, N-U
        f = self.F[0] * self.F[1] * self.F[2]

        # load the covariances using the correlations
        cov[0] = np.corrcoef(self.R[0][f], self.R[1][f])[0, 1] * self.factor[0] * self.factor[1]
        cov[1] = np.corrcoef(self.R[1][f], self.R[2][f])[0, 1] * self.factor[1] * self.factor[2]
        cov[2] = np.corrcoef(self.R[0][f], self.R[2][f])[0, 1] * self.factor[0] * self.factor[2]

        # build a variance-covariance matrix
        self.covar = np.diag(np.square(self.factor))

        self.covar[0, 1] = cov[0]
        self.covar[1, 0] = cov[0]
        self.covar[2, 1] = cov[1]
        self.covar[1, 2] = cov[1]
        self.covar[0, 2] = cov[2]
        self.covar[2, 0] = cov[2]

        if not self.isPD(self.covar):
            self.covar = self.nearestPD(self.covar)

    def save_excluded_soln(self, cnn):

        # only save if something to save
        if self.F.size > 0:
            for date, f, r in zip(self.soln.date,
                                  np.logical_and(np.logical_and(self.F[0], self.F[1]), self.F[2]),
                                  np.sqrt(np.sum(np.square(self.R), axis=0))):

                if not cnn.query_float('SELECT * FROM gamit_soln_excl WHERE "NetworkCode" = \'%s\' AND '
                                       '"StationCode" = \'%s\' AND "Project" = \'%s\' AND "Year" = %i AND "DOY" = %i'
                                       % (self.NetworkCode, self.StationCode, self.soln.stack_name,
                                          date.year, date.doy)) \
                        and not f:
                    cnn.query('INSERT INTO gamit_soln_excl ("NetworkCode", "StationCode", "Project", "Year", "DOY", '
                              'residual) VALUES (\'%s\', \'%s\', \'%s\', %i ,%i, %.4f)'
                              % (self.NetworkCode, self.StationCode, self.soln.stack_name, date.year, date.doy, r))

    def save_parameters(self, cnn):
        # only save the parameters when they've been estimated, not when loaded from database
        if self.param_origin == ESTIMATION:
            # insert linear parameters
            cnn.insert('etms', **to_postgres(self.Linear.p.toDict()))

            # insert jumps
            for jump in self.Jumps.table:
                if jump.fit:
                    cnn.insert('etms', **to_postgres(jump.p.toDict()))

            # insert periodic params
            cnn.insert('etms', **to_postgres(self.Periodic.p.toDict()))

            # save the variance factors
            cnn.query('INSERT INTO etms ("NetworkCode", "StationCode", soln, object, params, hash, stack) VALUES '
                      '(\'%s\', \'%s\', \'%s\', \'var_factor\', \'%s\', %i, \'%s\')'
                      % (self.NetworkCode, self.StationCode, self.soln.type, to_postgres(self.factor),
                         self.hash, self.soln.stack_name))

    def plot(self, pngfile=None, t_win=None, residuals=False, plot_missing=True,
             ecef=False, plot_outliers=True, plot_auto_jumps=False, fileio=None):

        import matplotlib.pyplot as plt

        # definitions
        m = []
        if ecef:
            labels = ('X [mm]', 'Y [mm]', 'Z [mm]')
        else:
            labels = (LABEL('north') + ' [mm]',
                      LABEL('east') + ' [mm]',
                      LABEL('up') + ' [mm]')

        # to remove jumps and polynomial terms (if requested)
        oj = np.zeros((3, self.soln.t.shape[0]))
        op = np.zeros((3, self.soln.t.shape[0]))
        # get filtered observations
        if self.A is not None:
            filt = self.F[0] * self.F[1] * self.F[2]

            # if jumps are to be removed, evaluate them
            mj = np.zeros((3, self.soln.ts.shape[0]))
            if self.plot_jumps_removed:
                for j in [j for j in self.Jumps.table if j.p.jump_type is not CO_SEISMIC_DECAY and j.fit]:
                    if j.p.jump_type is CO_SEISMIC_JUMP_DECAY:
                        # if jump is jump + decay, just remove the jump, not the decay
                        a = j.eval(self.soln.ts)
                        mj = mj + np.array([(np.dot(a[:, 0], j.p.params[i, 0])) * 1000 for i in range(3)])
                        a = j.eval(self.soln.t)
                        oj = oj + np.array([(np.dot(a[:, 0], j.p.params[i, 0])) * 1000 for i in range(3)])
                    else:
                        # jump has no decay, remove everything
                        mj = mj + np.array([(np.dot(j.eval(self.soln.ts), j.p.params[i])) * 1000 for i in range(3)])
                        oj = oj + np.array([(np.dot(j.eval(self.soln.t), j.p.params[i])) * 1000 for i in range(3)])

            mp = np.zeros((3, self.soln.ts.shape[0]))
            if self.plot_polynomial_removed:
                mp = [(np.dot(self.Linear.get_design_ts(self.soln.ts), self.Linear.p.params[i])) * 1000
                      for i in range(3)]
                op = [(np.dot(self.Linear.get_design_ts(self.soln.t) , self.Linear.p.params[i])) * 1000
                      for i in range(3)]

            m = [(np.dot(self.As, self.C[i])) * 1000 - mj[i] - mp[i]
                 for i in range(3)]
        else:
            filt = np.ones(self.soln.x.shape[0], dtype=bool)

        # rotate to NEU
        if ecef:
            lneu = self.rotate_2xyz(self.l * 1000)
        else:
            lneu = self.l * 1000 - oj - op

        # determine the window of the plot, if requested
        if t_win is not None:
            if type(t_win) is tuple:
                # data range, with possibly a final value
                if len(t_win) == 1:
                    t_win = (t_win[0], self.soln.t.max())
            else:
                # approximate a day in fyear
                t_win = (self.soln.t.max() - t_win / 365.25, self.soln.t.max())

        # new behaviour: plots the time series even if there is no ETM fit

        if self.A is not None:

            # create the axis
            if plot_outliers:
                f, axis = plt.subplots(nrows=3, ncols=2, sharex=True, figsize=(16, 10))  # type: plt.subplots
                axis_vect = (axis[0][0], axis[1][0], axis[2][0])
            else:
                f, axis = plt.subplots(nrows=3, ncols=1, sharex=True, figsize=(16, 10))  # type: plt.subplots
                axis_vect = (axis[0], axis[1], axis[2])

            # rotate modeled ts
            if not ecef:
                mneu = m
                rneu = self.R
                fneu = self.factor * 1000
            else:
                mneu = self.rotate_2xyz(m)
                # rotate residuals
                rneu = self.rotate_2xyz(self.R)
                fneu = np.sqrt(np.diag(self.rotate_sig_cov(covar=self.covar))) * 1000

            # ################# FILTERED PLOT #################

            f.suptitle(LABEL('station') + ' %s (%s %.2f%%/%.2f%%) lat: %.5f lon: %.5f\n'
                                          '%s\n%s\n'
                                          'NEU wrms [mm]: %5.2f %5.2f %5.2f %s %s' %
                       (stationID(self),
                        self.soln.stack_name.upper(),
                        self.soln.completion,
                        self.soln.span_completion,
                        self.soln.lat,
                        self.soln.lon,
                        self.Linear.print_parameters(self.ce_pos, self.soln.lat, self.soln.lon),
                        self.Periodic.print_parameters(),
                        fneu[0],
                        fneu[1],
                        fneu[2],
                        '' if not self.plot_jumps_removed else LABEL('jumps removed'),
                        '' if not self.plot_polynomial_removed else LABEL('polynomial removed')),
                       fontsize=9, family='monospace')

            table_n, table_e, table_u = self.Jumps.print_parameters()
            tables = (table_n, table_e, table_u)

            for i, ax in enumerate(axis_vect):

                # plot filtered time series
                if not residuals:
                    ax.plot(self.soln.t[filt], lneu[i][filt], 'ob', markersize=2)
                    ax.plot(self.soln.ts, mneu[i], 'r')
                    # error bars
                    ax.plot(self.soln.ts, mneu[i] - fneu[i] * LIMIT, 'b', alpha=0.1)
                    ax.plot(self.soln.ts, mneu[i] + fneu[i] * LIMIT, 'b', alpha=0.1)
                    ax.fill_between(self.soln.ts, mneu[i] - fneu[i] * LIMIT, mneu[i] + fneu[i] * LIMIT,
                                    antialiased=True, alpha=0.2)
                else:
                    ax.plot(self.soln.t[filt], rneu[i][filt] * 1000, 'ob', markersize=2)
                    # error bars
                    ax.plot(self.soln.ts, - np.repeat(fneu[i], self.soln.ts.shape[0]) * LIMIT, 'b', alpha=0.1)
                    ax.plot(self.soln.ts, np.repeat(fneu[i], self.soln.ts.shape[0]) * LIMIT, 'b', alpha=0.1)
                    ax.fill_between(self.soln.ts, -fneu[i] * LIMIT, fneu[i] * LIMIT, antialiased=True, alpha=0.2)

                ax.grid(True)

                # labels
                ax.set_ylabel(labels[i])
                p = ax.get_position()
                f.text(0.0025, p.y0, tables[i], fontsize=8, family='monospace')

                # window data
                self.set_lims(t_win, plt, ax)

                # plot jumps
                self.plot_jumps(ax, plot_auto_jumps)

            # ################# OUTLIERS PLOT #################
            if plot_outliers:
                for i, ax in enumerate((axis[0][1], axis[1][1], axis[2][1])):
                    ax.plot(self.soln.t, lneu[i], 'oc', markersize=2)
                    ax.plot(self.soln.t[filt], lneu[i][filt], 'ob', markersize=2)
                    ax.plot(self.soln.ts, mneu[i], 'r')
                    # error bars
                    ax.plot(self.soln.ts, mneu[i] - fneu[i] * LIMIT, 'b', alpha=0.1)
                    ax.plot(self.soln.ts, mneu[i] + fneu[i] * LIMIT, 'b', alpha=0.1)
                    ax.fill_between(self.soln.ts, mneu[i] - fneu[i] * LIMIT, mneu[i] + fneu[i] * LIMIT,
                                    antialiased=True, alpha=0.2)

                    self.set_lims(t_win, plt, ax)

                    ax.set_ylabel(labels[i])

                    ax.grid(True)

                    if plot_missing:
                        self.plot_missing_soln(ax)

            f.subplots_adjust(left=0.21)

        else:

            f, axis = plt.subplots(nrows=3, ncols=1, sharex=True, figsize=(16, 10))  # type: plt.subplots

            f.suptitle(LABEL('station') + ' %s (%s %.2f%%/%.2f%%) lat: %.5f lon: %.5f'
                       % (stationID(self), self.soln.type.upper(), self.soln.completion,
                          self.soln.span_completion, self.soln.lat, self.soln.lon) +
                       '\n' + LABEL('not_enough'), fontsize=9, family='monospace')

            for i, ax in enumerate((axis[0], axis[1], axis[2])):
                ax.plot(self.soln.t, lneu[i], 'ob', markersize=2)

                ax.set_ylabel(labels[i])

                ax.grid(True)

                self.set_lims(t_win, plt, ax)

                self.plot_jumps(ax, plot_auto_jumps)

                if plot_missing:
                    self.plot_missing_soln(ax)

        # save / show plot
        if pngfile is not None:
            plt.savefig(pngfile)
            plt.close()
        elif fileio is not None:
            plt.savefig(fileio, format='png')
            # plt.show()
            fileio.seek(0)  # rewind to beginning of file
            plt.close()
            return base64.b64encode(fileio.getvalue()).decode()
        else:
            self.f = f
            self.picking = False
            self.plt = plt
            axprev = plt.axes([0.85, 0.01, 0.08, 0.055])
            bcut = Button(axprev, 'Add jump', color='red', hovercolor='green')
            bcut.on_clicked(self.enable_picking)
            plt.show()
            plt.close()

    def onpick(self, event):

        import dbConnection

        self.f.canvas.mpl_disconnect(self.cid)
        self.picking = False
        print('Epoch: %s' % pyDate.Date(fyear=event.xdata).yyyyddd())
        jtype = int(eval(input(' -- Enter type of jump (0 = mechanic; 1 = geophysical): ')))
        if jtype == 1:
            relx = eval(input(' -- Enter relaxation (e.g. 0.5, 0.5,0.01): '))
        operation = str(input(' -- Enter operation (+, -): '))
        print(' >> Jump inserted')

        # now insert the jump into the db
        cnn = dbConnection.Cnn('gnss_data.cfg')

        self.plt.close()

        # reinitialize ETM

        # wait for 'keep' or 'undo' command

    def enable_picking(self, event):
        if not self.picking:
            print('Entering picking mode')
            self.picking = True
            self.cid = self.f.canvas.mpl_connect('button_press_event', self.onpick)
        else:
            print('Disabling picking mode')
            self.picking = False
            self.f.canvas.mpl_disconnect(self.cid)

    def plot_hist(self, pngfile=None, fileio=None):

        import matplotlib.pyplot as plt
        import matplotlib.mlab as mlab
        from scipy.stats import norm
        from matplotlib.patches import Ellipse

        labels = (LABEL('north') + ' [mm]',
                  LABEL('east') + ' [mm]',
                  LABEL('up') + ' [mm]')

        if self.A is not None:

            filt = self.F[0] * self.F[1] * self.F[2]

            f, axis = plt.subplots(nrows=2, ncols=2, figsize=(16, 10))  # type: plt.subplots

            f.suptitle(LABEL('station') + ' %s (%s %.2f%%/%.2f%%) lat: %.5f lon: %.5f\n'
                                          'VAR (N E U)      : %s\n'
                                          'COV (N-E N-U E-U): %s'
                       % (stationID(self),
                          self.soln.type.upper(), self.soln.completion,
                          self.soln.span_completion, self.soln.lat, self.soln.lon,
                          ' '.join('%10.3e' % i for i in np.diag(self.covar)),
                          ' '.join('%10.3e' % i for i in [self.covar[0, 1], self.covar[0, 2], self.covar[1, 2]])),
                       fontsize=9, family='monospace')

            n = np.sqrt(np.sum(self.R ** 2, axis=0))
            N = self.R[0][n <= 0.05] * 1000
            E = self.R[1][n <= 0.05] * 1000
            U = self.R[2][n <= 0.05] * 1000

            # N-E residuals and error ellipse
            ax = axis[0][0]
            ax.plot(E, N, 'ob', markersize=2)
            # ax.plot(E[filt], N[filt], 'ob', markersize=2)
            # ax.plot(E[np.logical_not(filt)], N[np.logical_not(filt)], 'oc', markersize=2)

            # process the covariance matrix
            c = self.covar[0:2, 0:2]
            c[1, 1], c[0, 0] = c[0, 0], c[1, 1]
            w, v = np.linalg.eigh(self.covar[0:2, 0:2])
            order = w.argsort()[::-1]
            w, v = w[order], v[:, order]
            theta = np.degrees(np.arctan2(*v[:, 0][::-1]))

            ellipse = Ellipse((np.mean(self.R[1][filt]),
                               np.mean(self.R[1][filt])),
                              width=2. * np.sqrt(w[0]) * 2.5 * 1000,
                              height=2. * np.sqrt(w[1]) * 2.5 * 1000,
                              angle=theta,
                              facecolor='none',
                              edgecolor='red',
                              zorder=3,
                              label=r'$2.5\sigma$')
            ax.add_patch(ellipse)
            ax.grid(True)
            ax.set_ylabel(labels[0])
            ax.set_xlabel(labels[1])
            ax.set_title("%s %s-%s" % (LABEL('residual plot'), LABEL('north'), LABEL('east')))
            ax.axis('equal')
            f.canvas.draw()
            ax.legend()
            nn = ax.get_ylim()
            ee = ax.get_xlim()

            # N histogram
            ax = axis[0][1]
            # (mu, sigma) = norm.fit(N)
            n, bins, patches = ax.hist(N, 200, alpha=0.75, facecolor='blue', orientation='horizontal')
            # y = mlab.normpdf(bins, mu, sigma)
            # ax.plot(y, bins, 'r--', linewidth=2)
            ax.grid(True)
            ax.set_xlabel(LABEL('frequency'))
            ax.set_ylabel(LABEL('N residuals') + ' [mm]')
            ax.set_title(LABEL('histogram plot') + ' ' + LABEL('north'))
            ax.set_ylim(nn)

            # E histogram
            ax = axis[1][0]
            # (mu, sigma) = norm.fit(E)
            n, bins, patches = ax.hist(E, 200, alpha=0.75, facecolor='blue')
            # y = mlab.normpdf(bins, mu, sigma)
            # ax.plot(bins, y, 'r--', linewidth=2)
            ax.grid(True)
            ax.set_ylabel(LABEL('frequency'))
            ax.set_xlabel(LABEL('E residuals') + ' [mm]')
            ax.set_title(LABEL('histogram plot') + ' ' + LABEL('east'))
            ax.set_xlim(ee)

            # Up histogram
            ax = axis[1][1]
            # (mu, sigma) = norm.fit(U)
            n, bins, patches = ax.hist(U, 200, alpha=0.75, facecolor='blue')
            # y = mlab.normpdf(bins, mu, sigma)
            # ax.plot(bins, y, 'r--', linewidth=2)
            ax.grid(True)
            ax.set_ylabel(LABEL('frequency'))
            ax.set_xlabel(LABEL('U residuals') + ' [mm]')
            ax.set_title(LABEL('histogram plot') + ' ' + LABEL('up'))

            # residuals = np.sqrt(np.square(L[0]) + np.square(L[1]) + np.square(L[2])) - \
            #            np.sqrt(np.square(np.dot(self.A, self.C[0])) + np.square(np.dot(self.A, self.C[1])) +
            #                    np.square(np.dot(self.A, self.C[2])))

            # (mu, sigma) = norm.fit(residuals)

            # n, bins, patches = plt.hist(residuals, 200, normed=1, alpha=0.75, facecolor='blue')

            # y = mlab.normpdf(bins, mu, sigma)
            # plt.plot(bins, y, 'r--', linewidth=2)
            # plt.title(r'$\mathrm{Histogram\ of\ residuals (mm):}\ \mu=%.3f,\ \sigma=%.3f$' % (mu*1000, sigma*1000))
            # plt.grid(True)

            if pngfile is not None:
                plt.savefig(pngfile)
                plt.close()
            elif fileio is not None:
                plt.savefig(fileio, format='png')
                # plt.show()
                fileio.seek(0)  # rewind to beginning of file
                plt.close()
                return base64.b64encode(fileio.getvalue())
            else:
                plt.show()
                plt.close()

    @staticmethod
    def autoscale_y(ax, margin=0.1):
        """This function rescales the y-axis based on the data that is visible given the current xlim of the axis.
        ax -- a matplotlib axes object
        margin -- the fraction of the total height of the y-data to pad the upper and lower ylims"""

        def get_bottom_top(line):
            xd = line.get_xdata()
            yd = line.get_ydata()
            lo, hi = ax.get_xlim()
            y_displayed = yd[((xd > lo) & (xd < hi))]
            h = np.max(y_displayed) - np.min(y_displayed)
            bot = np.min(y_displayed) - margin * h
            top = np.max(y_displayed) + margin * h
            return bot, top

        lines = ax.get_lines()
        bot, top = np.inf, -np.inf

        for line in lines:
            new_bot, new_top = get_bottom_top(line)
            if new_bot < bot:
                bot = new_bot
            if new_top > top:
                top = new_top

        if bot == top:
            ax.autoscale(enable=True, axis='y', tight=False)
            #ax.autoscale(enable=False, axis='y', tight=False)
        else:
            ax.set_ylim(bot, top)

    def set_lims(self, t_win, plt, ax):

        if t_win is None:
            # turn on to adjust the limits, then turn off to plot jumps
            ax.autoscale(enable=True, axis='x', tight=False)
            #ax.autoscale(enable=False, axis='x', tight=False)
            ax.autoscale(enable=True, axis='y', tight=False)
            #ax.autoscale(enable=False, axis='y', tight=False)
        else:
            if t_win[0] == t_win[1]:
                t_win[0] = t_win[0] - 1. / 365.25
                t_win[1] = t_win[1] + 1. / 365.25

            plt.xlim(t_win)
            self.autoscale_y(ax)

    def plot_missing_soln(self, ax):

        # plot missing solutions
        for missing in self.soln.ts_ns:
            ax.axvline(missing, color=(1, 0, 1, 0.2), linewidth=1)

        # plot the position of the outliers
        for blunder in self.soln.ts_blu:
            ax.quiver((blunder, blunder), ax.get_ylim(), (0, 0), (-0.01, 0.01), scale_units='height',
                      units='height', pivot='tip', width=0.008, edgecolors='r')

    def plot_jumps(self, ax, plot_auto_jumps=False):

        for jump in self.Jumps.table:
            if jump.date < self.soln.date[0] or jump.date > self.soln.date[-1]:
                continue

            c = 'tab:gray'
            if not jump.fit:
                c = 'tab:gray'
            elif jump.p.jump_type == GENERIC_JUMP:
                c = 'c'
            elif jump.p.jump_type == ANTENNA_CHANGE:
                c = 'b'
            elif jump.p.jump_type == REFERENCE_FRAME_JUMP:
                c = 'tab:green'
            elif jump.p.jump_type == CO_SEISMIC_JUMP_DECAY:
                c = 'r'
            elif jump.p.jump_type == CO_SEISMIC_JUMP:
                c = 'tab:purple'
            elif jump.p.jump_type == CO_SEISMIC_DECAY:
                # DDG: now plot the decay start
                c = 'tab:orange'
            else:
                continue
            ax.axvline(jump.date.fyear, color=c, linestyle=':')

        if plot_auto_jumps:
            for jump in self.Jumps.auto_jumps:
                if jump.date < self.soln.date[0] or jump.date > self.soln.date[-1]:
                    continue

                c = 'tab:orange'
                ax.axvline(jump.date.fyear, color=c, linestyle='-.')

    def todictionary(self, time_series=False, model=False):
        # convert the ETM adjustment into a dictionary
        # optionally, output the whole time series and evaluated model as well

        L = self.l

        # start with the parameters
        etm = {
            'Network': self.NetworkCode,
            'Station': self.StationCode,
            'lat': self.soln.lat[0],
            'lon': self.soln.lon[0],
            'ref_x': self.soln.auto_x[0],
            'ref_y': self.soln.auto_y[0],
            'ref_z': self.soln.auto_z[0],
            'ce_x' : self.ce_pos[0][0],
            'ce_y': self.ce_pos[1][0],
            'ce_z': self.ce_pos[2][0],
            't_ref': self.Linear.p.t_ref if self.A is not None else 0.0,
            'Jumps': [to_list(jump.p.toDict()) for jump in self.Jumps.table],
            'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'localhost': platform.node(),
            'version': VERSION
        }

        if self.A is not None:
            etm['Polynomial'] = to_list(self.Linear.p.toDict())
            etm['Periodic'] = to_list(self.Periodic.p.toDict())

            etm['wrms'] = {'n': self.factor[0],
                           'e': self.factor[1],
                           'u': self.factor[2]}

            etm['xyz_covariance'] = self.rotate_sig_cov(covar=self.covar).tolist()
            etm['neu_covariance'] = self.covar.tolist()

        if time_series:
            etm['time_series'] = {
                't': np.array([self.soln.t.tolist(), self.soln.mjd.tolist()]).transpose().tolist(),
                'mjd': self.soln.mjd.tolist(),
                'x': self.soln.x.tolist(),
                'y': self.soln.y.tolist(),
                'z': self.soln.z.tolist(),
                'n': L[0].tolist(),
                'e': L[1].tolist(),
                'u': L[2].tolist(),

                'residuals': self.R.tolist(),
                'weights': self.P.transpose().tolist(),

                'model_neu': [] if self.A is None or not model else \
                    [(np.dot(self.As, self.C[i]).tolist()) for i in range(3)],

                'filter': [] if self.A is None else \
                    np.logical_and(np.logical_and(self.F[0], self.F[1]), self.F[2]).tolist()
            }

        return etm

    def get_xyz_s(self, year, doy, jmp=None, sigma_h=SIGMA_FLOOR_H, sigma_v=SIGMA_FLOOR_V, force_model=False):
        # this function find the requested epochs and returns an X Y Z and sigmas
        # jmp = 'pre' returns the coordinate immediately before a jump
        # jmp = 'post' returns the coordinate immediately after a jump
        # jmp = None returns either the coordinate before or after, depending on the time of the jump.

        # find this epoch in the t vector
        date = pyDate.Date(year=year, doy=doy)
        window = None

        for jump in self.Jumps.table:
            if jump.date == date and \
                    jump.p.jump_type in (GENERIC_JUMP, CO_SEISMIC_JUMP_DECAY, ANTENNA_CHANGE, CO_SEISMIC_JUMP) and \
                    jump.fit and \
                    np.sqrt(np.sum(np.square(jump.p.params[:, 0]))) > 0.02:

                window = jump.date
                # if no pre or post specified, then determine using the time of the jump
                if jmp is None:
                    if (jump.date.datetime().hour + jump.date.datetime().minute / 60.0) < 12:
                        jmp = 'post'
                    else:
                        jmp = 'pre'
                # use the previous or next date to get the APR
                # if jmp == 'pre':
                #    date -= 1
                # else:
                #    date += 1

        index = np.where(self.soln.mjd == date.mjd)[0]

        neu = np.zeros((3, 1))

        L = self.L
        ref_pos = np.array([self.soln.auto_x,
                            self.soln.auto_y,
                            self.soln.auto_z])

        if index.size and self.A is not None:
            # found a valid epoch in the t vector
            # now see if this epoch was filtered
            if np.all(self.F[:, index]) and force_model is False:
                # the coordinate is good
                xyz = L[:, index]
                sig = self.R[:, index]
                source = self.soln.stack_name.upper() + ' with ETM solution: good'

            else:
                # the coordinate is marked as bad
                # get the requested epoch from the ETM
                idt = np.argmin(np.abs(self.soln.ts - date.fyear))

                for i in range(3):
                    neu[i] = np.dot(self.As[idt, :], self.C[i])
                xyz = self.rotate_2xyz(neu) + ref_pos
                # Use the deviation from the ETM multiplied by 2.5 to estimate the error
                sig = 2.5 * self.R[:, index]
                source = self.soln.stack_name.upper() + ' with ETM solution: filtered'

        elif not index.size and self.A is not None:

            # the coordinate doesn't exist, get it from the ETM
            idt = np.argmin(np.abs(self.soln.ts - date.fyear))
            source = 'No ' + self.soln.stack_name.upper() + ' solution: ETM'

            for i in range(3):
                neu[i] = np.dot(self.As[idt, :], self.C[i])

            xyz = self.rotate_2xyz(neu) + ref_pos
            # since there is no way to estimate the error,
            # use the nominal sigma multiplied by 2.5
            sig = 2.5 * self.factor[:, np.newaxis]

        elif index.size and self.A is None:

            # no ETM (too few points), but we have a solution for the requested day
            xyz = L[:, index]
            # set the uncertainties in NEU by hand
            sig = np.array([[9.99], [9.99], [9.99]])
            source = self.soln.stack_name.upper() + ' solution, no ETM'

        else:
            # no ETM (too few points) and no solution for this day, get average
            source = 'No ' + self.soln.stack_name.upper() + ' solution, no ETM: mean coordinate'
            xyz = np.mean(L, axis=1)[:, np.newaxis]
            # set the uncertainties in NEU by hand
            sig = np.array([[9.99], [9.99], [9.99]])

        if self.A is not None:
            # get the velocity of the site
            if np.sqrt(np.square(self.Linear.p.params[0, 1]) +
                       np.square(self.Linear.p.params[1, 1]) +
                       np.square(self.Linear.p.params[2, 1])) > 0.2:
                # fast moving station! bump up the sigma floor
                sigma_h = 99.9
                sigma_v = 99.9
                source += '. fast moving station, bumping up sigmas'

        # apply floor sigmas
        sig = np.sqrt(np.square(sig) + np.square(np.array([[sigma_h], [sigma_h], [sigma_v]])))

        return xyz, sig, window, source

    def rotate_2neu(self, ecef):

        return np.array(ct2lg(ecef[0], ecef[1], ecef[2], self.soln.lat, self.soln.lon))

    def rotate_2xyz(self, neu):

        return np.array(lg2ct(neu[0], neu[1], neu[2], self.soln.lat, self.soln.lon))

    def rotate_sig_cov(self, sigmas=None, covar=None):

        if sigmas is None and covar is None:
            raise pyETMException('Error in rotate_sig_cov: must provide either sigmas or covariance matrix')

        R = rotlg2ct(self.soln.lat, self.soln.lon)

        if sigmas is not None:
            # build a covariance matrix based on sigmas
            sd = np.diagflat(np.square(sigmas))

            sd[0, 1] = self.covar[0, 1]
            sd[1, 0] = self.covar[1, 0]
            sd[2, 1] = self.covar[2, 1]
            sd[1, 2] = self.covar[1, 2]
            sd[0, 2] = self.covar[0, 2]
            sd[2, 0] = self.covar[2, 0]

            # check that resulting matrix is PSD:
            if not self.isPD(sd):
                sd = self.nearestPD(sd)

            sneu = np.dot(np.dot(R[:, :, 0], sd), R[:, :, 0].transpose())

            dneu = np.sqrt(np.diag(sneu))

        else:
            # covariance matrix given, assume it is a covariance matrix
            dneu = np.dot(np.dot(R[:, :, 0], covar), R[:, :, 0].transpose())

        return dneu

    def nearestPD(self, A):
        """Find the nearest positive-definite matrix to input

        A Python/Numpy port of John D'Errico's `nearestSPD` MATLAB code [1], which
        credits [2].

        [1] https://www.mathworks.com/matlabcentral/fileexchange/42885-nearestspd

        [2] N.J. Higham, "Computing a nearest symmetric positive semidefinite
        matrix" (1988): https://doi.org/10.1016/0024-3795(88)90223-6
        """

        B = (A + A.T) / 2
        _, s, V = np.linalg.svd(B)

        H = np.dot(V.T, np.dot(np.diag(s), V))

        A2 = (B + H) / 2

        A3 = (A2 + A2.T) / 2

        if self.isPD(A3):
            return A3

        spacing = np.spacing(np.linalg.norm(A))
        # The above is different from [1]. It appears that MATLAB's `chol` Cholesky
        # decomposition will accept matrixes with exactly 0-eigenvalue, whereas
        # Numpy's will not. So where [1] uses `eps(mineig)` (where `eps` is Matlab
        # for `np.spacing`), we use the above definition. CAVEAT: our `spacing`
        # will be much larger than [1]'s `eps(mineig)`, since `mineig` is usually on
        # the order of 1e-16, and `eps(1e-16)` is on the order of 1e-34, whereas
        # `spacing` will, for Gaussian random matrixes of small dimension, be on
        # othe order of 1e-16. In practice, both ways converge, as the unit test
        # below suggests.
        I = np.eye(A.shape[0])
        k = 1

        while not self.isPD(A3):
            mineig = np.min(np.real(np.linalg.eigvals(A3)))
            A3 += I * (-mineig * k ** 2 + spacing)
            k += 1

        return A3

    @staticmethod
    def isPD(B):
        """Returns true when input is positive-definite, via Cholesky"""
        try:
            _ = np.linalg.cholesky(B)
            return True
        except np.linalg.LinAlgError:
            return False

    def load_parameters(self, params, l):

        factor = 1
        index = []
        residuals = []
        p = []

        for param in params:
            par = np.array(param['params'])
            sig = np.array(param['sigmas'])

            o = param['object']
            if 'polynomial' == o:
                self.Linear.load_parameters(par, sig, param['t_ref'])

            elif 'periodic' == o:
                self.Periodic.load_parameters(params=par, sigmas=sig)

            elif 'jump' == o:
                for jump in self.Jumps.table:
                    if jump.p.hash == param['hash']:
                        jump.load_parameters(params=par, sigmas=sig)

            elif 'var_factor' == o:
                # already a vector in the db
                factor = par

        x = self.Linear.p.params
        s = self.Linear.p.sigmas

        for jump in self.Jumps.table:
            if jump.fit:
                x = np.append(x, jump.p.params, axis=1)
                s = np.append(s, jump.p.sigmas, axis=1)

        x = np.append(x, self.Periodic.p.params, axis=1)
        s = np.append(s, self.Periodic.p.sigmas, axis=1)

        for i in range(3):
            residuals.append(l[i] - np.dot(self.A(constrains=False), x[i, :]))

            ss = np.abs(np.divide(residuals[i], factor[i]))
            index.append(ss <= LIMIT)

            f = np.ones((l.shape[1],))

            sw = np.power(10, LIMIT - ss[ss > LIMIT])
            sw[sw < np.finfo(float).eps] = np.finfo(float).eps
            f[ss > LIMIT] = sw

            p.append(np.square(np.divide(f, factor[i])))

        self.C = x
        self.S = s
        self.F = np.array(index)
        self.R = np.array(residuals)
        self.factor = factor
        self.P = np.array(p)

    def adjust_lsq(self, Ai, Li):

        A = Ai(constrains=True)
        L = Ai.get_l(Li, constrains=True)

        factor = 1
        So = 1
        dof = (Ai.shape[0] - Ai.shape[1])
        X1 = chi2.ppf(1 - 0.05 / 2, dof)
        X2 = chi2.ppf(0.05 / 2, dof)

        s = np.array([])
        v = np.array([])
        C = np.array([])

        P = Ai.get_p(constrains=True)

        for _ in range(11):
            W = np.sqrt(P)

            Aw = np.multiply(W[:, None], A)
            Lw = np.multiply(W, L)

            C = np.linalg.lstsq(Aw, Lw, rcond=-1)[0]

            v = L - np.dot(A, C)

            # unit variance
            So = np.sqrt(np.dot(v, np.multiply(P, v)) / dof)

            x = np.power(So, 2) * dof

            # obtain the overall uncertainty predicted by lsq
            factor = factor * So

            # calculate the normalized sigmas
            s = np.abs(np.divide(v, factor))

            if x < X2 or x > X1:
                # if it falls in here it's because it didn't pass the Chi2 test

                # reweigh by Mike's method of equal weight until 2 sigma
                f = np.ones((v.shape[0],))
                # f[s > LIMIT] = 1. / (np.power(10, LIMIT - s[s > LIMIT]))
                # do not allow sigmas > 100 m, which is basically not putting
                # the observation in. Otherwise, due to a model problem
                # (missing jump, etc) you end up with very unstable inversions
                # f[f > 500] = 500
                sw = np.power(10, LIMIT - s[s > LIMIT])
                sw[sw < np.finfo(float).eps] = np.finfo(float).eps
                f[s > LIMIT] = sw

                P = np.square(np.divide(f, factor))
            else:
                break  # cst_pass = True

        # make sure there are no values below eps. Otherwise matrix becomes singular
        P[P < np.finfo(float).eps] = np.finfo(float).eps

        # some statistics
        try:
            SS = np.linalg.inv(np.dot(A.transpose(), np.multiply(P[:, None], A)))
        except numpy.linalg.LinAlgError as e:
            logger.info('np.linalg.inv failed in adjust_lsq: %s' % str(e))
            SS = np.ones(A.shape)

        sigma = So * np.sqrt(np.diag(SS))

        # mark observations with sigma <= LIMIT
        index = Ai.remove_constrains(s <= LIMIT)

        v = Ai.remove_constrains(v)
        # DDG: output the full covariance matrix too
        return C, sigma, index, v, factor, P, np.square(So) * SS

    @staticmethod
    def chi2inv(chi, df):
        """Return prob(chisq >= chi, with df degrees of
        freedom).

        df must be even.
        """
        assert df & 1 == 0
        # XXX If chi is very large, exp(-m) will underflow to 0.
        m = chi / 2.0
        sum = term = np.exp(-m)
        for i in range(1, df // 2):
            term *= m / i
            sum += term
        # With small chi and large df, accumulated
        # roundoff error, plus error in
        # the platform exp(), can cause this to spill
        # a few ULP above 1.0. For
        # example, chi2P(100, 300) on my box
        # has sum == 1.0 + 2.0**-52 at this
        # point.  Returning a value even a teensy
        # bit over 1.0 is no good.
        return np.min(sum)

    @staticmethod
    def warn_with_traceback(message, category, filename, lineno, file=None, line=None):

        log = file if hasattr(file, 'write') else sys.stderr
        traceback.print_stack(file=log)
        log.write(warnings.formatwarning(message, category, filename, lineno, line))

    def get_outliers_list(self):
        """
        Function to obtain the outliers based on the ETMs sigma
        :return: a list containing the network code, station code and dates of the outliers in the time series
        """

        filt = self.F[0] * self.F[1] * self.F[2]
        return [(self.NetworkCode, self.StationCode, pyDate.Date(mjd=mjd))
                for mjd in self.soln.mjd[~filt]]

    def pull_params(self):
        """
        method to obtain the current parameters of the station (in json format)
        periodic returns a dictionary with the periods as keys and a str following the description
        in periodic_status_dict
        """
        date = pyDate.Date(fyear=self.Linear.p.t_ref)

        poly_dict = {'terms': self.Linear.terms,
                     'Year': date.year,
                     'DOY' : date.doy}

        jumps = [{'Year': jump.p.jump_date.year,
                  'DOY': pyDate.Date(datetime=jump.p.jump_date).doy,
                  'action': jump.action,
                  'fit': jump.fit,
                  'type': jump.p.jump_type,
                  'relaxation': [] if jump.p.jump_type < CO_SEISMIC_JUMP_DECAY else jump.p.relaxation.tolist(),
                  'metadata': jump.p.metadata} for jump in self.Jumps.table]

        periodic = {}
        # create a dictionary with keys that equal the 1/f (period) requested or automatically added
        for f in self.Periodic.p.frequencies.tolist():
            if f in self.Periodic.requested_frequencies.tolist():
                status = 'R'
            else:
                status = 'A'

            periodic[1 / f] = status

        for f in self.Periodic.requested_frequencies.tolist():
            if 1/f not in periodic.keys():
                # this frequency is in the requested list, but was not included, so it is requested but deactivated
                periodic[1 / f] = 'D'

        # periodic = {'requested_frequencies': self.Periodic.requested_frequencies.tolist(),
        #             'frequency_count': self.Periodic.frequency_count,
        #             'fit_frequencies': self.Periodic.p.frequencies.tolist()}

        return {'polynomial': poly_dict, 'periodic': periodic, 'jumps': jumps}

    def push_params(self, cnn, params=None, reset_polynomial=False, reset_periodic=False, reset_jumps=False):
        """
        cnn             : database connection object
        params          : a dictionary containing the following keys, for each of the corresponding objects
                          {'object': 'polynomial', 'terms': int, 'Year': int, 'DOY': int}
                            terms: sets the number of terms to use in the polynomial (eg. 2, 3, 4, etc)
                            Year: sets the reference year part of the date for the site (if passed, then DOY is needed).
                              It can be = None or not passed
                            DOY: sets the reference day of year part of the date for the site (if passed,
                              then year is needed). It can be = None or not passed
                          {'object': 'periodic', 'frequencies': list[float]}
                            frequencies: a list of integers of the frequencies to fit. This value must be
                              passed as days. If no periodic terms are requested, then pass [].
                          {'object': 'jump', 'Year': int, 'DOY': int, 'action': str, 'jump_type': int, 'relaxation': list}
                            Year: sets the year part of the discontinuity date. Mandatory.
                            DOY: sets the day of year part of the discontinuity date. Mandatory
                            action: two possible values, '-' or '+' to add or remove a jump. Mandatory.
                            jump_type: jump type, can be 0 for mechanical, 1 for co+postseismic and 2 for postseismic
                              only, see type_dict_user
                            relaxation: mandatory when type is > 0, list of floats with the relaxation times in years
        reset_polynomial: rest
        reset_periodic  : rest
        reset_jumps     : rest
        """

        # for polynomial and periodic, trigger reset anyway since we need to get rid of the old records
        if params:
            if params['object'] == 'polynomial':
                reset_polynomial = True
                # sanity checks
                if 'Year' in params.keys() and 'DOY' in params.keys():
                    if params['Year'] is not None and type(params['Year']) is not int:
                        raise pyETMException('Parameter Year must be of type int')
                    if params['Year'] is not None and type(params['DOY']) is not int:
                        raise pyETMException('Parameter DOY must be of type int')
                elif ('Year' in params.keys() and 'DOY' not in params.keys()) or \
                        ('Year' not in params.keys() and 'DOY' in params.keys()):
                    raise pyETMException('Both parameters Year and DOY must be specified if either one is set')

                # check that terms is an integer > 0
                if type(params['terms']) is not int or params['terms'] <= 0:
                    raise pyETMException('Parameter terms must be of type int > 0')

                # check that the date is valid
                if 'Year' in params.keys() and 'DOY' in params.keys():
                    _ = pyDate.Date(year=params['Year'], doy=params['DOY'])

            elif params['object'] == 'periodic':
                reset_periodic = True

                # check that frequencies are not negative
                for i, f in enumerate(params['frequencies']):
                    if f <= 0:
                        raise pyETMException('Cannot insert negative frequencies for periodic components')
                    else:
                        params['frequencies'][i] = 1/f

            # add the fields for station and network
            params['NetworkCode'] = self.NetworkCode
            params['StationCode'] = self.StationCode
            params['soln'] = 'gamit' if self.soln.type == 'file' else self.soln.type

        if reset_polynomial:
            # reset to default
            cnn.delete('etm_params', NetworkCode=self.NetworkCode, StationCode=self.StationCode,
                       soln='gamit' if self.soln.type == 'file' else self.soln.type, object='polynomial')

        if reset_periodic:
            # reset to default
            cnn.delete('etm_params', NetworkCode=self.NetworkCode, StationCode=self.StationCode,
                       soln='gamit' if self.soln.type == 'file' else self.soln.type, object='periodic')

        if reset_jumps:
            # reset to default
            cnn.delete('etm_params', NetworkCode=self.NetworkCode, StationCode=self.StationCode,
                       soln='gamit' if self.soln.type == 'file' else self.soln.type, object='jump')

        if params:
            if params['object'] == 'jump':

                if params['jump_type'] < 0:
                    raise pyETMException('jump_type must be >= 0')

                if params['action'] == '+' and params['jump_type'] > 0 and 'relaxation' not in params.keys():
                    raise pyETMException('Relaxation parameters needed when jump_type > 0 and action = +')

                if 'relaxation' in params.keys():
                    if len(params['relaxation']):
                        for r in params['relaxation']:
                            if type(r) not in (float, int):
                                raise pyETMException('Relaxation parameters must be float type')
                            if r <= 0:
                                raise pyETMException('Relaxation parameters must be > 0')
                    elif not len(params['relaxation']) and params['jump_type'] > 0:
                        raise pyETMException('At least one relaxation needed for jump_type > 0')

                # query params to find the jump
                qpar = copy.deepcopy(params)
                qpar = {k: v for k, v in qpar.items() if k not in ('action', 'relaxation', 'jump_type')}

                # remove existing parameters
                cnn.delete('etm_params', **qpar)

            # insert the parameters passed to the database
            cnn.insert('etm_params', **params)


class PPPETM(ETM):

    def __init__(self, cnn, NetworkCode, StationCode, plotit=False, no_model=False, models=(), ignore_db_params=False,
                 plot_remove_jumps=False, plot_polynomial_removed=False):
        # load all the PPP coordinates available for this station
        # exclude ppp solutions in the exclude table and any solution that is more than 100 meters from the auto coord

        self.ppp_soln = PppSoln(cnn, NetworkCode, StationCode)

        ETM.__init__(self, cnn, self.ppp_soln, no_model, plotit=plotit, models=models,
                     ignore_db_params=ignore_db_params, plot_remove_jumps=plot_remove_jumps,
                     plot_polynomial_removed=plot_polynomial_removed)


class GamitETM(ETM):

    def __init__(self, cnn, NetworkCode, StationCode, plotit=False, no_model=False, gamit_soln=None,
                 stack_name=None, models=(), ignore_db_params=False, plot_remove_jumps=False,
                 plot_polynomial_removed=False):

        if gamit_soln is None:
            self.polyhedrons = cnn.query_float('SELECT "X", "Y", "Z", "Year", "DOY" FROM stacks '
                                               'WHERE "name" = \'%s\' AND "NetworkCode" = \'%s\' AND '
                                               '"StationCode" = \'%s\' '
                                               'ORDER BY "Year", "DOY", "NetworkCode", "StationCode"'
                                               % (stack_name, NetworkCode, StationCode))

            self.gamit_soln = GamitSoln(cnn, self.polyhedrons, NetworkCode, StationCode, stack_name)

        else:
            # load the GAMIT polyhedrons
            self.gamit_soln = gamit_soln

        ETM.__init__(self, cnn, self.gamit_soln, no_model, plotit=plotit, ignore_db_params=ignore_db_params,
                     models=models, plot_remove_jumps=plot_remove_jumps,
                     plot_polynomial_removed=plot_polynomial_removed)

    def get_etm_soln_list(self, use_ppp_model=False, cnn=None):
        # this function return the values of the ETM ONLY

        stn_id = stationID(self)

        if self.A is None:
            raise pyETMException_NoDesignMatrix('No design matrix available for %s' % stn_id)

        elif not use_ppp_model:
            # get residuals from GAMIT solutions to GAMIT model
            neu = [np.dot(self.A, self.C[i])
                   for i in range(3)]
        else:
            # get residuals from GAMIT solutions to PPP model
            etm = PPPETM(cnn, self.NetworkCode, self.StationCode)
            if etm.A is None:
                raise pyETMException_NoDesignMatrix('No PPP design matrix available for %s' % stn_id)
            else:
                # DDG: 20-SEP-2018 compare using MJD not FYEAR to avoid round off errors
                index = np.isin(etm.soln.mjds, self.soln.mjd)
                # use the etm object to obtain the design matrix that matches the dimensions of self.soln.t
                neu = [np.dot(etm.As[index, :], etm.C[i])
                       for i in range(3)]

                del etm

        rxyz = self.rotate_2xyz(np.array(neu)) + np.array([self.soln.auto_x,
                                                           self.soln.auto_y,
                                                           self.soln.auto_z])

        return [(stn_id, x, y, z, date.year, date.doy, date.fyear)
                for x, y, z, date in
                zip(rxyz[0],
                    rxyz[1],
                    rxyz[2],
                    self.gamit_soln.date)]


class DailyRep(ETM):

    def __init__(self, cnn, NetworkCode, StationCode, plotit=False,
                 no_model=False, gamit_soln=None, project=None, save_excluded=False):

        if gamit_soln is None:
            # self.polyhedrons = cnn.query_float('SELECT "X", "Y", "Z", "Year", "DOY" FROM gamit_soln '
            #                                   'WHERE "Project" = \'%s\' AND "NetworkCode" = \'%s\' AND '
            #                                   '"StationCode" = \'%s\' '
            #                                   'ORDER BY "Year", "DOY", "NetworkCode", "StationCode"'
            #                                   % (project, NetworkCode, StationCode))

            # self.gamit_soln = GamitSoln(cnn, self.polyhedrons, NetworkCode, StationCode, project)
            raise ValueError('DailyRep class requires a gamit_soln object')

        else:
            # load the GAMIT polyhedrons
            self.soln = gamit_soln

        # the the solution type to dra
        self.soln.type = 'dra'
        # replace auto_[xyz] with zeros so that in ETM.__init__ the self.l vector is realized properly
        # DRA requires coordinates differences, not coordinates relative to reference
        self.soln.auto_x = [0]
        self.soln.auto_y = [0]
        self.soln.auto_z = [0]

        ETM.__init__(self, cnn, self.soln, no_model, FitEarthquakes=False, FitGenericJumps=False,
                     FitPeriodic=False, plotit=plotit)

        # @todo: add back the saving of excluded solutions for IGN
        if self.A is not None and save_excluded:
            self.save_excluded_soln(cnn)

    def get_residuals_dict(self):
        # this function return the values of the ETM ONLY

        if self.A is None:
            raise pyETMException_NoDesignMatrix('No design matrix available for %s' % stationID(self))

        neu = [np.dot(self.A, self.C[i])
               for i in range(3)]

        xyz = self.rotate_2xyz(np.array(neu)) + \
              np.array([self.soln.auto_x, self.soln.auto_y, self.soln.auto_z])

        rxyz = xyz - self.L

        px = np.ones(self.P[0].shape)
        py = np.ones(self.P[1].shape)
        pz = np.ones(self.P[2].shape)

        return [(self.NetworkCode, self.StationCode, x, y, z, sigx, sigy, sigz, date.year, date.doy)
                for x, y, z, sigx, sigy, sigz, date in
                zip(rxyz[0],
                    rxyz[1],
                    rxyz[2],
                    px,
                    py,
                    pz,
                    self.soln.date)]


class FileETM(ETM):

    def __init__(self, cnn, poly_list=None, plotit=False, no_model=False, plot_remove_jumps=False,
                 plot_polynomial_removed=False):

        # self.soln.type = 'file'

        ETM.__init__(self, cnn, poly_list, no_model, plotit=plotit, ignore_db_params=True,
                     plot_remove_jumps=plot_remove_jumps, plot_polynomial_removed=plot_polynomial_removed)

