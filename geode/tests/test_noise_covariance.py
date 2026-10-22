"""
Tests of geode.etm.least_squares.noise_covariance against the MATLAB test vectors of pgamit
(docs/geode_handoff/test_vectors, see GeoDE_noise_method_spec.md Section 14).

The vectors are not part of the repository: point GEODE_NOISE_TEST_VECTORS to the directory that holds
the .mat files. The tests are skipped when it is not set.
"""

import dataclasses
import json
import os

import numpy as np
import pytest

from geode.etm.least_squares.noise_covariance import (estimate_noise_model, geo_lags, dcorr_pln,
                                                      EpochProjector, NoiseCovarianceSettings)

VECTOR_DIR = os.environ.get('GEODE_NOISE_TEST_VECTORS', '')
CASES = ['toy_flicker', 'toy_flicker_175d', 'saja_n', 'saja_e', 'saja_u', 'naus_u', 'igm1_n', 'igm1_e', 'igm1_u']

pytestmark = pytest.mark.skipif(not os.path.isdir(VECTOR_DIR),
                                reason='set GEODE_NOISE_TEST_VECTORS to the test vector directory')

# tolerances of Section 14
DIRECT = 1e-9       # quantities computed directly (gamma, kernels, Sigma)
PROJECTED = 1e-8    # projected kernels, relative to each column's largest value
DOWNSTREAM = 1e-7   # anything downstream of an NNLS
ZERO = 1e-10        # absolute, for zero variances
EXACT = 1e-12       # frequencies that must be the same grid values (they agree to round-off)


def vec(x):
    return np.atleast_1d(np.asarray(x, dtype=float))


def assert_rel(actual, desired, rtol, what=''):
    """relative to each element, with an absolute floor for zero values"""
    actual, desired = vec(actual), vec(desired)
    assert actual.shape == desired.shape, f'{what}: shape {actual.shape} != {desired.shape}'
    np.testing.assert_allclose(actual, desired, rtol=rtol, atol=ZERO, err_msg=what, equal_nan=True)


def assert_scaled(actual, desired, rtol, what=''):
    """relative to the largest value of each column (finite entries only)"""
    actual, desired = np.asarray(actual, dtype=float), np.asarray(desired, dtype=float)
    assert actual.shape == desired.shape, f'{what}: shape {actual.shape} != {desired.shape}'
    scale = np.nanmax(np.abs(desired), axis=0)
    err = np.nanmax(np.abs(actual - desired) / scale, axis=0)
    assert np.all(err <= rtol), f'{what}: max scaled error {np.max(err):.2e} > {rtol:.0e}'


@pytest.fixture(scope='module', params=CASES)
def case(request):
    """one case, with both detection methods: (vectors, joint harmonic fit, stepwise grid search)"""
    scipy_io = pytest.importorskip('scipy.io')
    v = scipy_io.loadmat(os.path.join(VECTOR_DIR, request.param + '.mat'), squeeze_me=True)
    settings = NoiseCovarianceSettings(alphas=tuple(vec(v['set_alphas'])), lag_k=int(v['set_k']),
                                       share=float(v['set_share']), eps=float(v['set_eps']),
                                       white_min=float(v['set_white_min']), variance_factors=True,
                                       detection='joint',
                                       jhf_snr_min=float(v['set_jhf_snr_min']),
                                       jhf_cand_ratio=float(v['set_jhf_cand_ratio']),
                                       jhf_cand_sep=float(v['set_jhf_cand_sep']),
                                       jhf_cand_max=int(v['set_jhf_cand_max']),
                                       jhf_keep_min=float(v['set_jhf_keep_min']),
                                       jhf_min_cycles=float(v['set_jhf_min_cycles']),
                                       jhf_p_min=float(v['set_jhf_p_min']), jhf_pad=int(v['set_jhf_pad']),
                                       sgs_band=tuple(vec(v['set_sgs_band'])), sgs_over=int(v['set_sgs_over']),
                                       sgs_dchi2_min=float(v['set_sgs_dchi2_min']),
                                       sgs_snr_min=float(v['set_sgs_snr_min']))
    args = (v['in_t'], v['in_y'], v['in_A'], vec(v['in_p0']), int(v['in_irate']) - 1)
    joint = estimate_noise_model(*args, settings=settings)
    stepwise = estimate_noise_model(*args, settings=dataclasses.replace(settings, detection='stepwise'))
    return v, joint, stepwise


