"""Tests for RinexRecord.check_header()'s TIME SYSTEM normalization.

gnssmultipath 2.2.0 raises an IndexError on a blank TIME SYSTEM field in
TIME OF FIRST OBS, regardless of whether the file is single-GNSS or mixed.
pyRinex only used to backfill that field for mixed ('M') files; these tests
cover the single-GNSS case too.

ReadRinex.__init__ always shells out to gfzrnx_lx (RunGfzrnx()) after
check_header(), so instantiating it -- even just to exercise the header
normalization above -- requires the binary. gfzrnx_lx is a manually
installed external dependency (see INSTALL.md), not available via conda/pip,
so it isn't present in CI; skip there rather than failing on an environment
gap unrelated to what these tests check.
"""
import os
import shutil
from datetime import datetime, timedelta

import pytest

from geode import pyRinex

pytestmark = pytest.mark.skipif(shutil.which('gfzrnx_lx') is None,
                                reason='gfzrnx_lx binary not available (external dependency, '
                                       'not installed via conda/pip -- see INSTALL.md)')

# A minimal, real-header-derived RINEX 2.11 GPS observation file (3 epochs,
# LHCL station), with the system letter and TIME SYSTEM field left as
# format placeholders so each test can control exactly what check_header()
# sees.
_HEADER = """\
     2.11           OBSERVATION DATA    {sys} (GPS)             RINEX VERSION / TYPE
teqc  2016Nov7      IGN-Ar              20260101 04:22:29UTCPGM / RUN BY / DATE
LHCL                                                        MARKER NAME
41518S001                                                   MARKER NUMBER
IGN-Ar              Admin.de Parques Nacionales             OBSERVER / AGENCY
5112K74584          TRIMBLE NETR9       5.20                REC # / TYPE / VERS
1441107166          TRM57971.00     NONE                    ANT # / TYPE
  2079355.6185 -4582903.4762 -3905925.6615                  APPROX POSITION XYZ
        0.0000        0.0000        0.0000                  ANTENNA: DELTA H/E/N
     1     1                                                WAVELENGTH FACT L1/2
     9    L1    L2    C1    P1    P2    D1    D2    S1    S2# / TYPES OF OBSERV
     1.0000                                                 INTERVAL
    17                                                      LEAP SECONDS
  2026     1     1     0     0    0.0000000     {tsys:<3s}         TIME OF FIRST OBS
                                                            END OF HEADER
"""

