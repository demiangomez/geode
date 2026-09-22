"""
Project: Parallel.Archive
Date: 02/16/2017
Author: Demian D. Gomez
"""

from shutil import copyfile, copy, move, rmtree
import os
import datetime
import uuid
import re
import struct
import json

# app
from .pyEvents import Event
from .Utils import ecef2lla
from .pyRinexName import check_year
from . import pyRinexName
from . import pyDate
from . import pyRunWithRetry
from .metadata.station_info import StationInfo
from . import Utils
from .Utils import (file_open,
                          file_write,
                          file_readlines,
                          struct_unpack,
                          chmod_exec)

TYPE_CRINEZ = 0
TYPE_RINEX  = 1
TYPE_RINEZ  = 2
TYPE_CRINEX = 3
TYPE_CRINEZ_2 = 4


class pyRinexException(Exception):
    def __init__(self, value):
        self.value = value
        self.event = Event(Description=value, EventType='error')

    def __str__(self):
        return str(self.value)


class pyRinexExceptionBadFile     (pyRinexException): pass
class pyRinexExceptionSingleEpoch (pyRinexException): pass
class pyRinexExceptionNoAutoCoord (pyRinexException): pass


class RinexRecord(object):

    def __init__(self, NetworkCode=None, StationCode=None):

        self.StationCode = StationCode
        self.NetworkCode = NetworkCode

        self.header            = None
        self.data              = None
        self.firstObs          = None
        self.datetime_firstObs = None
        self.datetime_lastObs  = None
        self.lastObs           = None
        self.antType           = None
        self.marker_number     = None
        self.marker_name       = StationCode
        self.recType           = None
        self.recNo             = None
        self.recVers           = None
        self.antNo             = None
        self.antDome           = None
        self.antOffset         = None
        self.interval          = None
        self.size              = None
        self.x                 = None
        self.y                 = None
        self.z                 = None
        self.lat               = None
        self.lon               = None
        self.h                 = None
        self.date              = None
        self.rinex             = None
        self.crinez            = None
        self.crinez_path       = None
        self.rinex_path        = None
        self.origin_type       = None
        self.obs_types         = None
        self.observables       = None
        self.system            = None
        self.satsys            = None
        self.no_cleanup        = None
        self.multiday          = False
        self.multiday_rnx_list = []
        self.epochs            = None
        self.completion        = None
        self.rel_completion    = None
        self.rinex_version     = None
        self.min_time_seconds  = 3600

        # log list to append all actions performed to rinex file
        self.log = []

        # list of required header records and a flag to know if they were found or not in the current header
        # also, have a tuple of default values in case there is a missing record
        self.required_records = {'RINEX VERSION / TYPE':
                                     {'format_tuple': ('%9.2f', '%11s', '%1s', '%19s', '%1s', '%19s'),
                                      'found': False,
                                      'default': ('',)},

                                 'PGM / RUN BY / DATE':
                                     {'format_tuple': ('%-20s', '%-20s', '%-20s'),
                                      'found': False,
                                      'default': ('pyRinex: 1.00 000', 'Parallel.PPP', '21FEB17 00:00:00')},

                                 'MARKER NAME':
                                     {'format_tuple': ('%-60s',),
                                      'found': False,
                                      'default': (self.StationCode.upper(),)},

                                 'MARKER NUMBER':
                                     {'format_tuple': ('%-20s',),
                                      'found': False,
                                      'default': (self.StationCode.upper(),)},

                                 'OBSERVER / AGENCY':
                                     {'format_tuple': ('%-20s', '%-40s'),
                                      'found': False,
                                      'default': ('UNKNOWN', 'UNKNOWN')},

                                 'REC # / TYPE / VERS':
                                     {'format_tuple': ('%-20s', '%-20s', '%-20s'),
                                      'found': False,
                                      'default': ('0000000', 'ASHTECH Z-XII3', 'CC00')},

                                 'ANT # / TYPE':
                                     {'format_tuple': ('%-20s', '%-20s'),
                                      'found': False,
                                      'default': ('0000', 'ASH700936C_M SNOW')},

                                 'ANTENNA: DELTA H/E/N':
                                     {'format_tuple': ('%14.4f', '%14.4f', '%14.4f'),
                                      'found': False,
                                      'default': (0.0, 0.0, 0.0)},

                                 'APPROX POSITION XYZ':
                                     {'format_tuple': ('%14.4f', '%14.4f', '%14.4f'),
                                      'found': False,
                                      'default': (0.0, 0.0, 6371000.0)},
                                 # '# / TYPES OF OBSERV' : [('%6i',), False, ('',)],
                                 'TIME OF FIRST OBS':
                                     {'format_tuple': ('%6i', '%6i', '%6i', '%6i', '%6i', '%13.7f', '%8s'),
                                      'found': False,
                                      'default': (1, 1, 1, 1, 1, 0, 'GPS')},
                                 'INTERVAL':
                                     {'format_tuple': ('%10.3f',),
                                      'found': False,
                                      'default': (30,)},  # put a wrong interval when first reading the file so that
                                 # RinSum does not fail to read RINEX if interval record is > 60 chars
                                 # DDG: remove time of last observation all together. It just creates problems and
                                 # is not mandatory
                                 # 'TIME OF LAST OBS'    : [('%6i','%6i','%6i','%6i','%6i','%13.7f','%8s'),
                                 # True, (int(first_obs.year), int(first_obs.month), int(first_obs.day),
                                 # int(23), int(59), float(59), 'GPS')],
                                 'COMMENT':
                                     {'format_tuple': ('%-60s',), 'found': True, 'default': ('',)}}

        fieldnames = ['NetworkCode','StationCode','ObservationYear','ObservationMonth','ObservationDay',
                      'ObservationDOY','ObservationFYear','ObservationSTime','ObservationETime','ReceiverType',
                      'ReceiverSerial','ReceiverFw','AntennaType','AntennaSerial','AntennaDome','Filename','Interval',
                      'AntennaOffset', 'Completion']

        self.record = dict.fromkeys(fieldnames)

    def load_record(self):
        r = self.record
        r['NetworkCode']      = self.NetworkCode
        r['StationCode']      = self.StationCode
        r['ObservationYear']  = self.date.year
        r['ObservationMonth'] = self.date.month
        r['ObservationDay']   = self.date.day
        r['ObservationDOY']   = self.date.doy
        r['ObservationFYear'] = self.date.fyear
        r['ObservationSTime'] = self.firstObs
        r['ObservationETime'] = self.lastObs
        r['ReceiverType']     = self.recType
        r['ReceiverSerial']   = self.recNo
        r['ReceiverFw']       = self.recVers
        r['AntennaType']      = self.antType
        r['AntennaSerial']    = self.antNo
        r['AntennaDome']      = self.antDome
        r['Filename']         = self.rinex
        r['Interval']         = self.interval
        r['AntennaOffset']    = self.antOffset
        r['Completion']       = self.completion


