"""
Project: Geodetic Database Engine (GeoDE)
Date: 11/21/26
Author: Demian D. Gomez

Template for the cross-covariance diagnostic plot: one row per component pair (N-E, N-U, E-U) with the
empirical cross-covariance of the two components' step-1 residuals and its power-law cross-spectrum
fit (left) and the alpha_x grid search that chose the model's spectral index (right). The data come
from EtmFit.cross_covariance (geode/etm/least_squares/cross_covariance.py's CrossCovarianceResult.
summary(), units: mm, days), separate from the single-component noise_model plot (noise_model_template.py).
"""

import matplotlib.pyplot as plt
import numpy as np
from typing import Tuple

from ..visualization.plot_template import PlotTemplate
from ..visualization.noise_model_template import NoiseModelTemplate
from ..visualization.data_classes import NoiseModelPlotData, PlotOutputConfig

DAYS_PER_YEAR = 365.25
PAIR_LABELS = {'NE': ('north', 'east'), 'NU': ('north', 'up'), 'EU': ('east', 'up')}


def _floats(values) -> np.ndarray:
    """list from the JSON report (None = not available) to a float array with NaN"""
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def _fmt(value, spec: str = '.3f') -> str:
    return 'n/a' if value is None or not np.isfinite(value) else format(value, spec)


class NoiseCrossModelTemplate(PlotTemplate):
    """Template for the cross-covariance diagnostic plot"""

    @staticmethod
    def setup_colors():
        colors = PlotTemplate.setup_colors()
        colors.update({'empirical': 'gray', 'fit': (0.85, 0.325, 0.098), 'chosen': (0.85, 0.325, 0.098),
                       'grid': (0., 0.447, 0.741)})
        return colors

    def create_figure_layout(self) -> Tuple[plt.Figure, np.ndarray]:
        """3 rows (N-E, N-U, E-U) x 2 columns (covariance fit, alpha_x grid search)"""
        fig, axes = plt.subplots(nrows=3, ncols=2, figsize=(16, 15))
        fig.subplots_adjust(left=0.06, right=0.98, top=0.89, bottom=0.07, hspace=0.42, wspace=0.16)
        return fig, axes

    def generate_title(self, plot_data: NoiseModelPlotData, output_config: PlotOutputConfig) -> str:
        base_title = super().generate_title(plot_data, output_config)
        return base_title + '\nCross-covariance: PLN cross-spectrum model'

    def plot_covariance_fit(self, ax, cc: dict, pair: str) -> None:
        """Empirical cross-covariance of the two components' step-1 residuals and the fitted model"""
        comp_i, comp_j = PAIR_LABELS[pair]
        label = f"{self.config.get_label(comp_i)}-{self.config.get_label(comp_j)}"
        diag = cc['diagnostics']
        gamma = _floats(diag['gamma'])
        model = _floats(diag['model'])
        tau = _floats(diag['lag_days']) / DAYS_PER_YEAR
        fitted = np.array(diag['fitted_lag_days'], dtype=int)
        fitted_tau = fitted / DAYS_PER_YEAR
        fitted_idx = fitted - int(diag['lag_days'][0])

        ax.plot(tau, gamma, 'o', color=self.colors['empirical'], markersize=2, markeredgewidth=0,
                label='Empirical cross-covariance')
        ax.plot(fitted_tau, gamma[fitted_idx], 'd', mfc='none', mec='k', markersize=3,
                markeredgewidth=0.6, label='Fitted lags')
        ax.plot(tau, model, '-', color=self.colors['fit'], linewidth=1.5,
                label=f"PLN fit")
        ax.axhline(0, color='k', linestyle=':', linewidth=0.6)
        ax.axvline(0, color='k', linestyle=':', linewidth=0.6)

        ax.set_xlim(tau[0], tau[-1])
        ax.set_xlabel('lag [years]')
        ax.set_ylabel('mm$^2$')
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=8, loc='upper right')

        periods = ', '.join(f"{p['period_d']:.0f} d" for p in cc['periodic']) or 'none'
        ax.set_title(f"{label}: fit to the empirical cross-covariance\n"
                    f"theta_w={_fmt(cc['theta_w'])} w_x={_fmt(cc['w_x'])} mm$^2$ | matched periods: "
                    f"{periods}", fontsize=10)

    def plot_alpha_grid_search(self, ax, cc: dict, pair: str) -> None:
        """Right panel: the alpha_x grid search that chose the cross-spectrum model's spectral index"""
        comp_i, comp_j = PAIR_LABELS[pair]
        label = f"{self.config.get_label(comp_i)}-{self.config.get_label(comp_j)}"
        diag = cc['diagnostics']
        alpha_grid = _floats(diag['alpha_grid'])
        rnorm = _floats(cc['residual_norm_grid'])

        ax.plot(alpha_grid, rnorm, 'o-', color=self.colors['grid'], linewidth=1.2, markersize=4,
                label='residual norm')
        best = int(np.nanargmin(rnorm))
        ax.plot(alpha_grid[best], rnorm[best], '*', color=self.colors['chosen'], markersize=14,
               label=f"chosen: alpha_x={alpha_grid[best]:g}")
        ax.axvline(alpha_grid[best], color=self.colors['chosen'], linestyle=':', linewidth=1)

        ax.set_xlabel('alpha_x (cross-spectrum spectral index)')
        ax.set_ylabel('whitened residual norm')
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=8, loc='upper right')

        # cc['daily_residual_correlation'] is what the borrowed (Section 10.2) model would give for
        # every parameter pair alike, including the rate -- there is no separate "borrowed rate
        # correlation" to look up, since the two are always numerically identical
        ax.set_title(f"{label}: search over alpha_x\n"
                    f"rate correlation {_fmt(cc.get('rate_correlation'))} "
                    f"(borrowed: {_fmt(cc.get('daily_residual_correlation'))})",
                    fontsize=10)

    @staticmethod
    def plot_not_available(axes, label: str) -> None:
        """Row of a pair without power-law cross-covariance diagnostics (switch off, or that pair's fit
        fell back to the borrowed approximation)"""
        NoiseModelTemplate.plot_not_available(axes, label, message='cross-covariance diagnostics not available')
