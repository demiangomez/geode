
import os
import re
import subprocess
import sys
import traceback
import filecmp
import argparse
import stat
import shutil
import io
import base64
import json
import geopandas as gpd
from datetime import datetime
from zlib import crc32 as zlib_crc32
from pathlib import Path
from typing import Union, List, Dict
from shapely.geometry import Point

# deps
import numpy
import numpy as np
from importlib.metadata import version
from geopy.geocoders import Nominatim
import country_converter as coco

# app
from . import pyRinexName
from . import pyDate
from .station_selector import StationSelector, StationFilter

COUNTRIES = None


class UtilsException(Exception):
    def __init__(self, value):
        self.value = value
        
    def __str__(self):
        return str(self.value)


def add_version_argument(parser):
    __version__ = version('geode-gnss')
    parser.add_argument('-v', '--version', action='version', version=f'%(prog)s {__version__}')
    return parser


def cart2euler(x, y, z):
    alt = numpy.rad2deg(numpy.sqrt(x**2 + y**2 + z**2) * 1e-9 * 1e6)
    lat = numpy.rad2deg(numpy.arctan2(z, numpy.sqrt(x**2 + y**2)))
    lon = numpy.rad2deg(numpy.arctan2(y, x))
    return lat, lon, alt


def get_field_or_attr(obj, f):
    try:
        return obj[f]
    except:
        return getattr(obj, f)


def stationID(s):
    if isinstance(s, dict):
        has_network_code = 'network_code' in s
    else:  # For object
        has_network_code = hasattr(s, 'network_code')

    if has_network_code:
        # new format
        return "%s.%s" % (get_field_or_attr(s, 'network_code'),
                          get_field_or_attr(s, 'station_code'))
    else:
        return "%s.%s" % (get_field_or_attr(s, 'NetworkCode'),
                          get_field_or_attr(s, 'StationCode'))


def get_stack_stations(cnn, name):
    rs = cnn.query_float(f'SELECT DISTINCT "NetworkCode", "StationCode", auto_x, auto_y, auto_z '
                         f'FROM stacks INNER JOIN stations '
                         f'USING ("NetworkCode", "StationCode")'
                         f'WHERE "name" = \'{name}\'', as_dict=True)

    # since we require spherical lat lon for the Euler pole, I compute it from the xyz values
    for i, stn in enumerate(rs):
        lla = xyz2sphere_lla(numpy.array([stn['auto_x'], stn['auto_y'], stn['auto_z']]))
        rs[i]['lat'] = lla[0][0]
        rs[i]['lon'] = lla[0][1]

    return rs


def parse_atx_antennas(atx_file):

    output = file_readlines(atx_file)

    # return re.findall(r'START OF ANTENNA\s+(\w+[.-\/+]?\w*[.-\/+]?\w*)\s+(\w+)', ''.join(output), re.MULTILINE)
    # do not return the RADOME
    return re.findall(r'START OF ANTENNA\s+([\S]+)', ''.join(output), re.MULTILINE)


def smallestN_indices(a, N):
    """
    Function to return the row and column of the N smallest values
    :param a: array to search (any dimension)
    :param N: number of values to search
    :return: array with the rows-cols of min values
    """
    idx = a.ravel().argsort()[:N]
    return numpy.stack(numpy.unravel_index(idx, a.shape)).T


def ll2sphere_xyz(ell):
    r = 6371000.0
    x = []
    for lla in ell:
        x.append((r * numpy.cos(lla[0] * numpy.pi / 180) * numpy.cos(lla[1] * numpy.pi / 180),
                  r * numpy.cos(lla[0] * numpy.pi / 180) * numpy.sin(lla[1] * numpy.pi / 180),
                  r * numpy.sin(lla[0] * numpy.pi / 180)))

    return numpy.array(x)


def xyz2sphere_lla(xyz):
    """
    function to turn xyz coordinates to lat lon using spherical earth
    output is lat, lon, radius
    """
    if isinstance(xyz, list):
        xyz = numpy.array(xyz)

    if xyz.ndim == 1:
        xyz = xyz[np.newaxis, :]

    g = numpy.zeros(xyz.shape)
    for i, x in enumerate(xyz):
        g[i, 0] = numpy.rad2deg(numpy.arctan2(x[2], numpy.sqrt(x[0]**2 + x[1]**2)))
        g[i, 1] = numpy.rad2deg(numpy.arctan2(x[1], x[0]))
        g[i, 2] = numpy.sqrt(x[0]**2 + x[1]**2 + x[2]**2)

    return g


def required_length(nmin, nmax):
    class RequiredLength(argparse.Action):
        def __call__(self, parser, args, values, option_string=None):
            if not nmin <= len(values) <= nmax:
                msg = 'argument "{f}" requires between {nmin} and {nmax} arguments'.format(
                       f = self.dest, nmin = nmin, nmax = nmax)
                raise argparse.ArgumentTypeError(msg)

            setattr(args, self.dest, values)

    return RequiredLength


