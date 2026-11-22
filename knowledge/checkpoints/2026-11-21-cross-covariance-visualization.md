# Cross-covariance visualization: noise-plot diagnostics + velocity error ellipses

- **Date:** 2026-11-21
- **Branch:** master

## Summary

Follow-up to the cross-component covariance feature
([checkpoints/2026-11-21-cross-component-covariance.md](2026-11-21-cross-component-covariance.md)):
added two new plots requested by the user before committing — a `_noise_cross.png` diagnostic figure
(empirical cross-covariance fit + alpha_x grid search, one row per pair N-E/N-U/E-U, produced whenever
`-plot noise` is used) and a new `-plot ellipses` option (velocity error ellipses, one column per pair,
showing both the borrowed-residual-correlation and power-law cross-spectrum ellipses together when
both are available). This required restructuring each ETM function's per-pair cross-covariance storage
from a single tagged field into two always-distinct ones, so both models are simultaneously available
for the ellipse comparison — not just whichever one was "official" for a given run.

## Correction (same day, after this was committed)

The dual `cross_covar_borrowed`/`cross_covar_power_law` schema below was **reverted** after the user
tried it and pushed back on three points, all valid:
1. The `_log_cross_covariance_summary` "comparison" line showed `r` and `rate correlation` as two
   fields that are always numerically identical for the borrowed model — confirmed algebraically:
   `Cov_borrowed(x_i, x_j) = r · sigma_i · sigma_j` for *every* parameter pair alike (not just rate), so
   dividing the rate entry by `sigma_rate_i · sigma_rate_j` always gives back the same `r`. Recording
   both was pure redundancy, not a second real number.
2. The JSON `'comparison'` sub-dict had the same redundancy and an unclear purpose.
3. Two always-distinct per-function fields (`cross_covar_borrowed`/`cross_covar_power_law`) made
   parsing harder than necessary: a consumer has to check which one is non-empty to know which model
   ran, duplicating information already in `cross_covariance[pair]['method']`.

**Fix**: reverted to a single `cross_covar[pair]` field (whichever method actually ran, tagged by
`'method'`, exactly as it was before this round) and a single, non-redundant
`cross_covariance[pair]['borrowed_rate_correlation']` scalar (only present when the official method is
power-law — a genuine sanity-check number, not a duplicate of anything else in the summary). The
ellipse plot's "second" ellipse, when the official method is power-law, is now *reconstructed* in
`data_prep.prepare_ellipse_data()` from that scalar plus each component's own within-component rate
variance (`.covar`), rather than read from a stored matrix — exploiting the same algebraic fact above:
the borrowed model's rate covariance is always exactly `r · sqrt(Var(rate_i) · Var(rate_j))`, so no
matrix ever needed to be stored for it in the first place. Covered by a new test,
`test_prepare_ellipse_data_reconstructs_the_borrowed_block`.

The rest of this checkpoint (written before the correction) still accurately describes the noise-cross
plot, the ellipse plot's visual design, and the overall file list — only the specific field names
`cross_covar_borrowed`/`cross_covar_power_law` and the `'comparison'` sub-dict mentioned below are
superseded by the above.

## Correction 2 (same day, caught by the user a second time)

`'borrowed_rate_correlation'` — the single scalar Correction 1 above introduced to replace the
`'comparison'` dict — was itself still redundant with `'daily_residual_correlation'`, already present
in the same `cross_covariance[pair]` dict, for the identical algebraic reason: both are computed from
the same step-1 residuals via the same rank-1 borrowed-model structure (`Cov = r · sigma_i · sigma_j`
for *every* parameter pair alike, rate included), so they were always numerically equal. The user
caught this immediately: *"and you did it again... in the json: daily_residual_correlation and
borrowed_rate_correlation in cross_covariance. I beg you, stop the slop. Pay more attention to
details."*

**Fix**: removed `'borrowed_rate_correlation'` entirely — `process_cross_covariance()`,
`_log_cross_covariance_summary()`, `data_prep.prepare_ellipse_data()` (reconstructs the borrowed
ellipse from `daily_residual_correlation` directly instead), `noise_cross_template.py`'s title string,
and the tests were all updated to use the existing `daily_residual_correlation` field with no
replacement field added.

