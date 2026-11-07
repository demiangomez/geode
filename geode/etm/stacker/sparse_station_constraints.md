# Sparse station constraints: problem statement and notes for implementation

This document captures the problem, the requirements established in discussion, and
the dead ends explored (with the reasons they failed) for incorporating **sparse
stations** into the `EtmStacker` normal-equation system. The constraint-building
code itself has been removed from `stacker.py`; this is a specification to implement
fresh, not a description of code currently in the repo.

What *is* still in `stacker.py` and should be treated as stable groundwork:
- Sparse station detection (`EtmStackerConfig.min_time_span`, `min_span_completion`,
  the `is_sparse` check in `add_station`) and the `self.sparse_stations` list.
- `plot_constrained_sparse_etm` / `_build_sparse_etm_constraints`: a map-click
  "predict this station from the interpolated field alone" tool. It builds a
  constrained ETM using only `self.fields` (solved from full stations), for testing
  how well the interpolation predicts a sparse station's actual data. This is
  independent of how sparse data would feed back *into* the main solve, which is the
  subject of this document.

## What a sparse station is

A station whose span completion (`SolutionData.span_completion`, the fraction of its
observation window actually covered, vs. gaps) is below `min_span_completion`
(default 75%), or whose total time span is above `min_time_span` (default 2.5 yr) but
still too gappy to reliably fit its own velocity + coseismic jumps + postseismic
decays independently. These stations do **not** get their own columns in the shared
system (no owned parameters) — the goal is for their data to still inform the shared
spatial fields (interseismic velocity field, coseismic field per earthquake,
postseismic field per earthquake/relaxation) that full stations' mutual constraints
jointly determine.

## Hard requirements (established in discussion)

1. **The right-hand side must be data, not ETM-fit parameters.** Using a sparse
   station's own fitted velocity/jump/decay parameters (from `station.etm`, a
   per-station ETM that jointly fits constant + periodic + mechanical jumps +
   *every* geophysical jump/decay it's near) is wrong: those parameters are
   estimated jointly with many correlated nuisance and geophysical terms, biasing
   them. "The interpolated parameters added together have to equal the observations
   on the right hand side, not the fit parameters from the free ETM."

