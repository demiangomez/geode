#!/usr/bin/env python

"""
Project: Parallel.Archive
Date: 3/27/17 11:54 AM
Author: Demian D. Gomez

Integrity check utility of the database. Checks the following:
 - Station info consistency
 - Proposes a station info based on RINEX data
 - Searches for data gaps in the rinex table
 - Prints the station info records
 - renames or merges two stations into one
"""

import sys
import traceback
import os
import numpy
import argparse
import platform
from math import ceil

# deps
from tqdm import tqdm

# app
from geode import pyOptions
from geode import dbConnection
from geode import pyDate
from geode.metadata import station_info
from geode import pyArchiveStruct
from geode import pyPPP
from geode import Utils
from geode.Utils import (process_date, ecef2lla, parse_atx_antennas,
                          determine_frame, station_list_help, add_version_argument)
from geode import pyJobServer


differences = []
rinex_css   = []


def stnrnx_callback(job):

    global differences

    if job.result is not None:
        differences.append(job.result)
    else:
        tqdm.write(job.exception)


def compare_stninfo_rinex(NetworkCode, StationCode, STime, ETime, rinex_serial):

    try:
        cnn = dbConnection.Cnn("gnss_data.cfg")
    except Exception:
        return traceback.format_exc() + ' open de database when processing ' \
                                         'processing %s.%s' % (NetworkCode, StationCode), None

    try:
        # get the center of the session
        date = STime + (ETime - STime)/2
        date = pyDate.Date(datetime=date)

        stninfo = station_info.StationInfo(cnn, NetworkCode, StationCode, date)

    except station_info.StationInfoException as e:
        return "Station Information error: " + str(e), None

    if stninfo.current_record.ReceiverSerial.lower() != rinex_serial.lower():
        return None, [date, rinex_serial, stninfo.current_record.ReceiverSerial.lower()]

    return None, None


def get_differences(differences):

    # print out any error messages
    for error in (diff[0] for diff in differences if diff[0] is not None):
        sys.stdout.write(error + '\n')

    return sorted((diff[1] for diff in differences if diff[1] is not None),
                  key = lambda item: item[0])


def check_rinex_stn(NetworkCode, StationCode, start_date, end_date):

    # load the connection
    try:
        # try to open a connection to the database
        cnn = dbConnection.Cnn("gnss_data.cfg")
    except Exception:
        return traceback.format_exc() + ' processing: (' + NetworkCode + '.' + StationCode \
                + ') using node ' + platform.node(), None

    try:
        Archive = pyArchiveStruct.RinexStruct(cnn)

        return None, Archive.check_rinex_files(NetworkCode, StationCode, start_date, end_date, fix=True)

    except Exception:
        return traceback.format_exc() + ' processing: ' + NetworkCode + '.' + \
               StationCode + ' using node ' + platform.node(), None


def check_rinex_integrity(cnn, Config, stnlist, start_date, end_date, operation, JobServer):

    global differences

    tqdm.write(' >> Archive path: %s' % Config.archive_path)

    if operation == 'fix':
        modules = ('os', 'geode.pyArchiveStruct', 'geode.dbConnection', 'geode.pyOptions', 'traceback', 'platform',
                   'geode.pyEvents')

        pbar = tqdm(total=len(stnlist), ncols=80)
        differences = []

        JobServer.create_cluster(check_rinex_stn, callback=stnrnx_callback, progress_bar=pbar, modules=modules)

        for stn in stnlist:
            StationCode = stn['StationCode']
            NetworkCode = stn['NetworkCode']

            JobServer.submit(NetworkCode, StationCode, start_date, end_date)

        JobServer.wait()

        pbar.close()

        for rinex in differences:
            if rinex[1]:
                for diff in rinex[1]:
                    print('File ' + diff + ' was not found in the archive. See events for details.')
            elif rinex[0]:
                print('Error encountered: ' + rinex[0])

    elif operation == 'report':

        Archive = pyArchiveStruct.RinexStruct(cnn)

        for stn in tqdm(stnlist, ncols=160, desc=' >> Checking archive integrity', disable=None):
            for crinex_path in Archive.check_rinex_files(stn['NetworkCode'], stn['StationCode'],
                                                         start_date, end_date):
                tqdm.write(' -- CRINEZ file %s exists in the database but was not found in the archive'
                           % crinex_path)


