#!/usr/bin/env python

import argparse
import glob
import os
import re
import shutil
import traceback
from datetime import timedelta

import numpy
from tqdm import tqdm

# app
from geode import pyRinex
from geode import pyPPP
from geode import pyOptions
from geode import pyOTL
from geode import pyProducts
from geode import pyJobServer
from geode.metadata.station_info import StationInfoException, StationInfo
from geode import dbConnection
from geode.pyPPP import PPPSpatialCheck
from geode.Utils import file_readlines, add_version_argument


def main():

    parser = argparse.ArgumentParser(description='Simple PPP python wrapper. Calculate a coordinate for a RINEX file. '
                                                 'Output one line per file with stnm epoch x y z lat lon h')

    parser.add_argument('files', type=str, nargs='+',
                        help="List of files, directories or wildcards to process. If directories are given, searches "
                             "for .Z files. Individual files or wildcards can be either .Z or ??o. "
                             "Eg: LocationRinex.py ./igm10010.10d.Z ./igm1002a.10o ./cs*.Z ./rinex2process/")

    parser.add_argument('-otl', '--ocean_loading', action='store_true',
                        help="Apply ocean loading coefficients (obtained from grdtab).")

    parser.add_argument('-ns', '--no_split', action='store_true',
                        help="Do not split multiday RINEX files and obtain a single coordinate.")

    parser.add_argument('-no_met', '--no_met', action='store_true',
                        help="Do not apply the GPT2 model to correct tropospheric delays (use GPT).")

    parser.add_argument('-dec', '--decimate', action='store_true',
                        help="Decimate RINEX to 30 s if interval < 15.")

    parser.add_argument('-rnx', '--load_rinex', action='store_true',
                        help="Fix RINEX using pyRinex, create a local copy (with session number+1) and exit. "
                             "Do not run PPP.")

    parser.add_argument('-ins', '--insert_sql', nargs='?', default=False, const='???', metavar='[net]',
                        help="Produce a SQL INSERT statement for this station including OTL and coordinates. "
                             "If a network code [net] is specified, then produce the insert in the database. "
                             "Network code will be inserted if it does not exist. If [net] is not specified, then "
                             "the command will only output the insert statement with ??? as the network code.")

    parser.add_argument('-find', '--find', action='store_true',
                        help="Find the matching station in the db using the "
                        "spatial location algorithm.")

    parser.add_argument('-ne', '--no_erase', action='store_true',
                        help="Do not erase PPP folder structure after completion.")

    parser.add_argument('-back', '--backward_substitution', action='store_true', default=False,
                        help="Run PPP with backward substitution.")

    parser.add_argument('-fix', '--fix_coordinate', nargs='+', metavar='coordinate_file | x y z', default=None,
                        help='Do not solve for station coordinates, fix station position as given in [coordinate_file] '
                             'or provide a list of X Y Z coordinates. File should contain the '
                             'apriori coordinates as a list starting with the station name '
                             'and the X Y Z coordinates. For example: OSU1  595355.1776 -4856629.7091  4077991.9857')

    parser.add_argument('-st', '--solve_troposphere', type=int, default=105,
                        choices=(1, 2, 3, 4, 5, 102, 103, 104, 105),
                        help='Solve for the tropospheric wet delay. Possible options are 1: do not solve, 2-5: solve '
                             'without gradients (number determine the random walk in mm/hr), +100: solve gradients.')

    parser.add_argument('-elv', '--elevation_mask', type=int, default=10,
                        help='Elevation mask (default=10).')

    parser.add_argument('-min', '--min_time_seconds', type=int, default=3600,
                        help='Minimum observation time in seconds for observations (default=3600).')

    parser.add_argument('-code', '--code_only', action='store_true', default=False,
                        help='Run PPP using only code (C1) observations.')

    parser.add_argument('-c', '--copy_results', type=str, nargs=1, metavar='storage_dir',
                        help='Copy the output files (.ses, .sum, .res, .pos) to [storage_dir]. A folder with the '
                             'station name will be created in [storage_dir].')

    parser.add_argument('-res', '--residuals', action='store_true', default=False,
                        help="Save elevation-dependent phase residuals to [station]_[doy]_residuals.txt "
                             "in the current working directory. Uses BWD epochs if available, otherwise FWD.")

    parser.add_argument('-pres', '--print_residuals', action='store_true', default=False,
                        help="Print the elevation-dependent phase residuals table to the screen "
                             "(elevation in degrees, mean residual in mm). Bins with no observations "
                             "are skipped.")

    parser.add_argument('-nocfg', '--no_config_file', type=str, nargs=3,
                        metavar=('sp3_directory', 'sp3_types', 'brdc_directory'),
                        help='Do not attempt to open gnss_data.cfg. Append [sp3_directory], [sp3_types] '
                             'and [brdc_directory] to access the precise and broadcast orbit files. Use the keywords '
                             '$year, $doy, $month, $day, $gpsweek, $gpswkday to dynamically replace with the '
                             'appropriate values (based on the date in the RINEX file). Grdtab and otl_grid should '
                             'have the standard names if -otl is invoked and ppp should be in the PATH '
                             '(with executable name = ppp).')

    parser.add_argument('-eng', '--engine', type=str, choices=('gpspace', 'pride'), default=None,
                        help='Override the ppp_engine set in gnss_data.cfg for this run, without editing the '
                             'config file (e.g. to run both engines back to back and compare results).')

    parser.add_argument('-p', '--parallel', action='store_true', default=False,
                        help="Run in parallel using available cluster nodes (same dispy-based mechanism as "
                             "PlotETM.py). Requires gnss_data.cfg in the working directory.")

    parser.add_argument('-win', '--window', type=float, nargs='+', metavar='hours', default=None,
                        help="Process each file windowed to the given length(s) in hours, counted backwards "
                             "from the end of the observed session (e.g. -win 24 12 6 3 2 1 0.5 0.25 for a "
                             "convergence study). Each (file, window) combination is processed independently "
                             "-- one task per window when run with --parallel -- and its output is prefixed "
                             "with the window length. If not given, each file is processed once, unwindowed.")

    add_version_argument(parser)

    args = parser.parse_args()

    Config = pyOptions.ReadOptions('gnss_data.cfg')  # type: pyOptions.ReadOptions
    options = Config.options
    sp3types = Config.sp3types
    # DDG: now there is no sp3altrn anymore
    # sp3altrn = Config.sp3altrn
    brdc_path = Config.brdc_path

    if args.engine is not None:
        options['ppp_engine'] = args.engine

    if args.no_config_file is not None:
        # options['ppp_path'] = ''
        # options['ppp_exe']  = 'ppp'
        # options['grdtab']   = 'grdtab'
        # options['otlgrid']  = 'otl.grid'
        options['sp3']      = args.no_config_file[0]

        sp3types  = args.no_config_file[1].split(',')
        # sp3altrn  = ['jpl', 'jp2', 'jpr']
        # brdc_path = args.no_config_file[2]

    # flog to determine if should erase or not folder
    erase = not args.no_erase

    # DDG: dispy workers (even local ones) run with a different cwd than the user's shell, so every
    # file path handed to a worker must be absolute -- a relative path here would silently fail to
    # resolve on the worker side (FileNotFoundError), even though it looks fine in this process.
    rinex_list = []
    for xfile in args.files:
        if os.path.isdir(xfile):
            # add all d.Z files in folder
            rinex_list += glob.glob(os.path.join(xfile, '*d.Z'))
        elif os.path.isfile(xfile):
            # a single file
            rinex_list += [xfile]
        else:
            # a wildcard: expand
            rinex_list += glob.glob(xfile)

    rinex_list = [os.path.abspath(rinex) for rinex in rinex_list]

    if args.copy_results:
        args.copy_results = [os.path.abspath(args.copy_results[0])]

    if args.fix_coordinate is not None and len(args.fix_coordinate) == 1:
        # a single argument means a coordinate file (x/y/z would be 3 separate arguments)
        args.fix_coordinate = [os.path.abspath(args.fix_coordinate[0])]

    # DDG: one task per (rinex file, window). Without -win, each file is a single task (window=None,
    # matching prior behavior exactly). With -win, every window is its own independent task -- when
    # run with --parallel this is what lets each window be scheduled as its own dispy job instead of
    # one job per file looping over all windows in series.
    windows = args.window if args.window is not None else [None]
    tasks = [(rinex, window) for rinex in rinex_list for window in windows]

    args_dict = vars(args)

    Config.run_parallel = args.parallel

    JobServer = pyJobServer.JobServer(Config, check_archive=False, check_executables=False, check_atx=False)

    pbar = tqdm(desc='%-30s' % ' >> Processing RINEX files', total=len(tasks), ncols=160, disable=None)

    # DDG: dispy ships process_rinex_task's own code, but not sibling top-level functions it calls --
    # execute_ppp must be passed explicitly as a dependency (same pattern as ScanArchive.py's
    # scan_rinex/depfuncs), or workers fail with NameError: name 'execute_ppp' is not defined.
    JobServer.create_cluster(process_rinex_task, (execute_ppp,), callback_locate_rinex, pbar,
                             modules=('geode.pyRinex', 'geode.pyPPP', 'geode.pyOTL', 'geode.pyProducts',
                                      'geode.dbConnection', 'geode.metadata.station_info', 'geode.Utils',
                                      'numpy'))

    for rinex, window in tasks:
        JobServer.submit(rinex, window, args_dict, options, sp3types, brdc_path, erase)

    JobServer.wait()
    pbar.close()
    JobServer.close_cluster()


