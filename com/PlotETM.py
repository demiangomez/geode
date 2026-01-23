#!/usr/bin/env python

"""
Project: Parallel.PPP
Date: 10/10/17 9:10 AM
Author: Demian D. Gomez

User interface to plot and save JSON files of ETM objects.
Type python pyPlotETM.py -h for usage help
"""
import argparse
import os
import logging
from dataclasses import asdict
from datetime import datetime

# deps
import numpy as np
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
import matplotlib

# app
from geode import dbConnection
from geode.pyDate import Date
from geode.metadata.station_info import StationInfo
from geode.etm.core.logging_config import setup_etm_logging
from geode.etm.core.etm_engine import EtmEngine, EtmSolutionType, EtmEncoder
from geode.etm.core.etm_config import EtmConfig
from geode.etm.core.s_score import ScoreTable
from geode.etm.core.type_declarations import JumpType
from geode.etm.data.solution_data import SolutionDataException
from geode.etm.core.data_classes import (AdjustmentModels, SolutionType, CovarianceFunction,
                                          ModelingParameters, SolutionOptions, StationMetadata, JumpParameters)
from geode.etm.etm_functions.polynomial import PolynomialFunction
from geode.etm.etm_functions.periodic import PeriodicFunction
from geode.etm.etm_functions.jumps import JumpFunction
from geode.Utils import (process_date,
                          process_stnlist,
                          station_list_help,
                          stationID,
                          print_columns,
                          add_version_argument,
                          lla2ecef, get_country_code,
                          file_write,
                          load_json,
                          process_date_str)


# Map verbosity to logging levels
VERBOSITY_MAP = {
    'quiet': logging.CRITICAL,  # or logging.NOTSET to disable all
    'info': logging.INFO,
    'debug': logging.DEBUG
}

ADJUSTMENT_MODEL_MAP = {
    'rls': AdjustmentModels.ROBUST_LEAST_SQUARES,
    'lsc': AdjustmentModels.LSQ_COLLOCATION
}

COVARIANCE_MAP = {
    'arma': CovarianceFunction.ARMA,
    'gaussian': CovarianceFunction.GAUSSIAN
}

def read_kml_or_kmz(cnn: dbConnection.Cnn, file_path: str, metadata: str, s_score_mag_limit: float):
    # Check if the file is a KMZ (by its extension)
    if file_path.endswith('.kmz'):
        # Open the KMZ file and read it in memory
        with zipfile.ZipFile(file_path, 'r') as kmz:
            # List all files in the KMZ archive
            kml_file = None
            for file_name in kmz.namelist():
                if file_name.endswith(".kml"):
                    kml_file = file_name
                    break

            if not kml_file:
                raise Exception("No KML file found in the KMZ archive")

            # Extract the KML file into memory (as a BytesIO object)
            kml_content = kmz.read(kml_file)
            kml_file = BytesIO(kml_content)

    else:
        # If the file is a regular KML, process it directly
        kml_file = open(file_path, 'r')

    # Extract coordinates from the KML file
    placemarks = extract_placemarks(kml_file)

    # Close the file if it was opened from the filesystem
    if not isinstance(kml_file, BytesIO):
        kml_file.close()

    stnlist = []

    for stnm, coord in placemarks:
        network_code, station_code = stnm.split('.')

        ecef = lla2ecef(np.array([coord[0][0], coord[1][0], coord[2][0]]))

        # if a connection to the database is available, get s-scores
        if cnn:
            score = ScoreTable(cnn, network_code, station_code,
                               lat=coord[0][0], lon=coord[1][0],
                               sdate=Date(year=1990, doy=1),
                               edate=Date(datetime=datetime.now()),
                               magnitude_limit=s_score_mag_limit)
            score_table = score.table
        else:
            score_table = []

        if metadata:
            stn = StationInfo(cnn=None, NetworkCode=network_code, StationCode=station_code)
            stninfo = [item.to_json() for item in stn.parse_station_info(metadata)
                       if item.StationCode == station_code]
        else:
            stninfo = []

        station_meta = StationMetadata(
            name='unknown',
            country_code=get_country_code(coord[0], coord[1]),
            lon=np.array(coord[1]),
            lat=np.array(coord[0]),
            height=np.array(coord[2]),
            auto_x=np.array(ecef[0]),
            auto_y=np.array(ecef[1]),
            auto_z=np.array(ecef[2]),
            max_dist=1000,  # increase the allowable distance to avoid problems with aprox coordinates
            )
        # put in a dict, not stationinfo objects
        station_meta.station_information = stninfo

        # create a station list with dictionaries for sites
        stnlist.append({
            'network_code': network_code,
            'NetworkCode': network_code,
            'station_code': station_code,
            'StationCode': station_code,
            'solution_options': asdict(SolutionOptions(SolutionType.NGL, 'file')),
            'modeling_params': asdict(ModelingParameters(
                earthquake_jumps=score_table
            )),
            'station_meta': asdict(station_meta)
        })
    # for debugging
    # file_write('test.json', json.dumps(stnlist, indent=4, sort_keys=False, cls=EtmEncoder))

    return stnlist


