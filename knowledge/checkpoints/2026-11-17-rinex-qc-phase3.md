# RINEX QC module: Phase 3 (DB writer, backfill/incremental, ArchiveService hook)

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Completed the RINEX QC module per `knowledge/architecture/rinex-qc.md`'s plan: the real database
writer, `com/RinexQC.py`'s `--backfill`/`--incremental` modes (station-restricted or whole-archive),
and `ArchiveService.py`'s default end-of-run QC hook. Asked Demian whether backfill should be part
of ArchiveService or its own program; the answer (no strong preference, follow best practices) was
to put the actual selection/dispatch logic in exactly one place
(`geode/rinex_qc/batch.py`) and give it two thin callers — `com/RinexQC.py` and
`ArchiveService.py`'s hook — rather than duplicating it. See `architecture/rinex-qc.md`'s "Phase 3"
section for the lasting design notes; this checkpoint is the session log.

## Files / modules affected

- `geode/rinex_qc/pipeline.py`: `process_one_file()` now accepts `ObservationYear`/
  `ObservationDOY`/`rinex_id`, and always returns a populated `run` dict (even on failure) so a
  failed run is always storable, not dropped.
- `geode/rinex_qc/writer.py`: added `write_db()` (+ `_quantize_bins()`, `_run_value()`/
  `_signal_value()`/`_bins_value()` helpers).
- `geode/rinex_qc/batch.py` (new): `find_pending()`, `run_qc_job()`, `callback_handle()`,
  `process_batch()`.
- `com/RinexQC.py`: added `--backfill`/`--incremental`/`--force`/`--noparallel`; the positional
  argument is now dual-purpose (file paths in standalone mode, a station list in DB mode).
- `com/ArchiveService.py`: added `process_rinex_qc()` + `--no_rinex_qc`, called at the end of
  `main()`.
- `geode/tests/test_rinex_qc_writer.py` (new): `_quantize_bins()` unit tests.
- `knowledge/architecture/rinex-qc.md`, `cli-reference-map.md`, `overview.md`, `STATUS.md` updated.

## Architectural decisions

See `architecture/rinex-qc.md`'s "Phase 3" section. The two worth remembering if touching this
code later:
- `write_db()` uses raw parameterized SQL inside `begin_transac()`/`commit_transac()`, not
  `Cnn.insert()`/`insert_many()` -- those each call `self.cnn.commit()` internally, which breaks
  the run+signal+bins atomicity Phase 2's design requires.
- `geode.rinex_qc` is imported **locally inside** `ArchiveService.py`'s `process_rinex_qc()`, not
  at module level, so `gnssmultipath` never enters ArchiveService's import graph for a
  `--no_rinex_qc` run or an install that hasn't added the dependency yet.

## Tests executed and results

Full `geode/tests/` suite: 211 passed, 0 failed, 127 skipped (unrelated). New integration testing
against a **third disposable Postgres 16 instance** (this sandbox has no live `gnss_data` database
and no existing test harness for this layer -- same constraint as the schema-migration checkpoint):

- Built a real archive layout in the test DB: `keys`/`rinex_tank_struct` rows (network(3)/
  station(4)/year(4)/doy(3) levels), a `networks`/`stations` row, and a `rinex` row for the real
  LHCL day-005 file, placed at the exact path `pyArchiveStruct.build_rinex_path()` computes for it.
- `Cnn.__init__` has no `port` option (hardcoded to Postgres' default 5432), so to point a *real*
  `dbConnection.Cnn('gnss_data.cfg')` call at a non-default-port throwaway instance, this session's
  test scripts monkeypatched `psycopg2.connect` to force the host/port/user -- a test-only shim,
  not a code change.
- `batch.find_pending()` -> `batch.run_qc_job()` -> `writer.write_db()` run directly: produced the
  exact same run/signal values already validated in the Phase-1-only checkpoint (n_obs=81816,
  mp_rms_m=0.302, n_slips=222 for the 1C signal, etc.) and 827-cell bin rows per signal, matching
  the seeded grid.
- Re-running `find_pending()` after a successful write correctly returned empty (the file no
  longer "pending" under that config_hash).
- A different `config_hash` (elev_cutoff_deg=15 instead of 10) correctly produced a **second**
  `rinex_qc_run` row rather than replacing the first -- "new runs sit beside old ones" holds.
- `--force`-equivalent (`write_db(..., force=True)`) correctly deleted the existing row for that
  config_hash (new `id`, cascading to its old signal/bin rows) and inserted a fresh one, without
  duplicating rows for other config_hashes.
- `com/RinexQC.py`'s argument parsing exercised directly via `--dry-run` for all four shapes
  (`--backfill <station>`, `--incremental` with no station filter, standalone file mode, and the
  `--backfill --incremental` mutual-exclusivity error) -- no DB/JobServer touched by `--dry-run`.
- `com.ArchiveService` and `com.RinexQC` both import cleanly; confirmed `gnssmultipath` is **not**
  in `sys.modules` after importing `com.ArchiveService` (the local-import design holds).

**Not exercised**: the actual `pyJobServer`/`dispy` parallel dispatch path for this module
specifically. `pyJobServer.JobServer.__init__` calls `test_node()` even with `--noparallel`, which
checks for GAMIT/PPP executables, `grdtab`/`otlgrid`/`ppp_exe`, and ATX files -- none of which
exist in this sandbox, and a failure there calls `os._exit(1)` (a hard process exit, not a Python
exception), so it couldn't be worked around for this environment. This is a gap in *this session's
environment*, not evidence against the code: `dispy` itself was already validated end-to-end in the
Python-3.11-migration checkpoint, and `batch.process_batch()`'s `create_cluster()`/`submit()`/
`wait()`/`close_cluster()` sequence mirrors `com/ScanArchive.py`'s own working `process_ppp()`
exactly (same call shapes, same callback pattern). Worth a real run against an actual GeoDE
deployment before relying on it at scale.

## Known issues / limitations

- `run_qc_job()` runs the full pipeline (including the ~1.4-4.8s gnssmultipath call) before
  `write_db()`'s own skip-on-conflict check can short-circuit it. In normal operation this never
  matters -- `find_pending()` already excludes files with an existing run before they're even
  submitted as jobs -- but a direct/manual `run_qc_job()` call (or a race between two concurrent
  backfill runs) wastes one full analysis before discovering it should have skipped. Not optimized;
  rare enough in practice not to be worth the added complexity of a pre-check.
- `ArchiveService.py`'s hook calls `find_pending()` over the **whole archive** every run (not
  scoped to "files this run just archived"), per the original design doc's "or simply reusing the
  incremental query" option. Fine as long as the current-config backlog stays small (the common
  case, since the hook runs after every ArchiveService pass) -- would need scoping to newly-touched
  rows if a large one-time backlog ever needs to coexist with frequent small ArchiveService runs.

## Next steps

- Get the project's actual validation fixtures from Demian and check the documented reference
  values.
- Start the dynamic visualization module (backend for the separate web frontend) -- no code yet,
  no design decisions made.
- A real `--backfill`/ArchiveService run against an actual GeoDE deployment, to exercise the
  `pyJobServer` parallel path this sandbox couldn't test.
