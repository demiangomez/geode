#!/usr/bin/env python
"""
Project: Parallel.Archive
Date: 02/16/2017
Author: Demian D. Gomez

Main routines to load the RINEX files to the database, load station
information, run PPP on the archive files and obtain the OTL coefficients

"""

import traceback
import datetime
import os
import sys
import argparse
import platform
import json
import shutil
import glob
import uuid
from decimal import Decimal
import zipfile

# deps
from tqdm import tqdm
import numpy
import scandir

# app
from geode import pyArchiveStruct
from geode import dbConnection
from geode import pyDate
from geode import pyRinex
from geode import pyRinexName
from geode import pyOTL
from geode.metadata.station_info import StationInfo, StationInfoRecord, StationInfoException
from geode import pyProducts
from geode import pyPPP
from geode import pyOptions
from geode import Utils
from geode import pyJobServer
from geode import pyEvents
from geode.Utils import (add_version_argument,
                          process_date,
                          ecef2lla,
                          file_append,
                          file_open,
                          file_read_all,
                          stationID,
                          station_list_help,
                          crc32)


error_message = False

ERRORS_LOG = 'errors_ScanArchive.log'


class Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        elif isinstance(o, datetime.datetime):
            return datetime.datetime.strftime(o, '%Y-%m-%d %H:%M:%S')
        else:
            return super(Encoder, self).default(o)


class callback_class():
    def __init__(self, pbar):
        self.errors = None
        self.pbar   = pbar

    def callbackfunc(self, args):
        msg = args
        self.errors = msg
        self.pbar.update(1)


def callback_handle(job):
    global error_message

    if job.result is not None or job.exception:
        error_message = True
        msg           = job.result if job.result else job.exception

        tqdm.write(' -- There were unhandled errors during this batch. '
                   'Please check %s for details' % ERRORS_LOG)
        file_append(ERRORS_LOG,
                    'ON ' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') + 
                    ' an unhandled error occurred:\n' +
                    msg + '\n' + 
                    'END OF ERROR =================== \n\n')


def verify_rinex_date_multiday(cnn, date, rinexinfo, Config):
    # function to verify if rinex is multiday or if the file is from the date it was stored in the archive
    # returns true if parent process can continue with insert
    # returns false if file had to be moved from the archive (date != rinex.date or multiday file)

    # check if rinex is a multiday file (rinex with more than one day of observations)
    if rinexinfo.multiday:

        # move all the files to the repository, delete the crinex from the archive, log the event
        rnxlist = []
        for rnx in rinexinfo.multiday_rnx_list:
            rnxlist.append(rnx.rinex)
            # some other file, move it to the repository
            retry_folder = os.path.join(Config.repository_data_in_retry,
                                        'multidays_found/' + rnx.date.yyyy() + '/' + rnx.date.ddd())
            rnx.compress_local_copyto(retry_folder)

        # if the file corresponding to this session is found, assign its object to rinexinfo
        event = pyEvents.Event(Description='%s was a multi-day rinex file. The following rinex files where generated and moved to the repository/data_in_retry: %s.' \
                               % (rinexinfo.origin_file,
                                  ','.join(rnxlist)),
                               NetworkCode = rinexinfo.NetworkCode,
                               EventType   = 'warn',
                               StationCode = rinexinfo.StationCode,
                               Year        = int(rinexinfo.date.year),
                               DOY         = int(rinexinfo.date.doy))

        cnn.insert_event(event)

        # remove crinex from archive
        os.remove(rinexinfo.origin_file)

        return False

    # compare the date of the rinex with the date in the archive
    elif not date == rinexinfo.date:
        # move the file out of the archive because it's in the wrong spot (wrong folder, wrong name, etc)
        # let pyArchiveService fix the issue
        retry_folder = os.path.join(Config.repository_data_in_retry,
                                    'wrong_date_found/' + date.yyyy() + '/' + date.ddd())
        # move the crinex out of the archive
        rinexinfo.move_origin_file(retry_folder)

        event = pyEvents.Event(Description = ('The date in the archive for ' + rinexinfo.rinex + \
                                              ' (' + date.yyyyddd() + \
                                              ') does not agree with the mean session date (' + \
                                              rinexinfo.date.yyyyddd() + \
                                              '). The file was moved to the repository/data_in_retry.'),
                               NetworkCode = rinexinfo.NetworkCode,
                               EventType   = 'warn',
                               StationCode = rinexinfo.StationCode,
                               Year        = int(rinexinfo.date.year),
                               DOY         = int(rinexinfo.date.doy))

        cnn.insert_event(event)

        return False

    return True


def try_insert(NetworkCode, StationCode, year, doy, rinex):

    try:
        # try to open a connection to the database
        cnn    = dbConnection.Cnn("gnss_data.cfg")
        Config = pyOptions.ReadOptions("gnss_data.cfg")

        # get the rejection directory ready
        data_reject = os.path.join(Config.repository_data_reject, 'bad_rinex/%i/%03i' % (year, doy))

        # get the rinex file name
        rnx_name = pyRinexName.RinexNameFormat(rinex)
    except:
        return traceback.format_exc() + ' processing rinex: %s (%s.%s %s %s) using node %s' \
               % (rinex, NetworkCode, StationCode, str(year), str(doy), platform.node())


    try:
        filename = rnx_name.to_rinex_format(pyRinexName.TYPE_RINEX, no_path=True)

        # build the archive level sql string
        # the file has not to exist in the RINEX table (check done using filename)
        rs = cnn.query(
            'SELECT * FROM rinex WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' AND "Filename" = \'%s\''
            % (NetworkCode, StationCode, filename))

        if rs.ntuples() == 0:
            # no record found, possible new rinex file for this day
            with pyRinex.ReadRinex(NetworkCode, StationCode, rinex) as rinexinfo:

                date = pyDate.Date(year=year, doy=doy)

                # verify that the rinex is from this date and that is not a multiday file
                if verify_rinex_date_multiday(cnn, date, rinexinfo, Config):
                    try:
                        # create the insert statement
                        cnn.insert('rinex', **rinexinfo.record)

                        event = pyEvents.Event(
                            Description = 'Archived crinex file %s added to the database.' % (rinex),
                            EventType   = 'info',
                            StationCode = StationCode,
                            NetworkCode = NetworkCode,
                            Year        = date.year,
                            DOY         = date.doy)
                        cnn.insert_event(event)

                    except dbConnection.dbErrInsert:
                        # insert duplicate values: a rinex file with different name but same interval and completion %
                        # discard file
                        cnn.begin_transac()

                        event = pyEvents.Event(
                            Description = 'Crinex file %s was removed from the archive (and not added to db) because '
                                         'it matched the interval and completion of an already existing file.' % rinex,
                            EventType   = 'info',
                            StationCode = StationCode,
                            NetworkCode = NetworkCode,
                            Year        = date.year,
                            DOY         = date.doy)

                        cnn.insert_event(event)

                        rinexinfo.move_origin_file(os.path.join(Config.repository_data_reject,
                                                                'duplicate_insert/%i/%03i' % (year, doy)))

                        cnn.commit_transac()

    except (pyRinex.pyRinexExceptionBadFile,
            pyRinex.pyRinexExceptionSingleEpoch) as e:

        try:
            filename = Utils.move(rinex, os.path.join(data_reject, os.path.basename(rinex)))
        except OSError:
            # permission denied: could not move file out of the archive->return error in an orderly fashion
            return traceback.format_exc() + ' processing rinex: %s (%s.%s %s %s) using node %s' \
                   % (rinex, NetworkCode, StationCode, str(year), str(doy), platform.node())

        e.event['Description'] = 'During %s, file moved to %s: %s' \
                                 % (os.path.basename(rinex), filename, e.event['Description'])
        e.event['StationCode'] = StationCode
        e.event['NetworkCode'] = NetworkCode
        e.event['Year']        = year
        e.event['DOY']         = doy

        cnn.insert_event(e.event)

    except pyRinex.pyRinexException as e:

        if cnn.active_transaction:
            cnn.rollback_transac()

        e.event['Description'] = e.event['Description'] + ' during ' + rinex
        e.event['StationCode'] = StationCode
        e.event['NetworkCode'] = NetworkCode
        e.event['Year']        = year
        e.event['DOY']         = doy

        cnn.insert_event(e.event)

    except:
        if cnn.active_transaction:
            cnn.rollback_transac()

        return traceback.format_exc() + ' processing rinex: %s (%s.%s %s %s) using node %s' \
                                        % (rinex, NetworkCode, StationCode, str(year), str(doy), platform.node())