def stn_info_rinex_integrity(cnn, stnlist, start_date, end_date, JobServer):

    global differences

    modules = ('geode.metadata.station_info', 'geode.dbConnection', 'geode.pyDate', 'traceback')

    JobServer.create_cluster(compare_stninfo_rinex, callback=stnrnx_callback, modules=modules)

    for stn in stnlist:

        StationCode = stn['StationCode']
        NetworkCode = stn['NetworkCode']

        rs = cnn.query('SELECT * FROM rinex_proc WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' AND '
                       '"ObservationSTime" BETWEEN \'%s\' AND \'%s\' '
                       'ORDER BY "ObservationSTime"'
                       % (NetworkCode, StationCode, start_date.yyyymmdd(), end_date.yyyymmdd()))

        rnxtbl = rs.dictresult()

        tqdm.write('\nPerforming station info - rinex consistency '
                   'check for %s.%s...' % (NetworkCode, StationCode), file=sys.stderr)
        maxlen = 0
        pbar = tqdm(total=rs.ntuples())

        JobServer.progress_bar = pbar

        differences = []

        for rnx in rnxtbl:
            # for each rinex found in the table, submit a job
            JobServer.submit(NetworkCode,
                             StationCode,
                             rnx['ObservationSTime'],
                             rnx['ObservationETime'], 
                             rnx['ReceiverSerial'].lower())

        JobServer.wait()

        pbar.close()

        diff_vect = get_differences(differences)

        sys.stdout.write("\nStation info - rinex consistency check for %s.%s follows:\n" % (NetworkCode, StationCode))

        year = ''
        doy = ''
        print_head = True
        for i, diff in enumerate(diff_vect):
            year = Utils.get_norm_year_str(diff[0].year)
            doy  = Utils.get_norm_doy_str(diff[0].doy)

            if print_head:
                sys.stdout.write('Warning! %s.%s from %s %s to ' % (NetworkCode, StationCode, year, doy))

            if i != len(diff_vect)-1:
                if diff_vect[i+1][0] == diff[0] + 1 and \
                   diff_vect[i+1][1] == diff[1] and \
                   diff_vect[i+1][2] == diff[2]:
                    print_head = False
                else:
                    sys.stdout.write('%s %s: RINEX SN %s != Station Information %s Possible change in station or '
                                     'bad RINEX metadata.\n'
                                     % (year,
                                        doy,
                                        diff_vect[i-1][1].ljust(maxlen),
                                        diff_vect[i-1][2].ljust(maxlen)))
                    print_head = True

        if diff_vect:
            sys.stdout.write('%s %s: RINEX SN %s != Station Information %s Possible change in station or '
                             'bad RINEX metadata.\n'
                             % (year,
                                doy,
                                diff_vect[-1][1].ljust(maxlen),
                                diff_vect[-1][2].ljust(maxlen)))
        else:
            sys.stdout.write("No inconsistencies found.\n")


