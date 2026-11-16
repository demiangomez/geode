"""Tests for geode.rinex_qc.orbits: the same-day guard."""
import pytest

from geode import pyDate
from geode.rinex_qc import orbits


@pytest.mark.parametrize('filename', [
    'brdc0050.26n',
    'brdc0050.26g',
    'BRDC00IGS_R_20260050000_01D_MN.rnx',
    'BRDM00DLR_S_20260050000_01D_MN.rnx',
])
def test_guard_accepts_matching_day(filename):
    date = pyDate.Date(year=2026, doy=5)
    orbits.guard_same_day(filename, date)  # must not raise


def test_guard_rejects_wrong_day():
    date = pyDate.Date(year=2026, doy=5)
    with pytest.raises(orbits.OrbitGuardError):
        orbits.guard_same_day('brdc2110.25n', date)


def test_guard_allows_unrecognized_filename_conventions():
    # a user-supplied file with a naming convention this module doesn't
    # recognize is let through -- this is a safety net for known
    # conventions, not a filename allowlist.
    date = pyDate.Date(year=2026, doy=5)
    orbits.guard_same_day('my_custom_orbit_file.sp3', date)  # must not raise
