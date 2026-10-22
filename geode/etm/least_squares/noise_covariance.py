"""
Project: Geodetic Database Engine (GeoDE)
Date: 10/19/26
Author: Demian D. Gomez

Noise covariance of ETM residuals with automatic detection of periodic terms.

Per component of one station:
    1. WLS fit of the ETM -> residuals
    2. empirical covariance of the residuals over the observed pairs of epochs (lags up to N/2)
    3. fit of a non-negative mix of power laws (alpha = 0.5 ... 1.5) plus white noise. Each kernel is
       replaced by what the ETM fit does to it (projected kernels), and the fit is weighted by the
       covariance of the empirical covariance (Sigma)
    4. detection of periodic terms (phase-free cosine kernels): joint harmonic fit (default) or stepwise grid
       search (option)
    5. GLS refit of the ETM with the resulting full covariance
    6. diagnostics: effective spectral index and variance factors

Reference: GeoDE_noise_method_spec.md (pgamit/docs/geode_handoff). Section numbers in the comments refer
to that spec. Units: everything here is in the units of the observations (the spec uses mm) and days
(epochs and lags); frequencies are in cycles/yr in all inputs and outputs. The module does not depend on
the rest of GeoDE so that it can be tested against the MATLAB test vectors.
"""

import logging
from dataclasses import dataclass, field
from time import time
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import scipy.fft
from scipy.linalg import cholesky, solve_triangular, qr, orth, lapack, LinAlgError
from scipy.optimize import nnls, brentq

logger = logging.getLogger(__name__)

DAYS_PER_YEAR = 365.25
# candidate outcomes, with the codes of the test vectors
JHF_OUTCOME_CODES = {'accepted': 1, 'below threshold': 2, 'zero after refit': 3, 'zero in harmonic fit': 4}
SGS_OUTCOME_CODES = {'accepted': 1, 'snr': 2, 'joint': 3, 'zero': 4, 'unresolved': 5}
DETECTION_METHODS = ('joint', 'stepwise')
# report flags: chi2/dof of the final fit above this (unmodeled signal left), periods longer than this fraction
# of the span (may be quasi-periodic rather than steady)
FLAG_CHI2_DOF = 1.5
FLAG_SPAN_FRACTION = 0.25


@dataclass
class NoiseCovarianceSettings:
    """Settings of the method. The defaults are the final design choices of the spec (Section 13)"""
    # spectral indices of the power-law basis (do not go above 1.5)
    alphas: Tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5)
    # geometric lag set: every lag 0..k, then a ratio of 1 + 1/k up to Tmax
    lag_k: int = 16
    # detection of periodic terms: 'joint' (joint harmonic fit, Section 8.2) or 'stepwise' (stepwise grid search,
    # Section 8.3, kept for comparison)
    detection: str = 'joint'
    # joint harmonic fit: SNR threshold; candidates = peaks of the misfit-spectrum ratio >= cand_ratio, at least
    # cand_sep / N cycles/day apart, at most cand_max, skipped if the projected cosine at lag 0 < keep_min;
    # periods searched from N / min_cycles down to p_min days; zero padding of the lag FFT
    jhf_snr_min: float = 8.0
    jhf_cand_ratio: float = 1.5
    jhf_cand_sep: float = 2.0
    jhf_cand_max: int = 30
    jhf_keep_min: float = 0.5
    jhf_min_cycles: float = 2.0
    jhf_p_min: float = DAYS_PER_YEAR / 4
    jhf_pad: int = 8
    # stepwise grid search: band [cycles/yr], candidate grid oversampling, smallest delta chi2, SNR threshold
    sgs_band: Tuple[float, float] = (0.2, 4.0)
    sgs_over: int = 5
    sgs_dchi2_min: float = 2.0
    sgs_snr_min: float = 7.0
    # floor share used to find the band of the effective spectral index
    share: float = 0.1
    # Cholesky safety margin for Sigma (relative to its mean diagonal)
    eps: float = 1e-10
    # minimum white variance imposed in every fit, in the units of the observations squared: (0.5 mm)^2.
    # Real GNSS positions always have some white noise; without it the fit can put all near-white noise into
    # the alpha = 0.5 component
    white_min: float = 0.25
    # the predicted signal is the periodic terms plus the power laws with alpha >= signal_alpha_min; flatter
    # power laws and the white noise stay in the residuals
    signal_alpha_min: float = 1.0
    # compute the colored / white variance factors (needs one n x n triangular inverse per GLS)
    variance_factors: bool = False
    # threads of the FFTs of Sigma (scipy.fft workers; -1 = all cores). Use 1 when many series run in parallel
    fft_workers: int = -1


# ============================================================================
# Building blocks
# ============================================================================

def fft_length(n_days: int) -> int:
    """FFT length L = 2**ceil(log2(2N)) >= 2N - 1, so there is no circular wrap at any lag"""
    return int(2 ** np.ceil(np.log2(2 * n_days)))


def sigma_fft_length(n_days: int) -> int:
    """
    FFT length of the overlap counts of Sigma: any length >= 2N - 1 gives the same counts, so take the fastest
    real-FFT size of scipy.fft (as fast as or faster than the 2^k, 3 2^k, 5 2^k rule of pgamit: 0.89 against
    0.93 s at IGM1, N = 8314, and 3x faster than 2^ceil(log2(2N)))
    """
    return scipy.fft.next_fast_len(2 * n_days - 1, real=True)


def geo_lags(k: int, tmax: int) -> np.ndarray:
    """Lag set (Section 6): every lag 0..k, then a ratio of about 1 + 1/k, up to tmax inclusive"""
    lags = list(range(0, min(k, tmax) + 1))
    while lags[-1] < tmax:
        # floor(x + 0.5) reproduces the MATLAB round (half away from zero) for positive values
        nxt = max(lags[-1] + 1, int(np.floor(lags[-1] * (1 + 1 / k) + 0.5)))
        lags.append(min(nxt, tmax))
    return np.array(lags, dtype=int)


def dcorr_pln(n: int, alpha: float) -> np.ndarray:
    """
    Covariance function at lags 0..n-1 of power-law noise with spectral index alpha, normalized to 1 at
    lag 0 (exact algorithm of DCorrFn_PLN.m, Section 4.1)
    """
    c = 0.85
    m = np.arange(1, n + 1)
    # = (m - 0.15) / (2n) cycles/day: avoids f = 0 and ends near Nyquist
    f = (c * m / n + (1 - c) * (m - 1) / n) / 2
    ps = f ** (-alpha)
    cf = np.fft.ifft(np.concatenate([ps, ps[::-1]]))
    return np.real(cf[:n] / cf[0])


def candidate_grid(tmax: int, band: Sequence[float], over: int) -> np.ndarray:
    """Candidate frequencies of the periodic terms [cycles/yr] (Section 8.1, = MATLAB f1:df:f2)"""
    df = DAYS_PER_YEAR / tmax / over
    f1 = band[0] if band[0] > 0 else df
    f2 = min(band[1], DAYS_PER_YEAR / 2)
    return f1 + df * np.arange(int(np.floor((f2 - f1) / df + 1e-9)) + 1)


def to_grid(t: np.ndarray, values: np.ndarray, n_days: int) -> np.ndarray:
    """Place values (one row per epoch) on the daily grid, zeros on missing days"""
    out = np.zeros((n_days,) + values.shape[1:])
    out[t] = values
    return out


def circular_embed(b: np.ndarray, length: int) -> np.ndarray:
    """Symmetric b(|lag|), given at lags 0..N-1 along axis 0, on a circle of `length` >= 2N - 1"""
    n = b.shape[0]
    bc = np.zeros((length,) + b.shape[1:])
    bc[:n] = b
    bc[length - n + 1:] = b[n - 1:0:-1]
    return bc


