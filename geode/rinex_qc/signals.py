"""
RINEX 2 -> RINEX 3 (gnss, band_attr) signal normalization.

Table-driven by design: every RINEX 2 ambiguous-code decision lives in one
dict, not scattered across branches, so re-deciding (say) GPS C2 -> 2L vs
2X is a one-line table edit, not a hunt through aggregate.py. RINEX 3 codes
already carry band+attribute and pass through unchanged.
"""

# GNSS system codes both gnssmultipath and this module support (GPS/
# GLONASS/Galileo/BeiDou -- see knowledge/decisions/0003-gnssmultipath-dependency.md).
SYSTEMS = ('G', 'R', 'E', 'C')

# (system, RINEX2 range code) -> RINEX3 band_attr. GPS C2->2L and C5->5Q are
# genuinely ambiguous (RINEX 2 never distinguished L2C/L5 tracking
# attributes) -- accepted as defaults per the design doc.
RINEX2_RANGE_MAP = {
    ('G', 'C1'): '1C', ('G', 'P1'): '1W',
    ('G', 'C2'): '2L', ('G', 'P2'): '2W',
    ('G', 'C5'): '5Q',
    ('R', 'C1'): '1C', ('R', 'P1'): '1P',
    ('R', 'C2'): '2C', ('R', 'P2'): '2P',
}


class SignalMapping:
    """One observation code's normalized identity."""

    def __init__(self, gnss, band_attr, obs_code_orig, snr_band_only):
        self.gnss          = gnss
        self.band_attr     = band_attr
        self.obs_code_orig = obs_code_orig
        self.snr_band_only = snr_band_only

    @property
    def band(self):
        return self.band_attr[0]


def normalize(gnss, obs_code, rinex_version):
    """Map one range/phase observation code to (gnss, band_attr), keeping the original."""
    if rinex_version >= 3:
        # already band+attr, e.g. '1C', '2W'
        return SignalMapping(gnss, obs_code[1:3], obs_code, snr_band_only=False)

    band_attr = RINEX2_RANGE_MAP.get((gnss, obs_code))
    if band_attr is None:
        # not one of the known range codes (shouldn't normally happen --
        # gnssmultipath only hands us codes it found in the header) --
        # fall back to band-only with an unknown attribute.
        band_attr = (obs_code[1] + '?') if len(obs_code) > 1 and obs_code[1].isdigit() else '??'
    return SignalMapping(gnss, band_attr, obs_code, snr_band_only=True)


def find_snr_code(available_snr_codes, band_attr, rinex_version):
    """
    Pick the SNR observation code (as it appears in analysisResults[sys]['SNR'])
    that belongs with band_attr.

    RINEX 2 SNR codes (S1/S2/S5) carry no tracking attribute -- they attach
    to a band, not a specific signal, so the match is band-only there by
    construction. For RINEX 3, try an exact band+attribute match first
    (e.g. 'S1C' for band_attr '1C'); receivers sometimes report SNR under a
    different attribute than the range/phase codes (e.g. C1W/L1W with only
    S1C present), so fall back to any SNR code on the same band.

    :return: (snr_code, snr_band_only) -- snr_code is None if nothing on
        this band was found at all.
    """
    band = band_attr[0]
    if rinex_version < 3:
        code = 'S' + band
        return (code, True) if code in available_snr_codes else (None, True)

    exact = 'S' + band_attr
    if exact in available_snr_codes:
        return exact, False

    for code in available_snr_codes:
        if len(code) > 1 and code[1] == band:
            return code, True

    return None, True
