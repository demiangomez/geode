# Rename solution tables to multi-engine naming convention

## Context

`geode` currently has one engine per solution family: GPSPACE for PPP (`ppp_soln`) and GAMIT for network solutions (`gamit_soln` raw + `stacks` reference-frame-aligned). We are about to add support for additional engines (a second PPP engine, and PAGES as a second GAMIT-like engine). Prior discussion concluded that each engine should get its own physical table rather than a shared table with a discriminator column, to avoid retrofitting a PK/FK on a 20M-row table and to avoid the risk of a missed `WHERE` filter silently blending two engines' solutions.

This plan executes the **first step**: renaming the existing tables to the agreed convention — `soln_<engine>` for raw/PPP-style solution tables (grouping alphabetically: `soln_gamit`, `soln_gpspace`, later `soln_pages`), `stacks_<engine>` for aligned stack tables (`stacks_gamit`, later `stacks_pages`), and `antenna_residuals_<engine>` for the per-engine antenna PCV residual tables. This is a pure rename — no new engine, no new tables, no application-level multi-engine logic yet. That is deliberately out of scope for this change.

Renamed:
- `gamit_soln` → `soln_gamit`
- `gamit_soln_excl` → `soln_gamit_excl`
- `ppp_soln` → `soln_gpspace`
- `ppp_soln_excl` → `soln_gpspace_excl`
- `stacks` → `stacks_gamit`
- `gamit_antenna_residuals` → `antenna_residuals_gamit`
- `ppp_antenna_residuals` → `antenna_residuals_gpspace`

The two antenna-residuals tables are handled differently: they are already created lazily (only if missing) by `run_db_migrations()`, so not every deployment necessarily has them yet. The migration must therefore branch: if the old-named table exists, rename it (+ its constraints/indexes); if neither the old nor the new name exists, create it fresh directly under the new name (skip the old name entirely). See the dedicated DDL block below.

**Status: plan only. Not applied. To be implemented on a separate git branch.**

## Key finding: how schema changes actually ship in this codebase

`database/gnss_data_dump.sql` is a `pg_dump` **snapshot**, not a migration script — it's regenerated from the live DB and must not be hand-edited as part of this change; it will pick up the rename automatically next time it's dumped.

The real migration mechanism is `run_db_migrations()` in `geode/dbConnection.py` (called from `Cnn.__init__`, line 836). It runs idempotent, existence-checked `ALTER TABLE`/`CREATE TABLE` blocks on every connection (see the existing `orbit` column and `hash`-to-`BIGINT` blocks at `geode/dbConnection.py:150-172`, and the `ppp_antenna_residuals` table-creation block at lines 294-330). The rename must be added there, following that exact idiom, not run manually against production.

## Postgres mechanics that simplify this

`ALTER TABLE x RENAME TO y` automatically retargets every FK that references `x` — no need to drop/recreate `gamit_soln_excl_NetworkCode_fkey`, `stacks_gamit_soln_fkey`, or `ppp_antenna_residuals_..._fkey`. Sequences, indexes, and constraint names are **not** auto-renamed and keep working under their old names regardless. Renaming them too is purely cosmetic cleanup (avoids a `soln_gamit` table owning a sequence still called `gamit_soln_api_id_seq`), bundled into the same transaction for consistency.

## DDL (goes inside `run_db_migrations()`, guarded by an existence check)

