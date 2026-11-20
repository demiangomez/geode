"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # s_score_cache for storing the s_score values and not calculate them all the time

    s_score_cache = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public'
            AND table_name = 's_score_cache');
        """, as_dict=True)

    if not s_score_cache[0]['exists']:
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE s_score_cache (
                network_code VARCHAR(3) NOT NULL,
                station_code VARCHAR(4) NOT NULL,
                event_id VARCHAR(40) NOT NULL,
                coseismic NUMERIC(10,6),
                postseismic NUMERIC(10,6),
                hash BIGINT,
                PRIMARY KEY (network_code, station_code, event_id),
                FOREIGN KEY (network_code, station_code) 
                    REFERENCES stations("NetworkCode", "StationCode") 
                    ON DELETE CASCADE,
                FOREIGN KEY (event_id) 
                    REFERENCES earthquakes(id) 
                    ON DELETE CASCADE
            );
            CREATE INDEX idx_s_score_cache_hash ON s_score_cache(hash);
            CREATE INDEX idx_s_score_cache_station ON s_score_cache(network_code, station_code);
            CREATE INDEX idx_s_score_cache_event ON s_score_cache(event_id);
                """)
        cnn.commit_transac()
