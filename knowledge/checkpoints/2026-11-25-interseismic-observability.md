# Interseismic observability in the ETM stacker

- **Date:** 2026-11-25
- **Branch:** stacker_refactor

## Summary

Chasing a visibly wrong interseismic field in the Ecuador stack led to a structural
finding rather than a bug: with a realistic earthquake catalogue, exactly one station in
that network (`ecu.qui2`) carries no postseismic term at all, so the interseismic field
is not observable independently of the postseismic model. Several mechanisms that had
been hiding this were fixed or disarmed along the way, and a criterion built to decide
which stations may define the field was measured, found not to settle the question, and
shipped disabled.

## Files / modules affected

Stacker:

- `geode/etm/stacker/constraints/base.py` — `allow_zero_tie`; zero-tie sigma now the
  constraint's own h/v sigma instead of a hardcoded `1e-6`; `_station_list` refreshed on
  every `collect_constraints` instead of snapshotting at construction.
- `geode/etm/stacker/constraints/interseismic.py` — `allow_zero_tie = False`.
- `geode/etm/stacker/constraints/separability.py` — **new**; `velocity_inflation`,
  `apply_interseismic_eligibility`.
- `geode/etm/stacker/constraints/registry.py` — `clear()`; `skip`/`quiet` on
  `collect_all_constraints`/`add_all_constraints`.
- `geode/etm/stacker/data_classes.py` — `postseismic_min_separability`,
  `interseismic_max_inflation`.
- `geode/etm/stacker/solver.py` — eligibility probe pass; stale-system guard.
- `geode/etm/stacker/stacker.py`, `etm_builder.py`, `earthquake_catalog.py` —
  re-entrant `build_system`, `system_stale`, per-station build records.

Core ETM (affects every caller, not just the stacker):

- `geode/etm/core/jump_manager.py`, `geode/etm/core/data_classes.py` —
  `postseismic_min_separability` and `_postseismic_is_separable`.

CLI: `com/EtmStacker.py`, `com/PlotETM.py`, `com/PrintVelocities.py`.

## Architectural decisions

**`is_interseismic` is a data-availability test doing a model-adequacy job.**
`etm_builder.create_station` flags a station interseismic when its first geophysical jump
postdates its first observation. That is a proxy for "is this station's velocity the
interseismic rate", and it fails in both directions:

- Too generous when a pre-onset decay column has been dropped from the fit. The column is
  gone but the transient is not, so the velocity absorbs its gradient and the station is
  promoted while carrying exactly the bias it then helps define the field with. In the
  `postseismic_min_separability = 0.01` configuration, 13 of the 17 interseismic stations
  were in this state.
- Saturates to zero eligible stations under a generous catalogue, because in a subduction
  zone nobody has clean pre-event data. Lowering `-s_score` from 7.3 to 7 — which improves
  the field in every other respect — took the eligible set from 3 to 0.

**A flat decay column is not a zero-slope column.** `postseismic_min_separability` measures
whether a pre-onset `log(1 + dt/tau)` column is distinguishable from a constant plus a
trend over the station's own span. That is the right question for a *standalone* ETM,
where nothing can recover an unresolvable column, and the measured gain is real
(`ecu.jnec` Vn 53.2 -> 8.7 mm/yr, sigmas 8x better). It is the wrong question *inside the
stacker*, where resolving what one station cannot is the entire point: deleting the column
moves its gradient into the velocity, and also removes the station from every postseismic
constraint, so the shared field loses the only route by which it could have corrected it.

**The separability criterion (`interseismic_max_inflation`) does not settle eligibility.**
`R = sqrt([N_block^-1]_vv * N_vv)` over {velocity} + {decay columns}, on the system with
co/postseismic ties applied but not the interseismic one. Measured on Ecuador with every
term present:

```
threshold  2 ->  1 of 31      ecu.qui2  1.00  (0 decay terms)
threshold  3 ->  2 of 31      ecu.qvec  2.87
threshold  5 -> 15 of 31      col.psto  4.26
threshold 10 -> 28 of 31      ecu.coec 15.93
threshold 20 -> 30 of 31      per.lb01 20.08
```

