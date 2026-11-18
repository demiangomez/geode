"""
Subprocess wrapper around the RINGO binary (GSI, Japan).

Replaces gnssmultipath (ADR-0003/0004) -- see
knowledge/decisions/0005-switch-to-ringo.md for why: gnssmultipath had
repeated, confirmed bugs on real multi-GNSS CORS files at exactly the
scale GeoDE needs to run at (ADR-0004). RINGO processed every one of
those failing files plus a 35-file random sample from the real archive
with zero crashes, and handles raw .Z/.gz/Hatanaka archive files directly
-- no decompression step needed for the tool itself (pyRinex.ReadRinex is
still run by prepare.py, but only for provenance metadata; RINGO reads
the original archived file).

Two subprocess calls per file:
  - `ringo rnxcsv ... --qcmode` for per-epoch az/el/MP1/MP2/MP5/GF/MW and
    per-epoch slip flags, one row per (PRN, epoch).
  - `ringo rnxcsv ...` (no --qcmode) for the raw observations, needed only
    for SNR (RINGO's qcmode output doesn't carry SNR).
Both are parsed with pandas and pivoted into (nepochs, nsats) arrays per
GNSS system and per band -- the shape aggregate.aggregate_signal() needs.

RINGO reports multipath/GF/MW/slip data per *band* (1, 2, 5), not per
exact RINEX observation code -- which range+phase attribute it selected
for a given band isn't exposed in its output, so every RINGO-sourced
signal is recorded with an unknown attribute ('1?', '2?', '5?'), the same
convention signals.find_snr_code() already uses for a genuinely ambiguous
match.

The raw MP1/MP2/MP5 combination carries a slowly-varying residual
ionospheric trend on top of actual multipath (a known property of the
code-minus-phase combination, not a RINGO quirk) -- a plain RMS of the
raw values over a whole arc is dominated by that trend, not multipath,
and comes out 5-20x larger than it should (confirmed against RINGO's own
`qc` text summary on real files: raw per-epoch std ~2.5m for satellites
the text summary itself reports around 0.5m). RINGO detrends internally
for that summary but doesn't expose the result per epoch anywhere we can
read it (its running-mean qclog output still includes the slip jump
itself before resetting, which is worse, not better). _detrend_by_arc()
removes a degree-5 polynomial trend, fit over a rolling window within
each slip-bounded arc, instead -- the standard way to separate out the
residual ionospheric term (itself smoothly time-varying, not
whole-arc-constant) from genuine multipath. A degree-5 fit landed within
~1-10% of RINGO's own per-satellite numbers (vs 5-20x off for a flat
per-arc mean); fit over a *rolling window* rather than the whole arc at
once after a global per-arc fit turned out to be numerically unstable at
arc boundaries on real data -- see _detrend_by_arc()'s own docstring.
"""
import io
import subprocess
import time

import numpy as np
import pandas as pd
from scipy import signal

from geode.rinex_qc import signals

BAND_CODES = ('1', '2', '5')
DEFAULT_RINGO_BIN = 'ringo'
DEFAULT_TIMEOUT_S = 600

# Degree and rolling-window size for the per-arc polynomial detrend
# removed from raw MP before any statistic sees it (see module
# docstring and _detrend_by_arc()). DETREND_WINDOW matches RINGO's own
# default rolling-average window for MP-based slip detection
# (`s.mp.win`), chosen for consistency rather than independently tuned.
# An arc shorter than DETREND_DEGREE + 2 points can't be fit meaningfully
# (no degrees of freedom left for a residual) and is dropped entirely
# rather than left under-detrended.
DETREND_DEGREE   = 5
DETREND_WINDOW   = 51
MIN_ARC_EPOCHS   = DETREND_DEGREE + 2


class RunnerError(Exception):
    pass


class RunnerResult:
    def __init__(self, systems, runtime_s, nepochs, qc_csv=None, obs_csv=None):
        self.systems   = systems    # dict: sys_code -> {band_code: BandData}
        self.runtime_s = runtime_s
        self.nepochs   = nepochs    # total epochs in the file (shared across every system/band)
        self.qc_csv    = qc_csv     # raw CSV text, only kept if keep_tool_outputs
        self.obs_csv   = obs_csv


class BandData:
    """One GNSS system's one band (1/2/5): arrays shaped (nepochs, nsats)."""

    def __init__(self, prns, mp, az, el, slip, snr, snr_code):
        self.prns     = prns      # column order, e.g. ['G01', 'G02', ...]
        self.mp       = mp        # per-arc degree-5-polynomial-detrended, see module docstring
        self.az       = az
        self.el       = el
        self.slip     = slip      # bool array, True where RINGO flagged a slip
        self.snr      = snr       # or None if no SNR code matched this band
        self.snr_code = snr_code  # e.g. 'S1C', or None


