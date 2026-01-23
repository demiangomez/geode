from datetime import datetime
from typing import Optional, Union, List, Tuple, Dict, Any
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ...Utils import crc32
from ...pyDate import Date
from ..etm_functions.etm_function import EtmFunction
from ..core.etm_config import EtmConfig
from ..core.type_declarations import JumpType
from ..core.data_classes import Earthquake


class JumpFunction(EtmFunction):
    """Enhanced jump function with improved management and validation"""
    def __init__(self, config: EtmConfig,
                 time_vector: np.ndarray,
                 date: Union[Date, datetime],
                 jump_type: JumpType = JumpType.MECHANICAL_MANUAL,
                 magnitude: Optional[int] = 0,
                 epi_distance: Optional[float] = 0,
                 user_action: Optional[str] = 'A',
                 fit: bool = True,
                 earthquake: Earthquake = None, **kwargs):

        self.date = date
        self.magnitude = magnitude
        self.epi_distance = epi_distance
        self.user_action = user_action
        self.earthquake = earthquake
        # start with was_modified = False to indicate that the ETM did not alter this jump yet
        self.was_modified = False

        super().__init__(config, time_vector=time_vector,
                         date=date,
                         jump_type=jump_type,
                         magnitude=magnitude,
                         epi_distance=epi_distance,
                         user_action=user_action,
                         fit=fit, **kwargs)

    def initialize(self, time_vector: np.ndarray, date: Union[Date, datetime],
                   jump_type: JumpType = JumpType.MECHANICAL_MANUAL,
                   magnitude: Optional[int] = 0,
                   epi_distance: Optional[float] = 0,
                   user_action: Optional[str] = 'A',
                   **kwargs) -> None:
        """Initialize jump-specific parameters"""
        self.p.object = 'jump'
        self._time_vector = time_vector

        # Jump identification
        self.date = date if isinstance(date, Date) else Date(datetime=date)
        self.p.jump_date = self.date.datetime()
        self.p.jump_type = jump_type

        # Jump properties
        self.magnitude = magnitude
        self.user_action = user_action
        self.epi_distance = epi_distance

        # Relaxation parameters for postseismic user_jumps
        self.p.relaxation = self._setup_relaxation(jump_type)

        # Parameter counting and design matrix setup
        self._setup_parameter_count()
        # fill the parameter and sigma vectors with nans. This helps the creation of
        # empty objects for constraints
        for j in range(3):
            self.p.params[j] = np.array([np.nan] * self.param_count)
            self.p.sigmas[j] = np.array([np.nan] * self.param_count)

        self.design = self._create_design_matrix(time_vector) if self.fit else np.array([])

        # Validation and final setup
        self._validate_jump_configuration(time_vector)

        self._fill_metadata()

    def _fill_metadata(self) -> None:
        self.p.param_metadata = self.p.jump_type.description

        params = ','.join([f'a{i}' for i in range(self.p.relaxation.size)])

        if self.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
            self.p.param_metadata += f':[n:[b,{params}]],[e:[b,{params}]],[u:[b,{params}]]'
        elif self.p.jump_type == JumpType.POSTSEISMIC_ONLY:
            self.p.param_metadata += f':[n:[{params}]],[e:[{params}]],[u:[{params}]]'
        elif self.p.jump_type in (JumpType.REFERENCE_FRAME, JumpType.COSEISMIC_ONLY,
                                  JumpType.MECHANICAL_ANTENNA, JumpType.MECHANICAL_MANUAL):
            self.p.param_metadata += ':[n:[b]],[e:[b]],[u:[b]]'

    def _setup_relaxation(self, jump_type: JumpType) -> np.ndarray:
        """Setup relaxation parameters based on jump type.
        Find jump in the list user jumps for a personalized relaxation time"""
        if jump_type >= JumpType.COSEISMIC_JUMP_DECAY:
            # try to find the jump in the config
            jump_params = self.config.modeling.get_user_jump(self.date, jump_type)
            if jump_params is None or jump_params.action == '-':
                return self.config.modeling.relaxation.copy()
            elif isinstance(jump_params.relaxation, (list, tuple)):
                return np.array(jump_params.relaxation)
            elif isinstance(jump_params.relaxation, (int, float)):
                return np.array([jump_params.relaxation])
            else:
                return jump_params.relaxation.copy()
        else:
            return np.array([])

    def _setup_parameter_count(self) -> None:
        """Setup parameter count based on jump type and relaxation"""
        self.param_count = 1  # Base jump parameter

        if self.p.jump_type in (JumpType.COSEISMIC_JUMP_DECAY, JumpType.POSTSEISMIC_ONLY):
            self.param_count += len(self.p.relaxation)  # Add relaxation parameters

        if self.p.jump_type == JumpType.POSTSEISMIC_ONLY:
            self.param_count -= 1  # No instantaneous jump, only decay

        self.nr = len(self.p.relaxation)  # Number of relaxation terms

    def _create_design_matrix(self, time_vector: np.ndarray) -> np.ndarray:
        """Create design matrix for the jump"""
        if not self.fit or time_vector.size == 0:
            return np.array([])

        # Basic step function
        step_function = self._create_step_function(time_vector)

        if self.p.jump_type == JumpType.COSEISMIC_ONLY:
            return step_function
        elif self.p.jump_type == JumpType.POSTSEISMIC_ONLY:
            return self._create_logarithmic_decay(time_vector)
        elif self.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
            decay_matrix = self._create_logarithmic_decay(time_vector)
            if decay_matrix.size > 0:
                return np.column_stack((step_function, decay_matrix))
            else:
                # Fallback to jump only if decay can't be computed
                self.p.jump_type = JumpType.COSEISMIC_ONLY
                self.param_count = 1
                return step_function

        else:
            return step_function

    def _create_step_function(self, time_vector: np.ndarray) -> np.ndarray:
        """Create step function component"""
        step = np.zeros((time_vector.shape[0], 1))
        step[time_vector > self.date.fyear] = 1.0
        return step

    def _create_logarithmic_decay(self, time_vector: np.ndarray) -> np.ndarray:
        """Create logarithmic decay components for postseismic deformation"""
        if len(self.p.relaxation) == 0:
            return np.array([])

        decay_matrix = np.zeros((time_vector.shape[0], len(self.p.relaxation)))

        for i, relaxation_time in enumerate(self.p.relaxation):
            mask = time_vector > self.date.fyear
            if np.any(mask):
                decay_matrix[mask, i] = np.log10(
                    1.0 + (time_vector[mask] - self.date.fyear) / relaxation_time
                )

        return decay_matrix

    def _validate_jump_configuration(self, time_vector: np.ndarray) -> None:
        """Validate jump configuration and adjust if necessary"""
        if not self.fit:
            return

        # Check if jump is before time series start
        if self.date.fyear < time_vector.min():
            if self.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
                self.p.jump_type = JumpType.POSTSEISMIC_ONLY
                self.param_count -= 1
            elif self.p.jump_type == JumpType.COSEISMIC_ONLY:
                self.fit = False
                self.design = np.array([])
                return

        # Validate design matrix
        if self.design.size > 0:
            # Check for valid step function (some but not all values should be 1)
            if self.p.jump_type in (JumpType.COSEISMIC_ONLY, JumpType.COSEISMIC_JUMP_DECAY):
                step_col = self.design[:, 0]
                if np.all(step_col == 0) or np.all(step_col == 1):
                    self.fit = False
                    self.design = np.array([])
                    return

            # @ todo: analyze moving test in validate_design (now called just before doing the fit) here
            # Check condition number for combined jump+decay
            #if self.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY and self.design.shape[1] > 1:
            #    condition_num = np.log10(np.linalg.cond(self.design.T @ self.design))
            #    if condition_num > self.config.validation.max_condition_number:
            #        # Fallback to jump only
            #        self.p.jump_type = JumpType.COSEISMIC_ONLY
            #        self.param_count = 1
            #        self.design = self._create_step_function(time_vector)

    def get_relaxation_cols(self, relaxation: Union[float, List, np.ndarray] = None,
                            return_col_of_design_matrix: bool = True) -> List:
        """
        method to retrieve the column of a given relaxation (or all if None)
        if return_col_of_design_matrix then the returned index if that of the ETM design matrix
        if not return_col_of_design_matrix then the index is that
        returns cols in the order passed to the method
        """
        if relaxation is None:
            relaxation = self.p.relaxation
        elif isinstance(relaxation, float):
            relaxation = [relaxation]

        out_cols = []
        for relax in relaxation:
            idx = np.where(np.isin(self.p.relaxation, relax))[0].tolist()

            if len(idx) and self.fit:
                if return_col_of_design_matrix:
                    if self.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
                        out_cols.append(self.column_index[idx[0] + 1])
                    elif self.p.jump_type == JumpType.POSTSEISMIC_ONLY:
                        out_cols.append(self.column_index[idx[0]])
                else:
                    if self.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
                        out_cols.append(idx[0] + 1)
                    elif self.p.jump_type == JumpType.POSTSEISMIC_ONLY:
                        out_cols.append(idx[0])

        return out_cols

    def get_jump_col(self, return_col_of_design_matrix: bool = True) -> List:
        """
        method to retrieve the column of the jump
        if return_col_of_design_matrix then the returned index if that of the ETM design matrix
        if not return_col_of_design_matrix then the index is 0 (first element always)
        """
        if self.p.jump_type < JumpType.POSTSEISMIC_ONLY and self.fit:
            if return_col_of_design_matrix:
                out_col = self.column_index[0]
            else:
                out_col = 0
        else:
            out_col = None

        return out_col

    def get_design_ts(self, ts: np.ndarray) -> np.ndarray:
        """Generate design matrix for given time series"""
        return self._create_design_matrix(ts)

    def print_parameters(self) -> Tuple[list, list, list]:
        """Generate formatted parameter strings for N, E, U components"""
        # set the format string depending on jump type
        self.format_str = []

        if self.p.jump_type in (JumpType.COSEISMIC_JUMP_DECAY, JumpType.COSEISMIC_ONLY):
            self.format_str.append(f'{self.user_action} {self.date.yyyyddd()} {"":4}' + ' {:>7.1f}'
                                   + f' {self.magnitude:3.1f} {self.epi_distance:6.1f}')
        elif not self.p.jump_type == JumpType.POSTSEISMIC_ONLY:
            self.format_str.append(f'{self.user_action} {self.date.yyyyddd()} {"":4}' + ' {:>7.1f}')

        # add relaxation parameters if needed
        for relax in self.p.relaxation:
            self.format_str.append(f'{self.user_action} {self.date.yyyyddd()} {relax:4.2f}' + ' {:>7.1f}'
                                   + f' {self.magnitude:3.1f} {self.epi_distance:6.1f}')

        output_table = [[], [], []]

        for i in range(3):
            if not self.fit or not len(self.p.params):
                output_table[i].append((self.format_str[0].format(0), 'gray'))
            else:
                for j, param in enumerate(self.p.params[i]):
                    output_table[i].append((self.format_str[j].format(param * 1000.), self.p.jump_type.color))

        return output_table[0], output_table[1], output_table[2]

    def rehash(self) -> None:
        """Recompute hash for change detection"""
        hash_input = (f"{self.date}_{self.fit}_{self.param_count}_"
                      f"{self.p.jump_type}_{','.join(map(str, self.p.relaxation))}")
        self.p.hash = crc32(hash_input)

    def configure_behavior(self, behavior_config: Dict[str, Any]) -> None:
        """Configure jump-specific behavior"""
        super().configure_behavior(behavior_config)

        logger.debug(f'configuring behavior {behavior_config} ' + str(self))

        if 'relaxation' in behavior_config:
            new_relaxation = np.array(behavior_config['relaxation'])
            if not np.array_equal(new_relaxation, self.p.relaxation):
                self.p.relaxation = new_relaxation
                self._setup_parameter_count()
                if hasattr(self, '_time_vector'):
                    self.design = self._create_design_matrix(self._time_vector)

        if 'jump_type' in behavior_config:
            new_type = JumpType(behavior_config['jump_type'])
            if new_type != self.p.jump_type:
                if self.p.params:
                    # this code is meant to be executed for functions with parameters used as constraints
                    # params are filled in, remove superfluous elements
                    for i in range(3):
                        if new_type == JumpType.COSEISMIC_ONLY and self.p.jump_type != JumpType.POSTSEISMIC_ONLY:
                            # remove decays, leave only zero element
                            self.p.params[i] = self.p.params[i][0]
                            self.p.sigmas[i] = self.p.sigmas[i][0]
                        elif (new_type == JumpType.POSTSEISMIC_ONLY and
                              self.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY):
                            # leave the relaxations and remove the offset sd
                            self.p.params[i] = self.p.params[i][1:]
                            self.p.sigmas[i] = self.p.sigmas[i][1:]

                self.p.jump_type = new_type
                self._setup_parameter_count()

                if hasattr(self, '_time_vector'):
                    self.design = self._create_design_matrix(self._time_vector)
                    self._validate_jump_configuration(self._time_vector)


        if 'magnitude' in behavior_config:
            self.magnitude = float(behavior_config['magnitude'])

        self._fill_metadata()
        self.rehash()
        # flag this jump as have been modified by the ETN
        self.was_modified = True

    def remove_from_fit(self, user_action=None) -> None:
        """Remove jump from fitting process. If user_action provided, update the field"""
        self.fit = False
        self.design = np.array([])
        if user_action:
            self.user_action = user_action
        self.rehash()

    def validate_parameters(self) -> List[Tuple[EtmFunction, str]]:
        """Validate jump parameters"""
        issues = super().validate_parameters()

        if self.fit and len(self.p.params) > 0:
            # Check for unrealistic relaxation amplitudes but only when check_jump_collisions = True
            # otherwise leave ETM as is (this is needed when stacking ETMs)
            if self.config.modeling.check_jump_collisions:
                if self.p.jump_type in (JumpType.COSEISMIC_JUMP_DECAY, JumpType.POSTSEISMIC_ONLY):
                    max_amplitude = np.max(np.abs(np.array(self.p.params)[:, -self.p.relaxation.size:]))
                    if max_amplitude > self.config.validation.max_relaxation_amplitude:
                        issues.append((self, f"Unrealistic amplitude {max_amplitude:.3f} m: "
                                             f"{self.p.jump_type.description} {self.date.yyyyddd()}"))

        # Check relaxation values are positive
        if len(self.p.relaxation) > 0:
            if np.any(np.array(self.p.relaxation) <= 0):
                issues.append((self, f"{self.p.jump_type.description} {self.date.yyyyddd()}: "
                                     f"Relaxation times must be positive"))

        return issues

    def validate_design(self) -> List[Tuple[EtmFunction, str]]:
        issues = super().validate_design()
        # report condition number
        cond_num = np.log10(np.linalg.cond(self.design.T @ self.design))
        logger.debug(f'Condition number: {cond_num:.2f} ' + repr(self))

        # validate the design matrix
        if (cond_num >= self.config.validation.max_condition_number
                and self.p.jump_type in (JumpType.COSEISMIC_JUMP_DECAY, JumpType.POSTSEISMIC_ONLY)
                and self.fit):
            issues.append((self, f"Condition number too large for jump "
                                 f"{self.p.jump_type.description} {self.date.yyyyddd()} ({cond_num:.2f})"))
        return issues

    def eval(self, component: int,
             override_time_vector: np.ndarray = None,
             override_params: np.ndarray = None,
             remove_postseismic = False):
        """Implementation only removed jumps, not decay"""

        if (self.p.jump_type == JumpType.POSTSEISMIC_ONLY and not remove_postseismic or
                np.all(np.isnan(self.p.params[component])) or
                (self.design.size == 0 and override_time_vector is None)):
            return 0

        if override_time_vector is not None:
            design = self.get_design_ts(override_time_vector)
        else:
            design = self.design

        if self.p.jump_type not in (JumpType.COSEISMIC_ONLY, JumpType.MECHANICAL_MANUAL,
                                    JumpType.MECHANICAL_ANTENNA, JumpType.REFERENCE_FRAME, JumpType.UNDETERMINED):
            # to return a 2d array
            #design = design[:,[0]]
            pass

        if override_params is not None:
            return design @ override_params[component]
        else:
            return design @ self.p.params[component]

    def __lt__(self, other: 'JumpFunction') -> bool:
        """Enable sorting of user_jumps by date"""
        return self.date.fyear < other.date.fyear

    def __eq__(self, other: 'JumpFunction') -> Tuple[bool, Optional['JumpFunction']]:
        """
        Compare two user_jumps for equivalence and return decision on which to keep
        Returns (is_equivalent, preferred_jump)
        """
        if not isinstance(other, JumpFunction):
            raise ValueError("Can only compare JumpFunction objects")

        # Handle non-fitting user_jumps
        if not self.fit and other.fit:
            return True, other
        elif self.fit and not other.fit:
            return True, self
        elif not self.fit and not other.fit:
            return False, None

        # Both user_jumps are fitted - check for conflicts
        lt_earthquake_min_days = abs(other.date - self.date) <= self.config.modeling.earthquake_min_days
        lt_jump_min_days = abs(other.date - self.date) <= self.config.modeling.jump_min_days

        # Calculate data overlap
        design_overlap = 0
        if self.design.size > 0 and other.design.size > 0:
            design_overlap = np.sum(np.logical_xor(self.design[:, 0], other.design[:, 0]))

        # @ todo: implement a smarter method to remove jumps using condition number
        # this flag is used to decide when both are geophysical
        lt_design_eq_min_days = design_overlap <= self.config.modeling.earthquake_min_days
        # if one is geophysical but the other is not, use this flag
        lt_design_jump_min_days = design_overlap <= self.config.modeling.jump_min_days

        logger.debug(f'Comparing: {self.date} {self.p.jump_type} with {other.date} {other.p.jump_type}')

        logger.debug(f'lt_design_eq_min_days  : {lt_design_eq_min_days} '
                     f'with design_overlap: {design_overlap} and '
                     f'earthquake_min_days: {self.config.modeling.earthquake_min_days}')

        logger.debug(f'lt_design_jump_min_days: {lt_design_jump_min_days} with '
                     f'jump_min_days: {self.config.modeling.jump_min_days}')

        # Decision logic based on jump types and data availability
        if self.is_geophysical() and other.is_geophysical():
            # Two geophysical user_jumps
            # there are more than two weeks of data to constrain params, return false (not equal)
            # otherwise, decide based on the magnitude of events
            if lt_earthquake_min_days or lt_design_eq_min_days:
                # Insufficient data separation, by date or data points - choose by magnitude if jump happened after
                # start of the data. Otherwise, (before the start of data) leave most recent
                if other.date.fyear < self._time_vector.min() and self.date.fyear < self._time_vector.min():
                    logger.debug('Decision made based on date')
                    return True, self if self.date > other.date else other
                else:
                    logger.debug('Decision made based on magnitude')
                    return True, self if self.magnitude > other.magnitude else other
            else:
                return False, None  # Can coexist
        elif self.is_geophysical() and not other.is_geophysical():
            if lt_design_jump_min_days:
                return True, self  # geophysical prevails
            else:
                return False, None # Can coexist
        elif not self.is_geophysical() and other.is_geophysical():
            if lt_design_jump_min_days:
                return True, other  # geophysical prevails
            else:
                return False, None  # Can coexist
        else:
            # Two mechanical/generic user_jumps
            if lt_jump_min_days or lt_design_jump_min_days:
                if other.p.jump_type != JumpType.AUTO_DETECTED:
                    return True, other  # Prefer the latest jump (if latest is not an auto jump)
                else:
                    return True, self
            else:
                return False, None  # Can coexist

    def is_geophysical(self) -> bool:
        """Check if jump is geophysical, i.e. if >= COSEISMIC_JUMP_DECAY"""
        return self.p.jump_type >= JumpType.COSEISMIC_JUMP_DECAY

    def short_name(self) -> str:
        if not self.p.jump_type == JumpType.POSTSEISMIC_ONLY:
            name = [f'{"OFFSET":>10}']
        else:
            name = []

        for r in self.p.relaxation:
            name.append(f'{"RELAX " + f"{r:.1f}":>10}')

        return ' '.join(name)

    def __str__(self) -> str:
        """String representation for debugging"""
        out_str = [f"{self.user_action} {self.date.yyyyddd()}",
                   f"{self.p.jump_type.description:27s}",
                   f"fit: {str(self.fit)[0]:1s}",
                   f"params: {self.param_count}"]

        if self.p.jump_type >= JumpType.COSEISMIC_JUMP_DECAY:
            out_str.append(f"mag: {self.magnitude:.1f}")
            out_str.append(f"epi_dist: {self.epi_distance:6.1f} km")
            if self.earthquake is not None:
                out_str.append(f"id: {self.earthquake.id:32s}")
            out_str.append(f"relax: [" + " ".join([f'{item:.2f}' for item in self.p.relaxation.tolist()]) + "]")

        return '; '.join(out_str)

    def __repr__(self) -> str:
        return f"JumpFunction({str(self)})"

