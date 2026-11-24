"""
Configuration for the RINEX QC module.

Loads a JSON config file, then applies CLI switches on top (switches win),
and freezes the result into an immutable RinexQCConfig. ``config_hash`` is a
SHA-256 of the canonical (sorted-key, path-free) JSON of only the settings
that change the *science* -- two runs with the same config_hash must produce
identical statistics, which is what rinex_qc_run's unique key and pool()
based weekly/yearly stacking both depend on.
"""
import dataclasses
import hashlib
import json

# Ashtech receivers (and other vintage non-dB-Hz hardware) report SNR as a
# raw AGC-style value, not dB-Hz -- 1996-era Ashtech S1/S2 are ~140, not in
# the 0-60ish dB-Hz range. Matched case-insensitively against RinexRecord's
# recType (e.g. 'ASHTECH Z-XII3').
DEFAULT_SNR_RECEIVER_TYPES = ('ASHTECH',)
DEFAULT_SNR_MEDIAN_THRESHOLD = 70.0

DEFAULT_ELEV_CUTOFF_DEG = 10.0
DEFAULT_ORBIT_SOURCE    = 'brdc'
DEFAULT_GRID_ID         = 1
DEFAULT_RINGO_BIN       = 'ringo'  # assumed on PATH, see scripts/install_ringo.sh


class RinexQCConfigError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class SnrUnitPolicy:
    receiver_types:   tuple = DEFAULT_SNR_RECEIVER_TYPES
    median_threshold: float = DEFAULT_SNR_MEDIAN_THRESHOLD

    def is_receiver_units(self, receiver_type, snr_median):
        """True if SNR for this receiver/sample should be flagged 'receiver' units, not dBHz."""
        rtype = (receiver_type or '').upper()
        if any(marker in rtype for marker in self.receiver_types):
            return True
        return snr_median is not None and snr_median > self.median_threshold


# Fields that affect the computed statistics -- these, and only these, go
# into config_hash. Run-specific bookkeeping (explicit --nav/--sp3 paths,
# keep_tool_outputs, work_dir) never does: a run pointed at a different file
# with the same science settings must hash identically.
_HASHED_FIELDS = ('elev_cutoff_deg', 'systems', 'orbit_source', 'default_grid_id',
                  'snr_unit_policy')


@dataclasses.dataclass(frozen=True)
class RinexQCConfig:
    elev_cutoff_deg:   float = DEFAULT_ELEV_CUTOFF_DEG
    systems:           tuple = ()          # empty = all systems observed in the file
    orbit_source:      str   = DEFAULT_ORBIT_SOURCE  # 'brdc' | 'sp3'
    default_grid_id:   int   = DEFAULT_GRID_ID
    snr_unit_policy:   SnrUnitPolicy = dataclasses.field(default_factory=SnrUnitPolicy)

    # run-specific, excluded from config_hash
    nav_paths:         tuple = ()          # explicit --nav override(s)
    sp3_paths:         tuple = ()          # explicit --sp3 override(s)
    keep_tool_outputs: bool  = False
    work_dir:          str   = None        # None = system tempdir
    ringo_bin:         str   = DEFAULT_RINGO_BIN  # path to the ringo binary, or a bare name on PATH

    @property
    def config_hash(self):
        canonical = {}
        for field in _HASHED_FIELDS:
            value = getattr(self, field)
            if dataclasses.is_dataclass(value):
                value = dataclasses.asdict(value)
            canonical[field] = value
        payload = json.dumps(canonical, sort_keys=True, default=_json_default)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    @classmethod
    def load(cls, json_path=None, **cli_overrides):
        """Load a JSON config, then apply CLI switches on top (switches win).

        cli_overrides: keyword args matching RinexQCConfig's field names.
        Only keys whose value is not None override whatever the JSON (or
        the default) provided -- a CLI switch the user didn't pass should
        never clobber a JSON setting with a default.
        """
        data = {}
        if json_path:
            with open(json_path) as f:
                data = json.load(f)
            unknown = set(data) - {f.name for f in dataclasses.fields(cls)}
            if unknown:
                raise RinexQCConfigError('Unknown config key(s) in %s: %s'
                                         % (json_path, ', '.join(sorted(unknown))))

        for key, value in cli_overrides.items():
            if value is not None:
                data[key] = value

        if 'systems' in data and isinstance(data['systems'], (list, tuple)):
            data['systems'] = tuple(data['systems'])
        if 'nav_paths' in data and isinstance(data['nav_paths'], (list, tuple)):
            data['nav_paths'] = tuple(data['nav_paths'])
        if 'sp3_paths' in data and isinstance(data['sp3_paths'], (list, tuple)):
            data['sp3_paths'] = tuple(data['sp3_paths'])
        if 'snr_unit_policy' in data and isinstance(data['snr_unit_policy'], dict):
            data['snr_unit_policy'] = SnrUnitPolicy(**data['snr_unit_policy'])

        if data.get('orbit_source') not in (None, 'brdc', 'sp3'):
            raise RinexQCConfigError("orbit_source must be 'brdc' or 'sp3', got %r"
                                     % data['orbit_source'])

        try:
            return cls(**data)
        except TypeError as e:
            raise RinexQCConfigError(str(e)) from e


def _json_default(obj):
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError('Object of type %s is not JSON serializable' % type(obj).__name__)
