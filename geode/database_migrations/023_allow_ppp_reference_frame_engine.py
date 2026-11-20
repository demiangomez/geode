"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # Allow a 'ppp' reference_frames.engine (missed when reference_frames
    # was first created). ppp frames share the physical stacks table with
    # gamit (there is no separate per-engine table for them), and are
    # exempt from project validation since there's no PPP projects table:
    # project instead holds the PPP software name (e.g. gpspace), allowing
    # frames built from different PPP software to be told apart.
    # The CREATE TABLE branch above already includes 'ppp' for fresh
    # installs; this block upgrades a reference_frames table created
    # before 'ppp' existed.

    reference_frames_exists = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'reference_frames');
        """, as_dict=True)

    if reference_frames_exists[0]['exists']:
        engine_check = cnn.query_float("""
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conname = 'reference_frames_engine_check';
            """, as_dict=True)

        if engine_check and 'ppp' not in engine_check[0]['def'].lower():
            print(' >> Allowing ppp engine in reference_frames')
            cnn.begin_transac()
            cnn.query("""
                ALTER TABLE reference_frames DROP CONSTRAINT reference_frames_engine_check;
                ALTER TABLE reference_frames ADD  CONSTRAINT reference_frames_engine_check
                    CHECK (engine IN ('gamit', 'pages', 'ppp'));

                COMMENT ON COLUMN reference_frames.engine IS
                    'Processing engine that produced this frame: gamit, pages, or ppp. Determines which per-engine stacks/projects table this row corresponds to. ppp frames share the physical stacks table with gamit and are exempt from project validation (see reference_frames_validate_project_trigger).';
                COMMENT ON COLUMN reference_frames.project IS
                    'Project used to build this frame; validated against gamit_projects.project or pages_projects.project depending on engine (see reference_frames_validate_project_trigger). For engine=ppp there is no per-engine projects table: this instead holds the PPP software name (e.g. gpspace) and is not validated.';
            """)
            cnn.commit_transac()

        # Keep the euler_pole column comment in sync with FixPlate.py, which
        # stores the ECEF rotation vector (mas/yr) rather than lat/lon/rate.
        cnn.query("""
            COMMENT ON COLUMN reference_frames.euler_pole IS
                'Euler pole solution when fixed_plate is set, as ECEF rotation vector components (C[0:3] * k) in mas/yr, from FixPlate.py euler_pole(). Full-precision numeric, fixed 3-element order: [X, Y, Z]. NULL for no-net-rotation frames.';
            """)

        # Always re-install the trigger functions below (CREATE OR REPLACE
        # is idempotent) so an existing installation picks up ppp handling
        # even if the engine_check constraint was already correct.
        cnn.query("""
            CREATE OR REPLACE FUNCTION reference_frames_validate_project() RETURNS TRIGGER AS $BODY$
            DECLARE
                project_table TEXT;
                found         BOOLEAN;
            BEGIN
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
        """)
