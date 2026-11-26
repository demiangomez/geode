"""
Widget manager for field plot UI controls.
"""

import numpy as np
from matplotlib.widgets import RadioButtons, Slider, Button, CheckButtons
from typing import Callable, List, Optional, Tuple


class WidgetManager:
    """
    Manages UI widgets for field selection and scale control.
    """

    def __init__(self, fig):
        """
        Initialize the widget manager.

        Parameters
        ----------
        fig : matplotlib.figure.Figure
            The figure to add widgets to
        """
        self.fig = fig
        self._field_selector = None
        self._field_selector_ax = None
        self._scale_slider = None
        self._scale_slider_ax = None
        self._reset_button = None
        self._reset_button_ax = None
        self._field_callback = None
        self._scale_callback = None

    def create_field_selector(
            self,
            field_names: List[str],
            callback: Callable[[int], None],
            initial_index: int = 0
    ) -> RadioButtons:
        """
        Create a RadioButtons-based field selector.

        Parameters
        ----------
        field_names : List[str]
            List of field names to display
        callback : Callable[[int], None]
            Callback function when field selection changes.
            Receives the index of the selected field.
        initial_index : int
            Initially selected field index

        Returns
        -------
        RadioButtons
            The created radio button widget
        """
        n_fields = len(field_names)

        # Calculate the height needed for the radio buttons
        button_height = 0.025  # Height per button
        total_height = n_fields * button_height + 0.02  # Extra padding

        # Position the selector on the left side
        # Centered vertically
        start_y = (1 - total_height) / 2

        # Create the axis for radio buttons
        self._field_selector_ax = self.fig.add_axes(
            [0.01, start_y, 0.13, total_height],
            facecolor='lightgoldenrodyellow'
        )

        # Create radio buttons
        self._field_selector = RadioButtons(
            self._field_selector_ax,
            field_names,
            active=initial_index
        )

        # Style the radio buttons
        for label in self._field_selector.labels:
            label.set_fontsize(8)

        # Store the callback with index translation
        self._field_callback = callback
        self._field_names = field_names

        def on_select(label):
            idx = field_names.index(label)
            callback(idx)

        self._field_selector.on_clicked(on_select)

        return self._field_selector

    def create_scale_slider(
            self,
            callback: Callable[[float], None],
            initial_value: float = 50.0,
            val_min: float = 1.0,
            val_max: float = 100.0
    ) -> Slider:
        """
        Create a scale slider for adjusting vector sizes.

        Parameters
        ----------
        callback : Callable[[float], None]
            Callback function when slider value changes.
            Receives the new slider value.
        initial_value : float
            Initial slider value
        val_min, val_max : float
            Minimum and maximum slider values

        Returns
        -------
        Slider
            The created slider widget
        """
        # Position the slider at the bottom, centered
        self._scale_slider_ax = self.fig.add_axes([0.25, 0.03, 0.5, 0.025])

        self._scale_slider = Slider(
            ax=self._scale_slider_ax,
            label='Vector Scale',
            valmin=val_min,
            valmax=val_max,
            valinit=initial_value,
            valstep=0.5
        )

        self._scale_callback = callback
        self._scale_slider.on_changed(callback)

        return self._scale_slider

    def create_epoch_slider(self, callback, val_min: float, val_max: float,
                            marks=None) -> Slider:
        """Epoch slider for the panels that are evaluated at a point in time.

        Sits above the scale slider. Starts at val_max -- the last epoch any station
        observed -- because anything past that is extrapolation.
        """
        self._epoch_slider_ax = self.fig.add_axes([0.25, 0.072, 0.5, 0.022])
        self._epoch_slider = Slider(
            ax=self._epoch_slider_ax,
            label='Epoch',
            valmin=val_min,
            valmax=val_max,
            valinit=val_max,
            valstep=0.05,
            valfmt='%.2f'
        )
        # Event onsets, so the slider shows where the steps in the curve come from.
        # Drawn in the slider's own data coordinates, which run valmin..valmax.
        for mark in (marks or ()):
            if val_min <= mark <= val_max:
                self._epoch_slider_ax.axvline(mark, color='firebrick', linewidth=0.8,
                                              alpha=0.9, zorder=10, clip_on=True)

        self._epoch_callback = callback
        self._epoch_slider.on_changed(callback)
        return self._epoch_slider

    def get_epoch(self):
        """Current epoch slider value, or None when there is no epoch slider."""
        slider = getattr(self, '_epoch_slider', None)
        return slider.val if slider is not None else None

    def create_sigma_toggle(self, callback, initial: bool = False) -> CheckButtons:
        """Checkbox switching the panels between field vectors and uncertainty."""
        self._sigma_toggle_ax = self.fig.add_axes([0.01, 0.12, 0.115, 0.05])
        self._sigma_toggle_ax.set_axis_off()
        self._sigma_toggle = CheckButtons(
            ax=self._sigma_toggle_ax,
            labels=['uncertainty'],
            actives=[bool(initial)]
        )
        for label in self._sigma_toggle.labels:
            label.set_fontsize(8)
        self._sigma_callback = callback
        self._sigma_toggle.on_clicked(lambda _label: callback(self._sigma_toggle.get_status()[0]))
        return self._sigma_toggle

    def create_magnitude_toggle(self, callback, initial: bool = False) -> CheckButtons:
        """Checkbox collapsing the East/North panels into one horizontal magnitude."""
        self._magnitude_toggle_ax = self.fig.add_axes([0.01, 0.165, 0.115, 0.05])
        self._magnitude_toggle_ax.set_axis_off()
        self._magnitude_toggle = CheckButtons(
            ax=self._magnitude_toggle_ax,
            labels=['magnitude'],
            actives=[bool(initial)]
        )
        for label in self._magnitude_toggle.labels:
            label.set_fontsize(8)
        self._magnitude_callback = callback
        self._magnitude_toggle.on_clicked(
            lambda _label: callback(self._magnitude_toggle.get_status()[0]))
        return self._magnitude_toggle

    def get_scale_factor(self) -> float:
        """Get the current scale factor from slider value."""
        if self._scale_slider is not None:
            return self._scale_slider.val * 10 / 50
        return 10.0

    def get_selected_field_index(self) -> int:
        """Get the index of the currently selected field."""
        if self._field_selector is not None:
            return self._field_names.index(self._field_selector.value_selected)
        return 0

    def set_selected_field(self, index: int) -> None:
        """
        Programmatically select a field.

        Parameters
        ----------
        index : int
            Index of the field to select
        """
        if self._field_selector is not None and 0 <= index < len(self._field_names):
            self._field_selector.set_active(index)

    def create_reset_button(
            self,
            callback: Callable[[], None]
    ) -> Button:
        """
        Create a Reset View button.

        Parameters
        ----------
        callback : Callable[[], None]
            Callback function when button is clicked.

        Returns
        -------
        Button
            The created button widget
        """
        # Position the button at the bottom left
        self._reset_button_ax = self.fig.add_axes([0.01, 0.03, 0.08, 0.03])

        self._reset_button = Button(
            self._reset_button_ax,
            'Reset View',
            color='lightgray',
            hovercolor='lightblue'
        )
        self._reset_button.label.set_fontsize(8)

        self._reset_button.on_clicked(lambda event: callback())

        return self._reset_button

    def cleanup(self) -> None:
        """Remove all widgets."""
        if self._field_selector_ax is not None:
            self._field_selector_ax.remove()
            self._field_selector_ax = None
            self._field_selector = None

        if self._scale_slider_ax is not None:
            self._scale_slider_ax.remove()
            self._scale_slider_ax = None
            self._scale_slider = None

        if self._reset_button_ax is not None:
            self._reset_button_ax.remove()
            self._reset_button_ax = None
            self._reset_button = None
