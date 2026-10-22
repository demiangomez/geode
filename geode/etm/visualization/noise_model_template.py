"""
Project: Geodetic Database Engine (GeoDE)
Date: 10/19/26
Author: Demian D. Gomez

Template for the diagnostic plot of the noise model estimated by NoiseCovarianceGLS. One row per component:
the empirical covariance of the residuals with the fitted models (left) and the statistic used to detect the
periodic terms (right): the misfit-spectrum ratio of the joint harmonic fit, or the scan of the stepwise grid
search. The data come from AdjustmentResults.noise_model (units: mm, cycles/yr, days).
"""

import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, FormatStrFormatter, NullFormatter
import numpy as np
from typing import Tuple

from ..visualization.plot_template import PlotTemplate
from ..visualization.data_classes import NoiseModelPlotData, PlotOutputConfig

DAYS_PER_YEAR = 365.25

# outcome of a candidate periodic term -> marker style and legend label (stepwise grid search, joint harmonic fit)
CANDIDATE_STYLES = {
    'accepted':             ({'marker': 'o', 'markersize': 6}, 'accepted'),
    'snr':                  ({'marker': 'v', 'markersize': 6, 'mfc': 'none'}, 'rejected: SNR'),
    'joint':                ({'marker': 's', 'markersize': 6, 'mfc': 'none'}, 'rejected: joint fit'),
    'zero':                 ({'marker': 'x', 'markersize': 6}, 'rejected: zero variance'),
    'unresolved':           ({'marker': 'D', 'markersize': 5, 'mfc': 'none'}, 'unresolved'),
    'below threshold':      ({'marker': 'v', 'markersize': 6, 'mfc': 'none'}, 'rejected: SNR in the harmonic fit'),
    'zero after refit':     ({'marker': 's', 'markersize': 6, 'mfc': 'none'}, 'rejected: zero after refit'),
    'zero in harmonic fit': ({'marker': 'x', 'markersize': 6}, 'rejected: zero in the harmonic fit'),
}


def _floats(values) -> np.ndarray:
    """list from the JSON report (None = not available) to a float array with NaN"""
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def _fmt(value, spec: str = '.2f') -> str:
    return 'n/a' if value is None or not np.isfinite(value) else format(value, spec)


