# RinexQC visualization module

- **Date:** 2026-11-18
- **Branch:** master

## Summary

Built `geode/rinex_qc/visualization.py` + CLI entry point `com/PlotRinexQC.py`: the "dynamic
visualization" backend module that `knowledge/architecture/rinex-qc.md` had listed as planned-next
with no code. Three plot types per station, one figure per GNSS constellation: a daily-summary
time series (completeness, MP RMS, SNR mean, slip rate), and two heatmaps (MP/SNR vs elevation-
over-time, MP/SNR vs azimuth-over-time) built by collapsing `rinex_qc_signal_bins`' az-el grid
along one spatial axis. Equipment changes (receiver/antenna/radome/firmware, from `stationinfo`)
and site visits (`api_visits`) are overlaid as vertical lines on every plot. Design was worked out
interactively against real data for `usa.gisa` (the only station with RinexQC data in this test
DB) before any production code was written — see the session transcript for the exploration; this
checkpoint covers the resulting implementation only.

## Files / modules affected

- `geode/rinex_qc/visualization.py` (new) — query layer (`get_*` functions, all parameterized SQL,
  no raw string interpolation of user input), the az-el grid collapse layer
  (`collapse_to_elev_rings`/`collapse_to_azimuth`, `_overlap_weight_matrix`/`_az_weight_matrices`),
  and the three `plot_*()` functions + `plot_station()` convenience wrapper.
- `com/PlotRinexQC.py` (new) — CLI, mirrors `com/RinexQC.py`/`com/PlotETM.py` conventions
  (`Utils.process_stnlist`, `station_list_help()`, `add_version_argument`).
- `geode/tests/test_rinex_qc_visualization.py` (new) — 9 tests for the pure-numeric collapse layer
  (no DB required); the query layer itself has no automated coverage, consistent with the rest of
  `geode/rinex_qc`'s DB-touching code (see "Testing & CI" in `architecture/overview.md`).

## Architectural decisions

- **One figure per constellation, not one figure with GNSS systems as facet columns.** User
  request, for more granular delivery (a backend consumer can request just one system). Within one
  constellation's figure, bands (max 3: e.g. GPS 1?/2?/5?) use a fixed-order 3-color categorical
  palette (blue/orange/aqua, hex from the `dataviz` skill's validated reference palette —
  specifically the only 3 slots that clear its all-pairs CVD/contrast floors together, which
  happens to be exactly the max band count any one constellation has).
- **Completeness zero-fill distinguishes "no RINEX file" from "RINEX file exists, QC not run
  yet."** Only a true missing-file day renders as 0%; a day with an archived file but no
  `rinex_qc_run` row (e.g. mid-backfill) is left as a gap (NaN), not a false outage. Confirmed with
  the user before implementing — this was a real correctness fork, not a style choice. Applies to
  every daily series (MP RMS, SNR, slip rate too), reindexed onto the full calendar day range so a
  multi-month gap renders as a break in the line rather than matplotlib silently straight-line
  interpolating across it.
- **Elevation-vs-time and azimuth-vs-time both support an optional range filter on the *other*
  spatial axis** (`az_range` on the elevation plot, `elev_range` on the azimuth plot) — whole-
  sector/whole-ring inclusion, not fractional/area-weighted, and no azimuth-wraparound support
  (e.g. 350-10°) yet. A further elevation-banded azimuth split (0-20°/20-50°/>50° as three separate
  panels) was discussed and deliberately deferred — `collapse_to_azimuth`'s `elev_range` parameter
  already supports it as a repeated call, so it's a CLI/wrapper addition later, not a rewrite.
- **Equipment-change detection extends ETM's own convention.** `geode/etm/core/jump_manager.py`
  only flags antenna/radome changes (geometric jumps, what matters for a position time series).
  This module also flags receiver and *receiver firmware* changes (`stationinfo.ReceiverFirmware`)
  since those can shift SNR/tracking behavior without an antenna move — found by inspection
  against real `usa.gisa` data (3 real firmware bumps, 4.22→5.22→5.37→5.48, that the antenna/radome-
  only check was silently missing).
