"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # New table: sources_metadata
    # Stores URL/path structure for metadata files (IGS logs, stninfo).
    # Same fields as sources_servers; each sources_servers record can
    # reference one sources_metadata record via metadata_source_id.

    sources_metadata_exists = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'sources_metadata');
        """, as_dict=True)

    if not sources_metadata_exists[0]['exists']:
        print(' >> Creating sources_metadata table')
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE sources_metadata (
                id         SERIAL PRIMARY KEY,
                protocol   VARCHAR NOT NULL CHECK (protocol IN ('ftp', 'http', 'sftp',
                           'https', 'ftpa', 'ftps', 'FTP', 'HTTP', 'SFTP', 'HTTPS', 'FTPA', 'FTPS')),
                fqdn       VARCHAR NOT NULL,
                username   VARCHAR,
                "password" VARCHAR,
                "path"     VARCHAR,
                "format"   VARCHAR REFERENCES sources_formats(format)
                           DEFAULT 'DEFAULT_FORMAT'
            );

            COMMENT ON TABLE sources_metadata IS
                'URL/path templates for metadata files (IGS site logs, station info). '
                'Referenced by sources_servers.metadata_source_id.';
        """)
        cnn.commit_transac()
