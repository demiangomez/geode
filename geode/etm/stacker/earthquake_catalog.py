"""
Earthquake catalog bookkeeping for the stacker: which events are modeled, which
jumps/masks they contribute, and registering the coseismic/postseismic constraints
that follow from them.
"""

import math
from datetime import datetime as _dt
from typing import List

import numpy as np
from tqdm import tqdm

from . import etm_builder
from .constraints import InterseismicConstraint, CoseismicConstraint, PostseismicConstraint
from .constraints.fault_geometry import FaultGeometry
from ..core.data_classes import Earthquake
from ..core.s_score import ScoreTable
from ..core.type_declarations import JumpType
from ...dbConnection import Cnn
from ...pyDate import Date
from ...pyOkada import Mask
from ...Utils import stationID


class EarthquakeCatalogMixin:
    """Mixed into EtmStacker: owns self.earthquakes / self.collided_earthquakes."""

    def prepare_earthquake_list(self, cnn: Cnn, stnlist: list) -> None:
        """
        Pre-compute the canonical earthquake list before any station ETMs are fitted.

        For each station in stnlist, queries ScoreTable with the configured
        magnitude_limit to find all events with s-score > 0.  Events provided
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

        seen_ids: set = set()
        candidate_events: List[Earthquake] = []
        # Maps event_id -> set of station IDs (net.code) that see the event.
        # Used by the collision check: two close events only collide if they
        # share at least one common station.
        event_stations: dict = {}

        tqdm.write(f'Pre-computing earthquake list for {len(stnlist)} stations '
                   f'(mag >= {self.config.earthquake_magnitude_limit})...')

        # s-score scan for every station
        for stn in stnlist:
            net, code = stn['NetworkCode'], stn['StationCode']
            rs = cnn.query_float(
                'SELECT lat, lon FROM stations '
                f'WHERE "NetworkCode" = \'{net}\' AND "StationCode" = \'{code}\'',
                as_dict=True
            )
            if not rs:
                tqdm.write(f'  WARNING: Could not get coordinates for {net}.{code}, skipping')
                continue
            lat, lon = float(rs[0]['lat']), float(rs[0]['lon'])

            score = ScoreTable(cnn, net, code, lat, lon, sdate, edate,
                               magnitude_limit=self.config.earthquake_magnitude_limit,
                               include_all_events=True)
            for eq in score.table:
                if eq.id not in seen_ids:
                    seen_ids.add(eq.id)
                    candidate_events.append(eq)
                event_stations.setdefault(eq.id, set()).add(f'{net}.{code}')

        tqdm.write(f'  s-score scan: {len(candidate_events)} unique events from '
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
                    tqdm.write(f'  WARNING: Cherry-picked event {eid} not found in database')
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
                tqdm.write(f'  Added cherry-picked event {eid}')

        candidate_events.sort()

        # Exclude events with no focal mechanism — SW-Okada requires strike/dip/rake
        no_fm = [e for e in candidate_events if not e.strike]
        if no_fm:
            tqdm.write(f'  Excluding {len(no_fm)} event(s) with no focal mechanism: '
                       + ', '.join(e.id for e in no_fm))
            candidate_events = [e for e in candidate_events if e.strike]

        tqdm.write(f'  Total: {len(candidate_events)} candidate events')

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
            tqdm.write(f'  Collisions: {len(self.collided_earthquakes)} event(s) excluded: '
                       + ', '.join(sorted(self.collided_earthquakes)))

        # publish results — collision losers are excluded entirely, not zero-tied
        kept = [e.id for e in candidate_events if e.id not in self.collided_earthquakes]
        self.config.earthquakes_cherry_picked = kept
        tqdm.write(f'Earthquake list ready: {len(kept)} events '
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
                    tqdm.write('Recording event ' + repr(jump))
                    self.earthquakes.append(jump.earthquake)
                    self.earthquakes.sort()

                    lon, lat = self.grids.interpolation_geographic

                    # save a mask for the event
                    mask = Mask(cnn, jump.earthquake.id)
                    s_score, p_score = mask.score(lat, lon)

                    # tqdm.write(f'Getting mask for event {jump.earthquake.id}')
                    s_score = s_score > 0
                    p_score = p_score > 0
                    # save the actual object to query it
                    self.grids.earthquake_masks[jump.earthquake.id] = (s_score, p_score, mask)

        # Collision detection was already performed in prepare_earthquake_list.
        # self.collided_earthquakes is pre-populated; no action needed here.

    def _register_constraints(self):
        """Register all constraint types."""
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

        # Coseismic and postseismic for each earthquake
        for event in self.earthquakes:
            # Skip collided earthquakes - they will be constrained to zero
            if event.id in self.collided_earthquakes:
                tqdm.write(f'Setting number of stations for collided earthquake {event.id} to zero.')

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
                tqdm.write(f'No stations observed coseismic event {event.id}. '
                           f'A coseismic constraint for this event will not be added.')

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

        # Extension point: sparse stations (self.sparse_stations) have no owned
        # parameters and currently never contribute equations to the shared system --
        # this is where a future constraint built from their cleaned data (see
        # Station.free_fit) would be registered. Not implemented yet.
        if self.config.sparse_constraints_enabled and self.sparse_stations:
            tqdm.write(f'sparse_constraints_enabled is set but no sparse constraint '
                       f'contribution is implemented yet ({len(self.sparse_stations)} '
                       f'sparse station(s) will not contribute to the solve).')

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
                stn.etm = etm_result[0] if etm_result is not None else None
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
            tqdm.write(f'No stations observed coseismic event {event.id}. '
                       f'A coseismic constraint for this event will not be added.')

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
