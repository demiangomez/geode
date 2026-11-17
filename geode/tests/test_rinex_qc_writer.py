"""Tests for geode.rinex_qc.writer's storage-time quantization."""
import numpy as np

from geode.rinex_qc import writer


def test_quantize_bins_empty_cell_is_null_everywhere():
    bins = {'n_obs': np.array([0]), 'mp_rms': np.array([np.nan]), 'snr_mean': np.array([np.nan])}
    n_obs, mp_rms_mm, snr_ddbhz = writer._quantize_bins(bins)
    assert n_obs == [None]
    assert mp_rms_mm == [None]
    assert snr_ddbhz == [None]


def test_quantize_bins_converts_units_and_rounds():
    bins = {'n_obs': np.array([42]), 'mp_rms': np.array([0.1234]), 'snr_mean': np.array([41.26])}
    n_obs, mp_rms_mm, snr_ddbhz = writer._quantize_bins(bins)
    assert n_obs == [42]
    assert mp_rms_mm == [123]   # 0.1234 m -> 123.4 mm -> round to 123
    assert snr_ddbhz == [413]  # 41.26 dBHz -> 412.6 (0.1 units) -> round to 413


def test_quantize_bins_handles_multiple_cells_independently():
    bins = {'n_obs': np.array([0, 5, 10]),
           'mp_rms': np.array([np.nan, 0.05, 0.3]),
           'snr_mean': np.array([np.nan, 40.0, 38.5])}
    n_obs, mp_rms_mm, snr_ddbhz = writer._quantize_bins(bins)
    assert n_obs == [None, 5, 10]
    assert mp_rms_mm == [None, 50, 300]
    assert snr_ddbhz == [None, 400, 385]
