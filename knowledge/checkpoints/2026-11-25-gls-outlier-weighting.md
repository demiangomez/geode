# GLS outlier weighting in the stacker, and two bugs it uncovered

- **Date:** 2026-11-25
- **Branch:** stacker_refactor

## Summary

The stacker's variance of unit weight had been stuck at ~1.37 across every weighting,
sigma and catalogue change tried. The cause was that making GLS the default (`7abdfba`)
silently removed outlier handling from the normal equations: the colored-noise covariance
is estimated from inlier residuals but was applied to every epoch, so the ~4% of epochs
each station's own robust fit had rejected entered the shared system at full weight.
Downweighting them takes `sqrt(variance of unit weight)` to 1.023 and the WRMS-increment
median below 1. With honest weights, the catalogue question the previous session had
closed reopened and reversed: more events now measurably improves the solution.

## Files / modules affected

Stacker:

- `geode/etm/stacker/normal_equations.py` — `_outlier_inflated_covariance`,
  `_OUTLIER_MAX_INFLATION`; `observation_weights` now read off the covariance diagonal.
- `geode/etm/stacker/etm_builder.py` — `sync_geophysical_jumps`.
- `geode/etm/stacker/diagnostics.py` — `_run_constrained_etm` calls it.

CLI:

- `com/EtmStacker.py` — `do_plot_etm` resolves its folder argument with a trailing
  separator.

No change to the ETM core or the noise model.

## Architectural decisions

**A diagnostic ETM is synced against the constraint list, not against a reference ETM.**
`_run_constrained_etm` rebuilds an `EtmEngine` from the stacker config, which re-admits
every event in `earthquakes_cherry_picked`. The first fix for this synced the rebuilt jump
table against the station's own `stn.etm`, which is right for a full station (the stack
demoted its unresolvable events in place) and wrong for a sparse one (`stn.etm` is a free
fit, never subject to the drop, so it still carries those events as `fit=True`). The
constraint list is the only reference that is correct for both, because it is built from
the events the stack still models: a geophysical jump with no constraint is deactivated,
and one whose constraint describes a different jump type adopts that type.

**Outliers are downweighted inside the covariance, not dropped from the design.** Two
options were on the table: mask the rejected rows out of `a`/`l`/`t_mjd` before building
the Toeplitz, or inflate the covariance diagonal at those epochs. Inflating was chosen
because the covariance is lag-indexed — deleting rows punches holes in the time spacing
that `build_toeplitz_covariance` then has to interpolate across — and because it
reproduces, inside the GLS branch, exactly what the diagonal branch had always done:
`obs_sigmas` keeps the robust fit's inflated sigma at rejected epochs, so
`diag(1 / obs_sigmas ** 2)` downweighted them for free.

The inflation is `clip(obs_sigmas ** 2 / noise_lag[0], 1, 1e6)`. Both bounds are load
bearing, measured on Ecuador:

```
components with at least one rejected epoch: 93
ratio obs_sigma(rejected) / sqrt(noise_lag[0]):
  min of mins    0.175          57 of 93 components have a ratio < 1 somewhere
  median of med  2.316
  max of maxes   3.9e10          1 of 93 would set a 1.5e21 variance spread
```

Without the lower bound a rejected epoch whose robust sigma came out under the noise
floor would be *up*weighted; without the upper bound one hard-rejected epoch leaves the
Toeplitz too ill-conditioned for the Cholesky.

**Co-location ties were examined and left alone.** `constrain_nearby_stations` ties
parameters of station pairs under 5 km at a hardcoded `constraint_sigma = 1e-4`, which
bypasses `h_sigma`/`v_sigma`, `adaptive_weight` and `update_weights` alike — the same
shape as the 1e-6 zero-tie disarmed earlier. Measured, it is not doing harm: removing
every such tie changes `sqrt(var)` from 1.0182 to 1.0183, i.e. it is not fighting the data
anywhere, while it resolves parameters the data is indifferent to (`igs.qui4` moves
1.8 mm/yr, `igs.qui3` 1.7, `bra.amta` 0.97 when it is removed). Kept as is; recorded here
so the hardcoded sigma is not rediscovered as a surprise.

## Tests executed and results

