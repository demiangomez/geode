"""Tests for geode.rinex_qc.signals: SNR observation code selection."""
from geode.rinex_qc import signals


def test_finds_rinex2_band_only_code():
    assert signals.find_snr_code(['S1', 'S2'], '1') == 'S1'


def test_finds_rinex3_code_on_matching_band():
    assert signals.find_snr_code(['S1C', 'S2W'], '1') == 'S1C'


def test_returns_first_matching_code_when_several_share_a_band():
    assert signals.find_snr_code(['S1C', 'S1W'], '1') == 'S1C'


def test_returns_none_when_band_absent():
    assert signals.find_snr_code(['S2C'], '1') is None


def test_ignores_single_character_codes():
    # a stray 'S' with nothing after it can't carry a band digit
    assert signals.find_snr_code(['S'], '1') is None
