#!/usr/bin/env python
"""
Project: Geodetic Database Engine (GeoDE)
Date: 11/8/17 9:24 AM
Author: Demian D. Gomez

Script to synchronize sp3 and brdc orbits using the CDDIS FTP server.
"""

import os
import argparse
import shutil
from datetime import datetime
import ftplib
import re

# deps
import numpy as np
from tqdm import tqdm

# app
from geode import pyDate
from geode import pyOptions
from geode.Utils import required_length, process_date, add_version_argument
from geode import pyRunWithRetry

# Old FTP server:
# FTP_HOST = '198.118.242.40'

# New FTP-SSL server:
FTP_HOST = 'gdc.cddis.eosdis.nasa.gov'
FTP_USER = 'Anonymous'
FTP_PASS = 'gomez.124@osu.edu'
OPERA_FOLDER = '/pub/gps/products/$gpsweek'
REPRO_FOLDER = '/pub/gps/products/$gpsweek/repro3'

# Now downloading orbits from CODE
# FTP_HOST = 'ftp.aiub.unibe.ch'
# FTP_USER = ''
# FTP_PASS = ''
# OPERA_FOLDER = '/CODE/$year'
# REPRO_FOLDER = '/REPRO_2020/CODE/$year'

# @todo
# METHOD = 'HTTP'   # 'FTP' | 'HTTP'
# Taken from https://wiki.earthdata.nasa.gov/display/EL/How+To+Access+Data+With+Python
# http://sgf.rgo.ac.uk/forumNESC/index.php?topic=58.0
# *?list


