"""
services.py
===========
Pure business-logic functions for the GeoDE Campaign Planner.

No database access, no file I/O — only data-in / data-out functions
(except fetch_osrm_leg and geocode_city which call free public APIs,
and re-fetching done inside compute_plan when day boundaries are cut).
"""

import math
import time
from copy import deepcopy
from datetime import datetime, timedelta

import requests
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

_USER_AGENT = 'GeoDE-CampaignPlanner/1.0'
_TIMEOUT    = 10   # seconds for all external API calls


# ── Geo helpers ───────────────────────────────────────────────────────────────

def haversine_km(a: dict, b: dict) -> float:
    """
    Great-circle distance between two points in kilometres.
    Each point is a dict with 'lat' and 'lon' keys (decimal degrees).
    """
    R    = 6371.0
    lat1 = math.radians(a['lat'])
    lat2 = math.radians(b['lat'])
    dlat = math.radians(b['lat'] - a['lat'])
    dlon = math.radians(b['lon'] - a['lon'])
    h    = (math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(h))


def _resolve_time_overrides(raw_overrides: dict) -> dict:
    """Normalize a station_time_overrides mapping to lowercase, stripped keys."""
    return {str(k).strip().lower(): int(v) for k, v in (raw_overrides or {}).items()}


def _time_for_stop(stop: dict, default_minutes: int, overrides: dict) -> int:
    """
    Look up the time-on-site (minutes) for a single stop.
    Tries the stop's station id first (e.g. "net.stnm"), then its display
    name (new sites have no id), falling back to default_minutes.
    """
    stop_id = stop.get('id')
    if stop_id and str(stop_id).strip().lower() in overrides:
        return overrides[str(stop_id).strip().lower()]
    name = stop.get('name')
    if name and str(name).strip().lower() in overrides:
        return overrides[str(name).strip().lower()]
    return default_minutes


def order_stations_tsp(origin: dict, stations: list) -> list:
    """
    Nearest-neighbour greedy TSP starting from *origin*.
    Uses haversine straight-line distances — does NOT call OSRM.
    Returns the re-ordered list of station dicts.
    """
    remaining = list(stations)
    ordered   = []
    current   = origin
    while remaining:
        nearest = min(remaining, key=lambda s: haversine_km(current, s))
        ordered.append(nearest)
        remaining.remove(nearest)
        current = nearest
    return ordered


# ── External API calls ────────────────────────────────────────────────────────

def geocode_city(city_name: str) -> dict:
    """
    Geocode *city_name* using the Nominatim OSM service (no API key needed).
    Returns {'name': str, 'lat': float, 'lon': float}.
    Raises ValueError if the city cannot be found.
    """
    geolocator = Nominatim(user_agent=_USER_AGENT)
    geocode    = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    location   = geocode(city_name, timeout=_TIMEOUT)
    if location is None:
        raise ValueError(
            f'Could not geocode "{city_name}". '
            f'Try a more specific name, e.g. "Buenos Aires, Argentina".'
        )
    return {
        'name': city_name,
        'lat':  float(location.latitude),
        'lon':  float(location.longitude),
    }


def fetch_osrm_leg(point_a: dict, point_b: dict) -> dict:
    """
    Get driving distance and route geometry between two points via the public
    OSRM API (no API key needed).

    Returns:
        {
            'distance_km':   float,
            'drive_minutes': int,
            'geometry':      [[lon, lat], ...]
        }

    Raises RuntimeError on HTTP error or timeout.
    """
    url = (
        f'https://router.project-osrm.org/route/v1/driving/'
        f'{point_a["lon"]},{point_a["lat"]};{point_b["lon"]},{point_b["lat"]}'
        f'?overview=full&geometries=geojson'
    )
    try:
        resp = requests.get(url, headers={'User-Agent': _USER_AGENT}, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f'OSRM timed out routing "{point_a["name"]}" → "{point_b["name"]}". '
            f'Try again — the public API may be temporarily overloaded.'
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f'OSRM routing failed for "{point_a["name"]}" → "{point_b["name"]}": {exc}'
        )

    data = resp.json()
    if data.get('code') != 'Ok' or not data.get('routes'):
        raise RuntimeError(
            f'OSRM returned no route for '
            f'"{point_a["name"]}" → "{point_b["name"]}".'
        )
    route = data['routes'][0]
    return {
        'distance_km':   round(route['distance'] / 1000, 2),
        'drive_minutes': int(round(route['duration'] / 60)),
        'geometry':      route['geometry']['coordinates'],   # [[lon, lat], ...]
    }