def stn_info_check(cnn, stnlist, Config):
    # check that there are no inconsistencies in the station info records

    atx = dict()
    for frame in Config.options['frames']:
        # read all the available atx files
        atx[frame['name']] = parse_atx_antennas(frame['atx'])

    for stn in stnlist:
        NetworkCode = stn['NetworkCode']
        StationCode = stn['StationCode']

        first_obs = False
        try:
            stninfo = station_info.StationInfo(cnn, NetworkCode, StationCode)  # type: StationInfo

            # there should not be more than one entry with 9999 999 in DateEnd
            empty_edata = [(record['DateEnd'], record['DateStart'])
                           for record in stninfo.records
                           if not record['DateEnd']]

            if len(empty_edata) > 1:
                list_empty = ', '.join(pyDate.Date(datetime=record[1]).yyyyddd()
                                       for record in empty_edata)
                sys.stdout.write('%s.%s: There is more than one station info entry with Session Stop = 9999 999 '
                                 'Session Start -> %s\n' % (NetworkCode, StationCode, list_empty))

            # there should not be a DateStart < DateEnd of different record
            list_problems = []
            atx_problem   = False
            hc_problem    = False

            for i, record in enumerate(stninfo.records):

                # check existence of ANTENNA in ATX
                # determine the reference frame using the start date
                frame, atx_file = determine_frame(Config.options['frames'], record['DateStart'])
                # check if antenna in atx, if not, produce a warning
                if record['AntennaCode'] not in atx[frame]:
                    sys.stdout.write('%s.%s: %-16s%s -> Not found in ANTEX file %s (%s) - dome not checked\n'
                                     % (NetworkCode, StationCode, record['AntennaCode'], record['RadomeCode'],
                                        os.path.basename(atx_file), frame))
                    atx_problem = True

                # check if the station info has a slant height and if the slant can be translated into a DHARP

                overlaps = stninfo.overlaps(record)
                if overlaps:
                    for overlap in overlaps:
                        if overlap['DateStart'].datetime() != record['DateStart'].datetime():

                            list_problems.append([str(overlap['DateStart']),
                                                  str(overlap['DateEnd']),
                                                  str(record['DateStart']),
                                                  str(record['DateEnd'])])

            station_list_gaps = []
            if len(stninfo.records) > 1:
                # get gaps between stninfo records
                for erecord, srecord in zip(stninfo.records[0:-1],
                                            stninfo.records[1:]):

                    sdate = srecord['DateStart']
                    edate = erecord['DateEnd']

                    # if the delta between previous and current session exceeds one second, check if any rinex falls
                    # in that gap
                    if (sdate.datetime() - edate.datetime()).total_seconds() > 1:
                        count = cnn.query('SELECT count(*) as rcount FROM rinex_proc '
                                          'WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' AND '
                                          '"ObservationETime" > \'%s\' AND "ObservationSTime" < \'%s\' AND '
                                          '"Completion" >= 0.5'
                                          % (NetworkCode, StationCode,
                                             edate.strftime(),
                                             sdate.strftime())).dictresult()[0]['rcount']
                        if count != 0:
                            print(erecord)
                            print(srecord)
                            station_list_gaps += [[count, [str(erecord['DateStart']), str(erecord['DateEnd'])],
                                                   [str(srecord['DateStart']), str(srecord['DateEnd'])]]]

            # there should not be RINEX data outside the station info window
            rs = cnn.query('SELECT min("ObservationSTime") as first_obs, max("ObservationSTime") as last_obs '
                           'FROM rinex_proc WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
                           'AND "Completion" >= 0.5'
                           % (NetworkCode, StationCode))  # only check RINEX files with more than 12 hours of data

            rnxtbl = rs.dictresult()

            if rnxtbl[0]['first_obs'] is not None:
                # to avoid empty stations (no rinex data)
                if rnxtbl[0]['first_obs'] < stninfo.records[0]['DateStart'].datetime():
                    d1 = pyDate.Date(datetime=rnxtbl[0]['first_obs'])
                    d2 = stninfo.records[0]['DateStart']
                    sys.stdout.write('%s.%s: There is one or more RINEX observation file(s) outside the '
                                     'Session Start -> RINEX: %s STNINFO: %s\n'
                                     % (NetworkCode, StationCode, d1.yyyyddd(), d2.yyyyddd()))
                    first_obs = True

                if rnxtbl[0]['last_obs'] > stninfo.records[-1]['DateEnd'].datetime():
                    d1 = pyDate.Date(datetime=rnxtbl[0]['last_obs'])
                    d2 = stninfo.records[-1]['DateEnd']
                    sys.stdout.write('%s.%s: There is one or more RINEX observation file(s) outside the last '
                                     'Session End -> RINEX: %s STNINFO: %s\n'
                                     % (NetworkCode, StationCode, d1.yyyyddd(), d2.yyyyddd()))
                    first_obs = True

            if len(station_list_gaps) > 0:
                for gap in station_list_gaps:
                    sys.stdout.write('%s.%s: There is a gap with %s RINEX file(s) between '
                                     'the following station information records: %s -> %s :: %s -> %s\n'
                                     % (NetworkCode, StationCode, gap[0], gap[1][0], gap[1][1], gap[2][0], gap[2][1]))

            if len(list_problems) > 0:
                problems = '\n   '.join(record[0] + ' -> ' + record[1] +
                                        ' conflicts ' +
                                        record[2] + ' -> ' + record[3]
                                        for record in list_problems)

                sys.stdout.write('%s.%s: There are conflicting recods in the station information table\n   %s\n'
                                 % (NetworkCode, StationCode, problems))

            if len(empty_edata) > 1 or len(list_problems) > 0 or first_obs or len(station_list_gaps) > 0 or \
                atx_problem:
                # only print a partial of the station info:
                sys.stdout.write('\n' + stninfo.return_stninfo_short() + '\n\n')
            else:
                sys.stderr.write('%s.%s: No problems found\n' % (NetworkCode, StationCode))

            sys.stdout.flush()

        except station_info.StationInfoException as e:
            tqdm.write(str(e))


