"""
Input preparation: open and sanitize an archived RINEX file.

Deliberately thin. geode.pyRinex.ReadRinex already does everything this
module would otherwise reimplement -- temp-copy, decompression, header
normalization (including the blank TIME SYSTEM fix), RINEX 2/3 conversion
and interval checking -- because it is what ArchiveService.py/ScanArchive.py
already run before PPP. RinexQC reuses it rather than duplicating it.
"""
from geode import pyRinex
from geode import pyRinexName

# Placeholder NetworkCode for files not yet tied to a DB station record,
# matching ArchiveService.py's own convention for files still in the
# repository.
UNKNOWN_NETWORK_CODE = 'rnx'


def prepare(origin_file, NetworkCode=None, StationCode=None, min_time_seconds=1):
    """
    Open and sanitize origin_file via pyRinex.ReadRinex.

    :param origin_file: path to the archived RINEX/CRINEX(Z) file.
    :param NetworkCode/StationCode: pass the real values when known (e.g.
        from the rinex table during backfill/incremental runs); when None,
        StationCode is derived from the filename and NetworkCode defaults
        to UNKNOWN_NETWORK_CODE (standalone/no-DB Phase 1 usage).
    :param min_time_seconds: forwarded to ReadRinex; RinexQC does not need
        the >=3600s "at least an hour of data" guard PPP relies on, so this
        defaults low (1s) unless the caller wants ReadRinex's own guard.
    :return: a pyRinex.ReadRinex context manager. Raises pyRinex's own
        exceptions (pyRinexException and subclasses) on bad files -- the
        caller (geode.rinex_qc.pipeline) is responsible for turning those
        into a failed run record rather than aborting a batch.
    """
    if StationCode is None:
        StationCode = pyRinexName.RinexNameFormat(origin_file).StationCode.lower()
    if NetworkCode is None:
        NetworkCode = UNKNOWN_NETWORK_CODE

    return pyRinex.ReadRinex(NetworkCode, StationCode, origin_file,
                             min_time_seconds=min_time_seconds)
