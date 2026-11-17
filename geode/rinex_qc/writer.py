"""
Output: the Phase 1 <file>_qc.json + <file>_qc_bins.csv, and the Phase 3
database writer. The JSON layout mirrors the database tables one-to-one
(run, signal list, bins), so write_db() below is a direct mapping of the
same data, not a redesign.
"""
import csv
import json
import math
import os

import numpy as np
import psycopg2.extras


def write_json_csv(out_dir, basename, run, signal_results):
    """
    :param run: a pipeline.QCRunResult.run dict.
    :param signal_results: a pipeline.QCRunResult.signals list
        ({'summary', 'bins', 'grid_id'} dicts).
    :return: (json_path, csv_path).
    """
    os.makedirs(out_dir, exist_ok=True)

    payload = {'run': run, 'signals': [item['summary'] for item in signal_results]}

    json_path = os.path.join(out_dir, '%s_qc.json' % basename)
    with open(json_path, 'w') as f:
        json.dump(payload, f, indent=2, default=_json_default)

    csv_path = os.path.join(out_dir, '%s_qc_bins.csv' % basename)
    with open(csv_path, 'w', newline='') as f:
        rows = csv.writer(f)
        rows.writerow(['gnss', 'band_attr', 'grid_id', 'bin_index', 'n_obs', 'mp_rms_m', 'snr_mean'])
        for item in signal_results:
            s, bins = item['summary'], item['bins']
            for i in range(len(bins['n_obs'])):
                if bins['n_obs'][i] == 0:
                    continue
                rows.writerow([s['gnss'], s['band_attr'], item['grid_id'], i, int(bins['n_obs'][i]),
                              bins['mp_rms'][i], bins['snr_mean'][i]])

    return json_path, csv_path


_RUN_COLUMNS = (
    'NetworkCode', 'StationCode', 'ObservationYear', 'ObservationDOY', 'rinex_id', 'tool',
    'tool_version', 'config_hash', 'config', 'elev_cutoff_deg', 'orbit_source', 'orbit_files',
    'systems_processed', 'systems_without_orbits', 'rinex_version', 'receiver_type',
    'antenna_type', 'interval_s', 'epochs_expected', 'epochs_observed', 'n_clock_jumps',
    'header_fixed', 'flags', 'status', 'message', 'runtime_s',
)

_SIGNAL_COLUMNS = ('run_id', 'gnss', 'band_attr', 'obs_code_orig', 'mp_partner', 'n_obs', 'n_sats',
                   'mp_rms_m', 'snr_mean', 'snr_std', 'snr_unit', 'snr_band_only', 'n_slips',
                   'slips_by_elev')

_BINS_COLUMNS = ('run_id', 'gnss', 'band_attr', 'grid_id', 'n_obs', 'mp_rms_mm', 'snr_ddbhz')


