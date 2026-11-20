"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
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