def station_list_help():
    """
    Generate comprehensive help text for station list specification with geographic filters.
    """
    desc = (
        "List of networks/stations to process with support for geographic and type-based filtering.\n\n"

        "BASIC FORMATS:\n"
        "  [net].[stnm]    - Specific station (e.g., arg.igm1, igs.pwro)\n"
        "  [stnm]          - Station name only (all stations with this name)\n"
        "  all             - All stations in the database\n"
        "  [net].all       - All stations from network [net]\n"
        "  [COUNTRY]       - All stations in country (ISO 3166 code, uppercase, e.g., ARG, CHL, USA)\n\n"

        "WILDCARDS (PostgreSQL regex):\n"
        "  [a-z]           - Character ranges: ars.at1[3-5] matches at13, at14, at15\n"
        "  %%              - Any string: ars.at%% matches at01, at02, ..., at99, etc.\n"
        "  _               - Single character: ar_.ig_1 matches ara.igm1, arb.ign1, etc.\n"
        "  |               - OR operator: ars.at1[1|2] matches at11 and at12\n"
        "  Examples: arg.igm%% (all ARG IGMx stations), igs.pw%% (all IGS PWxx stations)\n\n"

        "STATION TYPE FILTERS:\n"
        "  [COUNTRY]:TYPE  - Filter by station type (requires GeoDE Studio tables)\n"
        "  Examples:\n"
        "    ARG:CONTINUOUS       - Continuous stations in Argentina\n"
        "    CHL:CAMPAIGN         - Campaign stations in Chile\n"
        "    USA:CORS             - CORS stations in USA\n"
        "    all:CONTINUOUS       - All CONTINUOUS stations\n\n"

        "GEOGRAPHIC FILTERS:\n"
        "  LAT[min,max]         - Latitude range (decimal degrees)\n"
        "  LON[min,max]         - Longitude range (decimal degrees)\n"
        "  BBOX[lat1,lat2,lon1,lon2] - Bounding box (shorthand for LAT+LON)\n"
        "  RADIUS[lat,lon,km]   - Circular region (center + radius in km)\n"
        "  PLATE[plate]         - Tectonic plate (two letter code)\n"
        "  Examples:\n"
        "    ARG:LAT[-35,-40]              - Argentina, latitudes -35° to -40°\n"
        "    CHL:LON[-72,-70]              - Chile, longitudes -72° to -70°\n"
        "    ARG:BBOX[-30,-40,-70,-60]     - Bounding box in Argentina\n"
        "    ARG:PLATE[SC]                 - Argentina station in the Scotia plate\n"
        "    ARG:RADIUS[-35.5,-65.2,500]   - 500 km radius around point\n\n"

        "COMBINED FILTERS:\n"
        "  Filters can be combined using colon separators:\n"
        "    ARG:CONTINUOUS:LAT[-30,-40]            - Continuous stations in latitude range\n"
        "    CHL:CONTINUOUS:BBOX[-32,-38,-72,-68]   - Continuous stations in bounding box\n"
        "    CHL:CONTINUOUS:PLATE[SA]               - Continuous stations in South America\n"
        "    ARG:CAMPAIGN:RADIUS[-35,-65,300]       - Campaign stations within 300 km\n\n"

        "REMOVING STATIONS:\n"
        "  -[spec] or *[spec]  - Remove stations matching specification (use * in command line)\n"
        "  Examples:\n"
        "    -igs.pwro         - Remove specific station\n"
        "    *pwro             - Remove all stations named pwro\n"
        "    -igs.all          - Remove all IGS network stations\n"
        "    *ARG              - Remove all Argentine stations\n"
        "    *CHL:LAT[-40,-45] - Remove Chilean stations in latitude range\n\n"

        "PARAMETERS (only works for station files):\n"
        "  [spec] [value]      - Add space-separated parameters (e.g., for weights)\n"
        "  Examples:\n"
        "    igm1.arg 1.00     - Station with parameter 1.00\n"
        "    arg.lpgs 1.20     - Station with parameter 1.20\n\n"

        "FILE INPUT:\n"
        "  Alternatively, provide a file path containing station specifications (one per line).\n"
        "  Files support all the same formats and conventions as command-line input.\n"
        "  When using files, '-' can replace '*' for removal (e.g., -igs.pwro)\n\n"

        "MORE USAGE EXAMPLES:\n"
        "  Basic selection:\n"
        "    arg.igm1                             # A specific station\n"
        "    ARG                                  # All stations in Argentina\n"
        "    igs.all                              # All IGS network stations\n\n"

        "  With wildcards:\n"
        "    arg.igm%%                             # All stations named IGMx\n"
        "    ars.at1[3-7]                         # Stations at13 through at17\n\n"

        "  With type filters:\n"
        "    ARG:CONTINUOUS CHL:CONTINUOUS        # Continuous stations in two countries\n\n"

        "  With geographic filters:\n"
        "    ARG:LAT[-32,-38] CHL:LAT[-32,-38]    # Latitude band across countries\n"
        "    ARG:RADIUS[-35,-65,500]              # Circular study area\n\n"

        "  Complex combinations:\n"
        "    ARG:CONTINUOUS:BBOX[-30,-40,-70,-60] CHL:CONTINUOUS:BBOX[-30,-40,-70,-60]\n"
        "    # Continuous stations in bounding box spanning two countries\n\n"

        "  With removals:\n"
        "    ARG -arg.igm1                        # All Argentina except one station\n"
        "    igs.all *igs.pw%%                     # All IGS except PW stations\n"
        "    ARG:CONTINUOUS *ARG:LAT[-40,-55]     # Continuous except southern region\n\n"

        "NOTES:\n"
        "  - Country codes must be uppercase (ARG, not arg)\n"
        "  - Station type filters require GeoDE Studio tables (api_stationtype, api_stationmeta)\n"
        "  - Stations with no assigned type are not considered\n"
        "  - Coordinates are in decimal degrees (latitude: -90 to 90, longitude: -180 to 180)\n"
        "  - RADIUS uses great-circle distance (Haversine formula)\n"
        "  - Filters are processed left to right; removals applied after additions\n"
        "  - Duplicate stations are automatically removed from final list"
    )

    return desc


def parse_crinex_rinex_filename(filename):
    # DDG: DEPRECATED
    # this function only accepts .Z as extension. Replaced with RinexName.split_filename which also includes .gz
    # parse a crinex filename
    sfile = re.findall(r'(\w{4})(\d{3})(\w{1})\.(\d{2})([d]\.[Z])$', filename)
    if sfile:
        return sfile[0]

    sfile = re.findall(r'(\w{4})(\d{3})(\w{1})\.(\d{2})([o])$', filename)
    if sfile:
        return sfile[0]

    return []


