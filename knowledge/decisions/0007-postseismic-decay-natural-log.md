# ADR-0007: Postseismic decay uses natural log, not log10

- **Status:** accepted
- **Date:** 2026-11-21
- **Branch (if not master):** master

## Context

The postseismic transient (logarithmic relaxation) component of the ETM — `amplitude * log(1 +
Δt/τ)` — was implemented with `np.log10` in both the legacy `geode/pyETM.py` and the modern
`geode/etm/etm_functions/jumps.py`. Mike Bevis' own postseismic-decay formulation uses natural log
(`ln`), not `log10`. To keep GeoDE's models consistent with that convention going forward, every
occurrence of this specific functional form needed to switch base.

## Decision

Switched `np.log10` → `np.log` in the four places that implement the `log(1 + Δt/τ)` postseismic
decay functional form:

- `geode/pyETM.py` (`Model.eval`, `CoSeisJump.eval`, `ETM.apply_postseismic_model`) — edited despite
  the project's normal "never modify pyETM.py" rule, per explicit user instruction for this task.
- `geode/etm/etm_functions/jumps.py` (`JumpFunction._create_logarithmic_decay`) — the modern
  equivalent.
- `geode/etm/stacker/stacker.py` (postseismic field combination in the spatial stacking step) — this
  re-derives the same decay formula (`log(1 + R_max/τ)`) to project per-relaxation field amplitudes
  onto a common reference time when combining postseismic fields across stations/relaxations; it
  must use the same log base as whatever produced those amplitudes or the weighting is wrong by a
  constant factor.

Also added `VERSION` (from `geode.etm.core.etm_config`, which tracks the installed package version
via `setuptools_scm`) into `JumpFunction.rehash()` (`geode/etm/etm_functions/jumps.py`), so
previously-stored jump/decay parameters in the `etms` table are detected as stale and re-estimated
the next time the modern ETM engine runs, instead of being silently reloaded and reinterpreted
under the new log base. See [known-issues.md](../known-issues.md) for the related gap this exposed.

**Deliberately left unchanged:** `geode/pyOkada.py` and `geode/etm/core/s_score.py`'s "s-score"
(`a*mag - log10(distance) + b`, aka "Mike's expression") — a distance/magnitude relevance
*threshold* for deciding whether a station needs a coseismic/postseismic jump at all. It shares
the name "postseismic" (`p_score`, `POST_SEISMIC_SCALE_FACTOR`) and a Bevis provenance, but it is a
different formula (empirical ground-motion-style attenuation, conventionally log10) from the
`log(1+Δt/τ)` temporal decay transient this ADR is about. Confirmed with the user to leave as-is.

Also left unchanged: all `np.log10` uses that are matrix-condition-number diagnostics
(`geode/pyETM.py:1310`, `geode/etm/etm_functions/jumps.py:389`,
`geode/etm/least_squares/design_matrix.py:210`), PSD/axis-scaling log-log math
(`geode/etm/least_squares/least_squares.py:127,330`, `field_visualizer.py:439`), and the unrelated
Love-number degree-truncation calc in `geode/elasticity/green_func.py:156-157` — none of these
implement the postseismic decay functional form.

## Rationale

The amplitude parameter fit alongside `log(1+Δt/τ)` is defined in units of "displacement per unit
of the log function," so its numeric value is tied to the log base used at fit time. `log10(x) =
ln(x)/ln(10)`, a constant ~2.303 scale factor independent of Δt/τ — so switching bases is a clean,
uniform rescale of the amplitude, not a change in the functional shape of the decay curve itself.
That's exactly why the Stacker's field-combination formula also had to switch: it reuses the same
functional form to re-evaluate already-fit amplitudes at a different reference time, so it must
match whatever base produced those amplitudes.

## Consequences

- New fits are directly comparable to Bevis-convention (natural-log) literature values.
- **Old fits are not interchangeable with new ones.** Any `etms` row with a decay-type jump
  (`CO_SEISMIC_DECAY`, `CO_SEISMIC_JUMP_DECAY` in pyETM.py terms; `POSTSEISMIC_ONLY`,
  `COSEISMIC_JUMP_DECAY` in the modern module) fit before this change has its amplitude in the old
  (log10) basis. Loading it and treating it as a natural-log amplitude understates the modeled
  displacement by a factor of ~2.303 for the affected decay term.
- For `pyETM.py`: already self-healing. `CoSeisJump.rehash()` already folds `VERSION` into its hash
  (`geode/pyETM.py:1283-1284`), and `VERSION` changes with every commit (via `setuptools_scm`), so
  `ETM.run_adjustment()`'s hash-sum comparison (`geode/pyETM.py:2112-2144`) will treat all stored
  parameters as stale and re-estimate on the next run after this commit lands — this was already
  true for *any* commit, not something new introduced here.
- For `geode/etm/` (modern module): was **not** self-healing before this ADR — none of
  `JumpFunction`/`PeriodicFunction`/`PolynomialFunction`/`StochasticFunction`'s `rehash()`
  implementations referenced `VERSION` at all (a pre-existing gap, not caused by this change). Fixed
  here only for `JumpFunction` (scoped choice — see known-issues.md for the remaining gap in the
  other three). This means jump/decay parameters will be force-recomputed on the next run after
  this commit; periodic/polynomial/stochastic parameters will not be, which is fine for this ADR's
  purposes since those don't use the decay formula.
- Grid files written to disk by `com/TrajectoryFit.py`/`com/PlotMapView.py` (which pass previously
  interpolated postseismic amplitudes back into a fresh ETM as a fixed external model) are **not**
  covered by any hash mechanism — they're files, not DB rows. If such a grid was produced before
  this change, re-running those scripts with it will silently mix log10-based and natural-log-based
  values. No automatic detection for this; flagging here since it's a manual workflow.

## Alternatives considered

- Leave `pyETM.py` untouched (per the normal project rule) and only switch the modern module —
  rejected per explicit user instruction that `pyETM.py` must also be updated for this task.
- Add `VERSION` to every modern-module `rehash()` (matching pyETM.py's blanket approach exactly) —
  rejected as broader than needed; would force a full re-estimate of every station's full ETM
  (periodic + polynomial + stochastic + jumps) on every future commit, not just decay-affected
  parameters. Scoped to `JumpFunction` only instead.
- Also convert the `pyOkada`/`s_score` distance-magnitude formula to natural log for blanket
  "Bevis-consistency" — rejected; it's a different formula (relevance scoring, not a temporal decay
  transient) and the user confirmed to leave it as log10.
