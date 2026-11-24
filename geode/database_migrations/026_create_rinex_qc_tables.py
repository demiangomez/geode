"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # RinexQC: per-signal/per-bin RINEX quality-control statistics
    # (multipath, SNR, cycle slips) computed by RINGO (see
    # knowledge/decisions/0005-switch-to-ringo.md; was gnssmultipath,
    # ADR-0003/0004). See knowledge/architecture/rinex-qc.md. Four tables
    # and two views:
    #   rinex_qc_grids        -- az-el grid definitions, write-once/versioned
    #   rinex_qc_run          -- one row per QC run (one rinex file x tool x config)
    #   rinex_qc_signal       -- one row per run x signal (gnss/band_attr)
    #   rinex_qc_signal_bins  -- one row per run x signal x grid, bins as arrays
    # Only one grid is seeded here (the az-el equal-area grid, computed live
    # from geode.rinex_qc.aggregate.make_equal_area_grid() so the seed can
    # never drift from what the code actually generates) -- no separate
    # elevation-ring grid is stored; collapse the az-el grid at query time
    # if an elevation-only view is ever needed. A different grid (finer
    # rings, different sector scheme, ...) is a *new row* in rinex_qc_grids
    # with a new id, never an edit to an existing one -- existing runs keep
    # pointing at whatever grid_id they were computed with.
    rinex_qc_run_exists = cnn.query_float("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'rinex_qc_run');
        """, as_dict=True)

    if not rinex_qc_run_exists[0]['exists']:
        print(' >> Creating RinexQC tables (rinex_qc_grids, rinex_qc_run, rinex_qc_signal, '
              'rinex_qc_signal_bins)...')

        from ..rinex_qc.aggregate import make_equal_area_grid
        default_grid = make_equal_area_grid()
        elev_edges_sql = 'ARRAY[%s]::real[]' % ','.join(str(v) for v in default_grid['elev_edges'])
        az_bins_sql    = 'ARRAY[%s]::smallint[]' % ','.join(str(v) for v in default_grid['az_bins'])

        cnn.begin_transac()
        cnn.query("""
            CREATE TABLE rinex_qc_grids (
                id          smallint PRIMARY KEY,
                kind        varchar  NOT NULL,
                elev_edges  real[]   NOT NULL,
                az_bins     smallint[],
                description text
            );

            COMMENT ON TABLE rinex_qc_grids IS
                'Az-el grid definitions used to bin RinexQC per-epoch statistics. Write-once: a '
                'changed grid is a new row with a new id, never an edit to an existing one.';
            COMMENT ON COLUMN rinex_qc_grids.kind IS
                'Grid family, e.g. ''azel_equal_area''.';
            COMMENT ON COLUMN rinex_qc_grids.elev_edges IS
                'Elevation ring boundaries in degrees, e.g. {0,5,10,...,90} for 18 5-degree rings.';
            COMMENT ON COLUMN rinex_qc_grids.az_bins IS
                'Number of azimuth sectors per elevation ring (same length as elev_edges minus '
                'one); sectors start at 0 degrees (north) and run clockwise.';

            INSERT INTO rinex_qc_grids (id, kind, elev_edges, az_bins, description) VALUES
                (1, 'azel_equal_area', """ + elev_edges_sql + """, """ + az_bins_sql + """,
                 'Default az-el equal-area grid: 5-degree elevation rings, azimuth sectors per '
                 'ring proportional to cos(ring-centre elevation) so each cell covers roughly the '
                 'same patch of sky. See geode.rinex_qc.aggregate.make_equal_area_grid().');

            CREATE TABLE rinex_qc_run (
                id                bigserial PRIMARY KEY,
                "NetworkCode"     varchar  NOT NULL,
                "StationCode"     varchar  NOT NULL,
                "ObservationYear" int      NOT NULL,
                "ObservationDOY"  int      NOT NULL,
                rinex_id          integer  NOT NULL
                                  REFERENCES rinex(api_id) ON UPDATE CASCADE ON DELETE CASCADE,
                tool              varchar  NOT NULL,
                tool_version      varchar  NOT NULL,
                config_hash       char(64) NOT NULL,
                config            jsonb    NOT NULL,
                elev_cutoff_deg   real     NOT NULL,
                orbit_source      varchar  NOT NULL,
                orbit_files       text[],
                systems_processed char(1)[],
                systems_without_orbits char(1)[],
                rinex_version     varchar,
                receiver_type     varchar,
                antenna_type      varchar,
                interval_s        real,
                epochs_expected   int,
                epochs_observed   int,
                n_clock_jumps     int,
                header_fixed      boolean  NOT NULL DEFAULT false,
                flags             text[],
                status            varchar  NOT NULL,
                message           text,
                runtime_s         real,
                run_at            timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT rinex_qc_run_status_check CHECK (status IN ('ok', 'partial', 'failed')),
                UNIQUE (rinex_id, tool, tool_version, config_hash)
            );

            CREATE INDEX rinex_qc_run_station_date_idx
                ON rinex_qc_run ("NetworkCode", "StationCode", "ObservationYear", "ObservationDOY");
            CREATE INDEX rinex_qc_run_tool_config_idx ON rinex_qc_run (tool, config_hash);

            COMMENT ON TABLE rinex_qc_run IS
                'One row per RinexQC run (one RINEX file x tool x tool_version x config_hash). '
                'Re-processing with the same config_hash skips by default, replaces with --force; '
                'a different config_hash or tool_version always sits beside the old run, never '
                'replaces it in place.';
            COMMENT ON COLUMN rinex_qc_run."NetworkCode" IS
                'Denormalized from the rinex row (via rinex_id) so station/date queries do not '
                'need to join rinex every time.';
            COMMENT ON COLUMN rinex_qc_run.rinex_id IS
                'FK to rinex.api_id (not rinex''s own composite primary key, which has no single '
                'unique-id column suitable for a simple FK).';
            COMMENT ON COLUMN rinex_qc_run.tool IS
                'QC engine, e.g. ''ringo'' (was ''gnssmultipath'', see '
                'knowledge/decisions/0005-switch-to-ringo.md).';
            COMMENT ON COLUMN rinex_qc_run.config_hash IS
                'SHA-256 of the canonical JSON of the science-affecting config fields only -- see '
                'geode.rinex_qc.config.RinexQCConfig.config_hash.';
            COMMENT ON COLUMN rinex_qc_run.config IS
                'Full config used for this run, for provenance/debugging.';
            COMMENT ON COLUMN rinex_qc_run.orbit_source IS
                'e.g. ''brdc'' or ''sp3''.';
            COMMENT ON COLUMN rinex_qc_run.orbit_files IS
                'Basenames of the orbit product file(s) actually used.';
            COMMENT ON COLUMN rinex_qc_run.systems_processed IS
                'GNSS systems actually analyzed (observed in the file and with usable orbits).';
            COMMENT ON COLUMN rinex_qc_run.systems_without_orbits IS
                'GNSS systems observed in the file but excluded for lack of orbit coverage.';
            COMMENT ON COLUMN rinex_qc_run.header_fixed IS
                'True if pyRinex.check_header() had to patch the RINEX header (e.g. a blank TIME '
                'SYSTEM field) before this run.';
            COMMENT ON COLUMN rinex_qc_run.flags IS
                'e.g. ''clock_jumps'', ''systems_without_orbits'', ''header_fixed''.';
            COMMENT ON COLUMN rinex_qc_run.status IS
                'ok | partial | failed. partial means some observed system(s) were excluded for '
                'lack of orbit coverage; failed means no usable system could be processed at all.';

            CREATE TABLE rinex_qc_signal (
                run_id        bigint  NOT NULL REFERENCES rinex_qc_run(id) ON DELETE CASCADE,
                gnss          char(1) NOT NULL,
                band_attr     char(2) NOT NULL,
                obs_code_orig varchar,
                mp_partner    char(2),
                n_obs         int,
                n_sats        smallint,
                mp_rms_m      real,
                snr_mean      real,
                snr_std       real,
                snr_unit      varchar,
                snr_band_only boolean NOT NULL DEFAULT false,
                n_slips       int,
                slips_by_elev int[],
                PRIMARY KEY (run_id, gnss, band_attr)
            );

            COMMENT ON TABLE rinex_qc_signal IS
                'One row per RinexQC run x signal (GNSS system x RINEX3 band/attribute).';
            COMMENT ON COLUMN rinex_qc_signal.gnss IS
                'G | R | E | C.';
            COMMENT ON COLUMN rinex_qc_signal.band_attr IS
                'RINEX band + attribute, e.g. ''1C'', ''2W''. RINGO (the current tool, see '
                'knowledge/decisions/0005-switch-to-ringo.md) reports multipath per band only, not '
                'per exact attribute, so its rows use ''<band>?'' (e.g. ''1?'') -- see '
                'geode.rinex_qc.runner.';
            COMMENT ON COLUMN rinex_qc_signal.obs_code_orig IS
                'Observation code as it appeared in the RINEX file (RINEX 2 codes kept as-is, '
                'e.g. ''P2'', so decades of RINEX 2/3 data stay traceable to their original code).';
            COMMENT ON COLUMN rinex_qc_signal.mp_partner IS
                'The second-frequency phase code combined with this signal to form multipath. '
                'Always NULL for RINGO-sourced rows -- it reports multipath per band, not per '
                'exact code pair, see geode.rinex_qc.runner.';
            COMMENT ON COLUMN rinex_qc_signal.snr_band_only IS
                'True when the SNR value attaches to the whole band rather than this specific '
                'tracking attribute (always true for RINEX 2 SNR; true for RINEX 3 only when no '
                'exact-attribute SNR code was present) -- see geode.rinex_qc.signals.find_snr_code().';
            COMMENT ON COLUMN rinex_qc_signal.n_slips IS
                'Count of epochs RINGO flagged as a slip on this band''s multipath combination '
                '(rnxcsv --qcmode''s per-epoch s1/s2/s5 flags) -- see geode.rinex_qc.runner.';
            COMMENT ON COLUMN rinex_qc_signal.slips_by_elev IS
                'Slip counts by elevation band: [0-10, 10-20, 20-30, 30-40, 40-50, >50] degrees.';

            CREATE TABLE rinex_qc_signal_bins (
                run_id     bigint   NOT NULL REFERENCES rinex_qc_run(id) ON DELETE CASCADE,
                gnss       char(1)  NOT NULL,
                band_attr  char(2)  NOT NULL,
                grid_id    smallint NOT NULL REFERENCES rinex_qc_grids(id),
                n_obs      integer[],
                mp_rms_mm  smallint[],
                snr_ddbhz  smallint[],
                PRIMARY KEY (run_id, gnss, band_attr, grid_id)
            );

            COMMENT ON TABLE rinex_qc_signal_bins IS
                'One row per RinexQC run x signal x grid; bins stored as flat arrays, one entry '
                'per grid cell in the same ring-major order geode.rinex_qc.aggregate.bin_indices() '
                'produces. NULL array entries mean an empty cell (not zero).';
            COMMENT ON COLUMN rinex_qc_signal_bins.n_obs IS
                'Observation count per cell.';
            COMMENT ON COLUMN rinex_qc_signal_bins.mp_rms_mm IS
                'Multipath RMS per cell, in millimeters.';
            COMMENT ON COLUMN rinex_qc_signal_bins.snr_ddbhz IS
                'Mean SNR per cell, in 0.1 units of rinex_qc_signal.snr_unit.';

            CREATE VIEW rinex_qc_bins_long AS
            WITH grid_rings AS (
                SELECT g.id AS grid_id,
                       ring.ordinality - 1                AS ring_index,
                       g.elev_edges[ring.ordinality]       AS elev_min,
                       g.elev_edges[ring.ordinality + 1]   AS elev_max,
                       ring.n_sectors                      AS n_sectors,
                       COALESCE(sum(ring.n_sectors) OVER (PARTITION BY g.id ORDER BY ring.ordinality
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS cells_before
                FROM rinex_qc_grids g
                CROSS JOIN LATERAL unnest(g.az_bins) WITH ORDINALITY AS ring(n_sectors, ordinality)
            )
            SELECT b.run_id, b.gnss, b.band_attr, b.grid_id,
                   cell.ordinality                                 AS cell_index,
                   r.ring_index,
                   r.elev_min,
                   r.elev_max,
                   (cell.ordinality - 1 - r.cells_before)           AS sector_index,
                   360.0 / r.n_sectors                               AS sector_width_deg,
                   (cell.ordinality - 1 - r.cells_before) * (360.0 / r.n_sectors) AS az_min,
                   (cell.ordinality     - r.cells_before) * (360.0 / r.n_sectors) AS az_max,
                   cell.n_obs, cell.mp_rms_mm, cell.snr_ddbhz
            FROM rinex_qc_signal_bins b
            CROSS JOIN LATERAL unnest(b.n_obs, b.mp_rms_mm, b.snr_ddbhz)
                         WITH ORDINALITY AS cell(n_obs, mp_rms_mm, snr_ddbhz, ordinality)
            JOIN grid_rings r ON r.grid_id = b.grid_id
                             AND cell.ordinality > r.cells_before
                             AND cell.ordinality <= r.cells_before + r.n_sectors;

            COMMENT ON VIEW rinex_qc_bins_long IS
                'One row per grid cell (unnested from rinex_qc_signal_bins''s flat arrays), with '
                'the cell''s elevation/azimuth limits resolved from rinex_qc_grids. For ad-hoc SQL only; '
                'numerical work (pooling, stacking) reads the arrays directly.';

            CREATE VIEW rinex_qc_latest AS
            SELECT DISTINCT ON (rinex_id, tool) *
            FROM rinex_qc_run
            ORDER BY rinex_id, tool, run_at DESC, id DESC;

            COMMENT ON VIEW rinex_qc_latest IS
                'Newest run per (rinex_id, tool). Relies on incremental/backfill runs always '
                'targeting the current default config_hash, so the newest run in practice is the '
                'default-config one -- "default" is a geode.rinex_qc.config.RinexQCConfig notion, '
                'not something this schema stores, so this view cannot filter on it directly.';
        """)
        cnn.commit_transac()
