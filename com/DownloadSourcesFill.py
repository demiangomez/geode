#!/usr/bin/env python
"""
Project: Geodetic Database Engine (GeoDE)
Date: 11/23/2023 11:09 AM
Author: Demian D. Gomez

Program to fill the sources_stations table
using a probe to specific FTP servers
"""

import argparse
import ftplib
import os
import re
import sys

import numpy as np
from tqdm import tqdm

# app
from geode import dbConnection, pyDate
from geode.proto_download import Client
from geode.pyRinexName import path_replace_tags
from geode.Utils import (fqdn_parse, process_date, process_stnlist,
                          required_length, station_list_help, stationID, add_version_argument,
                          SmartHelpFormatter)


def query_yes_no(question, default="yes"):
    """Ask a yes/no question via raw_input() and return their answer.

    "question" is a string that is presented to the user.
    "default" is the presumed answer if the user just hits <Enter>.
            It must be "yes" (the default), "no" or None (meaning
            an answer is required of the user).

    The "answer" return value is True for "yes" or False for "no".
    code obtained from https://stackoverflow.com/questions/3041986/
                               apt-command-line-interface-like-yes-no-input
    """
    valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}
    if default is None:
        prompt = " [y/n] "
    elif default == "yes":
        prompt = " [Y/n] "
    elif default == "no":
        prompt = " [y/N] "
    else:
        raise ValueError("invalid default answer: '%s'" % default)

    while True:
        sys.stdout.write(question + prompt)
        choice = input().lower()
        if default is not None and choice == "":
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            sys.stdout.write(
                "Please respond with 'yes' or 'no' " "(or 'y' or 'n').\n")


def main():
    parser = argparse.ArgumentParser(
        description='Probe FTP server to find stations',
        formatter_class=SmartHelpFormatter)

    parser.add_argument('stnlist', type=str, nargs='+',
                        metavar='all|net.stnm',
                        help=SmartHelpFormatter.RAW_MARKER + station_list_help())

    parser.add_argument('-date', '--date_range', nargs='+',
                        action=required_length(1, 2),
                        metavar='date_start | date_end',
                        help='''Date range to probe ftp given as [date_start]
                             or [date_start] and [date_end].
                             If only [date_start] is given, then [date_end]
                             is today. If an integer is provided, then
                             [date_start] is today minus value provided.
                             Allowed formats are wwww-d, yyyy_ddd,
                             yyyy/mm/dd or fyear.''')

    parser.add_argument('-source', '--data_source',
                        metavar='fqdm | server_id', default=0,
                        help='''A fully qualified domain name (FQDM) or
                             server_id existing in the sources_servers table
                             to probe. If multiple sources use the same FQDM,
                             then all will be probed to search for
                             RINEX data matching the station list provided.
                             Be careful with hitting a single server
                             with too many requests!''')

    parser.add_argument('-skip', '--skip_stations_with_source',
                        action='store_true', default=False,
                        help='Remove stations with sources from the search.')

    parser.add_argument('-yes', '--force_yes',
                        action='store_true', default=False,
                        help='''Always accept a match
                             (without prompting yes/no).''')

    add_version_argument(parser)

    args = parser.parse_args()
    cnn = dbConnection.Cnn("gnss_data.cfg")

    stnlist = process_stnlist(cnn, args.stnlist,
                              summary_title='Stations requested:')

    dates = process_date(args.date_range)

    if args.skip_stations_with_source:
        # determine stations with sources
        rm_list = []
        for stn in stnlist:
            rs = cnn.query('''SELECT count(*) as c FROM sources_stations WHERE
                           "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''''
                           % (stn['NetworkCode'],
                              stn['StationCode'])).dictresult()[0]
            if rs['c'] > 0:
                rm_list.append(stn)

        stnlist = [stn for stn in stnlist if stn not in rm_list]

        if not len(stnlist):
            print(''' >> No stations without a source.
                  Nothing to do (-skip requested)''')
            return
        else:
            print(''' >> Stations with source removed.
                  Stations without source: %i''' % len(stnlist))

    # go through the dates
    drange = np.arange(dates[0].mjd, dates[1].mjd, 1)

    if not args.data_source:
        print('Error: a source server has to be provided!')
        return
    else:
        try:
            # try with the source number
            rs = cnn.query('''SELECT * FROM sources_servers
                           WHERE server_id = %i  ''' % int(args.data_source))
        except ValueError:
            # FQDN
            rs = cnn.query('SELECT * FROM sources_servers WHERE fqdn = \'%s\''
                           % args.data_source)

    if rs is not None:
        for svr in rs.dictresult():
            # connect to ftp
            tqdm.write(' >> Working with source %s://%s (server id: %i)'
                       % (svr['protocol'].lower(),
                          svr['fqdn'], svr['server_id']))

            host, port = fqdn_parse(svr['fqdn'])

            client = Client(None, None, svr['server_id'], svr['protocol'],
                            host, port, svr['username'], svr['password'])

            client.proto.connect()

            # determine the type of structure: date or station
            tqdm.write(' >> Searching source: %i potential loops...'
                       % (len(stnlist) * len(drange)))

            if ('${station}' in os.path.dirname(svr['path'])
                    or '${STATION}' in os.path.dirname(svr['path'])):
                # search has to be done
                # by station name (same station in directory)
                match_list = search_by_station(stnlist, drange, svr, client)
            else:
                # seach can be done by date (all station in directory)
                match_list = search_by_date(stnlist, drange, svr, client)

            # close connection to server
            client.proto.disconnect()

            tqdm.write(' >> Done searching, found %i matches for %s://%s\n'
                       % (len(match_list),
                          svr['protocol'].lower(), svr['fqdn']))

            for match in match_list:
                ask_add = False
                try_order = 0
                # see if station has this source already
                srcs = cnn.query('''SELECT sources_servers.fqdn,
                                 sources_stations.* FROM sources_stations
                                 LEFT JOIN sources_servers on
                                 sources_servers.server_id
                                 = sources_stations.server_id WHERE
                                 "NetworkCode" = \'%s\'
                                 AND "StationCode" = \'%s\'
                                 ORDER BY try_order'''
                                 % (match['NetworkCode'],
                                    match['StationCode'])).dictresult()

                if len(srcs):
                    if svr['server_id'] not in [s['server_id'] for s in srcs]:
                        tqdm.write(''' >> Station %s does not have this source
                                   (but these other):''' % stationID(match))
                        for src in srcs:
                            tqdm.write(' -- try order %i source: %s '
                                       % (src['try_order'], src['fqdn']))
                            try_order = src['try_order'] + 1
                        ask_add = True
                    else:
                        tqdm.write(''' >> Station %s already has %s
                                   as a source.\n'''
                                   % (stationID(match), svr['fqdn']))
                else:
                    tqdm.write(' >> No sources for %s' % stationID(match))
                    ask_add = True
                    try_order = 1

                if ask_add:
                    if args.force_yes:
                        # don't ask for user prompt
                        add = True
                    else:
                        if query_yes_no('''    Would like like to add %s
                                        as a source? (source filename %s)'''
                                        % (svr['fqdn'],
                                           match['filename']), None):
                            add = True
                        else:
                            add = False

                    if add:
                        cnn.insert('sources_stations',
                                   NetworkCode=match['NetworkCode'],
                                   StationCode=match['StationCode'],
                                   server_id=svr['server_id'],
                                   try_order=try_order)


