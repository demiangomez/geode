# Known Issues: Doc/Code Drift, Dead Code, Gaps

Technical findings only — purely factual, no session narrative. Append new findings as they're
confirmed; remove an entry once it's actually fixed (and note the fix in a checkpoint, not here).

## Naming drift: Parallel.GAMIT → GeoDE (mostly fixed 2026-11-13)

The project was renamed from "Parallel.GAMIT" to "GeoDE," and further restructured from an even
older `pgamit` package layout. Most of the fallout was fixed as part of the knowledge-base
initiative (see `knowledge/checkpoints/2026-11-13-knowledge-base-bootstrap.md`):

- `docs/modules.rst` toctree title fixed ("Parallel.GAMIT" → "GeoDE").
- `docs/pgamit.rst` renamed to `docs/geode.rst` (its toctree reference in `modules.rst` said
  `geode`, but the file was literally named `pgamit.rst` — a broken toctree entry). Dead entries
  removed (`geode.StationList`, `geode.cluster`, `geode.dbConnection_old`, `geode.plots`, and an
  entire stale `pgamit.*` series pointing at a package that no longer exists). 11 entries with a
  confident current 1:1 top-level equivalent were renamed (`pyOTL`, `pyOkada`, `pyOptions`,
  `pyPPP`, `pyParseAntex`, `pyProducts`, `pyRinex`, `pyRinexName`, `pyRunWithRetry`, `pyStack`,
  `snxParse`).
- `docs/com.rst` had 2 dead entries (`CompareDBs`, `gamit_stats` — neither corresponds to a real
  `com/` script) removed, and 10 real scripts that had never been documented added
  (`CampaignPlanner`, `ConvertMetadata`, `CreateEtmFunctions`, `EtmStacker`, `MergeStation`,
  `ModelProcessing`, `PrintVelocities`, `StationKmz`, `StationReport`, `SyncMetadata`). The newly
  added entries only have the `.. automodule::` block, not the `.. sphinx_argparse_cli::` block
  the other entries have — **someone should verify each of these 10 scripts actually exposes a
  `main()` function matching the expected signature before adding that block** (not verified here).
- `README.md`'s "Support" link fixed (`Parallel.GAMIT` → `geode`).

**Deliberately deferred, not done:** `docs/geode.rst` still only documents flat `geode/` modules
(as it always did) — it has no entries at all for the `etm/`, `gamit/`, `metadata/`,
`campaign_planner/`, `clustering/`, `elasticity/`, `reports/` subpackages (60+ files). A handful of
entries with no confident current equivalent were deleted rather than guessed at
(`pyGamitConfig`/`pyGamitSession`/`pyGamitTask`/`pyGlobkTask` — now `geode/gamit/*`;
`pyStation`/`pyStationInfo`/`pyZTD`/`pyParseZTD` — now `geode/gamit/station.py` /
`geode/metadata/station_info.py` / `geode/gamit/ztd.py` / `parse_ztd.py`; `pyVoronoi`,
`pyTerminal`, `pyTrimbleT0x`, `pyStatic1d`). A proper fix is to run `sphinx-apidoc` fresh against
the current `geode`/`com` packages (Sphinx isn't installed in this environment, so this wasn't
attempted) and manually re-merge the `sphinx_argparse_cli` blocks per `com/` script, since
`sphinx-apidoc` doesn't know about those custom directives.

## Repo hygiene

- Two stale duplicate `*.egg-info` directories exist: `geode.egg-info` (old package name `geode`)
  alongside the current `geode_gnss.egg-info` (`geode-gnss`, matching `pyproject.toml`). Both are
  gitignored build artifacts — harmless, but `geode.egg-info` is leftover cruft from before the
  PyPI package rename and can be deleted without consequence whenever someone's cleaning up.