def check_spatial_coherence(cnn, stnlist, start_date, end_date):

    for stn in stnlist:

        NetworkCode = stn['NetworkCode']
        StationCode = stn['StationCode']

        rs = cnn.query(
            'SELECT * FROM ppp_soln WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' AND '
            '"Year" || \' \' || "DOY" BETWEEN \'%s\' AND \'%s\''
            % (NetworkCode, StationCode, start_date.yyyy() + ' ' + start_date.ddd(),
               end_date.yyyy() + ' ' + end_date.ddd()))

        ppp = rs.dictresult()

        if rs.ntuples() > 0:
            tqdm.write('\nChecking spatial coherence of PPP solutions for %s.%s...'
                       % (NetworkCode,StationCode), sys.stderr)

            for soln in tqdm(ppp):
                year = soln['Year']
                doy  = soln['DOY']
                date = pyDate.Date(year=year, doy=doy)

                # calculate lla of solution
                lat, lon, h = ecef2lla([float(soln['X']),
                                        float(soln['Y']),
                                        float(soln['Z'])])

                SpatialCheck = pyPPP.PPPSpatialCheck(lat, lon, h)

                Result, match, closest_stn = SpatialCheck.verify_spatial_coherence(cnn, StationCode)

                if Result:
                    if len(closest_stn) == 0:
                        if match[0]['NetworkCode'] != NetworkCode and match[0]['StationCode'] != StationCode:
                            # problem! we found a station but does not agree with the declared solution name
                            tqdm.write('Warning! Solution for %s.%s %s is a match for %s.%s '
                                       '(only this candidate was found).\n'
                                       % (NetworkCode, StationCode, date.yyyyddd(),
                                          match[0]['NetworkCode'], match[0]['StationCode']))
                    else:
                        closest_stn = closest_stn[0]
                        tqdm.write(
                            'Wanrning! Solution for %s.%s %s was found to be closer to %s.%s. '
                            'Distance to %s.%s: %.3f m. Distance to %s.%s: %.3f m' % (
                            NetworkCode, StationCode, date.yyyyddd(), closest_stn['NetworkCode'],
                            closest_stn['StationCode'], closest_stn['NetworkCode'], closest_stn['StationCode'],
                            closest_stn['distance'], match[0]['NetworkCode'], match[0]['StationCode'],
                            match[0]['distance']))

                else:
                    if len(match) == 1:
                        tqdm.write(
                            'Wanrning! Solution for %s.%s %s does not match its station code. Best match: %s.%s' % (
                            NetworkCode, StationCode, date.yyyyddd(), match[0]['NetworkCode'], match[0]['StationCode']))

                    elif len(match) > 1:
                        # No name match but there are candidates for the station
                        matches = ', '.join(['%s.%s: %.3f m' % (m['NetworkCode'], m['StationCode'], m['distance'])
                                             for m in match])
                        tqdm.write('Wanrning! Solution for %s.%s %s does not match its station code. '
                                   'Cantidates and distances found: %s'
                                   % (NetworkCode, StationCode, date.yyyyddd(), matches))
                    else:
                        tqdm.write('Wanrning! PPP for %s.%s %s had no match within 100 m. '
                                   'Closest station is %s.%s (%3.f km, %s.%s %s PPP solution is: %.8f %.8f)'
                                   % (NetworkCode, StationCode, date.yyyyddd(), closest_stn[0]['NetworkCode'],
                                      closest_stn[0]['StationCode'],
                                      numpy.divide(float(closest_stn[0]['distance']), 1000),
                                      NetworkCode, StationCode, date.yyyyddd(), lat[0], lon[0]))
        else:
            tqdm.write('\nNo PPP solutions found for %s.%s...' % (NetworkCode, StationCode), sys.stderr)


