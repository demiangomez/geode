"""
RinexQC visualizations: per-constellation daily-summary time series, and
elevation/azimuth-vs-time heatmaps built from rinex_qc_signal_bins. Callable
from the command line (com/PlotRinexQC.py) or from a backend process -- every
plot_*() function follows the same output contract as geode.etm's plotter
(QcPlotOutput.filename -> file on disk, .file_io -> base64-encoded string,
neither -> interactive plt.show()), see _handle_output().

Dates are geode.pyDate.Date throughout the public surface (function
parameters/returns), matching the convention used everywhere else in GeoDE
(geode.metadata.station_info.StationInfoRecord wraps this same table's
DateStart/DateEnd the same way; geode.rinex_qc.orbits takes a Date for its
observation-day parameter) -- not stdlib datetime.date. Plain datetime/date
values appear only at the two boundaries that need them and don't know about
Date: a psycopg2 query parameter (_sql_date()) and matplotlib's plotting
calls, which plot directly against Date.fyear on a plain numeric axis (no
date-locator machinery), exactly like geode.etm's time series plots do.

Query layer returns plain dicts/arrays; matplotlib code never touches the
database directly. Grid geometry (rings, az sectors) always comes from
geode.rinex_qc.aggregate.make_equal_area_grid() -- rinex_qc_grids seeds
exactly one grid (id=1, see knowledge/architecture/rinex-qc.md), so there is
only ever one geometry to reconcile bin arrays against.
"""
import base64
import dataclasses
import datetime
import os
import warnings
from io import BytesIO
from typing import Optional

warnings.filterwarnings('ignore', message='Starting a Matplotlib GUI outside')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from geode.pyDate import Date
from .aggregate import make_equal_area_grid

GNSS_NAMES = {'G': 'GPS', 'R': 'GLONASS', 'E': 'Galileo', 'C': 'BeiDou'}


def _band_label(band_attr):
    """Display label for a band_attr, e.g. '1?' -> 'L1'. RINGO-sourced rows
    always end in '?' (exact tracking attribute unknown, see
    rinex_qc_signal.band_attr's schema comment) -- that's a real provenance
    marker worth keeping in the stored data, just not worth showing on an
    axis/legend label where 'L1'/'L2'/'L5' reads cleaner."""
    if band_attr.endswith('?'):
        return f'L{band_attr[:-1]}'
    return band_attr

# Validated categorical palette (dataviz skill, references/palette.md) --
# slots 1-3 (blue/orange/aqua) are the only three that clear the all-pairs
# CVD/contrast floors together, which is exactly the max band count any one
# constellation has (G: 1/2/5, R: 1/2, E: 1/5). Slots 7/8 (violet/red), used
# for event lines below, sit far from 1-3 in the fixed ordering on purpose.
BAND_COLORS = ('#2a78d6', '#eb6834', '#1baf7a')  # blue, orange, aqua

COLOR_EQUIPMENT = '#4a3aa7'  # violet -- one color for all equipment changes
COLOR_VISIT = '#e34948'      # red -- site visits

STYLE_EQUIPMENT = {
    'antenna':  {'linestyle': ':',  'linewidth': 1.4, 'alpha': 0.9},
    'receiver': {'linestyle': '--', 'linewidth': 1.4, 'alpha': 0.9},
    'firmware': {'linestyle': '-.', 'linewidth': 1.2, 'alpha': 0.8},
}
STYLE_VISIT = {'linestyle': '-', 'linewidth': 1.0, 'alpha': 0.55}

MP_BOUNDS_CM = (0, 100)  # real-data MP RMS is ~cm-scale (median ~48cm); mm gave needlessly long ticks
SNR_BOUNDS_DBHZ = (25, 52)
N_TARGET_AZ = 72  # 5-degree common azimuth bins, matches the grid's finest ring


@dataclasses.dataclass
class QcPlotOutput:
    """Output routing, same contract as geode.etm.visualization.data_classes.PlotOutputConfig."""
    filename: Optional[str] = None
    file_io: Optional[BytesIO] = None
    format: str = 'png'
    save_kwargs: Optional[dict] = None


def _to_pydate(d):
    """DB-returned datetime/date -> pyDate.Date, GeoDE's shared date
    representation (see geode.metadata.station_info.StationInfoRecord, which
    wraps these same stationinfo.DateStart/DateEnd columns the same way). A
    bare date with no time-of-day in the source column (a computed
    ObservationYear/DOY day, api_visits.date) gets noon -- Date's own default
    when no hour is given, and what Date's own +/- day arithmetic always
    produces too, so every "day" value constructed anywhere in this module
    compares/hashes consistently."""
    if isinstance(d, datetime.datetime):
        return Date(datetime=d)
    return Date(year=d.year, month=d.month, day=d.day)


def _sql_date(d: Date):
    """Date -> plain datetime.date, for the one boundary that needs it:
    a psycopg2 query parameter (psycopg2 has no adapter for Date)."""
    return datetime.date(d.year, d.month, d.day)


