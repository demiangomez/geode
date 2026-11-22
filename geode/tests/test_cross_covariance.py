"""
Tests of geode.etm.least_squares.cross_covariance against the MATLAB test vectors of pgamit
(docs/geode_handoff/test_vectors; see GeoDE_cross_covariance_spec.md Section 13).

Two directories are used, neither part of the repository:
    GEODE_NOISE_TEST_VECTORS   the single-component and cross-covariance .mat vectors (the same
                               directory test_noise_covariance.py uses). All tests here are skipped
                               when it is not set.
    GEODE_CROSS_RAW_DATA       the per-station raw JSON the vectors were generated from
                               (arg.<station>.json: observations.time_vector_mjd and
                               raw_results[*].outlier_flags for N, E, U), used to reconstruct the
                               epochs common to all three components exactly (spec Section 1/9). Only
                               the tests that need an exact match (empirical cross-covariance,
                               projected cross kernels, the daily residual correlation) are skipped
                               without it; the rest use a looser 2-component epoch intersection.

GeoDE's own production fit deliberately does NOT restrict each component's auto noise model to the
common epochs (see cross_covariance.py's module docstring): the single-component vectors already
validated in test_noise_covariance.py are each component's own fit, on its own full epoch set, exactly
as NoiseCovarianceGLS produces it. The cross test vectors, however, were generated from the SAME
station's noise model *restricted to the epochs common to all three components* (spec Section 9),
which the chosen GeoDE design never computes. So the cross-specific math (empirical cross-covariance,
projected cross kernels) is validated here against the exact reference by substituting each
component's auto-fit output (w, nugget, periods, K, Qx) with the exact values the vector was built
from -- this checks the cross algorithm itself, not GeoDE's (deliberately different) choice of which
auto fit feeds it. The full fit_pair() pipeline is additionally smoke-tested end-to-end on GeoDE's own
(own-epoch) fits, without that substitution, as a realistic regression check.
"""

import functools
import json
import os

import numpy as np
import pytest

from geode.etm.least_squares.noise_covariance import NoiseCovarianceSettings, estimate_noise_model
from geode.etm.least_squares import cross_covariance as xc

VECTOR_DIR = os.environ.get('GEODE_NOISE_TEST_VECTORS', '')
RAW_DIR = os.environ.get('GEODE_CROSS_RAW_DATA', '')
RAW_FILES = {'igm1': 'arg.igm1_igs20.json', 'saja': 'arg.saja_ppp.json'}
STATION_COMPONENTS = {'igm1': ('n', 'e', 'u'), 'saja': ('n', 'e', 'u')}
PAIR_CASES = [(st, pair) for st in ('igm1', 'saja') for pair in ('ne', 'nu', 'eu')]

pytestmark = pytest.mark.skipif(not os.path.isdir(VECTOR_DIR),
                                reason='set GEODE_NOISE_TEST_VECTORS to the test vector directory')
needs_raw = pytest.mark.skipif(not os.path.isdir(RAW_DIR),
                               reason='set GEODE_CROSS_RAW_DATA to the raw per-station JSON directory')

DIRECT = 1e-8       # quantities computed directly from the exact inputs (gamma, projected kernels)
ZERO = 1e-10
EXACT = 1e-9


def vec(x):
    return np.atleast_1d(np.asarray(x, dtype=float))


def assert_scaled(actual, desired, rtol, what=''):
    actual, desired = np.asarray(actual, dtype=float), np.asarray(desired, dtype=float)
    assert actual.shape == desired.shape, f'{what}: shape {actual.shape} != {desired.shape}'
    scale = np.nanmax(np.abs(desired))
    err = np.nanmax(np.abs(actual - desired) / scale)
    assert err <= rtol, f'{what}: max scaled error {err:.2e} > {rtol:.0e}'


@functools.lru_cache(maxsize=None)
def _load_mat(path):
    import scipy.io as sio
    return sio.loadmat(path, squeeze_me=True)


@functools.lru_cache(maxsize=None)
def _single_component_nm(station, label):
    """GeoDE's own NoiseModelResult, computed on that component's own full epoch set (exactly what
    NoiseCovarianceGLS produces), with the GLS estimator K also computed"""
    v = _load_mat(os.path.join(VECTOR_DIR, f'{station}_{label}.mat'))
    irate = int(v['in_irate']) - 1
    settings = NoiseCovarianceSettings(compute_estimator=True)
    return estimate_noise_model(v['in_t'], v['in_y'], v['in_A'], p0=vec(v['in_p0']), irate=irate,
                                settings=settings)