# Helper function to extract placemark and coordinates from a KML file
def extract_placemarks(kml_file):
    tree = ET.parse(kml_file)
    root = tree.getroot()

    # Define the KML namespace
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}

    # Initialize list to store placemark names and coordinates
    placemarks = []

    # Loop through all placemarks
    for placemark in root.findall('.//kml:Placemark', ns):
        # Extract the placemark name (if available)
        name_element = placemark.find('kml:name', ns)
        name = name_element.text if name_element is not None else "Unnamed"

        # Extract the coordinates for the placemark
        coordinates_list = []
        for coord in placemark.findall('.//kml:coordinates', ns):
            coords = coord.text.strip().split()
            for coord_str in coords:
                lon, lat, height = coord_str.split(',')  # Ignore the altitude (third value)
                coordinates_list.append([np.array([float(lat)]), np.array([float(lon)]), np.array([float(height)])])

        # Store the placemark name and its coordinates
        for coord in coordinates_list:
            placemarks.append((name, coord))

    return placemarks


def process_plot_dates(args):
    dates = None
    if args.plot_window:
        if len(args.plot_window) == 1:
            try:
                dates = process_date(args.plot_window, missing_input=None, allow_days=False)
                dates = (dates[0].fyear, )
            except ValueError:
                # an integer value
                dates = float(args.plot_window[0])
        else:
            dates = process_date(args.plot_window)
            dates = (dates[0].fyear, dates[1].fyear)

    return dates


def process_fit_dates(args):
    dates = []
    if args.fit_window is not None:
        if np.mod(args.fit_window, 2):
            raise ValueError('Fit dates must be given in start/end pairs')

        for i in range(0, len(args.fit_window), 2):
            try:
                dates.append(process_date(args.fit_window[i:i+1]))
            except ValueError:
                raise ValueError('Invalid fit dates')

    return None if not dates else dates


def print_query(args, etm: EtmEngine):

    mode_obs = EtmSolutionType.MODEL if args.query[0] == 'model' else EtmSolutionType.OBSERVATION
    date_index = 1

    strp = ''
    # if user requests velocity too, output it
    if etm.config.modeling.status == etm.config.modeling.status.POSTFIT:
        if 'vel' in args.query:
            date_index += 1
            vxyz = []
            for x in etm.design_matrix.functions:
                if x.p.object == 'polynomial':
                    vxyz += [p[1] for p in x.p.params]
            if vxyz:
                strp = '%8.5f %8.5f %8.5f ' % (vxyz[0], vxyz[1], vxyz[2])

        if 'per' in args.query:
            date_index += 1
            pxyz = []
            for x in etm.design_matrix.functions:
                if x.p.object == 'periodic':
                    for i in range(3):
                        pxyz += ['%8.5f' % (p * 1000) for p in x.p.params[i].tolist()]
                    # also output seasonal terms, if requested
                    strp += ' '.join(pxyz)

    q_date = []
    for d in args.query[date_index:]:
        q_date.append(process_date_str(d))

    solution = etm.get_position(q_date, mode_obs)

    for i, d in enumerate(q_date):
        print(' %s %14.5f %14.5f %14.5f %8.3f %s -> %s' \
              % (etm.config.get_station_id(), solution['position'][0][i],
                 solution['position'][1][i], solution['position'][2][i], d.fyear, strp, solution['source']))


