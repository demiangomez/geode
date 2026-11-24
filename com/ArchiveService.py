#!/usr/bin/env python
"""
Project: Parallel.Archive
Date: 3/19/17 11:41 AM
Author: Demian D. Gomez

ArchiveService
==============================================================================
Main script that scans the repository for new rinex files.
It PPPs the rinex files and searches the database for stations
 (within configured distance in stations table)
with the same station 4 letter code.
If the station exists in the db, it moves the file to the archive
 and adds the new file to the "rinex" table.
if the station doesn't exist, then it incorporates the station
 with a special NetworkCode (???) and leaves the
file in the repo until you assign the correct NetworkCode and
 add the station information.

It is invoked just by calling python ArchiveService.py
Requires the config file gnss_data.cfg (in the running folder)

Options:
--purge_locks: deletes any locked files from repository and database
--no_parallel: runs without parallelizing the execution
"""

import os
import datetime
import time
import uuid
import traceback
import platform
import argparse


# deps
from tqdm import tqdm

# app
from geode.Utils import (file_append, file_try_remove, file_open,
                          dir_try_remove, stationID, get_field_or_attr, add_version_argument)
from geode import ConvertRaw
from geode import pyJobServer
from geode import pyEvents
from geode import pyOptions
from geode import Utils
from geode import pyOTL
from geode import pyRinex
from geode import pyRinexName
from geode import dbConnection
from geode.metadata.station_info import StationInfoException
from geode import pyArchiveStruct
from geode import pyPPP
from geode import pyProducts

repository_data_in   = ''
cnn                  = dbConnection.Cnn('gnss_data.cfg')
station_import_counts = {}  # net.stnm -> count of files imported this run


