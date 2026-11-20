"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

from ..dbConnection import DatabaseError


def migrate(cnn):
    # RinexQC: best-effort lz4 compression on rinex_qc_signal_bins's array
    # columns. Storage optimization only, never required for correctness
    # -- lz4 column compression needs PostgreSQL built with --with-lz4,
    # which is not guaranteed just because the server is new enough (14+)
    # to support the feature at all; confirmed unsupported on at least one
    # real GeoDE deployment. Attempted in its own transaction, separate
    # from the table creation above, so a server without lz4 support never
    # breaks table creation (and checked on every connection, not only
    # when the table is first created, so it still applies automatically
    # if the server is ever upgraded to support it).
    bins_compression = cnn.query_float("""
        SELECT attcompression FROM pg_attribute
        WHERE attrelid = 'rinex_qc_signal_bins'::regclass AND attname = 'n_obs';
        """, as_dict=True)

    if bins_compression and bins_compression[0]['attcompression'] != 'l':
        try:
            cnn.begin_transac()
            cnn.query("""
                ALTER TABLE rinex_qc_signal_bins
                    ALTER COLUMN n_obs     SET COMPRESSION lz4,
                    ALTER COLUMN mp_rms_mm SET COMPRESSION lz4,
                    ALTER COLUMN snr_ddbhz SET COMPRESSION lz4;
            """)
            cnn.commit_transac()
        except DatabaseError:
            cnn.rollback_transac()
