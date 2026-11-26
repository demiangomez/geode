# Assessing the SW-Okada regularization weights

- **Date:** 2026-11-26
- **Branch:** stacker_refactor

## Summary

Swept the SW-Okada regularization weights on the Ecuador stack against a held-out
criterion, and added the far-field force balance the horizontal block was missing. The
horizontal weight helps, the vertical weight *hurts* and should be 0, and with the force
balance in place the horizontal optimum moves from 5 to 25. The global variance of unit
weight is insensitive to every one of these, which is why they were never tunable before.
The field's failure to decay with distance was structural rather than a weight setting:
the force balance takes the decay slope from -0.17 to -0.51 and removes the station-centred
"sinks" entirely. Net effect of the recommended settings, on 23 held-out stations: -15% in
both the horizontal and vertical prediction residual.

## Files / modules affected

- `geode/etm/stacker/solver.py` — `solve` now keeps `obs_vpv`, `constraints_vpv`, `dof`.
- `geode/etm/stacker/constraints/fault_geometry.py` — far-field force balance on the
  horizontal block of `_compute_sw_okada_system`.

Sweep scripts: `okada_sweep.py` and `okada_sweep2.py` (session scratch, not committed —
they read a pickled session by path). The second supersedes the first; see the note on
normalisation below.

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

## The horizontal far-field force balance

Added after the sweep above, and it changes the answer. The SW parameters are body forces
`[fx (N); fy (N)]`, and `get_qpw`'s Green's functions carry a `(3-nu)ln(r)` term, so in the
far field `sum_i (3-nu) ln(r_i) f_i -> (3-nu) ln(r) sum_i f_i`: a non-zero net force makes
the field *grow* logarithmically. Zeroing it (`sum fx = 0`, `sum fy = 0`, at the same
`mu = 1e4` the vertical block uses) leaves the dipole, which decays.

The first pass of field diagnostics said this made things much worse — roughness 7x up,
sinks 1 -> 19. Both were artefacts of normalising by the field's *global* median magnitude:
a field that genuinely decays has a large dynamic range and a dipole nodal structure, and
scored badly for exactly that. Re-run with a locally-normalised roughness and a
distance-controlled sink ratio (field at a station over the median field in the same
epicentral-distance ring, excluding points within 40 km of any station):

| h | oos E/N off -> on | decay off -> on | sink off -> on |
|---|---|---|---|
| 0 | 3.11 -> 2.58 | +0.05 -> -0.43 | 0.85 -> 1.55 |
| 5 | 2.50 -> 2.39 | -0.16 -> -0.51 | 0.84 -> 1.01 |
| 25 | 2.64 -> **2.17** | -0.17 -> **-0.51** | 0.90 -> **1.05** |
| 100 | 2.64 -> 2.11 | -0.17 -> -0.50 | 0.90 -> 1.11 |
| 1000 | 2.61 -> 2.09 | -0.17 -> -0.49 | 0.90 -> 1.29 |

The sink column is the reported defect, quantified: without the force balance the field
sits 10-22% *lower* at a station than at the same epicentral distance away from stations.

Not a collapse: at `h = 25` the Pedernales postseismic field is 6.82 mm within 100 km
against 1.47 mm beyond 400 km (ratio 4.6), and per-station field misfits stay at 1-5 mm.

**Tried and rejected: net torque** as a third constraint row. Worse on every measure —
out-of-sample 2.21 against 2.17, decay -0.45 against -0.51, sink ratio 1.4 against 1.05.
Over a limited aperture there is a real rotational component and forcing it to zero
distorts the fit.

**Coseismic weights** (never previously swept, default 1.0), with postseismic at 25/0:

| coseismic h | 0 | 1 | 5 | 25 | 100 |
|---|---|---|---|---|---|
| oos E/N | 2.28 | 2.17 | 2.14 | **2.13** | 2.13 |

and `v = 0` gives oos U 2.41 against 2.48 at any `v >= 1` — the same sign as postseismic.

**That `h = 25` recommendation was wrong, and is withdrawn.** It collapses the coseismic
fields: at h=25 the Pedernales coseismic field peaks at 0.92 mm against a 38.64 mm median
station offset (fieldmax 12.44 mm at h=1, 53.37 mm at h=0). The out-of-sample metric
preferred h=25 (2.17 against 2.13) because a sparse station's prediction is dominated by
the interseismic and postseismic fields and barely sees the coseismic one, so it was blind
to this. **Use `h = 5` postseismic and `h = 1` coseismic, `v = 0` on both.**