def run(rinex_path, nav_paths=(), sp3_paths=(), elev_cutoff_deg=10.0,
       ringo_bin=DEFAULT_RINGO_BIN, timeout_s=DEFAULT_TIMEOUT_S, keep_tool_outputs=False):
    """
    Run RINGO on rinex_path.

    :param nav_paths: broadcast navigation file path(s).
    :param sp3_paths: SP3 file path(s). Exactly one of nav_paths/sp3_paths
        should be non-empty (orbits.py enforces this) -- RINGO accepts
        either as positional arguments after the obs file.
    :return: a RunnerResult. Raises RunnerError on anything that isn't a
        clean run (non-zero exit, or empty output -- RINGO exits 0 even on
        a missing/unreadable file, so exit code alone isn't enough) --
        the caller (geode.rinex_qc.pipeline) converts that into a failed
        run record, never aborting a batch.
    """
    common_args = [rinex_path, *nav_paths, *sp3_paths, '--elmaskf', str(elev_cutoff_deg),
                  '--no-space', '--narepr', 'NaN']

    t0 = time.time()
    qc_csv  = _run_rnxcsv(ringo_bin, common_args + ['--qcmode'], timeout_s)
    obs_csv = _run_rnxcsv(ringo_bin, common_args, timeout_s)
    runtime_s = time.time() - t0

    qc_df  = _read_ringo_csv(qc_csv)
    obs_df = _read_ringo_csv(obs_csv)
    systems_result = _build_systems(qc_df, obs_df)
    nepochs = qc_df['time'].nunique()

    return RunnerResult(systems_result, runtime_s, nepochs,
                        qc_csv=qc_csv if keep_tool_outputs else None,
                        obs_csv=obs_csv if keep_tool_outputs else None)


