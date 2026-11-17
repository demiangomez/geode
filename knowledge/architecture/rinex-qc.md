# RINEX QC (`geode/rinex_qc/`, `com/RinexQC.py`)

Per-signal/per-bin RINEX quality-control statistics (code multipath, SNR, cycle slips), computed
by the `gnssmultipath` library, with full provenance. Built from a design doc ("GeoDE RINEX QC
Module Implementation Instructions"); several things changed from that doc's first draft once the
actual codebase and `gnssmultipath`'s real behavior were checked — see the ADRs below rather than
trusting the original doc's assumptions.

**Status as of 2026-11-17: Phases 1, 2 and 3 are all done and validated** (standalone CLI, schema,
DB writer, `--incremental`/`--backfill` CLI modes, and the `ArchiveService.py` hook). What's left
is validating against the project's actual documented fixtures (see "What's not built yet" below)
and dynamic visualization (planned next, no code yet). Check `git log`/`git branch` before
assuming otherwise.

## Package layout

| Module | Responsibility |
|---|---|
| `config.py` | `RinexQCConfig` — JSON + CLI-switch config, frozen dataclass. `config_hash` covers only science-affecting fields (`elev_cutoff_deg`, `systems`, `orbit_source`, `default_grid_id`, `snr_unit_policy`) — run-specific fields (`nav_paths`/`sp3_paths`/`keep_tool_outputs`/`work_dir`) are excluded on purpose. |
| `prepare.py` | Thin wrapper around `pyRinex.ReadRinex` — **no decompression/header logic lives here**, it's all reused from `pyRinex.py` (see ADR-0002's pyRinex fix). |
| `orbits.py` | Resolves broadcast (`GetBrdmOrbits` → `GetBrdcOrbits`+`GetBrdgOrbits` fallback) or SP3 (`GetSp3Orbits`) orbits via the existing `pyProducts` classes; `guard_same_day()` rejects a nav/sp3 file whose own filename encodes a different day than the observation. |
| `signals.py` | RINEX2→RINEX3 `(gnss, band_attr)` mapping table + `find_snr_code()` (RINEX2 SNR is band-only; RINEX3 tries an exact attribute match first, falls back to band-only). |
| `runner.py` | The only module that imports `gnssmultipath`. In-process call to `GNSS_MultipathAnalysis`; `write_results_to_csv=False` — **the returned dict already has everything needed**, see below. |
| `aggregate.py` | `make_equal_area_grid()` (deterministic, 5° elevation rings × cos-weighted azimuth sectors), `bin_indices()` (vectorized), `aggregate_signal()` (per-signal + per-bin stats from one gnssmultipath result), `pool()` (exact n-weighted combination of stored bins — the basis for weekly/yearly stacking). |
| `writer.py` | `write_json_csv()` (Phase 1 standalone output) and `write_db()` (the real DB writer — run+signal+bin rows in one transaction, skip-or-`--force`-replace on a unique-key conflict). `_quantize_bins()` does the storage-time mm/0.1-unit quantization (NULL = empty cell, not zero). |
| `pipeline.py` | `process_one_file()` — the one place that chains prepare→orbits→runner→signals→aggregate and decides `ok`/`partial`/`failed`. Its `run` dict is always populated, even on failure (seeded from the caller-known `NetworkCode`/`StationCode`/`ObservationYear`/`ObservationDOY`/`rinex_id` when given), so a failed run is always something `write_db()` can store, not a dropped record. |
| `batch.py` | `find_pending()` (the `rinex` LEFT JOIN `rinex_qc_run` WHERE NULL query, optionally station-restricted/forced/ordered) and `process_batch()` (dispatches `run_qc_job()` over candidates via a caller-owned `pyJobServer.JobServer`). The one shared implementation `com/RinexQC.py`'s `--backfill`/`--incremental` and `ArchiveService.py`'s hook both call — neither reimplements "which files need a QC run". |

`com/RinexQC.py` has two modes on the same entry point:
- **Standalone** (default): `rinex_file [rinex_file ...] [--config] [--elev-cutoff] [--systems] [--nav] [--sp3] [--out-dir] [--keep-tool-outputs] [--dry-run]` — writes JSON/CSV, no DB.
- **Database** (`--backfill` or `--incremental`, mutually exclusive): `[station ...] --backfill|--incremental [--force] [--noparallel]` — the positional argument becomes a station-list (same syntax as `ScanArchive.py` etc., via `geode.Utils.process_stnlist`) instead of file paths; omit it for the whole archive. `--backfill` orders newest-first; `--incremental` doesn't bother (meant for the small set of just-archived files `ArchiveService.py` hands it). `--force` reprocesses even where a run already exists under the current config, replacing it.

Exit code 0 iff every file finished `ok`/`partial`.

## Non-obvious decisions (read before touching this code)