- `pytest geode/tests` — 241 passed, 184 skipped, 0 failed. Unchanged from before the
  work; skips are fixture-gated (`GEODE_NOISE_TEST_VECTORS`, `GEODE_CROSS_RAW_DATA`).
- Decomposition that located the cause, over all 93 GLS-fitted components:

  ```
  outlier fraction (E): median 3.95%   max 11.60% (ecu.cxec)
    all epochs : vpv/n = 1.8744  -> sqrt 1.369
    inliers    : vpv/n = 0.9932  -> sqrt 0.997
  ```

- Cold run from the database, `ecuador_stn.txt -s_score 7.3 -postseismic_okada_h_weight 5
  -postseismic_okada_v_weight 5`:

  | | before | after |
  |---|---|---|
  | sqrt(variance of unit weight) | 1.372 | 1.023 |
  | WRMS increment median | 1.14 | 0.97 |
  | WRMS increment max | 2.73 | 1.34 |

  Velocities move a median of 0.18 mm/yr (max 0.49, `ecu.qui2`) and sigmas fall by a
  uniform factor 0.742 — the a posteriori scaling no longer carrying the bogus inflation.
  The rejected epochs were inflating variance, not biasing parameters.

- Catalogue comparison at `-is_inflation 4`, cold from the database:

  | | `-s_score 7.3` | `-s_score 6.5` |
  |---|---|---|
  | sqrt(var) | 1.021 | 1.012 |
  | observation vᵀPv | 136 008.9 | 133 399.5 |
  | parameters | 903 | 1017 |
  | WRMS increment 90th / max | 1.04 / 1.26 | 1.00 / 1.15 |
  | interseismic-eligible | 23 of 31 | 11 of 31 |

- `plot_all` on the `-s_score 6.5` stack: 31 full + 23 sparse plots written, 0 failures,
  smallest plot 179 kB. An earlier pass of this check used a `-size -40k` threshold to find
  blank plots and reported 0 while four sparse stations (`col.abcc`, `col.abpd`, `col.gara`,
  `col.zarz`) were still blank at ~68-84 kB. Size alone is a poor blankness test; the
  smallest plot in the run is the number to look at.
- `plot_etm <station> <folder>` for two stations into one folder: both land inside it as
  `<station>_ppp.png`, no stray `<folder>.png`.

## Known issues / limitations

Four entries added to `known-issues.md` under "ETM Stacker: weighting and catalogue":
`post_seismic_back_lim` being an absolute `Date` rather than a per-station window; a
rank-deficient design deactivating every function rather than the offending columns; the
JSON cache quantizing `noise_covariance_lag` so `-load_json` and GLS are mutually
exclusive; and the stacker not using the inter-component covariance `master` estimates.

Two corrections to [2026-11-25-interseismic-observability.md](2026-11-25-interseismic-observability.md),
which was written before any of this was known:

- Its `sqrt(var)` figures predate the fix and are not comparable to current runs.
- The conclusion reached in that session that model specification error was *not* the
  cause of the residual misfit was wrong, and wrong for a specific reason: the test
  (lowering `-s_score` to 6.5) measured a 0.35% change in observation vᵀPv against a 37%
  systematic floor and was therefore unable to see anything. Repeated with the floor
  removed, the same test gives 1.9%, and `ecu.mhec` — the station that session singled out
  as being dragged by the stack rather than misfit by its own model — drops out of the
  worst-ten entirely once the missing events are present.

## Next steps

1. **Cross-check the fix with the other option.** Rebuild the noise kernel over surviving
   epochs only (option 1) instead of inflating. If that also lands near 1.00 it confirms
   the diagnosis independently of how the inflation was scaled.
2. **Give `post_seismic_back_lim` a per-station window in the stacker.** Both
   `drop_unconstrainable_postseismic` and `sync_geophysical_jumps` exist to clean up after
   an absolute 1990 floor handing decade-old decay columns to stations that came online
   long after. A window relative to each station's own start, or to the network validity
   epoch, removes the need for both.
3. **Revisit `-s_score` now that the comparison is meaningful.** 6.5 is better than 7.3 on
   every fit metric; what it costs is interseismic eligibility (23 of 31 down to 11 of 31,
   and 0 of 31 at the default `-is_inflation 0`). That trade is the observability problem
   from the previous checkpoint, not a stability one, and it is the thing to attack next.
