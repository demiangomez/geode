"""
Base constraint class for ETM Stacker.
"""

from abc import ABC, abstractmethod
from typing import List, Tuple, Union, TYPE_CHECKING
import logging

import numpy as np
from tqdm import tqdm

logger = logging.getLogger(__name__)

from ..data_classes import Station, ConstraintEquation
from ..exceptions import EtmStackerException
from ..types import ConstraintType
from ...core.data_classes import Earthquake
from ....Utils import stationID
from ....elasticity.elastic_interpolation import get_radius

if TYPE_CHECKING:
    from ..grid_system import GridSystem


class BaseConstraint(ABC):
    """Base class for all constraint types."""

    # Whether falling back to a zero-tie is defensible for this constraint type when
    # fewer than two stations are available to interpolate between. For a co- or
    # postseismic amplitude it can be: the parameter may be genuinely unobservable at
    # some stations (no post-event data), and tying it to zero is a statement that the
    # event did nothing measurable there, which is usually true and always checkable
    # against the constraint's own residual. For the interseismic field it never is --
    # that constraint reaches EVERY station, so the fallback silently asserts that the
    # whole network is motionless. Subclasses for which that is the case set this False
    # and the constraint raises instead.
    allow_zero_tie: bool = True

    # Filled in by a subclass that sets allow_zero_tie False, so the exception raised on
    # too few constraining stations can name what would have to change.
    eligibility_rule_description: str = 'decided by the constraint type'

    def __init__(self, constraint_type: ConstraintType,
                 h_sigma: float = 0.001, v_sigma: float = 0.003):
        self.constraint_type = constraint_type
        self.event: Earthquake = None
        self._station_list: List[Station] = []
        self.h_sigma = h_sigma
        self.v_sigma = v_sigma
        self.equations: List[ConstraintEquation] = []
        self._is_collected = False
        self.is_collision = False
        # Set by collect_constraints when this constraint had one or no constraining
        # station and its parameters were hard zero-tied instead of spatially coupled.
        # Reported by the registry's summary, where it is visible next to every other
        # constraint rather than buried in the collection stream.
        self.is_zero_tied = False
        # Set by collect_constraints. Kept apart because they answer different
        # questions: how many stations the field is interpolated FROM, and how many are
        # tied TO it. _station_list is their union, so reporting its length as
        # "constraining" made a zero-tie look like it had 23 constraining stations.
        self.n_constraining = 0
        self.n_to_constrain = 0
        self.relaxation: Union[float, None] = None

    @property
    def station_list(self):
        """Station names in order used in this constraint."""
        return self._station_list

    @abstractmethod
    def select_stations(self, all_stations: List[Station],
                        **kwargs) -> Tuple[List[Station], List[Station]]:
        """
        Returns (constraining_stations, stations_to_constrain)
        Constraining stations have data, stations_to_constrain need constraints.
        """
        pass

    @abstractmethod
    def get_parameters_and_covariance(self, solution: np.ndarray, covariance: np.ndarray):
        pass

    def collect_constraints(self,
                            all_stations: List[Station],
                            total_parameters: int,
                            grids: 'GridSystem', **kwargs):
        """Collects constraints for all applicable stations."""

        constraining, to_constrain = self.select_stations(all_stations, **kwargs)

        # Refresh the station list from what was just selected rather than leaving the
        # snapshot taken at construction. get_parameters_and_covariance and
        # SWOkada._compute_dislocation_model both read it, and a station removed since
        # construction still carries a parameter_start_idx from the old numbering -- so
        # a stale entry does not merely add a dead site, it reads the solution vector at
        # the wrong columns. Order is preserved and duplicates dropped, since a mutual
        # constraint returns the same list on both sides. Deduplicated by identity
        # rather than equality: Station is a dataclass with numpy-array fields, so its
        # generated __eq__ raises on an ambiguous array truth value.
        self.n_constraining = len(constraining)
        self.n_to_constrain = len(to_constrain)

        seen = set()
        self._station_list = []
        for station in list(constraining) + list(to_constrain):
            if id(station) not in seen:
                seen.add(id(station))
                self._station_list.append(station)

        if len(constraining) > 1:
            for station in to_constrain:
                constraint_params = ConstraintEquation(
                    station=station,
                    constraint_design=self._build_k_matrix(station, constraining, grids, total_parameters),
                    constraint_sigma=np.array([self.h_sigma, self.h_sigma, self.v_sigma])
                )

                self.equations.append(constraint_params)

            # couple parameters of nearby stations (d < 5 km)
            self.constrain_nearby_stations(all_stations, total_parameters)
        elif not self.allow_zero_tie:
            raise EtmStackerException(
                f'{self.short_description()} has {len(constraining)} constraining '
                f'station(s) and needs at least 2 to interpolate between them; this '
                f'constraint type has no zero-tie fallback. Which stations qualify is '
                f'{self.eligibility_rule_description}.')
        else:
            self.is_zero_tied = True
            logger.info(f'{self.short_description()} has one or no constraining '
                        f'station; zero-tying its parameters at its own h/v sigma')

            if len(to_constrain) > 0:
                # Tie to zero at this constraint's OWN sigma, not at a hardcoded 1e-6.
                # The old value was a one-micrometre tie that no other mechanism could
                # argue with -- it bypassed h_sigma/v_sigma, adaptive_weight, the sparse
                # settings and update_constraint alike, so the one case where a
                # constraint is weakest was also the one where it was applied hardest.
                for station in to_constrain:
                    ke = np.zeros((1, total_parameters * 3))
                    kn = np.zeros((1, total_parameters * 3))
                    ku = np.zeros((1, total_parameters * 3))

                    target_idx, _ = self._get_target_cols(station, constraining)

                    ke[0, target_idx] = 1
                    kn[0, target_idx + total_parameters] = 1
                    ku[0, target_idx + total_parameters * 2] = 1

                    constraint_params = ConstraintEquation(
                        station=station,
                        constraint_design=(ke, kn, ku),
                        constraint_sigma=np.array([self.h_sigma, self.h_sigma,
                                                   self.v_sigma])
                    )

                    self.equations.append(constraint_params)

        self._is_collected = True

    def constrain_nearby_stations(self, all_stations: List[Station], total_parameters: int):
        """Couple parameters of nearby stations (d < 5 km)."""
        x, y = np.array([stn.projected_coords for stn in all_stations]).T
        r, _, _ = get_radius(np.column_stack([x, y]),
                             np.column_stack([x, y]))

        # get pairs with distance < 5 km
        i, j = np.where(np.triu(r < 5, k=1))
        # Print the pairs
        for idx_i, idx_j in zip(i, j):

            target_idx, idx = self._get_target_cols(all_stations[idx_i], [all_stations[idx_j]])

            if target_idx is not None and idx[0] is not None:
                logger.info(f'Stations {stationID(all_stations[idx_i])} and '
                            f'{stationID(all_stations[idx_j])} are only '
                            f'{r[idx_i, idx_j]:.3f} km apart: linking their '
                            f'{self.short_description()} parameters')

                ke = np.zeros((1, total_parameters * 3))
                kn = np.zeros((1, total_parameters * 3))
                ku = np.zeros((1, total_parameters * 3))

                # first site
                ke[0, target_idx] = -1
                kn[0, target_idx + total_parameters] = -1
                ku[0, target_idx + total_parameters * 2] = -1
                # second site
                ke[0, idx] = 1
                kn[0, idx + total_parameters] = 1
                ku[0, idx + total_parameters * 2] = 1

                constraint_params = ConstraintEquation(
                    station=all_stations[idx_i],
                    constraint_design=(ke, kn, ku),
                    constraint_sigma=np.array([1e-4, 1e-4, 1e-4])
                )

                self.equations.append(constraint_params)

    def apply_to_normal_equations(self, total_parameters: int) -> np.ndarray:
        """
        Apply all constraints to the normal equation matrix.
        Returns: constraint contribution to NEQ.
        """
        if not self._is_collected:
            raise ValueError("Constraints must be collected before applying")

        # Count active equations first
        n_active = sum(1 for eq in self.equations if eq.is_active)
        # Pre-allocate
        k = np.zeros((n_active * 3, total_parameters * 3))

        idx = 0
        for equation in self.equations:
            if equation.is_active:
                # Build K matrix for this constraint
                ke, kn, ku = equation.constraint_design
                se, sn, su = equation.constraint_sigma
                # do not square! will get squared when doing k.T @ k
                k[idx:idx+3, :] = np.vstack((ke * (1/se), kn * (1/sn), ku * (1/su)))
                idx += 3

        # return contribution
        return k.T @ k

    def update_weights(self, new_h_sigma: float = None, new_v_sigma: float = None):
        """Update constraint weights (for refinement iterations)."""
        tqdm.write(f'Updating weight for {self.short_description()} '
                   f'current: {self.h_sigma:.6f} {self.v_sigma:.6f} '
                   f'new: {new_h_sigma:.6f} {new_v_sigma:.6f}')

        if new_h_sigma is not None:
            self.h_sigma = new_h_sigma
        if new_v_sigma is not None:
            self.v_sigma = new_v_sigma

        # Update all parameter sigmas
        for param in self.equations:
            param.constraint_sigma = np.array([self.h_sigma, self.h_sigma, self.v_sigma])

    @abstractmethod
    def _build_k_matrix(self, station: Station, constraining: List[Station],
                        grids: 'GridSystem', total_parameters: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        pass

    @abstractmethod
    def _get_target_cols(self, station: Station, constraining: List[Station]):
        """
        Abstract method to obtain which columns of the normal equations
        belong to each parameter (so it is constraint dependent).
        """
        pass

    @abstractmethod
    def short_description(self) -> str:
        """Return a short human-readable description of the constraint."""
        pass