def write_db(cnn, run, signal_results, force=False):
    """
    Write one run + its signals + bins to the database in a single
    transaction (geode.dbConnection.Cnn's insert()/insert_many() each
    auto-commit internally, which would break that atomicity -- this
    writes raw parameterized SQL inside begin_transac()/commit_transac()
    instead, the same pattern geode/dbConnection.py's own migrations use).

    :param run: a pipeline.QCRunResult.run dict. Written even when
        run['status'] == 'failed' -- a stored failure means a later
        incremental/backfill pass correctly skips re-trying it under the
        same config, rather than retrying a known-permanent failure
        forever.
    :param signal_results: a pipeline.QCRunResult.signals list.
    :param force: if a run already exists for this (rinex_id, tool,
        tool_version, config_hash), skip by default; force=True deletes
        it (cascading to its own signal/bin rows) and rewrites it.
    :return: True if written, False if skipped (existing run, not forced).
    """
    existing = cnn.query(
        'SELECT id FROM rinex_qc_run WHERE rinex_id = %s AND tool = %s '
        'AND tool_version = %s AND config_hash = %s',
        (run['rinex_id'], run['tool'], run['tool_version'], run['config_hash'])).dictresult()

    if existing:
        if not force:
            return False
        cnn.query('DELETE FROM rinex_qc_run WHERE id = %s', (existing[0]['id'],))

    cnn.begin_transac()
    try:
        values = tuple(_run_value(run, col) for col in _RUN_COLUMNS)
        placeholders = ', '.join(['%s'] * len(_RUN_COLUMNS))
        columns = '", "'.join(_RUN_COLUMNS)
        run_row = cnn.query(
            'INSERT INTO rinex_qc_run ("%s") VALUES (%s) RETURNING id'
            % (columns, placeholders), values).dictresult()
        run_id = run_row[0]['id']

        if signal_results:
            signal_rows = [tuple(_signal_value(run_id, item, col) for col in _SIGNAL_COLUMNS)
                           for item in signal_results]
            bins_rows = [tuple(_bins_value(run_id, item, col) for col in _BINS_COLUMNS)
                        for item in signal_results]

            psycopg2.extras.execute_values(
                cnn.cursor,
                'INSERT INTO rinex_qc_signal ("%s") VALUES %%s' % '", "'.join(_SIGNAL_COLUMNS),
                signal_rows)
            psycopg2.extras.execute_values(
                cnn.cursor,
                'INSERT INTO rinex_qc_signal_bins ("%s") VALUES %%s' % '", "'.join(_BINS_COLUMNS),
                bins_rows)

        cnn.commit_transac()
        return True
    except Exception:
        cnn.rollback_transac()
        raise


def _run_value(run, col):
    if col == 'config':
        return psycopg2.extras.Json(run['config'])
    return run[col]


def _signal_value(run_id, item, col):
    if col == 'run_id':
        return run_id
    return item['summary'][col]


def _bins_value(run_id, item, col):
    if col == 'run_id':
        return run_id
    if col == 'grid_id':
        return item['grid_id']
    if col in ('gnss', 'band_attr'):
        return item['summary'][col]
    n_obs, mp_rms_mm, snr_ddbhz = _quantize_bins(item['bins'])
    return {'n_obs': n_obs, 'mp_rms_mm': mp_rms_mm, 'snr_ddbhz': snr_ddbhz}[col]


_SMALLINT_MAX = 32767  # postgres smallint range is -32768..32767


def _quantize_bins(bins):
    """
    Storage-time quantization: MP in mm, SNR in 0.1 units, both smallint;
    an empty cell (n_obs == 0) is NULL across all three arrays, not zero,
    per rinex_qc_signal_bins' own convention.

    A quantized value outside smallint range is stored as NULL rather
    than passed through -- a real production crash (one bad bin took
    down an entire file's write, inside one execute_values() call with
    no per-row recovery) traced to a since-fixed multipath-detrending bug
    (runner._detrend_by_arc(), see its docstring) that briefly produced
    bins over 32m. That root cause is fixed, but this guard stays as a
    second line of defense: one future bad bin should mean one NULL
    cell, never the whole file silently losing every other signal's
    otherwise-good data.
    """
    n_obs, mp_rms_mm, snr_ddbhz = [], [], []
    for i in range(len(bins['n_obs'])):
        if bins['n_obs'][i] == 0:
            n_obs.append(None)
            mp_rms_mm.append(None)
            snr_ddbhz.append(None)
            continue
        n_obs.append(int(bins['n_obs'][i]))
        mp_rms_mm.append(_safe_smallint(bins['mp_rms'][i], 1000))
        snr_ddbhz.append(_safe_smallint(bins['snr_mean'][i], 10))
    return n_obs, mp_rms_mm, snr_ddbhz


def _safe_smallint(value, scale):
    if math.isnan(value):
        return None
    quantized = int(round(value * scale))
    return quantized if abs(quantized) <= _SMALLINT_MAX else None


def _json_default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if math.isnan(obj) else float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError('Object of type %s is not JSON serializable' % type(obj).__name__)