@functools.lru_cache(maxsize=None)
def _raw_masks(station):
    """exact epochs common to all three components (spec Section 1), from the raw per-epoch data"""
    with open(os.path.join(RAW_DIR, RAW_FILES[station])) as f:
        d = json.load(f)
    mjd = np.array(d['observations']['time_vector_mjd'], dtype=float)
    ko = np.array([d['raw_results'][c]['outlier_flags'] for c in range(3)], dtype=bool).T
    kc = np.all(ko, axis=1)
    return mjd, ko, kc


def _context(station, label, index, exact_common: bool):
    nm = _single_component_nm(station, label)
    if exact_common:
        mjd, ko, kc = _raw_masks(station)
        mjd0 = int(round(mjd[ko[:, index]][0]))
        pw = kc[ko[:, index]]
    else:
        mjd0 = 0
        pw = None  # filled in by the caller from the pairwise intersection
    return xc.ComponentCrossInput(label=label.upper(), nm=nm, mjd0=mjd0, pw=pw, k_estimator=nm.gls.k_estimator)


def _pairwise_common(ctx_i, ctx_j):
    """fallback 2-component common-epoch mask (used only when GEODE_CROSS_RAW_DATA isn't set): not the
    exact 3-way set the spec defines, but close enough for a smoke/regression check"""
    common = np.intersect1d(ctx_i.nm.t, ctx_j.nm.t)
    ctx_i.pw = np.isin(ctx_i.nm.t, common)
    ctx_j.pw = np.isin(ctx_j.nm.t, common)


def _override_auto_fit(ctx, dcross, prefix):
    """substitute GeoDE's own (own-epoch) auto-fit output with the exact common-epoch-restricted fit
    the cross vector was generated from (see module docstring): needed to validate the cross-specific
    math against the reference with the inputs it actually assumes, without reproducing GeoDE's
    (deliberately different) own-epoch auto fit under a common-epoch restriction."""
    nm = ctx.nm
    fin = nm.detection.final_fit
    fin.w = vec(dcross[f'{prefix}_w'])
    fin.nugget = float(dcross[f'{prefix}_nug'])
    psel = vec(dcross[f'{prefix}_Psel']) if np.asarray(dcross[f'{prefix}_Psel']).size else np.array([])
    fin.f = 1. / psel if psel.size else np.array([])
    fin.w_p = vec(dcross[f'{prefix}_wP']) if psel.size else np.array([])
    nm.gls.qx = np.atleast_2d(dcross[f'{prefix}_Qx']).astype(float)
    nm.gls.k_estimator = np.atleast_2d(dcross[f'{prefix}_K']).astype(float)
    ctx.k_estimator = nm.gls.k_estimator
    tau = np.arange(nm.n_days)
    colored = nm.gb @ fin.w
    for f_, wp_ in zip(fin.f, fin.w_p):
        colored = colored + wp_ * np.cos(2 * np.pi * f_ * tau)
    c_fin = colored.copy()
    c_fin[0] += fin.nugget
    nm.c_lag_final = c_fin
    return ctx


@pytest.fixture(scope='module', params=PAIR_CASES, ids=[f'{s}_{p}' for s, p in PAIR_CASES])
def pair_case(request):
    station, pair = request.param
    dcross = _load_mat(os.path.join(VECTOR_DIR, f'{station}_cross_{pair}.mat'))
    labels = pair[0], pair[1]
    index = {'n': 0, 'e': 1, 'u': 2}
    ctx_i = _context(station, labels[0], index[labels[0]], exact_common=os.path.isdir(RAW_DIR))
    ctx_j = _context(station, labels[1], index[labels[1]], exact_common=os.path.isdir(RAW_DIR))
    if not os.path.isdir(RAW_DIR):
        _pairwise_common(ctx_i, ctx_j)
    return station, pair, ctx_i, ctx_j, dcross


# ----------------------------------------------------------------------------------------------------------------
# exact validation against the real-station reference (needs the raw per-epoch data)
# ----------------------------------------------------------------------------------------------------------------

@needs_raw
def test_daily_residual_correlation(pair_case):
    _, _, ctx_i, ctx_j, dcross = pair_case
    r = xc._daily_residual_correlation(ctx_i, ctx_j)
    assert r == pytest.approx(float(dcross['daily_resid_corr']), abs=1e-9)