def _increment_filename(filename):
    """
    Returns a generator that yields filenames with a counter. This counter
    is placed before the file extension, and incremented with every iteration.
    For example:
        f1 = increment_filename("myimage.jpeg")
        f1.next() # myimage-1.jpeg
        f1.next() # myimage-2.jpeg
        f1.next() # myimage-3.jpeg
    If the filename already contains a counter, then the existing counter is
    incremented on every iteration, rather than starting from 1.
    For example:
        f2 = increment_filename("myfile-3.doc")
        f2.next() # myfile-4.doc
        f2.next() # myfile-5.doc
        f2.next() # myfile-6.doc
    The default marker is an underscore, but you can use any string you like:
        f3 = increment_filename("mymovie.mp4", marker="_")
        f3.next() # mymovie_1.mp4
        f3.next() # mymovie_2.mp4
        f3.next() # mymovie_3.mp4
    Since the generator only increments an integer, it is practically unlimited
    and will never raise a StopIteration exception.
    """
    # First we split the filename into three parts:
    #
    #  1) a "base" - the part before the counter
    #  2) a "counter" - the integer which is incremented
    #  3) an "extension" - the file extension

    sessions = ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9] + [chr(x) for x in range(ord('a'), ord('x')+1)] +
                [chr(x) for x in range(ord('A'), ord('X')+1)])

    path      = os.path.dirname(filename)
    filename  = os.path.basename(filename)
    # replace with parse_crinex_rinex_filename (deprecated)
    # fileparts = parse_crinex_rinex_filename(filename)
    fileparts = pyRinexName.RinexNameFormat(filename).split_filename(filename)

    # Check if there's a counter in the filename already - if not, start a new
    # counter at 0.
    value = 0

    filename = os.path.join(path, '%s%03i%s.%02i%s' % (fileparts[0].lower(), int(fileparts[1]), sessions[value],
                                                       int(fileparts[3]), fileparts[4]))

    # The counter is just an integer, so we can increment it indefinitely.
    while True:
        if value == 0:
            yield filename

        value += 1

        if value == len(sessions):
            raise ValueError('Maximum number of sessions reached: %s%03i%s.%02i%s'
                             % (fileparts[0].lower(), int(fileparts[1]), sessions[value-1],
                                int(fileparts[3]), fileparts[4]))

        yield os.path.join(path, '%s%03i%s.%02i%s' % (fileparts[0].lower(), int(fileparts[1]), sessions[value],
                                                      int(fileparts[3]), fileparts[4]))


def copyfile(src, dst, rnx_ver=2):
    """
    Copies a file from path src to path dst.
    If a file already exists at dst, it will not be overwritten, but:
     * If it is the same as the source file, do nothing
     * If it is different to the source file, pick a new name for the copy that
       is different and unused, then copy the file there (if rnx_ver=2)
     * If because rinex 3 files have names that are more comprehensive (include start time and duration)
       if a rnx_ver == 3 then copy the file unless it already exists (in which case it does nothing)
    Returns the path to the copy.
    """
    if not os.path.exists(src):
        raise ValueError('Source file does not exist: {}'.format(src))

    # make the folders if they don't exist
    # careful! racing condition between different workers
    try:
        dst_dir = os.path.dirname(dst)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
    except OSError:
        # some other process created the folder an instant before
        pass

    # Keep trying to copy the file until it works
    if rnx_ver < 3:
        # only use this method for RINEX 2
        # RINEX 3 files should have distinct names as a default if the files are different
        dst_gen = _increment_filename(dst)

    while True:
        if rnx_ver < 3:
            dst = next(dst_gen)

        # Check if there is a file at the destination location
        if os.path.exists(dst):

            # If the namesake is the same as the source file, then we don't
            # need to do anything else.
            if filecmp.cmp(src, dst):
                return dst
            else:
                # DDG: if the rinex version is == 3 and the files have the same name:
                # 1) if dst size is < than src, replace file
                # 2) if dst size is > than src, do nothing
                # for RINEX 2 files, loop over and find a different filename
                if rnx_ver >= 3:
                    if os.path.getsize(src) > os.path.getsize(dst):
                        os.remove(dst)
                        if do_copy_op(src, dst):
                            return dst
                        else:
                            raise OSError('File exists during copy of RINEX 3 file: ' + dst)
                    else:
                        return dst
        else:
            if do_copy_op(src, dst):
                # If we get to this point, then the write has succeeded
                return dst
            else:
                if rnx_ver >= 3:
                    raise OSError('Problem while copying RINEX 3 file: ' + dst)


def do_copy_op(src, dst):
    # If there is no file at the destination, then we attempt to write
    # to it. There is a risk of a race condition here: if a file
    # suddenly pops into existence after the `if os.path.exists()`
    # check, then writing to it risks overwriting this new file.
    #
    # We write by transferring bytes using os.open(). Using the O_EXCL
    # flag on the dst file descriptor will cause an OSError to be
    # raised if the file pops into existence; the O_EXLOCK stops
    # anybody else writing to the dst file while we're using it.
    src_fd = None
    dst_fd = None
    try:
        src_fd = os.open(src, os.O_RDONLY)
        dst_fd = os.open(dst, os.O_WRONLY | os.O_EXCL | os.O_CREAT)

        # Read 65536 bytes at a time, and copy them from src to dst
        while True:
            data = os.read(src_fd, 65536)
            if not data:
                # When there are no more bytes to read from the source
                # file, 'data' will be an empty string
                return True
            os.write(dst_fd, data)

    # An OSError errno 17 is what happens if a file pops into existence
    # at dst, so we print an error and try to copy to a new location.
    # Any other exception is unexpected and should be raised as normal.
    except OSError as e:
        if e.errno != 17 or e.strerror != 'File exists':
            raise
        return False
    finally:
        if src_fd != None:
            os.close(src_fd)
        if dst_fd != None:
            os.close(dst_fd)


def move(src, dst):
    """
    Moves a file from path src to path dst.
    If a file already exists at dst, it will not be overwritten, but:
     * If it is the same as the source file, do nothing
     * If it is different to the source file, pick a new name for the copy that
       is distinct and unused, then copy the file there.
    Returns the path to the new file.
    """
    rnx_ver = pyRinexName.RinexNameFormat(dst).version
    dst = copyfile(src, dst, rnx_ver)
    os.remove(src)
    return dst


