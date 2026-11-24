"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
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
                    CHECK (engine IN ('gamit', 'pages', 'ppp')),
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
                'Processing engine that produced this frame: gamit, pages, or ppp. Determines which per-engine stacks/projects table this row corresponds to. ppp frames share the physical stacks table with gamit and are exempt from project validation (see reference_frames_validate_project_trigger).';
            COMMENT ON COLUMN reference_frames.project IS
                'Project used to build this frame; validated against gamit_projects.project or pages_projects.project depending on engine (see reference_frames_validate_project_trigger). For engine=ppp there is no per-engine projects table: this instead holds the PPP software name (e.g. gpspace) and is not validated.';
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
                'Euler pole solution when fixed_plate is set, as ECEF rotation vector components (C[0:3] * k) in mas/yr, from FixPlate.py euler_pole(). Full-precision numeric, fixed 3-element order: [X, Y, Z]. NULL for no-net-rotation frames.';
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
                -- ppp frames have no per-engine projects table to validate
                -- against: project is a free-text PPP software name (e.g.
                -- gpspace) instead, so it is never validated.
                IF NEW.engine = 'ppp' THEN
                    RETURN NEW;
                END IF;

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
                    WHEN 'ppp'   THEN 'stacks'
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
                        WHEN 'ppp'   THEN 'stacks'
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
