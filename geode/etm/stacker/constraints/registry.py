"""
Constraint registry for ETM Stacker.
"""

from typing import List, Dict, Union, TYPE_CHECKING
import numpy as np
from tqdm import tqdm

from .base import BaseConstraint
from .interseismic import InterseismicConstraint
from .coseismic import CoseismicConstraint
from .postseismic import PostseismicConstraint

if TYPE_CHECKING:
    from ..data_classes import Station
    from ..grid_system import GridSystem


class ConstraintRegistry:
    """Manages all constraints for the stacking problem."""

    def __init__(self):
        self.constraints: Dict[str, List[Union[
            CoseismicConstraint,
            PostseismicConstraint,
            InterseismicConstraint,
            BaseConstraint
        ]]] = {
            'interseismic': [],
            'coseismic': [],
            'postseismic': []
        }
        self._application_order = ['interseismic', 'coseismic', 'postseismic']

    def add_constraint(self, constraint: BaseConstraint):
        """Add a constraint to the registry."""
        key = constraint.constraint_type.value
        self.constraints[key].append(constraint)

    def collect_all_constraints(self, stations: List['Station'],
                                total_parameters: int, grids: 'GridSystem', **kwargs):
        """Collect all registered constraints."""
        for constraint_type in self._application_order:
            tqdm.write(f"Collecting {constraint_type} constraints")
            for constraint in self.constraints[constraint_type]:
                # Reset equations and SW-Okada coefficient cache so that any change
                # to regularisation parameters takes effect on every solve() call,
                # including when the constraint object is reused after loading from
                # a pickle file or called a second time in the interactive shell.
                constraint.equations = []
                if hasattr(constraint, '_constraint_coefficients'):
                    constraint._constraint_coefficients = {}
                constraint.collect_constraints(stations, total_parameters, grids, **kwargs)

    def add_all_constraints(self, neq: np.ndarray,
                            total_parameters: int) -> int:
        """Add all constraints to normal equations."""
        total_constraints = 0
        for constraint_type in self._application_order:
            for constraint in self.constraints[constraint_type]:
                tqdm.write(f'Adding {repr(constraint)} to the system')
                neq += constraint.apply_to_normal_equations(total_parameters)
                # count how many constraints per equation
                total_constraints += sum(3 for const in constraint.equations if const.is_active)

        return total_constraints

    def get_constraint_summary(self) -> Dict:
        """Get summary of all constraints for reporting."""
        summary = {}
        for ctype, constraints in self.constraints.items():
            summary[ctype] = {
                'count': len(constraints),
                'total_equations': sum(len(c.equations) for c in constraints),
                'active_equations': sum(
                    sum(p.is_active for p in c.equations)
                    for c in constraints
                )
            }
        return summary