def _full_calendar_days(date_start: Date, date_end: Date):
    """Every calendar day in [date_start, date_end], inclusive, as Date
    objects -- the shared reindexing axis every daily series/grid is built
    against, so a day with no row (no rinex_qc_signal/_bins row that day)
    becomes an explicit gap at its correct calendar position instead of being
    silently absent from the array (which would let a line plot straight-line
    across the gap, or a heatmap's pcolormesh stretch the last real day's
    color across it -- pcolormesh has no notion of "this column is wider
    because days are missing," it just draws whatever edges it's given)."""
    n_days = date_end - date_start  # Date - Date -> integer day count (mjd difference)
    return [date_start + i for i in range(n_days + 1)]


# ---------------------------------------------------------------------
# Station metadata / events
# ---------------------------------------------------------------------

def get_station_meta(cnn, network, station):
    rows = cnn.query("""
        SELECT "StationName", lat, lon, height, dome, marker
        FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s
    """, (network, station)).dictresult()
    if not rows:
        raise ValueError(f'No station record for {network}.{station}')
    return rows[0]


def get_equipment_changes(cnn, network, station):
    """
    One entry per stationinfo transition where receiver, antenna/radome, or
    receiver firmware actually changed. ETM's own jump_manager.py only flags
    antenna/radome (geometric jumps); RinexQC also cares about receiver and
    firmware changes since those can shift SNR/tracking behavior without
    moving the antenna at all.
    """
    records = cnn.query("""
        SELECT "ReceiverCode", "ReceiverFirmware", "AntennaCode", "RadomeCode", "DateStart"
        FROM stationinfo
        WHERE "NetworkCode" = %s AND "StationCode" = %s
        ORDER BY "DateStart"
    """, (network, station)).dictresult()

    changes = []
    for prev, cur in zip(records[:-1], records[1:]):
        d = _to_pydate(cur['DateStart'])
        if prev['ReceiverCode'] != cur['ReceiverCode']:
            changes.append({'date': d, 'kind': 'receiver'})
        elif prev['ReceiverFirmware'] != cur['ReceiverFirmware']:
            changes.append({'date': d, 'kind': 'firmware'})
        if prev['AntennaCode'] != cur['AntennaCode'] or prev['RadomeCode'] != cur['RadomeCode']:
            changes.append({'date': d, 'kind': 'antenna'})
    return changes


def get_visits(cnn, network, station):
    """Actual (non-planned) site visits, via stations.api_id = api_visits.station_id."""
    rows = cnn.query("""
        SELECT v.date
        FROM api_visits v JOIN stations s ON s.api_id = v.station_id
        WHERE s."NetworkCode" = %s AND s."StationCode" = %s AND NOT v.planned
        ORDER BY v.date
    """, (network, station)).dictresult()
    return [_to_pydate(row['date']) for row in rows]


# ---------------------------------------------------------------------
# Daily scalar series (completeness, MP RMS, SNR mean, slip rate)
# ---------------------------------------------------------------------

def get_date_span(cnn, network, station):
    """Overall [min, max] ObservationDate across every rinex_qc_run for this
    station (any GNSS) -- the default plotting window, shared across all
    per-constellation plots so they stay directly comparable."""
    row = cnn.query("""
        SELECT min(make_date("ObservationYear", 1, 1) + ("ObservationDOY" - 1)) AS d0,
               max(make_date("ObservationYear", 1, 1) + ("ObservationDOY" - 1)) AS d1
        FROM rinex_qc_run WHERE "NetworkCode" = %s AND "StationCode" = %s
    """, (network, station)).dictresult()[0]
    if row['d0'] is None:
        raise ValueError(f'No rinex_qc_run rows for {network}.{station}')
    return _to_pydate(row['d0']), _to_pydate(row['d1'])


def get_daily_completeness(cnn, network, station, date_start: Date, date_end: Date):
    """
    Per-day completeness %, continuous over [date_start, date_end] with no
    silently-bridged gaps:
      - no rinex file at all that day   -> 0.0 (true outage)
      - rinex file exists, no QC run yet -> NaN (pending, left as a gap)
      - QC run exists                    -> 100 * epochs_observed / epochs_expected
    """
    rows = cnn.query("""
        WITH days AS (
            SELECT d::date AS day
            FROM generate_series(%s::date, %s::date, interval '1 day') d
        ),
        rinex_days AS (
            SELECT DISTINCT make_date("ObservationYear"::int, 1, 1)
                   + ("ObservationDOY"::int - 1) AS day
            FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s
        ),
        qc_days AS (
            SELECT DISTINCT ON (day) day, epochs_observed, epochs_expected
            FROM (
                SELECT make_date("ObservationYear", 1, 1) + ("ObservationDOY" - 1) AS day,
                       epochs_observed, epochs_expected, run_at
                FROM rinex_qc_run WHERE "NetworkCode" = %s AND "StationCode" = %s
            ) r
            ORDER BY day, run_at DESC
        )
        SELECT days.day, (rinex_days.day IS NOT NULL) AS has_rinex,
               qc_days.epochs_observed, qc_days.epochs_expected
        FROM days
        LEFT JOIN rinex_days ON rinex_days.day = days.day
        LEFT JOIN qc_days ON qc_days.day = days.day
        ORDER BY days.day
    """, (_sql_date(date_start), _sql_date(date_end), network, station, network, station)).dictresult()

    days = [_to_pydate(row['day']) for row in rows]
    completeness = np.full(len(rows), np.nan)
    for i, row in enumerate(rows):
        if row['epochs_expected']:
            completeness[i] = 100.0 * row['epochs_observed'] / row['epochs_expected']
        elif not row['has_rinex']:
            completeness[i] = 0.0
        # else: rinex exists but no QC run yet -- leave as NaN (pending)
    return days, completeness