- **Sanitization is `pyRinex.ReadRinex`, not a new sanitizer.** It already does temp-copy,
  decompression, header normalization, RINEX 2/3 conversion, interval checking — the same pipeline
  `ArchiveService.py`/`ScanArchive.py` run before PPP. Real gaps found against `gnssmultipath` get
  fixed directly in `pyRinex.check_header()`/the module-level helpers it calls, not duplicated
  here — two so far:
  - A blank `TIME SYSTEM` field for single-GNSS RINEX 2 files (`gnssmultipath` crashes on it
    regardless of system). See [ADR-0002](../decisions/0002-python-311-migration.md).
  - A blank (rather than explicit `0`) per-satellite observation count in RINEX 2's `PRN / # OF
    OBS` record — legal per spec (e.g. a GPS satellite under Anti-Spoofing that never decodes P1),
    but `gnssmultipath`'s whitespace-tokenizing parser drops the blank field as a missing token
    instead of reading it as zero, eventually raising `IndexError: pop from empty list` elsewhere
    in its header parser. Found on a real mixed GPS/GLONASS CORS station (`usa.gisa`) during a
    `--backfill` run; fixed by `pyRinex._fix_prn_obs_blanks()` (only *interior* blanks, between two
    populated fields, get zero-filled — trailing blanks past the last populated field are normal
    padding when a file declares fewer than 9 observation types, and are left alone).
- **No CSV round-trip.** The design doc assumed `runner.py` would parse
  `Result_files_CSV/<SYSTEM>_results.csv` for per-epoch MP/SNR/azimuth/elevation. It doesn't need
  to: everything is already in the `analysisResults` dict `GNSS_MultipathAnalysis()` returns
  in-process (`res[sys_name][band][code]['multipath_range1']` etc., `res['Sat_position'][sys_code]['azimuth']`
  — azimuth **is** populated for broadcast-nav runs too, not just SP3). `write_results_to_csv=False`.
  See [ADR-0003](../decisions/0003-gnssmultipath-dependency.md).
- **Slip counts by elevation band are already computed by the tool.** `gnssmultipath`'s
  `SignalStats` dataclass type-hints claim `range1_slip_distribution: int`, but at runtime it (and
  `cycle_slip_distribution`/`LLI_slip_distribution`/`slip_distribution_LLI_fusion`, plus their
  `_per_sat` variants) is actually a `dict` with `n_slips_0_10`/`n_slips_10_20`/.../`n_slips_over50`/
  `n_slips_NaN`/`n_slips_Tot` keys — verified against a real run, not from reading the source alone
  (ADR-0003 originally said the opposite before that correction; **trust the ADR's correction, not
  its first draft**). `aggregate.py` reads `slip_distribution_LLI_fusion` (the deduplicated fusion
  of the algorithmic range1/cycle-slip methods and the RINEX LLI flag) as the primary `n_slips`/
  `slips_by_elev` source — a judgment call among four slightly different slip-detection methods
  `gnssmultipath` computes, not something the design doc specified.
- **Grid storage is az-el-only**, per Demian's explicit decision: no separate elevation-ring grid is
  seeded or stored; `rinex_qc_grids` holds only the equal-area az-el grid, write-once/versioned (a
  different grid needs a new `grid_id`, never an edit to an existing row — not yet implemented,
  this is a Phase 2/schema note for whoever builds it).
- **`pool()` operates on the stored representation** (`n_obs`/`mp_rms`/`snr_mean` arrays, `NaN` =
  empty cell), reconstructing sum-of-squares internally from `rms² × n`, not on some separate
  in-memory accumulator shape — this is what weekly/yearly stacking will actually call with two
  already-persisted rows' bins.
- **`com/UpdateEarthquakes.py`** was rewritten off `libcomcat` onto direct USGS ComCat REST calls as
  part of unblocking this module's `gnssmultipath` dependency (numpy version conflict chain) — see
  ADR-0002. Unrelated to RinexQC's own logic, but touched in the same effort.

## Schema (Phase 2, done)

`rinex_qc_grids`, `rinex_qc_run`, `rinex_qc_signal`, `rinex_qc_signal_bins` + views `rinex_qc_bins_long`/
`rinex_qc_latest`, added to `geode/dbConnection.py`'s `_run_db_migrations()`, including the
defensive `rinex.api_id` existence-check-and-add (needed because GeoDE Studio's Django app, not
this repo, normally creates that column, and it may not exist on every install). A few departures
from the design doc's literal DDL, worth knowing before writing the Phase 3 DB writer:

- `rinex_qc_run.rinex_id integer REFERENCES rinex(api_id)` replaces the doc's placeholder
  `"Filename" varchar` column — `rinex`'s real primary key is composite
  (`NetworkCode, StationCode, ObservationYear, ObservationDOY, Interval, Completion`) and
  `Filename` isn't unique, so it can't be an FK target. `rinex_qc_run` still keeps
  `"NetworkCode"`/`"StationCode"`/`"ObservationYear"`/`"ObservationDOY"` as denormalized columns
  (copied from the `rinex` row at insert time by the future writer) so station/date queries don't
  need to join `rinex` every time.
- The unique key is `(rinex_id, tool, tool_version, config_hash)` — simpler than the doc's 5-column
  version now that `rinex_id` alone identifies the file.
- `rinex_qc_grids` seeds exactly **one** row (`id=1`, the az-el equal-area grid), computed live from
  `geode.rinex_qc.aggregate.make_equal_area_grid()` at migration time rather than a hardcoded
  literal — so the seeded DB row can never drift from what the code actually generates. No
  elevation-ring grid is seeded (Demian's call — see the checkpoint).
- `rinex_qc_latest` is `DISTINCT ON (rinex_id, tool) ... ORDER BY run_at DESC` — it does **not**
  filter on "the current default config_hash" the way the design doc describes, because "default"
  is a `RinexQCConfig` (client-side) notion this schema has no way to query. It relies on
  incremental/backfill runs always targeting the current default config, so in practice the
  newest run per file+tool already is the default-config one.
- `rinex_qc_bins_long` resolves each unnested cell's elevation/azimuth limits via a `rinex_qc_grids`-driven
  CTE (cumulative sum of `az_bins` per ring via a window function) — validated against a real
  Postgres instance, see the checkpoint for exact numbers.

Validated against a real, throwaway Postgres 16 instance (not the project's actual database) —
see `knowledge/checkpoints/2026-11-17-rinex-qc-schema.md` for what was checked. No automated
pytest coverage was added for the migration itself; `geode/dbConnection.py`/the migration system
has no existing test harness to extend (a pre-existing, separately tracked gap — see
`architecture/overview.md`'s "Testing & CI" section).

## Phase 3 (backfill/incremental/ArchiveService hook, done)

- `writer.write_db()` writes raw parameterized SQL inside `begin_transac()`/`commit_transac()`,
  **not** `Cnn.insert()`/`insert_many()`, which each call `self.cnn.commit()` internally and would
  break the run+signal+bins atomicity the design doc requires. Bulk signal/bin rows go through
  `psycopg2.extras.execute_values()` directly on `cnn.cursor` inside that same transaction.
- `ArchiveService.py`'s `process_rinex_qc()` hook runs at the very end of `main()` (after
  `print_archive_service_summary()`), reusing the *same* `JobServer` the main archiving pass built
  — `create_cluster()` can be called again on one `JobServer` after an earlier `close_cluster()`,
  exactly like `process_visits()`'s two sequential clusters already do. A QC failure never affects
  archiving: the hook is a separate pass over `rinex` rows, not inline in `process_crinex_file`.
  `--no_rinex_qc` opts out for one run. `geode.rinex_qc` is imported **locally inside the hook
  function**, not at `ArchiveService.py`'s top level, so `--no_rinex_qc` (or an install that hasn't
  added `gnssmultipath` yet) never pulls it into ArchiveService's import graph at all.
- `com/RinexQC.py`'s positional argument is dual-purpose: direct RINEX file paths in standalone
  mode, or a station-list (`geode.Utils.process_stnlist` syntax) in `--backfill`/`--incremental`
  mode. Argparse validates the mutual exclusivity and that standalone mode actually got files.

Validated end-to-end against a disposable Postgres 16 instance + a real archive layout (keys/
rinex_tank_struct/stations/rinex rows + the real LHCL day-005 file at its computed archive path,
via a `psycopg2.connect` patch standing in for `gnss_data.cfg`'s hardcoded-port-5432 connection
path — `Cnn.__init__` has no `port` option, so this was the only way to point a real `Cnn()` at a
non-default-port throwaway instance): `find_pending()` → `run_qc_job()` → `write_db()` produced
the exact same run/signal/bin values as the Phase-1-only test earlier; a second `find_pending()`
call correctly returned empty; a different `config_hash` produced a second row rather than
replacing the first; `--force` deleted-and-replaced the original row (new id, cascading to its old
signal/bin rows) without duplicating it. `com/RinexQC.py`'s argument parsing was exercised directly
via `--dry-run` (no DB/JobServer touched). **Not exercised**: the actual `pyJobServer` parallel
dispatch for this module specifically (this sandbox lacks the GAMIT/PPP/ATX files
`pyJobServer.test_node()` checks even in `--noparallel` mode, which is unrelated to RinexQC's own
correctness) — relies on `dispy` itself already being validated (ADR-0002) and `batch.py` mirroring
`ScanArchive.py`'s own working `process_ppp()` dispatch pattern exactly.

## What's not built yet

- Validation against the project's actual documented fixtures (LHCL 1996-251, SANT 2025-212, AGGO
  2025-212) with their exact reference MP RMS/epoch-count/clock-jump values — everything validated
  so far used real-but-different archive data (LHCL/AGGO day 005, 2026) for mechanical correctness,
  not the documented numbers.
- Dynamic visualization of the results (planned next — a backend module for the separate web
  frontend; no code yet).
- `gnssmultipath` dependency acceptance is recorded in [ADR-0003](../decisions/0003-gnssmultipath-dependency.md);
  the Python 3.11 migration it required is [ADR-0002](../decisions/0002-python-311-migration.md).
  Session logs: [2026-11-16-python-311-migration.md](../checkpoints/2026-11-16-python-311-migration.md),
  [2026-11-17-rinex-qc-schema.md](../checkpoints/2026-11-17-rinex-qc-schema.md),
  [2026-11-17-rinex-qc-phase3.md](../checkpoints/2026-11-17-rinex-qc-phase3.md).
