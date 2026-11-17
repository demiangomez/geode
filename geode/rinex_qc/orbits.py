"""
Orbit resolution and the same-day guard.

Resolves broadcast or SP3 orbits for the GPS day a RINEX file covers, via
the existing geode.pyProducts orbit-lookup classes -- no new download or
path-resolution logic needed here, that's all already built for PPP.
Guards against ever handing the QC tool (RINGO) an orbit product whose own
filename encodes a different day than the observation file's (a known
pitfall -- some tools accept a wrong-day file silently, producing unknown
elevations and bogus completeness).
"""
import os
import re

from geode import pyProducts

# geode.pyProducts can resolve broadcast orbits for 'G'/'R' via the legacy
# per-system brdc<doy>0.<yy>{n,g} files (GetBrdcOrbits/GetBrdgOrbits), or
# any of 'G','R','E','C' via the merged multi-GNSS BRDM/BRDC-MN product
# (GetBrdmOrbits). Galileo/BeiDou have no legacy per-system fallback.


class OrbitGuardError(Exception):
    pass


class OrbitResolutionError(Exception):
    pass


_NAV_FILENAME_DATE_PATTERNS = (
    # brdc<doy>0.<yy>n / brdc<doy>0.<yy>g -- legacy GPS/GLONASS broadcast
    (re.compile(r'^brdc(\d{3})0\.(\d{2})[ng]$', re.IGNORECASE),
     lambda m: (_expand_two_digit_year(int(m.group(2))), int(m.group(1)))),
    # BRDM00DLR_S_/BRDC00IGS_R_/BRDC00IGN_R_<YYYY><DOY>0000_01D_MN.rnx -- merged multi-GNSS
    (re.compile(r'^(?:BRDM00DLR_S_|BRDC00IGS_R_|BRDC00IGN_R_)(\d{4})(\d{3})0000_01D_MN\.rnx',
                re.IGNORECASE),
     lambda m: (int(m.group(1)), int(m.group(2)))),
)


def _expand_two_digit_year(yy):
    # matches pyDate.Date's own pivot for 2-digit years
    return 2000 + yy if yy < 80 else 1900 + yy


def _filename_date(path):
    """(year, doy) encoded in a known broadcast-nav filename, or None if unrecognized."""
    name = os.path.basename(path)
    for pattern, extract in _NAV_FILENAME_DATE_PATTERNS:
        m = pattern.match(name)
        if m:
            return extract(m)
    return None


def guard_same_day(nav_path, date):
    """Raise OrbitGuardError if nav_path's filename encodes a different day than date.

    A nav file whose naming convention isn't recognized is allowed through
    unguarded (e.g. a user-supplied SP3 file with an unconventional name) --
    this guard is a safety net for the common patterns, not a filename
    allowlist.
    """
    parsed = _filename_date(nav_path)
    if parsed is not None and parsed != (date.year, date.doy):
        raise OrbitGuardError(
            'Orbit file %s is for %04d-%03d, not the observation date %04d-%03d'
            % (os.path.basename(nav_path), parsed[0], parsed[1], date.year, date.doy))


class OrbitResolution:
    """Resolved orbit product paths for one RINEX file, plus cleanup."""

    def __init__(self, orbit_source, nav_paths=(), sp3_paths=(), systems_without_orbits=(),
                orbit_products=()):
        self.orbit_source           = orbit_source
        self.nav_paths              = list(nav_paths)
        self.sp3_paths              = list(sp3_paths)
        self.systems_without_orbits = list(systems_without_orbits)
        self._orbit_products        = list(orbit_products)  # keeps GetBrdcOrbits etc. alive for cleanup

    def cleanup(self):
        for product in self._orbit_products:
            product.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()