def ct2lg(dX, dY, dZ, lat, lon):

    n = dX.size

    R = rotct2lg(lat, lon, n)

    dxdydz = numpy.column_stack((numpy.column_stack((dX, dY)), dZ))

    RR = numpy.reshape(R[0, :, :], (3, n))
    dx = numpy.sum(numpy.multiply(RR, dxdydz.transpose()), axis=0)
    RR = numpy.reshape(R[1, :, :], (3, n))
    dy = numpy.sum(numpy.multiply(RR, dxdydz.transpose()), axis=0)
    RR = numpy.reshape(R[2, :, :], (3, n))
    dz = numpy.sum(numpy.multiply(RR, dxdydz.transpose()), axis=0)

    return dx, dy, dz


def rotct2lg(lat, lon, n=1):

    R = numpy.zeros((3, 3, n))

    R[0, 0, :] = -numpy.multiply(numpy.sin(numpy.deg2rad(lat)), numpy.cos(numpy.deg2rad(lon)))
    R[0, 1, :] = -numpy.multiply(numpy.sin(numpy.deg2rad(lat)), numpy.sin(numpy.deg2rad(lon)))
    R[0, 2, :] = numpy.cos(numpy.deg2rad(lat))
    R[1, 0, :] = -numpy.sin(numpy.deg2rad(lon))
    R[1, 1, :] = numpy.cos(numpy.deg2rad(lon))
    R[1, 2, :] = numpy.zeros((1, n))
    R[2, 0, :] = numpy.multiply(numpy.cos(numpy.deg2rad(lat)), numpy.cos(numpy.deg2rad(lon)))
    R[2, 1, :] = numpy.multiply(numpy.cos(numpy.deg2rad(lat)), numpy.sin(numpy.deg2rad(lon)))
    R[2, 2, :] = numpy.sin(numpy.deg2rad(lat))

    return R


def lg2ct(dN, dE, dU, lat, lon):

    n = dN.size

    R = rotlg2ct(lat, lon, n)

    dxdydz = numpy.column_stack((numpy.column_stack((dN, dE)), dU))

    RR = numpy.reshape(R[0, :, :], (3, n))
    dx = numpy.sum(numpy.multiply(RR, dxdydz.transpose()), axis=0)
    RR = numpy.reshape(R[1, :, :], (3, n))
    dy = numpy.sum(numpy.multiply(RR, dxdydz.transpose()), axis=0)
    RR = numpy.reshape(R[2, :, :], (3, n))
    dz = numpy.sum(numpy.multiply(RR, dxdydz.transpose()), axis=0)

    return dx, dy, dz


def rotlg2ct(lat, lon, n=1):

    R = numpy.zeros((3, 3, n))

    R[0, 0, :] = -numpy.multiply(numpy.sin(numpy.deg2rad(lat)), numpy.cos(numpy.deg2rad(lon)))
    R[1, 0, :] = -numpy.multiply(numpy.sin(numpy.deg2rad(lat)), numpy.sin(numpy.deg2rad(lon)))
    R[2, 0, :] = numpy.cos(numpy.deg2rad(lat))
    R[0, 1, :] = -numpy.sin(numpy.deg2rad(lon))
    R[1, 1, :] = numpy.cos(numpy.deg2rad(lon))
    R[2, 1, :] = numpy.zeros((1, n))
    R[0, 2, :] = numpy.multiply(numpy.cos(numpy.deg2rad(lat)), numpy.cos(numpy.deg2rad(lon)))
    R[1, 2, :] = numpy.multiply(numpy.cos(numpy.deg2rad(lat)), numpy.sin(numpy.deg2rad(lon)))
    R[2, 2, :] = numpy.sin(numpy.deg2rad(lat))

    return R


def ecef2lla(ecefArr):
    # convert ECEF coordinates to LLA
    # test data : test_coord = [2297292.91, 1016894.94, -5843939.62]
    # expected result : -66.8765400174 23.876539914 999.998386689

    # force what input (list, tuple, etc) to be a numpy array
    ecefArr = numpy.atleast_1d(ecefArr)

    # transpose to work on both vectors and scalars
    x = ecefArr.T[0]
    y = ecefArr.T[1]
    z = ecefArr.T[2]

    a = 6378137
    e = 8.1819190842622e-2

    asq = numpy.power(a, 2)
    esq = numpy.power(e, 2)

    b   = numpy.sqrt(asq * (1 - esq))
    bsq = numpy.power(b, 2)

    ep = numpy.sqrt((asq - bsq) / bsq)
    p  = numpy.sqrt(numpy.power(x, 2) + numpy.power(y, 2))
    th = numpy.arctan2(a * z, b * p)

    lon = numpy.arctan2(y, x)
    lat = numpy.arctan2((z + numpy.power(ep, 2) * b * numpy.power(numpy.sin(th), 3)),
                        (p - esq * a * numpy.power(numpy.cos(th), 3)))
    N   = a / (numpy.sqrt(1 - esq * numpy.power(numpy.sin(lat), 2)))
    alt = p / numpy.cos(lat) - N

    lon = lon * 180 / numpy.pi
    lat = lat * 180 / numpy.pi

    return lat.ravel(), lon.ravel(), alt.ravel()


def lla2ecef(llaArr):
    # convert LLA coordinates to ECEF
    # test data : test_coord = [-66.8765400174 23.876539914 999.998386689]
    # expected result : 2297292.91, 1016894.94, -5843939.62

    llaArr = numpy.atleast_1d(llaArr)

    # transpose to work on both vectors and scalars
    lat = llaArr.T[0]
    lon = llaArr.T[1]
    alt = llaArr.T[2]

    rad_lat = lat * (numpy.pi / 180.0)
    rad_lon = lon * (numpy.pi / 180.0)

    # WGS84
    a = 6378137.0
    finv = 298.257223563
    f = 1 / finv
    e2 = 1 - (1 - f) * (1 - f)
    v = a / numpy.sqrt(1 - e2 * numpy.sin(rad_lat) * numpy.sin(rad_lat))

    x = (v + alt) * numpy.cos(rad_lat) * numpy.cos(rad_lon)
    y = (v + alt) * numpy.cos(rad_lat) * numpy.sin(rad_lon)
    z = (v * (1 - e2) + alt) * numpy.sin(rad_lat)

    return numpy.round(x, 4).ravel(), numpy.round(y, 4).ravel(), numpy.round(z, 4).ravel()