- `com/EtmStacker.py --help` currently crashes before argparse even runs: `geode/etm/stacker/stacker.py:9`
  does `import numpy.linalg.linalg`, a private submodule numpy 2.x removed (`ModuleNotFoundError:
  No module named 'numpy.linalg.linalg'`). Found incidentally while verifying the CLI help-formatting
  fix below (`SmartHelpFormatter`) on every `com/` script — unrelated to that fix (confirmed via
  `git diff`: this file wasn't touched) and not fixed here. The call site needing the actual numpy
  symbol should import it from `numpy.linalg` directly instead.

## NumPy 2.x migration fallout (numpy 1.26.4 -> 2.4.6, commit 3c65737)

- `geode/pyETM.py:2311-2334` (`process_covariance`) has the same bug that was fixed in
  `geode/etm/least_squares/least_squares.py:1147-1170`: `cov = np.zeros((3, 1))` makes each
  `cov[k]` a 1-element 1-D array rather than a scalar, and assigning that into a true scalar slot
  (`self.covar[0, 1] = cov[0]`) raises `ValueError: setting an array element with a sequence.`
  under numpy>=2.x (numpy 1.x silently squeezed it). **Not fixed** — `pyETM.py` is legacy and must
  not be modified (see `CLAUDE.md`). Hits whenever `NoiseCovarianceGLS` output chi2/dof check
  triggers the N/E/U covariance step on a station processed through the legacy path (e.g. old
  `PlotETM.py` call paths still wired to `pyETM.py` instead of `geode/etm/`).
- No test in `geode/tests/` exercises `process_covariance` / `NoiseCovarianceGLS`'s
  N-E/E-U/N-U covariance step directly, which is why this wasn't caught until it was hit live for
  station `igs.rmbo`. Worth adding regression coverage for `LeastSquares.process_covariance` with
  non-trivial per-component outlier flags.
- `np.row_stack` (deprecated alias for `np.vstack`) is still used in `com/NEQStack.py`,
  `com/FixPlate.py`, and `geode/pyStack.py`. It still works in numpy 2.4.6 (emits
  `DeprecationWarning`) but is scheduled for removal in a future numpy release. Low priority —
  `pyStack.py` is legacy-but-load-bearing (see `knowledge/architecture/etm-stacker.md`), and the
  `com/` call sites are simple one-line swaps to `np.vstack` whenever someone's in that code.
- A grep sweep for other numpy 2.0 breaking changes (removed scalar aliases like `np.float`/
  `np.int`/`np.bool`/`np.object`/`np.string_`/`np.unicode_`, removed `np.product`/`np.alltrue`/
  `np.msort`, `copy=False` semantics, `np.matrix`, `ndarray.newbyteorder`, private `np.core`
  imports) found no other hits as of 2026-11-16. Full test suite (`pytest geode/tests`) passes
  (208 passed, 127 skipped) after the `least_squares.py` fix.

## Dead/broken code

- `geode/elasticity/elastic_gridder.py` imports `from elastic_interpolation import
  interpolate_at_points` — missing the `geode.elasticity.` package prefix (the function does exist,
  just at the wrong import path). Would raise `ModuleNotFoundError` if ever executed. Currently
  dead: no other module in the repo imports `elastic_gridder`.

## Doc/code drift

- `geode/metadata/DESIGN.md`'s "Trust model" section (per-source auto-apply trust tiers, e.g.
  official IGN-Ar/RAMSAC logs auto-applying INSERT/UPDATE) is **not implemented**. Current code
  applies pending INSERT findings uniformly regardless of source
  (`com/StationInfoEdit.py --apply-pending`). See
  [architecture/metadata-llm-pipeline.md](architecture/metadata-llm-pipeline.md).
- `AdjustmentResults.spectral_index_stochastic_noise` (`geode/etm/core/data_classes.py`) is
  mislabeled: it's set from `compute_plomb(results.stochastic_signal, ...)`
  (`geode/etm/least_squares/least_squares.py`, `NoiseCovarianceGLS.adjust()`), i.e. it's the
  spectral index of the collocation-predicted **signal**, not the noise. This predates (and is
  distinct from) the `stochastic_noise`/`stochastic_signal` split added 2026-11-16 (see
  [checkpoints/2026-11-16-stochastic-signal-noise-split.md](checkpoints/2026-11-16-stochastic-signal-noise-split.md));
  not renamed here to avoid an unrelated behavior change to existing JSON/report consumers of that
  field name.

## Stale references after dbConnection.py migration split (2026-11-20)

- `database/migration_plan_engine_table_naming.md` (pending, not-yet-applied plan for the
  `gamit_soln`→`soln_gamit`/`ppp_soln`→`soln_gpspace`/`stacks`→`stacks_gamit` rename) references
  specific `geode/dbConnection.py` line numbers (e.g. "line 836", "lines 150-172", "lines 254-292",
  "lines 294-330") and describes "replacing in place" specific blocks inside the old monolithic
  `_run_db_migrations()`. That function was split into one file per migration under
  `geode/database_migrations/NNN_name.py` (see
  [architecture/database-schema.md](architecture/database-schema.md)); the line numbers are now
  wrong and the "replace this block in place" instructions should instead mean "edit
  `009_create_gamit_antenna_residuals.py`/`010_create_ppp_antenna_residuals.py`/
  `005_ppp_soln_hash_to_bigint.py`". Not rewritten here — whoever picks up that plan should update
  its file/line references against the current `geode/database_migrations/` layout before
  following it literally.