**Self-caught during the same fix, before the user found it a third time**: the power-law branch's
own `'rate_correlation'` field (a *real*, non-redundant number there, since the power-law fitted
matrix isn't rank-1) was being computed the same way in the **fallback branch too**, where the matrix
*is* the rank-1 borrowed one — so `rate_correlation` in the fallback dict was, again, always exactly
`daily_residual_correlation` in that same dict. Fixed by setting `'rate_correlation': None` in the
fallback summary (consistent with `alpha_x`/`theta_w`/`w_x`, already `None` there for fields that don't
apply to that model), rather than computing a number that would just repeat another field under a
different name. `_log_cross_covariance_summary`'s existing `if summary.get('rate_correlation') is not
None` guard already suppresses the now-redundant log line for fallback pairs with no code change
needed there. Added a regression assertion
(`fit.cross_covariance[pair]['rate_correlation'] is None` for NU/EU in
`test_process_cross_covariance_populates_cross_covar_with_the_official_method`) so a future edit can't
silently reintroduce this a third time.

The general lesson, now the standing rule for this feature: any value derived from the borrowed
model's matrix, for *any* parameter (not just rate), is mathematically guaranteed to equal
`daily_residual_correlation` — there is no parameter-specific information to extract from that model
beyond the one scalar `r`. Every future field touching `cross_covariance` needs to be checked against
this fact before being added.

## Files / modules affected

- **Schema change** (approved via `AskUserQuestion`): `geode/etm/core/data_classes.py`
  (`EtmFunctionParameterVector`) — `cross_covar: dict` replaced by `cross_covar_borrowed: dict` (always
  populated once `NOISE_COVARIANCE_GLS` ran, regardless of `-xcov`) and `cross_covar_power_law: dict`
  (only for a pair where that model was actually fit). `geode/etm/etm_functions/etm_function.py` —
  `set_cross_covariance()` replaced by `set_cross_covariance_borrowed()`/`set_cross_covariance_power_law()`.
  `geode/etm/core/etm_engine.py` — `no_round_fields` updated to match (full JSON precision preserved
  for both, same as the earlier `cross_covar` fix).
- `geode/etm/least_squares/least_squares.py` (`EtmFit.process_cross_covariance`) — restructured to
  **always** compute the borrowed-residual-correlation matrix (cheap: one `corrcoef` + outer product)
  as a comparison value, independently of whether the power-law model was requested/succeeded for that
  pair. The station-level `self.cross_covariance[pair]` summary keeps its existing flat shape (whichever
  method is "official") plus one additive `'comparison'` key holding the borrowed value always.
  `_log_cross_covariance_summary` gets one added line (the comparison's rate correlation) when the
  official method is power-law.
- `geode/etm/least_squares/cross_covariance.py` (`CrossCovarianceResult`) — additive-only: new
  `lag_days`/`gamma`/`model`/`fitted_lag_days`/`alpha_grid` fields (plotting diagnostics, populated from
  values `fit_pair()` already computes internally but didn't return), and a `'diagnostics'` sub-dict in
  `summary()`. No existing field, formula, or test changed.
- **New plot pipeline**, mirroring the existing `plot_noise()` pipeline exactly
  (`EtmEngine.plot_noise()` → `PlotDataPreparer.prepare_noise_model_data()` → `NoiseModelPlotData` →
  `EtmPlotter.plot_noise_model()` → `NoiseModelTemplate`):
  - `geode/etm/visualization/noise_cross_template.py` (new, `NoiseCrossModelTemplate`) — 3 rows (N-E,
    N-U, E-U) x 2 cols (empirical cross-covariance + fit vs signed lag; alpha_x grid search). Reuses
    `NoiseModelTemplate.plot_not_available` (extended with an optional `message` parameter so the
    placeholder text is correct in this different context) for pairs without a power-law fit.
  - `geode/etm/visualization/ellipse_template.py` (new, `EllipseTemplate`) — 1 row x 3 cols. Draws the
    borrowed ellipse always (dashed) and the power-law one when present (solid), 1-sigma, centered at
    the origin (shape/orientation only, matching the reference paper's own Figure 8 convention — not
    the 2.5-sigma residual-scatter convention of `histogram_template.py`'s existing, unrelated
    `_plot_error_ellipse`). New `_ellipse_from_covariance()` helper (eigh + angle + width/height).
  - `geode/etm/visualization/data_classes.py` — `NoiseModelPlotData.cross_covariance` (new field) and
    new `EllipsePlotData` dataclass (same 7 header fields as every other `*PlotData` container, reusing
    `PlotTemplate.generate_title`'s "ETM header pattern").
  - `geode/etm/visualization/data_prep.py` — `prepare_noise_model_data` populates the new field;
    new `prepare_ellipse_data()` assembles each pair's 2x2 rate covariance blocks from the polynomial
    function's own `.covar`/`cross_covar_borrowed`/`cross_covar_power_law` (local index 1 = velocity;
    polynomial columns are always `[offset, velocity, acceleration]`). Returns `None` (not an empty
    plot) when there's no fitted rate term or the covariance wasn't (re)computed.
  - `geode/etm/visualization/etm_plotting.py` (`EtmPlotter`) — `plot_noise_cross_model()`,
    `plot_ellipses()`.
  - `geode/etm/core/etm_engine.py` (`EtmEngine`) — `plot_noise_cross()`, `plot_ellipses()`, each
    guarding on `FitStatus.POSTFIT` and warning+returning `None` if the needed data isn't there (DB-cached
    fits never repopulate `cross_covariance`/`cross_covar_*`, same reasoning as the existing `plot_noise()`).
  - `com/PlotETM.py` — `'ellipses'` added to `-plot`/`--plot_options`; `'noise'` now also triggers
    `etm.plot_noise_cross()` (one flag, two files); the `force_computation` override (previously
    `noise`-only, gated on `adjustment_model == NOISE_COVARIANCE_GLS`) extended to also fire for
    `'ellipses'` unconditionally (the borrowed ellipse needs a fresh fit regardless of strategy, since a
    DB-cached fit has no `cross_covar_*` at all, not just no power-law diagnostics).

## Architectural decisions

Two forks resolved via `AskUserQuestion` before implementing (full plan:
`knowledge/decisions/` doesn't have a dedicated ADR for this — it's a plotting/reporting-layer
decision, not core-algorithm, so recorded here rather than as a new ADR):
1. Cross-covariance diagnostics go into a **separate file** (`_noise_cross.png`), not extra rows
   appended to the existing `_noise.png` — keeps both a readable size; mirrors the reference paper's
   own split between auto-fit and cross-fit figures.
2. Per-function storage is **two always-distinct fields** (`cross_covar_borrowed`/`cross_covar_power_law`)
   rather than one field tagged by method — the only way to have both ellipses available
   simultaneously for the comparison plot.

## Tests executed and results

- Updated `geode/tests/test_cross_covariance.py` for the schema change; the daily-residual-correlation
  orchestration tests needed no change (they assert on the always-unchanged top-level key). Added
  `test_process_cross_covariance_populates_both_per_function_fields` (gated on `GEODE_CROSS_RAW_DATA`):
  builds a minimal concrete `EtmFunction` subclass + real IGM1 N/E noise models (U given none), confirms
  `cross_covar_borrowed` is populated for all three pairs and `cross_covar_power_law` only for N-E.
- `pytest geode/tests/test_cross_covariance.py` (both env vars): **56 passed** (was 55; the one new test).
- Full `pytest geode/tests/` (both env vars): **424 passed, 0 failed** — fully clean.
- **After the "Correction" above** (single `cross_covar` field, flat `borrowed_rate_correlation`):
  `test_process_cross_covariance_populates_both_per_function_fields` renamed/rewritten to
  `test_process_cross_covariance_populates_cross_covar_with_the_official_method`; added
  `test_prepare_ellipse_data_reconstructs_the_borrowed_block` (confirms the reconstructed borrowed
  ellipse matches `borrowed_rate_correlation * sqrt(Var_i * Var_j)` exactly, and that it's genuinely
  different from the power-law ellipse, not an accidental duplicate). `pytest
  geode/tests/test_cross_covariance.py` (both env vars): **57 passed**.
- **After Correction 2 above** (`borrowed_rate_correlation` removed, fallback `rate_correlation` set to
  `None`): added a regression assertion for the fallback `None` to the same test. `pytest
  geode/tests/test_cross_covariance.py` (both env vars): **57 passed**. Full `pytest geode/tests/` (no
  env vars): **241 passed, 184 skipped** — clean, no regressions.
- **No automated test renders the new plots** (matplotlib figures aren't asserted on in pytest here, matching
  the existing `test_noise_model_figure` pattern, which only checks the file exists). Verified instead with a
  direct, non-pytest smoke script: built synthetic `NoiseModelPlotData`/`EllipsePlotData` objects and
  rendered both plots through the real `EtmPlotter`, inspecting the output PNGs visually — confirmed
  both ellipses render distinctly (dashed borrowed + solid power-law) with a correct legend, the N-U
  panel correctly shows only the borrowed ellipse (power_law was `None`), the E-U panel correctly shows
  "not available", the cross-covariance fit panel renders a believable empirical+model curve with
  "fitted lags" diamond markers, and the alpha_x grid-search panel correctly stars the chosen minimum.
  Caught and fixed one real bug this way: the cross-covariance "not available" placeholder reused
  `NoiseModelTemplate.plot_not_available`'s hard-coded text ("noise model not available"), which is
  wrong wording for this different context — fixed by adding an optional `message` parameter.

## Known issues / limitations

- **Not verified against a live database.** Everything here was checked with the offline `.mat`/raw-JSON
  test vectors (for the data layer) and a synthetic-data smoke script (for the plots themselves) — no
  run of `com/PlotETM.py <station> -lsq gls -xcov -plot noise ellipses` against a real multi-year
  station from the local GeoDE database has been done yet. This is the natural next verification step
  before considering the feature fully done (see Next steps).
- The noise-cross plot's left-panel x-axis (signed lag in years) can span a very wide range for a
  long-baseline station (e.g. 2 x 23 years for IGM1) with the empirical cross-covariance plotted at
  every single day — no downsampling was added (matches the existing single-component noise plot's own
  behavior, which has the same characteristic at full density; not a new limitation introduced here).

## Next steps

- Manual verification against a real station from the local database:
  `com/PlotETM.py <station> -lsq gls -xcov -plot noise ellipses -json <dir>`, inspecting `_noise.png`
  (should be byte-for-byte unaffected), `_noise_cross.png` (real empirical/model curves, not
  placeholders), and `_ellipses.png` (both ellipses per pair); then the same run without `-xcov` to
  confirm `_ellipses.png` falls back to the borrowed-only ellipse and `_noise_cross.png` is all
  placeholders.
- If the full `pytest geode/tests/` run queued at checkpoint-writing time surfaces any regression,
  address it before considering this done.
