#!/usr/bin/env python
"""
AlterETM - Manage ETM parameters for GNSS stations.

This tool allows modification of trajectory model parameters including:
- Polynomial terms (velocity, acceleration, etc.)
- Periodic signals (annual, semi-annual, custom)
- Discontinuities/jumps (mechanical, coseismic, postseismic)
- Parameter synchronization between solution types
"""

import argparse
import sys
from typing import List, Dict

from geode import dbConnection
from geode import Utils
from geode.Utils import process_date, station_list_help, add_version_argument, SmartHelpFormatter
from geode.pyDate import Date
from geode.etm.core.etm_config import EtmConfig
from geode.etm.core.etm_engine import EtmEngine
from geode.etm.core.type_declarations import SolutionType, JumpType
from geode.etm.core.data_classes import SolutionOptions
from geode.etm.data.etm_params import EtmParams, EtmParamsException
from geode.etm.data.solution_data import SolutionDataException


def main():
    parser = argparse.ArgumentParser(
        description='Manage ETM (trajectory model) parameters for GNSS stations.',
        formatter_class=SmartHelpFormatter,
        epilog=SmartHelpFormatter.RAW_MARKER + """
Examples:
  # Set polynomial to 3 terms (velocity + acceleration) for a station
  %(prog)s igs.algo polynomial --terms 3

  # Add an annual and semi-annual periodic signal
  %(prog)s igs.algo periodic --periods 365.25 182.625

  # Add a mechanical jump on a specific date
  %(prog)s igs.algo jump --add --date 2020/01/15 --type mechanical

  # Add a coseismic jump with postseismic decay
  %(prog)s igs.algo jump --add --date 2010/02/27 --type coseismic --relaxation 0.5 1.0

  # Remove a jump
  %(prog)s igs.algo jump --remove --date 2020/01/15

  # Copy parameters from GAMIT solution to all other solutions
  %(prog)s igs.algo copy-params --enable --source gamit

  # Print current parameters
  %(prog)s igs.algo print

  # Reset all jumps to defaults
  %(prog)s igs.algo reset --jumps
""")

    # Station list argument (common to all commands)
    parser.add_argument('stations', type=str, nargs='+', metavar='station',
                        help=SmartHelpFormatter.RAW_MARKER + station_list_help())

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest='command', title='commands',
                                       description='Available commands (use "command --help" for details)')

    # === POLYNOMIAL command ===
    poly_parser = subparsers.add_parser('polynomial', aliases=['poly', 'p'],
                                        help='Set polynomial terms for the trajectory model')
    poly_parser.add_argument('--terms', '-t', type=int, required=True,
                            help='Number of polynomial terms (2=velocity, 3=velocity+acceleration, etc.)')
    poly_parser.add_argument('--reference-date', '-r', type=str, metavar='DATE',
                            help='Reference epoch for polynomial (format: YYYY/MM/DD or YYYY_DOY)')
    add_solution_args(poly_parser)

    # === PERIODIC command ===
    periodic_parser = subparsers.add_parser('periodic', aliases=['per', 'q'],
                                            help='Set periodic signals in the trajectory model')
    periodic_parser.add_argument('--periods', '-p', type=float, nargs='+', required=True,
                                 metavar='DAYS',
                                 help='List of periods in days (e.g., 365.25 for annual, 182.625 for semi-annual)')
    add_solution_args(periodic_parser)

    # === JUMP command ===
    jump_parser = subparsers.add_parser('jump', aliases=['j'],
                                        help='Add or remove discontinuities/jumps')
    jump_action = jump_parser.add_mutually_exclusive_group(required=True)
    jump_action.add_argument('--add', '-a', action='store_true',
                            help='Add a new jump')
    jump_action.add_argument('--remove', '-r', action='store_true',
                            help='Remove an existing jump')

    jump_parser.add_argument('--date', '-d', type=str, required=True,
                            help='Date of the jump (format: YYYY/MM/DD, YYYY_DOY, or decimal year)')
    jump_parser.add_argument('--type', '-t', type=str, default='mechanical',
                            choices=['mechanical', 'coseismic', 'postseismic-only'],
                            help='Type of jump: mechanical (0), coseismic with decay (1), postseismic-only (2)')
    jump_parser.add_argument('--relaxation', '-x', type=float, nargs='+', metavar='YEARS',
                            help='Relaxation time constants in years (required for coseismic/postseismic)')
    add_solution_args(jump_parser)

    # === COPY-PARAMS command ===
    copy_parser = subparsers.add_parser('copy-params', aliases=['copy', 'cp'],
                                        help='Synchronize parameters between solution types')
    copy_action = copy_parser.add_mutually_exclusive_group(required=True)
    copy_action.add_argument('--enable', '-e', action='store_true',
                            help='Enable parameter copying and copy from source to all other solutions')
    copy_action.add_argument('--disable', '-d', action='store_true',
                            help='Disable parameter copying (solutions will be independent)')
    copy_action.add_argument('--status', '-s', action='store_true',
                            help='Show current copy-params status')

    copy_parser.add_argument('--source', type=str, choices=['gamit', 'ppp', 'ngl'],
                            help='Source solution type to copy FROM (required with --enable)')

    # === PRINT command ===
    print_parser = subparsers.add_parser('print', aliases=['show', 'ls'],
                                         help='Display current ETM parameters')
    print_parser.add_argument('--etm', '-e', action='store_true',
                             help='Query the actual ETM instead of database parameters '
                                  '(shows fitted params, fit status, metadata)')
    print_parser.add_argument('--stack', type=str, metavar='NAME',
                             help='Stack name for GAMIT solutions (required with --etm for gamit)')
    print_parser.add_argument('--verbose', '-v', action='store_true',
                             help='Show detailed parameter information')
    # Print shows all solutions by default
    print_parser.add_argument('--solution', '-s', type=str, nargs='+',
                             choices=['gamit', 'ppp', 'ngl'],
                             default=['gamit', 'ppp', 'ngl'],
                             help='Solution type(s) to display (default: all)')

    # === RESET command ===
    reset_parser = subparsers.add_parser('reset', help='Reset parameters to defaults')
    reset_parser.add_argument('--polynomial', action='store_true',
                             help='Reset polynomial parameters')
    reset_parser.add_argument('--periodic', action='store_true',
                             help='Reset periodic parameters')
    reset_parser.add_argument('--jumps', action='store_true',
                             help='Reset all jump parameters')
    reset_parser.add_argument('--all', action='store_true',
                             help='Reset all parameters')
    add_solution_args(reset_parser)

    # === BULK-REMOVE command ===
    bulk_parser = subparsers.add_parser('bulk-remove', aliases=['bulk'],
                                        help='Bulk remove jumps by criteria')
    bulk_type = bulk_parser.add_mutually_exclusive_group(required=True)
    bulk_type.add_argument('--earthquakes', '-e', type=float, metavar='MAX_MAG',
                          help='Remove earthquake jumps with magnitude <= MAX_MAG')
    bulk_type.add_argument('--mechanical', '-m', action='store_true',
                          help='Remove mechanical jumps')

    bulk_parser.add_argument('--start-date', type=str, metavar='DATE',
                            help='Start date for removal window (default: beginning of data)')
    bulk_parser.add_argument('--end-date', type=str, metavar='DATE',
                            help='End date for removal window (default: today)')
    bulk_parser.add_argument('--stack', type=str, metavar='NAME',
                            help='Stack name for GAMIT solutions (required if using gamit)')
    add_solution_args(bulk_parser)

    add_version_argument(parser)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Connect to database
    cnn = dbConnection.Cnn("gnss_data.cfg")

    # Process station list
    stnlist = Utils.process_stnlist(cnn, args.stations)

    if not stnlist:
        print("Error: No stations found matching the criteria")
        sys.exit(1)

    # Dispatch to appropriate handler
    try:
        if args.command in ('polynomial', 'poly', 'p'):
            handle_polynomial(cnn, stnlist, args)
        elif args.command in ('periodic', 'per', 'q'):
            handle_periodic(cnn, stnlist, args)
        elif args.command in ('jump', 'j'):
            handle_jump(cnn, stnlist, args)
        elif args.command in ('copy-params', 'copy', 'cp'):
            handle_copy_params(cnn, stnlist, args)
        elif args.command in ('print', 'show', 'ls'):
            handle_print(cnn, stnlist, args)
        elif args.command == 'reset':
            handle_reset(cnn, stnlist, args)
        elif args.command in ('bulk-remove', 'bulk'):
            handle_bulk_remove(cnn, stnlist, args)
    except EtmParamsException as e:
        print(f"Error: {e}")
        sys.exit(1)