Two problems. There is no gap anywhere in the distribution, so the threshold picks the
answer rather than the data doing it. And the ordering inverts: the 2023 cohort scores
3.0-4.5 while `col.psto` and `ecu.coec` — the only two stations with genuine pre-event
data — score 4.26 and 15.93. R is a posterior quantity, so a station whose decay the
postseismic field pins at 1 mm looks well separated, while a station that helps *define*
that field does not. It credits the tie without asking whether the tie is right.

Shipped with `interseismic_max_inflation = 0` (old rule) and the numbers recorded beside
the default. Not promoted to an ADR: the conclusion is about this network's geometry as
much as about the method, and a second network should be measured before generalizing.

## Tests executed and results

- `pytest geode/tests` — 241 passed, 184 skipped, 0 failed. Skips are fixture-gated
  (`GEODE_NOISE_TEST_VECTORS`, `GEODE_CROSS_RAW_DATA`), neither present on this machine.
- Full CLI end to end on `ecuador_stn.txt` (`-s_score 7.3`, okada 5/5), with and without
  `-sparse -sparse_constraint_mode`: builds and solves, no tracebacks, 31 full / 23 sparse,
  sqrt(var) 1.074.
- `-ps_separability 0`: now raises `EtmStackerException` from `InterseismicConstraint`
  instead of silently pinning all 31 velocities to zero. This is the intended behaviour.
- Station add/remove round trip: remove `ecu.chec`, rebuild (20.3 s) + solve (7.4 s),
  survivors move median 0.04 / max 0.16 mm/yr; add it back and the velocities return to
  the original baseline to 0.000 mm/yr. The exact round trip is what shows the rebuild
  leaves nothing stale behind.

## Known issues / limitations

- **The interseismic field in this network is conditional on the postseismic model.** Only
  `ecu.qui2` is free of postseismic terms. Added to `known-issues.md`.
- **The visible defect is in the East component, not the magnitude.** The interseismic
  field on the grid is numerically clean (|Vh| 9.45-18.61 mm/yr, no point above 40 or
  below 3, smoother away from stations). What looks wrong on the map is `Ve` running +9.1
  at the coast to -2.4 inland; on a diverging ramp that is red-west / blue-inland. Inland
  cohort stations have Ve median -1.9 against +3.2 for inland non-cohort, i.e. roughly
  5 mm/yr of spurious westward motion in the Amazon foreland, where the field should be
  approaching stable South America. Small n (3 vs 5) — not yet conclusive.
- `max_condition_number` does not catch the collinearity it exists for: `ecu.jnec` reached
  a design matrix condition number of 7.0e6 against a threshold of 15 (and of 8 on the
  standalone path) without firing.
- Okada weights, constraint sigmas and the adaptive-weighting ratio were all tuned when
  station velocities had a 260 mm/yr median. None of that tuning is meaningful now.
- Sessions pickled before `BaseConstraint.is_zero_tied` existed load correctly (read via
  `getattr`), but sessions pickled before the `master` merge do not: `3aacd4b` renamed
  `etm_functions/stochastic_signal.py`/`StochasticSignal` to `stochastic.py`/`Stochastic`.
  A module alias is not enough — `Stochastic.p.params` changed shape — so old `.pkl`
  sessions must be rebuilt.

## Next steps

Three threads, in priority order:

1. **External constraint on the interseismic field (plate model).** The only option that
   breaks the circularity rather than relabelling it. The field is currently inferred
   entirely from inside the deforming zone; `bra.amta` at 1032 km is the sole station
   even arguably outside the postseismic footprint, and it scores R = 6.07.
2. **Honest, time-dependent uncertainty.** If the field cannot be determined
   independently, say so in the covariance rather than in a comment: the TPM is a local
   linearisation whose accuracy degrades away from the fitting window, and that should be
   reported as an epoch-dependent uncertainty rather than the single constant sigma a
   velocity parameter currently carries. This is worth doing *even if* thread 1 succeeds.
3. **Finish diagnosing the Ve sign reversal.** The 3-vs-5-station comparison above is
   suggestive, not conclusive. Confirm with the `-ps_separability 0` build (where the
   cohort's decay terms exist and are field-constrained) whether inland Ve returns toward
   zero. If it does, the bias is confirmed and quantified; if it does not, something else
   is driving the inland field.