# ----------------------------------------------------------------------------------------------------------------
# common to both detections
# ----------------------------------------------------------------------------------------------------------------

def test_default_detection_is_joint():
    assert NoiseCovarianceSettings().detection == 'joint'
    assert NoiseCovarianceSettings().jhf_snr_min == 8 and NoiseCovarianceSettings().sgs_snr_min == 7


def test_building_blocks(case):
    v, r, _ = case
    assert r.n_days == int(v['N']) and r.tmax == int(v['Tmax'])
    np.testing.assert_array_equal(r.lags, vec(v['lags']).astype(int))
    np.testing.assert_array_equal(geo_lags(int(v['set_k']), int(v['Tmax'])), r.lags)
    np.testing.assert_array_equal(r.n_tau, vec(v['cov_Ntau']))
    assert_scaled(r.v0, v['s1_v0'], DIRECT, 'step-1 residuals')
    assert_rel(r.x0, v['s1_x0'], DIRECT, 'step-1 parameters')
    assert_scaled(r.gamma, v['cov_gam'], DIRECT, 'empirical covariance')
    assert_scaled(r.gb, v['bas_Gb'], DIRECT, 'power-law kernels')
    assert_scaled(dcorr_pln(r.n_days, 1.0), v['bas_Gb'][:, 2], DIRECT, 'flicker kernel')


def test_projected_kernels(case):
    v, r, rs = case
    assert_scaled(r.gtf, v['bas_Gtf'], PROJECTED, 'projected power-law kernels')
    assert_scaled(r.htf, v['bas_htf'], PROJECTED, 'projected white kernel')
    assert_scaled(rs.detection.cg, v['sgs_Cg'], PROJECTED, 'projected grid cosines')


def test_cosine_shortcut_matches_general_projection(case):
    """the rank-2 projection of the cosines is the general algorithm of Section 5"""
    v, r, _ = case
    t = vec(v['in_t']).astype(int)
    t -= t[0]
    a = np.asarray(v['in_A'], dtype=float)
    p0 = vec(v['in_p0'])
    k0 = np.linalg.solve(a.T @ (p0[:, None] * a), (a * p0[:, None]).T)
    proj = EpochProjector(t, a, k0)
    tau = np.arange(r.n_days)
    # cycles/day; not the annual or semiannual, which the ETM absorbs (projected kernel ~ round-off)
    f = np.array([2 / r.n_days, 1 / 500, 1 / 91.3125])
    kg = np.cos(2 * np.pi * tau[:, None] * f[None, :])
    t1 = r.tmax + 1
    assert_scaled(proj.project_cosines(f, t1, cycles_per_day=True), proj.project(kg)[:t1], PROJECTED,
                  'cosine projection')
    assert_scaled(proj.project_cosines(f * 365.25, t1), proj.project(kg)[:t1], PROJECTED, 'cosine projection, cpy')


def test_sigma_one_pass(case):
    """Sigma from the vector's model residual covariance (isolates the one-pass algorithm of Section 7.2)"""
    v, r, _ = case
    t = vec(v['in_t']).astype(int)
    t -= t[0]
    a = np.asarray(v['in_A'], dtype=float)
    p0 = vec(v['in_p0'])
    k0 = np.linalg.solve(a.T @ (p0[:, None] * a), (a * p0[:, None]).T)
    sigma = EpochProjector(t, a, k0).gamma_covariance(vec(v['lags']).astype(int), vec(v['sig_ct']))
    assert_scaled(sigma, v['Sig'], DIRECT, 'Sigma (one pass)')
    np.testing.assert_allclose(sigma, sigma.T, rtol=0, atol=0)


def test_covariance_fits(case):
    v, r, rs = case
    assert_rel(r.fit0.w, v['fit0_w'], DOWNSTREAM, 'unweighted w')
    assert_rel(r.fit0.nugget, v['fit0_nugget'], DOWNSTREAM, 'unweighted nugget')
    assert_scaled(r.sigma_ct, v['sig_ct'], DOWNSTREAM, 'model residual covariance for Sigma')
    assert_scaled(r.sigma, v['Sig'], DIRECT, 'Sigma')
    for ref in (r.reference, rs.reference):
        assert_rel(ref.w, v['ref_w'], DOWNSTREAM, 'reference w')
        assert_rel(ref.nugget, v['ref_nugget'], DOWNSTREAM, 'reference nugget')
        assert_rel(ref.chi2, v['ref_chi2'], DOWNSTREAM, 'reference chi2')
        assert ref.dof == int(v['ref_dof'])
    assert_rel(r.alpha_reference.as_array(), v['aeff_ref'], DOWNSTREAM, 'effective alpha (reference)')


