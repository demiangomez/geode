# Replace gnssmultipath with RINGO as RinexQC's computation engine

- **Date:** 2026-11-17
- **Branch:** master

## Summary

After the gnssmultipath patch round (`2026-11-17-gnssmultipath-multisystem-bugs.md`) still left one
real backfill failure tracing into a fourth and fifth bug in the same family, Demian called it:
*"this code has proven to be very unreliable... these crashes do not seem encouraging"* — and asked
for an evaluation of switching to RINGO (GSI, Japan), including a robustness check against real
archive files and an installation plan. Full rationale, alternatives, and consequences in
[decisions/0005-switch-to-ringo.md](../decisions/0005-switch-to-ringo.md); summary of what actually
happened in this session:

1. **Research + robustness evaluation.** Downloaded RINGO v0.9.6 directly from GSI, ran it against
   all 10 real `usa.gisa` files that broke gnssmultipath plus a genuinely random 25-file sample
   across 17 networks spanning 1995-2026 — 35/35 clean, including direct `.Z`/`.gz`/Hatanaka
   archive files with zero pre-processing. Reported this plus the installation plan and effort
   estimate to Demian before writing any code.
2. **Demian's calls, taken as given rather than re-litigated:** drop clock-jump tracking for now;
   keep `pyRinex.ReadRinex` in the loop for provenance even though RINGO doesn't need it for
   sanitization; remove gnssmultipath entirely rather than keep it as a second source.
3. **Built `scripts/install_ringo.sh`** — platform/arch detection, download, unpack, chmod, PATH
   check.
4. **Rewrote `geode/rinex_qc/runner.py`** around two `ringo rnxcsv` subprocess calls (`--qcmode` for
   per-epoch az/el/MP/slip-flags, plain for raw SNR), parsed with pandas into per-system/per-band
   arrays.
5. **Found and fixed a real correctness bug mid-implementation, not a wiring bug:** RINGO's raw
   per-epoch MP values carry a residual ionospheric trend that inflated RMS 5-20x versus RINGO's
   own reported per-satellite STD. Tried three detrending approaches empirically (flat per-arc
   mean, per-arc mean with a minimum-length cutoff, linear per-arc detrend) before stopping to ask
   Demian how precise this needed to be, rather than silently picking one and shipping a
   subtly-wrong statistic. Demian's answer: degree-5 per-arc polynomial (standard for isolating
   ionospheric effects) — implemented as `runner._detrend_by_arc()`, landed within ~1-10% of
   RINGO's own numbers (vs 15-100%+ for the earlier attempts).
6. **Rewrote `aggregate.aggregate_signal()`** for the new `BandData` shape, added `_slips_by_elev()`
   (RINGO gives per-epoch slip flags, not a pre-binned distribution like gnssmultipath did).
7. **Simplified `signals.py`** — the old RINEX2→RINEX3 `(gnss, obs_code) → band_attr` mapping table
   is gone (nothing to map to any more; RINGO reports per band, not per exact code); only
   `find_snr_code()` survives, simplified to take a bare band digit.
8. **Rewrote `pipeline.py`**'s signal-building loop for the new per-system/per-band iteration,
   dropped `n_clock_jumps` population, changed `TOOL_NAME`/`TOOL_VERSION` to `'ringo'`/`'0.9.6'`,
   changed `runner.run()`'s input from `rinexobj.rinex_path` to the original `origin_file`.
9. **Removed gnssmultipath entirely**: dropped the pin from `pyproject.toml`/`environment.yml`,
   deleted `scripts/patch_gnssmultipath.py`, updated every stale comment in `dbConnection.py`/
   `aggregate.py`/`orbits.py`/`ArchiveService.py` that described gnssmultipath-specific behavior
   that's no longer true, marked ADR-0003/0004 superseded.

## Tests executed and results

