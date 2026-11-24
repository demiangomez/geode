# Split `_run_db_migrations()` into per-migration files; fix `agency_roles` PK

- **Date:** 2026-11-20
- **Branch:** master

## Summary

`_run_db_migrations()` in `geode/dbConnection.py` had grown to ~1,840 lines, one long function with
28 sequential, independently idempotent "check column/table/index exists, then ALTER/CREATE if
not" blocks. Split it into one file per migration under `geode/database_migrations/NNN_name.py`
(zero-padded numeric prefix for explicit run order, each exposing `migrate(cnn)`), with
`_run_db_migrations()` reduced to a ~15-line loader that discovers, sorts, imports, and runs them.
Pure move, no logic change, except two cross-block dependencies the split exposed (see below).
Also fixed `agency_roles`'s primary key per explicit instruction: dropped the separate `id` column,
made `api_id` the primary key directly — see [ADR-0006](../decisions/0006-single-surrogate-pk-convention.md).

## Files / modules affected

- [geode/dbConnection.py](../../geode/dbConnection.py) — `_run_db_migrations()` body (2319 lines
  total in the file before this) replaced with the loader; `run_db_migrations()`'s advisory-lock
  wrapper untouched.
- `geode/database_migrations/` (new package) — 28 migration files (`001_add_rinexed_column.py`
  through `028_create_agency_tables.py`) + `__init__.py`.
- [geode/database_migrations/028_create_agency_tables.py](../../geode/database_migrations/028_create_agency_tables.py) —
  `agency_roles` PK fix (see below).
- [knowledge/architecture/database-schema.md](../architecture/database-schema.md) — "Migration
  mechanism" section rewritten for the new per-file layout; added the ADR-0006 pointer.
- [knowledge/known-issues.md](../known-issues.md) — flagged that
  `database/migration_plan_engine_table_naming.md` (pending, unapplied) references now-stale
  `dbConnection.py` line numbers against the old monolithic function.
- [knowledge/decisions/0006-single-surrogate-pk-convention.md](../decisions/0006-single-surrogate-pk-convention.md) —
  new ADR formalizing the single-`api_id`-as-PK convention.
- [knowledge/checkpoints/2026-11-19-agency-tables.md](2026-11-19-agency-tables.md) — annotated with
  a correction note pointing here, rather than rewritten.

## Architectural decisions

- **Numeric filename prefix is the only thing that determines migration run order** — not import
  order, not git history, not mtime. A new migration is always a new file with the next unused
  prefix, appended at the end; an already-shipped migration file is never edited in place (same
  rule as not editing a past Alembic/Django migration) — `agency_roles`'s PK fix below is an
  exception only because that table was added in the immediately preceding commit on this same
  branch and had not yet been applied to any real database.
- **Two cross-block dependencies had to be fixed, not just moved, when splitting:**
  - `005_ppp_soln_hash_to_bigint.py` read a `fields` variable that used to be computed by the
    preceding block (`004_add_ppp_soln_orbit_column.py`, `fields = cnn.get_columns('ppp_soln')`).
    Now recomputes it itself (cheap, and migrations must not depend on sibling-file execution
    order beyond the numeric sort already guaranteeing).
  - `027_rinex_qc_lz4_compression.py` referenced `DatabaseError`, a class defined elsewhere in
    `dbConnection.py`. Now imports it explicitly (`from ..dbConnection import DatabaseError`) —
    works despite the apparent circularity because the import happens at call time, inside
    `migrate(cnn)`, by which point `geode.dbConnection` has already finished executing and is in
    `sys.modules`.
  - Two inline relative imports (`from .station_selector import ...`,
    `from .rinex_qc.aggregate import ...`) needed an extra `.` once moved one package level deeper
    into `geode/database_migrations/`.
- **`agency_roles` PK fix** (explicit instruction, not a judgment call made this session): dropped
  `id SERIAL PRIMARY KEY` + separate `api_id INTEGER UNIQUE` (the shape from
  [checkpoints/2026-11-19-agency-tables.md](2026-11-19-agency-tables.md), explicitly requested that
  day to mirror the Django app's `api_stationrole`); `agency_roles` now has `api_id SERIAL PRIMARY
  KEY` only, matching `agencies`. `agency_stations.agency_role_id`'s FK updated from
  `REFERENCES agency_roles(id)` to `REFERENCES agency_roles(api_id)` to match. See
  [ADR-0006](../decisions/0006-single-surrogate-pk-convention.md) for the now-formalized convention
  this establishes for future tables.
- **No logging added to the new loader**, despite
  [checkpoints/2026-11-13-migrations-disabled-regression.md](2026-11-13-migrations-disabled-regression.md)
  suggesting a print/log line on every call so a disabled call site is visible — explicitly out of
  scope for this task (requested as a pure structural split, "no code change, just breaking into
  pieces"). Still an open suggestion, not acted on.

## Tests executed and results

- `python3 -m py_compile` on `dbConnection.py` and all 28 migration files — passed.
- Built a throwaway, fully isolated local Postgres 16 instance (own `initdb`/`pg_ctl`, no sudo,
  custom Unix socket dir, no TCP listener) to avoid touching the system's `postgres` cluster or the
  real OSU database. Loaded `database/gnss_data_dump.sql` (a July 2026 schema snapshot, missing
  several of the later migrations — gamit_projects, reference_frames, RinexQC tables, agency
  tables — so it exercises most of the 28 blocks for real) into cloned test databases.
- Ran the **old** monolithic `_run_db_migrations()` against one clone and the **new** split loader
  against a second, independently-created clone of the same starting schema; `pg_dump
  --schema-only` output was byte-for-byte identical between the two (only difference: pg_dump's
  random per-run `\restrict` token) — confirms the split changed nothing observable.
- Ran migrations a second time against the same database (new connection) — clean no-op, confirming
  idempotency survived the split.
- After the `agency_roles` PK fix: re-ran against a fresh clone of the same starting dump, confirmed
  via `\d agency_roles` that `api_id` is the sole column/PK, confirmed the seeded
  `('owner'), ('host')` rows got sequential `api_id` values, and confirmed
  `agency_stations_agency_role_id_fkey` now points at `agency_roles(api_id)`. Re-ran a second time —
  clean no-op.
- `pytest geode/tests` (full suite): 241 passed, 127 skipped, both before and after the
  `agency_roles` fix — matches the prior known-good baseline noted in `knowledge/known-issues.md`.
- Test Postgres instance and its socket dir were stopped and removed at the end of the session;
  nothing was run against the real OSU database.

## Known issues / limitations

- `database/migration_plan_engine_table_naming.md`'s file/line references into `dbConnection.py`
  are now stale (see `knowledge/known-issues.md` entry) — not rewritten here, flagged for whoever
  picks that plan up next.
- The suggestion from the 2026-11-13 regression checkpoint (log every migration module as it's
  checked, not just when one fires) remains unimplemented.

## Next steps

- None required for this change. If/when `database/migration_plan_engine_table_naming.md` is
  picked up, update its `dbConnection.py` references against the current
  `geode/database_migrations/` layout first.
