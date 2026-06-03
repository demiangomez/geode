"""
ETM Stacker type declarations.
"""

from enum import Enum
from typing import Callable


class ConstraintType(Enum):
    """Types of constraints for ETM stacking."""
    INTERSEISMIC = "interseismic"
    COSEISMIC = "coseismic"
    POSTSEISMIC = "postseismic"
    PERIODIC = "periodic"

    @property
    def function(self) -> Callable:
        """Get the function type associated with this constraint type."""
        # Lazy imports to avoid circular dependencies
        from ..etm_functions.polynomial import PolynomialFunction
        from ..etm_functions.periodic import PeriodicFunction
        from .jump_functions import CoseismicJumpFunction, PostseismicJumpFunction

        function_map = {
            ConstraintType.INTERSEISMIC: PolynomialFunction,
            ConstraintType.COSEISMIC: CoseismicJumpFunction,
            ConstraintType.POSTSEISMIC: PostseismicJumpFunction,
            ConstraintType.PERIODIC: PeriodicFunction
        }
        return function_map.get(self, PolynomialFunction)

    @property
    def description(self) -> str:
        """Get the description for this constraint type."""
        description_map = {
            ConstraintType.INTERSEISMIC: "Interseismic",
            ConstraintType.COSEISMIC: "",
            ConstraintType.POSTSEISMIC: "",
            ConstraintType.PERIODIC: "Periodic"
        }
        return description_map.get(self, "Unknown")
