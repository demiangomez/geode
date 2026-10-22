"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/22/25 8:38 AM
Author: Demian D. Gomez
"""

import numpy as np
import warnings
# Suppress the specific warning
warnings.filterwarnings('ignore', message='Starting a Matplotlib GUI outside')

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from typing import List, Tuple, Dict, Any, Union
from matplotlib.axes import Axes
from matplotlib.figure import Figure


# app
from ..visualization.plot_template import PlotTemplate
from ..visualization.data_classes import TimeSeriesPlotData, PlotOutputConfig, ComponentData
from ..core.type_declarations import AdjustmentModels, JumpType
from ..etm_functions.jumps import JumpFunction


class TimeSeriesTemplate(PlotTemplate):
    """Template for time series plots"""

    def determine_layout(self, plot_data: TimeSeriesPlotData,
                         output_config: PlotOutputConfig) -> Dict[str, Any]:
        """Determine plot layout based on data and configuration"""
        show_outliers = (output_config and output_config.plot_show_outliers and
                         plot_data.has_etm_results)

        if show_outliers:
            fig_size = (16, 10)
            subplot_config = {'nrows': 3, 'ncols': 2, 'sharex': True}
            main_axes_indices = [0, 2, 4]
            outlier_axes_indices = [1, 3, 5]
        else:
            fig_size = (16, 10)
            subplot_config = {'nrows': 3, 'ncols': 1, 'sharex': True}
            main_axes_indices = [0, 1, 2]
            outlier_axes_indices = None

            if output_config.plot_missing_solutions:
                import logging
                logging.getLogger(__name__).info(
                    'Missing solutions requested but the outlier panel was not requested')

        return {
            'fig_size': fig_size,
            'subplot_config': subplot_config,
            'main_axes_indices': main_axes_indices,
            'outlier_axes_indices': outlier_axes_indices,
            'show_outliers': show_outliers
        }

    def create_figure_layout(self, layout: Dict[str, Any]) -> Tuple[plt.Figure, List]:
        """Create time series figure layout"""
        fig, axes = plt.subplots(figsize=layout['fig_size'], **layout['subplot_config'])

        # Handle single vs multiple axis cases
        if not isinstance(axes, np.ndarray):
            axes = [axes]
        elif axes.ndim == 2:
            # Flatten 2D array for easier indexing
            axes = axes.flatten()

        return fig, axes

    def generate_title(self, plot_data: TimeSeriesPlotData, output_config: PlotOutputConfig) -> str:
        """Generate comprehensive time series title"""
        base_title = super().generate_title(plot_data, output_config)

        if plot_data.has_etm_results and plot_data.parameter_summary:
            title_parts = [base_title, plot_data.parameter_summary]

            if plot_data.periodic_summary:
                title_parts.append(plot_data.periodic_summary)

            if plot_data.wrms_values:
                wrms_str = (f"NEU wrms [mm]: {plot_data.wrms_values[0]:.2f} "
                            f"{plot_data.wrms_values[1]:.2f} {plot_data.wrms_values[2]:.2f}")
                title_parts.append(wrms_str)

            # Add processing flags
            flags = []
            if output_config.plot_remove_polynomial:
                flags.append(self.config.get_label('polynomial'))
            if output_config.plot_remove_periodic:
                flags.append(self.config.get_label('seasonal'))
            if output_config.plot_remove_jumps:
                flags.append(self.config.get_label('jumps'))
            if (output_config.plot_remove_stochastic
                    and self.config.modeling.least_squares_strategy.adjustment_model
                    in (AdjustmentModels.LSQ_COLLOCATION, AdjustmentModels.NOISE_COVARIANCE_GLS)):
                flags.append(self.config.get_label('stochastic'))

            for prefit in self.config.modeling.prefit_models:
                flags.append(self.config.get_label('prefit') + ' ' + prefit.short_name())

            if len(flags):
                flags_str = ' - ' + self.config.get_label('removed') + ': ' + ', '.join(flags)
            else:
                flags_str = ''

            return '\n'.join(title_parts) + flags_str

        elif not plot_data.has_etm_results:
            return base_title + '\n' + self.config.get_label('not_enough')

        return base_title

    def plot_observations(self, ax, data: ComponentData) -> None:
        """Plot coordinate observations"""
        if data.outlier_flags is not None:
            # Plot good observations
            good_mask = data.outlier_flags
            ax.plot(data.time_vector_fit[good_mask], data.observations_fit[good_mask],
                    color=self.colors['observations'], **self.styles['observations'])
        else:
            ax.plot(data.time_vector, data.observations,
                    color=self.colors['observations'], **self.styles['observations'])

        if data.observations_not_fit is not None:
            ax.plot(data.time_vector_not_fit, data.observations_not_fit,
                    color=self.colors['observations_not_fit'], **self.styles['observations'])


    def plot_all_observations(self, ax, data: ComponentData) -> None:
        """Plot all observations including outliers"""
        if data.outlier_flags is not None:
            good_mask = data.outlier_flags
            outlier_mask = ~good_mask

            # Plot outliers first (so they appear behind good points)
            if np.any(outlier_mask):
                ax.plot(data.time_vector_fit[outlier_mask], data.observations_fit[outlier_mask],
                        color=self.colors['outliers'], **self.styles['observations'])

            # Plot good observations
            ax.plot(data.time_vector_fit[good_mask], data.observations_fit[good_mask],
                    color=self.colors['observations'], **self.styles['observations'])
        else:
            ax.plot(data.time_vector, data.observations,
                    color=self.colors['observations'], **self.styles['observations'])

        if data.observations_not_fit is not None:
            ax.plot(data.time_vector_not_fit, data.observations_not_fit,
                    color=self.colors['observations_not_fit'], **self.styles['observations'])

    def plot_model(self, ax, data: ComponentData) -> None:
        """Plot ETM model"""
        if data.model_values is not None:
            ax.plot(data.model_time_vector, data.model_values,
                    color=self.colors['model'], **self.styles['model'])

    def plot_confidence_bounds(self, ax, data: ComponentData) -> None:
        """Plot confidence bounds"""
        if data.confidence_bounds is not None:
            lower_bound, upper_bound = data.confidence_bounds
            ax.plot(data.model_time_vector, lower_bound, 'b', alpha=0.1)
            ax.plot(data.model_time_vector, upper_bound, 'b', alpha=0.1)
            ax.fill_between(data.model_time_vector, lower_bound, upper_bound,
                            color=self.colors['confidence'], **self.styles['confidence'])

    def plot_residuals(self, ax, data: ComponentData) -> None:
        """Plot residuals"""
        if data.residuals is not None:
            if data.outlier_flags is not None:
                good_mask = data.outlier_flags
                ax.plot(data.time_vector_fit[good_mask], data.residuals[good_mask],
                        color=self.colors['observations'], **self.styles['observations'])
            else:
                ax.plot(data.time_vector, data.residuals,
                        color=self.colors['observations'], **self.styles['observations'])

            if np.any(~data.mask):
                ax.plot(data.time_vector_not_fit, data.residuals_not_fit,
                        color=self.colors['observations_not_fit'], **self.styles['observations'])

    def plot_jumps(self, ax, jumps: List, time_range: Tuple[float, float]) -> None:
        """Plot jump markers"""
        for jump in jumps:
            if time_range[0] <= jump.date.fyear <= time_range[1]:
                if jump.p.jump_type == JumpType.AUTO_DETECTED:
                    ax.axvline(np.array([jump.date.fyear]), color=jump.p.jump_type.color if jump.fit else 'gray',
                               **self.styles['auto_jumps'])
                else:
                    ax.axvline(np.array([jump.date.fyear]), color=jump.p.jump_type.color if jump.fit else 'gray',
                               **self.styles['jumps'])

    def plot_missing_solutions(self, ax, missing_solutions: List) -> None:
        """Plot missing solution markers"""
        for missing_time in missing_solutions:
            ax.axvline(missing_time, color=self.colors['missing_solution'],
                       alpha=0.2, linewidth=1)

    def add_grid_and_labels(self, ax, component: str) -> None:
        """Add grid and component labels"""
        ax.grid(True)
        ax.set_ylabel(f"{self.config.get_label(component.lower())} [mm]")

    def add_jump_tables(self, fig: Figure, axes: List[Axes],
                        jump_tables: Tuple[list, list, list],
                        jumps: List[JumpFunction]) -> None:
        """Add jump parameter tables to figure"""
        # This is handled in add_component_annotations for time series
        MAX_ITEMS = 20
        for i, table in enumerate(jump_tables):
            initial_pos = axes[i].get_position().y0
            axes_bbox = axes[i].get_window_extent()

            for j, item in enumerate(reversed(table)):
                line, color = item
                if jumps[len(jumps) - j - 1].constrained[i]:
                    bbox = dict(
                        edgecolor='black',  # Border color
                        facecolor='none',  # Transparent background
                        boxstyle='round,pad=0.0',  # Rounded corners with padding
                        linewidth=0.25  # Border width
                    )
                else:
                    bbox = None  # No box at all

                if j < MAX_ITEMS:
                    text = fig.text(0.0025, initial_pos, line, color=color, fontsize=8,
                                    family='monospace', bbox=bbox)
                else:
                    text = fig.text(0.0025, initial_pos, self.config.get_label('table_too_long'), color='black',
                                    fontsize=8, family='monospace')
                ex = text.get_window_extent()
                initial_pos += ex.height / axes_bbox.height / 3.75
                if j >= MAX_ITEMS:
                    break
            # draw title
            fig.text(0.0025, initial_pos, self.config.get_label('table_title'), color='black',
                     fontsize=8, family='monospace')

    def apply_time_window(self, ax,
                          time_window: Union[Tuple[float, float], int, float]) -> None:
        """Apply time window to axis"""

        if type(time_window) == float or type(time_window) == int:
            time_window = ((self.config.metadata.last_obs - int(time_window)).fyear,
                           self.config.metadata.last_obs.fyear)

        if len(time_window) < 2:
            time_window = (time_window[0], self.config.metadata.last_obs.fyear)

        if time_window[0] < self.config.metadata.first_obs.fyear:
            time_window = (self.config.metadata.first_obs.fyear, time_window[1])

        ax.set_xlim(time_window)
        # Auto-scale y-axis for the visible data
        self._autoscale_y_axis(ax)

    def _autoscale_y_axis(self, ax, margin: float = 0.1) -> None:
        """This function rescales the y-axis based on the data that is visible given the current xlim of the axis.
        ax -- a matplotlib axes object
        margin -- the fraction of the total height of the y-data to pad the upper and lower ylims"""

        def get_bottom_top(line):
            xd = line.get_xdata()
            yd = line.get_ydata()
            lo, hi = ax.get_xlim()
            if np.any(((xd > lo) & (xd < hi))):
                y_displayed = yd[((xd > lo) & (xd < hi))]
                h = np.max(y_displayed) - np.min(y_displayed)
                bot = np.min(y_displayed) - margin * h
                top = np.max(y_displayed) + margin * h
                return bot, top
            else:
                return np.inf, -np.inf

        lines = ax.get_lines()
        bot, top = np.inf, -np.inf

        for line in lines:
            new_bot, new_top = get_bottom_top(line)
            if new_bot < bot:
                bot = new_bot
            if new_top > top:
                top = new_top

        if bot == top:
            ax.autoscale(enable=True, axis='y', tight=False)
        else:
            ax.set_ylim(bot, top)