- **Fixed physical color-scale bounds** (MP RMS 0-1000mm, SNR 25-52 dB-Hz), not auto-scaled per
  plot, chosen from real `usa.gisa` statistics (median MP RMS ~480mm post-detrend, SNR 36-50 dB-Hz)
  rather than guessed — an initial guess of 0-10mm was off by two orders of magnitude and would
  have silently produced a useless all-saturated chart. Fixed bounds only apply when
  `rinex_qc_signal.snr_unit == 'dBHz'`; not handled for `'receiver'`-unit stations yet (none
  encountered in this session, so this is unverified, not just undocumented).
- **No time-axis downsampling.** A full 15-year `usa.gisa` heatmap renders ~5200 daily columns into
  a fixed-width figure, which produces real moiré/aliasing banding (confirmed against both the
  elevation and azimuth heatmaps). Noted as a known limitation rather than fixed — the natural fix
  (weekly/monthly pooling via the time axis, reusing `aggregate.pool()`'s n-weighted combination
  logic) wasn't requested or implemented this session.

## Tests executed and results

- `pytest geode/tests/test_rinex_qc_visualization.py` — 9/9 passed (grid-collapse weight-matrix
  correctness, uniform-bin sanity checks, range-filter-changes-result checks, `_ring_included`
  validation).
- `pytest geode/tests/ -k rinex_qc` — 57/57 passed (new tests + all pre-existing `rinex_qc` tests,
  confirming nothing broke).
- Manual end-to-end smoke test against the real database (`~/pg_osu/gnss_data.cfg`, see
  [reference_db-config-location](reference memory) for `usa.gisa`: all 3 plot types × all 3
  constellations (G/R/E) generated successfully to disk; the base64/`file_io` output path verified
  separately (decodes to a valid PNG). CLI (`com/PlotRinexQC.py`) tested directly, including both
  `--elev-range` and `--az-range` filters, confirmed they visibly change output (not silently
  ignored).
- One real bug caught by this testing, not left in: `collapse_to_elev_rings`/`collapse_to_azimuth`
  initially returned SNR averaged from the raw `snr_ddbhz` column without converting its stored
  0.1 dB-Hz units to actual dB-Hz, producing values ~10x too high that fully saturated every SNR
  heatmap to solid yellow against the 25-52 dB-Hz color bounds. Fixed before this checkpoint.

## Known issues / limitations

- No downsampling for long time spans (see above) — full-history heatmaps have real moiré at
  render width. Worth fixing before this is used routinely for multi-year views.
- `snr_unit == 'receiver'` (non-dBHz) stations would get physically meaningless fixed-bound colors
  on the SNR heatmaps — not handled, not tested (no such station in this session's data).
- Azimuth range filters don't support wraparound (e.g. 350°-10° spanning north) — raises
  `ValueError` instead. Elevation/azimuth range filters use whole-cell inclusion (a sector either
  counts fully or not at all), not fractional/area-weighted inclusion at the range boundary.
- Only `usa.gisa` has any `rinex_qc_run` data in this test database, so the per-constellation
  output was validated on one station only (3 systems: G/R/E; no BeiDou data to check the 4th
  `GNSS_NAMES` entry against). Equipment-change and visit-line rendering were validated separately
  against other stations (`igs.sio2` for equipment history, `ars.btrs` for visits) since `usa.gisa`
  itself has neither real equipment changes beyond firmware nor any visits.
- `plot_station()`'s backend/base64 path returns one PNG per (constellation × plot type) call —
  no batching/parallelism across stations; fine for the current single-station interactive use,
  would want `pyJobServer` dispatch if this is ever called over a large station list at once (the
  plotting itself is cheap relative to QC, but DB round-trips per plot add up).

## Next steps

- Add time-axis downsampling (weekly/monthly pooling, reusing `aggregate.pool()`) for date ranges
  beyond some threshold (a few months), gated on span length.
- Decide on and implement the deferred elevation-banded azimuth split (0-20°/20-50°/>50° as
  separate panels) if/when actually wanted — the collapse function already supports it.
- Handle `snr_unit == 'receiver'` stations in the SNR heatmap (skip fixed bounds, auto-scale and
  label the axis instead) before this is run against a station known to have non-dBHz SNR.
- Validate against a second/third station once more of the archive has been backfilled through
  RinexQC, to catch anything `usa.gisa`'s specific data shape (dBHz throughout, GPS/GLONASS/
  Galileo only, no BeiDou, firmware-only equipment history) happens to not exercise.
