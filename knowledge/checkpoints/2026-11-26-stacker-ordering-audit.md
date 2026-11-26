# Stacker audit: parameter/station ordering and ETM rebuilds

- **Date:** 2026-11-26
- **Branch:** stacker_refactor

## Summary

A visibly wrong map — two stations 0 m apart drawn with velocity vectors 4.4 mm/yr apart —
turned out to be the interseismic field being interpolated with every velocity attached to
another station's coordinates. A systematic sweep for that class of defect across the
stacker found a second instance in the plot path and, separately, a third instance of an
unrelated root cause (a config-driven ETM rebuild re-admitting dropped postseismic terms)
that made the add/remove-event path produce a NaN variance of unit weight.

## Files / modules affected

- `geode/etm/stacker/data_classes.py` — `create_field` no longer takes a station list.
- `geode/etm/stacker/solver.py` — matching call site.
- `geode/etm/stacker/diagnostics.py` — `_parameter_columns`.
- `geode/etm/stacker/earthquake_catalog.py` — `_recompute_affected_stations` rewritten.
- `geode/etm/stacker/stacker.py` — sparse stations get `projected_coords`.

## Architectural decisions

**A parameter array travels with the list it is indexed by, never with a parallel one.**
`get_parameters_and_covariance` indexes `params` by `constraint.station_list`. Any consumer
that pairs those parameters with a different list of the same length is wrong in a way that
produces plausible-looking numbers. Two mechanisms caused this:

- Passing a station list alongside the parameters (`create_field(self.stations, ...)`). The
  argument is now gone; `create_field` reads `constraint.station_list` for both branches,
  as the seismic branch already did.
- Using a boolean `np.isin` mask to scatter ordered values (`diagnostics`). A mask fills in
  the *target's* order, so it silently transposes whenever the two orders differ.
  `_parameter_columns` returns an explicit `(cols, keep)` index pair instead.

**One root cause, three symptoms: a config-driven ETM rebuild re-admits dropped events.**
`build_etm` derives its jump table from `earthquakes_cherry_picked`;
`drop_unconstrainable_postseismic` edits the ETM objects that exist when it runs. Anything
that rebuilds an ETM afterwards gets the dropped decay columns back. Found in
`_run_constrained_etm` (blank plots, fixed `6b68e31`/`416da53`), in a sparse station's free
fit (never subject to the drop at all), and now in `_recompute_affected_stations`. Each site
fixes it locally; the real fix is upstream — see the `post_seismic_back_lim` entry in
`known-issues.md`.

## Tests executed and results

`pytest geode/tests` — 241 passed, 184 skipped, 0 failed, unchanged throughout.

Runtime audit against a solved Ecuador stack (`-s_score 6.5 -is_inflation 4 -sparse
-sparse_constraint_mode`), after the fixes:

| check | result |
|---|---|
| A. `params`/`covariance` vs `station_list` order, all 17 constraints | all OK, 0 mismatches |
| B. `field.constrained_parameters` vs `constrain_stations`, all 15 fields | all OK, worst 0.0000 mm |
| C. every constraint equation targets its own station's column | all OK, 0 bad rows |
| D. field interpolated at each constraining station vs its own parameter | interseismic median 0.096, max 1.668 mm |
| E. structural invariants (range tiling, neq/station aliasing, shapes) | OK at every mutation step |

Before the fix, check B failed for 28 of 31 stations on the interseismic field; field
interpolation rms went 2.2 mm -> 0.1 mm.

Round trips, both exact to 0.0000 mm/yr after the fix:

```
remove_earthquake us7000fxq2 + solve, then add back + solve
  before:  339 -> 366 -> 377 params, sqrt(var) 1.019 -> 1.021 -> nan, |V|max 2788.68
  after:   339 -> 328 -> 339 params, sqrt(var) 1.019 -> 1.020 -> 1.019, |V|max 14.35
remove_station ecu.chec + rebuild + solve, then add back
  339 -> 329 -> 339 params, sqrt(var) 1.019 throughout
```

Read and found correct, no change needed: `separability.velocity_inflation` and
`_station_block` (global columns via an explicit `component * tp` offset);
`adaptive_weight._entangled_columns`/`_effective_diag` (local columns via
`- parameter_start_idx`); `solver.solve`'s per-station WRMS loop (ENU-consistent);
`sparse.reduce_local_system`, `_collapse_to_tempered_constraint` and `sparse_misfit` (all
consistently indexed by the `fields` list); `interseismic._build_k_matrix` and
`_get_target_cols`; `grid_system.interpolate_field`'s covariance block extraction.

## Known issues / limitations

- `interpolate_field` and `predict_seismic_deformation` both return
  `concatenate((en_cov, nu_cov, eu_cov))`, while their docstrings say `"en, e-u, n-u"` and
  `"en, eu, nu"`. Harmless today — `field_visualizer` reads index 0 only — but both
  docstrings are wrong about the last two and should be corrected.
- The audit covered ordering, indexing and structural consistency. It did not cover the
  numerical content of the Okada/SW regularization, nor whether any interpolation is
  *geophysically* right — only that the right number reaches the right station.
- Seismic fields reproduce their stations far less tightly than the interseismic one
  (postseismic medians 1.4-11.7 mm against 0.096). That is expected from the Okada
  regularization at weight 5/5 rather than evidence of a defect, but it has not been
  verified against an unregularized run.

## Next steps

1. **Re-examine the interseismic field.** Every previous visual read of it, including the
   inland `Ve` sign reversal in
   [2026-11-25-interseismic-observability.md](2026-11-25-interseismic-observability.md),
   was made against a scrambled field and has to be redone.
2. **Give `post_seismic_back_lim` a per-station window**, which removes the need for all
   three drop-replay sites.
3. **Look at the sparse prediction residuals** now that the fields are correct: `col.boga`
   reports 3.03 mm own fit against 35.38 mm predicted in East, 240.75 mm in Up.
