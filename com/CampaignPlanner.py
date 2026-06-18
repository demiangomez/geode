#!/usr/bin/env python

"""
Project: Geodesy Database Engine (GeoDE)
Date: 2025
Author: Demian D. Gomez

Plan a multi-day GNSS field campaign: optimally order station visits,
compute driving legs via OSRM, schedule across days, and write a
self-contained HTML report with an interactive Leaflet map.

Usage examples
--------------
  # Full run from a JSON config
  CampaignPlanner.py --config example_campaign.json

  # Override output file from the command line
  CampaignPlanner.py --config example_campaign.json --output my_plan.html

  # Specify everything via switches (no JSON needed)
  CampaignPlanner.py \\
      --start-city "Buenos Aires, Argentina" \\
      --end-city   "San Juan, Argentina" \\
      --stations   arg.unsj arg.vmol arg.rwsn arg.ljar \\
      --start-date 2025-09-01 \\
      --time-on-site 120 \\
      --fuel-cost 0.15

  # Mix existing stations with planned new sites (city name or lat,lon)
  CampaignPlanner.py --config example_campaign.json \\
      --new-sites "Mendoza, Argentina" "-34.1667,-69.7167"
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from geode import dbConnection
from geode.Utils import add_version_argument, process_stnlist, station_list_help
from geode.campaign_planner import services, report

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    'time_on_site_minutes':   120,
    'day_start':              '08:00',
    'hard_stop':              '20:00',
    'fuel_cost_per_km':       0.0,
    'lodging_cost_per_night': 70.0,
    'start_date':             None,
    'output_file':            'campaign_plan.html',
    'new_sites':              [],
}

_REQUIRED = ('start_city', 'end_city', 'start_date')


# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging(log_file: str = 'campaign_planner.log') -> logging.Logger:
    logger = logging.getLogger('CampaignPlanner')
    logger.setLevel(logging.DEBUG)

    # File handler — DEBUG level, full tracebacks
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)-8s %(name)s: %(message)s'
    ))
    logger.addHandler(fh)

    # Console handler — INFO only, no tracebacks
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(' >> %(message)s'))
    logger.addHandler(ch)

    return logger


# ── Argument parsing ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Plan a multi-day GNSS field campaign and write an HTML report.',
        epilog=(
            'Examples:\n'
            '  CampaignPlanner.py --config example_campaign.json\n'
            '  CampaignPlanner.py --start-city "Buenos Aires, Argentina" '
            '--end-city "San Juan, Argentina" \\\n'
            '      --stations arg.unsj arg.vmol --start-date 2025-09-01'
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        '--config', metavar='FILE',
        help='JSON config file. Command-line switches override values in the file.',
    )
    parser.add_argument(
        '--start-city', metavar='CITY',
        help='City (or address) where the campaign starts.',
    )
    parser.add_argument(
        '--end-city', metavar='CITY',
        help='City (or address) where the campaign ends.',
    )
    parser.add_argument(
        '--stations', metavar='SPEC', nargs='+',
        help=station_list_help(),
    )
    parser.add_argument(
        '--new-sites', metavar='SPEC', nargs='+',
        help=(
            'Planned installation sites not yet in the GeoDE database.\n'
            'Each value is either a "lat,lon" pair (e.g. -34.1667,-69.7167)\n'
            'or a place name to geocode (e.g. "Mendoza, Argentina").\n'
            'For a custom display name with coordinates, use the JSON config\n'
            'with {"name": "My Site", "lat": ..., "lon": ...}.'
        ),
    )
    parser.add_argument(
        '--time-on-site', metavar='MINUTES', type=int,
        help='Time spent at each station in minutes (default: 120).',
    )
    parser.add_argument(
        '--day-start', metavar='HH:MM',
        help='Time to start driving each day (default: 08:00).',
    )
    parser.add_argument(
        '--hard-stop', metavar='HH:MM',
        help='Hard stop time each day — no new arrivals after this (default: 20:00).',
    )
    parser.add_argument(
        '--fuel-cost', metavar='COST_PER_KM', type=float,
        help='Fuel cost per km in local currency (default: 0.0 = omit fuel column).',
    )
    parser.add_argument(
        '--lodging-cost', metavar='COST_PER_NIGHT', type=float,
        help='Lodging cost per night in local currency (default: 70.0).',
    )
    parser.add_argument(
        '--start-date', metavar='YYYY-MM-DD',
        help='First day of the campaign.',
    )
    parser.add_argument(
        '--output', metavar='FILE',
        help='Output HTML file (default: campaign_plan.html).',
    )
    add_version_argument(parser)
    return parser


def _merge_config(args: argparse.Namespace) -> dict:
    """
    Build the final config dict.
    Priority: CLI switches  >  JSON file  >  DEFAULT_CONFIG
    """
    config = DEFAULT_CONFIG.copy()

    # Load JSON config if provided
    if args.config:
        cfg_path = args.config
        if not os.path.exists(cfg_path):
            print(f' !! Config file not found: {cfg_path}', file=sys.stderr)
            sys.exit(1)
        try:
            with open(cfg_path, encoding='utf-8') as f:
                json_cfg = json.load(f)
        except json.JSONDecodeError as exc:
            print(f' !! Invalid JSON in {cfg_path}: {exc}', file=sys.stderr)
            sys.exit(1)
        # Merge (strip comment keys)
        config.update({k: v for k, v in json_cfg.items() if not k.startswith('_')})

    # Apply CLI switches (only if explicitly provided)
    if args.start_city   is not None: config['start_city']            = args.start_city
    if args.end_city     is not None: config['end_city']              = args.end_city
    if args.stations     is not None: config['stations']              = args.stations
    if args.new_sites    is not None: config['new_sites']             = args.new_sites
    if args.time_on_site is not None: config['time_on_site_minutes']  = args.time_on_site
    if args.day_start    is not None: config['day_start']             = args.day_start
    if args.hard_stop    is not None: config['hard_stop']             = args.hard_stop
    if args.fuel_cost    is not None: config['fuel_cost_per_km']      = args.fuel_cost
    if args.lodging_cost is not None: config['lodging_cost_per_night'] = args.lodging_cost
    if args.start_date   is not None: config['start_date']            = args.start_date
    if args.output       is not None: config['output_file']           = args.output

    return config


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

    if config.get('start_date'):
        try:
            from datetime import datetime
            datetime.strptime(config['start_date'], '%Y-%m-%d')
        except ValueError:
            errors.append(f'"start_date" must be YYYY-MM-DD, got: {config["start_date"]!r}')

    return errors


# ── Database helpers ──────────────────────────────────────────────────────────

def _fetch_stations(cnn, station_specs: list) -> list:
    """
    Resolve station specifications via process_stnlist, then fetch coordinates.
    Accepts any format supported by the GeoDE station parser (wildcards, country
    codes, geographic filters, etc.).
    Returns a list of dicts with name, lat, lon, type='station', id.
    Exits cleanly if no stations resolve or none have valid coordinates.
    """
    resolved = process_stnlist(cnn, station_specs, print_summary=True)
    if not resolved:
        print(' !! No stations matched the provided specification.', file=sys.stderr)
        sys.exit(1)

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
        print(f' !! Stations skipped (null coordinates): {", ".join(null_coords)}',
              file=sys.stderr)
    if not result:
        print(' !! No stations with valid coordinates found.', file=sys.stderr)
        sys.exit(1)

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
                    print(f' !! {exc}', file=sys.stderr)
                    sys.exit(1)
                result.append({'name': entry.get('name') or city,
                                'lat': loc['lat'], 'lon': loc['lon'],
                                'type': 'new_site', 'id': None})
            else:
                print(f' !! new_site entry missing both lat/lon and city: {entry}',
                      file=sys.stderr)
                sys.exit(1)
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
                print(f' !! {exc}', file=sys.stderr)
                sys.exit(1)
            result.append({'name': entry, 'lat': loc['lat'], 'lon': loc['lon'],
                           'type': 'new_site', 'id': None})
        else:
            print(f' !! Unsupported new_site entry type: {type(entry).__name__}',
                  file=sys.stderr)
            sys.exit(1)
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = _build_parser()
    args   = parser.parse_args()
    logger = _setup_logging()

    # ── Config ────────────────────────────────────────────────────────────────
    config = _merge_config(args)
    errors = _validate_config(config)
    if errors:
        for e in errors:
            print(f' !! {e}', file=sys.stderr)
        sys.exit(1)

    # ── Database connection ───────────────────────────────────────────────────
    try:
        cnn = dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)
    except Exception as exc:
        logger.debug('DB connection failed', exc_info=True)
        print(f' !! Could not connect to database: {exc}', file=sys.stderr)
        sys.exit(1)

    # ── Fetch station coordinates from DB ─────────────────────────────────────
    if config.get('stations'):
        logger.info('Resolving station list from database...')
        stations = _fetch_stations(cnn, config['stations'])
    else:
        stations = []

    # ── Resolve new (planned) sites ───────────────────────────────────────────
    new_site_waypoints = _resolve_new_sites(config.get('new_sites', []), logger)
    all_stops = stations + new_site_waypoints

    # ── Geocode start and end cities ──────────────────────────────────────────
    logger.info('Geocoding %s...', config['start_city'])
    try:
        origin = services.geocode_city(config['start_city'])
        origin['type'] = 'origin'
    except ValueError as exc:
        logger.debug('Geocoding failed', exc_info=True)
        print(f' !! {exc}', file=sys.stderr)
        sys.exit(1)

    logger.info('Geocoding %s...', config['end_city'])
    try:
        destination = services.geocode_city(config['end_city'])
        destination['type'] = 'destination'
    except ValueError as exc:
        logger.debug('Geocoding failed', exc_info=True)
        print(f' !! {exc}', file=sys.stderr)
        sys.exit(1)

    # ── TSP ordering ─────────────────────────────────────────────────────────
    logger.info('Ordering %d stop(s) using nearest-neighbour TSP...', len(all_stops))
    ordered_stations  = services.order_stations_tsp(origin, all_stops)
    ordered_waypoints = [origin] + ordered_stations + [destination]

    # ── Fetch OSRM driving legs ───────────────────────────────────────────────
    legs = []
    n_legs = len(ordered_waypoints) - 1
    for i in range(n_legs):
        a = ordered_waypoints[i]
        b = ordered_waypoints[i + 1]
        logger.info('Routing: %s → %s (leg %d/%d)...', a['name'], b['name'], i + 1, n_legs)
        try:
            leg = services.fetch_osrm_leg(a, b)
            legs.append(leg)
        except RuntimeError as exc:
            logger.debug('OSRM failed', exc_info=True)
            print(f' !! {exc}', file=sys.stderr)
            sys.exit(1)
        if i < n_legs - 1:
            time.sleep(0.5)   # respect public API rate limits

    # ── Compute multi-day plan ────────────────────────────────────────────────
    logger.info('Computing campaign schedule...')
    try:
        plan = services.compute_plan(config, ordered_waypoints, legs)
    except RuntimeError as exc:
        logger.debug('compute_plan failed', exc_info=True)
        print(f' !! {exc}', file=sys.stderr)
        sys.exit(1)

    summary = plan['summary']
    logger.info(
        'Plan: %d day(s), %d station(s), %.1f km total.',
        summary['total_days'], summary['total_stations'], summary['total_km'],
    )

    # ── Generate HTML report ──────────────────────────────────────────────────
    logger.info('Generating HTML report...')
    html = report.generate_html(plan, config)

    out_path = config['output_file']
    try:
        Path(out_path).write_text(html, encoding='utf-8')
    except OSError as exc:
        logger.debug('Write failed', exc_info=True)
        print(f' !! Could not write output file "{out_path}": {exc}', file=sys.stderr)
        sys.exit(1)

    print(f'\nPlan written to {out_path} — open it in your browser.')


if __name__ == '__main__':
    main()
