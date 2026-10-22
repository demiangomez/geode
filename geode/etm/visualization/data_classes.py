"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/22/25 8:53 AM
Author: Demian D. Gomez
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from io import BytesIO

from ..core.data_classes import BaseDataClass


@dataclass
class ComponentData:
    """Data for one coordinate component"""
    observations: np.ndarray = field(default_factory=lambda: np.array([]))
    observations_fit: np.ndarray = field(default_factory=lambda: np.array([]))
    observations_not_fit: np.ndarray = field(default_factory=lambda: np.array([]))
    time_vector: np.ndarray = field(default_factory=lambda: np.array([]))
    time_vector_fit: np.ndarray = field(default_factory=lambda: np.array([]))
    time_vector_not_fit: np.ndarray = field(default_factory=lambda: np.array([]))
    mask: np.ndarray = field(default_factory=lambda: np.array([]))
    model_values: Optional[np.ndarray] = None
    model_time_vector: Optional[np.ndarray] = None
    confidence_bounds: Optional[Tuple[np.ndarray, np.ndarray]] = None
    residuals: Optional[np.ndarray] = None
    residuals_not_fit: Optional[np.ndarray] = None
    outlier_flags: Optional[np.ndarray] = None
    time_range: Optional[Tuple[float, float]] = None


@dataclass
class TimeSeriesPlotData:
    """Container for time series plot data"""
    station_id: str
    solution_type: str
    stack_name: str
    completion: float
    latitude: float
    longitude: float

    # Coordinate data
    north_data: ComponentData
    east_data: ComponentData
    up_data: ComponentData

    missing_solutions: np.ndarray = field(default_factory=lambda: np.array([]))

    # ETM results
    has_etm_results: bool = False
    parameter_summary: Optional[str] = None
    periodic_summary: Optional[str] = None
    wrms_values: Optional[Tuple[float, float, float]] = None

    # Jump information
    jumps: Optional[List] = None
    jump_tables: Optional[Tuple[list, list, list]] = None
    auto_jumps: Optional[List] = None
    show_auto_jumps: bool = False

    covariance_matrix: Optional[np.ndarray] = None

    def get_component_data(self, index: int) -> ComponentData:
        """Get data for component by index (0=N, 1=E, 2=U)"""
        return [self.north_data, self.east_data, self.up_data][index]


@dataclass
class NoiseModelPlotData:
    """Container for the noise model diagnostic plot (NoiseCovarianceGLS)"""
    station_id: str
    solution_type: str
    stack_name: str
    completion: float
    latitude: float
    longitude: float

    # noise_model report of each component (N, E, U); empty dict if not available
    noise_models: List[dict] = field(default_factory=list)


@dataclass
class PlotOutputConfig(BaseDataClass):
    """Configuration for plot output"""
    filename: Optional[str] = None
    file_io: Optional[BytesIO] = None
    format: str = 'png'
    save_kwargs: Dict[str, Any] = None

    # Plot configuration
    plot_show_outliers: bool = False
    plot_residuals_mode: bool = False
    plot_time_window: Optional[Tuple[float, float]] = None
    plot_remove_jumps: bool = False
    plot_remove_polynomial: bool = False
    plot_remove_periodic: bool = False
    plot_remove_stochastic: bool = False
    plot_no_model: bool = False

    # Missing data
    plot_missing_solutions: bool = False
    interactive: bool = False