# RINEX QC module: Phase 1 (standalone, no DB)

- **Date:** 2026-11-16
- **Branch:** master

## Summary

Built Phase 1 of the RINEX QC module from the design doc ("GeoDE RINEX QC Module Implementation
Instructions"), after the Python 3.11 migration and `gnssmultipath` dependency work earlier the
same session (see `2026-11-16-python-311-migration.md`). Phase 1: a new `geode/rinex_qc/` package
+ `com/RinexQC.py` CLI that runs `gnssmultipath` on one or more RINEX files and writes
`<file>_qc.json`/`<file>_qc_bins.csv`. No database involved yet. See
`knowledge/architecture/rinex-qc.md` for the lasting architecture description — this checkpoint is
the session log of what was built, tested, and found along the way.

## Files / modules affected

- New package `geode/rinex_qc/`: `config.py`, `prepare.py`, `orbits.py`, `signals.py`, `runner.py`,
  `aggregate.py`, `writer.py`, `pipeline.py`.
- New `com/RinexQC.py` CLI.
- New tests: `geode/tests/test_rinex_qc_config.py`, `test_rinex_qc_signals.py`,
  `test_rinex_qc_orbits.py`, `test_rinex_qc_aggregate.py`.
- `knowledge/architecture/rinex-qc.md` (new), `overview.md` and `cli-reference-map.md` updated,
  `docs/com.rst` updated (new `com.RinexQC` entry, alphabetically between `ReadPackage`/`S-score`).

## Architectural decisions

See `knowledge/architecture/rinex-qc.md`'s "Non-obvious decisions" section for the lasting ones
(no CSV parsing, slip-by-elevation-band already computed by the tool, `slip_distribution_LLI_fusion`
chosen as the primary slip metric, az-el-only grid, `pool()`'s exact shape). Not repeated here.

## Tests executed and results

- `geode/tests/` full suite: 208 passed, 0 failed, 127 skipped (unrelated, vector-dir-gated).
- Real end-to-end pipeline runs (prepare → orbits → runner → signals → aggregate → writer → CLI),
  against **real archive data this machine happened to have** (LHCL and AGGO station, day 005 of
  2026, matched against a real local `brdc0050.26n` broadcast nav file) -- **not** the project's
  documented validation fixtures (LHCL 1996-251, SANT 2025-212, AGGO 2025-212), which aren't
  available in this environment and still need Demian to supply for an exact reference-value check:
  - LHCL: ran in ~6s (doc's estimated range is 1.4-4.8s/file; this file is a 2-hour session, not a
    full day, so not a contradiction), `status=ok`, 2 signals (C1/1C, P2/2W — P1 didn't get a
    usable combination, which is `gnssmultipath`'s own decision, not investigated further), MP RMS
    ~0.30m, SNR ~37-43 dB-Hz, slip counts sane.
  - AGGO: `status=partial`, correctly flagged `GLONASS` in `systems_without_orbits` (only a GPS-only
    legacy `brdc0050.26n` was available locally, no `.26g`/BRDM file) and `flags=['systems_without_orbits']`.
    3 GPS signals, MP RMS ~0.27-0.34m.
  - Noted (not a bug): a noticeable fraction (~13%) of valid per-epoch MP values are exactly `0.0`
    for LHCL's C1 signal. Traced to `gnssmultipath`'s own "zero-mean per arc" normalization
    producing exact zeros for very short arcs -- this is the tool's own data, not an aggregation
    bug in `geode/rinex_qc/aggregate.py`; not investigated further than confirming the overall RMS
    (0.302m) is sane.
- `aggregate.pool()` identity verified twice: once against real LHCL per-epoch data (split in half,
  aggregate each half, pool, compare to whole-file aggregation -- exact match), once against
  synthetic random data in `test_rinex_qc_aggregate.py`.
- `pyRinex.ReadRinex`'s `check_header()` TIME SYSTEM fix (from the earlier Python-311-migration
  session work, but directly load-bearing here) re-verified against a real decompressed RINEX file
  with the field manually blanked.

## Known issues / limitations

- No fixture-exact validation yet (see above) -- the doc's reference MP RMS/epoch-count/clock-jump
  values for LHCL 1996-251/SANT 2025-212/AGGO 2025-212 have not been checked against this
  implementation because those files aren't available here.
- `gnssmultipath` still prints progress bars and some `INFO`/warning lines to stdout/stderr
  regardless of the flags passed (`includeResultSummary=False` etc. only suppress its own output
  *files*, not console logging) -- noisy but harmless; not silenced, since that's the library's
  own logging configuration, out of scope to patch here.
- `pipeline.py`'s `_sys_code()` hardcodes the same 4-system (`G`/`R`/`E`/`C`) name↔code mapping
  `gnssmultipath` itself uses internally (`_CODE2NAME`, a private module attribute we deliberately
  don't import) -- if `gnssmultipath` ever adds QZSS/IRNSS/SBAS support, this mapping (and
  `signals.SYSTEMS`) needs a matching update.
- No handling yet for `desiredGNSSsystems` systems that `gnssmultipath` itself can't find in the
  observation file at all (as opposed to lacking orbit coverage) -- untested path.

## Next steps

- Phase 2: schema migration (`qc_grid`, `rinex_qc_run`, `rinex_qc_signal`, `rinex_qc_signal_bins`,
  views, the defensive `rinex.api_id` backfill) in `geode/dbConnection.py`.
- Phase 3: DB writer (replacing `writer.py`'s JSON/CSV with a `dbConnection`-based one),
  `--incremental`/`--backfill` CLI modes, the `ArchiveService.py` QC hook.
- Get the project's actual validation fixtures from Demian and run the doc's documented reference-
  value checks.
- Decide whether to silence `gnssmultipath`'s console output (tqdm progress bars / INFO prints) if
  it turns out to be a problem for batch/parallel runs (likely noisy under `pyJobServer` with many
  concurrent workers) -- flagged, not yet a confirmed problem.
