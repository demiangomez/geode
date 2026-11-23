#!/usr/bin/env python
"""
Project: Geodetic Database Engine (GeoDE)
Date: 11/20/2023
Author: Demian D. Gomez

This script assigns country codes to the stations table
"""
from geode import dbConnection
import country_converter as coco

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter


def main():
    geolocator = Nominatim(user_agent="GeoDE")

    cnn = dbConnection.Cnn('gnss_data.cfg')

    stations = cnn.query('''SELECT * FROM stations
                         WHERE country_code IS NULL AND
                         lat IS NOT NULL and lon IS NOT NULL''').dictresult()

    ''' add minimum delay of 1s to comply with nominatim usage policy &
avoid timeout error
    https://operations.osmfoundation.org/policies/nominatim/
    https://gis.stackexchange.com/questions/331144
/bulk-reverse-geocoding-with-geopy-using-built-in-rate-limiter
    '''

    reverse = RateLimiter(geolocator.reverse, min_delay_seconds=1)

    for stn in stations:
        print('Station %s.%s has no ISO3 lat: %12.8f lon: %13.8f'
              % (stn['NetworkCode'], stn['StationCode'],
                 stn['lat'], stn['lon']))

        # Nominatim has no country_code for Antarctica (no sovereign
        # boundaries in OSM), so stations south of the Antarctic Treaty
        # line get ATA directly instead of relying on reverse geocoding.
        if stn['lat'] <= -60:
            print(' -- Updating %s.%s with ISO3 ATA (south of -60 lat)'
                  % (stn['NetworkCode'], stn['StationCode']))
            cnn.update('stations', {'country_code': 'ATA'}, **stn)
            continue

        location = reverse("%f, %f" % (stn['lat'], stn['lon']))
        if location and 'country_code' in location.raw['address'].keys():
            ISO3 = coco.convert(names=location.raw['address']['country_code'],
                                to='ISO3')
            print(' -- Updating %s.%s with ISO3 %s'
                  % (stn['NetworkCode'], stn['StationCode'], ISO3))
            cnn.update('stations', {'country_code': ISO3}, **stn)
        else:
            print(' -- Could not determine ISO3 for %s.%s'
                  % (stn['NetworkCode'], stn['StationCode']))


if __name__ == '__main__':
    main()
