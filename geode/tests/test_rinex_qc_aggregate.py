"""Tests for geode.rinex_qc.aggregate: the grid generator and pool()."""
import numpy as np
import pytest

from geode.rinex_qc import aggregate
from geode.rinex_qc.runner import BandData


def test_grid_generator_is_deterministic():
    g1 = aggregate.make_equal_area_grid()
    g2 = aggregate.make_equal_area_grid()
    assert g1 == g2


def test_grid_elev_edges_cover_0_to_90_in_5deg_rings():
    grid = aggregate.make_equal_area_grid()
    assert grid['elev_edges'][0] == 0.0
    assert grid['elev_edges'][-1] == 90.0
    assert len(grid['elev_edges']) == 19  # 18 rings
    assert len(grid['az_bins']) == 18


def test_grid_sectors_decrease_towards_zenith():
    grid = aggregate.make_equal_area_grid()
    az_bins = grid['az_bins']
    # cos(elevation) is monotonically decreasing over 0-90deg, so sector
    # counts must be non-increasing ring to ring.
    assert all(az_bins[i] >= az_bins[i + 1] for i in range(len(az_bins) - 1))
    assert all(n >= 1 for n in az_bins)


def test_bin_indices_assigns_horizon_and_zenith_correctly():
    grid = aggregate.make_equal_area_grid()
    # due north at the horizon -> ring 0, sector 0
    idx = aggregate.bin_indices(np.array([2.5]), np.array([0.0]), grid)
    assert idx[0] == 0
    # NaN elevation (masked by the cutoff) -> -1
    idx_nan = aggregate.bin_indices(np.array([np.nan]), np.array([10.0]), grid)
    assert idx_nan[0] == -1


def _make_bins(n_obs, mp_rms, snr_mean):
    return {'n_obs': np.asarray(n_obs, dtype=np.int64),
           'mp_rms': np.asarray(mp_rms, dtype=np.float64),
           'snr_mean': np.asarray(snr_mean, dtype=np.float64)}


def test_pool_matches_whole_dataset_when_splitting_in_half():
    rng = np.random.default_rng(0)
    n_bins = 20
    nepochs = 400

    bin_ids = rng.integers(0, n_bins, size=nepochs)
    mp = rng.normal(0, 0.3, size=nepochs)
    snr = rng.normal(40, 3, size=nepochs)

    def aggregate_slice(sl):
        ids, m, s = bin_ids[sl], mp[sl], snr[sl]
        n_obs = np.zeros(n_bins, dtype=np.int64)
        sum_sq = np.zeros(n_bins)
        sum_snr = np.zeros(n_bins)
        np.add.at(n_obs, ids, 1)
        np.add.at(sum_sq, ids, m ** 2)
        np.add.at(sum_snr, ids, s)
        mp_rms = np.full(n_bins, np.nan)
        snr_mean = np.full(n_bins, np.nan)
        nz = n_obs > 0
        mp_rms[nz] = np.sqrt(sum_sq[nz] / n_obs[nz])
        snr_mean[nz] = sum_snr[nz] / n_obs[nz]
        return _make_bins(n_obs, mp_rms, snr_mean)

    whole = aggregate_slice(slice(None))
    half = nepochs // 2
    pooled = aggregate.pool(aggregate_slice(slice(0, half)), aggregate_slice(slice(half, None)))

    assert np.array_equal(pooled['n_obs'], whole['n_obs'])
    assert np.allclose(np.nan_to_num(pooled['mp_rms']), np.nan_to_num(whole['mp_rms']), atol=1e-9)
    assert np.allclose(np.nan_to_num(pooled['snr_mean']), np.nan_to_num(whole['snr_mean']), atol=1e-9)


def test_pool_handles_empty_bins_on_both_sides():
    a = _make_bins([0, 5], [np.nan, 1.0], [np.nan, 40.0])
    b = _make_bins([0, 0], [np.nan, np.nan], [np.nan, np.nan])
    pooled = aggregate.pool(a, b)
    assert pooled['n_obs'].tolist() == [0, 5]
    assert np.isnan(pooled['mp_rms'][0])
    assert pooled['mp_rms'][1] == pytest.approx(1.0)


def test_pool_weights_by_n_obs():
    a = _make_bins([10], [1.0], [40.0])
    b = _make_bins([30], [2.0], [50.0])
    pooled = aggregate.pool(a, b)
    assert pooled['n_obs'][0] == 40
    expected_rms = ((10 * 1.0 ** 2 + 30 * 2.0 ** 2) / 40) ** 0.5
    assert pooled['mp_rms'][0] == pytest.approx(expected_rms)
    expected_mean = (10 * 40.0 + 30 * 50.0) / 40
    assert pooled['snr_mean'][0] == pytest.approx(expected_mean)


def _make_band(mp, az, el, slip, snr=None):
    arr = lambda x: np.asarray(x, dtype=np.float64)
    return BandData(prns=['X01'], mp=arr(mp).reshape(-1, 1), az=arr(az).reshape(-1, 1),
                    el=arr(el).reshape(-1, 1), slip=np.asarray(slip, dtype=bool).reshape(-1, 1),
                    snr=arr(snr).reshape(-1, 1) if snr is not None else None, snr_code=None)


def test_aggregate_signal_basic_stats():
    band = _make_band(mp=[1.0, -1.0, 2.0, np.nan], az=[0, 90, 180, 270],
                      el=[5, 15, 25, 35], slip=[False, False, False, False],
                      snr=[40.0, 42.0, 44.0, 46.0])
    grid = aggregate.make_equal_area_grid()
    summary, bins = aggregate.aggregate_signal(band, grid)

    assert summary['n_obs'] == 3  # the NaN mp epoch is excluded
    assert summary['n_sats'] == 1
    assert summary['mp_rms_m'] == pytest.approx(np.sqrt((1.0 + 1.0 + 4.0) / 3))
    assert summary['snr_mean'] == pytest.approx(np.mean([40.0, 42.0, 44.0]))
    assert summary['n_slips'] == 0


def test_aggregate_signal_counts_slips_by_elevation_band():
    band = _make_band(mp=[0.1, 0.1, 0.1], az=[0, 0, 0], el=[5.0, 25.0, 55.0],
                      slip=[True, False, True])
    grid = aggregate.make_equal_area_grid()
    summary, _ = aggregate.aggregate_signal(band, grid)

    assert summary['n_slips'] == 2
    # bands are (0-10, 10-20, 20-30, 30-40, 40-50, >50)
    assert summary['slips_by_elev'] == [1, 0, 0, 0, 0, 1]


def test_aggregate_signal_slip_at_nan_elevation_not_assigned_to_any_band():
    band = _make_band(mp=[0.1], az=[0], el=[np.nan], slip=[True])
    grid = aggregate.make_equal_area_grid()
    summary, _ = aggregate.aggregate_signal(band, grid)

    assert summary['n_slips'] == 1       # still counted in the scalar total
    assert sum(summary['slips_by_elev']) == 0  # but not attributable to a band


def test_aggregate_signal_without_snr():
    band = _make_band(mp=[0.5, -0.5], az=[0, 10], el=[20, 30], slip=[False, False], snr=None)
    grid = aggregate.make_equal_area_grid()
    summary, _ = aggregate.aggregate_signal(band, grid)

    assert summary['snr_mean'] is None
    assert summary['snr_std'] is None
