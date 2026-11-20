"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # New column: sources_servers.metadata_source_id
    # Foreign key to sources_metadata for metadata download paths.

    if 'metadata_source_id' not in cnn.get_columns('sources_servers').keys():
        print(' >> Adding metadata_source_id to sources_servers')
        cnn.begin_transac()
        cnn.query("""
            ALTER TABLE sources_servers
                ADD COLUMN metadata_source_id INTEGER REFERENCES sources_metadata(id);
        """)
        cnn.commit_transac()