_DATA = """\
 26  1  1  0  0  0.0000000  0 11G05G07G08G09G11G13G14G20G21G22G30
 117177286.672 6  91307108.872 9  22298104.938 6                  22298114.66448
      2009.598 6                        44.200          41.900
 109759153.175 6  85526698.087 9  20886423.531 6                  20886431.09448
     -2302.281 6                        46.700          42.700
 131351731.622 3 102352021.401 7  24995401.867 3                  24995414.40646
       878.094 3                        36.300          31.700
 119537308.349 6  93146027.242 8  22747160.141 6                  22747171.76248
     -2401.109 6                        43.100          37.900
 129973633.610 3 101278197.365 8  24733182.313 3                  24733198.79345
     -2632.535 3                        37.200          37.600
 123674216.994 5  96369596.72346  23534412.820 5                  23534423.98446
      2592.227 5                        41.600          19.300
 112314518.995 6  87517928.974 9  21372703.859 6                  21372713.94147
      2889.684 6                        45.600          45.200
 110298453.251 6  85946926.55047  20989090.930 6                  20989099.19947
      -617.406 6                        47.500          33.000
 108332384.830 6  84414899.667 9  20614956.695 6                  20614964.99648
       146.598 6                        46.400          48.800
 122781747.020 6  95674183.97346  23364559.875 6                  23364572.16046
      3634.840 6                        42.700          22.000
 105747294.884 7  82400581.406 9  20122983.672 7                  20122993.69149
      -438.434 7                        47.800          46.700
 26  1  1  0  0  1.0000000  0 11G05G07G08G09G11G13G14G20G21G22G30
 117175277.388 6  91305543.186 9  22297722.453 6                  22297732.67648
      2009.082 6      1565.686 9        44.100          41.500
 109761455.692 6  85528492.217 9  20886861.523 6                  20886869.54348
     -2302.637 6     -1794.130 9        46.300          42.600
 131350853.703 3 102351337.335 7  24995234.586 3                  24995247.46146
       877.688 3       684.066 7        36.100          32.100
 119539709.639 6  93147898.372 8  22747616.852 6                  22747628.52048
     -2401.277 6     -1871.130 8        43.200          38.200
 129976266.268 3 101280248.799 8  24733683.188 3                  24733700.15245
     -2632.730 3     -2051.434 8        37.100          37.000
 123671624.833 5  96367576.86746  23533919.305 5                  23533931.28946
      2592.234 5      2019.85646        41.300          19.600
 112311629.578 6  87515677.478 9  21372153.539 6                  21372163.86747
      2889.289 6      2251.496 9        45.200          45.300
 110299071.029 7  85947407.93347  20989208.727 7                  20989216.65247
      -618.078 7      -481.38347        47.600          32.800
 108332238.603 6  84414785.720 9  20614929.117 6                  20614936.97748
       145.977 6       113.947 9        46.400          48.900
 122778112.266 6  95671351.69946  23363868.734 6                  23363880.13346
      3634.746 6      2832.27446        42.600          22.900
 105747733.528 6  82400923.219 9  20123067.461 6                  20123077.37149
      -438.809 6      -341.813 9        47.300          46.400
 26  1  1  0  0  2.0000000  0 11G05G07G08G09G11G13G14G20G21G22G30
 117173268.633 6  91303977.919 9  22297341.016 6                  22297349.77748
      2008.461 6      1565.266 9        44.300          41.700
 109763758.477 6  85530286.627 9  20887299.375 6                  20887307.78948
     -2303.008 6     -1794.410 9        46.400          42.700
 131349976.257 3 102350653.586 7  24995068.000 3                  24995080.48046
       877.129 3       683.749 7        36.300          32.400
 119542111.182 6  93149769.694 8  22748074.172 6                  22748085.50848
     -2401.754 6     -1871.322 8        43.600          37.800
 129978899.037 4 101282300.298 8  24734184.211 4                  24734200.75045
     -2632.898 4     -2051.499 8        37.700          36.500
 123669032.698 5  96365557.02846  23533426.094 5                  23533437.89146
      2592.031 5      2019.83846        41.100          19.000
 112308740.599 6  87513426.342 9  21371603.703 6                  21371614.43447
      2888.676 6      2251.136 9        45.000          45.100
 110299689.485 6  85947889.84247  20989326.398 6                  20989334.39547
      -618.895 6      -481.90947        47.100          33.000
 108332093.034 6  84414672.306 9  20614901.156 6                  20614909.34448
       145.102 6       113.415 9        46.200          48.800
 122774477.677 6  95668519.57346  23363176.867 6                  23363188.72746
      3634.453 6      2832.12646        43.000          22.500
 105748172.613 6  82401265.354 9  20123150.969 6                  20123160.69949
      -439.359 6      -342.136 9        47.200          46.300
"""


def _write_fixture(path, sys_letter, tsys):
    with open(path, 'w') as f:
        f.write(_HEADER.format(sys=sys_letter, tsys=tsys))
        f.write(_DATA)


def _time_of_first_obs_line(rinex_path):
    with open(rinex_path, encoding='latin-1') as f:
        for line in f:
            if 'TIME OF FIRST OBS' in line:
                return line
    raise AssertionError('TIME OF FIRST OBS not found in normalized header')


@pytest.mark.parametrize('sys_letter,expected_tsys', [
    ('G', 'GPS'),
    ('R', 'GLO'),
    ('E', 'GAL'),
    ('S', 'GPS'),
])
def test_blank_time_system_fixed_for_single_gnss(tmp_path, sys_letter, expected_tsys):
    fixture = tmp_path / 'lhcl0010.26o'
    _write_fixture(fixture, sys_letter, '   ')

    with pyRinex.ReadRinex('arg', 'lhcl', str(fixture), min_time_seconds=1) as r:
        assert r.header_fixed is True
        line = _time_of_first_obs_line(r.rinex_path)
        assert line[48:51] == expected_tsys


def test_blank_time_system_on_mixed_file_still_defaults_to_gps(tmp_path):
    fixture = tmp_path / 'lhcl0010.26o'
    _write_fixture(fixture, 'M', '   ')

    with pyRinex.ReadRinex('arg', 'lhcl', str(fixture), min_time_seconds=1) as r:
        assert r.header_fixed is True
        line = _time_of_first_obs_line(r.rinex_path)
        assert line[48:51] == 'GPS'


def test_already_populated_time_system_is_left_untouched(tmp_path):
    fixture = tmp_path / 'lhcl0010.26o'
    _write_fixture(fixture, 'G', 'GPS')
    original_line = _time_of_first_obs_line(str(fixture))

    with pyRinex.ReadRinex('arg', 'lhcl', str(fixture), min_time_seconds=1) as r:
        assert r.header_fixed is False
        assert _time_of_first_obs_line(r.rinex_path) == original_line