def get_daily_signal_series(cnn, network, station, gnss, date_start: Date, date_end: Date):
    """
    Per-band daily series (mp_rms_cm, snr_mean, slip_rate), reindexed onto the
    full calendar day range with NaN for days with no signal row -- so a
    multi-day/week/year gap shows as a break in the line, never a straight
    interpolation across missing data.

    :return: {band_attr: {'days': [Date,...], 'mp': ndarray, 'snr': ndarray, 'slip_rate': ndarray}}
    """
    rows = cnn.query("""
        SELECT make_date(q."ObservationYear", 1, 1) + (q."ObservationDOY" - 1) AS day,
               s.band_attr, s.mp_rms_m, s.snr_mean, s.n_slips, s.n_obs
        FROM rinex_qc_run q JOIN rinex_qc_signal s ON s.run_id = q.id
        WHERE q."NetworkCode" = %s AND q."StationCode" = %s AND s.gnss = %s
          AND make_date(q."ObservationYear", 1, 1) + (q."ObservationDOY" - 1)
              BETWEEN %s AND %s
        ORDER BY day
    """, (network, station, gnss, _sql_date(date_start), _sql_date(date_end))).dictresult()

    all_days = _full_calendar_days(date_start, date_end)
    n_days = len(all_days)
    # keyed by .mjd, not the Date object itself -- Date's __hash__ is fyear-based
    # while __eq__ is mjd-based, so two Date objects for the same calendar day
    # but constructed with a different hour (e.g. a bare date defaulting to noon
    # vs a datetime carrying its own real time-of-day) would compare equal but
    # hash differently, silently breaking dict lookups. .mjd sidesteps that.
    day_index = {d.mjd: i for i, d in enumerate(all_days)}

    by_band = {}
    for row in rows:
        band = row['band_attr']
        series = by_band.setdefault(band, {
            'days': all_days,
            'mp': np.full(n_days, np.nan),
            'snr': np.full(n_days, np.nan),
            'slip_rate': np.full(n_days, np.nan),
        })
        i = day_index[_to_pydate(row['day']).mjd]
        if row['mp_rms_m'] is not None:
            series['mp'][i] = row['mp_rms_m'] * 100.0
        if row['snr_mean'] is not None:
            series['snr'][i] = row['snr_mean']
        if row['n_obs']:
            series['slip_rate'][i] = 1000.0 * row['n_slips'] / row['n_obs']
    return by_band


def get_gnss_present(cnn, network, station):
    """Distinct GNSS systems this station has any rinex_qc_signal rows for, in a fixed display order."""
    rows = cnn.query("""
        SELECT DISTINCT s.gnss
        FROM rinex_qc_run q JOIN rinex_qc_signal s ON s.run_id = q.id
        WHERE q."NetworkCode" = %s AND q."StationCode" = %s
    """, (network, station)).dictresult()
    present = {row['gnss'] for row in rows}
    return [g for g in ('G', 'R', 'E', 'C') if g in present]


def get_bands_present(cnn, network, station, gnss):
    rows = cnn.query("""
        SELECT DISTINCT s.band_attr
        FROM rinex_qc_run q JOIN rinex_qc_signal s ON s.run_id = q.id
        WHERE q."NetworkCode" = %s AND q."StationCode" = %s AND s.gnss = %s
        ORDER BY 1
    """, (network, station, gnss)).dictresult()
    return [row['band_attr'] for row in rows]


# ---------------------------------------------------------------------
# Az-el bin collapse (elevation-vs-time, azimuth-vs-time)
# ---------------------------------------------------------------------

def get_daily_bins(cnn, network, station, gnss, band_attr, date_start: Date, date_end: Date):
    """Raw per-day bin arrays (grid_id=1, the only seeded grid) for one band."""
    # cast_decimals=False: n_obs/mp_rms_mm/snr_ddbhz are integer[]/smallint[],
    # never NUMERIC/DECIMAL, so Cnn.query()'s default elementwise Decimal->float
    # pass is pure wasted work here -- profiled at ~5s of a ~7s full-history
    # fetch for one band (827-element arrays x 3 x ~5000 rows), the dominant
    # cost of generating a heatmap, well above the query itself.
    rows = cnn.query("""
        SELECT make_date(q."ObservationYear", 1, 1) + (q."ObservationDOY" - 1) AS day,
               b.n_obs, b.mp_rms_mm, b.snr_ddbhz
        FROM rinex_qc_run q JOIN rinex_qc_signal_bins b ON b.run_id = q.id
        WHERE q."NetworkCode" = %s AND q."StationCode" = %s
          AND b.gnss = %s AND b.band_attr = %s AND b.grid_id = 1
          AND make_date(q."ObservationYear", 1, 1) + (q."ObservationDOY" - 1)
              BETWEEN %s AND %s
        ORDER BY day
    """, (network, station, gnss, band_attr, _sql_date(date_start), _sql_date(date_end)),
    cast_decimals=False).dictresult()
    for row in rows:
        row['day'] = _to_pydate(row['day'])
    return rows