def process_date_str(arg, allow_days=False):

    rdate = pyDate.Date(datetime=datetime.now())

    try:
        if '.' in arg:
            rdate = pyDate.Date(fyear=float(arg))
        elif '_' in arg:
            rdate = pyDate.Date(year=int(arg.split('_')[0]),
                                doy=int(arg.split('_')[1]))
        elif '/' in arg:
            rdate = pyDate.Date(year=int(arg.split('/')[0]),
                                month=int(arg.split('/')[1]),
                                day=int(arg.split('/')[2]))
        elif '-' in arg:
            rdate = pyDate.Date(gpsWeek=int(arg.split('-')[0]),
                                gpsWeekDay=int(arg.split('-')[1]))
        elif len(arg) > 0:
            if allow_days:
                rdate = pyDate.Date(datetime=datetime.now()) - int(arg)
            else:
                raise ValueError('Invalid input date: allow_days was set to False.')

    except Exception as e:
        raise ValueError('Could not decode input date (valid entries: '
                         'fyear, yyyy_ddd, yyyy/mm/dd, gpswk-wkday). '
                         'Error while reading the date start/end parameters: ' + str(e))

    return rdate


def process_date(arg, missing_input='fill', allow_days=True):
    # function to handle date input from PG.
    # Input: arg = arguments from command line
    #        missing_input = a string specifying if vector should be filled when something is missing
    #        allow_day = allow a single argument which represents an integer N expressed in days, to compute now()-N

    now = datetime.now()
    if missing_input == 'fill':
        dates = [pyDate.Date(year=1980, doy=1),
                 pyDate.Date(datetime = now)]
    else:
        dates = [None, None]

    if arg:
        for i, arg in enumerate(arg):
            dates[i] = process_date_str(arg, allow_days)

    return tuple(dates)


def determine_frame(frames, date):

    for frame in frames:
        if frame['dates'][0] <= date <= frame['dates'][1]:
            return frame['name'], frame['atx']

    raise Exception('No valid frame was found for the specified date.')


def print_columns(l):

    for a, b, c, d, e, f, g, h in zip(l[::8], l[1::8], l[2::8], l[3::8], l[4::8], l[5::8], l[6::8], l[7::8]):
        print('    {:<10}{:<10}{:<10}{:<10}{:<10}{:<10}{:<10}{:<}'.format(a, b, c, d, e, f, g, h),
              file=sys.stderr)

    if len(l) % 8 != 0:
        sys.stderr.write('    ')
        for i in range(len(l) - len(l) % 8, len(l)):
            sys.stderr.write('{:<10}'.format(l[i]))
        sys.stderr.write('\n')


def get_resource_delimiter():
    return '.'


def get_country_code(lat, lon):
    """Obtain the country code based on lat lon of station"""

    from importlib.resources import files
    data_path = files('geode.elasticity.data').joinpath(
        'ne_10m_admin_0_countries_arg.shp'
    )
    shapefile_path = str(data_path)

    global COUNTRIES

    # Load shapefile only once
    if COUNTRIES is None:
        COUNTRIES = gpd.read_file(shapefile_path)

    point = Point(lon, lat)

    # Find country containing this point
    country = COUNTRIES[COUNTRIES.contains(point)]

    if not country.empty:
        return country.iloc[0]['ISO_A3']

    return None


def remove_stations(station_list: List[Dict], removal_filters: List[StationFilter],
                    selector: StationSelector) -> List[Dict]:
    """Remove stations from the list based on removal filters."""
    stations_to_remove = set()

    for f in removal_filters:
        if f.filter_str == 'all':
            # Remove all (unusual but supported)
            return []

        # Get stations matching the removal filter
        stations = selector.select_stations(f)
        stations_to_remove.update(stationID(s) for s in stations)

        # Also handle station-code-only removals (no network specified)
        if f.station and not f.network and not f.country_code:
            # Remove by station code across all networks
            stations_to_remove.update(
                stationID(s) for s in station_list
                if s['StationCode'] == f.station
            )

    # Filter out removed stations
    return [s for s in station_list if stationID(s) not in stations_to_remove]


