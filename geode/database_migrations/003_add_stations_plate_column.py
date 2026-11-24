"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # New plate field in table stations
    from ..station_selector import get_tectonic_plate

    if 'plate' not in cnn.get_columns('stations').keys():
        print(' >> Adding plate field to stations, may take a few seconds')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE stations
        ADD COLUMN plate VARCHAR(2) DEFAULT NULL;
        """)
        cnn.commit_transac()
        # now add tectonic plates to all stations
        stations = cnn.query_float('SELECT lat, lon, api_id FROM stations', as_dict=True)
        for stn in stations:
            if stn['lon'] is not None and stn['lat'] is not None:
                plate, _ = get_tectonic_plate(stn['lon'], stn['lat'])
                if plate:
                    cnn.update('stations', {'plate': plate}, api_id=stn['api_id'])
    else:
        # check that all stations
        stations = cnn.query_float('SELECT lat, lon, api_id FROM stations '
                                   'WHERE "NetworkCode" NOT LIKE \'?%%\' AND plate IS NULL', as_dict=True)
        for stn in stations:
            if stn['lon'] is not None and stn['lat'] is not None:
                plate, _ = get_tectonic_plate(stn['lon'], stn['lat'])
                if plate:
                    cnn.update('stations', {'plate': plate}, api_id=stn['api_id'])
