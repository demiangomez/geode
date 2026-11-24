# Fix station-merge crash on ppp_antenna_residuals FK

- **Date:** 2026-11-13
- **Branch:** master

## Summary

`IntegrityCheck.py ... -del_stn` crashed with a `ForeignKeyViolation` while merging stations
(`tig.aldi` -> `usa.aldi`) that had PPP antenna residuals. `ppp_antenna_residuals`'s FK to
`ppp_soln` only declared `ON DELETE CASCADE`, not `ON UPDATE CASCADE`. `station_merge`'s
`_move_time_tables()` reassigns time-table rows to the destination station by `UPDATE`ing
`ppp_soln`'s key columns (`NetworkCode`/`StationCode`) in place rather than delete+reinsert, so
Postgres needs `ON UPDATE CASCADE` to propagate that key change to the referencing
`ppp_antenna_residuals` rows; without it the parent `UPDATE` is rejected outright.

## Files / modules affected

- `geode/dbConnection.py` (`_run_db_migrations`): fixed the `ppp_antenna_residuals` `CREATE TABLE`
  DDL for fresh installs, and added an existence-checked `ALTER TABLE` migration block (following
  the file's existing `pg_get_constraintdef()` idiom, e.g. `sources_servers_protocol_check`,
  `reference_frames_engine_check`) to upgrade already-created tables. Runs automatically on the
  next `Cnn()` connection (every connection calls `run_db_migrations()`).

## Architectural decisions

None beyond following the file's existing migration idiom (drop/re-add a named constraint, gated
on inspecting `pg_get_constraintdef()` for the current definition).

## Tests executed and results

- `python3 -m ast` parse of `geode/dbConnection.py` — syntax check only.
- Cross-checked every other table that references a `station_merge.TIME_TABLES` table
  (`gamit_soln_excl` -> `gamit_soln`, `stacks` -> `gamit_soln`) against
  `database/gnss_data_dump.sql` and confirmed both already carry `ON UPDATE CASCADE ON DELETE
  CASCADE` — `ppp_antenna_residuals` -> `ppp_soln` was the only one missing it, confirming the fix
  is scoped correctly rather than papering over one symptom of a wider pattern.
- **Not run against a live Postgres.** No DB credentials/local replica were available in this
  environment (`gnss_data.cfg` points at a remote host; the local postgres cluster here has no
  accessible role). The migration has not been exercised end-to-end.

## Known issues / limitations

- `gamit_antenna_residuals` has an analogous FK shape (-> `gamit_stats` on `(project, subnet,
  year, doy, system)`) with the same `ON DELETE CASCADE`-only pattern. `gamit_stats` is not in
  `station_merge.TIME_TABLES` and is never `UPDATE`d by `station_merge`, so it isn't exposed to
  this bug today — left alone rather than fixed speculatively. Logged in
  `knowledge/known-issues.md` in case `gamit_stats` ever becomes mutable by a merge-like path.
- A second station-merge problem was mentioned ("did not work as expected", not a crash) but no
  details were given — not investigated.

## Next steps

- Confirm on the next real run that the `tig.aldi` -> `usa.aldi` merge (or any merge involving
  stations with `ppp_antenna_residuals` rows) now succeeds against production.
- Get details on the second, non-crashing merge issue if still relevant.