def get_daily_bin_grids(cnn, network, station, gnss, band_attr, date_start: Date, date_end: Date,
                        grid, cache=None):
    """
    get_daily_bins(), reshaped into 2D (n_days, n_cells) arrays -- one row per
    calendar day in [date_start, date_end] (NaN-filled for a day with no
    rinex_qc_signal_bins row, same gap semantics as _full_calendar_days),
    ready for collapse_to_elev_rings()/collapse_to_azimuth()'s vectorized-
    across-days form. This is the one DB round trip per (gnss, band): when
    plot_station() generates both the elevation and azimuth heatmap for the
    same band (the default), it passes a shared `cache` dict so the second
    plot reuses this call's result instead of re-querying identical rows.

    :return: (days: [Date,...], (n_obs, mp_rms_mm, snr_ddbhz)) -- each array
        shaped (len(days), n_cells); or (None, None) if the band has no bins
        at all in range.
    """
    key = (gnss, band_attr, date_start.mjd, date_end.mjd)
    if cache is not None and key in cache:
        return cache[key]

    rows = get_daily_bins(cnn, network, station, gnss, band_attr, date_start, date_end)
    if not rows:
        result = (None, None)
        if cache is not None:
            cache[key] = result
        return result

    days = _full_calendar_days(date_start, date_end)
    n_days = len(days)
    n_cells = int(sum(grid['az_bins']))

    # Scatter rows into their calendar position by one vectorized fancy-index
    # assignment per array, not a Python loop over (up to several thousand)
    # days -- building the three (n_rows, n_cells) arrays via np.array(...)
    # is itself one bulk C-level conversion (None -> NaN included) rather than
    # a per-row numpy __setitem__ call each with its own overhead.
    row_index = np.fromiter((r['day'].mjd - date_start.mjd for r in rows), dtype=np.int64,
                            count=len(rows))
    n_obs = np.full((n_days, n_cells), np.nan)
    mp_rms_mm = np.full((n_days, n_cells), np.nan)
    snr_ddbhz = np.full((n_days, n_cells), np.nan)
    n_obs[row_index] = np.array([r['n_obs'] for r in rows], dtype=np.float64)
    mp_rms_mm[row_index] = np.array([r['mp_rms_mm'] for r in rows], dtype=np.float64)
    snr_ddbhz[row_index] = np.array([r['snr_ddbhz'] for r in rows], dtype=np.float64)

    result = (days, (n_obs, mp_rms_mm, snr_ddbhz))
    if cache is not None:
        cache[key] = result
    return result


def _overlap_weight_matrix(n_source, n_target):
    """W[i, j] = fraction of source sector i's azimuth width that overlaps target bin j."""
    src_edges = np.linspace(0, 360, n_source + 1)
    tgt_edges = np.linspace(0, 360, n_target + 1)
    W = np.zeros((n_source, n_target))
    for i in range(n_source):
        s0, s1 = src_edges[i], src_edges[i + 1]
        for j in range(n_target):
            t0, t1 = tgt_edges[j], tgt_edges[j + 1]
            overlap = max(0.0, min(s1, t1) - max(s0, t0))
            if overlap > 0:
                W[i, j] = overlap / (s1 - s0)
    return W


def _az_weight_matrices(grid, n_target=N_TARGET_AZ):
    """One azimuth-redistribution matrix per ring -- rings have different
    sector counts (coarser at high elevation), so collapsing to a single
    common azimuth axis needs each ring's cells redistributed into it by
    az-range overlap, same idea as the ring-boundary math in
    rinex_qc_bins_long. Grid is fixed/deterministic, so this is cheap to
    recompute per call; no caching needed for a module used a handful of
    times per process invocation.
    """
    return [_overlap_weight_matrix(n, n_target) for n in grid['az_bins']]


def _ring_included(grid, az_range):
    """Per-ring list of sector indices (within that ring) to keep for a
    given azimuth filter -- None means no filter (keep all)."""
    if az_range is None:
        return None
    lo, hi = az_range
    if lo >= hi:
        raise ValueError('az_range must be (lo, hi) with lo < hi (no wraparound support yet)')
    keep = []
    for n_sectors in grid['az_bins']:
        width = 360.0 / n_sectors
        keep.append([s for s in range(n_sectors)
                     if (s * width) < hi and ((s + 1) * width) > lo])
    return keep