def add_solution_args(parser):
    """Add common solution type arguments to a parser."""
    parser.add_argument('--solution', '-s', type=str, nargs='+',
                       choices=['gamit', 'ppp', 'ngl'],
                       default=['gamit', 'ppp'],
                       help='Solution type(s) to modify (default: gamit ppp)')


def get_solution_types(args) -> List[str]:
    """Get list of solution types from args."""
    return getattr(args, 'solution', ['gamit', 'ppp'])


def create_etm_params(cnn, network_code: str, station_code: str, soln: str) -> EtmParams:
    """Create an EtmParams instance for a station/solution combination."""
    solution_type = SolutionType.from_code(soln)
    solution_options = SolutionOptions(solution_type=solution_type)
    config = EtmConfig(network_code=network_code, station_code=station_code,
                       solution_options=solution_options)
    return EtmParams(config, cnn)


def create_etm_engine(cnn, network_code: str, station_code: str, soln: str,
                      stack_name: str = None) -> EtmEngine:
    """Create an EtmEngine instance for a station/solution combination."""
    solution_type = SolutionType.from_code(soln)
    solution_options = SolutionOptions(solution_type=solution_type,
                                       stack_name=stack_name or soln)
    config = EtmConfig(network_code=network_code, station_code=station_code,
                       cnn=cnn, solution_options=solution_options)
    return EtmEngine(config, cnn=cnn, silent=True)


