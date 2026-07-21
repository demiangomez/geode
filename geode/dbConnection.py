"""
Project: Geodetic Database Engine (GeoDE)
Date: 02/16/2017
Author: Demian D. Gomez

This class is used to connect to the database and handles inserts, updates and selects
It also handles the error, info and warning messages
"""

import sys
import platform
import configparser
import inspect
import re
import psycopg2
import psycopg2.extras
import psycopg2.extensions
import numpy as np
from decimal import Decimal

# app
from .Utils import file_read_all, file_append, create_empty_cfg


DB_HOST = 'localhost'
DB_USER = 'postgres'
DB_PASS = ''
DB_NAME = 'gnss_data'


DEBUG = False


def cast_array_to_float(recordset):

    if len(recordset) > 0:
        if not isinstance(recordset[0], dict):
            result = []
            for record in recordset:
                new_record = []
                for field in record:
                    if isinstance(field, list):
                        new_record.append([float(value) if isinstance(value, Decimal) else value for value in field])
                    else:
                        if isinstance(field, Decimal):
                            new_record.append(float(field))
                        else:
                            new_record.append(field)

                result.append(tuple(new_record))

            return result
        else:
            # Convert any DECIMAL values to float
            for record in recordset:
                for key, value in record.items():
                    if isinstance(value, Decimal):
                        record[key] = float(value)
                    elif isinstance(value, list) and all(isinstance(i, Decimal) for i in value):
                        record[key] = [float(i) for i in value]

    return recordset


# class to match the pygreSQl structure using psycopg2
class query_obj(object):
    def __init__(self, cursor):
        self.rows = []
        # to maintain backwards compatibility
        try:
            self.rows = cast_array_to_float(cursor.fetchall())
        except psycopg2.ProgrammingError as e:
            if 'no results to fetch' in str(e):
                pass
            else:
                raise e

    def dictresult(self):
        return self.rows

    def ntuples(self):
        return len(self.rows)

    def getresult(self):
        return [tuple(d.values()) for d in self.rows]

    def __len__(self):
        return len(self.rows)


def debug(s):
    if DEBUG:
        file_append('/tmp/db.log', "DB: %s\n" % s)


