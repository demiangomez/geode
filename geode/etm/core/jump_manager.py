"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/13/25 5:22 PM
Author: Demian D. Gomez
"""
from typing import List, Tuple, Union
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ...Utils import print_yellow
from ..core.etm_config import EtmConfig
from ..core.type_declarations import JumpType
from ..etm_functions.jumps import JumpFunction
from ..etm_functions.auto_jumps import AutoJumps
from ..data.solution_data import SolutionData
from ...pyDate import Date


class JumpManager:
    """Comprehensive jump management system"""

    def __init__(self, solution_data: SolutionData, config: EtmConfig):
        self.config = config
        self.jumps: List[JumpFunction] = []
        # max and min dates
        # check if user has selected a windowed fit
        mask = np.where(self.config.modeling.get_observation_mask(solution_data.time_vector))[0]
        self.date_min = np.array(solution_data.date)[mask].min()
        self.date_max = np.array(solution_data.date)[mask].max()
        # save for autodetect jumps
        self.solution_data = solution_data

    def build_jump_table(self, time_vector: np.ndarray,
                         observations: List[np.ndarray]) -> None:
        """
        Build complete jump table including database user_jumps and auto-detected user_jumps

        Args:
            time_vector: Time vector for jump evaluation
            observations: neu list of observations to apply auto-jumps (if selected)
        """
        logger.info("Loading earthquake jump table")
        # Load earthquake-based user_jumps
        self._load_earthquake_jumps(time_vector)

        # Load mechanical/generic user_jumps
        logger.info("Loading manual jump table")
        self._load_manual_jumps(time_vector)

        # load metadata changes
        logger.info("Loading metadata jump table")
        self._load_mechanical_jumps(time_vector)

        # Add automatic jump detection
        if self.config.modeling.fit_auto_detected_jumps:
            auto_jumps = AutoJumps(self.config,
                                   method=self.config.modeling.fit_auto_detected_jumps_method)
            for jump in auto_jumps.detect(time_vector, observations):
                self.add_jump(jump)

        # Validate final jump configuration
        # self._validate_jump_constraints(time_vector)

        logger.info(f"Final jump table: {len(self.get_active_jumps())} active jumps of {len(self.jumps)}")
        for jump in self.jumps:
            logger.info(str(jump) if jump.p.jump_type != JumpType.AUTO_DETECTED else print_yellow(str(jump)))

    def _load_earthquake_jumps(self, time_vector: np.ndarray) -> None:
        """Load earthquake-based user_jumps from catalog"""

        if isinstance(self.config.modeling.post_seismic_back_lim, Date):
            sdate = self.config.modeling.post_seismic_back_lim
        else:
            sdate = self.date_min - self.config.modeling.post_seismic_back_lim

        # this is the second (and final) filter applied to the catalog earthquakes already
        # fetched into config.modeling.earthquake_jumps (see EtmConfig._load_jump_config): an
        # earthquake only becomes a jump here if it falls within the actual observation window
        # [date_min, date_max], or -- for M>=7 events -- within the wider [sdate, date_max]
        # back-limit window. anything else is silently skipped, so log what gets through.
        logger.info(f"Earthquake jump window: observations span {self.date_min} to {self.date_max}, "
                   f"M>=7 back-limit {sdate}; evaluating {len(self.config.modeling.earthquake_jumps)} "
                   f"catalog earthquake(s)")

        added = 0
        for eq in self.config.modeling.earthquake_jumps:
            if (self.date_min <= eq.date <= self.date_max or (sdate <= eq.date <= self.date_max
                    and eq.magnitude >= 7)):

                jump = JumpFunction(
                    config=self.config,
                    metadata=eq.build_metadata(),
                    time_vector=time_vector,
                    date=eq.date,
                    magnitude=eq.magnitude,
                    epi_distance=eq.distance,
                    jump_type=eq.jump_type,
                    fit=self.config.modeling.fit_earthquakes,
                    earthquake=eq
                )

                self.add_jump(jump, self.config.modeling.check_jump_collisions)
                added += 1

        logger.info(f"Added {added} of {len(self.config.modeling.earthquake_jumps)} "
                   f"catalog earthquake(s) to the jump table")

    def _load_mechanical_jumps(self, time_vector: np.ndarray) -> None:
        """Load mechanical user_jumps from station metadata and database"""
        # Load antenna changes from station info
        try:
            records = self.config.metadata.station_information
            added = 0

            for i, record in enumerate(records[1:], 1):
                prev_record = records[i - 1]

                # Check if equipment actually changed
                if (prev_record['AntennaCode'] != record['AntennaCode'] or
                        prev_record['RadomeCode'] != record['RadomeCode']):

                    if prev_record['RadomeCode']:
                        pre_ant = prev_record['AntennaCode'] + ' ' + prev_record['RadomeCode']
                    else:
                        pre_ant = prev_record['AntennaCode'] + ' NONE'

                    if record['RadomeCode']:
                        new_ant = record['AntennaCode'] + ' ' + record['RadomeCode']
                    else:
                        new_ant = record['AntennaCode'] + ' NONE'

                    # Check database for manual override
                    jump = JumpFunction(
                        config=self.config,
                        metadata=f'Antenna: {pre_ant}->{new_ant}',
                        time_vector=time_vector,
                        date=record['DateStart'],
                        jump_type=JumpType.MECHANICAL_ANTENNA,
                        fit=self.config.modeling.fit_metadata_jumps
                    )

                    # jump outside the region with data, deactivate
                    if not self.date_min < record['DateStart'] < self.date_max:
                        jump.remove_from_fit()

                    self.add_jump(jump)
                    added += 1

            logger.info(f"Added {added} mechanical (antenna/radome) jump(s) from station info "
                       f"({len(records)} station info record(s) checked)")

        except Exception as e:
            logger.warning(f"Could not load station info for mechanical jumps: {e}")

    def _validate_jump_constraints(self, time_vector: np.ndarray) -> None:
        """Validate final jump configuration and apply constraints"""
        active_jumps = self.get_active_jumps()

        # Check last jump has sufficient data
        if active_jumps:
            last_jump = None
            for jump in reversed(active_jumps):
                if jump.fit:
                    last_jump = jump
                    break

            if last_jump:
                # Check data availability after last jump
                data_after_jump = np.sum(time_vector > last_jump.date.fyear)
                min_data_required = max(
                    self.config.validation.min_data_for_jump,
                    last_jump.param_count * 10  # Heuristic: 10 observations per parameter
                )

                if data_after_jump < min_data_required:
                    logger.warning(
                        f"Insufficient data after last jump {last_jump.date.yyyyddd()}: "
                        f"{data_after_jump} < {min_data_required}"
                    )
                    # Convert complex jump to simple jump
                    if last_jump.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
                        last_jump.p.jump_type = JumpType.COSEISMIC_ONLY
                        last_jump.param_count = 1
                        last_jump.design = last_jump._create_design_matrix(time_vector)
                        last_jump.rehash()

    def get_active_jumps(self) -> List[JumpFunction]:
        """Get list of user_jumps that are fitted"""
        return [jump for jump in self.jumps if jump.fit]

    def get_active_mechanical_jumps(self) -> List[JumpFunction]:
        """Get list of user_jumps that are fitted"""
        return [jump for jump in self.jumps if jump.fit and jump.p.jump_type < JumpType.COSEISMIC_JUMP_DECAY]

    def get_jump_functions(self) -> List[JumpFunction]:
        return [jump for jump in self.jumps]

    def get_geophysical_jump(
            self, id_or_jump: Union[JumpFunction, str]) -> Union[JumpFunction, None]:
        if isinstance(id_or_jump, JumpFunction):
            event_id = id_or_jump.earthquake.id
        else:
            event_id = id_or_jump

        for jump in self.jumps:
            if (jump.is_geophysical() and jump.earthquake is not None
                    and jump.earthquake.id == event_id):
                return jump

        return None

    def get_parameter_count(self) -> int:
        """Get total parameter count for active user_jumps"""
        return sum(jump.param_count for jump in self.jumps if jump.fit)

    def add_jump(self, jump: JumpFunction, check_jump_collisions=True) -> None:
        """
        Add jump with conflict resolution
        parameter check_jump_collisions controls if we should check or not for any collisions / problems
        for fitting jumps in the time series. Intended for stacking etm objects
        """
        # Check for conflicts with existing user_jumps
        if check_jump_collisions:
            # debug info
            logger.debug(f'Adding jump: {jump.date} {jump.p.jump_type}')

            for existing_jump in self.jumps:
                if existing_jump.fit:
                    is_equivalent, preferred = existing_jump.__eq__(jump)
                    if is_equivalent:
                        if preferred is jump:
                            existing_jump.remove_from_fit()
                        else:
                            # if the jump that is going to be deactivated is an auto jump,
                            # do not add it to the list!
                            if jump.p.jump_type == JumpType.AUTO_DETECTED:
                                logger.info('DETECTED BUT REMOVED: ' + str(jump))
                                return

                            jump.remove_from_fit()
                        break


        if jump.fit:
            # if jump got deactivated, do not bother to find it, some other jump already won
            user_action, user_type = self._get_jump_action(jump)

            # deactivate jump if user selected decided to
            if not self._should_fit_jump(user_action):
                jump.remove_from_fit(user_action=user_action)

            # check that type match! if types don't match, transform the jump
            if not jump.p.jump_type == user_type:
                jump.configure_behavior({'jump_type': user_type})

            if not jump.user_action == user_action:
                jump.user_action = user_action

        # logger.info(str(jump) if jump.p.jump_type != JumpType.AUTO_DETECTED else print_yellow(str(jump)))

        self.jumps.append(jump)

        self.jumps.sort()

    def _get_jump_action(self, jump) -> Tuple[str, JumpType]:
        """Get jump action override from database"""
        # find if user has activated or deactivated this event
        jump_params = self.config.modeling.get_user_jump(jump.date, jump.p.jump_type)
        if jump_params:
            return jump_params.action, jump_params.jump_type
        else:
            return 'A', jump.p.jump_type

    def _should_fit_jump(self, user_action: str) -> bool:
        """Determine if mechanical jump should be fitted. To decide in case of geophysical jump, if user action is +
        and the jump action is A, then deactivate it because it will be added later on as a manual jump by
        _load_manual_jumps
        """
        if user_action == '+':
            return True
        elif user_action == '-':
            return False
        else: # any other case, set user_action == 'A'
            return self.config.modeling.fit_generic_jumps

    def _load_manual_jumps(self, time_vector: np.ndarray) -> None:
        """Load manually specified user_jumps from database"""
        # Implementation for loading manual user_jumps
        jump_dates = [jump.date for jump in self.jumps]
        added = 0
        for j in self.config.modeling.user_jumps:
            # if jump date not in table already, then add it
            if j.date not in jump_dates:
                if j.jump_type == JumpType.MECHANICAL_MANUAL:
                    metadata = 'Manual mechanic jump'
                    earthquake = None
                else:
                    metadata = 'Manual geophysical jump'
                    earthquake = None
                    # try to find a match (using the date) with one of the earthquakes
                    for eq in self.config.modeling.earthquake_jumps:
                        if eq.date == j.date:
                            earthquake = eq
                            break

                jump = JumpFunction(
                    config=self.config,
                    time_vector=time_vector,
                    date=j.date,
                    jump_type=j.jump_type,
                    user_action=j.action,
                    metadata=metadata,
                    fit=self.config.modeling.fit_generic_jumps,
                    earthquake=earthquake
                )
                self.add_jump(jump)
                added += 1

        logger.info(f"Added {added} of {len(self.config.modeling.user_jumps)} "
                   f"manual jump override(s) (rest already covered by an existing jump)")

    def validate_all_jumps(self) -> List[str]:
        """Validate all user_jumps and return issues"""
        all_issues = []
        for jump in self.jumps:
            issues = jump.validate_parameters()
            all_issues.extend(issues)
        return all_issues

    def get_first_geophysical(self) -> Union[JumpFunction, None]:
        for jump in self.jumps:
            if jump.fit and jump.p.jump_type >= JumpType.COSEISMIC_JUMP_DECAY:
                return jump

        return None