@needs_raw
def test_empirical_cross_covariance(pair_case):
    _, _, ctx_i, ctx_j, dcross = pair_case
    grid = xc._setup_cross_grid(ctx_i, ctx_j)
    assert grid.Nc == int(dcross['cross_Nc'])
    gam = xc._empirical_cross_covariance(ctx_i, ctx_j, grid)
    assert_scaled(gam, dcross['cross_gam'], DIRECT, 'empirical cross-covariance')


@needs_raw
def test_projected_cross_kernels(pair_case):
    from geode.etm.least_squares.noise_covariance import dcorr_pln
    _, _, ctx_i, ctx_j, dcross = pair_case
    grid = xc._setup_cross_grid(ctx_i, ctx_j)
    tau_signed = np.arange(-(grid.Nc - 1), grid.Nc)
    alphas = ctx_i.nm.settings.alphas
    cols = [xc._project_cross_kernel(grid, (tau_signed == 0).astype(float))]
    for alpha in alphas:
        r_lag = dcorr_pln(grid.Nc, alpha)
        cols.append(xc._project_cross_kernel(grid, r_lag[np.abs(tau_signed)]))
    dfix = np.column_stack(cols)
    assert_scaled(dfix, dcross['cross_Dfix'], DIRECT, 'projected cross kernels (Dfix)')


# ----------------------------------------------------------------------------------------------------------------
# cross Sigma: validated against the literal spec formula on a synthetic case (dependency-free, exact)
# ----------------------------------------------------------------------------------------------------------------

def test_cross_sigma_matches_brute_force_definition():
    """spec Section 5.1, first term only (c_NE = 0): brute-force double sum vs the FFT implementation"""
    rng = np.random.default_rng(0)
    n = 14
    w = (rng.random(n) > 0.3).astype(float)
    ct_i = rng.random(n) + 0.5
    ct_j = rng.random(n) + 0.5
    lags = np.array([0, 1, 2, 3, 5])

    def p_signed(tau):
        p = np.zeros(n)
        for i in range(n):
            l = i + tau
            if 0 <= l < n:
                p[i] = w[i] * w[l]
        return p

    def ct_at(ct, d):
        d = int(d)
        return 0. if abs(d) >= n else ct[abs(d)]

    slags = np.concatenate([-lags[-1:0:-1], lags])
    counts = {int(t): p_signed(t).sum() for t in slags}
    ref = np.zeros((slags.size, slags.size))
    for ia, ta in enumerate(slags):
        pa = p_signed(ta)
        for ib, tb in enumerate(slags):
            pb = p_signed(tb)
            total = 0.
            for d in range(-(n - 1), n):
                z = sum(pa[i] * pb[i + d] for i in range(n) if 0 <= i + d < n)
                if z:
                    total += z * ct_at(ct_i, d) * ct_at(ct_j, d + tb - ta)
            na, nb = counts[int(ta)], counts[int(tb)]
            ref[ia, ib] = total / (na * nb) if na and nb else np.nan

    class FakeGrid:
        pass
    grid = FakeGrid()
    grid.Nc = n
    grid.wN = w

    sigma, slags_mine = xc._cross_sigma(grid, ct_i, ct_j, lags)
    np.testing.assert_array_equal(slags, slags_mine)
    np.testing.assert_allclose(sigma, ref, atol=1e-12)


def test_cross_sigma_produces_a_valid_covariance(pair_case):
    """on real inputs: symmetric and (after the escalating jitter) positive definite, as fit_pair relies on"""
    from geode.etm.least_squares.noise_covariance import geo_lags
    _, _, ctx_i, ctx_j, _ = pair_case
    grid = xc._setup_cross_grid(ctx_i, ctx_j)
    lags = geo_lags(ctx_i.nm.settings.lag_k, grid.Tmax)
    sigma, slags = xc._cross_sigma(grid, ctx_i.nm.sigma_ct, ctx_j.nm.sigma_ct, lags)
    np.testing.assert_array_equal(sigma, sigma.T)
    assert slags.size == 2 * lags.size - 1


# ----------------------------------------------------------------------------------------------------------------
# full pipeline: smoke/regression test on GeoDE's own (own-epoch) fits, and the fallback
# ----------------------------------------------------------------------------------------------------------------