class ReadRinex(RinexRecord):
    def read_fields(self, line, record, format_tuple):
        # create the parser object
        formatstr = re.sub(r'\..', '',
                           ' '.join(format_tuple) \
                           .replace('%', '') \
                           .replace('f', 's') \
                           .replace('i', 's') \
                           .replace('-', ''))

        fs  = struct.Struct(formatstr)

        # get the data section by spliting the line using the record text
        data = line.split(record)[0]

        if len(data) < fs.size:
            # line too short, add padding spaces
            data = ('%-' + str(fs.size) + 's') % line
        elif len(data) > fs.size:
            # line too long! cut
            data = line[0:fs.size]

        fields = struct_unpack(fs, data) 
        
        # convert each element in the list to numbers if necessary, also strip strings
        for i, field in enumerate(fields):
            if 'f' in format_tuple[i]:
                try:
                    fields[i] = float(fields[i])
                except ValueError:
                    # invalid number in the field!, replace with something harmless
                    fields[i] = float(2.11)

            elif 'i' in format_tuple[i]:
                try:
                    fields[i] = int(fields[i])
                except ValueError:
                    # invalid number in the field!, replace with something harmless
                    fields[i] = int(1)

            elif 's' in format_tuple[i]:
                fields[i] = fields[i].strip()

        return fields, data

    def format_record(self, record_dict, record, values):

        if type(values) not in (list, tuple):
            values = [values]

        data = ''.join(record_dict[record]['format_tuple']) % tuple(values)

        if len('%-60s' % data) > 60:
            # field is too long!! cut to 60 chars
            data = data[0:60]
            self.log_event('Found that record data for ' + record +
                           ' was too long (> 60 chars). Replaced with: ' + data)

        return '%-60s' % data + record

    def write_rinex(self, new_header):
        if new_header != self.header:

            self.header = new_header

            # add new header
            rinex = new_header + self.data

            with file_open(self.rinex_path, 'w') as f:
                f.writelines(rinex)

    def read_data(self):
        rinex = file_readlines(self.rinex_path)

        if not any("END OF HEADER" in s for s in rinex):
            raise pyRinexExceptionBadFile('Invalid header: could not find END OF HEADER tag.')

        # find the end of header
        index = [i for i, item in enumerate(rinex) if 'END OF HEADER' in item][0]
        # delete header
        del rinex[0:index + 1]

        self.data = rinex

    def replace_record(self, header, record, new_values):

        if record not in self.required_records.keys():
            raise pyRinexException('Record ' + record + ' not implemented!')

        new_header = []
        for line in header:
            if line.strip().endswith(record):
                new_header += [self.format_record(self.required_records, record, new_values) + '\n']
            else:
                new_header += [line]

        if type(new_values) not in (list, tuple):
            new_values = [new_values]

        self.log_event('RINEX record replaced: ' + record + ' value: ' + ','.join(map(str, new_values)))

        return new_header

    def log_event(self, desc):
        self.log += [Event(StationCode = self.StationCode,
                           NetworkCode = self.NetworkCode,
                           Description = desc)]

    def insert_comment(self, header, comment):

        # remove the end or header
        index = [i for i, item in enumerate(header) if 'END OF HEADER' in item][0]
        del header[index]

        new_header = (header + [self.format_record(self.required_records, 'COMMENT', comment) + '\n'] + \
                      [''.ljust(60, ' ') + 'END OF HEADER\n'])

        self.log_event('RINEX COMMENT inserted: ' + comment)

        return new_header

    def __purge_comments(self, header):
        new_header = [line for line in header if not line.strip().endswith('COMMENT')]

        self.log_event('Purged all COMMENTs from RINEX header.')

        return new_header

    def purge_comments(self):

        new_header = self.__purge_comments(self.header)

        self.write_rinex(new_header)

    def check_interval(self):

        interval_record = {'INTERVAL': {'format_tuple' : ('%10.3f',),
                                        'found'        : False,
                                        'default'      : (30,)}}

        new_header = []

        for line in self.header:

            if line.strip().endswith('INTERVAL'):
                # get the first occurrence only!
                record = [key for key in interval_record.keys() if key in line][0]

                interval_record[record]['found'] = True

                fields, _ = self.read_fields(line, 'INTERVAL', interval_record['INTERVAL']['format_tuple'])

                if fields[0] != self.interval:
                    # interval not equal. Replace record
                    new_header += [self.format_record(interval_record, 'INTERVAL', self.interval) + '\n']
                    self.log_event('Wrong INTERVAL record, setting to %i' % self.interval)
                else:
                    # record matches, leave it untouched
                    new_header += [line]
            else:
                # not a critical field, just put it back in
                if not line.strip().endswith('END OF HEADER'):
                    # leave END OF HEADER until the end to add possible missing records
                    new_header += [line]

        # now check that all the records where included! there's missing ones, then force them
        if not interval_record['INTERVAL']['found']:
            new_header += [self.format_record(interval_record, 'INTERVAL', self.interval) + '\n']
            new_header += [self.format_record(self.required_records, 'COMMENT',
                                              'pyRinex: WARN! added interval to fix file!') + '\n']
            self.log_event('INTERVAL record not found, setting to %i' % self.interval)

        new_header += [''.ljust(60, ' ') + 'END OF HEADER\n']

        self.write_rinex(new_header)

    def check_header(self):

        self.header = self.get_header()
        new_header  = []

        self.system = ''
        first_obs   = None

        for line in self.header:
            # DDG: to avoid problems with accents, and non-UTF-8 chars, remove them
            line = ''.join(i if 32 <= ord(i) < 128 or ord(i) == 10 or ord(i) == 13 else '_' for i in line)

            line_strip = line.strip()

            # line = line.decode('utf-8', 'ignore').encode('utf-8')
            # line = unicodedata.normalize('NFD', unicode(line, 'utf-8')).encode('ascii', 'ignore').decode('utf-8')
            if any(line_strip.endswith(key) for key in self.required_records.keys()):
                # get the first occurrence only!
                record = [key for key in self.required_records.keys() if key in line][0]

                # mark the record as found
                self.required_records[record]['found'] = True

                fields, _ = self.read_fields(line, record, self.required_records[record]['format_tuple'])

                if record == 'RINEX VERSION / TYPE':
                    # read the information about the RINEX type
                    # save the system to use during TIME OF FIRST OBS
                    self.system = fields[4].strip()

                    self.rinex_version = float(fields[0])

                    # now that we know the version, we can get the first obs
                    self.read_data()
                    first_obs = self.get_firstobs()

                    if first_obs is None:
                        raise pyRinexExceptionBadFile(
                            'Could not find a first observation in RINEX file. '
                            'Truncated file? Header follows:\n' + ''.join(self.header))

                    if self.system not in (' ', 'G', 'R', 'S', 'E', 'M'):
                        # assume GPS
                        self.system = 'G'
                        fields[4]   = 'G'
                        self.log_event('System set to (G)PS')

                else:
                    # reformat the header line
                    if record in ('TIME OF FIRST OBS',
                                  'TIME OF LAST OBS'):
                        if self.system == 'M' and not fields[6].strip():
                            fields[6] = 'GPS'
                            self.log_event('Adding TIME SYSTEM to TIME OF FIRST OBS')

                        if first_obs is None:
                            # if header is problematic, put a dummy first observation
                            first_obs = pyDate.Date(year=1990, month=1, day=1, hour=0, minute=0, second=0)

                        # check if the first observation is meaningful or not
                        if record == 'TIME OF FIRST OBS' and (fields[0] != first_obs.year or
                                                              fields[1] != first_obs.month or
                                                              fields[2] != first_obs.day or
                                                              fields[3] != first_obs.hour or
                                                              fields[4] != first_obs.minute or
                                                              fields[5] != first_obs.second):
                            # bad first observation! replace with the real one
                            fields[0] = first_obs.year
                            fields[1] = first_obs.month
                            fields[2] = first_obs.day
                            fields[3] = first_obs.hour
                            fields[4] = first_obs.minute
                            fields[5] = first_obs.second

                            self.log_event('Bad TIME OF FIRST OBS found -> fixed')

                    elif record == 'MARKER NAME':
                        # load the marker name
                        self.marker_name = fields[0].strip().lower()

                # regenerate the fields
                # save to new header
                new_header += [self.format_record(self.required_records, record, fields) + '\n']
            else:
                # not a critical field, just put it back in
                if not line_strip.endswith('END OF HEADER') and \
                   not line_strip.endswith('TIME OF LAST OBS') and \
                   line_strip != '':

                    if line_strip.endswith('COMMENT'):
                        # reformat comments (some come in wrong positions!)
                        fields, _ = self.read_fields(line, 'COMMENT', self.required_records['COMMENT']['format_tuple'])
                        new_header += [self.format_record(self.required_records, 'COMMENT', fields) + '\n']
                    else:
                        # leave END OF HEADER until the end to add possible missing records
                        new_header += [line]

        if self.system == '':
            # if we are out of the loop and we could not determine the system, raise error
            raise pyRinexExceptionBadFile('Unfixable RINEX header: could not find RINEX VERSION / TYPE')

        # now check that all the records where included! there's missing ones, then force them
        if not all(item['found'] for item in self.required_records.values()):
            # get the keys of the missing records
            missing_records = {item: self.required_records[item]
                               for item in self.required_records
                               if self.required_records[item]['found'] is False}

            for record in missing_records.keys():
                if '# / TYPES OF OBSERV' in record:
                    raise pyRinexExceptionBadFile('Unfixable RINEX header: could not find # / TYPES OF OBSERV')

                new_header += [self.format_record(missing_records, record, missing_records[record]['default']) + '\n',
                               self.format_record(self.required_records, 'COMMENT',
                                                  'pyRinex: WARN! default value to fix file!') + '\n']
                self.log_event('Missing required RINEX record added: ' + record)

        new_header += [''.ljust(60, ' ') + 'END OF HEADER\n']

        self.write_rinex(new_header)

    def indentify_file(self, input_file):
        # get the crinez and rinex names
        filename = os.path.basename(input_file)

        self.origin_file = input_file
        self.origin_type = self.rinex_name_format.type
        self.local_copy  = os.path.abspath(os.path.join(self.rootdir, filename))

        self.rinex  = self.rinex_name_format.to_rinex_format(pyRinexName.TYPE_RINEX,  no_path=True)
        self.crinez = self.rinex_name_format.to_rinex_format(pyRinexName.TYPE_CRINEZ, no_path=True)

        # get the paths
        self.crinez_path = os.path.join(self.rootdir, self.crinez)
        self.rinex_path  = os.path.join(self.rootdir, self.rinex)

        self.log_event('Origin type is %i' % self.origin_type)

    def create_temp_dirs(self):

        self.rootdir = os.path.join('production', 'rinex', str(uuid.uuid4()))

        # create a production folder to analyze the rinex file
        if not os.path.exists(self.rootdir):
            os.makedirs(self.rootdir)

    def create_script(self, name, command):
        script_path = os.path.join(self.rootdir, name)
        file_write(script_path, '#! /bin/bash\n' + command + '\n')
        chmod_exec(script_path)

    def uncompress(self):

        # determine compression type, if necessary
        result = os.system('file "%s" | grep -q "Zip"' % self.local_copy)
        if result == 0:
            prg1 = 'unzip -p "%s" ' % self.local_copy
        else:
            result = os.system('file "%s" | grep -q "ASCII"' % self.local_copy)
            if result == 0:
                prg1 = 'cat "%s" ' % self.local_copy
            else:
                prg1 = 'zcat "%s" ' % self.local_copy

        # determine the program to pipe into
        if self.origin_type in (TYPE_CRINEZ, TYPE_CRINEX, TYPE_CRINEZ_2):
            prg2 = ' crx2rnx > "%s"' % self.rinex_path
        else:
            prg2 = ' > "%s"' % self.rinex_path

        # create an uncompression script
        self.create_script('uncompress.sh', prg1 + '|' + prg2)

        # run crz2rnx with timeout structure
        cmd = pyRunWithRetry.RunCommand(os.path.join(self.rootdir, 'uncompress.sh'), 45)
        try:
            _, err = cmd.run_shell()
        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

        # check the size of the file
        size = os.path.getsize(self.local_copy)
        if os.path.isfile(self.rinex_path):
            if err and os.path.getsize(self.rinex_path) < size:
                raise pyRinexExceptionBadFile("Error in ReadRinex.__init__ -- crz2rnx: error and empty file: "
                                              + self.origin_file + ' -> ' + err)
        else:
            raise pyRinexException(
                ('Could not create RINEX file. crx2rnx stderr follows: ' + err) if err else
                'Could not create RINEX file. Unknown reason. Possible problem with crx2rnx?')

    def ConvertRinex(self, to_version):
        # only available to convert from 3 -> 2
        try:
            # most programs still don't support RINEX 3 (partially implemented in this code)
            # convert to RINEX 2.11 using gfzrnx_lx
            cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s -fout %s.t -vo %i -f'
                                            % (self.rinex, self.rinex, to_version), 45, self.rootdir)

            _, err = cmd.run_shell()

            result_path = self.rinex_path + '.t'
            
            if '| E |' in err:
                raise pyRinexExceptionBadFile('gfzrnx_lx returned error converting to RINEX 2.11:\n' + err)

            elif not os.path.exists(result_path):
                raise pyRinexExceptionBadFile('gfzrnx_lx failed to convert to RINEX 2.11:\n' + err)

            # if all ok, move converted file to rinex_path
            os.remove(self.rinex_path)
            move(result_path, self.rinex_path)
            # change version
            self.rinex_version = to_version

            self.log_event('Origin file was RINEX 3 -> Converted to 2.11')

        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

    def RunRinSum(self):
        """
        deprecated function to run RinSum
        :return:
        """
        try:
            # run RinSum to get file information
            cmd = pyRunWithRetry.RunCommand('RinSum --notable ' + self.rinex_path, 45)  # DDG: increased from 21 to 45.

            output, _ = cmd.run_shell()

            # write RinSum output to a log file (debug purposes)
            file_write(self.rinex_path + '.log', output)

            return output

        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

    def RunGfzrnx(self):
        # run Gfzrnx to get file information
        cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s -fout %s.log -meta medium:json'
                                        % (self.rinex_path, self.rinex_path), 45)
        try:
            _, err = cmd.run_shell()
        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

        if '| E |' in err:
            raise pyRinexExceptionBadFile('gfzrnx_lx returned error:\n' + err)

        # write RinSum output to a log file (debug purposes)
        with file_open(self.rinex_path + '.log') as info:
            return json.load(info)

    def __init__(self, NetworkCode, StationCode, origin_file, no_cleanup=False, allow_multiday=False,
                 min_time_seconds=3600):
        """
        pyRinex initialization
        if file is multiday, DO NOT TRUST date object for initial file. Only use pyRinex objects contained in the
        multiday list
        """
        RinexRecord.__init__(self, NetworkCode, StationCode)

        self.no_cleanup     = no_cleanup
        self.allow_multiday = allow_multiday
        self.origin_file    = None
        self.local_copy     = None
        self.rootdir        = None

        self.min_time_seconds = min_time_seconds
        # check that the rinex file name is valid!
        try:
            self.rinex_name_format = pyRinexName.RinexNameFormat(origin_file)
        except pyRinexName.RinexNameException as e:
            raise pyRinexException('File name does not follow the RINEX(Z)/CRINEX(Z) naming convention: %s'
                                   % (os.path.basename(origin_file))) from e

        self.create_temp_dirs()

        self.indentify_file(origin_file)

        copy(origin_file, self.rootdir)

        if self.origin_type in (TYPE_CRINEZ, TYPE_CRINEX, TYPE_RINEZ, TYPE_CRINEZ_2):
            self.uncompress()

        # check basic infor in the rinex header to avoid problems with RinSum
        self.check_header()

        # if self.rinex_version >= 3:
        #    self.ConvertRinex3to2()

        self.size = os.path.getsize(os.path.join(self.rootdir,
                                                 self.rinex_name_format.to_rinex_format(TYPE_RINEX, no_path=True)))
        # process the output
        self.parse_output(self.RunGfzrnx(), self.min_time_seconds)

        # DDG: new interval checking after running Gfzrnx
        # check the sampling interval
        self.check_interval()

        # check for files that have more than one day inside (yes, there are some like this... amazing)
        # condition is: the start and end date don't match AND
        # either there is more than two hours in the second day OR
        # there is more than one day of data
        if not allow_multiday and self.datetime_lastObs.date() != self.datetime_firstObs.date():
            # more than one day in this file. Is there more than one hour? (at least in principle, based on the time)
            first_obs = datetime.datetime(self.datetime_lastObs.date().year,
                                          self.datetime_lastObs.date().month,
                                          self.datetime_lastObs.date().day)

            # DDG: this is not enough to check consistent RINEX file. Here, we checked if there is more than one hour
            # of data after the first observation of the second day (variable first_obs). But, one cases look like this
            #  01  2  5 23 15  0.0000000  0  7G 5G 4G30G 9G 7G24G10
            # ....
            #  01  2  6  3 35 30.0000000  0  5G 6G26G17G10G23
            # a single observation of the next day exists, but at 3:35. This makes
            # (self.datetime_lastObs - first_obs).total_seconds() return more than 3600

            if (self.datetime_lastObs - first_obs).total_seconds() >= 3600:
                # the file has more than one day in it...
                # use teqc to window the data
                if not self.multiday_handle(origin_file):
                    return
            else:
                # window the data to remove superfluous epochs
                last_obs = datetime.datetime(self.datetime_firstObs.date().year,
                                             self.datetime_firstObs.date().month,
                                             self.datetime_firstObs.date().day,
                                             23, 59, 59)
                self.window_data(end=last_obs)
                self.log_event('RINEX had incomplete epochs (or < 1 hr) outside of the corresponding UTC day -> '
                               'Data windowed to one UTC day.')

        # reported date for this file is session/2
        self.date = pyDate.Date(datetime = self.datetime_firstObs+(self.datetime_lastObs -
                                                                   self.datetime_firstObs)/2)

        # DDG: calculate the completion of the file (at sampling rate)
        # completion of day
        # done after binning so that if the file is a multiday we don't get a bad completion
        self.completion     = self.epochs * self.interval / 86400
        # completion of time window in file
        self.rel_completion = self.epochs * self.interval / ((self.datetime_lastObs -
                                                              self.datetime_firstObs).total_seconds() + self.interval)

        # load the RinexRecord class
        self.load_record()

    def multiday_handle(self, origin_file):
        # split the file
        self.split_file()

        continue_statements = True

        if len(self.multiday_rnx_list) > 1:
            # truly a multiday file
            self.multiday = True
            # self.log_event('RINEX file is multiday -> generated $i RINEX files' % len(self.multiday_rnx_list))
            continue_statements = True

        elif len(self.multiday_rnx_list) == 1:
            # maybe one of the files has a single epoch in it. Drop the current rinex and use the binned version
            self.cleanup()
            temp_path = self.multiday_rnx_list[0].rootdir
            # keep the log
            self.log_event('RINEX appeared to be multiday but had incomplete epochs (or < 1 hr) -> '
                           'Data windowed to one UTC day.')
            temp_log = self.log
            # set to no cleanup so that the files survive the __init__ statement
            self.multiday_rnx_list[0].no_cleanup = True
            # reinitialize self
            self.__init__(self.multiday_rnx_list[0].NetworkCode,
                          self.multiday_rnx_list[0].StationCode,
                          self.multiday_rnx_list[0].rinex_path)
            # the origin file should still be the rinex passed to init the object, not the multiday file
            self.origin_file = origin_file
            # remove the temp directory
            self.log += temp_log

            rmtree(temp_path)
            # now self points the the binned version of the rinex
            continue_statements = False

        return continue_statements

    def split_file(self):
        temp = os.path.join(self.rootdir, 'split')
        # create a temporary folder to isolate the files
        if not os.path.exists(temp):
            os.makedirs(temp)

        # run in the local folder to get the files inside rootdir
        cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s -fout split/::RX%i:: -split 86400 -kv'
                                        % (self.rinex, int(self.rinex_version)), 45, self.rootdir)
        try:
            _, err = cmd.run_shell()
            # raise error if error reported by gfzrnx
            if '| E |' in err:
                raise pyRinexException('Error while binning RINEX file: ' + err)

        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

        # successfully binned the file
        # delete current file and rename the new files
        os.remove(self.rinex_path)

        # now we should have as many files named rnxDDD0.??o as days inside the RINEX
        for ff in os.listdir(temp):
            # move the file out of the temp folder
            move(os.path.join(temp, ff), os.path.join(self.rootdir, ff))
            # get the info for this file
            try:
                rnx = ReadRinex(self.NetworkCode, self.StationCode, os.path.join(self.rootdir, ff))
                # append this rinex object to the multiday list
                self.multiday_rnx_list.append(rnx)
            except (pyRinexException,
                    pyRinexExceptionBadFile):
                # there was a problem with one of the multiday files. Do not append
                pass

        # remove the temp folder
        rmtree(temp)

    def parse_output(self, output, min_time_seconds=3600):

        try:
            p = output['site']['position']
            self.satsys = output['data']['satsys']
            self.x, self.y, self.z     = (float(p['x']), float(p['y']), float(p['z']))
            self.lat, self.lon, self.h = ecef2lla([self.x, self.y, self.z])
        except:
            self.x, self.y, self.z     = (None, None, None)
            self.lat, self.lon, self.h = (None, None, None)
            self.log_event('Problem parsing approximate position, setting to 0')

        try:
            p = output['antenna']['height']
            self.antOffset, self.antOffsetN, self.antOffsetE = (float(p['h']), float(p['n']), float(p['e']))
        except:
            self.antOffset, self.antOffsetN, self.antOffsetE = (0, 0, 0)
            self.log_event('Problem parsing ANTENNA OFFSETS, setting to 0')

        try:
            p = output['receiver']
            self.recNo, self.recType, self.recVers = (p['number'], p['name'], p['firmware'])
        except:
            self.recNo, self.recType, self.recVers = ('', '', '')
            self.log_event('Problem parsing REC # / TYPE / VERS, setting to EMPTY')

        try:
            self.marker_number = output['site']['number']
        except:
            self.marker_number = 'NOT FOUND'
            self.log_event('No MARKER NUMBER found, setting to NOT FOUND')

        try:
            p = output['antenna']
            self.antNo, self.antType, self.antDome = (p['name'], p['name'], p['radome'])
        except:
            self.antNo, self.antType, self.antDome = ('UNKNOWN', 'UNKNOWN', 'NONE')
            self.log_event('Problem parsing ANT # / TYPE, setting to UNKNOWN NONE')

        try:
            p = output['data']['epoch']
            # DDG: int(p['number']) + int(p['number_extra']) to consider TOTAL number of epochs in the file
            self.interval = float(p['interval'])
            self.epochs   = int(p['number']) + int(p['number_extra'])
        except:
            self.interval = 0
            self.epochs   = 0
            self.log_event('Problem interval and epochs, setting to 0')

        # stop here is epochs of interval is invalid
        if self.interval == 0:
            raise pyRinexExceptionSingleEpoch('RINEX interval equal to zero. Single epoch or bad RINEX file. ' +
                                              (('Reported epochs in file were %i' % self.epochs) if self.epochs > 0 else
                                               'No epoch information to report. The output from Gfzrnx was:\n' + str(output)))
        elif self.interval > 120:
            raise pyRinexExceptionBadFile('RINEX sampling interval > 120s. The output from Gfzrnx was:\n' + str(output))

        elif self.epochs * self.interval < min_time_seconds:
            raise pyRinexExceptionBadFile('RINEX file with < %i seconds of observation time. '
                                          'The output from Gfzrnx was:\n' % min_time_seconds + str(output))

        try:
            p = output['data']['epoch']

            def date2vals(d):
                yy, mm, dd, hh, MM, ss = d.split()
                dt = datetime.datetime(check_year(yy), int(mm), int(dd), int(hh), int(MM), int(float(ss)))
                return dt, dt.strftime('%Y/%m/%d %H:%M:%S')

            self.datetime_firstObs, self.firstObs = date2vals(p['first'])
            self.datetime_lastObs,  self.lastObs  = date2vals(p['last'])

            if self.datetime_lastObs <= self.datetime_firstObs:
                # bad rinex! first obs > last obs
                raise pyRinexExceptionBadFile('Last observation (' + self.lastObs + ') <= first observation (' +
                                              self.firstObs + ')')
        except Exception as e:
            raise pyRinexException(self.rinex_path +
                                   ': error in ReadRinex.parse_output: the output for first/last obs is invalid '
                                   '(' + str(e) + ') The output from Gfzrnx was:\n' + str(output))

        try:
            self.obs_types = [i for o in output['file']['sysobs'] for i in o]
        except:
            self.obs_types = 0
            self.log_event('Problem parsing observation types, setting to 0')

        try:
            self.observables = output['file']['sysobs']
        except Exception as e:
            self.observables = ()
            self.log_event('Problem parsing observables (%s), setting to ()' % str(e))

        # remove non-utf8 chars
        # => now this is implicit in the open() call
        # self.recNo   = self.recNo  .decode('utf-8', 'ignore').encode('utf-8')
        # self.recType = self.recType.decode('utf-8', 'ignore').encode('utf-8')
        # self.recVers = self.recVers.decode('utf-8', 'ignore').encode('utf-8')
        # self.antNo   = self.antNo  .decode('utf-8', 'ignore').encode('utf-8')
        # self.antType = self.antType.decode('utf-8', 'ignore').encode('utf-8')
        # self.antDome = self.antDome.decode('utf-8', 'ignore').encode('utf-8')

    def get_firstobs(self):

        if self.rinex_version < 3:
            fs = struct.Struct('1s2s1s2s1s2s1s2s1s2s11s2s1s3s')
        else:
            fs = struct.Struct('2s4s1s2s1s2s1s2s1s2s11s2s1s3s')

        date = None
        skip = 0
        for line in self.data:
            if skip == 0:
                fields = struct_unpack(fs, line) 

                if int(fields[12]) <= 1: # OK FLAG
                    # read first observation
                    year   = int(fields[1])
                    month  = int(fields[3])
                    day    = int(fields[5])
                    hour   = int(fields[7])
                    minute = int(fields[9])
                    second = float(fields[10])

                    try:
                        date = pyDate.Date(year   = year,
                                           month  = month,
                                           day    = day,
                                           hour   = hour,
                                           minute = minute,
                                           second = second)
                    except pyDate.pyDateException as e:
                        raise pyRinexExceptionBadFile(str(e))

                    break
                elif int(fields[12]) > 1:
                    # event, skip lines indicated in next field
                    skip = int(fields[13])
            else:
                skip -= 1

        return date

    def get_header(self):

        header = []
        # retry reading. Every now and then there is a problem during file read.
        for i in range(2):
            try:
                with file_open(self.rinex_path) as fileio:
                    for line in fileio:
                        header.append(line)
                        if line.strip().endswith('END OF HEADER'):
                            break
                    break
            except IOError:
                # try again
                if i == 0:
                    continue
                else:
                    raise

        return header

    def auto_coord(self, brdc, chi_limit=3):
        # use NRCAN PPP in code-only mode to obtain a coordinate of the station
        from geode import pyPPP, pyOptions

        rnx = ReadRinex(self.NetworkCode, self.StationCode, self.rinex_path, allow_multiday=True)

        config = pyOptions.ReadOptions('gnss_data.cfg')  # type: pyOptions.ReadOptions

        # DDG: code-only bootstrap coordinate is only implemented for GPSPACE -- force it here regardless
        # of the configured ppp_engine (e.g. 'pride'), which does not support code-only PPP yet.
        options = dict(config.options)
        options['ppp_engine'] = 'gpspace'

        ppp = pyPPP.RunPPP(rnx, '', options, '', '', 0,
                           clock_interpolation=True, strict=False, apply_met=False, observations=pyPPP.OBSERV_CODE_ONLY)

        ppp.exec_ppp()

        self.x = ppp.x
        self.y = ppp.y
        self.z = ppp.z

        self.lat, self.lon, self.h = ecef2lla([self.x, self.y, self.z])

        # copy the header to replace with new coordinate
        new_header = self.replace_record(self.header,'APPROX POSITION XYZ', (self.x, self.y, self.z))
        # write the rinex file with the new header
        rnx.write_rinex(new_header)

        return (self.x, self.y, self.z), (self.lat, self.lon, self.h)

    def auto_coord_sh_rx2apr(self, brdc, chi_limit=3):
        # use gamit's sh_rx2apr to obtain a coordinate of the station

        # do not work with the original file. Decimate and remove other systems (to increase speed)
        try:
            # make a copy to decimate and remove systems to help sh_rx2apr (also, convert to rinex 2, if rinex 3)
            # allow multiday files (will not change the answer), just get a coordinate for this file
            rnx = ReadRinex(self.NetworkCode, self.StationCode, self.rinex_path, allow_multiday=True)

            if rnx.rinex_version >= 3:
                rnx.ConvertRinex(2)

            if rnx.interval < 15:
                rnx.decimate(30)
                self.log_event('Decimating to 30 seconds to run auto_coord')

            # remove the other systems that sh_rx2apr does not use
            if rnx.system == 'M':
                rnx.remove_systems()
                self.log_event('Removing systems other systems to run auto_coord')

        except pyRinexException as e:
            # print str(e)
            # ooops, something went wrong, try with local file (without removing systems or decimating)
            rnx = self
            # raise pyRinexExceptionBadFile('During decimation or remove_systems (to run auto_coord),
            # teqc returned: %s' + str(e))

        # copy brdc orbit if we have a decimated rinex file
        # DDG 26 Jun 19: BUT CHECK THAT PATHS ARE DIFFERENT! If ReadRinex in line 1073 fails, then rnx = self
        # and was trying to copy BRDC file over itself!
        brdc_path = os.path.join(rnx.rootdir, brdc.filename)
        if brdc.brdc_path != brdc_path:
            copyfile(brdc.brdc_path, brdc_path)

        # check if the apr coordinate is zero and iterate more than once if true
        if self.x == 0 and self.y == 0 and self.z == 0:
            max_it = 2
        else:
            max_it = 1

        out = ''

        for i in range(max_it):

            cmd = pyRunWithRetry.RunCommand(
                'sh_rx2apr -site ' + rnx.rinex + ' -nav ' + brdc.filename + ' -chi ' + str(chi_limit), 60,
                rnx.rootdir)
            # leave errors un-trapped on purpose (will raise an error to the parent)
            out, err = cmd.run_shell()

            if err != '' and err is not None:
                raise pyRinexExceptionNoAutoCoord(str(err) + '\n' + out)
            else:
                # check that the Final chi**2 is < 3
                for line in out.split('\n'):
                    if '* Final sqrt(chi**2/n)' in line:
                        chi = line.split()[-1]

                        if chi == 'NaN':
                            raise pyRinexExceptionNoAutoCoord('chi2 = NaN! ' + str(err) + '\n' + out)

                        elif float(chi) < chi_limit:
                            # open the APR file and read the coordinates
                            apr_path = os.path.join(rnx.rootdir, rnx.rinex[0:4] + '.apr')
                            if os.path.isfile(apr_path):
                                with file_open(apr_path) as apr:
                                    line = apr.readline().split()

                                    self.x = float(line[1])
                                    self.y = float(line[2])
                                    self.z = float(line[3])

                                    self.lat, self.lon, self.h = ecef2lla([self.x, self.y, self.z])

                                # only exit and return coordinate if current iteration == max_it
                                # (minus one due to arrays starting at 0).
                                if i == max_it - 1:
                                    return (float(line[1]), float(line[2]), float(line[3])), \
                                           (self.lat, self.lon, self.h)

                # copy the header to replace with new coordinate
                # note that this piece of code only executes if there is more than one iteration
                new_header = self.replace_record(self.header,
                                                 'APPROX POSITION XYZ', (self.x, self.y, self.z))
                # write the rinex file with the new header
                rnx.write_rinex(new_header)

        raise pyRinexExceptionNoAutoCoord(str(out) + '\nLIMIT FOR CHI**2 was %i' % chi_limit)

    def window_data(self, start=None, end=None, copyto=None):
        """
        Window the RINEX data using GFZRNX
        :param start: a start datetime or self.firstObs if None
        :param end: a end datetime or self.lastObs if None
        :return:
        """
        if start is None:
            start = self.datetime_firstObs
            self.log_event('Setting start = first obs in window_data')

        if end is None:
            end = self.datetime_lastObs
            self.log_event('Setting end = last obs in window_data')

        d = int((end - start).total_seconds())

        cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s -fout %s.t -epo_beg %i%02i%02i_%02i%02i%02i -d %i -f -kv'
                                        % (self.rinex_path, self.rinex_path,
                                           start.year, start.month, start.day,
                                           start.hour, start.minute, start.second, d), 120)
        try:
            _, err = cmd.run_shell()
            # raise error if error reported by gfzrnx
            if '| E |' in err:
                raise pyRinexException('Error while windowing RINEX file: ' + err)

        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

        # delete the original file and replace with .t
        if copyto is None:
            os.remove(self.rinex_path)
            move(self.rinex_path + '.t', self.rinex_path)
            self.datetime_firstObs = start
            self.datetime_lastObs  = end
            self.firstObs          = start.strftime('%Y/%m/%d %H:%M:%S')
            self.lastObs           = end  .strftime('%Y/%m/%d %H:%M:%S')
        else:
            move(self.rinex_path + '.t', copyto)

    def decimate(self, decimate_rate, copyto=None):
        # if copy to is passed, then the decimation is done on the copy of the file, not on the current rinex.
        # otherwise, decimation is done in current rinex
        if copyto is not None:
            copyfile(self.rinex_path, copyto)
        else:
            copyto = self.rinex_path
            self.interval = decimate_rate

        cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s -fout %s.t -smp %i -f -kv'
                                        % (copyto, copyto, decimate_rate), 120)
        try:
            _, err = cmd.run_shell()
            # raise error if error reported by gfzrnx
            if '| E |' in err:
                raise pyRinexException('Error while windowing RINEX file: ' + err)

        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

        # delete the original file and replace with .t
        os.remove(copyto)
        move(copyto + '.t', copyto)

        self.log_event('RINEX decimated to %is (applied to %s)' % (decimate_rate, str(copyto)))

    def remove_systems(self, systems=('C', 'E', 'I', 'J', 'R', 'S'), copyto=None):
        # if copy to is passed, then the system removal is done on the copy of the file, not on the current rinex.
        # other wise, system removal is done to current rinex
        if copyto is not None:
            copyfile(self.rinex_path, copyto)
        else:
            copyto = self.rinex_path

        vsys = 'CEIGJRS'
        rsys = ''.join(s for s in vsys if s not in systems)

        cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s -fout %s.t -satsys %s -f -kv' % (copyto, copyto, rsys), 120)

        try:
            _, err = cmd.run_shell()
            # raise error if error reported by gfzrnx
            if '| E |' in err:
                raise pyRinexException('Error while binning RINEX file: ' + err)

        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

        # delete the original file and replace with .t
        os.remove(copyto)
        move(copyto + '.t', copyto)
        # if working on local copy, reload the rinex information
        if copyto == self.rinex_path:
            # reload information from this file
            self.parse_output(self.RunGfzrnx(), self.min_time_seconds)
        else:
            raise pyRinexException(err)

        self.log_event('Removed systems %s (applied to %s)' % (','.join(systems), str(copyto)))

    def normalize_header(self, NewValues=None, brdc=None, x=None, y=None, z=None):
        # this function gets rid of the heaer information and replaces it with the station info (trusted)
        # should be executed before calling PPP or before rebuilding the Archive
        # new function now accepts a dictionary OR a station info object

        if type(NewValues) is StationInfo:
            if NewValues.date is not None and NewValues.date != self.date:
                raise pyRinexException('The StationInfo object was initialized for a different date than that of the '
                                       'RINEX file. Date on RINEX: ' + self.date.yyyyddd() +
                                       '; Station Info: ' + NewValues.date.yyyyddd())
            else:
                NewValues = {k: v for k, v in vars(NewValues.current_record).items()
                             if not k.startswith('_')}

        # DDG: check if NewValues is None -> assign empty dict in that case
        if NewValues is None:
            NewValues = {}

        fieldnames  = ('AntennaHeight', 'AntennaNorth', 'AntennaEast', 'ReceiverCode', 'ReceiverVers',
                       'ReceiverSerial', 'AntennaCode', 'RadomeCode', 'AntennaSerial')
        rinex_field = ('AntennaOffset', None, None, 'ReceiverType', 'ReceiverFw', 'ReceiverSerial',
                       'AntennaType', 'AntennaDome', 'AntennaSerial')

        new_header = self.header

        # DDG: to keep things compatible, only check the first 4 chars of the station code (to keep the rest of the
        # stuff of RINEX 3 files)
        if self.marker_name[0:4] != self.StationCode:
            if self.rinex_version < 3:
                marker_name = self.StationCode.upper()
            else:
                marker_name = self.StationCode.upper() + self.marker_name[4:].upper()
                
            new_header = self.replace_record(new_header, 'MARKER NAME', marker_name)
            new_header = self.insert_comment(new_header, 'PREV MARKER NAME: ' + self.marker_name.upper())
            
            self.marker_name = marker_name
            # DDG: allow invoking without any new values to check the marker name
            if NewValues is None:
                self.write_rinex(new_header)
                return

        # set values
        for i, field in enumerate(fieldnames):
            if field not in NewValues.keys():
                if rinex_field[i] is not None:
                    NewValues[field] = self.record[rinex_field[i]]
                else:
                    NewValues[field] = 0.0

        if (NewValues['ReceiverCode']   != self.recType or
            NewValues['ReceiverVers']   != self.recVers or
            NewValues['ReceiverSerial'] != self.recNo):

            new_header = self.replace_record(new_header, 'REC # / TYPE / VERS',
                                             (NewValues['ReceiverSerial'],
                                              NewValues['ReceiverCode'],
                                              NewValues['ReceiverVers']))

            if NewValues['ReceiverSerial'] != self.recNo:
                new_header = self.insert_comment(new_header, 'PREV REC #   : ' + self.recNo)
                self.recNo = NewValues['ReceiverSerial']

            if NewValues['ReceiverCode'] != self.recType:
                new_header = self.insert_comment(new_header, 'PREV REC TYPE: ' + self.recType)
                self.recType = NewValues['ReceiverCode']

            if NewValues['ReceiverVers'] != self.recVers:
                new_header = self.insert_comment(new_header, 'PREV REC VERS: ' + self.recVers)
                self.recVers = NewValues['ReceiverVers']

        # if (NewValues['AntennaCode'] != self.antType or
        #    NewValues['AntennaSerial'] != self.antNo or
        #    NewValues['RadomeCode'] != self.antDome):
        if True:

            # DDG: New behaviour, ALWAYS replace the antenna and DOME field due to problems with formats for some
            # stations. Eg:
            # 13072               ASH700936D_M    NONE                    ANT # / TYPE
            # 13072               ASH700936D_M SNOW                       ANT # / TYPE
            new_header = self.replace_record(new_header, 'ANT # / TYPE',
                                             (NewValues['AntennaSerial'],
                                              '%-15s' % NewValues['AntennaCode'] + ' ' + NewValues['RadomeCode']))

            if NewValues['AntennaCode'] != self.antType:
                new_header = self.insert_comment(new_header, 'PREV ANT #   : ' + self.antType)
                self.antType = NewValues['AntennaCode']
                
            if NewValues['AntennaSerial'] != self.antNo:
                new_header = self.insert_comment(new_header, 'PREV ANT TYPE: ' + self.antNo)
                self.antNo = NewValues['AntennaSerial']
                
            if NewValues['RadomeCode'] != self.antDome:
                new_header = self.insert_comment(new_header, 'PREV ANT DOME: ' + self.antDome)
                self.antDome = NewValues['RadomeCode']

        if (NewValues['AntennaHeight'] != self.antOffset or
            NewValues['AntennaNorth']  != self.antOffsetN or
            NewValues['AntennaEast']   != self.antOffsetE):

            new_header = self.replace_record(new_header,
                                             'ANTENNA: DELTA H/E/N',
                                             (NewValues['AntennaHeight'],
                                              NewValues['AntennaEast'],
                                              NewValues['AntennaNorth']))

            if NewValues['AntennaHeight'] != self.antOffset:
                new_header      = self.insert_comment(new_header, 'PREV DELTA H: %.4f' % self.antOffset)
                self.antOffset = float(NewValues['AntennaHeight'])
                
            if NewValues['AntennaNorth'] != self.antOffsetN:
                new_header      = self.insert_comment(new_header, 'PREV DELTA N: %.4f' % self.antOffsetN)
                self.antOffsetN = float(NewValues['AntennaNorth'])
                
            if NewValues['AntennaEast'] != self.antOffsetE:
                new_header      = self.insert_comment(new_header, 'PREV DELTA E: %.4f' % self.antOffsetE)
                self.antOffsetE = float(NewValues['AntennaEast'])

        # always replace the APPROX POSITION XYZ
        if x is None and brdc is None and self.x is None:
            raise pyRinexException(
                'Cannot normalize the header\'s APPROX POSITION XYZ without a coordinate or '
                'a valid broadcast ephemeris object')

        elif self.x is None and brdc is not None:
            self.auto_coord(brdc)

        elif x is not None:
            self.x = float(x)
            self.y = float(y)
            self.z = float(z)

        new_header = self.replace_record(new_header, 'APPROX POSITION XYZ', (self.x, self.y, self.z))

        new_header = self.insert_comment(new_header, 'APPROX POSITION SET TO AUTONOMOUS SOLUTION')
        new_header = self.insert_comment(new_header, 'HEADER NORMALIZED BY pyRinex ON ' +
                                         datetime.datetime.now().strftime('%Y/%m/%d %H:%M'))

        self.write_rinex(new_header)

    def apply_file_naming_convention(self):
        """
        function to rename a file to make it consistent with the RINEX naming convention
        gfzrnx now makes things simpler: just use the appropriate command
        :return:
        """

        self.rename(self.rinex_name_format.to_rinex_format(TYPE_RINEX, True))

        # version that uses gfzrnx: too slow with large files!!
        # temp = os.path.join(self.rootdir, 'rename')
        # create a temporary folder to isolate the files
        # if not os.path.exists(temp):
        #    os.makedirs(temp)

        # before applying naming convention, check the marker name in the header
        # if self.marker_name[0:4].lower() != self.StationCode.lower():
        #     site = '-site ' + self.StationCode.lower()
        #     self.normalize_header()
        # else:
        #     site = ''

        # cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s -fout rename/::RX%i:: -kv %s'
        #                                 % (self.rinex, int(self.rinex_version), site), 90, self.rootdir)

        # try:
        #     _, err = cmd.run_shell()
        #     # raise error if error reported by gfzrnx
        #     if '| E |' in err:
        #         raise pyRinexException('Error while applying RINEX naming convention: ' + err)

        # except pyRunWithRetry.RunCommandWithRetryExeception as e:
        #     # catch the timeout except and pass it as a pyRinexException
        #     raise pyRinexException(str(e))

        # f = glob.glob(os.path.join(temp, '*'))
        # if not f:
        #     raise pyRinexException('Error while applying RINEX naming convention (no file found): ' + err)
        # else:
        #     # rename file if there was a change
        #     if not os.path.basename(f[0]) == self.rinex:
        #         self.rename(os.path.basename(f[0]))

        # remove the temp directory
        # rmtree(temp)

    def move_origin_file(self, path, destiny_type=TYPE_CRINEZ):
        # this function moves the ARCHIVE file (or repository) to another location indicated by path
        # can also specify other types, but assumed to be CRINEZ by default
        # it also makes sure that it doesn't overwrite any existing file

        # get the filename according to the requested destiny format type
        dst = self.rinex_name_format.to_rinex_format(destiny_type, no_path=True)

        if not os.path.isabs(path):
            raise pyRinexException('Destination must be an absolute path')

        elif path == os.path.dirname(self.origin_file) and os.path.basename(self.origin_file) == dst:
            raise pyRinexException('Error during move_origin_file: origin and destiny are the same!')

        path_dst = os.path.join(path, dst)
        # determine action base on origin type
        if self.origin_type == destiny_type:
            # intelligent move (creates folder and checks for file existence)
            # origin and destiny match, do the thing directly
            filename = Utils.move(self.origin_file, path_dst)

        # if other types are requested, or origin is not the destiny type, then use local file and delete the
        elif destiny_type is TYPE_RINEX:
            filename = copyfile(self.rinex_path, path_dst)

        elif destiny_type is TYPE_CRINEZ:
            filename = self.compress_local_copyto(path)

        elif destiny_type is TYPE_CRINEX:
            self.create_script('compress.sh', 'cat "%s" | rnx2crx > "%s"'
                               % (self.rinex_path, path_dst))

            # apply hatanaka compression to the destiny file
            cmd = pyRunWithRetry.RunCommand(os.path.join(self.rootdir, 'compress.sh'), 45)
            try:
                _, err = cmd.run_shell()

                if os.path.getsize(path_dst) == 0:
                    raise pyRinexException('Error in move_origin_file: compressed version of ' +
                                           self.rinex_path + ' has zero size!\n' + err)
            except pyRunWithRetry.RunCommandWithRetryExeception as e:
                # catch the timeout except and pass it as a pyRinexException
                raise pyRinexException(str(e))

            filename = path_dst

        else:
            raise pyRinexException('pyRinex will not natively generate a RINEZ or CRINEZ_2 (RINEX 2.gz) file.')

        # to keep everything consistent, also change the local copies of the file
        self.rename(filename)
        # delete original (if the dest exists!)
        if os.path.isfile(self.origin_file):
            if os.path.isfile(filename):
                os.remove(self.origin_file)
            else:
                raise pyRinexException('New \'origin_file\' (%s) does not exist!' % os.path.join(path, dst))

        # change origin file reference
        self.origin_file = filename
        self.origin_type = destiny_type

        self.log_event('Origin moved to %s and converted to %i' % (self.origin_file, destiny_type))

        return filename

    def compress_local_copyto(self, path, force_filename=None):
        # this function compresses and moves the local copy of the rinex
        # meant to be used when a multiday rinex file is encountered and we need to move it to the repository

        # compress the rinex into crinez. Make the filename
        # DDG: only create a RINEX compliant filename if force_filename=None
        if not force_filename:
            crinez = self.rinex_name_format.to_rinex_format(TYPE_CRINEZ, no_path=True)
        else:
            # use provided filename
            crinez = force_filename

        crinez_path = os.path.join(self.rootdir, crinez)
        # we make the crinez again (don't use the existing from the database) to apply any corrections
        # made during the __init__ stage. Notice the -f in rnx2crz
        self.create_script('compress.sh', 'cat "%s" | rnx2crx | gzip -c > "%s"'
                           % (self.rinex_path, crinez_path))

        cmd = pyRunWithRetry.RunCommand(os.path.join(self.rootdir, 'compress.sh'), 45)
        try:
            _, err = cmd.run_shell()

            if os.path.getsize(crinez_path) == 0:
                raise pyRinexException('Error in compress_local_copyto: compressed version of ' +
                                       self.rinex_path + ' has zero size!\n' + err)
        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            # catch the timeout except and pass it as a pyRinexException
            raise pyRinexException(str(e))

        filename = Utils.copyfile(crinez_path,
                                  os.path.join(path, crinez),
                                  self.rinex_version)

        self.log_event('Created CRINEZ from local copy and copied to %s' % path)

        return filename

    def rename(self, new_name = None, NetworkCode = None, StationCode = None):

        # function that renames the local crinez and rinex file based on the provided information
        # it also changes the variables in the object to reflect this change
        # new name can be any valid format (??d.Z, .??o, ??d, ??o.Z)

        if new_name:
            rnx = pyRinexName.RinexNameFormat(new_name)
            # generate the corresponding names
            rinex  = rnx.to_rinex_format(TYPE_RINEX,  no_path=True)
            crinez = rnx.to_rinex_format(TYPE_CRINEZ, no_path=True)
            # do not continue executing unless there is a REAL change!
            if rinex != self.rinex:
                # rename the rinex
                new_rinex_path = os.path.join(self.rootdir, rinex)
                if os.path.isfile(self.rinex_path):
                    move(self.rinex_path,
                         new_rinex_path)

                self.rinex_path = new_rinex_path
                # DDG JUN 16 2021: have to also rename the new object rinex_name_format to make it consistent with new
                #                  stations code!
                self.rinex_name_format.StationCode = rnx.StationCode

                # rename the files
                # check if local crinez exists (possibly made by compress_local_copyto)
                new_crinez_path = os.path.join(self.rootdir, crinez)
                if os.path.isfile(self.crinez_path):
                    move(self.crinez_path, new_crinez_path)

                self.crinez_path = new_crinez_path

                # rename the local copy of the origin file (if exists)
                # only cases that need to be renamed (again, IF they exist; they shouldn't, but just in case)
                # are RINEZ and CRINEX since RINEX and CRINEZ are renamed above
                if os.path.isfile(self.local_copy):
                    local = rnx.to_rinex_format(self.origin_type, no_path=True)
                    move(self.local_copy, os.path.join(self.rootdir, local))

                self.crinez = crinez
                self.rinex  = rinex

                self.log_event('RINEX/CRINEZ renamed to %s' % rinex)

                # update the database dictionary record
                self.record['Filename'] = self.rinex
                # DDG Apr 9 2022: needed to update the pyRinexName, otherwise, changes in date (doy, yr, etc)
                # were not applied
                # update the pyRinexName object
                self.rinex_name_format = rnx

        # we don't touch the metadata StationCode and NetworkCode unless explicitly passed
        if NetworkCode:
            self.NetworkCode           = \
            self.record['NetworkCode'] = NetworkCode.strip().lower()

        if StationCode:
            self.StationCode           = \
            self.record['StationCode'] = StationCode.strip().lower()

    def cleanup(self):
        if self.rinex_path and not self.no_cleanup:
            # remove all the directory contents
            try:
                rmtree(self.rootdir)
            except OSError:
                # something was not found, ignore (we are deleting anyways)
                pass

            # if it's a multiday rinex, delete the multiday objects too
            if self.multiday:
                for Rnx in self.multiday_rnx_list:
                    Rnx.cleanup()

    def __del__(self):
        self.cleanup()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def __enter__(self):
        return self

    def __add__(self, other):

        if not isinstance(other, ReadRinex):
            raise pyRinexException('type: '+type(other)+' invalid. Can only splice two RINEX objects.')

        elif self.StationCode != other.StationCode:
            raise pyRinexException('Cannot splice together two different stations!')

        # determine which one goes first
        if other.datetime_firstObs > self.datetime_firstObs:
            f1 = self
            f2 = other
        else:
            f1 = other
            f2 = self

        # now splice files
        # cmd = pyRunWithRetry.RunCommand('teqc -n_GLONASS 64 -n_GPS 64 -n_SBAS 64 -n_Galileo 64 +obs %s.t %s %s'
        #                                 % (f1.rinex_path, f1.rinex_path, f2.rinex_path), 5)
        cmd = pyRunWithRetry.RunCommand('gfzrnx_lx -finp %s %s -fout %s -vo %i'
                                        % (f1.rinex_path, f2.rinex_path, f1.rinex_path + '.t',
                                           int(self.rinex_version * 10) / 10), 45)
        # leave errors un-trapped on purpose (will raise an error to the parent)
        out, err = cmd.run_shell()

        if 'gfzrnx: failure to read' in str(err):
            raise pyRinexException(err)

        filename = Utils.move(f1.rinex_path + '.t',
                              f1.rinex_path)
        return ReadRinex(self.NetworkCode, self.StationCode, filename, allow_multiday=True)

    def __repr__(self):
        return 'pyRinex.ReadRinex(%s, %s, %s, %s)' % (self.NetworkCode, self.StationCode,
                                                      str(self.date.year), str(self.date.doy))