def GetGaps(cnn, NetworkCode, StationCode, start_date, end_date):

    rs = cnn.query(
        'SELECT * FROM rinex_proc WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' AND '
        '"ObservationSTime" BETWEEN \'%s\' AND \'%s\' ORDER BY "ObservationSTime"' % (
        NetworkCode, StationCode, start_date.yyyymmdd(), end_date.yyyymmdd()))

    # make the start date and end date the limits of the data
    rnxtbl = rs.dictresult()
    gaps = []
    possible_doys = []

    if len(rnxtbl) > 0:
        start_date = pyDate.Date(year=rnxtbl[0]['ObservationYear'],  doy=rnxtbl[0]['ObservationDOY'])
        end_date   = pyDate.Date(year=rnxtbl[-1]['ObservationYear'], doy=rnxtbl[-1]['ObservationDOY'])

        possible_doys = [pyDate.Date(mjd=mjd) for mjd in range(start_date.mjd, end_date.mjd+1)]

        # @todo optimize, has quadratic time
        actual_doys = [pyDate.Date(year=rnx['ObservationYear'], doy=rnx['ObservationDOY']) for rnx in rnxtbl]
        gaps = [doy
                for doy in possible_doys
                if doy not in actual_doys]

    return gaps, possible_doys


def RinexCount(cnn, stnlist, start_date, end_date):

    master_list = [item['NetworkCode'] + '.' + item['StationCode'] for item in stnlist]

    sys.stderr.write('Querying the database for the number of RINEX files...')

    rs = cnn.query(
        'SELECT "ObservationYear" as year, "ObservationDOY" as doy, count(*) as suma FROM '
        'rinex_proc WHERE "NetworkCode" || \'.\' || "StationCode" IN '
        '(\'' + '\',\''.join(master_list) + '\') AND "ObservationSTime" BETWEEN \'%s\' AND \'%s\' '
                                            'ORDER BY "ObservationSTime"'
        % (start_date.yyyymmdd(), end_date.yyyymmdd()))

    rnxtbl = rs.dictresult()

    for doy in rnxtbl:
        sys.stdout.write(' %4i %3i %4i' % (doy['year'], doy['doy'], doy['suma']))


def get_stn_gaps(cnn, stnlist, ignore_val, start_date, end_date):

    for stn in stnlist:
        NetworkCode = stn['NetworkCode']
        StationCode = stn['StationCode']

        rs = cnn.query(
            'SELECT * FROM rinex_proc WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
            'AND "ObservationSTime" BETWEEN \'%s\' AND \'%s\' ORDER BY "ObservationSTime"'
            % (NetworkCode, StationCode, start_date.yyyymmdd(), end_date.yyyymmdd()))

        rnxtbl = rs.dictresult()
        gap_begin = None
        gaps = []
        for i, rnx in enumerate(rnxtbl):

            if i > 0:
                d1 = pyDate.Date(year = rnx['ObservationYear'],doy=rnx['ObservationDOY'])
                d2 = pyDate.Date(year = rnxtbl[i-1]['ObservationYear'],doy=rnxtbl[i-1]['ObservationDOY'])

                if d1 != d2 + 1 and not gap_begin:
                    gap_begin = d2 + 1

                if d1 == d2 + 1 and gap_begin:
                    days = ((d2-1).mjd - gap_begin.mjd)+1
                    if days > ignore_val:
                        gaps.append('%s.%s gap in data found %s -> %s (%i days)'
                                    % (NetworkCode, StationCode, gap_begin.yyyyddd(), (d2-1).yyyyddd(), days))

                    gap_begin = None

        if gaps:
            sys.stdout.write('\nData gaps in %s.%s follow:\n' % (NetworkCode, StationCode))
            sys.stdout.write('\n'.join(gaps) + '\n')
        else:
            sys.stdout.write('\nNo data gaps found for %s.%s\n' % (NetworkCode, StationCode))


