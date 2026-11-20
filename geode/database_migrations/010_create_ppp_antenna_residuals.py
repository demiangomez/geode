"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # ppp_antenna_residuals for storing the residual values after PPP processing

    antenna_residuals = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'ppp_antenna_residuals');
        """, as_dict=True)

    if not antenna_residuals[0]['exists']:
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE ppp_antenna_residuals (
                network_code    VARCHAR(3)  NOT NULL,
                station_code    VARCHAR(4)  NOT NULL,
                reference_frame VARCHAR(20) NOT NULL,
                system          CHARACTER(1),
                year            SMALLINT NOT NULL,
                doy             SMALLINT NOT NULL,
                antenna_code    VARCHAR(22) NOT NULL,
                radome_code     VARCHAR(7)  NOT NULL,
                residuals       DOUBLE PRECISION[91],  -- elevation-dependent residuals, index 1=0deg to 91=90deg
                CONSTRAINT ppp_antenna_residuals_pkey
                    PRIMARY KEY (network_code, station_code, year, doy, reference_frame),
                FOREIGN KEY (network_code, station_code)
                    REFERENCES stations("NetworkCode", "StationCode")
                    ON DELETE CASCADE,
                FOREIGN KEY (network_code, station_code, year, doy, reference_frame)
                    REFERENCES ppp_soln("NetworkCode", "StationCode", "Year", "DOY", "ReferenceFrame")
                    ON UPDATE CASCADE ON DELETE CASCADE
            ) WITH (
                autovacuum_enabled = TRUE);
            CREATE INDEX idx_ppp_antenna_residuals_station ON ppp_antenna_residuals(network_code, station_code);
            CREATE INDEX idx_ppp_antenna_residuals_date ON ppp_antenna_residuals(year, doy);
            CREATE INDEX idx_ppp_antenna_residuals_antenna ON ppp_antenna_residuals(antenna_code, radome_code);
                """)
        cnn.commit_transac()
