#!/usr/bin/env python
"""
Project: Parallel.Archive
Date: 3/3/17 9:56 AM
Author: Demian D. Gomez

This class queries the USGS ComCat earthquake catalog directly (FDSN event web
service + the ComCat detail/product feed) and its purpose is to update the
table "earthquakes" in the db with the latest events (M >= 6).
"""
import calendar
import xmltodict
import argparse
from datetime import datetime, timezone
from tqdm import tqdm

# deps
import requests

# app
from geode import dbConnection
from geode.Utils import add_version_argument

TIMEFMT2 = '%Y-%m-%d %H:%M:%S.%f'

FDSN_EVENT_URL  = 'https://earthquake.usgs.gov/fdsnws/event/1/query'
COMCAT_DETAIL_URL = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/detail/%s.geojson'

WEEKSECS = 86400*14


class ProductNotFoundError(Exception):
    pass


class SummaryEvent:
    """Minimal stand-in for libcomcat's SummaryEvent, built from a ComCat geojson feature."""

    def __init__(self, feature):
        props = feature['properties']
        self.id        = feature['id']
        self.time      = datetime.utcfromtimestamp(props['time'] / 1000.0)
        self.latitude  = feature['geometry']['coordinates'][1]
        self.longitude = feature['geometry']['coordinates'][0]
        self.depth     = props.get('depth', feature['geometry']['coordinates'][2])
        self.magnitude = props['mag']
        self.location  = props.get('place')


def search(starttime, endtime, minmagnitude):
    # https://earthquake.usgs.gov/fdsnws/event/1/ -- FDSN event web service
    params = {
        'format':       'geojson',
        'starttime':    starttime.strftime('%Y-%m-%dT%H:%M:%S'),
        'endtime':      endtime.strftime('%Y-%m-%dT%H:%M:%S'),
        'minmagnitude': minmagnitude,
    }
    r = requests.get(FDSN_EVENT_URL, params=params, timeout=60)
    r.raise_for_status()

    return [SummaryEvent(feature) for feature in r.json()['features']]


def get_product_bytes(eventid, product_type, content_file):
    # ComCat's per-event detail feed lists every product (moment-tensor, shakemap, ...)
    # attached to the event; the first entry in each product's list is the preferred one.
    r = requests.get(COMCAT_DETAIL_URL % eventid, timeout=60)
    r.raise_for_status()

    products = r.json()['properties'].get('products', {})

    if product_type not in products:
        raise ProductNotFoundError('event %s has no %s product' % (eventid, product_type))

    contents = products[product_type][0]['contents']

    if content_file not in contents:
        raise ProductNotFoundError('event %s %s product has no %s content'
                                   % (eventid, product_type, content_file))

    cr = requests.get(contents[content_file]['url'], timeout=60)
    cr.raise_for_status()

    return cr.content


def getTimeSegments2(starttime, endtime):
    #startsecs = int(starttime.strftime('%s'))
    startsecs = calendar.timegm(starttime.timetuple())
    #endsecs = int(endtime.strftime('%s'))
    endsecs = calendar.timegm(endtime.timetuple())
    starts = list(range(startsecs, endsecs, WEEKSECS))
    ends   = list(range(startsecs+WEEKSECS+1, endsecs+WEEKSECS, WEEKSECS))
    if ends[-1] > endsecs:
        ends[-1] = endsecs
    if len(starts) != len(ends):
        raise IndexError('Number of time segment starts/ends does not match for times: "%s" and "%s"' %
                         (starttime, endtime))

    return [(datetime.utcfromtimestamp(start),
             datetime.utcfromtimestamp(end))
            for start, end in zip(starts, ends)]


