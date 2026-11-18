"""Tests for geode.rinex_qc.runner: CSV parsing, pivoting, and the per-arc
detrend -- the pure-Python logic that doesn't need the real ringo binary.
See knowledge/decisions/0005-switch-to-ringo.md for why detrending exists
at all and why it's a degree-5 polynomial per slip-bounded arc.
"""
import subprocess

import numpy as np
import pandas as pd
import pytest

from geode.rinex_qc import runner


def test_read_ringo_csv_drops_repeated_header_lines():
    # RINGO repeats its '> '-prefixed header once per satellite block when
    # writing to stdout instead of --outdir -- see runner._read_ringo_csv.
    text = (
        '> PRN,time,az,el,MP1\n'
        'G01,2026-01-01 00:00:00.000,10.0,20.0,0.1\n'
        '> PRN,time,az,el,MP1\n'
        'G02,2026-01-01 00:00:00.000,30.0,40.0,0.2\n'
    )
    df = runner._read_ringo_csv(text)
    assert list(df['PRN']) == ['G01', 'G02']
    assert list(df.columns) == ['PRN', 'time', 'az', 'el', 'MP1']


def test_pivot_keeps_rows_where_every_column_is_blank():
    # pivot_table's default dropna=True would drop an epoch where no
    # satellite has a slip flagged at all -- the common case -- which
    # would silently misalign this array's shape against az/el/mp built
    # from the same frame. See runner._pivot's dropna=False.
    df = pd.DataFrame({
        'PRN':  ['G01', 'G01', 'G02', 'G02'],
        'time': ['t0', 't1', 't0', 't1'],
        's1':   ['', '', '', ''],  # nobody slipped at either epoch
    })
    out = runner._pivot(df, 's1', ['G01', 'G02'], pd.Index(['t0', 't1']), is_flag=True)
    assert out.shape == (2, 2)
    assert not out.any()


def test_pivot_reindexes_to_the_given_time_index_not_just_its_own():
    # --qcmode and the plain rnxcsv call are two separate ringo
    # invocations -- confirmed on a real file that they don't always
    # agree on the exact epoch set for the same system. _build_systems()
    # computes one shared time_index up front and passes it to every
    # _pivot() call so every BandData array ends up the same shape by
    # construction, not by assumption that the two calls happened to
    # agree this time.
    df = pd.DataFrame({'PRN': ['G01', 'G01'], 'time': ['t0', 't1'], 'az': [10.0, 20.0]})
    shared_index = pd.Index(['t0', 't1', 't2'])  # t2 is missing from this df entirely

    out = runner._pivot(df, 'az', ['G01'], shared_index)
    assert out.shape == (3, 1)
    assert out[0, 0] == 10.0
    assert out[1, 0] == 20.0
    assert np.isnan(out[2, 0])


def test_pivot_numeric_column_reindexes_missing_prn_as_nan():
    df = pd.DataFrame({'PRN': ['G01'], 'time': ['t0'], 'az': [45.0]})
    out = runner._pivot(df, 'az', ['G01', 'G02'], pd.Index(['t0']))
    assert out.shape == (1, 2)
    assert out[0, 0] == 45.0
    assert np.isnan(out[0, 1])


def test_detrend_by_arc_removes_a_polynomial_trend():
    # a slow curve (what the ionospheric residual looks like) plus a tiny
    # constant "real multipath" offset -- detrending should recover
    # something close to the offset, not the curve.
    n = 30
    t = np.arange(n, dtype=np.float64)
    trend = 0.01 * t - 0.0003 * t ** 2
    noise_free_signal = 0.05  # the "real multipath" we want left over
    raw = (trend + noise_free_signal).reshape(-1, 1)
    slip = np.zeros((n, 1), dtype=bool)

    detrended = runner._detrend_by_arc(raw, slip)
    assert np.nanstd(detrended) < 0.01  # the slow trend is gone
    assert np.all(np.isfinite(detrended))