2. **Mechanical jumps / constant / periodic signal must be removed before use, using
   an independent, uncontaminated fit** — not the station's own stacker-config ETM
   (which jointly fits those nuisance terms alongside geophysical jumps, so removing
   them from *that* fit's output reintroduces bias). The clean approach:
   - Fit a *free* ETM for the station: polynomial + periodic + whatever mechanical/
     default-detected jumps it finds on its own, with no cherry-picked earthquake
     jumps at all (`EtmConfig(network_code, station_code, cnn=cnn)` with no
     `earthquakes_cherry_picked`/`earthquake_magnitude_limit` override — this is
     exactly the same "Step 1 unconstrained ETM" that `_build_etm` already computes
     for every station, for prefit/obs_sigma purposes).
   - Subtract that free fit's constant + periodic + mechanical-jump terms from the
     raw NEU data. Leave velocity *in* the data (don't subtract the polynomial's
     linear term) — velocity is one of the things the stacker is solving for.
   - Caution found during implementation: `SolutionData.coordinates.neu` is computed
     at construction time, *before* prefit models (mechanical-jump corrections) are
     assigned to the config, so it does **not** have mechanical jumps removed. Don't
     rely on it directly; evaluate the free ETM's own fitted mechanical-jump
     parameters and subtract them explicitly (the free ETM fits mechanical jumps as
     ordinary `fit=True` parameters, since that's how `_build_etm` detects them in
     the first place).
   - Caution: when a station's main ETM is loaded from a cached JSON
     (`-load_json`), `_build_etm` does not build Step 1's unconstrained fit at all
     (skipped for speed, since most stations are full and never need it). If you
     need this free fit for sparse stations specifically, compute it lazily, only
     for stations that turn out to be sparse, rather than unconditionally (which
     would significantly slow down `-load_json` runs for every station).

3. **The result must preserve the correlation between phenomena, not just marginal
   uncertainty.** When a sparse station has a data gap spanning an earthquake (no
   pre-event history), its velocity and that earthquake's postseismic decay are
   genuinely not locally separable — the data constrains a *combination* of the two
   tightly, even though neither term alone is well determined. This is supposedly
   the whole point of the stacking method: spatial information from other stations
   (with different gap patterns) should resolve what a single sparse station's data
   cannot. **Reducing to one independent equation per phenomenon (e.g. via per-
   phenomenon OLS with only the diagonal of the covariance kept) throws this
   correlation away** and was found to produce equations carrying almost no useful
   information in exactly the cases that matter most (see Attempt 3 below).

4. Sparse stations have no "owned" parameter being compared against the field the
   way a full station does (full-station mutual ties use `h_sigma`/`v_sigma` as a
   *smoothing tolerance* between two already-estimated quantities — the station's
   own well-determined parameter and the spatial interpolation). It's unresolved
   whether an analogous tolerance belongs in the sparse case at all, and if so, how
   it should enter without the bug described in Attempt 2 below. This needs
   deliberate design, not a reflexive reuse of `h_sigma` at the per-epoch level.

## Attempts made, and why each was wrong or abandoned

**Attempt 1 — per-epoch design matrix, uniform data-noise sigma.**
For each sparse station, build `K_e, K_n, K_u` matrices mapping global parameters
(spatially interpolated velocity × `Δt`, coseismic step `H(t)`, postseismic decay
`log10(1+Δt/τ)`) to the predicted position at every inlier epoch of the station's
*cleaned* data, and accumulate `K^T W K` / `K^T W obs` into the global system with a
uniform per-epoch weight `w = 1/(k·wrms)²` (`wrms` = the free fit's RMS, `k≈2` as a
safety inflation).

This is, algebraically, the *correct* approach — summing `K^T W K` over all epochs
with a uniform scalar weight is exactly equivalent to a local weighted least squares
over the station's full temporal basis, which correctly preserves the
velocity/postseismic correlation (point 3 above) for free, without needing to
explicitly construct or propagate a covariance matrix. **This was wrongly abandoned**
mid-session based on a flawed "epoch-count overweighting" diagnosis (reasoning that
feeding thousands of epochs somehow gives a station disproportionate influence
relative to full stations). On reflection that diagnosis doesn't hold up: if sigma
truly reflects per-epoch noise, the information content scaling with epoch count is
exactly how a station's own formal parameter sigma is derived everywhere else in
this codebase, and is not pathological by itself.

**What actually produced bad fits while using this approach was a separate, real
bug**: several stations in the Ecuador test dataset are *co-located* (e.g.
`igs.qui3`/`igs.qui4` at effectively 0.000 km separation — confirmed in existing
stacker log output via the "linking" mechanism in
`BaseConstraint.constrain_nearby_stations`). When such a pair appears together in
the list of full stations used as interpolation sources
(`compute_constraint_coefficients`), their rows in the Sandwell-Wessel design matrix
(`compute_sw_design_matrix`) become numerically identical (`get_qpw`'s fudge factor
`dr` only prevents the literal `log(0)` singularity; it does not prevent two
*distinct* stations at the same coordinates from producing identical rows). This
makes the matrix singular or severely ill-conditioned. Full-station mutual
constraints are shielded from the practical effect of this because of the separate
`constrain_nearby_stations` tie that forces such stations' own parameters to
(nearly) agree — but any *new* evaluation point near that cluster (i.e. a sparse
station asking "what does interpolation predict here") is fully exposed to it,
producing wildly unstable interpolation coefficients.

**The fix for this part is validated and worth keeping regardless of which
constraint-building approach is chosen**: before computing interpolation
coefficients for any target (sparse-station evaluation specifically, since full
stations are otherwise protected), drop near-duplicate-location stations (< ~10 m
apart) from the list of constraining/source stations. This is a `get_radius`-based
dedup over `station.projected_coords`, independent of everything else.

**Attempt 2 — OLS reduction to one amplitude per phenomenon per station.**
To avoid (what was believed to be) epoch-count overweighting, each sparse station's
full time series was reduced via ordinary least squares (against only its own active
temporal-basis columns — velocity `Δt`, each coseismic `H(t)`, each postseismic
`decay(t)` — using the cleaned data) to a single best-fit amplitude + marginal sigma
per phenomenon, then each phenomenon was fed as one independent equation into the
global system (observation = the OLS amplitude, sigma = the OLS-propagated marginal
uncertainty).

**This violates point 3.** The OLS amplitude for each phenomenon is a regression
coefficient from the *same* joint fit a station's own ETM would produce for those
columns — it is not free of the "coupled, not separable" problem point 1 and 3 are
about; it's exactly the same coupling. The *only* thing nuisance-pre-removal buys you
is avoiding contamination from mechanical/periodic/constant terms, not resolving
velocity-vs-postseismic collinearity. Worse, keeping only the marginal (diagonal)
sigma per phenomenon and feeding them as *independent* equations throws away the
covariance between e.g. velocity and postseismic — precisely the information the
stacking method needs to resolve local ambiguity using other stations' spatial
information. In the degenerate case (no pre-event data), this reduction produces two
equations each with large, "shrug" sigma, which contribute almost nothing useful to
the system — not because the data has no information, but because the reduction
discarded the one thing it did know (the well-determined *combination* of the two).

**Attempt 3 — per-epoch design matrix with `h_sigma` folded into the per-epoch
sigma.**
An attempt to reconcile "sparse stations shouldn't be able to dictate the field more
tightly than full stations are allowed to" by adding `(temporal_basis(t) · h_sigma)²`
to each epoch's variance, on top of the data-noise term, before weighting.

**This is mathematically unsound and was the worst-performing attempt.** `h_sigma`/
`v_sigma` represent a *single, shared* discrepancy between one station's (already
estimated) parameter and the spatial consensus — not independent noise redrawn at
every epoch. Applying it per-epoch treats one correlated bias as if it were
thousands of independent draws. Concretely, coseismic's `h_sigma = 1.0` (1 meter,
deliberately loose to allow real near-fault variability) added `1.0 m²` of variance
to *every single post-earthquake epoch independently* — crushing the weight of
essentially all data after any earthquake relative to the (information-free, for
geophysical terms) pre-earthquake segment, and compounding across multiple
earthquakes for stations with data after several events. This produced unstable,
nonsensical results. If a smoothing-tolerance role for `h_sigma` is wanted at all in
the sparse case (point 4), it must enter as a single term at a reduced/aggregate
level, not multiplied into per-epoch weight — and even then, doing so without
re-introducing Attempt 2's correlation-loss problem is an open design question.