def handle_polynomial(cnn, stnlist: List[Dict], args):
    """Handle polynomial parameter changes."""
    # Parse reference date if provided
    ref_year = ref_doy = None
    if args.reference_date:
        try:
            date, _ = process_date([args.reference_date])
            ref_year, ref_doy = date.year, date.doy
        except Exception as e:
            raise EtmParamsException(f"Invalid reference date: {e}")

    params = {
        'object': 'polynomial',
        'terms': args.terms
    }
    if ref_year is not None:
        params['Year'] = ref_year
        params['DOY'] = ref_doy

    for station in stnlist:
        for soln in get_solution_types(args):
            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], soln)
            print(f"  Setting polynomial terms={args.terms} for "
                  f"{station['NetworkCode']}.{station['StationCode']} ({soln})")
            etm_params.push_params(params=params)


def handle_periodic(cnn, stnlist: List[Dict], args):
    """Handle periodic parameter changes."""
    # Validate periods
    for p in args.periods:
        if p <= 0:
            raise EtmParamsException(f"Period must be positive: {p}")

    params = {
        'object': 'periodic',
        'frequencies': args.periods  # Will be converted to frequencies in push_params
    }

    for station in stnlist:
        for soln in get_solution_types(args):
            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], soln)
            periods_str = ', '.join(f"{p:.2f}" for p in args.periods)
            print(f"  Setting periodic periods=[{periods_str}] days for "
                  f"{station['NetworkCode']}.{station['StationCode']} ({soln})")
            etm_params.push_params(params=params)