class NoiseModelTemplate(PlotTemplate):
    """Template for the noise model diagnostic plot"""

    @staticmethod
    def setup_colors():
        colors = PlotTemplate.setup_colors()
        # reference fit (no cosines) in blue and final fit in orange, in both panels
        colors.update({'empirical': 'gray',
                       'fit': (0.85, 0.325, 0.098),
                       'fit_reference': (0., 0.447, 0.741),
                       'scan_reference': (0., 0.447, 0.741),
                       'scan_final': (0.85, 0.325, 0.098),
                       'accepted': (0., 0.447, 0.741),
                       'rejected': 'dimgray'})
        return colors

    def create_figure_layout(self) -> Tuple[plt.Figure, np.ndarray]:
        """3 rows (N, E, U) x 2 columns (covariance fit, scan)"""
        fig, axes = plt.subplots(nrows=3, ncols=2, figsize=(16, 15))
        fig.subplots_adjust(left=0.06, right=0.98, top=0.89, bottom=0.07, hspace=0.42, wspace=0.16)
        return fig, axes

    def generate_title(self, plot_data: NoiseModelPlotData, output_config: PlotOutputConfig) -> str:
        """Station title plus the settings of the noise model"""
        base_title = super().generate_title(plot_data, output_config)

        nm = next((n for n in plot_data.noise_models if n and 'settings' in n), None)
        if nm is None:
            return base_title

        st = nm['settings']
        if nm.get('detection') == 'joint':
            detection = (f"joint harmonic fit: SNR >= {st['snr_min']:g} in the harmonic fit; candidates at misfit "
                         f"ratio >= {st['cand_ratio']:g}; periods {nm['span_days'] / st['min_cycles']:.0f} to "
                         f"{st['p_min']:.0f} d")
        else:
            detection = (f"stepwise grid search: band {st['band_cpy'][0]:g}-{st['band_cpy'][1]:g} cy/yr; "
                         f"SNR >= {st['snr_min']:g}; dchi2 >= {st['dchi2_min']:g}")
        return (base_title + '\n' +
                f"Noise model: power laws alpha = {min(st['alphas']):g}-{max(st['alphas']):g} + white "
                f"(>= {st['white_min']:g} mm^2) + periodic terms; {nm['n_lags']} fitted lags up to "
                f"{nm['tau_max']} d\nDetection: {detection}")

    def plot_covariance_fit(self, ax, nm: dict, label: str) -> None:
        """Empirical covariance of the residuals and the projected fits without and with the periodic terms"""
        diag = nm['diagnostics']
        gamma = _floats(diag['gamma'])
        tau = np.arange(gamma.size) / DAYS_PER_YEAR
        lags = np.array(diag['lags'], dtype=int)
        n_periods = len(nm['periodic'])

        ax.plot(tau, gamma, 'o', color=self.colors['empirical'], markersize=2, markeredgewidth=0,
                label='empirical covariance of residuals')
        ax.plot(tau[lags], gamma[lags], 'd', mfc='none', mec='k', markersize=3, markeredgewidth=0.6,
                label='fitted lags')
        ax.plot(tau, _floats(diag['fit_reference']), '--', color=self.colors['fit_reference'], linewidth=1.5,
                label='fit without cosines (reference)')
        ax.plot(tau, _floats(diag['fit_final']), '-', color=self.colors['fit'], linewidth=1.5,
                label=f'fit with {n_periods} detected cosine(s)')
        ax.axhline(0, color='k', linestyle=':', linewidth=0.6)

        ax.set_xlim(0, tau[-1])
        ax.set_xlabel('lag [years]')
        ax.set_ylabel('mm$^2$')
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=8, loc='upper right')

        ref, fin = nm['effective_alpha']['reference'], nm['effective_alpha']['final']
        ax.set_title(f"{label}: fit to the empirical covariance (as seen in the residuals)\n"
                     f"effective $\\alpha$: reference {self._alpha(ref)}; with cosines {self._alpha(fin)}",
                     fontsize=10)

    def plot_detection(self, ax, nm: dict, label: str) -> None:
        """Right panel: the statistic of the detection method used"""
        if nm.get('detection') == 'joint':
            self.plot_spectrum(ax, nm, label)
        else:
            self.plot_scan(ax, nm, label)

    def plot_spectrum(self, ax, nm: dict, label: str) -> None:
        """Joint harmonic fit: ratio of the misfit spectrum to the noise per bin, reference fit and final check"""
        diag = nm['diagnostics']
        st = nm['settings']
        f = _floats(diag['spec_f_cpy'])
        r_ref = _floats(diag['spec_r_reference'])
        r_fin = _floats(diag['spec_r_final'])
        periodic = nm['periodic']

        # the tallest peak at half the height: the top half is for the legend (7 entries)
        floor = 0.1
        peak = max(np.nanmax(r_ref), np.nanmax(r_fin), st['snr_min'])
        top = floor * (peak / floor) ** 2

        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.plot(f, np.maximum(r_ref, floor), color=self.colors['scan_reference'], linewidth=1.2,
                label='reference fit (no cosines)')
        ax.plot(f, np.maximum(r_fin, floor), color=self.colors['scan_final'], linewidth=1.2,
                label=f'final fit ({len(periodic)} period(s)): final check')

        # thresholds in the legend (labels on the axes collide with the candidates at the band edges)
        ax.axhline(st['cand_ratio'], color='gray', linestyle=':', linewidth=1,
                   label=f"candidates: ratio $\\geq$ {st['cand_ratio']:g}")
        ax.axhline(st['snr_min'], color='gray', linestyle='--', linewidth=0.8,
                   label=f"final check: ratio $\\geq$ {st['snr_min']:g}")

        for p in periodic:
            ax.axvline(p['f_cpy'], color='k', linestyle=':', linewidth=0.8)

        # candidates at the peak of the reference ratio where they were found (before refinement), by outcome;
        # periods added by the final check at their peak of the final ratio
        cands = [dict(c, f_peak=DAYS_PER_YEAR / c['period_before_refinement_d']) for c in nm['candidates']]
        self._plot_candidates(ax, cands, f, r_ref)
        added = [dict(p, f_peak=p['f_cpy'], outcome='added') for p in periodic if p.get('final_check') == 'added']
        for p in added:
            ax.plot(p['f_peak'], np.interp(p['f_peak'], f, r_fin), linestyle='none', marker='*', markersize=10,
                    color=self.colors['scan_final'], label='added by the final check')
            ax.annotate(f"{p['period_d']:.0f} d", (p['f_peak'], np.interp(p['f_peak'], f, r_fin)),
                        textcoords='offset points', xytext=(0, 7), ha='center', fontsize=7,
                        color=self.colors['scan_final'])

        self._frequency_axis(ax, f, floor, top)
        ax.set_ylabel('misfit spectrum / noise per bin')
        self._legend_unique(ax)

        ax.set_title(f"{label}: spectrum of the covariance misfit (joint harmonic fit)\n"
                     f"periods: {self._periods(periodic)}", fontsize=10)

    def plot_scan(self, ax, nm: dict, label: str) -> None:
        """Drop in weighted misfit when a cosine is added, for the reference and the final fit"""
        diag = nm['diagnostics']
        st = nm['settings']
        f = _floats(diag['fgrid_cpy'])
        scan_ref = _floats(diag['scan_reference'])
        scan_fin = _floats(diag['scan_final'])
        accepted = nm['periodic']

        # log axes: values below the floor are drawn on the floor; the tallest peak at 60% of the height
        # leaves the top of the axes to the legend
        floor = 0.1
        peak = max(np.nanmax(scan_ref), st['dchi2_min'])
        top = floor * (peak / floor) ** (1 / 0.6)

        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.plot(f, np.maximum(scan_ref, floor), color=self.colors['scan_reference'], linewidth=1.2,
                label='reference fit (no cosines)')
        ax.plot(f, np.maximum(scan_fin, floor), color=self.colors['scan_final'], linewidth=1.2,
                label=f'with the {len(accepted)} accepted period(s)')

        ax.axhline(st['dchi2_min'], color='gray', linestyle=':', linewidth=1,
                   label=f"minimum $\\Delta\\chi^2$ = {st['dchi2_min']:g}")

        for p in accepted:
            ax.axvline(p['f_cpy'], color='k', linestyle=':', linewidth=0.8)

        # candidates on the reference scan, marker by outcome
        self._plot_candidates(ax, [dict(c, f_peak=c['f_cpy']) for c in nm['candidates']], f, scan_ref)

        self._frequency_axis(ax, f, floor, top)
        ax.set_ylabel('$\\Delta\\chi^2$ (drop in weighted misfit)')
        self._legend_unique(ax)

        ax.set_title(f"{label}: drop in weighted misfit when a cosine at f is added (stepwise grid search)\n"
                     f"accepted (SNR $\\geq$ {st['snr_min']:g}, $\\Delta\\chi^2 \\geq$ {st['dchi2_min']:g}): "
                     f"{self._periods(accepted)}", fontsize=10)

    def _plot_candidates(self, ax, candidates: list, f: np.ndarray, curve: np.ndarray) -> None:
        """candidates at (f_peak, curve(f_peak)), marker by outcome, accepted ones labeled with their period"""
        for c in candidates:
            style, text = CANDIDATE_STYLES.get(c['outcome'], CANDIDATE_STYLES['unresolved'])
            color = self.colors['accepted'] if c['outcome'] == 'accepted' else self.colors['rejected']
            y = np.interp(c['f_peak'], f, curve)
            ax.plot(c['f_peak'], y, linestyle='none', color=color, mec=color, label=text, **style)
            if c['outcome'] == 'accepted':
                ax.annotate(f"{c['period_d']:.0f} d", (c['f_peak'], y), textcoords='offset points',
                            xytext=(0, 6), ha='center', fontsize=7, color=color)

    @staticmethod
    def _frequency_axis(ax, f: np.ndarray, floor: float, top: float) -> None:
        """log axes over the searched band, plain frequency ticks instead of powers of 10"""
        ax.set_xlim(f[0], f[-1])
        ax.set_ylim(floor, top)
        ax.xaxis.set_major_locator(FixedLocator([t for t in (0.1, 0.2, 0.3, 0.5, 1, 2, 3, 5, 10, 20, 50)
                                                 if f[0] <= t <= f[-1]]))
        ax.xaxis.set_major_formatter(FormatStrFormatter('%g'))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_xlabel('frequency [cycles/yr]')
        ax.grid(True, which='both', alpha=0.3)

    @staticmethod
    def _legend_unique(ax) -> None:
        """legend with one entry per label"""
        handles, labels = ax.get_legend_handles_labels()
        unique = dict(zip(labels, handles))
        ax.legend(unique.values(), unique.keys(), fontsize=8, loc='upper right')

    @staticmethod
    def _periods(periodic: list) -> str:
        return ', '.join(f"{p['period_d']:.0f} d" for p in periodic) or 'none'

    @staticmethod
    def plot_not_available(axes, label: str) -> None:
        """Row of a component without a noise model (e.g. the fit fell back to robust least squares)"""
        for ax in axes:
            ax.text(0.5, 0.5, f'{label}: noise model not available', transform=ax.transAxes,
                    ha='center', va='center', fontsize=10, color='gray')
            ax.set_xticks([])
            ax.set_yticks([])

    @staticmethod
    def _alpha(ea: dict) -> str:
        band = ea['band_cpy']
        if ea['band_empty']:
            # the steep part never dominates the floor: alpha is the local slope at 1/N
            return f"{_fmt(ea['alpha'])} (local at {_fmt(band[0], '.3g')} cy/yr)"
        return f"{_fmt(ea['alpha'])} ({_fmt(band[0], '.3g')}-{_fmt(band[1], '.3g')} cy/yr)"
