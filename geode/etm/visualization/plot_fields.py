"""
Project: Geode
Date: 11/7/25 8:42 AM
Author: Demian D. Gomez

Velocity/displacement field visualization module.
This module provides backward-compatible access to the refactored field_plot package.
"""

import numpy as np
import matplotlib.pyplot as plt
from typing import Optional, Tuple

from .field_plot import FieldVisualizer, FieldPlotConfig
from .field_plot.map_renderer import CartopyRenderer

# Re-export mask_ocean_points for backward compatibility
mask_ocean_points = CartopyRenderer.mask_ocean_points


def plot_velocity_field(
        lon: list,
        lat: list,
        data: list,
        stations_lon: np.ndarray = None,
        stations_lat: np.ndarray = None,
        stations_data: list = None,
        stations_names: list = None,
        on_station_click: Optional[callable] = None,
        available_fields: list = None,
        title: str = "GeoDE Deformation Visualizer",
        cmap: str = 'RdBu_r',
        figsize: Tuple[float, float] = (18, 6),
        dpi: int = 100,
        output_file: str = None,
        coastline_resolution: str = 'i',
        colorbar_extend: str = 'both',
        plot_sigmas: bool = False,
        covar: list = None,
        sparse_lon: np.ndarray = None,
        sparse_lat: np.ndarray = None,
        sparse_data: list = None,
        sparse_names: list = None,
        on_sparse_click: Optional[callable] = None
) -> plt.Figure:
    """
    Plot geodetic velocity or displacement field on a map with 3 subplots (East, North, Up).

    This function is a thin wrapper around FieldVisualizer for backward compatibility.

    Parameters
    ----------
    lon : list of np.ndarray
        Longitude coordinates for each field
    lat : list of np.ndarray
        Latitude coordinates for each field
    data : list of np.ndarray
        Velocity/displacement data list, each with shape (3, n) where:
        - data[i][0, :] = East component
        - data[i][1, :] = North component
        - data[i][2, :] = Up component
    stations_lon : np.ndarray
        Station longitude coordinates
    stations_lat : np.ndarray
        Station latitude coordinates
    stations_data : list of np.ndarray
        Station data for each field, each (3, n_stations)
    stations_names : list
        Station names
    on_station_click : callable, optional
        Callback function when station is clicked.
        Signature: callback(station_index)
    available_fields : list, optional
        Names for each available field
    title : str, default="GeoDE Deformation Visualizer"
        Main title for the figure
    cmap : str, default='RdBu_r'
        Colormap for contours
    figsize : tuple, default=(18, 6)
        Figure size (width, height) in inches
    dpi : int, default=100
        Figure resolution
    output_file : str, optional
        If provided, save figure to this file
    coastline_resolution : str, default='i'
        Map resolution. Legacy Basemap values ('c', 'l', 'i', 'h', 'f') are
        automatically mapped to Cartopy resolutions ('110m', '50m', '10m').
    colorbar_extend : str, default='both'
        Colorbar extension: 'neither', 'both', 'min', 'max'
    plot_sigmas : bool, default=False
        If True, plot uncertainty ellipses instead of vectors
    covar : list of np.ndarray, optional
        Covariance data for uncertainty ellipses

    Returns
    -------
    fig : matplotlib.figure.Figure
        The figure object
    """
    # Map legacy Basemap resolution codes to Cartopy resolutions
    resolution_map = {
        'c': '110m',  # crude -> low resolution
        'l': '110m',  # low -> low resolution
        'i': '50m',   # intermediate -> medium resolution
        'h': '50m',   # high -> medium resolution (10m is very slow)
        'f': '10m',   # full -> high resolution
    }
    cartopy_resolution = resolution_map.get(coastline_resolution, '50m')

    # Create configuration
    config = FieldPlotConfig(
        figsize=figsize,
        dpi=dpi,
        coastline_resolution=cartopy_resolution,
        cmap=cmap,
        colorbar_extend=colorbar_extend,
        plot_sigmas=plot_sigmas,
        output_file=output_file,
        title=title
    )

    # Create visualizer and plot
    visualizer = FieldVisualizer(config)

    return visualizer.plot(
        lon=lon,
        lat=lat,
        data=data,
        stations_lon=stations_lon,
        stations_lat=stations_lat,
        stations_data=stations_data,
        stations_names=stations_names,
        on_station_click=on_station_click,
        available_fields=available_fields,
        covar=covar,
        sparse_lon=sparse_lon,
        sparse_lat=sparse_lat,
        sparse_data=sparse_data,
        sparse_names=sparse_names,
        on_sparse_click=on_sparse_click
    )