def handle_jump(cnn, stnlist: List[Dict], args):
    """Handle jump parameter changes."""
    # Parse date
    try:
        date, _ = process_date([args.date])
    except Exception as e:
        raise EtmParamsException(f"Invalid date: {e}")

    # Map type string to numeric value
    type_map = {'mechanical': 0, 'coseismic': 1, 'postseismic-only': 2}
    jump_type = type_map[args.type]

    # Validate relaxation for non-mechanical jumps
    if args.add and jump_type > 0:
        if not args.relaxation:
            raise EtmParamsException(
                f"Relaxation times required for {args.type} jumps (use --relaxation)")
        for r in args.relaxation:
            if r <= 0:
                raise EtmParamsException(f"Relaxation time must be positive: {r}")

    action = '+' if args.add else '-'

    params = {
        'object': 'jump',
        'Year': date.year,
        'DOY': date.doy,
        'action': action,
        'jump_type': jump_type
    }
    if args.relaxation:
        params['relaxation'] = args.relaxation

    action_str = "Adding" if args.add else "Removing"
    for station in stnlist:
        for soln in get_solution_types(args):
            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], soln)
            print(f"  {action_str} {args.type} jump on {date.year}/{date.doy:03d} for "
                  f"{station['NetworkCode']}.{station['StationCode']} ({soln})")
            etm_params.push_params(params=params)


def handle_copy_params(cnn, stnlist: List[Dict], args):
    """Handle copy-params command."""
    if args.enable:
        if not args.source:
            raise EtmParamsException("--source is required when using --enable")

        for station in stnlist:
            # Create EtmParams for the source solution
            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], args.source)

            print(f"  Enabling copy-params for {station['NetworkCode']}.{station['StationCode']} "
                  f"(source: {args.source})")
            etm_params.push_params(copy_params=True)

    elif args.disable:
        for station in stnlist:
            # Use any solution type - the flag is stored with soln='all'
            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], 'gamit')
            print(f"  Disabling copy-params for {station['NetworkCode']}.{station['StationCode']}")
            etm_params.push_params(copy_params=False)

    elif args.status:
        for station in stnlist:
            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], 'gamit')
            enabled = etm_params.is_copy_params_enabled()
            status = "enabled" if enabled else "disabled"
            print(f"  {station['NetworkCode']}.{station['StationCode']}: copy-params is {status}")


def handle_print(cnn, stnlist: List[Dict], args):
    """Handle print command."""
    # Use specified solutions, or all if not specified
    solutions = get_solution_types(args)

    # If querying ETM, need stack for gamit
    if args.etm:
        if 'gamit' in solutions and not args.stack:
            raise EtmParamsException("--stack is required when using --etm with gamit solutions")

    for station in stnlist:
        stn_id = f"{station['NetworkCode']}.{station['StationCode']}"
        print(f"\n{stn_id}:")

        for soln in solutions:
            if args.etm:
                # Query actual ETM
                print_etm_params(cnn, station, soln, args)
            else:
                # Query database parameters
                print_db_params(cnn, station, soln)

        # Check copy_params status
        etm_params = create_etm_params(cnn, station['NetworkCode'],
                                       station['StationCode'], 'gamit')
        if etm_params.is_copy_params_enabled():
            print(f"  [copy-params: enabled]")
        else:
            print(f"  [copy-params: disabled]")