def obtain_otl(NetworkCode, StationCode):

    errors = ''
    x = []
    y = []
    z = []

    stn_id = "%s.%s" % (NetworkCode, StationCode)

    try:
        cnn    = dbConnection.Cnn("gnss_data.cfg")
        Config = pyOptions.ReadOptions("gnss_data.cfg")

        pyArchive = pyArchiveStruct.RinexStruct(cnn)

        # assumes that the files in the db are correct. We take 10 records from the time span (evenly spaced)
        count = cnn.query('SELECT count(*) as cc FROM rinex_proc as r WHERE "NetworkCode" = \'%s\' AND '
                          '"StationCode" = \'%s\'' % (NetworkCode, StationCode)).dictresult()

        if count[0]['cc'] >= 10:
            stn = cnn.query('SELECT * FROM (SELECT *, row_number() OVER '
                            '(PARTITION BY "NetworkCode", "StationCode") as rnum, '
                            'count(*) OVER (PARTITION BY "NetworkCode", "StationCode") as cc FROM rinex_proc) as rinex '
                            'WHERE rinex."NetworkCode" = \'%s\' AND rinex."StationCode" = \'%s\' '
                            'AND rinex.rnum %% (rinex.cc/10) = 0 ORDER BY rinex."ObservationSTime"'
                            % (NetworkCode, StationCode))

        elif count[0]['cc'] < 10:
            stn = cnn.query('SELECT * FROM (SELECT row_number() OVER() as rnum, r.* FROM rinex_proc as r '
                            'WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                            'ORDER BY "ObservationSTime") AS rr ' % (NetworkCode, StationCode))

        else:
            return 'Station %s.%s had no rinex files in the archive. Please check the database for problems.' \
                   % (NetworkCode, StationCode)

        tblrinex = stn.dictresult()

        for dbRinex in tblrinex:
            # obtain the path to the crinex
            file = pyArchive.build_rinex_path(NetworkCode, StationCode,
                                              dbRinex['ObservationYear'],
                                              dbRinex['ObservationDOY'])

            with pyRinex.ReadRinex(dbRinex['NetworkCode'], 
                                   dbRinex['StationCode'],
                                   os.path.join(Config.archive_path, file)) as Rinex:

                # read the crinex
                try:
                    # run ppp without otl and met and in non-strict mode
                    # DDG: now there is no sp3altrn anymore
                    with pyPPP.RunPPP(Rinex, '', Config.options, Config.sp3types, (),
                                      Rinex.antOffset, strict=False, apply_met=False,
                                      clock_interpolation=True) as ppp:

                        ppp.exec_ppp()

                        x.append(ppp.x)
                        y.append(ppp.y)
                        z.append(ppp.z)

                        errors += 'PPP -> %s: %.3f %.3f %.3f\n' % (stn_id, ppp.x, ppp.y, ppp.z)

                except (pyProducts.pySp3Exception,
                        pyProducts.pyClkException,
                        pyPPP.pyRunPPPException):

                    # try autonomous solution
                    try:
                        brdc = pyProducts.GetBrdcOrbits(Config.brdc_path, Rinex.date, Rinex.rootdir)

                        Rinex.auto_coord(brdc, chi_limit=1000)

                        x.append(Rinex.x)
                        y.append(Rinex.y)
                        z.append(Rinex.z)

                    except Exception as e:
                        errors += str(e) + '\n'
                        continue

                except (IOError, 
                        pyRinex.pyRinexException, 
                        pyRinex.pyRinexExceptionBadFile) as e:
                    # problem loading this file, try another one
                    errors += str(e) + '\n'
                    continue

                except:

                    return traceback.format_exc() + \
                           ' processing: %s using node %s' % (stn_id, platform.node())

        # average the x y z values
        if len(x) > 0:

            if len(x) > 1:
                x = numpy.array(x)
                y = numpy.array(y)
                z = numpy.array(z)

                x = numpy.mean(x[abs(x - numpy.mean(x)) < 2 * numpy.std(x)])
                y = numpy.mean(y[abs(y - numpy.mean(y)) < 2 * numpy.std(y)])
                z = numpy.mean(z[abs(z - numpy.mean(z)) < 2 * numpy.std(z)])
            else:
                x = x[0]
                y = y[0]
                z = z[0]

            lat, lon, h = ecef2lla([x,y,z])

            # calculate the otl parameters if the auto_coord returned a valid position
            errors = errors + 'Mean -> %s: %.3f %.3f %.3f\n' % (stn_id, x, y, z)

            otl   = pyOTL.OceanLoading(StationCode, 
                                       Config.options['grdtab'], 
                                       Config.options['otlgrid'])
            coeff = otl.calculate_otl_coeff(x=x, y=y, z=z)

            # update record in the database
            cnn.query('UPDATE stations SET "auto_x" = %.3f, "auto_y" = %.3f, "auto_z" = %.3f, '
                      '"lat" = %.8f, "lon" = %.8f, "height" = %.3f, '
                      '"Harpos_coeff_otl" = \'%s\' '
                      'WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                      % (x, y, z, 
                         lat[0], lon[0], h[0], 
                         coeff, 
                         NetworkCode, StationCode))

        else:
            outmsg = 'Could not obtain a coordinate/otl coefficients for ' + NetworkCode + ' ' + StationCode + \
                     ' after 20 tries. Maybe there where few valid RINEX files or could not find an ephemeris file. ' \
                     'Debug info and errors follow:\n' + errors

            return outmsg

    except pyOTL.pyOTLException as e:

        return "Error while calculating OTL for %s: %s\n" % (stn_id, str(e)) + \
               'Debug info and errors follow: \n' + errors

    except:
        # print 'problem!' + traceback.format_exc()
        outmsg = traceback.format_exc() + ' processing otl: %s using node %s\n' \
                                          % (stn_id, platform.node()) \
                                          + 'Debug info and errors follow: \n' + errors

        return outmsg