# Minimal mixed GPS/GLONASS RINEX 2.11 fixture reproducing a real station
# (usa.gisa) found during a --backfill run: GPS satellite G03 has zero P1
# observations (Anti-Spoofing), written as a blank field in 'PRN / # OF
# OBS' -- see test_pyrinex_prn_obs.py for why that crashes gnssmultipath.
_MIXED_HEADER = """\
     2.11           OBSERVATION DATA    M (MIXED)           RINEX VERSION / TYPE
teqc  2010Oct21     CORS/NGS/NOAA       20111215 06:22:28UTCPGM / RUN BY / DATE
GISA                                                        MARKER NAME
GISA                                                        MARKER NUMBER
5035K69869          TRIMBLE NETR9       4.22                REC # / TYPE / VERS
TRM55971.00         TRM55971.00     NONE                    ANT # / TYPE
 -2419202.3416 -4703569.1027  3553012.4295                  APPROX POSITION XYZ
        0.0000        0.0000        0.0000                  ANTENNA: DELTA H/E/N
     1     1                                                WAVELENGTH FACT L1/2
     7    C1    L1    L2    P1    P2    S1    S2            # / TYPES OF OBSERV
    30.0000                                                 INTERVAL
    15                                                      LEAP SECONDS
    24                                                      # OF SATELLITES
   G03  1109  1109  1105        1105  1109  1105            PRN / # OF OBS
   R01  1036  1036  1033  1036  1033  1036  1033            PRN / # OF OBS
  2011    12     8     0     0    0.0000000     GPS         TIME OF FIRST OBS
                                                            END OF HEADER
"""

_MIXED_DATA = """\
 11 12 08 00 00 00.0000000  0  2G03R01
  21720628.938   114142803.398 8  88942483.33645                  21720635.234
        48.900          35.500
  21792882.758   114522436.685 8  89238358.04046                  21792889.426
        49.800          37.800
 11 12 08 00 00 30.0000000  0  2G03R01
  21720277.750   114140966.260 8  88941213.79946                  21720282.340
        48.800          35.600
  21792882.758   114522436.685 8  89238358.04046                  21792889.426
        49.800          37.800
 11 12 08 00 01 00.0000000  0  2G03R01
  21720277.750   114140966.260 8  88941213.79946                  21720282.340
        48.800          35.600
  21792882.758   114522436.685 8  89238358.04046                  21792889.426
        49.800          37.800
"""


def test_prn_obs_blank_field_fixed_for_real_mixed_system_file(tmp_path):
    fixture = tmp_path / 'gisa0010.26o'
    fixture.write_text(_MIXED_HEADER + _MIXED_DATA)

    with pyRinex.ReadRinex('usa', 'gisa', str(fixture), min_time_seconds=1) as r:
        assert r.system == 'M'
        with open(r.rinex_path, encoding='latin-1') as f:
            prn_obs_lines = [line for line in f if 'PRN / # OF OBS' in line]

    assert prn_obs_lines[0][:60].split() == \
        ['G03', '1109', '1109', '1105', '0', '1105', '1109', '1105']
    assert prn_obs_lines[1][:60].split() == \
        ['R01', '1036', '1036', '1033', '1036', '1033', '1036', '1033']


# Second real station sample (usa.gisa, file gisa0860.26d.Z) found after the
# fix above: this file declares 11 observation types, so 'PRN / # OF OBS'
# wraps onto a blank-PRN continuation line per satellite -- and GLONASS R01
# has zero observations on *every* code covered by that continuation line
# (S2, S5), making the whole physical line blank. Built programmatically
# (rather than hand-typed) to get RINEX's fixed-width columns exactly right;
# see test_pyrinex_prn_obs.py for the same scenario at the unit level.
def _fmt_types_of_observ(codes):
    lines = []
    n = len(codes)
    for i, start in enumerate(range(0, n, 9)):
        chunk = codes[start:start + 9]
        head = f'{n:6d}' if i == 0 else ' ' * 6
        body = ''.join(f'{c:>6s}' for c in chunk).ljust(54)
        lines.append((head + body).ljust(60) + '# / TYPES OF OBSERV\n')
    return lines


def _fmt_prn_obs(prn, counts):
    lines = []
    n = len(counts)
    for i, start in enumerate(range(0, n, 9)):
        chunk = counts[start:start + 9]
        head = ('   ' + prn) if i == 0 else ' ' * 6
        body = ''.join((f'{c:6d}' if c is not None else ' ' * 6) for c in chunk).ljust(54)
        lines.append((head + body).ljust(60) + 'PRN / # OF OBS\n')
    return lines


def _fmt_epoch(yy, mm, dd, hh, mi, ss, sats):
    date = f' {yy:02d} {mm:02d} {dd:02d} {hh:02d} {mi:02d} {ss:010.7f}'
    return date + '  0' + f'{len(sats):3d}' + ''.join(f'{s:>3s}' for s in sats) + '\n'


