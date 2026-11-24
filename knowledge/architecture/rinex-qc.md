# RINEX QC (`geode/rinex_qc/`, `com/RinexQC.py`)

Per-signal/per-bin RINEX quality-control statistics (code multipath, SNR, cycle slips), computed
by **RINGO** (GSI, Japan — a single static binary, see `scripts/install_ringo.sh`), with full
provenance. Originally built around `gnssmultipath` (ADR-0003); switched to RINGO after
`gnssmultipath` turned up a string of real crashes on ordinary multi-GNSS CORS files at the scale
GeoDE needs to run at (ADR-0004) — see [ADR-0005](../decisions/0005-switch-to-ringo.md) for the
switch itself, what changed, and why. gnssmultipath is fully removed: no pip pin, no patch script,
no import anywhere in `geode/rinex_qc/`.

**Status as of 2026-11-18: Phases 1-4 are all done** (standalone CLI, schema, DB writer,
`--incremental`/`--backfill` CLI modes, the `ArchiveService.py` hook, and visualization), now on
RINGO. What's left is validating against the project's actual documented fixtures (see "What's not
built yet" below). Check `git log`/`git branch` before assuming otherwise.

## Package layout

| Module | Responsibility |
|---|---|
| `config.py` | `RinexQCConfig` — JSON + CLI-switch config, frozen dataclass. `config_hash` covers only science-affecting fields (`elev_cutoff_deg`, `systems`, `orbit_source`, `default_grid_id`, `snr_unit_policy`) — run-specific fields (`nav_paths`/`sp3_paths`/`keep_tool_outputs`/`work_dir`/`ringo_bin`) are excluded on purpose. |
| `prepare.py` | Thin wrapper around `pyRinex.ReadRinex` — kept for *provenance only* now (`header_fixed`, `rinex_version`, receiver/antenna type, interval). RINGO itself reads the original archived file directly, not pyRinex's sanitized temp copy — it handles raw `.Z`/`.gz`/Hatanaka files and the header issues pyRinex fixes with no pre-processing at all (ADR-0005). |
| `orbits.py` | Resolves broadcast (`GetBrdmOrbits` → `GetBrdcOrbits`+`GetBrdgOrbits` fallback) or SP3 (`GetSp3Orbits`) orbits via the existing `pyProducts` classes; `guard_same_day()` rejects a nav/sp3 file whose own filename encodes a different day than the observation. Engine-agnostic, untouched by the RINGO switch. |
| `signals.py` | `find_snr_code()` only — picks the raw SNR column for a given band. The old RINEX2→RINEX3 `(gnss, obs_code) → band_attr` mapping table is gone: RINGO reports multipath per band, not per exact observation code, so there's no attribute to map to any more. |
| `runner.py` | The only module that shells out to `ringo`. Two subprocess calls (`rnxcsv --qcmode` for per-epoch az/el/MP/slip-flags, plain `rnxcsv` for raw SNR), parsed with pandas into `BandData` arrays per system/band. Also owns `_detrend_by_arc()` — see below, this is the part that actually matters. |
| `aggregate.py` | `make_equal_area_grid()` (deterministic, 5° elevation rings × cos-weighted azimuth sectors), `bin_indices()` (vectorized), `aggregate_signal()` (per-signal + per-bin stats from one `BandData`, including `_slips_by_elev()` — RINGO gives per-epoch flags, not a pre-binned distribution like gnssmultipath did), `pool()` (exact n-weighted combination of stored bins — the basis for weekly/yearly stacking). |
| `writer.py` | `write_json_csv()` (Phase 1 standalone output) and `write_db()` (the real DB writer — run+signal+bin rows in one transaction, skip-or-`--force`-replace on a unique-key conflict). `_quantize_bins()` does the storage-time mm/0.1-unit quantization (NULL = empty cell, not zero). Untouched by the RINGO switch — same `summary`/`bins` shape either engine produces. |
| `pipeline.py` | `process_one_file()` — the one place that chains prepare→orbits→runner→aggregate and decides `ok`/`partial`/`failed`. Its `run` dict is always populated, even on failure (seeded from the caller-known `NetworkCode`/`StationCode`/`ObservationYear`/`ObservationDOY`/`rinex_id` when given), so a failed run is always something `write_db()` can store, not a dropped record. `TOOL_NAME`/`TOOL_VERSION` are `'ringo'`/`'0.9.6'`. `n_clock_jumps` is always `None` for now — see "Non-obvious decisions". |
| `batch.py` | `find_pending()` (the `rinex` LEFT JOIN `rinex_qc_run` WHERE NULL query, optionally station-restricted/forced/ordered) and `process_batch()` (dispatches `run_qc_job()` over candidates via a caller-owned `pyJobServer.JobServer`). The one shared implementation `com/RinexQC.py`'s `--backfill`/`--incremental` and `ArchiveService.py`'s hook both call — neither reimplements "which files need a QC run". |

`com/RinexQC.py` has two modes on the same entry point:
- **Standalone** (default): `rinex_file [rinex_file ...] [--config] [--elev-cutoff] [--systems] [--nav] [--sp3] [--out-dir] [--keep-tool-outputs] [--dry-run]` — writes JSON/CSV, no DB.
- **Database** (`--backfill` or `--incremental`, mutually exclusive): `[station ...] --backfill|--incremental [--force] [--noparallel]` — the positional argument becomes a station-list (same syntax as `ScanArchive.py` etc., via `geode.Utils.process_stnlist`) instead of file paths; omit it for the whole archive. `--backfill` orders newest-first; `--incremental` doesn't bother (meant for the small set of just-archived files `ArchiveService.py` hands it). `--force` reprocesses even where a run already exists under the current config, replacing it.

Exit code 0 iff every file finished `ok`/`partial`.

## Non-obvious decisions (read before touching this code)

- **RINGO needs no sanitization, but `pyRinex.ReadRinex` still runs for provenance.** RINGO handles
  raw `.Z`/`.gz`/Hatanaka-compressed archive files, both RINEX versions, and the exact header
  quirks `pyRinex.check_header()` was built to fix (blank `TIME SYSTEM`, blank `PRN / # OF OBS`
  fields) with zero pre-processing — confirmed by feeding it the real files that used to crash
  gnssmultipath before any sanitization at all. `prepare.prepare()` still runs `pyRinex.ReadRinex`
  for every file, but purely to harvest `header_fixed`/`rinex_version`/receiver+antenna
  type/interval for `rinex_qc_run`'s provenance columns; `runner.run()` is handed the *original*
  `origin_file` path, not `rinexobj.rinex_path`. The pyRinex fixes themselves
  (`_PrnObsFixer` etc.) stay in `pyRinex.py` regardless — they're still load-bearing for PPP/other
  consumers of `ArchiveService.py`/`ScanArchive.py`'s own sanitization pass, this module just no
  longer depends on them for its own correctness.
- **Multipath needs detrending RINGO doesn't expose per epoch — this is the one real piece of new
  science GeoDE now owns.** RINGO's raw `MP1`/`MP2`/`MP5` (from `rnxcsv --qcmode`) carry a
  slowly-varying residual ionospheric term on top of actual multipath — a textbook property of the
  code-minus-phase combination, not a RINGO quirk. A plain RMS of the raw values came out 5-20x too
  large on real files (confirmed against RINGO's own `qc` text-summary STD on the same satellite:
  raw ~2.5m vs its reported ~0.55m). RINGO detrends internally for that summary but never exposes
  the result per epoch anywhere readable (its `qc --qclog` running-mean output still includes the
  raw jump at each slip before resetting, which is worse, not better, for binning). `runner.py`'s
  `_detrend_by_arc()` fits and removes a **degree-5 polynomial, over a rolling window, per
  continuously-observed run** (not just per slip-bounded arc — see below) before anything
  downstream — including the per-bin az-el aggregation — ever sees an MP value. Chosen over
  simpler alternatives (flat per-arc mean: ~1.0-2x off; linear per-arc detrend: ~25-30% off)
  because it's standard practice for isolating the ionospheric term and landed within ~1-10% of
  RINGO's own numbers on every satellite checked. Runs shorter than `DETREND_DEGREE + 2` epochs are
  dropped entirely — too short to fit reliably.
  **Two real bugs found in this algorithm after it first shipped, both from the same root cause:**
  a slip-bounded "arc" can silently span a huge gap in actual observation (the satellite set and
  rose again hours later, or just dropped out briefly) with no slip ever flagged across it, since
  there's nothing to compare against when there's no data in between. One real file's "arc" spanned
  a 2643-epoch (~22 hour) gap between two unrelated passes; fitting across it (whether as one global
  polynomial or one rolling-window fit) produced ±30-40m residuals at the boundary — a real
  production crash (`psycopg2.errors.NumericValueOutOfRange` writing `rinex_qc_signal_bins.mp_rms_mm`,
  a `smallint[]` column). Fixed by (1) additionally splitting on *any* gap in observed epochs, not
  just slip flags, and (2) switching the fit itself from one global polynomial per arc to a rolling
  window via `scipy.signal.savgol_filter`, since a single high-degree fit over a long-but-genuinely-
  continuous run is independently prone to Runge's-phenomenon-style edge wobble. `writer._quantize_bins()`
  also gained a defense-in-depth guard: a value that would still overflow smallint is stored as
  `NULL` for that one cell, never crashes the whole file's write. See ADR-0005's "Consequences" for
  the full story, including that this wasn't just a crash fix — several satellites' whole-signal
  RMS dropped to match the rest of their file once the hidden gap-inflation was removed.
  This also means `obs_code_orig`/`band_attr` for
  every RINGO-sourced signal is a *band*, not an exact RINEX code (`'MP1'`/`'1?'`, not e.g.
  `'C1C'`/`'1C'`) — RINGO never says which exact range+phase attribute it combined for a band, so
  `mp_partner` is always `NULL` and `snr_band_only` is always `True` for these rows.
- **The two `ringo rnxcsv` calls (`--qcmode` and plain) can disagree on the exact epoch set for the
  same system.** `runner.py` pivots az/el/MP/slip from one call and SNR from the other — confirmed
  on real files from a large `--backfill` run that these two separately-pivoted arrays don't always
  come out the same number of rows (`ValueError: operands could not be broadcast together`,
  `aggregate_signal()`'s `snr != 0` mask against a differently-shaped `snr` array). Fixed by
  computing one shared `time_index` (the union of both calls' times) per system in
  `_build_systems()` and reindexing every array in `_pivot()` against it, so every `BandData`
  array for a system is the same shape by construction, not by the assumption that the two calls
  happened to agree.
- **`n_clock_jumps` is always `NULL` for now.** gnssmultipath reported it as a QC statistic; RINGO
  treats clock jumps as something to *fix* (a separate `clkcorr` command that rewrites the file),
  not something `qc` reports a count of. Dropped deliberately rather than guessed at — Demian's
  call when switching engines. Revisit if clock-jump counting turns out to matter in practice.
- **Grid storage is az-el-only**, per Demian's explicit decision: no separate elevation-ring grid is
  seeded or stored; `rinex_qc_grids` holds only the equal-area az-el grid, write-once/versioned (a
  different grid needs a new `grid_id`, never an edit to an existing row — not yet implemented,
  this is a Phase 2/schema note for whoever builds it).
- **`pool()` operates on the stored representation** (`n_obs`/`mp_rms`/`snr_mean` arrays, `NaN` =
  empty cell), reconstructing sum-of-squares internally from `rms² × n`, not on some separate
  in-memory accumulator shape — this is what weekly/yearly stacking will actually call with two
  already-persisted rows' bins.
- **`com/UpdateEarthquakes.py`** was rewritten off `libcomcat` onto direct USGS ComCat REST calls as
  part of unblocking this module's original `gnssmultipath` dependency (numpy version conflict
  chain) — see ADR-0002. Unrelated to RinexQC's own logic (and gnssmultipath itself is gone now,
  ADR-0005), but touched in the same earlier effort.
- **History: gnssmultipath was the original engine (ADR-0003), patched three times for real bugs
  on real multi-GNSS files (ADR-0004), then replaced (ADR-0005)** once it became clear the bug
  count wasn't converging and the files GeoDE needs to process at scale (22M+) kept finding new
  ones. All of ADR-0003/0004's content is now historical, not current architecture — read
  ADR-0005 for what's true today.

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
  function**, not at `ArchiveService.py`'s top level, so `--no_rinex_qc` (or an install that
  hasn't run `scripts/install_ringo.sh` yet) never pulls it into ArchiveService's import graph at
  all.
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

## Phase 4 (visualization, done)

`geode/rinex_qc/visualization.py` + CLI entry point `com/PlotRinexQC.py` — three plot types, one
figure per GNSS constellation (not one figure faceted by system): a daily-summary time series
(completeness, MP RMS, SNR mean, slip rate), and two heatmaps built by collapsing
`rinex_qc_signal_bins`' az-el grid along one spatial axis (`collapse_to_elev_rings` /
`collapse_to_azimuth`) — MP/SNR vs. elevation-over-time and vs. azimuth-over-time, each with an
optional range filter on the *other* spatial axis (`az_range` on the elevation plot, `elev_range`
on the azimuth plot). Equipment changes (receiver/antenna/radome/firmware, from `stationinfo` —
extends `geode/etm/core/jump_manager.py`'s antenna/radome-only convention to also flag receiver
and firmware changes, since those affect SNR/tracking without moving the antenna) and site visits
(`api_visits`, joined via `stations.api_id`) are overlaid as vertical lines on every plot. Same
output contract as `geode.etm`'s plotter (`QcPlotOutput.filename` → file, `.file_io` → base64
string). See
[checkpoints/2026-11-18-rinex-qc-visualization.md](../checkpoints/2026-11-18-rinex-qc-visualization.md)
for the full design rationale (color palette choice, the completeness zero-fill correctness fork,
fixed color-scale bounds derived from real data) and known limitations (no time-axis downsampling
yet — long spans render with real moiré; `snr_unit == 'receiver'` stations unhandled).