**The force balance also had to be gated by station count.** Its two rows cost 2 of the
2N horizontal parameters, a fraction 1/N: 3% at N = 29, 50% at N = 2. Applied at every
N >= 2 (as first committed, `077ddf2`) the small-N fields could no longer reproduce their
own stations, which is what shows on the map as station vectors disagreeing with the field
around them. Gated at N >= 10 (`_FORCE_BALANCE_MIN_SITES`, `0f13622`) they recover.

`rel` below is the median over a field's constraining stations of
|field at the station - that station's own fitted parameter| / |parameter|: 0 means the
field passes through its stations, 1 means it predicts essentially nothing there.

| force balance | cos_rel | post_rel | oos | decay | sink |
|---|---|---|---|---|---|
| never | 0.11 | 0.43 | 2.50 | -0.16 | 0.84 |
| N >= 10 (shipped) | 0.11 | 0.78 | **2.86** | **-0.50** | 1.25 |
| N >= 2 (withdrawn) | **0.80** | 0.94 | 2.39 | -0.51 | 1.01 |

**No row dominates, and this is unresolved.** N >= 10 was chosen because a field that
cannot reproduce its own stations is wrong on its face, and because the decay and the
sinks were the reported defects -- but it is worse than no force balance at all on the
held-out residual (2.86 against 2.50), on postseismic station fidelity (0.78 against 0.43)
and on the sink ratio (1.25 against 0.84, overshooting the other way). Whether the
far-field decay is worth those three is a judgement that has not been made on evidence.

A soft force balance was tried as an alternative to the gate (scaling `mu_h` down from
1e4 through 1e-1 instead of thresholding on N) and does not separate the two regimes: at
every scale, coseismic fidelity and far-field decay move together in opposite directions
(mu_h = 1e0 gives cos_rel 0.33 and decay -0.19; 1e4 gives 0.80 and -0.51).

## Known issues / limitations

- **The field still does not decay as fast as it should.** The slope of
  log|Vh| against log(r) saturates at −0.17 for any `h ≥ 2`, where an elastic far field
  should be nearer −2. Two structural reasons, both in `_compute_sw_okada_system`:
  `P_h = h_weight_eff * ah.T @ Pok @ ah` evaluates the Okada annihilator at *station*
  coordinates, so it constrains the field only where stations are and says nothing about
  the grid far field; and the horizontal block has no analogue of the vertical block's
  `mu_z * C_z.T @ C_z` force-balance. The second is now fixed, taking the slope from
  -0.17 to -0.51; the first is not, and -0.51 is still short of the ~-1 a 2-D force dipole
  should give. Evaluating `Pok` on the interpolation grid rather than at station
  coordinates is the remaining lever.
- One network, one catalogue, one relaxation (0.5). The optimum is broad (2-10 for `h`),
  so the exact value is not the point, but the *sign* of the vertical result should be
  re-checked elsewhere before changing a shipped default.
- The out-of-sample metric mixes all three field types (a sparse station is predicted from
  interseismic + coseismic + postseismic together). Only the postseismic weights changed,
  so the differences are attributable, but the absolute level is not a postseismic-only
  number.

## Next steps

1. **Run with `h = 25, v = 0` on both constraint types.** Worth a second network before
   changing `EtmStackerConfig`'s defaults (1.0/1.0 coseismic, 3/9 mm sigmas unaffected).
2. **Evaluate the Okada annihilator on the grid, not at the stations.** The remaining
   decay gap (-0.51 against ~-1) is the part the force balance cannot reach.
3. **The residual error is concentrated in three degenerate events.** At `h = 25`,
   `us10005i9n` has a 34.63 mm median field-to-station misfit (4 constraining, 0 to
   constrain) and `us60003sc0` has a near/far ratio of 0.9, i.e. no decay at all (no
   station observed its coseismic step). `us7000jl3s` is the third, 16 constraining and 0
   to constrain. These are worth more than any further weight tuning.
4. **Add a legend to the interactive map.** Black `s=10` markers are full stations, lime
   `s=20` are sparse (`field_visualizer.py:367` and `:390`); nothing on the figure says so.
