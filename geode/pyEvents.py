"""
Project: Geodetic Database Engine (GeoDE)
Date: 10/25/17 8:53 AM
Author: Demian D. Gomez

Class to manage (insert, create and query) events produced by the Parallel.PPP wrapper
"""
import datetime
import platform
import traceback
import inspect
import re


_STACK_NOISE = ('/threading.py', '/multiprocessing/', '/pycos/', '/dispy.py', '/dispynode.py')


class Event(dict):

    def __init__(self, filter_stack=True, **kwargs):

        dict.__init__(self)

        self['EventDate']   = datetime.datetime.now()
        self['EventType']   = 'info'
        self['NetworkCode'] = None
        self['StationCode'] = None
        self['Year']        = None
        self['DOY']         = None
        self['Description'] = ''
        self['node']        = platform.node()
        self['stack']       = None

        module = inspect.getmodule(inspect.stack()[1][0])
        stack  = traceback.extract_stack()[0:-2]

        if module is None:
            self['module'] = inspect.stack()[1][3]  # just get the calling module
        else:
            # self['module'] = module.__name__ + '.' + inspect.stack()[1][3]  # just get the calling module
            self['module'] = module.__name__ + '.' + stack[-1][2]  # just get the calling module

        # initialize the dictionary based on the input
        for key in kwargs:
            if key not in self.keys():
                raise Exception('Provided key not in list of valid fields.')

            arg = kwargs[key]
            self[key] = arg

        if self['EventType'] == 'error':
            # DDG: filter_stack (on by default) drops frames belonging to threading/multiprocessing/
            # dispy/pycos worker-dispatch plumbing -- when an error is raised inside a dispy worker
            # (e.g. a PPP job), that's dozens of boilerplate frames burying the one that actually
            # matters. Falls back to the full, unfiltered stack if every frame turns out to be
            # infrastructure, so nothing is ever silently hidden; pass filter_stack=False to opt out.
            frames = stack
            if filter_stack:
                relevant = [f for f in stack if not any(n in f.filename for n in _STACK_NOISE)]
                if relevant:
                    frames = relevant
            self['stack'] = ''.join(traceback.format_list(frames))  # print the traceback until just before this call
        else:
            self['stack'] = None

        

    def db_dict(self):
        # remove any invalid chars that can cause problems in the database
        # also, remove the timestamp so that we use the default now() in the databasae
        # out of sync clocks in nodes can cause problems.
        val = self.copy()
        val.pop('EventDate')

        for key in val:
            s = val[key]
            if type(s) is str:
                # Remove NULL bytes and problematic control characters
                # Keep: tab (\x09), newline (\x0a), carriage return (\x0d)
                # Keep: all printable ASCII and Unicode (including accented chars)
                # Old line removed all non-ASCII including accented letters:
                # s = re.sub(r'[^\x00-\x7f]+', '', s)
                s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
                s = s.replace('\'', '"')
                s = re.sub(r'BASH.*', '', s)
                s = re.sub(r'PSQL.*', '', s)
                val[key] = s

        return val

    def __repr__(self):
        return 'pyEvent.Event(%s)' % str(self['Description'])

    def __str__(self):
        return str(self['Description'])