def collapse_to_elev_rings(n_obs, mp_rms_mm, snr_ddbhz, grid, az_range=None):
    """Per-ring n-weighted MP RMS (cm) / SNR mean (dB-Hz), vectorized across
    days -- n_obs/mp_rms_mm/snr_ddbhz are 2D (n_days, n_cells) arrays (see
    get_daily_bin_grids()), one row per day, same flat ring-major cell layout
    aggregate.bin_indices() produces. Optionally restricted to azimuth
    sectors overlapping az_range=(lo, hi) degrees (whole-sector inclusion,
    not fractional). mp_rms_mm/snr_ddbhz are stored in mm / 0.1 dB-Hz units
    (rinex_qc_signal_bins.mp_rms_mm/snr_ddbhz) -- converted to cm / dB-Hz
    here so every caller gets physical display units back (real MP RMS is
    cm-scale, mm needlessly widened every tick label/colorbar).

    Looping over rings (18, fixed by the grid) rather than days (up to
    several thousand, the actual caller's day count) is what makes this
    fast: each iteration is one vectorized numpy reduction across every day
    at once, not up to 5000+ repetitions of the same tiny loop body.

    :return: (mp_grid, snr_grid), each shaped (n_rings, n_days) -- ready for
        pcolormesh(day_edges, elev_edges, mp_grid).
    """
    az_bins = grid['az_bins']
    n_rings = len(az_bins)
    n_days = n_obs.shape[0]
    ring_offsets = np.concatenate([[0], np.cumsum(az_bins)])
    keep = _ring_included(grid, az_range)

    n_all = np.nan_to_num(np.asarray(n_obs, dtype=np.float64))
    mp_all = np.nan_to_num(np.asarray(mp_rms_mm, dtype=np.float64))
    snr_all = np.nan_to_num(np.asarray(snr_ddbhz, dtype=np.float64))

    mp_grid = np.full((n_rings, n_days), np.nan)
    snr_grid = np.full((n_rings, n_days), np.nan)
    for ring in range(n_rings):
        lo_i, hi_i = ring_offsets[ring], ring_offsets[ring + 1]
        n = n_all[:, lo_i:hi_i]    # (n_days, ring_width)
        mp = mp_all[:, lo_i:hi_i]
        snr = snr_all[:, lo_i:hi_i]
        if keep is not None:
            mask = np.zeros(n.shape[1], dtype=bool)
            mask[keep[ring]] = True
            n = n * mask           # broadcasts the sector mask over every day
        wsum = n.sum(axis=1)       # (n_days,)
        nz = wsum > 0
        mp_grid[ring, nz] = np.sqrt(np.sum((mp[nz] ** 2) * n[nz], axis=1) / wsum[nz]) / 10.0
        snr_grid[ring, nz] = np.sum(snr[nz] * n[nz], axis=1) / wsum[nz] / 10.0
    return mp_grid, snr_grid


def collapse_to_azimuth(n_obs, mp_rms_mm, snr_ddbhz, grid, w_per_ring, elev_range=None):
    """N-weighted MP RMS (cm) / SNR mean (dB-Hz) redistributed onto a common
    N_TARGET_AZ-bin azimuth axis across all rings, vectorized across days --
    n_obs/mp_rms_mm/snr_ddbhz are 2D (n_days, n_cells) arrays (see
    get_daily_bin_grids()). Optionally restricted to rings overlapping
    elev_range=(lo, hi) degrees (whole-ring inclusion, not fractional).
    mp_rms_mm/snr_ddbhz are stored in mm / 0.1 dB-Hz units -- converted to
    cm / dB-Hz here, same reasoning as collapse_to_elev_rings(). See that
    function for why looping over rings (18) rather than days is what makes
    this fast.

    :return: (az_mp, az_snr), each shaped (N_TARGET_AZ, n_days) -- ready for
        pcolormesh(day_edges, az_edges, az_mp).
    """
    az_bins = grid['az_bins']
    elev_edges = grid['elev_edges']
    n_rings = len(az_bins)
    n_days = n_obs.shape[0]
    ring_offsets = np.concatenate([[0], np.cumsum(az_bins)])
    n_target = w_per_ring[0].shape[1]

    n_all = np.nan_to_num(np.asarray(n_obs, dtype=np.float64))
    mp_all = np.nan_to_num(np.asarray(mp_rms_mm, dtype=np.float64))
    snr_all = np.nan_to_num(np.asarray(snr_ddbhz, dtype=np.float64))

    target_n = np.zeros((n_days, n_target))
    target_sumsq_mp = np.zeros((n_days, n_target))
    target_sum_snr = np.zeros((n_days, n_target))
    for ring in range(n_rings):
        if elev_range is not None:
            lo, hi = elev_range
            if elev_edges[ring + 1] <= lo or elev_edges[ring] >= hi:
                continue
        lo_i, hi_i = ring_offsets[ring], ring_offsets[ring + 1]
        n = n_all[:, lo_i:hi_i]    # (n_days, ring_width)
        mp = mp_all[:, lo_i:hi_i]
        snr = snr_all[:, lo_i:hi_i]
        W = w_per_ring[ring]       # (ring_width, n_target)
        target_n += n @ W
        target_sumsq_mp += (n * mp ** 2) @ W
        target_sum_snr += (n * snr) @ W

    az_mp = np.full((n_days, n_target), np.nan)
    az_snr = np.full((n_days, n_target), np.nan)
    nz = target_n > 0
    az_mp[nz] = np.sqrt(target_sumsq_mp[nz] / target_n[nz]) / 10.0
    az_snr[nz] = target_sum_snr[nz] / target_n[nz] / 10.0
    return az_mp.T, az_snr.T


# ---------------------------------------------------------------------
# Shared plot scaffolding: title, event lines, output routing
# ---------------------------------------------------------------------

def _build_title(meta, network, station, gnss, subtitle_extra, date_start, date_end,
                 completeness_pct, filter_str=None):
    """Returns the suptitle as one '\n'-joined string: station/system line, then
    lat/lon/date-range/completeness, then (only if a spatial filter is active on
    this plot) a third line naming it."""
    gnss_name = GNSS_NAMES.get(gnss, gnss)
    line1 = f'{network.upper()}.{station.upper()}  --  {gnss_name} {subtitle_extra}'

    completeness_str = f'{completeness_pct:.1f}% complete' if completeness_pct is not None else 'completeness n/a'
    line2 = (f"{meta['StationName'] or ''}  |  lat: {meta['lat']:.5f}  lon: {meta['lon']:.5f}  "
             f"h: {meta['height']:.1f}m  |  "
             f'{date_start.iso_date()} to {date_end.iso_date()} ({completeness_str})')
    lines = [line1, line2]
    if filter_str:
        lines.append(filter_str)
    return '\n'.join(lines)


