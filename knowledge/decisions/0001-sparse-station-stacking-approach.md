# ADR-0001: Sparse-station stacking needs direct data injection, not better weighting

- **Status:** accepted direction; implementation not merged
- **Date:** 2026-11-09 (investigation), recorded 2026-11-13
- **Branch:** `stacker_refactor` (implementation only; this reasoning applies regardless of branch)

## Context

Sparsely-constrained stations in the ETM Stacker (e.g. `igs.qui3`) produce unstable
velocity/postseismic-decay solutions (velocity 29.5 mm/yr, decay terms up to -961 mm) when tied to
the spatially-interpolated field from other stations, even when the tie weight is set correctly.

## Decision

The fix has to be architectural — apply the sparse-station direct-data-injection method
(`constraints/sparse.py` on `stacker_refactor`) to full stations too, rather than continuing to
refine how strongly a station is tied to the field.

## Rationale

Two weighting-based approaches were tried and both work correctly as weighting, yet don't fix the
instability:

1. Per-station adaptive constraint weighting using raw normal-equation diagonals.
2. The same, but using Schur-complement-corrected "effective" diagonals (entanglement-aware,
   exact marginal precision of a correlated block) — implemented in `constraints/adaptive_weight.py`
   on `stacker_refactor`.

For `qui3`, the Schur-complement weighting was verified directly to be mathematically correct: its
effective weight for postseismic decay came out 17–85x *below* the default external tie weight,
meaning the field should have completely dominated its solved value. The solved result was still
unstable anyway.

**Conclusion:** if an honestly-strong tie still produces garbage, the problem isn't *how much* a
station's own estimate is trusted relative to the field — it's that the field itself is built from
other stations that each independently resolved the same velocity/decay ambiguity in their own
(also unstable) per-station fits, before any weighting is applied. Reweighting a tie cannot fix a
field that's built from already-corrupted per-station inputs.

The sparse-station method avoids this by construction: it never lets a single station
independently decompose velocity-vs-decay from its own data. It cleans per-epoch data of nuisance
terms only (constant/periodic/mechanical jumps — not the geophysical signal) and injects that
cleaned data directly into the shared field's normal equations via spatial projection (leave-one-
out interpolation coefficients, the same kind `BaseConstraint` subclasses already build). The
temporal ambiguity gets resolved collectively across many stations' raw data, not individually per
station and then patched together via a scalar tie weight.

## Consequences

- Full stations likely don't need the sparse path's pseudo-inverse collapse step
  (`_collapse_to_tempered_constraint` in the `stacker_refactor` implementation) — that exists for
  genuinely gappy/infeasible data, and full stations have enough data to inject directly.
- Not yet isolated: whether some *other* station in the network (not just `qui3`) is the actual
  source of the corrupted field, and whether the co-location-linking mechanism for nearby stations
  interacts badly with per-station reweighting.
- This direction is implemented on `stacker_refactor` but not merged to `master` — see
  [architecture/etm-stacker.md](../architecture/etm-stacker.md) for the branch state and
  [STATUS.md](../STATUS.md).

## Alternatives considered

- **Continue refining tie weighting** (reject): ruled out directly — see Rationale. Weighting is
  not the axis the bug lives on.
- **Sequential/layered adjustment** (open candidate, not yet scoped): fit in temporal layers instead
  of jointly estimating velocity + all jump/decay parameters at once, removing each resolved
  parameter from later stages rather than just reweighting it:
  1. Fit `is_interseismic=True` stations using only pre-event data (no jump/decay columns exist in
     that window, so velocity is unambiguous by construction). Build the interseismic velocity
     field from these.
  2. For stations without clean pre-event data, fix/subtract the Layer-0-interpolated velocity and
     estimate only the first event's jump/decay on the residual.
  3. Repeat per event, holding everything determined so far fixed — this also breaks
     decay-vs-decay collinearity for multi-event stations (e.g. `qui3`), which neither weighting
     approach addressed.

  Reuses `is_interseismic` (already the right Layer-0 classification) and generalizes the
  nuisance-subtraction mechanism already needed for the direct-injection approach. Known
  complication: fixing an estimated quantity (e.g. Layer-0 velocity) as if exactly known
  understates later-layer uncertainty (the standard "generated regressor" problem) — manageable via
  a tight prior carrying the earlier layer's sigma forward, rather than a hard fix, but not
  designed yet.

  **Side benefit if pursued:** removes the need for `earthquake_magnitude_limit` as a pre-filter.
  In the current joint/simultaneous architecture, including smaller earthquakes means more
  simultaneously-free jump/decay parameters competing for the same data — worse collinearity,
  hence the magnitude cutoff as a safety net. Sequentially, each event is estimated against a
  residual that already has everything prior removed, so a too-small event comes out as ~0 with
  honest uncertainty instead of polluting other parameters. Same logic would let events a few days
  apart (not a true same-day/location collision) be handled without exclusion: the coseismic offset
  can be estimated from the sparse window between them, while postseismic decay stays undetermined
  until enough data exists. The existing `is_collision`/10-day-window logic for genuine collisions
  is unaffected.

  This is a complementary candidate to direct-injection, not a replacement — worth comparing both
  before committing to an implementation if/when this is picked back up.