def print_db_params(cnn, station: Dict, soln: str):
    """Print parameters from database."""
    etm_params = create_etm_params(cnn, station['NetworkCode'],
                                   station['StationCode'], soln)
    params = etm_params.pull_params_from_db()

    print(f"  [{soln}]")

    # Polynomial
    if params['polynomial']:
        poly = params['polynomial']
        ref_str = ""
        if poly['Year'] and poly['DOY']:
            ref_str = f" (ref: {poly['Year']} {poly['DOY']:03d})"
        print(f"    Polynomial: {poly['terms']} terms{ref_str}")
    else:
        print(f"    Polynomial: default")

    # Periodic - table format
    print(f"    Periodic:")
    if params['periodic']:
        print(f"      {'Period (days)':<15} {'Status':<20}")
        print(f"      {'-'*15} {'-'*20}")
        for period, status in params['periodic'].items():
            status_str = status.description if hasattr(status, 'description') else str(status)
            print(f"      {period:<15.2f} {status_str:<20}")
    else:
        print(f"      (default: auto-detect)")

    # Jumps - table format
    print(f"    Jumps:")
    if params['jumps']:
        type_names = {0: 'mechanical', 1: 'coseismic', 2: 'postseismic-only'}
        action_names = {'+': 'add', '-': 'remove'}
        print(f"      {'Date':<12} {'Action':<8} {'Type':<18} {'Relaxation':<20}")
        print(f"      {'-'*12} {'-'*8} {'-'*18} {'-'*20}")
        for jump in params['jumps']:
            jtype = type_names.get(jump['jump_type'], 'unknown')
            action = action_names.get(jump.get('action'), jump.get('action', '?'))
            date_str = f"{jump['Year']} {jump['DOY']:03d}"
            relax_str = str(jump['relaxation']) if jump['relaxation'] else "-"
            print(f"      {date_str:<12} {action:<8} {jtype:<18} {relax_str:<20}")
    else:
        print(f"      (none - using defaults)")
    print(f"    Copy params: {params['copy_params']}")


def print_etm_params(cnn, station: Dict, soln: str, args):
    """Print parameters from actual ETM."""
    try:
        stack_name = args.stack if soln == 'gamit' else soln
        etm = create_etm_engine(cnn, station['NetworkCode'],
                                station['StationCode'], soln, stack_name)

        etm_params = EtmParams.from_etm(etm, cnn)
        params = etm_params.pull_params()

        print(f"  [{soln}] (from ETM)")

        # Polynomial
        poly = params['polynomial']
        ref_str = ""
        if poly['Year'] and poly['DOY']:
            ref_str = f" (ref: {poly['Year']} {poly['DOY']:03d})"
        print(f"    Polynomial: {poly['terms']} terms{ref_str}")

        # Periodic - table format
        print(f"    Periodic:")
        if params['periodic']:
            print(f"      {'Period (days)':<15} {'Status':<20}")
            print(f"      {'-'*15} {'-'*20}")
            for period, status in params['periodic'].items():
                status_str = status.description if hasattr(status, 'description') else str(status)
                print(f"      {period:<15.2f} {status_str:<20}")
        else:
            print(f"      (none)")

        # Jumps - table format with metadata
        fitted = [j for j in params['jumps'] if j['fit']]
        not_fitted = [j for j in params['jumps'] if not j['fit']]
        print(f"    Jumps ({len(fitted)} fitted, {len(not_fitted)} not fitted):")

        if params['jumps']:
            print(f"      {'Date':<12} {'Type':<25} {'Relaxation':<20} {'Fit':<6} {'Metadata':<30}")
            print(f"      {'-'*12} {'-'*25} {'-'*20} {'-'*6} {'-'*30}")
            for jump in params['jumps']:
                date_str = f"{jump['Year']} {jump['DOY']:03d}"
                # 'type' is already a description string from pull_params
                jtype = jump['type'][:25] if len(jump['type']) > 25 else jump['type']
                relax_str = str(jump['relaxation']) if jump['relaxation'] and any(r > 0 for r in jump['relaxation']) else "-"
                fit_str = "yes" if jump['fit'] else "no"
                meta_str = (jump.get('metadata', '-') or '-')[:30]
                print(f"      {date_str:<12} {jtype:<25} {relax_str:<20} {fit_str:<6} {meta_str:<30}")
        else:
            print(f"      (none)")

    except SolutionDataException as e:
        print(f"  [{soln}] Error: {e}")
    except Exception as e:
        print(f"  [{soln}] Error loading ETM: {e}")


