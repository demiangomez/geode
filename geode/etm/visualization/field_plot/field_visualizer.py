"""
Main field visualizer orchestrator.
"""

import numpy as np
import warnings

warnings.filterwarnings('ignore', message='Starting a Matplotlib GUI outside')

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse
from matplotlib.gridspec import GridSpec
import cartopy.crs as ccrs
from scipy.interpolate import griddata
from typing import List, Optional, Callable, Tuple

from .data_classes import FieldPlotConfig, PlotState, ViewState
from .map_renderer import CartopyRenderer
from .widget_manager import WidgetManager
from .interaction_handler import InteractionHandler


class FieldVisualizer:
    """
    Main orchestrator for field visualization.

    Handles figure creation, field switching with view preservation,
    and coordinates between renderer, widgets, and interactions.
    """

    def __init__(self, config: Optional[FieldPlotConfig] = None):
        """
        Initialize the field visualizer.

        Parameters
        ----------
        config : FieldPlotConfig, optional
            Configuration for the visualization. Uses defaults if not provided.
        """
        self.config = config or FieldPlotConfig()
        self.renderer = CartopyRenderer(resolution=self.config.coastline_resolution)
        self.state = PlotState()

        self.fig = None
        self.axes = None
        self.widgets = None
        self.interaction = None

        # For linked zoom
        self._syncing_views = False
        self._xlim_cids = []
        self._ylim_cids = []

        # Store original view limits for reset view button
        self._original_xlim = None
        self._original_ylim = None

        # Data storage
        self._lon = None
        self._lat = None
        self._data = None
        self._stations_lon = None
        self._stations_lat = None
        self._stations_data = None
        self._stations_names = None
        self._sparse_lon = None
        self._sparse_lat = None
        self._sparse_data = None
        self._sparse_names = None
        self._covar = None
        self._on_station_click = None
        self._field_names = None

    def plot(
            self,
            lon: List[np.ndarray],
            lat: List[np.ndarray],
            data: List[np.ndarray],
            stations_lon: np.ndarray,
            stations_lat: np.ndarray,
            stations_data: List[np.ndarray],
            stations_names: List[str],
            on_station_click: Optional[Callable[[int], None]] = None,
            available_fields: Optional[List[str]] = None,
            covar: Optional[List[np.ndarray]] = None,
            sparse_lon: np.ndarray = None,
            sparse_lat: np.ndarray = None,
            sparse_data: List[np.ndarray] = None,
            sparse_names: List[str] = None,
            on_sparse_click: Optional[Callable[[int], None]] = None
    ) -> plt.Figure:
        """
        Create the field visualization.

        Parameters
        ----------
        lon : List[np.ndarray]
            Grid longitude coordinates for each field
        lat : List[np.ndarray]
            Grid latitude coordinates for each field
        data : List[np.ndarray]
            Field data arrays, each (3, n) for East, North, Up
        stations_lon, stations_lat : np.ndarray
            Station coordinates
        stations_data : List[np.ndarray]
            Station data for each field, each (3, n_stations)
        stations_names : List[str]
            Station names
        on_station_click : Callable, optional
            Callback when station is clicked
        available_fields : List[str], optional
            Names for each field
        covar : List[np.ndarray], optional
            Covariance data for uncertainty ellipses
        sparse_lon, sparse_lat : np.ndarray, optional
            Sparse station coordinates
        sparse_data : List[np.ndarray], optional
            Sparse station data for each field, each (3, n_sparse)
        sparse_names : List[str], optional
            Sparse station names

        Returns
        -------
        fig : matplotlib.figure.Figure
            The created figure
        """
        plt.ion()

        # Store data
        self._lon = lon
        self._lat = lat
        self._data = data
        self._stations_lon = stations_lon
        self._stations_lat = stations_lat
        self._stations_data = stations_data
        self._stations_names = stations_names
        self._sparse_lon = sparse_lon
        self._sparse_lat = sparse_lat
        self._sparse_data = sparse_data
        self._sparse_names = sparse_names
        self._on_sparse_click = on_sparse_click
        self._covar = covar
        self._on_station_click = on_station_click
        self._field_names = available_fields or [f"Field {i}" for i in range(len(data))]

        # Set up projection using first field (most extensive)
        self.renderer.get_projection(lon[0], lat[0])

        # Create figure with fixed layout
        self._create_figure()

        # Set up the maps
        for ax in self.axes:
            self.renderer.setup(ax, lon[0], lat[0])

        # Transform station coordinates
        self.state.x_stn, self.state.y_stn = self.renderer.transform(stations_lon, stations_lat)

        # Transform sparse station coordinates
        if sparse_lon is not None and len(sparse_lon) > 0:
            self.state.x_sparse, self.state.y_sparse = self.renderer.transform(sparse_lon, sparse_lat)

        # Process and display first field
        self._process_data(0)
        self._draw_all_subplots()

        # Set up widgets
        self._setup_widgets()

        # Set up interaction on first axis only
        self._setup_interaction()

        # Set up linked zoom across all axes
        self._setup_linked_zoom()

        # Store original view limits and initialize navigation stack for Home button
        self._store_original_view()

        # Save if requested
        if self.config.output_file:
            plt.savefig(self.config.output_file, dpi=self.config.dpi, bbox_inches='tight')
            print(f"Figure saved to {self.config.output_file}")

        plt.show()
        return self.fig

    def _create_figure(self) -> None:
        """Create the figure with fixed layout using GridSpec."""
        self.fig = plt.figure(
            figsize=self.config.figsize,
            dpi=self.config.dpi,
            constrained_layout=False
        )

        # Create GridSpec with fixed margins (never use tight_layout)
        gs = GridSpec(
            1, 3,
            figure=self.fig,
            left=0.18,
            right=0.98,
            bottom=0.12,
            top=0.90,
            wspace=0.08
        )

        # Create axes with Cartopy projection
        self.axes = []
        for i in range(3):
            ax = self.fig.add_subplot(gs[0, i], projection=self.renderer.projection)
            # Keep geographic aspect ratio but adjust data limits instead of box size
            # This prevents axis resizing on zoom while maintaining map proportions
            ax.set_adjustable('datalim')
            self.axes.append(ax)

        self.fig.suptitle(self.config.title, fontsize=16, fontweight='bold')

    def _process_data(self, field_index: int) -> None:
        """
        Process field data for display.

        Parameters
        ----------
        field_index : int
            Index of the field to process
        """
        lon = self._lon[field_index]
        lat = self._lat[field_index]
        data = self._data[field_index]

        # Transform coordinates to projection
        x, y = self.renderer.transform(lon, lat)

        # Extract components (convert to mm)
        ve = data[0, :] * 1000.0  # East
        vn = data[1, :] * 1000.0  # North
        vu = data[2, :] * 1000.0  # Up

        # Station data
        stn_data = self._stations_data[field_index]
        self.state.ve_stn = stn_data[0, :] * 1000.0
        self.state.vn_stn = stn_data[1, :] * 1000.0

        # Sparse station data
        if self._sparse_data is not None and len(self._sparse_data) > field_index:
            sp_data = self._sparse_data[field_index]
            self.state.ve_sparse = sp_data[0, :] * 1000.0
            self.state.vn_sparse = sp_data[1, :] * 1000.0

        # Create interpolation grid
        grid_spacing = self.config.grid_spacing_m
        xi = np.linspace(x.min(), x.max(), int((x.max() - x.min()) / grid_spacing))
        yi = np.linspace(y.min(), y.max(), int((y.max() - y.min()) / grid_spacing))
        xi_grid, yi_grid = np.meshgrid(xi, yi)

        # Interpolate fields
        valid = ~np.isnan(ve)
        ve_grid = griddata((x[valid], y[valid]), ve[valid], (xi_grid, yi_grid), method='cubic')
        vn_grid = griddata((x[valid], y[valid]), vn[valid], (xi_grid, yi_grid), method='cubic')
        vu_grid = griddata((x[valid], y[valid]), vu[valid], (xi_grid, yi_grid), method='cubic')

        # Mask ocean points
        glon, glat = self.renderer.inverse_transform_grid(xi_grid, yi_grid)
        mask = CartopyRenderer.mask_ocean_points(glon.flatten(), glat.flatten(), 25)
        ve_grid.flat[~mask] = np.nan
        vn_grid.flat[~mask] = np.nan
        vu_grid.flat[~mask] = np.nan

        # Store in state
        self.state.xi_grid = xi_grid
        self.state.yi_grid = yi_grid
        self.state.ve_grid = ve_grid
        self.state.vn_grid = vn_grid
        self.state.vu_grid = vu_grid

        # Process covariance if available
        if self.config.plot_sigmas and self._covar is not None:
            cov_en = self._covar[field_index][0, :] * (1000.0 ** 2)
            self.state.cov_grid = griddata(
                (x[valid], y[valid]), cov_en[valid],
                (xi_grid, yi_grid), method='cubic'
            )
        else:
            self.state.cov_grid = None

        # Calculate reference vector
        vh = np.sqrt(ve_grid ** 2 + vn_grid ** 2)
        self.state.reference_vector = np.nanmax(vh)
        self.state.current_field_index = field_index

    def _draw_all_subplots(self) -> None:
        """Draw all three subplots (East, North, Up)."""
        labels = ['East', 'North', 'Up']

        for i, (ax, label) in enumerate(zip(self.axes, labels)):
            self._draw_subplot(ax, i, label, connect_interaction=(i == 0))

    def _draw_subplot(self, ax, index: int, label: str, connect_interaction: bool = False) -> None:
        """
        Draw a single subplot.

        Parameters
        ----------
        ax : matplotlib.axes.Axes
            The axis to draw on
        index : int
            Component index (0=East, 1=North, 2=Up)
        label : str
            Component label
        connect_interaction : bool
            Whether to set up interaction handlers on this axis
        """
        # Select field data
        if index == 0:
            field = self.state.ve_grid
        elif index == 1:
            field = self.state.vn_grid
        else:
            field = self.state.vu_grid

        # Compute color range (symmetric around zero)
        vmin = np.nanmin(field)
        vmax = np.nanmax(field)

        if np.abs(vmax) > np.abs(vmin):
            vmin = -vmax
        else:
            vmax = -vmin

        if vmin == vmax:
            vmin, vmax = -1, 1

        levels = np.linspace(vmin, vmax, self.config.n_contour_levels)

        # Draw contours
        cs = ax.contourf(
            self.state.xi_grid, self.state.yi_grid, field,
            levels=levels,
            cmap=self.config.cmap,
            extend=self.config.colorbar_extend,
            transform=self.renderer.projection
        )
        self.state.contours[index] = cs

        # Add colorbar
        cbar = plt.colorbar(cs, ax=ax, orientation='horizontal', pad=0.05, aspect=30)
        cbar.set_label(label, fontsize=9)
        self.state.colorbars[index] = cbar

        # Calculate scale
        scale = self.state.reference_vector * self.state.scale_factor

        # Draw vectors (only for East and North)
        if index != 2:
            if self.config.plot_sigmas and self.state.cov_grid is not None:
                self._draw_ellipses(ax, index, scale)
            else:
                self._draw_quivers(ax, index, scale)

        # Draw station markers and vectors
        scatter = ax.scatter(
            self.state.x_stn, self.state.y_stn,
            s=10, c='black', marker='o',
            zorder=10,
            transform=self.renderer.projection
        )
        self.state.scatter_artists[index] = scatter

        if index != 2:
            q_stn = ax.quiver(
                self.state.x_stn, self.state.y_stn,
                self.state.ve_stn, self.state.vn_stn,
                scale=scale,
                color=self.config.vector_color,
                width=self.config.vector_width,
                edgecolor=self.config.vector_edge_color,
                linewidth=self.config.vector_edge_width,
                zorder=6,
                transform=self.renderer.projection
            )
            self.state.quivers_stn[index] = q_stn

        # Draw sparse station markers and vectors
        if self.state.x_sparse is not None and len(self.state.x_sparse) > 0:
            scatter_sp = ax.scatter(
                self.state.x_sparse, self.state.y_sparse,
                s=20, c='lime', marker='o',
                zorder=10,
                transform=self.renderer.projection
            )
            self.state.scatter_sparse[index] = scatter_sp

            if index != 2 and self.state.ve_sparse is not None:
                q_sp = ax.quiver(
                    self.state.x_sparse, self.state.y_sparse,
                    self.state.ve_sparse, self.state.vn_sparse,
                    scale=scale,
                    color=self.config.vector_color,
                    width=self.config.vector_width,
                    edgecolor=self.config.vector_edge_color,
                    linewidth=self.config.vector_edge_width,
                    zorder=6,
                    transform=self.renderer.projection
                )
                self.state.quivers_sparse[index] = q_sp

        ax.set_title(label, fontsize=12, fontweight='bold')

        # Add contour lines for Up component
        if index == 2:
            cs_lines = ax.contour(
                self.state.xi_grid, self.state.yi_grid, field,
                levels=10, colors='black',
                linewidths=0.5, alpha=0.4,
                transform=self.renderer.projection
            )
            self.state.contour_lines[index] = cs_lines
            labels = ax.clabel(cs_lines, inline=True, fontsize=7, fmt='%1.1f')
            self.state.contour_labels[index] = labels

    @staticmethod
    def _nice_number(value: float) -> float:
        """
        Round a value to a 'nice' number (1, 2, 5, 10, 20, 50, etc.).

        Parameters
        ----------
        value : float
            The value to round

        Returns
        -------
        float
            The nearest nice number
        """
        if value <= 0:
            return 1.0

        import math
        exponent = math.floor(math.log10(value))
        fraction = value / (10 ** exponent)

        # Round to nearest nice fraction (1, 2, 5)
        if fraction < 1.5:
            nice_fraction = 1
        elif fraction < 3.5:
            nice_fraction = 2
        elif fraction < 7.5:
            nice_fraction = 5
        else:
            nice_fraction = 10

        return nice_fraction * (10 ** exponent)

    def _draw_quivers(self, ax, index: int, scale: float) -> None:
        """Draw quiver arrows for a subplot."""
        from matplotlib.offsetbox import AnchoredOffsetbox, HPacker, TextArea, DrawingArea
        from matplotlib.patches import FancyArrowPatch

        q = ax.quiver(
            self.state.xi_grid, self.state.yi_grid,
            self.state.ve_grid, self.state.vn_grid,
            scale=scale,
            color=self.config.vector_color,
            width=self.config.vector_width,
            alpha=self.config.vector_alpha,
            edgecolor=self.config.vector_edge_color,
            linewidth=self.config.vector_edge_width,
            zorder=5,
            transform=self.renderer.projection
        )
        self.state.quivers_grid[index] = q

        # Calculate a reference value that gives consistent visual arrow size
        # Target ~10% of axis width for the reference arrow visual length
        target_visual_length = 0.10
        raw_reference = target_visual_length * scale
        nice_reference = self._nice_number(raw_reference)

        # Format the label (integer if whole number, else one decimal)
        if nice_reference == int(nice_reference):
            label_text = f'{int(nice_reference)} mm[/yr]'
        else:
            label_text = f'{nice_reference:.1f} mm[/yr]'

        # Create a custom boxed reference with arrow and text
        # Fixed arrow length in pixels for consistent visual appearance
        arrow_length = 40
        arrow_height = 15

        # Drawing area for the arrow
        da = DrawingArea(arrow_length, arrow_height, 0, 0)
        arrow = FancyArrowPatch(
            (0, arrow_height / 2),
            (arrow_length - 2, arrow_height / 2),
            arrowstyle='-|>',
            mutation_scale=10,
            color='black',
            linewidth=1.5
        )
        da.add_artist(arrow)

        # Text area for the label
        txt = TextArea(label_text, textprops=dict(size=8, color='black'))

        # Pack arrow and text horizontally
        box = HPacker(children=[da, txt], align='center', pad=2, sep=5)

        # Create anchored box with white background
        anchored_box = AnchoredOffsetbox(
            loc='upper right',
            child=box,
            pad=0.3,
            frameon=True,
            bbox_to_anchor=(0.98, 0.98),
            bbox_transform=ax.transAxes,
            borderpad=0.5
        )
        anchored_box.patch.set_facecolor('white')
        anchored_box.patch.set_edgecolor('gray')
        anchored_box.patch.set_alpha(0.9)
        anchored_box.patch.set_boxstyle('round,pad=0.1')

        ax.add_artist(anchored_box)
        self.state.quiver_keys[index] = anchored_box

    def _draw_ellipses(self, ax, index: int, scale: float) -> None:
        """Draw uncertainty ellipses for a subplot."""
        step = 4
        xi_flat = self.state.xi_grid[::step, ::step].flatten()
        yi_flat = self.state.yi_grid[::step, ::step].flatten()
        ve_flat = self.state.ve_grid[::step, ::step].flatten()
        vn_flat = self.state.vn_grid[::step, ::step].flatten()
        cov_flat = self.state.cov_grid[::step, ::step].flatten()

        # Normalize scale
        norm_scale = scale / self.state.reference_vector

        ellipses = []
        for j in range(ve_flat.size):
            if np.isnan(ve_flat[j]) or np.isnan(vn_flat[j]):
                continue

            # Build 2x2 covariance matrix
            cov_2x2 = np.array([
                [ve_flat[j] ** 2, cov_flat[j]],
                [cov_flat[j], vn_flat[j] ** 2]
            ])

            # Eigenvalue decomposition
            eigenvalues, eigenvectors = np.linalg.eigh(cov_2x2)

            # Sort by eigenvalue
            order = eigenvalues.argsort()[::-1]
            eigenvalues = eigenvalues[order]
            eigenvectors = eigenvectors[:, order]

            width = 2 * 3 * np.sqrt(eigenvalues[0]) * norm_scale * 10000
            height = 2 * 3 * np.sqrt(eigenvalues[1]) * norm_scale * 10000
            angle = np.degrees(np.arctan2(eigenvectors[1, 0], eigenvectors[0, 0]))

            ellipse = Ellipse(
                xy=(xi_flat[j], yi_flat[j]),
                width=width, height=height, angle=angle,
                edgecolor='black', facecolor='none', linewidth=1,
                transform=self.renderer.projection
            )
            ellipses.append(ax.add_patch(ellipse))

        self.state.ellipses[index] = ellipses

    def _remove_artists(self) -> None:
        """Remove all mutable artists from axes WITHOUT clearing."""
        for i in range(3):
            ax = self.axes[i]

            # Remove contours
            if self.state.contours[i] is not None:
                for coll in self.state.contours[i].collections:
                    coll.remove()
                self.state.contours[i] = None

            # Remove colorbars
            if self.state.colorbars[i] is not None:
                self.state.colorbars[i].remove()
                self.state.colorbars[i] = None

            # Remove grid quivers
            if self.state.quivers_grid[i] is not None:
                self.state.quivers_grid[i].remove()
                self.state.quivers_grid[i] = None

            # Remove quiver keys
            if self.state.quiver_keys[i] is not None:
                self.state.quiver_keys[i].remove()
                self.state.quiver_keys[i] = None

            # Remove station quivers
            if self.state.quivers_stn[i] is not None:
                self.state.quivers_stn[i].remove()
                self.state.quivers_stn[i] = None

            # Remove ellipses
            for ellipse in self.state.ellipses[i]:
                ellipse.remove()
            self.state.ellipses[i] = []

            # Remove scatter
            if self.state.scatter_artists[i] is not None:
                self.state.scatter_artists[i].remove()
                self.state.scatter_artists[i] = None

            # Remove sparse scatter
            if self.state.scatter_sparse[i] is not None:
                self.state.scatter_sparse[i].remove()
                self.state.scatter_sparse[i] = None

            # Remove sparse quivers
            if self.state.quivers_sparse[i] is not None:
                self.state.quivers_sparse[i].remove()
                self.state.quivers_sparse[i] = None

            # Remove contour lines
            if self.state.contour_lines[i] is not None:
                for coll in self.state.contour_lines[i].collections:
                    coll.remove()
                self.state.contour_lines[i] = None

            # Remove contour labels
            for label in self.state.contour_labels[i]:
                label.remove()
            self.state.contour_labels[i] = []

    def switch_field(self, field_index: int) -> None:
        """
        Switch to a different field while preserving view state.

        Parameters
        ----------
        field_index : int
            Index of the field to switch to
        """
        if field_index == self.state.current_field_index:
            return

        # 1. Capture view state from all axes
        for i, ax in enumerate(self.axes):
            self.state.view_states[i].capture(ax)

        # 2. Remove old artists (NOT ax.clear()!)
        self._remove_artists()

        # 3. Process new field data
        self._process_data(field_index)

        # 4. Draw new artists
        self._draw_all_subplots()

        # 5. Update interaction handler with new scatter artists
        if self.interaction is not None:
            self.interaction.update_scatters(self.state.scatter_artists,
                                            self.state.scatter_sparse)

        # 6. Restore view state
        for i, ax in enumerate(self.axes):
            self.state.view_states[i].restore(ax)

        # 7. Update display
        self.fig.canvas.draw_idle()

    def update_scale(self, slider_value: float) -> None:
        """
        Update vector scale based on slider value.

        Parameters
        ----------
        slider_value : float
            New slider value (1-100, higher = larger vectors)
        """
        # Invert the scale: higher slider value = larger vectors (lower quiver scale)
        # Map slider 1-100 to scale_factor that decreases with higher slider values
        inverted_value = 101 - slider_value  # 100->1, 1->100
        scale_factor = inverted_value * 10 / 50
        self.state.scale_factor = scale_factor
        scale = self.state.reference_vector * scale_factor

        # Update quivers for East and North
        for i in range(2):
            ax = self.axes[i]

            if self.config.plot_sigmas and self.state.cov_grid is not None:
                # Remove old ellipses
                for ellipse in self.state.ellipses[i]:
                    ellipse.remove()
                self.state.ellipses[i] = []

                # Draw new ellipses
                self._draw_ellipses(ax, i, scale)
            else:
                # Remove old quivers
                if self.state.quivers_grid[i] is not None:
                    self.state.quivers_grid[i].remove()
                if self.state.quiver_keys[i] is not None:
                    self.state.quiver_keys[i].remove()

                # Draw new quivers
                self._draw_quivers(ax, i, scale)

            # Update station quivers
            if self.state.quivers_stn[i] is not None:
                self.state.quivers_stn[i].remove()

            q_stn = ax.quiver(
                self.state.x_stn, self.state.y_stn,
                self.state.ve_stn, self.state.vn_stn,
                scale=scale,
                color=self.config.vector_color,
                width=self.config.vector_width,
                edgecolor=self.config.vector_edge_color,
                linewidth=self.config.vector_edge_width,
                zorder=6,
                transform=self.renderer.projection
            )
            self.state.quivers_stn[i] = q_stn

            # Update sparse station quivers
            if self.state.quivers_sparse[i] is not None:
                self.state.quivers_sparse[i].remove()
                self.state.quivers_sparse[i] = None

            if (self.state.x_sparse is not None and len(self.state.x_sparse) > 0
                    and self.state.ve_sparse is not None):
                q_sp = ax.quiver(
                    self.state.x_sparse, self.state.y_sparse,
                    self.state.ve_sparse, self.state.vn_sparse,
                    scale=scale,
                    color=self.config.vector_color,
                    width=self.config.vector_width,
                    edgecolor=self.config.vector_edge_color,
                    linewidth=self.config.vector_edge_width,
                    zorder=6,
                    transform=self.renderer.projection
                )
                self.state.quivers_sparse[i] = q_sp

        self.fig.canvas.draw_idle()

    def _setup_widgets(self) -> None:
        """Set up UI widgets."""
        self.widgets = WidgetManager(self.fig)

        # Field selector
        self.widgets.create_field_selector(
            self._field_names,
            self.switch_field,
            initial_index=0
        )

        # Scale slider
        self.widgets.create_scale_slider(
            self.update_scale,
            initial_value=50.0
        )

        # Reset view button
        self.widgets.create_reset_button(self.reset_view)

        # Keep references to prevent garbage collection
        self.fig._widgets = self.widgets

    def _setup_interaction(self) -> None:
        """Set up interaction handlers for station clicks on all axes."""
        self.interaction = InteractionHandler()
        self.interaction.setup(
            axes=self.axes,
            scatters=self.state.scatter_artists,
            x=self.state.x_stn,
            y=self.state.y_stn,
            names=self._stations_names,
            on_click=self._on_station_click,
            sparse_scatters=self.state.scatter_sparse,
            sparse_x=self.state.x_sparse,
            sparse_y=self.state.y_sparse,
            sparse_names=self._sparse_names,
            on_sparse_click=self._on_sparse_click
        )

        # Keep reference
        self.fig._interaction = self.interaction

    def _setup_linked_zoom(self) -> None:
        """Set up linked zoom/pan across all axes."""

        def on_xlim_change(ax_changed):
            """Callback when x-limits change on any axis."""
            if self._syncing_views:
                return
            self._syncing_views = True
            try:
                new_xlim = ax_changed.get_xlim()
                for ax in self.axes:
                    if ax is not ax_changed:
                        ax.set_xlim(new_xlim)
                self.fig.canvas.draw_idle()
            finally:
                self._syncing_views = False

        def on_ylim_change(ax_changed):
            """Callback when y-limits change on any axis."""
            if self._syncing_views:
                return
            self._syncing_views = True
            try:
                new_ylim = ax_changed.get_ylim()
                for ax in self.axes:
                    if ax is not ax_changed:
                        ax.set_ylim(new_ylim)
                self.fig.canvas.draw_idle()
            finally:
                self._syncing_views = False

        # Connect callbacks for each axis
        for ax in self.axes:
            cid_x = ax.callbacks.connect('xlim_changed', on_xlim_change)
            cid_y = ax.callbacks.connect('ylim_changed', on_ylim_change)
            self._xlim_cids.append(cid_x)
            self._ylim_cids.append(cid_y)

    def _store_original_view(self) -> None:
        """Store original view limits for the Reset View button."""
        # Store original limits (use first axis as reference since they're linked)
        self._original_xlim = self.axes[0].get_xlim()
        self._original_ylim = self.axes[0].get_ylim()

        # Draw the canvas to ensure view is properly captured
        self.fig.canvas.draw()

    def reset_view(self) -> None:
        """Reset all axes to the original view (programmatic home button)."""
        if self._original_xlim is not None and self._original_ylim is not None:
            self._syncing_views = True
            try:
                for ax in self.axes:
                    ax.set_xlim(self._original_xlim)
                    ax.set_ylim(self._original_ylim)
                self.fig.canvas.draw_idle()
            finally:
                self._syncing_views = False
