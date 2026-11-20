"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
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
