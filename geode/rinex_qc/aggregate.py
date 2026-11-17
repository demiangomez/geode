"""
Per-signal and per-bin statistics, the equal-area az-el grid, and pool().

Works on the BandData arrays runner.py returns (see
knowledge/decisions/0005-switch-to-ringo.md) -- (nepochs, nsats) arrays of
MP/azimuth/elevation/SNR plus a boolean slip-flag array. Unlike
gnssmultipath, RINGO reports slips per-epoch rather than pre-binned by
elevation, so _slips_by_elev() below does that binning here instead.

All statistics come from per-epoch values, after the elevation cutoff
already applied by runner.py's call to RINGO (masked as NaN in az/el) --
nothing here re-applies or second-guesses that cutoff.
"""
import math

import numpy as np

DEFAULT_RING_WIDTH_DEG  = 5.0
DEFAULT_HORIZON_SECTORS = 72

# Same 6 elevation bands the design doc specified (<10, 10-20, ..., >50),
# previously read straight out of gnssmultipath's own slip_distribution
# dict (ADR-0003); computed directly from (slip_mask, elevation) now.
_ELEV_BAND_EDGES = (0, 10, 20, 30, 40, 50, 90)


def make_equal_area_grid(ring_width_deg=DEFAULT_RING_WIDTH_DEG,
                         horizon_sectors=DEFAULT_HORIZON_SECTORS):
    """
    Deterministic az-el equal-area grid: elev_edges are ring_width_deg-wide
    rings from 0 to 90 degrees; az_bins[i] is the number of azimuth sectors
    in ring i, proportional to cos(ring-centre elevation) so each cell
    covers roughly the same patch of sky (sectors start at 0 deg = north,
    clockwise). Deterministic by construction -- same arguments always
    produce the same grid; a grid actually stored in rinex_qc_grids is
    never recomputed, only ever referenced by its grid_id.
    """
    n_rings = int(round(90.0 / ring_width_deg))
    elev_edges = [round(i * ring_width_deg, 6) for i in range(n_rings + 1)]
    ref = math.cos(math.radians(ring_width_deg / 2.0))

    az_bins = []
    for i in range(n_rings):
        centre = (elev_edges[i] + elev_edges[i + 1]) / 2.0
        az_bins.append(max(1, round(horizon_sectors * math.cos(math.radians(centre)) / ref)))

    return {'kind': 'azel_equal_area', 'elev_edges': elev_edges, 'az_bins': az_bins}


def bin_indices(elevation, azimuth, grid):
    """
    Vectorized flat bin index for elevation/azimuth arrays of the same
    shape. NaN elevation (masked by RINGO's own --elmaskf cutoff) maps to -1,
    as does any other non-finite input.
    """
    elevation = np.asarray(elevation, dtype=np.float64)
    azimuth   = np.asarray(azimuth, dtype=np.float64)
    elev_edges = np.asarray(grid['elev_edges'])
    az_bins    = np.asarray(grid['az_bins'])
    n_rings    = len(az_bins)
    ring_offsets = np.concatenate([[0], np.cumsum(az_bins)])

    valid = np.isfinite(elevation) & np.isfinite(azimuth)
    safe_elev = np.where(valid, elevation, elev_edges[0])
    ring = np.clip(np.searchsorted(elev_edges, safe_elev, side='right') - 1, 0, n_rings - 1)

    n_sectors = az_bins[ring]
    az = np.mod(np.where(valid, azimuth, 0.0), 360.0)
    sector = np.clip((az / (360.0 / n_sectors)).astype(np.int64), 0, n_sectors - 1)

    idx = ring_offsets[ring] + sector
    return np.where(valid, idx, -1)