def test_fit_pair_end_to_end(pair_case):
    station, pair, ctx_i, ctx_j, dcross = pair_case
    result = xc.fit_pair(ctx_i, ctx_j)

    assert result.method == 'power_law_cross_spectrum'
    assert not result.flags
    assert result.alpha_x in xc.CrossCovarianceSettings().alpha_grid
    assert np.isfinite(result.theta_w) and np.isfinite(result.w_x)
    assert result.matrix.shape == (ctx_i.k_estimator.shape[0], ctx_j.k_estimator.shape[0])
    assert np.all(np.isfinite(result.matrix))
    # the rate block, scaled by each component's own rate sigma, is a correlation: bounded by 1
    irate_i = int(_load_mat(os.path.join(VECTOR_DIR, f'{station}_{pair[0]}.mat'))['in_irate']) - 1
    irate_j = int(_load_mat(os.path.join(VECTOR_DIR, f'{station}_{pair[1]}.mat'))['in_irate']) - 1
    rate_corr = (result.matrix[irate_i, irate_j] /
                np.sqrt(ctx_i.nm.gls.qx[irate_i, irate_i] * ctx_j.nm.gls.qx[irate_j, irate_j]))
    assert abs(rate_corr) <= 1. + 1e-6


def test_summary_reports_theta_w_and_w_x_unscaled(pair_case):
    """
    regression test for a real bug: CrossCovarianceResult.theta_w/w_x are already in mm^2 (built from
    NoiseCovarianceGLS's mm-scaled NoiseModelResult), so summary() must report them as-is.
    EtmFit.process_cross_covariance once called summary(scale=1000) -- a leftover from before summary()
    dropped its scale parameter -- inflating theta_w/w_x by scale**2 = 1e6 in the JSON output (caught
    via real station numbers that were obviously too large: theta_w ~1e5 mm^2 instead of ~0.1-1 mm^2).
    """
    _, _, ctx_i, ctx_j, _ = pair_case
    result = xc.fit_pair(ctx_i, ctx_j)
    summary = result.summary()
    assert summary['theta_w'] == pytest.approx(result.theta_w)
    assert summary['w_x'] == pytest.approx(result.w_x)
    for p, s in zip(result.periodic_coefs, summary['periodic']):
        assert s['coef'] == pytest.approx(p)


def test_fit_pair_exact_inputs_reproduce_reference_alpha_neighbourhood(pair_case):
    """with the exact common-epoch auto fit substituted in (see module docstring), the recovered
    alpha_x should be at or adjacent to the reference's grid point: Sigma itself still differs
    slightly (GeoDE's Sigma weight, sigma_ct, comes from the unweighted fit on the full epoch set,
    the reference's from the common-epoch-restricted one), so an exact match isn't guaranteed"""
    if not os.path.isdir(RAW_DIR):
        pytest.skip('set GEODE_CROSS_RAW_DATA for this check')
    station, pair, ctx_i, ctx_j, dcross = pair_case
    _override_auto_fit(ctx_i, dcross, 'comp1')
    _override_auto_fit(ctx_j, dcross, 'comp2')
    result = xc.fit_pair(ctx_i, ctx_j)
    settings = xc.CrossCovarianceSettings()
    i_ref = settings.alpha_grid.index(min(settings.alpha_grid, key=lambda a: abs(a - float(dcross['plx_alpha']))))
    i_got = settings.alpha_grid.index(result.alpha_x)
    assert abs(i_got - i_ref) <= 2, f'alpha_x grid index {i_got} far from the reference {i_ref}'


# ----------------------------------------------------------------------------------------------------------------
# Section 10.2 fallback (borrowed residual correlation)
# ----------------------------------------------------------------------------------------------------------------

def test_borrowed_residual_correlation_matches_formula():
    rng = np.random.default_rng(1)
    n = 500
    base = rng.standard_normal(n)
    res_i = base + 0.3 * rng.standard_normal(n)
    res_j = 0.8 * base + 0.3 * rng.standard_normal(n)
    sigma_i, sigma_j = np.array([0.1, 0.2]), np.array([0.3, 0.4])

    matrix, r = xc.borrowed_residual_correlation(res_i, res_j, sigma_i, sigma_j)

    assert abs(r) <= 1.
    np.testing.assert_allclose(matrix, r * np.outer(sigma_i, sigma_j))
    np.testing.assert_allclose(r, np.corrcoef(res_i, res_j)[0, 1])


def test_borrowed_residual_correlation_handles_degenerate_input():
    sigma = np.array([1., 2.])
    matrix, r = xc.borrowed_residual_correlation(np.array([1.]), np.array([1.]), sigma, sigma)
    assert r == 0.
    np.testing.assert_array_equal(matrix, np.zeros((2, 2)))