class AddEarthquakes:

    def __init__(self, cnn, stime=None, etime=None):

        # check the last earthquake in the db

        if stime is None:
            quakes = cnn.query('SELECT max(date) as mdate FROM earthquakes')
            stime = quakes.dictresult()[0].get('mdate')

            if stime is None:
                # no events in the table, add all
                rinex = cnn.query('SELECT min("ObservationSTime") as mdate FROM rinex')
                stime = rinex.dictresult()[0].get('mdate')

        if etime is None:
            etime = datetime.now(timezone.utc)

        # we used to do a count of how many events would be returned,
        # but it turns out that doing the count takes almost as much time
        # as a query that actually returns the data.  So, here we're just
        # going to split the time segment up into one-week chunks and assume
        # that no individual segment will return more than the 20,000 event limit.
        segments  = getTimeSegments2(stime, etime)

        print('Breaking request into %i segments.' % len(segments))
        print('Requesting data by segment. This might take a few minutes...\n')

        for stime, etime in tqdm(segments, ncols=120):
            # sys.stderr.write('%s - Getting data for %s => %s\n' % (comcat.ShakeDateTime.now(),stime,etime))
            # https://earthquake.usgs.gov/fdsnws/event/1/
            retry = 0
            eventlist = []
            while retry < 3:
                try:
                    eventlist = search(starttime=stime, endtime=etime, minmagnitude=6)
                    break
                except requests.exceptions.RequestException as e:
                    retry += 1
                    tqdm.write('There was a connection error while downloading segment %s %s, retrying %i, %s'
                               % (str(stime), str(etime), retry, str(e)))

            if not len(eventlist):
                tqdm.write('No events found')

            # eventlist is a list of SummaryEvent objects
            for event in eventlist:
                event_date = datetime.strptime(event.time.strftime(TIMEFMT2), TIMEFMT2)

                rs_event = cnn.query('SELECT * FROM earthquakes WHERE id = \'%s\'' % event.id)

                if not len(rs_event.dictresult()):
                    try:
                        # DDG: try to get the moment-tensor solution, if it exists
                        mt = get_product_bytes(event.id,
                                               'moment-tensor',
                                               'quakeml.xml').decode('utf-8')
                        # convert from XML to DICT
                        mtp = xmltodict.parse(mt)

                        nodal_planes = mtp['q:quakeml']['eventParameters']['event']['focalMechanism']['nodalPlanes']
                        strike1 = float(nodal_planes['nodalPlane1']['strike']['value'])
                        dip1 = float(nodal_planes['nodalPlane1']['dip']['value'])
                        rake1 = float(nodal_planes['nodalPlane1']['rake']['value'])
                        strike2 = float(nodal_planes['nodalPlane2']['strike']['value'])
                        dip2 = float(nodal_planes['nodalPlane2']['dip']['value'])
                        rake2 = float(nodal_planes['nodalPlane2']['rake']['value'])

                    except (ProductNotFoundError, KeyError, Exception) as e:
                        tqdm.write('Event id %s (%s mag=%.1f) has no moment-tensor solution: %s'
                                   % (event.id, event_date, event.magnitude, str(e)))
                        strike1 = 'NaN'
                        dip1 = 'NaN'
                        rake1 = 'NaN'
                        strike2 = 'NaN'
                        dip2 = 'NaN'
                        rake2 = 'NaN'

                    try:
                        # print('inserting', event)
                        cnn.insert('earthquakes',
                                   id       = event.id,
                                   date     = event_date,
                                   lat      = event.latitude,
                                   lon      = event.longitude,
                                   depth    = event.depth,
                                   mag      = event.magnitude,
                                   strike1  = strike1,
                                   dip1     = dip1,
                                   rake1    = rake1,
                                   strike2  = strike2,
                                   dip2     = dip2,
                                   rake2    = rake2,
                                   location = event.location)
                        tqdm.write('inserting event %s in the database' % event.id)
                    except Exception as e:
                        tqdm.write('Insert failed! %s' % str(e))
                        pass
                else:
                    tqdm.write('event %s %s already exists in the database' % (event.id, event.location))


def main():
    parser = argparse.ArgumentParser(description='Update earthquakes table')
    add_version_argument(parser)
    _ = parser.parse_args()

    cnn = dbConnection.Cnn('gnss_data.cfg')
    AddEarthquakes(cnn)


if __name__ == '__main__':
    main()
