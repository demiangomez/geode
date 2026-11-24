# RINEX QC module: Phase 2 (schema migration)

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Added the RinexQC schema (`qc_grid`, `rinex_qc_run`, `rinex_qc_signal`, `rinex_qc_signal_bins` +
views `rinex_qc_bins_long`/`rinex_qc_latest`) to `geode/dbConnection.py`'s `_run_db_migrations()`,
following the file's existing existence-checked idempotent-DDL idiom. See
`knowledge/architecture/rinex-qc.md`'s "Schema (Phase 2, done)" section for the lasting design
notes (why `rinex_id` replaces the doc's `Filename` placeholder, why only one grid is seeded, how
`rinex_qc_latest`/`rinex_qc_bins_long` work) — not repeated here.

## Files / modules affected

- `geode/dbConnection.py`: new migration block at the end of `_run_db_migrations()` (defensive
  `rinex.api_id` backfill + the four new tables/two views/indexes).
- `knowledge/architecture/rinex-qc.md`, `database-schema.md`, `STATUS.md` updated.

## Tests executed and results

No live `gnss_data` database (production or dev) was reachable in this environment, and
`geode/dbConnection.py` has no existing test harness to extend (see `architecture/overview.md`'s
"Testing & CI" coverage-gap note). Validated instead with a **throwaway, disposable Postgres 16
instance** (`initdb`/`pg_ctl` run as the current OS user against a scratch data directory on an
alternate port + Unix socket, no sudo/system-service involvement, destroyed afterward):

- Loaded the real `database/gnss_data_dump.sql` (79-table schema snapshot) into it, then called
  `geode.dbConnection._run_db_migrations()` directly (via a hand-built `Cnn`-like object wrapping a
  raw `psycopg2` connection, bypassing `gnss_data.cfg`/port-5432 assumptions baked into `Cnn.__init__`).
- **Full existing migration suite ran clean against the real dump** (gamit_projects, reference_frames,
  reference_frame_constraints, ppp_antenna_residuals FK fix, stacks tuning, etc.) — confirms this
  change didn't regress anything upstream of it.
- `rinex.api_id` already existed in the dump, so the defensive-backfill branch correctly no-op'd
  (confirmed it does NOT print its "Adding api_id field..." message).
- `qc_grid`'s seeded row matches `geode.rinex_qc.aggregate.make_equal_area_grid()`'s actual output
  exactly (`elev_edges`/`az_bins` compared byte-for-byte against a live Python call).
- **Idempotency**: ran `_run_db_migrations()` a second time against the now-migrated database —
  produced zero output (every check, including the new RinexQC block, correctly found everything
  already in place and skipped).
- Inserted real-shaped test rows (`networks` → `stations` → `rinex` → `rinex_qc_run` →
  `rinex_qc_signal` + `rinex_qc_signal_bins`, the bins using a 827-length array matching the
  seeded grid's cell count) and queried both views:
  - `rinex_qc_latest` returned the expected single row.
  - `rinex_qc_bins_long` correctly unnested two non-NULL test cells to the right
    ring/elevation/sector/azimuth limits (verified by hand: ring 0 is `[0,5)` degrees with 72
    sectors at 5deg each; cell_index 1 -> sector 0 -> az `[0,5)`; cell_index 3 -> sector 2 ->
    az `[10,15)` — matched the view's output exactly).
- **`ON DELETE CASCADE` verified end-to-end**: deleting the test `rinex` row correctly cascaded
  through `rinex_qc_run` into both `rinex_qc_signal` and `rinex_qc_signal_bins` (all three went
  from 1 row to 0).
- Confirmed `lz4` column compression is actually applied (`pg_attribute.attcompression = 'l'` for
  `n_obs`/`mp_rms_mm`/`snr_ddbhz`).

## Post-commit fix (same day)

The disposable Postgres 16 instance used for validation happened to be built with lz4 support;
Demian's actual deployment is not. `ALTER TABLE rinex_qc_signal_bins ... SET COMPRESSION lz4` was
inline in the same transaction as the table creation, so on a server without lz4 support the whole
transaction aborted -- nothing committed, and because `run_db_migrations()` runs on every `Cnn()`
connection, this broke **every** `com/*.py` script immediately (the aborted transaction state also
broke the `pg_advisory_unlock()` call right after it, in `run_db_migrations()` itself). Fixed by
moving the compression attempt into its own transaction after table creation, wrapped in
try/except with an explicit `rollback_transac()` on failure -- see commit `2bb37b9`. Re-verified
against two disposable instances: one where lz4 succeeds, and a direct simulation of the failure
class (`SET COMPRESSION not_a_real_method`) confirming the connection stays healthy afterward.
Lesson: `lz4` column compression needs PG14+ **and** `--with-lz4` at build time -- version alone
doesn't guarantee it, and this wasn't caught by testing against only one (capable) Postgres build.

## Known issues / limitations

- Not tested: the `rinex.api_id` column actually being *absent* and getting added fresh (the dump
  already had it). The defensive-backfill code path itself was only exercised by the "already
  exists, skip" branch here; worth a deliberate test against a dump with that column stripped
  before this is trusted on an install that genuinely lacks GeoDE Studio.
- No automated pytest coverage added for the migration (see Summary) -- if `geode/dbConnection.py`
  ever gets a real test harness, this migration should get a regression test alongside it.
- `rinex_qc_latest`'s "newest run = default-config run" assumption (see architecture doc) has not
  been tested with actually-conflicting config_hashes for the same file; it's a design inference,
  not yet empirically exercised.

## Next steps

- Phase 3: `writer.py`'s DB writer, `com/RinexQC.py --incremental`/`--backfill`, the
  `ArchiveService.py` hook.
- Get the project's actual validation fixtures from Demian.