def insert_stninfo(NetworkCode, StationCode, stninfofile):

    errors = []

    try:
        cnn = dbConnection.Cnn("gnss_data.cfg")
    except:
        return traceback.format_exc() + ' insert_stninfo: ' + NetworkCode + ' ' + StationCode + \
               ' using node ' + platform.node()

    try:
        stnInfo = station_info.StationInfo(cnn, NetworkCode, StationCode, allow_empty=True)
        stninfo = stnInfo.parse_station_info(stninfofile)

    except station_info.StationInfoException:
        return traceback.format_exc() + ' insert_stninfo: ' + NetworkCode + ' ' + StationCode + \
               ' using node ' + platform.node()

    # DDG: 18-Feb-2019 used to have some code here to force the insertion of receivers and antennas
    # this is not done anymore, and receivers and antennas should exists in the corresponding tables before inserting
    # otherwise, a python exception will be raised.

    # ready to insert stuff to station info table
    for stn in stninfo:
        if stn.get('StationCode').lower() == StationCode:
            try:
                # call station info again in case there was a change from other records
                stnobj = station_info.StationInfo(cnn, NetworkCode, StationCode, allow_empty=True)
                stnobj.insert_station_info(stn)
            except station_info.StationInfoException as e:
                errors.append(str(e))

            except:
                errors.append(traceback.format_exc() + ' insert_stninfo: ' + NetworkCode + ' ' + StationCode +
                              ' using node ' + platform.node())
                continue

    if errors:
        return '\n\n'.join(errors)
        

def remove_from_archive(cnn, record, Rinex, Config):

    # do not make very complex things here, just move it out from the archive
    retry_folder = os.path.join(Config.repository_data_in_retry,
                                'inconsistent_ppp_solution/' + Rinex.date.yyyy() + '/' + Rinex.date.ddd())

    pyArchive = pyArchiveStruct.RinexStruct(cnn)
    pyArchive.remove_rinex(record, retry_folder)

    event = pyEvents.Event(
        Description = 'After running PPP it was found that the rinex file %s does not belong to this station. '
                     'This file was removed from the rinex table and moved to the repository/data_in_retry to add it '
                     'to the corresponding station.' % Rinex.origin_file,
        NetworkCode = record['NetworkCode'],
        StationCode = record['StationCode'],
        EventType   = 'warn',
        Year        = int(Rinex.date.year),
        DOY         = int(Rinex.date.doy))

    cnn.insert_event(event)