## Modern ETM module: no VERSION-based cache invalidation outside JumpFunction (2026-11-21)

`geode/etm/data/etm_database.py`'s `load_parameters_db()` decides whether to reuse a station's
stored `etms` parameters purely by comparing a hash sum against what's currently computed
(`geode/etm/data/etm_database.py:57-68`). As of
[ADR-0007](decisions/0007-postseismic-decay-natural-log.md), `JumpFunction.rehash()`
(`geode/etm/etm_functions/jumps.py:303-307`) folds in `VERSION` (tied to the installed package
version via `setuptools_scm`, so it changes on every commit), matching the pattern the legacy
`pyETM.py` has always used. `PeriodicFunction.rehash()` (`etm_functions/periodic.py:145-148`),
`PolynomialFunction.rehash()` (`etm_functions/polynomial.py:95-98`), `StochasticFunction.rehash()`
(`etm_functions/stochastic.py:70-`), and `SolutionData.compute_hash()`
(`geode/etm/data/solution_data.py:291-304`) still do not — their hashes depend only on
config/data characteristics, never on code version. A logic change to any of those function types
(e.g. a periodic-model formula change) will not auto-invalidate stored parameters; they'll be
silently reloaded as if nothing changed. Scoped out of ADR-0007 deliberately (that change only
needed jump/decay parameters invalidated); revisit if another logic change to one of those four
needs the same treatment.

## Postseismic grid files predate the natural-log decay switch (2026-11-21)

`com/TrajectoryFit.py` and `com/PlotMapView.py` read previously-computed postseismic
amplitude grids from disk (`np.loadtxt(g)`, see `com/TrajectoryFit.py` around line 293) and feed
them into a fresh ETM as a fixed external model (`postseismic=[...]` kwarg, consumed by
`pyETM.ETM.apply_postseismic_model`, `geode/pyETM.py:2090-2110`). Grid files produced before
[ADR-0007](decisions/0007-postseismic-decay-natural-log.md) encode amplitudes in the old log10
basis; nothing detects or converts this — a grid generated pre-change, fed into the post-change
code, is silently off by ~ln(10) ≈ 2.303 for that term. No DB hash covers this path since it's a
file, not an `etms` row. Anyone rerunning those scripts with an older grid needs to regenerate it
first.

## Dependency bookkeeping

- `pyproject.toml` and `environment.yml` both declare dependencies independently; nothing keeps
  them in sync automatically. When adding/removing a dependency, update both or expect drift.

## Database

- `run_db_migrations()` (`geode/dbConnection.py`) is the only schema-change mechanism and has no
  version table — purely existence-checked idempotent DDL. Verify its call site is actually active
  before assuming new migrations will run; it has been observed commented out in the past.
- `gamit_antenna_residuals`'s FK to `gamit_stats` (`(project, subnet, year, doy, system)`) only
  declares `ON DELETE CASCADE`, not `ON UPDATE CASCADE` — the same shape that caused
  `ppp_antenna_residuals` -> `ppp_soln` to crash `station_merge` (fixed 2026-11-13, see
  [checkpoints/2026-11-13-ppp-antenna-residuals-fk.md](checkpoints/2026-11-13-ppp-antenna-residuals-fk.md)).
  Currently harmless because `gamit_stats` is not in `station_merge.TIME_TABLES` and nothing
  `UPDATE`s its key columns in place. Revisit if `gamit_stats` ever becomes mutable by a
  merge-like code path.

## ETM Stacker: interseismic field observability

Confirmed 2026-11-25 on the Ecuador stack (branch `stacker_refactor`, 31 full stations,
`-s_score 7.3`); see
[checkpoints/2026-11-25-interseismic-observability.md](checkpoints/2026-11-25-interseismic-observability.md).

- **Exactly one station (`ecu.qui2`) carries no postseismic term at all.** Every other
  velocity in the network is entangled with a decay amplitude only the postseismic model
  can supply, so the interseismic field is not observable independently of that model. This
  is a property of the network and the earthquake catalogue, not of any one code path, and
  it is not currently reflected anywhere in the reported covariance.
