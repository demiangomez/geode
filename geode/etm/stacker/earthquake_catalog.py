"""
Earthquake catalog bookkeeping for the stacker: which events are modeled, which
jumps/masks they contribute, and registering the coseismic/postseismic constraints
that follow from them.
"""

import logging
from typing import List
import math
from datetime import datetime as _dt

import numpy as np
from tqdm import tqdm

from . import console

from . import etm_builder
from .constraints import InterseismicConstraint, CoseismicConstraint, PostseismicConstraint
from .constraints import postseismic as postseismic_module
from .constraints.fault_geometry import FaultGeometry
from ..core.data_classes import Earthquake
from ..core.s_score import batch_score_scan
from ..core.type_declarations import JumpType
from ...dbConnection import Cnn
from ...pyDate import Date
from ...pyOkada import Mask
from ...Utils import stationID, print_yellow

logger = logging.getLogger(__name__)


class EarthquakeCatalogMixin:
    """Mixed into EtmStacker: owns self.earthquakes / self.collided_earthquakes."""

    def prepare_earthquake_list(self, cnn: Cnn, stnlist: list) -> None:
        """
        Pre-compute the canonical earthquake list before any station ETMs are fitted.

        Scans every station in stnlist in one batched query (see
        core.s_score.batch_score_scan) with the configured magnitude_limit to find all
        events with s-score > 0.  Events provided
        via config.earthquakes_cherry_picked (--force) are merged on top without
        a magnitude-limit check (identical to per-station behaviour).

        The union is deduplicated by event ID, sorted, and collision-windowed using
        the same 10-day rule as _record_earthquakes.  Collision losers are excluded
        from cherry_picked entirely (they will not appear in any station ETM).
        Results are written to:
          - self.config.earthquakes_cherry_picked  (surviving event IDs only)
          - self.collided_earthquakes              (IDs of excluded losers, for bookkeeping)

        _record_earthquakes will therefore skip the collision step.
        """
        # date range for the scan
        if isinstance(self.config.post_seismic_back_lim, Date):
            sdate = self.config.post_seismic_back_lim
        else:
            # float = years back from station start; use conservative fallback
            sdate = Date(year=1975, doy=1)
        edate = Date(datetime=_dt.utcnow())

        console.phase('earthquake catalogue')
        console.info(f'Scanning {len(stnlist)} station(s) for events with '
                     f'mag >= {self.config.earthquake_magnitude_limit}')

        # Batch every station's coordinates into one query instead of one round-trip
        # per station -- with 70+ stations this was a meaningful fraction of this
        # function's own wall-clock time before the per-event ScoreTable cost even
        # starts.
        values_list = ', '.join(
            f"""('{stn['NetworkCode']}', '{stn['StationCode']}')""" for stn in stnlist
        )
        coords = {}
        if values_list:
            rs = cnn.query_float(
                f'SELECT "NetworkCode", "StationCode", lat, lon FROM stations '
                f'WHERE ("NetworkCode", "StationCode") IN ({values_list})',
                as_dict=True
            )
            for row in rs:
                coords[(row['NetworkCode'], row['StationCode'])] = (float(row['lat']), float(row['lon']))

        # s-score scan for every station at once -- batch_score_scan does the same
        # per-(station, event) scoring ScoreTable does, but in a single query instead of
        # one full scan of the earthquakes table per station (see its own docstring).
        scan_stations = []
        for stn in stnlist:
            net, code = stn['NetworkCode'], stn['StationCode']
            if (net, code) not in coords:
                console.warn(f'no coordinates for {net}.{code}, skipping')
                continue
            lat, lon = coords[(net, code)]
            scan_stations.append((net, code, lat, lon))

        candidate_events, event_stations = batch_score_scan(
            cnn, scan_stations, sdate, edate, self.config.earthquake_magnitude_limit)
        seen_ids = {eq.id for eq in candidate_events}

        console.result('Events passing the s-score scan', f'{len(candidate_events)} from '
                   f'{len(stnlist)} stations')

        # merge force (cherry-picked) events on top
        if self.config.earthquakes_cherry_picked:
            for eid in self.config.earthquakes_cherry_picked:
                if eid in seen_ids:
                    continue
                rows = cnn.query_float(
                    f"SELECT * FROM earthquakes WHERE id = '{eid}'", as_dict=True
                )
                if not rows:
                    console.warn(f'cherry-picked event {eid} not found in the database')
                    continue
                j = rows[0]
                has_fm = not math.isnan(float(j['strike1']))
                strike = [float(j['strike1']), float(j['strike2'])] if has_fm else []
                dip    = [float(j['dip1']),    float(j['dip2'])]    if has_fm else []
                rake   = [float(j['rake1']),   float(j['rake2'])]   if has_fm else []
                candidate_events.append(Earthquake(
                    id=j['id'],
                    lat=float(j['lat']), lon=float(j['lon']),
                    date=Date(datetime=j['date']),
                    depth=int(j['depth']),
                    magnitude=float(j['mag']),
                    location=j['location'],
                    strike=strike, dip=dip, rake=rake,
                    jump_type=JumpType.COSEISMIC_JUMP_DECAY
                ))
                seen_ids.add(eid)
                console.info(f'added cherry-picked event {eid}')

        candidate_events.sort()

        # Exclude events with no focal mechanism — SW-Okada requires strike/dip/rake
        no_fm = [e for e in candidate_events if not e.strike]
        if no_fm:
            console.result('Excluded, no focal mechanism', f'{len(no_fm)}: '
                       + ', '.join(e.id for e in no_fm))
            candidate_events = [e for e in candidate_events if e.strike]

        console.result('Candidate events', f'{len(candidate_events)}')

        # collision detection: exclude the smaller event whenever two events are
        # within 15 days of each other, regardless of ordering.
        collision_window_days = 15
        for i, event_i in enumerate(candidate_events):
            for j, event_j in enumerate(candidate_events):
                if i >= j:
                    continue
                days_apart = abs(event_i.date.fyear - event_j.date.fyear) * 365.25
                if days_apart <= collision_window_days:
                    # Only a true collision when both events affect at least one
                    # common station.  Two events close in time but in different
                    # regions (no shared stations) are independent and should
                    # remain in the stacker separately.
                    shared = (event_stations.get(event_i.id, set()) &
                              event_stations.get(event_j.id, set()))
                    if not shared:
                        tqdm.write(
                            f'  Events {event_i.id} and {event_j.id} are '
                            f'{days_apart:.1f} days apart but have no common '
                            f'stations — not a collision'
                        )
                        continue

                    if event_i.magnitude >= event_j.magnitude:
                        loser, winner = event_j, event_i
                    else:
                        loser, winner = event_i, event_j

                    if loser.id not in self.collided_earthquakes:
                        tqdm.write(
                            f'WARNING: Earthquake collision detected between '
                            f'{event_i.id} (M{event_i.magnitude:.1f}, {event_i.date.yyyyddd():s}) and '
                            f'{event_j.id} (M{event_j.magnitude:.1f}, {event_j.date.yyyyddd():s}) '
                            f'({days_apart:.1f} days apart). '
                            f'Keeping {winner.id}, excluding {loser.id}.'
                        )
                        self.collided_earthquakes.add(loser.id)

        if self.collided_earthquakes:
            console.result('Excluded, collisions', f'{len(self.collided_earthquakes)}: '
                       + ', '.join(sorted(self.collided_earthquakes)))

        # publish results — collision losers are excluded entirely, not zero-tied
        kept = [e.id for e in candidate_events if e.id not in self.collided_earthquakes]
        self.config.earthquakes_cherry_picked = kept
        console.result('Events to be modelled', f'{len(kept)} '
                   f'({len(self.collided_earthquakes)} excluded due to collisions)')

    def _record_earthquakes(self):

        # open connection to database
        cnn = Cnn('gnss_data.cfg')

        for stn in self.stations:
            for jump in [jump for jump in stn.etm.jump_manager.jumps
                         if jump.is_geophysical() and jump.fit]:

                if jump.earthquake is None:
                    tqdm.write(f'Could not identify earthquake ID for station '
                               f'{stationID(stn)} for jump date {jump.date}')
                    continue

                if jump.earthquake not in self.earthquakes:
                    logger.info('Recording event ' + repr(jump))
                    self.earthquakes.append(jump.earthquake)
                    self.earthquakes.sort()

                # The mask is a property of the GRID, not of the event list, so it is
                # keyed off whether this grid has it rather than off whether the event
                # is new. build_system creates a fresh GridSystem every time it runs,
                # so on a rebuild (a station added or removed) every event is already
                # in self.earthquakes while the new grid has no masks at all -- tying
                # the two together left FaultGeometry reading an empty dict.
                if jump.earthquake.id not in self.grids.earthquake_masks:
                    lon, lat = self.grids.interpolation_geographic

                    mask = Mask(cnn, jump.earthquake.id)
                    s_score, p_score = mask.score(lat, lon)

                    # save the actual object to query it
                    self.grids.earthquake_masks[jump.earthquake.id] = (s_score > 0,
                                                                       p_score > 0, mask)

        # Collision detection was already performed in prepare_earthquake_list.
        # self.collided_earthquakes is pre-populated; no action needed here.

    def drop_unconstrainable_postseismic(self) -> List[str]:
        """
        Stop estimating the postseismic decay of any event no station can resolve, and
        rebuild the normal equations without those columns. Returns the event ids
        dropped.

        An event that no station caught early (fewer than two that
        constraints.postseismic.classify_station calls 'constraining') has no
        observable decay anywhere in the network. Left alone, every station still
        carries a free decay column for it, and BaseConstraint falls back to tying all
        of them to zero. That fallback is worse than it looks:

          - It is not neutral. The column stays nearly collinear with velocity, and the
            tie only bounds it to within its own sigma, so each such event can still
            absorb that much velocity -- times six to ten events per station in a
            catalogue reaching back to 1990.
          - It fakes information. constraints/separability.py reads a tightly-tied
            parameter as KNOWN, so the more unresolvable events are carried, the better
            a station's velocity looks separated from its decays. Exactly backwards.

        Dropping the column does not recover the transient -- that signal is in the
        data either way and still lands in the velocity. It stops the model claiming
        otherwise, which is the prerequisite for reporting the velocity's uncertainty
        honestly.

        A jump that also has a coseismic step keeps it: the step is observable whenever
        a station straddles the event, and is not collinear with a trend. Only the decay
        goes.
        """
        if not self.config.drop_unconstrainable_postseismic:
            return []

        dropped = []
        changed = set()
        for event in self.earthquakes:
            recent = postseismic_module.event_is_recent(event)
            resolvable = any(
                sum(1 for stn in self.stations
                    if postseismic_module.classify_station(stn, event, relax, recent)
                    == 'constraining') >= 2
                for relax in self.config.relaxation)
            if resolvable:
                continue

            demoted = 0
            for stn in self.stations + self.sparse_stations:
                jump = stn.etm.jump_manager.get_geophysical_jump(event.id)
                if jump is None or not jump.fit:
                    continue
                if jump.p.jump_type == JumpType.COSEISMIC_JUMP_DECAY:
                    # keep the step, drop the decay -- the same demotion
                    # JumpFunction already performs when a decay cannot be computed
                    jump.p.jump_type = JumpType.COSEISMIC_ONLY
                    jump.param_count = 1
                    demoted += 1
                    changed.add(id(stn))
                elif jump.p.jump_type == JumpType.POSTSEISMIC_ONLY:
                    # nothing but the decay, so there is nothing left to estimate
                    jump.fit = False
                    demoted += 1
                    changed.add(id(stn))
            if demoted:
                dropped.append(event.id)
                logger.info(f'{event.id}: no station can resolve its postseismic decay; '
                            f'dropped the decay term at {demoted} station(s)')

        if dropped:
            self._rebuild_all_normal_equations(changed)
            console.result('Postseismic terms dropped as unresolvable',
                           f'{len(dropped)} event(s): {", ".join(dropped)}')

        return dropped

    def _rebuild_all_normal_equations(self, changed: set):
        """Re-derive normal equations after some stations' designs changed.

        DesignMatrix.matrix is a property that reassigns column indices and rebuilds
        from the currently active functions, so demoting a jump shrinks that station's
        matrix -- and shifts parameter_start_idx for every station after it.

        Only the stations in `changed` (a set of id()) are recomputed. The rest keep
        their matrices and are merely renumbered. That distinction matters under
        gls_for_all_stations: _create_normal_equations builds a dense epoch-by-epoch
        covariance and Choleskys it, which for a station with 7786 daily solutions is a
        7786x7786 factorisation per component. Recomputing all of them to renumber a
        few would roughly double the most expensive step of the build for no gain.
        """
        self.total_parameters = 0
        self.total_equations = 0
        rebuilt = []
        for station, neq in zip(self.stations, list(self.normal_equations)):
            if id(station) in changed:
                rebuilt.append(self._create_normal_equations(station))
                continue
            neq.parameter_start_idx = self.total_parameters
            neq.parameter_range = np.arange(neq.parameter_count) + self.total_parameters
            station.normal_equations = neq
            self.total_parameters += neq.parameter_count
            self.total_equations += neq.equation_count
            rebuilt.append(neq)
        self.normal_equations = rebuilt

    def _register_constraints(self):
        """Register all constraint types, replacing any already registered.

        Every constraint object is rebuilt from the current self.stations, so this is
        also how a rebuild picks up an added or removed station.
        """
        self.constraint_registry.clear()

        # Interseismic
        self.constraint_registry.add_constraint(
            InterseismicConstraint(
                self.stations,
                self.config.interseismic_h_sigma,
                self.config.interseismic_v_sigma
            )
        )

        # record all earthquakes that might affect the ETMs
        self._record_earthquakes()

        # Must run here, not before _register_constraints: self.earthquakes is populated
        # by _record_earthquakes just above, so calling it any earlier iterates an empty
        # list and silently does nothing. It must still run before the loop below, which
        # builds the constraints whose station sets it changes.
        self.drop_unconstrainable_postseismic()

        # Coseismic and postseismic for each earthquake
        for event in self.earthquakes:
            # Skip collided earthquakes - they will be constrained to zero
            if event.id in self.collided_earthquakes:
                logger.info(f'Collided earthquake {event.id}: station count set to zero')

            # coseismic stations
            stations = [stn for stn in self.stations if stn.get_coseismic_column(event.id) is not None]
            # compute fault_geometry for this event (will use co or postseismic stations)
            fault_geometry = FaultGeometry(event, self.stations, np.max(self.config.relaxation), self.grids)

            if len(stations):
                coseis = CoseismicConstraint(
                    event, fault_geometry, stations, self.grids,
                    self.config.coseismic_h_sigma,
                    self.config.coseismic_v_sigma,
                    is_collision=event.id in self.collided_earthquakes
                )
                self.constraint_registry.add_constraint(coseis)
            else:
                console.warn(f'no station observed coseismic event {event.id}; '
                             f'no coseismic constraint added')

            # Postseismic for each relaxation
            for relax in self.config.relaxation:
                # Build a FaultGeometry from postseismic stations if coseismic had none
                stations = [stn for stn in self.stations if stn.get_postseismic_column(event.id, relax) is not None]

                if not stations:
                    tqdm.write(f'No stations for postseismic event {event.id} relax={relax:.3f}. '
                               f'Skipping postseismic constraint.')
                    continue

                self.constraint_registry.add_constraint(
                    PostseismicConstraint(
                        event, fault_geometry, stations, relax, self.grids,
                        self.config.postseismic_h_sigma,
                        self.config.postseismic_v_sigma,
                        is_collision=event.id in self.collided_earthquakes
                    )
                )

    def _recompute_affected_stations(self, cnn: Cnn, mask: Mask,
                                     json_folder: str = None, save_json_folder: str = None):
        """
        Rebuild the ETM (and normal equations) for every station whose s-score or
        p-score against `mask` is nonzero, and renumber parameter ranges for the rest.

        Shared by add_earthquake and remove_earthquake, which both need to redo this
        whenever the earthquake list changes which jumps a station's ETM must fit.
        """
        for i, stn in enumerate(self.stations):
            s_score, p_score = mask.score(stn.lat, stn.lon)
            if p_score > 0 or s_score > 0:
                tqdm.write(f'Recomputing etm for {stationID(stn)}')
                # replace old etm
                etm_result = etm_builder.build_etm(self.config, cnn, stn.network_code, stn.station_code,
                                                   json_folder, save_json_folder)
                stn.etm = etm_result[0]
                if stn.etm is None:
                    tqdm.write(print_yellow(
                        f'   ! could not rebuild ETM for {stationID(stn)}: '
                        f'{etm_result[2]}'))
                    continue
                # get dimensions of neq
                new_par_count = stn.etm.design_matrix.matrix.shape[1]
                # assume number of unknowns will change, so update the rest of the stations down from current
                remove_from_index = 0
                # will be added when calling _create_normal_equations
                self.total_parameters = 0
                self.total_equations = 0

                for station in self.stations:
                    if stationID(station) == stationID(stn):
                        # add the number of new parameters to remove_from_index
                        remove_from_index = station.normal_equations.parameter_count - new_par_count
                        # create a new normal equations object and replace current
                        self.normal_equations[i] = self._create_normal_equations(stn)
                    else:
                        # remove from the parameter range the station that has been removed
                        # this is applied to any stations that come after the removed site
                        station.normal_equations.parameter_range -= remove_from_index
                        station.normal_equations.parameter_start_idx -= remove_from_index
                        self.total_parameters += station.normal_equations.parameter_count
                        self.total_equations += station.normal_equations.equation_count

    def add_earthquake(self, event: Earthquake, json_folder: str = None, save_json_folder: str = None):
        """Add event to the list of modeled earthquakes."""

        # check the event is not already in the list
        if event in self.earthquakes:
            tqdm.write(f'Event {event.id} is already in the list of modeled events')
            return

        tqdm.write('Adding event ' + str(event))
        self.earthquakes.append(event)
        self.config.earthquakes_cherry_picked.append(f'{event.id}')
        self.earthquakes.sort()

        # get the mask
        # open connection to database
        cnn = Cnn('gnss_data.cfg')
        lon, lat = self.grids.interpolation_geographic

        # save a mask for the event
        mask = Mask(cnn, event.id)
        s_score, p_score = mask.score(lat, lon)

        tqdm.write(f'Getting mask for event {event.id}')
        s_score = s_score > 0
        p_score = p_score > 0
        # save the actual object to query it
        self.grids.earthquake_masks[event.id] = (s_score, p_score, mask)

        # figure out which stations need to be recomputed
        self._recompute_affected_stations(cnn, mask, json_folder, save_json_folder)

        # now add the constraint
        fault_geometry = FaultGeometry(event, self.stations, np.max(self.config.relaxation), self.grids)

        stations = [stn for stn in self.stations if stn.get_coseismic_column(event.id) is not None]
        if len(stations):
            coseis = CoseismicConstraint(
                event, fault_geometry, stations, self.grids,
                self.config.coseismic_h_sigma,
                self.config.coseismic_v_sigma,
                is_collision=event.id in self.collided_earthquakes
            )
            self.constraint_registry.add_constraint(coseis)
        else:
            console.warn(f'no station observed coseismic event {event.id}; '
                         f'no coseismic constraint added')

        # Postseismic for each relaxation
        for relax in self.config.relaxation:
            stations = [stn for stn in self.stations if stn.get_postseismic_column(event.id, relax) is not None]

            if not stations:
                tqdm.write(f'No stations for postseismic event {event.id} relax={relax:.3f}. '
                           f'Skipping postseismic constraint.')
                continue

            self.constraint_registry.add_constraint(
                PostseismicConstraint(
                    event, fault_geometry, stations, relax, self.grids,
                    self.config.postseismic_h_sigma,
                    self.config.postseismic_v_sigma,
                    is_collision=event.id in self.collided_earthquakes
                )
            )

        # Apply any non-default Okada regularization weight to this event's new
        # constraints too -- see stacker.build_system's own call for why coseismic and
        # postseismic are applied separately instead of sharing one value.
        if (self.config.coseismic_sw_okada_h_weight != 1.0 or
                self.config.coseismic_sw_okada_v_weight != 1.0):
            self.update_okada_weights(self.config.coseismic_sw_okada_h_weight,
                                      self.config.coseismic_sw_okada_v_weight,
                                      event_id=event.id, constraint_type='coseismic')
        if (self.config.postseismic_sw_okada_h_weight != 1.0 or
                self.config.postseismic_sw_okada_v_weight != 1.0):
            self.update_okada_weights(self.config.postseismic_sw_okada_h_weight,
                                      self.config.postseismic_sw_okada_v_weight,
                                      event_id=event.id, constraint_type='postseismic')

    def remove_earthquake(self, event: Earthquake, json_folder: str = None, save_json_folder: str = None):
        """Remove event from the list of modeled earthquakes."""

        # check the event is not already in the list
        if event in self.earthquakes:
            tqdm.write('Removing event ' + str(event))

            self.earthquakes.pop(self.earthquakes.index(event))
            self.config.earthquakes_cherry_picked.pop(self.config.earthquakes_cherry_picked.index(f'{event.id}'))
            self.earthquakes.sort()

            cnn = Cnn('gnss_data.cfg')

            # remove mask
            _, _, mask = self.grids.earthquake_masks[event.id]
            self.grids.earthquake_masks.pop(event.id)

            # figure out which stations need to be recomputed
            self._recompute_affected_stations(cnn, mask, json_folder, save_json_folder)

            # now remove the constraints
            for i, constraint in enumerate(self.constraint_registry.constraints['coseismic']):
                if constraint.event.id == event.id:
                    tqdm.write(f'Removed {constraint}')
                    self.constraint_registry.constraints['coseismic'].pop(i)
                    break

            # remove the as many postseismic constraints as we have
            while event.id in [c.event.id for c in self.constraint_registry.constraints['postseismic']]:
                for i, constraint in enumerate(self.constraint_registry.constraints['postseismic']):
                    if constraint.event.id == event.id:
                        tqdm.write(f'Removed {constraint}')
                        self.constraint_registry.constraints['postseismic'].pop(i)
                        break

        else:
            tqdm.write(f'Event {event.id} is not in the list of modeled events')