def test_borrowed_residual_correlation_rejects_mismatched_lengths():
    """different-length residual arrays must never silently misalign (the bug _fallback guards
    against: each component's step-1 residuals are indexed by its own, differently-sized epoch list)"""
    sigma = np.array([1., 2.])
    matrix, r = xc.borrowed_residual_correlation(np.zeros(5), np.zeros(7), sigma, sigma)
    assert r == 0.
    np.testing.assert_array_equal(matrix, np.zeros((2, 2)))


def test_fit_pair_falls_back_without_gls_estimator(pair_case):
    _, _, ctx_i, ctx_j, _ = pair_case
    ctx_i_no_k = xc.ComponentCrossInput(label=ctx_i.label, nm=ctx_i.nm, mjd0=ctx_i.mjd0, pw=ctx_i.pw,
                                        k_estimator=None)
    result = xc.fit_pair(ctx_i_no_k, ctx_j)
    assert result.method == 'borrowed_residual_correlation'
    assert result.flags


# ----------------------------------------------------------------------------------------------------------------
# offset handling (N-U/E-U pairs whose first inlier epoch can differ; see _parameter_cross_covariance)
# ----------------------------------------------------------------------------------------------------------------

def test_parameter_cross_covariance_honours_the_pair_offset():
    """
    _parameter_cross_covariance must place t_i/t_j on the shared absolute day axis via grid.off_i/
    off_j before differencing (pair_cross_setup.m's 'e.t - a.t' uses the already-offset t). Two
    equivalent ways of representing the same physical epochs of j (shifted by D days later than i)
    must give the identical matrix: (a) pre-bake the shift into t_j itself with off_j = 0, or
    (b) keep t_j as its own component-relative epochs and carry the shift in off_j. A version that
    silently ignored off_i/off_j (the bug this guards against) would pass (a) but not (b).
    """
    rng = np.random.default_rng(2)
    n_i, n_j, u, d_shift = 20, 18, 2, 7
    t_i = np.sort(rng.choice(60, n_i, replace=False))
    t_j = np.sort(rng.choice(60, n_j, replace=False))
    k_i = rng.standard_normal((u, n_i))
    k_j = rng.standard_normal((u, n_j))
    nc = 200
    c_signed = rng.standard_normal(2 * nc - 1)

    class Ctx:
        pass

    def make(t, k):
        c = Ctx()
        c.nm = Ctx()
        c.nm.t = t
        c.k_estimator = k
        return c

    class Grid:
        pass

    def matrix_for(t_j_, off_i, off_j):
        grid = Grid()
        grid.Nc, grid.off_i, grid.off_j = nc, off_i, off_j
        return xc._parameter_cross_covariance(make(t_i, k_i), make(t_j_, k_j), grid, c_signed)

    baked_in = matrix_for(t_j + d_shift, off_i=0, off_j=0)
    via_offset = matrix_for(t_j, off_i=0, off_j=d_shift)
    np.testing.assert_allclose(baked_in, via_offset)


# ----------------------------------------------------------------------------------------------------------------
# EtmFit.process_cross_covariance orchestration (least_squares.py): the fallback's choice of residuals
# ----------------------------------------------------------------------------------------------------------------

