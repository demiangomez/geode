"""
Data classes for field plot visualization.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Tuple, Optional, List, Callable


@dataclass
class ViewState:
    """
    Stores the current view (zoom/pan) state of a matplotlib axis.
    Used to preserve view when switching between fields.
    """
    xlim: Optional[Tuple[float, float]] = None
    ylim: Optional[Tuple[float, float]] = None

    def capture(self, ax) -> None:
        """Capture the current view state from an axis."""
        self.xlim = ax.get_xlim()
        self.ylim = ax.get_ylim()

    def restore(self, ax) -> None:
        """Restore the saved view state to an axis."""
        if self.xlim is not None:
            ax.set_xlim(self.xlim)
        if self.ylim is not None:
            ax.set_ylim(self.ylim)

    def is_captured(self) -> bool:
        """Check if a view state has been captured."""
        return self.xlim is not None and self.ylim is not None


@dataclass
class FieldPlotData:
    """
    Container for all data needed to plot a velocity/displacement field.
    """
    # Grid coordinates (for interpolated field)
    grid_lon: np.ndarray
    grid_lat: np.ndarray

    # Field data (3, n) array: East, North, Up components
    field_data: np.ndarray

    # Station data
    stations_lon: np.ndarray
    stations_lat: np.ndarray
    stations_data: np.ndarray  # (3, n_stations) array
    stations_names: List[str]

    # Optional covariance for uncertainty ellipses
    covariance: Optional[np.ndarray] = None

    # Field metadata
    field_name: str = ""
    field_index: int = 0


@dataclass
class FieldPlotConfig:
    """
    Configuration for field plot visualization.
    """
    # Figure settings
    figsize: Tuple[float, float] = (18, 6)
    dpi: int = 100

    # Map settings
    coastline_resolution: str = '50m'  # Cartopy resolution: '10m', '50m', '110m'
    land_color: str = 'wheat'
    ocean_color: str = 'lightblue'
    land_alpha: float = 0.3

    # Colormap settings
    cmap: str = 'RdBu_r'
    colorbar_extend: str = 'both'
    n_contour_levels: int = 21

    # Vector settings
    initial_scale_factor: float = 10.0
    vector_color: str = 'white'
    vector_width: float = 0.003
    vector_alpha: float = 0.6
    vector_edge_color: str = 'black'
    vector_edge_width: float = 0.25

    # Station marker settings
    station_marker: str = '^'
    station_color: str = 'red'
    station_size: int = 20
    station_alpha: float = 0.6

    # Plot uncertainty ellipses instead of vectors
    plot_sigmas: bool = False

    # Grid interpolation
    grid_spacing_m: float = 20000  # Grid spacing in meters

    # Output
    output_file: Optional[str] = None

    # Title
    title: str = "GeoDE Deformation Visualizer"


@dataclass
class PlotState:
    """
    Mutable state for the plot, used for updating artists.
    """
    # Artists per subplot (indexed 0=East, 1=North, 2=Up)
    contours: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    quivers_grid: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    quivers_stn: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    quiver_keys: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    colorbars: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    ellipses: List[List[object]] = field(default_factory=lambda: [[], [], []])
    scatter_artists: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    scatter_sparse: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    quivers_sparse: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    contour_lines: List[Optional[object]] = field(default_factory=lambda: [None, None, None])
    contour_labels: List[List[object]] = field(default_factory=lambda: [[], [], []])

    # View states for each subplot
    view_states: List[ViewState] = field(default_factory=lambda: [ViewState(), ViewState(), ViewState()])

    # Current data
    scale_factor: float = 10.0
    reference_vector: float = 1.0
    current_field_index: int = 0

    # Processed grid data (cached after process_data)
    xi_grid: Optional[np.ndarray] = None
    yi_grid: Optional[np.ndarray] = None
    ve_grid: Optional[np.ndarray] = None
    vn_grid: Optional[np.ndarray] = None
    vu_grid: Optional[np.ndarray] = None
    cov_grid: Optional[np.ndarray] = None

    # Station projected coordinates
    x_stn: Optional[np.ndarray] = None
    y_stn: Optional[np.ndarray] = None
    ve_stn: Optional[np.ndarray] = None
    vn_stn: Optional[np.ndarray] = None

    # Sparse station projected coordinates
    x_sparse: Optional[np.ndarray] = None
    y_sparse: Optional[np.ndarray] = None
    ve_sparse: Optional[np.ndarray] = None
    vn_sparse: Optional[np.ndarray] = None
