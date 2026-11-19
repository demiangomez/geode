# Agency metadata tables (agency_roles, agencies, agency_stations)

- **Date:** 2026-11-19
- **Branch:** master

## Summary

Added three tables to `run_db_migrations()` to track which organizations contribute, own, or host
which GNSS stations: `agency_roles` (lookup: owner/host/...), `agencies` (one row per contributing
org, contact info only), and `agency_stations` (many-to-many link, agency + station + role +
free-text description, one row per agency/station pair). `agency_roles.id` and `agencies.api_id`
are each table's own primary key directly; `agency_stations` FKs to `stations.api_id` (not
stations' own composite key) and to `agencies.api_id`.

## Files / modules affected

- [geode/dbConnection.py](../../geode/dbConnection.py) — new migration block at the end of
  `_run_db_migrations()`, after the lz4-compression block.
- [knowledge/architecture/database-schema.md](../architecture/database-schema.md) — added the new
  tables to the "owned by this repo" list.

## Architectural decisions

- **`agencies.api_id` is the table's primary key directly** (`SERIAL PRIMARY KEY`), not a surrogate
  column parallel to a different natural-key PK. `name` is `UNIQUE NOT NULL` but not the key, so
  renaming an agency never cascades anywhere. This was a real back-and-forth during the task: first
  cut used `name` as PK (matching most other tables in this schema, which key on a natural business
  value); user asked for a serial surrogate instead; then corrected that the surrogate should be
  `api_id` itself, not a second `id` column alongside it. See
  [[feedback-ask-before-schema-design-choices]] in memory — this should have been asked up front.
- **`agency_roles`** keeps the `id SERIAL PRIMARY KEY` / `name UNIQUE` shape explicitly requested
  (same shape as the Django app's `api_stationrole`), plus `api_id` as a genuinely separate surrogate
  column (not the PK) — unlike `agencies`, `agency_roles.id` is the real key here, `api_id` is only
  for the Studio/API layer, because the requirement was explicitly "just has an id and a name."
- **`agency_stations.station_id` FKs to `stations.api_id`**, not stations' own composite
  `(NetworkCode, StationCode)` primary key — same reasoning as the existing
  `rinex_qc_run.rinex_id -> rinex.api_id`. `stations.api_id` is defensively backfilled (mirrors the
  existing `rinex.api_id` block) for installs that have never run GeoDE Studio against this database.
- **`agencies` holds identity/contact info only** — name, primary/secondary contact, RTN names,
  `api_id`. Submission-specific fields (number of stations, date range available, transfer method,
  submission date, IGS site log URL) were deliberately dropped after user review mid-task — they
  describe a specific intake/submission event, not a durable property of the agency. Not stored
  anywhere currently; no "submissions" table exists for them.
- **`agency_stations_pkey PRIMARY KEY (agency_id, station_id)`** enforces "a given agency can only
  be associated with a given station once" — `agency_role_id`/`description` describe that one
  relationship, they don't key it.
- **`ON DELETE CASCADE` on both `agency_id` and `station_id`** in `agency_stations`, and this is
  intentional, confirmed by the user: deleting a station or an agency should drop its associations,
  not migrate them (e.g. `station_merge.py` merging stations should NOT carry agency associations
  over to the destination station). `agency_role_id -> agency_roles(id)` has no CASCADE (default
  RESTRICT): a role still referenced by an association can't be deleted out from under it.
- **No defensive/auto-upgrading migration code for the PK rework.** The `name`-as-PK shape was only
  ever applied to one database (this project's live OSU instance, empty of real agency data) during
  this same task, before the correction landed — so rather than adding a permanent
  auto-detecting-and-upgrading block to `run_db_migrations()` (which was tried and explicitly
  rejected — "the defensive block is exactly what I DO NOT want you to do"), the `CREATE TABLE
  agencies` statement was just fixed directly, and the one database that already has the old shape
  gets a one-off manual fix (see below), not production-auto-migration logic.
- Seeded `agency_roles` with `('owner'), ('host')` from the user's own example; not an authoritative
  enum — more rows can be added freely since it's a plain lookup table, no CHECK constraint.
- `transfer_method`/dropdown-style fields were dropped along with the other intake-only columns (see
  above), so there's currently nothing to enforce a GeoDE Studio dropdown against.

## Tests executed and results

- `python3 -c "import ast; ast.parse(...)"` after every edit — syntax check, passed.
- Earlier in the task (before the PK rework), connected to the live OSU database
  (`~/pg_osu/gnss_data.cfg`) via `geode.dbConnection.Cnn`, which runs `run_db_migrations()` on
  connect, and verified: all three tables/columns created, `agency_roles` seeded correctly,
  `stations.api_id` already existed. Also verified, inside a rolled-back transaction (no permanent
  data left): a duplicate `(agency_id, station_id)` insert is rejected, and deleting a station
  cascade-deletes its `agency_stations` row rather than being blocked.
- **The PK rework (name -> api_id) itself has NOT been applied to or tested against the live
  database** — per explicit instruction, this round of changes was code-only. The live database
  still has the earlier (`name`-as-PK) shape from the first verification pass; see "manual fix"
  below.

## Known issues / limitations

- The live OSU `agencies` table currently has `name` as its primary key (the pre-correction shape)
  and needs the one-off fix below applied by hand before it matches what's now in
  `geode/dbConnection.py`. It is currently empty (no real agency rows), so this is a pure DDL change,
  no data migration needed.
- No CLI tooling to manage these tables yet (add/edit agencies, assign roles to stations) — this
  checkpoint only covers the schema.

## Next steps

- **Run the manual fix below against the live database** (not done by this session, per
  instruction) to bring it in line with the corrected `CREATE TABLE agencies` in
  `geode/dbConnection.py`:

  ```sql
  BEGIN;

  ALTER TABLE agencies DROP CONSTRAINT agencies_pkey;
  ALTER TABLE agencies ADD CONSTRAINT agencies_name_key UNIQUE (name);
  ALTER TABLE agencies DROP CONSTRAINT agencies_api_id_key;
  ALTER TABLE agencies ADD CONSTRAINT agencies_pkey PRIMARY KEY (api_id);

  COMMENT ON COLUMN agencies.name IS
      'Full agency name, e.g. ''Ohio Department of Transportation''. Unique, but not the primary key -- agency_stations references agencies.api_id, so renaming an agency never cascades anywhere.';
  COMMENT ON COLUMN agencies.api_id IS
      'Primary key, and the surrogate id used by the Django/web-interface API layer; also the FK target for agency_stations.agency_id.';

  COMMIT;
  ```

  `agency_stations.agency_id` already FKs to `agencies.api_id` (which was already `UNIQUE`, just not
  `PRIMARY KEY`), so this doesn't need to touch `agency_stations` at all.
- If/when submission-intake fields (number of stations, date range, transfer method, submission
  date, IGS site log URL) need to be stored, decide where they belong (likely a separate table keyed
  on agency, since they describe an event, not the agency itself) and raise it as its own task.
- No CLI/reporting code reads these tables yet; that's presumably the next consumer once GeoDE
  Studio or a `com/` script needs to populate/display agency data.
