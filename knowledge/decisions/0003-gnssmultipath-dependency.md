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
  - **Correction after running it against real data (LHCL day 005, 2026) — trust this over the
    static source-reading above.** `range1_slip_distribution`, `cycle_slip_distribution`,
    `LLI_slip_distribution` and `slip_distribution_LLI_fusion` (and their `_per_sat` variants) are
    **not** plain `int`/`np.ndarray` as the `SignalStats` dataclass's own type annotations and
    docstring claim — at runtime each is actually a `dict` with keys `n_slips_0_10, n_slips_10_20,
    n_slips_20_30, n_slips_30_40, n_slips_40_50, n_slips_over50, n_slips_NaN, n_slips_Tot`. So the
    design doc's `rinex_qc_signal.slips_by_elev int[]` column **is** directly reported by the tool
    after all — `aggregate.py` just reads the six band keys (dropping `n_slips_NaN`/`n_slips_Tot`,
    the latter being redundant with the scalar `n_slips`) into an ordered array, no cross-referencing
    of slip periods against elevation arrays needed. `slip_distribution_LLI_fusion` (the
    deduplicated fusion of the algorithmic and LLI-reported methods) is what `aggregate.py` uses
    for the primary `n_slips`/`slips_by_elev` columns, over the other three raw methods — it's the
    most complete single signal gnssmultipath computes. This is a good general lesson for future
    gnssmultipath-version bumps: verify its dataclass docstrings against an actual run before
    trusting them, they can drift from the real return shape.
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
