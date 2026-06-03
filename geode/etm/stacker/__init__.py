"""
ETM Stacker package for geodetic time series stacking.

This package provides functionality for stacking multiple GNSS station
time series to constrain interseismic velocities, coseismic displacements,
and postseismic relaxation using spatial interpolation methods.
"""

from .types import ConstraintType
from .exceptions import EtmStackerException
from .jump_functions import CoseismicJumpFunction, PostseismicJumpFunction
from .data_classes import (
    EtmStackerConfig,
    NormalEquations,
    Station,
    ConstraintEquation,
    EtmStackerField,
)
from .grid_system import GridSystem, fill_region_with_grid, visualize_disks, visualize_vectors
from .stacker import EtmStacker
from .constraints import (
    BaseConstraint,
    InterseismicConstraint,
    CoseismicConstraint,
    PostseismicConstraint,
    ConstraintRegistry,
)

__all__ = [
    # Types
    'ConstraintType',

    # Exceptions
    'EtmStackerException',

    # Jump functions
    'CoseismicJumpFunction',
    'PostseismicJumpFunction',

    # Data classes
    'EtmStackerConfig',
    'NormalEquations',
    'Station',
    'ConstraintEquation',
    'EtmStackerField',

    # Grid system
    'GridSystem',
    'fill_region_with_grid',
    'visualize_disks',
    'visualize_vectors',

    # Main stacker
    'EtmStacker',

    # Constraints
    'BaseConstraint',
    'InterseismicConstraint',
    'CoseismicConstraint',
    'PostseismicConstraint',
    'ConstraintRegistry',
]