def process_rinex_task(rinex_path, window_hours, args_dict, options, sp3types, brdc_path, erase):
    """
    Dispy worker: fully self-contained processing of one (rinex_file, window) task -- opens the
    RINEX file, splits multiday files, and runs execute_ppp() on each day (which applies the window,
    if any, right after its own purge_comments() call -- see the comment there for why it can't
    happen here instead).
    Returns (error_msg, output_text, rinex_path, window_hours).
    """
    import argparse
    import os
    import traceback

    from geode import pyRinex, pyPPP

    stnm = os.path.basename(rinex_path)[0:4].lower()
    args = argparse.Namespace(**args_dict)

    label = ('%04.1fh' % window_hours) if window_hours is not None else None

    try:
        output = []

        with pyRinex.ReadRinex('???', stnm, rinex_path, allow_multiday=args.no_split,
                               min_time_seconds=args.min_time_seconds) as rinexinfo:
            rnx_days = [rinexinfo]
            if rinexinfo.multiday and not args.no_split:
                output.append('Provided RINEX file is a multiday file!')
                # rinex file is a multiday file, output all the solutions
                rnx_days = rinexinfo.multiday_rnx_list

            for rnx in rnx_days:
                text = execute_ppp(rnx, args, stnm, options, sp3types, (), brdc_path, erase,
                                   not args.no_met, args.decimate, args.fix_coordinate, args.solve_troposphere,
                                   args.copy_results, args.backward_substitution, args.elevation_mask,
                                   args.code_only, args.residuals, args.print_residuals, window_hours)

                if label and text:
                    text = '\n'.join('%s %s' % (label, line) for line in text.splitlines())

                if text:
                    output.append(text)

        return None, '\n'.join(output), rinex_path, window_hours

    except pyRinex.pyRinexException as e:
        return str(e), None, rinex_path, window_hours
    except Exception:
        return traceback.format_exc(), None, rinex_path, window_hours


