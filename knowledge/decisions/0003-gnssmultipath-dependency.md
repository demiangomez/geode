# ADR-0003: Adopt `gnssmultipath` as the RINEX QC computation engine

- **Status:** accepted
- **Date:** 2026-11-16
- **Branch (if not master):** —

## Context

The RINEX QC module (per-signal/per-bin multipath, SNR and cycle-slip statistics, stored with full
provenance — see the design doc this was built from) needs a computation engine. `gnssmultipath`
was the candidate selected in the design doc, over RINGO (closed binary, coarser per-arc MP
combinations) and GFZRNX-QC (no public release). This ADR is the formal dependency-evaluation
record required by `knowledge/workflows.md` before adding any new package.

Adding it required [ADR-0002](0002-python-311-migration.md) first: `gnssmultipath` declares
`numpy>=2.1.0` (every release checked, back to 1.6.1, requires it — not a version-specific quirk),
which conflicted with GeoDE's then-`numpy==1.26.4` pin. That migration is a separate decision
(Python ≥3.11) and isn't re-explained here.

## Decision

Add `gnssmultipath==2.2.0` (exact pin — its own output records which version produced a run, so
drift here would silently change what "the same config" means) to `pyproject.toml` and
`environment.yml`. Installed cleanly in the Python-3.11 `geode` env: `pip check` reports no broken
requirements.

## Rationale

- Per-epoch output (MP, SNR, azimuth/elevation per tracked code) rather than per-arc summary
  statistics — this is the reason RINGO was rejected in the design doc (RINGO's STD is taken about
  a 50-epoch running mean, not the arc mean, and disagreed with gnssmultipath by tens of
  centimeters on the same data).
- Pure Python, pip-installable, no binary to manage or license.
- **Read its actual source** (not just its docs) to resolve the design doc's open "facts to
  establish" about its return value, since the doc explicitly flagged this as unknown:
  - `GNSS_MultipathAnalysis()` returns one dict (`analysisResults`). Per-system
    (`analysisResults[sys_name]`, e.g. `'GPS'`) → per-band (`'Band_1'`, ...) → per-code
    (`range1_Code`, e.g. `'C1'`) holds a flattened `SignalStats` dataclass
    (`geode`-relevant fields, with the library's own docstrings explaining each —
    `site-packages/gnssmultipath/signalAnalysis.py`): per-epoch arrays
    (`multipath_range1`, `range1_observations`, `phase1_observations`, `ion_delay_phase1`,
    `sat_elevation_angles`, shape `(nepochs, max_sat+1)`), slip **counts** per satellite
    (`range1_slip_distribution_per_sat`, `cycle_slip_distribution_per_sat`,
    `LLI_slip_distribution_per_sat`, `slip_distribution_per_sat_LLI_fusion` — four slightly
    different slip-detection methods, not duplicates), and slip **periods** per satellite
    (`range1_slip_periods`/`cycle_slip_periods`, dict keyed by 1-based PRN of `(n, 2)`
    `[start_epoch, end_epoch]` arrays).
  - **Azimuth is available for broadcast-nav runs too**, not just SP3 — a real risk the design doc
    didn't resolve. It's `analysisResults['Sat_position'][sys_code]['azimuth']`
    (unconditionally populated from `computeSatElevAzimuth_fromNav()`'s own return value,
    regardless of `use_sp3`; a separate, SP3-only local variable named `sat_azimut_angles` had
    looked at first read like azimuth was SP3-only, but that variable is unrelated to what ends up
    in the returned dict).
  - `n_clock_jumps` is `analysisResults['ExtraOutputInfo']['nClockJumps']` (also
    `meanClockJumpInterval`/`stdClockJumpInterval` alongside it).
  - **There is no pre-binned "slips by elevation band" field.** The design doc's schema column
    (`rinex_qc_signal.slips_by_elev int[]`, documented as "as reported by the tool") is not
    literally reported by the tool as a single value — `aggregate.py` has to compute it itself,
    by cross-referencing each per-PRN slip period's start epoch against that satellite's
    `sat_elevation_angles` at that epoch and bucketing into bands. Noted so a future session
    doesn't go looking for a field that isn't there.
  - **GeoDE does not need to parse `Result_files_CSV/<SYSTEM>_results.csv` at all.** Every value
    the design doc planned to read from that CSV (per-epoch MP, SNR, azimuth, elevation per code)
    is already present in-process in the returned `analysisResults` dict. `runner.py`/
    `aggregate.py` should consume the dict directly; CSV output is gnssmultipath's own
    side-artifact (`write_results_to_csv`), not something GeoDE needs to round-trip through.
- Confirmed via its actual dependency metadata that `matplotlib` is only imported inside its
  `plot/` submodule files — plotting is disabled anyway in GeoDE's planned call
  (`plotEstimates=False, plot_polarplot=False`).

## Consequences

- `runner.py` is simpler than the design doc assumed: no CSV parsing/PRN-padding-from-CSV step
  (`G1`→`G01` normalization still needed, but from `GNSSsystems`/satellite-index metadata in the
  returned dict, not from re-parsing a CSV column).
- `aggregate.py` owns deriving "slips by elevation band" from slip periods + elevation arrays —
  this is real GeoDE logic, not a passthrough, and needs its own test coverage.
- `config_hash`/provenance must record the exact `gnssmultipath==2.2.0` pin, since both the
  per-epoch computation and the `res` dict shape are tied to this version; a future upgrade gets a
  new `tool_version` and a new run, never overwrites history (per the design doc's own rule).

## Alternatives considered

- RINGO — rejected in the design doc (per-arc summary statistics disagree with per-epoch
  computation by tens of cm on the same data; closed binary).
- GFZRNX-QC — rejected in the design doc (no public release, undocumented format, commercial
  license).
- Not adding a dependency at all / hand-rolling MP computation — rejected; re-deriving MP/cycle-slip
  detection correctly (receiver clock jumps, ambiguity resolution, LLI fusion) is exactly the kind
  of numerically delicate work a maintained, per-epoch library already does correctly.