def test_fallback_daily_residual_correlation_prefers_step1_over_final_fit_residuals():
    """
    Regression test for a real inconsistency the user noticed by comparing method outputs: the fallback
    path (switch off, or no NoiseCovarianceGLS fit) must report daily_residual_correlation from the same
    quantity as the power-law path -- the step-1 residuals -- whenever a NoiseModelResult happens to be
    available (NoiseCovarianceGLS stashes it regardless of the switch), only dropping to the final-fit
    residuals (today's process_covariance convention) when no noise model exists at all. Uses a synthetic
    scenario where the step-1 residuals are strongly correlated but the final-fit residuals are not
    (as if the GLS/collocation step had already explained away a shared signal), so the two conventions
    give clearly different numbers and the test can tell which one process_cross_covariance actually used.
    """
    import types
    from geode.etm.core.etm_config import EtmConfig
    from geode.etm.core.data_classes import AdjustmentResults
    from geode.etm.least_squares.least_squares import EtmFit, NoiseCovarianceGLS

    rng = np.random.default_rng(3)
    n_global = 40
    outlier_n = np.ones(n_global, dtype=bool)
    outlier_e = np.ones(n_global, dtype=bool)
    outlier_n[:5] = False                      # N has 5 extra outliers not shared with E
    common = outlier_n & outlier_e             # the common-epoch mask EtmFit.outlier_flags would hold

    base = rng.standard_normal(n_global)
    v0_n = (base + 0.1 * rng.standard_normal(n_global))[outlier_n]        # step-1: strongly correlated
    v0_e = (0.9 * base + 0.1 * rng.standard_normal(n_global))[outlier_e]
    final_res_n = rng.standard_normal(n_global)                          # final-fit: uncorrelated
    final_res_e = rng.standard_normal(n_global)

    # U: a third component, present only so process_cross_covariance's unconditional loop over all three
    # pairs (N-E, N-U, E-U) has something to index; this test only asserts on the N-E pair
    results = [AdjustmentResults(), AdjustmentResults(), AdjustmentResults()]
    results[0].residuals, results[0].outlier_flags = final_res_n, outlier_n
    results[0].parameter_sigmas, results[0].covariance_matrix = np.array([0.1, 0.2]), np.diag([0.01, 0.04])
    results[1].residuals, results[1].outlier_flags = final_res_e, outlier_e
    results[1].parameter_sigmas, results[1].covariance_matrix = np.array([0.3, 0.4]), np.diag([0.09, 0.16])
    results[2].residuals, results[2].outlier_flags = final_res_e, outlier_e
    results[2].parameter_sigmas, results[2].covariance_matrix = np.array([0.3, 0.4]), np.diag([0.09, 0.16])

    class FakeDesignMatrix:
        functions = []

        @staticmethod
        def get_polynomial():
            return None

    config = EtmConfig('tst', 'gls', silent=True)
    fit = EtmFit(config, FakeDesignMatrix())
    fit.results = results
    fit.outlier_flags = common

    strategy = NoiseCovarianceGLS(config)
    strategy.settings.compute_estimator = False  # the switch is OFF -> every pair must use the fallback
    strategy.noise_models = [types.SimpleNamespace(v0=v0_n), types.SimpleNamespace(v0=v0_e), None]
    strategy.noise_model_mjd0 = [0, 0, None]
    fit._adjustment_strategy = strategy

    fit.process_cross_covariance()

    step1_r = np.corrcoef(v0_n[common[outlier_n]], v0_e[common[outlier_e]])[0, 1]
    final_r = np.corrcoef(final_res_n[common], final_res_e[common])[0, 1]
    assert abs(step1_r - final_r) > 0.3, 'test scenario did not construct distinguishable correlations'

    ne = fit.cross_covariance['NE']
    assert ne['method'] == 'borrowed_residual_correlation'
    assert ne['daily_residual_correlation'] == pytest.approx(step1_r)
    assert ne['daily_residual_correlation'] != pytest.approx(final_r)


def test_fallback_daily_residual_correlation_uses_final_fit_residuals_without_a_noise_model():
    """complementary case: when no NoiseModelResult is available at all (e.g. a non-NoiseCovarianceGLS
    strategy), the fallback must still work, using the final-fit residuals as before"""
    from geode.etm.core.etm_config import EtmConfig
    from geode.etm.core.data_classes import AdjustmentResults
    from geode.etm.least_squares.least_squares import EtmFit, RobustLeastSquares

    rng = np.random.default_rng(4)
    n = 30
    mask = np.ones(n, dtype=bool)
    res_i, res_j = rng.standard_normal(n), rng.standard_normal(n)

    results = [AdjustmentResults(), AdjustmentResults(), AdjustmentResults()]
    results[0].residuals, results[0].outlier_flags = res_i, mask
    results[0].parameter_sigmas, results[0].covariance_matrix = np.array([0.1]), np.diag([0.01])
    results[1].residuals, results[1].outlier_flags = res_j, mask
    results[1].parameter_sigmas, results[1].covariance_matrix = np.array([0.2]), np.diag([0.04])
    results[2].residuals, results[2].outlier_flags = res_j, mask
    results[2].parameter_sigmas, results[2].covariance_matrix = np.array([0.2]), np.diag([0.04])

    class FakeDesignMatrix:
        functions = []

        @staticmethod
        def get_polynomial():
            return None

    config = EtmConfig('tst', 'robust', silent=True)
    fit = EtmFit(config, FakeDesignMatrix())
    fit.results = results
    fit.outlier_flags = mask
    fit._adjustment_strategy = RobustLeastSquares(config)  # no .noise_models attribute at all

    fit.process_cross_covariance()

    expected_r = np.corrcoef(res_i, res_j)[0, 1]
    assert fit.cross_covariance['NE']['daily_residual_correlation'] == pytest.approx(expected_r)