def insert_station_w_lock(cnn, StationCode, filename,
                          lat, lon, h, x, y, z, otl):
    rs = cnn.query(
        """SELECT "NetworkCode" FROM
        (SELECT *, 2*asin(sqrt(sin((radians(%.8f)-radians(lat))/2)^2 +
        cos(radians(lat)) * cos(radians(%.8f)) * sin((radians(%.8f) -
        radians(lon))/2)^2))*6371000 AS distance
        FROM stations WHERE "NetworkCode" LIKE \'?%%\' AND
        "StationCode" = \'%s\') as DD
        WHERE distance <= 100""" % (lat, lat, lon, StationCode))

    if rs.ntuples():
        NetworkCode = rs.dictresult()[0]['NetworkCode']
        # if it's a record that was found, update the locks
        #  with the station code
        cnn.update('locks', {'filename': filename},
                   NetworkCode=NetworkCode, StationCode=StationCode)
    else:
        # insert this new record in the stations table
        #  using a default network name (???)
        # this network name is a flag that tells ArchiveService
        #  that no data should be added to this station
        # until a NetworkCode is assigned.

        # check if network code exists
        NetworkCode = '???'
        index = 0
        while cnn.query(
            'SELECT * FROM stations '
            'WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                % (NetworkCode, StationCode)).ntuples() != 0:

            NetworkCode = hex(index).replace('0x', '').rjust(3, '?')
            index += 1
            if index > 255:
                # FATAL ERROR! the networkCode exceed FF
                raise Exception("While looking for a temporary network code, "
                                "?ff was reached! Cannot continue executing pyArchiveService. "
                                "Please free some temporary network codes.")

        # @todo optimize changing the query for EXISTS / LIMIT 1?
        rs = cnn.query(
            'SELECT * FROM networks WHERE "NetworkCode" = \'%s\''
            % NetworkCode)

        cnn.begin_transac()
        if rs.ntuples() == 0:
            # create network code
            cnn.insert('networks',
                       NetworkCode=NetworkCode,
                       NetworkName='Temporary network for new stations')

        # insert record in stations with temporary NetworkCode
        try:
            # DDG: added code to insert new station including the country_code
            from geopy.geocoders import Nominatim
            import country_converter as coco
            # find the country code for the station
            geolocator = Nominatim(user_agent="GeoDE")
            location = geolocator.reverse("%f, %f" % (lat, lon))

            if location and 'country_code' in location.raw['address'].keys():
                ISO3 = coco.convert(
                    names=location.raw['address']['country_code'], to='ISO3')
            else:
                ISO3 = None

            cnn.insert('stations',
                       NetworkCode=NetworkCode,
                       StationCode=StationCode,
                       auto_x=x,
                       auto_y=y,
                       auto_z=z,
                       Harpos_coeff_otl=otl,
                       lat=round(lat, 8),
                       lon=round(lon, 8),
                       height=round(h, 3),
                       country_code=ISO3)
        except dbConnection.dbErrInsert as e:
            # another process did the insert before, ignore the error
            file_append('errors_ArchiveService.log',
                        'ON ' + datetime.datetime.now().strftime(
                            '%Y-%m-%d %H:%M:%S') +
                        ' an unhandled error occurred:\n' +
                        str(e) + '\n' +
                        'END OF ERROR =================== \n\n')
            pass

        # update the lock information for this station
        cnn.update('locks', {'filename': filename},
                   NetworkCode=NetworkCode, StationCode=StationCode)
        cnn.commit_transac()


def callback_handle(job):
    global cnn
    global repository_data_in
    global station_import_counts

    def log_job_error(msg):
        tqdm.write(' -- There were unhandled errors during this batch. '
                   'Please check errors_ArchiveService.log for details')

        # function to print any error that are
        #  encountered during parallel execution
        file_append('errors_ArchiveService.log',
                    'ON ' + datetime.datetime.now().strftime(
                        '%Y-%m-%d %H:%M:%S') +
                    ' an unhandled error occurred:\n' +
                    str(msg) + '\n' +
                    'END OF ERROR =================== \n\n')

    if job.result is not None:
        out_message = job.result[0]
        new_station = job.result[1]
        imported_station = job.result[2] if len(job.result) > 2 else None

        if imported_station:
            station_import_counts[imported_station] = (
                station_import_counts.get(imported_station, 0) + 1)

        if out_message:
            log_job_error(out_message)

        if new_station:
            # check the distance w.r.t the current new stations

            StationCode = new_station[0]
            x = new_station[1][0]
            y = new_station[1][1]
            z = new_station[1][2]
            otl = new_station[2]
            lat = new_station[3][0]
            lon = new_station[3][1]
            h = new_station[3][2]

            filename = os.path.relpath(new_station[4], repository_data_in)

            tqdm.write(" -- New station %s was found in the repository at %s. "
                       "Please assign a network to the new station and remove "
                       "the locks from the files before running again ArchiveService." % (StationCode, filename))

            # logic behind this sql sentence:
            # we are searching for a station within 100 meters
            #  that has been recently added, so NetworkCode = ???
            # we also force the StationName to be equal to that of
            #  the incoming RINEX to avoid having problems with
            # stations that are within 100 m
            #  (misidentifying IGM1 for IGM0, for example).
            # This logic assumes that stations within
            #  100 m do not have the same name!
            insert_station_w_lock(
                cnn, StationCode, filename, lat, lon, h, x, y, z, otl)

    elif job.exception:
        log_job_error(job.exception)


def check_rinex_timespan_int(rinex, stn):

    # how many seconds difference between
    #  the rinex file and the record in the db
    stime_diff = abs((stn['ObservationSTime'] -
                      rinex.datetime_firstObs).total_seconds())
    etime_diff = abs((stn['ObservationETime'] -
                      rinex.datetime_lastObs) .total_seconds())

    # at least four minutes different on each side
    if (stime_diff <= 240 and
        etime_diff <= 240 and
        stn['Interval'] == rinex.interval):
        return False
    else:
        return True


def write_error(folder, filename, msg):
    # @todo why retries are used?
    # do append just in case...
    count = 0
    while True:
        try:
            file_append(os.path.join(folder, filename), msg)
            return
        except IOError as e:
            if count < 3:
                count += 1
            else:
                raise IOError(str(e) + ' after 3 retries')


def error_handle(cnn, event, crinez, folder, filename, no_db_log=False):

    # rollback any active transactions
    if cnn.active_transaction:
        cnn.rollback_transac()

    # move to the folder indicated
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
    except OSError:
        # racing condition of two processes trying to create the same folder
        pass

    message = event['Description']

    mfile = filename
    try:
        mfile = os.path.basename(Utils.move(crinez,
                                            os.path.join(folder, filename)))
    except (OSError,
            ValueError) as e:
        message = 'could not move file into this folder!' + str(e) + \
                  '\n. Original error: ' + event['Description']

    error_file = pyRinexName.RinexNameFormat(mfile).filename_no_ext() + '.log'
    write_error(folder, error_file, message)

    if not no_db_log:
        cnn.insert_event(event)


def insert_data(cnn, archive, rinexinfo):

    inserted = archive.insert_rinex(rinexobj=rinexinfo)
    # if archive.insert_rinex has a dbInserErr,
    # it will be catched by the calling function
    # always remove original file
    os.remove(rinexinfo.origin_file)

    if not inserted:
        # insert an event to account for the file
        #  (otherwise is weird to have a missing rinex in the events table
        event = pyEvents.Event(
            Description=rinexinfo.crinez +
            "had the same interval and completion as an existing file. CRINEZ deleted from data_in.",
            NetworkCode=rinexinfo.NetworkCode,
            StationCode=rinexinfo.StationCode,
            Year=int(rinexinfo.date.year),
            DOY=int(rinexinfo.date.doy))

        cnn.insert_event(event)

    return inserted


def verify_rinex_multiday(cnn, rinexinfo, Config):
    # function to verify if rinex is multiday
    # returns true if parent process can continue with insert
    # returns false if file had to be moved to the retry

    # check if rinex is a multiday file
    #  (rinex with more than one day of observations)
    if not rinexinfo.multiday:
        return True

    # move all the files to the repository
    rnxlist = []
    for rnx in rinexinfo.multiday_rnx_list:
        rnxlist.append(rnx.rinex)
        # some other file, move it to the repository
        retry_folder = os.path.join(Config.repository_data_in_retry,
                                    'multidays_found/' + rnx.date.yyyy() +
                                    '/' + rnx.date.ddd())
        rnx.compress_local_copyto(retry_folder)

    # if the file corresponding to this session is found,
    #  assign its object to rinexinfo
    event = pyEvents.Event(
        Description="%s was a multi-day rinex file. The following rinex files where "
                    "generated and moved to the repository/data_in_retry: %s. The "
                    "file %s did not enter the database at this time." %
                    (rinexinfo.origin_file, ','.join(rnxlist),
                     rinexinfo.crinez),
        NetworkCode=rinexinfo.NetworkCode,
        StationCode=rinexinfo.StationCode,
        Year=int(rinexinfo.date.year),
        DOY=int(rinexinfo.date.doy))

    cnn.insert_event(event)

    # remove crinez from the repository (origin_file points to the repository,
    #  not to the archive in this case)
    os.remove(rinexinfo.origin_file)

    return False


def process_crinex_file(crinez, filename, data_rejected, data_retry):

    imported_station = None  # set to 'net.stnm' if a new record is inserted

    # create a uuid temporary folder in case we cannot read
    #  the year and doy from the file (and gets rejected)
    reject_folder = os.path.join(data_rejected, str(uuid.uuid4()))

    try:
        cnn = dbConnection.Cnn("gnss_data.cfg")
        Config = pyOptions.ReadOptions("gnss_data.cfg")
        archive = pyArchiveStruct.RinexStruct(cnn)
        # apply local configuration (path to repo) in the executing node
        crinez = os.path.join(Config.repository_data_in, crinez)

    except Exception:
        return (traceback.format_exc() +
                ' while opening the database to process file %s node %s'
                % (crinez, platform.node()), None, None)

    # assume a default networkcode
    NetworkCode = 'rnx'
    try:
        fileparts = pyRinexName.RinexNameFormat(filename)

        StationCode = fileparts.StationCode.lower()
        doy = fileparts.date.doy
        year = fileparts.date.year
    except pyRinexName.RinexNameException:
        event = pyEvents.Event(
            Description='''Could not read the station code,
                        year or doy for file ''' + crinez,
            EventType='error')
        error_handle(cnn, event, crinez, reject_folder,
                     filename, no_db_log=True)
        cnn.close()
        return event['Description'], None, None

    def fill_event(ev, desc=None):
        if desc:
            ev['Description'] += desc

        ev['StationCode'] = StationCode
        ev['NetworkCode'] = '???'
        ev['Year'] = year
        ev['DOY'] = doy

    # we can now make better reject and retry folders
    reject_folder = os.path.join(data_rejected,
                                 '%reason%' + '/%04i/%03i' % (year, doy))
    retry_folder = os.path.join(data_retry,
                                '%reason%' + '/%04i/%03i' % (year, doy))

    try:
        # main try except block
        # type: pyRinex.ReadRinex
        with pyRinex.ReadRinex(NetworkCode, StationCode, crinez) as rinexinfo:

            # STOP! see if rinexinfo is a multiday rinex file
            if not verify_rinex_multiday(cnn, rinexinfo, Config):
                # was a multiday rinex. verify_rinex_date_multiday
                #  took care of it
                cnn.close()
                return None, None, None

            # DDG: we don't use otl coefficients because
            #  we need an approximated coordinate
            # we therefore just calculate the first coordinate without otl
            # NOTICE that we have to trust
            #  the information coming in the RINEX header
            #  (receiver type, antenna type, etc)
            # we don't have station info data! Still, good enough
            # the final PPP coordinate will be calculated by
            #  pyScanArchive on a different process

            # make sure that the file has the appropriate
            #  coordinates in the header for PPP.
            # put the correct APR coordinates in the header.
            # ppp didn't work, try using sh_rx2apr
            brdc = pyProducts.GetBrdcOrbits(Config.brdc_path,
                                            rinexinfo.date,
                                            rinexinfo.rootdir)

            # inflate the chi**2 limit to make sure it will pass
            #  (even if we get a crappy coordinate)
            try:
                rinexinfo.auto_coord(brdc, chi_limit=1000)

                # normalize header to add the APR coordinate
                # empty dict since nothing extra to change
                #  (other than the APR coordinate)
                rinexinfo.normalize_header({})
            except pyRinex.pyRinexExceptionNoAutoCoord:
                # could not determine an autonomous coordinate,
                #  try PPP anyways. 50% chance it will work
                pass

            # DDG: now there is no sp3altrn anymore
            # type: pyPPP.RunPPP
            with pyPPP.RunPPP(rinexinfo, '',
                              Config.options, Config.sp3types, (),
                              rinexinfo.antOffset, strict=False,
                              apply_met=False,
                              clock_interpolation=True) as ppp:
                try:
                    ppp.exec_ppp()

                except pyPPP.pyRunPPPException as ePPP:

                    # inflate the chi**2 limit to make sure it
                    #  will pass (even if we get a crappy coordinate)
                    # if coordinate is TOO bad it will get kicked off
                    #  by the unreasonable geodetic height
                    try:
                        auto_coords_xyz, auto_coords_lla = (
                            rinexinfo.auto_coord(brdc, chi_limit=1000))

                    except pyRinex.pyRinexExceptionNoAutoCoord as e:
                        # catch pyRinexExceptionNoAutoCoord and convert
                        #  it into a pyRunPPPException

                        raise pyPPP.pyRunPPPException(
                            "Both PPP and sh_rx2apr failed to obtain a coordinate for %s.\n"
                            "The file has been moved into the rejection folder. "
                            "Summary PPP file and error (if exists) follows:\n%s\n\n"
                            "ERROR section:\n%s\npyRinex.auto_coord "
                            "error follows:\n%s"
                            % (crinez.replace(Config.repository_data_in, ''),
                                ppp.summary,
                                str(ePPP).strip(), str(e).strip()))

                    # DDG: this is correct - auto_coord returns
                    #  a numpy array (calculated in ecef2lla),
                    # so ppp.lat = auto_coords_lla is consistent.
                    ppp.lat = auto_coords_lla[0]
                    ppp.lon = auto_coords_lla[1]
                    ppp.h = auto_coords_lla[2]
                    ppp.x = auto_coords_xyz[0]
                    ppp.y = auto_coords_xyz[1]
                    ppp.z = auto_coords_xyz[2]

                # check for unreasonable heights
                if ppp.h[0] > 9000 or ppp.h[0] < -400:

                    raise pyRinex.pyRinexException(
                        os.path.relpath(crinez, Config.repository_data_in) +
                        ": unreasonable geodetic height (%.3f). "
                        "RINEX file will not enter the archive." %
                        (ppp.h[0]))

                result, match, _ = ppp.verify_spatial_coherence(cnn,
                                                                StationCode)

                if result:
                    # insert: there is only 1 match with the same StationCode.
                    rinexinfo.rename(NetworkCode=match[0]['NetworkCode'])
                    if insert_data(cnn, archive, rinexinfo):
                        imported_station = '%s.%s' % (rinexinfo.NetworkCode,
                                                      rinexinfo.StationCode)

                elif len(match) == 1:
                    error = ("%s matches the coordinate of %s.%s (distance = %8.3f m) but the filename "
                             "indicates it is %s. Please verify that this file belongs to %s.%s, rename "
                             "it and try again. The file was moved to the retry folder. "
                             "Rename script and pSQL sentence follows:\n"
                             "BASH# mv %s %s\n"
                             "PSQL# INSERT INTO stations (\"NetworkCode\", \"StationCode\", \"auto_x\", \"auto_y\", "
                             "\"auto_z\", \"lat\", \"lon\", \"height\") "
                             "VALUES ('???','%s', %12.3f, %12.3f, %12.3f, %10.6f, %10.6f, %8.3f)\n") % (
                            os.path.relpath(crinez, Config.repository_data_in),
                            match[0]['NetworkCode'],
                            match[0]['StationCode'],
                            float(match[0]['distance']), StationCode,
                            match[0]['NetworkCode'],
                            match[0]['StationCode'],
                            os.path.join(retry_folder, filename),
                            os.path.join(retry_folder, filename.replace(StationCode, match[0]['StationCode'])),
                            StationCode, ppp.x, ppp.y, ppp.z, ppp.lat[0],
                            ppp.lon[0], ppp.h[0])

                    raise pyPPP.pyRunPPPExceptionCoordConflict(error)

                elif len(match) > 1:
                    # a number of things could have happened:
                    # 1) wrong station code, and more than
                    #    one matching stations
                    #    (that do not match the station code, of course)
                    #    see rms.lhcl 2007 113 -> matches rms.igm0:
                    #    34.293 m, rms.igm1: 40.604 m, rms.byns: 4.819 m
                    # 2) no entry in the database for this solution
                    #  -> add a lock and populate the exit args

                    # no match, but we have some candidates

                    error = ("Solution for RINEX in repository (%s %s) did not match a unique station location "
                             "(and station code) within 5 km. Possible candidate(s): %s. "
                             "This file has been moved to data_in_retry. pSQL sentence follows:\n"
                             "PSQL# INSERT INTO stations (\"NetworkCode\", \"StationCode\", \"auto_x\", \"auto_y\", "
                             "\"auto_z\", \"lat\", \"lon\", \"height\") "
                             "VALUES ('???','%s', %12.3f, %12.3f, %12.3f, %10.6f, %10.6f, %8.3f)\n") % (
                            os.path.relpath(crinez, Config.repository_data_in),
                            rinexinfo.date.yyyyddd(),
                            ', '.join(['%s.%s: %.3f m' % (
                                m['NetworkCode'], m['StationCode'],
                                m['distance']) for m in match]),
                                StationCode, ppp.x, ppp.y, ppp.z, ppp.lat[0],
                                ppp.lon[0], ppp.h[0])

                    raise pyPPP.pyRunPPPExceptionCoordConflict(error)

                else:
                    # only found a station removing the distance limit
                    #  (could be thousands of km away!)

                    # The user will have to add the metadata to the database
                    # before the file can be added,
                    # but in principle no problem was detected
                    # by the process. This file will stay in this folder
                    # so that it gets analyzed again but a "lock"
                    # will be added to the file that will have to be
                    # removed before the service analyzes again.
                    # if the user inserted the station by then,
                    # it will get moved to the appropriate place.
                    # we return all the relevant metadata to ease the
                    # insert of the station in the database

                    otl = pyOTL.OceanLoading(
                        StationCode, Config.options['grdtab'],
                        Config.options['otlgrid'])
                    # use the ppp coordinates to calculate the otl
                    coeff = otl.calculate_otl_coeff(x=ppp.x, y=ppp.y, z=ppp.z)

                    # add the file to the locks table so that it
                    # doesn't get processed over and over
                    # this will be removed by user so that the file
                    # gets reprocessed once all the metadata is ready
                    cnn.insert('locks', filename=os.path.relpath(crinez, Config.repository_data_in))
                    cnn.close()
                    return None, [StationCode,
                                  (ppp.x, ppp.y, ppp.z),
                                  coeff,
                                  (ppp.lat[0], ppp.lon[0], ppp.h[0]),
                                  crinez], None

    except (pyRinex.pyRinexExceptionBadFile,
            pyRinex.pyRinexExceptionSingleEpoch,
            pyRinex.pyRinexExceptionNoAutoCoord) as e:

        reject_folder = reject_folder.replace('%reason%', 'bad_rinex')

        # add more verbose output
        fill_event(e.event, '\n%s: (file moved to %s)'
                   % (os.path.relpath(crinez, Config.repository_data_in),
                      reject_folder))

        # error, move the file to rejected folder
        error_handle(cnn, e.event, crinez, reject_folder, filename)

    except pyRinex.pyRinexException as e:

        retry_folder = retry_folder.replace('%reason%', 'rinex_issues')

        # add more verbose output
        fill_event(e.event, '\n%s: (file moved to %s)'
                   % (os.path.relpath(crinez, Config.repository_data_in), retry_folder))
        # error, move the file to rejected folder
        error_handle(cnn, e.event, crinez, retry_folder, filename)

    except pyPPP.pyRunPPPExceptionCoordConflict as e:

        retry_folder = retry_folder.replace('%reason%', 'coord_conflicts')

        fill_event(e.event)
        e.event['Description'] = e.event['Description'].replace(
            '%reason%', 'coord_conflicts')

        error_handle(cnn, e.event, crinez, retry_folder, filename)

    except pyPPP.pyRunPPPException as e:

        reject_folder = reject_folder.replace('%reason%', 'no_ppp_solution')

        fill_event(e.event)
        error_handle(cnn, e.event, crinez, reject_folder, filename)

    except station_info.StationInfoException as e:

        retry_folder = retry_folder.replace(
            '%reason%', 'station_info_exception')

        fill_event(e.event, ". The file will stay in the repository and will "
                            "be processed during the next cycle of pyArchiveService.")
        error_handle(cnn, e.event, crinez, retry_folder, filename)

    except pyOTL.pyOTLException as e:

        retry_folder = retry_folder.replace('%reason%', 'otl_exception')

        fill_event(e.event,  " while calculating OTL for %s. The file has been moved into the retry folder."
                   % os.path.relpath(crinez, Config.repository_data_in))
        error_handle(cnn, e.event, crinez, retry_folder, filename)

    except pyProducts.pyProductsExceptionUnreasonableDate as e:
        # a bad RINEX file requested an orbit for a date < 0 or > now()
        reject_folder = reject_folder.replace('%reason%', 'bad_rinex')

        fill_event(e.event, " during %s. The file has been moved to the rejected folder. "
                            "Most likely bad RINEX header/data."
                   % os.path.relpath(crinez, Config.repository_data_in))
        error_handle(cnn, e.event, crinez, reject_folder, filename)

    except pyProducts.pyProductsException as e:

        # if PPP fails and ArchiveService tries to run sh_rnx2apr
        # and it doesn't find the orbits, send to retry
        retry_folder = retry_folder.replace('%reason%', 'sp3_exception')

        fill_event(e.event, ": %s. Check the brdc/sp3/clk files and also check that the RINEX data is not corrupt."
                   % os.path.relpath(crinez, Config.repository_data_in))

        error_handle(cnn, e.event, crinez, retry_folder, filename)

    except dbConnection.dbErrInsert as e:

        reject_folder = reject_folder.replace('%reason%', 'duplicate_insert')

        # insert duplicate values: two parallel processes tried
        # to insert different filenames (or the same) of the same station
        # to the db: move it to the rejected folder.
        # The user might want to retry later. Log it in events
        # this case should be very rare
        event = pyEvents.Event(
            Description='Duplicate rinex insertion attempted while ' +
            'processing ' + os.path.relpath(crinez, Config.repository_data_in) +
            ' : (file moved to rejected folder)\n' + str(e),
            EventType='warn')
        fill_event(event)
        error_handle(cnn, event, crinez, reject_folder, filename)

    except Exception:

        retry_folder = retry_folder.replace('%reason%', 'general_exception')

        event = pyEvents.Event(
            Description=traceback.format_exc() + ' processing: ' +
            os.path.relpath(crinez, Config.repository_data_in) + ' in node ' +
            platform.node() + ' (file moved to retry folder)',
            EventType='error')

        error_handle(cnn, event, crinez, retry_folder,
                     filename, no_db_log=True)
        cnn.close()

        return event['Description'], None, None

    cnn.close()
    return None, None, imported_station


def process_rinex_qc(JobServer):
    """
    RinexQC hook: after the main archiving pass, process any rinex row
    that doesn't yet have a QC run under RinexQC's current default
    config. Deliberately decoupled from the per-file archiving job
    (process_crinex_file) -- a QC failure, or RINGO's own runtime, never
    affects whether a file gets archived. See
    knowledge/architecture/rinex-qc.md. Reuses the JobServer this run
    already built (create_cluster() can be called again on the same
    JobServer after the main pass' close_cluster(), same as
    process_visits()'s two clusters above).
    """
    # local import: keeps everything rinex_qc pulls in out of
    # ArchiveService's import graph for anyone running with
    # --no_rinex_qc, or on an install that hasn't installed the ringo
    # binary yet (scripts/install_ringo.sh).
    from geode.rinex_qc import batch
    from geode.rinex_qc.config import RinexQCConfig

    cnn = dbConnection.Cnn('gnss_data.cfg')
    config = RinexQCConfig.load()

    candidates = batch.find_pending(cnn, config, newest_first=False)

    if not candidates:
        tqdm.write(' >> RinexQC: nothing pending')
        return

    tqdm.write(' >> RinexQC: %i file(s) pending' % len(candidates))
    batch.process_batch(JobServer, candidates, config, desc='RinexQC')


def remove_empty_folders(folder):
    # Listing the files
    for dirpath, _, files in os.walk(folder, topdown=False):
        for file in files:
            if file.endswith('DS_Store'):
                # delete the stupid mac files
                file_try_remove(os.path.join(dirpath, file))

        if dirpath == folder:
            break

        dir_try_remove(dirpath, recursive=False)


def print_archive_service_summary():

    global cnn
    global station_import_counts

    # find the last event in the executions table
    exec_date = cnn.query_float(
        '''SELECT max(exec_date) as mx FROM executions
          WHERE script = \'ArchiveService.py\'''')

    info = cnn.query_float(
        '''SELECT count(*) as cc FROM events
        WHERE "EventDate" >= \'%s\' AND "EventType" = \'info\''''
        % exec_date[0][0])

    erro = cnn.query_float(
        '''SELECT count(*) as cc FROM events
        WHERE "EventDate" >= \'%s\' AND "EventType" = \'error\''''
        % exec_date[0][0])

    warn = cnn.query_float(
        '''SELECT count(*) as cc FROM events
        WHERE "EventDate" >= \'%s\' AND "EventType" = \'warn\''''
        % exec_date[0][0])

    print(' >> Summary of events for this run:')
    print(' -- info    : %i' % info[0][0])
    print(' -- errors  : %i' % erro[0][0])
    print(' -- warnings: %i' % warn[0][0])

    if station_import_counts:
        total = sum(station_import_counts.values())
        print('\n >> Files imported per station (%i total):' % total)
        print(' -- %-24s %s' % ('Station', 'Files'))
        print(' -- ' + '-' * 32)
        for stn, count in sorted(station_import_counts.items(),
                                  key=lambda x: (-x[1], x[0])):
            print(' -- %-24s %i' % (stn, count))
    else:
        print('\n >> No files were imported in this run.')


def process_visit_file(Config, record):

    # import raw file processing functions

    try:
        cnn = dbConnection.Cnn('gnss_data.cfg')
        data_in = os.path.join(Config.repository, 'data_in/%s' % stationID(record))

        # get a hold of the file and make sure it exists
        filename = os.path.join(Config.media, record['file'])

        convert = ConvertRaw.ConvertRaw(record['StationCode'], filename, data_in)

        result = convert.process_files()

        if result:
            event = pyEvents.Event(NetworkCode=record['NetworkCode'],
                                   StationCode=record['StationCode'],
                                   Description='Visit GNSS file %s has been converted to '
                                               'RINEX and left in the repository'
                                               % record['filename'])
            cnn.insert_event(event)
            # mark the file as done
            cnn.update('api_visitgnssdatafiles', {'rinexed': True}, id=record['file_id'])
        else:
            event = pyEvents.Event(NetworkCode=record['NetworkCode'],
                                   StationCode=record['StationCode'],
                                   EventType='warn',
                                   Description='Visit GNSS file %s could not be converted to '
                                               'RINEX. Log from ConvertRaw follows:\n%s'
                                               % (record['filename'],
                                                  '\n'.join(['- ' + str(event) for event in convert.logger])))
            cnn.insert_event(event)

        # return '', False to use the same callback_handle
        return None, False
    except Exception as e:
        return str(e), False


def merge_rinex_files(Config, record):

    # import raw file processing functions

    try:
        data_in = os.path.join(Config.repository, 'data_in/%s' % stationID(record))

        # invoke the convert object with no path_to_raw: will be used just to merge
        convert = ConvertRaw.ConvertRaw(record['StationCode'], '', data_in)
        # loops through the folder attempting to merge RINEX files from the same day, same interval
        convert.merge_rinex()

        # return '', False to use the same callback_handle
        return None, False
    except Exception as e:
        return str(e), False


def process_visits(JobServer):
    # function to process visit files and add them to the repository

    cnn = dbConnection.Cnn("gnss_data.cfg")

    Config = pyOptions.ReadOptions("gnss_data.cfg")
    data_in = os.path.join(Config.repository, 'data_in')

    # get stations with visit files that require conversion
    stns = cnn.query_float("""SELECT "NetworkCode", "StationCode" FROM api_visitgnssdatafiles 
                                         LEFT JOIN api_visits ON visit_id = api_visits.id 
                                         LEFT JOIN stations   ON station_id = stations.api_id
                                         WHERE rinexed = False GROUP BY "NetworkCode", "StationCode"
                                         """, as_dict=True)

    # now get visit files
    rs = cnn.query_float("""SELECT api_visitgnssdatafiles.id as file_id, * FROM api_visitgnssdatafiles 
                                     LEFT JOIN api_visits ON visit_id = api_visits.id 
                                     LEFT JOIN stations   ON station_id = stations.api_id 
                                     WHERE rinexed = False
                                     """, as_dict=True)

    # create the folders to avoid racing condition
    tqdm.write(' >> Creating station directories in data_in')
    for stn in tqdm(stns, ncols=160):
        folder = os.path.join(data_in, stationID(stn))
        if not os.path.exists(folder):
            os.makedirs(folder)

    pbar = tqdm(desc='%-30s' % ' >> Processing visits',
                total=len(rs), ncols=160, disable=None)

    # dependency functions
    depfuncs = (get_field_or_attr, stationID)

    # import modules
    JobServer.create_cluster(process_visit_file, depfuncs,
                             callback_handle, pbar,
                             modules=('geode.pyRinex',
                                      'geode.ConvertRaw',
                                      'geode.pyEvents',
                                      'geode.dbConnection',
                                      'geode.pyRunWithRetry',
                                      'geode.Utils',
                                      'geode.pyRinexName',
                                      'platform', 'os'))

    for record in rs:
        JobServer.submit(Config, record)

    JobServer.wait()
    pbar.close()

    tqdm.write(' >> Done processing visits')
    JobServer.close_cluster()

    # =============================================================
    # now try to merge RINEX files from the same day, same interval

    pbar = tqdm(desc='%-30s' % ' >> Merging RINEX files (same day, same interval, may take a while!)',
                total=len(stns), ncols=160, disable=None)

    # import modules
    JobServer.create_cluster(merge_rinex_files, depfuncs,
                             callback_handle, pbar,
                             modules=('geode.pyRinex',
                                      'geode.ConvertRaw',
                                      'geode.pyEvents',
                                      'geode.dbConnection',
                                      'geode.pyRunWithRetry',
                                      'geode.Utils',
                                      'geode.pyRinexName',
                                      'platform', 'os'))
    for record in stns:
        JobServer.submit(Config, record)

    JobServer.wait()
    pbar.close()

    tqdm.write(' >> Done merging RINEX files')
    JobServer.close_cluster()


def main():

    # put connection and config in global variable
    #  to use inside callback_handle
    global cnn
    global repository_data_in

    # bind to the repository directory
    parser = argparse.ArgumentParser(description='Archive operations Main Program')

    parser.add_argument('-purge', '--purge_locks', action='store_true',
                        help="""Delete any network starting with '?'
                             from the stations table and purge the contents of
                             the locks table, deleting
                             the associated files from data_in.""")

    parser.add_argument('-visits', '--process_visits', action='store_true', default=False,
                        help="Check and convert GNSS visit files to RINEX.")

    parser.add_argument('-np', '--noparallel', action='store_true',
                        help="Execute command without parallelization.")

    parser.add_argument('-no_qc', '--no_rinex_qc', action='store_true',
                        help="Skip the RinexQC pass at the end of this run (runs by default -- "
                             "see com/RinexQC.py). QC failures never block archiving regardless; "
                             "this flag only controls whether the pass runs at all.")

    add_version_argument(parser)

    args = parser.parse_args()

    Config = pyOptions.ReadOptions('gnss_data.cfg')

    repository_data_in = Config.repository_data_in

    if not os.path.isdir(Config.repository):
        print(" >> the provided repository path in gnss_data.cfg is not a folder")
        exit()

    JobServer = pyJobServer.JobServer(Config,
                                      run_parallel=not args.noparallel,
                                      software_sync=[Config.options['ppp_remote_local']])
    # type: pyJobServer.JobServer

    # create the execution log
    cnn.insert('executions', script='ArchiveService.py')

    # check that media folder is accessible
    if args.process_visits:
        if not os.path.isdir(Config.media):
            print(" >> the provided media path in gnss_data.cfg is not accessible")
            exit()

        process_visits(JobServer)

    # set the data_xx directories
    data_in = os.path.join(Config.repository, 'data_in')
    data_in_retry = os.path.join(Config.repository, 'data_in_retry')
    data_reject = os.path.join(Config.repository, 'data_rejected')

    # if if the subdirs exist
    for path in (data_in, data_in_retry, data_reject):
        if not os.path.isdir(path):
            os.makedirs(path)

    # delete any locks with a NetworkCode != '?%'
    cnn.query('DELETE FROM locks WHERE "NetworkCode" NOT LIKE \'?%\'')
    # get the locks to avoid reprocessing files
    #  that had no metadata in the database
    locks = cnn.query('SELECT * FROM locks').dictresult()

    if args.purge_locks:
        # first, delete all associated files
        for lock in tqdm(locks, ncols=160, unit='crz',
                         desc='%-30s' % ' >> Purging locks', disable=None):
            file_try_remove(os.path.join(Config.repository_data_in,
                                         lock['filename']))

        # purge the contents of stations.
        #  This will automatically purge the locks table
        cnn.query('delete from stations where "NetworkCode" like \'?%\'')
        # purge the networks
        cnn.query('delete from networks where "NetworkCode" like \'?%\'')
        # purge the locks already taken care of (just in case)
        cnn.query('delete from locks where "NetworkCode" not like \'?%\'')
        # get the locks to avoid reprocessing files
        # that had no metadata in the database
        locks = cnn.query('SELECT * FROM locks').dictresult()

    # look for data in the data_in_retry and move it to data_in

    archive = pyArchiveStruct.RinexStruct(cnn)

    pbar = tqdm(desc='%-30s' % ' >> Scanning data_in_retry',
                ncols=160, unit='crz', disable=None)

    rfiles, paths, _ = archive.scan_archive_struct(data_in_retry, pbar)

    pbar.close()

    pbar = tqdm(desc='%-30s' % ' -- Moving files to data_in',
                total=len(rfiles), ncols=160, unit='crz', disable=None)

    for rfile, path in zip(rfiles, paths):

        dest_file = os.path.join(data_in, rfile)

        # move the file into the folder
        Utils.move(path, dest_file)

        pbar.set_postfix(crinez=rfile)
        pbar.update()

        # remove folder from data_in_retry (also removes the log file)
        # remove the log file that accompanies this CRINEZ file
        file_try_remove(
            pyRinexName.RinexNameFormat(path).filename_no_ext() + '.log')

    pbar.close()
    tqdm.write(' -- Cleaning data_in_retry')
    remove_empty_folders(data_in_retry)

    # take a break to allow the FS to finish the task
    time.sleep(5)

    files_list = []

    pbar = tqdm(desc='%-30s' % ' >> Repository CRINEZ scan', ncols=160,
                disable=None)

    rpaths, _, files = archive.scan_archive_struct(data_in, pbar)

    pbar.close()

    pbar = tqdm(desc='%-30s' % ' -- Checking the locks table',
                total=len(files), ncols=130, unit='crz', disable=None)

    locks = set(lock['filename'] for lock in locks)
    for file, path in zip(files, rpaths):
        pbar.set_postfix(crinez=file)
        pbar.update()
        if path not in locks:
            files_list.append((path, file))

    pbar.close()

    tqdm.write(" -- Found %i files in the lock list..." % len(locks))
    tqdm.write(" -- Found %i files (matching RINEX 2/3 format) to process..." % len(files_list))

    pbar = tqdm(desc='%-30s' % ' >> Processing repository',
                total=len(files_list), ncols=160, unit='crz',
                disable=None)

    # dependency functions
    depfuncs = (check_rinex_timespan_int, write_error, error_handle,
                insert_data, verify_rinex_multiday, file_append,
                file_try_remove, file_open)

    # import modules
    JobServer.create_cluster(process_crinex_file,
                             depfuncs,
                             callback_handle,
                             pbar,
                             modules=('geode.pyRinex',
                                      'geode.pyArchiveStruct',
                                      'geode.pyOTL', 'geode.metadata.station_info',
                                      'geode.dbConnection', 'geode.Utils',
                                      'geode.pyDate', 'geode.pyProducts',
                                      'geode.pyOptions', 'geode.pyEvents',
                                      'geode.pyRinexName',
                                      'geode.pyPPP', 'os', 'uuid',
                                      'datetime', 'numpy', 'traceback',
                                      'platform'))

    for file_to_process, sfile in files_list:
        JobServer.submit(file_to_process, sfile, data_reject, data_in_retry)

    JobServer.wait()

    pbar.close()

    JobServer.close_cluster()

    print_archive_service_summary()

    if not args.no_rinex_qc:
        process_rinex_qc(JobServer)

    # iterate to delete empty folders
    remove_empty_folders(data_in)


if __name__ == '__main__':
    main()