def process_stnlist(cnn, stnlist_in, print_summary=True, summary_title=None):
    """
    Process a station list with support for multiple filter types.

    Station list parser handles postgres regular expressions and geographic filters.
    Supports:
    - Reading from file or command line
    - Country codes with filters: ARG:CONTINUOUS:LAT(-30,-40):LON(-70,-60)
    - Network.station notation with wildcards
    - Station removals with - or * prefix
    - Additional parameters (space-separated) for each station

    Examples:
        ARG                          # All stations in Argentina
        ARG:CONTINUOUS               # Continuous stations in Argentina
        ARG:LAT(-30,-40)             # Stations in Argentina between -30 and -40 lat
        ARG:BBOX(-30,-40,-70,-60)    # Stations in bounding box
        ARG:RADIUS(-35,-65,500)      # Stations within 500km of point
        ARG:PLATE(SA)                # Stations a tectonic plate
        igm1.arg                     # Specific station
        igm1.arg 1.00                # Station with parameter (cannot be through command line)
        -chl.sant                    # Remove station
        *CHL                         # Remove all Chilean stations
        ARG:CONTINUOUS:PLATE(SA)     # Combinations of filters

    Args:
        cnn: Database connection
        stnlist_in: List of station filter strings or path to file
        print_summary: Whether to print selected stations
        summary_title: Optional custom title for summary

    Returns:
        List of station dictionaries with keys:
            NetworkCode, StationCode, marker, country_code, parameters
    """
    # Read from file if single argument is a file path
    if len(stnlist_in) == 1 and os.path.isfile(stnlist_in[0]):
        print(f' >> Station list read from file: {stnlist_in[0]}', file=sys.stderr)
        with open(stnlist_in[0], 'r') as f:
            stnlist_in = [line.strip() for line in f if line.strip()]

    # Initialize selector
    selector = StationSelector(cnn)

    # Parse all filters
    filters = [StationFilter(s) for s in stnlist_in]

    # Separate additions and removals
    addition_filters = [f for f in filters if not f.is_removal]
    removal_filters = [f for f in filters if f.is_removal]

    # Collect all stations from addition filters
    station_dict = {}  # Use dict to avoid duplicates: station_id -> station_info

    for f in addition_filters:
        stations = selector.select_stations(f)
        for stn in stations:
            stn_id = stationID(stn)
            if stn_id not in station_dict:
                station_dict[stn_id] = {
                    'NetworkCode': stn['NetworkCode'],
                    'StationCode': stn['StationCode'],
                    'marker': stn.get('marker', 0) or 0,
                    'country_code': stn.get('country_code', '') or '',
                    'parameters': f.parameters  # Store parameters from this filter
                }

    # Convert to list
    station_list = list(station_dict.values())

    # Apply removals
    if removal_filters:
        station_list = remove_stations(station_list, removal_filters, selector)

    # Sort by station code
    station_list = sorted(station_list, key=lambda s: s['StationCode'])

    # Print summary if requested
    if print_summary:
        if summary_title is None:
            print(' >> Selected station list:', file=sys.stderr)
        else:
            print(f' >> {summary_title}', file=sys.stderr)

        # Assuming print_columns is defined elsewhere
        try:
            print_columns([stationID(s) for s in station_list])
        except NameError:
            # Fallback if print_columns not available
            print(', '.join([stationID(s) for s in station_list]), file=sys.stderr)

    return station_list


def get_norm_year_str(year):
    
    # mk 4 digit year
    try:
        year = int(year)
        # defensively, make sure that the year is positive
        assert year >= 0 
    except:
        raise UtilsException('must provide a positive integer year YY or YYYY');
    
    if 80 <= year <= 99:
        year += 1900
    elif 0 <= year < 80:
        year += 2000        

    return str(year)


def get_norm_doy_str(doy):
    try:
        doy = int(doy)
        # create string version up fround
        return "%03d" % doy
    except:
        raise UtilsException('must provide an integer day of year'); 


def parseIntSet(nputstr=""):

    selection = []
    invalid   = []
    # tokens are comma separated values
    tokens    = [x.strip() for x in nputstr.split(';')]
    for i in tokens:
        if len(i) > 0:
            if i[:1] == "<":
                i = "1-%s" % (i[1:])
        try:
            # typically tokens are plain old integers
            selection.append(int(i))
        except:
            # if not, then it might be a range
            try:
                token = [int(k.strip()) for k in i.split('-')]
                if len(token) > 1:
                    token.sort()
                    # we have items seperated by a dash
                    # try to build a valid range
                    first = token[0]
                    last  = token[-1]
                    for x in range(first, last+1):
                        selection.append(x)
            except:
                # not an int and not a range...
                invalid.append(i)
    # Report invalid tokens before returning valid selection
    if len(invalid) > 0:
        print("Invalid set: " + str(invalid))
        sys.exit(2)
    return selection


def get_platform_id():
    # ask the os for platform information
    uname = os.uname()
    
    # combine to form the platform identification
    return '.'.join((uname[0], uname[2], uname[4]))
    

def human_readable_time(secs):
    
    # start with work time in seconds
    time = secs
    unit = 'secs'
    
    # make human readable work time with units
    if 60 < time < 3600:
        time = time / 60.0
        unit = 'mins'
    elif time > 3600:
        time = time / 3600.0
        unit = 'hours'
        
    return time, unit


def fix_gps_week(file_path):
    
    # example:  g017321.snx.gz --> g0107321.snx.gz
    
    # extract the full file name
    path,full_file_name = os.path.split(file_path);    
    
    # init 
    file_name = full_file_name
    file_ext  = ''
    ext       = None
    
    # remove all file extensions
    while ext != '':
        file_name, ext = os.path.splitext(file_name)
        file_ext       = ext + file_ext
    
    # if the name is short 1 character then add zero
    if len(file_name) == 7:
        file_name = file_name[0:3]+'0'+file_name[3:]
    
    # reconstruct file path
    return  os.path.join(path,file_name+file_ext);


def split_string(str, limit, sep=" "):
    words = str.split()
    if max(list(map(len, words))) > limit:
        raise ValueError("limit is too small")
    res, part, others = [], words[0], words[1:]
    for word in others:
        if len(sep)+len(word) > limit-len(part):
            res.append(part)
            part = word
        else:
            part += sep+word
    if part:
        res.append(part)
    return res


def indent(text, amount, ch=' '):
    padding = amount * ch
    return ''.join(padding + line for line in text.splitlines(True))


# python 3 unpack_from returns bytes instead of strings
def struct_unpack(fs, data):
    return [(f.decode('utf-8', 'ignore') if isinstance(f, (bytes, bytearray)) else f)
            for f in fs.unpack_from(bytes(data, 'utf-8'))]


# python 3 zlib.crc32 requires bytes instead of strings
# also returns a positive int (ints are bignums on python 3)
def crc32(s):
    x = zlib_crc32(bytes(s, 'utf-8'))
    return x - ((x & 0x80000000) << 1)


# Text files

def file_open(path, mode='r'):
    return open(path, mode+'t', encoding='utf-8', errors='ignore')


def file_write(path, data):
    with file_open(path, 'w') as f:
        f.write(data)


def file_append(path, data):
    with file_open(path, 'a') as f:
        f.write(data)


def file_readlines(path):
    with file_open(path) as f:
        return f.readlines()


def file_read_all(path):
    with file_open(path) as f:
        return f.read()


