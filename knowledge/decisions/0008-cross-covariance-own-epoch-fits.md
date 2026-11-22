# ADR-0008: Cross-component covariance keeps each component's own-epoch GLS fit untouched

- **Status:** accepted
- **Date:** 2026-11-21
- **Branch:** master

## Context

The N-E/N-U/E-U parameter cross-covariance (`Cov(x_i, x_j) = K_i C_ij K_j'`, the velocity error
ellipse and every other ETM parameter pair) was handed off from `pgamit`'s noise-method work
(`docs/geode_handoff/GeoDE_cross_covariance_spec.md`, `docs/cross_component_covariance.pdf`). The
spec's own design (Section 9, "Common epochs") says that whenever the cross-covariance is computed,
each component's *noise-model* estimation (empirical covariance, the weighted covariance fit,
periodic detection, Sigma — everything that produces the `w`/`nugget`/periods ultimately fed into
that component's own GLS) should be restricted to the epochs common to all three components, not
each component's own full set of inlier epochs. The component's final GLS adjustment itself would
still use all of that component's own valid epochs — only the noise-model parameters feeding it
would change.

The reference authors measured this change as small (GLS rates move by at most 0.13σ, sigmas by
-10% to +2%, Section 9.2) and designed it deliberately: it's what lets the auto and cross Sigma
matrices share one set of overlap counts (the "6-22% extra cost" efficiency claim, Section 9.3).

## Decision

GeoDE does **not** restrict the auto noise-model estimation to common epochs. Every component's
existing `NoiseCovarianceGLS`/`estimate_noise_model` fit (`geode/etm/least_squares/
noise_covariance.py`) is read as-is, on that component's own full set of inlier epochs, exactly as it
already runs today whether or not cross-covariance is requested. The common-epoch restriction is
applied *only* inside the new `geode/etm/least_squares/cross_covariance.py` module, for its own
internal steps (the empirical cross-covariance of the two components' step-1 residuals, the projected
cross kernels, and the cross Sigma) — never to recompute either component's own `w`/`nugget`/periods/
GLS result.

## Rationale

- The user's framing of the request was explicit: cross-covariance should be "isolated in its own
  module... since code will run after the GLS is done fitting each component individually." Any
  design that reruns the auto noise-model fit under a different epoch mask breaks that isolation:
  turning the switch on would then perturb today's N/E/U rate and sigma values, a behavior change to
  the *existing*, already-shipped single-component pipeline triggered by an unrelated, optional
  feature.
- The reference's own numbers say the perturbation is small and, by their own account, harmless for
  the GLS results — the main cost of NOT doing this is a modest increase in the cost/complexity of
  the implementation (no shared overlap-count trick between auto and cross Sigma) and small,
  explainable deviations between GeoDE's cross-covariance numbers and the reference MATLAB vectors
  for real stations (see "Consequences").
- `K_N`, `K0`, `A`, `v0`, `t` are **unaffected** by this choice: confirmed directly (see
  `knowledge/checkpoints/2026-11-21-cross-component-covariance.md`) that the step-1 fit (`K0`, hence
  `v0`) is identical whether or not the later stages are epoch-restricted, since
  `pair_setup.m`/`pair_cross_setup.m` themselves always compute the step-1 estimator from a
  component's *full* epoch set ("With `.pw`, the step-1 fit (A, K0) still uses all n epochs"). Only
  the empirical-covariance/Sigma/detection stage (which produces `w`, `nugget`, periods, and hence
  `sigma_ct`/`c_lag_final`/`Qx`/`K`) differs.

## Consequences

- **Easier:** the feature is strictly additive and provably non-invasive — enabling
  `noise_cross_covariance` can never change an existing station's rate, sigma, or any other
  already-reported ETM parameter. No change to `NoiseCovarianceGLS.adjust()`'s core logic was needed,
  only an additive hook (stash the finished `NoiseModelResult` + the GLS estimator `K`, computed from
  an already-available intermediate at near-zero extra cost) plus new orchestration in
  `EtmFit.process_cross_covariance()`.
- **Harder / open:** GeoDE's cross-covariance numbers for a given pair (`alpha_x`, `theta_w`, `w_x`,
  the rate correlation, and hence the velocity error ellipse) will not exactly reproduce the MATLAB
  reference test vectors (`docs/geode_handoff/test_vectors/{igm1,saja}_cross_*.mat`) for the auto-fit
  fields they were generated from (`comp{1,2}_w/nug/Psel/wP`), because those vectors assume the
  common-epoch-restricted auto fit. Validated directly: for IGM1 N-E, substituting the reference's
  exact common-epoch auto-fit values gives an exact match (empirical cross-covariance 1e-12,
  projected cross kernels 1e-14); using GeoDE's own production (own-epoch) auto fit instead, the
  `daily_residual_correlation` diagnostic still matches to 1e-13 (it only depends on `v0`/the common
  mask, not on the auto fit), but `theta_w`/`alpha_x`/the rate correlation differ by amounts
  consistent with, and no larger than, the reference's own documented own-vs-common-epoch
  sensitivity. `geode/tests/test_cross_covariance.py` validates the cross-specific math exactly
  (against the reference, with its own auto-fit values substituted in) and the full pipeline only as
  an end-to-end smoke/sanity check on GeoDE's actual (own-epoch) production inputs — it does not and
  cannot assert exact agreement with `plx_alpha`/`plx_theta_w`/`cross_cov_plx` for the real stations.
- If a future need arises for tighter agreement with the reference (or for the Sigma-sharing cost
  saving), revisit this decision — it would require threading a common-epoch mask into
  `estimate_noise_model`'s empirical-covariance/Sigma/detection stage (not its step-1 or final GLS
  stage), which does not exist today.

## Alternatives considered

- **Follow the spec exactly** (rerun each component's noise-model estimation restricted to common
  epochs whenever cross-covariance is requested, feeding both that component's own GLS and the cross
  terms) — rejected: breaks the "isolated add-on, never changes existing results" property the
  feature was explicitly asked to have, for a documented-as-small accuracy gain.
