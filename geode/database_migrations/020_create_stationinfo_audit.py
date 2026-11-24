"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # New table: stationinfo_audit
    # Tracks audit findings from metadata comparisons, keyed by session hash.
    # Used to prevent re-flagging findings that humans have already reviewed.

    stationinfo_audit_exists = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'stationinfo_audit');
        """, as_dict=True)

    if not stationinfo_audit_exists[0]['exists']:
        print(' >> Creating stationinfo_audit table')
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE stationinfo_audit (
                api_id          SERIAL PRIMARY KEY,

                -- which station
                "NetworkCode"   VARCHAR(3)   NOT NULL,
                "StationCode"   VARCHAR(4)   NOT NULL,

                -- CRC32 fingerprint of the external session (or DB record for ORPHAN)
                session_hash    BIGINT       NOT NULL,

                -- what Claude found
                finding_type    VARCHAR(30)  NOT NULL,
                action_required VARCHAR(10)  NOT NULL,

                -- db_record: {"DateStart": "YYYY-MM-DD HH:MM:SS"} to identify DB session
                db_record       JSONB,
                claude_summary  TEXT,

                -- structured field values for programmatic updates (only differing fields)
                db_field_values   JSONB,
                file_field_values JSONB,

                -- human disposition (NULL = not yet reviewed)
                reviewed_by     VARCHAR(80),
                reviewed_at     TIMESTAMP,
                disposition     VARCHAR(10),
                review_notes    TEXT,

                -- audit trail
                created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMP    NOT NULL DEFAULT NOW()
            );

            -- Prevents duplicate audit rows for the same session content
            CREATE UNIQUE INDEX stationinfo_audit_unique
                ON stationinfo_audit ("NetworkCode", "StationCode", session_hash);

            -- Index for fast lookups by station
            CREATE INDEX idx_stationinfo_audit_station
                ON stationinfo_audit ("NetworkCode", "StationCode");

            COMMENT ON TABLE stationinfo_audit IS
                'Tracks metadata comparison findings per station session. '
                'session_hash is the CRC32 fingerprint of the external session content.';
            COMMENT ON COLUMN stationinfo_audit.session_hash IS
                'CRC32 of the canonical stninfo-format line for the external session, '
                'or the DB record for ORPHAN_SESSION findings. Matches StationInfoRecord.hash.';
            COMMENT ON COLUMN stationinfo_audit.disposition IS
                'Human decision: APPLIED, DISMISSED, DEFERRED, or NO_ACTION (auto-set for matches).';
            COMMENT ON COLUMN stationinfo_audit.db_field_values IS
                'JSONB object with field names as keys and current DB values. '
                'Only contains fields that differ between DB and external file. '
                'Field names match StationInfoRecord attributes (e.g., ReceiverCode, AntennaHeight).';
            COMMENT ON COLUMN stationinfo_audit.file_field_values IS
                'JSONB object with field names as keys and recommended values from external file. '
                'Only contains fields that differ between DB and external file. '
                'Field names match StationInfoRecord attributes (e.g., ReceiverCode, AntennaHeight).';
        """)
        cnn.commit_transac()