# DDG: frames belonging to known worker-dispatch plumbing -- dozens of these sit between a dispy
# worker thread's own bootstrap and the one frame that actually matters, burying it. Matched by
# substring against each frame's filename.
_TRACEBACK_NOISE = ('/threading.py', '/multiprocessing/', '/pycos/', '/dispy.py', '/dispynode.py')


def format_exc_relevant(noise=_TRACEBACK_NOISE):
    """
    Like traceback.format_exc(), but drops stack frames belonging to threading/multiprocessing/
    dispy/pycos worker-dispatch internals, which are never useful for debugging and bury the
    GeoDE frame(s) that actually matter under dozens of boilerplate ones. Must be called from
    inside an except block (reads the currently-handled exception via sys.exc_info()). Falls back
    to the full, unfiltered trace if every frame turns out to be infrastructure.
    """
    exc_type, exc_value, exc_tb = sys.exc_info()
    if exc_tb is None:
        return traceback.format_exc()

    frames = [f for f in traceback.extract_tb(exc_tb) if not any(n in f.filename for n in noise)]
    if not frames:
        return traceback.format_exc()

    return ''.join(['Traceback (most recent call last):\n'] +
                   traceback.format_list(frames) +
                   traceback.format_exception_only(exc_type, exc_value))


def file_try_remove(path):
    try:
        os.remove(path)
        return True
    except:
        return False


def dir_try_remove(path, recursive=False):
    try:
        if recursive:
            shutil.rmtree(path)
        else:
            os.rmdir(path)
        return True
    except:
        return False

    
def chmod_exec(path):
    # chmod +x path
    f = Path(path)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)


# A custom json converter is needed to fix this exception:
# TypeError: Object of type 'int64' is not JSON serializable
# See https://github.com/automl/SMAC3/issues/453
def json_converter(obj):
    if isinstance(obj, numpy.integer):
        return int(obj)
    elif isinstance(obj, numpy.floating):
        return float(obj)
    elif isinstance(obj, numpy.ndarray):
        return obj.tolist()
        

def load_json(input_json: Union[str, dict] = None):
    """load json file, string, or dict, will always return dict"""
    if isinstance(input_json, dict):
        return input_json
    elif os.path.isfile(input_json):
        with open(input_json, 'r') as f:
            return json.load(f)
    elif isinstance(input_json, str):
        return json.loads(input_json)
    else:
        raise ValueError("Either filepath or json_dict or json_string must be provided")


def create_empty_cfg():
    """
    function to create an empty cfg file with all the parts that are needed
    """
    cfg = """[postgres]
# information to connect to the database (self explanatory)
# replace the keywords in []
hostname = [fqdm]
username = [user]
password = [pass]
database = [gnss_data]

# keys for brdc and sp3 tanks
# $year, $doy, $month, $day, $gpsweek, $gpswkday
#
[archive]
# absolute location of the rinex archive
path = [absolute_path]
repository = [absolute_path]

# parallel execution of certain tasks. If set to false, everything runs in series.
parallel = True

# absolute location of the broadcast orbits, can use keywords declared above
#brdc = [absolute_path]
brdc = [absolute_path]

# absolute location of the sp3 orbits
sp3 = [absolute_path]

# orbit center to use for processing. Separate by commas to try more than one.
sp3_ac = IGS
# precedence of orbital reprocessing campaign
sp3_cs = R03,R02,R01,OPS
# precedence of orbital solution types
sp3_st = FIN,SNX,RAP

[otl]
# location of grdtab to compute OTL
grdtab = [absolute_path]/gamit/gamit/bin/grdtab
# location of the grid to be used by grdtab
otlgrid = [absolute_path]/gamit/tables/otl.grid

[ppp]
# engine used by RunPPP(): 'gpspace' (default, NRCAN PPP) or 'pride' (PRIDE PPP-AR, pdp3)
ppp_engine = gpspace
ppp_path = [absolute_path]/PPP_NRCAN
ppp_exe = [absolute_path]/PPP_NRCAN/source/ppp34613
# ppp_remote_local are the locations, remote and local on each node, where the PPP software lives
# pride_exe/pride_table are only required when ppp_engine = pride
pride_exe = [absolute_path]/PRIDE-PPPAR/bin/pdp3
pride_table = [absolute_path]/PRIDE-PPPAR/table
institution = [institution]
info = [Address, zip code, etc]
# comma separated frames, defined with time interval (see below)
frames = IGb08, IGS14
IGb08 = 1992_1, 2017_28
IGS14 = 2017_29,
atx = /example/igs08_1930.atx, /example/igs08_1930.atx
"""

    file_write('gnss_data.cfg', cfg)


# The 'fqdn' stored in the db is really fqdn + [:port]
def fqdn_parse(fqdn, default_port=None):
    if ':' in fqdn:
        fqdn, port = fqdn.split(':')
        return fqdn, int(port[1])
    else:
        return fqdn, default_port