## What's not built yet

- Validation against the project's actual documented fixtures (LHCL 1996-251, SANT 2025-212, AGGO
  2025-212) with their exact reference MP RMS/epoch-count values — everything validated so far
  used real-but-different archive data for mechanical correctness, not the documented numbers.
  (Clock-jump values no longer apply either way — see "Non-obvious decisions".)
- Time-axis downsampling for the Phase 4 heatmaps on multi-year spans (see above).
- A fourth, narrower gnssmultipath-era bug (per-epoch satellite handling when orbit coverage is
  partial) was found and left unfixed before the switch to RINGO — now moot, kept only as a
  historical note in `knowledge/known-issues.md`.
- Engine history: [ADR-0003](../decisions/0003-gnssmultipath-dependency.md) (adopted gnssmultipath),
  [ADR-0004](../decisions/0004-patch-gnssmultipath-multisystem-bugs.md) (patched three of its real
  bugs), [ADR-0005](../decisions/0005-switch-to-ringo.md) (replaced it with RINGO — current state).
  The Python 3.11 migration ADR-0003 required is [ADR-0002](../decisions/0002-python-311-migration.md)
  (still relevant — RINGO has no Python version dependency itself, but the rest of GeoDE does).
  Session logs: [2026-11-16-python-311-migration.md](../checkpoints/2026-11-16-python-311-migration.md),
  [2026-11-17-rinex-qc-schema.md](../checkpoints/2026-11-17-rinex-qc-schema.md),
  [2026-11-17-rinex-qc-phase3.md](../checkpoints/2026-11-17-rinex-qc-phase3.md),
  [2026-11-17-gnssmultipath-multisystem-bugs.md](../checkpoints/2026-11-17-gnssmultipath-multisystem-bugs.md),
  [2026-11-17-switch-to-ringo.md](../checkpoints/2026-11-17-switch-to-ringo.md),
  [2026-11-18-rinex-qc-visualization.md](../checkpoints/2026-11-18-rinex-qc-visualization.md).