- **`etm_builder.create_station`'s `is_interseismic` test is a data-availability proxy for a
  model-adequacy question** ("does this station have clean pre-event data" standing in for
  "is its velocity the interseismic rate"). It is too generous when a pre-onset decay column
  has been dropped from the fit — the transient's gradient moves into the velocity and the
  station is promoted anyway — and it saturates to zero eligible stations as the earthquake
  catalogue gets more complete. Lowering `-s_score` from 7.3 to 7.0 takes the eligible set
  from 3 to 0.
- **`validation.max_condition_number` does not catch velocity/decay collinearity.**
  `ecu.jnec` reached a design-matrix condition number of 7.0e6 against a threshold of 15 in
  the stacker (8 on the standalone `PlotETM`/`PrintVelocities` path) without the check
  firing, producing a velocity of -3507/+3946/+3672 mm/yr with postseismic amplitudes of
  +576 m / -2071 m / +1656 m cancelling each other.
- **Session pickles predating the 2026-11-24 `master` merge cannot be loaded.** `3aacd4b`
  renamed `etm_functions/stochastic_signal.py`/`StochasticSignal` to
  `stochastic.py`/`Stochastic`, and `Stochastic.p.params` changed shape, so a module alias
  is not sufficient. Rebuild affected `.pkl` sessions rather than shimming them.

## ETM Stacker: weighting and catalogue (2026-11-25)

Confirmed on the Ecuador stack (branch `stacker_refactor`); see
[checkpoints/2026-11-25-gls-outlier-weighting.md](checkpoints/2026-11-25-gls-outlier-weighting.md).

- **`EtmStackerConfig.post_seismic_back_lim` is an absolute `Date(1990, 1)`, not a window.**
  `jump_manager.py:74-77` branches on the type: a `Date` is used verbatim, an int is
  subtracted from the station's own `date_min` (`ModelingParameters`' default, `365 * 5`).
  So every station in a stack admits catalogue events back to 1990 regardless of when it
  came online, and `earthquakes_cherry_picked` additionally admits them without the
  per-station magnitude check. `bra.amta` (799 solutions, 2015-036 to 2017-332) receives
  decay columns for events in 1994, 2001, 2005 and 2007 — flat over its own span and
  collinear with each other and the intercept. `drop_unconstrainable_postseismic` and
  `etm_builder.sync_geophysical_jumps` both clean up after this rather than preventing it.
  A per-station window, or a floor tied to the network's validity epoch, is the real fix.
  The drop only reaches the ETMs that exist when it runs: a sparse station's free fit is
  never subject to it, and any ETM rebuilt from the config afterwards re-admits every
  dropped event. `col.abcc`, `col.abpd`, `col.gara` and `col.zarz` carry decay columns for
  1991/1992/2004 events against data that starts in 2020.
- **A rank-deficient design discards the entire model, not the offending columns.**
  `least_squares.py:1069-1079` catches `DesignMatrixException` by setting
  `FitStatus.UNABLE_TO_FIT`, blanking `results`, and setting `fit = False` on *every*
  function — polynomial and periodic included. A station with one unresolvable decay
  column therefore reports "Not enough solutions to fit an ETM" and plots blank even with
  a complete time series. The message names the wrong cause.
- **The JSON cache quantizes the GLS noise model, so `-load_json` and GLS are mutually
  exclusive.** `etm_engine.py:~389` serializes with `round_digits=6`;
  `noise_covariance_lag` is in m² (order 1e-6, set in `least_squares.py:784` as
  `nm.c_lag_final / s ** 2`) and is absent from `no_round_fields`, so rounding destroys
  positive-definiteness — observed min eigenvalue -8.6e-05, failing as
  `ecu.abec E is not positive definite even with a jitter of 1`. `stochastic_signal` and
  `stochastic_noise` carry the same units and are also absent from the list. The comment
  one line above the assignment ("in mm: the JSON output rounds to 6 decimals") shows the
  rounding was designed for mm-unit diagnostics and then applied to an m²-unit array.
- **The stacker does not use the estimated inter-component covariance.** `master`'s
  `4f45d96` added N-E/N-U/E-U cross-covariance (`EtmFit.cross_covariance`), but
  `normal_equations._create_normal_equations` loops `for i in [1, 0, 2]` building an
  independent `N` per component, and `_build_base_normal_equations` lays those out as three
  separate diagonal blocks. There is also no cross-*station* term anywhere, by construction,
  so common-mode error is invisible to the weighting.