# ----------------------------------------------------------------------------------------------------------------
# joint harmonic fit (Section 8.2)
# ----------------------------------------------------------------------------------------------------------------

def test_joint_spectrum(case):
    """misfit spectrum of the reference fit, noise per bin and their ratio (finite values: the band)"""
    v, r, _ = case
    d = r.detection
    assert d.method == 'joint'
    assert_rel(d.spec_f * 365.25, v['jhf_spec_f'], EXACT, 'spectrum frequencies')
    assert_scaled(d.spec_s, v['jhf_spec_S'], DIRECT, 'misfit spectrum')
    b = np.isfinite(vec(v['jhf_spec_R']))
    np.testing.assert_array_equal(np.flatnonzero(b), d.band)
    assert_rel(d.spec_s[b], vec(v['jhf_spec_S'])[b], DIRECT, 'misfit spectrum in the band')
    assert_rel(d.spec_sigma2_bin[b], vec(v['jhf_spec_sigma2_bin'])[b], DIRECT, 'noise per bin')
    assert_rel(d.spec_r[b], vec(v['jhf_spec_R'])[b], DIRECT, 'misfit ratio')
    assert np.all(np.isnan(d.spec_r[~b]))


def test_joint_candidates(case):
    v, r, _ = case
    d = r.detection
    n = r.n_days
    f0 = np.array([c.f0 for c in d.candidates])
    f = np.array([c.f for c in d.candidates])
    assert_rel(f0 * 365.25, v['jhf_cand_f0'], EXACT, 'candidates before refinement')
    assert_rel(f * 365.25, v['jhf_cand_f'], EXACT, 'candidates after refinement')
    # the refined frequencies lie on the grid f0 + k / (3N), |k| <= 3 per refinement
    k = (f - f0) * 3 * n
    np.testing.assert_allclose(k, np.round(k), atol=1e-6)
    assert np.all(np.abs(k) <= 3 + 1e-6)

    assert_rel([c.w for c in d.candidates], v['jhf_cand_w'], DOWNSTREAM, 'variances in the harmonic fit')
    assert_rel([c.snr for c in d.candidates], v['jhf_cand_snr'], DOWNSTREAM, 'SNR in the harmonic fit')
    np.testing.assert_array_equal([c.outcome_code for c in d.candidates], vec(v['jhf_cand_outcome_code']))

    harm = d.harmonic_fit
    assert_rel(harm.w, v['jhf_harm_w'], DOWNSTREAM, 'harmonic fit: power law')
    assert_rel(harm.nugget, v['jhf_harm_nugget'], DOWNSTREAM, 'harmonic fit: white')
    assert_rel(harm.chi2, v['jhf_harm_chi2'], DOWNSTREAM, 'harmonic fit: chi2')


def test_joint_final(case):
    v, r, _ = case
    d = r.detection
    fin = r.final
    assert_rel(d.check_found * 365.25, v['jhf_check_f_found'], EXACT, 'final check: found')
    assert_rel(d.check_added * 365.25, v['jhf_check_f_added'], EXACT, 'final check: added')
    assert_scaled(d.check_r[d.band], vec(v['jhf_check_R'])[d.band], DOWNSTREAM, 'final check: misfit ratio')

    assert_rel(r.periods_f, v['jhf_fin_f'], EXACT, 'final frequencies')
    assert_rel(1 / fin.f, v['jhf_fin_periods_d'], EXACT, 'final periods')
    assert_rel(fin.w, v['jhf_fin_w'], DOWNSTREAM, 'final w')
    assert_rel(fin.w_p, v['jhf_fin_wP'], DOWNSTREAM, 'final periodic variances')
    assert_rel(fin.nugget, v['jhf_fin_nugget'], DOWNSTREAM, 'final nugget')
    assert_rel(fin.chi2, v['jhf_fin_chi2'], DOWNSTREAM, 'final chi2')
    assert fin.dof == int(v['jhf_fin_dof'])
    assert_rel(d.snr, v['jhf_fin_snr'], DOWNSTREAM, 'final SNR')
    assert_rel(fin.survive, v['jhf_fin_survive'], DOWNSTREAM, 'fraction surviving the ETM')
    assert_scaled(r.c_lag_final, v['jhf_C_lag'], DOWNSTREAM, 'final covariance function')

    # the SNR in the harmonic fit of the final periods; NaN for those added by the final check
    added = np.isin(fin.f, d.check_added)
    assert np.all(np.isnan(d.snr_harmonic[added])) and np.all(np.isfinite(d.snr_harmonic[~added]))
    # final check actions: every final period passed or was added; removed ones are not in the final fit
    assert d.check_action == ['added' if a else 'passed' for a in added]
    assert not np.any(np.isin(d.check_removed, fin.f))
    # found = added (before refinement) + found ones it removed
    assert d.check_found.size == d.check_added.size + np.sum(np.isin(d.check_removed, d.check_found))


