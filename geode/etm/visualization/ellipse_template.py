"""
Project: Geodetic Database Engine (GeoDE)
Date: 11/21/26
Author: Demian D. Gomez

Template for the velocity error-ellipse plot: one 1-sigma ellipse per component pair (N-E, N-U, E-U)
from the borrowed-residual-correlation approximation (always available once the ETM is fit with
NOISE_COVARIANCE_GLS) and, when -xcov was used and the fit succeeded, a second ellipse from the
power-law cross-spectrum model. Ellipses are centered at the origin (shape/orientation of the
uncertainty only, not the actual rate vector), matching cross_component_covariance.pdf's own Figure 8.
"""

import matplotlib.pyplot as plt
import numpy as np
from typing import Dict, Optional, Tuple
from matplotlib.patches import Ellipse

from ..visualization.plot_template import PlotTemplate
from ..visualization.data_classes import EllipsePlotData, PlotOutputConfig

PAIR_LABELS = {'NE': ('north', 'east'), 'NU': ('north', 'up'), 'EU': ('east', 'up')}


def _ellipse_from_covariance(cov: np.ndarray, n_sigma: float = 1.) -> Tuple[float, float, float]:
    """width, height (full axis lengths, same units as cov's sqrt) and rotation angle [deg] of the
    n_sigma error ellipse of a 2x2 covariance matrix (eigh of cov, angle of the major axis)"""
    eigenvals, eigenvecs = np.linalg.eigh(cov)
    order = eigenvals.argsort()[::-1]
    eigenvals, eigenvecs = eigenvals[order], eigenvecs[:, order]
    angle = np.degrees(np.arctan2(*eigenvecs[:, 0][::-1]))
    width = 2 * np.sqrt(max(eigenvals[0], 0.)) * n_sigma
    height = 2 * np.sqrt(max(eigenvals[1], 0.)) * n_sigma
    return width, height, angle


class EllipseTemplate(PlotTemplate):
    """Template for the velocity error-ellipse plot"""

    @staticmethod
    def setup_colors():
        colors = PlotTemplate.setup_colors()
        colors.update({'borrowed': (0., 0.447, 0.741), 'power_law': (0.85, 0.325, 0.098)})
        return colors

    def create_figure_layout(self) -> Tuple[plt.Figure, np.ndarray]:
        """1 row x 3 columns: N-E, N-U, E-U"""
        fig, axes = plt.subplots(nrows=1, ncols=3, figsize=(16, 6))
        fig.subplots_adjust(left=0.07, right=0.98, top=0.80, bottom=0.12, wspace=0.3)
        return fig, axes

    def generate_title(self, plot_data: EllipsePlotData, output_config: PlotOutputConfig) -> str:
        base_title = super().generate_title(plot_data, output_config)
        return base_title + '\nVelocity error ellipses (1-sigma, centered at the origin)'

    def plot_pair(self, ax, pair: str, data: Optional[Dict[str, Optional[np.ndarray]]]) -> None:
        """One panel: the borrowed ellipse always, the power-law one when available"""
        comp_i, comp_j = PAIR_LABELS[pair]
        label_i, label_j = self.config.get_label(comp_i), self.config.get_label(comp_j)

        if data is None or data.get('borrowed') is None:
            ax.text(0.5, 0.5, f'{label_i}-{label_j}: not available', transform=ax.transAxes,
                   ha='center', va='center', fontsize=10, color='gray')
            ax.set_xticks([])
            ax.set_yticks([])
            return

        extents = []
        for method, linestyle in (('borrowed', '--'), ('power_law', '-')):
            cov = data.get(method)
            if cov is None:
                continue
            width, height, angle = _ellipse_from_covariance(cov)
            label = 'borrowed residual correlation' if method == 'borrowed' else 'power-law cross-spectrum'
            ellipse = Ellipse((0, 0), width, height, angle=angle, facecolor='none',
                              edgecolor=self.colors[method], linestyle=linestyle, linewidth=2,
                              zorder=3, label=label)
            ax.add_patch(ellipse)
            extents.append(max(width, height) / 2)

        lim = max(extents) * 1.2 if extents else 1.
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.axhline(0, color='k', linewidth=0.5, alpha=0.5)
        ax.axvline(0, color='k', linewidth=0.5, alpha=0.5)
        ax.grid(True, alpha=0.4)
        ax.set_aspect('equal')
        ax.set_xlabel(f'{label_i} rate [mm/yr]')
        ax.set_ylabel(f'{label_j} rate [mm/yr]')
        ax.set_title(f"{label_i}-{label_j}")
        ax.legend(fontsize=8, loc='upper right')
