"""
Project: Geodetic Database Engine (GeoDE)
Date: 3/21/17 5:36 PM
Author: Demian D. Gomez

Class with all the configuration information necessary to run many of the
scripts. It loads the config file (gnss_data.cfg).
"""

import configparser
import os

# app
from .Utils import process_date, file_open, create_empty_cfg
from .pyDate import Date


class ReadOptions:
    def __init__(self, configfile, write_cfg_file=False):

        self.options = {'path'                 : None,
                        'media'                : None,
                        'repository'           : None,
                        'format_scripts_path'  : '/tmp', 
                        'parallel'             : False,
                        'cluster_delay'        : 90,
                        'cups'                 : None,
                        'node_list'            : None,
                        'ip_address'           : None,
                        'brdc'                 : None,
                        'ionex'                : None,
                        'sp3_ac'               : ['IGS', 'JPL'],
                        'sp3_cs'               : ['R03', 'R02', 'OPS'],
                        'sp3_st'               : ['FIN', 'RAP'],
                        'sp3_type_1'           : None,
                        'sp3_type_2'           : None,
                        'sp3_type_3'           : None,
                        'sp3_altr_1'           : None,
                        'sp3_altr_2'           : None,
                        'sp3_altr_3'           : None,
                        'grdtab'               : None,
                        'otlgrid'              : None,
                        'otlmodel'             : 'FES2014b',
                        'ppp_path'             : None,
                        'institution'          : None,
                        'info'                 : None,
                        'sp3'                  : None,
                        'frames'               : None,
                        'atx'                  : None,
                        'height_codes'         : None,
                        'ppp_exe'              : None,
                        'ppp_remote_local'     : (),
                        'ppp_engine'           : 'gpspace',
                        'pride_exe'            : None,
                        'pride_table'          : None}

        config = configparser.ConfigParser()

        try:
            with file_open(configfile) as f:
                config.read_file(f)
        except FileNotFoundError:
            if write_cfg_file:
                create_empty_cfg()
                print(' >> No gnss_data.cfg file found, an empty one has been created. Replace all the necessary '
                      'config and try again.')
                exit(1)
            else:
                raise

        # get the archive config
        self.options.update(dict(config.items('archive')))

        # get the otl config
        self.options.update(dict(config.items('otl')))

        # get the ppp config
        for iconfig, val in dict(config.items('ppp')).items():
            self.options[iconfig] = os.path.expandvars(val).replace('//', '/')

        # frames and dates
        frames = [item.strip() for item in self.options['frames'].split(',')]
        atx    = [item.strip() for item in self.options['atx'].split(',')]

        self.Frames = []

        for frame, atx in zip(frames, atx):
            date = process_date(self.options[frame.lower()].split(','))
            self.Frames += [{'name' : frame,
                             'atx'  : atx,
                             'dates': (Date(year=date[0].year, doy=date[0].doy, hour=0 , minute=0,  second=0),
                                       Date(year=date[1].year, doy=date[1].doy, hour=23, minute=59, second=59))}]

        self.options['frames'] = self.Frames

        self.archive_path        = self.options['path']
        self.media               = self.options['media']
        self.sp3_path            = self.options['sp3']
        self.brdc_path           = self.options['brdc']
        self.ionex_path          = self.options['ionex']
        self.repository          = self.options['repository']
        self.format_scripts_path = self.options['format_scripts_path']
        
        self.repository_data_in       = os.path.join(self.repository, 'data_in')
        self.repository_data_in_retry = os.path.join(self.repository, 'data_in_retry')
        self.repository_data_reject   = os.path.join(self.repository, 'data_rejected')

        # build the sp3types based on the provided options
        self.sp3types = []
        for ac in self.options['sp3_ac'].split(','):
            for cs in self.options['sp3_cs'].split(','):
                for st in self.options['sp3_st'].split(','):
                    self.sp3types.append(ac.strip().upper() + '[0-9]' +
                                         cs.strip().upper() +
                                         st.strip().upper() + '_{YYYYDDD}0000_{PER}_{INT}_')

        # repeat the types but for repro2 orbits (short names), in case repro3 do not exist
        for ac in self.options['sp3_ac'].split(','):
            # get the ACs' last letter
            last_letter = ac[-1].lower()
            # form the old-style (ig2, igs, igr) product AC filename
            for ll in ['2', last_letter, 'r']:
                self.sp3types.append(ac[0:2].lower() + ll + '{WWWWD}')

        self.run_parallel = (self.options['parallel'] == 'True')
        self.cluster_delay = int(self.options['cluster_delay'])