def test_joint_alpha_and_gls(case):
    v, r, _ = case
    assert_rel(r.alpha_final.as_array(), v['aeff_jhf'], DOWNSTREAM, 'effective alpha')
    assert_rel(r.gls.as_array(), v['gls_jhf'], DOWNSTREAM, 'GLS')
    assert_rel(r.gls.x, v['gls_jhf_x'], DOWNSTREAM, 'GLS parameters')
    assert_rel(r.gls.redundancies, v['gls_jhf_red'], DOWNSTREAM, 'redundancies')


# ----------------------------------------------------------------------------------------------------------------
# stepwise grid search (Section 8.3)
# ----------------------------------------------------------------------------------------------------------------

def test_stepwise(case):
    v, _, r = case
    d = r.detection
    assert d.method == 'stepwise'
    assert_rel(d.fgrid, v['sgs_fgrid'], EXACT, 'candidate grid')
    assert_scaled(d.scan_dchi2, v['sgs_dchi2'], DOWNSTREAM, 'reference scan')

    # the candidate set is exact: same grid indices (the grid itself agrees to round-off)
    def grid_index(f):
        return [int(np.argmin(np.abs(vec(v['sgs_fgrid']) - fi))) for fi in vec(f)]

    cand = d.candidates
    assert [c.index for c in cand] == grid_index(v['sgs_cand_f'])
    assert_rel([c.f_cpy for c in cand], v['sgs_cand_f'], EXACT, 'candidate frequencies')
    np.testing.assert_array_equal([c.outcome_code for c in cand], vec(v['sgs_cand_outcome_code']))
    assert_rel([c.dchi2 for c in cand], v['sgs_cand_dchi2'], DOWNSTREAM, 'candidate dchi2')
    assert_rel([c.snr for c in cand], v['sgs_cand_snr'], DOWNSTREAM, 'candidate SNR')
    assert_rel([c.dropone_dchi2 for c in cand], v['sgs_cand_dropone'], DOWNSTREAM, 'candidate drop-one')

    fin = r.final
    assert_rel(r.periods_f, v['sgs_fin_f'], EXACT, 'accepted frequencies')
    assert_rel(1 / fin.f, v['sgs_fin_periods_d'], EXACT, 'periods')
    assert_rel(fin.w, v['sgs_fin_w'], DOWNSTREAM, 'final w')
    assert_rel(fin.w_p, v['sgs_fin_wP'], DOWNSTREAM, 'final periodic variances')
    assert_rel(fin.nugget, v['sgs_fin_nugget'], DOWNSTREAM, 'final nugget')
    assert_rel(fin.chi2, v['sgs_fin_chi2'], DOWNSTREAM, 'final chi2')
    assert fin.dof == int(v['sgs_fin_dof'])
    assert_rel(d.snr, v['sgs_fin_snr'], DOWNSTREAM, 'final SNR')
    assert_rel(fin.survive, v['sgs_fin_survive'], DOWNSTREAM, 'fraction surviving the ETM')

    assert_rel(r.alpha_final.as_array(), v['aeff_sgs'], DOWNSTREAM, 'effective alpha')
    assert_rel(r.gls.as_array(), v['gls_sgs'], DOWNSTREAM, 'GLS')
    assert_rel(r.gls.x, v['gls_sgs_x'], DOWNSTREAM, 'GLS parameters')
    assert_rel(r.gls.redundancies, v['gls_sgs_red'], DOWNSTREAM, 'redundancies')

    # the accepted periods drop out of the scan of the final fit
    assert np.all(d.scan_final[[c.index for c in cand if c.outcome == 'accepted']] == 0)


