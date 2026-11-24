# Fix: multipath detrending crashed production on satellite reappearance gaps

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Same day RINGO shipped (`2026-11-17-switch-to-ringo.md`), a live `--backfill` run hit
`psycopg2.errors.NumericValueOutOfRange: smallint out of range` inside `writer.write_db()`'s
`execute_values()` call. Two pre-existing observability gaps meant the error log didn't say which
file: `batch.py`'s `callback_handle()` only identified the failing station/file on the
`job.result`-is-failed path, not on an *unhandled* exception (`job.exception`, raised from
`write_db()` itself, after `process_one_file()` had already returned `'ok'`); `counting_callback()`
also silently excluded unhandled-exception jobs from the `ok`/`partial`/`failed` tally entirely, so
the real failure rate was under-reported. Both fixed first (`job.args` carries what `submit()` was
called with, so the record is recoverable even on this path).

Reproduced directly from the real archive: queried `rinex_qc_signal_bins` for the largest
`mp_rms_mm` already successfully written (32437 — a hair under smallint's 32767 max), traced it to
`usa.gisa` DOY 241/2026, re-ran the file through `runner.run()` and found satellite E23 with a
40m residual. Root cause, found by direct inspection of the raw per-epoch data (not guessed):
`_detrend_by_arc()`'s arc segmentation used only RINGO's explicit slip flags, but a slip flag isn't
the only thing that legitimately ends continuous observation — a satellite fully setting and
rising again later, or even a short dropout, produces no slip flag either (nothing to compare
across a gap with no data in it). One "arc" silently spanned a **2643-epoch (~22 hour) gap**
between two completely unrelated passes of the same satellite; fitting one polynomial across that
gap produced giant, physically meaningless residuals right at the boundary.

First attempted fix (switching the fit itself from one global degree-5 polynomial per arc to a
rolling-window fit via `scipy.signal.savgol_filter`, to address a *second*, independent problem —
Runge's-phenomenon-style edge instability in a single high-degree fit over a long span) did **not**
fix this on its own, since the rolling window still bridged the gap in index-position terms. The
real fix needed both: split arcs on *any* index discontinuity (not just slip flags) *and* fit each
resulting continuous run with the rolling window. Confirmed both pieces were independently real
problems: a small (5-epoch) gap on a different satellite (E03) produced a smaller (-6.66m) version
of the same artifact even without the 22-hour-gap scale.

## Fix

- `geode/rinex_qc/runner.py`: `_detrend_by_arc()` now further splits each slip-bounded arc into
  maximal runs of *index-contiguous* observed epochs (`np.split` on `np.diff(idx) > 1`) before
  fitting anything. Each run gets its own `scipy.signal.savgol_filter(col[run], window,
  DETREND_DEGREE)` fit (a vectorized rolling polynomial fit evaluated at each window's centre —
  not a workaround, the literal right tool), with `window = min(DETREND_WINDOW, len(run))` shrunk
  to the nearest valid odd size for short runs. `DETREND_WINDOW = 51`, matching RINGO's own
  default rolling-average window for MP-based slip detection (`s.mp.win`) for consistency rather
  than independent tuning.
- `geode/rinex_qc/writer.py`: `_quantize_bins()` gained `_safe_smallint()` — a value that would
  still overflow `smallint` after everything above is stored as `NULL` for that one cell instead of
  raising. Root cause is fixed, but this is deliberate defense-in-depth: before this, *any* future
  anomaly in *one* bin took down the entire `execute_values()` call, losing every other signal's
  otherwise-good data for that file (confirmed this was really happening — that's exactly how the
  original crash manifested).
- `geode/rinex_qc/batch.py`: `callback_handle()` now extracts the station/year/DOY from `job.args`
  on the unhandled-exception path and includes it in both the `tqdm.write()` line and
  `errors_RinexQC.log`; `counting_callback()` now counts an unhandled exception as `STATUS_FAILED`
  instead of silently excluding it from the `ok`/`partial`/`failed` tally.

## Tests executed and results

- `geode/tests/test_rinex_qc_runner.py`: added `test_detrend_by_arc_splits_on_a_gap_even_without_a_slip_flag`
  (a 2700-epoch synthetic gap between two constant-but-different runs, no slip flagged — each run
  must detrend to exactly zero, not show a boundary artifact), `test_detrend_by_arc_splits_on_a_small_gap_too`
  (a 5-epoch gap, same assertion), `test_detrend_by_arc_uses_a_rolling_not_global_window` (a smooth
  quadratic trend over 300 points plus small noise — residuals must stay small everywhere,
  including near the ends). Existing `_detrend_by_arc` tests (polynomial removal, slip-reset,
  min-length drop, NaN propagation) still pass unchanged.
- `geode/tests/test_rinex_qc_writer.py`: added `test_quantize_bins_stores_null_instead_of_overflowing_smallint`.
- Full `geode/tests/` suite: 229 passed, 0 failed, 127 skipped (unrelated).
- **Real end-to-end re-verification**: re-ran `usa.gisa` DOY 241/2026 (the exact file behind the
  32437mm bin) through `runner.run()` directly — max absolute detrended value across the *entire*
  file dropped from a confirmed 37-40m outlier to 2.35m; through full `pipeline.process_one_file()`
  — `status='ok'`, every signal's worst bin now under ~1.4mm-scale (well clear of the 32.767m
  smallint ceiling), whole-signal RMS all in the expected 0.44-0.59m range.
- **Re-checked the previously-"validated" DOY 292/2026 file for regression** — not just a
  non-regression check: GLONASS P2's whole-signal RMS dropped from an earlier-reported 1.58m to
  0.50m, now consistent with every other band/system in the same file. The gap-splitting bug was
  inflating results across many files to a lesser (non-crashing) degree too, not just the few
  extreme enough to overflow smallint — this was a real accuracy fix, not only a crash fix.

## Known issues / limitations

- `DETREND_WINDOW = 51` is inherited from RINGO's own slip-detection default for consistency, not
  independently tuned or validated against a reference fixture. Same caveat as ADR-0005's: worth
  revisiting once the documented LHCL/SANT/AGGO fixtures are available.
- The gap-splitting threshold is "any discontinuity at all" (`np.diff(idx) > 1`), the most
  conservative choice. Haven't checked whether this over-fragments otherwise-fine arcs with
  frequent single-epoch dropouts (e.g. a noisy receiver) into runs too short to detrend
  (`MIN_ARC_EPOCHS = 7`), discarding more data than strictly necessary. Not observed as a problem on
  the files checked so far, but not stress-tested for it either.

## Next steps

- Demian re-runs the real `--backfill -force` with this fix in place; the improved
  `batch.py`/`errors_RinexQC.log` identification means any *future* unhandled exception will name
  the file directly, without needing a DB-query-based reproduction hunt like this one required.
- Watch for `NULL` `mp_rms_mm`/`snr_ddbhz` cells in `rinex_qc_signal_bins` going forward — the
  defense-in-depth guard means a future anomaly will show up as a quiet `NULL`, not a crash; worth
  an occasional `WHERE n_obs IS NOT NULL AND mp_rms_mm IS NULL` sanity query rather than assuming
  it'll never trigger again.
