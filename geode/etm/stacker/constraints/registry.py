import logging
"""
Constraint registry for ETM Stacker.
"""

from typing import List, Dict, Union, TYPE_CHECKING
import numpy as np

logger = logging.getLogger(__name__)

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
        """Collect all registered constraints and report them as one table.

        Each constraint used to narrate its own collection -- a progress bar over one
        to sixteen stations, plus the SW-Okada setup lines, plus any zero-tie notice,
        interleaved per event. That is a lot of screen for a step whose whole result is
        "how many equations did each constraint produce, and against how many
        constraining stations". The mechanics are in the log; what comes out here is
        that answer.
        """
        from .. import console

        rows = []
        for constraint_type in self._application_order:
            logger.info(f'Collecting {constraint_type} constraints')
            for constraint in self.constraints[constraint_type]:
                # Reset equations and SW-Okada coefficient cache so that any change
                # to regularisation parameters takes effect on every solve() call,
                # including when the constraint object is reused after loading from
                # a pickle file or called a second time in the interactive shell.
                constraint.equations = []
                if hasattr(constraint, '_constraint_coefficients'):
                    constraint._constraint_coefficients = {}
                constraint.collect_constraints(stations, total_parameters, grids, **kwargs)

                notes = []
                if constraint.is_collision:
                    notes.append('collision')
                if constraint.is_zero_tied:
                    notes.append('ZERO-TIED (1 or no constraining station)')
                rows.append([
                    constraint_type,
                    constraint.event.id if constraint.event is not None else '-',
                    f'{constraint.relaxation:.3f}' if constraint.relaxation is not None else '-',
                    f'{len(constraint.station_list)}',
                    f'{len(constraint.equations) * 3}',
                    f'{constraint.h_sigma * 1000.:g}/{constraint.v_sigma * 1000.:g}',
                    ', '.join(notes)])

        console.step('constraint equations')
        console.table(['type', 'event', 'relax', 'constraining', 'equations',
                       'h/v sigma', 'note'],
                      rows, align='lllrrll')
        zero_tied = [r[1] for r in rows if 'ZERO-TIED' in r[6]]
        if zero_tied:
            console.warn(f'{len(zero_tied)} constraint(s) zero-tied for want of a second '
                         f'constraining station: {", ".join(zero_tied)}')

    def add_all_constraints(self, neq: np.ndarray,
                            total_parameters: int) -> int:
        """Add all constraints to normal equations."""
        total_constraints = 0
        for constraint_type in self._application_order:
            for constraint in self.constraints[constraint_type]:
                logger.info(f'Adding {repr(constraint)} to the system')
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