def run_db_migrations(cnn: 'Cnn'):
    ##################################################################
    # New field in table api_visitgnssdatafiles
    if 'rinexed' not in cnn.get_columns('api_visitgnssdatafiles').keys():
        print(' >> Adding rinexed field to api_visitgnssdatafiles')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE api_visitgnssdatafiles
        ADD COLUMN rinexed BOOLEAN DEFAULT FALSE;
        """)
        cnn.commit_transac()

    ##################################################################
    # New AntennaDAZ field in table stationinfo
    if 'AntennaDAZ' not in cnn.get_columns('stationinfo').keys():
        print(' >> Adding AntennaDAZ field to stationinfo')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE stationinfo 
        ADD COLUMN "AntennaDAZ" NUMERIC(4,1) DEFAULT 0.0
        CHECK ("AntennaDAZ" >= 0.0 AND "AntennaDAZ" <= 360.0);
        """)
        cnn.commit_transac()

    ##################################################################
    # New plate field in table stations
    from .station_selector import get_tectonic_plate

    if 'plate' not in cnn.get_columns('stations').keys():
        print(' >> Adding plate field to stations, may take a few seconds')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE stations
        ADD COLUMN plate VARCHAR(2) DEFAULT NULL;
        """)
        cnn.commit_transac()
        # now add tectonic plates to all stations
        stations = cnn.query_float('SELECT lat, lon, api_id FROM stations', as_dict=True)
        for stn in stations:
            if stn['lon'] is not None and stn['lat'] is not None:
                plate, _ = get_tectonic_plate(stn['lon'], stn['lat'])
                if plate:
                    cnn.update('stations', {'plate': plate}, api_id=stn['api_id'])
    else:
        # check that all stations
        stations = cnn.query_float('SELECT lat, lon, api_id FROM stations '
                                   'WHERE "NetworkCode" NOT LIKE \'?%%\' AND plate IS NULL', as_dict=True)
        for stn in stations:
            if stn['lon'] is not None and stn['lat'] is not None:
                plate, _ = get_tectonic_plate(stn['lon'], stn['lat'])
                if plate:
                    cnn.update('stations', {'plate': plate}, api_id=stn['api_id'])

    ##################################################################
    # modifications to ppp_soln to store big int values
    fields = cnn.get_columns('ppp_soln')

    if 'orbit' not in fields.keys():
        print(' >> Adding orbit field to ppp_soln')
        # New field in table ppp_soln present, no need to migrate.
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE ppp_soln
        ADD COLUMN orbit VARCHAR(100) DEFAULT '';
        """)
        cnn.commit_transac()

    ##################################################################
    if fields['hash'].lower() != 'bigint':
        # check the database to modify the ppp_soln table hash column from integer to bigint
        print(' >> Converting hash column in ppp_soln to BIGINT. This operation might take a while...')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE ppp_soln
        ALTER COLUMN hash TYPE BIGINT;
        """)
        cnn.commit_transac()

    ##################################################################
    # check precision of lat lon height and auto_[x|y|z] in stations table
    stn_types = cnn.query_float("""
    SELECT 
        column_name,
        data_type,
        numeric_precision,
        numeric_scale
    FROM information_schema.columns 
        WHERE table_name = 'stations' 
        AND column_name = 'auto_x';
    """, as_dict=True)
    if stn_types[0]['numeric_precision'] is None:
        print(' >> Converting lat lon height and auto_[x|y|z] types')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE stations ALTER COLUMN auto_x TYPE NUMERIC(16,5) USING ROUND(auto_x::numeric, 5);
        ALTER TABLE stations ALTER COLUMN auto_y TYPE NUMERIC(16,5) USING ROUND(auto_y::numeric, 5);
        ALTER TABLE stations ALTER COLUMN auto_z TYPE NUMERIC(16,5) USING ROUND(auto_z::numeric, 5);
        ALTER TABLE stations ALTER COLUMN lat    TYPE NUMERIC(12,9) USING ROUND(lat::numeric, 9);
        ALTER TABLE stations ALTER COLUMN lon    TYPE NUMERIC(12,9) USING ROUND(lon::numeric, 9);
        ALTER TABLE stations ALTER COLUMN height TYPE NUMERIC(10,5) USING ROUND(height::numeric, 5);
        """)
        cnn.commit_transac()

    ##################################################################
    # For the Mask object: check that the new fields exist or create them
    if 'density' not in cnn.get_columns('earthquakes').keys():
        cnn.begin_transac()
        cnn.query("""
                    ALTER TABLE earthquakes
                    ADD COLUMN density INTEGER   DEFAULT NULL,
                    ADD COLUMN c_kml   TEXT      DEFAULT NULL,
                    ADD COLUMN cp_kml  TEXT      DEFAULT NULL;
                    """)
        cnn.commit_transac()

    # check that the index exists
    idx = cnn.query("SELECT * FROM pg_indexes WHERE tablename = 'earthquakes' "
                    "AND indexname = 'earthquake_id_key'")

    if not len(idx):
        cnn.begin_transac()
        cnn.query("""CREATE UNIQUE INDEX earthquake_id_key ON earthquakes (id);""")
        cnn.commit_transac()

    ##################################################################
    # s_score_cache for storing the s_score values and not calculate them all the time

    s_score_cache = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public'
            AND table_name = 's_score_cache');
        """, as_dict=True)

    if not s_score_cache[0]['exists']:
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE s_score_cache (
                network_code VARCHAR(3) NOT NULL,
                station_code VARCHAR(4) NOT NULL,
                event_id VARCHAR(40) NOT NULL,
                coseismic NUMERIC(10,6),
                postseismic NUMERIC(10,6),
                hash BIGINT,
                PRIMARY KEY (network_code, station_code, event_id),
                FOREIGN KEY (network_code, station_code) 
                    REFERENCES stations("NetworkCode", "StationCode") 
                    ON DELETE CASCADE,
                FOREIGN KEY (event_id) 
                    REFERENCES earthquakes(id) 
                    ON DELETE CASCADE
            );
            CREATE INDEX idx_s_score_cache_hash ON s_score_cache(hash);
            CREATE INDEX idx_s_score_cache_station ON s_score_cache(network_code, station_code);
            CREATE INDEX idx_s_score_cache_event ON s_score_cache(event_id);
                """)
        cnn.commit_transac()

    ##################################################################
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

    ##################################################################
    # ppp_antenna_residuals for storing the residual values after PPP processing

    antenna_residuals = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'ppp_antenna_residuals');
        """, as_dict=True)

    if not antenna_residuals[0]['exists']:
        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE ppp_antenna_residuals (
                network_code    VARCHAR(3)  NOT NULL,
                station_code    VARCHAR(4)  NOT NULL,
                reference_frame VARCHAR(20) NOT NULL,
                system          CHARACTER(1),
                year            SMALLINT NOT NULL,
                doy             SMALLINT NOT NULL,
                antenna_code    VARCHAR(22) NOT NULL,
                radome_code     VARCHAR(7)  NOT NULL,
                residuals       DOUBLE PRECISION[91],  -- elevation-dependent residuals, index 1=0deg to 91=90deg
                CONSTRAINT ppp_antenna_residuals_pkey
                    PRIMARY KEY (network_code, station_code, year, doy, reference_frame),
                FOREIGN KEY (network_code, station_code)
                    REFERENCES stations("NetworkCode", "StationCode")
                    ON DELETE CASCADE,
                FOREIGN KEY (network_code, station_code, year, doy, reference_frame)
                    REFERENCES ppp_soln("NetworkCode", "StationCode", "Year", "DOY", "ReferenceFrame")
                    ON DELETE CASCADE
            ) WITH (
                autovacuum_enabled = TRUE);
            CREATE INDEX idx_ppp_antenna_residuals_station ON ppp_antenna_residuals(network_code, station_code);
            CREATE INDEX idx_ppp_antenna_residuals_date ON ppp_antenna_residuals(year, doy);
            CREATE INDEX idx_ppp_antenna_residuals_antenna ON ppp_antenna_residuals(antenna_code, radome_code);
                """)
        cnn.commit_transac()

    run_this = False
    if run_this:
        ##################################################################
        # gamit_projects: per-project GAMIT processing configuration.
        # "Project" in gamit_soln/gamit_soln_excl/gamit_subnets/gamit_stats/
        # gamit_antenna_residuals becomes a FK into this table (cascading on
        # delete/update), so deleting a project here removes all of its
        # solutions, subnets, stats and antenna residuals in one shot.

        gamit_projects = cnn.query_float("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'gamit_projects');
            """, as_dict=True)

        if not gamit_projects[0]['exists']:
            print(' >> Creating and populating gamit_projects table, please wait...')
            cnn.begin_transac()
            cnn.query("""
                CREATE TABLE gamit_projects (
                    project          VARCHAR(20)  NOT NULL,
                    network_type     VARCHAR(20)  NOT NULL DEFAULT 'global',
                    cluster_size     INTEGER      NOT NULL DEFAULT 25,
                    ties             INTEGER      NOT NULL DEFAULT 10,
                    process_defaults TEXT,
                    sestbl           TEXT,
                    solutions_dir    TEXT,
                    experiment_type  VARCHAR(20)  NOT NULL DEFAULT 'baseline',
                    experiment_name  VARCHAR(4),
                    org              VARCHAR(3),
                    noftp            BOOLEAN      NOT NULL DEFAULT TRUE,
                    eop_type         VARCHAR(10)  NOT NULL DEFAULT 'usno',
                    systems          CHARACTER(1)[],
                    overconst_action VARCHAR(10),
                    sigma_floor_h    NUMERIC(6,4) NOT NULL DEFAULT 0.0100,
                    sigma_floor_v    NUMERIC(6,4) NOT NULL DEFAULT 0.0300,
                    station_list     VARCHAR(8)[],
                    api_id           INTEGER      NOT NULL,
                    CONSTRAINT gamit_projects_pkey PRIMARY KEY (project),
                    CONSTRAINT gamit_projects_api_id_key UNIQUE (api_id),
                    CONSTRAINT gamit_projects_network_type_check
                        CHECK (network_type IN ('regional', 'global')),
                    CONSTRAINT gamit_projects_experiment_type_check
                        CHECK (experiment_type IN ('baseline', 'relax', 'orbit')),
                    CONSTRAINT gamit_projects_overconst_action_check
                        CHECK (overconst_action IS NULL
                               OR overconst_action IN ('inflate', 'relax', 'remove', 'delete')),
                    CONSTRAINT gamit_projects_systems_check
                        CHECK (systems IS NULL OR systems <@ ARRAY['G','R','E','C']::character(1)[])
                ) WITH (autovacuum_enabled = TRUE);
    
                CREATE SEQUENCE gamit_projects_api_id_seq
                    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
                ALTER SEQUENCE gamit_projects_api_id_seq OWNED BY gamit_projects.api_id;
                ALTER TABLE ONLY gamit_projects
                    ALTER COLUMN api_id SET DEFAULT nextval('gamit_projects_api_id_seq'::regclass);
    
                COMMENT ON TABLE gamit_projects IS
                    'Per-project GAMIT processing configuration; one row per distinct "Project" value used in gamit_soln and related tables.';
                COMMENT ON COLUMN gamit_projects.project IS
                    'Project identifier, matches "Project" in gamit_soln/gamit_soln_excl/gamit_subnets/gamit_stats/gamit_antenna_residuals.';
                COMMENT ON COLUMN gamit_projects.network_type IS
                    'Type of network processed: regional or global.';
                COMMENT ON COLUMN gamit_projects.cluster_size IS
                    'Number of stations per processing cluster/subnet.';
                COMMENT ON COLUMN gamit_projects.ties IS
                    'Number of tie stations shared between subnets.';
                COMMENT ON COLUMN gamit_projects.process_defaults IS
                    'Contents of the GAMIT process.defaults file used for this project.';
                COMMENT ON COLUMN gamit_projects.sestbl IS
                    'Contents of the GAMIT sestbl. file used for this project.';
                COMMENT ON COLUMN gamit_projects.solutions_dir IS
                    'Filesystem path where GAMIT solution files for this project are stored.';
                COMMENT ON COLUMN gamit_projects.experiment_type IS
                    'GAMIT experiment type: baseline, relax, or orbit.';
                COMMENT ON COLUMN gamit_projects.experiment_name IS
                    'Experiment name as required by GAMIT (4-character code).';
                COMMENT ON COLUMN gamit_projects.org IS
                    'Organization code responsible for this project.';
                COMMENT ON COLUMN gamit_projects.noftp IS
                    'If true, do not fetch orbit/EOP products via FTP for this project.';
                COMMENT ON COLUMN gamit_projects.eop_type IS
                    'Earth orientation parameters source used, e.g. usno.';
                COMMENT ON COLUMN gamit_projects.systems IS
                    'GNSS constellations processed: G=GPS, R=GLONASS, E=Galileo, C=BeiDou.';
                COMMENT ON COLUMN gamit_projects.overconst_action IS
                    'Action to take when a solution is overconstrained: inflate, relax, remove, or delete.';
                COMMENT ON COLUMN gamit_projects.sigma_floor_h IS
                    'Minimum horizontal sigma floor (m) applied to solutions.';
                COMMENT ON COLUMN gamit_projects.sigma_floor_v IS
                    'Minimum vertical sigma floor (m) applied to solutions.';
                COMMENT ON COLUMN gamit_projects.station_list IS
                    'Stations processed under this project, as NetworkCode.StationCode entries.';
                COMMENT ON COLUMN gamit_projects.api_id IS
                    'Surrogate id for the Django/web-interface API layer.';
    
                -- backfill: one row per project already seen across the GAMIT tables.
                -- Fields we have no historical record of (process_defaults, sestbl,
                -- solutions_dir, experiment_name, org, systems, overconst_action) are
                -- left NULL and must be filled in manually per project.
                INSERT INTO gamit_projects (project)
                SELECT DISTINCT "Project" FROM gamit_soln
                UNION
                SELECT DISTINCT "Project" FROM gamit_soln_excl
                UNION
                SELECT DISTINCT "Project" FROM gamit_subnets
                UNION
                SELECT DISTINCT "Project" FROM gamit_stats
                UNION
                SELECT DISTINCT project FROM gamit_antenna_residuals
                ON CONFLICT (project) DO NOTHING;
    
                -- station_list is derivable from existing data, so backfill it for real
                -- instead of leaving it NULL.
                UPDATE gamit_projects gp
                SET station_list = sub.stations
                FROM (
                    SELECT "Project" AS project,
                           array_agg(DISTINCT "NetworkCode" || '.' || "StationCode") AS stations
                    FROM gamit_soln
                    GROUP BY "Project"
                ) sub
                WHERE gp.project = sub.project;
    
                -- wire up the cascading FKs from the existing GAMIT tables
                ALTER TABLE ONLY gamit_soln
                    ADD CONSTRAINT gamit_soln_project_fkey FOREIGN KEY ("Project")
                    REFERENCES gamit_projects(project) ON UPDATE CASCADE ON DELETE CASCADE;
                ALTER TABLE ONLY gamit_soln_excl
                    ADD CONSTRAINT gamit_soln_excl_project_fkey FOREIGN KEY ("Project")
                    REFERENCES gamit_projects(project) ON UPDATE CASCADE ON DELETE CASCADE;
                ALTER TABLE ONLY gamit_subnets
                    ADD CONSTRAINT gamit_subnets_project_fkey FOREIGN KEY ("Project")
                    REFERENCES gamit_projects(project) ON UPDATE CASCADE ON DELETE CASCADE;
                ALTER TABLE ONLY gamit_stats
                    ADD CONSTRAINT gamit_stats_project_fkey FOREIGN KEY ("Project")
                    REFERENCES gamit_projects(project) ON UPDATE CASCADE ON DELETE CASCADE;
                ALTER TABLE ONLY gamit_antenna_residuals
                    ADD CONSTRAINT gamit_antenna_residuals_project_fkey FOREIGN KEY (project)
                    REFERENCES gamit_projects(project) ON UPDATE CASCADE ON DELETE CASCADE;
                    """)
            cnn.commit_transac()

        ##################################################################
        # reference_frames: one row per reference-frame realization (a "stack"),
        # spanning multiple processing engines. The same frame_name can exist
        # once per engine (e.g. "igs20" built from GAMIT and, later, a separate
        # "igs20" built from PAGES) since each engine keeps its own physical
        # stacks table (stacks, and in the future stacks_pages). Because a
        # single FK can't conditionally point at one of two tables depending on
        # the "engine" column, referential integrity to the per-engine stacks
        # table and to the per-engine projects table is enforced with triggers
        # instead of plain FKs:
        #   - reference_frames_validate_project_trigger (BEFORE INSERT/UPDATE):
        #     checks that `project` exists in gamit_projects/pages_projects,
        #     whichever `engine` selects.
        #   - reference_frames_cascade_delete_trigger (AFTER DELETE): deletes
        #     the matching rows from stacks/stacks_pages when a frame row is
        #     deleted, since ON DELETE CASCADE can only target one parent table.
        # Both triggers dynamically check whether the target table exists yet,
        # so the 'pages' branch is a no-op until stacks_pages/pages_projects
        # are actually created -- no changes needed here when PAGES ships.

        reference_frames = cnn.query_float("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'reference_frames');
            """, as_dict=True)

        if not reference_frames[0]['exists']:
            print(' >> Creating and populating reference_frames table, please wait...')
            cnn.begin_transac()
            cnn.query("""
                CREATE TABLE reference_frames (
                    frame_name       VARCHAR(20)  NOT NULL,
                    engine           VARCHAR(10)  NOT NULL,
                    project          VARCHAR(20)  NOT NULL,
                    fixed_plate      VARCHAR(2),
                    constraints_id   VARCHAR(20),
                    position_wrms    NUMERIC(8,5),
                    velocity_wrms    NUMERIC(8,5),
                    periodic_wrms    NUMERIC(8,5)[],
                    euler_pole       NUMERIC[],
                    euler_pole_stations VARCHAR(8)[],
                    first_epoch      TIMESTAMP WITHOUT TIME ZONE,
                    last_epoch       TIMESTAMP WITHOUT TIME ZONE,
                    created          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                    modified         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                    api_id           INTEGER      NOT NULL,
                    CONSTRAINT reference_frames_pkey PRIMARY KEY (frame_name, engine),
                    CONSTRAINT reference_frames_api_id_key UNIQUE (api_id),
                    CONSTRAINT reference_frames_engine_check
                        CHECK (engine IN ('gamit', 'pages')),
                    CONSTRAINT reference_frames_periodic_wrms_check
                        CHECK (periodic_wrms IS NULL OR array_length(periodic_wrms, 1) = 4),
                    CONSTRAINT reference_frames_euler_pole_check
                        CHECK (euler_pole IS NULL OR array_length(euler_pole, 1) = 3)
                ) WITH (autovacuum_enabled = TRUE);
    
                CREATE SEQUENCE reference_frames_api_id_seq
                    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
                ALTER SEQUENCE reference_frames_api_id_seq OWNED BY reference_frames.api_id;
                ALTER TABLE ONLY reference_frames
                    ALTER COLUMN api_id SET DEFAULT nextval('reference_frames_api_id_seq'::regclass);
    
                COMMENT ON TABLE reference_frames IS
                    'One row per reference-frame realization (stack), across all processing engines. frame_name is unique per engine, not globally: the same name may exist once per engine.';
                COMMENT ON COLUMN reference_frames.frame_name IS
                    'Reference frame / stack name, matches "name" in the per-engine stacks table (e.g. stacks.name for engine=gamit).';
                COMMENT ON COLUMN reference_frames.engine IS
                    'Processing engine that produced this frame: gamit or pages. Determines which per-engine stacks/projects table this row corresponds to.';
                COMMENT ON COLUMN reference_frames.project IS
                    'Project used to build this frame; validated against gamit_projects.project or pages_projects.project depending on engine (see reference_frames_validate_project_trigger).';
                COMMENT ON COLUMN reference_frames.fixed_plate IS
                    'Two-character tectonic plate code this frame is fixed to (see stations.plate). NULL means a no-net-rotation frame.';
                COMMENT ON COLUMN reference_frames.constraints_id IS
                    'Free-text label grouping the rows in reference_frame_constraints that apply to this frame (often, but not necessarily, the same string as frame_name, e.g. an inherited ITRF constraint set). Not an enforced FK: one constraints_id can have many stations and be reused by more than one reference_frames row.';
                COMMENT ON COLUMN reference_frames.position_wrms IS
                    'Overall WRMS scatter (m) of the position-space realization, from Stack.align_spaces().';
                COMMENT ON COLUMN reference_frames.velocity_wrms IS
                    'Overall WRMS scatter (m/yr) of the velocity-space realization, from Stack.align_spaces().';
                COMMENT ON COLUMN reference_frames.periodic_wrms IS
                    'WRMS scatter (m) of the periodic-space realization, from Stack.remove_common_modes(). Fixed 4-element order: [annual_cos, annual_sin, semiannual_cos, semiannual_sin].';
                COMMENT ON COLUMN reference_frames.euler_pole IS
                    'Euler pole solution when fixed_plate is set, from cart2euler() in FixPlate.py. Full-precision numeric, fixed 3-element order: [pole_lat_deg, pole_lon_deg, rotation_rate_deg_per_myr]. NULL for no-net-rotation frames and, for now, for all frames (not yet written by any process).';
                COMMENT ON COLUMN reference_frames.euler_pole_stations IS
                    'Stations used to compute euler_pole, as NetworkCode.StationCode entries (see FixPlate.py). NULL for now, same as euler_pole.';
                COMMENT ON COLUMN reference_frames.first_epoch IS
                    'Date of the earliest solution available in this stack (see stacks.Year/DOY for engine=gamit). Not auto-maintained: must be updated by the process that builds/extends the stack.';
                COMMENT ON COLUMN reference_frames.last_epoch IS
                    'Date of the latest solution available in this stack (see stacks.Year/DOY for engine=gamit). Not auto-maintained: must be updated by the process that builds/extends the stack.';
                COMMENT ON COLUMN reference_frames.created IS
                    'When this reference_frames row was created. For rows backfilled from pre-existing stacks, this is the backfill date, not the original stack build date (no historical record of that exists).';
                COMMENT ON COLUMN reference_frames.modified IS
                    'When this reference_frames row was last updated; auto-maintained by reference_frames_set_modified_trigger on every UPDATE.';
                COMMENT ON COLUMN reference_frames.api_id IS
                    'Surrogate id for the Django/web-interface API layer.';
    
                -- Validate `project` against the right per-engine projects table.
                -- Skips validation (with a warning) if that table doesn't exist yet,
                -- so 'pages' rows are simply unchecked until pages_projects ships.
                CREATE OR REPLACE FUNCTION reference_frames_validate_project() RETURNS TRIGGER AS $BODY$
                DECLARE
                    project_table TEXT;
                    found         BOOLEAN;
                BEGIN
                    project_table := CASE NEW.engine
                        WHEN 'gamit' THEN 'gamit_projects'
                        WHEN 'pages' THEN 'pages_projects'
                        ELSE NULL
                    END;
    
                    IF project_table IS NULL THEN
                        RAISE EXCEPTION 'reference_frames: unknown engine ''%''', NEW.engine;
                    END IF;
    
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = project_table
                    ) THEN
                        RAISE WARNING 'reference_frames: % does not exist yet, skipping project validation for %/%',
                            project_table, NEW.engine, NEW.project;
                        RETURN NEW;
                    END IF;
    
                    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE project = $1)', project_table)
                        INTO found USING NEW.project;
    
                    IF NOT found THEN
                        RAISE EXCEPTION 'reference_frames: project ''%'' not found in % (engine=%)',
                            NEW.project, project_table, NEW.engine;
                    END IF;
    
                    RETURN NEW;
                END;
                $BODY$ LANGUAGE plpgsql;
    
                CREATE TRIGGER reference_frames_validate_project_trigger
                    BEFORE INSERT OR UPDATE ON reference_frames
                    FOR EACH ROW EXECUTE FUNCTION reference_frames_validate_project();
    
                -- Cascade a frame delete into the matching per-engine stacks table.
                -- Skips (no-op) if that table doesn't exist yet.
                CREATE OR REPLACE FUNCTION reference_frames_cascade_delete() RETURNS TRIGGER AS $BODY$
                DECLARE
                    stack_table TEXT;
                BEGIN
                    stack_table := CASE OLD.engine
                        WHEN 'gamit' THEN 'stacks'
                        WHEN 'pages' THEN 'stacks_pages'
                        ELSE NULL
                    END;
    
                    IF stack_table IS NOT NULL AND EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = stack_table
                    ) THEN
                        EXECUTE format('DELETE FROM %I WHERE name = $1', stack_table) USING OLD.frame_name;
                    END IF;
    
                    RETURN OLD;
                END;
                $BODY$ LANGUAGE plpgsql;
    
                CREATE TRIGGER reference_frames_cascade_delete_trigger
                    AFTER DELETE ON reference_frames
                    FOR EACH ROW EXECUTE FUNCTION reference_frames_cascade_delete();
    
                -- Cascade a frame_name rename into the matching per-engine stacks table.
                -- Only fires when frame_name actually changes; assumes engine stays the
                -- same during a rename (moving a frame between engines is a much larger
                -- operation than a rename and isn't handled here). Skips (no-op) if the
                -- target stacks table doesn't exist yet.
                CREATE OR REPLACE FUNCTION reference_frames_cascade_rename() RETURNS TRIGGER AS $BODY$
                DECLARE
                    stack_table TEXT;
                BEGIN
                    IF NEW.frame_name IS DISTINCT FROM OLD.frame_name THEN
                        stack_table := CASE OLD.engine
                            WHEN 'gamit' THEN 'stacks'
                            WHEN 'pages' THEN 'stacks_pages'
                            ELSE NULL
                        END;
    
                        IF stack_table IS NOT NULL AND EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_name = stack_table
                        ) THEN
                            EXECUTE format('UPDATE %I SET name = $1 WHERE name = $2', stack_table)
                                USING NEW.frame_name, OLD.frame_name;
                        END IF;
                    END IF;
    
                    RETURN NEW;
                END;
                $BODY$ LANGUAGE plpgsql;
    
                CREATE TRIGGER reference_frames_cascade_rename_trigger
                    AFTER UPDATE OF frame_name ON reference_frames
                    FOR EACH ROW EXECUTE FUNCTION reference_frames_cascade_rename();
    
                -- Auto-maintain `modified` on every UPDATE (created is set once, at
                -- INSERT, via its column DEFAULT and is never touched again here).
                CREATE OR REPLACE FUNCTION reference_frames_set_modified() RETURNS TRIGGER AS $BODY$
                BEGIN
                    NEW.modified = now();
                    RETURN NEW;
                END;
                $BODY$ LANGUAGE plpgsql;
    
                CREATE TRIGGER reference_frames_set_modified_trigger
                    BEFORE UPDATE ON reference_frames
                    FOR EACH ROW EXECUTE FUNCTION reference_frames_set_modified();
    
                -- backfill: one row per distinct (name, Project) already built in stacks.
                -- If a frame name was ever (incorrectly) built from more than one
                -- Project, only one arbitrary mapping survives (ON CONFLICT DO NOTHING);
                -- fixed_plate, constraints_id and all wrms columns are left NULL since
                -- they aren't recoverable from existing data. created/modified default
                -- to the backfill time (see column comments).
                INSERT INTO reference_frames (frame_name, engine, project)
                SELECT DISTINCT name, 'gamit', "Project" FROM stacks
                ON CONFLICT (frame_name, engine) DO NOTHING;
    
                -- backfill first_epoch/last_epoch from the actual Year/DOY range
                -- already present in stacks for each frame.
                UPDATE reference_frames rf
                SET first_epoch = sub.first_epoch,
                    last_epoch  = sub.last_epoch
                FROM (
                    SELECT name,
                           MIN(make_date("Year"::int, 1, 1) + ("DOY"::int - 1) * interval '1 day') AS first_epoch,
                           MAX(make_date("Year"::int, 1, 1) + ("DOY"::int - 1) * interval '1 day') AS last_epoch
                    FROM stacks
                    GROUP BY name
                ) sub
                WHERE rf.frame_name = sub.name AND rf.engine = 'gamit';
                    """)
            cnn.commit_transac()

        ##################################################################
        # reference_frame_constraints: per-station position/velocity/periodic
        # constraints inherited from an external frame (e.g. ITRF) when building
        # a reference_frames realization. One row per (constraints_id, station),
        # mirroring exactly what Stacker.py's load_constrains() already parses
        # from an external constraints file (x, y, z, epoch, vx, vy, vz, plus 12
        # periodic terms per station -- some of which may be absent for a given
        # station). constraints_id is a free-text label, not an enforced FK:
        # reference_frames.constraints_id points here only by convention/lookup,
        # since one constraints_id can span many stations and be reused by more
        # than one reference_frames row.

        reference_frame_constraints = cnn.query_float("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'reference_frame_constraints');
            """, as_dict=True)

        if not reference_frame_constraints[0]['exists']:
            print(' >> Creating and populating reference_frame_constraint table')
            cnn.begin_transac()
            cnn.query("""
                CREATE TABLE reference_frame_constraints (
                    constraints_id VARCHAR(20)  NOT NULL,
                    network_code   VARCHAR(3)   NOT NULL,
                    station_code   VARCHAR(4)   NOT NULL,
                    x              NUMERIC(12,4),
                    y              NUMERIC(12,4),
                    z              NUMERIC(12,4),
                    epoch          NUMERIC,
                    vx             NUMERIC(12,5),
                    vy             NUMERIC(12,5),
                    vz             NUMERIC(12,5),
                    n_periodic     NUMERIC(8,5)[],
                    e_periodic     NUMERIC(8,5)[],
                    u_periodic     NUMERIC(8,5)[],
                    api_id         INTEGER      NOT NULL,
                    CONSTRAINT reference_frame_constraints_pkey
                        PRIMARY KEY (constraints_id, network_code, station_code),
                    CONSTRAINT reference_frame_constraints_api_id_key UNIQUE (api_id),
                    CONSTRAINT reference_frame_constraints_n_periodic_check
                        CHECK (n_periodic IS NULL OR array_length(n_periodic, 1) = 4),
                    CONSTRAINT reference_frame_constraints_e_periodic_check
                        CHECK (e_periodic IS NULL OR array_length(e_periodic, 1) = 4),
                    CONSTRAINT reference_frame_constraints_u_periodic_check
                        CHECK (u_periodic IS NULL OR array_length(u_periodic, 1) = 4),
                    FOREIGN KEY (network_code, station_code)
                        REFERENCES stations("NetworkCode", "StationCode")
                        ON DELETE CASCADE
                ) WITH (autovacuum_enabled = TRUE);
    
                CREATE SEQUENCE reference_frame_constraints_api_id_seq
                    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
                ALTER SEQUENCE reference_frame_constraints_api_id_seq
                    OWNED BY reference_frame_constraints.api_id;
                ALTER TABLE ONLY reference_frame_constraints
                    ALTER COLUMN api_id SET DEFAULT nextval('reference_frame_constraints_api_id_seq'::regclass);
    
                CREATE INDEX idx_reference_frame_constraints_station
                    ON reference_frame_constraints(network_code, station_code);
    
                COMMENT ON TABLE reference_frame_constraints IS
                    'Per-station position/velocity/periodic constraints inherited from an external frame (e.g. ITRF) when building a reference_frames realization. Grouped by the free-text constraints_id label. Mirrors the external constraints file format read by Stacker.py load_constrains().';
                COMMENT ON COLUMN reference_frame_constraints.constraints_id IS
                    'Free-text label for this set of constraints (see reference_frames.constraints_id); not an enforced FK.';
                COMMENT ON COLUMN reference_frame_constraints.network_code IS
                    'Station network code (see stations.NetworkCode).';
                COMMENT ON COLUMN reference_frame_constraints.station_code IS
                    'Station code (see stations.StationCode).';
                COMMENT ON COLUMN reference_frame_constraints.x IS
                    'Constrained ECEF X position (m) at epoch. NULL if no position constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.y IS
                    'Constrained ECEF Y position (m) at epoch. NULL if no position constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.z IS
                    'Constrained ECEF Z position (m) at epoch. NULL if no position constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.epoch IS
                    'Reference epoch (fractional year) for x, y, z. NULL if no position constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.vx IS
                    'Constrained ECEF X velocity (m/yr). NULL if no velocity constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.vy IS
                    'Constrained ECEF Y velocity (m/yr). NULL if no velocity constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.vz IS
                    'Constrained ECEF Z velocity (m/yr). NULL if no velocity constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.n_periodic IS
                    'North periodic constraint (m), fixed 4-element order: [annual_cos, annual_sin, semiannual_cos, semiannual_sin]. NULL if no periodic constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.e_periodic IS
                    'East periodic constraint (m), same 4-element order as n_periodic. NULL if no periodic constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.u_periodic IS
                    'Up periodic constraint (m), same 4-element order as n_periodic. NULL if no periodic constraint for this station.';
                COMMENT ON COLUMN reference_frame_constraints.api_id IS
                    'Surrogate id for the Django/web-interface API layer.';
                    """)
            cnn.commit_transac()

    ##################################################################
    # Migrate antennas table: extend primary key to include RadomeCode,
    # and update stationinfo FK to enforce both AntennaCode + RadomeCode.
    #
    # Before this migration:
    #   - antennas PK:  (AntennaCode)             <-- radome-blind
    #   - stationinfo FK: AntennaCode → antennas  <-- radome unconstrained
    #
    # After this migration:
    #   - antennas PK:  (AntennaCode, RadomeCode) <-- full IGS pair
    #   - stationinfo FK: (AntennaCode, RadomeCode) → antennas
    #
    # Side-effect: gamit_htc.antenna_fk references the old single-column PK
    # and cannot survive the PK change. It is dropped here. gamit_htc data
    # is untouched; HTC data is radome-independent so no FK is re-added.
    if 'RadomeCode' not in cnn.get_columns('antennas').keys():
        print(' >> Migrating antennas: adding RadomeCode to primary key '
              'and updating stationinfo FK to enforce (AntennaCode, RadomeCode)')
        cnn.begin_transac()
        cnn.query("""
        -- Step 1: Add RadomeCode to antennas.
        --         Existing rows default to 'NONE' (IGS code for no radome).
        ALTER TABLE antennas
            ADD COLUMN "RadomeCode" VARCHAR(7) NOT NULL DEFAULT 'NONE';

        -- Step 2: Drop stationinfo's FK FIRST — it depends on antennas_pkey,
        --         so the PK cannot be touched while this constraint is alive.
        ALTER TABLE stationinfo
            DROP CONSTRAINT "stationinfo_AntennaCode_fkey";

        -- Step 3: Drop gamit_htc's FK (also references antennas_pkey).
        ALTER TABLE gamit_htc
            DROP CONSTRAINT antenna_fk;

        -- Step 4: Now that no FKs depend on it, drop the old single-column PK.
        ALTER TABLE antennas
            DROP CONSTRAINT antennas_pkey;

        -- Step 5: Establish the new composite primary key.
        ALTER TABLE antennas
            ADD CONSTRAINT antennas_pkey
                PRIMARY KEY ("AntennaCode", "RadomeCode");

        -- Step 6: Back-fill any (AntennaCode, RadomeCode) pairs that already
        --         exist in stationinfo but are missing from antennas.
        --         api_id is omitted — the sequence fills it automatically.
        INSERT INTO antennas ("AntennaCode", "RadomeCode")
        SELECT DISTINCT si."AntennaCode", si."RadomeCode"
        FROM   stationinfo si
        WHERE  NOT EXISTS (
            SELECT 1 FROM antennas a
            WHERE  a."AntennaCode" = si."AntennaCode"
              AND  a."RadomeCode"  = si."RadomeCode"
        );

        -- Step 7: Re-add stationinfo's FK against the new composite PK.
        ALTER TABLE stationinfo
            ADD CONSTRAINT "stationinfo_AntennaCode_RadomeCode_fkey"
                FOREIGN KEY ("AntennaCode", "RadomeCode")
                REFERENCES antennas ("AntennaCode", "RadomeCode")
                ON UPDATE CASCADE
                ON DELETE RESTRICT;

        -- Step 8: Drop the DEFAULT now that the schema is stable.
        --         Future inserts into antennas must supply RadomeCode explicitly.
        ALTER TABLE antennas
            ALTER COLUMN "RadomeCode" DROP DEFAULT;
        """)
        cnn.commit_transac()

        ##################################################################
        # ATX calibration tables: atx_files, antenna_calibrations,
        # antenna_calibration_freq, antenna_calibration_pcv.
        #
        # These four tables store the complete content of ANTEX 1.4 files
        # so that multiple ATX sources can be held simultaneously and every
        # calibration value is traceable back to a specific file.
        #
        # Dependency: the antennas table must already have the composite
        # PK (AntennaCode, RadomeCode) added by the earlier migration above.

        atx_files_exists = cnn.query_float("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND   table_name   = 'atx_files'
            )
        """, as_dict=True)

        if not atx_files_exists[0]['exists']:
            print(' >> Creating ATX calibration tables '
                  '(atx_files, antenna_calibrations, antenna_calibration_freq, '
                  'antenna_calibration_pcv)')
            cnn.begin_transac()
            cnn.query("""
                -- ----------------------------------------------------------------
                -- 1. ATX source files registry
                -- ----------------------------------------------------------------
                CREATE TABLE atx_files (
                    atx_file_id        SERIAL      PRIMARY KEY,
                    filename           VARCHAR(255) NOT NULL,
                    pcv_type           CHAR(1)      NOT NULL
                                       CHECK (pcv_type IN ('A', 'R')),
                    ref_antenna        VARCHAR(20),
                    ref_antenna_serial VARCHAR(20),
                    loaded_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
                    CONSTRAINT atx_files_filename_key UNIQUE (filename)
                );

                COMMENT ON TABLE  atx_files IS
                    'Registry of ATX (ANTEX 1.4) source files loaded into the database.';
                COMMENT ON COLUMN atx_files.pcv_type IS
                    '''A'' = absolute, ''R'' = relative phase center variations.';

                -- ----------------------------------------------------------------
                -- 2. Antenna calibration blocks
                --    One row per (AntennaCode, RadomeCode, serial_no, atx_file).
                --    Stores everything needed to reconstruct the ATX antenna section.
                -- ----------------------------------------------------------------
                CREATE TABLE antenna_calibrations (
                    calibration_id   SERIAL       PRIMARY KEY,

                    "AntennaCode"    VARCHAR(20)  NOT NULL,
                    "RadomeCode"     VARCHAR(4)   NOT NULL,
                    serial_no        VARCHAR(20)  NOT NULL DEFAULT '',

                    atx_file_id      INTEGER      NOT NULL
                        REFERENCES atx_files (atx_file_id) ON DELETE CASCADE,

                    -- METH / BY / # / DATE
                    method           VARCHAR(20),
                    calibrated_by    VARCHAR(20),
                    num_calibrations INTEGER,
                    cal_date         VARCHAR(10),

                    -- DAZI  (0.0 → no azimuth dependence)
                    dazi             NUMERIC(6,1) NOT NULL DEFAULT 0.0,

                    -- ZEN1 / ZEN2 / DZEN
                    zen1             NUMERIC(6,1) NOT NULL,
                    zen2             NUMERIC(6,1) NOT NULL,
                    dzen             NUMERIC(6,1) NOT NULL,

                    -- # OF FREQUENCIES
                    num_frequencies  INTEGER      NOT NULL,

                    -- VALID FROM / VALID UNTIL (optional in ANTEX 1.4)
                    valid_from       TIMESTAMP,
                    valid_until      TIMESTAMP,

                    -- SINEX CODE (optional)
                    sinex_code       VARCHAR(10),

                    -- All COMMENT lines inside the antenna block (preserves order)
                    comments         TEXT[],

                    CONSTRAINT antenna_calibrations_antenna_fk
                        FOREIGN KEY ("AntennaCode", "RadomeCode")
                        REFERENCES antennas ("AntennaCode", "RadomeCode")
                        ON UPDATE CASCADE ON DELETE RESTRICT,

                    CONSTRAINT antenna_calibrations_unique
                        UNIQUE ("AntennaCode", "RadomeCode", serial_no, atx_file_id)
                );

                CREATE INDEX idx_ant_cal_antenna
                    ON antenna_calibrations ("AntennaCode", "RadomeCode");
                CREATE INDEX idx_ant_cal_atxfile
                    ON antenna_calibrations (atx_file_id);

                COMMENT ON TABLE  antenna_calibrations IS
                    'One row per antenna/radome/serial/ATX-file. '
                    'Contains the full antenna block header needed to reconstruct the ATX section.';
                COMMENT ON COLUMN antenna_calibrations.serial_no IS
                    'Empty string means the calibration applies to all representatives of this antenna type.';
                COMMENT ON COLUMN antenna_calibrations.dazi IS
                    '0.0 means no azimuth-dependent corrections are stored.';
                COMMENT ON COLUMN antenna_calibrations.comments IS
                    'Array of COMMENT lines found inside the antenna block, in file order.';

                -- ----------------------------------------------------------------
                -- 3. Phase centre offsets (PCO)
                --    One row per calibration × GNSS frequency.
                -- ----------------------------------------------------------------
                CREATE TABLE antenna_calibration_freq (
                    freq_id          SERIAL        PRIMARY KEY,
                    calibration_id   INTEGER       NOT NULL
                        REFERENCES antenna_calibrations (calibration_id) ON DELETE CASCADE,

                    frequency        VARCHAR(3)    NOT NULL,  -- G01, G02, R01, E01 …

                    -- NORTH / EAST / UP eccentricities in mm (relative to ARP)
                    north_offset     NUMERIC(10,4) NOT NULL,
                    east_offset      NUMERIC(10,4) NOT NULL,
                    up_offset        NUMERIC(10,4) NOT NULL,

                    -- Optional RMS from START OF FREQ RMS
                    north_offset_rms NUMERIC(10,4),
                    east_offset_rms  NUMERIC(10,4),
                    up_offset_rms    NUMERIC(10,4),

                    CONSTRAINT antenna_calibration_freq_unique
                        UNIQUE (calibration_id, frequency)
                );

                CREATE INDEX idx_ant_cal_freq_cal
                    ON antenna_calibration_freq (calibration_id);

                COMMENT ON TABLE  antenna_calibration_freq IS
                    'Phase centre offsets (PCO) per calibration and GNSS frequency.';
                COMMENT ON COLUMN antenna_calibration_freq.frequency IS
                    'Three-character ANTEX frequency code: G01=L1, G02=L2, R01=G1, E01=E1, etc.';

                -- ----------------------------------------------------------------
                -- 4. Phase centre variations (PCV)
                --    One row per freq × azimuth bin.
                --    azimuth IS NULL  → NOAZI (non-azimuth-dependent) pattern.
                --    azimuth NOT NULL → azimuth-dependent row (degrees, 0–360).
                --    pcv_values holds PCV values [mm] from ZEN1 to ZEN2 step DZEN.
                -- ----------------------------------------------------------------
                CREATE TABLE antenna_calibration_pcv (
                    pcv_id          BIGSERIAL           PRIMARY KEY,
                    freq_id         INTEGER             NOT NULL
                        REFERENCES antenna_calibration_freq (freq_id) ON DELETE CASCADE,

                    -- NULL = NOAZI;  numeric = azimuth angle in degrees (0.0–360.0)
                    azimuth         NUMERIC(6,1),

                    -- PCV array [mm], length = (zen2 - zen1) / dzen + 1
                    pcv_values      DOUBLE PRECISION[]  NOT NULL,

                    -- Optional RMS array from START OF FREQ RMS
                    pcv_rms_values  DOUBLE PRECISION[]
                );

                -- Enforce uniqueness of the NOAZI row per frequency.
                -- A partial index is used for NULL azimuth (compatible with PG < 15).
                CREATE UNIQUE INDEX idx_ant_pcv_noazi
                    ON antenna_calibration_pcv (freq_id)
                    WHERE azimuth IS NULL;

                -- Unique index for azimuth-dependent rows.
                CREATE UNIQUE INDEX idx_ant_pcv_azi
                    ON antenna_calibration_pcv (freq_id, azimuth)
                    WHERE azimuth IS NOT NULL;

                CREATE INDEX idx_ant_pcv_freq
                    ON antenna_calibration_pcv (freq_id);

                COMMENT ON TABLE  antenna_calibration_pcv IS
                    'Phase centre variations (PCV) per frequency and azimuth bin. '
                    'azimuth IS NULL for NOAZI (non-azimuth-dependent) rows.';
                COMMENT ON COLUMN antenna_calibration_pcv.azimuth IS
                    'NULL = NOAZI pattern; otherwise azimuth angle in degrees (0.0–360.0).';
                COMMENT ON COLUMN antenna_calibration_pcv.pcv_values IS
                    'Array of PCV values [mm] from ZEN1 to ZEN2 in DZEN steps '
                    '(length = (zen2 - zen1) / dzen + 1).';
            """)
            cnn.commit_transac()

    ##################################################################
    # Widen ReceiverDescription in receivers from VARCHAR(22) to VARCHAR(256).
    # The original schema allocated only 22 chars — identical to ReceiverCode —
    # which is far too short for the IGS equipment descriptions in rcvr_ant.txt.
    rcv_desc = cnn.query_float("""
        SELECT character_maximum_length
        FROM information_schema.columns
        WHERE table_name  = 'receivers'
          AND column_name = 'ReceiverDescription';
    """, as_dict=True)

    if rcv_desc and rcv_desc[0]['character_maximum_length'] is not None \
            and rcv_desc[0]['character_maximum_length'] < 256:
        print(' >> Widening receivers."ReceiverDescription" from '
              f'VARCHAR({rcv_desc[0]["character_maximum_length"]}) to VARCHAR(256)')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE receivers
            ALTER COLUMN "ReceiverDescription" TYPE VARCHAR(256);
        """)
        cnn.commit_transac()

    ##################################################################
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
                           'https', 'ftpa', 'FTP', 'HTTP', 'SFTP', 'HTTPS', 'FTPA')),
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

    ##################################################################
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

    ##################################################################
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

    ##################################################################
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


def adapt_numpy_array(numpy_array):
    return psycopg2.extensions.adapt(numpy_array.tolist())


class dbErrInsert (psycopg2.errors.UniqueViolation): pass


class dbErrUpdate (Exception): pass


class dbErrConnect(Exception): pass


class dbErrDelete (Exception): pass


class DatabaseError(psycopg2.DatabaseError): pass


class Cnn(object):

    def __init__(self, configfile, use_float=False, write_cfg_file=False):

        options = {'hostname': DB_HOST,
                   'username': DB_USER,
                   'password': DB_PASS,
                   'database': DB_NAME}

        self.active_transaction = False
        self.options            = options
        
        # parse session config file
        config = configparser.ConfigParser()

        try:
            config.read_string(file_read_all(configfile))
        except FileNotFoundError:
            if write_cfg_file:
                create_empty_cfg()
                print(' >> No gnss_data.cfg file found, an empty one has been created. Replace all the necessary '
                      'config and try again.')
                exit(1)
            else:
                raise
        # get the database config
        options.update(dict(config.items('postgres')))

        # register an adapter to convert decimal to float
        # see: https://www.psycopg.org/docs/faq.html#faq-float
        DEC2FLOAT = psycopg2.extensions.new_type(
            psycopg2.extensions.DECIMAL.values,
            'DEC2FLOAT',
            lambda value, curs: float(value) if value is not None else None)

        # Define the custom type for an array of decimals
        DECIMAL_ARRAY_TYPE = psycopg2.extensions.new_type(
            (psycopg2.extensions.DECIMAL.values,),  # This matches the type codes for DECIMAL
            'DECIMAL_ARRAY',  # Name of the type
            lambda value, curs: [float(d) for d in value] if value is not None else None
        )

        psycopg2.extensions.register_type(DEC2FLOAT)
        psycopg2.extensions.register_type(DECIMAL_ARRAY_TYPE)
        psycopg2.extensions.register_adapter(np.ndarray, adapt_numpy_array)

        # open connection to server
        err = None
        for i in range(3):
            try:
                self.cnn = psycopg2.connect(host=options['hostname'], user=options['username'],
                                            password=options['password'], dbname=options['database'],
                                            connect_timeout=10)

                self.cnn.autocommit = True
                self.cursor = self.cnn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

                debug("Database connection established")

                run_db_migrations(self)

            except psycopg2.Error as e:
                raise e
            else:
                break
        else:
            raise dbErrConnect(err)

    def query(self, command):
        try:
            self.cursor.execute(command)

            debug(" QUERY: command=%r" % command)

            # passing a query object to match response from pygresql
            return query_obj(self.cursor)
        except Exception as e:
            raise DatabaseError(e)

    def query_float(self, command, as_dict=False):
        # deprecated: using psycopg2 now solves the problem of returning float numbers
        # still in to maintain backwards compatibility
        if not as_dict:
            cursor = self.cnn.cursor()
            cursor.execute(command)
            recordset = cast_array_to_float(cursor.fetchall())
        else:
            # return results as a dictionary
            self.cursor.execute(command)
            recordset = cast_array_to_float(self.cursor.fetchall())

        return recordset


    def get(self, table, filter_fields, return_fields=None, limit=None):
        """
        Selects from the given table the records that match filter_fields and returns ONE dictionary.
        Method should not be used to retrieve more than one single record.
        Parameters:
        table (str): The table to select from.
        filter_fields (dict): The dictionary where the keys are the field names and the values are the filter values.
        return_fields (list of str): The fields to return. If empty return all columns
        limit (int): sets a limit for rows in case it is a query to determine if records exist

        Returns:
        list: A list of dictionaries, each representing a record that matches the filter.
        """

        if return_fields is None:
            return_fields = list(self.get_columns(table).keys())

        where_clause = ' AND '.join([f'"{key}" = %s' if val is not None else f'"{key}" IS %s'
                                     for key, val in zip(filter_fields.keys(), filter_fields.values())])
        fields_clause = ', '.join([f'"{field}"' for field in return_fields])
        if where_clause:
            query = f'SELECT {fields_clause} FROM {table} WHERE {where_clause}'
        else:
            query = f'SELECT {fields_clause} FROM {table}'
        values = list(filter_fields.values())
        # new feature to limit the results
        if limit:
            query += ' LIMIT %i' % limit

        try:
            self.cursor.execute(query, values)
            records = self.cursor.fetchall()
            debug(f"SELECT: query={query}, values={values}")

            if len(records) > 0:
                return records[0]
            else:
                raise DatabaseError('query returned no records: ' + query)

        except psycopg2.Error as e:
            raise e

    def get_columns(self, table):
        tblinfo = self.query('select column_name, data_type from information_schema.columns where table_name=\'%s\''
                             % table).dictresult()

        return {field['column_name']: field['data_type'] for field in tblinfo}

    def begin_transac(self):
        # do not begin a new transaction with another one active.
        if self.active_transaction:
            self.rollback_transac()

        self.active_transaction = True
        self.cursor.execute('BEGIN TRANSACTION')

    def commit_transac(self):
        self.active_transaction = False
        self.cursor.execute('COMMIT')

    def rollback_transac(self):
        self.active_transaction = False
        self.cursor.execute('ROLLBACK')

    def insert(self, table: str, **kw):
        debug("INSERT: table=%r kw=%r" % (table, kw))

        # figure out any extra columns and remove them from the incoming **kw
        cols = list(self.get_columns(table).keys())

        # assuming fields are passed through kw which are keyword arguments
        fields = [k for k in kw.keys() if k in cols]
        values = [v for v, k in zip(kw.values(), kw.keys()) if k in cols]

        # form the insert query dynamically
        placeholders = ', '.join(['%s'] * len(fields))
        columns = '", "'.join(fields)
        query = f'INSERT INTO {table} ("{columns}") VALUES ({placeholders})'
        try:
            self.cursor.execute(query, values)
            self.cnn.commit()
        except psycopg2.errors.UniqueViolation as e:
            self.cnn.rollback()
            raise dbErrInsert(e)

    def update(self, table: str, set_clause_dict: dict, **kwargs):
        """
        Updates the specified table with new field values. The row(s) are updated based on the primary key(s)
        indicated in the 'row' dictionary. New values are specified in kwargs. Field names must be enclosed
        with double quotes to handle camel case names.

        Parameters:
        table (str): The table to update.
        set_row (dict): New field values for the row.
        kwargs: The dictionary where the keys are the primary key fields and the values are the row's identifiers.
        """
        # Build the SET clause of the query
        cols = list(self.get_columns(table))
        set_clause = ', '.join([f'"{field}" = %s' for field in set_clause_dict.keys() if field in cols])

        # Build the WHERE clause based on the row dictionary
        where_clause = ' AND '.join([f'"{key}" = %s' if val is not None else f'"{key}" IS %s'
                                     for key, val in zip(kwargs.keys(), kwargs.values())])
        # Construct query
        query = f'UPDATE {table} SET {set_clause} WHERE {where_clause}'

        # Values to use in the query
        values = (list([value for field, value in set_clause_dict.items() if field in cols])
                  + list(kwargs.values()))

        try:
            self.cursor.execute(query, values)
            self.cnn.commit()
            debug(f"UPDATE {table}: set={set_clause_dict}, where={kwargs}")
            debug(query)
        except psycopg2.Error as e:
            self.cnn.rollback()
            raise dbErrUpdate(e)

    def delete(self, table, **kw):
        """
        Deletes row(s) from the specified table based on the provided keyword arguments.

        Parameters:
        table (str): The table to delete from.
        kw: Keywords to identify the row(s) to be deleted.
        """
        debug("DELETE: table=%r kw=%r" % (table, kw))

        if not kw:
            raise ValueError("No conditions provided for deletion")

        where_clause = ' AND '.join([f'"{key}" = %s' if val is not None else f'"{key}" IS %s'
                                     for key, val in zip(kw.keys(), kw.values())])
        query = f'DELETE FROM {table} WHERE {where_clause}'
        values = list(kw.values())

        try:
            self.cursor.execute(query, values)
            self.cnn.commit()
            debug(f"DELETE FROM {table}: kw={kw}")
        except psycopg2.Error as e:
            self.cnn.rollback()
            raise dbErrDelete(e)

    def insert_event(self, event):
        debug("EVENT: event=%r" % (event.db_dict()))

        self.insert('events', **event.db_dict())

    def insert_event_bak(self, type, module, desc):
        debug("EVENT_BAK: type=%r module=%r desc=%r" % (type, module, desc))

        # do not insert if record exists
        desc = '%s%s' % (module, desc.replace('\'', ''))
        desc = re.sub(r'[^\x00-\x7f]+', '', desc)
        # remove commands from events
        # modification introduced by DDG (suggested by RS)
        desc = re.sub(r'BASH.*', '', desc)
        desc = re.sub(r'PSQL.*', '', desc)

        # warn = self.query('SELECT * FROM events WHERE "EventDescription" = \'%s\'' % (desc))

        # if warn.ntuples() == 0:
        self.insert('events', EventType=type, EventDescription=desc)

    def insert_warning(self, desc):
        self.insert_event_bak('warn', _caller_str(), desc)

    def insert_error(self, desc):
        self.insert_event_bak('error', _caller_str(), desc)

    def insert_info(self, desc):
        self.insert_event_bak('info', _caller_str(), desc)

    def close(self):
        self.cursor.close()
        self.cnn.close()

    def __del__(self):
        if self.active_transaction:
            self.cnn.rollback()


def _caller_str():
    # get the module calling to make clear how is logging this message
    frame = inspect.stack()[2]
    line   = frame[2]
    caller = frame[3]
    
    return '[%s:%s(%s)]\n' % (platform.node(), caller, str(line))

