"""
ETM Stacker - Backward compatibility wrapper.

This module re-exports all classes from geode.etm.stacker for backward compatibility.
All implementation has been moved to geode/etm/stacker/.
"""

# Re-export all public classes from the stacker package
from ..stacker import (
    # Types
    ConstraintType,

    # Exceptions
    EtmStackerException,

    # Jump functions
    CoseismicJumpFunction,
    PostseismicJumpFunction,

    # Data classes
    EtmStackerConfig,
    NormalEquations,
    Station,
    ConstraintEquation,
    EtmStackerField,

    # Grid system
    GridSystem,
    fill_region_with_grid,
    visualize_disks,
    visualize_vectors,

    # Main stacker
    EtmStacker,

    # Constraints
    BaseConstraint,
    InterseismicConstraint,
    CoseismicConstraint,
    PostseismicConstraint,
    ConstraintRegistry,
)

# For backward compatibility with older import statements
__all__ = [
    'ConstraintType',
    'EtmStackerException',
    'CoseismicJumpFunction',
    'PostseismicJumpFunction',
    'EtmStackerConfig',
    'NormalEquations',
    'Station',
    'ConstraintEquation',
    'EtmStackerField',
    'GridSystem',
    'fill_region_with_grid',
    'visualize_disks',
    'visualize_vectors',
    'EtmStacker',
    'BaseConstraint',
    'InterseismicConstraint',
    'CoseismicConstraint',
    'PostseismicConstraint',
    'ConstraintRegistry',
]
