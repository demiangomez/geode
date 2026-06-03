"""
ETM Stacker jump functions for coseismic and postseismic modeling.
"""

from ..etm_functions.jumps import JumpFunction
from ..core.type_declarations import JumpType


class CoseismicJumpFunction(JumpFunction):
    """Jump function for coseismic-only deformation."""

    def __init__(self, config, time_vector, date):
        # Invoke coseismic only jump because steps and decays are
        # applied on separate steps
        super().__init__(
            config,
            time_vector=time_vector,
            date=date,
            jump_type=JumpType.COSEISMIC_ONLY,
            fit=True
        )


class PostseismicJumpFunction(JumpFunction):
    """Jump function for postseismic-only deformation."""

    def __init__(self, config, time_vector, date):
        super().__init__(
            config,
            time_vector=time_vector,
            date=date,
            jump_type=JumpType.POSTSEISMIC_ONLY,
            fit=True
        )