def _add_event_lines(ax, equipment_changes, visits):
    """Plots directly against Date.fyear, same as geode.etm's time series
    plots -- a plain numeric axis, no date-locator machinery (see this
    module's docstring)."""
    for ch in equipment_changes:
        ax.axvline(ch['date'].fyear, color=COLOR_EQUIPMENT, **STYLE_EQUIPMENT[ch['kind']])
    for v in visits:
        ax.axvline(v.fyear, color=COLOR_VISIT, **STYLE_VISIT)


def _event_legend_handles():
    from matplotlib.lines import Line2D
    return [
        Line2D([0], [0], color=COLOR_EQUIPMENT, **STYLE_EQUIPMENT['antenna'], label='antenna/radome change'),
        Line2D([0], [0], color=COLOR_EQUIPMENT, **STYLE_EQUIPMENT['receiver'], label='receiver change'),
        Line2D([0], [0], color=COLOR_EQUIPMENT, **STYLE_EQUIPMENT['firmware'], label='firmware change'),
        Line2D([0], [0], color=COLOR_VISIT, **STYLE_VISIT, label='site visit'),
    ]


def _handle_output(fig, output: Optional[QcPlotOutput]):
    """Same output contract as geode.etm's EtmPlotter._handle_plot_output()."""
    if output is None:
        plt.show()
        plt.close(fig)
        return None

    kwargs = output.save_kwargs or {}
    if output.filename:
        fig.savefig(output.filename, **kwargs)
        plt.close(fig)
        return None
    elif output.file_io is not None:
        fig.savefig(output.file_io, format=output.format, **kwargs)
        output.file_io.seek(0)
        plt.close(fig)
        return base64.b64encode(output.file_io.getvalue()).decode()
    else:
        plt.show()
        plt.close(fig)
        return None


# ---------------------------------------------------------------------
# Plot 1: daily summary (one constellation per figure)
# ---------------------------------------------------------------------