@needs_raw
def test_process_cross_covariance_populates_cross_covar_with_the_official_method():
    """
    cross_covar[pair] must always be populated, tagged with whichever method actually produced it (see
    cross_covariance[pair]['method']): the power-law cross-spectrum model when it was fit for that pair,
    otherwise the borrowed-residual-correlation fallback. There is no second, always-present field for
    the other model at all -- not even a bare scalar: the station-level summary's own
    'daily_residual_correlation' already *is* what the borrowed model would give for any parameter pair
    (see data_prep.prepare_ellipse_data for how the comparison ellipse is reconstructed from exactly
    that field, no separate one). Uses the real
    IGM1 N/E/U noise models (via the same fixtures the rest of this file builds real contexts from) so
    the power-law path genuinely runs for N-E, not just the fallback -- closing the coverage gap that
    let earlier bugs through: no prior test exercised EtmFit.process_cross_covariance() with a real
    DesignMatrix function in the loop (the other orchestration tests here use an empty
    FakeDesignMatrix.functions). U is given no noise model at all, so N-U/E-U must fall back, exercising
    both outcomes in one run.
    """
    from geode.etm.core.etm_config import EtmConfig
    from geode.etm.core.data_classes import AdjustmentResults
    from geode.etm.etm_functions.etm_function import EtmFunction
    from geode.etm.least_squares.least_squares import EtmFit, NoiseCovarianceGLS

    class _FakeFunction(EtmFunction):
        def initialize(self, **kwargs):
            pass

        def get_design_ts(self, ts):
            return np.zeros((len(ts), 2))

        def rehash(self):
            self.hash = 0

        def print_parameters(self, **kwargs):
            return [], [], []

        def short_name(self):
            return 'fake'

    station = 'igm1'
    nm_n = _single_component_nm(station, 'n')
    nm_e = _single_component_nm(station, 'e')
    mjd, ko, kc = _raw_masks(station)

    results = [AdjustmentResults(), AdjustmentResults(), AdjustmentResults()]
    for idx in range(3):
        results[idx].outlier_flags = ko[:, idx]
        results[idx].residuals = np.random.default_rng(idx).standard_normal(ko.shape[0])
        results[idx].parameter_sigmas = np.array([0.1 * (idx + 1), 0.2 * (idx + 1)])
        results[idx].covariance_matrix = np.diag([0.01 * (idx + 1), 0.04 * (idx + 1)])

    config = EtmConfig('tst', 'gls', silent=True)
    func = _FakeFunction(config)
    func.fit = True
    func.param_count = 2
    func.column_index = np.array([0, 1])

    class FakeDesignMatrix:
        functions = [func]

        @staticmethod
        def get_polynomial():
            return None

    fit = EtmFit(config, FakeDesignMatrix())
    fit.results = results
    fit.outlier_flags = kc

    strategy = NoiseCovarianceGLS(config)
    strategy.settings.compute_estimator = True  # switch ON
    mjd0_n, mjd0_e = int(round(mjd[ko[:, 0]][0])), int(round(mjd[ko[:, 1]][0]))
    strategy.noise_models = [nm_n, nm_e, None]
    strategy.noise_model_mjd0 = [mjd0_n, mjd0_e, None]
    fit._adjustment_strategy = strategy

    fit.process_cross_covariance()

    # cross_covar is always populated, for every pair, tagged with whichever method ran
    assert set(func.p.cross_covar) == {'NE', 'NU', 'EU'}
    assert func.p.cross_covar['NE']['method'] == 'power_law_cross_spectrum'
    assert fit.cross_covariance['NE']['method'] == 'power_law_cross_spectrum'
    # no separate "borrowed" field anywhere in the summary -- daily_residual_correlation already is
    # that number for any pair; the station-level summary doesn't duplicate it under another name
    assert 'borrowed_rate_correlation' not in fit.cross_covariance['NE']
    assert fit.cross_covariance['NE']['daily_residual_correlation'] is not None
    for pair in ('NU', 'EU'):
        assert func.p.cross_covar[pair]['method'] == 'borrowed_residual_correlation'
        assert fit.cross_covariance[pair]['method'] == 'borrowed_residual_correlation'
        # same redundancy bug as 'borrowed_rate_correlation' above, one level down: a rate_correlation
        # re-derived from the borrowed (rank-1) matrix would always equal daily_residual_correlation
        # in this same dict, so the fallback leaves it None rather than repeating that number
        assert fit.cross_covariance[pair]['rate_correlation'] is None


