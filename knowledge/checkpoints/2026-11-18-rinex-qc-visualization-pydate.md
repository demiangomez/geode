# RinexQC visualization: pyDate.Date convention + fyear x-axis

- **Date:** 2026-11-18
- **Branch:** master

## Summary

Second follow-up to
[2026-11-18-rinex-qc-visualization.md](2026-11-18-rinex-qc-visualization.md) /
[...-fixes.md](2026-11-18-rinex-qc-visualization-fixes.md). User flagged that the module used
stdlib `datetime.date` throughout instead of `geode.pyDate.Date`, breaking the convention used
everywhere else in GeoDE. Fixing that surfaced a cleaner fix for an already-reported x-axis bug
(the gnarled duplicate-year tick labels on a narrow date range from the gap-fix round): plotting
directly against `Date.fyear` on a plain numeric axis, exactly like `geode.etm`'s time series
plots do, needs no date-locator machinery at all. Also tightened the colorbar gap (overcorrected
in the previous round) and registered the new CLI in `setup.py`.

## Files / modules affected

- `geode/rinex_qc/visualization.py` -- every function touching a date.
- `com/PlotRinexQC.py` -- `--date-start`/`--date-end` now parsed via `geode.Utils.process_date_str`
  (fyear, `yyyy_ddd`, `yyyy/mm/dd`, `gpswk-wkday`), not a custom ISO-only parser.
- `setup.py` -- added `com/PlotRinexQC.py` to the `scripts=[...]` list (was missing entirely).

## Architectural decisions

- **Dates are `pyDate.Date` at every public boundary** (function parameters/returns,
  `get_equipment_changes`/`get_visits`' `date` fields), matching
  `geode.metadata.station_info.StationInfoRecord` (wraps this exact same `stationinfo` table's
  `DateStart`/`DateEnd` the same way) and `geode.rinex_qc.orbits` (takes a `Date` for its
  observation-day parameter). Plain `datetime.date` appears only at two legitimate boundaries that
  don't know about `Date`: a psycopg2 query parameter (`_sql_date()`) and matplotlib's plotting
  calls (via `.fyear`). Saved as a standing memory
  (`~/.claude/projects/-home-demian-geode/memory/feedback_use-pydate-convention.md`) since this is
  a repo-wide convention, not specific to this module.
- **A real subtlety in `Date` worth knowing before using it as a dict key**: `__eq__` compares
  `.mjd` (day-granularity) but `__hash__` is `hash(self.fyear)` (which includes hour/minute/second).
  Two `Date` objects for the same calendar day but constructed with a different hour (e.g. a bare
  date defaulting to noon vs. a `datetime` carrying a real timestamp) would compare equal but hash
  differently -- a real violation of Python's hash/eq contract that could silently break dict
  lookups. Sidestepped by keying the internal day-reindexing dicts (`get_daily_signal_series`,
  both heatmap `build_grids()` closures) by `.mjd` directly rather than the `Date` object itself.
  `_to_pydate()` also deliberately constructs bare dates via `Date(year=, month=, day=)` (defaults
  to noon, matching `Date`'s own `+`/`-` arithmetic) rather than `Date(datetime=midnight)`, so this
  mismatch shouldn't arise in practice -- but the `.mjd`-keying is kept anyway as defense in depth.
- **X-axis switched from `matplotlib.dates.date2num` + `DateFormatter('%Y')` to plotting directly
  against `Date.fyear`** on a plain numeric axis -- no custom locator/formatter at all, confirmed
  against `geode/etm/visualization/time_series_template.py` (`ax.plot(data.time_vector, ...)` where
  `time_vector` is fyear; `ax.set_xlim(time_window)` in fyear). This is both the GeoDE-idiomatic
  way to do it and incidentally fixes the duplicate-year-tick-label bug from the previous round for
  real (the earlier fix only corrected the axis *range*, not the formatter choice that was
  producing the garbled labels in the first place).
- **Colorbar gap tightened**: previous round's fix (computing each `cax` from its column's real
  `get_position()`) was correct but used a gap (~1.2in) that was far more than needed; narrowed to
  ~0.55in (cax x-positions 0.875/0.94, figure width 12.5in -> 12in) -- confirmed by crop-zooming
  the rendered output that unit labels ("[mm]"/"[dB-Hz]") no longer collide, with headroom to
  spare.

## Tests executed and results

- `pytest geode/tests/ -k rinex_qc` -- 57/57 passed after the full refactor (test file itself
  needed no changes: it only exercises the pure-numeric collapse layer, which has no dates).
- Manual verification against real `usa.gisa` data: full default-range smoke test (all 3
  constellations x 3 plot types, file + base64 paths) and a narrow 8-month window
  (`com/PlotRinexQC.py usa.gisa -start 2020/06/01 -end 2021/02/01`, exercising the new
  `process_date_str`-based CLI parsing) both render correctly -- fyear ticks read cleanly at any
  window width, no duplicate labels, colorbars visibly non-overlapping on a crop-zoom.

## Known issues / limitations

None new. Carries forward the same open items as the two earlier checkpoints from today (time-axis
downsampling for multi-year heatmaps, `snr_unit == 'receiver'` handling, elevation-banded azimuth
split, second-station validation).

## Next steps

Same as the earlier checkpoints. Nothing new opened by this round.