# ── Multi-day scheduling ──────────────────────────────────────────────────────

def compute_plan(params: dict, ordered_waypoints: list, legs: list) -> dict:
    """
    Build a multi-day campaign plan from pre-ordered waypoints and OSRM legs.

    Parameters
    ----------
    params : dict
        Validated config values. Required keys:
        start_date, day_start, hard_stop, time_on_site_minutes, fuel_cost_per_km.
        Optional keys: station_time_overrides (dict of station id/name -> minutes,
        takes precedence over time_on_site_minutes for matching stops),
        num_participants (int, default 1), per_diem_cost_per_day (float, default 0.0).
    ordered_waypoints : list of dicts
        [origin] + [stations in TSP order] + [destination].
        Each dict: {'name', 'lat', 'lon', 'type'}.
        'type' is 'origin', 'station', or 'destination'.
    legs : list of dicts
        Pre-fetched OSRM legs in waypoint order (len == len(ordered_waypoints)-1).
        Each dict: {'distance_km', 'drive_minutes', 'geometry'}.

    Returns
    -------
    dict  — see spec for full structure:
        {'days': [...], 'summary': {...}}
    """
    start_date    = datetime.strptime(params['start_date'], '%Y-%m-%d').date()
    day_start_hm  = [int(x) for x in params['day_start'].split(':')]
    hard_stop_hm  = [int(x) for x in params['hard_stop'].split(':')]
    default_time_on_site = int(params['time_on_site_minutes'])
    time_overrides       = _resolve_time_overrides(params.get('station_time_overrides'))
    fuel_per_km   = float(params.get('fuel_cost_per_km', 0.0))
    daily_minutes = (hard_stop_hm[0] * 60 + hard_stop_hm[1]) - (day_start_hm[0] * 60 + day_start_hm[1])

    legs = list(deepcopy(legs))   # may be patched with re-fetched legs

    # ── Day-state helpers ─────────────────────────────────────────────────────
    day_number        = 1
    current_date      = start_date
    days              = []
    current_day_stops = []
    day_km            = 0.0
    day_drive_min     = 0
    day_fuel          = 0.0

    def _make_dt(date, hm):
        return datetime(date.year, date.month, date.day, hm[0], hm[1])

    current_time  = _make_dt(current_date, day_start_hm)
    hard_stop_dt  = _make_dt(current_date, hard_stop_hm)
    overnight_pos = ordered_waypoints[0]

    def _hhmm(dt):
        return dt.strftime('%H:%M')

    def _hhmm_day(dt):
        """Format time; append '+N' when dt falls N calendar days after current_date."""
        s     = dt.strftime('%H:%M')
        delta = (dt.date() - current_date).days
        if delta > 0:
            s += f' +{delta}'
        return s

    def _close_day():
        days.append({
            'day_number':              day_number,
            'date':                    current_date.isoformat(),
            'stops':                   list(current_day_stops),
            'day_total_km':            round(day_km, 2),
            'day_total_drive_minutes': day_drive_min,
            'day_total_fuel_cost':     round(day_fuel, 2),
        })

    def _advance_day():
        nonlocal day_number, current_date, current_time, hard_stop_dt
        nonlocal current_day_stops, day_km, day_drive_min, day_fuel
        day_number       += 1
        current_date      = start_date + timedelta(days=day_number - 1)
        current_time      = _make_dt(current_date, day_start_hm)
        hard_stop_dt      = _make_dt(current_date, hard_stop_hm)
        current_day_stops = []
        day_km            = 0.0
        day_drive_min     = 0
        day_fuel          = 0.0

    def _add_start_stop(pos):
        """Add the day's first stop (departure only, no drive leg)."""
        current_day_stops.append({
            'type':              pos['type'],
            'name':              pos['name'],
            'code':              pos.get('id', ''),
            'lat':               pos.get('lat'),
            'lon':               pos.get('lon'),
            'arrival':           None,
            'departure':         _hhmm(current_time),
            'leg_km':            0.0,
            'leg_drive_minutes': 0,
            'leg_fuel_cost':     0.0,
            'warning':           None,
            'geometry':          [],
        })

    # ── Main scheduling loop ──────────────────────────────────────────────────
    _add_start_stop(overnight_pos)

    i        = 0
    max_days = 365   # safety guard against infinite day splits

    while i < len(legs):
        if day_number > max_days:
            raise RuntimeError(
                f'Campaign exceeded {max_days} days — possible scheduling loop. '
                f'Check that hard_stop allows at least one station to be reached per day.'
            )

        leg     = legs[i]
        dest_wp = ordered_waypoints[i + 1]
        is_last = (i == len(legs) - 1)

        arrival  = current_time + timedelta(minutes=leg['drive_minutes'])
        leg_km   = leg['distance_km']
        leg_fuel = round(leg_km * fuel_per_km, 2)

        # ── Hard-stop check: applies to every leg, including the last ─────────
        if arrival > hard_stop_dt:
            # Split: drive as far as possible today, continue next day.
            # Re-fetching the same leg from OSRM always yields the same drive time,
            # so we go straight to the split instead of wasting a day on a retry.
            avail_min  = max(1, int((hard_stop_dt - current_time).total_seconds() // 60))
            ratio      = avail_min / leg['drive_minutes']
            part1_km   = round(leg['distance_km'] * ratio, 2)
            part2_km   = round(leg['distance_km'] - part1_km, 2)
            geom       = leg['geometry']
            if len(geom) > 1:
                split_idx  = max(1, min(int(len(geom) * ratio), len(geom) - 1))
                part1_geom = geom[:split_idx + 1]
                part2_geom = geom[split_idx:]
            else:
                part1_geom = geom
                part2_geom = geom
            inter_wp = {'name': 'Overnight stop', 'lat': None, 'lon': None,
                        'type': 'intermediate'}
            legs[i]  = {'distance_km': part1_km, 'drive_minutes': avail_min,
                        'geometry': part1_geom}
            legs.insert(i + 1, {'distance_km': part2_km,
                                 'drive_minutes': leg['drive_minutes'] - avail_min,
                                 'geometry': part2_geom})
            ordered_waypoints.insert(i + 1, inter_wp)
            # Re-process legs[i] = part1 (fits within today's window).
            continue

        # ── Destination (last waypoint) ───────────────────────────────────────
        if is_last:
            current_day_stops.append({
                'type':              dest_wp['type'],
                'name':              dest_wp['name'],
                'code':              dest_wp.get('id', ''),
                'lat':               dest_wp['lat'],
                'lon':               dest_wp['lon'],
                'arrival':           _hhmm_day(arrival),
                'departure':         None,
                'leg_km':            round(leg_km, 2),
                'leg_drive_minutes': leg['drive_minutes'],
                'leg_fuel_cost':     leg_fuel,
                'warning':           None,
                'geometry':          leg['geometry'],
            })
            day_km        += leg_km
            day_drive_min += leg['drive_minutes']
            day_fuel      += leg_fuel
            _close_day()
            break

        # ── Intermediate stop (route split from a previous long leg) ──────────
        if dest_wp['type'] == 'intermediate':
            current_day_stops.append({
                'type':              'intermediate',
                'name':              dest_wp['name'],
                'code':              '',
                'lat':               dest_wp.get('lat'),
                'lon':               dest_wp.get('lon'),
                'arrival':           _hhmm_day(arrival),
                'departure':         None,
                'leg_km':            round(leg_km, 2),
                'leg_drive_minutes': leg['drive_minutes'],
                'leg_fuel_cost':     leg_fuel,
                'warning':           'Route continues next day',
                'geometry':          leg['geometry'],
            })
            day_km        += leg_km
            day_drive_min += leg['drive_minutes']
            day_fuel      += leg_fuel
            overnight_pos  = dest_wp
            _close_day()
            _advance_day()
            _add_start_stop(overnight_pos)
            i += 1
            continue

        # ── GNSS station ──────────────────────────────────────────────────────
        time_on_site = _time_for_stop(dest_wp, default_time_on_site, time_overrides)
        departure    = arrival + timedelta(minutes=time_on_site)

        if departure > hard_stop_dt:
            # Work cannot finish today.  Record what gets done today, then
            # advance through as many full work days as needed before the
            # remaining work fits within a single day's window.
            work_done_min = int((hard_stop_dt - arrival).total_seconds() // 60)
            remaining_min = time_on_site - work_done_min
            current_day_stops.append({
                'type':              dest_wp['type'],
                'name':              dest_wp['name'],
                'code':              dest_wp.get('id', ''),
                'lat':               dest_wp['lat'],
                'lon':               dest_wp['lon'],
                'arrival':           _hhmm_day(arrival),
                'departure':         _hhmm(hard_stop_dt),
                'leg_km':            round(leg_km, 2),
                'leg_drive_minutes': leg['drive_minutes'],
                'leg_fuel_cost':     leg_fuel,
                'warning':           'Work continues next day',
                'geometry':          leg['geometry'],
            })
            day_km        += leg_km
            day_drive_min += leg['drive_minutes']
            day_fuel      += leg_fuel
            overnight_pos  = dest_wp
            _close_day()
            _advance_day()

            # Burn through any additional full work days needed.
            while remaining_min > daily_minutes:
                remaining_min -= daily_minutes
                current_day_stops.append({
                    'type':              dest_wp['type'],
                    'name':              dest_wp['name'],
                    'code':              dest_wp.get('id', ''),
                    'lat':               dest_wp['lat'],
                    'lon':               dest_wp['lon'],
                    'arrival':           None,
                    'departure':         _hhmm(hard_stop_dt),
                    'leg_km':            0.0,
                    'leg_drive_minutes': 0,
                    'leg_fuel_cost':     0.0,
                    'warning':           'Work continues next day',
                    'geometry':          [],
                })
                _close_day()
                _advance_day()

            # Final partial (or exact) work period — fits within today.
            finish_time = current_time + timedelta(minutes=remaining_min)
            current_day_stops.append({
                'type':              dest_wp['type'],
                'name':              dest_wp['name'],
                'code':              dest_wp.get('id', ''),
                'lat':               dest_wp['lat'],
                'lon':               dest_wp['lon'],
                'arrival':           None,
                'departure':         _hhmm(finish_time),
                'leg_km':            0.0,
                'leg_drive_minutes': 0,
                'leg_fuel_cost':     0.0,
                'warning':           None,
                'geometry':          [],
            })
            current_time = finish_time
            i += 1
            continue

        # Work fits within the day — record station normally.
        current_day_stops.append({
            'type':                 dest_wp['type'],
            'name':                 dest_wp['name'],
            'code':                 dest_wp.get('id', ''),
            'lat':                  dest_wp['lat'],
            'lon':                  dest_wp['lon'],
            'arrival':              _hhmm_day(arrival),
            'departure':            _hhmm_day(departure),
            'leg_km':               round(leg_km, 2),
            'leg_drive_minutes':    leg['drive_minutes'],
            'leg_fuel_cost':        leg_fuel,
            'time_on_site_minutes': time_on_site,
            'warning':              None,
            'geometry':             leg['geometry'],
        })
        day_km        += leg_km
        day_drive_min += leg['drive_minutes']
        day_fuel      += leg_fuel
        current_time   = departure
        overnight_pos  = dest_wp
        i += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    total_km   = sum(d['day_total_km']              for d in days)
    total_min  = sum(d['day_total_drive_minutes']   for d in days)
    total_fuel = sum(d['day_total_fuel_cost']       for d in days)

    # Count actual field stops (arrival is not None).
    # Overnight/day-start positions also appear in stops but have arrival=None.
    total_stns = sum(
        1 for d in days for s in d['stops']
        if s['type'] in ('station', 'new_site') and s['arrival'] is not None
    )

    n_nights          = max(len(days) - 1, 0)   # last day you sleep at home
    num_participants  = int(params.get('num_participants', 1))
    lodging_per_night = float(params.get('lodging_cost_per_night', 0.0))
    total_lodging     = round(lodging_per_night * n_nights * num_participants, 2)

    per_diem_per_day  = float(params.get('per_diem_cost_per_day', 0.0))
    total_per_diem    = round(per_diem_per_day * len(days) * num_participants, 2)

    return {
        'days': days,
        'summary': {
            'total_km':             round(total_km, 2),
            'total_drive_minutes':  total_min,
            'total_fuel_cost':      round(total_fuel, 2),
            'total_lodging_cost':   total_lodging,
            'total_per_diem_cost':  total_per_diem,
            'total_days':           len(days),
            'total_stations':       total_stns,
            'num_participants':     num_participants,
        },
    }
