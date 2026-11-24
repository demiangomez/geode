"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # Allow 'ftps'/'FTPS' (explicit FTP over TLS) in the protocol
    # CHECK constraints of sources_servers and sources_metadata.

    protocol_check = cnn.query_float("""
        SELECT pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE conname = 'sources_servers_protocol_check';
        """, as_dict=True)

    if protocol_check and 'ftps' not in protocol_check[0]['def'].lower():
        print(' >> Allowing ftps protocol in sources_servers/sources_metadata')
        cnn.begin_transac()
        cnn.query("""
            ALTER TABLE sources_servers  DROP CONSTRAINT sources_servers_protocol_check;
            ALTER TABLE sources_servers  ADD  CONSTRAINT sources_servers_protocol_check
                CHECK (protocol IN ('ftp', 'http', 'sftp', 'https', 'ftpa', 'ftps',
                                     'FTP', 'HTTP', 'SFTP', 'HTTPS', 'FTPA', 'FTPS'));

            ALTER TABLE sources_metadata DROP CONSTRAINT sources_metadata_protocol_check;
            ALTER TABLE sources_metadata ADD  CONSTRAINT sources_metadata_protocol_check
                CHECK (protocol IN ('ftp', 'http', 'sftp', 'https', 'ftpa', 'ftps',
                                     'FTP', 'HTTP', 'SFTP', 'HTTPS', 'FTPA', 'FTPS'));
        """)
        cnn.commit_transac()
