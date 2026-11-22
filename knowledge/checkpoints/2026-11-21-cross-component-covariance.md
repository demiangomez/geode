# N-E/N-U/E-U parameter cross-covariance (power-law cross-spectrum model)

- **Date:** 2026-11-21
- **Branch:** master

## Summary

Implemented the cross-component parameter covariance handed off from `pgamit`'s noise-method work
(`docs/geode_handoff/GeoDE_cross_covariance_spec.md`, `docs/cross_component_covariance.pdf`):
`Cov(x_i, x_j) = K_i C_ij K_j'` for each component pair (N-E, N-U, E-U), the velocity error ellipse
and every other ETM parameter pair's cross-covariance. Only the power-law cross-spectrum model
(spec Section 6.2/6.3) was implemented, by explicit request — not the constant-coherence model, even
though the spec recommends computing both. Exposed as an opt-in switch (`-xcov`/
`--cross_covariance` in `com/PlotETM.py`, `config.modeling.least_squares_strategy.
noise_cross_covariance`); off by default, where it falls back to "borrowed residual correlation"
(spec Section 10.2), generalized from a single scalar (today, velocity-only, in
`com/PrintVelocities.py`) to every ETM parameter pair.

Two pre-work decisions were resolved with the user via AskUserQuestion before implementing (recorded
in [decisions/0008-cross-covariance-own-epoch-fits.md](decisions/0008-cross-covariance-own-epoch-fits.md)
for the first; the second is below):
1. **Each component's existing GLS/noise-model fit is left untouched** — the spec's own design
   (Section 9) reruns the auto noise-model estimation restricted to epochs common to all three
   components; GeoDE does not, to keep the feature strictly additive/isolated. See the ADR for the
   full rationale and the measured consequence (real-station cross-covariance numbers don't exactly
   reproduce the reference vectors, for an explainable, bounded reason).
2. **Default fallback is Section 10.2 ("borrowed residual correlation"), not Section 10.1
   ("residual-ellipse scaling")** as literally named in the request — the PDF documents real flaws in
   10.1 (sign flips, invalid ellipses, degenerates to zero for near-equal rate sigmas) and, contrary
   to its own claim that GeoDE currently runs 10.1, GeoDE's existing code
   (`com/PrintVelocities.py`/`EtmFit.process_covariance`) already implements 10.2, just as a single
   scalar covariance (not per-parameter) and not written into the main JSON report.

## Files / modules affected

