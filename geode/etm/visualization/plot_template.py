"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/22/25 8:53 AM
Author: Demian D. Gomez

Base class for plot templates defining the common interface and shared functionality.
"""

from abc import ABC, abstractmethod
from typing import Dict, Tuple, Any
import matplotlib.pyplot as plt
import logging

logger = logging.getLogger(__name__)

from ..core.etm_config import EtmConfig
from ..core.type_declarations import JumpType
from ..visualization.data_classes import TimeSeriesPlotData, PlotOutputConfig


class PlotTemplate(ABC):
    """Abstract base class for plot templates"""

    def __init__(self, config: EtmConfig):
        self.config = config
        self.colors = self.setup_colors()
        self.styles = self.setup_styles()

        logger.debug(f'PlotTemplate: {str(self.config.plotting_config)}')

    @staticmethod
    def setup_colors() -> Dict[str, Any]:
        """Setup color scheme"""
        return {
            'observations': (0, 150 / 255, 235 / 255),
            'model': 'red',
            'confidence': 'lightblue',
            'outliers': 'cyan',
            'observations_not_fit': 'darkgray',
            'mechanical_jump': JumpType.MECHANICAL_MANUAL.color,
            'antenna_jump': JumpType.MECHANICAL_ANTENNA.color,
            'frame_jump': JumpType.REFERENCE_FRAME.color,
            'coseismic_jump': JumpType.COSEISMIC_ONLY.color,
            'coseismic_decay': JumpType.COSEISMIC_JUMP_DECAY.color,
            'postseismic': JumpType.POSTSEISMIC_ONLY.color,
            'auto_jump': JumpType.AUTO_DETECTED.color,
            'missing_solution': 'magenta'
        }

    @staticmethod
    def setup_styles() -> Dict[str, Dict[str, Any]]:
        """Setup plot styles"""
        return {
            'observations': {'marker': 'o', 'markersize': 2, 'linestyle': 'none'},
            'model': {'linewidth': 1.5},
            'confidence': {'alpha': 0.2},
            'jumps': {'linestyle': ':', 'linewidth': 1.75},
            'auto_jumps': {'linestyle': '-.', 'linewidth': 1}
        }

    @abstractmethod
    def create_figure_layout(self, *args, **kwargs) -> Tuple[plt.Figure, Any]:
        """Create figure and axis layout"""
        pass

    def generate_title(self, plot_data: TimeSeriesPlotData, output_config: PlotOutputConfig) -> str:
        """Generate plot title"""
        base_title = (f"{plot_data.station_id} ({plot_data.stack_name.upper()} {plot_data.completion:.2f}%/{plot_data.span_completion:.2f}% - "
                      f"{self.config.modeling.least_squares_strategy.adjustment_model.description}) "
                      f"lat: {plot_data.latitude:.5f} lon: {plot_data.longitude:.5f}")
        return base_title
