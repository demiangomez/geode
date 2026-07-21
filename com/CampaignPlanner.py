#!/usr/bin/env python

"""
Project: Geodetic Database Engine (GeoDE)
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
from pathlib import Path

from geode.Utils import add_version_argument, station_list_help
from geode.campaign_planner.planner import (
    DEFAULT_CONFIG, CampaignPlannerError, plan_campaign,
)


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
        '--station-time', metavar='ID=MINUTES', nargs='+',
        help=(
            'Override time-on-site for specific stops (repeatable).\n'
            'ID is a station code (net.stnm) or a new site\'s display name,\n'
            'matched case-insensitively. Takes precedence over --time-on-site\n'
            'for the matching stop(s) only.\n'
            'Example: --station-time arg.unsj=180 "Mendoza, Argentina=90"'
        ),
    )
    parser.add_argument(
        '--num-participants', metavar='N', type=int,
        help='Number of people on the campaign (default: 1). Used to compute '
             'per diem and lodging cost.',
    )
    parser.add_argument(
        '--per-diem', metavar='COST_PER_PERSON_PER_DAY', type=float,
        help='Per diem cost per person per day in local currency '
             '(default: 0.0 = omit per diem column).',
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
        '--lodging-cost', metavar='COST_PER_PERSON_PER_NIGHT', type=float,
        help='Lodging cost per person per night in local currency (default: 70.0).',
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


def _parse_station_time_overrides(entries: list) -> dict:
    """
    Parse ['id=minutes', ...] (as given to --station-time) into
    {id: minutes}. Raises CampaignPlannerError on any malformed entry.
    """
    overrides = {}
    for entry in entries:
        if '=' not in entry:
            raise CampaignPlannerError(
                f'--station-time entry must be ID=MINUTES, got: {entry!r}')
        key, _, val = entry.rpartition('=')
        key = key.strip()
        try:
            minutes = int(val.strip())
        except ValueError:
            raise CampaignPlannerError(
                f'--station-time minutes must be an integer, got: {entry!r}')
        if minutes <= 0:
            raise CampaignPlannerError(
                f'--station-time minutes must be positive, got: {entry!r}')
        overrides[key] = minutes
    return overrides


def _merge_config(args: argparse.Namespace) -> dict:
    """
    Build the final config dict.
    Priority: CLI switches  >  JSON file  >  DEFAULT_CONFIG

    Raises CampaignPlannerError if the config file is missing/invalid or a
    --station-time entry is malformed.
    """
    config = DEFAULT_CONFIG.copy()

    # Load JSON config if provided
    if args.config:
        cfg_path = args.config
        if not os.path.exists(cfg_path):
            raise CampaignPlannerError(f'Config file not found: {cfg_path}')
        try:
            with open(cfg_path, encoding='utf-8') as f:
                json_cfg = json.load(f)
        except json.JSONDecodeError as exc:
            raise CampaignPlannerError(f'Invalid JSON in {cfg_path}: {exc}') from exc
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
    if args.num_participants is not None: config['num_participants']  = args.num_participants
    if args.per_diem     is not None: config['per_diem_cost_per_day'] = args.per_diem
    if args.start_date   is not None: config['start_date']            = args.start_date
    if args.output       is not None: config['output_file']           = args.output

    if args.station_time is not None:
        overrides = dict(config.get('station_time_overrides') or {})
        overrides.update(_parse_station_time_overrides(args.station_time))
        config['station_time_overrides'] = overrides

    return config


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = _build_parser()
    args   = parser.parse_args()
    logger = _setup_logging()

    try:
        config = _merge_config(args)
        result = plan_campaign(config, logger=logger)
    except CampaignPlannerError as exc:
        print(f' !! {exc}', file=sys.stderr)
        sys.exit(1)

    out_path = config['output_file']
    try:
        Path(out_path).write_text(result['html'], encoding='utf-8')
    except OSError as exc:
        logger.debug('Write failed', exc_info=True)
        print(f' !! Could not write output file "{out_path}": {exc}', file=sys.stderr)
        sys.exit(1)

    print(f'\nPlan written to {out_path} — open it in your browser.')


if __name__ == '__main__':
    main()