def test_detrend_by_arc_resets_at_each_slip():
    # two arcs with very different constant offsets; without segmenting
    # on the slip, a single polynomial fit across both would leave a
    # large step in the residual at the boundary.
    n = 20
    raw = np.full((n, 1), 10.0)
    raw[n // 2:] = -10.0
    slip = np.zeros((n, 1), dtype=bool)
    slip[n // 2] = True

    detrended = runner._detrend_by_arc(raw, slip)
    assert np.allclose(detrended, 0.0, atol=1e-9)


def test_detrend_by_arc_drops_arcs_shorter_than_min_length():
    n = 3  # shorter than MIN_ARC_EPOCHS (DETREND_DEGREE + 2 == 7)
    raw = np.full((n, 1), 5.0)
    slip = np.zeros((n, 1), dtype=bool)

    detrended = runner._detrend_by_arc(raw, slip)
    assert np.all(np.isnan(detrended))


def test_detrend_by_arc_splits_on_a_gap_even_without_a_slip_flag():
    # A satellite can set and rise again (or just drop out briefly)
    # without RINGO ever flagging a slip across the gap -- there's
    # nothing to compare against when there's no data in between. A
    # single fit bridging two genuinely unrelated runs produces giant,
    # physically meaningless residuals right at the gap -- this is the
    # real production bug (see module docstring): confirmed on a real
    # file where one "arc" silently spanned a 2643-epoch gap.
    n = 3000
    raw = np.full((n, 1), np.nan)
    first_run = np.arange(10, 30)         # one real pass, offset ~5
    second_run = np.arange(2700, 2720)    # a much later, unrelated pass, offset ~-5
    raw[first_run, 0] = 5.0
    raw[second_run, 0] = -5.0
    slip = np.zeros((n, 1), dtype=bool)   # no slip flagged anywhere

    detrended = runner._detrend_by_arc(raw, slip)
    # each run is perfectly constant on its own -- if the gap between
    # them were bridged by one fit, the huge jump from +5 to -5 across
    # ~2700 "epochs" of nothing would show up as a large residual right
    # at the edges of one or both runs; split correctly, each run is its
    # own constant and detrends to exactly zero.
    assert np.allclose(detrended[first_run, 0], 0.0, atol=1e-9)
    assert np.allclose(detrended[second_run, 0], 0.0, atol=1e-9)


def test_detrend_by_arc_splits_on_a_small_gap_too():
    # not just huge gaps -- even a handful of missing epochs produced a
    # smaller but real version of the same artifact on a different
    # satellite in the real file this was found on.
    n = 40
    raw = np.full((n, 1), np.nan)
    before_gap = np.arange(0, 15)
    after_gap  = np.arange(20, 35)        # a 5-epoch gap, no slip flagged
    raw[before_gap, 0] = 3.0
    raw[after_gap, 0] = -3.0
    slip = np.zeros((n, 1), dtype=bool)

    detrended = runner._detrend_by_arc(raw, slip)
    assert np.allclose(detrended[before_gap, 0], 0.0, atol=1e-9)
    assert np.allclose(detrended[after_gap, 0], 0.0, atol=1e-9)


def test_detrend_by_arc_uses_a_rolling_not_global_window():
    # a degree-5 fit spanning a long, genuinely continuous run can swing
    # wildly near its own ends (Runge's-phenomenon-style edge
    # instability) even with no gap at all -- confirmed on a real
    # 241-epoch arc. A rolling window keeps every individual fit short
    # regardless of how long the overall run is.
    rng = np.random.default_rng(0)
    n = 300
    t = np.arange(n, dtype=np.float64)
    trend = 0.01 * t - 0.00006 * t ** 2  # a slow, smooth curve over the whole run
    noise = rng.normal(0, 0.1, size=n)
    raw = (trend + noise).reshape(-1, 1)
    slip = np.zeros((n, 1), dtype=bool)

    detrended = runner._detrend_by_arc(raw, slip)
    assert np.all(np.isfinite(detrended))
    # residuals should look like the injected noise, not blow up near
    # the ends the way a single whole-run polynomial fit did on real data
    assert np.nanmax(np.abs(detrended)) < 1.0


def test_detrend_by_arc_leaves_missing_data_as_nan():
    raw = np.array([[1.0], [np.nan], [1.0], [1.0], [1.0], [1.0], [1.0], [1.0]])
    slip = np.zeros((8, 1), dtype=bool)

    detrended = runner._detrend_by_arc(raw, slip)
    assert np.isnan(detrended[1, 0])


def test_run_rnxcsv_raises_on_nonzero_exit(monkeypatch):
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a, returncode=1, stdout='', stderr='boom')
    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(runner.RunnerError, match='boom'):
        runner._run_rnxcsv('ringo', ['x'], 10)


def test_run_rnxcsv_raises_on_empty_stdout_even_with_rc_zero(monkeypatch):
    # RINGO exits 0 even for a missing/unreadable file -- see module docstring.
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a, returncode=0, stdout='   ', stderr='no obs file found')
    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(runner.RunnerError, match='no obs file found'):
        runner._run_rnxcsv('ringo', ['x'], 10)


def test_run_rnxcsv_raises_clear_error_when_binary_missing(monkeypatch):
    def fake_run(*a, **kw):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(runner.RunnerError, match='install_ringo'):
        runner._run_rnxcsv('ringo', ['x'], 10)
