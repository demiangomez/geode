"""
Tests for pyRinex._PrnObsFixer -- pure string/state logic, no external
tools needed (unlike test_pyrinex_header.py's ReadRinex-based tests,
which need gfzrnx_lx).

A satellite with zero observations of a given code (e.g. GPS P1 under
Anti-Spoofing, where the receiver tracks C1 but never decodes P1) is
written as a blank 6-character field in RINEX 2's 'PRN / # OF OBS'
record rather than an explicit 0 -- legal per the RINEX 2 spec, but
gnssmultipath 2.2.0's line.split(' ')-based tokenizer drops the blank
field as a missing token instead of reading it as zero, eventually
raising "pop from empty list" elsewhere in its header parser.

When a file declares more than 9 observation types, this record (like
'# / TYPES OF OBSERV' itself) continues onto a second physical line per
satellite with the PRN field left blank -- and a satellite can have zero
observations for *every* obscode on that continuation line, making the
whole line blank, not just one field between two populated ones. Found
via two real stations during --backfill runs (usa.gisa, both the
single-line 7-obscode case and this multi-line 11-obscode one).
"""
from geode.pyRinex import _PrnObsFixer


def test_interior_blank_is_zero_filled():
    fixer = _PrnObsFixer()
    fixer.note_obs_types_line('     7    C1    L1    L2    P1    P2    S1    S2            # / TYPES OF OBSERV\n')

    # G03: 7 declared obs types, the 4th (P1) has zero observations
    line = '   G03  1109  1109  1105        1105  1109  1105            PRN / # OF OBS\n'
    fixed = fixer.fix_prn_obs_line(line)
    assert len(fixed) == len(line)
    assert fixed[:60].split() == ['G03', '1109', '1109', '1105', '0', '1105', '1109', '1105']


def test_fully_populated_line_is_byte_identical():
    fixer = _PrnObsFixer()
    fixer.note_obs_types_line('     7    C1    L1    L2    P1    P2    S1    S2            # / TYPES OF OBSERV\n')

    # R01: all 7 declared obs types have observations -- nothing to fix
    line = '   R01  1036  1036  1033  1036  1033  1036  1033            PRN / # OF OBS\n'
    assert fixer.fix_prn_obs_line(line) == line


def test_trailing_slots_past_declared_count_are_untouched():
    fixer = _PrnObsFixer()
    fixer.note_obs_types_line('     3    C1    L1    L2                                    # / TYPES OF OBSERV\n')

    # only 3 obs types declared -- the unused 4th-9th slots are normal
    # padding, not "zero observations", and must stay blank
    line = '   G01  1109  1105  1105                                    PRN / # OF OBS\n'
    assert fixer.fix_prn_obs_line(line) == line


def test_continuation_line_entirely_blank_is_zero_filled():
    # 11 declared obs types -> 9 on the first physical line, 2 (S2, S5)
    # on a continuation line with a blank PRN field. A satellite that
    # tracks nothing on either of those last two codes (e.g. a GLONASS
    # satellite with no L5-equivalent signal) has a WHOLLY blank
    # continuation line -- nothing on the line to anchor a content-only
    # fix, which is exactly what broke the simpler, line-local version
    # of this fix.
    fixer = _PrnObsFixer()
    fixer.note_obs_types_line('    11    C1    C2    C5    L1    L2    L5    P1    P2    S1# / TYPES OF OBSERV\n')
    fixer.note_obs_types_line('          S2    S5                                          # / TYPES OF OBSERV\n')

    first_line = '   R01   373               371         373                  PRN / # OF OBS\n'
    cont_line  = '                                                            PRN / # OF OBS\n'

    fixed_first = fixer.fix_prn_obs_line(first_line)
    fixed_cont  = fixer.fix_prn_obs_line(cont_line)

    # total_obs_types=11, consumed=0 before this line -> all 9 slots on the
    # satellite's first physical line are "real" (min(9, 11-0) == 9), so the
    # 3 trailing blanks (slots 6-8, unused only because this satellite's
    # continuation line covers S2/S5) get zero-filled too, not just the
    # interior ones.
    assert fixed_first[:60].split() == ['R01', '373', '0', '0', '371', '0', '373', '0', '0', '0']
    assert fixed_cont[:60].split() == ['0', '0']
    assert fixed_cont[:6] == '      '  # PRN field itself stays blank on a continuation line


def test_continuation_line_partially_populated_is_fixed_correctly():
    fixer = _PrnObsFixer()
    fixer.note_obs_types_line('    11    C1    C2    C5    L1    L2    L5    P1    P2    S1# / TYPES OF OBSERV\n')
    fixer.note_obs_types_line('          S2    S5                                          # / TYPES OF OBSERV\n')

    first_line = '   E02   120         120   120         120               120PRN / # OF OBS\n'
    cont_line  = '               120                                          PRN / # OF OBS\n'

    fixer.fix_prn_obs_line(first_line)
    fixed_cont = fixer.fix_prn_obs_line(cont_line)
    assert fixed_cont[:60].split() == ['0', '120']


def test_new_prn_resets_the_consumed_counter():
    # without a reset, a second satellite's first line would be treated
    # as a continuation of the previous satellite's record.
    fixer = _PrnObsFixer()
    fixer.note_obs_types_line('    11    C1    C2    C5    L1    L2    L5    P1    P2    S1# / TYPES OF OBSERV\n')
    fixer.note_obs_types_line('          S2    S5                                          # / TYPES OF OBSERV\n')

    fixer.fix_prn_obs_line('   E02   120         120   120         120               120PRN / # OF OBS\n')
    fixer.fix_prn_obs_line('               120                                          PRN / # OF OBS\n')

    # next satellite's first line should again get all 9 real slots,
    # not be treated as a 3rd continuation line of E02 (which would make
    # every slot "beyond declared count" and left untouched).
    next_first = '   E03   480         480   480         480               480PRN / # OF OBS\n'
    fixed = fixer.fix_prn_obs_line(next_first)
    assert fixed[:60].split() == ['E03', '480', '0', '480', '480', '0', '480', '0', '0', '480']


def test_falls_back_to_content_sniffing_when_obs_type_count_unknown():
    # defensive path: if '# / TYPES OF OBSERV' was somehow never seen,
    # don't guess -- only fix interior blanks, same as before.
    fixer = _PrnObsFixer()
    line = '   G03  1109  1109  1105        1105  1109  1105            PRN / # OF OBS\n'
    fixed = fixer.fix_prn_obs_line(line)
    assert fixed[:60].split() == ['G03', '1109', '1109', '1105', '0', '1105', '1109', '1105']
