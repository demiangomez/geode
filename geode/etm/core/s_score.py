"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/14/25 10:34 AM
Author: Demian D. Gomez
"""
import numpy as np
import math
import logging
from psycopg2.errors import ForeignKeyViolation

logger = logging.getLogger(__name__)

from typing import Dict, List, Set, Tuple

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
                 force_events: List = (),
                 include_all_events: bool = False):
        """
        Parameters
        ----------
        include_all_events : bool
            When False (default), applies DISTINCT ON (date::date) so only the
            highest-magnitude event per calendar day is returned — appropriate
            for ETM fitting which supports one jump per day.
            When True, all qualifying events are returned without per-day
            deduplication.  Use this when building event lists for collision
            detection, where two same-day co-located events must both appear so
            their station sets can be intersected.
        """
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

        if include_all_events:
            distinct_clause = ''
            order_clause = 'ORDER BY date ASC, mag DESC'
        else:
            distinct_clause = 'DISTINCT ON (date::date)'
            order_clause = 'ORDER BY date::date ASC, mag DESC'

        jumps = cnn.query_float(
            f"""
            SELECT {distinct_clause} * FROM
            (
                SELECT 2*ASIN(sqrt(sin((radians({lat})-radians(lat))/2)^2 + cos(radians(lat)) *
                cos(radians({lat})) * sin((radians({lon})-radians(lon))/2)^2))*6371 AS distance, *
                FROM earthquakes
                    LEFT JOIN s_score_cache ON id = event_id
                    AND station_code = '{station_code}' AND network_code = '{network_code}'
            )
            WHERE {a} * mag - log10(distance) + {b} + log10({POST_SEISMIC_SCALE_FACTOR}) > 0 AND
            date BETWEEN '%s' AND '%s' AND (mag >= %f %s) {order_clause}"""
            % (sdate.yyyymmdd(), edate.yyyymmdd(), magnitude_limit, cherry_picked_events), as_dict=True)

        # Computed once, not once per row -- same value every iteration.
        current_hash = crc32(str(a) + str(b) + str(POST_SEISMIC_SCALE_FACTOR))

        # Cache-miss rows are collected here and flushed in two bulk round-trips at the
        # end (one DELETE covering every stale key, one multi-row INSERT), instead of up
        # to two individual commits per cache miss -- with a broad event catalog and a
        # large station list, that was the dominant cost of this whole scan when most
        # (station, event) pairs haven't been scored before (a new/larger station list,
        # or a formula-constant change that invalidates every existing hash).
        to_delete_keys = []
        to_write = []

        for j in jumps:
            strike = [float(j['strike1']), float(j['strike2'])] if not math.isnan(j['strike1']) else []
            dip    = [float(j['dip1']), float(j['dip2'])]       if not math.isnan(j['strike1']) else []
            rake   = [float(j['rake1']), float(j['rake2'])]     if not math.isnan(j['strike1']) else []

            dist = distance(lon, lat, j['lon'], j['lat'])

            if j['coseismic'] is not None and j['hash'] == current_hash:
                s_score = float(j['coseismic'])
                p_score = float(j['postseismic'])
            else:
                score = Score(float(j['lat']), float(j['lon']), float(j['depth']), float(j['mag']),
                              strike, dip, rake, j['date'], location=j['location'], event_id=j['id'])

                # capture co-seismic and post-seismic scores. Score.score_values returns
                # a one-element array rather than a scalar whenever the event has a fault
                # mask (pyOkada: the masked branch indexes c_mask/p_mask with a KD-tree
                # query result, which is an array even for a single query point) and a
                # plain scalar otherwise -- so coerce here, once, rather than at each of
                # the uses below. Under numpy 2.x float() on a one-element array raises,
                # which made every mask-carrying event fail on a cache miss.
                s_score, p_score = score.score(lat, lon)
                s_score = float(np.asarray(s_score).reshape(-1)[0])
                p_score = float(np.asarray(p_score).reshape(-1)[0])

                if j['hash'] is not None:
                    to_delete_keys.append((j['id'], network_code, station_code))
                to_write.append({
                    'event_id': j['id'],
                    'network_code': network_code,
                    'station_code': station_code,
                    'coseismic': s_score,
                    'postseismic': p_score,
                    'hash': current_hash,
                })

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

        # Flush every cache-miss write collected above in two round-trips total, rather
        # than up to two per row.
        if to_delete_keys:
            placeholders = ', '.join(['(%s, %s, %s)'] * len(to_delete_keys))
            values = [v for row in to_delete_keys for v in row]
            cnn.cursor.execute(
                f'DELETE FROM s_score_cache WHERE (event_id, network_code, station_code) IN ({placeholders})',
                values
            )
            cnn.cnn.commit()
        if to_write:
            try:
                cnn.insert_many('s_score_cache', to_write)
            except ForeignKeyViolation:
                # station_code/network_code are the same for every row in this batch, so
                # this is the same outcome the original per-row insert would give for
                # every one of them: the station isn't in the stations table (e.g. from
                # a kml), so none of these can be cached -- ignore and move on.
                pass


def batch_score_scan(cnn: Cnn, stations: List[Tuple[str, str, float, float]],
                     sdate: Date, edate: Date, magnitude_limit: float
                     ) -> Tuple[List[Earthquake], Dict[str, Set[str]]]:
    """
    Same per-(station, event) s-score scan ScoreTable(include_all_events=True) performs,
    but for every station in one query instead of one full scan of the earthquakes table
    per station. Calling ScoreTable once per station -- as
    EtmStacker.prepare_earthquake_list originally did -- re-scans the whole earthquakes
    table (computing the haversine distance per row) from scratch each time; for a
    70-station list that scan alone (a few seconds per station server-side) was the
    dominant cost of preparing the earthquake list, independent of how many of those
    (station, event) pairs are already cached (see ScoreTable's own cache-write batching
    above, which addresses a different cost: writing new cache entries).

    This does not replace ScoreTable, which single-station callers elsewhere still use.

    stations: list of (network_code, station_code, lat, lon) tuples.

    Returns (candidate_events, event_stations): a deduplicated Earthquake list (one per
    unique qualifying event) and a map of event id -> set of 'net.code' strings that see
    it -- the same two outputs prepare_earthquake_list's own per-station loop built by
    calling ScoreTable in a loop and merging the results by hand.
    """
    candidate_events: List[Earthquake] = []
    seen_ids: Set[str] = set()
    event_stations: Dict[str, Set[str]] = {}

    if not stations:
        return candidate_events, event_stations

    station_coords: Dict[Tuple[str, str], Tuple[float, float]] = {
        (net, code): (lat, lon) for net, code, lat, lon in stations
    }
    values_list = ', '.join(
        f"('{net}', '{code}', {lat!r}, {lon!r})" for net, code, lat, lon in stations
    )

    rows = cnn.query_float(
        f"""
        SELECT * FROM
        (
            SELECT 2*ASIN(sqrt(sin((radians(s.lat)-radians(eq.lat))/2)^2 + cos(radians(eq.lat)) *
            cos(radians(s.lat)) * sin((radians(s.lon)-radians(eq.lon))/2)^2))*6371 AS distance,
            s.net_code, s.stn_code, eq.*,
            cache.coseismic, cache.postseismic, cache.hash
            FROM earthquakes eq
            CROSS JOIN (VALUES {values_list}) AS s(net_code, stn_code, lat, lon)
            LEFT JOIN s_score_cache cache
                ON cache.event_id = eq.id AND cache.network_code = s.net_code
                AND cache.station_code = s.stn_code
        ) sub
        WHERE {a} * mag - log10(distance) + {b} + log10({POST_SEISMIC_SCALE_FACTOR}) > 0
          AND date BETWEEN '{sdate.yyyymmdd()}' AND '{edate.yyyymmdd()}' AND mag >= {magnitude_limit}
        ORDER BY date ASC, mag DESC
        """,
        as_dict=True
    )

    current_hash = crc32(str(a) + str(b) + str(POST_SEISMIC_SCALE_FACTOR))
    to_delete_keys = []
    to_write = []

    for j in rows:
        net, code = j['net_code'], j['stn_code']
        station_id = f'{net}.{code}'
        lat, lon = station_coords[(net, code)]

        strike = [float(j['strike1']), float(j['strike2'])] if not math.isnan(j['strike1']) else []
        dip    = [float(j['dip1']), float(j['dip2'])]       if not math.isnan(j['strike1']) else []
        rake   = [float(j['rake1']), float(j['rake2'])]     if not math.isnan(j['strike1']) else []

        dist = distance(lon, lat, j['lon'], j['lat'])

        if j['coseismic'] is not None and j['hash'] == current_hash:
            s_score = float(j['coseismic'])
            p_score = float(j['postseismic'])
        else:
            score = Score(float(j['lat']), float(j['lon']), float(j['depth']), float(j['mag']),
                         strike, dip, rake, j['date'], location=j['location'], event_id=j['id'])
            s_score, p_score = score.score(lat, lon)

            if j['hash'] is not None:
                to_delete_keys.append((j['id'], net, code))
            to_write.append({
                'event_id': j['id'],
                'network_code': net,
                'station_code': code,
                'coseismic': float(s_score),
                'postseismic': float(p_score),
                'hash': current_hash,
            })

        if s_score > 0 or p_score > 0:
            jump_type = JumpType.COSEISMIC_JUMP_DECAY if s_score > 0 else JumpType.POSTSEISMIC_ONLY
            if j['id'] not in seen_ids:
                seen_ids.add(j['id'])
                candidate_events.append(Earthquake(
                    id=j['id'], lat=j['lat'], lon=j['lon'], date=Date(datetime=j['date']),
                    depth=j['depth'], magnitude=j['mag'], distance=dist, location=j['location'],
                    strike=strike, dip=dip, rake=rake, jump_type=jump_type))
            event_stations.setdefault(j['id'], set()).add(station_id)

    if to_delete_keys:
        placeholders = ', '.join(['(%s, %s, %s)'] * len(to_delete_keys))
        values = [v for row in to_delete_keys for v in row]
        cnn.cursor.execute(
            f'DELETE FROM s_score_cache WHERE (event_id, network_code, station_code) IN ({placeholders})',
            values
        )
        cnn.cnn.commit()
    if to_write:
        try:
            cnn.insert_many('s_score_cache', to_write)
        except ForeignKeyViolation:
            pass

    return candidate_events, event_stations

