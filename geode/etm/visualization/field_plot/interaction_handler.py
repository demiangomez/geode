"""
Interaction handler for station hover and click events.
"""

import numpy as np
from typing import Callable, List, Optional


class InteractionHandler:
    """
    Handles station hover annotations and click callbacks for multiple axes.
    Uses existing scatter artists instead of creating new ones.
    """

    def __init__(self):
        """Initialize the interaction handler."""
        self._axes = []
        self._scatters = []
        self._annotations = []
        self._x = None
        self._y = None
        self._names = None
        self._on_click = None
        self._sparse_scatters = []
        self._sparse_x = None
        self._sparse_y = None
        self._sparse_names = None
        self._on_sparse_click = None
        self._hover_cid = None
        self._click_cid = None
        self._fig = None

    def setup(
            self,
            axes: List,
            scatters: List,
            x: np.ndarray,
            y: np.ndarray,
            names: List[str],
            on_click: Optional[Callable[[int], None]] = None,
            sparse_scatters: List = None,
            sparse_x: np.ndarray = None,
            sparse_y: np.ndarray = None,
            sparse_names: List[str] = None,
            on_sparse_click: Optional[Callable[[int], None]] = None
    ) -> None:
        """
        Set up interactive hover labels and click handling on multiple axes.

        Parameters
        ----------
        axes : List[matplotlib.axes.Axes]
            List of axes to add interactions to
        scatters : List[matplotlib.collections.PathCollection]
            List of existing scatter artists (one per axis)
        x, y : np.ndarray
            Station coordinates in axis units
        names : List[str]
            List of station names
        on_click : Callable[[int], None], optional
            Callback when a station is clicked. Receives station index.
        sparse_scatters : List, optional
            Scatter artists for sparse stations
        sparse_x, sparse_y : np.ndarray, optional
            Sparse station coordinates in axis units
        sparse_names : List[str], optional
            Sparse station names
        on_sparse_click : Callable[[int], None], optional
            Callback when a sparse station is clicked.
        """
        self._axes = axes
        self._scatters = scatters
        self._x = x
        self._y = y
        self._names = names
        self._on_click = on_click
        self._sparse_scatters = sparse_scatters or []
        self._sparse_x = sparse_x
        self._sparse_y = sparse_y
        self._sparse_names = sparse_names
        self._on_sparse_click = on_sparse_click
        self._fig = axes[0].figure

        # Make scatter artists pickable
        for scatter in scatters:
            if scatter is not None:
                scatter.set_picker(True)

        for scatter in self._sparse_scatters:
            if scatter is not None:
                scatter.set_picker(True)

        # Create annotation for each axis (initially invisible)
        self._annotations = []
        for ax in axes:
            annot = ax.annotate(
                "",
                xy=(0, 0),
                xytext=(10, 10),
                textcoords="offset points",
                bbox=dict(boxstyle="round", facecolor="yellow", alpha=0.9),
                fontsize=8,
                zorder=101
            )
            annot.set_visible(False)
            self._annotations.append(annot)

        # Connect event handlers
        self._connect_events()

    def _connect_events(self) -> None:
        """Connect matplotlib event handlers."""
        if self._fig is None:
            return

        # Disconnect any existing handlers
        self._disconnect_events()

        # Connect hover event
        self._hover_cid = self._fig.canvas.mpl_connect(
            "motion_notify_event",
            self._on_hover
        )

        # Connect click event
        if self._on_click is not None or self._on_sparse_click is not None:
            self._click_cid = self._fig.canvas.mpl_connect(
                "button_press_event",
                self._on_button_click
            )

    def _disconnect_events(self) -> None:
        """Disconnect matplotlib event handlers."""
        if self._fig is None:
            return

        if self._hover_cid is not None:
            self._fig.canvas.mpl_disconnect(self._hover_cid)
            self._hover_cid = None

        if self._click_cid is not None:
            self._fig.canvas.mpl_disconnect(self._click_cid)
            self._click_cid = None

    def _find_axis_index(self, event_ax) -> int:
        """Find which axis the event occurred in."""
        for i, ax in enumerate(self._axes):
            if ax is event_ax:
                return i
        return -1

    def _on_hover(self, event) -> None:
        """Handle mouse hover events."""
        if event.inaxes is None:
            # Hide all annotations when not in any axis
            for annot in self._annotations:
                if annot.get_visible():
                    annot.set_visible(False)
            self._fig.canvas.draw_idle()
            return

        ax_idx = self._find_axis_index(event.inaxes)
        if ax_idx < 0:
            return

        # Check full stations
        scatter = self._scatters[ax_idx]
        if scatter is not None:
            cont, ind = scatter.contains(event)
            if cont:
                idx = ind["ind"][0]
                annot = self._annotations[ax_idx]
                annot.xy = (self._x[idx], self._y[idx])
                annot.set_text(self._names[idx])
                annot.set_visible(True)
                for i, other_annot in enumerate(self._annotations):
                    if i != ax_idx and other_annot.get_visible():
                        other_annot.set_visible(False)
                self._fig.canvas.draw_idle()
                return

        # Check sparse stations
        if ax_idx < len(self._sparse_scatters) and self._sparse_scatters[ax_idx] is not None:
            cont, ind = self._sparse_scatters[ax_idx].contains(event)
            if cont and self._sparse_names:
                idx = ind["ind"][0]
                annot = self._annotations[ax_idx]
                annot.xy = (self._sparse_x[idx], self._sparse_y[idx])
                annot.set_text(self._sparse_names[idx])
                annot.set_visible(True)
                for i, other_annot in enumerate(self._annotations):
                    if i != ax_idx and other_annot.get_visible():
                        other_annot.set_visible(False)
                self._fig.canvas.draw_idle()
                return

        # Hide annotation if not over any point
        annot = self._annotations[ax_idx]
        if annot.get_visible():
            annot.set_visible(False)
            self._fig.canvas.draw_idle()

    def _on_button_click(self, event) -> None:
        """Handle mouse click events."""
        if event.inaxes is None:
            return

        ax_idx = self._find_axis_index(event.inaxes)
        if ax_idx < 0:
            return

        # Check full stations
        scatter = self._scatters[ax_idx]
        if scatter is not None:
            cont, ind = scatter.contains(event)
            if cont and self._on_click is not None:
                idx = ind["ind"][0]
                self._on_click(idx)
                return

        # Check sparse stations
        if ax_idx < len(self._sparse_scatters) and self._sparse_scatters[ax_idx] is not None:
            cont, ind = self._sparse_scatters[ax_idx].contains(event)
            if cont and self._on_sparse_click is not None:
                idx = ind["ind"][0]
                self._on_sparse_click(idx)

    def update_positions(self, x: np.ndarray, y: np.ndarray) -> None:
        """
        Update station marker positions.

        Parameters
        ----------
        x, y : np.ndarray
            New station coordinates
        """
        self._x = x
        self._y = y

        for scatter in self._scatters:
            if scatter is not None:
                scatter.set_offsets(np.column_stack([x, y]))

    def update_scatters(self, scatters: List, sparse_scatters: List = None) -> None:
        """
        Update the scatter artists (e.g., after field switch).

        Parameters
        ----------
        scatters : List
            New scatter artists
        sparse_scatters : List, optional
            New sparse scatter artists
        """
        self._scatters = scatters
        for scatter in scatters:
            if scatter is not None:
                scatter.set_picker(True)

        if sparse_scatters is not None:
            self._sparse_scatters = sparse_scatters
            for scatter in sparse_scatters:
                if scatter is not None:
                    scatter.set_picker(True)

    def cleanup(self) -> None:
        """
        Clean up event handlers.
        Call this before axis updates to prevent stale references.
        """
        self._disconnect_events()

        # Remove annotations
        for annot in self._annotations:
            if annot is not None:
                annot.remove()
        self._annotations = []

        self._axes = []
        self._scatters = []
        self._sparse_scatters = []
        self._x = None
        self._y = None
        self._names = None
        self._on_click = None
        self._sparse_x = None
        self._sparse_y = None
        self._sparse_names = None
        self._on_sparse_click = None
        self._fig = None