- **New module** `geode/etm/least_squares/cross_covariance.py` — all cross-covariance math, ported
  line-for-line from the MATLAB prototype (`pgamit/toy_arma/pair_cross_setup.m`,
  `pair_cross_empcov.m`, `pair_cross_projavg.m`, `ecov_sigma_multi.m`) and the real-data pipeline
  (`pgamit/PL_make_cross_test_vectors.m`): empirical cross-covariance, projected cross kernels, cross
  Sigma (first pass only, per spec Section 12's "do not implement a second Sigma pass"), the power-law
  cross-spectrum grid search + cutting-plane constrained fit (`scipy.optimize.minimize(SLSQP)`, per the
  spec's own suggestion — no new dependency), periodic cross terms, the parameter cross-covariance
  `K_i C_ij K_j'`, and the `borrowed_residual_correlation` fallback.
- `geode/etm/least_squares/noise_covariance.py` — minimal additive hooks: `NoiseCovarianceSettings.
  compute_estimator`, `GlsResult.k_estimator` (= `Qx @ (C^-1 A)'`, one extra matmul reusing an
  already-computed intermediate — no new Cholesky), `NoiseModelResult.a`/`.k0` exposed (were already
  computed locally, just not returned). No existing field or behavior changed.
- `geode/etm/core/data_classes.py` — `LeastSquares.noise_cross_covariance` (the switch);
  `EtmFunctionParameterVector.cross_covar` (new per-function JSON section, since `.covar` was already
  taken by the *within*-component covariance block).
- `geode/etm/etm_functions/etm_function.py` — `EtmFunction.set_cross_covariance()`, slicing a
  station-level pair matrix down to a function's own columns, mirroring `load_parameters()`'s
  existing `.covar` slicing.
- `geode/etm/least_squares/least_squares.py` — `NoiseCovarianceGLS` stashes each component's finished
  `NoiseModelResult`/`mjd0` (`self.noise_models`/`self.noise_model_mjd0`) when the switch is on;
  `EtmFit.process_cross_covariance()` (new, called right after `process_covariance()` in
  `load_results_to_functions()`) orchestrates all 3 pairs, choosing the power-law model or the
  fallback per pair, and pushes blocks into every function plus a station-level summary
  (`self.cross_covariance`).
- `geode/etm/core/etm_engine.py` — `save_etm()`'s top-level JSON: renamed `"covariance"` →
  `"residual_covariance"` (per the user's correction mid-session: it's derived from residuals, not the
  model) and added `"cross_covariance"`. `EtmEncoder`'s `no_round_fields` list (the same call site)
  updated to match the rename (`'covariance'` → `'residual_covariance'` — the stale name meant this
  block was silently rounded to 6 decimals too, a regression from the rename) and to add
  `'cross_covar'` (the user caught this: "the json output seems to be truncating the cross covariance
  to only 6 digits"). The station-level `cross_covariance` summary dict (diagnostics only, no matrix)
  is deliberately left rounded, matching `noise_model`'s similar diagnostic dict elsewhere. The
  pre-existing, same-class gap in each function's within-component `covar` field (not in
  `no_round_fields` either, initially logged to `known-issues.md` as out of scope) was also fixed, at
  the user's follow-up request, by adding `'covar'` to the same list.
- `EtmFit._log_cross_covariance_summary()` (`least_squares.py`) — screen diagnostic output per pair,
  called from `process_cross_covariance()`, mirroring `NoiseCovarianceGLS._log_summary`'s existing
  style (the user flagged this was missing partway through review): method used, `alpha_x`/`theta_w`/
  `w_x`/matched periods or the fallback reason, and the rate correlation (computed here, not inside
  the isolated module, since it needs the design matrix's rate column index).
- `com/PlotETM.py` — `-xcov`/`--cross_covariance` CLI switch, wired to the new config field.
- **New tests** `geode/tests/test_cross_covariance.py` — 47 tests, following
  `test_noise_covariance.py`'s exact pattern (`GEODE_NOISE_TEST_VECTORS` env var). A second, new,
  optional env var `GEODE_CROSS_RAW_DATA` (pgamit repo root, holding `arg.igm1_igs20.json`/
  `arg.saja_ppp.json`) gates the exact-match tests (empirical cross-covariance, projected cross
  kernels, daily residual correlation): these need the real per-epoch outlier flags to reconstruct
  the exact 3-way common-epoch mask the cross test vectors assume, which a 2-component-only
  intersection can't reproduce exactly (see ADR-0008). Without it, those specific tests are skipped;
  the rest (synthetic Sigma brute-force validation, the fallback formula, the offset-handling fix, and
  a full end-to-end smoke test using GeoDE's own production inputs) still run.

## Architectural decisions

- See [decisions/0008-cross-covariance-own-epoch-fits.md](decisions/0008-cross-covariance-own-epoch-fits.md).
- `K_N` (the GLS estimator, needed for `K_N C_NE K_E'`) was not previously stored anywhere, but
  `wa = C^-1 A` already existed inside `gls_fit()` (used for the variance-factor diagnostics) —
  `K = Qx @ wa.T` is a near-free addition, gated behind `compute_estimator` so it costs nothing when
  the feature is off.
- `self.p.covar` (within-component parameter covariance, per `EtmFunctionParameterVector`) was already
  a taken name; the new per-function section is `self.p.cross_covar`, keyed by pair label (`'NE'`,
  `'NU'`, `'EU'`).

## Tests executed and results

- `pytest geode/tests/test_cross_covariance.py` (both env vars set): **47 passed** in ~225s.
- `pytest geode/tests/test_noise_covariance.py geode/tests/test_noise_covariance_jitter.py`
  (`GEODE_NOISE_TEST_VECTORS` set): **123 passed**, the same **9 pre-existing failures** as on a clean
  stash of this work (`test_noise_model_figure`, unrelated `NoiseModelPlotData` constructor drift —
  fixed later in this same session, see below). Confirms the `noise_covariance.py` hooks (new
  `a`/`k0`/`k_estimator` fields) change nothing for existing callers.
- `pytest geode/tests/` (no env vars, default skip gating): **241 passed, 174 skipped** (127
  pre-existing + the 47 new cross-covariance tests, skipped without `GEODE_NOISE_TEST_VECTORS`),
  matching the pre-existing baseline exactly.
- Full `pytest geode/tests/` with both env vars set: **406 passed, 9 failed** — the 9 are exactly the
  pre-existing `test_noise_model_figure` failures above, nothing new.
- **Re-confirmed after the follow-up round** (JSON precision fix for `residual_covariance`/
  `cross_covar`, and the new `_log_cross_covariance_summary` diagnostic logging):
  `pytest geode/tests/test_cross_covariance.py geode/tests/test_noise_covariance.py
  geode/tests/test_noise_covariance_jitter.py` with both env vars — **170 passed, 9 failed** (same 9
  pre-existing failures), and the default fast suite — **241 passed, 174 skipped** — both unchanged.
- **The 9 pre-existing `test_noise_model_figure` failures were fixed** in a second follow-up, at the
  user's request (see "Known issues / limitations" below): `pytest
  geode/tests/test_noise_covariance.py::test_noise_model_figure` — **9 passed** (was 9 failed). Full
  `pytest geode/tests/` re-run after that fix: **415 passed, 0 failed**.
- **After the `theta_w`/`w_x` double-scaling fix** (bug 3 below) and its new regression test
  (`test_summary_reports_theta_w_and_w_x_unscaled`, parametrized over all 6 station pairs):
  `pytest geode/tests/test_cross_covariance.py` — **53 passed** (47 original + 6 new).
- **After the `daily_residual_correlation` alignment fix** (bug 4 below) and its two new orchestration
  tests: `pytest geode/tests/test_cross_covariance.py` — **55 passed** (53 + 2 new). Full
  `pytest geode/tests/` (both env vars): **423 passed, 0 failed** — fully clean, nothing outstanding.

## Validation approach against the real-station reference vectors

Validated incrementally against `docs/geode_handoff/test_vectors/{igm1,saja}_cross_{ne,nu,eu}.mat`,
in the spec's own acceptance order (Section 13.3), reading the actual MATLAB source
(`pgamit/toy_arma/*.m`, `pgamit/PL_make_cross_test_vectors.m`) rather than guessing indexing
conventions from the pseudocode alone:
- Empirical cross-covariance: exact (1e-12).
- Projected cross kernels (white + 5 power laws): exact (1e-14).
- Cross Sigma: validated against the literal spec formula via a brute-force synthetic case (1e-14,
  dependency-free); against the real station it differs from the reference by an amount consistent
  with ADR-0008 (own-epoch vs common-epoch `sigma_ct` input), not a bug — isolated and confirmed by
  swapping in the reference's own `sigma_ct`-equivalent inputs, which closes the gap by >40x.
- Full `fit_pair()` pipeline: runs end-to-end on real IGM1/SAJA data; `daily_residual_correlation`
  (depends only on `v0` and the common mask, not the auto fit) matches to 1e-13; `alpha_x`/`theta_w`/
  the rate correlation differ from the reference by amounts bounded by, and explained by, the same
  own-epoch/common-epoch difference.

Four real bugs were caught by this process before landing, all fixed and covered by regression tests:
1. `_parameter_cross_covariance` omitted the pair's epoch offset entirely (invisible for IGM1 N-E/N-U,
   where both components happen to share the same first epoch; would silently misalign `C_ij` whenever
   two components' first inlier epochs differ).
2. The fallback path (`_fallback`/`borrowed_residual_correlation`) applied one component's
   common-epoch mask to *both* components' step-1 residual arrays, which have different lengths and
   different index spaces — an `IndexError` in the best case, silent misalignment in the worst. Fixed
   by having each caller pre-slice its own residuals with its own mask before calling a now
   mask-free `borrowed_residual_correlation(residuals_i, residuals_j, sigma_i, sigma_j)`.
3. **Found live, by the user, from real station numbers** (`theta_w=151471.596 w_x=-431186.408 mm^2` —
   obviously too large for a cross-variance): `EtmFit.process_cross_covariance()` called
   `result.summary(scale=scale)` (`scale=1000`) to build the diagnostic `cross_covariance[pair]` dict.
   But `CrossCovarianceResult.theta_w`/`w_x` are already in mm² (`ctx.nm` comes from
   `NoiseCovarianceGLS`'s mm-scaled fit), so `summary()`'s internal `* scale ** 2` inflated them by
   `1000² = 1e6` on top of that — exactly the ratio in the reported numbers
   (151471.596 / 0.151... ≈ 1e6). The parameter-cross-covariance *matrix* conversion
   (`result.matrix / scale ** 2`, to GeoDE's native m², matching `.covar`) was correct and unaffected —
   only the human-readable diagnostics were wrong. Fixed by removing `summary()`'s `scale` parameter
   entirely (it never had a legitimate non-default value — every caller's inputs are always already in
   mm², so the parameter was a double-conversion bug waiting to happen) rather than just fixing the one
   call site; `summary()` now always reports `theta_w`/`w_x`/periodic coefficients in mm², matching the
   single-component `noise_model` diagnostic's unscaled convention. Covered by
   `test_summary_reports_theta_w_and_w_x_unscaled` (`geode/tests/test_cross_covariance.py`) — this
   exact bug slipped through the original test suite because it tested `cross_covariance.py`'s
   dataclass fields directly, never the `EtmFit`-level orchestration that called `summary()` wrong.
4. **Found live, by the user, asking why `daily_residual_correlation` differed between the power-law
   and fallback methods**: it legitimately shouldn't, but did — the two paths computed it from
   different residual arrays entirely. The power-law path (`cross_covariance._daily_residual_
   correlation`) uses the step-1 (pre-GLS) residuals (`nm.v0`), matching the spec's own definition
   (confirmed against the MATLAB reference: `corr(a.v0(a.P.pw), e.v0(e.P.pw))`). The fallback branch of
   `EtmFit.process_cross_covariance()` instead always used `self.results[i].residuals` — the *final*-fit
   residuals (after the GLS/collocation step has already removed whatever periodic/colored signal it
   explains), mirroring the older, unrelated `process_covariance()` convention. A real shared seasonal
   signal between two components shows up strongly in the former and is largely absent from the latter,
   so the two numbers can differ a lot, not just by rounding. User chose "align to step-1 residuals when
   available" over "leave as-is, just document it" (`AskUserQuestion`). Fixed: the fallback now prefers
   `noise_models[i]/[j].v0` at the common epochs (available whenever `NoiseCovarianceGLS` ran at all,
   regardless of whether the cross-covariance switch itself was on — `NoiseCovarianceGLS.adjust()`
   stashes the `NoiseModelResult` unconditionally), and only drops to the final-fit residuals when no
   noise model exists for one of the two components at all (e.g. a non-`NoiseCovarianceGLS` strategy).
   Covered by two new tests: `test_fallback_daily_residual_correlation_prefers_step1_over_final_fit_
   residuals` (synthetic case with the two conventions deliberately giving different answers) and
   `test_fallback_daily_residual_correlation_uses_final_fit_residuals_without_a_noise_model` (the
   genuine last-resort case still works). These are also the first tests in the suite that exercise
   `EtmFit.process_cross_covariance()`'s orchestration directly (a minimal hand-built `EtmFit` +
   `NoiseCovarianceGLS`/`RobustLeastSquares`, not a full `DesignMatrix`/`SolutionData`) rather than only
   `cross_covariance.py`'s functions — closing the exact kind of coverage gap that let bug 3 through.

## Known issues / limitations

- **Performance**: the SLSQP-based cutting-plane fit (15 `alpha_x` grid points x the constrained
  least-squares solve) takes ~20s per pair on IGM1 (23-year series), vs. the MATLAB reference's
  ~0.3s/pair (spec Section 9.3's `lsqlin`-based approach). Not optimized further in this round
  (correctness was prioritized); a real QP solver (OSQP/CVXPY) or a hand-rolled active-set method
  would likely close most of this gap if it becomes a practical bottleneck.
- **Exact agreement with the reference MATLAB vectors is not expected for real stations** — see
  ADR-0008. `test_cross_covariance.py` validates the cross-specific math exactly (with the reference's
  own auto-fit substituted in) and smoke-tests the full pipeline on GeoDE's actual (own-epoch)
  production inputs, which is the combination that matters for what actually ships.
- ~~Found, not fixed~~ **Fixed** (initially logged to `known-issues.md` per the "don't fix side
  quests" workflow rule, since it was unrelated, pre-existing `NoiseModelPlotData` constructor/test
  drift; the user then asked for it to be fixed and the entry removed): `test_noise_model_figure`
  (`geode/tests/test_noise_covariance.py:352`) was missing the required `span_completion` argument to
  `NoiseModelPlotData` — one-line fix, added `span_completion=100.` to the call. All 9 parametrized
  cases pass now.

## Next steps

- Manual end-to-end run of `com/PlotETM.py <station> -lsq gls -xcov -json <dir>` against a live
  database station, to confirm the produced JSON's `cross_covariance` top-level block and each
  function's `cross_covar` section look sane in context (not yet done — this session validated the
  module against the offline `.mat`/raw-JSON vectors and GeoDE's existing test fixtures only, no DB
  access was exercised).
- If the ~20s/pair cost proves too slow in practice (e.g. batch-processing many stations with the
  switch on), revisit the constrained-fit solver.