- New/updated unit tests, no real binary needed: `test_rinex_qc_runner.py` (new — CSV
  header-repetition parsing, `_pivot`'s `dropna=False` fix, `_detrend_by_arc`'s polynomial removal/
  arc-reset/min-length/NaN-propagation behavior, `_run_rnxcsv`'s error paths via monkeypatched
  `subprocess.run`), `test_rinex_qc_aggregate.py` (added `aggregate_signal`/`_slips_by_elev`
  coverage — none existed before, old or new), `test_rinex_qc_signals.py` (rewritten for the
  simplified `find_snr_code(codes, band)` signature), `test_rinex_qc_config.py` (added `ringo_bin`
  default + confirmed excluded from `config_hash`).
- Full `geode/tests/` suite: 225 passed, 0 failed, 127 skipped (unrelated).
- **Real end-to-end verification**, same discipline as every fix this session: `runner.run()`
  against two real `usa.gisa` files (DOY 290, 292/2026) with the real BRDM merged nav product —
  sane per-system/per-band MP RMS (0.45-0.64m for GPS/Galileo, 0.64-1.58m for GLONASS — GLONASS P2
  code noise is a known, real effect, not a bug), slip counts matching RINGO's own text summary
  exactly. Full `pipeline.process_one_file()` run end-to-end (`status='ok'`, correct
  `header_fixed`/`systems_processed`/`rinex_version` provenance, 7 signal rows). Full standalone
  CLI (`com/RinexQC.py ... -keep`) produced correct `*_qc.json`/`*_qc_bins.csv`/
  `*_ringo_qcmode.csv`/`*_ringo_obs.csv`. Full DB write path against a disposable, from-scratch
  Postgres 16 instance (base `gnss_data_dump.sql` + migrations + a real `rinex` row, via the same
  `psycopg2.connect` port-patch technique used for the Phase 3 verification) — confirmed
  `rinex_qc_run.tool='ringo'`, `n_clock_jumps=NULL`, `rinex_qc_signal.band_attr='<band>?'`,
  `mp_partner=NULL`, correct `mp_rms_m`/`n_slips` values, 7 bin rows written.

## Known issues / limitations

- Not yet validated against the project's documented reference fixtures (LHCL 1996-251, SANT
  2025-212, AGGO 2025-212) with their exact reference MP RMS/epoch-count values — same gap that
  existed before this switch, now doubly relevant since the detrending algorithm changed entirely.
- The degree-5 polynomial detrend's accuracy (vs RINGO's own internal, undocumented algorithm) was
  checked on one station-day (`usa.gisa` DOY 292/2026) across ~7 satellites per band. Worth
  spot-checking against a few more real files before treating the ~1-10% agreement as universally
  representative, though the underlying method (isolate the ionospheric term per arc) is standard
  practice, not something tuned to match this one file.
- Anubis (G-Nut/Anubis) surfaced during research as a tool in the same space but was not evaluated
  — no robustness testing, no installation assessment. Worth a look if RINGO itself turns up
  problems at scale later.
- `scripts/install_ringo.sh` has only been exercised on Linux x86_64 in this session; the
  Windows/macOS/ARM/FreeBSD/Solaris branches are written from GSI's documented download URLs but
  not run.

## Next steps

- Demian re-runs the full `--backfill -force` (no `-limit`) against `usa.gisa`'s whole archive
  (and ideally a broader station sample) to confirm this holds at scale, now with the real
  `scripts/install_ringo.sh`-installed binary rather than the scratchpad copy used for development.
- Validate against the documented LHCL/SANT/AGGO fixtures once available, to get a real accuracy
  number for the degree-5 detrend against known-good reference values, not just internal
  consistency with RINGO's own text summary.
- Once `usa.gisa` (and ideally a few other stations) backfill cleanly at scale, move on to the
  dynamic visualization backend module for the web frontend — the task this was all blocking,
  per Demian's original sequencing from earlier in the RinexQC effort.