def get_prefit_models(config, args):

    user_prefit_models = []

    if args:
        function_map_str = {'poly': 'polynomial', 'per': 'periodic', 'jump': 'jump'}
        function_map = {'poly': PolynomialFunction, 'per': PeriodicFunction, 'jump': JumpFunction}
        # begin by loading the json
        functions = load_json(args[0])['functions']
        # read current function
        current_funct = args[1]
        # set i at the correct location
        i = 2
        while i < len(args):
            # find the json info for this function
            print(' -- Adding ' + function_map_str[current_funct] + ' as prefit model')
            if current_funct == 'jump':
                # if it is a jump assimilate the date also
                mjd = process_date_str(args[i]).mjd
                # find the correct jump
                jf = [funct for funct in functions if funct['object'] == function_map_str[current_funct]
                      and funct['jump_date']['mjd'] == mjd]
                # create instance of function
                funct = function_map[current_funct](config,
                                                    time_vector=np.array([0]),
                                                    date=Date(**jf[0]['jump_date']),
                                                    jump_type=JumpType.UNDETERMINED,
                                                    fit=True)
                i += 1
            else:
                jf = [funct for funct in functions if funct['object'] == function_map_str[current_funct]]
                # create instance of function
                funct = function_map[current_funct](config)

            if not len(jf):
                raise ValueError('Constraint function ' + function_map_str[current_funct] +
                                 ' with matching arguments could not be found')

            funct.load_from_json(jf[0])
            # append to constraints
            user_prefit_models.append(funct)
            i += 1

        for funct in user_prefit_models:
            print(funct.p.params)

    return user_prefit_models


def get_constraints(config, args):

    user_constraints = []
    if args:
        function_map_str = {'poly': 'polynomial', 'per': 'periodic', 'jump': 'jump'}
        function_map = {'poly': PolynomialFunction, 'per': PeriodicFunction, 'jump': JumpFunction}
        # begin by loading the json
        functions = load_json(args[0])['functions']
        # read current function
        current_funct = args[1]
        arg_count = 0
        # set i at the correct location
        i = 2
        while i < len(args):
            # get first element in const, which should be a function keyword
            if arg_count == 0:
                # find the json info for this function
                print(' -- Adding ' + function_map_str[current_funct] + ' as constraint')
                if current_funct == 'jump':
                    # if it is a jump assimilate the date also
                    mjd = process_date_str(args[i]).mjd
                    # find the correct jump
                    jf = [funct for funct in functions if funct['object'] == function_map_str[current_funct]
                          and funct['jump_date']['mjd'] == mjd]
                    # create instance of function
                    funct = function_map[current_funct](config,
                                                        time_vector=np.array([0]),
                                                        date=Date(**jf[0]['jump_date']),
                                                        jump_type=JumpType.UNDETERMINED,
                                                        fit=False)
                    i += 1
                else:
                    jf = [funct for funct in functions if funct['object'] == function_map_str[current_funct]]
                    # create instance of function
                    funct = function_map[current_funct](config)

                if not len(jf):
                    raise ValueError('Constraint function ' + function_map_str[current_funct] +
                                     ' with matching arguments could not be found')

                funct.load_from_json(jf[0])
                # to flag which parameters to use as constraints
                params = np.zeros_like(funct.p.params[0], dtype=bool)

                # append to constraints
                user_constraints.append(funct)
                arg_count += 1
            else:
                # check if we are at a new function or we have a new parameter to process
                if args[i] in function_map_str.keys():
                    # apply constraints
                    for j in range(3):
                        user_constraints[-1].p.params[j][~params] = np.nan
                        user_constraints[-1].p.sigmas[j][~params] = np.nan
                    # reset arg_count
                    arg_count = 0
                    # set new current_funct
                    current_funct = args[i]
                    print(' -- Found new constraint ' + current_funct)
                else:
                    print(' -- Assigning parameter ' + args[i])
                    # read and activate parameter if selected
                    params[int(args[i][1])] = True
                    arg_count += 1

                i += 1
        # if we arrived to the end of the argument list, apply the constraints to the last element
        # apply constraints
        for i in range(3):
            user_constraints[-1].p.params[i][~params] = np.nan
            user_constraints[-1].p.sigmas[i][~params] = np.nan

        for funct in user_constraints:
            print(funct.p.params)

    return user_constraints


