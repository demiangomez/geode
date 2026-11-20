"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # gamit_antenna_residuals for storing the residual values after DD processing

    antenna_residuals = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public'
            AND table_name = 'gamit_antenna_residuals');
        """, as_dict=True)

    if not antenna_residuals[0]['exists']:
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE gamit_antenna_residuals (
                network_code VARCHAR(3) NOT NULL,
                station_code VARCHAR(4) NOT NULL,
                project      VARCHAR(20),
                system       CHARACTER(1),
                subnet       SMALLINT NOT NULL,
                year         SMALLINT NOT NULL,
                doy          SMALLINT NOT NULL,
                antenna_code VARCHAR(22) NOT NULL,
                radome_code  VARCHAR(7) NOT NULL,
                residuals    DOUBLE PRECISION[91],  -- elevation-dependent residuals, index 1=0deg to 91=90deg
                CONSTRAINT gamit_antenna_residuals_pkey 
                    PRIMARY KEY (network_code, station_code, project, subnet, year, doy, system),
                FOREIGN KEY (network_code, station_code) 
                    REFERENCES stations("NetworkCode", "StationCode") 
                    ON DELETE CASCADE,
                FOREIGN KEY (project, subnet, year, doy, system) 
                    REFERENCES gamit_stats("Project", subnet, "Year", "DOY", system) 
                    ON DELETE CASCADE
            ) WITH (
                autovacuum_enabled = TRUE);
            CREATE INDEX idx_gamit_antenna_residuals_station ON gamit_antenna_residuals(network_code, station_code);
            CREATE INDEX idx_gamit_antenna_residuals_date ON gamit_antenna_residuals(year, doy);
            CREATE INDEX idx_gamit_antenna_residuals_antenna ON gamit_antenna_residuals(antenna_code, radome_code);
                """)
        cnn.commit_transac()