def PrintStationInfo(cnn, stnlist, short=False):

    for stn in stnlist:
        NetworkCode = stn['NetworkCode']
        StationCode = stn['StationCode']

        try:
            stninfo = station_info.StationInfo(cnn, NetworkCode, StationCode)

            if short:
                sys.stdout.write('\n' + stninfo.return_stninfo_short() + '\n\n')
            else:
                sys.stdout.write('# ' + NetworkCode.upper() + '.' + StationCode.upper() +
                                 '\n' + stninfo.return_stninfo() + '\n')

        except station_info.StationInfoException as e:
            sys.stderr.write(str(e) + '\n')


def visualize_gaps(cnn, stnlist, start_date, end_date):

    for Stn in stnlist:
        stn_id = '%s.%s' % (Stn['NetworkCode'], Stn['StationCode'])
        print(' >> Calculating gaps for %s' % stn_id)

        missing_doys, possible_doys = GetGaps(cnn, Stn['NetworkCode'], Stn['StationCode'],
                                              start_date, end_date)

        if len(possible_doys) == 0:
            print(' -- %s has no data' % stn_id)
            continue

        sys.stdout.write(' -- %s: (First and last observation in timespan: %i %03i - %i %03i)\n'
                         % (stn_id,
                            possible_doys[0].year,  possible_doys[0].doy,
                            possible_doys[-1].year, possible_doys[-1].doy))

        # make a group per year
        for year in sorted({d.year for d in possible_doys}):

            missing_dates = {m.doy for m in missing_doys  if m.year == year}
            p_doys        = [m.doy for m in possible_doys if m.year == year]

            sys.stdout.write('\n%i:\n    %03i>' % (year, p_doys[0]))

            if len(p_doys) / 2. > 120:
                cut_len = int(ceil(len(p_doys) / 4.))
            else:
                cut_len = len(p_doys)

            for i, doy in enumerate(zip(p_doys[0:-1:2], p_doys[1::2])):

                if doy[0] not in missing_dates:
                    if doy[1] not in missing_dates:
                        c = chr(0x2588)
                    else:
                        c = chr(0x258C)
                else:
                    if doy[1] not in missing_dates:
                        c = chr(0x2590)
                    else:
                        c = ' '

                sys.stdout.write(c)

                if i+1 == cut_len:
                    sys.stdout.write('<%03i\n' % doy[0])
                    sys.stdout.write('    %03i>' % (doy[0]+1))

            if len(p_doys) % 2 != 0:
                # last one missing
                if p_doys[-1] not in missing_dates:
                    sys.stdout.write(chr(0x258C))
                else:
                    sys.stdout.write(' ')

                if cut_len < len(p_doys):
                    sys.stdout.write('< %03i\n' % (p_doys[-1]))
                else:
                    sys.stdout.write('<%03i\n' % (p_doys[-1]))
            else:
                sys.stdout.write('<%03i\n' % (p_doys[-1]))

        print('')
        sys.stdout.flush()


def exclude_solutions(cnn, stnlist, start_date, end_date):

    for stn in stnlist:
        NetworkCode = stn['NetworkCode']
        StationCode = stn['StationCode']

        rs = cnn.query(
            'SELECT * FROM ppp_soln WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\' '
            'AND ("Year", "DOY") BETWEEN (%i, %i) AND (%i, %i)' %
            (NetworkCode, StationCode, start_date.year, start_date.doy, end_date.year, end_date.doy))

        ppp = rs.dictresult()
        tqdm.write(' >> Inserting solutions in excluded table for station %s.%s' % (NetworkCode, StationCode))
        for soln in tqdm(ppp):
            try:
                cnn.insert('ppp_soln_excl', NetworkCode=NetworkCode, StationCode=StationCode,
                           Year=soln['Year'], DOY=soln['DOY'])
            except dbConnection.dbErrInsert:
                tqdm.write('PPP solution for %i %i is already in the excluded solutions table\n'
                           % (soln['Year'], soln['DOY']))


