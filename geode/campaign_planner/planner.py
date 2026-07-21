"""
planner.py
==========
Orchestration layer for the GeoDE Campaign Planner: config validation,
GeoDE database lookups (stations, new sites), and the top-level
plan_campaign() entry point.

This is the module a web view (or any other non-CLI caller) should import
from. It never calls sys.exit() and never prints to stdout/stderr — every
recoverable failure is raised as a CampaignPlannerError with a
human-readable message, so it is safe to call from a web request handler.

com/CampaignPlanner.py is a thin CLI wrapper around plan_campaign() — argv
parsing, JSON config file loading, and writing the HTML report to disk are
CLI-only concerns and stay there.
"""

import logging
import time

from geode import dbConnection
from geode.Utils import process_stnlist
from . import services, report

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    'time_on_site_minutes':   120,
    'station_time_overrides': {},
    'day_start':              '08:00',
    'hard_stop':              '20:00',
    'fuel_cost_per_km':       0.0,
    'lodging_cost_per_night': 70.0,
    'num_participants':       1,
    'per_diem_cost_per_day':  0.0,
    'start_date':             None,
    'output_file':            'campaign_plan.html',
    'new_sites':              [],
}

_REQUIRED = ('start_city', 'end_city', 'start_date')


class CampaignPlannerError(Exception):
    """
    Raised for any recoverable campaign-planning error (bad input, geocoding
    failure, OSRM failure, etc.). Never raised alongside a sys.exit() or a
    print to stderr — safe to catch from a web request handler.
    """
    pass


# ── Config validation ──────────────────────────────────────────────────────────

def _validate_config(config: dict) -> list:
    """Return a list of error strings (empty = valid)."""
    errors = []

    for key in _REQUIRED:
        if not config.get(key):
            errors.append(f'Missing required field: {key!r}')

    if not config.get('stations') and not config.get('new_sites'):
        errors.append(
            'At least one of "stations" or "new_sites" must be provided.'
        )

    for field in ('day_start', 'hard_stop'):
        val = config.get(field, '')
        parts = str(val).split(':')
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            errors.append(f'{field!r} must be HH:MM, got: {val!r}')

    if config.get('num_participants') is not None:
        try:
            if int(config['num_participants']) < 1:
                errors.append('"num_participants" must be a positive integer.')
        except (TypeError, ValueError):
            errors.append(
                f'"num_participants" must be an integer, got: {config["num_participants"]!r}')

    if config.get('per_diem_cost_per_day') is not None:
        try:
            if float(config['per_diem_cost_per_day']) < 0:
                errors.append('"per_diem_cost_per_day" must be >= 0.')
        except (TypeError, ValueError):
            errors.append(
                f'"per_diem_cost_per_day" must be a number, got: '
                f'{config["per_diem_cost_per_day"]!r}')

    overrides = config.get('station_time_overrides') or {}
    if not isinstance(overrides, dict):
        errors.append(
            '"station_time_overrides" must be a mapping of station id/name to minutes.')
    else:
        for key, val in overrides.items():
            try:
                if int(val) <= 0:
                    errors.append(
                        f'"station_time_overrides[{key!r}]" must be a positive integer.')
            except (TypeError, ValueError):
                errors.append(
                    f'"station_time_overrides[{key!r}]" must be an integer, got: {val!r}')

    if config.get('start_date'):
        try:
            from datetime import datetime
            datetime.strptime(config['start_date'], '%Y-%m-%d')
        except ValueError:
            errors.append(f'"start_date" must be YYYY-MM-DD, got: {config["start_date"]!r}')

    return errors


# ── Database helpers ──────────────────────────────────────────────────────────

