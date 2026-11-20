"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # Agency metadata: which organizations contribute, own, or host which
    # stations, and how to reach them. Three tables:
    #   agency_roles    -- lookup of relationship types (owner, host, ...),
    #                      same shape as the Django app's api_stationrole
    #                      (id + name); kept here (not api_stationrole)
    #                      because agency_stations needs to FK to it and
    #                      this repo cannot add FKs into api_* tables (see
    #                      knowledge/architecture/external-django-app.md).
    #   agencies        -- one row per contributing organization; api_id is
    #                      its primary key directly (name is unique, not
    #                      the key, so renaming an agency never cascades).
    #   agency_stations -- many-to-many: which agency has which role on
    #                      which station. A given agency can only be
    #                      associated with a given station once. References
    #                      stations by its .api_id surrogate, not its own
    #                      (NetworkCode, StationCode) composite primary key,
    #                      same reasoning as rinex_qc_run.rinex_id ->
    #                      rinex.api_id above.
    agencies_exist = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'agencies');
        """, as_dict=True)

    if not agencies_exist[0]['exists']:
        # agency_stations FKs to stations.api_id. That column is normally
        # created by GeoDE Studio's Django migrations, not this repo's --
        # same situation as rinex.api_id above -- so back it onto the
        # stations table defensively here too, for installs that have
        # never run GeoDE Studio against this database.
        if 'api_id' not in cnn.get_columns('stations').keys():
            print(' >> Adding api_id field to stations (normally created by GeoDE Studio; '
                  'backfilling here so agency_stations can reference it on installs without '
                  'GeoDE Studio)')
            cnn.begin_transac()
            cnn.query("""
                ALTER TABLE stations ADD COLUMN api_id INTEGER;

                CREATE SEQUENCE stations_api_id_seq
                    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
                ALTER SEQUENCE stations_api_id_seq OWNED BY stations.api_id;

                UPDATE stations SET api_id = nextval('stations_api_id_seq') WHERE api_id IS NULL;

                ALTER TABLE stations ALTER COLUMN api_id SET DEFAULT nextval('stations_api_id_seq');
                ALTER TABLE stations ALTER COLUMN api_id SET NOT NULL;
                ALTER TABLE ONLY stations ADD CONSTRAINT stations_api_id_key UNIQUE (api_id);

                COMMENT ON COLUMN stations.api_id IS
                    'Surrogate id for the Django/web-interface API layer; also the FK target for agency_stations.station_id.';
            """)
            cnn.commit_transac()

        print(' >> Creating agency tables (agency_roles, agencies, agency_stations)...')
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE agency_roles (
                api_id SERIAL      PRIMARY KEY,
                name   VARCHAR(50) NOT NULL,
                CONSTRAINT agency_roles_name_key UNIQUE (name)
            );

            COMMENT ON TABLE agency_roles IS
                'Lookup of agency/station relationship types (e.g. owner, host). Same shape as '
                'the Django app''s api_stationrole (id + name); see the comment above this block '
                'for why it lives here instead.';
            COMMENT ON COLUMN agency_roles.api_id IS
                'Primary key, and the surrogate id used by the Django/web-interface API layer; '
                'also the FK target for agency_stations.agency_role_id.';

            INSERT INTO agency_roles (name) VALUES ('owner'), ('host');

            CREATE TABLE agencies (
                api_id                   SERIAL  PRIMARY KEY,
                name                     VARCHAR NOT NULL,
                primary_contact_name     VARCHAR NOT NULL,
                primary_contact_email    VARCHAR NOT NULL,
                primary_contact_phone    VARCHAR NOT NULL,
                secondary_contact_name   VARCHAR,
                secondary_contact_email  VARCHAR,
                rtn_names                VARCHAR[],
                CONSTRAINT agencies_name_key UNIQUE (name)
            );

            COMMENT ON TABLE agencies IS
                'One row per organization contributing, owning, or hosting GNSS stations. '
                'Linked to the stations it is associated with via agency_stations.';
            COMMENT ON COLUMN agencies.name IS
                'Full agency name, e.g. ''Ohio Department of Transportation''. Unique, but not '
                'the primary key -- agency_stations references agencies.api_id, so renaming an '
                'agency never cascades anywhere.';
            COMMENT ON COLUMN agencies.rtn_names IS
                'Real-time network(s) operated by this agency, e.g. {''Ohio RTN''}.';
            COMMENT ON COLUMN agencies.api_id IS
                'Primary key, and the surrogate id used by the Django/web-interface API layer; '
                'also the FK target for agency_stations.agency_id.';

            CREATE TABLE agency_stations (
                agency_id      INTEGER NOT NULL REFERENCES agencies(api_id)
                               ON UPDATE CASCADE ON DELETE CASCADE,
                station_id     INTEGER NOT NULL REFERENCES stations(api_id)
                               ON UPDATE CASCADE ON DELETE CASCADE,
                agency_role_id INTEGER NOT NULL REFERENCES agency_roles(api_id)
                               ON UPDATE CASCADE,
                description    TEXT,
                api_id         INTEGER NOT NULL,
                CONSTRAINT agency_stations_pkey      PRIMARY KEY (agency_id, station_id),
                CONSTRAINT agency_stations_api_id_key UNIQUE (api_id)
            );

            CREATE INDEX agency_stations_station_idx ON agency_stations (station_id);

            CREATE SEQUENCE agency_stations_api_id_seq
                AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
            ALTER SEQUENCE agency_stations_api_id_seq OWNED BY agency_stations.api_id;
            ALTER TABLE ONLY agency_stations
                ALTER COLUMN api_id SET DEFAULT nextval('agency_stations_api_id_seq'::regclass);

            COMMENT ON TABLE agency_stations IS
                'Many-to-many: which agency has which role on which station. agency_stations_pkey '
                'enforces that a given agency is only ever associated once with a given station; '
                'agency_role_id/description describe that single relationship.';
            COMMENT ON COLUMN agency_stations.agency_id IS
                'FK to agencies.api_id, which is agencies'' own primary key.';
            COMMENT ON COLUMN agency_stations.station_id IS
                'FK to stations.api_id, not stations'' own (NetworkCode, StationCode) composite '
                'primary key.';
            COMMENT ON COLUMN agency_stations.description IS
                'Free-text description of this agency''s relationship to the station, e.g. '
                '''instrument'' or ''site owner''.';
            COMMENT ON COLUMN agency_stations.api_id IS
                'Surrogate id for the Django/web-interface API layer.';
        """)
        cnn.commit_transac()