def callback_locate_rinex(job):
    if job.result is not None:
        error_msg, output_text, rinex_path, window_hours = job.result
        label = ('%04.1fh ' % window_hours) if window_hours is not None else ''
        if error_msg:
            tqdm.write(' -- %s%s: %s' % (label, rinex_path, error_msg))
        elif output_text:
            tqdm.write(output_text)
    elif job.exception:
        tqdm.write(' -- Unhandled job exception:\n%s' % str(job.exception))


def execute_ppp(rinexinfo, args, stnm, options, sp3types, sp3altrn, brdc_path, erase, apply_met=True, decimate=True,
                fix_coordinate=None, solve_troposphere=105, copy_results=None, backward_substitution=False,
                elevation_mask=5, code_only=False, save_residuals=False, print_residuals=False, window_hours=None):
    # DDG: self-contained local imports -- this function is shipped to dispy workers as a
    # dependency of process_rinex_task() (see JobServer.create_cluster's deps= argument), so it
    # can't rely on the caller module's own top-level imports being present in that namespace.
    import re
    import shutil
    from datetime import timedelta
    from geode import pyRinex, pyPPP, pyOTL, pyProducts, dbConnection
    from geode.Utils import file_readlines
    import numpy

    # DDG: returns its console output as a single string (rather than printing directly) so that,
    # when run under JobServer/dispy, a completion callback can surface it after the job returns --
    # a remote worker's own print() would happen on that node and never reach the local console.
    output = []

    # put the correct APR coordinates in the header.
    # stninfo = pyStationInfo.StationInfo(None, allow_empty=True)
    brdc = pyProducts.GetBrdcOrbits(brdc_path, rinexinfo.date, rinexinfo.rootdir)

    try:
        rinexinfo.purge_comments()

        # DDG: window AFTER purge_comments(), not before -- purge_comments()'s write_rinex() call
        # reconstructs the file from the object's own cached header/body, which silently undoes any
        # windowing done earlier (confirmed empirically: file size reverts to the pre-window size).
        if window_hours is not None:
            # window relative to the end of the observed session (e.g. -win 6 = the last 6 hours of
            # data), mutating this task's own private working copy in place (ReadRinex already
            # operates on an isolated copy, never the source file).
            end   = rinexinfo.datetime_lastObs
            start = end - timedelta(hours=window_hours)
            rinexinfo.window_data(start, end)

        # DDG: auto_coord() bootstraps an approximate coordinate into the RINEX header via an extra
        # code-only GPSPACE PPP run -- GPSPACE benefits from/needs that a-priori coordinate, but
        # PRIDE computes its own initial position (SPP) regardless of the RINEX header, so skip this
        # extra PPP run entirely when using PRIDE to save the processing time.
        if str(options.get('ppp_engine', 'gpspace')).strip().lower() != 'pride':
            # inflate the chi**2 limit
            rinexinfo.auto_coord(brdc = brdc, chi_limit = 1000)
            stninfo = {}
            rinexinfo.normalize_header(stninfo)  # empty dict: only applies the coordinate change
    except pyRinex.pyRinexException as e:
        output.append(str(e))

    if args.load_rinex:
        rinexinfo.compress_local_copyto('./')
        output.append('RINEX created in current directory.')
        return '\n'.join(output)

    try:
        otl_coeff = ''

        if args.ocean_loading or args.insert_sql:
            # get a first ppp coordinate
            ppp = pyPPP.RunPPP(rinexinfo, '', options, sp3types, sp3altrn, 0,
                               strict=False, apply_met=False, kinematic=False, clock_interpolation=True,
                               observations=pyPPP.OBSERV_CODE_ONLY if code_only else pyPPP.OBSERV_CODE_PHASE)

            ppp.exec_ppp()

            # use it to get the OTL (when the auto_coord is very bad, PPP doesn't like the resulting OTL).
            otl       = pyOTL.OceanLoading(stnm, options['grdtab'], options['otlgrid'],
                                           ppp.x, ppp.y, ppp.z)
            otl_coeff = otl.calculate_otl_coeff()
            # run again, now with OTL coeff:

        # determine if need to solve for coordinates or not
        x = y = z = 0
        if fix_coordinate is not None:
            if len(fix_coordinate) > 1:
                x = float(fix_coordinate[0])
                y = float(fix_coordinate[1])
                z = float(fix_coordinate[2])
            else:
                # read from file
                cstr = file_readlines(fix_coordinate[0])
                xyz = re.findall(r'%s (-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)' % rinexinfo.StationCode,
                                 ''.join(cstr), re.IGNORECASE)
                if len(xyz):
                    x = float(xyz[0][0])
                    y = float(xyz[0][1])
                    z = float(xyz[0][2])
                else:
                    output.append('WARNING: coordinate fixing invoked but could not find %s in list of coordinates '
                                  '-> unfixing station coordinate in PPP' % rinexinfo.StationCode)
                    fix_coordinate = False
            output.append('%14.4f %14.4f %14.4f' % (x, y, z))

        ppp = pyPPP.RunPPP(rinexinfo, otl_coeff, options, sp3types, sp3altrn, 0,
                           strict=False, apply_met=apply_met, kinematic=False,
                           clock_interpolation=True, erase=erase, decimate=decimate,
                           solve_coordinates=True if not fix_coordinate else False,
                           solve_troposphere=solve_troposphere, back_substitution=backward_substitution,
                           elev_mask=elevation_mask, x=x, y=y, z=z,
                           observations=pyPPP.OBSERV_CODE_ONLY if code_only else pyPPP.OBSERV_CODE_PHASE)

        ppp.exec_ppp()

        if save_residuals and ppp.elevation_residuals is not None:
            fname = '%s_%i_%03i_residuals.txt' % (stnm, rinexinfo.date.year, rinexinfo.date.doy)
            numpy.savetxt(fname,
                          numpy.column_stack((ppp.elevation_bins, ppp.elevation_residuals,
                                              ppp.elevation_residuals_std)),
                          fmt=['%3i', '%14.6f', '%14.6f'],
                          header='elev_deg  mean_vcp_mm  std_vcp_mm')
            output.append('Residuals saved to %s' % fname)

        if print_residuals:
            if ppp.elevation_residuals is not None:
                output.append('elev_deg  mean_vcp_mm    std_vcp_mm')
                for deg, res, std in zip(ppp.elevation_bins, ppp.elevation_residuals,
                                         ppp.elevation_residuals_std):
                    if not numpy.isnan(res):
                        output.append('%8i  %12.6f  %12.6f' % (deg, res, std))
            else:
                output.append('No elevation-binned residuals available for this engine/run.')

        if not ppp.check_phase_center(ppp.proc_parameters):
            output.append('WARNING: phase center parameters not found for declared antenna!')

        if not args.insert_sql:
            # DDG: clock_phase/phase_drift/clock_rms are GPSPACE's NRCAN-style receiver clock
            # polynomial -- not reported by every engine (e.g. PRIDE leaves them None rather than
            # fabricating a value), so only print them when the engine actually populated them.
            # solution_type is always populated ('FLOAT' or 'FIXED') -- GPSPACE never does
            # ambiguity resolution so it's always FLOAT; PRIDE reports FIXED only when it actually
            # resolved integer ambiguities (needs ATT.OBX/OSB.BIA products; falls back to FLOAT
            # otherwise -- see PRIDE.parse_summary()). systems_used (IGS letters, e.g. 'G' or
            # 'GRE') is always populated too -- GPSPACE only ever processes GPS; PRIDE reports
            # whichever systems actually contributed satellites (see PRIDE.parse_res_file()).
            if ppp.clock_phase is not None:
                output.append('%s %-5s %-5s %10.5f %13.4f %13.4f %13.4f %14.9f %14.9f %8.3f %8.3f %8.3f %8.3f %8.3f %8.3f' % (
                    stnm, ppp.solution_type, ppp.systems_used, rinexinfo.date.fyear, ppp.x, ppp.y, ppp.z,
                    ppp.lat[0], ppp.lon[0], ppp.h[0],
                    ppp.clock_phase, ppp.clock_phase_sigma, ppp.phase_drift, ppp.phase_drift_sigma,
                    ppp.clock_rms))
            else:
                output.append('%s %-5s %-5s %10.5f %13.4f %13.4f %13.4f %14.9f %14.9f %8.3f' % (
                    stnm, ppp.solution_type, ppp.systems_used, rinexinfo.date.fyear, ppp.x, ppp.y, ppp.z,
                    ppp.lat[0], ppp.lon[0], ppp.h[0]))
        else:
            from geopy.geocoders import Nominatim
            import country_converter as coco
            # find the country code for the station
            geolocator = Nominatim(user_agent="GeoDE")
            location = geolocator.reverse("%f, %f" % (ppp.lat[0], ppp.lon[0]))

            if location and 'country_code' in location.raw['address'].keys():
                ISO3 = coco.convert(names=location.raw['address']['country_code'], to='ISO3')
            else:
                ISO3 = None

            if args.insert_sql == '???':
                output.append('INSERT INTO stations ("NetworkCode", "StationCode", "auto_x", "auto_y", "auto_z", ' \
                              '"Harpos_coeff_otl", lat, lon, height, country_code) VALUES ' \
                              '(\'???\', \'%s\', %.4f, %.4f, %.4f, \'%s\', %.8f, %.8f, %.3f, \'%s\')' \
                              % (stnm, ppp.x, ppp.y, ppp.z, otl_coeff, ppp.lat[0], ppp.lon[0], ppp.h[0], ISO3))
            else:
                # try to do the insert
                cnn = dbConnection.Cnn('gnss_data.cfg')
                NetworkCode = args.insert_sql.lower()
                try:
                    cnn.get('networks', {'NetworkCode': NetworkCode})
                except dbConnection.DatabaseError:
                    # net does not exist, add it
                    cnn.insert('networks', NetworkCode=NetworkCode)
                # now insert the station
                try:
                    cnn.insert('stations',
                               NetworkCode=NetworkCode,
                               StationCode=stnm,
                               auto_x=ppp.x,
                               auto_y=ppp.y,
                               auto_z=ppp.z,
                               Harpos_coeff_otl=otl_coeff,
                               lat=ppp.lat[0],
                               lon=ppp.lon[0],
                               height=ppp.h[0],
                               country_code=ISO3)

                    output.append('Station %s.%s added to the database (country code: %s)' %
                                  (NetworkCode, stnm, ISO3))
                except dbConnection.dbErrInsert:
                    output.append('Station %s.%s (country code: %s) already exists in database' %
                                  (NetworkCode, stnm, ISO3))

        if args.find:
            cnn = dbConnection.Cnn('gnss_data.cfg')

            Result, match, closest_stn = ppp.verify_spatial_coherence(cnn, stnm)

            if Result:
                output.append('Found matching station: %s.%s' % (match[0]['NetworkCode'],
                                                                  match[0]['StationCode']))

            elif len(match) == 1:
                output.append('%s matches the coordinate of %s.%s (distance = %8.3f m) but the filename '
                              'indicates it is %s' \
                              % (rinexinfo.rinex,
                                 match[0]['NetworkCode'],
                                 match[0]['StationCode'],
                                 float(match[0]['distance']),
                                 stnm))

            elif len(match) > 0:
                output.append('Solution for RINEX (%s %s) did not match a unique station location (and station '
                              'code) within 10 km. Possible cantidate(s): %s' \
                              % (rinexinfo.rinex,
                                 rinexinfo.date.yyyyddd(),
                                 ', '.join(['%s.%s: %.3f m' %
                                            (m['NetworkCode'],
                                             m['StationCode'],
                                             m['distance']) for m in match])))

            elif len(match) == 0 and len(closest_stn) > 0:
                output.append('No matches found. Closest station: %s.%s. (distance = %8.3f m)' \
                              % (closest_stn[0]['NetworkCode'],
                                 closest_stn[0]['StationCode'],
                                 closest_stn[0]['distance']))

        if copy_results:
            copy_results = copy_results[0]
            try:
                fpath = os.path.join(copy_results, rinexinfo.StationCode)
                if not os.path.exists(fpath):
                    os.makedirs(fpath)
                shutil.copyfile(ppp.path_res_file, os.path.join(fpath, os.path.basename(ppp.path_res_file)))
                shutil.copyfile(ppp.path_pos_file, os.path.join(fpath, os.path.basename(ppp.path_pos_file)))
                shutil.copyfile(ppp.path_ses_file, os.path.join(fpath, os.path.basename(ppp.path_ses_file)))
                shutil.copyfile(ppp.path_sum_file, os.path.join(fpath, os.path.basename(ppp.path_sum_file)))
                shutil.copyfile(os.path.join(ppp.rootdir, 'commands.cmd'), os.path.join(fpath, os.path.basename(ppp.path_sum_file) + '.cmd'))
            except Exception as e:
                output.append('WARNING: There was a problem copying results to %s: %s' % (copy_results, str(e)))

    except pyPPP.pyRunPPPException as e:
        output.append('Exception in PPP: ' + str(e))

    except pyRinex.pyRinexException as e:
        output.append('Exception in pyRinex: ' + str(e))

    return '\n'.join(output)


if __name__ == '__main__':
    main()
