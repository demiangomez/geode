# Physics-Constrained Interpolation of Coseismic Deformation Fields

## Overview

This document describes the methodology for interpolating coseismic surface displacements
using a physics-constrained extension of the Sandwell and Wessel (2016) vector interpolation
method. The horizontal (East, North) and vertical (Up) components are handled by different
interpolation techniques but share a common Okada-based physical constraint.

The goal is to replace the existing pure-dislocation-model prediction in `CoseismicConstraint`
with a hybrid method that:

1. Fits the observed GNSS coseismic displacements at station locations
2. Guides the interpolated field toward the expected elastic dislocation pattern using
   a soft projection constraint derived from Okada (1985)
3. Produces physically meaningful predictions at unobserved grid locations

---

## Coordinate System and Projections

All computations are performed in a local Cartesian coordinate system centered at the
earthquake epicenter, obtained via azimuthal equidistant projection:

```python
# x = East (km), y = North (km), origin = epicenter
c = np.arccos(np.sin(lat0)*np.sin(lat) + np.cos(lat0)*np.cos(lat)*np.cos(lon - lon0))
k = c / np.sin(c) * 6371
x = k * np.cos(lat) * np.sin(lon - lon0)
y = k * (np.cos(lat0)*np.sin(lat) - np.sin(lat0)*np.cos(lat)*np.cos(lon - lon0))
```

A rotation matrix `R` aligns the coordinate system with the fault strike so that Okada
computations are performed in the fault frame (x-axis = along-strike):

```python
R = np.array([[np.sin(strike),  np.cos(strike)],
              [-np.cos(strike), np.sin(strike)]])
# fault-frame coordinates
tx, ty = R @ np.vstack([x, y])
```

Okada outputs `(ux, uy, uz)` in the fault frame (along-strike, across-strike, up).
Rotating back to geographic frame with `R.T` yields `(East, North, Up)`.

---

## Fault Geometry

Fault dimensions are estimated from Wells and Coppersmith (1994):

```python
along_strike = 10.**(-3.22 + 0.69 * magnitude) * 1000  # m -> km with inflation
down_dip     = 10.**(-1.01 + 0.32 * magnitude) * 1000  # m -> km with inflation
avg_disp     = 10.**(-4.80 + 0.69 * magnitude)         # m
```

The fault plane is discretized into a grid of rectangular patches:

```python
L1, L2 = -along_strike/2, along_strike/2   # along-strike extent
W1, W2 = -down_dip/2,     down_dip/2       # down-dip extent
```

The fault plane is selected from the two nodal planes of the moment tensor by inverting
the GNSS coseismic offsets for each candidate plane and selecting the one with smaller
IQR of residuals.

---

## Horizontal Component: Sandwell-Wessel Interpolation with Okada Constraint

### SW Green's Function Matrix

The SW method (Sandwell & Wessel 2016) models the deformation field as the response
of a thin elastic sheet to a set of vector body forces located at the station positions.
The Green's functions (their Eq. 8, with Poisson ratio `v`) are:

```python
q(r) = (3-v)*ln(r) + (1+v)*dy^2/r^2   # couples fx -> u
p(r) = (3-v)*ln(r) + (1+v)*dx^2/r^2   # couples fy -> v
w(r) = -(1+v)*dx*dy/r^2               # cross-coupling
```

where `r = sqrt(dx^2 + dy^2) + reg` (with regularization offset `reg` to avoid
singularities at coincident stations). The regularization offset is set to:

```python
reg = max(8, median_nn_spacing * 0.5)  # km
```

The design matrix for N stations is assembled as:

```python
A = np.block([[q, w],   # 2N x 2N
              [w, p]])
L = np.concatenate([east, north])  # 2N x 1 observations
```

### Okada Projector Construction

The Okada constraint is built from a set of K patches, each contributing two columns
to the design matrix `Dok` — one for pure strike-slip and one for pure dip-slip.
This decouples the constraint from any assumed rake:

```python
# For each patch i:
ux_ss, uy_ss, _ = okada(..., B1=ad, B2=0,  B3=0)  # pure strike-slip
ux_ds, uy_ds, _ = okada(..., B1=0,  B2=ad, B3=0)  # pure dip-slip

# rotate back to geographic frame
T_ss = R.T @ np.vstack([ux_ss, uy_ss])
T_ds = R.T @ np.vstack([ux_ds, uy_ds])

# stack East and North, two columns per patch
Dok[:, 2*i]   = np.concatenate([T_ss[0], T_ss[1]])  # 2N x 1
Dok[:, 2*i+1] = np.concatenate([T_ds[0], T_ds[1]])  # 2N x 1
```

Columns are normalized to unit L2 norm before forming the projector:

```python
norms = np.linalg.norm(Dok, axis=0)
Dok   = Dok / norms
```

The orthogonal projector onto the complement of the Okada subspace is:

```python
Pok = np.eye(2*N) - Dok @ np.linalg.solve(Dok.T @ Dok, Dok.T)
```

`Pok` has the properties: `Pok^2 = Pok` (idempotent), `Pok^T = Pok` (symmetric),
`Pok @ Dok = 0` (annihilates the Okada subspace). Its physical interpretation is:
for any vector `v` in observation space, `Pok @ v` is the component of `v` that
cannot be explained by any linear combination of Okada patch responses.

### Constrained Normal Equations

The body forces `f` are estimated by minimizing:

```
min_f  ||A*f - L||^2  +  alpha * ||Pok * A * f||^2
```

The first term enforces fit to the GNSS observations. The second term penalizes
the component of the SW-predicted field that lies outside the Okada subspace —
it does not enforce a specific displacement value, only the spatial pattern.

The normal equations are:

```python
P   = alpha * A.T @ Pok @ A
LHS = A.T @ A + P
RHS = A.T @ L
f   = np.linalg.solve(LHS, RHS)
```

The constraint weight `alpha` is set so that the constraint term is approximately
5-10 times larger than the data term:

```python
scale_AA  = np.linalg.norm(A.T @ A,   'fro')
scale_Pok = np.linalg.norm(A.T @ Pok @ A, 'fro')
alpha     = 5 * scale_AA / scale_Pok
```

### Patch Count Selection

The number of patches is controlled by two criteria applied together:

1. **Physical resolution**: patches must be larger than the median nearest-neighbor
   station spacing (patches smaller than the station spacing cannot be resolved):
   ```python
   K_strike = min(3, max(1, int(along_strike / d_avg)))
   K_dip    = min(3, max(1, int(down_dip    / d_avg)))
   ```

2. **Hard cap at 3 per dimension**: beyond 3×3 = 9 patches the Okada subspace
   becomes rich enough to contain any smooth field, neutralizing the constraint.
   The effective patch count satisfies `2*K < N/2` to ensure `rank(Pok) >= N - K`
   is large enough to meaningfully constrain the solution.

### Forward Prediction

Once the body forces `f` are estimated, the displacement at any grid location `p` is:

```python
u_p = Ap @ f
```