class EpochProjector:
    """
    Pair structure of a set of epochs and the residual operator M0 = I - A K0 of a linear ETM estimator
    (pair_setup.m). It is data-independent: it depends only on the epochs, A and K0.
    """
    def __init__(self, t: np.ndarray, a: np.ndarray, k0: np.ndarray):
        self.t = t
        self.a = a
        self.k0 = k0
        self.n_days = int(t[-1] + 1)
        self.tmax = self.n_days // 2
        self.fft_len = fft_length(self.n_days)

        n_days, fft_len = self.n_days, self.fft_len

        self.w = np.zeros(n_days)
        self.w[t] = 1.
        # number of observed pairs at lags 0..N-1
        self.n_tau = np.rint(np.fft.irfft(np.abs(np.fft.rfft(self.w, fft_len)) ** 2, fft_len)[:n_days])

        # daily-grid copies of A and K0.T and their FFTs
        self.af = to_grid(t, a, n_days)
        self.kf = to_grid(t, k0.T, n_days)
        self.fa = np.fft.rfft(self.af, fft_len, axis=0)
        self.fk = np.fft.rfft(self.kf, fft_len, axis=0)

    def _pair_average(self, s: np.ndarray) -> np.ndarray:
        """divide pair sums by the pair counts (NaN where there are no pairs)"""
        n_tau = self.n_tau[:s.shape[0]]
        with np.errstate(invalid='ignore', divide='ignore'):
            return s / (n_tau[:, None] if s.ndim > 1 else n_tau)

    def empirical_covariance(self, v: np.ndarray, n_lags: int) -> np.ndarray:
        """Empirical covariance over observed pairs, no mean removal (Section 3)"""
        vf = to_grid(self.t, v, self.n_days)
        s = np.fft.irfft(np.abs(np.fft.rfft(vf, self.fft_len)) ** 2, self.fft_len)[:n_lags]
        return self._pair_average(s)

    def project(self, b: np.ndarray) -> np.ndarray:
        """
        Projected kernels (Section 5): expected pair-averaged covariance of the residuals at lags 0..N-1
        when the noise has covariance R_il = b(|t_i - t_l|). Uses
            M0 R M0' = R - A B - (A B)' + A G A',  B = K0 R, G = B K0'
        b has one kernel per column (or is a vector), given at lags 0..N-1
        """
        b = np.asarray(b, dtype=float)
        is_vector = b.ndim == 1
        if is_vector:
            b = b[:, None]

        n_days, fft_len = self.n_days, self.fft_len

        fb = np.fft.rfft(circular_embed(b, fft_len), axis=0).real
        # rows of K0 R on the daily grid (index: day, ETM column, kernel), kept on observed days only
        bm = np.fft.irfft(self.fk[:, :, None] * fb[:, None, :], fft_len, axis=0)[:n_days]
        bm *= self.w[:, None, None]
        fbm = np.fft.rfft(bm, fft_len, axis=0)
        # G = K0 R K0' and A G on the daily grid
        g = np.einsum('nkj,nl->klj', bm, self.kf)
        fh = np.fft.rfft(np.einsum('nk,klj->nlj', self.af, g), fft_len, axis=0)

        fa = self.fa[:, :, None]
        # pair sums of A B + (A B)' - A G A'
        spec = (np.conj(fa) * fbm + np.conj(fbm) * fa - np.conj(fh) * fa).sum(axis=1)
        s = b * self.n_tau[:, None] - np.fft.irfft(spec, fft_len, axis=0)[:n_days]

        bt = self._pair_average(s)
        return bt[:, 0] if is_vector else bt

    def project_cosines(self, freq: np.ndarray, n_lags: int, chunk: int = 64,
                        cycles_per_day: bool = False) -> np.ndarray:
        """
        Projected cosine kernels cos(2 pi f tau) at lags 0..n_lags-1, one column per frequency (cycles/yr, or
        cycles/day if cycles_per_day). Same result as project(), but uses that the kernel has rank 2:
            cos(w (t_i - t_l)) = cos(w t_i) cos(w t_l) + sin(w t_i) sin(w t_l)
        so that M0 R M0' = (M0 c)(M0 c)' + (M0 s)(M0 s)' and the pair sums are autocorrelations.
        """
        freq = np.atleast_1d(np.asarray(freq, dtype=float))
        out = np.empty((n_lags, freq.size))
        fft_len = self.fft_len

        for j in range(0, freq.size, chunk):
            f = freq[j:j + chunk]
            if cycles_per_day:
                phase = 2 * np.pi * np.outer(self.t, f)
            else:
                phase = 2 * np.pi * np.outer(self.t, f) / DAYS_PER_YEAR
            z = np.concatenate([np.cos(phase), np.sin(phase)], axis=1)
            # apply the residual operator M0 = I - A K0
            z -= self.a @ (self.k0 @ z)
            pz = np.abs(np.fft.rfft(to_grid(self.t, z, self.n_days), fft_len, axis=0)) ** 2
            s = np.fft.irfft(pz[:, :f.size] + pz[:, f.size:], fft_len, axis=0)[:n_lags]
            out[:, j:j + f.size] = self._pair_average(s)

        return out

    def gamma_covariance(self, lags: np.ndarray, ct: np.ndarray, workers: Optional[int] = -1) -> np.ndarray:
        """
        Covariance of the empirical covariance at the fitted lags (Sigma, Section 7.2), in one pass:
            Sigma_ab = 1/(N_a N_b) sum_d R_ab(d) [c(d) c(d + tau_b - tau_a) + c(d + tau_b) c(d - tau_a)]
        where R_ab(d) = sum_i p_a[i] p_b[i + d] are the overlap counts of the pair indicators of lags a and b, and
        c is the model residual covariance at lags 0..N-1. For each row a, the counts of all b >= a come from one
        batched inverse real FFT and are used at once over the only range where they can be nonzero,
        -(N - 1 - tau_a) <= d <= N - 1 - tau_b; nothing of size K (K + 1) / 2 x (2N - 1) is stored.
        """
        n_days = self.n_days
        fft_len = sigma_fft_length(n_days)
        lags = np.asarray(lags, dtype=int)
        n_lags = lags.size

        # c at signed lags: cp[2N + l] = c(|l|) for |l| <= N - 1, zero beyond, so that every term of the bracket
        # is a contiguous slice
        ct = np.where(np.isfinite(ct), ct, 0.)
        cp = np.zeros(4 * n_days)
        cp[n_days + 1:3 * n_days] = np.concatenate([ct[n_days - 1:0:-1], ct])
        o = 2 * n_days

        # pair indicators p_a[i] = w[i] w[i + tau_a]
        p = np.zeros((n_lags, n_days))
        for i, tau in enumerate(lags):
            p[i, :n_days - tau] = self.w[:n_days - tau] * self.w[tau:]
        fp = scipy.fft.rfft(p, fft_len, axis=1, workers=workers)
        n_tau = self.n_tau[lags]

        sigma = np.zeros((n_lags, n_lags))
        for i, tau_a in enumerate(lags):
            # R_ab(d) for all b >= a; index d >= 0 at d, d < 0 at fft_len + d
            r = scipy.fft.irfft(np.conj(fp[i]) * fp[i:], fft_len, axis=1, workers=workers)
            d1 = -(n_days - 1 - tau_a)
            r_neg = np.rint(r[:, fft_len + d1:])            # d = d1..-1, the same range for every b
            for k, tau_b in enumerate(lags[i:]):
                d2 = n_days - 1 - tau_b
                f = (cp[o + d1:o + d2 + 1] * cp[o + d1 + tau_b - tau_a:o + d2 + 1 + tau_b - tau_a] +
                     cp[o + d1 + tau_b:o + d2 + 1 + tau_b] * cp[o + d1 - tau_a:o + d2 + 1 - tau_a])
                sigma[i, i + k] = ((r_neg[k] @ f[:-d1] + np.rint(r[k, :d2 + 1]) @ f[-d1:]) /
                                   (n_tau[i] * n_tau[i + k]))

        return np.triu(sigma) + np.triu(sigma, 1).T


# ============================================================================
# Covariance fits
# ============================================================================