def search_by_station(stnlist, drange, svr, client):
    match_list = []

    for stn in stnlist:
        # tqdm.write(' -- Looking for RINEX files for %s' % stationID(stn))
        for date in (pyDate.Date(mjd=mdj) for mdj in reversed(drange)):

            data_folder = path_replace_tags(svr['path'], date,
                                            stn['NetworkCode'],
                                            stn['StationCode'],
                                            stn['marker'],
                                            stn['country_code'])

            filename = os.path.basename(data_folder)

            # change directory to the provided date
            try:
                data_list = client.proto.list_dir(
                    os.path.dirname(data_folder) + '/')
                # tqdm.write(data_list)
                # now use the station information
                # to see if file is in the list
                if type(data_list) is set:
                    r = re.findall('(.*' + filename + '*)',
                                   '\n'.join(data_list))
                else:
                    r = re.findall('(.*' + filename + '*)', data_list)
                # match = list(filter(r.match, data_list))
                if r:
                    if stn not in match_list:
                        stn_match = stn
                        stn_match['filename'] = filename
                        match_list.append(stn_match)
                        sys.stdout.write('+')
                        sys.stdout.flush()
                    # if a match was found, stop looping
                    break
                else:
                    sys.stdout.write('-')
                    sys.stdout.flush()

            except ftplib.error_perm as e:
                if '550 CWD' in str(e):
                    sys.stdout.write('-')
                    sys.stdout.flush()
                    pass
                else:
                    raise
            except ftplib.error_proto as e:
                tqdm.write('Unexpected error: ' + str(e))

            except Exception as e:
                if '404' in str(e):
                    pass
                else:
                    tqdm.write('Unexpected error: ' + str(e))

    sys.stdout.write('\n')
    return match_list


def search_by_date(stnlist, drange, svr, client):
    match_list = []

    for date in (pyDate.Date(mjd=mdj) for mdj in reversed(drange)):
        data_folder = path_replace_tags(os.path.dirname(svr['path']), date)

        # change directory to the provided date
        data_list = client.proto.list_dir(data_folder + '/')

        for stn in stnlist:
            # once a match has been found, do not keep searching this station
            if stn not in match_list:
                filename = path_replace_tags(os.path.basename(svr['path']),
                                             date, stn['NetworkCode'],
                                             stn['StationCode'],
                                             stn['marker'],
                                             stn['country_code'])

                # now use the station information
                # to see if file is in the list
                if type(data_list) is set:
                    # convert sets to str
                    data_list = '\n'.join(data_list)
                match = re.search(filename, data_list)
                if match:
                    stn_match = stn
                    stn_match['filename'] = filename
                    match_list.append(stn_match)
                    sys.stdout.write('+')
                    sys.stdout.flush()
                else:
                    sys.stdout.write('-')
                    sys.stdout.flush()
            else:
                sys.stdout.write('-')
                sys.stdout.flush()
    sys.stdout.write('\n')
    return match_list


if __name__ == '__main__':
    main()