where `Ap` is the SW Green's function matrix evaluated between grid points and station
locations (using the same Green's functions but with grid points as targets).

### Weighting Coefficients for ETM Stacker

The constraint coefficients used in the ETM stacker follow directly from the
pseudo-inverse interpretation. The body force solution is:

```python
f = A_dagger @ L,   where  A_dagger = (A.T @ A + P)^{-1} @ A.T
```

The prediction at a grid point `p` with forward matrix `Ap` is:

```python
L_p = Ap @ f = (Ap @ A_dagger) @ L
```

so the weighting coefficients are `W = Ap @ A_dagger`. For the East component only:

```python
# q_p is the East block of Ap (rows 0:n_grid)
A_dagger = np.linalg.solve(A.T @ A + P, A.T)
W_east   = q_p @ A_dagger   # n_grid x 2N
W_north  = w_p @ A_dagger   # n_grid x 2N
```

These replace the existing dislocation-only weighting coefficients `ke`, `kn` in
`compute_constraint_coefficients`.

---

## Vertical Component: Scalar Spline with Okada Constraint

The vertical component uses a scalar biharmonic spline (`spline2d`) rather than the
vector SW method, since the SW formulation is inherently 2D horizontal.

### Scalar Spline Green's Function Matrix

The scalar spline Green's function (Wessel & Bercovici 1998) for tension parameter `t`:

```python
# coordinate scaling for numerical stability
xsc  = max(x) - min(x);  ysc = max(y) - min(y)
x_sc = x / xsc;          y_sc = y / ysc

# length scale (diagonal of output domain / 50)
length_scale = np.sqrt((max(xp_sc)-min(xp_sc))**2 + (max(yp_sc)-min(yp_sc))**2) / 50
p_tens       = np.sqrt(t / max(1-t, eps)) / length_scale

# Green's function matrix (N x N)
A_z[i, :] = spline2dgreen(abs((x_sc[i] - x_sc) + 1j*(y_sc[i] - y_sc)), p_tens)
```

No detrending is applied — coseismic displacements are spatially localized with no
secular planar trend, so detrending removes genuine signal.

### Vertical Okada Projector

The vertical projector follows the same logic as horizontal but uses only the vertical
component of Okada output `uz`. Independent strike-slip and dip-slip columns are used:

```python
_, _, uz_ss = okada(..., B1=ad, B2=0,  B3=0)  # pure strike-slip vertical
_, _, uz_ds = okada(..., B1=0,  B2=ad, B3=0)  # pure dip-slip vertical

Dok_z[:, 2*i]   = uz_ss  # N x 1
Dok_z[:, 2*i+1] = uz_ds  # N x 1
```

The projector `Pok_z` is `N x N` (scalar observation space):

```python
norms_z = np.linalg.norm(Dok_z, axis=0)
Dok_z   = Dok_z / norms_z
Pok_z   = np.eye(N) - Dok_z @ np.linalg.solve(Dok_z.T @ Dok_z, Dok_z.T)
```

### Vertical Patch Count

The vertical system has N observations (scalar, not vector), so the patch count
constraint is stricter:

```python
K_max_z  = N // 4          # each patch adds 2 columns (SS + DS)
K_side_z = max(1, min(2, int(np.sqrt(K_max_z))))  # hard cap at 2 per dimension
```

The singular value spectrum of `Dok_z` should be inspected to confirm the effective
rank before proceeding — for small N (< 20 stations) even a single patch may be
sufficient.

### Force Balance Constraint

The scalar spline Green's function `r^2(ln r - 1)` grows without bound, so without
regularization the field can diverge logarithmically far from the fault. The force
balance constraint `sum(alpha_z) = 0` guarantees decay to zero at infinity:

```python
C_z  = np.ones((1, N))
mu_z = 1e4 * norm(A_z.T @ A_z, 'fro') / N   # near-hard constraint
```

### Vertical Constrained Normal Equations

```python
alpha_z = 5 * norm(A_z.T @ A_z, 'fro') / norm(A_z.T @ Pok_z @ A_z, 'fro')

LHS_z     = A_z.T @ A_z + alpha_z * A_z.T @ Pok_z @ A_z + mu_z * C_z.T @ C_z
RHS_z     = A_z.T @ L_z
alpha_z_w = np.linalg.solve(LHS_z, RHS_z)   # N x 1 spline weights
```

### Vertical Weighting Coefficients for ETM Stacker

The vertical weighting coefficients follow directly from the spline pseudo-inverse:

```python
A_dagger_z = np.linalg.solve(A_z.T @ A_z + alpha_z * A_z.T @ Pok_z @ A_z
                              + mu_z * C_z.T @ C_z,  A_z.T)
W_up = Ap_z @ A_dagger_z   # n_grid x N
```

where `Ap_z` is the vertical spline Green's function matrix evaluated at grid locations.

---

## Combined System

The horizontal and vertical systems can be solved jointly using block-diagonal structure:

```python
from scipy.linalg import block_diag

A_joint   = block_diag(A,   A_z)          # (3N x 3N)
Pok_joint = block_diag(Pok, Pok_z)        # (3N x 3N)
L_joint   = np.concatenate([east, north, up])  # 3N x 1

P_h = alpha   * A.T   @ Pok   @ A      # 2N x 2N
P_z = alpha_z * A_z.T @ Pok_z @ A_z   # N x N

LHS_joint = A_joint.T @ A_joint + block_diag(P_h, P_z)
RHS_joint = A_joint.T @ L_joint
f_joint   = np.linalg.solve(LHS_joint, RHS_joint)

f_h = f_joint[:2*N]   # horizontal body forces
f_z = f_joint[2*N:]   # vertical spline weights
```

`alpha` and `alpha_z` are kept independent to allow separate tuning of the
horizontal and vertical constraint strengths.

---

## Integration into ETM Stacker

### What Changes in `CoseismicConstraint`

The existing `compute_constraint_coefficients` method uses a pure Okada dislocation
model as the prediction kernel (stored in `self.dislocation_model`). This is replaced
by the SW+Okada constrained interpolation:

**Current approach** (to be replaced):
- Computes Okada prediction `ae, an, au` at station locations
- Solves `(ae.T @ ae + smoothing) @ be = ae.T` (separate per component)
- Returns `ke = ae[target, :] @ be` as weighting coefficients

**New approach**:
- Builds SW design matrix `A` (horizontal, `2N x 2N`) and `A_z` (vertical, `N x N`)
- Builds Okada projectors `Pok` (`2N x 2N`) and `Pok_z` (`N x N`)
- Solves the constrained normal equations to obtain `A_dagger` and `A_dagger_z`
- Returns weighting coefficients `W_east`, `W_north`, `W_up` as `Ap @ A_dagger`
  and `Ap_z @ A_dagger_z`

### What Changes in `GridSystem`

The methods `compute_horizontal_grid_interpolant` and `compute_vertical_grid_interpolant`
currently return SVD-based interpolation weights for the postseismic field. For the
coseismic case, `predict_coseismic` should call the new SW+Okada constrained version.

A new method `compute_sw_okada_interpolant` should be added to `GridSystem`:

```python
def compute_sw_okada_interpolant(self, stations, event, mask):
    """
    Returns (W_east, W_north, W_up) weighting matrices of shape (n_grid x N_stations)
    using the SW+Okada constrained interpolation.
    Replaces the dislocation-model-only prediction for coseismic fields.
    """
```

### Parameters to Expose

The following parameters should be added to the stacker configuration or to
`CoseismicConstraint`:

| Parameter | Default | Description |
|---|---|---|
| `alpha` | auto (5x ratio) | Horizontal Okada constraint weight |
| `alpha_z` | auto (5x ratio) | Vertical Okada constraint weight |
| `mu_z` | auto (1e4 * scale / N) | Vertical force balance weight |
| `t_spline` | 0.1 | Spline tension for vertical |
| `poisson_ratio` | 0.5 | Poisson ratio for SW Green's functions |
| `reg_offset` | max(8, d_avg/2) | SW regularization offset (km) |

### Duplicate Station Handling

Before building any design matrix, stations closer than 0.1 km are identified and
one of each pair is removed to prevent rank deficiency in `A`:

```python
# find and remove duplicate stations
nn_dist = cdist(coords, coords)
np.fill_diagonal(nn_dist, np.inf)
is_dup = np.any(nn_dist < 0.1, axis=1) & (np.argmin(nn_dist, axis=1) < np.arange(N))
stations = [s for i, s in enumerate(stations) if not is_dup[i]]
```

---

## Key Properties and Constraints

- The Okada constraint is **scale-free**: it enforces the spatial pattern of the
  displacement field without assuming any specific slip magnitude. The projector
  `Pok` is invariant to the amplitude of the Okada predictions used to build it.

- The constraint uses **independent strike-slip and dip-slip columns** per patch,
  rather than fixing the rake from the moment tensor. This allows the effective rake
  to vary spatially while still being guided by the fault geometry.

- The horizontal field has **no force balance constraint** applied — far-field GNSS
  stations carry non-zero tectonic plate motion, so requiring zero net body force
  would bias the interpolation.

- The vertical field **requires force balance** (`sum(alpha_z) = 0`) because the
  scalar biharmonic spline grows logarithmically without it, and coseismic vertical
  displacements genuinely decay to zero far from the fault.

- The constraint strength is determined by the **ratio of normal equation term
  magnitudes** rather than by a fixed value, making it robust to changes in network
  size, station spacing, and fault geometry.