def _run_rnxcsv(ringo_bin, args, timeout_s):
    cmd = [ringo_bin, 'rnxcsv', *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        raise RunnerError("ringo binary not found (%r) -- see scripts/install_ringo.sh" % ringo_bin)
    except subprocess.TimeoutExpired:
        raise RunnerError('ringo rnxcsv timed out after %ss: %s' % (timeout_s, ' '.join(cmd)))

    # RINGO exits 0 even for a missing/empty/unreadable file (it just logs
    # "no obs file found" to stderr) -- empty stdout is the real failure
    # signal, exit code is not sufficient on its own.
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RunnerError('ringo rnxcsv failed (rc=%s): %s'
                          % (proc.returncode, proc.stderr.strip() or '(no output)'))
    return proc.stdout


def _read_ringo_csv(csv_text):
    # RINGO repeats its '> '-prefixed header line once per satellite
    # block in stdout (not just once for the whole stream) when writing
    # to stdout rather than --outdir -- keep the column names from the
    # first one, drop every other line starting with '>'.
    lines = csv_text.splitlines()
    header = lines[0].lstrip('>').strip()
    body = (line for line in lines[1:] if not line.startswith('>'))
    # low_memory=False: the slip-flag columns (blank or '*') otherwise get
    # chunk-sniffed as numeric from early all-blank rows, then warn when a
    # '*' shows up later -- the whole column is read as object dtype either
    # way (we only ever compare it to '*'), this just silences the warning.
    return pd.read_csv(io.StringIO(header + '\n' + '\n'.join(body)),
                       skipinitialspace=True, low_memory=False)


def _build_systems(qc_df, obs_df):
    qc_df  = qc_df.assign(sys=qc_df['PRN'].str[0])
    obs_df = obs_df.assign(sys=obs_df['PRN'].str[0])

    snr_columns = [c for c in obs_df.columns if c.startswith('S') and c != 'sys']

    systems = {}
    for sys_code in sorted(qc_df['sys'].unique()):
        sub = qc_df[qc_df['sys'] == sys_code]
        prns = sorted(sub['PRN'].unique())
        obs_sub = obs_df[obs_df['sys'] == sys_code]
        available_snr = [c for c in snr_columns if (obs_sub[c].notna().any() if c in obs_sub else False)]

        # `sub` and `obs_sub` come from two separate `ringo rnxcsv`
        # invocations (--qcmode and plain) -- confirmed on a real file
        # that they don't always agree on the exact set of epochs
        # written out for the same system, which would otherwise
        # silently misalign az/el/mp against snr by a row or two.
        # Pivoting every array against one shared, explicit time index
        # (the union of both calls' times) makes every BandData array
        # the same shape by construction, not by assumption.
        time_index = pd.Index(sorted(set(sub['time']) | set(obs_sub['time'])), name='time')

        az = _pivot(sub, 'az', prns, time_index)
        el = _pivot(sub, 'el', prns, time_index)

        bands = {}
        for band in BAND_CODES:
            mp_col = 'MP' + band
            if mp_col not in sub.columns:
                continue
            raw_mp = _pivot(sub, mp_col, prns, time_index)
            if not np.isfinite(raw_mp).any():
                continue  # this system never tracked this band at all

            slip = _pivot(sub, 's' + band, prns, time_index, is_flag=True)
            mp = _detrend_by_arc(raw_mp, slip)

            snr_code = signals.find_snr_code(available_snr, band)
            snr = _pivot(obs_sub, snr_code, prns, time_index) if snr_code else None

            bands[band] = BandData(prns=prns, mp=mp, az=az, el=el, slip=slip,
                                   snr=snr, snr_code=snr_code)

        if bands:
            systems[sys_code] = bands

    return systems


def _pivot(df, value_col, prns, time_index, is_flag=False):
    # dropna=False: pivot_table's default drops an index (time) row, or a
    # columns (PRN) value, whenever every entry for it is NaN -- true for
    # plenty of real epochs/satellites on the slip-flag columns in
    # particular (most epochs have no slip on any satellite at all),
    # which would otherwise silently misalign this array's shape against
    # the az/el/mp arrays built from the same `df`. reindex(index=...) to
    # the caller's shared time_index on top of that -- see _build_systems().
    table = df.pivot_table(index='time', columns='PRN', values=value_col,
                           aggfunc='first', dropna=False) \
             .reindex(index=time_index, columns=prns)
    if is_flag:
        return (table == '*').to_numpy()
    return table.to_numpy(dtype=np.float64)


def _detrend_by_arc(raw_mp, slip_mask):
    """
    Remove a degree-DETREND_DEGREE polynomial trend per *continuously
    observed* arc, column by column (one GNSS satellite per column), fit
    over a rolling window rather than the whole arc at once. A
    slip-flagged epoch starts a new arc (belongs to the arc after the
    jump, not before) -- but a slip flag is not the only thing that ends
    one: "no slip was flagged" also just means the satellite was never
    observed long enough in a row to compare against, which is equally
    true across a brief dropout *and* across the satellite fully setting
    and rising again hours later. Confirmed on a real file: one
    slip-bounded "arc" silently spanned a 2643-epoch (~22 hour) gap where
    the satellite was below the elevation mask the whole time -- two
    unrelated passes, read by this code as one arc because nothing
    tripped the slip detector across a gap with no data to compare. A
    single fit (global or windowed) bridging that gap produced giant,
    physically meaningless residuals right at the boundary -- enough to
    overflow rinex_qc_signal_bins.mp_rms_mm's smallint column in
    production. So an arc is additionally split wherever consecutive
    *observed* epochs aren't adjacent (any gap at all, not just a large
    one -- even a handful of missing epochs produced a smaller but real
    version of the same artifact on a different satellite).

    Within one such continuously-observed run, the polynomial is fit over
    a rolling window rather than across the whole run at once, since a
    single high-degree polynomial asked to span a long domain is prone to
    oscillating wildly near its own ends (Runge's-phenomenon-style edge
    instability) even without any gap involved. scipy.signal.savgol_filter
    is a vectorized rolling polynomial fit evaluated at each window's
    centre -- not a coincidentally-similar tool, the exact one for this.
    """
    detrended = np.full_like(raw_mp, np.nan)
    nepochs, nsats = raw_mp.shape
    for j in range(nsats):
        col = raw_mp[:, j]
        valid = np.isfinite(col)
        if not valid.any():
            continue
        arc_id = np.cumsum(slip_mask[:, j])
        for arc in np.unique(arc_id[valid]):
            idx = np.flatnonzero(valid & (arc_id == arc))
            # further split on any gap between consecutive observed
            # epochs -- see docstring; np.split on the break points keeps
            # each piece's own original (non-contiguous-overall) indices.
            breaks = np.flatnonzero(np.diff(idx) > 1) + 1
            for run in np.split(idx, breaks):
                if len(run) < MIN_ARC_EPOCHS:
                    continue  # too short to fit DETREND_DEGREE reliably, drop it
                # shrink the window for a run shorter than the default,
                # but never below what DETREND_DEGREE itself needs;
                # savgol_filter requires an odd window_length > polyorder.
                window = min(DETREND_WINDOW, len(run))
                if window % 2 == 0:
                    window -= 1
                trend = signal.savgol_filter(col[run], window, DETREND_DEGREE)
                detrended[run, j] = col[run] - trend
    return detrended
