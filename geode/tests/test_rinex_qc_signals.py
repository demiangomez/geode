"""Tests for geode.rinex_qc.signals: RINEX2 -> RINEX3 code normalization."""
import pytest

from geode.rinex_qc import signals


@pytest.mark.parametrize('gnss,code,expected_band_attr', [
    ('G', 'C1', '1C'),
    ('G', 'P1', '1W'),
    ('G', 'P2', '2W'),
    ('G', 'C2', '2L'),
    ('G', 'C5', '5Q'),
    ('R', 'C1', '1C'),
    ('R', 'P1', '1P'),
    ('R', 'C2', '2C'),
    ('R', 'P2', '2P'),
])
def test_rinex2_range_codes_map_to_expected_band_attr(gnss, code, expected_band_attr):
    mapping = signals.normalize(gnss, code, rinex_version=2)
    assert mapping.band_attr == expected_band_attr
    assert mapping.obs_code_orig == code
    assert mapping.snr_band_only is True


def test_rinex3_codes_pass_through_unchanged():
    # gnssmultipath's range1_Code/range2_Code for RINEX 3 files carry the
    # full 3-char observation code (type + band + attribute), e.g. 'C1C'
    # from a 'C1C L1C D1C S1C ...' SYS / # / OBS TYPES header line.
    mapping = signals.normalize('G', 'C1C', rinex_version=3)
    assert mapping.band_attr == '1C'
    assert mapping.band == '1'
    assert mapping.snr_band_only is False


def test_find_snr_code_rinex2_is_band_only():
    code, band_only = signals.find_snr_code(['S1', 'S2'], '1C', rinex_version=2)
    assert code == 'S1'
    assert band_only is True


def test_find_snr_code_rinex3_exact_attribute_match():
    code, band_only = signals.find_snr_code(['S1C', 'S2W'], '1C', rinex_version=3)
    assert code == 'S1C'
    assert band_only is False


def test_find_snr_code_rinex3_falls_back_to_band_only_match():
    # range/phase on 1W but the receiver only reports SNR on 1C
    code, band_only = signals.find_snr_code(['S1C'], '1W', rinex_version=3)
    assert code == 'S1C'
    assert band_only is True


def test_find_snr_code_returns_none_when_band_absent():
    code, band_only = signals.find_snr_code(['S2C'], '1C', rinex_version=3)
    assert code is None
