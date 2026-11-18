# RinexQC visualization: heatmap gap-smearing fix + display polish

- **Date:** 2026-11-18
- **Branch:** master

## Summary

Follow-up to the same day's
[2026-11-18-rinex-qc-visualization.md](2026-11-18-rinex-qc-visualization.md) checkpoint, from
user review of the first working output. One real correctness bug found and fixed (the
elevation/azimuth heatmaps were silently stretching the last real day's color across data gaps
instead of rendering them blank), plus display polish (band labels, colorbar layout, title
length, unit convention) requested after seeing the actual rendered plots.

## Files / modules affected

- `geode/rinex_qc/visualization.py` only — no schema/query-shape changes, no new files.

## Architectural decisions

- **The gap-smearing bug and its fix.** `plot_elevation_time`/`plot_azimuth_time`'s `build_grids()`
  closures called `get_daily_bins()`, which only returns rows for days that actually have a
  `rinex_qc_signal_bins` row -- any day with no QC data was simply *absent* from the returned list,
  not NaN-filled. `_heatmap_figure` then built `pcolormesh`'s column edges from
  `np.median(np.diff(dnums))`, i.e. assuming every entry in the (possibly sparse) array represents
  an adjacent day. For a real 120-day gap in `usa.gisa`'s data (2020-08-27 to 2020-12-25,
  confirmed against the live DB), this collapsed the entire gap into a single pcolormesh cell
  carrying the last real day's color, stretched across the full gap width -- a silently misleading
  "solid color" where the data was actually missing. Confirmed the completeness line plot never
  had this bug (it already reindexes onto the full calendar via `get_daily_signal_series`, which
  predates this round).
  Fixed by extracting the existing calendar-reindex pattern into a shared `_full_calendar_days()`
  helper (used by `get_daily_signal_series` too, replacing its own inline copy) and reindexing both
  `build_grids()` closures onto it, filling `np.nan` for any day with no row. `pcolormesh` renders
  NaN cells as blank (the same mechanism already visible as the "L5 before GPS launched enough
  L5-capable satellites" and "before Galileo existed" blank regions), so gaps now render correctly
  with no special-casing needed downstream.
- **A second, related bug surfaced while verifying the first fix with a narrow custom date range**:
  `axvline` for an equipment-change/visit date *outside* the requested `[date_start, date_end]`
  window was expanding the axes' autoscaled x-limits to include it, squeezing the actual requested
  window into a sliver of the plot and producing garbled duplicate-year tick labels. Fixed by
  setting `ax.set_xlim()` explicitly from `date_start`/`date_end` after drawing event lines, in
  both `plot_daily_summary` and `_heatmap_figure` -- the axis range is now authoritative regardless
  of what `axvline` draws outside it.
- **Display polish**, no data-model changes:
  - Band labels (`1?`/`2?`/`5?`) now render as `L1`/`L2`/`L5` everywhere a band is shown (legend,
    heatmap y-axis) via a new `_band_label()` helper. The underlying `?` stays in the stored
    `band_attr` column untouched -- it's a real provenance marker (RINGO can't report the exact
    tracking attribute), just not useful on an axis label.
  - Titles dropped the current-equipment line (receiver/antenna/radome) -- made the header too
    long for no real benefit. A third title line now appears, but only when an elevation/azimuth
    range filter is actually active, naming it explicitly (e.g. "azimuth filter: 180-220 deg").
  - Units switched from `(unit)` to `[unit]` convention throughout (axis labels, panel titles,
    colorbar labels). The azimuth heatmap's y-axis dropped its unit entirely (`'azimuth [deg]'` ->
    `'azimuth'`) since the axis is labeled with compass points (N/E/S/W), not numeric degree
    values, so a degree unit next to it was confusing.
  - Colorbar placement rewritten: originally used two `fig.add_axes()` calls at hardcoded
    figure-fraction positions, tuned by eye for one specific row count -- these drifted badly
    for other `n_rows` (title/legend margins are ~fixed in inches, so their fraction of a
    taller/shorter figure shifts) and, after an intermediate attempt using
    `fig.colorbar(im, ax=axes[:, col])` (matplotlib's own axes-aware placement), the two colorbars
    ended up adjacent with no gap and overlapping each other's tick labels/unit text. Final fix:
    build each `cax` from the real post-`tight_layout` bounding box of its own column
    (`axes[0, col].get_position()` to `axes[-1, col].get_position()`) with a wide (~0.11 figure-
    fraction) gap between the two caxes, and widened the figure itself (11in -> 12.5in) to give
    both room without starving the plot area.

## Tests executed and results

- `pytest geode/tests/ -k rinex_qc` -- 57/57 passed after every change in this round (no test
  changes were needed; the bug was in plotting code the existing grid-collapse tests don't cover,
  since they test `collapse_to_elev_rings`/`collapse_to_azimuth` directly with synthetic arrays,
  not the day-reindexing layer around them).
- Manual verification against real `usa.gisa` data: confirmed the known 251-day-missing /
  120-day-largest-gap directly against the DB (`get_daily_bins` row count vs. calendar span),
  rendered a zoomed window (2020-06-01 to 2021-02-01) spanning that gap before and after the fix,
  confirmed blank rendering after. Confirmed colorbar/label fixes visually across differing row
  counts (`G`: 3 bands, `R`: 2 bands) and both heatmap orientations (elevation, azimuth).

## Known issues / limitations

- **New, not yet fixed**: a narrow custom date range (e.g. the 8-month window used to verify the
  gap fix) produces a cluttered/duplicated x-axis tick label sequence (`2020 2020 2020 2021`) from
  `mdates.DateFormatter('%Y')` combined with matplotlib's default year locator choosing sub-year
  tick spacing. Not fixed this round (not what was reported; the default full-history view most
  users will see doesn't trigger it). Would need an explicit locator (e.g. `AutoDateLocator` tuned,
  or switching format based on span length) if custom narrow ranges become a common CLI use case.
- The `_full_calendar_days()` reindexing is now done independently in three places
  (`get_daily_completeness`, `get_daily_signal_series`, both heatmap `build_grids()` closures) with
  three different fill strategies (0-or-NaN for completeness, NaN for the other two) -- correct as
  implemented, but worth knowing this isn't a single unified code path if touching any of them
  again.

## Next steps

None specific to this round beyond the existing checkpoint's "Next steps" (time-axis downsampling,
`snr_unit == 'receiver'` handling, elevation-banded azimuth split, second-station validation).