def resolve_orbits(Config, date, observed_systems, orbit_source='brdc', work_dir=None,
                   explicit_nav_paths=(), explicit_sp3_paths=()):
    """
    Resolve orbit products for `date`.

    :param Config: a pyOptions.ReadOptions instance (brdc_path/sp3_path/sp3types).
    :param date: pyDate.Date for the observation day.
    :param observed_systems: iterable of single-char GNSS system codes seen
        in the RINEX file (e.g. {'G', 'R', 'E'}).
    :param orbit_source: 'brdc' or 'sp3'.
    :param explicit_nav_paths/explicit_sp3_paths: user-supplied --nav/--sp3
        overrides; still run through the same-day guard. When given,
        archive lookup is skipped entirely.
    :return: an OrbitResolution (also a context manager for cleanup).
        Raises OrbitResolutionError if nothing usable could be resolved at
        all (e.g. explicit sp3 orbit_source with no broadcast fallback).
    """
    if explicit_nav_paths or explicit_sp3_paths:
        for path in (*explicit_nav_paths, *explicit_sp3_paths):
            guard_same_day(path, date)
        # Trust an explicit override to cover whatever it was given for --
        # GeoDE has no local parser to verify which systems a given nav/sp3
        # file actually contains, so nothing is excluded preemptively here.
        return OrbitResolution(orbit_source,
                               nav_paths=explicit_nav_paths,
                               sp3_paths=explicit_sp3_paths,
                               systems_without_orbits=[])

    if orbit_source == 'sp3':
        return _resolve_sp3(Config, date, observed_systems, work_dir)
    return _resolve_brdc(Config, date, observed_systems, work_dir)


def _resolve_brdc(Config, date, observed_systems, work_dir):
    products = []
    try:
        brdm = pyProducts.GetBrdmOrbits(Config.brdc_path, date, work_dir)
        guard_same_day(brdm.brdm_path, date)
        products.append(brdm)
        # BRDM covers every system pyProducts supports; nothing excluded.
        return OrbitResolution('brdc', nav_paths=[brdm.brdm_path],
                               systems_without_orbits=[], orbit_products=products)
    except pyProducts.pyBrdmException:
        pass

    # fall back to legacy per-system files: GPS (.n) always, GLONASS (.g) if present
    nav_paths = []
    resolved_systems = set()
    try:
        brdc = pyProducts.GetBrdcOrbits(Config.brdc_path, date, work_dir)
        products.append(brdc)
        nav_paths.append(brdc.brdc_path)
        resolved_systems.add('G')
    except pyProducts.pyBrdcException:
        pass

    if 'R' in observed_systems:
        try:
            brdg = pyProducts.GetBrdgOrbits(Config.brdc_path, date, work_dir)
            products.append(brdg)
            nav_paths.append(brdg.brdg_path)
            resolved_systems.add('R')
        except pyProducts.pyBrdgException:
            pass

    for path in nav_paths:
        guard_same_day(path, date)

    if not nav_paths:
        for product in products:
            product.cleanup()
        raise OrbitResolutionError(
            'Could not resolve any broadcast orbit product for %04d-%03d' % (date.year, date.doy))

    systems_without_orbits = sorted(set(observed_systems) - resolved_systems)

    return OrbitResolution('brdc', nav_paths=nav_paths,
                           systems_without_orbits=systems_without_orbits,
                           orbit_products=products)


def _resolve_sp3(Config, date, observed_systems, work_dir):
    try:
        sp3 = pyProducts.GetSp3Orbits(Config.sp3_path, date, Config.sp3types, work_dir)
    except pyProducts.pySp3Exception as e:
        raise OrbitResolutionError(str(e)) from e

    guard_same_day(sp3.file_path, date)
    # SP3 orbits cover whatever systems the product itself includes; GeoDE
    # has no local parser for that today, so nothing is excluded here --
    # a system absent from the SP3 file simply yields no estimates for it.
    return OrbitResolution('sp3', sp3_paths=[sp3.file_path],
                           systems_without_orbits=[], orbit_products=[sp3])