def main():
    parser = argparse.ArgumentParser(description = 'Synchronize orbit archive')

    parser.add_argument('-date', '--date_range', nargs='+', action=required_length(1, 2), metavar='date_start|date_end',
                        help="Date range to check given as [date_start] or [date_start] and [date_end]. "
                             "Allowed formats are yyyy.doy or yyyy/mm/dd..")

    parser.add_argument('-win', '--window', nargs=1, metavar='days', type=int,
                        help="Download data from a given time window determined by today - {days}.")

    add_version_argument(parser)

    try:
        args   = parser.parse_args()
        Config = pyOptions.ReadOptions('gnss_data.cfg')

        dates = ()
        now   = datetime.now()
        try:
            if args.window:
                # today - ndays
                d = pyDate.Date( year = now.year,
                                month = now.month,
                                 day  = now.day)
                dates = (d - int(args.window[0]), d)
            else:
                dates = process_date(args.date_range)
        except ValueError as e:
            parser.error(str(e))

        if dates[0] < pyDate.Date(gpsWeek=650, gpsWeekDay=0):
            dates = (pyDate.Date(gpsWeek=650, gpsWeekDay=0),
                     pyDate.Date(year  = now.year,
                                 month = now.month,
                                 day  =  now.day))

        # go through the dates
        # DDG: dates[1].mjd + 1 to include the last day
        drange = np.arange(dates[0].mjd, dates[1].mjd + 1, 1)

        pbar = tqdm(desc='%-30s' % ' >> Synchronizing orbit files', total=len(drange), ncols=160, disable=None)

        # connect to ftp
        ftp = ftplib.FTP_TLS(FTP_HOST, FTP_USER, FTP_PASS)
        # ftp = ftplib.FTP(FTP_HOST, FTP_USER, FTP_PASS)
        # ftp.login()
        ftp.set_pasv(True)
        ftp.prot_p()

        def downloadIfMissing(ftp_list, ftp_filename, local_filename, local_dir, desc):
            mark_path = os.path.join(local_dir, local_filename)
            if not os.path.isfile(mark_path) and ftp_filename in ftp_list:
                tqdm.write('%-31s: %s' % (' -- trying to download ' + desc, ftp_filename))
                down_path = os.path.join(local_dir, ftp_filename)
                with open(down_path, 'wb') as f:
                    ftp.retrbinary("RETR " + ftp_filename, f.write)
                return True
            else:
                return False

        def replace_vars(archive, date):
            return archive.replace('$year',     str(date.year)) \
                          .replace('$doy',      str(date.doy).zfill(3)) \
                          .replace('$gpsweek',  str(date.gpsWeek).zfill(4)) \
                          .replace('$gpswkday', str(date.gpsWeekDay))

        for date in (pyDate.Date(mjd=mdj) for mdj in drange):

            sp3_archive = replace_vars(Config.sp3_path, date)
            tqdm.write(' -- Working on local dir ' + sp3_archive)
            if not os.path.exists(sp3_archive):
                os.makedirs(sp3_archive)

            # because the CODE FTP has all files in a single directory, list and then search for all desired elements
            opera_folder = replace_vars(OPERA_FOLDER, date)
            repro_folder = replace_vars(REPRO_FOLDER, date)
            # do not list again if in the same year

            try:
                ftp.cwd(opera_folder)
                opera_list = set(ftp.nlst())
            except ftplib.error_perm:
                # no operational dir?
                opera_list = ()

            try:
                ftp.cwd(repro_folder)
                repro_list = set(ftp.nlst())
            except ftplib.error_perm:
                # no repro dir
                repro_list = ()

            # first look for the operational product
            for sp3type in Config.sp3types:
                if sp3type[0].isupper():
                    # long name IGS format
                    sp3_filename = (sp3type.replace('{YYYYDDD}', date.yyyyddd(space=False)).
                                    replace('{INT}', '[0-1]5M').replace('{PER}', '01D') + 'ORB.SP3')
                    clk_filename = (sp3type.replace('{YYYYDDD}', date.yyyyddd(space=False)).
                                    replace('{INT}', '[0-3][0-5][SM]').replace('{PER}', '01D') + 'CLK.CLK')
                    eop_filename = (sp3type.replace('{YYYYDDD}', date.yyyyddd(space=False)).
                                    replace('{INT}', '01D').replace('{PER}', '07D') + '(?:ERP|ORB).ERP')
                    # DDG: ATT.OBX / OSB.BIA are the satellite attitude and code/phase bias products
                    # required by PRIDE PPP-AR. Only published in long-name (MGEX) format.
                    obx_filename = (sp3type.replace('{YYYYDDD}', date.yyyyddd(space=False)).
                                    replace('{INT}', '[0-3][0-5][SM]').replace('{PER}', '01D') + 'ATT.OBX')
                    bia_filename = (sp3type.replace('{YYYYDDD}', date.yyyyddd(space=False)).
                                    replace('{INT}', '01D').replace('{PER}', '01D') + 'OSB.BIA')
                else:
                    # short name IGS format -- no short-name convention for OBX/BIA
                    sp3_filename = sp3type.replace('{WWWWD}', date.wwwwd()) + '.sp3.Z'
                    clk_filename = sp3type.replace('{WWWWD}', date.wwwwd()) + '.clk.Z'
                    eop_filename = sp3type.replace('{WWWWD}', date.wwwwd()) + '.clk.Z'
                    obx_filename = None
                    bia_filename = None

                tqdm.write(' -- Checking in %s and %s for sp3, clock, erp, obx, and bia files' %
                          (opera_folder, repro_folder))

                for folder, ftp_list in [(opera_folder, opera_list), (repro_folder, repro_list)]:
                    # try to download SP3 files
                    try:
                        ftp.cwd(folder)

                        for ext, recmp in [('SP3', sp3_filename), ('CLK', clk_filename), ('ERP', eop_filename),
                                          ('OBX', obx_filename), ('BIA', bia_filename)]:
                            if recmp is None:
                                continue
                            r = re.compile('(' + recmp + ')')
                            match = list(filter(r.match, ftp_list))
                            for file in match:
                                downloadIfMissing(ftp_list, file, file, sp3_archive, ext)
                    except ftplib.error_perm:
                        continue

            # ##### now the brdc files #########
            folder = "/pub/gps/data/daily/%s/%s/%sn" % (date.yyyy(), date.ddd(), date.yyyy()[2:])
            tqdm.write(' -- Changing folder to ' + folder)
            try:
                ftp.cwd(folder)
                ftp_list = set(ftp.nlst())
            except Exception:
                ftp_list = ()

            brdc_archive = replace_vars(Config.brdc_path, date)

            if not os.path.exists(brdc_archive):
                os.makedirs(brdc_archive)
            try:
                filename = 'brdc%s0.%sn' % (str(date.doy).zfill(3), str(date.year)[2:4])
                for ext in ('.Z', '.gz'):
                    ftp_filename = filename + ext
                    if downloadIfMissing(ftp_list, ftp_filename, filename, brdc_archive, 'BRDC'):
                        # decompress file
                        tqdm.write('  -> Download succeeded %s' %  os.path.join(brdc_archive, ftp_filename))
                        pyRunWithRetry.RunCommand('gunzip -f ' + os.path.join(brdc_archive, ftp_filename),
                                                  15).run_shell()
            except Exception as e:
                tqdm.write(' -- BRDC ERROR: %s' % str(e))

            # ##### now the GLONASS-only broadcast nav file, an optional PRIDE PPP-AR fallback #####
            folder = "/pub/gps/data/daily/%s/%s/%sg" % (date.yyyy(), date.ddd(), date.yyyy()[2:])
            tqdm.write(' -- Changing folder to ' + folder)
            try:
                ftp.cwd(folder)
                ftp_list = set(ftp.nlst())
            except Exception:
                ftp_list = ()

            try:
                filename = 'brdc%s0.%sg' % (str(date.doy).zfill(3), str(date.year)[2:4])
                for ext in ('.Z', '.gz'):
                    ftp_filename = filename + ext
                    if downloadIfMissing(ftp_list, ftp_filename, filename, brdc_archive, 'BRDG'):
                        tqdm.write('  -> Download succeeded %s' % os.path.join(brdc_archive, ftp_filename))
                        pyRunWithRetry.RunCommand('gunzip -f ' + os.path.join(brdc_archive, ftp_filename),
                                                  15).run_shell()
            except Exception as e:
                tqdm.write(' -- BRDG ERROR: %s' % str(e))

            # ##### now the merged multi-GNSS broadcast nav file (BRDM), needed by PRIDE PPP-AR #####
            folder = "/pub/gps/data/daily/%s/%s/%sp" % (date.yyyy(), date.ddd(), date.yyyy()[2:])
            tqdm.write(' -- Changing folder to ' + folder)
            try:
                ftp.cwd(folder)
                ftp_list = set(ftp.nlst())
            except Exception:
                ftp_list = ()

            try:
                for prefix in ('BRDM00DLR_S_', 'BRDC00IGS_R_', 'BRDC00IGN_R_'):
                    filename = prefix + date.yyyyddd(space=False) + '0000_01D_MN.rnx'
                    for ext in ('.gz', '.Z'):
                        ftp_filename = filename + ext
                        if downloadIfMissing(ftp_list, ftp_filename, filename, brdc_archive, 'BRDM'):
                            tqdm.write('  -> Download succeeded %s' % os.path.join(brdc_archive, ftp_filename))
                            pyRunWithRetry.RunCommand('gunzip -f ' + os.path.join(brdc_archive, ftp_filename),
                                                      15).run_shell()
                            break
                    if os.path.isfile(os.path.join(brdc_archive, filename)):
                        break
            except Exception as e:
                tqdm.write(' -- BRDM ERROR: %s' % str(e))

            # ##### now the ionex files #########
            folder = "/pub/gps/products/ionex/%s/%s" % (date.yyyy(), date.ddd())
            tqdm.write(' -- Changing folder to ' + folder)
            try:
                ftp.cwd(folder)
                ftp_list = set(ftp.nlst())
            except ftplib.error_perm:
                ftp_list = ()

            ionex_archive = replace_vars(Config.ionex_path, date)

            if not os.path.exists(ionex_archive):
                os.makedirs(ionex_archive)
            try:
                # try the long name
                l_fname = f'IGS0OPSFIN_{date.yyyy()}{date.ddd()}0000_01D_02H_GIM.INX.gz'
                s_fname = 'igsg%s0.%si.Z' % (date.ddd(), str(date.year)[2:4])

                if not (os.path.exists(os.path.join(ionex_archive, l_fname)) or
                        os.path.exists(os.path.join(ionex_archive, s_fname))):
                    if downloadIfMissing(ftp_list, l_fname, l_fname, ionex_archive, 'IONEX'):
                        # try long name first
                        tqdm.write('  -> Download succeeded %s' % os.path.join(ionex_archive, l_fname))
                        # leave it zipped
                    elif downloadIfMissing(ftp_list, s_fname, s_fname, ionex_archive, 'IONEX'):
                        # try short name
                        tqdm.write('  -> Download succeeded %s' % os.path.join(ionex_archive, s_fname))
                        # leave it zipped, but change the name to the long name
                        shutil.move(os.path.join(ionex_archive, s_fname),
                                    os.path.join(ionex_archive, l_fname))

            except Exception as e:
                tqdm.write(' -- IONEX ERROR: %s' % str(e))

            pbar.set_postfix(gpsWeek='%i %i' % (date.gpsWeek, date.gpsWeekDay))
            pbar.update()

        pbar.close()
        ftp.quit()

    except argparse.ArgumentTypeError as e:
        parser.error(str(e))


if __name__ == '__main__':
    main()
