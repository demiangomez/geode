"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # ppp_antenna_residuals -> ppp_soln FK: add ON UPDATE CASCADE.
    # station_merge's _move_time_tables() UPDATEs ppp_soln's primary key
    # columns (NetworkCode/StationCode) in place when merging stations,
    # instead of deleting and re-inserting rows. ON DELETE CASCADE alone
    # only covers row deletion; without ON UPDATE CASCADE, Postgres has
    # no action to take on referencing ppp_antenna_residuals rows and
    # rejects the parent UPDATE with a ForeignKeyViolation. The CREATE
    # TABLE above already includes it for fresh installs; this upgrades
    # a table created before the clause was added.

    fk_def = cnn.query_float("""
        SELECT pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE conname = 'ppp_antenna_residuals_network_code_station_code_year_doy_r_fkey';
        """, as_dict=True)

    if fk_def and 'on update cascade' not in fk_def[0]['def'].lower():
        print(' >> Adding ON UPDATE CASCADE to ppp_antenna_residuals -> ppp_soln FK')
        cnn.begin_transac()
        cnn.query("""
            ALTER TABLE ppp_antenna_residuals
                DROP CONSTRAINT ppp_antenna_residuals_network_code_station_code_year_doy_r_fkey;
            ALTER TABLE ppp_antenna_residuals
                ADD CONSTRAINT ppp_antenna_residuals_network_code_station_code_year_doy_r_fkey
                    FOREIGN KEY (network_code, station_code, year, doy, reference_frame)
                    REFERENCES ppp_soln("NetworkCode", "StationCode", "Year", "DOY", "ReferenceFrame")
                    ON UPDATE CASCADE ON DELETE CASCADE;
        """)
        cnn.commit_transac()
