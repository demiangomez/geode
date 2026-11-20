"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # New column: sources_stations.metadata_hash
    # Stores the CRC32 hash of the last downloaded metadata file for each station.
    # Used to detect changes without re-parsing and calling the API.

    if 'metadata_hash' not in cnn.get_columns('sources_stations').keys():
        print(' >> Adding metadata_hash to sources_stations')
        cnn.begin_transac()
        cnn.query("""
            ALTER TABLE sources_stations
                ADD COLUMN metadata_hash BIGINT;

            COMMENT ON COLUMN sources_stations.metadata_hash IS
                'CRC32 hash of the last downloaded metadata file for this station. '
                'Used to detect file changes without re-parsing.';
        """)
        cnn.commit_transac()
