# Assessing the SW-Okada regularization weights

- **Date:** 2026-11-26
- **Branch:** stacker_refactor

## Summary

Swept the postseismic SW-Okada regularization weights on the Ecuador stack against a
held-out criterion. The horizontal weight helps and `h = 5` is at the optimum; the vertical
weight *hurts* and should be 0, not the 5 currently used. The global variance of unit
weight is insensitive to both, which is why these were never tunable before. The field's
failure to decay with distance is structural, not a weight setting.

## Files / modules affected

- `geode/etm/stacker/solver.py` — `solve` now keeps `obs_vpv`, `constraints_vpv`, `dof`.

Sweep script: `okada_sweep.py` (session scratch, not committed — it reads a pickled
session by path).

## Architectural decisions

**`h_weight` and `v_weight` are exactly separable, so two 1-D sweeps replace a grid
search.** `fault_geometry._compute_sw_okada_system` builds `a = block_diag(ah, av)` and
`p = block_diag(P_h, P_z)` with `h_weight` only in `P_h` and `v_weight` only in `P_z`, so
the regularized inverse is block diagonal: `ke`/`kn` depend only on `h`, `ku` only on `v`.
The stacker then solves E/N/U as three independent blocks. Confirmed empirically — across
the whole `v` sweep the E and N out-of-sample residuals move by a mean of +0.000.

**The tuning criterion has to be out-of-sample.** `sqrt(variance of unit weight)` is
**1.0117 at every weight tried**, and the observation vᵀPv moves by 6.6 parts in 133 400
(0.005%): 131 004 observation equations simply drown ~300 constraint equations. The
criterion used instead is the sparse-station prediction residual with
`sparse_constraints_enabled` forced False, so `add_sparse_contributions` is a no-op while
the free fits built earlier remain — making `sparse_misfit` a genuine held-out test on 23
stations that contributed nothing to the solution.

## Tests executed and results

`pytest geode/tests` — 241 passed, 184 skipped, 0 failed.

Horizontal sweep (`v = 5` fixed), out-of-sample E/N prediction increment, far-field decay
slope of log|Vh| against log(distance from epicentre), and count of grid points within
30 km of a station where |Vh| falls below half its own neighbourhood:

| h | oos E/N | decay | sinks |
|---|---|---|---|
| 0 | 3.11 | +0.05 | 4 |
| 0.5 | 2.77 | −0.10 | 3 |
| 1 | 2.56 | −0.14 | 4 |
| 2 | 2.51 | −0.16 | 2 |
| **5** | **2.50** | −0.16 | **1** |
| 10 | 2.59 | −0.17 | 1 |
| 25 | 2.64 | −0.17 | 1 |
| 100 | 2.64 | −0.17 | 1 |

Vertical sweep (`h = 5` fixed), out-of-sample U:

| v | 0 | 0.5 | 1 | 2 | 5 | 10 | 25 | 100 |
|---|---|---|---|---|---|---|---|---|
| oos U | **2.48** | 2.58 | 2.54 | 2.61 | 2.84 | 2.77 | 2.77 | 2.77 |

Paired per-station check, `v = 5` against `v = 0` at `h = 5`:

```
E: n=23  2.50 -> 2.50  improved at 11/23  mean change +0.000
N: n=20  3.00 -> 3.00  improved at  2/20  mean change +0.000
U: n=23  2.84 -> 2.48  improved at 18/23  mean change -0.397
   col.popa 5.95 -> 2.17   col.neva 4.45 -> 2.52   col.cali 2.86 -> 1.11
   worst: ecu.esmr 2.85 -> 4.89   igs.riop 4.74 -> 6.54
```

## Known issues / limitations

- **The field does not decay with distance, and no weight fixes it.** The slope of
  log|Vh| against log(r) saturates at −0.17 for any `h ≥ 2`, where an elastic far field
  should be nearer −2. Two structural reasons, both in `_compute_sw_okada_system`:
  `P_h = h_weight_eff * ah.T @ Pok @ ah` evaluates the Okada annihilator at *station*
  coordinates, so it constrains the field only where stations are and says nothing about
  the grid far field; and the horizontal block has no analogue of the vertical block's
  `mu_z * C_z.T @ C_z` force-balance (`sum(alpha) = 0`, commented "field decays at
  infinity") at `mu_z = 1e4`. Raising `h` cannot substitute for either.
- One network, one catalogue, one relaxation (0.5). The optimum is broad (2-10 for `h`),
  so the exact value is not the point, but the *sign* of the vertical result should be
  re-checked elsewhere before changing a shipped default.
- The coseismic Okada weights were not swept; only postseismic.
- The out-of-sample metric mixes all three field types (a sparse station is predicted from
  interseismic + coseismic + postseismic together). Only the postseismic weights changed,
  so the differences are attributable, but the absolute level is not a postseismic-only
  number.

## Next steps

1. **Use `-postseismic_okada_v_weight 0`.** 18 of 23 held-out stations improve and the
   median drops 13%. Worth a second network before changing `EtmStackerConfig`'s default.
2. **Give the horizontal block a far-field condition.** Either evaluate the annihilator on
   the grid rather than at stations, or add the horizontal counterpart of `mu_z C_z' C_z`.
   This is what the "does not decay with distance" complaint actually needs.
3. **Add a legend to the interactive map.** Black `s=10` markers are full stations, lime
   `s=20` are sparse (`field_visualizer.py:367` and `:390`); nothing on the figure says so.