def _fetch_stations(cnn, station_specs: list, logger) -> list:
    """
    Resolve station specifications via process_stnlist, then fetch coordinates.
    Accepts any format supported by the GeoDE station parser (wildcards, country
    codes, geographic filters, etc.).
    Returns a list of dicts with name, lat, lon, type='station', id.
    Raises CampaignPlannerError if no stations resolve or none have valid coordinates.
    """
    resolved = process_stnlist(cnn, station_specs, print_summary=True)
    if not resolved:
        raise CampaignPlannerError('No stations matched the provided specification.')

    null_coords = []
    result      = []

    for stn in resolved:
        nc = stn['NetworkCode']
        sc = stn['StationCode']
        rows = cnn.query_float(
            f"""SELECT "StationName", lat, lon
                FROM stations
                WHERE "NetworkCode" = '{nc}' AND "StationCode" = '{sc}'""",
            as_dict=True,
        )
        if not rows:
            continue
        row = rows[0]
        if row.get('lat') is None or row.get('lon') is None:
            null_coords.append(f'{nc}.{sc}')
            continue
        result.append({
            'name': (str(row.get('StationName') or '') or f'{nc.upper()}.{sc.upper()}'),
            'lat':  float(row['lat']),
            'lon':  float(row['lon']),
            'type': 'station',
            'id':   f'{nc}.{sc}',
        })

    if null_coords:
        logger.warning('Stations skipped (null coordinates): %s', ', '.join(null_coords))
    if not result:
        raise CampaignPlannerError('No stations with valid coordinates found.')

    return result


# ── New-site resolver ─────────────────────────────────────────────────────────

def _resolve_new_sites(new_sites: list, logger) -> list:
    """
    Convert new_sites entries to waypoint dicts with type='new_site'.

    Each entry may be:
      str "lat,lon"               → direct coordinates, auto-generated name
      str "City, Country"         → geocoded via Nominatim
      dict {name, lat, lon}       → direct coordinates with custom name
      dict {name, city}           → geocoded with custom name
    """
    result = []
    for entry in new_sites:
        if isinstance(entry, dict):
            if 'lat' in entry and 'lon' in entry:
                lat  = float(entry['lat'])
                lon  = float(entry['lon'])
                name = entry.get('name') or f'Site ({lat:.4f}°, {lon:.4f}°)'
                result.append({'name': name, 'lat': lat, 'lon': lon,
                                'type': 'new_site', 'id': None})
            elif 'city' in entry:
                city = entry['city']
                logger.info('Geocoding new site: %s...', entry.get('name') or city)
                try:
                    loc = services.geocode_city(city)
                except ValueError as exc:
                    raise CampaignPlannerError(str(exc)) from exc
                result.append({'name': entry.get('name') or city,
                                'lat': loc['lat'], 'lon': loc['lon'],
                                'type': 'new_site', 'id': None})
            else:
                raise CampaignPlannerError(
                    f'new_site entry missing both lat/lon and city: {entry}')
        elif isinstance(entry, str):
            # Try to parse as "lat,lon"
            parts = entry.split(',')
            if len(parts) == 2:
                try:
                    lat = float(parts[0].strip())
                    lon = float(parts[1].strip())
                    result.append({
                        'name': f'Site ({lat:.4f}°, {lon:.4f}°)',
                        'lat':  lat, 'lon': lon,
                        'type': 'new_site', 'id': None,
                    })
                    continue
                except ValueError:
                    pass
            # Geocode as place name
            logger.info('Geocoding new site: %s...', entry)
            try:
                loc = services.geocode_city(entry)
            except ValueError as exc:
                raise CampaignPlannerError(str(exc)) from exc
            result.append({'name': entry, 'lat': loc['lat'], 'lon': loc['lon'],
                           'type': 'new_site', 'id': None})
        else:
            raise CampaignPlannerError(
                f'Unsupported new_site entry type: {type(entry).__name__}')
    return result


# ── Web-safe entry point ───────────────────────────────────────────────────────

