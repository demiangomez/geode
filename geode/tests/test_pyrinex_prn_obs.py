"""
Tests for pyRinex._fix_prn_obs_blanks() -- pure string formatting, no
external tools needed (unlike test_pyrinex_header.py's ReadRinex-based
tests, which need gfzrnx_lx).

A satellite with zero observations of a given code (e.g. GPS P1 under
Anti-Spoofing, where the receiver tracks C1 but never decodes P1) is
written as a blank 6-character field in RINEX 2's 'PRN / # OF OBS'
record rather than an explicit 0 -- legal per the RINEX 2 spec, but
gnssmultipath 2.2.0's line.split(' ')-based tokenizer drops the blank
field as a missing token instead of reading it as zero, eventually
raising "pop from empty list" elsewhere in its header parser. Found via
a real station (usa.gisa, a mixed GPS/GLONASS CORS receiver) during a
--backfill run.
"""
from geode.pyRinex import _fix_prn_obs_blanks


def test_interior_blank_is_zero_filled():
    # G03: 7 declared obs types, the 4th (P1) has zero observations
    line = '   G03  1109  1109  1105        1105  1109  1105            PRN / # OF OBS\n'
    fixed = _fix_prn_obs_blanks(line)
    assert len(fixed) == len(line)
    assert fixed[:60].split() == ['G03', '1109', '1109', '1105', '0', '1105', '1109', '1105']


def test_fully_populated_line_is_byte_identical():
    # R01: all 7 declared obs types have observations -- nothing to fix
    line = '   R01  1036  1036  1033  1036  1033  1036  1033            PRN / # OF OBS\n'
    assert _fix_prn_obs_blanks(line) == line


def test_trailing_blanks_past_last_populated_field_are_untouched():
    # only 3 obs types declared -- the unused 4th-9th slots are normal
    # padding, not "zero observations", and must stay blank
    line = '   G01  1109  1105  1105                                    PRN / # OF OBS\n'
    assert _fix_prn_obs_blanks(line) == line


def test_multiple_interior_blanks_all_zero_filled():
    line = '   G02       1105        1109                               PRN / # OF OBS\n'
    fixed = _fix_prn_obs_blanks(line)
    assert fixed[:60].split() == ['G02', '0', '1105', '0', '1109']


def test_preserves_label_and_length_exactly():
    line = '   G03  1109  1109  1105        1105  1109  1105            PRN / # OF OBS\n'
    fixed = _fix_prn_obs_blanks(line)
    assert fixed.endswith('PRN / # OF OBS\n')
    assert fixed[:6] == line[:6]  # leading blank + PRN untouched
