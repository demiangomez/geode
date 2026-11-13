# Automatic DB migrations were silently disabled since Oct 5

- **Date:** 2026-11-13
- **Branch:** master

## Summary

After shipping the `ppp_antenna_residuals` -> `ppp_soln` FK fix
([checkpoint](2026-11-13-ppp-antenna-residuals-fk.md)) and releasing it as `geode-gnss` 1.2.74, a
colleague upgraded and re-ran `MergeStation.py` but hit the identical `ForeignKeyViolation`.
Investigation found the real root cause: `run_db_migrations(self)`, the call inside `Cnn.__init__`
that's supposed to run on every new connection, had been commented out in commit `9009d6d`
("Improved event recording for PPP", 2026-10-05) — an unrelated one-line change bundled into that
commit. Since then **no migration has run automatically against production**, regardless of which
package version is installed. Confirmed directly against the live `gnss_data_osu` database
(`ses-pgsql.asc.ohio-state.edu`, credentials in `~/pg_osu/gnss_data.cfg`):
- `ppp_antenna_residuals`'s FK still showed `ON DELETE CASCADE` only, even after the colleague's
  1.2.74 upgrade. Downloaded the 1.2.74 wheel from PyPI directly and confirmed it does contain the
  fix — so the installed code was correct, but never executed.
- `stacks`'s `reloptions` showed only `{autovacuum_enabled=true}` — the `autovacuum_analyze_scale_factor`
  tuning from commit `3fb3420` ("Speed up PlotETM.py...", 2026-10-29) is also unapplied for the
  same reason.

Fix: uncommented `run_db_migrations(self)` in `geode/dbConnection.py`'s `Cnn.__init__`. Once this
ships and any client reconnects, both queued migrations (the FK fix and the `stacks` autovacuum
tuning) will apply automatically — no manual DB intervention needed, and none was performed in
this session (checked the user before considering it, they declined, preferring the normal
automatic path).

## Files / modules affected

- `geode/dbConnection.py`: restored the `run_db_migrations(self)` call in `Cnn.__init__`.

## Architectural decisions

None — this restores previously-intended behavior (the function's own docstring/comment at
`MIGRATIONS_LOCK_ID` already assumed "every new Cnn() runs migrations").

## Tests executed and results

- Confirmed via live `psql` query against `gnss_data_osu` that both queued migrations are
  currently unapplied (see Summary).
- Downloaded and inspected the `geode_gnss-1.2.74` wheel from PyPI directly — confirmed it
  contains the FK fix, ruling out a bad release/build as the cause.
- `git log -p -L` on the affected line identified the exact commit, date, and author that
  introduced the regression.
- Did **not** re-run `MergeStation.py` or otherwise re-verify end-to-end in this session — that
  requires a new package release and the colleague re-running on the HPC side.

## Known issues / limitations

- Every migration added between 2026-10-05 and now (this fix, plus the `stacks` autovacuum
  tuning) has been sitting unapplied in production. Once this fix ships, the next connection from
  any client will apply both automatically (idempotent, existence-checked) — but if the `stacks`
  planner-statistics problem described in `3fb3420` has been causing slow `PlotETM.py` runs in
  the meantime, that's now explained.
- No safeguard exists to catch this class of regression (a one-line call silently dropped inside
  an unrelated commit) — `run_db_migrations()` has no test coverage and no startup log line
  confirming it ran. Worth considering a `print`/log statement on every call (not just when a
  migration actually fires) so a disabled call site is visible in normal output, or a smoke test.

## Next steps

- Cut a new release once this commit lands; have the colleague upgrade again and re-run
  `MergeStation.py tig.aldi usa.aldi` to confirm it now succeeds.
- Consider adding minimal test coverage or a startup log line for `run_db_migrations()` so this
  class of regression is caught sooner next time (see Known issues above) — not done here, just
  flagged.