def main():

    parser = argparse.ArgumentParser(description='Database integrity tools, metadata check and fixing tools program')

    parser.add_argument('stnlist', type=str, nargs='+', metavar='all|net.stnm',
                        help=station_list_help())

    parser.add_argument('-d', '--date_filter', nargs='+', metavar='date',
                        help='Date range filter for all operations. '
                             'Can be specified in wwww-d, yyyy_ddd, yyyy/mm/dd or fyear format')

    parser.add_argument('-rinex', '--check_rinex', choices=['fix', 'report'], type=str, nargs=1,
                        help='Check the RINEX integrity of the archive-database by verifying that the RINEX files '
                             'reported in the rinex table exist in the archive. If argument = "fix" and a RINEX file '
                             'does not exist, remove the record. PPP records or gamit_soln are deleted. If argument = '
                             '"report" then just list the missing files.')

    parser.add_argument('-rnx_count', '--rinex_count', action='store_true',
                        help='Count the total number of RINEX files (unique station-days) '
                             'per day for a given time interval.')

    parser.add_argument('-stnr', '--station_info_rinex', action='store_true',
                        help='Check that the receiver serial number in the rinex headers agrees with the station info '
                             'receiver serial number.')

    parser.add_argument('-stns', '--station_info_solutions', action='store_true',
                        help='Check that the PPP hash values match the station info hash.')

    parser.add_argument('-stnp', '--station_info_proposed', metavar='ignore_days', const=0, type=int, nargs='?',
                        help='Output a proposed station.info using the RINEX metadata. Optional, specify [ignore_days] '
                             'to ignore station.info records <= days.')

    parser.add_argument('-stnc', '--station_info_check', action='store_true',
                        help='Check the consistency of the station information records in the database. Date range '
                             'does not apply. Also, check that the RINEX files fall within a valid station information '
                             'record.')

    parser.add_argument('-g', '--data_gaps', metavar='ignore_days', const=0, type=int, nargs='?',
                        help='Check the RINEX files in the database and look for gaps (missing days). '
                             'Optional, [ignore_days] with the smallest gap to display.')

    parser.add_argument('-gg', '--graphical_gaps', action='store_true',
                        help='Visually output RINEX gaps for stations.')

    parser.add_argument('-sc', '--spatial_coherence', choices=['exclude', 'delete', 'noop'], type=str, nargs=1,
                        help='Check that the RINEX files correspond to the stations they are linked to using their '
                             'PPP coordinate. If keyword [exclude] or [delete], add the PPP solution to the excluded '
                             'table or delete the PPP solution. If [noop], then only report but do not '
                             'exlude or delete.')

    parser.add_argument('-print', '--print_stninfo', choices=['long', 'short'], type=str, nargs=1,
                        help='Output the station info to stdout. [long] outputs the full line of the station info. '
                             '[short] outputs a short version (better for screen visualization).')

    parser.add_argument('-r', '--rename', metavar='net.stnm', nargs=1,
                        help="Takes the data from the station list and renames (merges) it to net.stnm. "
                             "It also changes the rinex filenames in the archive to match those of the new destiny "
                             "station. Only a single station can be given as the origin and destiny. "
                             "Limit the date range using the -d option. If origin station is empty (no RINEX files) "
                             "after rename/merge, the station is deleted from the database if --delete_station "
                             "is invoked.")

    parser.add_argument('-del_stn', '--delete_station', action='store_true', default=False,
                        help='Switch to enable station deletion after rename/merge operation. Only works when invoking '
                             '--rename.')

    parser.add_argument('-es', '--exclude_solutions', metavar=('{start_date}', '{end_date}'), nargs=2,
                        help='Exclude PPP solutions (by adding them to the excluded table) between {start_date} '
                             'and {end_date}')

    parser.add_argument('-del', '--delete_rinex', metavar=('{start_date}', '{end_date}', '{completion}'), nargs=3,
                        help='Delete RINEX files (and associated solutions, PPP and GAMIT) '
                             'from archive between {start_date} and {end_date} with completion <= {completion}. '
                             'Completion ranges form 1.0 to 0.0. Use 1.0 to delete all data. '
                             'Operation cannot be undone!')

    parser.add_argument('-np', '--noparallel', action='store_true',
                        help="Execute command without parallelization.")

    add_version_argument(parser)

    args = parser.parse_args()

    cnn = dbConnection.Cnn("gnss_data.cfg")  # type: dbConnection.Cnn

    # create the execution log
    cnn.insert('executions', script='pyIntegrityCheck.py')

    Config    = pyOptions.ReadOptions("gnss_data.cfg")  # type: pyOptions.ReadOptions

    stnlist   = Utils.process_stnlist(cnn, args.stnlist)

    JobServer = pyJobServer.JobServer(Config, run_parallel=not args.noparallel, check_executables=False,
                                      check_archive=bool(args.rename or args.delete_rinex or args.check_rinex),
                                      check_atx=False)  # type: pyJobServer.JobServer

    #####################################
    # date filter

    dates = [pyDate.Date(year=1980, doy=1),
             pyDate.Date(year=2100, doy=1)]
    try:
        dates = process_date(args.date_filter)
    except ValueError as e:
        parser.error(str(e))

    #####################################

    if args.check_rinex:
        check_rinex_integrity(cnn, Config, stnlist, dates[0], dates[1], args.check_rinex[0], JobServer)

    #####################################

    if args.rinex_count:
        RinexCount(cnn, stnlist, dates[0], dates[1])

    #####################################

    if args.station_info_rinex:
        stn_info_rinex_integrity(cnn, stnlist, dates[0], dates[1], JobServer)

    #####################################

    if args.station_info_check:
        stn_info_check(cnn, stnlist, Config)

    #####################################

    if args.data_gaps is not None:
        get_stn_gaps(cnn, stnlist, args.data_gaps, dates[0], dates[1])

    if args.graphical_gaps:
        visualize_gaps(cnn, stnlist, dates[0], dates[1])

    #####################################

    if args.spatial_coherence is not None:
        check_spatial_coherence(cnn, stnlist, dates[0], dates[1])

    #####################################

    if args.exclude_solutions is not None:
        try:
            dates = process_date(args.exclude_solutions)
        except ValueError as e:
            parser.error(str(e))

        exclude_solutions(cnn, stnlist, dates[0], dates[1])

    #####################################

    if args.print_stninfo is not None:
        if args.print_stninfo[0] == 'short':
            PrintStationInfo(cnn, stnlist, True)
        elif args.print_stninfo[0] == 'long':
            PrintStationInfo(cnn, stnlist, False)
        else:
            parser.error('Argument for print_stninfo has to be either long or short')

    #####################################

    if args.station_info_proposed is not None:
        for stn in stnlist:
            stninfo = station_info.StationInfo(cnn, stn['NetworkCode'], stn['StationCode'], allow_empty=True)
            sys.stdout.write(stninfo.rinex_based_stninfo(args.station_info_proposed))

    #####################################

    if args.delete_rinex is not None:
        try:
            dates = process_date(args.delete_rinex[0:2])
        except ValueError as e:
            parser.error(str(e))

        pyArchiveStruct.RinexStruct(cnn).delete_rinex(stnlist, dates[0], dates[1], float(args.delete_rinex[2]))

    #####################################

    if args.rename:
        if len(stnlist) > 1:
            parser.error('Only a single station should be given for the origin station')

        if '.' not in args.rename[0]:
            parser.error('Format for destiny station should be net.stnm')
        else:
            dest_network_code = args.rename[0].split('.')[0]
            dest_station_code = args.rename[0].split('.')[1]

            pyArchiveStruct.RinexStruct(cnn).rename_station(stnlist[0]['NetworkCode'], stnlist[0]['StationCode'],
                                                            dest_network_code, dest_station_code,
                                                            dates[0], dates[1], args.delete_station)

    JobServer.close_cluster()


if __name__ == '__main__':
    main()
