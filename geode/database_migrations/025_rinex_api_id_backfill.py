"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # RinexQC: defensive rinex.api_id backfill. rinex_qc_run (below) FKs to
    # it, but api_id on the rinex table is normally created by GeoDE
    # Studio's Django migrations, not this repo's -- it may not exist yet
    # on an install that hasn't run GeoDE Studio against this database. If
    # GeoDE Studio creates it later, this existence check means we never
    # touch it again.
    if 'api_id' not in cnn.get_columns('rinex').keys():
        print(' >> Adding api_id field to rinex (normally created by GeoDE Studio; backfilling '
              'here so rinex_qc_run can reference it on installs without GeoDE Studio)')
        cnn.begin_transac()
        cnn.query("""
            ALTER TABLE rinex ADD COLUMN api_id INTEGER;

            CREATE SEQUENCE rinex_api_id_seq
                AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
            ALTER SEQUENCE rinex_api_id_seq OWNED BY rinex.api_id;

            UPDATE rinex SET api_id = nextval('rinex_api_id_seq') WHERE api_id IS NULL;

            ALTER TABLE rinex ALTER COLUMN api_id SET DEFAULT nextval('rinex_api_id_seq');
            ALTER TABLE rinex ALTER COLUMN api_id SET NOT NULL;
            ALTER TABLE ONLY rinex ADD CONSTRAINT rinex_api_id_key UNIQUE (api_id);

            COMMENT ON COLUMN rinex.api_id IS
                'Surrogate id for the Django/web-interface API layer; also the FK target for rinex_qc_run.';
        """)
        cnn.commit_transac()