@dataclass
class CovarianceFit:
    """Result of one NNLS fit of the noise model to the empirical covariance (Section 7.4)"""
    w: np.ndarray                   # power-law variances
    w_p: np.ndarray                 # variances of the periodic terms (a^2 / 2), in fit order
    nugget: float                   # white-noise variance
    chi2: float
    dof: int
    # frequencies of the periodic terms [cycles/day] and their projected cosines (lags 0..Tmax, one column each)
    f: np.ndarray = field(default_factory=lambda: np.array([]))
    c: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    x: np.ndarray = field(default_factory=lambda: np.array([]))     # NNLS solution, last element = s
    dw: np.ndarray = field(default_factory=lambda: np.array([]))
    noise_lambda: Optional[np.ndarray] = None

    @property
    def colored_variance(self) -> float:
        return float(np.sum(self.w))

    @property
    def white_at_min(self) -> bool:
        """the white variance sits at its minimum (s = 0)"""
        return bool(self.x.size and self.x[-1] <= 0)

    @property
    def f_cpy(self) -> np.ndarray:
        return self.f * DAYS_PER_YEAR

    @property
    def survive(self) -> np.ndarray:
        """projected cosine at lag 0: fraction of each sinusoid's variance that survives the ETM fit"""
        return self.c[0] if self.w_p.size else np.array([])


def fit_white_min(d: np.ndarray, g: np.ndarray, white_min: float, n_alpha: int,
                  f: Optional[np.ndarray] = None, c: Optional[np.ndarray] = None) -> CovarianceFit:
    """
    NNLS fit of the columns d = [power laws, periodic terms, white] to g with white >= white_min, by
    substitution: white = white_min + s, s >= 0, moving the minimum to the data side (Section 7.1). chi2 is
    that of the original problem, and a white variance at its minimum is not a free variable of the dof
    """
    x, rnorm = nnls(d, g - white_min * d[:, -1])
    cf = CovarianceFit(w=x[:n_alpha], w_p=x[n_alpha:-1], nugget=float(x[-1] + white_min),
                       chi2=float(rnorm ** 2), dof=int(d.shape[0] - np.sum(x > 0)), x=x, dw=d)
    if f is not None:
        cf.f, cf.c = np.asarray(f, dtype=float), c
    return cf


class CovarianceFitter:
    """
    Weighted NNLS fits with the columns [Gt, C, ht] at the fitted lags (Section 7.3), C = projected cosine
    kernels of the periodic terms, with the white variance >= white_min; and the SNR of the periodic terms
    """
    def __init__(self, d: np.ndarray, g: np.ndarray, sigma: np.ndarray, eps: float, gb: np.ndarray,
                 lags: np.ndarray, white_min: float = 0.):
        self.n_alpha = gb.shape[1]
        self.white_min = white_min
        self.n_days = gb.shape[0]
        self.gb = gb
        self.lags = lags
        self.n_lags = d.shape[0]

        self.chol = cholesky(sigma + eps * np.mean(np.diag(sigma)) * np.eye(self.n_lags), lower=True)
        self.dw = solve_triangular(self.chol, d, lower=True)
        self.gw = solve_triangular(self.chol, g, lower=True)

    def whiten(self, c: np.ndarray) -> np.ndarray:
        """whitened columns of projected kernels given at lags 0..Tmax"""
        if not c.shape[1]:
            return np.zeros((self.n_lags, 0))
        return solve_triangular(self.chol, c[self.lags], lower=True)

    def fit(self, f: Sequence[float] = (), c: Optional[np.ndarray] = None,
            cw: Optional[np.ndarray] = None) -> CovarianceFit:
        """fit with periodic terms at frequencies f [cycles/day], projected cosines c (or already whitened cw)"""
        f = np.asarray(f, dtype=float).ravel()
        if c is None:
            c = np.zeros((0, 0))
        if cw is None:
            cw = self.whiten(c) if f.size else np.zeros((self.n_lags, 0))
        na = self.n_alpha
        return fit_white_min(np.hstack([self.dw[:, :na], cw, self.dw[:, na:]]), self.gw, self.white_min, na, f, c)

    def noise_bin(self, cf: CovarianceFit, f: np.ndarray, cumulative: bool = False) -> np.ndarray:
        """
        Variance of the fit's power law + white (unprojected) in |freq - f| <= 1/(2N), both signs (Section 8.1);
        f in cycles/day. cumulative: the cumulative-sum form of the joint harmonic fit (same value unless
        f +- 1/(2N) falls exactly on a frequency of the circulant)
        """
        n_days = self.n_days
        if cf.noise_lambda is None:
            # circulant embedding of length 2N - 2
            gn = self.gb @ cf.w
            gn[0] += cf.nugget
            cf.noise_lambda = np.real(np.fft.fft(np.concatenate([gn, gn[n_days - 2:0:-1]])))

        lam = cf.noise_lambda
        lc = lam.size
        d = 1 / (2 * n_days)
        f = np.atleast_1d(np.asarray(f, dtype=float))

        if cumulative:
            cs = np.concatenate([[0.], np.cumsum(lam)])

            def win(a, b):
                # sum of lam[m] with a <= m / lc <= b
                return (cs[np.clip(np.floor(b * lc).astype(int) + 1, 0, lc)] -
                        cs[np.clip(np.ceil(a * lc).astype(int), 0, lc)])

            return (win(f - d, f + d) + win(1 - f - d, 1 - f + d)) / lc

        fm = np.arange(lc) / lc
        return np.array([lam[(np.abs(fm - fi) <= d) | (np.abs(fm - (1 - fi)) <= d)].sum() / lc for fi in f])

    def snr(self, cf: CovarianceFit, cumulative: bool = False) -> np.ndarray:
        """
        Signal-to-noise ratio of each periodic term of cf (Section 8.1): its power after the ETM fit over the
        power of the fit's power law + white in one frequency-resolution bin around it
        """
        if not cf.w_p.size:
            return np.array([])
        return cf.w_p * cf.c[0] / self.noise_bin(cf, cf.f, cumulative)


# ============================================================================
# Detection of periodic terms (Section 8)
# ============================================================================

@dataclass
class JointCandidate:
    """Candidate of the joint harmonic fit and its outcome (Section 8.2)"""
    f0: float                   # before refinement [cycles/day]
    f: float                    # after refinement [cycles/day]
    w: float                    # variance in the harmonic fit
    snr: float                  # SNR in the harmonic fit
    outcome: str                # accepted, below threshold, zero after refit, zero in harmonic fit

    @property
    def period_d(self) -> float:
        return 1 / self.f

    @property
    def period0_d(self) -> float:
        return 1 / self.f0

    @property
    def outcome_code(self) -> int:
        return JHF_OUTCOME_CODES[self.outcome]


@dataclass
class JointDetection:
    """Result of the joint harmonic fit"""
    final_fit: CovarianceFit
    snr: np.ndarray             # SNR of the final periods in the final fit
    snr_harmonic: np.ndarray    # SNR of the final periods in the harmonic fit (NaN: added by the final check)
    candidates: List[JointCandidate]
    harmonic_fit: CovarianceFit
    spec_f: np.ndarray          # frequencies of the lag spectrum [cycles/day]
    band: np.ndarray            # indices of the search band in spec_f
    spec_s: np.ndarray          # misfit spectrum of the reference fit
    spec_sigma2_bin: np.ndarray # noise per bin of the reference fit (NaN outside the band)
    spec_r: np.ndarray          # ratio R of the reference fit (NaN outside the band)
    check_r: np.ndarray         # ratio R of the final check
    check_found: np.ndarray     # final check: peaks with R >= snr_min away from the kept periods [cycles/day]
    check_added: np.ndarray     # final check: the added ones (after refinement) [cycles/day]
    check_removed: np.ndarray   # final check: terms it removed (found ones that failed, kept ones with variance 0)
    check_action: List[str]     # final check action on each final period: passed (kept) or added
    method: str = 'joint'


