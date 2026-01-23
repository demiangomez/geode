"""
Project: Geodesy Database Engine (GeoDE)
Date: 9/14/25 10:34 AM
Author: Demian D. Gomez
"""
import numpy as np
import math
import logging
from psycopg2.errors import ForeignKeyViolation

logger = logging.getLogger(__name__)

from typing import List

from ...dbConnection import Cnn
from ...pyDate import Date
from ...pyOkada import Score
from ...Utils import crc32
from ..core.type_declarations import JumpType
from ..core.data_classes import Earthquake

# from Gómez et al 2024
a = 0.5261
b =-1.1478

POST_SEISMIC_SCALE_FACTOR = 1.5

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

class ScoreTable(object):
    """
    Given a connection to the database, lat and lon of point of interest, and date range, find all the seismic events
    with level-2 s-score = 1. If no strike, dip, and rake parameters available, return events with level-1 s-score > 0
    Returns a list with [mag, date, lon, lat] ordered by ascending date and descending magnitude.
    """
    def __init__(self, cnn: Cnn,
                 network_code: str,
                 station_code: str,
                 lat: float,
                 lon: float,
                 sdate: Date,
                 edate: Date,
                 magnitude_limit: float,
                 force_events: List = ()):

        self.table: List[Earthquake] = []

        logger.info(f'Loading s-score table for {lat:.8f} {lon:.8f} from {sdate} to {edate}')

        # get the earthquakes based on Mike's expression
        # speed up the process by performing the s-score
        # calc in the postgres server
        # return only the largest magnitude if aftershock are present on a single day using DISTINCT ON (date::date)
        # can also override the magnitude_limit using the force_events list (by ids)

        if len(force_events):
            cherry_picked_events = " OR id IN ('%s')" % "', '".join(force_events)
        else:
            cherry_picked_events = ''

        jumps = cnn.query_float(
            f"""
            SELECT DISTINCT ON (date::date) * FROM 
            (
                SELECT 2*ASIN(sqrt(sin((radians({lat})-radians(lat))/2)^2 + cos(radians(lat)) * 
                cos(radians({lat})) * sin((radians({lon})-radians(lon))/2)^2))*6371 AS distance, * 
                FROM earthquakes
                    LEFT JOIN s_score_cache ON id = event_id 
                    AND station_code = '{station_code}' AND network_code = '{network_code}'
            )
            WHERE {a} * mag - log10(distance) + {b} + log10({POST_SEISMIC_SCALE_FACTOR}) > 0 AND
            date BETWEEN '%s' AND '%s' AND (mag >= %f %s) ORDER BY date::date ASC, mag DESC"""
            % (sdate.yyyymmdd(), edate.yyyymmdd(), magnitude_limit, cherry_picked_events), as_dict=True)

        for j in jumps:
            strike = [float(j['strike1']), float(j['strike2'])] if not math.isnan(j['strike1']) else []
            dip    = [float(j['dip1']), float(j['dip2'])]       if not math.isnan(j['strike1']) else []
            rake   = [float(j['rake1']), float(j['rake2'])]     if not math.isnan(j['strike1']) else []

            dist = distance(lon, lat, j['lon'], j['lat'])

            if j['coseismic'] is not None and j['hash'] == crc32(str(a) + str(b) + str(POST_SEISMIC_SCALE_FACTOR)):
                s_score = float(j['coseismic'])
                p_score = float(j['postseismic'])
            else:
                score = Score(float(j['lat']), float(j['lon']), float(j['depth']), float(j['mag']),
                              strike, dip, rake, j['date'], location=j['location'], event_id=j['id'])

                # capture co-seismic and post-seismic scores
                s_score, p_score = score.score(lat, lon)

                if j['hash'] is not None:
                    cnn.delete('s_score_cache',
                               event_id=j['id'],
                               network_code=network_code,
                               station_code=station_code)
                try:
                    cnn.insert('s_score_cache',
                               event_id=j['id'],
                               network_code=network_code,
                               station_code=station_code,
                               coseismic=float(s_score),
                               postseismic=float(p_score),
                               hash=crc32(str(a) + str(b) + str(POST_SEISMIC_SCALE_FACTOR)))
                except ForeignKeyViolation:
                    # the station is not in the stations table, ignore error (probably station from kml)
                    pass

            if s_score > 0:
                # seismic score came back > 0, add jump
                event = Earthquake(
                    id = j['id'],
                    lat = j['lat'],
                    lon = j['lon'],
                    date = Date(datetime=j['date']),
                    depth = j['depth'],
                    magnitude = j['mag'],
                    distance = dist,
                    location = j['location'],
                    strike=strike,
                    dip=dip,
                    rake=rake,
                    jump_type = JumpType.COSEISMIC_JUMP_DECAY)

                self.table.append(event)
            elif p_score > 0:
                # seismic score came back == 0, but post-seismic score > 0 add jump
                # seismic score came back > 0, add jump
                event = Earthquake(
                    id=j['id'],
                    lat=j['lat'],
                    lon=j['lon'],
                    date=Date(datetime=j['date']),
                    depth=j['depth'],
                    magnitude=j['mag'],
                    distance=dist,
                    location=j['location'],
                    strike=strike,
                    dip=dip,
                    rake=rake,
                    jump_type=JumpType.POSTSEISMIC_ONLY)

                self.table.append(event)