def execute_ppp(record, rinex_path, h_tolerance):

    NetworkCode = record['NetworkCode']
    StationCode = record['StationCode']
    year        = record['ObservationYear']
    doy         = record['ObservationDOY']

    try:
        # try to open a connection to the database
        cnn = dbConnection.Cnn("gnss_data.cfg")

        Config = pyOptions.ReadOptions("gnss_data.cfg")

    except:
        return traceback.format_exc() + ' processing rinex: %s %s %s using node %s' \
                   % (stationID(record), str(year), str(doy), platform.node())

    # create a temp folder in production to put the orbit in
    # we need to check the RF of the orbit to see if we have this solution in the DB
    try:

        # check to see if record exists for this file in ppp_soln
        # DDG: now read the frame from the config file
        frame, _ = Utils.determine_frame(Config.options['frames'], pyDate.Date(year=year, doy=doy))

        ppp_soln = cnn.query('SELECT * FROM ppp_soln WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' AND '
                             '"Year" = %s AND "DOY" = %s AND "ReferenceFrame" = \'%s\''
                             % (NetworkCode, StationCode, year, doy, frame))

        if ppp_soln.ntuples() == 0:

            # load the stations record to get the OTL params
            rs_stn = cnn.query('SELECT * FROM stations WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                               % (NetworkCode, StationCode))
            stn = rs_stn.dictresult()

            # RINEX FILE TO BE PROCESSED
            with pyRinex.ReadRinex(NetworkCode, StationCode, rinex_path) as Rinex:

                if not verify_rinex_date_multiday(cnn, Rinex.date, Rinex, Config):
                    # the file is a multiday file. These files are not supposed to be in the archive, but, due to a bug
                    # in ScanArchive (now fixed - 2017-10-26) some multiday files are still in the rinex table
                    # the file is moved out of the archive (into the retry folder and the rinex record is deleted
                    event = pyEvents.Event(EventType   = 'warn',
                                           Description = 'RINEX record in database belonged to a multiday file. '
                                                         'The record has been removed from the database. '
                                                         'See previous associated event.',
                                           StationCode = StationCode,
                                           NetworkCode = NetworkCode,
                                           Year        = int(Rinex.date.year),
                                           DOY         = int(Rinex.date.doy))
                    cnn.insert_event(event)

                    cnn.begin_transac()
                    where_obs = ('"NetworkCode" = \'%s\' AND "StationCode" = \'%s\' ' \
                                 'AND "Year" = %i AND "DOY" = %i' % (record['NetworkCode'],
                                                                     record['StationCode'], 
                                                                     record['ObservationYear'],
                                                                     record['ObservationDOY']))
                    cnn.query('DELETE FROM gamit_soln WHERE %s' % where_obs)
                    cnn.query('DELETE FROM ppp_soln WHERE %s'   % where_obs)
                    cnn.query('DELETE FROM rinex WHERE %s AND "Filename" = \'%s\'' % (where_obs, record['Filename']))
                    cnn.commit_transac()
                    cnn.close()
                    return

                stninfo = station_info.StationInfo(cnn, NetworkCode, StationCode, Rinex.date, h_tolerance=h_tolerance)

                Rinex.normalize_header(stninfo, 
                                       x = stn[0]['auto_x'], 
                                       y = stn[0]['auto_y'], 
                                       z = stn[0]['auto_z'])

                # DDG: no more sp3altrn
                with pyPPP.RunPPP(Rinex,
                                  stn[0]['Harpos_coeff_otl'],
                                  Config.options,
                                  Config.sp3types, (),
                                  stninfo.to_dharp(stninfo.current_record).AntennaHeight,
                                  hash = stninfo.current_record.hash) as ppp:
                    ppp.exec_ppp()

                    # verify that the solution is from the station it claims to be
                    Result, match, _ = ppp.verify_spatial_coherence(cnn, StationCode)

                    if Result and (match[0]['NetworkCode'] == NetworkCode and
                                   match[0]['StationCode'] == StationCode):
                        # the match agrees with the station-day that we THINK we are processing
                        # this check should not be necessary if the rinex went through Archive Service, since we
                        # already match rinex vs station
                        # but it's still here to prevent that a rinex imported by ScanArchive (which assumes the
                        # rinex files belong to the network/station of the folder) doesn't get into the PPP table
                        # if it's not of the station it claims to be.

                        # insert record in DB
                        cnn.insert('ppp_soln', **ppp.record)

                        if ppp.elevation_residuals is not None:
                            try:
                                cnn.insert('ppp_antenna_residuals',
                                           network_code    = NetworkCode,
                                           station_code    = StationCode,
                                           reference_frame = ppp.record['ReferenceFrame'],
                                           year            = int(year),
                                           doy             = int(doy),
                                           antenna_code    = stninfo.current_record.AntennaCode.strip(),
                                           radome_code     = stninfo.current_record.RadomeCode.strip(),
                                           residuals       = ppp.elevation_residuals.tolist())
                            except Exception as e:
                                event = pyEvents.Event(
                                    Description = 'Failed to insert PPP antenna residuals: %s' % str(e),
                                    NetworkCode = NetworkCode,
                                    StationCode = StationCode,
                                    EventType   = 'warn',
                                    Year        = int(year),
                                    DOY         = int(doy))
                                cnn.insert_event(event)

                        # DDG: Eric's request to generate a date of PPP solution
                        event = pyEvents.Event(Description = 'A new PPP solution was created for frame ' + ppp.frame,
                                               NetworkCode = NetworkCode,
                                               StationCode = StationCode,
                                               Year        = int(year),
                                               DOY         = int(doy))
                        cnn.insert_event(event)
                    else:
                        remove_from_archive(cnn, record, Rinex, Config)

    except (pyRinex.pyRinexException,
            pyRinex.pyRinexExceptionBadFile,
            pyRinex.pyRinexExceptionSingleEpoch,
            pyPPP.pyRunPPPException,
            station_info.StationInfoException) as e:

        e.event['StationCode'] = StationCode
        e.event['NetworkCode'] = NetworkCode
        e.event['Year']        = int(year)
        e.event['DOY']         = int(doy)

        cnn.insert_event(e.event)
        cnn.close()

    except:
        cnn.close()
        return (traceback.format_exc() + ' processing rinex: %s.%s %s %s using node %s' %
                (NetworkCode, StationCode, str(year), str(doy), platform.node()))


def post_scan_rinex_job(cnn, Archive, rinex_file, rinexpath, master_list, JobServer, ignore):

    valid, result = Archive.parse_archive_keys(rinex_file, key_filter=('network', 'station', 'year', 'doy'))

    if valid:

        NetworkCode = result['network']
        StationCode = result['station']
        year        = result['year']
        doy         = result['doy']

        # check the master_list
        if NetworkCode + '.' + StationCode in master_list or ignore:
            # check existence of network in the db
            rs = cnn.query('SELECT * FROM networks WHERE "NetworkCode" = \'%s\'' % NetworkCode)
            if rs.ntuples() == 0:
                cnn.insert('networks', NetworkCode=NetworkCode, NetworkName='UNK')

            # check existence of station in the db
            rs = cnn.query('SELECT * FROM stations WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                           % (NetworkCode, StationCode))
            if rs.ntuples() == 0:
                # run grdtab to get the OTL parameters in HARPOS format and insert then in the db
                # use the current rinex to get an approximate coordinate
                cnn.insert('stations', NetworkCode=NetworkCode, StationCode=StationCode)

            JobServer.submit(NetworkCode, StationCode, year, doy, rinexpath)


def scan_rinex(cnn, JobServer, pyArchive, archive_path, master_list, ignore):

    master_list = [stationID(item) for item in master_list]

    print(" >> Analyzing the archive's structure...")
    pbar = tqdm(ncols=80, unit='crz', disable=None)

    depfuncs = (verify_rinex_date_multiday,)
    modules  = ('geode.dbConnection', 'geode.pyDate', 'geode.pyRinex', 'shutil', 'platform', 'datetime',
                'traceback', 'geode.pyOptions', 'geode.pyEvents', 'geode.Utils', 'os', 'geode.pyRinexName')

    JobServer.create_cluster(try_insert, dependencies=depfuncs, modules=modules, callback=callback_handle)

    ignore = (ignore[0] == 1)

    for path, _, files in scandir.walk(archive_path):
        for sfile in files:
            # DDG issue #15: match the name of the file to a valid rinex filename
            try:
                _ = pyRinexName.RinexNameFormat(sfile)
                # only examine valid rinex compressed files
                rnx      = os.path.join(path, sfile).rsplit(archive_path + '/')[1]
                path2rnx = os.path.join(path, sfile)

                pbar.set_postfix(crinex=rnx)
                pbar.update()

                post_scan_rinex_job(cnn, pyArchive, rnx, path2rnx, master_list, JobServer, ignore)
            except pyRinexName.RinexNameException:
                pass

        JobServer.wait()

    # handle any output messages during this batch
    if error_message:
        tqdm.write(' -- There were unhandled errors. Please check %s for details' % ERRORS_LOG)

    pbar.close()


def process_otl(cnn, JobServer, master_list):

    print("")
    print(" >> Calculating coordinates and OTL for new stations...")

    master_list = [stationID(item) for item in master_list]

    records = cnn.query('SELECT "NetworkCode", "StationCode" FROM stations '
                        'WHERE auto_x is null OR auto_y is null OR auto_z is null OR "Harpos_coeff_otl" is null '
                        'AND "NetworkCode" not like \'?%\' '
                        'AND "NetworkCode" || \'.\' || "StationCode" IN (\'' + '\',\''.join(master_list) + '\')').dictresult()

    pbar = tqdm(total=len(records), ncols=80, disable=None)

    depfuncs = (ecef2lla,)
    modules  = ('geode.dbConnection', 'geode.pyRinex', 'geode.pyArchiveStruct', 'geode.pyOTL', 'geode.pyPPP',
                'numpy', 'platform', 'geode.pyProducts', 'traceback', 'geode.pyOptions')

    JobServer.create_cluster(obtain_otl, depfuncs, callback_handle, progress_bar=pbar, modules=modules)

    for record in records:
        JobServer.submit(record['NetworkCode'],
                         record['StationCode'])

    JobServer.wait()

    pbar.close()


def scan_station_info(JobServer, pyArchive, archive_path, master_list):

    print(" >> Searching for station info files in the archive...")

    stninfo, path2stninfo = pyArchive.scan_archive_struct_stninfo(archive_path)

    print("   >> Processing Station Info files...")

    master_list = set(stationID(item) for item in master_list)

    pbar = tqdm(total=len(stninfo), ncols=80, disable=None)

    modules = ('geode.dbConnection', 'geode.metadata.station_info', 'sys', 'datetime', 'geode.pyDate',
               'platform', 'traceback')

    JobServer.create_cluster(insert_stninfo, callback=callback_handle, progress_bar=pbar, modules=modules)

    for stninfofile, stninfopath in zip(stninfo, path2stninfo):

        valid, result = pyArchive.parse_archive_keys(stninfofile, key_filter=('network', 'station'))

        if valid:
            NetworkCode = result['network']
            StationCode = result['station']

            if (NetworkCode + '.' + StationCode) in master_list:
                # we were able to get the network and station code, add it to the database
                JobServer.submit(NetworkCode, StationCode, stninfopath)

    JobServer.wait()

    pbar.close()


def scan_station_info_man(cnn, pyArchive, stn_info_path, stations, stn_info_net, stdin=None):
    # input "stations" has a list in net.stnm format

    print(" >> Manual scan of station info files in " + stn_info_path)

    NetworkCode = stn_info_net

    def process_stations(stninfo_file_list, desc):
        stn_info_obj = StationInfo(cnn)
        stn_list     = stn_info_obj.parse_station_info(stninfo_file_list)
        stn_codes    = set(stn['StationCode'].lower() for stn in stn_list)

        for Station in tqdm(stations, total=len(stations), disable=None):
            # input "stations" has a list in net.stnm format
            if Station['StationCode'] in stn_codes:
                tqdm.write("   >> Processing %s using network code %s" % (Station['StationCode'], NetworkCode))
                out = insert_stninfo(NetworkCode, Station['StationCode'], stninfo_file_list)

                if out:
                    tqdm.write(out)
            else:
                tqdm.write('   >> Station %s.%s was not found in the station info file %s' %
                           (Station['NetworkCode'], Station['StationCode'], desc))


    if stdin:
        process_stations(stdin, 'standard input')

    else:
        if os.path.isfile(stn_info_path):
            path2stninfo = [stn_info_path]
        else:
            _, path2stninfo = pyArchive.scan_archive_struct_stninfo(stn_info_path)

        print("   >> Found %i station information files." % len(path2stninfo))

        for stninfopath in path2stninfo:
            process_stations(stninfopath, stninfopath)


def hash_check(cnn, master_list, sdate, edate, rehash=False, h_tolerant=0):

    print(" >> Running hash check to the PPP solutions...")

    master_list = [item['NetworkCode'] + '.' + item['StationCode'] for item in master_list]

    ppp_soln = cnn.query('SELECT * FROM ppp_soln '
                         'WHERE "NetworkCode" || \'.\' || "StationCode" IN (\'' + '\',\''.join(master_list) + '\') '
                         'AND "Year" || \' \' || to_char("DOY", \'fm000\') '
                         'BETWEEN \'' + sdate.yyyyddd() + '\' AND \'' + (edate+1).yyyyddd() + '\' '
                         'ORDER BY "Year", "DOY", "NetworkCode", "StationCode"')

    tbl = ppp_soln.dictresult()

    archive = pyArchiveStruct.RinexStruct(cnn)

    # check the hash values if specified
    if not rehash:
        print(' -- Checking hash values.')
    else:
        print(' -- Rehashing all records. This may take a while...')

    for soln in tqdm(tbl, ncols=80, disable=None):
        # load station info object
        try:

            # lookup for the rinex_proc record
            rinex = archive.get_rinex_record(NetworkCode     = soln['NetworkCode'], 
                                             StationCode     = soln['StationCode'],
                                             ObservationYear = soln['Year'], 
                                             ObservationDOY  = soln['DOY'])

            obs_id = "%s.%s %i %03i" % (soln['NetworkCode'], 
                                        soln['StationCode'], 
                                        soln['Year'], 
                                        soln['DOY'])
            if not rinex:
                # if no records, print warning
                tqdm.write(" -- Could not find RINEX for %s. PPP solution will be deleted." % obs_id)
                cnn.delete('ppp_soln', **soln)
            else:
                # select the first record
                rinex = rinex[0]

                dd = rinex['ObservationSTime'] + (rinex['ObservationETime'] - rinex['ObservationSTime']) // 2

                stninfo = StationInfo(cnn, soln['NetworkCode'], soln['StationCode'],
                                                    pyDate.Date(datetime = dd),
                                                    h_tolerance = h_tolerant)

                # DDG: now also add the value of the CRC32 of orbit
                if stninfo.current_record.hash + crc32(soln['orbit']) != soln['hash']:
                    if not rehash:
                        tqdm.write(" -- Hash value for %s does not match with Station Information hash. "
                                   "PPP coordinate will be recalculated." % obs_id)
                        cnn.delete('ppp_soln', **soln)
                    else:
                        tqdm.write(" -- %s has been rehashed." % obs_id)
                        cnn.update('ppp_soln', {'hash': stninfo.current_record.hash + crc32(soln['orbit'])}, **soln)

        except StationInfoException as e:
            tqdm.write(str(e))

    if not rehash:
        print(' -- Done checking hash values.')
    else:
        print(' -- Done rehashing PPP records.')


def process_ppp(cnn, Config, pyArchive, archive_path, JobServer, master_list, sdate, edate, h_tolerance):

    print(" >> Running PPP on the RINEX files in the archive...")

    master_list = [item['NetworkCode'] + '.' + item['StationCode'] for item in master_list]

    # for each rinex in the db, run PPP and get a coordinate
    rs_rnx = cnn.query('SELECT rinex.* FROM rinex_proc as rinex '
                       'LEFT JOIN ppp_soln ON '
                       'rinex."NetworkCode" = ppp_soln."NetworkCode" AND '
                       'rinex."StationCode" = ppp_soln."StationCode" AND '
                       'rinex."ObservationYear" = ppp_soln."Year" AND '
                       'rinex."ObservationDOY" = ppp_soln."DOY" '
                       'WHERE ppp_soln."NetworkCode" is null AND '
                       'rinex."NetworkCode" || \'.\' || rinex."StationCode" IN (\'' + '\',\''.join(master_list) + '\') '
                       'AND rinex."ObservationSTime" BETWEEN \''
                       + sdate.yyyymmdd() + '\' AND \'' + (edate+1).yyyymmdd() + '\' '
                       'ORDER BY "ObservationSTime"')

    tblrinex = rs_rnx.dictresult()

    pbar = tqdm(total=len(tblrinex), ncols=80, disable=None)

    modules = ('geode.dbConnection', 'geode.pyRinex', 'geode.pyPPP', 'geode.metadata.station_info', 'geode.pyDate',
               'geode.pyProducts', 'os', 'platform', 'geode.pyArchiveStruct', 'traceback', 'geode.pyOptions',
               'geode.pyEvents', 'geode.Utils', 'numpy')

    depfuncs = (remove_from_archive, verify_rinex_date_multiday)

    JobServer.create_cluster(execute_ppp, depfuncs, callback=callback_handle, progress_bar=pbar, modules=modules)

    for record in tblrinex:

        rinex_path = pyArchive.build_rinex_path(record['NetworkCode'],
                                                record['StationCode'],
                                                record['ObservationYear'],
                                                record['ObservationDOY'])

        # add the base dir
        rinex_path = os.path.join(archive_path, rinex_path)

        JobServer.submit(record, rinex_path, h_tolerance)

    JobServer.wait()

    pbar.close()

    # print a summary of the events generated by the run
    print_scan_archive_summary(cnn)


def print_scan_archive_summary(cnn):

    # find the last event in the executions table
    exec_date = cnn.query_float('SELECT max(exec_date) as mx FROM executions WHERE script = \'ScanArchive.py\'')

    info = cnn.query_float('SELECT count(*) as cc FROM events WHERE "EventDate" >= \'%s\' AND "EventType" = \'info\''
                           % exec_date[0][0])

    erro = cnn.query_float('SELECT count(*) as cc FROM events WHERE "EventDate" >= \'%s\' AND "EventType" = \'error\''
                           % exec_date[0][0])

    warn = cnn.query_float('SELECT count(*) as cc FROM events WHERE "EventDate" >= \'%s\' AND "EventType" = \'warn\''
                           % exec_date[0][0])

    print(' >> Summary of events for this run:')
    print(' -- info    : %i' % info[0][0])
    print(' -- errors  : %i' % erro[0][0])
    print(' -- warnings: %i' % warn[0][0])


def export_station(cnn, stnlist, pyArchive, archive_path, dataless):

    # loop collecting the necessary information
    print(" >> Collecting the information for each station in the list...")

    pbar1 = tqdm(total=len(stnlist), ncols=160, position=0, disable=None)

    for stn in stnlist:

        NetworkCode = stn['NetworkCode']
        StationCode = stn['StationCode']

        rs_stn = cnn.query('SELECT * FROM stations WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                           % (NetworkCode, StationCode))
        stn = rs_stn.dictresult()[0]

        pbar1.set_postfix(Station='%s.%s' % (NetworkCode, StationCode))
        pbar1.update()

        # list of rinex files
        rinex_lst = cnn.query('SELECT * FROM rinex WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                              'ORDER BY "ObservationYear", "ObservationDOY"' % (NetworkCode, StationCode))
        rinex_lst = rinex_lst.dictresult()
        # rinex_lst = pyArchive.get_rinex_record(NetworkCode=NetworkCode, StationCode=StationCode)
        # list of metadata
        stninfo = StationInfo(cnn, NetworkCode, StationCode, allow_empty=True)

        export_dic = {'NetworkCode' : NetworkCode,
                      'StationCode' : StationCode,
                      'StationInfo' : stninfo } 

        if stn['lat'] and stn['auto_x'] and stn['Harpos_coeff_otl']:
            export_dic['lat']          = stn['lat']
            export_dic['lon']          = stn['lon']
            export_dic['height']       = stn['height']
            export_dic['x']            = stn['auto_x']
            export_dic['y']            = stn['auto_y']
            export_dic['z']            = stn['auto_z']
            export_dic['otl']          = stn['Harpos_coeff_otl']
            export_dic['dome']         = stn['dome']
            export_dic['max_dist']     = stn['max_dist']
            export_dic['marker'] = stn['marker']
            export_dic['country_code'] = stn['country_code']
        else:
            tqdm.write(' -- Warning! %s.%s has incomplete station data' % (NetworkCode, StationCode))

        # create dir for the rinex files
        # dest = 'production/export/%s.%s' % (NetworkCode, StationCode)
        # if not os.path.isdir(dest):
        #    os.makedirs(dest)

        rinex_dict = []
        pbar2      = tqdm(total=len(rinex_lst), ncols=160, position=1, disable=None, leave=False)

        tqdm.write(' >> Creating station package for %s.%s' % (NetworkCode, StationCode))

        with zipfile.ZipFile('%s.%s.zip' % (NetworkCode, StationCode),
                             "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for rnx in rinex_lst:
                # update bar first to provide more information
                pbar2.set_postfix(Date='%4.0f %03.0f' % (rnx['ObservationYear'], rnx['ObservationDOY']))
                pbar2.update()
                # make a copy of each file
                rnx_path = pyArchive.build_rinex_path(NetworkCode     = NetworkCode,
                                                      StationCode     = StationCode,
                                                      ObservationYear = rnx['ObservationYear'],
                                                      ObservationDOY  = rnx['ObservationDOY'],
                                                      filename        = rnx['Filename'])
                # tqdm.write(' -- Adding %s observation date %4.0f %03.0f filename %s'
                #            % (stationID(rnx), rnx['ObservationYear'], rnx['ObservationDOY'], rnx_path))
                try:
                    if not dataless:
                        # only copy the files if dataless == False
                        # shutil.copy(os.path.join(archive_path, rnx_path), os.path.join(dest, os.path.basename(rnx_path)))
                        name = os.path.normpath(os.path.join(archive_path, rnx_path))
                        zf.write(name, os.path.basename(name))

                    rinex_dict += [rnx]
                except IOError:
                    tqdm.write(' -- Warning! File not found in archive: %s' % (os.path.join(archive_path, rnx_path)))

            export_dic['files'] = len(rinex_dict)
            export_dic['rinex'] = rinex_dict

            with zf.open( '%s.%s.json' % (NetworkCode, StationCode), 'w') as json_file:
                data_bytes = json.dumps(export_dic, indent=4, sort_keys=True, cls=Encoder).encode('utf-8')
                json_file.write(data_bytes)

        pbar2.close()
    pbar1.close()


def import_station(cnn, args):

    files   = args[1:]
    network = args[0]

    archive = pyArchiveStruct.RinexStruct(cnn)

    print(" >> Processing input files...")

    for ff in tqdm(files, ncols=160, disable=None):

        filename = os.path.basename(ff)

        if filename.endswith('.zip'):

            fileparts = filename.split('.')

            NetworkCode = fileparts[0].lower()
            StationCode = fileparts[1].lower()

            path = 'production/archive/' + str(uuid.uuid4())

            try:
                # process each station file
                zipfile.ZipFile(ff).extractall(path)

                jfile = glob.glob(os.path.join(path, '*.json'))

                # DDG: may want to consider other compression formats
                rnx_files = [f
                             for f_ in [glob.glob(os.path.join(path, e))
                                        for e in ('*.gz', '*d.Z')]
                             for f in f_]

                station = json.loads(file_read_all(jfile[0]))

                spatial = pyPPP.PPPSpatialCheck([station['lat']], 
                                                [station['lon']], 
                                                [station['height']])

                result, match, closest_stn = spatial.verify_spatial_coherence(cnn, StationCode)

                tqdm.write(' -- Processing %s' % filename)
                
                if result:
                    tqdm.write(' -- Found external station %s.%s in network %s (distance %.3f)'
                               % (NetworkCode, StationCode, match[0]['NetworkCode'], match[0]['distance']))

                    # ask the user what to do with the data
                    r = input('\n  Insert data to this station?: y/n ')

                    if r.lower() == 'y':
                        try_insert_files(cnn, archive, station, match[0]['NetworkCode'], StationCode, rnx_files)

                elif len(match) == 1:
                    tqdm.write(' -- External station %s.%s not found. Possible match is %s.%s: %.3f m'
                               % (NetworkCode, StationCode, match[0]['NetworkCode'],
                                  match[0]['StationCode'], match[0]['distance']))

                    # ask the user what to do with the data
                    r = input('\n  Insert new station %s with network code %s '
                                  'or add this data to station %s.%s?: (i)nsert new/(a)dd '
                                  % (StationCode, network, match[0]['NetworkCode'], match[0]['StationCode']))

                    if r.lower() == 'i':
                        if insert_station(cnn, network, station):
                            try_insert_files(cnn, archive, station, network, StationCode, rnx_files)
                    else:
                        # if data is added to existing station, replace the StationCode with the matched
                        # StationCode the rinexobj will apply the naming convention to the file
                        try_insert_files(cnn, archive, station, match[0]['NetworkCode'],
                                         match[0]['StationCode'], rnx_files)

                elif len(match) > 1:
                    tqdm.write(' -- External station %s.%s not found. Possible matches are %s'
                               % (NetworkCode, StationCode,
                                  ', '.join(['%s.%s: %.3f m' %
                                             (m['NetworkCode'], m['StationCode'], m['distance']) for m in match])))

                    options = ', '.join(['%s.%s (%i)' % (m['NetworkCode'], m['StationCode'], i+1)
                                         for i, m in enumerate(match)])

                    r = input('\n  Insert new station %s with network code %s '
                                  'or add this data as %s: (i)nsert new/(number)' % (StationCode, network, options))

                    if r.lower() == 'i':
                        if insert_station(cnn, network, station):
                            try_insert_files(cnn, archive, station, network, StationCode, rnx_files)
                    else:
                        try:
                            i = int(r)
                            try_insert_files(cnn, archive, station, match[i]['NetworkCode'],
                                             match[i]['StationCode'], rnx_files)
                        except ValueError:
                            tqdm.write(' -- Selected value is not numeric!')

                else:
                    tqdm.write(' -- External station %s.%s not found. Closest station is %s.%s: %.3f m'
                               % (NetworkCode, StationCode, closest_stn[0]['NetworkCode'],
                                  closest_stn[0]['StationCode'], closest_stn[0]['distance']))

                    # ask the user what to do with the data
                    r = input('\n  Insert new station with default station network %s?: y/n ' % network)

                    if r.lower() == 'y':
                        if insert_station(cnn, network, station):
                            # now that station was created, insert files
                            try_insert_files(cnn, archive, station, network,
                                             StationCode, rnx_files)

                # delete all files once we're done.
                shutil.rmtree(path)

            except zipfile.BadZipfile:
                tqdm.write(' -- Bad zipfile detected: %s' % ff)


def insert_station(cnn, network, station):

    # check that another station with same name doesn't exist in this network
    rstn = cnn.query_float('SELECT * FROM stations WHERE "NetworkCode" = \'%s\' AND '
                           '"StationCode" = \'%s\'' % (network, station['StationCode']))
    if len(rstn) > 0:
        tqdm.write(' -- Station code %s already exists in network %s. Cannot insert station'
                   % (station['StationCode'], network))
        return False
    else:
        # check if network exists
        if not cnn.query_float('SELECT * FROM networks WHERE "NetworkCode" = \'%s\'' % network):
            cnn.insert('networks', NetworkCode=network)

        # insert the station and metadata in the json file
        cnn.insert('stations',
                   NetworkCode      = network,
                   StationCode      = station['StationCode'],
                   auto_x           = station['x'],
                   auto_y           = station['y'],
                   auto_z           = station['z'],
                   Harpos_coeff_otl = station['otl'],
                   lat              = station['lat'],
                   lon              = station['lon'],
                   height           = station['height'],
                   max_dist         = station['max_dist'] if 'max_dist' in station.keys() else None,
                   dome             = station['dome']     if 'dome'     in station.keys() else None)
        return True


def try_insert_files(cnn, archive, station, NetworkCode, StationCode, rinex):

    import_stninfo = station['StationInfo']
    stninfo        = StationInfo(cnn, NetworkCode, StationCode, allow_empty=True)

    if rinex:
        # a station file with rinex data in it. Attempt to insert the data and the associated station information
        for rnx in rinex:

            try:
                with pyRinex.ReadRinex(NetworkCode, StationCode, rnx) as rinexinfo:

                    inserted = archive.insert_rinex(rinexobj=rinexinfo)

                    if not inserted:
                        # display an error message
                        tqdm.write(' -- %s.%s (%s) not imported: already existed in database.'
                                   % (NetworkCode, StationCode, os.path.basename(rnx)))
                    else:
                        tqdm.write(' -- %s.%s (%s) successfully imported into database.'
                                   % (NetworkCode, StationCode, os.path.basename(rnx)))

                    try:
                        StationInfo(cnn, NetworkCode, StationCode, rinexinfo.date)

                    except StationInfoException:

                        # station info not in db! import the corresponding station info

                        stninfo_inserted = False

                        for record in import_stninfo:
                            import_record = StationInfoRecord(NetworkCode, StationCode, _record=record)

                            # DDG: to avoid problems with files that contain two different station info records, check
                            # that import_record.DateEnd.datetime() is not less than the first observation of the rinex
                            if rinexinfo.datetime_firstObs >= import_record.DateStart.datetime() and \
                               not import_record.DateEnd.datetime() <= rinexinfo.datetime_firstObs:

                                if rinexinfo.datetime_lastObs > import_record.DateEnd.datetime():
                                    tqdm.write('    WARNING! RINEX file %s has an end data past the station info '
                                               'record. Maybe this file has a receiver/antenna change in the middle.'
                                               % os.path.basename(rnx))

                                # the record we are looking for
                                try:
                                    stninfo.insert_station_info(import_record)
                                    stninfo_inserted = True

                                except StationInfoException as e:
                                    tqdm.write('    ' + str(e))

                        if not stninfo_inserted:
                            tqdm.write('    Could not find a valid station info in the database or in the station '
                                       'package. File remains in database without metadata.')
            except pyRinex.pyRinexException as e:
                tqdm.write('    Problem while processing RINEX file for database record insert: ' + str(e))
    else:
        # a station file without rinex data
        # attempt to merge the station information

        for record in import_stninfo:

            import_record = StationInfoRecord(NetworkCode, StationCode, _record=record)

            try:
                stninfo.insert_station_info(import_record)
                tqdm.write('  -- Successful insert: %s -> %s' % (str(import_record['DateStart']), 
                                                                 str(import_record['DateEnd'])))

            except StationInfoException as e:
                tqdm.write(' -- ' + str(e))


def get_rinex_file(cnn, stnlist, date, Archive_path):

    archive = pyArchiveStruct.RinexStruct(cnn)

    print(" >> Getting stations from db...")

    for stn in tqdm(stnlist, ncols=80, disable=None):

        NetworkCode = stn['NetworkCode']
        StationCode = stn['StationCode']

        rinex = archive.build_rinex_path(NetworkCode, StationCode, date.year, date.doy)

        if rinex is not None:
            rinex = os.path.join(Archive_path, rinex)

            with pyRinex.ReadRinex(NetworkCode, StationCode, rinex, False) as Rinex:  # type: pyRinex.ReadRinex

                stninfo = StationInfo(cnn, NetworkCode, StationCode, Rinex.date)

                Rinex.normalize_header(stninfo)
                Rinex.compress_local_copyto('./')
        else:
            tqdm.write(" -- %s not found for %s.%s" % (date.yyyyddd(), NetworkCode, StationCode))


def main():

    parser = argparse.ArgumentParser(description='Archive operations Main Program')

    parser.add_argument('stnlist', type=str, nargs='+', metavar='all|net.stnm',
                        help=station_list_help())

    parser.add_argument('-rinex', '--rinex', metavar='{ignore_stnlist}', type=int, nargs=1, default=None,
                        help="Scan the current archive for RINEX 2/3 files and add them to the database if missing. "
                             "Station list will be used to filter specific networks and stations if {ignore_stnlist} = "
                             "0. For example: ScanArchive [net].all -rinex 0 will process all the stations in network "
                             "[net], but networks and stations have to exist in the database. "
                             "If ScanArchive [net].all -rinex 1 the station list will be ignored and everything in the "
                             "archive will be checked (and added to the db if missing) even if networks and stations "
                             "don't exist. Networks and stations will be added if they don't exist.")

    parser.add_argument('-otl', '--ocean_loading', action='store_true',
                        help="Calculate ocean loading coefficients using FES2004. To calculate FES2014b coefficients, "
                             "use OTL_FES2014b.py")

    parser.add_argument('-stninfo', '--station_info', nargs='*', metavar='argument',
                        help="Insert station information to the database. "
                             "If no arguments are given, then scan the archive for station info files and use their "
                             "location (folder) to determine the network to use during insertion. "
                             "Only stations in the station list will be processed. "
                             "If a filename is provided, then scan that file only, in which case a second argument "
                             "specifies the network to use during insertion. Eg: -stninfo ~/station.info arg. "
                             "In cases where multiple networks are being processed, the network argument will be used "
                             "to desambiguate station code conflicts. "
                             "Eg: ScanArchive all -stninfo ~/station.info arg -> if a station named igm1 exists in "
                             "networks 'igs' and 'arg', only 'arg.igm1' will get the station information insert. "
                             "Use keyword 'stdin' to read the station information data from the pipeline.")

    parser.add_argument('-export', '--export_station', nargs='?', metavar='[dataless seed]', default=None, const=False,
                        help="Export a station from the local database that can be imported into another "
                             "GeoDE system using the -import option."
                             "One file is created per station in the current directory. If the [dataless seed] switch "
                             "is passed (e.g. -export true), then the export seed is created without data "
                             "(only metadata included, i.e. station info, station record, etc).")

    parser.add_argument('-import', '--import_station', nargs='+', type=str, metavar=('{default net}', '{zipfiles}'),
                        help="Import a station from zipfiles produced by another GeoDE system. "
                             "Wildcards are accepted to import multiple zipfiles. If station does not exist, use "
                             "{default net} to specify the network where station should be added to. If {default net} "
                             "does not exit, it will be created. Station list is ignored.")

    parser.add_argument('-get', '--get_from_archive', nargs=1, metavar='{date}',
                        help="Get the specified station from the archive and copy it to the current directory. Fix it "
                             "to match the station information in the database.")

    parser.add_argument('-ppp', '--ppp', nargs='*', metavar='argument',
                        help="Run ppp on the rinex files in the database. Append [date_start] and (optionally) "
                             "[date_end] to limit the range of the processing. Allowed formats are yyyy_doy, wwww-d, "
                             "fyear or yyyy/mm/dd. Append keyword 'hash' to the end to check the PPP hash values "
                             "against the station information records and used orbit. If hash doesn't match, "
                             "recalculate the PPP solutions.")

    parser.add_argument('-rehash', '--rehash', nargs='*', metavar='argument',
                        help="Check PPP hash against station information and orbit hash. Rehash PPP solutions to match "
                             "the station information and orbit hash without recalculating the PPP solution. "
                             "Optionally append [date_start] and (optionally) [date_end] to limit the rehashing time "
                             "window. Allowed formats are yyyy.doy or yyyy/mm/dd.")

    parser.add_argument('-tol', '--stninfo_tolerant', nargs=1, type=int, metavar='{hours}', default=[0],
                        help="Specify a tolerance (in hours) for station information gaps (only use for early "
                             "survey data). Default is zero.")

    parser.add_argument('-np', '--noparallel', action='store_true', help="Execute command without parallelization.")

    add_version_argument(parser)

    args = parser.parse_args()

    if args.station_info is not None and (not len(args.station_info) in (0, 2)):
        parser.error('-stninfo requires 0 or 2 arguments. {} given.'.format(len(args.station_info)))

    Config = pyOptions.ReadOptions("gnss_data.cfg")  # type: pyOptions.ReadOptions

    cnn = dbConnection.Cnn("gnss_data.cfg")
    # create the execution log
    cnn.insert('executions', script='ScanArchive.py')

    # get the station list
    stnlist = Utils.process_stnlist(cnn, args.stnlist)

    pyArchive = pyArchiveStruct.RinexStruct(cnn)

    if args.station_info is None:
        JobServer = pyJobServer.JobServer(Config, run_parallel=not args.noparallel,
                                          software_sync=[Config.options['ppp_remote_local']])
    else:
        JobServer = None

    #########################################

    if args.rinex is not None:
        scan_rinex(cnn, JobServer, pyArchive, Config.archive_path, stnlist, args.rinex)

    #########################################

    if args.ocean_loading:
        process_otl(cnn, JobServer, stnlist)

    #########################################

    if args.station_info is not None:
        if len(args.station_info) == 0:
            scan_station_info(JobServer, pyArchive, Config.archive_path, stnlist)
        else:
            stn_info_stdin = []
            if args.station_info[0] == 'stdin':
                for line in sys.stdin:
                    stn_info_stdin.append(line)

            scan_station_info_man(cnn, pyArchive, args.station_info[0], stnlist, args.station_info[1], stn_info_stdin)

    #########################################

    if args.rehash is not None:
        dates = []
        try:
            dates = process_date(args.rehash)
        except ValueError as e:
            parser.error(str(e))

        hash_check(cnn, stnlist, dates[0], dates[1], rehash=True, h_tolerant=args.stninfo_tolerant[0])

    #########################################

    if args.ppp is not None:
        # check other possible arguments
        dates = []

        try:
            dates = process_date([date for date in args.ppp if date != 'hash'])
        except ValueError as e:
            parser.error(str(e))

        if 'hash' in args.ppp:
            hash_check(cnn, stnlist, dates[0], dates[1], rehash=False, h_tolerant=args.stninfo_tolerant[0])

        process_ppp(cnn, Config, pyArchive, Config.archive_path, JobServer, stnlist, dates[0], dates[1],
                    args.stninfo_tolerant[0])

    #########################################

    if args.export_station is not None:

        export_station(cnn, stnlist, pyArchive, Config.archive_path, args.export_station)

    #########################################

    if args.import_station:

        import_station(cnn, args.import_station)

    #########################################

    if args.get_from_archive:

        dates = process_date(args.get_from_archive)

        get_rinex_file(cnn, stnlist, dates[0], Config.archive_path)

    # remove the production dir
    # if os.path.isdir('production'):
    #    rmtree('production')

    if JobServer is not None:
        JobServer.close_cluster()


if __name__ == '__main__':
    main()
