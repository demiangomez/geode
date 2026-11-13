# ETM & Stacker (`geode/etm/`)

The Extended Trajectory Model (ETM) subsystem: per-station time series modeling, plus spatial
stacking of per-station parameters into shared reference-frame fields. This is the most actively
developed area of the codebase.

**Before relying on anything below about in-progress stacker work, check which branch you're on.**
Architectural work here has previously existed only on feature branches and been reverted/redone on
`master` — see "Sparse-station work" below and [decisions/](../decisions/).

## Directory map

- **`core/`** — `etm_engine.py` (`EtmEngine`, the per-station orchestrator), `etm_config.py`
  (`EtmConfig`), `jump_manager.py` (`JumpManager`), `s_score.py` (`ScoreTable`, earthquake
  relevance scoring), `data_classes.py` (shared dataclasses), `type_declarations.py` (enums +
  `EtmException`), `logging_config.py`, plus `etm_stacker.py` (compat re-export shim for
  `geode.etm.stacker`) and `etm_stacker_vce.py` (Variance Component Estimation weighting, used by
  `com/EtmStacker.py`).
- **`data/`** — `solution_data.py` (`SolutionData` ABC / `CoordinateTimeSeries`, loads PPP/DRA/GAMIT
  coordinate series), `etm_database.py` (`save_parameters_db`/`load_parameters_db`), `etm_params.py`
  (`EtmParams`, get/set/validate API).
- **`etm_functions/`** — design-matrix building blocks, each an `EtmFunction` (ABC):
  `polynomial.py` (velocity+), `jumps.py` (mechanical + co-/post-seismic log-decay relaxation,
  the richest of these), `periodic.py` (annual/semiannual), `stochastic_signal.py` (GLS/collocation
  colored-noise signal, diagnostic-only), `auto_jumps.py` (automatic discontinuity detection).
- **`least_squares/`** — the adjustment engine, see below.
- **`stacker/`** — multi-station spatial stacking, see below.
- **`visualization/`** — `EtmPlotter` + template classes, plus `field_plot/` (interactive Cartopy
  map UI: `FieldVisualizer`, `CartopyRenderer`, `InteractionHandler`, `WidgetManager`).

`geode/etm/test.py` is a dev scratch script, not a pytest module.

## Per-station engine

`EtmEngine` loads `SolutionData`, builds a `JumpManager` (mechanical + auto-detected + earthquake
jumps, the latter scored via `s_score.ScoreTable` using the Gómez et al. 2024 empirical relation,
cached in the `s_score_cache` table), assembles a `DesignMatrix` from
`[PolynomialFunction, PeriodicFunction] + jump_functions` (+ `StochasticSignal` under
collocation/GLS), and wraps the fit in `EtmFit`. `run_adjustment()` tries cached DB/JSON parameters
first, else calls `fit.run_fit()`.

## Least squares (`least_squares/`)

Three `AdjustmentStrategy` implementations, selected via `AdjustmentModels` config:

- **`RobustLeastSquares`** — iteratively-reweighted LSQ with chi-square pass/fail, outlier
  downweighting past `sigma_filter_limit`.
- **`LeastSquaresCollocation`** — adds a stochastic (collocation) component via Gaussian/Cauchy
  covariance kernels (`ls_collocation.py`).