```sql
BEGIN;

-- gamit_soln -> soln_gamit
ALTER TABLE public.gamit_soln RENAME TO soln_gamit;
ALTER TABLE public.gamit_soln_excl RENAME TO soln_gamit_excl;
ALTER SEQUENCE public.gamit_soln_api_id_seq RENAME TO soln_gamit_api_id_seq;
ALTER SEQUENCE public.gamit_soln_excl_api_id_seq RENAME TO soln_gamit_excl_api_id_seq;
ALTER TABLE public.soln_gamit RENAME CONSTRAINT gamit_soln_api_id_key TO soln_gamit_api_id_key;
ALTER TABLE public.soln_gamit RENAME CONSTRAINT gamit_soln_pkey TO soln_gamit_pkey;
ALTER TABLE public.soln_gamit RENAME CONSTRAINT "gamit_soln_NetworkCode_fkey" TO "soln_gamit_NetworkCode_fkey";
ALTER TABLE public.soln_gamit_excl RENAME CONSTRAINT gamit_soln_excl_api_id_key TO soln_gamit_excl_api_id_key;
ALTER TABLE public.soln_gamit_excl RENAME CONSTRAINT gamit_soln_excl_pkey TO soln_gamit_excl_pkey;
ALTER TABLE public.soln_gamit_excl RENAME CONSTRAINT "gamit_soln_excl_NetworkCode_fkey" TO "soln_gamit_excl_NetworkCode_fkey";

-- ppp_soln -> soln_gpspace
ALTER TABLE public.ppp_soln RENAME TO soln_gpspace;
ALTER TABLE public.ppp_soln_excl RENAME TO soln_gpspace_excl;
ALTER SEQUENCE public.ppp_soln_api_id_seq RENAME TO soln_gpspace_api_id_seq;
ALTER SEQUENCE public.ppp_soln_excl_api_id_seq RENAME TO soln_gpspace_excl_api_id_seq;
ALTER TABLE public.soln_gpspace RENAME CONSTRAINT ppp_soln_api_id_key TO soln_gpspace_api_id_key;
ALTER TABLE public.soln_gpspace RENAME CONSTRAINT ppp_soln_pkey TO soln_gpspace_pkey;
ALTER TABLE public.soln_gpspace RENAME CONSTRAINT "ppp_soln_NetworkName_StationCode_fkey" TO "soln_gpspace_NetworkName_StationCode_fkey";
ALTER TABLE public.soln_gpspace_excl RENAME CONSTRAINT ppp_soln_excl_api_id_key TO soln_gpspace_excl_api_id_key;
ALTER TABLE public.soln_gpspace_excl RENAME CONSTRAINT ppp_soln_excl_pkey TO soln_gpspace_excl_pkey;
ALTER TABLE public.soln_gpspace_excl RENAME CONSTRAINT "ppp_soln_excl_NetworkCode_fkey" TO "soln_gpspace_excl_NetworkCode_fkey";
ALTER INDEX public.ppp_soln_idx RENAME TO soln_gpspace_idx;
ALTER INDEX public.ppp_soln_order RENAME TO soln_gpspace_order;
ALTER INDEX public.ppp_soln_year_doy RENAME TO soln_gpspace_year_doy;

-- stacks -> stacks_gamit
ALTER TABLE public.stacks RENAME TO stacks_gamit;
ALTER SEQUENCE public.stacks_api_id_seq RENAME TO stacks_gamit_api_id_seq;
ALTER TABLE public.stacks_gamit RENAME CONSTRAINT stacks_api_id_key TO stacks_gamit_api_id_key;
ALTER TABLE public.stacks_gamit RENAME CONSTRAINT stacks_pkey TO stacks_gamit_pkey;
ALTER TABLE public.stacks_gamit RENAME CONSTRAINT "stacks_NetworkCode_fkey" TO "stacks_gamit_NetworkCode_fkey";
ALTER TABLE public.stacks_gamit RENAME CONSTRAINT stacks_gamit_soln_fkey TO stacks_gamit_soln_gamit_fkey;
ALTER INDEX public.stacks_idx RENAME TO stacks_gamit_idx;

COMMIT;
```

Add this to `run_db_migrations()` (`geode/dbConnection.py`, near the other `ppp_soln`-related block at line ~150) as:

```python
##################################################################
# Rename solution tables to the soln_<engine> / stacks_<engine> convention
tables = cnn.query_float("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'gamit_soln'
""", as_dict=True)
if tables:
    print(' >> Renaming gamit_soln/ppp_soln/stacks to the multi-engine naming convention')
    cnn.begin_transac()
    cnn.query(""" <DDL body above, without BEGIN/COMMIT> """)
    cnn.commit_transac()
```
(Follow the exact existence-check idiom already used for `s_score_cache`/`gamit_antenna_residuals` at `geode/dbConnection.py:220-292` — check `information_schema.tables` for `gamit_soln`, since after the first run it won't exist anymore, making this naturally idempotent.)

### Antenna-residuals tables: rename-if-exists, else create-fresh

`geode/dbConnection.py` currently has two lazy "create if missing" blocks for these tables (lines 254-292 for `gamit_antenna_residuals`, lines 294-330 for `ppp_antenna_residuals`). Both blocks get **replaced in place** with a rename-or-create version — not a separate migration block, since they already own the "does this table exist" logic:

```sql
-- gamit_antenna_residuals -> antenna_residuals_gamit
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'gamit_antenna_residuals') THEN
        ALTER TABLE public.gamit_antenna_residuals RENAME TO antenna_residuals_gamit;
        ALTER TABLE public.antenna_residuals_gamit
            RENAME CONSTRAINT gamit_antenna_residuals_pkey TO antenna_residuals_gamit_pkey;
        ALTER TABLE public.antenna_residuals_gamit
            RENAME CONSTRAINT gamit_antenna_residuals_network_code_station_code_fkey
            TO antenna_residuals_gamit_network_code_station_code_fkey;
        ALTER TABLE public.antenna_residuals_gamit
            RENAME CONSTRAINT gamit_antenna_residuals_project_subnet_year_doy_system_fkey
            TO antenna_residuals_gamit_project_subnet_year_doy_system_fkey;
        ALTER INDEX public.idx_gamit_antenna_residuals_station RENAME TO idx_antenna_residuals_gamit_station;
        ALTER INDEX public.idx_gamit_antenna_residuals_date    RENAME TO idx_antenna_residuals_gamit_date;
        ALTER INDEX public.idx_gamit_antenna_residuals_antenna RENAME TO idx_antenna_residuals_gamit_antenna;
    ELSIF NOT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = 'antenna_residuals_gamit') THEN
        CREATE TABLE public.antenna_residuals_gamit (
            network_code VARCHAR(3)  NOT NULL,
            station_code VARCHAR(4)  NOT NULL,
            project      VARCHAR(20) NOT NULL,
            system       CHARACTER(1) NOT NULL,
            subnet       SMALLINT NOT NULL,
            year         SMALLINT NOT NULL,
            doy          SMALLINT NOT NULL,
            antenna_code VARCHAR(22) NOT NULL,
            radome_code  VARCHAR(7)  NOT NULL,
            residuals    DOUBLE PRECISION[91],
            CONSTRAINT antenna_residuals_gamit_pkey
                PRIMARY KEY (network_code, station_code, project, subnet, year, doy, system),
            FOREIGN KEY (network_code, station_code)
                REFERENCES stations("NetworkCode", "StationCode") ON DELETE CASCADE,
            FOREIGN KEY (project, subnet, year, doy, system)
                REFERENCES gamit_stats("Project", subnet, "Year", "DOY", system) ON DELETE CASCADE
        ) WITH (autovacuum_enabled = TRUE);
        CREATE INDEX idx_antenna_residuals_gamit_station ON antenna_residuals_gamit(network_code, station_code);
        CREATE INDEX idx_antenna_residuals_gamit_date    ON antenna_residuals_gamit(year, doy);
        CREATE INDEX idx_antenna_residuals_gamit_antenna ON antenna_residuals_gamit(antenna_code, radome_code);
    END IF;
END $$;

-- ppp_antenna_residuals -> antenna_residuals_gpspace
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'ppp_antenna_residuals') THEN
        ALTER TABLE public.ppp_antenna_residuals RENAME TO antenna_residuals_gpspace;
        ALTER TABLE public.antenna_residuals_gpspace
            RENAME CONSTRAINT ppp_antenna_residuals_pkey TO antenna_residuals_gpspace_pkey;
        ALTER TABLE public.antenna_residuals_gpspace
            RENAME CONSTRAINT ppp_antenna_residuals_network_code_station_code_fkey
            TO antenna_residuals_gpspace_network_code_station_code_fkey;
        ALTER TABLE public.antenna_residuals_gpspace
            RENAME CONSTRAINT ppp_antenna_residuals_network_code_station_code_year_doy_r_fkey
            TO antenna_residuals_gpspace_network_code_station_code_year_doy_r_fkey;
        ALTER INDEX public.idx_ppp_antenna_residuals_station RENAME TO idx_antenna_residuals_gpspace_station;
        ALTER INDEX public.idx_ppp_antenna_residuals_date    RENAME TO idx_antenna_residuals_gpspace_date;
        ALTER INDEX public.idx_ppp_antenna_residuals_antenna RENAME TO idx_antenna_residuals_gpspace_antenna;
        -- NOTE: this table's FK to ppp_soln/soln_gpspace is retargeted automatically by
        -- Postgres when soln_gpspace is renamed above; no action needed for that FK's target.
    ELSIF NOT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = 'antenna_residuals_gpspace') THEN
        CREATE TABLE public.antenna_residuals_gpspace (
            network_code    VARCHAR(3)  NOT NULL,
            station_code    VARCHAR(4)  NOT NULL,
            reference_frame VARCHAR(20) NOT NULL,
            system          CHARACTER(1),
            year            SMALLINT NOT NULL,
            doy             SMALLINT NOT NULL,
            antenna_code    VARCHAR(22) NOT NULL,
            radome_code     VARCHAR(7)  NOT NULL,
            residuals       DOUBLE PRECISION[91],
            CONSTRAINT antenna_residuals_gpspace_pkey
                PRIMARY KEY (network_code, station_code, year, doy, reference_frame),
            FOREIGN KEY (network_code, station_code)
                REFERENCES stations("NetworkCode", "StationCode") ON DELETE CASCADE,
            FOREIGN KEY (network_code, station_code, year, doy, reference_frame)
                REFERENCES soln_gpspace("NetworkCode", "StationCode", "Year", "DOY", "ReferenceFrame")
                ON DELETE CASCADE
        ) WITH (autovacuum_enabled = TRUE);
        CREATE INDEX idx_antenna_residuals_gpspace_station ON antenna_residuals_gpspace(network_code, station_code);
        CREATE INDEX idx_antenna_residuals_gpspace_date    ON antenna_residuals_gpspace(year, doy);
        CREATE INDEX idx_antenna_residuals_gpspace_antenna ON antenna_residuals_gpspace(antenna_code, radome_code);
    END IF;
END $$;
```

This logic must run **after** the `soln_gpspace` rename above (the create-fresh branch for `antenna_residuals_gpspace` references `soln_gpspace` by its new name), so order the blocks accordingly inside `run_db_migrations()`.

## Code changes: mechanical string rename of table names in SQL

Every occurrence below is a literal SQL table name (in a query string or as a `cnn.insert/update/delete/get` first argument) and must change 1:1 with the DDL renames above. No logic changes — same columns, same WHERE clauses, just the table identifier. Grouped by file:

| File | Lines | Old → New |
|---|---|---|
| `geode/dbConnection.py` | 151, 154-155, 158, 165-166, 169 | `ppp_soln` → `soln_gpspace` (the `orbit`/`hash` migration block — becomes dead code once the rename above has run once, but keep it correct/inert) |
| `geode/dbConnection.py` | 254-292 | Replace the `gamit_antenna_residuals` create-if-missing block with the rename-or-create logic above (table + constraint + index names) |
| `geode/dbConnection.py` | 294-330 | Replace the `ppp_antenna_residuals` create-if-missing block with the rename-or-create logic above; FK body `REFERENCES ppp_soln(...)` → `REFERENCES soln_gpspace(...)` |
| `com/ScanArchive.py` | 579 | `cnn.insert('ppp_antenna_residuals', ...)` → `cnn.insert('antenna_residuals_gpspace', ...)` |
| `geode/etm/data/solution_data.py` | 485, 494, 508, 517 | `ppp_soln`/`ppp_soln_excl` → `soln_gpspace`/`soln_gpspace_excl` |
| `geode/etm/data/solution_data.py` | 581, 590, 612 | `stacks` → `stacks_gamit` |
| `geode/pyStack.py` | 137, 143, 148, 155, 170, 173, 182, 185, 189, 198, 202, 204, 206, 214, 218, 220, 222, 229, 232, 806, 813, 828 | `gamit_soln` → `soln_gamit`; `stacks` → `stacks_gamit` (this is the `Stack` class — highest concentration of references) |
| `geode/Utils.py` | 80 | `stacks` → `stacks_gamit` (`get_stack_stations`) |
| `geode/gamit/station.py` | 152 | `gamit_soln` → `soln_gamit` |
| `geode/gamit/station.py` | 236 | `ppp_soln` → `soln_gpspace` |
| `geode/pyArchiveStruct.py` | 183 | `gamit_soln` → `soln_gamit` |
| `geode/pyArchiveStruct.py` | 188 | `ppp_soln` → `soln_gpspace` |
| `com/ScanArchive.py` | 505, 539-540, 575, 806, 839, 855, 858, 877-882 | `ppp_soln`/`gamit_soln` → `soln_gpspace`/`soln_gamit` (includes `cnn.insert('ppp_soln', ...)`, `cnn.delete`, `cnn.update` calls) |
| `com/IntegrityCheck.py` | 439, 793-794, 894, 902, 950 (comment) | `ppp_soln`, `ppp_soln_excl`, `gamit_soln`, `gamit_soln_excl`, `stacks` → new names (line 793-794 is a table-name list iterated in a loop — update the string list) |
| `com/ParallelGamit.py` | 175, 686, 694 | `gamit_soln_excl`, `stacks`, `gamit_soln` → new names (line 175 is a tuple of table names iterated in a loop) |
| `com/DRA.py` | 50, 64, 71, 162, 171, 180, 320 (help text) | `gamit_soln`, `gamit_soln_excl` → new names |
| `com/NEQStack.py` | 121-122, 139, 158, 165 | `gamit_soln` → `soln_gamit` (note: self-join `gamit_soln g1 LEFT JOIN gamit_soln g2`, both aliases need updating) |
| `com/GenerateSinex.py` | 66 | `gamit_soln` → `soln_gamit` |
| `com/WeeklyCombination.py` | 71, 338, 345 | `gamit_soln`, `gamit_soln_excl` → new names |
| `com/GenerateKml.py` | 269, 438 | `ppp_soln` → `soln_gpspace` |
| `com/FixPlate.py` | 392, 442 | `stacks` → `stacks_gamit` (`cnn.query` delete + `cnn.insert('stacks', ...)`) |
| `com/QueryETM.py` | 125 | `stacks` → `stacks_gamit` |
| `com/Stacker.py` | 345 | `cnn.get('stacks', ...)` → `cnn.get('stacks_gamit', ...)` |
| `geode/pyETM.py` | 414, 419, 467, 481, 507, 596, 1541, 2150, 2341, 2346, 3427 | `ppp_soln`, `ppp_soln_excl`, `stacks`, `gamit_soln_excl` → new names. **Important**: although `pyETM.py` is the legacy module being phased out for *new development*, it is still imported and executed today by `com/Stacker.py`, `com/QueryETM.py`, `com/PlotMapView.py`, and `com/TrajectoryFit.py` — its queries will break immediately after the rename if not updated. This is in scope. |

Not to touch: the many `gamit_soln=` / `.gamit_soln.` occurrences in `com/PlotMapView.py`, `com/TrajectoryFit.py`, `com/QueryETM.py:133`, `com/Stacker.py:44`, `geode/pyStack.py:566,1093` are a Python **keyword argument / attribute name** on `pyETM.GamitETM`/`DailyRep` (`self.gamit_soln = ...` at `geode/pyETM.py:3433,3437,3498`), unrelated to the DB table name — renaming the table doesn't require touching these.

## Verification

1. Apply the DDL to a scratch/test copy of the database (not production) and confirm `\d soln_gamit`, `\d soln_gpspace`, `\d stacks_gamit`, `\d antenna_residuals_gamit`, `\d antenna_residuals_gpspace` show the expected columns, PK, and FKs still pointing at the right renamed parents.
1b. Test the antenna-residuals branch both ways: once against a copy where `gamit_antenna_residuals`/`ppp_antenna_residuals` already exist (must rename), and once against a copy where neither exists yet (must create fresh under the new name, and skip the old name entirely).
2. Grep the repo after edits for the old bare table names to confirm nothing was missed: `grep -rn "\bppp_soln\b\|\bgamit_soln\b" --include="*.py" .` and a separate check for `FROM stacks\|INTO stacks\|'stacks'\|"stacks"` (bare `stacks` is noisy, since it's an English word — must inspect each hit) — expect zero true-positive matches outside of `stacks_gamit`.
3. Run an existing ETM plot end-to-end against the test DB for both a PPP and a GAMIT station (`geode/etm/` path) and confirm coordinates load and plot correctly.
4. Run `com/ScanArchive.py`, `com/IntegrityCheck.py`, and `com/pyStack.py`'s stack-building path against the test DB to confirm inserts/updates/deletes hit the renamed tables without FK violations.
5. Confirm `run_db_migrations()` is idempotent: run it twice against the test DB; the second run must no-op (the `information_schema.tables` check on the old name should find nothing and skip).
