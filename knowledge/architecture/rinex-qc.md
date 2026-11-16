# RINEX QC (`geode/rinex_qc/`, `com/RinexQC.py`)

Per-signal/per-bin RINEX quality-control statistics (code multipath, SNR, cycle slips), computed
by the `gnssmultipath` library, with full provenance. Built from a design doc ("GeoDE RINEX QC
Module Implementation Instructions"); several things changed from that doc's first draft once the
actual codebase and `gnssmultipath`'s real behavior were checked — see the ADRs below rather than
trusting the original doc's assumptions.

**Status as of 2026-11-16: Phase 1 complete and validated against real archive data (LHCL/AGGO
day-005 2026, not the project's documented validation fixtures — those still need to be supplied
and run for exact reference-value checks). Phase 2 (schema) and Phase 3 (DB writer, backfill/
incremental CLI modes, `ArchiveService.py` hook) are not yet built.** Check `git log`/`git branch`
before assuming otherwise.

## Package layout

| Module | Responsibility |
|---|---|
| `config.py` | `RinexQCConfig` — JSON + CLI-switch config, frozen dataclass. `config_hash` covers only science-affecting fields (`elev_cutoff_deg`, `systems`, `orbit_source`, `default_grid_id`, `snr_unit_policy`) — run-specific fields (`nav_paths`/`sp3_paths`/`keep_tool_outputs`/`work_dir`) are excluded on purpose. |
| `prepare.py` | Thin wrapper around `pyRinex.ReadRinex` — **no decompression/header logic lives here**, it's all reused from `pyRinex.py` (see ADR-0002's pyRinex fix). |
| `orbits.py` | Resolves broadcast (`GetBrdmOrbits` → `GetBrdcOrbits`+`GetBrdgOrbits` fallback) or SP3 (`GetSp3Orbits`) orbits via the existing `pyProducts` classes; `guard_same_day()` rejects a nav/sp3 file whose own filename encodes a different day than the observation. |
| `signals.py` | RINEX2→RINEX3 `(gnss, band_attr)` mapping table + `find_snr_code()` (RINEX2 SNR is band-only; RINEX3 tries an exact attribute match first, falls back to band-only). |
| `runner.py` | The only module that imports `gnssmultipath`. In-process call to `GNSS_MultipathAnalysis`; `write_results_to_csv=False` — **the returned dict already has everything needed**, see below. |
| `aggregate.py` | `make_equal_area_grid()` (deterministic, 5° elevation rings × cos-weighted azimuth sectors), `bin_indices()` (vectorized), `aggregate_signal()` (per-signal + per-bin stats from one gnssmultipath result), `pool()` (exact n-weighted combination of stored bins — the basis for weekly/yearly stacking). |
| `writer.py` | Phase 1 only: `<file>_qc.json` + `<file>_qc_bins.csv`. `TODO(db)`: Phase 3 replaces this with a `dbConnection`-based writer; the JSON layout already mirrors the planned DB tables 1:1. |
| `pipeline.py` | `process_one_file()` — the one place that chains prepare→orbits→runner→signals→aggregate and decides `ok`/`partial`/`failed`. Both `com/RinexQC.py` and the future `ArchiveService.py` hook call this, not each other. |

`com/RinexQC.py` is the CLI: `rinex_file [rinex_file ...] [--config] [--elev-cutoff] [--systems] [--nav] [--sp3] [--out-dir] [--keep-tool-outputs] [--dry-run]`. Exit code 0 iff every file finished `ok`/`partial`.

## Non-obvious decisions (read before touching this code)

- **Sanitization is `pyRinex.ReadRinex`, not a new sanitizer.** It already does temp-copy,
  decompression, header normalization, RINEX 2/3 conversion, interval checking — the same pipeline
  `ArchiveService.py`/`ScanArchive.py` run before PPP. The one real gap (`gnssmultipath` crashing on
  a blank `TIME SYSTEM` field for single-GNSS RINEX 2 files) was fixed directly in
  `pyRinex.check_header()`, not duplicated here. See [ADR-0002](../decisions/0002-python-311-migration.md).
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
  seeded or stored; `qc_grid` holds only the equal-area az-el grid, write-once/versioned (a
  different grid needs a new `grid_id`, never an edit to an existing row — not yet implemented,
  this is a Phase 2/schema note for whoever builds it).
- **`pool()` operates on the stored representation** (`n_obs`/`mp_rms`/`snr_mean` arrays, `NaN` =
  empty cell), reconstructing sum-of-squares internally from `rms² × n`, not on some separate
  in-memory accumulator shape — this is what weekly/yearly stacking will actually call with two
  already-persisted rows' bins.
- **`com/UpdateEarthquakes.py`** was rewritten off `libcomcat` onto direct USGS ComCat REST calls as
  part of unblocking this module's `gnssmultipath` dependency (numpy version conflict chain) — see
  ADR-0002. Unrelated to RinexQC's own logic, but touched in the same effort.

## What's not built yet

- Schema migration (`qc_grid`, `rinex_qc_run`, `rinex_qc_signal`, `rinex_qc_signal_bins`, views
  `rinex_qc_bins_long`/`rinex_qc_latest`) in `geode/dbConnection.py`'s `_run_db_migrations()`,
  including the defensive `rinex.api_id` existence-check-and-add (Demian wants this because
  GeoDE Studio's Django app, not this repo, is what normally creates that column, and it may not
  exist on every install).
- `writer.py`'s DB writer (replacing the Phase 1 JSON/CSV `TODO(db)`).
- `com/RinexQC.py --incremental`/`--backfill` modes, modeled on `com/ScanArchive.py`'s
  `process_ppp()` (`rinex` LEFT JOIN `rinex_qc_run` WHERE NULL, dispatched via `pyJobServer`).
- `ArchiveService.py`'s QC hook: a decoupled end-of-run pass (own `JobServer` cluster, like
  `process_otl`/`process_visits`), not inline in the same per-file job as PPP.
- Validation against the project's actual documented fixtures (LHCL 1996-251, SANT 2025-212, AGGO
  2025-212) with their exact reference MP RMS/epoch-count/clock-jump values — everything validated
  so far used real-but-different archive data (LHCL/AGGO day 005, 2026) for mechanical correctness,
  not the documented numbers.
- `gnssmultipath` dependency acceptance is recorded in [ADR-0003](../decisions/0003-gnssmultipath-dependency.md);
  the Python 3.11 migration it required is [ADR-0002](../decisions/0002-python-311-migration.md).
  Session logs: [2026-11-16-python-311-migration.md](../checkpoints/2026-11-16-python-311-migration.md).
