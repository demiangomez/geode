# Database Schema

Single Postgres database (conventionally named `gnss_data`), shared between this repo and the
sibling Django web app (see [external-django-app.md](external-django-app.md)). 79 tables total as
of this writing.

## Ownership split

**Owned by this repo** (geodetic/processing domain, managed by `run_db_migrations()` in
`geode/dbConnection.py`): `stations`, `stationalias`, `stationinfo` + `stationinfo_audit`,
`networks`, `rinex` + `rinex_tank_struct` + `rinex_sources_info`, `receivers`, `antennas` +
`antenna_calibration*` + `antenna_residuals` + `antenna_multipath` + `antenna_empirical`,
`atx_files`, `ppp_soln` + `ppp_soln_excl` + `ppp_antenna_residuals`, `gamit_soln` +
`gamit_soln_excl` + `gamit_antenna_residuals` + `gamit_stats` + `gamit_subnets` + `gamit_ztd` +
`gamit_htc` + `gamit_projects`, `stacks`, `reference_frames` + `reference_frame_constraints`,
`etms`, `etm_params`, `earthquakes`, `events`, `locks`, `executions`, `keys`, `aws_sync`,
`data_source`, `s_score_cache`, `sources_metadata` + `sources_servers` + `sources_stations` +
`sources_formats`.

**Owned by the sibling Django app** (migrated by Django, not by this repo — never add/alter these
from `geode` code except through the documented read-only joins): `api_*` (campaigns, person,
role, stationtype, stationmeta, stationstatus, visits, images, attached files, ...), `auth_*`,
`django_*`, `auditlog_logentry`.

## Migration mechanism

There is **no migration-version table and no framework** (no Alembic/Django-migrations
equivalent). `run_db_migrations()` is a hand-written, idempotent "check column/table/index exists,
then ALTER/CREATE if not" script, guarded by a Postgres advisory lock
(`pg_advisory_lock(MIGRATIONS_LOCK_ID)`). It's meant to run on every connection — check the current
call site in `dbConnection.py` before assuming it's wired up; it has been seen commented out.

`database/gnss_data_dump.sql` is a **`pg_dump` snapshot**, regenerated from a live DB — it must
never be hand-edited to represent a schema change; it will pick up any change automatically next
time someone dumps it.

`database/postgres_schema/schemaspy/` is a one-time SchemaSpy HTML export (relationship diagrams,
per-column stats) — not auto-regenerated, treat as a historical snapshot, not current truth.

## Pending, unapplied change: multi-engine table naming

`database/migration_plan_engine_table_naming.md` is a fully-specified, reviewed migration plan
(not yet applied, not yet on any branch per its own "Status" line) to rename
`gamit_soln`→`soln_gamit`, `ppp_soln`→`soln_gpspace`, `stacks`→`stacks_gamit`, and the matching
antenna-residuals tables, in preparation for adding a second PPP engine and a second GAMIT-like
engine (PAGES). It documents the full DDL, the `run_db_migrations()` insertion point, and every
`.py` file/line that references the old table names. Read it in full before touching
`gamit_soln`/`ppp_soln`/`stacks` naming anywhere — don't rediscover this plan from scratch.

## Getting the current ground truth

Don't trust a cached table list (including the one above) for anything you're about to act on —
re-derive it:

```sql
SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1;
```

or `grep -oP "CREATE TABLE (IF NOT EXISTS )?public\.\K[a-zA-Z_\"]+" database/gnss_data_dump.sql`
against a freshly-dumped `gnss_data_dump.sql`.
