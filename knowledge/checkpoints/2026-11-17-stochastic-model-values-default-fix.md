# ETM noise-model diagnostics, signal_alpha_min CLI flag, and a default-plot bug fix

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Follow-up to [2026-11-16-stochastic-signal-noise-split.md](2026-11-16-stochastic-signal-noise-split.md).
Three things, the third found by the user while trying out the second:

1. `NoiseCovarianceGLS`'s screen log now prints a table of each PLN (power-law-noise) basis
   function's weight (reference vs. final fit) right before the "effective alpha" line, and that
   line's wording was clarified.
2. `com/PlotETM.py` gained `-alpha_min`/`--signal_alpha_min` to expose `noise_signal_alpha_min`.
3. **Bug found via (2) and fixed**: with no `-rm` flag at all, the plotted model curve changed
   shape when `-alpha_min` changed. Root cause: `model_values` (the plotted curve) had *always*
   baked `stochastic_signal` into itself unconditionally (pre-existing, not something the prior
   session introduced) — `-rm stoch_s`/`stoch` was the only way to strip it back out. Since
   `noise_signal_alpha_min` controls how much of the colored noise counts as "signal," the default
   curve necessarily changed with it once that knob became user-facing. Asked the user: should
   this default stay (alpha_min visibly affects the default plot, understood as intentional), or
   should the default become deterministic-only with signal/noise strictly opt-in via `-rm`? They
   chose the latter.

## Files / modules affected

**Diagnostics / logging:**
- `geode/etm/least_squares/least_squares.py` — `NoiseCovarianceGLS._log_summary()`: added the PLN
  basis weight table (`alpha`, reference weight, final weight, one row per `settings.alphas`
  entry); reworded the effective-alpha line from `effective alpha X -> Y with periodic terms` to
  `effective spectral index X (period band ...) before periodic terms -> Y (period band ...)
  after`, and expanded the `alpha()` helper's inline comments to explain what `band_empty` means
  and why the band's edges can shift slightly after periodic terms are added.

**CLI:**
- `com/PlotETM.py` — new `-alpha_min`/`--signal_alpha_min` argument (default `1.0`, matching the
  `LeastSquares` dataclass default), wired to
  `config.modeling.least_squares_strategy.noise_signal_alpha_min` alongside the other GLS knobs
  (`-pdet`, `-snr`, `-vfac`).

**The bug fix:**
- `geode/etm/visualization/data_prep.py`:
  - `_add_model_and_residuals()`: `model_values` is now **always** the pure deterministic model
    (`design.alternate_time_vector(...) @ result.parameters`, no `+ result.stochastic_signal`).
    `data.residuals` is now computed **directly** as `observations_fit - model_fit` (evaluating
    the deterministic model at the exact fit epochs, mirroring the existing `residuals_not_fit`
    pattern) instead of copying `result.residuals`. For GLS/LSQ_COLLOCATION this means
    `data.residuals` baseline is the full stochastic realization (signal + noise together), by
    direct construction rather than by incremental bookkeeping.
  - `_handle_stochastic_signal()`: simplified to a fully symmetric form — each flag
    (`plot_remove_stochastic_signal`/`_noise`) only ever subtracts its term from `observations*`
    and `residuals`, never touches `model_values` (which no longer contains either term to begin
    with). The old asymmetric "subtract from model if removing signal, else add signal back into
    residuals" logic is gone — it's no longer needed now that the baseline already contains both
    terms correctly. Dropped the now-unused `etm_results` parameter.

## Architectural decisions

- **model_values is deterministic-only, permanently** (user's explicit choice over keeping the old
  "signal baked in by default" behavior). This changes the default appearance of every existing
  LSQ_COLLOCATION/NOISE_COVARIANCE_GLS plot that didn't pass `-rm`: the model curve no longer
  follows the correlated noise wiggle; it's always just polynomial + periodic + jumps. The
  correlated wiggle is only visible by looking at the residuals subplot (which includes it by
  default) or by explicitly removing it from the data with `-rm stoch_s`/`stoch_n`/`stoch`.
- **`data.residuals` is computed directly from the displayed curve, not tracked incrementally.**
  The old code added/subtracted terms into `data.residuals` procedurally per flag, which worked
  but didn't by itself guarantee `observations - model_values == residuals`; the new code computes
  the baseline directly from `observations_fit - model_fit`, and each removal subtracts the exact
  same term from both sides, so the identity holds exactly for all 4 flag combinations by
  construction, not by case-by-case reasoning. Verified numerically (see Tests).

## Tests executed and results

- `python -m pytest geode/tests/ -q` → 208 passed, 127 skipped (unchanged).
- Manual synthetic check of `_log_summary()`'s new table/wording against a synthetic 400-day
  series with a known alpha=0.75 component (reused from the prior checkpoint's validation).
- Manual standalone script instantiating `PlotDataPreparer` with fake `EtmFit`/`SolutionData`/
  `StochasticSignal` objects (deterministic model = constant 5.0, known injected signal + noise
  arrays), calling `_add_model_and_residuals()` then `_handle_stochastic_signal()` across all 4
  combinations of (`plot_remove_stochastic_signal`, `plot_remove_stochastic_noise`). Confirmed:
  `model_values` is flat/deterministic in every case (no longer varies with the signal); the
  `observations_fit - model_values == residuals` identity holds to ~1e-13 in all 4 cases; with both
  flags off, `residuals` equals exactly the injected `signal + noise` and `model_values` is exactly
  the deterministic constant (max abs diff 0.0).
- **Not tested: an actual live PlotETM.py run against a real station** (no DB connection in this
  environment) — this is what originally surfaced the bug, so a live re-check is the natural next
  step.

## Known issues / limitations

- No automated test exists for `geode/etm/visualization/data_prep.py` (consistent with the
  existing known gap: `EtmFit`, `DesignMatrix`, and the adjustment strategies are all untested per
  [architecture/etm-stacker.md](../architecture/etm-stacker.md)). The manual script above is not
  checked into the test suite — worth promoting to a real pytest test given it caught a real,
  user-facing default-behavior bug.

## Next steps

- Re-run the user's original command
  (`PlotETM.py mcar -lsq gls -sol igs20_an-fixed -plot out noise -no_save -snr 2 -alpha_min 1.1`)
  against the live DB with `-alpha_min 1.0` vs `1.1` and confirm the model curve no longer changes
  without `-rm`, and that `-rm stoch_s`/`stoch_n`/`stoch` still look correct.
- Consider adding a `geode/tests/test_data_prep.py` covering the `observations - model == residuals`
  invariant across the 4 removal-flag combinations, using the same fake-object approach as the
  manual check here — this is cheap to make permanent and would have caught the original bug
  automatically.