class JointHarmonicFit:
    """
    Joint harmonic fit (Section 8.2, joint_harmonic_fit.m): candidate periods from the spectrum of the misfit
    of the covariance fit, all fitted together as cosine kernels, kept when their SNR in that fit is >= snr_min
    """
    # trial shifts of the frequency refinement [bins of 1/N], in this order
    SHIFTS = np.array([-1, -2 / 3, -1 / 3, 1 / 3, 2 / 3, 1])

    def __init__(self, fitter: CovarianceFitter, proj: EpochProjector, gamma: np.ndarray, gt: np.ndarray,
                 ht: np.ndarray, settings: NoiseCovarianceSettings):
        self.fitter = fitter
        self.proj = proj
        self.st = settings
        self.n_days = proj.n_days
        self.tmax = proj.tmax
        self.gamma = gamma
        # projected power-law and white kernels at lags 0..Tmax
        self.gt = gt
        self.ht = ht

        # lag spectrum grid [cycles/day] and the search band
        self.lf = settings.jhf_pad * 2 ** int(np.ceil(np.log2(self.tmax)))
        self.fr = np.arange(self.lf // 2 + 1) / self.lf
        self.inb = np.flatnonzero((self.fr >= settings.jhf_min_cycles / self.n_days) &
                                  (self.fr <= 1 / settings.jhf_p_min))
        # half a resolution bin of the lag spectrum
        self.dsep = 1 / (2 * self.tmax)

    def proj_cos(self, f: np.ndarray) -> np.ndarray:
        """projected cosine kernels at frequencies f [cycles/day], lags 0..Tmax"""
        f = np.atleast_1d(np.asarray(f, dtype=float))
        if not f.size:
            return np.zeros((self.tmax + 1, 0))
        return self.proj.project_cosines(f, self.tmax + 1, cycles_per_day=True)

    def snr(self, cf: CovarianceFit) -> np.ndarray:
        return self.fitter.snr(cf, cumulative=True)

    def misfit_ratio(self, cf: CovarianceFit) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Spectrum S of the misfit gamma - fitted covariance at lags 1..Tmax (an oscillation s cos(2 pi f tau) in
        the misfit gives a peak of height ~ s) and its ratio R to the noise per bin of the fit (band only)
        """
        model = self.gt @ cf.w + self.ht * cf.nugget
        if cf.w_p.size:
            model = model + cf.c @ cf.w_p
        r = self.gamma[1:self.tmax + 1] - model[1:self.tmax + 1]
        r = r - r.mean()
        s = 2 * np.abs(np.fft.rfft(r, self.lf)) / self.tmax
        sb = np.full(self.fr.size, np.nan)
        sb[self.inb] = self.fitter.noise_bin(cf, self.fr[self.inb], cumulative=True)
        return s / sb, s, sb

    def local_peaks(self, r: np.ndarray, threshold: float) -> np.ndarray:
        """interior local maxima of r in the band with r >= threshold (the band edges are never peaks)"""
        ip = self.inb[1:-1]
        return ip[(r[ip] > r[ip - 1]) & (r[ip] >= r[ip + 1]) & (r[ip] >= threshold)]

    def refine(self, f: np.ndarray, c: np.ndarray, order: Sequence[int]):
        """
        Coordinate descent over the terms in order: each frequency is shifted by -1, -2/3, -1/3, 1/3, 2/3 and 1
        bin (1/N), keeping the shift that lowers the chi2 of the joint fit the most, against the chi2 with the
        frequencies refined so far (no shift if none does; a later shift wins only if strictly better)
        """
        fitter = self.fitter
        f, c = f.copy(), c.copy()
        cur = fitter.fit(f, c).chi2
        for i in order:
            best, fb, cb = cur, f[i], c[:, i].copy()
            for d in self.SHIFTS / self.n_days:
                ct = c.copy()
                ct[:, i] = self.proj_cos(f[i] + d)[:, 0]
                trial = fitter.fit(f, ct)
                if trial.chi2 < best:
                    best, fb, cb = trial.chi2, f[i] + d, ct[:, i].copy()
            f[i], c[:, i], cur = fb, cb, best
        return f, c, fitter.fit(f, c)

    def run(self, reference: CovarianceFit) -> JointDetection:
        st = self.st
        fitter = self.fitter
        n_days = self.n_days
        fr = self.fr

        # 2. candidates: peaks of the misfit-spectrum ratio of the reference fit, strongest first, cand_sep / N
        # apart, at most cand_max; then skip those the ETM absorbs (projected cosine at lag 0 < keep_min)
        r0, s0, sb0 = self.misfit_ratio(reference)
        ip = self.local_peaks(r0, st.jhf_cand_ratio)
        ip = ip[np.argsort(-r0[ip], kind='stable')]
        fh = []
        for i in ip:
            if len(fh) >= st.jhf_cand_max:
                break
            if fh and np.any(np.abs(fr[i] - np.array(fh)) < st.jhf_cand_sep / n_days):
                continue
            fh.append(fr[i])
        fh = np.array(fh, dtype=float)
        ch = self.proj_cos(fh)
        keep = ch[0] >= st.jhf_keep_min
        fh, ch = fh[keep], ch[:, keep]
        fh0 = fh.copy()

        # 3. harmonic fit and refinement of the frequencies with variance > 0, strongest (w_P * C0) first
        harm = fitter.fit(fh, ch)
        if fh.size:
            order = np.argsort(-(harm.w_p * ch[0]), kind='stable')
            order = order[harm.w_p[order] > 0]
            fh, ch, harm = self.refine(fh, ch, order)
        wh = harm.w_p

        # 4. decision with the SNR in the harmonic fit
        qh = self.snr(harm)
        passed = (qh >= st.jhf_snr_min) & (wh > 0)

        # 5. refit with the kept periods; zero-variance terms drop out
        fc, cc = fh[passed], ch[:, passed]
        rf = fitter.fit(fc, cc)
        while fc.size and np.any(rf.w_p <= 0):
            k = rf.w_p > 0
            fc, cc = fc[k], cc[:, k]
            rf = fitter.fit(fc, cc)

        # 6. final check: peaks of the misfit ratio against the full model with R >= snr_min, more than half a
        # bin from the kept periods, are added at once; the added term with the lowest SNR (or any term with
        # variance 0) is removed until every added term passes; the added terms are then refined (not re-tested)
        r_check, _, _ = self.misfit_ratio(rf)
        fx = fr[self.local_peaks(r_check, st.jhf_snr_min)]
        if fc.size:
            fx = fx[np.all(np.abs(fx[:, None] - fc[None, :]) > self.dsep, axis=1)]
        found = fx.copy()
        added = np.array([])
        removed = []
        if fx.size:
            f2, c2 = np.concatenate([fc, fx]), np.hstack([cc, self.proj_cos(fx)])
            is_added = np.r_[np.zeros(fc.size, dtype=bool), np.ones(fx.size, dtype=bool)]
            r2 = fitter.fit(f2, c2)
            while f2.size:
                q2 = self.snr(r2)
                q2[~is_added] = np.inf
                q2[r2.w_p <= 0] = -np.inf
                j = int(np.argmin(q2))
                if q2[j] >= st.jhf_snr_min:
                    break
                removed.append(f2[j])
                f2, c2, is_added = np.delete(f2, j), np.delete(c2, j, axis=1), np.delete(is_added, j)
                r2 = fitter.fit(f2, c2)
            if is_added.any():
                f2, c2, r2 = self.refine(f2, c2, np.flatnonzero(is_added))
            added = np.setdiff1d(f2, fc)
            fc, cc, rf = f2, c2, r2

        # 7. outcomes of the candidates and SNR of the final periods
        outcomes = np.array(['below threshold'] * fh.size, dtype=object)
        outcomes[passed] = 'zero after refit'
        for i in range(fh.size):
            if fc.size and np.any(np.abs(fc - fh[i]) < 1e-12):
                outcomes[i] = 'accepted'
        outcomes[wh <= 0] = 'zero in harmonic fit'
        candidates = [JointCandidate(f0=float(a), f=float(b), w=float(w), snr=float(q), outcome=str(o))
                      for a, b, w, q, o in zip(fh0, fh, wh, qh, outcomes)]

        snr_harmonic = np.full(fc.size, np.nan)
        for i, fi in enumerate(fc):
            k = np.flatnonzero(np.abs(fh - fi) < 1e-12)
            if k.size:
                snr_harmonic[i] = qh[k[0]]

        action = ['added' if np.any(np.abs(added - fi) < 1e-12) else 'passed' for fi in fc]

        return JointDetection(final_fit=rf, snr=self.snr(rf), snr_harmonic=snr_harmonic, candidates=candidates,
                              harmonic_fit=harm, spec_f=fr, band=self.inb, spec_s=s0, spec_sigma2_bin=sb0,
                              spec_r=r0, check_r=r_check, check_found=found, check_added=added,
                              check_removed=np.array(removed), check_action=action)


@dataclass
class StepwiseCandidate:
    """Candidate periodic term of the stepwise grid search and its outcome (Section 8.3)"""
    index: int                  # index in the candidate grid
    f_cpy: float                # frequency [cycles/yr]
    dchi2: float                # scan peak of the reference fit
    snr: float                  # in the final fit (accepted) or when added to the final set (others)
    dropone_dchi2: float        # drop-one (accepted) or joint delta chi2 when added (others)
    outcome: str                # accepted, snr, joint, zero

    @property
    def period_d(self) -> float:
        return DAYS_PER_YEAR / self.f_cpy

    @property
    def outcome_code(self) -> int:
        return SGS_OUTCOME_CODES[self.outcome]


@dataclass
class StepwiseDetection:
    """Result of the stepwise grid search"""
    final_fit: CovarianceFit
    snr: np.ndarray             # SNR of the accepted periods in the final fit, in final-fit order
    candidates: List[StepwiseCandidate]
    dropone_dchi2: np.ndarray
    fgrid: np.ndarray           # candidate grid [cycles/yr]
    cg: np.ndarray              # projected candidate cosines, lags 0..Tmax
    scan_dchi2: np.ndarray      # scan of the reference fit
    scan_final: np.ndarray      # scan of the final fit (with the accepted periods)
    method: str = 'stepwise'


class StepwiseGridSearch:
    """
    Stepwise grid search (Section 8.3, stepwise_grid_search.m): candidates from a scan over a fixed grid of
    periods, fitted jointly and removed one at a time, with re-entry
    """
    def __init__(self, fitter: CovarianceFitter, fgrid: np.ndarray, cg: np.ndarray,
                 settings: NoiseCovarianceSettings):
        self.fitter = fitter
        self.fgrid = fgrid
        self.cg = cg
        self.st = settings
        # whitened candidate columns (fixed)
        self.cw = fitter.whiten(cg)
        self._cache: Dict[Tuple[int, ...], CovarianceFit] = {}

    def fit(self, sel: Sequence[int] = ()) -> CovarianceFit:
        sel = tuple(int(s) for s in sel)
        if sel not in self._cache:
            k = list(sel)
            self._cache[sel] = self.fitter.fit(self.fgrid[k] / DAYS_PER_YEAR, self.cg[:, k], self.cw[:, k])
        return self._cache[sel]

    def scan(self, sel: Sequence[int] = ()) -> np.ndarray:
        """
        Linearized drop in chi2 when a cosine (variance >= 0) is added at each grid frequency to the fit
        with the periods in sel (scan_stat)
        """
        fitter = self.fitter
        rf = self.fit(sel)
        # whitened misfit g - D_sel x, with the white variance white_min + s
        z = fitter.gw - rf.dw @ rf.x - fitter.white_min * rf.dw[:, -1]
        # active columns: w > 0, w_P > 0, and the white column only if it is above its minimum
        active = rf.dw[:, rf.x > 0]
        if active.shape[1]:
            q = orth(active)
            up = self.cw - q @ (q.T @ self.cw)
        else:
            up = self.cw.copy()

        nu = np.linalg.norm(up, axis=0)
        # periods already in the fit drop out
        ok = nu > 1e-6 * np.linalg.norm(self.cw, axis=0)
        proj = np.zeros(nu.size)
        proj[ok] = (up[:, ok].T @ z) / nu[ok]
        return np.maximum(proj, 0) ** 2

    def run(self) -> StepwiseDetection:
        st = self.st
        dchi2_min, snr_min = st.sgs_dchi2_min, st.sgs_snr_min

        def snr(rf):
            return self.fitter.snr(rf)

        # candidates: interior local maxima of the reference scan with dchi2 >= dchi2_min, tallest first
        d_ref = self.scan(())
        i = np.arange(1, d_ref.size - 1)
        peaks = i[(d_ref[i] > d_ref[i - 1]) & (d_ref[i] >= d_ref[i + 1])]
        peaks = peaks[d_ref[peaks] >= dchi2_min]
        sel0 = [int(p) for p in peaks[np.argsort(-d_ref[peaks], kind='stable')]]

        def drop_zero(sel, rf):
            while sel and np.any(rf.w_p <= 0):
                sel = [s for s, wp in zip(sel, rf.w_p) if wp > 0]
                rf = self.fit(sel)
            return sel, rf

        def prune(sel):
            sel, rf = drop_zero(list(sel), self.fit(sel))
            q, d1 = np.array([]), np.array([])
            while sel:
                d1 = np.array([self.fit(sel[:k] + sel[k + 1:]).chi2 - rf.chi2 for k in range(len(sel))])
                q = snr(rf)
                bad_q = q < snr_min
                bad_d = d1 < dchi2_min
                if not bad_q.any() and not bad_d.any():
                    break
                if bad_q.any():
                    k = np.flatnonzero(bad_q)[np.argmin(q[bad_q])]
                else:
                    k = np.flatnonzero(bad_d)[np.argmin(d1[bad_d])]
                sel = sel[:k] + sel[k + 1:]
                sel, rf = drop_zero(sel, self.fit(sel))
                q, d1 = np.array([]), np.array([])
            return sel, rf, q, d1

        def test_add(sel, rf, r):
            r2 = self.fit(sel + [r])
            return r2.w_p[-1], snr(r2)[-1], rf.chi2 - r2.chi2

        sel, rf, q, d1 = prune(sel0)

        # re-entry: makes the result independent of the order of the candidates. The guard stops a cycle
        # (re-entering a candidate can remove another, which then qualifies to re-enter, and so on)
        seen = [sorted(sel)]
        n_add = 0
        while n_add < 2 * len(sel0) + 2:
            best, best_d1 = None, -np.inf
            for r in sel0:
                if r in sel:
                    continue
                wp_r, q_r, d1_r = test_add(sel, rf, r)
                if wp_r > 0 and q_r >= snr_min and d1_r >= dchi2_min and d1_r > best_d1:
                    best, best_d1 = r, d1_r
            if best is None:
                break
            sel, rf, q, d1 = prune(sel + [best])
            n_add += 1
            if sorted(sel) in seen:
                logger.warning(f'Stepwise grid search: re-entry revisited the set {sorted(sel)}, stopping')
                break
            seen.append(sorted(sel))

        candidates = []
        for r in sel0:
            if r in sel:
                k = sel.index(r)
                outcome, q_r, d1_r = 'accepted', q[k], d1[k]
            else:
                wp_r, q_r, d1_r = test_add(sel, rf, r)
                if wp_r <= 0:
                    outcome = 'zero'
                elif q_r < snr_min:
                    outcome = 'snr'
                elif d1_r < dchi2_min:
                    outcome = 'joint'
                else:
                    outcome = 'unresolved'
            candidates.append(StepwiseCandidate(index=r, f_cpy=float(self.fgrid[r]), dchi2=float(d_ref[r]),
                                                snr=float(q_r), dropone_dchi2=float(d1_r), outcome=outcome))

        return StepwiseDetection(final_fit=rf, snr=q, candidates=candidates, dropone_dchi2=d1, fgrid=self.fgrid,
                                 cg=self.cg, scan_dchi2=d_ref, scan_final=self.scan(sel))


# ============================================================================
# Effective spectral index and GLS
# ============================================================================

@dataclass
class EffectiveAlpha:
    """Effective spectral index of a fitted power-law mix (Section 10). Band in cycles/yr"""
    alpha: float
    f_low: float
    f_high: float
    local_low: float
    local_high: float
    band_empty: bool
    at_nyquist: bool

    def as_array(self) -> np.ndarray:
        """[alpha, fL, fU, local(fL), local(fU), band_empty, at_nyquist] as in the test vectors"""
        return np.array([self.alpha, self.f_low, self.f_high, self.local_low, self.local_high,
                         float(self.band_empty), float(self.at_nyquist)])


def effective_alpha(w: np.ndarray, nugget: float, alphas: Sequence[float], n_days: int,
                    share: float = 0.1) -> EffectiveAlpha:
    """
    Mean log-slope of the power-law spectrum between 1/N and the frequency where the floor
    (lowest-alpha component + white) reaches `share` of the steep part (pl_alpha_eff.m)
    """
    alphas = np.asarray(alphas, dtype=float)
    w = np.asarray(w, dtype=float)
    n = n_days

    # variance of an un-normalized f^-alpha spectrum on the dcorr_pln grid -> spectral amplitudes
    fg = (0.85 * np.arange(1, n + 1) / n + 0.15 * np.arange(n) / n) / 2
    eta = np.array([np.mean(fg ** -a) for a in alphas])
    amp = w / eta

    low = alphas == alphas.min()
    ratio = (1 - share) / share

    def phi(f):
        return np.sum(amp * f ** -alphas)

    def local(f):
        p = phi(f)
        return np.sum(alphas * amp * f ** -alphas) / p if p > 0 else np.nan

    def steep(f):
        return np.sum(amp[~low] * f ** -alphas[~low])

    def floor(f):
        return np.sum(amp[low] * f ** -alphas[low]) + nugget

    f_low, f_nyq = 1 / n, 0.5
    band_empty = at_nyquist = False

    if steep(f_low) <= ratio * floor(f_low):
        # the steep part never dominates
        alpha = local(f_low)
        f_high = f_low
        band_empty = True
    else:
        if steep(f_nyq) >= ratio * floor(f_nyq):
            f_high = f_nyq
            at_nyquist = True
        else:
            # monotone in ln f
            x = brentq(lambda lf: np.log(steep(np.exp(lf))) - np.log(ratio * floor(np.exp(lf))),
                       np.log(f_low), np.log(f_nyq), xtol=1e-14, rtol=4 * np.finfo(float).eps)
            f_high = float(np.exp(x))
        alpha = (np.log(phi(f_low)) - np.log(phi(f_high))) / np.log(f_high / f_low)

    return EffectiveAlpha(alpha=float(alpha), f_low=f_low * DAYS_PER_YEAR, f_high=f_high * DAYS_PER_YEAR,
                          local_low=float(local(f_low)), local_high=float(local(f_high)),
                          band_empty=band_empty, at_nyquist=at_nyquist)


@dataclass
class GlsResult:
    """GLS refit of the ETM with a full covariance (Section 9)"""
    x: np.ndarray
    qx: np.ndarray                  # covariance of the parameters (not scaled by a variance factor)
    residuals: np.ndarray           # y - A x
    c_inv_v: np.ndarray             # C^-1 (y - A x), used for the collocation prediction
    quadratic_form: float           # v' C^-1 v
    redundancy: float               # n - tr(Qx A' C^-1 A) = n - u without constraints
    jitter: float = 0.
    rate: float = np.nan
    sigma_rate: float = np.nan
    # [colored, white, whole fit]; colored and white are NaN when not computed
    factors: np.ndarray = field(default_factory=lambda: np.full(3, np.nan))
    # [colored, white]
    redundancies: np.ndarray = field(default_factory=lambda: np.full(2, np.nan))

    def as_array(self) -> np.ndarray:
        """[rate, sigma, factor_colored, factor_white, factor_whole, jitter] as in the test vectors"""
        return np.array([self.rate, self.sigma_rate, *self.factors, self.jitter])


def gls_fit(t: np.ndarray, y: np.ndarray, a: np.ndarray, c_lag: np.ndarray, irate: Optional[int] = None,
            nugget: float = 0., variance_factors: bool = True,
            n_neq: Optional[np.ndarray] = None, c_neq: Optional[np.ndarray] = None) -> GlsResult:
    """
    GLS fit of y = A x with C_il = c_lag(|t_i - t_l|). Never forms C^-1. Optional constraints are added to
    the normal equations as N + n_neq, c + c_neq.
    """
    n, u = a.shape
    t = np.asarray(t, dtype=np.int32)

    def build(jitter: float) -> np.ndarray:
        c = c_lag[np.abs(np.subtract.outer(t, t))]
        if jitter:
            c[np.diag_indices(n)] += jitter
        return c

    # if the Cholesky fails, add 1e-8 * mean(diag(C)) * I, x10 until it works
    jitter = 0.
    while True:
        try:
            lc = cholesky(build(jitter), lower=True, overwrite_a=True, check_finite=False)
            break
        except LinAlgError:
            jitter = 1e-8 * c_lag[0] if jitter == 0 else jitter * 10
            if jitter > c_lag[0]:
                raise
    if jitter:
        logger.info(f'GLS: Cholesky needed a jitter of {jitter:.3e} ({jitter / c_lag[0]:.1e} of C(0))')

    # whitened design and observations in one triangular solve
    ayw = solve_triangular(lc, np.column_stack([a, y]), lower=True, check_finite=False)
    aw, yw = ayw[:, :u], ayw[:, u]
    naw = aw.T @ aw

    if n_neq is None or not np.any(n_neq):
        q, r = qr(aw, mode='economic')
        x = solve_triangular(r, q.T @ yw)
        ri = solve_triangular(r, np.eye(u))
        qx = ri @ ri.T
    else:
        nmat = naw + n_neq
        x = np.linalg.solve(nmat, aw.T @ yw + c_neq)
        qx = np.linalg.inv(nmat)

    v = y - a @ x
    # Lc^-1 v without another solve; then C^-1 A and C^-1 v together
    vw = yw - aw @ x
    wav = solve_triangular(lc, np.column_stack([aw, vw]), lower=True, trans='T', check_finite=False)
    wa, wv = wav[:, :u], wav[:, u]
    quad = float(vw @ vw)
    red = float(n - np.trace(qx @ naw))

    res = GlsResult(x=x, qx=qx, residuals=v, c_inv_v=wv, quadratic_form=quad, redundancy=red, jitter=jitter)
    res.factors[2] = quad / red

    if irate is not None:
        res.rate = float(x[irate])
        res.sigma_rate = float(np.sqrt(qx[irate, irate]))

    if variance_factors:
        # variance factors of the colored and white parts (Section 9, eq. 12)
        if nugget > 0:
            lc_inv = lapack.dtrtri(lc, lower=1, overwrite_c=1)[0]
            tr_w = float(np.einsum('ij,ij->', lc_inv, lc_inv))
            del lc_inv
            r_w = nugget * (tr_w - np.trace(qx @ (wa.T @ wa)))
            q_w = nugget * float(wv @ wv)
            res.factors[1] = q_w / r_w
        else:
            # no white noise: everything is colored and the white factor is undefined
            r_w = q_w = 0.
        r_c = red - r_w
        res.factors[0] = (quad - q_w) / r_c
        res.redundancies[:] = [r_c, r_w]

    return res


# ============================================================================
# Driver
# ============================================================================

@dataclass
class NoiseModelResult:
    """Everything the method computes for one component (Section 11)"""
    settings: NoiseCovarianceSettings
    t: np.ndarray                   # epochs, days from the first one
    n_days: int                     # N: span
    tmax: int                       # largest lag of the empirical covariance
    # step 1
    x0: np.ndarray
    v0: np.ndarray
    # empirical covariance and kernels
    n_tau: np.ndarray               # pair counts, lags 0..Tmax
    gamma: np.ndarray               # empirical covariance, lags 0..Tmax
    gb: np.ndarray                  # power-law kernels, lags 0..N-1
    gtf: np.ndarray                 # projected power-law kernels, lags 0..N-1
    htf: np.ndarray                 # projected white kernel, lags 0..N-1
    lags: np.ndarray                # fitted lags
    # fits
    fit0: CovarianceFit             # unweighted
    sigma_ct: np.ndarray            # model residual covariance used for Sigma, lags 0..N-1
    sigma: np.ndarray               # covariance of the empirical covariance at the fitted lags
    reference: CovarianceFit        # weighted, no periodic terms
    detection: Union[JointDetection, StepwiseDetection]
    # covariance functions at lags 0..N-1 (white noise included at lag 0)
    c_lag_final: np.ndarray
    colored_lag_final: np.ndarray   # power-law + periodic part (no white noise)
    signal_lag: np.ndarray          # covariance function of the predicted signal (periodic + steep power laws)
    # GLS of the ETM with the final covariance
    gls: GlsResult
    alpha_reference: EffectiveAlpha
    alpha_final: EffectiveAlpha
    signal: np.ndarray              # signal prediction on the daily grid 0..N-1
    timings: Dict[str, float] = field(default_factory=dict)

    @property
    def final(self) -> CovarianceFit:
        return self.detection.final_fit

    @property
    def periods_f(self) -> np.ndarray:
        """selected frequencies [cycles/yr], in final-fit order"""
        return self.final.f_cpy

    def projected_model(self, cf: Optional[CovarianceFit] = None) -> np.ndarray:
        """
        projected model covariance of a fit (default: the final fit) at lags 0..Tmax: what the empirical
        covariance of the residuals should look like
        """
        cf = cf or self.final
        t1 = self.tmax + 1
        model = self.gtf[:t1] @ cf.w + self.htf[:t1] * cf.nugget
        return model + cf.c @ cf.w_p if cf.w_p.size else model

    @property
    def weak_final_snr(self) -> np.ndarray:
        """final periods that passed in the harmonic fit but have SNR < snr_min in the final fit (joint only)"""
        det = self.detection
        if det.method != 'joint':
            return np.zeros(self.final.f.size, dtype=bool)
        return np.isfinite(det.snr_harmonic) & (det.snr < self.settings.jhf_snr_min)

    def flag_items(self) -> List[Tuple[str, str]]:
        """
        (kind, text) of the results worth a look when reviewing real data (CHANGES_joint_harmonic_fit.md,
        Section 6); kinds: final_snr, long_period, chi2
        """
        out = []
        fin, det = self.final, self.detection
        for f, q, q_h, weak in zip(fin.f, det.snr, getattr(det, 'snr_harmonic', fin.f), self.weak_final_snr):
            if weak:
                out.append(('final_snr', f'{1 / f:.0f} d: SNR {q:.1f} in the final fit (passed with {q_h:.1f} in the '
                                         f'harmonic fit)'))
        for f in fin.f:
            if 1 / f > FLAG_SPAN_FRACTION * self.n_days:
                out.append(('long_period', f'{1 / f:.0f} d: longer than a quarter of the span ({self.n_days} d), '
                                           f'may be quasi-periodic rather than steady'))
        chi2_dof = fin.chi2 / max(fin.dof, 1)
        if chi2_dof > FLAG_CHI2_DOF:
            out.append(('chi2', f'chi2/dof of the final fit {chi2_dof:.2f} > {FLAG_CHI2_DOF:g}: unmodeled signal '
                                f'left (e.g. a mismatched postseismic term)'))
        return out

    @property
    def flags(self) -> List[str]:
        return [text for _, text in self.flag_items()]

    def summary(self, scale: float = 1.) -> dict:
        """
        JSON-friendly report (Section 11) plus the data of the diagnostic figure. Variances are multiplied by
        scale ** 2 and rates by scale, to report in other units than those of the input (e.g. scale = 1000 to
        go from m to mm). Values that are not available (NaN) are None.
        """
        s2 = scale ** 2

        def num(x):
            """float, or None if not finite (JSON has no NaN)"""
            x = float(x)
            return x if np.isfinite(x) else None

        def nums(x):
            return [num(v) for v in np.asarray(x, dtype=float).ravel()]

        def fit_dict(cf: CovarianceFit) -> dict:
            return {'w': nums(cf.w * s2),
                    'colored': num(cf.colored_variance * s2),
                    'white': num(cf.nugget * s2),
                    'white_at_minimum': cf.white_at_min,
                    'chi2': num(cf.chi2),
                    'dof': cf.dof}

        def gls_dict(gr: GlsResult, c0: float) -> dict:
            return {'rate': num(gr.rate * scale),
                    'sigma': num(gr.sigma_rate * scale),
                    'factors_colored_white_whole': nums(gr.factors),
                    'redundancy_colored_white': nums(gr.redundancies),
                    # relative to C(0): the jitter is a tiny fraction of the variance
                    'jitter_relative': num(gr.jitter / c0)}

        def alpha_dict(ea: EffectiveAlpha) -> dict:
            return {'alpha': num(ea.alpha),
                    'band_cpy': nums([ea.f_low, ea.f_high]),
                    'local': nums([ea.local_low, ea.local_high]),
                    'band_empty': ea.band_empty,
                    'at_nyquist': ea.at_nyquist}

        fin, det, st = self.final, self.detection, self.settings
        periodic = [{'f_cpy': num(f * DAYS_PER_YEAR),
                     'period_d': num(1 / f),
                     'w_p': num(wp * s2),
                     'amplitude': num(np.sqrt(2 * wp) * scale),
                     'snr': num(q),
                     'survive': num(c0)}
                    for f, wp, q, c0 in zip(fin.f, fin.w_p, det.snr, fin.survive)]

        diagnostics = {'gamma': nums(self.gamma * s2),
                       'fit_reference': nums(self.projected_model(self.reference) * s2),
                       'fit_final': nums(self.projected_model() * s2),
                       'lags': self.lags.tolist()}

        out = {'method': 'power-law + white + periodic noise covariance (GeoDE_noise_method_spec)',
               'detection': det.method,
               'settings': {'alphas': list(st.alphas), 'lag_k': st.lag_k, 'white_min': num(st.white_min * s2)},
               'n_epochs': int(self.t.size),
               'span_days': self.n_days,
               'tau_max': self.tmax,
               'n_lags': int(self.lags.size),
               'unweighted_fit': fit_dict(self.fit0),
               'reference_fit': fit_dict(self.reference),
               'final_fit': fit_dict(fin),
               'periodic': periodic,
               'flags': self.flags}

        if det.method == 'joint':
            out['settings'].update({'snr_min': st.jhf_snr_min, 'cand_ratio': st.jhf_cand_ratio,
                                    'cand_sep': st.jhf_cand_sep, 'cand_max': st.jhf_cand_max,
                                    'keep_min': st.jhf_keep_min, 'min_cycles': st.jhf_min_cycles,
                                    'p_min': st.jhf_p_min, 'pad': st.jhf_pad})
            for p, q_h, action in zip(periodic, det.snr_harmonic, det.check_action):
                p['snr_harmonic_fit'] = num(q_h)
                p['final_check'] = action
            out['candidates'] = [{'f_cpy': num(c.f * DAYS_PER_YEAR),
                                  'period_d': num(c.period_d),
                                  'period_before_refinement_d': num(c.period0_d),
                                  'variance_harmonic_fit': num(c.w * s2),
                                  'snr_harmonic_fit': num(c.snr),
                                  'outcome': c.outcome} for c in det.candidates]
            out['harmonic_fit'] = fit_dict(det.harmonic_fit)
            out['final_check'] = {'found_period_d': nums(1 / det.check_found),
                                  'added_period_d': nums(1 / det.check_added),
                                  'removed_period_d': nums(1 / det.check_removed)}
            band = det.band
            diagnostics.update({'spec_f_cpy': nums(det.spec_f[band] * DAYS_PER_YEAR),
                                'spec_r_reference': nums(det.spec_r[band]),
                                'spec_r_final': nums(det.check_r[band])})
        else:
            out['settings'].update({'band_cpy': list(st.sgs_band), 'over': st.sgs_over,
                                    'dchi2_min': st.sgs_dchi2_min, 'snr_min': st.sgs_snr_min})
            out['n_candidate_frequencies'] = int(det.fgrid.size)
            out['candidates'] = [{'f_cpy': num(c.f_cpy),
                                  'period_d': num(c.period_d),
                                  'dchi2': num(c.dchi2),
                                  'snr': num(c.snr),
                                  'dropone_dchi2': num(c.dropone_dchi2),
                                  'outcome': c.outcome} for c in det.candidates]
            diagnostics.update({'fgrid_cpy': nums(det.fgrid),
                                'scan_reference': nums(det.scan_dchi2),
                                'scan_final': nums(det.scan_final)})

        out.update({'effective_alpha': {'reference': alpha_dict(self.alpha_reference),
                                        'final': alpha_dict(self.alpha_final)},
                    'gls': gls_dict(self.gls, self.c_lag_final[0]),
                    'timings': self.timings,
                    # data of the diagnostic figure: covariances at lags 0..Tmax [days], detection statistics
                    'diagnostics': diagnostics})
        return out


def estimate_noise_model(t: np.ndarray, y: np.ndarray, a: np.ndarray, p0: Optional[np.ndarray] = None,
                         irate: Optional[int] = None, settings: Optional[NoiseCovarianceSettings] = None,
                         n_neq: Optional[np.ndarray] = None,
                         c_neq: Optional[np.ndarray] = None) -> NoiseModelResult:
    """
    Run the method for one component.
        t       observed epochs, integer days, strictly increasing (gaps allowed)
        y       observations
        a       ETM design matrix (time columns in years, so the rate is per year)
        p0      step-1 weights 1 / sigma_obs ** 2 (default: equal weights)
        irate   index (0-based) of the rate column of a, only used for reporting
        n_neq, c_neq: optional constraints added to the normal equations of the ETM fits
    """
    settings = settings or NoiseCovarianceSettings()
    if settings.detection not in DETECTION_METHODS:
        raise ValueError(f'estimate_noise_model: unknown detection {settings.detection!r}, '
                         f'use one of {DETECTION_METHODS}')
    timings = {}
    tt = time()

    t = np.asarray(t).astype(np.int64)
    t = t - t[0]
    if np.any(np.diff(t) <= 0):
        raise ValueError('estimate_noise_model: epochs must be strictly increasing integer days')

    y = np.asarray(y, dtype=float)
    a = np.asarray(a, dtype=float)
    n, u = a.shape
    p0 = np.ones(n) if p0 is None else np.asarray(p0, dtype=float)
    has_constraints = n_neq is not None and np.any(n_neq)

    # step 1: WLS fit of the ETM (Section 2)
    ap = a * p0[:, None]
    nmat = a.T @ ap + (n_neq if has_constraints else 0)
    k0 = np.linalg.solve(nmat, ap.T)
    x0 = k0 @ y
    if has_constraints:
        x0 += np.linalg.solve(nmat, c_neq)
    v0 = y - a @ x0

    # pair structure and empirical covariance (Section 3)
    proj = EpochProjector(t, a, k0)
    n_days, tmax = proj.n_days, proj.tmax
    t1 = tmax + 1
    gamma = proj.empirical_covariance(v0, t1)
    timings['setup'] = time() - tt

    # kernels and projected kernels at all lags 0..N-1 (Sections 4, 5)
    tt = time()
    gb = np.column_stack([dcorr_pln(n_days, alpha) for alpha in settings.alphas])
    delta = np.zeros(n_days)
    delta[0] = 1.
    kt = proj.project(np.column_stack([gb, delta]))
    gtf, htf = kt[:, :-1], kt[:, -1]
    timings['kernels'] = time() - tt

    # lag set and unweighted fit (Sections 6, 7.1)
    tt = time()
    lags = geo_lags(settings.lag_k, tmax)
    d = np.column_stack([gtf[lags], htf[lags]])
    g = gamma[lags]
    valid = np.isfinite(g) & np.all(np.isfinite(d), axis=1)
    if not np.all(valid):
        logger.debug(f'estimate_noise_model: dropping {np.sum(~valid)} lags without pairs')
        lags, d, g = lags[valid], d[valid], g[valid]

    na = gb.shape[1]
    fit0 = fit_white_min(d, g, settings.white_min, na)

    # weight matrix Sigma from the unweighted fit, held fixed (Section 7.2)
    ct = gtf @ fit0.w + htf * fit0.nugget
    ct[~np.isfinite(ct)] = 0.
    sigma = proj.gamma_covariance(lags, ct, settings.fft_workers)
    timings['sigma'] = time() - tt

    # weighted reference fit (Section 7.3) and detection of periodic terms (Section 8)
    tt = time()
    fitter = CovarianceFitter(d, g, sigma, settings.eps, gb, lags, settings.white_min)
    reference = fitter.fit()
    if settings.detection == 'joint':
        detection = JointHarmonicFit(fitter, proj, gamma, gtf[:t1], htf[:t1], settings).run(reference)
    else:
        fgrid = candidate_grid(tmax, settings.sgs_band, settings.sgs_over)
        detection = StepwiseGridSearch(fitter, fgrid, proj.project_cosines(fgrid, t1), settings).run()
    fin = detection.final_fit
    timings['detection'] = time() - tt

    # covariance function with the unprojected kernels (Section 9)
    tt = time()
    tau = np.arange(n_days)
    colored = gb @ fin.w
    for f, wp in zip(fin.f, fin.w_p):
        colored += wp * np.cos(2 * np.pi * f * tau)
    c_fin = colored.copy()
    c_fin[0] += fin.nugget

    # GLS of the ETM with the final covariance only: the spec's refit with the reference covariance (no periodic
    # terms) is not done, it is not a useful diagnostic and doubles the cost of this step
    gls = gls_fit(t, y, a, c_fin, irate, fin.nugget, settings.variance_factors,
                  n_neq if has_constraints else None, c_neq)
    timings['gls'] = time() - tt

    # effective spectral index (Section 10)
    alpha_reference = effective_alpha(reference.w, reference.nugget, settings.alphas, n_days, settings.share)
    alpha_final = effective_alpha(fin.w, fin.nugget, settings.alphas, n_days, settings.share)

    # collocation prediction of the signal on the daily grid: C_s(grid, t) C^-1 v. The signal is the periodic
    # terms plus the power laws with alpha >= signal_alpha_min (flicker and steeper); flatter power laws and the
    # white noise stay in the residuals. A flat power law in the signal makes it follow the day-to-day scatter
    # (alpha = 0.75 has 55% of its variance at periods shorter than 30 d, alpha = 1 30%, alpha = 1.5 3%)
    in_signal = np.asarray(settings.alphas) >= settings.signal_alpha_min
    signal_lag = gb[:, in_signal] @ fin.w[in_signal] + (colored - gb @ fin.w)
    fft_len = proj.fft_len
    signal = np.fft.irfft(np.fft.rfft(circular_embed(signal_lag, fft_len)) *
                          np.fft.rfft(to_grid(t, gls.c_inv_v, n_days), fft_len), fft_len)[:n_days]

    return NoiseModelResult(settings=settings, t=t, n_days=n_days, tmax=tmax, x0=x0, v0=v0,
                            n_tau=proj.n_tau[:t1], gamma=gamma, gb=gb,
                            gtf=gtf, htf=htf, lags=lags, fit0=fit0, sigma_ct=ct, sigma=sigma,
                            reference=reference, detection=detection,
                            c_lag_final=c_fin, colored_lag_final=colored, signal_lag=signal_lag, gls=gls,
                            alpha_reference=alpha_reference, alpha_final=alpha_final, signal=signal,
                            timings=timings)