def plot_rinex_completion(cnn, NetworkCode, StationCode, landscape=False):

    import matplotlib.pyplot as plt

    # find the available data
    rinex = numpy.array(cnn.query_float("""
    SELECT "ObservationYear", "ObservationDOY",
    "Completion" FROM rinex_proc WHERE
    "NetworkCode" = '%s' AND "StationCode" = '%s'""" % (NetworkCode,
                                                        StationCode)))

    if landscape:
        fig, ax = plt.subplots(figsize=(25, 10))
        x = 1
        y = 0
    else:
        fig, ax = plt.subplots(figsize=(10, 25))
        x = 0
        y = 1

    fig.tight_layout(pad=5)
    ax.set_title('RINEX and missing data for %s.%s'
                 % (NetworkCode, StationCode))

    if rinex.size:
        # create a continuous vector for missing data
        md = numpy.arange(1, 367)
        my = numpy.unique(rinex[:, 0])
        for yr in my:

            if landscape:
                ax.plot(md, numpy.repeat(yr, 366), 'o', fillstyle='none',
                        color='silver', markersize=4, linewidth=0.1)
            else:
                ax.plot(numpy.repeat(yr, 366), md, 'o', fillstyle='none',
                        color='silver', markersize=4, linewidth=0.1)

        ax.scatter(rinex[:, x], rinex[:, y],
                   c=['tab:blue' if c >= 0.5 else 'tab:orange'
                      for c in rinex[:, 2]], s=10, zorder=10)

        ax.tick_params(top=True, labeltop=True, labelleft=True,
                       labelright=True, left=True, right=True)
        if landscape:
            plt.yticks(numpy.arange(my.min(), my.max() + 1, step=1))  # Set label locations.
        else:
            plt.xticks(numpy.arange(my.min(), my.max()+1, step=1),
                       rotation='vertical')  # Set label locations.

    ax.grid(True)
    ax.set_axisbelow(True)

    if landscape:
        plt.xlim([0, 367])
        plt.xticks(numpy.arange(0, 368, step=5))  # Set label locations.

        ax.set_xlabel('DOYs')
        ax.set_ylabel('Years')
    else:
        plt.ylim([0, 367])
        plt.yticks(numpy.arange(0, 368, step=5))  # Set label locations.

        ax.set_ylabel('DOYs')
        ax.set_xlabel('Years')

    figfile = io.BytesIO()

    try:
        plt.savefig(figfile, format='png')
        # plt.show()
        figfile.seek(0)  # rewind to beginning of file

        figdata_png = base64.b64encode(figfile.getvalue()).decode()
    except Exception:
        # either no rinex or no station info
        figdata_png = ''

    plt.close()

    return figdata_png


def import_blq(blq_str, NetworkCode=None, StationCode=None):

    if blq_str[0:2] != '$$':
        raise UtilsException('Input string does not appear to be in BLQ format!')

    # header as defined in the new version of the holt.oso.chalmers.se service
    header = """$$ Ocean loading displacement
$$
$$ OTL provider: http://holt.oso.chalmers.se/loading/
$$ Created by Scherneck & Bos
$$
$$ WARNING: All your longitudes were within -90 to +90 degrees
$$ There is a risk that longitude and latitude were swapped
$$ Please verify for yourself that this has not been the case
$$
$$ COLUMN ORDER:  M2  S2  N2  K2  K1  O1  P1  Q1  MF  MM SSA
$$
$$ ROW ORDER:
$$ AMPLITUDES (m)
$$   RADIAL
$$   TANGENTL    EW
$$   TANGENTL    NS
$$ PHASES (degrees)
$$   RADIAL
$$   TANGENTL    EW
$$   TANGENTL    NS
$$
$$ Displacement is defined positive in upwards, South and West direction.
$$ The phase lag is relative to Greenwich and lags positive. The PREM
$$ Green's function is used. The deficit of tidal water mass in the tide
$$ model has been corrected by subtracting a uniform layer of water with
$$ a certain phase lag globally.
$$
$$ CMC:  NO (corr.tide centre of mass)
$$
$$ A constant seawater density of 1030 kg/m^3 is used.
$$
$$ A thin tidal layer is subtracted to conserve water mass.
$$
$$ FES2014b: m2 s2 n2 k2 k1 o1
$$ FES2014b: p1 q1 Mf Mm Ssa
$$
$$ END HEADER
$$"""
    # it's BLQ alright
    pattern = re.compile(r'(?m)(^\s{2}(\w{3}_\w{4})[\s\S]*?^\$\$(?=\s*(?:END TABLE)?$))', re.MULTILINE)
    matches = pattern.findall(blq_str)

    # create a list with the matches
    otl_records = []
    for match in matches:
        net, stn = match[1].split('_')
        # add the match to the list if none requested or if a specific station was requested
        if NetworkCode is None or StationCode is None or (net == NetworkCode and stn == StationCode):
            otl = header + '\n' + match[0].replace('$$ ' + match[1], '$$ %-8s' % stn).replace(match[1], stn)
            otl_records.append({'StationCode': stn,
                                'NetworkCode': net,
                                'otl': otl})

    return otl_records


def print_yellow(skk):
    if os.fstat(0) == os.fstat(1):
        return "\033[93m{}\033[00m" .format(skk)
    else:
        return skk


def azimuthal_equidistant(c_lon: np.ndarray, c_lat: np.ndarray,
                          grid_lon: np.ndarray, grid_lat: np.ndarray):
    # azimuthal equidistant
    cosd = lambda x: np.cos(np.deg2rad(x))
    sind = lambda x: np.sin(np.deg2rad(x))

    if c_lon.size > 1 or c_lat.size > 1:
        raise IndexError('Invalid dimension for projection center point')

    c = np.arccos(sind(c_lat) * sind(grid_lat) +
                  cosd(c_lat) * cosd(grid_lat) * cosd(grid_lon - c_lon))

    # For small c, use Taylor expansion: c/sin(c) ≈ 1 + c²/6
    threshold = 1e-7
    scale_factor = np.where(c < threshold,
                            1.0 + c ** 2 / 6.0,  # Taylor approximation
                            c / np.sin(c))
    k = scale_factor * 6371.0

    x = k * cosd(grid_lat) * sind(grid_lon - c_lon)
    y = k * (cosd(c_lat) * sind(grid_lat) -
             sind(c_lat) * cosd(grid_lat) * cosd(grid_lon - c_lon))

    return x, y

def inverse_azimuthal(c_lon, c_lat, x, y):
    # inverse azimuthal equidistant
    cosd = lambda x: np.cos(np.deg2rad(x))
    sind = lambda x: np.sin(np.deg2rad(x))
    atand = lambda x: np.rad2deg(np.arctan(x))
    asind = lambda x: np.rad2deg(np.arcsin(x))

    r = np.sqrt(np.square(x) + np.square(y)).flatten()
    c = r / 6371.

    i_lat = asind(np.cos(c) * sind(c_lat) + y.flatten() * np.sin(c) * cosd(c_lat) / r)
    i_lon = c_lon + atand((x.flatten() * np.sin(c)) /
                          (r * cosd(c_lat) * np.cos(c) - y.flatten() * sind(c_lat) * np.sin(c)))

    return i_lon, i_lat

