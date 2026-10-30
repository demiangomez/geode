import warnings
# Suppress the specific warning
warnings.filterwarnings('ignore', message='Starting a Matplotlib GUI outside')

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.widgets import Button
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import base64
from typing import Optional
import logging
import os
from pathlib import Path

# app
from ..core.etm_config import EtmConfig

# Pre-shrunk variant of geode_logo.png (used at full resolution by other
# reports, e.g. station_report.py/station_kmz.py/campaign_planner/report.py):
# the ETM plot only displays this at ~200px wide, and resampling the full
# 2373x1156 source with lanczos interpolation on every savefig() cost ~1s.
LOGO_PATH = Path(__file__).parent / 'geode_logo_small.png'
from ..visualization.time_series_template import TimeSeriesTemplate
from ..visualization.histogram_template import HistogramTemplate
from ..visualization.noise_model_template import NoiseModelTemplate
from ..visualization.data_classes import TimeSeriesPlotData, PlotOutputConfig, NoiseModelPlotData

logger = logging.getLogger(__name__)


class EtmPlotter:
    """Main plotting class for ETM visualizations"""

    def __init__(self, config: EtmConfig):
        self.config = config
        self.time_series_template = TimeSeriesTemplate(config)
        self.histogram_template = HistogramTemplate(config)
        self.noise_model_template = NoiseModelTemplate(config)

        # Interactive plotting state
        self.figure = None
        self.picking_enabled = False
        self.pick_callback_id = None

    def plot_time_series(self, plot_data: TimeSeriesPlotData,
                         output_config: Optional[PlotOutputConfig] = None) -> Optional[str]:
        """
        Create time series plot

        Args:
            plot_data: Data container with all plotting information
            output_config: Output configuration (file, format, etc.)

        Returns:
            Base64 encoded image string if output to BytesIO, None otherwise
        """
        # if no specific output_config passed, replaced with default
        if output_config is None:
            output_config = self.config.plotting_config

        template = self.time_series_template

        # Prepare plot layout
        layout = template.determine_layout(plot_data, output_config)

        # Create figure and axes
        fig, axes = template.create_figure_layout(layout)
        self.figure = fig

        # Generate title
        title = template.generate_title(plot_data, output_config)
        fig.suptitle(title, fontsize=9, family='monospace')

        main_axes = axes[layout['main_axes_indices']]
        # Add jump parameter tables
        if plot_data.jump_tables:
            template.add_jump_tables(fig, main_axes, plot_data.jump_tables, plot_data.jumps)

        # Plot data for each component
        for i, (ax, component) in enumerate(zip(main_axes, ['North', 'East', 'Up'])):
            self._plot_component_data(ax, plot_data, component, i, output_config)

        fig.subplots_adjust(left=0.21)

        # Add outlier plots if requested
        if layout['show_outliers']:
            outlier_axes = axes[layout['outlier_axes_indices']]
            for i, ax in enumerate(outlier_axes):
                self._plot_outlier_data(ax, plot_data, i, output_config)

        # Handle output
        return self._handle_plot_output(fig, output_config)

    def plot_histogram(self, plot_data: TimeSeriesPlotData,
                       output_config: Optional['PlotOutputConfig'] = None) -> Optional[str]:
        """Create histogram/residual analysis plot"""
        # if no specific output_config passed, replaced with default
        if output_config is None:
            output_config = self.config.plotting_config

        template = self.histogram_template

        # Create figure layout
        fig, axes = template.create_figure_layout()

        # Generate title
        title = template.generate_title(plot_data, output_config)
        fig.suptitle(title, fontsize=9, family='monospace')

        # Plot N-E scatter with error ellipse
        xlim, ylim = template.plot_ne_scatter(fig, axes['ne_scatter'], plot_data)

        # Plot component histograms
        template.plot_component_histograms(axes, plot_data, xlim, ylim)

        axes['ne_scatter'].set_xlim(xlim)
        axes['ne_scatter'].set_ylim(ylim)

        return self._handle_plot_output(fig, output_config, histogram=True)

    def plot_noise_model(self, plot_data: NoiseModelPlotData,
                         output_config: Optional[PlotOutputConfig] = None) -> Optional[str]:
        """
        Create the noise model diagnostic plot: one row per component with the empirical covariance and its
        fits (left) and the scan used to detect the periodic terms (right)
        """
        # if no specific output_config passed, replaced with default
        if output_config is None:
            output_config = self.config.plotting_config

        template = self.noise_model_template

        fig, axes = template.create_figure_layout()

        title = template.generate_title(plot_data, output_config)
        fig.suptitle(title, fontsize=9, family='monospace')

        for i, component in enumerate(['north', 'east', 'up']):
            label = self.config.get_label(component)
            noise_model = plot_data.noise_models[i]
            if noise_model and 'diagnostics' in noise_model:
                template.plot_covariance_fit(axes[i, 0], noise_model, label)
                template.plot_detection(axes[i, 1], noise_model, label)
            else:
                template.plot_not_available(axes[i], label)

        return self._handle_plot_output(fig, output_config, suffix='_noise')

    def _plot_component_data(self, ax, plot_data: TimeSeriesPlotData,
                             component: str, component_idx: int,
                             output_config: PlotOutputConfig) -> None:
        """Plot data for one coordinate component"""
        template = self.time_series_template
        data = plot_data.get_component_data(component_idx)

        # Plot observations
        if output_config.plot_residuals_mode:
            template.plot_residuals(ax, data)
        else:
            template.plot_observations(ax, data)

            # Plot model if available and requested
            if plot_data.has_etm_results and not self.config.plotting_config.plot_no_model:
                template.plot_model(ax, data)
                template.plot_confidence_bounds(ax, data)

        # Add grid and labels
        template.add_grid_and_labels(ax, component)

        # Apply time window if specified
        if output_config.plot_time_window:
            template.apply_time_window(ax, output_config.plot_time_window)

        # Plot jumps
        if plot_data.jumps:
            template.plot_jumps(ax, plot_data.jumps, data.time_range)

    def _plot_outlier_data(self, ax, plot_data: TimeSeriesPlotData,
                           component_idx: int,
                           output_config: PlotOutputConfig) -> None:
        """Plot outlier comparison data"""
        template = self.time_series_template
        data = plot_data.get_component_data(component_idx)

        # activate grid
        ax.grid(True)

        # Plot all data (including outliers)
        template.plot_all_observations(ax, data)
        template.plot_model(ax, data)
        template.plot_confidence_bounds(ax, data)

        # Plot missing solutions if available
        if output_config.plot_missing_solutions:
            template.plot_missing_solutions(ax, plot_data.missing_solutions)

        # Apply time window
        if output_config.plot_time_window:
            template.apply_time_window(ax, output_config.plot_time_window)

    def _add_logo(self, fig, width: float = 0.12) -> None:
        """Add GeoDE logo to bottom right corner of figure

        Args:
            fig: Matplotlib figure
            width: Logo width as fraction of figure width (default 0.12 = 12%)
        """
        if not LOGO_PATH.exists():
            logger.warning(f'Logo file not found: {LOGO_PATH}')
            return

        try:
            logo_img = mpimg.imread(LOGO_PATH)
            # Calculate height to preserve aspect ratio
            aspect_ratio = logo_img.shape[1] / logo_img.shape[0]  # width / height
            height = width / aspect_ratio
            # Position in bottom right corner: [left, bottom, width, height]
            left = 1.0 - width - 0.01  # 1% margin from right edge
            bottom = 0.01  # 1% margin from bottom
            logo_ax = fig.add_axes((left, bottom, width, height))
            # Use high-quality interpolation for better rendering when scaled down
            logo_ax.imshow(logo_img, interpolation='lanczos', resample=True)
            logo_ax.axis('off')
        except Exception as e:
            logger.warning(f'Failed to add logo: {e}')

    def _handle_plot_output(self, fig,
                            output_config: Optional[PlotOutputConfig],
                            histogram: bool = False,
                            suffix: str = '') -> Optional[str]:
        """Handle plot output based on configuration. suffix is appended to the file name"""
        # Add logo to figure
        self._add_logo(fig)

        if histogram:
            suffix = '_hist'

        if output_config.filename:
            if not os.path.basename(output_config.filename):
                filename = (os.path.join(output_config.filename,
                                                      self.config.build_filename()) + suffix
                                          + '.' + output_config.format)
            else:
                dirs = os.path.dirname(output_config.filename)
                # split at the format if provided
                file = os.path.basename(output_config.filename).split('.' + output_config.format)
                filename = (os.path.join(dirs, file[0]) + suffix
                                          + '.' + output_config.format)
        else:
            filename = output_config.filename

        if not output_config.save_kwargs:
            output_config.save_kwargs = {}

        if output_config.interactive:
            # Interactive mode
            self._setup_interactive_mode(fig)
            plt.show()
            return None

        elif filename:
            # Save to file
            plt.savefig(filename, **output_config.save_kwargs)
            plt.close()
            return None

        elif output_config.file_io:
            # Save to BytesIO
            plt.savefig(output_config.file_io, format=output_config.format,
                        **output_config.save_kwargs)
            output_config.file_io.seek(0)
            plt.close()
            return base64.b64encode(output_config.file_io.getvalue()).decode()

        else:
            # Default interactive
            plt.show()
            plt.close()
            return None

    def _setup_interactive_mode(self, fig) -> None:
        """Setup interactive mode with jump picking capability"""
        self.figure = fig

        # Add jump picking button
        ax_button = plt.axes((0.85, 0.01, 0.08, 0.055))
        button = Button(ax_button, 'Add Jump', color='red', hovercolor='green')
        button.on_clicked(self._toggle_jump_picking)

    def _toggle_jump_picking(self, event) -> None:
        """Toggle jump picking mode"""
        if not self.picking_enabled:
            logger.info('Entering jump picking mode')
            self.picking_enabled = True
            self.pick_callback_id = self.figure.canvas.mpl_connect(
                'button_press_event', self._on_pick_jump
            )
        else:
            logger.info('Disabling jump picking mode')
            self.picking_enabled = False
            if self.pick_callback_id:
                self.figure.canvas.mpl_disconnect(self.pick_callback_id)

    def _on_pick_jump(self, event) -> None:
        """Handle jump picking event"""
        if event.xdata is None:
            return

        from geode import pyDate
        picked_date = pyDate.Date(fyear=event.xdata)
        logger.info(f'Picked potential jump at: {picked_date.yyyyddd()}')

        # TODO: Integrate with jump management system
        # For now, just log the event
        print(f'Jump picked at epoch: {event.xdata:.3f} ({picked_date.yyyyddd()})')
