"""
Project: Geodetic Database Engine (GeoDE)
Date: 02/16/2017
Author: Demian D. Gomez

This class handles the interface between the directory structure of the rinex archive and the databased records.
It can be used to retrieve a rinex path based on a rinex database record
It can also scan the dirs of a supplied path for d.Z and station.info files (the directories and files have to match the
declared directory structure and {stmn}{doy}{session}.{year}d.Z, respectively)
"""

import os
import sys
import re
import shutil

# deps
import scandir
from tqdm import tqdm

# app
from . import dbConnection
from . import pyDate
from . import pyOptions
from . import pyEvents
from . import Utils
from . import pyRinex
from . import pyRinexName
from .pyRinexName import RinexNameFormat
from .Utils import file_try_remove


class RinexStruct(object):

    def __init__(self, cnn, path_cfg=''):

        self.cnn = cnn
        self.archiveroot = None

        # read the structure definition table
        self.levels = cnn.query('SELECT rinex_tank_struct.*, keys.* FROM rinex_tank_struct '
                                'LEFT JOIN keys ON keys."KeyCode" = rinex_tank_struct."KeyCode" '
                                'ORDER BY "Level"').dictresult()

        self.keys     = cnn.query('SELECT * FROM keys').dictresult()
        # read the station and network tables
        # self.networks = cnn.query('SELECT * FROM networks').dictresult()
        # self.stations = cnn.query('SELECT * FROM stations').dictresult()

        self.Config = pyOptions.ReadOptions(os.path.join(path_cfg, 'gnss_data.cfg'))

    def insert_rinex(self, record=None, rinexobj=None):
        """
        Insert a RINEX record and file into the database and archive. If only record is provided, only insert into db
        If only rinexobj is provided, then RinexRecord of rinexobj is used for the insert. If both are given, then
        RinexRecord overrides the passed record.
        :param record: a RinexRecord dictionary to make the insert to the db
        :param rinexobj: the pyRinex object containing the file being processed
        :param rnxaction: accion to perform to rinexobj.
        :return: True if insertion was successful. False if no insertion was done.
        """

        if record is None and rinexobj is None:
            raise ValueError('insert_rinex exception: both record and rinexobj cannot be None.')

        if rinexobj is not None:
            record = rinexobj.record

        copy_succeeded  = False
        archived_crinex = ''

        # check if record exists in the database
        if not self.get_rinex_record(NetworkCode     = record['NetworkCode'],
                                     StationCode     = record['StationCode'],
                                     ObservationYear = record['ObservationYear'],
                                     ObservationDOY  = record['ObservationDOY'],
                                     Interval        = record['Interval'],
                                     Completion      = float('%.3f' % record['Completion'])):
            # no record, proceed

            # check if we need to perform any rinex operations. We might be inserting a new record, but it may just be
            # a ScanRinex op where we don't copy the file into the archive
            if rinexobj is not None:
                # is the rinex object correctly named?
                rinexobj.apply_file_naming_convention()
                # update the record to the (possible) new name
                record['Filename'] = rinexobj.rinex

            self.cnn.begin_transac()

            try:
                self.cnn.insert('rinex', **record)

                if rinexobj is not None:
                    # a rinexobj was passed, copy it into the archive.

                    path2archive = os.path.join(self.Config.archive_path,
                                                self.build_rinex_path(record['NetworkCode'],
                                                                      record['StationCode'],
                                                                      record['ObservationYear'],
                                                                      record['ObservationDOY'],
                                                                      with_filename = False,
                                                                      rinexobj = rinexobj))

                    # copy fixed version into the archive (in case another session exists for RINEX v2)
                    archived_crinex = rinexobj.compress_local_copyto(path2archive)
                    copy_succeeded = True
                    # get the rinex filename to update the database
                    rnx = RinexNameFormat(archived_crinex).to_rinex_format(pyRinexName.TYPE_RINEX, no_path=True)

                    if rnx != rinexobj.rinex:
                        # update the table with the filename (always force with step)
                        self.cnn.query('UPDATE rinex SET "Filename" = \'%s\' '
                                       'WHERE "NetworkCode" = \'%s\' '
                                       'AND "StationCode" = \'%s\' '
                                       'AND "ObservationYear" = %i '
                                       'AND "ObservationDOY" = %i '
                                       'AND "Interval" = %i '
                                       'AND "Completion" = %.3f '
                                       'AND "Filename" = \'%s\'' %
                                       (rnx,
                                        record['NetworkCode'],
                                        record['StationCode'],
                                        record['ObservationYear'],
                                        record['ObservationDOY'],
                                        record['Interval'],
                                        record['Completion'],
                                        record['Filename']))

                    event = pyEvents.Event(Description = 'A new RINEX was added to the archive, filename %s, interval '
                                                         '%i, and completion %.3f'
                                                         % (record['Filename'],
                                                            record['Interval'],
                                                            record['Completion']),
                                           NetworkCode = record['NetworkCode'],
                                           StationCode = record['StationCode'],
                                           Year        = record['ObservationYear'],
                                           DOY         = record['ObservationDOY'])
                else:
                    event = pyEvents.Event(Description = 'Archived CRINEX file %s added to the database interval '
                                                         '%i, and completion %.3f'
                                                         % (record['Filename'],
                                                            record['Interval'],
                                                            record['Completion']),
                                           NetworkCode = record['NetworkCode'],
                                           StationCode = record['StationCode'],
                                           Year        = record['ObservationYear'],
                                           DOY         = record['ObservationDOY'])

                self.cnn.insert_event(event)

            except:
                self.cnn.rollback_transac()

                if rinexobj and copy_succeeded:
                    # transaction rolled back due to error. If file made into the archive, delete it.
                    os.remove(archived_crinex)

                raise

            self.cnn.commit_transac()

            return True
        else:
            # record already existed
            return False

    def remove_rinex(self, record, move_to_dir=None):
        # function to remove a file from the archive
        # should receive a rinex record
        # if move_to is None, file is deleted
        # otherwise, moves file to specified location
        try:
            self.cnn.begin_transac()
            # propagate the deletes
            # check if this rinex file is the file that was processed and used for solutions
            where_station = '"NetworkCode" = \'%s\' AND "StationCode" = \'%s\'' % (record['NetworkCode'], record['StationCode'])
            rs = self.cnn.query(
                    'SELECT * FROM rinex_proc WHERE %s AND "ObservationYear" = %i AND "ObservationDOY" = %i'
                    % (where_station,
                       record['ObservationYear'],
                       record['ObservationDOY']))

            if rs.ntuples() > 0:
                self.cnn.query(
                    'DELETE FROM gamit_soln WHERE %s AND "Year" = %i AND "DOY" = %i'
                    % (where_station,
                       record['ObservationYear'], record['ObservationDOY']))

                self.cnn.query(
                    'DELETE FROM ppp_soln WHERE %s AND "Year" = %i AND "DOY" = %i'
                    % (where_station,
                       record['ObservationYear'], record['ObservationDOY']))

            # get the filename
            rinex_path = self.build_rinex_path(record['NetworkCode'], record['StationCode'],
                                               record['ObservationYear'], record['ObservationDOY'],
                                               filename=record['Filename'])

            rinex_path = os.path.join(self.Config.archive_path, rinex_path)

            # delete the rinex record
            self.cnn.query(
                'DELETE FROM rinex WHERE %s AND "ObservationYear" = %i AND "ObservationDOY" = %i AND "Filename" = \'%s\''
                % (where_station,
                   record['ObservationYear'],
                   record['ObservationDOY'], record['Filename']))

            if os.path.isfile(rinex_path):
                if move_to_dir:

                    filename = Utils.move(rinex_path,
                                          os.path.join(move_to_dir, os.path.basename(rinex_path)))
                    description = 'RINEX %s was removed from the database and archive. ' \
                                  'File moved to %s. See next events for reason.' % (record['Filename'], filename)
                else:

                    os.remove(rinex_path)
                    description = 'RINEX %s was removed from the database and archive. ' \
                                  'File was deleted. See next events for reason.' % (record['Filename'])

            else:
                description = 'RINEX %s was removed from the database and archive. File was NOT found in the archive ' \
                              'so no deletion was performed. See next events for reason.' % (record['Filename'])

            # insert an event
            event = pyEvents.Event(Description = description,
                                   NetworkCode = record['NetworkCode'],
                                   StationCode = record['StationCode'],
                                   EventType   = 'info',
                                   Year        = record['ObservationYear'],
                                   DOY         = record['ObservationDOY'])

            self.cnn.insert_event(event)

            self.cnn.commit_transac()
        except:
            self.cnn.rollback_transac()
            raise

    def check_rinex_files(self, NetworkCode, StationCode, start_date, end_date, fix=False):
        """
        Looks for the rinex records of a station that have no file in the archive.
        :param NetworkCode: NetworkCode of the station
        :param StationCode: StationCode of the station
        :param start_date: pyDate.Date of the first day to check
        :param end_date: pyDate.Date of the last day to check
        :param fix: if set, removes the records (and their solutions) whose file is missing and inserts an event
        :return: list with the paths of the files not found in the archive
        """
        rs = self.cnn.query('SELECT * FROM rinex WHERE "NetworkCode" = \'%s\' AND '
                            '"StationCode" = \'%s\' AND '
                            '"ObservationSTime" BETWEEN \'%s\' AND \'%s\' '
                            'ORDER BY "ObservationSTime"'
                            % (NetworkCode, StationCode, start_date.yyyymmdd(), end_date.yyyymmdd()))

        missing_files = []

        for rnx in rs.dictresult():

            crinex_path = os.path.join(self.Config.archive_path,
                                       self.build_rinex_path(NetworkCode, StationCode,
                                                             rnx['ObservationYear'], rnx['ObservationDOY'],
                                                             filename=rnx['Filename']))

            if not os.path.exists(crinex_path):
                missing_files.append(crinex_path)

                if fix:
                    self.remove_rinex(rnx)

                    event = pyEvents.Event(
                        Description=('A missing RINEX file was found during RINEX integrity check: ' + crinex_path +
                                     '. It has been removed from the database. Consider rerunning PPP for this '
                                     'station.'),
                        NetworkCode=NetworkCode,
                        StationCode=StationCode,
                        Year=rnx['ObservationYear'],
                        DOY=rnx['ObservationDOY'])

                    self.cnn.insert_event(event)

        return missing_files

    def delete_rinex(self, stnlist, start_date, end_date, completion_limit=0.0):
        """
        Deletes the rinex files (and the PPP and GAMIT solutions that depend on them) of a list of stations.
        :param stnlist: list of dictionaries with NetworkCode and StationCode
        :param start_date: pyDate.Date of the first day to delete
        :param end_date: pyDate.Date of the last day to delete
        :param completion_limit: only the rinex with Completion <= completion_limit are deleted (1.0: all)
        """
        for stn in stnlist:
            NetworkCode = stn['NetworkCode']
            StationCode = stn['StationCode']

            rs = self.cnn.query(
                'SELECT * FROM rinex WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                'AND "ObservationFYear" BETWEEN %f AND %f AND "Completion" <= %f' %
                (NetworkCode, StationCode, start_date.fyear, end_date.fyear, completion_limit))

            rinex = rs.dictresult()
            tqdm.write(' >> Deleting %i RINEX files and solutions for %s.%s between %s - %s and completion <= %.3f' %
                       (len(rinex), NetworkCode, StationCode, start_date.yyyyddd(), end_date.yyyyddd(),
                        completion_limit))

            for rnx in tqdm(rinex):
                try:
                    # delete rinex file (also deletes PPP and GAMIT solutions)
                    self.remove_rinex(rnx)

                except dbConnection.dbErrDelete as e:
                    tqdm.write('Failed to delete solutions and/or RINEX files for %i %i. Reason: %s\n' %
                               (rnx['ObservationYear'], rnx['ObservationDOY'], str(e)))

    def rename_station(self, NetworkCode, StationCode, DestNetworkCode, DestStationCode, start_date, end_date,
                       delete_if_empty=True, dry_run=False):
        """
        Renames (merges) a station into another one: moves its rinex files in the archive and all its data in the
        database. The work is done by pyStationMerge.StationMerge, this only reports the progress.
        :param delete_if_empty: delete the origin station if it is left without RINEX files
        :param dry_run: only print what would be done, without changing anything
        """
        # imported here because pyStationMerge uses this module
        from .pyStationMerge import StationMerge, StationMergeException, format_report

        try:
            merge = StationMerge(self.cnn, (NetworkCode, StationCode), (DestNetworkCode, DestStationCode),
                                 start_date, end_date, delete_if_empty, archive=self)

            previewed = format_report(merge.preview())
            tqdm.write('\n'.join(previewed))

            if not dry_run:
                # the preview was already shown: only what the execution adds (warnings, result)
                tqdm.write('\n'.join(line for line in format_report(merge.execute()) if line not in previewed))

        except StationMergeException as e:
            tqdm.write(' -- ' + str(e))

    def get_rinex_record(self, **kwargs):
        """
        Retrieve a single or multiple records from the rinex table given a set parameters. If parameters are left empty,
        it wil return all records matching the specified criteria. Each parameter acts like a filter, narrowing down the
        records returned by the function. The default behavior is to use tables rinex or rinex_proc depending on the
        provided parameters. E.g. if Interval, Completion and Filename are all left blank, the function will return the
        records using rinex_proc. Otherwise, the rinex table will be used.
        :param NetworkCode: filter
        :param StationCode: filter
        :param ObservationYear: filter
        :param ObservationDOY: filter
        :param Interval: filter
        :param Completion: filter
        :param Filename: filter
        :return: a dictionary will the records matching the provided parameters
        """

        if any(param in ('Interval', 'Completion', 'Filename') for param in kwargs.keys()):
            table = 'rinex'
        else:
            table = 'rinex_proc'

        # get table fields
        fields = self.cnn.get_columns(table)
        psql = []

        # parse args
        for key in kwargs:

            if key not in fields.keys():
                raise ValueError('Parameter ' + key + ' is not a field in table ' + table)

            elif key != 'ObservationFYear':
                # avoid FYear due to round off problems
                arg = kwargs[key]

                if 'character' in fields[key]:
                    psql += ['"%s" = \'%s\'' % (key, arg)]

                elif 'numeric' in fields[key]:
                    psql += ['"%s" = %f' % (key, arg)]

        sql = 'SELECT * FROM %s ' % table
        if psql:
            sql += 'WHERE ' + ' AND '.join(psql)

        return self.cnn.query(sql).dictresult()

    def scan_archive_struct(self, rootdir, progress_bar=None):
        self.archiveroot = rootdir

        rnx      = []
        path2rnx = []
        fls      = []
        for path, _, files in scandir.walk(rootdir):
            for file in files:
                file_path = os.path.join(path, file)
                crinex    = file_path.rsplit(rootdir + '/')[1]
                if progress_bar is not None:
                    progress_bar.set_postfix(crinex = crinex)
                    progress_bar.update()

                try:
                    RinexNameFormat(file)  # except if invalid
                    # only add valid rinex files (now allows the full range)
                    fls.append(file)
                    rnx.append(crinex)
                    path2rnx.append(file_path)

                except pyRinexName.RinexNameException:
                    if file.endswith('DS_Store') or file.startswith('._'):
                        # delete the stupid mac files
                        file_try_remove(file_path)

        return rnx, path2rnx, fls

    def scan_archive_struct_stninfo(self, rootdir):

        # same as scan archive struct but looks for station info files
        self.archiveroot = rootdir

        stninfo      = []
        path2stninfo = []
        for path, dirs, files in scandir.walk(rootdir):
            for file in files:
                file_path = os.path.join(path, file)
                if file.endswith(".info"):
                    # only add valid rinex compressed files
                    stninfo.append(file_path.rsplit(rootdir+'/')[1])
                    path2stninfo.append(file_path)
                elif file.endswith('DS_Store') or file.startswith('._'):
                    # delete the stupid mac files
                    file_try_remove(file_path)

        return stninfo, path2stninfo

    def archive_folder(self, record):
        """
        Folder of the archive (relative to the archive root) where the rinex of a rinex record is stored.
        :param record: a dictionary with at least the columns used as levels of the archive (rinex_tank_struct)
        :return: the relative folder, without the filename
        """
        return "/".join('{key:0{width}{type}}'.format(key=record[level['rinex_col_in']],
                                                      width=level['TotalChars'],
                                                      type='.0f' if level['isnumeric'] == '1' else 's')
                        for level in self.levels)

    def rename_rinex_paths(self, record, DestNetworkCode, DestStationCode):
        """
        Paths involved in renaming a rinex record to another station. Reads nothing from the database and
        does not touch the archive.
        :param record: a rinex record (dictionary) of the origin station
        :param DestNetworkCode: NetworkCode of the destiny station
        :param DestStationCode: StationCode of the destiny station
        :return: (source path, destiny path, new Filename for the rinex table). Both paths are absolute and
                 point to the CRINEZ of the archive
        """
        src_name = RinexNameFormat(record['Filename'])
        dst_name = RinexNameFormat(record['Filename'])
        dst_name.StationCode = DestStationCode

        dest_record = dict(record, NetworkCode=DestNetworkCode, StationCode=DestStationCode)

        src_path = os.path.join(self.Config.archive_path, self.archive_folder(record),
                                src_name.to_rinex_format(pyRinexName.TYPE_CRINEZ, no_path=True))
        dst_path = os.path.join(self.Config.archive_path, self.archive_folder(dest_record),
                                dst_name.to_rinex_format(pyRinexName.TYPE_CRINEZ, no_path=True))

        return src_path, dst_path, dst_name.to_rinex_format(pyRinexName.TYPE_RINEX, no_path=True)

    def rename_rinex(self, record, DestNetworkCode, DestStationCode):
        """
        Renames a rinex of the archive to another station: updates NetworkCode, StationCode and Filename of the
        rinex table and moves the file in the archive. It does not manage the transaction nor check collisions,
        that is up to the caller.
        :param record: a rinex record (dictionary) of the origin station
        :param DestNetworkCode: NetworkCode of the destiny station
        :param DestStationCode: StationCode of the destiny station
        :return: (source path, destiny path) of the file moved, so the caller can undo the move
        """
        src_path, dst_path, db_filename = self.rename_rinex_paths(record, DestNetworkCode, DestStationCode)

        self.cnn.query('UPDATE rinex SET "NetworkCode" = %s, "StationCode" = %s, "Filename" = %s '
                       'WHERE api_id = %s', (DestNetworkCode, DestStationCode, db_filename, record['api_id']))

        if not os.path.isdir(os.path.dirname(dst_path)):
            os.makedirs(os.path.dirname(dst_path))

        shutil.move(src_path, dst_path)

        return src_path, dst_path

    def build_rinex_path(self, NetworkCode, StationCode, ObservationYear, ObservationDOY,
                         with_filename=True, filename=None, rinexobj=None):
        """
        Function to get the location in the archive of a rinex file. It has two modes of operation:
        1) retrieve an existing rinex file, either specific or the rinex for processing
        (most complete, largest interval) or a specific rinex file (already existing in the rinex table).
        2) To get the location of a potential file (probably used for injecting a new file in the archive. No this mode,
        filename has no effect.
        :param NetworkCode: NetworkCode of the station being retrieved
        :param StationCode: StationCode of the station being retrieved
        :param ObservationYear: Year of the rinex file being retrieved
        :param ObservationDOY: DOY of the rinex file being retrieved
        :param with_filename: if set, returns a path including the filename. Otherwise, just returns the path
        :param filename: name of a specific file to search in the rinex table
        :param rinexobj: a pyRinex object to pull the information from (to fill the achive keys).
        :return: a path with or without filename
        """
        if not rinexobj:
            # not an insertion (user wants the rinex path of existing file)
            # build the levels struct
            sql_string = ", ".join(['"' + level['rinex_col_in'] + '"'
                                    for level in self.levels] + ['"Filename"'])

            if filename:
                filename = RinexNameFormat(filename).to_rinex_format(pyRinexName.TYPE_RINEX)

                # if filename is set, user requesting a specific file: query rinex table
                rs = self.cnn.query('SELECT ' + sql_string + ' FROM rinex WHERE "NetworkCode" = \'' +
                                    NetworkCode + '\' AND "StationCode" = \'' + StationCode +
                                    '\' AND "ObservationYear" = ' + str(ObservationYear) + ' AND "ObservationDOY" = ' +
                                    str(ObservationDOY) + ' AND "Filename" = \'' + filename + '\'')
            else:
                # if filename is NOT set, user requesting a the processing file: query rinex_proc
                rs = self.cnn.query(
                    'SELECT ' + sql_string + ' FROM rinex_proc WHERE "NetworkCode" = \'' + NetworkCode +
                    '\' AND "StationCode" = \'' + StationCode + '\' AND "ObservationYear" = ' + str(
                        ObservationYear) + ' AND "ObservationDOY" = ' + str(ObservationDOY))

            if not rs.ntuples():
                return None

            field = rs.dictresult()[0]
            path = self.archive_folder(field)

            if with_filename:
                rnx_name = RinexNameFormat(field['Filename'])
                # database stores rinex, we want crinez
                return path + "/" + rnx_name.to_rinex_format(pyRinexName.TYPE_CRINEZ)
            else:
                return path

        else:
            # new file (get the path where it's supposed to go)
            keys = []
            for level in self.levels:
                kk = str(rinexobj.record[level['rinex_col_in']])
                if level['isnumeric'] == '1':
                    kk = kk.zfill(level['TotalChars'])

                if len(kk) != level['TotalChars']:
                    raise ValueError('Invalid record \'%s\' for key \'%s\'' % (kk, level['KeyCode']))

                keys += [kk]

            path = '/'.join(keys)
            crinez_path = os.path.join(path, rinexobj.crinez)
            valid, _ = self.parse_archive_keys(crinez_path, 
                                               tuple(item['KeyCode'] for item in self.levels))
            if not valid:
                raise ValueError('Invalid path result: %s' % path)

            elif with_filename:
                return crinez_path
            else:
                return path

    def parse_archive_keys(self, path_filename, key_filter=()):
        """
        based on a path and filename, this function parses the data and organizes the information in a dictionary
        key_filter allows to select which keys you want to get a hold on. The order of the keys in the path is given
        by the database table rinex_tank_struct
        :param path:
        :param key_filter:
        :return:
        """
        keys_out = {}

        try:
            path     = os.path.dirname(path_filename).split('/')
            filename = os.path.basename(path_filename)

            # check the number of levels in path parts against the number of expected levels
            if len(path) != len(self.levels):
                return False, {}

            # now look in the different levels to match more data (or replace filename keys)
            for key in self.levels:
                path_l = path[key['Level'] - 1]
                if len(path_l) != key['TotalChars']:
                    return False, {}

                keys_out[key['KeyCode']] = int(path_l) if key['isnumeric'] == '1' else path_l.lower()

            if not filename.endswith('.info'):

                fileparts = RinexNameFormat(filename)

                # fill in all the possible keys_out using the crinex file info
                keys_out['station'] = fileparts.StationCode
                keys_out['doy']     = fileparts.date.doy
                keys_out['session'] = fileparts.session
                keys_out['year']    = fileparts.date.year

                # check date is valid and also fill day and month keys_out
                keys_out['day']   = fileparts.date.day
                keys_out['month'] = fileparts.date.month

                return True, {key: keys_out[key] 
                              for key in keys_out.keys()
                              if key in key_filter}

        except:
            return False, {}

