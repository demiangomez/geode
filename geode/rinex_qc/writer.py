"""
Phase 1 output: <file>_qc.json + <file>_qc_bins.csv.

The JSON layout mirrors the Phase 2 database tables one-to-one (run,
signal list, bins) so the eventual DB writer (TODO(db)) is a direct
mapping, not a redesign.
"""
import csv
import json
import math
import os

import numpy as np


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


def _json_default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if math.isnan(obj) else float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError('Object of type %s is not JSON serializable' % type(obj).__name__)
