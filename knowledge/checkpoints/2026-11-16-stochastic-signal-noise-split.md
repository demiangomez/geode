# ETM stochastic signal/noise split + independent plot removal

- **Date:** 2026-11-16
- **Branch:** master

## Summary

For `LeastSquaresCollocation` and `NoiseCovarianceGLS`, the single "stochastic" component (the
collocation prediction) is now explicitly split into two independently-exposed, independently
removable parts: `stochastic_signal` (the collocation-predicted, correlated part — periodic terms
plus, for GLS, power laws with alpha >= `noise_signal_alpha_min`) and `stochastic_noise` (for GLS
only: the complementary collocation-predicted power laws with alpha < `noise_signal_alpha_min` —
**deliberately excluding white/near-white noise**, which has no meaningful predictor away from the
data it was observed at). Previously only `stochastic_signal` existed as a named quantity.
`com/PlotETM.py`'s `-rm`/`--remove` flag gained `stoch_s` (signal only) and `stoch_n` (noise only);
`stoch` was redefined to mean "both" (previously it meant signal-only — confirmed as an intentional
behavior change with the user).

This followed a prior request in the same session that added `noise_signal_alpha_min` as a config
knob for the GLS alpha threshold (`geode/etm/core/data_classes.py`'s `LeastSquares` dataclass) —
this is the follow-up that exposes signal vs. noise as separate, user-controllable outputs.

**The definition of `stochastic_noise` changed mid-session.** The first implementation defined it
as "whatever's left in `results.residuals`" (sparse, white noise included, placed only at actual
observation epochs). The user clarified that white noise must be **excluded** — noise should be
only the colored power-law bands below the threshold. That required a real second collocation
prediction (symmetric to how `signal` is predicted), not just scattering residuals onto the grid.
The code below describes only the final, corrected version; the intermediate one left no trace.

## Files / modules affected

**Core computation:**
- `geode/etm/least_squares/noise_covariance.py` — `estimate_noise_model()` now also computes
  `noise_lag`/`noise`: the same FFT-based collocation prediction as `signal_lag`/`signal`
  (`C(grid, t) C^-1 v`, via `circular_embed` + `to_grid` + `gls.c_inv_v`), but built from
  `gb[:, ~in_signal] @ fin.w[~in_signal]` only — no periodic terms, no `fin.nugget` (white
  variance). `NoiseModelResult` gained `noise_lag`/`noise` fields. The `grid_v_fft` term (the FFT
  of `C^-1 v` on the daily grid) is now computed once and reused for both convolutions.
- `geode/etm/core/data_classes.py` — `AdjustmentResults.stochastic_noise` field (array_fields list
  updated too).
- `geode/etm/etm_functions/stochastic_signal.py` — `StochasticSignal` gained `noise_params` (with
  `set_noise_params()` normalizing stale/missing/wrong-shape entries to zero — e.g. an older cached
  JSON without this field) and `eval_noise()`, mirroring `p.params`/`eval()`.
- `geode/etm/least_squares/least_squares.py`:
  - `NoiseCovarianceGLS.adjust()`: `results.stochastic_noise = self._signal_at(nm.noise, ...)`,
    the same dense day-grid lookup already used for `stochastic_signal` (not a scatter of
    residuals). The three early-return fallback branches (not enough inliers / non-unique days /
    noise model failed to fit) set it to zero, same treatment as `stochastic_signal` there.
  - `LeastSquaresCollocation._least_squares()`: `results.stochastic_noise` is always zero. This
    strategy's covariance is a single correlated kernel (`css`/`csz`) plus a purely diagonal/white
    `crr = diag(obs_sigmas**2)` (confirmed by reading `EmpiricalCovariance.__init__`) — there is no
    colored band below the single kernel to call "noise" without it being white noise, which is
    excluded by the same rule as GLS.
  - `EtmFit.load_results_to_functions()`: now also calls `f.set_noise_params(...)`.
- `geode/etm/data/etm_database.py` — `load_parameters_db()` (cached-DB-load path) does **not**
  reconstruct `stochastic_noise`: doing so correctly needs `estimate_noise_model()`'s collocation
  prediction, which a cache hit deliberately skips re-running. It stays at its zero default
  (`StochasticSignal.set_noise_params()`'s normalization handles this gracefully — same as any
  other stale/missing-shape input).

**Plotting:**
- `geode/etm/visualization/data_classes.py` — `PlotOutputConfig.plot_remove_stochastic` replaced
  by `plot_remove_stochastic_signal` + `plot_remove_stochastic_noise`.
- `geode/etm/visualization/data_prep.py` — `_handle_stochastic_signal()` now independently handles
  both flags (4 combinations verified by hand: both off = today's default "signal excluded from
  model, added back into residuals"; signal-only = today's old `stoch` behavior; noise-only = new,
  zeroes residuals and pulls observations toward the model+signal curve; both = pure deterministic,
  zero residuals).
- `geode/etm/visualization/time_series_template.py` — title "removed" flags list now reports
  signal/noise independently.
- `geode/etm/core/etm_config.py` — language dicts: `"stochastic"` label replaced by
  `"stochastic_signal"`/`"stochastic_noise"` (eng/spa/fra).
- `geode/etm/test.py` (dev scratch script) — updated to the new two-flag config.

**CLI:**
- `com/PlotETM.py` — `-rm`/`--remove` gained `stoch_s`/`stoch_n` choices; `stoch` now sets both new
  flags; help text rewritten.

## Architectural decisions

- **White noise is never part of `stochastic_noise`, by the user's explicit instruction.** It has
  no meaningful collocation predictor away from the epoch it was observed at (zero correlation
  length), so including it would have meant falling back to "whatever's left in the residuals" —
  which is what the first (superseded) implementation did, and exactly what was rejected.
- **`stochastic_noise` is never persisted to the DB.** Unlike `stochastic_signal` (saved/loaded via
  the `etms` table's generic per-function `object='stochastic'` row), reconstructing noise
  correctly requires re-running `estimate_noise_model()`'s fit, which a cache hit is specifically
  there to avoid. Left at zero on a cached load rather than approximated.
- **Noise lives on the same `EtmFunction` object as signal** (`StochasticSignal.noise_params`, a
  plain instance attribute outside the `self.p` dataclass), not a second `object='...'` function —
  avoids any DB/JSON schema surface area, since `self.p` is what `get_parameter_dict()`/`asdict()`
  serializes.
- **`stoch` semantics changed** (user-confirmed via AskUserQuestion): previously removed only the
  signal; now removes both signal and noise, leaving a pure deterministic curve. `stoch_s` is the
  new spelling of the old (signal-only) behavior.
- **`LeastSquaresCollocation`'s `stochastic_noise` is always zero**, not an approximation — there's
  genuinely no colored sub-band for that strategy to report once white noise is excluded by
  definition.

## Tests executed and results

- `python -m pytest geode/tests/ -q` → 208 passed, 127 skipped (skips are the pre-existing
  `GEODE_NOISE_TEST_VECTORS`-gated MATLAB vector tests, unrelated to this change — same skip count
  as before and after).
- Manual synthetic-data check of `estimate_noise_model()` (no MATLAB test vectors needed): built a
  400-day series with a known alpha=0.75 colored component + white noise, fit it with default
  settings (`signal_alpha_min=1.0`). Confirmed `nm.signal` is zero (no periodic/steep terms
  present) while `nm.noise` is nonzero and dense (tracks the alpha=0.75 band). Confirmed the
  structural check that distinguishes this from "scattered residuals": setting
  `signal_alpha_min=0.0` (so every alpha is "in signal") makes `nm.noise` **exactly** zero
  everywhere — residuals never would be exactly zero, so this confirms `noise` genuinely tracks the
  alpha partition rather than leftover scatter.
- Manual unit checks: `StochasticSignal` construction/`eval()`/`eval_noise()`/`set_noise_params()`
  including the stale/scalar-`0` normalization path; `com/PlotETM.py`'s new `-rm` argparse choices
  parse correctly and render sane `--help` text (via an isolated parser — the full script needs a
  live Postgres connection even for `--help`, a pre-existing, unrelated quirk).
- **Not tested: an actual end-to-end GLS/collocation fit against real station data** (no DB
  connection in this environment).

## Known issues / limitations

- Logged to `knowledge/known-issues.md`: `AdjustmentResults.spectral_index_stochastic_noise` is a
  pre-existing, unrelated mislabeling — it's computed from `stochastic_signal`, not noise. Not
  touched here to avoid an unrelated field-name behavior change.
- `stochastic_noise` is always zero for `LeastSquaresCollocation` and on any cached DB load (GLS or
  collocation) — not a bug, but worth remembering if a future consumer expects it to always be
  populated.
- No end-to-end verification against a live station fit — worth doing the first time `-rm stoch_n`
  is run against a real GLS station.

## Next steps

- Run `PlotETM.py -lsq gls -rm stoch_n` (and `stoch_s`, and plain `stoch`) against a real station
  with a live DB connection to visually confirm the removal combinations and that `stochastic_noise`
  looks like genuine colored (not white) scatter.
- Consider whether `spectral_index_stochastic_noise` should eventually be renamed/fixed, or
  supplemented with a true noise-spectral-index field now that `stochastic_noise` exists as its own
  array (flagged in known-issues, not done here).