def _fmt_obs_values(values):
    lines = []
    for start in range(0, len(values), 5):
        chunk = values[start:start + 5]
        parts = [(' ' * 16) if v is None else f'{v:14.3f}  ' for v in chunk]
        lines.append(''.join(parts).rstrip() + '\n')
    return lines


_MULTILINE_OBS_CODES = ['C1', 'C2', 'C5', 'L1', 'L2', 'L5', 'P1', 'P2', 'S1', 'S2', 'S5']


def _build_multiline_prn_obs_fixture():
    header = []
    header.append('     2.11           OBSERVATION DATA    M (MIXED)           RINEX VERSION / TYPE\n')
    header.append('teqc  2026Mar27     CORS/NGS/NOAA       20260327 06:22:28UTCPGM / RUN BY / DATE\n')
    header.append('GISA                                                        MARKER NAME\n')
    header.append('GISA                                                        MARKER NUMBER\n')
    header.append('5035K69869          TRIMBLE NETR9       5.44                REC # / TYPE / VERS\n')
    header.append('TRM55971.00         TRM55971.00     NONE                    ANT # / TYPE\n')
    header.append(' -2419202.3416 -4703569.1027  3553012.4295                  APPROX POSITION XYZ\n')
    header.append('        0.0000        0.0000        0.0000                  ANTENNA: DELTA H/E/N\n')
    header.append('     1     1                                                WAVELENGTH FACT L1/2\n')
    header += _fmt_types_of_observ(_MULTILINE_OBS_CODES)
    header.append('    30.0000                                                 INTERVAL\n')
    header.append('    18                                                      LEAP SECONDS\n')
    header.append('     3                                                      # OF SATELLITES\n')
    # E02/E03: Galileo, all codes observed except C2/L2/S2 (not tracked on this band).
    header += _fmt_prn_obs('E02', [120, None, 120, 120, None, 120, None, None, 120, None, 120])
    header += _fmt_prn_obs('E03', [480, None, 480, 480, None, 480, None, None, 480, None, 480])
    # R01: GLONASS, zero observations on every code of the continuation line
    # (S2, S5) -- the wholly-blank-line case that crashed gnssmultipath.
    header += _fmt_prn_obs('R01', [373, None, None, 371, None, 373, None, None, None, None, None])
    header.append('  2026     3    27     0     0    0.0000000     GPS         TIME OF FIRST OBS\n')
    header.append('                                                            END OF HEADER\n')

    data = []
    t0 = datetime(2026, 3, 27, 0, 0, 0)
    for i in range(240):  # 2 hours at 30s cadence, so Gfzrnx reports the interval correctly
        t = t0 + timedelta(seconds=30 * i)
        data.append(_fmt_epoch(26, 3, 27, t.hour, t.minute, float(t.second), ['E02', 'E03', 'R01']))
        data += _fmt_obs_values([None, None, 120.0, None, 120.0, None, None, None, 120.0, None, 120.0])
        data += _fmt_obs_values([None, None, 480.0, None, 480.0, None, None, None, 480.0, None, 480.0])
        data += _fmt_obs_values([373.0, None, None, 371.0, None, 373.0, None, None, None, None, None])

    return ''.join(header) + ''.join(data)


def test_prn_obs_blank_continuation_line_fixed_for_real_multiline_system_file(tmp_path):
    content = _build_multiline_prn_obs_fixture()
    fixture = tmp_path / 'gisa0860.26o'
    fixture.write_text(content)

    # confirm the fixture reproduces the real crash before any fix is applied
    # -- gnssmultipath's header reader tokenizes 'PRN / # OF OBS' by
    # line.split(' '), and a wholly-blank continuation line gives it nothing
    # to pop().
    gnss_readers = pytest.importorskip('gnssmultipath.readers.readRinexObs')
    with pytest.raises(IndexError):
        gnss_readers.rinexReadObsFileHeader211(str(fixture), 1, 1, [], [], [])

    with pyRinex.ReadRinex('usa', 'gisa', str(fixture), min_time_seconds=1) as r:
        assert r.system == 'M'
        assert r.header_fixed is True
        with open(r.rinex_path, encoding='latin-1') as f:
            prn_obs_lines = [line for line in f if 'PRN / # OF OBS' in line]

        # R01's continuation line (S2, S5) was entirely blank -- now zero-filled.
        assert prn_obs_lines[4][:60].split() == \
            ['R01', '373', '0', '0', '371', '0', '373', '0', '0', '0']
        assert prn_obs_lines[5][:60].split() == ['0', '0']

        # conclusive check: gnssmultipath's own header reader, which crashed
        # on the raw file above, now succeeds on the fixed one.
        success = gnss_readers.rinexReadObsFileHeader211(r.rinex_path, 1, 1, [], [], [])[0]
        assert success == 1