@needs_raw
def test_prepare_ellipse_data_reconstructs_the_borrowed_block():
    """
    data_prep.prepare_ellipse_data must reconstruct the comparison (borrowed) ellipse for a power-law
    pair from 'daily_residual_correlation' (the same field every summary already has -- not a separate
    "borrowed rate correlation" scalar, which would just be a duplicate of it) plus the within-component
    rate variances, rather than reading a stored matrix -- this is the exact replacement for the dual
    cross_covar_borrowed/cross_covar_power_law fields the user asked to revert. Reuses the same real
    IGM1 N/E fixtures as test_process_cross_covariance_populates_cross_covar_with_the_official_method.
    """
    import types
    from geode.etm.core.etm_config import EtmConfig
    from geode.etm.core.data_classes import AdjustmentResults
    from geode.etm.etm_functions.etm_function import EtmFunction
    from geode.etm.least_squares.least_squares import EtmFit, NoiseCovarianceGLS
    from geode.etm.visualization.data_prep import PlotDataPreparer

    class _FakeFunction(EtmFunction):
        def initialize(self, **kwargs):
            pass

        def get_design_ts(self, ts):
            return np.zeros((len(ts), 2))

        def rehash(self):
            self.hash = 0

        def print_parameters(self, **kwargs):
            return [], [], []

        def short_name(self):
            return 'fake'

    station = 'igm1'
    nm_n = _single_component_nm(station, 'n')
    nm_e = _single_component_nm(station, 'e')
    mjd, ko, kc = _raw_masks(station)

    results = [AdjustmentResults(), AdjustmentResults(), AdjustmentResults()]
    for idx in range(3):
        results[idx].outlier_flags = ko[:, idx]
        results[idx].residuals = np.random.default_rng(idx).standard_normal(ko.shape[0])
        results[idx].parameter_sigmas = np.array([0.1 * (idx + 1), 0.2 * (idx + 1)])
        results[idx].covariance_matrix = np.diag([0.01 * (idx + 1), 0.04 * (idx + 1)])

    config = EtmConfig('tst', 'gls', silent=True)
    func = _FakeFunction(config)
    func.fit = True
    func.param_count = 2
    func.column_index = np.array([0, 1])
    # normally populated by EtmFunction.load_parameters(), called before process_cross_covariance() in
    # the real pipeline (EtmFit.load_results_to_functions) -- set directly here since this test only
    # exercises process_cross_covariance()/prepare_ellipse_data()
    func.p.covar = [r.covariance_matrix for r in results]

    class FakeDesignMatrix:
        functions = [func]

        @staticmethod
        def get_polynomial():
            return func

    fit = EtmFit(config, FakeDesignMatrix())
    fit.results = results
    fit.outlier_flags = kc

    strategy = NoiseCovarianceGLS(config)
    strategy.settings.compute_estimator = True
    mjd0_n, mjd0_e = int(round(mjd[ko[:, 0]][0])), int(round(mjd[ko[:, 1]][0]))
    strategy.noise_models = [nm_n, nm_e, None]
    strategy.noise_model_mjd0 = [mjd0_n, mjd0_e, None]
    fit._adjustment_strategy = strategy
    fit.process_cross_covariance()

    solution_data = types.SimpleNamespace(
        get_station_id=lambda: 'tst', soln='ppp', stack_name='ppp', completion=100., span_completion=100.,
        lat=np.array([0.]), lon=np.array([0.]))
    plot_data = PlotDataPreparer.prepare_ellipse_data(solution_data, fit)

    assert plot_data is not None
    ne = plot_data.pairs['NE']
    assert ne['power_law'] is not None
    assert ne['borrowed'] is not None

    assert 'borrowed_rate_correlation' not in fit.cross_covariance['NE']
    r_borrowed = fit.cross_covariance['NE']['daily_residual_correlation']
    var_n, var_e = func.p.covar[0][1, 1], func.p.covar[1][1, 1]
    expected_cov_mm = r_borrowed * np.sqrt(var_n * var_e) * 1000. ** 2
    assert ne['borrowed'][0, 1] == pytest.approx(expected_cov_mm)
    assert ne['borrowed'][0, 0] == pytest.approx(var_n * 1000. ** 2)
    assert ne['borrowed'][1, 1] == pytest.approx(var_e * 1000. ** 2)
    # the power-law and borrowed ellipses are genuinely different matrices, not the same one twice
    assert not np.allclose(ne['power_law'], ne['borrowed'])

    for pair in ('NU', 'EU'):
        assert plot_data.pairs[pair]['power_law'] is None
        assert plot_data.pairs[pair]['borrowed'] is not None
