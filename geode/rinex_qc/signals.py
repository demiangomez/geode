"""
SNR observation code selection for a given band.

RINGO reports multipath per *band* (1/2/5), not per exact RINEX
observation code (see geode.rinex_qc.runner), so the only mapping
problem left here is finding which raw SNR column belongs with a given
band -- there is no (gnss, obs_code) -> (band, attribute) table to
maintain any more.
"""

# GNSS system codes RINGO and this module support (GPS/GLONASS/Galileo/
# BeiDou -- see knowledge/decisions/0005-switch-to-ringo.md).
SYSTEMS = ('G', 'R', 'E', 'C')


def find_snr_code(available_snr_codes, band):
    """
    Pick the SNR observation code on the given band, e.g. 'S1C' or 'S1W'
    for band '1' (RINEX 3), or 'S1' for band '1' (RINEX 2, which carries
    no tracking attribute at all). Band-only by construction -- RINGO
    never tells us which exact range/phase attribute it combined for a
    given band's multipath, so there is never a specific attribute to
    match SNR against either.

    :return: the SNR code, or None if nothing on this band was found.
    """
    for code in available_snr_codes:
        if len(code) > 1 and code[1] == band:
            return code
    return None