## Where this leaves things

Attempt 1 (per-epoch, correlation-preserving, uniform data-noise weight) plus the
co-located-station dedup fix is the most defensible combination found, but it was
never cleanly validated end-to-end: by the time both pieces were in place together, a
separate, unrelated bug (the main ETM's "Step 1" unconstrained fit silently not being
computed when `-load_json` is used, leaving every sparse station's cleaning step with
nothing to work from) meant the sparse contribution was accidentally a no-op for an
entire test run, and investigation moved to the revert before a genuine fresh
evaluation could happen.

Suggested next steps if resuming this:
1. Re-implement Attempt 1 (per-epoch `K^T W K`, uniform data-noise sigma only) as
   the baseline — it's the one with correct underlying math.
2. Re-add the co-located-station dedup before computing interpolation coefficients.
3. Decide deliberately, from scratch, whether/how a station-level smoothing
   tolerance should temper the result, rather than reaching for `h_sigma` reflexively.
   Candidates worth considering: a genuine noise-covariance model for the per-epoch
   weight matrix (this codebase already has `NoiseCovarianceGLS` for correlated GPS
   noise — worth checking whether it's directly reusable here instead of a flat
   per-epoch sigma), or a single aggregate smoothing-tolerance term applied to the
   *reduced* system in a way that doesn't discard cross-phenomenon correlation (e.g.
   added to the diagonal of the full local Gram matrix before inversion, not applied
   independently per phenomenon).
4. Test each change in isolation (there's a lot of value in toggling pieces on/off
   one at a time — e.g. a `sparse_constraints_enabled` flag that lets you compare a
   solve with vs. without the sparse contribution, and per-station diagnostic plots
   of the cleaned data before it enters the system) before layering the next one on.