- **`NoiseCovarianceGLS`** — GLS using a noise covariance (Sigma) estimated in
  `noise_covariance.py` (power-law + white-noise mixture; `cholesky_jitter()` does
  escalating-jitter Cholesky when Sigma isn't quite PD). Runs `RobustLeastSquares` first to flag
  outliers, excluded from both the noise model and the GLS fit.

Only `noise_covariance.py` has dedicated tests (`test_noise_covariance.py`,
`test_noise_covariance_jitter.py`) — `EtmFit`, `DesignMatrix`, and the strategies themselves are
untested.

## Stacker (`stacker/`) — spatial field stacking

**Problem:** make velocity / coseismic / postseismic parameters spatially consistent across many
stations' independent `EtmEngine` fits, rather than each station resolving its own
velocity-vs-decay ambiguity in isolation.

`EtmStacker` owns `stations`/`sparse_stations`, a `ConstraintRegistry`, a `GridSystem`, and
assembles/solves a combined normal-equation system (`build_system()` → `solve()`).

- `BaseConstraint` (ABC): `select_stations()` → `(constraining, to_constrain)`; builds one
  `ConstraintEquation` per constrained station via the abstract `_build_k_matrix` hook (implemented
  differently per subclass — not one shared k-matrix builder); auto-links stations <5 km apart;
  `apply_to_normal_equations()` stacks `(ke, kn, ku)` rows scaled by `1/sigma` into `K.T @ K`.
- `InterseismicConstraint` — constraining set = stations with `is_interseismic` (no early
  earthquakes affecting them); builds Sandwell-Wessel horizontal + elastic-load vertical
  interpolation weights.
- `CoseismicConstraint` / `PostseismicConstraint` — mutual leave-one-out constraints among stations
  sharing a geophysical jump for a given earthquake, built on the `SWOkada` mixin (shared
  leave-one-out interpolation logic) + `FaultGeometry` (Okada patch grid) for physics-based
  regularization. `PostseismicConstraint` demotes stations with too little post-event data
  (`MIN_CONSTRAINING_YEARS`) to the "to_constrain" side.
- `ConstraintRegistry` applies constraints in a fixed order (interseismic, coseismic,
  postseismic), resetting per-constraint caches before each solve.

See [campaign-clustering-elasticity.md](campaign-clustering-elasticity.md) for how `pyOkada` and
`geode/elasticity` back this.

### Sparse-station work — branch-scoped, not on `master`

`Station.is_interseismic` + `EtmStackerConfig.min_time_span`/`min_span_completion` classify a
station as "sparse" in `EtmStacker.add_station()`. On `master`, sparse stations get no owned
normal-equation columns — they're used only by a map-click "predict this sparse station from the
already-solved field" diagnostic (`plot_constrained_sparse_etm`/`_build_sparse_etm_constraints`),
**not** fed back into the solve.

A fuller approach — direct-data-injection for sparse stations into the shared normal equations
(`constraints/sparse.py`) plus Schur-complement-corrected per-station adaptive reweighting
(`constraints/adaptive_weight.py`) — was implemented on branch **`stacker_refactor`**
(commits `531d72c`, `2e2f83a`, `1894054`, 3 commits ahead of a common ancestor shared with
`master`) but is **not merged**. The commit that *is* on `master` in that lineage
(`ec6f0ff`, "Add sparse station filtering, map display, and prediction testing") deliberately kept
only the detection/plotting groundwork and reverted the constraint-building code, leaving
`geode/etm/stacker/sparse_station_constraints.md` as a **forward-looking spec**, not documentation
of shipped code. That spec records three rejected design attempts (per-epoch uniform weight;
OLS-per-phenomenon reduction, which loses velocity/postseismic correlation; `h_sigma` folded into
per-epoch variance, which crushes post-earthquake weighting) and recommends re-implementing the
per-epoch approach, with co-located-station dedup and a smoothing-tolerance term still undecided.

See [decisions/0001-sparse-station-stacking-approach.md](../decisions/0001-sparse-station-stacking-approach.md)
for the reasoning behind why pure reweighting can't fix this, carried over from prior investigation.

## Legacy relationship

- `pyETM.py` — legacy, do not modify (see [overview.md](overview.md)). Still imported directly by
  several `com/*.py` scripts; `pyStack.py` imports from it too.
- `pyStack.py` — legacy but **still actively used**, not superseded by `geode/etm/stacker/` (it
  solves a different problem: polyhedron/network combination across a day's subnetworks, not
  spatial stacking of ETM parameters). `com/DRA.py` uses the new `geode.etm` engine *and*
  `pyStack.Polyhedron`/`np_array_vertices` together.

Import split across `com/*.py` (useful when deciding which engine a given CLI touches):

- New `geode.etm.*` only: `AlterETM.py`, `EtmStacker.py`, `CreateEtmFunctions.py`,
  `PrintVelocities.py`, `PlotETM.py`.
- Legacy `pyETM` only: `FixPlate.py`, `PlotMapView.py`, `QueryETM.py`, `TrajectoryFit.py`,
  `NEQStack.py`.
- Mixed: `Stacker.py` (both `pyETM` and `pyStack`), `DRA.py` (new engine + `pyStack`).