def handle_reset(cnn, stnlist: List[Dict], args):
    """Handle reset command."""
    if not any([args.polynomial, args.periodic, args.jumps, args.all]):
        raise EtmParamsException("Specify what to reset: --polynomial, --periodic, --jumps, or --all")

    reset_poly = args.polynomial or args.all
    reset_per = args.periodic or args.all
    reset_jumps = args.jumps or args.all

    what = []
    if reset_poly:
        what.append("polynomial")
    if reset_per:
        what.append("periodic")
    if reset_jumps:
        what.append("jumps")

    for station in stnlist:
        for soln in get_solution_types(args):
            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], soln)
            print(f"  Resetting {', '.join(what)} for "
                  f"{station['NetworkCode']}.{station['StationCode']} ({soln})")
            etm_params.push_params(reset_polynomial=reset_poly,
                                   reset_periodic=reset_per,
                                   reset_jumps=reset_jumps)


def handle_bulk_remove(cnn, stnlist: List[Dict], args):
    """Handle bulk-remove command for earthquakes or mechanical jumps."""
    solutions = get_solution_types(args)

    # Parse date range
    start_date, end_date = process_date(
        ([args.start_date] if args.start_date else []) +
        ([args.end_date] if args.end_date else [])
    )

    # Map JumpType enum values for filtering
    # Earthquake types: COSEISMIC_JUMP_DECAY (10), COSEISMIC_ONLY (15), POSTSEISMIC_ONLY (20)
    earthquake_types = (JumpType.COSEISMIC_JUMP_DECAY, JumpType.COSEISMIC_ONLY, JumpType.POSTSEISMIC_ONLY)
    # Mechanical types: MECHANICAL_MANUAL (1), MECHANICAL_ANTENNA (2)
    mechanical_types = (JumpType.MECHANICAL_MANUAL, JumpType.MECHANICAL_ANTENNA)

    for station in stnlist:
        for soln in solutions:
            stn_id = f"{station['NetworkCode']}.{station['StationCode']} ({soln})"
            print(f"  Processing {stn_id}...")

            # Need to load ETM to get jump list
            try:
                stack_name = args.stack if soln == 'gamit' else soln
                if soln == 'gamit' and not args.stack:
                    print(f"    Warning: --stack required for GAMIT solutions, skipping")
                    continue

                etm = create_etm_engine(cnn, station['NetworkCode'],
                                        station['StationCode'], soln, stack_name)
            except SolutionDataException as e:
                print(f"    Error loading ETM: {e}")
                continue
            except Exception as e:
                print(f"    Error loading ETM: {e}")
                continue

            etm_params = create_etm_params(cnn, station['NetworkCode'],
                                           station['StationCode'], soln)

            removed_count = 0

            if args.earthquakes is not None:
                # Remove earthquake jumps below magnitude threshold
                for jump in etm.jump_manager.jumps:
                    if jump.p.jump_type in earthquake_types and jump.magnitude <= args.earthquakes:
                        if start_date <= jump.date <= end_date:
                            params = {
                                'object': 'jump',
                                'Year': jump.date.year,
                                'DOY': jump.date.doy,
                                'action': '-',
                                'jump_type': 1  # coseismic in etm_params format
                            }
                            etm_params.push_params(params=params)
                            removed_count += 1
                            print(f"    Removed M{jump.magnitude:.1f} earthquake on "
                                  f"{jump.date.year}/{jump.date.doy:03d}")

            elif args.mechanical:
                # Remove mechanical jumps
                for jump in etm.jump_manager.jumps:
                    if jump.p.jump_type in mechanical_types:
                        if start_date <= jump.date <= end_date:
                            params = {
                                'object': 'jump',
                                'Year': jump.date.year,
                                'DOY': jump.date.doy,
                                'action': '-',
                                'jump_type': 0  # mechanical in etm_params format
                            }
                            etm_params.push_params(params=params)
                            removed_count += 1
                            print(f"    Removed mechanical jump on "
                                  f"{jump.date.year}/{jump.date.doy:03d}")

            if removed_count == 0:
                print(f"    No matching jumps found")
            else:
                print(f"    Removed {removed_count} jump(s)")


if __name__ == '__main__':
    main()