def plot_daily_summary(cnn, network, station, gnss, date_start: Optional[Date] = None,
                       date_end: Optional[Date] = None, output: Optional[QcPlotOutput] = None):
    meta = get_station_meta(cnn, network, station)
    if date_start is None or date_end is None:
        date_start, date_end = get_date_span(cnn, network, station)

    days, completeness = get_daily_completeness(cnn, network, station, date_start, date_end)
    # plain numeric axis of Date.fyear, same as geode.etm's time series plots
    # (see this module's docstring) -- days/s['days'] are always the exact
    # same _full_calendar_days(date_start, date_end) sequence, so one fyear
    # array serves every panel below.
    fyears = np.array([d.fyear for d in days])
    series = get_daily_signal_series(cnn, network, station, gnss, date_start, date_end)
    bands = get_bands_present(cnn, network, station, gnss)

    changes = get_equipment_changes(cnn, network, station)
    visits = get_visits(cnn, network, station)

    overall_completeness = float(np.nanmean(completeness)) if len(completeness) else None
    title = _build_title(meta, network, station, gnss, 'RinexQC daily summary',
                         date_start, date_end, overall_completeness)

    fig, axes = plt.subplots(4, 1, figsize=(11, 9), sharex=True,
                             gridspec_kw={'height_ratios': [0.6, 1, 1, 0.8]})
    fig.suptitle(title, fontsize=10, family='monospace', y=0.99)

    ax = axes[0]
    ax.plot(fyears, completeness, color='0.3', lw=0.6)
    ax.axhline(95, color='red', ls=':', lw=0.8)
    ax.set_ylim(0, 102)
    ax.set_ylabel('completeness [%]', fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    for i, band in enumerate(bands):
        s = series.get(band)
        if s:
            ax.plot(fyears, s['mp'], color=BAND_COLORS[i % 3], lw=0.6, label=_band_label(band))
    ax.legend(fontsize=8, loc='upper left', ncol=len(bands))
    ax.set_ylabel('MP RMS [cm]', fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[2]
    for i, band in enumerate(bands):
        s = series.get(band)
        if s:
            ax.plot(fyears, s['snr'], color=BAND_COLORS[i % 3], lw=0.6, label=_band_label(band))
    ax.set_ylabel('SNR mean [dB-Hz]', fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[3]
    for i, band in enumerate(bands):
        s = series.get(band)
        if s:
            ax.plot(fyears, s['slip_rate'], color=BAND_COLORS[i % 3], lw=0.6, label=_band_label(band))
    ax.set_ylabel('slips [per 1k obs]', fontsize=8)
    ax.grid(alpha=0.3)

    xlim = (date_start.fyear, date_end.fyear)
    for ax in axes:
        _add_event_lines(ax, changes, visits)
        # explicit, not autoscaled: an equipment change/visit outside
        # [date_start, date_end] still draws its axvline (useful context right
        # at the plot's edge) but must not silently stretch the axis to include
        # it -- axvline's x value participates in autoscale like any artist.
        ax.set_xlim(xlim)

    fig.legend(handles=_event_legend_handles(), loc='lower center', fontsize=8, ncol=4,
              bbox_to_anchor=(0.5, 0.0), frameon=False)
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    return _handle_output(fig, output)


# ---------------------------------------------------------------------
# Plots 2/3: elevation-vs-time and azimuth-vs-time heatmaps
# ---------------------------------------------------------------------

def _heatmap_figure(cnn, network, station, gnss, meta, bands, changes, visits,
                    date_start: Date, date_end: Date, y_edges, y_label, y_ticks, y_ticklabels,
                    title, build_grids_fn):
    n_rows = len(bands)
    fig, axes = plt.subplots(n_rows, 2, figsize=(12, 2.0 * n_rows + 0.3), sharex=True, sharey=True,
                             squeeze=False)
    fig.suptitle(title, fontsize=10, family='monospace', y=0.99)

    mp_im = snr_im = None
    for row_i, band in enumerate(bands):
        days, mp_grid, snr_grid = build_grids_fn(band)
        ax_mp, ax_snr = axes[row_i, 0], axes[row_i, 1]
        ax_mp.set_ylabel(f'{_band_label(band)}\n{y_label}', fontsize=8)
        if days is not None and len(days) >= 2:
            # plain numeric axis of Date.fyear, same as geode.etm's time
            # series plots (see this module's docstring)
            fyears = np.array([d.fyear for d in days])
            day_step = np.median(np.diff(fyears))
            day_edges = np.concatenate([fyears - day_step / 2, [fyears[-1] + day_step / 2]])
            mp_im = ax_mp.pcolormesh(day_edges, y_edges, mp_grid, cmap='viridis',
                                     vmin=MP_BOUNDS_CM[0], vmax=MP_BOUNDS_CM[1], shading='flat')
            snr_im = ax_snr.pcolormesh(day_edges, y_edges, snr_grid, cmap='magma',
                                       vmin=SNR_BOUNDS_DBHZ[0], vmax=SNR_BOUNDS_DBHZ[1], shading='flat')
        if row_i == 0:
            ax_mp.set_title('MP RMS [cm]', fontsize=9)
            ax_snr.set_title('SNR mean [dB-Hz]', fontsize=9)
        for ax in (ax_mp, ax_snr):
            ax.set_ylim(y_edges[0], y_edges[-1])
            ax.set_yticks(y_ticks)
            if y_ticklabels is not None:
                ax.set_yticklabels(y_ticklabels)
            _add_event_lines(ax, changes, visits)
            # explicit, not autoscaled -- see plot_daily_summary's set_xlim comment
            ax.set_xlim(date_start.fyear, date_end.fyear)

    fig.legend(handles=_event_legend_handles(), loc='lower center', fontsize=8, ncol=4,
              bbox_to_anchor=(0.46, 0.0), frameon=False)
    fig.tight_layout(rect=(0, 0.03, 0.86, 0.95))

    # Build each cax from the MP/SNR column's *actual* post-tight_layout bounding
    # box (bottom of the last row to top of the first), not a guessed fraction of
    # figure height -- that guess doesn't scale with n_rows (title/legend margins
    # are ~fixed in inches, so their fraction of a taller/shorter figure shifts),
    # which previously left the colorbar misaligned with its own column. The two
    # caxes still need a deliberate (not hairline) gap between them: MP RMS ticks
    # run up to 3 digits ("100") and both carry a "[unit]" label, which collide
    # with the next colorbar over if the gap is too tight.
    if mp_im is not None:
        top, bottom = axes[0, 0].get_position(), axes[-1, 0].get_position()
        cax = fig.add_axes((0.875, bottom.y0, 0.018, top.y1 - bottom.y0))
        fig.colorbar(mp_im, cax=cax, label='[cm]')
    if snr_im is not None:
        top, bottom = axes[0, 1].get_position(), axes[-1, 1].get_position()
        cax = fig.add_axes((0.94, bottom.y0, 0.018, top.y1 - bottom.y0))
        fig.colorbar(snr_im, cax=cax, label='[dB-Hz]')
    return fig


def plot_elevation_time(cnn, network, station, gnss, az_range=None, date_start: Optional[Date] = None,
                        date_end: Optional[Date] = None, output: Optional[QcPlotOutput] = None,
                        _bins_cache=None):
    """_bins_cache: internal, shared with a sibling plot_azimuth_time() call
    by plot_station() so the same rinex_qc_signal_bins rows for a (gnss,
    band) aren't fetched from the database twice when both heatmaps are
    generated together (the default) -- see get_daily_bin_grids()."""
    meta = get_station_meta(cnn, network, station)
    if date_start is None or date_end is None:
        date_start, date_end = get_date_span(cnn, network, station)
    bands = get_bands_present(cnn, network, station, gnss)
    changes = get_equipment_changes(cnn, network, station)
    visits = get_visits(cnn, network, station)
    grid = make_equal_area_grid()
    elev_edges = np.asarray(grid['elev_edges'])

    _, completeness = get_daily_completeness(cnn, network, station, date_start, date_end)
    overall_completeness = float(np.nanmean(completeness)) if len(completeness) else None

    filter_str = (f'azimuth filter: {az_range[0]:.0f}-{az_range[1]:.0f} deg'
                 if az_range is not None else None)
    title = _build_title(meta, network, station, gnss, 'MP/SNR vs ELEVATION over time',
                         date_start, date_end, overall_completeness, filter_str=filter_str)

    def build_grids(band):
        days, day_grids = get_daily_bin_grids(cnn, network, station, gnss, band, date_start, date_end,
                                              grid, cache=_bins_cache)
        if days is None:
            return None, None, None
        n_obs, mp_rms_mm, snr_ddbhz = day_grids
        mp_grid, snr_grid = collapse_to_elev_rings(n_obs, mp_rms_mm, snr_ddbhz, grid, az_range=az_range)
        return days, mp_grid, snr_grid

    fig = _heatmap_figure(cnn, network, station, gnss, meta, bands, changes, visits,
                          date_start, date_end, elev_edges, 'elev [deg]', [0, 30, 60, 90], None,
                          title, build_grids)
    return _handle_output(fig, output)


def plot_azimuth_time(cnn, network, station, gnss, elev_range=None, date_start: Optional[Date] = None,
                      date_end: Optional[Date] = None, output: Optional[QcPlotOutput] = None,
                      _bins_cache=None):
    """_bins_cache: see plot_elevation_time()'s docstring."""
    meta = get_station_meta(cnn, network, station)
    if date_start is None or date_end is None:
        date_start, date_end = get_date_span(cnn, network, station)
    bands = get_bands_present(cnn, network, station, gnss)
    changes = get_equipment_changes(cnn, network, station)
    visits = get_visits(cnn, network, station)
    grid = make_equal_area_grid()
    w_per_ring = _az_weight_matrices(grid)
    az_edges = np.linspace(0, 360, N_TARGET_AZ + 1)

    _, completeness = get_daily_completeness(cnn, network, station, date_start, date_end)
    overall_completeness = float(np.nanmean(completeness)) if len(completeness) else None

    filter_str = (f'elevation filter: {elev_range[0]:.0f}-{elev_range[1]:.0f} deg'
                 if elev_range is not None else None)
    title = _build_title(meta, network, station, gnss, 'MP/SNR vs AZIMUTH over time',
                         date_start, date_end, overall_completeness, filter_str=filter_str)

    def build_grids(band):
        days, day_grids = get_daily_bin_grids(cnn, network, station, gnss, band, date_start, date_end,
                                              grid, cache=_bins_cache)
        if days is None:
            return None, None, None
        n_obs, mp_rms_mm, snr_ddbhz = day_grids
        az_mp, az_snr = collapse_to_azimuth(n_obs, mp_rms_mm, snr_ddbhz, grid, w_per_ring,
                                           elev_range=elev_range)
        return days, az_mp, az_snr

    fig = _heatmap_figure(cnn, network, station, gnss, meta, bands, changes, visits,
                          date_start, date_end, az_edges, 'azimuth', [0, 90, 180, 270, 360],
                          ['N', 'E', 'S', 'W', 'N'], title, build_grids)
    return _handle_output(fig, output)


# ---------------------------------------------------------------------
# CLI/backend convenience: every constellation x every requested plot type
# ---------------------------------------------------------------------

PLOT_FUNCTIONS = {
    'summary': plot_daily_summary,
    'elevation': plot_elevation_time,
    'azimuth': plot_azimuth_time,
}


def plot_station(cnn, network, station, plot_types=('summary', 'elevation', 'azimuth'),
                 gnss_list=None, out_dir=None, fmt='png', elev_range=None, az_range=None,
                 date_start: Optional[Date] = None, date_end: Optional[Date] = None):
    """
    Generate every requested plot type for every constellation the station has
    QC data for (or just gnss_list, if given). Returns None (plots written
    under out_dir, one file per gnss x plot type) if out_dir is given, else a
    nested {gnss: {plot_type: base64_str}} dict -- the backend/no-file path.
    date_start/date_end are geode.pyDate.Date (default: the station's full
    rinex_qc_run span, from get_date_span()).
    """
    gnss_list = gnss_list or get_gnss_present(cnn, network, station)
    results = {} if out_dir is None else None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    for gnss in gnss_list:
        if results is not None:
            results[gnss] = {}
        # shared across this gnss's elevation/azimuth calls below, so a band
        # present in both plot_types is only fetched from rinex_qc_signal_bins
        # once (see plot_elevation_time's _bins_cache docstring)
        bins_cache = {}
        for plot_type in plot_types:
            kwargs = dict(date_start=date_start, date_end=date_end)
            if plot_type == 'elevation':
                kwargs['az_range'] = az_range
                kwargs['_bins_cache'] = bins_cache
            elif plot_type == 'azimuth':
                kwargs['elev_range'] = elev_range
                kwargs['_bins_cache'] = bins_cache

            if out_dir:
                filename = os.path.join(out_dir, f'{network}.{station}_{gnss}_{plot_type}.{fmt}')
                output = QcPlotOutput(filename=filename, format=fmt)
            else:
                output = QcPlotOutput(file_io=BytesIO(), format=fmt)

            b64 = PLOT_FUNCTIONS[plot_type](cnn, network, station, gnss, output=output, **kwargs)
            if results is not None:
                results[gnss][plot_type] = b64

    return results