# ----------------------------------------------------------------------------------------------------------------
# signal, report and figure (both detections)
# ----------------------------------------------------------------------------------------------------------------

def test_signal_prediction(case):
    """the signal is the collocation prediction C_s(grid, t) C^-1 v of the periodic terms + steep power laws"""
    for r in case[1:]:
        fin = r.final
        steep = np.asarray(r.settings.alphas) >= r.settings.signal_alpha_min
        tau = np.arange(r.n_days)
        periodic = sum((wp * np.cos(2 * np.pi * f * tau) for f, wp in zip(fin.f, fin.w_p)), np.zeros(r.n_days))
        np.testing.assert_allclose(r.signal_lag, r.gb[:, steep] @ fin.w[steep] + periodic, rtol=1e-12,
                                   atol=1e-12)

        if not np.any(r.signal_lag):
            # no periodic term and no steep power law: no signal
            assert np.all(r.signal == 0)
            continue

        t = r.t
        days = np.linspace(0, r.n_days - 1, 50).astype(int)
        c_sv = r.signal_lag[np.abs(days[:, None] - t[None, :])]
        assert_scaled(r.signal[days], c_sv @ r.gls.c_inv_v, 1e-9, 'signal')
        # the flat power laws and the white noise stay in the residuals
        resid = r.gls.residuals - r.signal[t]
        assert np.std(resid) > 0.1 * np.std(r.gls.residuals)


def test_summary(case):
    """the report is valid JSON (no NaN) and carries the data of the diagnostic figure"""
    for r in case[1:]:
        nm = r.summary()
        json.dumps(nm, allow_nan=False)
        assert nm['detection'] == r.detection.method
        diag = nm['diagnostics']
        assert len(diag['gamma']) == len(diag['fit_reference']) == len(diag['fit_final']) == r.tmax + 1
        assert diag['lags'] == r.lags.tolist()
        assert [p['period_d'] for p in nm['periodic']] == pytest.approx(list(1 / r.final.f))
        assert [p['snr'] for p in nm['periodic']] == pytest.approx(list(r.detection.snr))
        if r.detection.method == 'joint':
            assert len(diag['spec_f_cpy']) == len(diag['spec_r_reference']) == r.detection.band.size
            assert len(nm['candidates']) == len(r.detection.candidates)
            assert [p['final_check'] == 'added' for p in nm['periodic']] == list(np.isin(r.final.f,
                                                                                      r.detection.check_added))
            assert len(nm['final_check']['removed_period_d']) == r.detection.check_removed.size
        else:
            assert len(diag['scan_reference']) == len(diag['scan_final']) == r.detection.fgrid.size


def test_flags(case):
    """review flags: SNR below the threshold in the final fit, long periods, high chi2/dof"""
    for r in case[1:]:
        flags = r.flags
        fin = r.final
        long = np.sum(1 / fin.f > 0.25 * r.n_days)
        assert sum('quarter of the span' in f for f in flags) == long
        assert any('chi2/dof' in f for f in flags) == (fin.chi2 / fin.dof > 1.5)
        if r.detection.method == 'joint':
            weak = np.sum(np.isfinite(r.detection.snr_harmonic) & (r.detection.snr < r.settings.jhf_snr_min))
            assert sum('in the final fit' in f for f in flags) == weak


def test_noise_model_figure(case, tmp_path):
    """the diagnostic figure renders for both detections, including a component without a noise model"""
    matplotlib = pytest.importorskip('matplotlib')
    matplotlib.use('Agg')
    from geode.etm.core.etm_config import EtmConfig
    from geode.etm.visualization.etm_plotting import EtmPlotter
    from geode.etm.visualization.data_classes import NoiseModelPlotData

    v, joint, stepwise = case
    for r in (joint, stepwise):
        config = EtmConfig('tst', r.detection.method, silent=True)
        config.plotting_config.filename = str(tmp_path) + os.sep
        nm = r.summary()
        plot_data = NoiseModelPlotData(station_id='TST.CASE', solution_type='ppp', stack_name='ppp',
                                       completion=100., latitude=0., longitude=0., noise_models=[nm, {}, nm])
        EtmPlotter(config).plot_noise_model(plot_data)
        assert (tmp_path / (config.build_filename() + '_noise.png')).is_file()