def plan_campaign(config: dict, cnn=None, logger=None) -> dict:
    """
    Run the full campaign-planning pipeline for a config dict (see
    DEFAULT_CONFIG for recognized keys).

    This is the function a web view (or any other non-CLI caller) should use.
    It validates the config itself, never calls sys.exit(), and never prints
    to stdout/stderr — every recoverable failure is raised as a
    CampaignPlannerError with a human-readable message.

    Parameters
    ----------
    config : dict
        Config values, e.g. as assembled directly by a web view from a JSON
        request body, or built by com.CampaignPlanner._merge_config() for CLI
        use. Does not need to have been pre-validated, and does not need
        every key present — missing keys fall back to DEFAULT_CONFIG, and
        invalid values raise CampaignPlannerError here.
    cnn : geode.dbConnection.Cnn, optional
        Reuse an existing database connection. If omitted, a new one is
        opened (using gnss_data.cfg) and closed before returning.
    logger : logging.Logger, optional
        Defaults to the 'CampaignPlanner' logger.

    Returns
    -------
    dict — {'plan': <services.compute_plan() dict>, 'html': <str>}

    Raises
    ------
    CampaignPlannerError
        On invalid config, unresolved stations/new sites, geocoding failure,
        or OSRM routing failure.
    """
    logger = logger or logging.getLogger('CampaignPlanner')

    # Fill in any keys the caller omitted (e.g. a web view posting a partial
    # dict) so downstream code can rely on them all being present.
    config = {**DEFAULT_CONFIG, **config}

    errors = _validate_config(config)
    if errors:
        raise CampaignPlannerError('; '.join(errors))

    owns_cnn = cnn is None
    if owns_cnn:
        try:
            cnn = dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)
        except Exception as exc:
            raise CampaignPlannerError(f'Could not connect to database: {exc}') from exc

    try:
        # ── Fetch station coordinates from DB ─────────────────────────────
        if config.get('stations'):
            logger.info('Resolving station list from database...')
            stations = _fetch_stations(cnn, config['stations'], logger)
        else:
            stations = []

        # ── Resolve new (planned) sites ───────────────────────────────────
        new_site_waypoints = _resolve_new_sites(config.get('new_sites', []), logger)
        all_stops = stations + new_site_waypoints

        # ── Geocode start and end cities ──────────────────────────────────
        logger.info('Geocoding %s...', config['start_city'])
        try:
            origin = services.geocode_city(config['start_city'])
            origin['type'] = 'origin'
        except ValueError as exc:
            raise CampaignPlannerError(str(exc)) from exc

        logger.info('Geocoding %s...', config['end_city'])
        try:
            destination = services.geocode_city(config['end_city'])
            destination['type'] = 'destination'
        except ValueError as exc:
            raise CampaignPlannerError(str(exc)) from exc

        # ── TSP ordering ───────────────────────────────────────────────────
        logger.info('Ordering %d stop(s) using nearest-neighbour TSP...', len(all_stops))
        ordered_stations  = services.order_stations_tsp(origin, all_stops)
        ordered_waypoints = [origin] + ordered_stations + [destination]

        # ── Fetch OSRM driving legs ────────────────────────────────────────
        legs = []
        n_legs = len(ordered_waypoints) - 1
        for i in range(n_legs):
            a = ordered_waypoints[i]
            b = ordered_waypoints[i + 1]
            logger.info('Routing: %s → %s (leg %d/%d)...', a['name'], b['name'], i + 1, n_legs)
            try:
                leg = services.fetch_osrm_leg(a, b)
            except RuntimeError as exc:
                raise CampaignPlannerError(str(exc)) from exc
            legs.append(leg)
            if i < n_legs - 1:
                time.sleep(0.5)   # respect public API rate limits

        # ── Compute multi-day plan ─────────────────────────────────────────
        logger.info('Computing campaign schedule...')
        try:
            plan = services.compute_plan(config, ordered_waypoints, legs)
        except RuntimeError as exc:
            raise CampaignPlannerError(str(exc)) from exc

        summary = plan['summary']
        logger.info(
            'Plan: %d day(s), %d station(s), %.1f km total.',
            summary['total_days'], summary['total_stations'], summary['total_km'],
        )

        # ── Generate HTML report ──────────────────────────────────────────
        logger.info('Generating HTML report...')
        html = report.generate_html(plan, config)

        return {'plan': plan, 'html': html}

    except CampaignPlannerError:
        logger.debug('Campaign planning failed', exc_info=True)
        raise
    finally:
        if owns_cnn:
            cnn.close()