def aggregate_signal(band_data, grid):
    """
    Per-signal summary statistics and per-bin statistics for one RINGO
    band result (geode.rinex_qc.runner.BandData).

    :return: (signal_summary: dict, bins: dict) -- bins is in the same
        {'n_obs', 'mp_rms', 'snr_mean'} shape pool() operates on.
    """
    mp   = band_data.mp
    elev = band_data.el
    azimuth = band_data.az
    valid = np.isfinite(mp) & np.isfinite(elev)

    n_obs = int(valid.sum())
    n_sats = int(np.count_nonzero(np.any(valid, axis=0)))
    mp_rms_m = float(np.sqrt(np.mean(mp[valid] ** 2))) if n_obs else None

    snr_mean = snr_std = None
    if band_data.snr is not None:
        snr = band_data.snr
        snr_valid = valid & np.isfinite(snr) & (snr != 0)
        if snr_valid.any():
            snr_vals = snr[snr_valid]
            snr_mean = float(np.mean(snr_vals))
            snr_std  = float(np.std(snr_vals))

    signal_summary = {
        'n_obs':         n_obs,
        'n_sats':        n_sats,
        'mp_rms_m':      mp_rms_m,
        'snr_mean':      snr_mean,
        'snr_std':       snr_std,
        'n_slips':       int(band_data.slip.sum()),
        'slips_by_elev': _slips_by_elev(band_data.slip, elev),
        'mp_partner':    None,  # RINGO doesn't expose the exact code pair it used, see runner.py
    }

    bins = _aggregate_bins(mp, elev, azimuth, grid, band_data.snr)
    return signal_summary, bins


def _slips_by_elev(slip_mask, elev):
    """Count slip-flagged epochs per elevation band; a slip at a NaN-elevation
    epoch is not assignable to any band and is excluded (matches gnssmultipath's
    own n_slips_NaN, which the previous ADR-0003-era code also dropped)."""
    counts = []
    for lo, hi in zip(_ELEV_BAND_EDGES[:-1], _ELEV_BAND_EDGES[1:]):
        in_band = np.isfinite(elev) & (elev >= lo) & (elev < hi)
        counts.append(int(np.count_nonzero(slip_mask & in_band)))
    return counts


def _aggregate_bins(mp, elev, azimuth, grid, snr_array=None):
    n_bins = int(np.sum(grid['az_bins']))
    idx = bin_indices(elev, azimuth, grid)

    sum_n     = np.zeros(n_bins, dtype=np.int64)
    sum_sq_mp = np.zeros(n_bins, dtype=np.float64)

    mask  = np.isfinite(mp) & (idx >= 0)
    idx_m = idx[mask]
    np.add.at(sum_n, idx_m, 1)
    np.add.at(sum_sq_mp, idx_m, mp[mask] ** 2)

    mp_rms = np.full(n_bins, np.nan)
    nz = sum_n > 0
    mp_rms[nz] = np.sqrt(sum_sq_mp[nz] / sum_n[nz])

    snr_mean = np.full(n_bins, np.nan)
    if snr_array is not None:
        snr = np.asarray(snr_array, dtype=np.float64)
        snr_mask = np.isfinite(snr) & (snr != 0) & (idx >= 0)
        idx_s = idx[snr_mask]
        sum_snr = np.zeros(n_bins, dtype=np.float64)
        n_snr   = np.zeros(n_bins, dtype=np.int64)
        np.add.at(sum_snr, idx_s, snr[snr_mask])
        np.add.at(n_snr, idx_s, 1)
        nz_snr = n_snr > 0
        snr_mean[nz_snr] = sum_snr[nz_snr] / n_snr[nz_snr]

    return {'n_obs': sum_n, 'mp_rms': mp_rms, 'snr_mean': snr_mean}


def pool(bins_a, bins_b):
    """
    Combine two per-bin stat dicts exactly: n-weighted sum-of-squares for
    mp_rms, n-weighted mean for snr_mean. This is the basis for stacking
    daily bins into weekly/yearly ones -- splitting a day in half,
    aggregating each half and pooling must equal aggregating the whole day.
    """
    n_a = np.asarray(bins_a['n_obs'], dtype=np.float64)
    n_b = np.asarray(bins_b['n_obs'], dtype=np.float64)
    n = n_a + n_b
    nz = n > 0

    def pool_rms(a, b):
        sq_sum = np.nan_to_num(np.asarray(a, dtype=np.float64) ** 2) * n_a \
               + np.nan_to_num(np.asarray(b, dtype=np.float64) ** 2) * n_b
        out = np.full(n.shape, np.nan)
        out[nz] = np.sqrt(sq_sum[nz] / n[nz])
        return out

    def pool_mean(a, b):
        s = np.nan_to_num(np.asarray(a, dtype=np.float64)) * n_a \
          + np.nan_to_num(np.asarray(b, dtype=np.float64)) * n_b
        out = np.full(n.shape, np.nan)
        out[nz] = s[nz] / n[nz]
        return out

    return {
        'n_obs':    n.astype(np.int64),
        'mp_rms':   pool_rms(bins_a['mp_rms'], bins_b['mp_rms']),
        'snr_mean': pool_mean(bins_a['snr_mean'], bins_b['snr_mean']),
    }