def process_custom_relaxations(cnn:dbConnection.Cnn, config: EtmConfig, custom_relax):
    events = []
    current_event = None
    current_relaxations = []

    for arg in custom_relax:
        try:
            # Try to convert to float - if successful, it's a relaxation value
            relaxation = float(arg)
            current_relaxations.append(relaxation)
        except ValueError:
            # If conversion fails, it's an event ID
            # Save the previous event if it exists
            if current_event is not None:
                events.append((current_event, current_relaxations))

            # Start a new event
            current_event = arg
            current_relaxations = []

    # Don't forget the last event
    if current_event is not None:
        events.append((current_event, current_relaxations))

    # now process the relaxations
    for event, relax in events:
        rs = cnn.query_float("SELECT * FROM earthquakes WHERE id = '%s'" % event, as_dict=True)[0]
        date = Date(datetime=rs['date'])
        jump_params = JumpParameters(
            jump_type=JumpType.COSEISMIC_JUMP_DECAY,
            relaxation=relax,
            date=date,
            action='+')

        config.modeling.user_jumps.append(jump_params)


def main():
    parser = argparse.ArgumentParser(description='Plot extended trajectory models (ETMs) '
                                                 'for station data stored in the database, json files, or text files',
                                     formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('stnlist', type=str, nargs='+',
                        help=station_list_help() + '\n\nAlternatively, read station list from '
                                                   'kmz/kml file to obtain network codes and stations names')

    parser.add_argument('-sol', '--solution', type=str, metavar='{stack|ppp}', default='ppp',
                        help="Required: specify the GAMIT stack name or ppp to use GPSPACE solutions.")

    parser.add_argument('-lsq', '--least_squares_strategy',
                        choices=['lsc', 'rls'], default='rls',
                        help="Adjustment strategy to fit the time series. Choose between least squares collocation "
                             "(lsc) and robust least squares (rls)")

    parser.add_argument('-const', '--constraints', nargs='+', default=None, metavar='{json} {function}',
                        help="Constrain fit using json function (ETM) file. Provide list of functions to constrain "
                             "using keywords: poly -> polynomial; per -> periodic; jumps -> mechanical and "
                             "geophysical jumps. Each function has associated arguments to better specify which "
                             "parameters to constrain: poly [p0] [p1] ... [pn] -> include key 'p+degree' to constrain "
                             "where degree = 0 to n depending how many you want to constrain; "
                             "per [f0] [f1] ... [fn] -> include key 'f+number' to constrain a frequency, where number "
                             "is the frequency number in the order that appear in the json; "
                             "jump [date] [j0] [j1] [j2] ... [jn] -> include date in all accepted formats to select "
                             "which jump from the json to use and 'j+number' to constraint each jump component where "
                             "number indicates the parameter number in the order that appear in the json (offset "
                             "is always first)")

    parser.add_argument('-prefit', '--prefit_model', nargs='+', default=None,
                        metavar='{json} {function}',
                        help="Remove model from data using json function (ETM) file. Provide list of functions "
                             "to remove using keywords: poly -> polynomial; per -> periodic; jumps -> mechanical and "
                             "geophysical jumps")

    parser.add_argument('-cova', '--covariance', nargs='+',
                        choices=['arma', 'gaussian'], default=['arma', 49, 200],
                        help="To use with strategy lsc, provide the method to estimate the covariance matrix. "
                             "Choose between Autoregressive Moving Average (arma, default) and gaussian covariance "
                             "function. If arma is used, optionally provide the number of roots for the decomposition "
                             "(49 by default) and the number of points to estimate the AR process (200 by default)")

    parser.add_argument('-fit_win', '--fit_window', nargs='+', metavar='interval',
                        help='Date range to window data fit. Can be specified in yyyy/mm/dd, yyyy_doy '
                             'in pairs for data ranges to be included in the ETM fit. Data outside '
                             'the range will be plotted but not included in the fit')

    parser.add_argument('-no_jump_check', '--no_jump_check', action='store_true', default=False,
                        help="Avoid checking for possible collisions between jumps which can lead to "
                             "unstable systems of equations. Default is to check jump collisions")

    parser.add_argument('-s_score', '--s_score_mag_limit', type=float, default=6.0, metavar='magnitude',
                        help="Limit the s-score search to earthquakes with magnitude >= {magnitude}. Default is 6.0")

    parser.add_argument('-post_back', '--post_seismic_back', type=str, default=10, metavar='years|date',
                        help="How many years (or date) since the start of the time series should the ETM fit postseismic "
                             "transients. Default is 10 years")

    parser.add_argument('-force', '--force_earthquakes', nargs='+', default=[], metavar='event_id',
                        help="Add cherry-picked seismic earthquake (that fall outside of s_score_mag_limit) to the "
                             "list of jump functions to fit (using the USGS event id). Event needs to have an "
                             "s-score > 0 to be considered, even if it has been cherry-picked")

    parser.add_argument('-cond', '--max_condition_number', type=float, default=3.5, metavar='log10(cond)',
                        help="Maximum acceptable log10 condition number for a jump function. Jump parameters with "
                             "log10(cond) > max_condition_number will get their smallest relaxation value removed. If "
                             "high condition number persists, then the jump will be reduced to POSTSEISMIC-ONLY if "
                             "jump is CO+POSTSEISMIC")

    parser.add_argument('-sigma', '--sigma_limit', type=float, default=2.5,
                        help="Number of sigmas for the limit of outlier detection. Default is 2.5 sigma")

    parser.add_argument('-iter', '--max_iterations', type=int, default=10,
                        help="Maximum number of iterations during outlier detection. Default is 10")

    parser.add_argument('-relax', '--default_relax', type=float, nargs='+',
                        default=ModelingParameters().relaxation,
                        help="Relaxation value(s) to use during the fit. Default as defined by the station in "
                             "the database or in the ETM module (0.05 and 1 years)")

    parser.add_argument('-event_relax', '--event_relax', type=str, nargs='+',
                        default=[], metavar='event_id relax1 relax2',
                        help="Override default relaxation values with custom relaxation for specific events without "
                             "modifying the database. If custom database entry exists, this parameter overrides "
                             "the existing configuration")

    parser.add_argument('-plot', '--plot_options', nargs='*',
                        choices=['out', 'hist', 'missing', 'residuals', 'no-model', 'no-plots'],
                        default=[],
                        help="Plotting options: "
                             "out -> plot right panel with the outliers marked in cyan; "
                             "hist -> plot additional file with histograms of residuals; "
                             "residuals -> show only residuals (remove all ETM functions); "
                             "missing -> show missing solution days as magenta lines in the plot; "
                             "no-model -> plot time series without fitting a model (do not estimate parameters); "
                             "no-plots -> to use for querying, do not produce plots but estimate parameters")

    parser.add_argument('-dir', '--directory', type=str, default='production/',
                        help="Directory to save the time series PNG files. If not specified, "
                             "files will be saved in the production directory")

    parser.add_argument('-json', '--json',
                        choices=['dmx', 'obs', 'raw', 'mod'], nargs='*', default=None,
                        help="Export ETM to JSON. By default the json will contain the "
                             "station metadata and the functions fit by the ETM (with "
                             "parameters and sigmas). Append additional output options: "
                             "dmx (design matrix), obs (observations), raw (raw results), "
                             "mod (model)")

    parser.add_argument('-gui', '--interactive', action='store_true',
                        help="Interactive mode: allows to zoom and view the plot interactively")

    parser.add_argument('-rm', '--remove', nargs='+',
                        choices=['poly', 'per', 'jumps', 'stoch'], default=None,
                        help="Remove components from model and time series before plotting. Options are: "
                             "poly -> polynomial; per -> periodic; jumps -> mechanical and geophysical jumps; "
                             "stoch -> stochastic noise if adjustment strategy is 'least squares collocation'. "
                             "If no arguments are given, then all components are removed (same output as "
                             "residuals mode)")

    parser.add_argument('-plot_win', '--plot_window', nargs='+', metavar='interval',
                        default=[],
                        help='Date range to window plot. Can be specified in yyyy/mm/dd, yyyy_doy or as a single '
                             'integer value (N) which shall be interpreted as last epoch minus N days')

    parser.add_argument('-lang', '--language', type=str, default='eng',
                        help="Change the language of the plots. Default is English. "
                        "Use ESP to select Spanish. To add more languages, "
                        "include the ISO 639-1 code in etm_config")

    parser.add_argument('-file', '--filename', type=str, default=None,
                        help="Obtain data from an external source (filename, json or text format). This name accepts "
                             "variables for {net} and {stn} to specify more than one file based on a list of "
                             "stations. If a single file is used (no variables), then only the first station "
                             "is processed. File column format should be specified with -format (required) "
                             "unless files are in json format")

    parser.add_argument('-meta', '--metadata_filename', type=str, default=None,
                        help="Obtain metadata from an external source (igs log, NGL, station info). This name accepts "
                             "variables for {net} and {stn} to specify more than one file based on a list of "
                             "stations.")

    parser.add_argument('-format', '--format', nargs='+', type=str,
                        default=('na', 'na', 'fyear', 'x', 'y', 'z'),
                        help="To be used together with --filename. Specify order of the fields as found in the input "
                             "file. Format strings are gpsWeek, gpsWeekDay, year, doy, fyear, month, day, mjd, "
                             "x, y, z, na. Use 'na' to specify a field that should be ignored. If fields to be ignored "
                             "are at the end of the line, then there is no need to specify those. Default is "
                             "na na fyear x y z")

    parser.add_argument('-kmz', '--kmz', nargs=1, type=str, default=None,
                        help="To be used together with --filename and --format. Do not fetch station metadata from "
                             "the database but rather use the provided kmz/kml file to obtain station coordinates "
                             "and other relevant metadata. When using this option, the station list is ignored and "
                             "only one station is processed.")

    parser.add_argument('-fit_auto', '--fit_auto_jumps', nargs=1,
                        choices=['angry', 'dbscan'], default=[None],
                        help="Fit unmodeled but automatically detected jumps. "
                             "Choose between two algorithms: angry (used and provided by JPL) and dbscan")

    parser.add_argument('-q', '--query', nargs='*',
                        metavar='{type}', default=[],
                        help='Specify "model" or "solution" to get the '
                             'ETM value or the value of the daily solution (if exists). '
                             'Specify the date/dates of the desired output. Append "vel" and "per" after model or '
                             'solution to also include the velocity and seasonal (periodic) components. '
                             'Output is in XYZ.')

    parser.add_argument('-no_save', '--no_save_database', action='store_true', default=False,
                        help="Do not fetch / save ETM solution from / to database")

    parser.add_argument('-verbosity', '--verbosity',
                        choices=['quiet', 'info', 'debug'], default='info',
                        help="Determine how detailed the execution messages should be. "
                             "Default is 'info'")

    add_version_argument(parser)

    args = parser.parse_args()

    cnn = dbConnection.Cnn('gnss_data.cfg', write_cfg_file=True)

    setup_etm_logging(level=VERBOSITY_MAP[args.verbosity])

    from_kmz = False
    if args.stnlist[0].endswith(('kmz', 'kml')):
        # user selected database override
        stnlist = read_kml_or_kmz(cnn, args.stnlist[0], args.metadata_filename, args.s_score_mag_limit)

        print(' >> Station from kml/kmz file %s' % args.stnlist[0])
        print_columns([stationID(item) for item in stnlist])
        from_kmz = True
    else:
        stnlist = process_stnlist(cnn, args.stnlist)

    plot_dates = process_plot_dates(args)

    # make sure dir ends in /
    if args.directory[-1] != '/':
        args.directory += '/'

    for stn in stnlist:

        # initialize the solution options to pass to EtmConfig
        solution_options = SolutionOptions()

        if not args.filename:
            solution_options.solution_type = SolutionType.PPP if args.solution == 'ppp' else SolutionType.GAMIT
            solution_options.stack_name = args.solution
        else:
            filename = args.filename.replace('{net}', stn['NetworkCode']).replace('{stn}', stn['StationCode'])
            # requested a file as the source of data
            solution_options.solution_type = SolutionType.NGL
            solution_options.stack_name = 'external file'
            solution_options.format = args.format
            solution_options.filename = filename
            # do not save anything to the database in filename mode
            args.no_save_database = True

        try:
            if from_kmz:
                print('About to process ' + stn['network_code'] + '.' + stn['station_code'])
                config = EtmConfig(stn['network_code'], stn['station_code'], json_file=stn,
                                   solution_options=solution_options)
            else:
                print('About to process ' + stn['NetworkCode'] + '.' + stn['StationCode'])
                config = EtmConfig(stn['NetworkCode'], stn['StationCode'], cnn=cnn,
                                   solution_options=solution_options)

            # select language for plots
            config.language = args.language.lower()

            config.plotting_config.plot_time_window = plot_dates

            if args.remove is not None:
                if not len(args.remove):
                    args.remove = ['poly', 'per', 'jumps', 'stoch']

                config.plotting_config.plot_remove_polynomial = 'poly' in args.remove
                config.plotting_config.plot_remove_jumps = 'jumps' in args.remove
                config.plotting_config.plot_remove_periodic = 'per' in args.remove
                config.plotting_config.plot_remove_stochastic = 'stoch' in args.remove

            if args.interactive:
                matplotlib.use('TkAgg')
                config.plotting_config.interactive = True
            else:
                config.plotting_config.filename = args.directory

            config.plotting_config.plot_show_outliers = 'out' in args.plot_options
            config.plotting_config.plot_residuals_mode = 'residuals' in args.plot_options

            config.modeling.relaxation = np.array(args.default_relax)
            config.modeling.data_model_window = process_fit_dates(args)
            config.modeling.prefit_models = get_prefit_models(config, args.prefit_model)

            config.modeling.check_jump_collisions = not args.no_jump_check
            config.modeling.least_squares_strategy.adjustment_model = ADJUSTMENT_MODEL_MAP[args.least_squares_strategy]
            config.modeling.fit_auto_detected_jumps = args.fit_auto_jumps[0] is not None
            config.modeling.fit_auto_detected_jumps_method = args.fit_auto_jumps[0]
            config.modeling.least_squares_strategy.iterations = args.max_iterations
            config.modeling.least_squares_strategy.sigma_filter_limit = args.sigma_limit
            config.modeling.least_squares_strategy.covariance_function = COVARIANCE_MAP[args.covariance[0]]
            # check for any constraints
            config.modeling.least_squares_strategy.constraints = get_constraints(config, args.constraints)
            if args.covariance[0] == 'arma' and len(args.covariance) > 1:
                config.modeling.least_squares_strategy.arma_roots = args.covariance[1]
            if args.covariance[0] == 'arma' and len(args.covariance) > 2:
                config.modeling.least_squares_strategy.arma_points = args.covariance[2]

            if not os.path.exists(args.directory):
                os.mkdir(args.directory)

            config.validation.max_condition_number = args.max_condition_number
            if (isinstance(args.post_seismic_back, str) and
                    any(char in args.post_seismic_back for char in ('_', '.', '/'))):
                time_back = process_date_str(args.post_seismic_back)
            else:
                time_back = args.post_seismic_back * 365
            config.modeling.post_seismic_back_lim = time_back
            # do not estimate s-score for events < s_score_mag_limit
            config.modeling.earthquake_magnitude_limit = args.s_score_mag_limit
            config.modeling.earthquakes_cherry_picked = args.force_earthquakes
            if not from_kmz:
                config.refresh_config(cnn)

            # add any custom relaxation values for events
            process_custom_relaxations(cnn, config, args.event_relax)

            etm = EtmEngine(config, cnn=cnn)
            etm.run_adjustment(cnn=cnn,
                               try_save_to_db=not args.no_save_database,
                               try_loading_db=not args.no_save_database)

            if args.json is not None:
                etm.save_etm(filename=args.directory,
                             dump_observations='obs' in args.json,
                             dump_design_matrix='dmx' in args.json,
                             dump_raw_results='raw' in args.json,
                             dump_model='mod' in args.json)

            if 'no-plots' not in args.plot_options:
                etm.plot()
                if 'hist' in args.plot_options:
                    etm.plot_hist()

            if args.query:
                print_query(args, etm)

            print('Successfully plotted ' + stn['NetworkCode'] + '.' + stn['StationCode'])
        except SolutionDataException as e:
            print(str(e))


if __name__ == '__main__':
    main()
