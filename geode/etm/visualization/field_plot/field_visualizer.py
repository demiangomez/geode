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
from scipy.interpolate import griddata, CloughTocher2DInterpolator
from scipy.spatial import Delaunay
from typing import List, Optional, Callable, Tuple

from .data_classes import FieldPlotConfig, PlotState, ViewState
from .map_renderer import CartopyRenderer
from .widget_manager import WidgetManager
from .interaction_handler import InteractionHandler


# Upper bound on how many field arrows a single panel draws; _draw_quivers decimates the
# grid to stay under it. Nothing below this is about accuracy -- the full grid is still
# contoured, and the field itself is unchanged -- only about how many artists matplotlib
# has to re-render on each interaction.
_MAX_GRID_ARROWS = 700


# Colour map for the non-negative magnitude panel, where a diverging map is wrong.
_SEQUENTIAL_CMAP = 'viridis'

# Length of a unit-mode arrow, as a fraction of the field's largest vector, so the
# arrows stay comparable to the scale slider's other settings.
_UNIT_ARROW_FRACTION = 0.45


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
        self.renderer = CartopyRenderer(
            resolution=self.config.coastline_resolution,
            show_plate_boundaries=self.config.show_plate_boundaries,
            plate_boundary_color=self.config.plate_boundary_color,
            plate_boundary_width=self.config.plate_boundary_width
        )
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
            on_sparse_click: Optional[Callable[[int], None]] = None,
            field_notes: Optional[List[Optional[str]]] = None,
            epoch_callback: Optional[Callable[[int, float], tuple]] = None,
            epoch_limits: Optional[Tuple[float, float]] = None,
            epoch_indices: Optional[List[int]] = None,
            sigma_data: Optional[List[Optional[np.ndarray]]] = None,
            epoch_marks: Optional[List[float]] = None,
            field_units: Optional[List[str]] = None
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
        self._field_notes = field_notes or []
        self._interp_cache = {}
        self._locked_levels = {}
        self._locked_magnitude = {}
        self._magnitude = False
        self._unit_vectors = False
        self._sigma_data = sigma_data or []
        self._epoch_marks = epoch_marks or []
        self._field_units = field_units or []
        self._show_sigmas = bool(self.config.plot_sigmas)
        self._epoch_callback = epoch_callback
        self._epoch_limits = epoch_limits
        self._epoch_indices = set(epoch_indices or [])
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

        # Dedicated colorbar axes, created once at fixed positions.
        #
        # plt.colorbar(cs, ax=ax) steals space from its parent to make room, and removing
        # it does not hand all of that space back. Every redraw therefore left the map
        # axes a little smaller -- and these axes are adjustable='datalim' with a fixed
        # aspect, so matplotlib absorbs a smaller box by WIDENING the data limits. The
        # result was a map that zoomed out a little on every draw, with no interaction at
        # all, compounding without limit. Giving the colorbars their own axes means the
        # map boxes are never touched.
        # The map axes have to give up the space themselves now, since the colorbars no
        # longer take it: the gridspec cell is split into the map and, below it, a strip
        # for the bar. The 0.8 reproduces the height the axes used to settle at once
        # plt.colorbar had taken its cut, so the layout is unchanged from before.
        self._cbar_axes = []
        self._cbar_rects = []
        for ax in self.axes:
            cell = ax.get_position()
            map_height = 0.8 * cell.height
            ax.set_position([cell.x0, cell.y0 + cell.height - map_height,
                             cell.width, map_height])
            rect = [cell.x0, cell.y0 + cell.height - map_height - 0.055,
                    cell.width, 0.022]
            self._cbar_rects.append(rect)
            self._cbar_axes.append(self.fig.add_axes(list(rect)))


        self.fig.suptitle(self.config.title, fontsize=16, fontweight='bold')

        # One-line caption for the selected field, empty for most of them. Lives on the
        # figure rather than in the field selector: the selector is already as wide as
        # its longest event label, while there is a full figure width going spare here.
        # In the strip between the colorbar labels and the slider row -- the only band of
        # full figure width that is reliably free. Above it collides with the subplot
        # titles, below it with the Vector Scale slider.
        self._note_text = self.fig.text(
            0.5, 0.128, '', ha='center', va='center', fontsize=9,
            color='firebrick', style='italic')
        self._update_field_note(self.state.current_field_index)

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
        data = self._sigma_source(field_index)

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

        # Everything here that does not depend on the field VALUES is cached: the display
        # grid, the Delaunay triangulation griddata would otherwise rebuild three times
        # per call, and the ocean mask. The epoch slider re-enters this method with the
        # same geometry and different values on every move, and without the cache that is
        # three triangulations and a point-in-polygon pass over the whole display grid
        # each time.
        valid = ~np.isnan(ve)
        key = (field_index, valid.tobytes())
        cached = self._interp_cache.get(key)

        if cached is None:
            grid_spacing = self.config.grid_spacing_m
            xi = np.linspace(x.min(), x.max(), int((x.max() - x.min()) / grid_spacing))
            yi = np.linspace(y.min(), y.max(), int((y.max() - y.min()) / grid_spacing))
            xi_grid, yi_grid = np.meshgrid(xi, yi)

            tri = Delaunay(np.column_stack([x[valid], y[valid]]))
            glon, glat = self.renderer.inverse_transform_grid(xi_grid, yi_grid)
            mask = CartopyRenderer.mask_ocean_points(glon.flatten(), glat.flatten(), 25)

            cached = (xi_grid, yi_grid, tri, mask)
            self._interp_cache[key] = cached

        xi_grid, yi_grid, tri, mask = cached

        def interpolate(values):
            out = CloughTocher2DInterpolator(tri, values[valid])(xi_grid, yi_grid)
            out.flat[~mask] = np.nan
            return out

        ve_grid = interpolate(ve)
        vn_grid = interpolate(vn)
        vu_grid = interpolate(vu)

        # Store in state
        self.state.xi_grid = xi_grid
        self.state.yi_grid = yi_grid
        self.state.ve_grid = ve_grid
        self.state.vn_grid = vn_grid
        self.state.vu_grid = vu_grid

        # Process covariance if available
        if self._show_sigmas and self._covar is not None and self._covar[field_index] is not None:
            cov_en = self._covar[field_index][0, :] * (1000.0 ** 2)
            self.state.cov_grid = CloughTocher2DInterpolator(tri, cov_en[valid])(xi_grid, yi_grid)
        else:
            self.state.cov_grid = None

        # Reference vector for the arrow scale, taken from the FIELD even when the
        # uncertainty is the thing being contoured. The arrows always show the field, so
        # deriving their scale from whatever is on the colour map made them jump by the
        # ratio of the two -- about 15x here, since a velocity of 15 mm/yr has a sigma
        # near 1 mm/yr -- the moment the uncertainty box was ticked.
        field = self._data[field_index]
        reference = np.nanmax(np.hypot(field[0, :], field[1, :])) * 1000.0
        if not np.isfinite(reference) or reference <= 0:
            reference = float(np.nanmax(np.hypot(ve_grid, vn_grid)))
        self.state.reference_vector = float(reference)
        self.state.current_field_index = field_index

    def _panel_specs(self) -> List[dict]:
        """What each of the three panels shows, as (label, field, vectors, contours,
        visible).

        Component mode is the long-standing East / North / Up. Magnitude mode replaces
        the first two with one horizontal-magnitude panel and hides the third: the
        separate E and N colour fields are hard to read as one vector field, and the
        arrows already carry the direction.
        """
        if not self._magnitude:
            return [
                dict(label='East', field=self.state.ve_grid, vectors=True,
                     contours=False, visible=True),
                dict(label='North', field=self.state.vn_grid, vectors=True,
                     contours=False, visible=True),
                dict(label='Up', field=self.state.vu_grid, vectors=False,
                     contours=True, visible=True),
            ]

        horizontal = np.hypot(self.state.ve_grid, self.state.vn_grid)
        return [
            dict(label='Horizontal magnitude', field=horizontal, vectors=True,
                 contours=False, visible=True, sequential=True),
            dict(label='Up', field=self.state.vu_grid, vectors=False,
                 contours=True, visible=True),
            dict(label=None, field=None, vectors=False, contours=False, visible=False),
        ]

    def _draw_all_subplots(self) -> None:
        """Draw all three subplots."""
        for i, (ax, spec) in enumerate(zip(self.axes, self._panel_specs())):
            ax.set_visible(spec['visible'])
            self._cbar_axes[i].set_visible(spec['visible'])
            if not spec['visible']:
                continue
            self._draw_subplot(ax, i, spec, connect_interaction=(i == 0))

    def _draw_subplot(self, ax, index: int, spec: dict, connect_interaction: bool = False) -> None:
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
        field = spec['field']
        label = self._panel_label(spec)

        vmin, vmax = self._colour_range(index, field,
                                        non_negative=bool(spec.get('sequential')))

        levels = np.linspace(vmin, vmax, self.config.n_contour_levels)

        # Draw contours
        # A magnitude is non-negative, so the diverging map would put its midpoint at
        # half the maximum and render everything below that as saturated colour.
        cs = ax.contourf(
            self.state.xi_grid, self.state.yi_grid, field,
            levels=levels,
            cmap=_SEQUENTIAL_CMAP if spec.get('sequential') else self.config.cmap,
            extend=self.config.colorbar_extend,
            transform=self.renderer.projection
        )
        self.state.contours[index] = cs

        # Rebuilt on its own fixed axes, not with ax=... . Recreating is what gives the
        # correct ticks for a new field -- update_normal leaves a contour colorbar's
        # boundaries stale, so the bar kept the previous field's numbers. Doing it on a
        # dedicated cax is harmless; it was plt.colorbar(ax=ax) stealing from the map
        # axis that used to shrink the box on every redraw.
        cax = self._cbar_axes[index]
        cax.set_visible(True)
        cax.clear()
        # clear() keeps the aspect the previous Colorbar set on this axis, which would
        # squeeze the rebuilt bar down to a stub
        cax.set_aspect('auto')
        cbar = self.fig.colorbar(cs, cax=cax, orientation='horizontal')
        # Colorbar() installs an axes locator that re-derives the cax position at DRAW
        # time to honour its aspect, so the bar narrowed on every redraw -- the same
        # creep the map axes had, moved onto the cax. Dropping the locator and pinning
        # the rect is what actually holds it; set_position alone does not, because the
        # locator runs afterwards.
        cax.set_axes_locator(None)
        cax.set_aspect('auto')
        cax.set_position(list(self._cbar_rects[index]))
        cbar.set_label(label, fontsize=9)
        self.state.colorbars[index] = cbar

        # Calculate scale
        scale = self.state.reference_vector * self.state.scale_factor

        # Draw vectors (only for East and North)
        if spec['vectors']:
            if self._show_sigmas and self.state.cov_grid is not None:
                self._draw_ellipses(ax, index, scale)
            else:
                self._draw_quivers(ax, index, scale)

        # Draw station markers and vectors
        scatter = ax.scatter(
            self.state.x_stn, self.state.y_stn,
            s=10, c='black', marker='o',
            zorder=10,
            label='full station',
            transform=self.renderer.projection
        )
        self.state.scatter_artists[index] = scatter

        if spec['vectors']:
            q_stn = ax.quiver(
                self.state.x_stn, self.state.y_stn,
                *self._arrows(self.state.ve_stn, self.state.vn_stn),
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
                label='sparse station',
                transform=self.renderer.projection
            )
            self.state.scatter_sparse[index] = scatter_sp

            if spec['vectors'] and self.state.ve_sparse is not None:
                q_sp = ax.quiver(
                    self.state.x_sparse, self.state.y_sparse,
                    *self._arrows(self.state.ve_sparse, self.state.vn_sparse),
                    scale=scale,
                    color=self.config.vector_color,
                    width=self.config.vector_width,
                    edgecolor=self.config.vector_edge_color,
                    linewidth=self.config.vector_edge_width,
                    zorder=6,
                    transform=self.renderer.projection
                )
                self.state.quivers_sparse[index] = q_sp

        # Which marker is which. The two station classes are not interchangeable -- a full
        # station owns the parameters the field is built from, a sparse one is only
        # predicted by it -- and nothing on the figure said so. Re-created rather than
        # updated on every _draw_subplot: ax.legend() replaces the axes' existing legend,
        # and switch_field removes and redraws these artists.
        station_handles = [scatter]
        if self.state.scatter_sparse[index] is not None:
            station_handles.append(self.state.scatter_sparse[index])
        ax.legend(handles=station_handles, loc='lower left', fontsize=7,
                  framealpha=0.85, borderpad=0.4, handletextpad=0.3).set_zorder(20)

        ax.set_title(label, fontsize=12, fontweight='bold')

        # Add contour lines for the vertical panel
        if spec['contours']:
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

        # Decimate, the way _draw_ellipses already does. Drawing one arrow per grid point
        # means 2909 arrows per panel at the default 25 km spacing, re-rendered on every
        # pan, zoom and scale-slider drag -- which is both slow and unreadable, since at
        # that density the arrows merge into a solid mass. The step is derived from the
        # grid size rather than fixed, so the display stays legible at any grid_spacing.
        step = max(1, int(np.ceil(np.sqrt(self.state.xi_grid.size / _MAX_GRID_ARROWS))))
        sl = (slice(None, None, step), slice(None, None, step))

        q = ax.quiver(
            self.state.xi_grid[sl], self.state.yi_grid[sl],
            *self._arrows(self.state.ve_grid[sl], self.state.vn_grid[sl]),
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

        if self._unit_vectors:
            # No reference arrow: in unit mode the arrow length carries no value, and a
            # key reading "20 mm/yr" next to arrows that all mean the same thing is worse
            # than no key at all.
            self.state.quiver_keys[index] = None
            return

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

        # Map a 3-sigma length through exactly the same transform the arrows use: quiver
        # draws a value v as v / scale of the axis width, so the equivalent length in
        # data units is v / scale * span. Previously this was an arbitrary constant,
        # which put a 0.5 mm sigma on screen as a 400 km ellipse against a 25 km grid --
        # every ellipse overlapping its neighbours. Tying it to `scale` also means the
        # ellipses follow the Vector Scale slider the way the arrows do.
        x0, x1 = ax.get_xlim()
        span = abs(x1 - x0)
        axis_per_unit = span / scale if scale else 0.0

        ellipses = []
        for j in range(ve_flat.size):
            if np.isnan(ve_flat[j]) or np.isnan(vn_flat[j]):
                continue

            # Build 2x2 covariance matrix
            cov_2x2 = np.array([
                [ve_flat[j] ** 2, cov_flat[j]],
                [cov_flat[j], vn_flat[j] ** 2]
            ])

            # Eigenvalue decomposition. The matrix is assembled from an interpolated
            # sigma field and an interpolated E-N covariance, which are gridded
            # independently, so nothing guarantees |cov| <= sigma_e * sigma_n and a
            # negative eigenvalue is entirely possible -- it produced a NaN axis and a
            # RuntimeWarning per grid point. Clip at zero and skip anything still not
            # finite rather than drawing a degenerate patch.
            eigenvalues, eigenvectors = np.linalg.eigh(cov_2x2)
            eigenvalues = np.clip(eigenvalues, 0.0, None)
            if not np.all(np.isfinite(eigenvalues)):
                continue

            # Sort by eigenvalue
            order = eigenvalues.argsort()[::-1]
            eigenvalues = eigenvalues[order]
            eigenvectors = eigenvectors[:, order]

            width = 2 * 3 * np.sqrt(eigenvalues[0]) * axis_per_unit
            height = 2 * 3 * np.sqrt(eigenvalues[1]) * axis_per_unit
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

            # Colorbars are NOT removed: they live on their own axes and are updated
            # in place by _draw_subplot. Recreating them is what used to shrink the map
            # axes a little on every redraw.

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

    def _update_field_note(self, field_index: int) -> None:
        """Show the caption for `field_index`, or clear it when that field has none."""
        note = ''
        if 0 <= field_index < len(self._field_notes):
            note = self._field_notes[field_index] or ''
        if self._show_sigmas and not self.has_sigma(field_index):
            note = ('no uncertainty for this panel -- it sums several events and the '
                    'cross-event covariance is not propagated to the grid')
        self._note_text.set_text(note)

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

        self._render_field(field_index)

    def _render_field(self, field_index: int) -> None:
        """Redraw the figure for `field_index`, preserving the current view.

        Split out of switch_field, which early-returns when the index is unchanged --
        update_epoch needs exactly this work on the panel already being shown.
        """
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

        # 7. Update the caption and display
        self._update_field_note(field_index)
        self.fig.canvas.draw_idle()

    def _colour_range(self, panel_index: int, field,
                      non_negative: bool = False) -> Tuple[float, float]:
        """Symmetric colour limits for one panel.

        Locked for the epoch-dependent panels: their values change by an order of
        magnitude across the slider's range, and rescaling the colourbar with every move
        makes two epochs impossible to compare by eye. The lock is set from the extremes
        of the epoch range, so the scale holds at the largest value the panel ever takes.
        """
        if self._magnitude:
            bounds = self._locked_magnitude.get(self.state.current_field_index)
            if bounds is not None and panel_index < 2:
                bound = bounds[panel_index]
                return (0.0, bound) if panel_index == 0 else (-bound, bound)
        else:
            locked = self._locked_levels.get((self.state.current_field_index, panel_index))
            if locked is not None:
                return locked

        vmin = float(np.nanmin(field))
        vmax = float(np.nanmax(field))

        # A magnitude starts at zero. Only a signed component wants the range made
        # symmetric so that the diverging colour map's midpoint lands on zero; doing it
        # to a magnitude put negative numbers on the bar for a quantity that cannot be
        # negative.
        if non_negative:
            vmin = 0.0
            if vmax <= 0.0:
                vmax = 1.0
            return vmin, vmax

        if abs(vmax) > abs(vmin):
            vmin = -vmax
        else:
            vmax = -vmin
        if vmin == vmax:
            vmin, vmax = -1.0, 1.0
        return vmin, vmax

    def _lock_epoch_levels(self) -> None:
        """Precompute the fixed colour limits of every epoch-dependent panel.

        Sampled across the epoch range rather than taken from the current epoch: the rate
        panel peaks at the earliest epoch and the cumulative one at the latest, so either
        end alone would clip the other.
        """
        if self._epoch_callback is None or self._epoch_limits is None:
            return

        lo, hi = self._epoch_limits
        epochs = np.linspace(lo, hi, 9)

        for field_index in self._epoch_indices:
            peak = np.zeros(3)
            for epoch in epochs:
                field, _stations, _sparse = self._epoch_callback(field_index, float(epoch))
                peak = np.maximum(peak, np.nanmax(np.abs(field), axis=1) * 1000.0)

            horizontal = float(np.hypot(peak[0], peak[1]))
            for panel_index, bound in enumerate((peak[0], peak[1], peak[2])):
                bound = float(bound) or 1.0
                self._locked_levels[(field_index, panel_index)] = (-bound, bound)
            # magnitude mode: panel 0 carries |E,N| and panel 1 carries Up
            self._locked_magnitude[field_index] = (horizontal or 1.0, float(peak[2]) or 1.0)

    def _panel_label(self, spec: dict) -> str:
        """Colourbar label, saying what the quantity is and in what units.

        In uncertainty mode the magnitude panel shows sqrt(sigma_E^2 + sigma_N^2), which
        is not self-evident from the words "horizontal magnitude", so it is spelled out.
        """
        index = self.state.current_field_index
        unit = self._field_units[index] if index < len(self._field_units) else ''
        suffix = f' [{unit}]' if unit else ''

        base = spec['label']
        if self._show_sigmas and self.has_sigma(index):
            base = ('Horizontal sigma = sqrt(sE^2 + sN^2)'
                    if spec.get('sequential') else f'{base} sigma')

        return base + suffix

    def _arrows(self, ve, vn):
        """Arrow components to draw: the vectors themselves, or all the same length.

        In unit-vector mode every arrow is normalised to the same length, so the arrows
        carry direction only and the contoured field carries magnitude. Normalised to a
        fraction of the field's largest vector rather than to 1, so the scale slider
        keeps working the way it does in the other mode.
        """
        if not self._unit_vectors:
            return ve, vn

        magnitude = np.hypot(ve, vn)
        with np.errstate(invalid='ignore', divide='ignore'):
            factor = np.where(magnitude > 0,
                              self.state.reference_vector * _UNIT_ARROW_FRACTION / magnitude,
                              0.0)
        factor = np.nan_to_num(factor, nan=0.0, posinf=0.0, neginf=0.0)
        return ve * factor, vn * factor

    def toggle_unit_vectors(self, show: bool) -> None:
        """Switch the arrows between true vectors and direction-only unit vectors."""
        self._unit_vectors = bool(show)
        self._render_field(self.state.current_field_index)
        self.fig.canvas.draw_idle()

    def _sigma_source(self, field_index: int):
        """The array to display for `field_index`: its uncertainty when the toggle is on
        and this panel has one, otherwise the field itself."""
        if self._show_sigmas and field_index < len(self._sigma_data):
            sigma = self._sigma_data[field_index]
            if sigma is not None:
                return sigma
        return self._data[field_index]

    def has_sigma(self, field_index: int) -> bool:
        """Whether `field_index` has an uncertainty view at all."""
        return (field_index < len(self._sigma_data)
                and self._sigma_data[field_index] is not None)

    def toggle_magnitude(self, show: bool) -> None:
        """Switch between East/North/Up panels and horizontal-magnitude + Up."""
        self._magnitude = bool(show)
        self._render_field(self.state.current_field_index)
        self.fig.canvas.draw_idle()

    def toggle_sigmas(self, show: bool) -> None:
        """Switch all panels between the field and its uncertainty."""
        self._show_sigmas = bool(show)
        self._interp_cache.clear()
        self._render_field(self.state.current_field_index)
        self._update_field_note(self.state.current_field_index)
        self.fig.canvas.draw_idle()

    def update_epoch(self, epoch: float) -> None:
        """Re-evaluate the epoch-dependent panels and redraw if one is on screen."""
        if self._epoch_callback is None:
            return

        for index in self._epoch_indices:
            field, stations, sparse = self._epoch_callback(index, float(epoch))
            self._data[index] = field
            self._stations_data[index] = stations
            if self._sparse_data is not None:
                self._sparse_data[index] = sparse

        if self.state.current_field_index in self._epoch_indices:
            self._render_field(self.state.current_field_index)

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

        # Update quivers on whichever panels carry them
        for i, spec in enumerate(self._panel_specs()):
            if not spec['vectors']:
                continue
            ax = self.axes[i]

            if self._show_sigmas and self.state.cov_grid is not None:
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
                *self._arrows(self.state.ve_stn, self.state.vn_stn),
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
                    *self._arrows(self.state.ve_sparse, self.state.vn_sparse),
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

        self._lock_epoch_levels()

        # Epoch slider, only when some panel actually depends on an epoch
        if self._epoch_callback is not None and self._epoch_limits is not None:
            self.widgets.create_epoch_slider(self.update_epoch, *self._epoch_limits,
                                             marks=self._epoch_marks)

        # Uncertainty toggle, only when some panel actually has an uncertainty
        if any(sigma is not None for sigma in self._sigma_data):
            self.widgets.create_sigma_toggle(self.toggle_sigmas, initial=self._show_sigmas)
        self.widgets.create_magnitude_toggle(self.toggle_magnitude, initial=self._magnitude)
        self.widgets.create_unit_vector_toggle(self.toggle_unit_vectors,
                                               initial=self._unit_vectors)

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
