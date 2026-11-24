"""
Project: Geodetic Database Engine (GeoDE)
Author: Demian D. Gomez

Cross-component parameter covariance: Cov(x_i, x_j) = K_i C_ij K_j' for a pair of components (N-E,
N-U or E-U) of one station, using the power-law cross-spectrum model for C_ij.

Runs strictly after each component's own single-component noise model/GLS (noise_covariance.py) is
complete, and never changes it: every input here (t, v0, the design matrix, the step-1 estimator K0,
the fitted auto covariance function, the GLS estimator K and its covariance Qx) is read from that
component's own already-finished NoiseModelResult, fit independently on that component's own full set
of inlier epochs. The only new ingredient this module introduces is the *common* epoch mask (kept, not
flagged as an outlier, in every component that was fit) used to build the shared daily grid the cross
terms live on.

Reference: GeoDE_cross_covariance_spec.md / cross_component_covariance.pdf (pgamit/docs/geode_handoff),
ported line-for-line from the MATLAB prototype (pgamit/toy_arma/pair_cross_setup.m,
pair_cross_empcov.m, pair_cross_projavg.m, ecov_sigma_multi.m) and the real-data pipeline
(pgamit/PL_make_cross_test_vectors.m). Section numbers in the comments refer to the spec. Units:
mm/days, matching noise_covariance.py. Only the power-law cross-spectrum model (spec Section 6.2/6.3)
is implemented; the constant-coherence model (Section 6.1) is not (by design, not an oversight).

When the model can't be fit (switch off, missing per-component GLS estimator, or too few common
epochs), every pair falls back to Section 10.2 "borrowed residual correlation"
(Cov(x_i, x_j) = r * sigma_i * sigma_j, r = correlation of the two components' step-1 residuals at
the common epochs), generalized here from a single scalar (today, velocity-only, in
com/PrintVelocities.py) to an arbitrary parameter block.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
import scipy.fft
from scipy.linalg import solve_triangular, LinAlgError
from scipy.optimize import minimize

from .noise_covariance import NoiseModelResult, cholesky_jitter, dcorr_pln, fft_length, geo_lags

logger = logging.getLogger(__name__)

PAIRS: Tuple[Tuple[str, str], ...] = (('N', 'E'), ('N', 'U'), ('E', 'U'))


@dataclass
class CrossCovarianceSettings:
    """Settings of the power-law cross-spectrum model (spec Sections 6.2-6.4)."""
    # grid search over the cross-spectrum's own spectral index (Section 6.3)
    alpha_grid: Tuple[float, ...] = tuple(round(v, 4) for v in np.arange(0.25, 2.0001, 0.125))
    # periods of the two components matched as a cross periodic term when within this fraction (Section 6.3)
    periodic_tol: float = 0.02
    # cutting planes (Section 6.3/9.4): starting constraint frequencies (the first N linear, then
    # log-spaced up to the Nyquist-like grid), max refinement iterations
    cutting_plane_linear: int = 21
    cutting_plane_log: int = 80
    max_cutting_plane_iter: int = 20
    # Cholesky safety margin for the cross Sigma (relative to its mean diagonal), escalating jitter
    jitter_start: float = 1e-10
    jitter_factor: float = 10.
    jitter_max: float = 1.
    # below this many common epochs, don't attempt the power-law fit (too little data): fall back
    min_common_epochs: int = 50
    fft_workers: int = -1


@dataclass
class ComponentCrossInput:
    """What the cross module needs from one component's already-completed, untouched single-component
    fit (noise_covariance.estimate_noise_model), plus the absolute epoch of its own first inlier and
    the common-epoch mask (computed by the caller as the AND of every fitted component's own inlier
    flags, restricted to this component's own epoch ordering)."""
    label: str                     # 'N', 'E', or 'U'
    nm: NoiseModelResult
    mjd0: int                      # absolute mjd of nm.t[0] (this component's own first inlier epoch)
    pw: np.ndarray                 # bool (n,), aligned to nm.t: True where common to every component fit
    k_estimator: Optional[np.ndarray]  # GLS estimator K = Qx A' C^-1 (u x n); nm.gls.k_estimator


@dataclass
class CrossCovarianceResult:
    pair: str
    method: str                                            # 'power_law_cross_spectrum' | 'borrowed_residual_correlation'
    matrix: np.ndarray                                      # u_i x u_j parameter cross-covariance
    daily_residual_correlation: float = np.nan              # step-1 residual correlation at common epochs
    n_common_epochs: int = 0
    flags: List[str] = field(default_factory=list)
    alpha_x: float = np.nan
    theta_w: float = np.nan
    w_x: float = np.nan
    periodic_periods_d: np.ndarray = field(default_factory=lambda: np.array([]))
    periodic_coefs: np.ndarray = field(default_factory=lambda: np.array([]))
    residual_norm_grid: np.ndarray = field(default_factory=lambda: np.array([]))
    # plotting diagnostics (power_law_cross_spectrum only; empty for the fallback): all at signed lags
    # -Tmax..Tmax (ascending, days), matching noise_covariance.py's single-component 'diagnostics' block
    lag_days: np.ndarray = field(default_factory=lambda: np.array([]))
    gamma: np.ndarray = field(default_factory=lambda: np.array([]))         # empirical cross-covariance
    model: np.ndarray = field(default_factory=lambda: np.array([]))        # fitted model curve, same lags
    fitted_lag_days: np.ndarray = field(default_factory=lambda: np.array([]))  # the K lags actually fitted
    alpha_grid: np.ndarray = field(default_factory=lambda: np.array([]))   # alpha_x grid, pairs residual_norm_grid

    def summary(self) -> dict:
        """
        JSON-friendly report (spec Section 11): the fit diagnostics (alpha_x, theta_w, w_x, periodic
        terms, residual_norm_grid), always in mm^2 -- the units fit_pair's inputs are already in
        (NoiseCovarianceGLS scales observations to mm before any noise-model fit). Deliberately no
        scale parameter: there is no other unit these fields are ever stored in, so one would only
        ever be a double-conversion bug waiting to happen (as it briefly was: EtmFit.
        process_cross_covariance once called this with scale=1000, inflating theta_w/w_x by 1e6).
        The parameter cross-covariance matrix is not part of this summary at all -- it is converted
        separately, from mm^2 to GeoDE's native m^2, by dividing self.matrix by scale ** 2 (see
        EtmFit.process_cross_covariance).
        """

        def num(x):
            x = float(x)
            return x if np.isfinite(x) else None

        def nums(x):
            return [num(v) for v in np.asarray(x, dtype=float).ravel()]

        return {'pair': self.pair, 'method': self.method, 'n_common_epochs': self.n_common_epochs,
                'daily_residual_correlation': num(self.daily_residual_correlation),
                'alpha_x': num(self.alpha_x), 'theta_w': num(self.theta_w), 'w_x': num(self.w_x),
                'periodic': [{'period_d': num(p), 'coef': num(c)}
                            for p, c in zip(self.periodic_periods_d, self.periodic_coefs)],
                'residual_norm_grid': nums(self.residual_norm_grid) if self.residual_norm_grid.size else [],
                'flags': self.flags,
                'units': {'theta_w': 'mm^2 (white cross-variance)', 'w_x': 'mm^2 (power-law cross amplitude)',
                          'periodic.coef': 'mm^2'},
                # data of the diagnostic figure (empty dict for the fallback, nothing to plot): empirical
                # cross-covariance and the fitted model at every signed lag, the K lags actually fitted
                # (diamond markers), and the alpha_x grid search (paired with residual_norm_grid above)
                'diagnostics': ({'lag_days': nums(self.lag_days), 'gamma': nums(self.gamma),
                                'model': nums(self.model), 'fitted_lag_days': nums(self.fitted_lag_days),
                                'alpha_grid': nums(self.alpha_grid)} if self.lag_days.size else {})}


def _sg(x: np.ndarray, length: int, tmax: int) -> np.ndarray:
    """circular (length `length`) -> signed lags -tmax..tmax, ascending (pair_cross_setup.m's sg)"""
    return np.concatenate([x[length - tmax:], x[:tmax + 1]])


@dataclass
class _CrossGrid:
    Nc: int
    L: int
    Tmax: int
    wN: np.ndarray
    wE: np.ndarray
    Ncr: np.ndarray                 # pair counts at signed lags -Tmax..Tmax (ascending), length 2*Tmax+1
    FAN: np.ndarray
    FKN: np.ndarray
    FAE: np.ndarray
    FKE: np.ndarray
    KfE: np.ndarray
    XiNE: np.ndarray
    posN: np.ndarray
    posE: np.ndarray
    off_i: int                      # absolute-grid offset of ctx_i.nm.t / ctx_j.nm.t (shared day-zero
    off_j: int                      # = the earlier of the two components' own first inlier epoch)


def _place_on_grid(nm: NoiseModelResult, pw: np.ndarray, off: int,
                   Nc: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """pair_setup.m / pair_cross_setup.m: this component's own A/K0 placed on the common grid. The
    step-1 estimator K0 uses every one of this component's own epochs (unrestricted); the design
    matrix A and the observation indicator w are restricted to the epochs common to the whole pair"""
    pos = nm.t + off
    u = nm.k0.shape[0]
    af = np.zeros((Nc, u))
    af[pos[pw], :] = nm.a[pw, :]
    kf = np.zeros((Nc, u))
    kf[pos, :] = nm.k0.T
    w = np.zeros(Nc)
    w[pos[pw]] = 1.
    return pos, af, kf, w


def _setup_cross_grid(ctx_i: ComponentCrossInput, ctx_j: ComponentCrossInput) -> _CrossGrid:
    """pair_cross_setup.m: lag-pair structure of the two components on their common daily grid."""
    base = min(ctx_i.mjd0, ctx_j.mjd0)
    off_i, off_j = int(ctx_i.mjd0 - base), int(ctx_j.mjd0 - base)
    Nc = max(int(ctx_i.nm.t[-1]) + off_i, int(ctx_j.nm.t[-1]) + off_j) + 1
    Tmax = Nc // 2
    L = fft_length(Nc)

    posN, AfN, KfN, wN = _place_on_grid(ctx_i.nm, ctx_i.pw, off_i, Nc)
    posE, AfE, KfE, wE = _place_on_grid(ctx_j.nm, ctx_j.pw, off_j, Nc)

    Ncr = np.round(_sg(np.fft.irfft(np.conj(np.fft.rfft(wN, L)) * np.fft.rfft(wE, L), L), L, Tmax))

    FAN = np.fft.rfft(AfN, L, axis=0)
    FKN = np.fft.rfft(KfN, L, axis=0)
    FAE = np.fft.rfft(AfE, L, axis=0)
    FKE = np.fft.rfft(KfE, L, axis=0)

    uN, uE = AfN.shape[1], AfE.shape[1]
    XiNE = np.empty((2 * Tmax + 1, uN * uE))
    for m in range(uE):
        for k in range(uN):
            x = np.fft.irfft(np.conj(FAN[:, k]) * FAE[:, m], L)
            XiNE[:, m * uN + k] = _sg(x, L, Tmax)

    return _CrossGrid(Nc=Nc, L=L, Tmax=Tmax, wN=wN, wE=wE, Ncr=Ncr, FAN=FAN, FKN=FKN, FAE=FAE, FKE=FKE,
                      KfE=KfE, XiNE=XiNE, posN=posN, posE=posE, off_i=off_i, off_j=off_j)


def _empirical_cross_covariance(ctx_i: ComponentCrossInput, ctx_j: ComponentCrossInput,
                                grid: _CrossGrid) -> np.ndarray:
    """pair_cross_empcov.m (Section 3): gam_NE at signed lags -Tmax..Tmax (ascending). No mean removal."""
    a = np.zeros(grid.Nc)
    a[grid.posN[ctx_i.pw]] = ctx_i.nm.v0[ctx_i.pw]
    e = np.zeros(grid.Nc)
    e[grid.posE[ctx_j.pw]] = ctx_j.nm.v0[ctx_j.pw]
    s = _sg(np.fft.irfft(np.conj(np.fft.rfft(a, grid.L)) * np.fft.rfft(e, grid.L), grid.L), grid.L, grid.Tmax)
    with np.errstate(invalid='ignore', divide='ignore'):
        gam = s / grid.Ncr
    gam[grid.Ncr == 0] = np.nan
    return gam


def _project_cross_kernel(grid: _CrossGrid, b_signed: np.ndarray) -> np.ndarray:
    """
    pair_cross_projavg.m (Section 4): project one signed-lag kernel b (length 2*Nc-1, b[Nc-1+tau] =
    b(tau), tau = -(Nc-1)..(Nc-1)) through both components' step-1 residual operators M = I - A K0.
    Returns the projected kernel at the fitted signed lags -Tmax..Tmax (ascending, length 2*Tmax+1).
    """
    Nc, L, Tmax = grid.Nc, grid.L, grid.Tmax
    bc = np.zeros(L)
    bc[:Nc] = b_signed[Nc - 1:]
    bc[L - Nc + 1:] = b_signed[:Nc - 1]
    fb = np.fft.rfft(bc)

    bf = np.fft.irfft(grid.FKN * fb[:, None], L, axis=0)[:Nc]      # K0_N * R, on the daily grid (Nc, uN)
    bm = bf * grid.wE[:, None]
    dm = np.fft.irfft(grid.FKE * np.conj(fb)[:, None], L, axis=0)[:Nc]   # R * K0_E', (Nc, uE)
    dm = dm * grid.wN[:, None]

    fbm = np.fft.rfft(bm, L, axis=0)
    fdm = np.fft.rfft(dm, L, axis=0)
    s2 = np.fft.irfft((np.conj(grid.FAN) * fbm).sum(axis=1), L)
    s3 = np.fft.irfft((np.conj(fdm) * grid.FAE).sum(axis=1), L)

    g = bf.T @ grid.KfE                                             # (uN, uE), over all epochs of both step-1 fits
    b_fitted = b_signed[Nc - 1 - Tmax: Nc + Tmax]
    s = b_fitted * grid.Ncr - _sg(s2, L, Tmax) - _sg(s3, L, Tmax) + grid.XiNE @ g.ravel(order='F')

    with np.errstate(invalid='ignore', divide='ignore'):
        out = s / grid.Ncr
    out[grid.Ncr == 0] = np.nan
    return out


def _next_fast_sigma_length(n: int) -> int:
    """the 2^k, 3*2^k, 5*2^k rule used by ecov_sigma_multi.m for the (small) cross-Sigma FFT"""
    target = 2 * n - 1
    return min(m * 2 ** int(np.ceil(np.log2(target / m))) for m in (1, 3, 5))


def _cross_sigma(grid: _CrossGrid, ct_i: np.ndarray, ct_j: np.ndarray, lags: np.ndarray,
                 workers: int = -1) -> Tuple[np.ndarray, np.ndarray]:
    """
    ecov_sigma_multi.m's cross part (Section 5), first pass only (c_NE assumed 0 -- spec Section 12,
    "do not implement a second Sigma pass with the fitted cross-covariance"): Sigma of the empirical
    cross-covariance at the signed fitted lags, built from one shared set of overlap counts (one per
    unsigned lag pair a <= b), reused for all 4 sign combinations.

    ct_i, ct_j: each component's own unweighted-fit projected covariance (noise_covariance.py's
    sigma_ct -- the weight of the single-component Sigma too, held fixed for the same reason), at lags
    0..n_days-1 (shorter than Nc when that component's own span is shorter; zero-padded here, NaN
    treated as 0 same as the single-component Sigma).

    Returns (Sigma, slags): Sigma is (2K-1) x (2K-1), slags = [-lags[-1:0:-1], lags] (ascending).
    """
    N = grid.Nc
    lags = np.asarray(lags, dtype=np.int64)
    K = lags.size
    L = _next_fast_sigma_length(N)
    w = grid.wN

    fp = np.empty((K, L // 2 + 1), dtype=complex)
    n_tau = np.empty(K)
    for a, tau in enumerate(lags):
        p = np.zeros(N)
        if tau == 0:
            p[:] = w
        else:
            p[:N - tau] = w[:N - tau] * w[tau:]
        n_tau[a] = p.sum()
        fp[a] = scipy.fft.rfft(p, L, workers=workers)

    def pad(ct: np.ndarray) -> np.ndarray:
        c = np.zeros(N)
        m = min(N, ct.size)
        c[:m] = np.where(np.isfinite(ct[:m]), ct[:m], 0.)
        out = np.zeros(4 * N + 1)
        out[o - (N - 1):o + N] = np.concatenate([c[N - 1:0:-1], c])
        return out

    o = 2 * N
    cNp, cEp = pad(ct_i), pad(ct_j)

    slags = np.concatenate([-lags[-1:0:-1], lags])
    iz = K - 1
    sigma = np.zeros((2 * K - 1, 2 * K - 1))

    for a in range(K):
        t1 = int(lags[a])
        fa = np.conj(fp[a])
        for b in range(a, K):
            t2 = int(lags[b])
            d1 = -(N - 1 - t1)
            d2 = N - 1 - t2
            if d2 < d1:
                continue
            r = scipy.fft.irfft(fa * fp[b], L, workers=workers)
            if d1 < 0:
                R = np.round(np.concatenate([r[L + d1:], r[:d2 + 1]]))
            else:
                R = np.round(r[d1:d2 + 1])
            R = R / (n_tau[a] * n_tau[b])

            i1, i2 = o + d1, o + d2
            sa = (1,) if t1 == 0 else (1, -1)
            sb = (1,) if t2 == 0 else (1, -1)
            for s1 in sa:
                for s2 in sb:
                    dA = t1 if s1 < 0 else 0
                    dB = t2 if s2 < 0 else 0
                    sh = dB - dA
                    dl = s2 * t2 - s1 * t1
                    iA = iz + s1 * a
                    iB = iz + s2 * b
                    v = float(R @ (cNp[i1 + sh:i2 + sh + 1] * cEp[i1 + sh + dl:i2 + sh + dl + 1]))
                    sigma[iA, iB] = v
                    sigma[iB, iA] = v
    return sigma, slags


def _kernel_spectra(n: int, alphas: Sequence[float]) -> np.ndarray:
    """kernel_spectra.m / spec Section 10.1: spectral weights B[k, j] of the white kernel (column 0)
    and of each power-law kernel (DCorrFn_PLN / dcorr_pln), at f_k = k / (2n), k = 0..n."""
    c = 0.85
    m = np.arange(1, n + 1)
    f = (c * m / n + (1 - c) * (m - 1) / n) / 2

    def fold(a: np.ndarray) -> np.ndarray:
        b = np.zeros(n + 1)
        b[0], b[n] = a[0], a[n]
        b[1:n] = a[1:n] + a[2 * n - 1:n:-1]
        return b

    b = np.zeros((n + 1, 1 + len(alphas)))
    b[:, 0] = fold(np.full(2 * n, 1. / (2 * n)))
    for j, alpha in enumerate(alphas):
        ps = f ** (-alpha)
        ps_full = np.concatenate([ps, ps[::-1]])
        b[:, j + 1] = fold(ps_full / ps_full.sum())
    return b


def _matched_periods(periods_i: np.ndarray, w_p_i: np.ndarray, periods_j: np.ndarray, w_p_j: np.ndarray,
                     tol: float, tau_signed: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cross periodic terms (Section 6.3): periods detected in both components, within `tol` of each
    other (fraction). Returns (periods_d, Bper (len(tau_signed) x 2*np), lper (box bounds, length 2*np))."""
    periods, bper_cols, lper = [], [], []
    for pi, wi in zip(periods_i, w_p_i):
        if periods_j.size == 0:
            continue
        j = int(np.argmin(np.abs(periods_j - pi) / pi))
        if abs(periods_j[j] - pi) / pi < tol:
            bper_cols.append(np.cos(2 * np.pi * tau_signed / pi))
            bper_cols.append(np.sin(2 * np.pi * tau_signed / pi))
            bound = float(np.sqrt(max(wi * w_p_j[j], 0.)))
            lper += [bound, bound]
            periods.append(pi)
    if not periods:
        return np.array([]), np.zeros((tau_signed.size, 0)), np.array([])
    return np.array(periods), np.column_stack(bper_cols), np.array(lper)


def _cutting_plane_grid(nf: int, n_linear: int, n_log: int) -> np.ndarray:
    """starter constraint-frequency indices (fit_cross_spec.m / the inline grid search): the first
    n_linear indices, plus n_log log-spaced ones up to nf - 1"""
    linear = np.arange(min(n_linear, nf))
    if nf > n_linear:
        log_part = np.round(np.logspace(np.log10(n_linear + 1), np.log10(nf), n_log)).astype(int) - 1
    else:
        log_part = np.array([], dtype=int)
    kk = np.unique(np.concatenate([linear, log_part]))
    return kk[(kk >= 0) & (kk < nf)]


def _constrained_fit(design: np.ndarray, data: np.ndarray, bsp_cols: np.ndarray, envelope: np.ndarray,
                     n_periodic: int, lper: np.ndarray, settings: CrossCovarianceSettings) -> Tuple[np.ndarray, float]:
    """
    fit_cross_spec.m / the inline grid-search fit of PL_make_cross_test_vectors.m (Section 6.2/9.4):
    constrained least squares of `design @ theta ~= data`, theta = [theta_w, w_x, periodic...], subject
    to the box bounds of the periodic terms and the spectral validity condition
        |bsp_cols[:, 0] * theta_w + bsp_cols[:, 1] * w_x| <= envelope
    at every frequency of the grid, enforced with cutting planes (start with a subset of frequencies,
    add violations, refit; converges in a few iterations). theta_w, w_x are unbounded (both signs).
    """
    n_theta = 2 + n_periodic
    bounds = [(-np.inf, np.inf), (-np.inf, np.inf)] + [(-float(b), float(b)) for b in lper]

    def objective(theta):
        r = design @ theta - data
        return float(r @ r), 2 * design.T @ r

    kk = _cutting_plane_grid(envelope.size, settings.cutting_plane_linear, settings.cutting_plane_log)
    theta = np.zeros(n_theta)
    for _ in range(settings.max_cutting_plane_iter):
        bk, env = bsp_cols[kk], envelope[kk]

        def con_fun(theta, bk=bk, env=env):
            co = bk @ theta[:2]
            return np.concatenate([env - co, env + co])

        def con_jac(theta, bk=bk):
            j = np.zeros((2 * bk.shape[0], theta.size))
            j[:bk.shape[0], :2] = -bk
            j[bk.shape[0]:, :2] = bk
            return j

        res = minimize(objective, theta, jac=True, method='SLSQP', bounds=bounds,
                       constraints=[{'type': 'ineq', 'fun': con_fun, 'jac': con_jac}],
                       options={'maxiter': 100, 'ftol': 1e-12})
        theta = res.x
        co = bsp_cols @ theta[:2]
        viol = np.where(np.abs(co) > envelope * (1 + 1e-9))[0]
        if viol.size == 0:
            break
        kk = np.union1d(kk, viol)

    rnorm = float(np.linalg.norm(design @ theta - data))
    return theta, rnorm


def _daily_residual_correlation(ctx_i: ComponentCrossInput, ctx_j: ComponentCrossInput) -> float:
    """correlation of the two components' step-1 residuals at the common epochs (spec Section 11's
    'daily residual correlation' diagnostic -- distinct from the existing EtmFit.process_covariance's
    GLS-residual-based correlation, see the module docstring)"""
    v_i, v_j = ctx_i.nm.v0[ctx_i.pw], ctx_j.nm.v0[ctx_j.pw]
    if v_i.size < 2:
        return np.nan
    r = float(np.corrcoef(v_i, v_j)[0, 1])
    return r if np.isfinite(r) else np.nan


def _parameter_cross_covariance(ctx_i: ComponentCrossInput, ctx_j: ComponentCrossInput, grid: _CrossGrid,
                                c_signed: np.ndarray) -> np.ndarray:
    """spec Section 7: Cov(x_i, x_j) = K_i C_ij K_j', C_ij[k, l] = c(t_j[l] - t_i[k]) at every pair of
    the two components' own (full) epochs -- not just the common ones: the model covariance function is
    evaluated at whatever signed lag separates any two observations. t_i, t_j are each on their own
    component-relative day scale, so the pair's shared offsets (grid.off_i/off_j) put them on the same
    absolute day axis before differencing (they coincide, off_i == off_j == 0, only when the two
    components' first inlier epochs happen to be the same calendar day)."""
    t_i = ctx_i.nm.t + grid.off_i
    t_j = ctx_j.nm.t + grid.off_j
    lag_ix = t_j[None, :] - t_i[:, None] + (grid.Nc - 1)
    c_ij = c_signed[lag_ix]
    return ctx_i.k_estimator @ c_ij @ ctx_j.k_estimator.T


def borrowed_residual_correlation(residuals_i: np.ndarray, residuals_j: np.ndarray,
                                  sigma_i: np.ndarray, sigma_j: np.ndarray) -> Tuple[np.ndarray, float]:
    """
    Section 10.2, generalized from a single scalar (today, velocity-only, in com/PrintVelocities.py /
    EtmFit.process_covariance) to an arbitrary parameter block:
        Cov(x_i[k], x_j[l]) = r * sigma_i[k] * sigma_j[l],
    r = correlation of the two components' residuals. Always valid (|r| <= 1), cannot flip sign or
    blow up, unlike residual-ellipse scaling (Section 10.1).
    residuals_i, residuals_j: already restricted by the caller to the epochs common to both (and in
    the same order: index k of each must be the same calendar day), and so the same length -- this
    function does no masking/alignment of its own, since the two callers need it done differently
    (EtmFit.process_cross_covariance: one shared boolean mask over two equal-length arrays already in
    the same order; cross_covariance._fallback: two different per-component masks, since each
    component's step-1 residuals are indexed by that component's own, differently-sized epoch list).
    sigma_i, sigma_j: parameter sigmas of the two blocks (sqrt of the diagonal of their own covariance).
    """
    r = 0.
    if (residuals_i.size == residuals_j.size and residuals_i.size >= 2 and
            np.all(np.isfinite(sigma_i)) and np.all(np.isfinite(sigma_j))):
        rc = np.corrcoef(residuals_i, residuals_j)[0, 1]
        if np.isfinite(rc):
            r = float(rc)
    return r * np.outer(sigma_i, sigma_j), r


def _fallback(ctx_i: ComponentCrossInput, ctx_j: ComponentCrossInput, pair: str, flags: List[str],
             daily_r: float = np.nan, n_common: int = 0) -> CrossCovarianceResult:
    """when the power-law fit can't run: fall back using each component's own step-1 residuals/sigmas
    at the common epochs (same formula as borrowed_residual_correlation), applied to the full
    parameter vector so every function still gets a cross_covar block (EtmFit.process_cross_covariance).
    ctx_i.pw and ctx_j.pw each select that component's OWN common-epoch subset of its OWN v0 -- since
    both represent the same underlying set of calendar days in the same (ascending) order, the two
    masked arrays line up index-for-index despite having different lengths before masking"""
    sigma_i = np.sqrt(np.diag(ctx_i.nm.gls.qx))
    sigma_j = np.sqrt(np.diag(ctx_j.nm.gls.qx))
    matrix, r = borrowed_residual_correlation(ctx_i.nm.v0[ctx_i.pw], ctx_j.nm.v0[ctx_j.pw], sigma_i, sigma_j)
    return CrossCovarianceResult(pair=pair, method='borrowed_residual_correlation', matrix=matrix,
                                 daily_residual_correlation=daily_r if np.isfinite(daily_r) else r,
                                 n_common_epochs=n_common or int(ctx_i.pw.sum()), flags=flags)


def fit_pair(ctx_i: ComponentCrossInput, ctx_j: ComponentCrossInput,
            settings: Optional[CrossCovarianceSettings] = None) -> CrossCovarianceResult:
    """
    Power-law cross-spectrum model for one pair (spec Sections 2-7): empirical cross-covariance,
    projected cross kernels, cross Sigma, grid search over alpha_x with a constrained fit at each
    trial, periodic cross terms, and the parameter cross-covariance K_i C_ij K_j'.

    Falls back to borrowed_residual_correlation when there isn't enough common data, or when either
    component's GLS estimator K wasn't computed (settings.compute_estimator was off for that fit).
    """
    settings = settings or CrossCovarianceSettings()
    pair = f'{ctx_i.label}{ctx_j.label}'
    n_common = int((ctx_i.pw.sum() if ctx_i.pw.size else 0))

    if ctx_i.k_estimator is None or ctx_j.k_estimator is None:
        return _fallback(ctx_i, ctx_j, pair, ['no GLS estimator K (compute_estimator was off)'])
    if n_common < settings.min_common_epochs:
        return _fallback(ctx_i, ctx_j, pair,
                         [f'too few common epochs ({n_common} < {settings.min_common_epochs})'])

    daily_r = _daily_residual_correlation(ctx_i, ctx_j)

    grid = _setup_cross_grid(ctx_i, ctx_j)
    gam = _empirical_cross_covariance(ctx_i, ctx_j, grid)

    fin_i, fin_j = ctx_i.nm.final, ctx_j.nm.final
    alphas = ctx_i.nm.settings.alphas

    lags = geo_lags(ctx_i.nm.settings.lag_k, grid.Tmax)
    tau_signed = np.arange(-(grid.Nc - 1), grid.Nc)

    dw = _project_cross_kernel(grid, (tau_signed == 0).astype(float))

    periods, bper, lper = _matched_periods(1. / fin_i.f, fin_i.w_p, 1. / fin_j.f, fin_j.w_p,
                                           settings.periodic_tol, tau_signed)
    n_periodic = periods.size
    dper = (np.column_stack([_project_cross_kernel(grid, bper[:, q]) for q in range(bper.shape[1])])
           if n_periodic else np.zeros((2 * grid.Tmax + 1, 0)))

    # Sigma's weight comes from each component's own UNWEIGHTED-fit projected covariance (sigma_ct,
    # Section 7.2 of the noise-model spec), not the final fitted one (c_lag_final) -- same quantity
    # noise_covariance.py's own (single-component) Sigma step uses, held fixed for the same reason
    cross_sigma, slags = _cross_sigma(grid, ctx_i.nm.sigma_ct, ctx_j.nm.sigma_ct, lags, settings.fft_workers)
    row_idx = slags + grid.Tmax

    ok = np.isfinite(gam[row_idx]) & np.isfinite(dw[row_idx])
    if ok.sum() < 3:
        return _fallback(ctx_i, ctx_j, pair, ['too few valid fitted lags for the cross fit'], daily_r, n_common)

    sx = cross_sigma[np.ix_(ok, ok)]
    try:
        jitter_diag = np.eye(sx.shape[0]) * np.mean(np.diag(sx))
        lw, _ = cholesky_jitter(lambda j: sx + j * jitter_diag, 'the cross Sigma',
                                settings.jitter_start, settings.jitter_factor, settings.jitter_max)
    except LinAlgError:
        return _fallback(ctx_i, ctx_j, pair, ['cross Sigma is not positive definite'], daily_r, n_common)

    gw = solve_triangular(lw, gam[row_idx][ok], lower=True)
    dw_w = solve_triangular(lw, dw[row_idx][ok], lower=True)
    dper_w = solve_triangular(lw, dper[row_idx][ok], lower=True) if n_periodic else np.zeros((ok.sum(), 0))

    bsp_wc = _kernel_spectra(grid.Nc, alphas)
    bsp_white = bsp_wc[:, 0]
    s_i = bsp_wc @ np.concatenate([[fin_i.nugget], fin_i.w])
    s_j = bsp_wc @ np.concatenate([[fin_j.nugget], fin_j.w])
    envelope = np.sqrt(np.clip(s_i * s_j, 0., None))

    best = None
    rnorm_grid = np.full(len(settings.alpha_grid), np.nan)
    for k, alpha_x in enumerate(settings.alpha_grid):
        r_lag = dcorr_pln(grid.Nc, alpha_x)
        d_cross = _project_cross_kernel(grid, r_lag[np.abs(tau_signed)])
        bsp_cross = _kernel_spectra(grid.Nc, [alpha_x])[:, 1]

        design = np.column_stack([dw_w, solve_triangular(lw, d_cross[row_idx][ok], lower=True), dper_w])
        bsp_cols = np.column_stack([bsp_white, bsp_cross])

        theta, rnorm = _constrained_fit(design, gw, bsp_cols, envelope, lper.size, lper, settings)
        rnorm_grid[k] = rnorm
        if best is None or rnorm < best[0]:
            best = (rnorm, alpha_x, theta)

    _, alpha_x, theta = best
    theta_w, w_x = float(theta[0]), float(theta[1])
    per_coefs = theta[2:]

    r_alpha = dcorr_pln(grid.Nc, alpha_x)
    c_signed = theta_w * (tau_signed == 0).astype(float) + w_x * r_alpha[np.abs(tau_signed)]
    if n_periodic:
        c_signed = c_signed + bper @ per_coefs

    matrix = _parameter_cross_covariance(ctx_i, ctx_j, grid, c_signed)

    # diagnostics for the noise-cross plot: gam/model at the same dense signed-lag domain as gam itself
    # (-Tmax..Tmax, matching the single-component plot's 'gamma' convention), model sliced from c_signed
    # the same way _project_cross_kernel's own b_fitted is
    model = c_signed[grid.Nc - 1 - grid.Tmax: grid.Nc + grid.Tmax]
    lag_days = np.arange(-grid.Tmax, grid.Tmax + 1)

    return CrossCovarianceResult(pair=pair, method='power_law_cross_spectrum', matrix=matrix,
                                 daily_residual_correlation=daily_r, n_common_epochs=n_common,
                                 alpha_x=alpha_x, theta_w=theta_w, w_x=w_x,
                                 periodic_periods_d=periods, periodic_coefs=per_coefs,
                                 residual_norm_grid=rnorm_grid, lag_days=lag_days, gamma=gam, model=model,
                                 fitted_lag_days=slags, alpha_grid=np.array(settings.alpha_grid))
