"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
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
