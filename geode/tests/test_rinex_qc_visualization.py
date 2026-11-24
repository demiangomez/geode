"""
Tests for geode.rinex_qc.visualization's pure-numeric grid-collapse layer
(_overlap_weight_matrix, collapse_to_elev_rings, collapse_to_azimuth,
_ring_included). No database required -- query functions (get_*) aren't
covered here, same gap noted for the rest of geode.rinex_qc's DB-touching
code (see knowledge/architecture/rinex-qc.md's "Testing & CI" note).

collapse_to_elev_rings()/collapse_to_azimuth() operate on a batch of days at
once (2D (n_days, n_cells) arrays in, 2D (n_y, n_days) arrays out) -- the
whole point of that shape is to vectorize across days instead of looping
over them in Python one at a time (profiled: looping per-day was the
dominant cost of generating a full-history heatmap, well over the database
query itself). Tests use n_days=3 to exercise that axis, not just the
single-day case.
"""
import numpy as np
import pytest

from geode.rinex_qc import visualization as viz
from geode.rinex_qc.aggregate import make_equal_area_grid


def test_overlap_weight_matrix_rows_sum_to_one():
    # every source sector's width must be fully redistributed across targets
    W = viz._overlap_weight_matrix(7, 72)
    assert np.allclose(W.sum(axis=1), 1.0)


def test_overlap_weight_matrix_identity_when_equal_resolution():
    W = viz._overlap_weight_matrix(72, 72)
    assert np.allclose(W, np.eye(72))


def _flat_bins(grid, n_days=3, n_obs_per_cell=10.0, mp_per_cell=5.0, snr_ddbhz_per_cell=450.0):
    """A uniform bin set (same n_obs/mp/snr in every cell, every day) -- the
    simplest case where every collapse should reproduce the per-cell
    constants, for every day in the batch."""
    n_cells = int(sum(grid['az_bins']))
    n_obs = np.full((n_days, n_cells), n_obs_per_cell)
    mp_rms_mm = np.full((n_days, n_cells), mp_per_cell)
    snr_ddbhz = np.full((n_days, n_cells), snr_ddbhz_per_cell)
    return n_obs, mp_rms_mm, snr_ddbhz


def _random_bins(grid, n_days, seed):
    n_cells = int(sum(grid['az_bins']))
    rng = np.random.default_rng(seed)
    n_obs = rng.integers(1, 100, (n_days, n_cells)).astype(float)
    mp_rms_mm = rng.uniform(100, 900, (n_days, n_cells))
    snr_ddbhz = rng.uniform(250, 500, (n_days, n_cells))
    return n_obs, mp_rms_mm, snr_ddbhz


def test_collapse_to_elev_rings_uniform_bins_reproduce_constants():
    grid = make_equal_area_grid()
    n_obs, mp_rms_mm, snr_ddbhz = _flat_bins(grid)
    mp_grid, snr_grid = viz.collapse_to_elev_rings(n_obs, mp_rms_mm, snr_ddbhz, grid)
    n_rings = len(grid['az_bins'])
    assert mp_grid.shape == (n_rings, 3)
    assert np.allclose(mp_grid, 0.5)   # 5mm in storage units -> 0.5cm
    assert np.allclose(snr_grid, 45.0)  # 450 in 0.1 dB-Hz units -> 45.0 dB-Hz


def test_collapse_to_elev_rings_missing_day_is_independent_nan_column():
    # a day with no data (every cell NaN, as get_daily_bin_grids fills a
    # calendar gap) must not bleed into neighboring days' columns -- this is
    # the vectorized form of the gap-smearing regression the per-day loop
    # used to need a reindex-with-NaN workaround for.
    grid = make_equal_area_grid()
    n_obs, mp_rms_mm, snr_ddbhz = _flat_bins(grid)
    n_obs[1, :] = np.nan
    mp_rms_mm[1, :] = np.nan
    snr_ddbhz[1, :] = np.nan

    mp_grid, snr_grid = viz.collapse_to_elev_rings(n_obs, mp_rms_mm, snr_ddbhz, grid)
    assert np.all(np.isnan(mp_grid[:, 1]))
    assert np.all(np.isnan(snr_grid[:, 1]))
    assert np.allclose(mp_grid[:, 0], 0.5)
    assert np.allclose(mp_grid[:, 2], 0.5)


def test_collapse_to_elev_rings_az_range_filter_changes_result():
    grid = make_equal_area_grid()
    n_obs, mp_rms_mm, snr_ddbhz = _random_bins(grid, n_days=3, seed=1)

    unfiltered, _ = viz.collapse_to_elev_rings(n_obs, mp_rms_mm, snr_ddbhz, grid)
    filtered, _ = viz.collapse_to_elev_rings(n_obs, mp_rms_mm, snr_ddbhz, grid, az_range=(0, 90))
    valid = ~np.isnan(unfiltered) & ~np.isnan(filtered)
    assert valid.any()
    assert not np.allclose(unfiltered[valid], filtered[valid])


def test_collapse_to_azimuth_uniform_bins_reproduce_constants():
    grid = make_equal_area_grid()
    w_per_ring = viz._az_weight_matrices(grid)
    n_obs, mp_rms_mm, snr_ddbhz = _flat_bins(grid)
    az_mp, az_snr = viz.collapse_to_azimuth(n_obs, mp_rms_mm, snr_ddbhz, grid, w_per_ring)
    assert az_mp.shape == (viz.N_TARGET_AZ, 3)
    assert np.allclose(az_mp, 0.5)   # 5mm in storage units -> 0.5cm
    assert np.allclose(az_snr, 45.0)


def test_collapse_to_azimuth_missing_day_is_independent_nan_column():
    grid = make_equal_area_grid()
    w_per_ring = viz._az_weight_matrices(grid)
    n_obs, mp_rms_mm, snr_ddbhz = _flat_bins(grid)
    n_obs[1, :] = np.nan
    mp_rms_mm[1, :] = np.nan
    snr_ddbhz[1, :] = np.nan

    az_mp, az_snr = viz.collapse_to_azimuth(n_obs, mp_rms_mm, snr_ddbhz, grid, w_per_ring)
    assert np.all(np.isnan(az_mp[:, 1]))
    assert np.all(np.isnan(az_snr[:, 1]))
    assert np.allclose(az_mp[:, 0], 0.5)
    assert np.allclose(az_mp[:, 2], 0.5)


def test_collapse_to_azimuth_elev_range_filter_changes_result():
    grid = make_equal_area_grid()
    w_per_ring = viz._az_weight_matrices(grid)
    n_obs, mp_rms_mm, snr_ddbhz = _random_bins(grid, n_days=3, seed=2)

    unfiltered, _ = viz.collapse_to_azimuth(n_obs, mp_rms_mm, snr_ddbhz, grid, w_per_ring)
    filtered, _ = viz.collapse_to_azimuth(n_obs, mp_rms_mm, snr_ddbhz, grid, w_per_ring,
                                          elev_range=(0, 20))
    valid = ~np.isnan(unfiltered) & ~np.isnan(filtered)
    assert valid.any()
    assert not np.allclose(unfiltered[valid], filtered[valid])


def test_ring_included_none_means_no_filter():
    grid = make_equal_area_grid()
    assert viz._ring_included(grid, None) is None


def test_ring_included_rejects_bad_range():
    grid = make_equal_area_grid()
    with pytest.raises(ValueError):
        viz._ring_included(grid, (200, 100))


def test_qc_plot_output_defaults():
    out = viz.QcPlotOutput()
    assert out.filename is None
    assert out.file_io is None
    assert out.format == 'png'
