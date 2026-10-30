"""
Escalating-jitter Cholesky of the noise model (geode.etm.least_squares.noise_covariance): the weight matrix
Sigma and the GLS covariance. These tests do not need the pgamit test vectors.
"""

import numpy as np
import pytest
from scipy.linalg import LinAlgError

from geode.etm.least_squares.noise_covariance import (cholesky_jitter, CovarianceFitter, gls_fit,
                                                      NoiseCovarianceSettings)


def indefinite(n: int, min_eig: float, seed: int = 1) -> np.ndarray:
    """correlation-like matrix (unit diagonal) with one eigenvalue at min_eig, the others positive"""
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.normal(size=(n, n)))
    ev = np.r_[min_eig, rng.uniform(0.2, 2., n - 1)]
    s = (q * ev) @ q.T
    d = np.sqrt(np.diag(s))
    return s / np.outer(d, d)


def relative_build(s: np.ndarray):
    def build(jitter):
        out = s.copy()
        out[np.diag_indices(s.shape[0])] += jitter * np.diag(s)
        return out
    return build


def test_positive_definite_needs_no_jitter():
    s = indefinite(40, 0.05)
    lc, jitter = cholesky_jitter(relative_build(s), 'test')
    assert jitter == 0
    np.testing.assert_allclose(lc @ lc.T, s, atol=1e-12)


def test_ladder_stops_at_the_first_jitter_that_works():
    # like IGS.PECE N/E: one eigenvalue of the correlation matrix of Sigma at about -0.6%
    s = indefinite(110, -6e-3)
    lc, jitter = cholesky_jitter(relative_build(s), 'test')
    assert jitter == pytest.approx(1e-2)
    np.testing.assert_allclose(lc @ lc.T, relative_build(s)(jitter), atol=1e-12)
    # a smaller start and factor give the first value of their own ladder that works
    _, jitter = cholesky_jitter(relative_build(s), 'test', start=1e-3, factor=2.)
    assert jitter == pytest.approx(8e-3)


def test_beyond_the_maximum_raises():
    s = indefinite(30, -0.5)
    with pytest.raises(LinAlgError, match='Sigma is not positive definite even with a jitter of 0.1'):
        cholesky_jitter(relative_build(s), 'Sigma', max_jitter=0.1)


def test_fitter_with_indefinite_sigma():
    k, n_days = 60, 400
    rng = np.random.default_rng(2)
    # lags with different variances (as in Sigma); the relative jitter does not depend on them
    scale = rng.uniform(0.5, 2., k)
    sigma = indefinite(k, -6e-3) * np.outer(scale, scale)
    d = np.abs(rng.normal(size=(k, 6)))
    g = d @ np.r_[1., 0.5, 0, 0, 0, 0.3]
    gb = np.ones((n_days, 5))
    st = NoiseCovarianceSettings()
    fitter = CovarianceFitter(d, g, sigma, st.eps, gb, np.arange(k), st.white_min, st.jitter_start,
                              st.jitter_factor, st.sigma_jitter_max)
    assert fitter.jitter == pytest.approx(1e-2)
    assert np.isfinite(fitter.fit().chi2)

    # the same Sigma made positive definite needs no jitter
    ev, vec = np.linalg.eigh(sigma)
    fitter = CovarianceFitter(d, g, (vec * np.maximum(ev, 1e-3)) @ vec.T, st.eps, gb, np.arange(k))
    assert fitter.jitter == 0

    # a broken Sigma is not hidden: beyond the maximum, and non-finite values, raise
    with pytest.raises(LinAlgError):
        CovarianceFitter(d, g, sigma - 0.9 * np.eye(k) * np.diag(sigma).max(), st.eps, gb, np.arange(k))
    bad = sigma.copy()
    bad[3, 4] = np.nan
    with pytest.raises(ValueError):
        CovarianceFitter(d, g, bad, st.eps, gb, np.arange(k))


def test_gls_jitter_unchanged():
    """gls_fit: no jitter for a positive definite covariance; the same ladder (1e-8, x10, up to C(0)) otherwise"""
    n_days = 200
    t = np.arange(n_days)
    a = np.column_stack([np.ones(n_days), t / 365.25])
    y = 2 + 3 * t / 365.25 + np.random.default_rng(3).normal(size=n_days)
    tau = np.arange(n_days)
    res = gls_fit(t, y, a, 1. * (tau == 0) + 0.5 * np.exp(-tau / 30), irate=1)
    assert res.jitter == 0
    # a Gaussian covariance function is numerically indefinite (min eigenvalue -1e-14 C(0)): first rung of the ladder
    gauss = 2. * np.exp(-(tau / 30.) ** 2)
    res = gls_fit(t, y, a, gauss, irate=1)
    assert res.jitter == pytest.approx(1e-8 * gauss[0])
    # far from positive definite (boxcar): not hidden, it raises beyond C(0)
    with pytest.raises(LinAlgError, match='the GLS covariance is not positive definite'):
        gls_fit(t, y, a, (tau < 20).astype(float), irate=1)
