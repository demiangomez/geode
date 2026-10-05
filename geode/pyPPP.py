"""
Project: Geodetic Database Engine (GeoDE)
Date: 2/21/17 3:34 PM
Author: Demian D. Gomez

Python wrapper for PPP processing. RunPPP() is a factory that dispatches to a concrete engine
(GPSPACE, the NRCAN PPP software; PRIDE, PRIDE PPP-AR) chosen via options['ppp_engine']
(default 'gpspace'). Both engines expose the same public surface (exec_ppp(), .record, .x/.y/.z,
.sigma*, .frame, .elevation_bins/.elevation_residuals, verify_spatial_coherence()) so callers do
not need to know which engine produced a solution.
"""
from abc import ABC, abstractmethod
from shutil import copyfile, rmtree
from math import isnan, sqrt
import os
import uuid
import re

# deps
import numpy

# app
from . import pyRinex
from . import pyProducts
from . import pyEvents
from . import pyRunWithRetry
from .pyDate import Date
from .Utils import lg2ct, ecef2lla, determine_frame, file_write, file_readlines

OBSERV_CODE_ONLY  = '1'
OBSERV_CODE_PHASE = '2'


def find_between(s, first, last):
    try:
        start = s.index(first) + len(first)
        end   = s.index(last, start)
        return s[start:end]
    except ValueError:
        return ""


class pyRunPPPException(Exception):
    def __init__(self, value):
        self.value = value
        self.event = pyEvents.Event(Description=value, EventType='error')

    def __str__(self):
        return str(self.value)


class pyRunPPPExceptionCoordConflict    (pyRunPPPException): pass
class pyRunPPPExceptionTooFewAcceptedObs(pyRunPPPException): pass
class pyRunPPPExceptionNaN              (pyRunPPPException): pass
class pyRunPPPExceptionZeroProcEpochs   (pyRunPPPException): pass
class pyRunPPPExceptionEOPError         (pyRunPPPException): pass
class pyRunPPPExceptionUnsupported      (pyRunPPPException): pass


class PPPSpatialCheck:

    def __init__(self, lat=None, lon=None, h=None, epoch=None):
        self.lat   = lat
        self.lon   = lon
        self.h     = h
        self.epoch = epoch

    def verify_spatial_coherence(self, cnn, StationCode, search_in_new=False):
        # checks the spatial coherence of the resulting coordinate
        # will not make any decisions, just output the candidates
        # if ambiguities are found, the rinex StationCode is used to solve them
        # third output arg is used to return a list with the closest station/s if no match is found
        # or if we had to disambiguate using station name
        # DDG Mar 21 2018: Added the velocity of the station to account for fast moving stations (on ice)
        # the logic is as follows:
        # 1) if etm data is available, then use it to bring the coordinate to self.epoch
        # 2) if no etm parameters are available, default to the coordinate reported in the stations table

        if not search_in_new:
            where_clause = 'WHERE "NetworkCode" not like \'?%%\''
        else:
            where_clause = ''

        # start by reducing the number of stations filtering everything beyond 100 km from the point of interest
        # rs = cnn.query("""
        #     SELECT * FROM
        #     (SELECT *, 2*asin(sqrt(sin((radians(%.8f)-radians(lat))/2)^2 + cos(radians(lat)) * cos(radians(%.8f)) *
        #     sin((radians(%.8f)-radians(lon))/2)^2))*6371000 AS distance
        #     FROM stations %s) as DD
        #     WHERE distance <= %f
        #     """ % (self.lat[0], self.lat[0], self.lon[0], where_clause, 1e3))  # DO NOT RETURN RESULTS
        #     WITH NetworkCode = '?%'

        rs = cnn.query("""
            SELECT st1."NetworkCode", st1."StationCode", st1."StationName", st1."DateStart", st1."DateEnd",
             st1."auto_x", st1."auto_y", st1."auto_z", st1."Harpos_coeff_otl", st1."lat", st1."lon", st1."height",
             st1."max_dist", st1."dome", st1.distance FROM
            (SELECT *, 2*asin(sqrt(sin((radians(%.8f)-radians(lat))/2)^2 + cos(radians(lat)) *
            cos(radians(%.8f)) * sin((radians(%.8f)-radians(lon))/2)^2))*6371000 AS distance
            FROM stations %s) as st1 left join stations as st2 ON
                st1."StationCode" = st2."StationCode" and
                st1."NetworkCode" = st2."NetworkCode" and
                st1.distance < coalesce(st2.max_dist, 20)
                WHERE st2."NetworkCode" is not NULL
            """ % (self.lat[0], self.lat[0], self.lon[0], where_clause))  # DO NOT RETURN RESULTS NetworkCode = '?%'

        stn_match = rs.dictresult()

        # using the list of coordinates, check if StationCode exists in the list
        if len(stn_match) == 0:
            # no match, find closest station
            # get the closest station and distance in km to help the caller function
            rs = cnn.query("""
                SELECT * FROM
                    (SELECT *, 2*asin(sqrt(sin((radians(%.8f)-radians(lat))/2)^2 + cos(radians(lat)) *
                    cos(radians(%.8f)) * sin((radians(%.8f)-radians(lon))/2)^2))*6371000 AS distance
                        FROM stations %s) as DD ORDER BY distance
                """ % (self.lat[0], self.lat[0], self.lon[0], where_clause))

            stn = rs.dictresult()

            return False, [], stn

        elif len(stn_match) == 1:
            if stn_match[0]['StationCode'] == StationCode:
                # one match, same name (return a dictionary)
                return True, stn_match, []
            else:
                # one match, not the same name (return a list, not a dictionary)
                return False, stn_match, []

        elif len(stn_match) > 1:
            # more than one match, same name
            # this is most likely a station that got moved a few meters and renamed
            # or a station that just got renamed.
            # disambiguation might be possible using the name of the station
            min_stn = [stni for stni in stn_match if stni['StationCode'] == StationCode]

            if len(min_stn) > 0:
                # the minimum distance if to a station with same name, we are good:
                # does the name match the closest station to this solution? yes
                return True, min_stn, []
            else:
                return False, stn_match, []


class PPPEngine(PPPSpatialCheck, ABC):
    """
    Common attribute surface and orchestration shared by all PPP engine backends.
    A subclass provides prepare_rinex() (any engine-specific RINEX pre-processing), stage()
    (product staging + control-file generation) and exec_ppp() (run + parse + retry policy).
    """

    def __init__(self, in_rinex, otl_coeff, options, sp3types, sp3altrn, antenna_height, strict=True,
                 apply_met=True, kinematic=False, clock_interpolation=False, hash=0, erase=True,
                 decimate=True, solve_coordinates=True, solve_troposphere=105, back_substitution=False,
                 elev_mask=10, x=0, y=0, z=0, observations=OBSERV_CODE_PHASE):

        # DDG: move this definition before anything else is called to avoid problems with object deletion in case the
        # pyRinex call below fails
        # generate a unique id for this instance
        self.rootdir = os.path.join(os.path.join('production', 'ppp'), str(uuid.uuid4()))

        self.antH      = antenna_height
        self.options   = options
        self.kinematic = kinematic

        self.ppp_version = None

        self.file_summary        = None
        self.proc_parameters     = None
        self.observation_session = None
        self.coordinate_estimate = None
        self.clock_estimates     = None

        self.frame             = None
        self.atx               = None
        # DDG: now can choose between code and code+phase observations
        self.observations      = observations
        # DDG: now accepts solving for a fixed coordinate PPP
        self.solve_coordinates = solve_coordinates
        # do not allow back_substitution or troposphere if code-only observations
        self.solve_troposphere = 1     if observations == OBSERV_CODE_ONLY else solve_troposphere
        self.back_substitution = False if observations == OBSERV_CODE_ONLY else back_substitution
        self.elev_mask         = elev_mask
        self.x                 = x
        self.y                 = y
        self.z                 = z
        self.lat               = None
        self.lon               = None
        self.h                 = None
        self.sigmax            = None
        self.sigmay            = None
        self.sigmaz            = None
        self.sigmaxy           = None
        self.sigmaxz           = None
        self.sigmayz           = None
        self.clock_phase       = None
        self.clock_phase_sigma = None
        self.phase_drift       = None
        self.phase_drift_sigma = None
        self.clock_rms         = None
        self.clock_rms_number  = None
        self.hash              = hash

        self.processed_obs = None
        self.rejected_obs  = None

        self.orbit_type    = None
        self.orbits1       = None
        self.orbits2       = None
        self.clocks1       = None
        self.clocks2       = None
        self.eop_file       = None
        self.sp3altrn      = sp3altrn
        self.sp3types      = sp3types
        self.otl_coeff     = otl_coeff
        self.strict        = strict
        self.apply_met     = apply_met
        self.erase         = erase
        self.out           = ''
        self.summary       = ''
        self.pos           = ''

        self.elevation_bins          = None
        self.elevation_residuals     = None
        self.elevation_residuals_std = None

        # DDG: 'FLOAT' or 'FIXED' (integer-fixed ambiguities). GPSPACE (ionosphere-free code+phase,
        # no ambiguity resolution) is always FLOAT, so 'FLOAT' is left as the default here rather
        # than set explicitly in that subclass; PRIDE overrides this in parse_summary() once it
        # knows whether ambiguity resolution was actually attempted and succeeded (depends on
        # ATT.OBX/OSB.BIA product availability, which varies per date/AC -- see PRIDE.get_orbits()).
        self.solution_type = 'FLOAT'

        # DDG: GNSS system letters (IGS convention: G/R/E/C/J) actually used in the solution. 'G'
        # is only a fallback default for the (unlikely) case a subclass's own parsing finds
        # nothing -- both engines override this once parsed: GPSPACE.parse_summary() (this build
        # is not GPS-only; it's an OSU-customized GPSPACE that also handles GLONASS/Galileo, per
        # section 3.2's per-constellation breakdown) and PRIDE.parse_res_file() (from the actual
        # satellite list of contributing PRNs).
        self.systems_used = 'G'

        assert isinstance(in_rinex, pyRinex.ReadRinex)

        rinexobj = self.prepare_rinex(in_rinex)

        # DDG: issue with JPL orbits: some files with one epoch after midnight of next day make PPP
        # crash when using JPL orbits. Clip to the nominal calendar day -- but clamp against (not
        # reset to) the day bounds, so a narrower window already applied upstream (e.g. LocateRinex's
        # -win) survives. A plain window_data(day_start, day_end) here would unconditionally stretch
        # any pre-windowed file back out to the full day.
        day_start = rinexobj.date.first_epoch('datetime')
        day_end   = rinexobj.date.last_epoch('datetime')
        rinexobj.window_data(max(rinexobj.datetime_firstObs, day_start),
                             min(rinexobj.datetime_lastObs, day_end))

        PPPSpatialCheck.__init__(self)

        self.rinex     = rinexobj
        self.epoch     = rinexobj.date

        # DDG: do not allow clock interpolation before May 1 2001
        # DDG: unless it is a code-only request, then MUST be turned on
        if observations == OBSERV_CODE_PHASE:
            self.clock_interpolation = clock_interpolation if rinexobj.date > Date(year=2001, month=5, day=1) else False
        else:
            # override user's decision, must be on to run
            self.clock_interpolation = True

        fieldnames = ('NetworkCode', 'StationCode', 'X', 'Y', 'Z', 'Year', 'DOY',
                      'ReferenceFrame', 'sigmax', 'sigmay',
                      'sigmaz', 'sigmaxy', 'sigmaxz', 'sigmayz', 'hash', 'orbit')

        self.record = dict.fromkeys(fieldnames)

        # determine the atx to use
        self.frame, self.atx = determine_frame(self.options['frames'], self.epoch)

        if os.path.isfile(self.rinex.rinex_path):

            try:
                # create a production folder to analyze the rinex file
                if not os.path.exists(self.rootdir):
                    os.makedirs(self.rootdir)
            except Exception:
                # could not create production dir! FATAL
                raise

            self.stage(decimate)
        else:
            raise pyRunPPPException('The file ' + self.rinex.rinex_path +
                                    ' could not be found. PPP was not executed.')

    @abstractmethod
    def prepare_rinex(self, in_rinex):
        """Return the ReadRinex object to actually process (may convert version, etc.)."""

    @abstractmethod
    def stage(self, decimate):
        """Fetch products, write control files and stage the RINEX copy in self.rootdir."""

    @abstractmethod
    def exec_ppp(self):
        """Run the engine (retrying per engine-specific policy), then populate self.record."""

    def check_phase_center(self, section):
        # default: no known phase-center issue to report; GPSPACE overrides this with a real check
        return True

    def load_record(self):

        self.record['NetworkCode']    = self.rinex.NetworkCode
        self.record['StationCode']    = self.rinex.StationCode
        self.record['X']              = self.x
        self.record['Y']              = self.y
        self.record['Z']              = self.z
        self.record['Year']           = self.rinex.date.year
        self.record['DOY']            = self.rinex.date.doy
        self.record['ReferenceFrame'] = self.frame
        self.record['sigmax']         = self.sigmax
        self.record['sigmay']         = self.sigmay
        self.record['sigmaz']         = self.sigmaz
        self.record['sigmaxy']        = self.sigmaxy
        self.record['sigmaxz']        = self.sigmaxz
        self.record['sigmayz']        = self.sigmayz
        self.record['hash']           = self.hash
        self.record['orbit']          = self.orbits1.archive_filename

    def cleanup(self):
        # DDG: subclasses may raise (e.g. missing options key) before PPPEngine.__init__ has run,
        # in which case self.rootdir/self.erase were never set -- guard __del__ against that.
        rootdir = getattr(self, 'rootdir', None)
        if rootdir and os.path.isdir(rootdir) and getattr(self, 'erase', True):
            # remove all the directory contents
            rmtree(rootdir)

    def __del__(self):
        self.cleanup()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def __enter__(self):
        return self


class GPSPACE(PPPEngine):
    """
    Wrapper for the NRCAN PPP software (GPSPACE / CSRS-PPP), the original engine behind RunPPP.
    """

    def __init__(self, in_rinex, otl_coeff, options, sp3types, sp3altrn, antenna_height, strict=True,
                 apply_met=True, kinematic=False, clock_interpolation=False, hash=0, erase=True,
                 decimate=True, solve_coordinates=True, solve_troposphere=105, back_substitution=False,
                 elev_mask=10, x=0, y=0, z=0, observations=OBSERV_CODE_PHASE):

        self.ppp_path = options['ppp_path']
        self.ppp      = options['ppp_exe']
        self.engine   = 'GPSPACE'

        PPPEngine.__init__(self, in_rinex, otl_coeff, options, sp3types, sp3altrn, antenna_height, strict,
                           apply_met, kinematic, clock_interpolation, hash, erase, decimate,
                           solve_coordinates, solve_troposphere, back_substitution, elev_mask, x, y, z,
                           observations)

    def prepare_rinex(self, in_rinex):
        # DDG: if RINEX 3 version, convert to RINEX 2 (no PPP support)
        if in_rinex.rinex_version >= 3:
            # DDG: make a new object and convert to RINEX 3 to leave the other one untouched
            rinexobj = pyRinex.ReadRinex(in_rinex.NetworkCode, in_rinex.StationCode, in_rinex.origin_file,
                                         no_cleanup=in_rinex.no_cleanup, allow_multiday=in_rinex.allow_multiday)
            rinexobj.ConvertRinex(2)
        else:
            # file is in RINEX 2 format, use file as is
            rinexobj = in_rinex

        return rinexobj

    def stage(self, decimate):

        path = os.path.join(self.rootdir, self.rinex.rinex[:-3])
        self.path_sum_file = path + 'sum'
        self.path_pos_file = path + 'pos'
        self.path_ses_file = path + 'ses'
        self.path_res_file = path + 'res'

        os.makedirs(os.path.join(self.rootdir, 'orbits'))

        try:
            self.get_orbits(self.sp3types)

        except (pyProducts.pySp3Exception,
                pyProducts.pyClkException,
                pyProducts.pyEOPException) as e:

            if self.sp3altrn:
                self.get_orbits(self.sp3altrn)
            else:
                raise type(e)(str(e) + ' -> This exception usually occurs due to the need of having '
                                      'the orbit for the day being processed and the orbit of the '
                                      'next day.')

        self.write_otl()
        self.copyfiles()
        self.config_session()

        # make a local copy of the rinex file
        # decimate the rinex file if the interval is < 15 sec.
        # DDG: only decimate when told by caller
        if self.rinex.interval < 15 and decimate:
            self.rinex.decimate(30)

        copyfile(self.rinex.rinex_path,
                 os.path.join(self.rootdir, self.rinex.rinex))

    def copyfiles(self):
        # prepare all the files required to run PPP
        files = ('gpsppp.stc', 'gpsppp.svb_gnss_yrly', 'gpsppp.flt')
        if self.apply_met:
            files = ('gpsppp.met',) + files

        for f in files:
            if os.path.exists(os.path.join(self.ppp_path, f)):
                copyfile(os.path.join(self.ppp_path, f),
                         os.path.join(self.rootdir,  f))
            else:
                if f == 'gpsppp.svb_gnss_yrly':
                    raise pyRunPPPException(f'Missing gpsppp.svb_gnss_yrly for PPP processing.')

        copyfile(os.path.join(self.atx),
                 os.path.join(self.rootdir, os.path.basename(self.atx)))

    def write_otl(self):
        file_write(os.path.join(self.rootdir, self.rinex.StationCode + '.olc'),
                   self.otl_coeff)

    def config_session(self):

        options = self.options

        # create the def file
        file_write(os.path.join(self.rootdir, 'gpsppp.def'),
                   "'LNG' 'ENGLISH'\n"
                   "'TRF' 'gpsppp.trf'\n"
                   "'SVB' 'gpsppp.svb_gnss_yrly'\n"
                   "'PCV' '%s'\n"
                   "'FLT' 'gpsppp.flt'\n"
                   "'OLC' '%s.olc'\n"
                   "'MET' 'gpsppp.met'\n"
                   "'ERP' '%s'\n"
                   "'GSD' '%s'\n"
                   "'GSD' '%s'\n"
                   % (os.path.basename(self.atx),
                      self.rinex.StationCode,
                      self.eop_file,
                      options['institution'],
                      options['info']))

        file_write(os.path.join(self.rootdir, 'commands.cmd'),
                   "' UT DAYS OBSERVED                      (1-45)'                   1\n"
                   "' USER DYNAMICS         (1=STATIC,2=KINEMATIC)'                   %s\n"
                   "' OBSERVATION TO PROCESS         (1=COD,2=C&P)'                   %s\n"
                   "' FREQUENCY TO PROCESS        (1=L1,2=L2,3=L3)'                   %s\n"
                   "' SATELLITE EPHEMERIS INPUT     (1=BRD ,2=SP3)'                   %s\n"
                   "' SATELLITE PRODUCT (1=NO,2=Prc,3=RTCA,4=RTCM)'                   2\n"
                   "' SATELLITE CLOCK INTERPOLATION   (1=NO,2=YES)'                   %s\n"
                   "' IONOSPHERIC GRID INPUT          (1=NO,2=YES)'                   1\n"
                   "' SOLVE STATION COORDINATES       (1=NO,2=YES)'                   %s\n"
                   "' SOLVE TROP. (1=NO,2-5=RW MM/HR) (+100=grad) '                   %i\n"
                   "' BACKWARD SUBSTITUTION           (1=NO,2=YES)'                   %s\n"
                   "' REFERENCE SYSTEM            (1=NAD83,2=ITRF)'                   2\n"
                   "' COORDINATE SYSTEM(1=ELLIPSOIDAL,2=CARTESIAN)'                   2\n"
                   "' A-PRIORI PSEUDORANGE SIGMA               (m)'              2.0000   9.00\n"
                   "' A-PRIORI CARRIER PHASE SIGMA             (m)'              0.0150   9.00\n"
                   "' LATITUDE  (ddmmss.sss,+N) or ECEF X      (m)'      %14.4f   0.000\n"
                   "' LONGITUDE (ddmmss.sss,+E) or ECEF Y      (m)'      %14.4f   0.000\n"
                   "' HEIGHT (m)                or ECEF Z      (m)'      %14.4f   0.000\n"
                   "' ANTENNA HEIGHT                           (m)'      %14.4f\n"
                   "' CUTOFF ELEVATION                       (deg)'      %14.4f\n"
                   "' GDOP CUTOFF                                 '             20.0000\n"
                   % ('1' if not self.kinematic else '2',
                      self.observations,
                      '1' if self.observations == OBSERV_CODE_ONLY else '3',
                      '1' if self.observations == OBSERV_CODE_ONLY else '2',
                      '1' if not self.clock_interpolation else '2',
                      '1' if not self.solve_coordinates else '2',
                      self.solve_troposphere,
                      '1' if not self.back_substitution else '2',
                      self.x, self.y, self.z,
                      self.antH, self.elev_mask))

        file_write(os.path.join(self.rootdir, 'input.inp'),
                   "%s\n"
                   "commands.cmd\n"
                   "0 0\n"
                   "0 0\n"
                   "orbits/%s\n"
                   "orbits/%s\n"
                   "orbits/%s\n"
                   "orbits/%s\n"
                   % (self.rinex.rinex, self.orbits1.filename, self.clocks1.filename,
                      self.orbits2.filename, self.clocks2.filename))

    def get_orbits(self, orbit_type):

        options = self.options

        orbits_path = os.path.join(self.rootdir, 'orbits')

        if self.observations == OBSERV_CODE_PHASE:
            orbits1 = pyProducts.GetSp3Orbits(options['sp3'], self.rinex.date,     orbit_type, orbits_path, True)
            orbits2 = pyProducts.GetSp3Orbits(options['sp3'], self.rinex.date + 1, orbit_type, orbits_path, True)

            clocks1 = pyProducts.GetClkFile(  options['sp3'], self.rinex.date,     orbit_type, orbits_path, True)
            clocks2 = pyProducts.GetClkFile(  options['sp3'], self.rinex.date + 1, orbit_type, orbits_path, True)
        else:
            # for code-only solution we get the BRDC orbit and we use the same information for all files.
            orbits1 = pyProducts.GetBrdcOrbits(options['brdc'], self.rinex.date, orbits_path, True)
            orbits2 = orbits1
            clocks1 = orbits1
            clocks2 = orbits1
        try:
            eop_file = pyProducts.GetEOP(options['sp3'], self.rinex.date, orbit_type, self.rootdir)
            eop_file = eop_file.filename
        except pyProducts.pyEOPException:
            # no eop, continue with out one
            eop_file = 'dummy.eop'

        self.orbits1    = orbits1
        self.orbits2    = orbits2
        self.clocks1    = clocks1
        self.clocks2    = clocks2
        self.eop_file   = eop_file
        # get the type of orbit
        self.orbit_type = orbits1.type
        # DDG: new -> add the value of the orbit hash to the PPP hash to include the orbit type
        self.hash += orbits1.hash

    def get_text(self, summary, start, end):
        copy = False

        if type(summary) is str:
            summary = summary.split('\n')

        out = []
        for line in summary:
            if start in line.strip():
                copy = True
            elif end in line.strip():
                copy = False
            elif copy:
                out += [line]

        return '\n'.join(out)

    @staticmethod
    def get_xyz(section):

        x = re.findall(r'X\s\(m\)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0][1]
        y = re.findall(r'Y\s\(m\)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0][1]
        z = re.findall(r'Z\s\(m\)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0][1]

        if '*' not in x and '*' not in y and '*' not in z:
            x = float(x)
            y = float(y)
            z = float(z)
        else:
            raise pyRunPPPExceptionNaN('One or more coordinate is NaN')

        if isnan(x) or isnan(y) or isnan(z):
            raise pyRunPPPExceptionNaN('One or more coordinate is NaN')

        return x, y, z

    @staticmethod
    def get_clock(section, kinematic):
        # DDG: TODO -> read if ms or ns and scale output accordingly
        try:
            clock_phase = re.findall(r'Clock Phase\s*\([nm]s\)\s*:\s*(-?\d+\.\d+)\s*(-?\d+\.\d+)',     section)[0]
            phase_drift = re.findall(r'Phase Drift\s*\([nm]s/day\)\s*:\s*(-?\d+\.\d+)\s*(-?\d+\.\d+)', section)[0]
            clock_resid = re.findall(r'RMS residuals\s*\([nm]s\)\s*:\s*(-?\d+\.\d+)\s*(\d+)',          section)[0]

        except IndexError:
            clock_phase = [0, 0]
            phase_drift = [0, 0]
            clock_resid = [0, 0]

        return float(clock_phase[0]), float(clock_phase[1]), \
               float(phase_drift[0]), float(phase_drift[1]), \
               float(clock_resid[0]), int(clock_resid[1])

    @staticmethod
    def get_sigmas(section, kinematic):

        if kinematic:

            sx = re.findall(r'X\s\(m\)\s+-?\d+\.\d+\s+-?\d+\.\d+\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0]
            sy = re.findall(r'Y\s\(m\)\s+-?\d+\.\d+\s+-?\d+\.\d+\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0]
            sz = re.findall(r'Z\s\(m\)\s+-?\d+\.\d+\s+-?\d+\.\d+\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0]

            if '*' not in sx and '*' not in sy and '*' not in sz:
                sx = float(sx)
                sy = float(sy)
                sz = float(sz)
                sxy = 0.0
                sxz = 0.0
                syz = 0.0
            else:
                raise pyRunPPPExceptionNaN('One or more sigma is NaN')

        else:
            sx, sxy, sxz = re.findall(r'X\(m\)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)'
                                      r'\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0]
            sy, syz      = re.findall(r'Y\(m\)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0]
            sz           = re.findall(r'Z\(m\)\s+(-?\d+\.\d+|[nN]a[nN]|\*+)', section)[0]

            if '*' in sx  or '*' in sy  or '*' in sz or \
               '*' in sxy or '*' in sxz or '*' in syz:
                raise pyRunPPPExceptionNaN('Sigmas are NaN')
            else:
                sx = float(sx)
                sy = float(sy)
                sz = float(sz)
                sxy = float(sxy)
                sxz = float(sxz)
                syz = float(syz)

        if isnan(sx)  or isnan(sy)  or isnan(sz) or \
           isnan(sxy) or isnan(sxz) or isnan(syz):
            raise pyRunPPPExceptionNaN('Sigmas are NaN')

        return sx, sy, sz, sxy, sxz, syz

    def get_pr_observations(self, section, kinematic):

        if self.ppp_version == '1.05':
            processed = re.findall(r'Number of epochs processed\s+\:\s+(\d+)', section)[0]
        else:
            processed = re.findall(r'Number of epochs processed \(%fix\)\s+\:\s+(\d+)', section)[0]

        if kinematic:
            rejected = re.findall(r'Number of epochs rejected\s+\:\s+(\d+)', section)
        else:
            # processed = re.findall('Number of observations processed\s+\:\s+(\d+)', section)[0]
            rejected = re.findall(r'Number of observations rejected\s+\:\s+(\d+)', section)

        if len(rejected) > 0:
            rejected = int(rejected[0])
        else:
            rejected = 0

        return int(processed), rejected

    # DDG: maps the constellation names this (OSU-customized) GPSPACE build prints in section 3.2
    # to IGS single-letter system codes, matching PRIDE's convention (systems_used).
    _CONSTELLATION_CODES = {'GPS': 'G', 'GLONASS': 'R', 'GALILEO': 'E', 'BEIDOU': 'C', 'QZSS': 'J'}

    @classmethod
    def get_systems_used(cls, section):
        systems = set()
        for line in section.split('\n'):
            if 'Number of satellites processed' not in line or ':' not in line:
                continue
            tokens = line.split(':', 1)[1].split()
            # the first (aggregate, all-systems) occurrence has no trailing constellation name
            if len(tokens) < 2:
                continue
            count, constellation = tokens[0], tokens[1]
            if constellation in cls._CONSTELLATION_CODES and count.isdigit() and int(count) > 0:
                systems.add(cls._CONSTELLATION_CODES[constellation])
        return ''.join(sorted(systems)) if systems else 'G'

    @staticmethod
    def check_phase_center(section):
        return not len(re.findall(r'Antenna phase center.+NOT AVAILABLE', section)) > 0

    @staticmethod
    def check_otl(section):
        return not len(re.findall(r'Ocean loading coefficients.+NOT FOUND', section)) > 0

    @staticmethod
    def check_eop(section):
        pole = re.findall(r'Pole X\s+.\s+(-?\d+\.\d+|[nN]a[nN])\s+(-?\d+\.\d+|[nN]a[nN])', section)
        return len(pole) <= 0 or \
            (type(pole[0]) is tuple and 'nan' not in pole[0][0].lower())

    @staticmethod
    def get_frame(section):
        return re.findall(r'\s+ITRF\s\((\s*\w+\s*)\)', section)[0].strip()

    def parse_summary(self):

        self.summary = ''.join(self.out)

        self.ppp_version = re.findall(r'.*Version\s+(\d.\d+)\/', self.summary)

        if len(self.ppp_version) == 0:
            self.ppp_version = re.findall(r'.*CSRS-PPP ver.\s+(\d.\d+)\/', self.summary)[0]
        else:
            self.ppp_version = self.ppp_version[0]

        self.file_summary        = self.get_text(self.summary,
                                                 'SECTION 1.',
                                                 'SECTION 2.')
        self.proc_parameters     = self.get_text(self.summary,
                                                 'SECTION 2. ',
                                                 ' SECTION 3. ')
        self.observation_session = self.get_text(self.summary,
                                                 '3.2 Observation Session',
                                                 '3.3 Coordinate estimates')
        self.coordinate_estimate = self.get_text(self.summary,
                                                 '3.3 Coordinate estimates',
                                                 '3.4 Coordinate differences ITRF')
        self.clock_estimates     = self.get_text(self.summary,
                                                 '3.5 Receiver clock estimates',
                                                 '3.6 Observation rejection table')
        if self.strict:
            if not self.check_phase_center(self.proc_parameters):
                raise pyRunPPPException(
                    'Error while running PPP: could not find the antenna and radome in antex file. '
                    'Check RINEX header for formatting issues in the ANT # / TYPE field. RINEX header follows:\n' +
                    ''.join(self.rinex.get_header()))

            if not self.check_otl(self.proc_parameters):
                raise pyRunPPPException(
                    'Error while running PPP: could not find the OTL coefficients. '
                    'Check RINEX header for formatting issues in the APPROX ANT POSITION field. If APR is too far '
                    'from OTL coordinates (declared in the HARPOS or BLQ format) NRCAN will reject the coefficients. '
                    'OTL coefficients record follows:\n' + self.otl_coeff)

        if not self.check_eop(self.file_summary):
            raise pyRunPPPExceptionEOPError('EOP returned NaN in Pole XYZ.')

        # parse rejected and accepted observations
        self.processed_obs, self.rejected_obs = self.get_pr_observations(self.observation_session, self.kinematic)

        if self.processed_obs == 0:
            raise pyRunPPPExceptionZeroProcEpochs('PPP returned zero processed epochs')

        # DDG: this OSU build of GPSPACE is not GPS-only -- section 3.2 repeats "Number of
        # satellites processed" once per constellation actually processed (the very first such
        # line, with no trailing constellation name, is the all-systems aggregate and is skipped
        # here). Only count a system as used if it actually processed at least one satellite.
        self.systems_used = self.get_systems_used(self.observation_session)

        # if self.strict and (self.processed_obs == 0 or self.rejected_obs > 0.95 * self.processed_obs):
        #    raise pyRunPPPExceptionTooFewAcceptedObs('The processed observations (' + str(self.processed_obs) +
        #                                             ') is zero or more than 95% of the observations were rejected (' +
        #                                             str(self.rejected_obs) + ')')

        # FRAME now comes from the startup process, where the function Utils.determine_frame is called
        # self.frame = self.get_frame(self.coordinate_estimate)

        self.x, self.y, self.z     = self.get_xyz(self.coordinate_estimate)
        self.lat, self.lon, self.h = ecef2lla([self.x, self.y, self.z])

        self.sigmax,  \
        self.sigmay,  \
        self.sigmaz,  \
        self.sigmaxy, \
        self.sigmaxz, \
        self.sigmayz = self.get_sigmas(self.coordinate_estimate, self.kinematic)

        self.clock_phase,       \
        self.clock_phase_sigma, \
        self.phase_drift,       \
        self.phase_drift_sigma, \
        self.clock_rms,         \
        self.clock_rms_number = self.get_clock(self.clock_estimates, self.kinematic)

        # not implemented in PPP: apply NE offset if is NOT zero
        if self.rinex.antOffsetN != 0.0 or \
           self.rinex.antOffsetE != 0.0:

            dx, dy, dz = lg2ct(numpy.array(self.rinex.antOffsetN),
                               numpy.array(self.rinex.antOffsetE),
                               numpy.array([0]),
                               self.lat, self.lon)
            # reduce coordinates
            self.x -= dx[0]
            self.y -= dy[0]
            self.z -= dz[0]
            self.lat, self.lon, self.h = ecef2lla([self.x, self.y, self.z])

    def parse_res_file(self):
        """
        Parse the PPP .res file and compute mean phase residuals (VCP column)
        binned by 1-degree elevation intervals, using BWD (backward substitution)
        epochs only.  Bins with no observations are set to NaN.

        Populates:
            self.elevation_bins          : numpy integer array [0, 1, ..., 90] (degrees)
            self.elevation_residuals     : numpy float array, mean VCP per 1-degree bin
            self.elevation_residuals_std : numpy float array, std dev of VCP per 1-degree bin
                                            (NaN where a bin has no observations, same as the mean)
        """
        if not os.path.isfile(self.path_res_file):
            return

        bwd_elev, bwd_res = [], []
        fwd_elev, fwd_res = [], []

        for line in file_readlines(self.path_res_file):
            direction = line[:3]
            if direction not in ('BWD', 'FWD'):
                continue
            parts = line.split()
            if len(parts) < 9:
                continue
            try:
                elev = float(parts[6])  # ELV column (7th field, 0-indexed)
                res  = float(parts[8])  # VCP column (9th field, 0-indexed)
            except ValueError:
                continue
            if direction == 'BWD':
                bwd_elev.append(elev)
                bwd_res.append(res)
            else:
                fwd_elev.append(elev)
                fwd_res.append(res)

        # prefer BWD; fall back to FWD if no backward substitution was performed
        elevations = bwd_elev if bwd_elev else fwd_elev
        residuals  = bwd_res  if bwd_elev else fwd_res

        if not elevations:
            return

        elevations = numpy.array(elevations)
        residuals  = numpy.array(residuals)

        # round to nearest integer degree and clamp to 0..90
        elev_bin = numpy.clip(numpy.round(elevations).astype(int), 0, 90)

        bins  = numpy.arange(0, 91)
        means = numpy.full(91, numpy.nan)
        stds  = numpy.full(91, numpy.nan)

        for deg in bins:
            mask = elev_bin == deg
            if numpy.any(mask):
                means[deg] = numpy.nanmean(residuals[mask])
                stds[deg]  = numpy.nanstd(residuals[mask])

        self.elevation_bins          = bins
        self.elevation_residuals     = means
        self.elevation_residuals_std = stds

    def __exec_ppp__(self, raise_error=True):

        try:
            # DDG: handle the error found in PPP (happens every now and then)
            # Fortran runtime error: End of file
            for i in range(2):
                out, err = pyRunWithRetry.RunCommand(self.ppp, 60, self.rootdir, 'input.inp').run_shell()

                if '*END - NORMAL COMPLETION' not in out:

                    if 'Fortran runtime error: End of file' in err and i == 0:
                        # error detected, try again!
                        continue

                    msg = 'PPP ended abnormally for ' + self.rinex.rinex_path + ':\n' + err + '\n' + out
                    if raise_error:
                        raise pyRunPPPException(msg)
                    else:
                        return False, msg
                else:
                    self.out = file_readlines(self.path_sum_file)
                    self.pos = file_readlines(self.path_pos_file)
                    break

        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            msg = str(e)
            if raise_error:
                raise pyRunPPPException(e)
            else:
                return False, msg
        except IOError as e:
            raise pyRunPPPException(e)

        return True, ''

    def exec_ppp(self):

        while True:
            # execute PPP but do not raise an error if timed out
            result, message = self.__exec_ppp__(False)

            if result:
                try:
                    self.parse_summary()
                    break

                except pyRunPPPExceptionEOPError:
                    # problem with EOP!
                    if self.eop_file != 'dummy.eop':
                        self.eop_file = 'dummy.eop'
                    else:
                        raise

                except (pyRunPPPExceptionNaN,
                        pyRunPPPExceptionTooFewAcceptedObs,
                        pyRunPPPExceptionZeroProcEpochs):

                    # DDG: only attempt reruns if OBSERV_CODE_PHASE
                    if self.observations == OBSERV_CODE_PHASE:
                        # Nan in the result
                        if not self.kinematic:
                            # first retry, turn to kinematic mode
                            self.kinematic = True

                        elif self.rinex.date.fyear >= 2001.33287 and not self.clock_interpolation:
                            # date has to be > 2001 May 1 (SA deactivation date)
                            self.clock_interpolation = True

                        elif self.sp3altrn and self.orbit_type not in self.sp3altrn:
                            # second retry, kinematic and alternative orbits (if exist)
                            self.get_orbits(self.sp3altrn)

                        else:
                            # it didn't work in kinematic mode either! raise error
                            raise
                    else:
                        raise
            elif self.sp3altrn and self.orbit_type not in self.sp3altrn:
                # maybe a bad orbit, fall back to alternative
                self.get_orbits(self.sp3altrn)
            else:
                raise pyRunPPPException(message)

            # reconfig and try again
            self.config_session()

        self.load_record()
        self.parse_res_file()


# Hand-written from a real pdp3-generated config file (PRIDE PPP-AR 3.2.10), keeping every section
# and the default satellite list / ambiguity-fixing block verbatim. Only the fields geode actually
# has a run-specific value for are substituted; everything else is left at PRIDE's own defaults.
_PRIDE_CONFIG_TEMPLATE = """# Configuration template for PRIDE PPP-AR 3

## Observation configuration
Frequency combination  = G12 R12 E15 C26 J12
Interval               = {interval:g}
Time window            = 0.01
Session time           = {session_time}
Table directory        = {table_dir}

## Satellite product
Product directory      = {product_dir}
Satellite orbit        = {sp3_filename}
Satellite clock        = {clk_filename}
ERP                    = {erp_filename}
Quaternions            = {obx_filename}
Code/phase bias        = {bia_filename}
LEO quaternions        = NONE

## Data processing strategies
Strict editing         = YES
RCK model              = WNO
ISB model              = NO
ZTD model              = {ztd_model}
HTG model              = {htg_model}
Iono 2nd               = NO
Tides                  = SOLID/OCEAN/POLE
Multipath              = NO

## Ambiguity fixing options
Ambiguity co-var        = YES
Ambiguity duration      = 600                    ! time duration in seconds for a resolvable ambiguity
AI Ambiguity validation = YES
Cutoff elevation        = 15                     ! cutoff mean elevation for eligible ambiguities to be resolved
PCO on wide-lane        = YES
Widelane decision       = 0.20 0.15 1000.        ! deviation (cycle), sigma (cycle) and decision threshold for WL ambiguities
Narrowlane decision     = 0.15 0.15 1000.        ! deviation (cycle), sigma (cycle) and decision threshold for NL ambiguities
Critical search         = 3 4 1.8 3.0            ! highest number of ambiguities to be excluded, lowest number to be reserved, fixed/float, ratio threshold
Truncate at midnight    = NO
Verbose output          = NO

## Satellite list
# Inserting `#' at the beginning of individual GNSS PRN means not to use this satellite
+GNSS satellites
*PRN variance
 G01   1
 G02   1
 G03   1
 G04   1
 G05   1
 G06   1
 G07   1
 G08   1
 G09   1
 G10   1
 G11   1
 G12   1
 G13   1
 G14   1
 G15   1
 G16   1
 G17   1
 G18   1
 G19   1
 G20   1
 G21   1
 G22   1
 G23   1
 G24   1
 G25   1
 G26   1
 G27   1
 G28   1
 G29   1
 G30   1
 G31   1
 G32   1
 R01   1
 R02   1
 R03   1
 R04   1
 R05   1
 R06   1
 R07   1
 R08   1
 R09   1
 R10   1
 R11   1
 R12   1
 R13   1
 R14   1
 R15   1
 R16   1
 R17   1
 R18   1
 R19   1
 R20   1
 R21   1
 R22   1
 R23   1
 R24   1
 E01   1
 E02   1
 E03   1
 E04   1
 E05   1
 E06   1
 E07   1
 E08   1
 E09   1
 E10   1
 E11   1
 E12   1
 E13   1
 E14   1
 E15   1
 E16   1
 E17   1
 E18   1
 E19   1
 E20   1
 E21   1
 E22   1
 E23   1
 E24   1
 E25   1
 E26   1
 E27   1
 E28   1
 E29   1
 E30   1
 E31   1
 E32   1
 E33   1
 E34   1
 E35   1
 E36   1
#C01   3
#C02   3
#C03   3
#C04   3
#C05   3
#C06   1
#C07   1
#C08   1
#C09   1
#C10   1
#C11   1
#C12   1
#C13   1
#C14   1
#C15   1
#C16   1
#C17   1
#C18   3
#C19   1
#C20   1
#C21   1
#C22   1
#C23   1
#C24   1
#C25   1
#C26   1
#C27   1
#C28   1
#C29   1
#C30   1
#C31   1
#C32   1
#C33   1
#C34   1
#C35   1
#C36   1
#C37   1
#C38   1
#C39   1
#C40   1
#C41   1
#C42   1
#C43   1
#C44   1
#C45   1
#C46   1
#C47   1
#C48   1
#C56   1
#C57   1
#C58   1
#C59   3
#C60   3
#C61   3
#J01   1
#J02   1
#J03   1
#J07   3
-GNSS satellites

## Option line
# There should be only one option line to be processed
# Arguments can be replaced by command-line automatically
# Available positioning mode:  S -- static
#                              P -- piec-wise
#                              K -- kinematic
#                              F -- fixed
# Available mapping function:  NIE -- Niell Mapping Function (NMF)
#                              GMF -- Global Mapping Function (GMF)
#                              VM1 -- Vienna Mapping Function (VMF1)
#                              VM3 -- Vienna Mapping Function (VMF3)
# Other arguments can be kept if you are not familiar with them
+Station used
*NAME TP MAP CLKm  PoDm EV ZTDm  PoDm HTGm  PoDm RAGm PHSc PoLns PoXEm PoYNm PoZHm
 {site:<4s} {mode} GMF 9000 0.000  {ev:.0f} 0.20 .0004 .005 0.002 0.30 0.01 300 {pos_sigma} {pos_sigma} {pos_sigma} 0
-Station used
"""


class PRIDE(PPPEngine):
    """
    Wrapper for PRIDE PPP-AR (pdp3), a carrier-phase PPP-AR engine. Unlike GPSPACE it supports
    RINEX 3/4 natively, resolves integer ambiguities, and computes ocean tide loading internally
    (options['pride_engine'] does not need otl_coeff -- it is accepted for interface parity only).

    ATT.OBX (satellite attitude) and OSB.BIA (code/phase bias) products are optional: not every
    analysis center publishes them and older dates rarely have them. When missing, they are set to
    NONE in the PRIDE config file rather than failing the run -- PRIDE PPP-AR still runs without
    them, with reduced fidelity (e.g. no ambiguity resolution).

    Broadcast navigation: the pre-merged multi-GNSS BRDM product (best quality) is only available
    from ~2013 onward (MGEX era). For older dates this falls back to staging the plain GPS (.n,
    required) and GLONASS (.g, optional) broadcast nav files next to the RINEX file instead --
    pdp3 merges them itself locally, or runs single-GNSS (GPS-only) if GLONASS isn't available.

    Fixed-coordinate PPP (solve_coordinates=False): PRIDE calls this "quasi-fixed", not hard-fixed.
    Confirmed directly by PRIDE's developers (pride@whu.edu.cn): "F" mode does not pin the position
    to sit.xyz -- it gives the seed coordinate a small a-priori sigma, READ FROM sit.xyz itself
    (not from the Option-line's PoXEm/PoYNm/PoZHm, which they confirmed only applies when the mode
    is NOT "F"). To approximate a hard fix, they recommend a very tight sigma, e.g. 1 micrometer
    (1e-6 m) -- see _SIT_XYZ_FIXED_SIGMA below.

    pdp3's "F" mode normally tries to download a position-SINEX product to seed the coordinate, but
    falls back to a local sit.xyz file in its per-day work directory if present -- we stage that
    ourselves (from self.x/y/z, with the tight sigma) to avoid the network call entirely, same
    pattern as the BRDM navigation fallback.

    Zenith troposphere delay gradients (solve_troposphere, same values/meaning as GPSPACE: 1 = do
    not solve troposphere at all, 2-5 = solve without gradients, 102-105 = solve with gradients)
    map onto PRIDE's "ZTD model"/"HTG model" config keys (NON/STO/PWC:720). The specific random-walk
    magnitude encoded in GPSPACE's 2-5 vs 102-105 values is not replicated -- only the on/off/none
    distinction PRIDE actually exposes through these two keys.

    v1 scope: static/fixed mode only (kinematic and code-only PPP are not yet implemented and raise
    pyRunPPPExceptionUnsupported).
    """

    def __init__(self, in_rinex, otl_coeff, options, sp3types, sp3altrn, antenna_height, strict=True,
                 apply_met=True, kinematic=False, clock_interpolation=False, hash=0, erase=True,
                 decimate=True, solve_coordinates=True, solve_troposphere=105, back_substitution=False,
                 elev_mask=10, x=0, y=0, z=0, observations=OBSERV_CODE_PHASE):

        if kinematic:
            raise pyRunPPPExceptionUnsupported('The PRIDE PPP-AR engine does not support kinematic mode yet.')
        if observations != OBSERV_CODE_PHASE:
            raise pyRunPPPExceptionUnsupported('The PRIDE PPP-AR engine does not support code-only PPP yet.')

        self.ppp_path = options['pride_table']
        self.ppp      = options['pride_exe']

        self._ensure_offline(self.ppp)
        self._ensure_tmp_config_fix(self.ppp)

        self.erp  = None
        self.brdm = None
        self.obx  = None
        self.bia  = None
        self.path_cfg_file = None
        self.local_table   = None
        self.engine = 'PRIDE'

        PPPEngine.__init__(self, in_rinex, otl_coeff, options, sp3types, sp3altrn, antenna_height, strict,
                           apply_met, kinematic, clock_interpolation, hash, erase, decimate,
                           solve_coordinates, solve_troposphere, back_substitution, elev_mask, x, y, z,
                           observations)

    @staticmethod
    def _ensure_offline(pdp3_path):
        # DDG: pdp3 hardcodes `readonly OFFLINE=NO` near the top of the script -- left at its
        # factory default, it reaches out to PRIDE's own servers mid-run (e.g. for an ANTEX file),
        # which we never want. This is normally a one-line manual edit after installing/updating
        # PRIDE, but it's easy to forget on a freshly deployed cluster node, so patch it here
        # automatically every time instead. Idempotent (no-op once already YES) and deliberately
        # silent on failure (e.g. read-only install, unwritable by this user) -- this is a
        # best-effort convenience, not something that should block PPP processing.
        try:
            with open(pdp3_path, 'r') as f:
                content = f.read()
            patched = re.sub(r'(readonly\s+OFFLINE\s*=\s*)NO\b', r'\1YES', content, count=1)
            if patched != content:
                with open(pdp3_path, 'w') as f:
                    f.write(patched)
        except (IOError, OSError):
            pass

    @staticmethod
    def _ensure_tmp_config_fix(pdp3_path):
        # DDG: pdp3 builds its per-run temp config path with `mktemp -u | sed "s/tmp\./config\./"`,
        # an unanchored substitution that's supposed to rename the basename (tmp.XXXXXXXXXX ->
        # config.XXXXXXXXXX) but instead replaces whatever "tmp." it finds FIRST in the full path.
        # On HPC schedulers whose $TMPDIR itself contains "tmp." (e.g. OSC's SLURM-assigned
        # /tmp/slurmtmp.<jobid>), that's the directory name, not the basename -- e.g.
        # /tmp/slurmtmp.12345/tmp.XXXXXXXXXX becomes /tmp/slurmCONFIG.12345/tmp.XXXXXXXXXX (basename
        # untouched), and pdp3 then fails trying to cp into a directory that was never created.
        # Anchoring the pattern to a preceding path separator restricts it to the basename only.
        # Idempotent (no-op once already anchored) and silent on failure, same as _ensure_offline.
        try:
            with open(pdp3_path, 'r') as f:
                content = f.read()
            patched = content.replace('mktemp -u | sed "s/tmp\\./config\\./"',
                                      'mktemp -u | sed "s#/tmp\\.#/config.#"')
            if patched != content:
                with open(pdp3_path, 'w') as f:
                    f.write(patched)
        except (IOError, OSError):
            pass

    def prepare_rinex(self, in_rinex):
        # PRIDE PPP-AR supports RINEX 3/4 natively -- no version downgrade needed
        return in_rinex

    def check_phase_center(self, section):
        # DDG: overrides PPPEngine's unconditional-True default. PRIDE reports the antenna type it
        # actually matched (from the RINEX header + ANTEX) directly in the pos_ file's own header;
        # "SITE ANTENNA TYPE = NONE" means it could not match one -- mirrors GPSPACE's
        # "Antenna phase center ... NOT AVAILABLE" check. section is unused (kept for interface
        # parity with GPSPACE/callers like LocateRinex.py's ppp.check_phase_center(ppp.proc_parameters)).
        for line in self.out:
            if 'SITE ANTENNA TYPE' in line:
                return line.split('SITE ANTENNA TYPE')[0].strip().upper() != 'NONE'
        return True

    def stage(self, decimate):

        os.makedirs(os.path.join(self.rootdir, 'products'))

        try:
            self.get_orbits(self.sp3types)

        except (pyProducts.pySp3Exception,
                pyProducts.pyClkException,
                pyProducts.pyEOPException,
                pyProducts.pyBrdcException) as e:

            if self.sp3altrn:
                self.get_orbits(self.sp3altrn)
            else:
                raise type(e)(str(e) + ' -> PRIDE PPP-AR requires SP3, CLK, ERP and at least a GPS '
                                      'broadcast navigation (BRDC) product for the day being processed.')

        self.local_table = self.stage_table_dir()
        self.config_session()

        if self.rinex.interval < 15 and decimate:
            self.rinex.decimate(30)

        copyfile(self.rinex.rinex_path,
                 os.path.join(self.rootdir, self.rinex.rinex))

    def stage_table_dir(self):
        """
        pdp3 has no config key or CLI flag for the ANTEX file: it resolves one internally (usually
        from the SP3/CLK product's own header, e.g. 'igs14') and always looks for it in the
        directory named by the config's "Table directory" line, unconditionally overwriting
        whatever local file might already be there. The only way to force it to use GeoDE's own
        ATX (determine_frame(), same as GPSPACE) is to control that directory's contents.

        We can't just overwrite the real pride_table directory in place -- concurrent PRIDE runs
        for different stations would race on the same shared file. Instead, build a process-
        isolated overlay under self.rootdir that symlinks every entry from the real table
        directory, except *.atx/*.ATX entries, which are redirected to GeoDE's own ATX file.

        Mirroring existing *.atx entries alone isn't enough: pdp3 resolves the specific filename
        it wants (e.g. 'igs20_2388.atx') from the CLK product's own header, and that exact
        version may not already exist as a file in the real table dir (e.g. a newer ATX version
        than what's locally cached) -- in that case pdp3 falls straight to a network download
        attempt, bypassing our override entirely. So this also replicates pdp3's own extraction
        (pdp3.sh: grep "SYS / PCVS APPLIED" $clk | cut -c21-34 | tr A-Z a-z | sed 's/r3/R3/') from
        our own already-staged CLK file, and stages that exact filename too -- including pdp3's
        CODE-MGEX special case (COD0MGX/COM clock + label "igs14" -> M14.ATX/M20.ATX), see
        _resolve_clk_atx_name() for details.
        """
        local_table = os.path.join(self.rootdir, 'table')
        os.makedirs(local_table)

        real_table = os.path.abspath(self.ppp_path)
        real_atx   = os.path.abspath(self.atx)

        for entry in os.listdir(real_table):
            link_path = os.path.join(local_table, entry)
            if entry.lower().endswith('.atx'):
                os.symlink(real_atx, link_path)
            else:
                os.symlink(os.path.join(real_table, entry), link_path)

        clk_atx_name = self._resolve_clk_atx_name()
        if clk_atx_name:
            link_path = os.path.join(local_table, clk_atx_name)
            if os.path.lexists(link_path):
                os.remove(link_path)
            os.symlink(real_atx, link_path)

        return local_table

    def _resolve_clk_atx_name(self):
        """
        Replicate pdp3.sh's own ANTEX filename resolution from the staged CLK product's "SYS /
        PCVS APPLIED" header line, so stage_table_dir() can stage GeoDE's ATX under that exact
        name. Returns None if the CLK file has no such header (pdp3 would then fall back to its
        own table_dir scan, already covered by mirroring existing *.atx entries).

        Also replicates pdp3.sh's CODE-MGEX special case: when the clock product is CODE's
        combined MGEX solution (filename starting with COD0MGX or COM) and the header-derived
        label is the generic "igs14" (no specific realization), pdp3 overrides the filename to
        M14.ATX or M20.ATX (depending on whether the processing date is before/after MJD 59336,
        the IGS14->IGS20 frame switch) and fetches it from CODE's own FTP mirror instead of the
        usual IGS ANTEX naming -- a path that pdp3.sh does NOT gate behind OFFLINE at all, so it
        must be pre-staged locally or it'll attempt a network download regardless.
        """
        if self.clocks1 is None or not os.path.isfile(self.clocks1.clk_path):
            return None

        try:
            with open(self.clocks1.clk_path, 'r', errors='ignore') as f:
                for line in f:
                    if 'SYS / PCVS APPLIED' in line:
                        label = line[20:34].strip().lower()
                        if not label:
                            return None

                        clk_name = os.path.basename(self.clocks1.clk_path)
                        if label == 'igs14' and clk_name.startswith(('COD0MGX', 'COM')):
                            return 'M14.ATX' if self.rinex.date.mjd <= 59336 else 'M20.ATX'

                        if 'r3' in label:
                            label = label.replace('r3', 'R3', 1)
                        return label if label.endswith('.atx') else label + '.atx'
        except OSError:
            return None

        return None

    def get_orbits(self, orbit_type):

        options = self.options
        products_path = os.path.join(self.rootdir, 'products')

        orbits = pyProducts.GetSp3Orbits(options['sp3'], self.rinex.date, orbit_type, products_path,
                                         True, short_name=False)
        clocks = pyProducts.GetClkFile(  options['sp3'], self.rinex.date, orbit_type, products_path,
                                         True, short_name=False)
        erp    = pyProducts.GetEOP(      options['sp3'], self.rinex.date, orbit_type, products_path,
                                         short_name=False)

        # DDG: PRIDE PPP-AR needs a broadcast nav file next to the RINEX obs file itself
        # (self.rootdir), not in products/ -- that is where pdp3's own PrepareRinexNav looks for it.
        # Prefer the pre-merged multi-GNSS BRDM product (best quality), but that only exists from
        # ~2013 onward (MGEX era); for older dates fall back to the plain GPS (.n) broadcast nav
        # (required) plus GLONASS (.g) if available (optional) -- pdp3 merges these itself locally,
        # or runs single-GNSS (GPS-only) if only the .n file is present. Either way, no network call.
        try:
            brdm = pyProducts.GetBrdmOrbits(options['brdc'], self.rinex.date, self.rootdir, True)
        except pyProducts.pyBrdmException:
            brdm = None
            pyProducts.GetBrdcOrbits(options['brdc'], self.rinex.date, self.rootdir, True)
            try:
                pyProducts.GetBrdgOrbits(options['brdc'], self.rinex.date, self.rootdir, True)
            except pyProducts.pyBrdgException:
                pass

        # DDG: ATT.OBX (attitude) and OSB.BIA (code/phase bias) are optional -- not every AC publishes
        # them and older dates rarely have them. PRIDE PPP-AR runs without them (with reduced fidelity/
        # no ambiguity resolution rather than failing), so fall back to NONE instead of raising.
        try:
            obx = pyProducts.GetObxFile(options['sp3'], self.rinex.date, orbit_type, products_path, True)
        except pyProducts.pyObxException:
            obx = None

        try:
            bia = pyProducts.GetBiaFile(options['sp3'], self.rinex.date, orbit_type, products_path, True)
        except pyProducts.pyBiaException:
            bia = None

        self.orbits1 = orbits
        self.clocks1 = clocks
        self.erp     = erp
        self.brdm    = brdm
        self.obx     = obx
        self.bia     = bia
        self.orbit_type = orbits.type
        self.hash   += orbits.hash

    def config_session(self):

        site  = self.rinex.StationCode.lower()
        first = self.rinex.datetime_firstObs
        last  = self.rinex.datetime_lastObs
        span  = (last - first).total_seconds()

        session_time = '%04d %02d %02d %02d %02d %05.2f %.2f' % \
                       (first.year, first.month, first.day, first.hour, first.minute, first.second, span)

        # DDG: solve_troposphere follows GPSPACE's convention (1 = don't solve, 2-5 = solve without
        # gradients, 102-105 = solve with gradients); map that onto PRIDE's ZTD/HTG model keys.
        if self.solve_troposphere == 1:
            ztd_model, htg_model = 'NON', 'NON'
        elif self.solve_troposphere >= 100:
            ztd_model, htg_model = 'STO', 'PWC:720'
        else:
            ztd_model, htg_model = 'STO', 'NON'

        # DDG: PoXEm/PoYNm/PoZHm (Option-line trailing columns) is the a-priori position sigma, but
        # per PRIDE's developers it is only applied when the mode is NOT "F" -- it's a no-op for
        # fixed-coordinate runs, hence always the static-mode default here regardless of mode.
        pos_sigma = '10.00'

        if self.solve_coordinates:
            mode = 'S'
        else:
            mode = 'F'

            # DDG: pdp3's "F" mode tries to download a position-SINEX product to seed the fixed
            # coordinate, but falls back to a local sit.xyz in its per-day work directory
            # (self.rootdir/<year>/<doy>/) if already present -- stage it ourselves from self.x/y/z
            # (same site/network-avoidance pattern as the BRDM navigation fallback).
            #
            # Per PRIDE's developers (pride@whu.edu.cn), "F" mode does not hard-fix the position --
            # it gives the seed coordinate a small a-priori sigma read FROM sit.xyz itself (not from
            # PoXEm/PoYNm/PoZHm, which they confirmed is ignored in F mode). They recommend a very
            # tight sigma, e.g. 1e-6 m, to approximate a hard fix. This corrects two earlier wrong
            # turns: the manual's own -m section (5.4.1) only documents "staname posx posy posz"
            # (4 fields, no sigma) with no mention that a sigma is read from the file at all, and an
            # interim attempt guessed 1e-4/1e-3 m from pdp3.sh's internal field indexing without
            # confirmation -- neither actually constrained the solution in testing.
            year_doy = os.path.join(self.rootdir, str(self.rinex.date.year), str(self.rinex.date.doy).zfill(3))
            os.makedirs(year_doy, exist_ok=True)
            file_write(os.path.join(year_doy, 'sit.xyz'),
                      ' %s%16.4f%16.4f%16.4f%10.6f%10.6f%10.6f\n' %
                      (site, self.x, self.y, self.z, 1e-6, 1e-6, 1e-6))

        self.path_cfg_file = os.path.join(self.rootdir, 'config.' + site)

        file_write(self.path_cfg_file, _PRIDE_CONFIG_TEMPLATE.format(
            interval       = self.rinex.interval,
            session_time   = session_time,
            # DDG: points at the process-isolated overlay built by stage_table_dir(), not the real
            # shared pride_table -- see stage_table_dir() for why (forces GeoDE's own ATX).
            table_dir      = os.path.join(os.path.abspath(self.local_table), ''),
            # DDG: pdp3 runs with cwd=self.rootdir (a relative path); a relative "Product directory"
            # would resolve against that cwd and point nowhere, so must be absolute. When pdp3 can't
            # find a product locally it silently falls back to fetching it from its own remote
            # server -- an absolute, correct path avoids that fallback (and the network calls) entirely.
            product_dir    = os.path.join(os.path.abspath(self.rootdir), 'products', ''),
            sp3_filename   = self.orbits1.filename,
            clk_filename   = self.clocks1.filename,
            erp_filename   = self.erp.filename,
            obx_filename   = self.obx.filename if self.obx else 'NONE',
            bia_filename   = self.bia.filename if self.bia else 'NONE',
            ztd_model      = ztd_model,
            htg_model      = htg_model,
            site           = site,
            mode           = mode,
            pos_sigma      = pos_sigma,
            # DDG: EV (Option-line field 6, general observation/editing elevation mask) stays
            # user-configurable via elev_mask/-elv (default 10) -- confirmed in pdp3.sh (lines
            # 1108-1125) to be the value actually passed as -elev to tedit/lsq, read straight from
            # this config field (CLI -c is just an optional override on top of it, which we don't
            # need since we write the field directly). The ambiguity-fixing "Cutoff elevation"
            # above is a genuinely separate parameter -- never read by the bash wrapper at all
            # (must be consumed directly by the AR binary), used only to gate which observations
            # are eligible for ambiguity resolution -- so it's deliberately NOT tied to elev_mask
            # and kept at PRIDE's own factory default (15) instead.
            ev             = self.elev_mask))

    def __exec_ppp__(self, raise_error=True):

        site = self.rinex.StationCode.lower()
        mode = 'S' if self.solve_coordinates else 'F'
        cmd = '%s -cfg %s -m %s -n %s' % (self.ppp, os.path.basename(self.path_cfg_file), mode, site)

        # DDG TEMPORARY: ambiguity-fixing is unstable below ~1h of data -- a thin/marginal LAMBDA
        # search can land on a weak, barely-accepted integer combination (ratio close to 1) instead
        # of the correct one, biasing the coordinate by decimeters even though PRIDE reports SUCCESS.
        # Disable ambiguity resolution entirely (float solution) for sessions under 1 hour until this
        # is revisited. Must be appended before the RINEX filename -- pdp3 treats its last argument
        # as the positional RINEX file and only parses options before it.
        span = (self.rinex.datetime_lastObs - self.rinex.datetime_firstObs).total_seconds()
        if span < 3600:
            cmd += ' -f'

        cmd += ' %s' % self.rinex.rinex

        try:
            # PRIDE PPP-AR (ambiguity resolution over a full day) runs considerably longer than GPSPACE
            out, err = pyRunWithRetry.RunCommand(cmd, 900, self.rootdir).run_shell()
        except pyRunWithRetry.RunCommandWithRetryExeception as e:
            msg = str(e)
            if raise_error:
                raise pyRunPPPException(e)
            return False, msg

        # DDG: pdp3 writes its results into a <year>/<doy>/ subdirectory of cwd, not flat in cwd
        yyyyddd  = '%04d%03d' % (self.rinex.date.year, self.rinex.date.doy)
        year_doy = os.path.join(self.rootdir, str(self.rinex.date.year), str(self.rinex.date.doy).zfill(3))
        self.path_pos_file = os.path.join(year_doy, 'pos_%s_%s' % (yyyyddd, site))
        self.path_res_file = os.path.join(year_doy, 'res_%s_%s' % (yyyyddd, site))

        if not os.path.isfile(self.path_pos_file):
            msg = 'PRIDE PPP-AR (pdp3) ended abnormally for ' + self.rinex.rinex_path + ':\n' + err + '\n' + out
            if raise_error:
                raise pyRunPPPException(msg)
            return False, msg

        self.out = file_readlines(self.path_pos_file)
        return True, ''

    def parse_summary(self):

        self.summary = ''.join(self.out)

        try:
            header_end = self.out.index([l for l in self.out if 'END OF HEADER' in l][0]) + 1
        except IndexError:
            raise pyRunPPPException('Could not find END OF HEADER in PRIDE pos file ' + self.path_pos_file)

        data_lines = [l for l in self.out[header_end:] if l.strip() and not l.lstrip().startswith('*')]

        if not data_lines:
            raise pyRunPPPExceptionZeroProcEpochs('PRIDE PPP-AR returned zero processed epochs')

        # DDG: strict mode means the same thing here as in GPSPACE -- if the antenna model
        # couldn't be resolved, do not accept the run. Only the antenna half is implemented: I
        # don't have a verified way to detect "OTL could not be determined" for PRIDE specifically
        # (it computes OTL internally via its own grid rather than taking our HARPOS coefficient,
        # and I have no confirmed real-world example of what a failed OTL lookup looks like in its
        # output -- as opposed to GPSPACE's explicit "Ocean loading coefficients ... NOT FOUND"
        # text). Flagging this as a known gap rather than guessing at a check I can't verify.
        if self.strict and not self.check_phase_center(None):
            raise pyRunPPPException(
                'Error while running PPP: could not find the antenna model in the ANTEX file '
                '(PRIDE pos file reports SITE ANTENNA TYPE = NONE). Check RINEX header for '
                'formatting issues in the ANT # / TYPE field.')

        # DDG: the header's "AMB FIXING" line reports whether ambiguity resolution was attempted at
        # all ("NO" if e.g. no OSB.BIA product was available -- see PRIDE.get_orbits()) and, if so,
        # the number of ambiguities actually fixed per GNSS system, e.g.
        # "YES  GPS    67  GAL     0  ...". Attempted-but-nothing-fixed (all-zero counts) is still
        # effectively a float solution, so only count it FIXED if at least one system has a
        # nonzero count.
        for line in self.out[:header_end]:
            if 'AMB FIXING' in line:
                fixing_fields = line.split()
                if fixing_fields and fixing_fields[0] == 'YES':
                    counts = [int(v) for v in fixing_fields[2::2] if v.isdigit()]
                    self.solution_type = 'FIXED' if any(c > 0 for c in counts) else 'FLOAT'
                else:
                    self.solution_type = 'FLOAT'
                break

        # static mode -> a single data row spanning the whole day
        fields = data_lines[-1].split()

        if len(fields) < 13:
            raise pyRunPPPException('Unexpected PRIDE pos file record: ' + data_lines[-1])

        _name, _mjd, x, y, z, sx, sy, sz, rxy, rxz, ryz, sig0, nobs = fields[:13]

        x, y, z = float(x), float(y), float(z)

        if isnan(x) or isnan(y) or isnan(z):
            raise pyRunPPPExceptionNaN('One or more coordinate is NaN')

        self.x, self.y, self.z     = x, y, z
        self.lat, self.lon, self.h = ecef2lla([self.x, self.y, self.z])

        # pos file header describes Sx/Sy/Sz/Rxy/Rxz/Ryz as cofactors and Sig0 as sqrt(variance factor):
        # variance = cofactor * Sig0**2. This matches the general geodetic cofactor-matrix convention,
        # but has not been cross-checked against PRIDE PPP-AR's own source/documentation -- validate
        # before relying on these sigmas for QC.
        var0 = float(sig0) ** 2
        self.sigmax  = sqrt(float(sx) * var0)
        self.sigmay  = sqrt(float(sy) * var0)
        self.sigmaz  = sqrt(float(sz) * var0)
        self.sigmaxy = float(rxy) * var0
        self.sigmaxz = float(rxz) * var0
        self.sigmayz = float(ryz) * var0

        self.processed_obs = int(nobs)
        self.rejected_obs  = 0

    def parse_res_file(self):
        """
        Parse the PRIDE PPP-AR res_<yyyyddd>_<site> file and compute mean LC-phase residuals
        binned by 1-degree elevation intervals, mirroring GPSPACE's parse_res_file(). Bins with no
        observations are set to NaN.

        Each post-header data line looks like:
            <PRN> <LC resid, m> <PC resid, m> <iono, D-notation> <weight, D-notation> <flag> <elev> <az> <obs types...>
        The LC (phase) residual is converted from meters to millimeters to match GPSPACE's VCP
        convention (already in mm) so self.elevation_residuals is comparable across engines.

        Populates:
            self.elevation_bins          : numpy integer array [0, 1, ..., 90] (degrees)
            self.elevation_residuals     : numpy float array, mean residual (mm) per 1-degree bin
            self.elevation_residuals_std : numpy float array, std dev of residual (mm) per
                                            1-degree bin (NaN where a bin has no observations,
                                            same as the mean)
        """
        if not os.path.isfile(self.path_res_file):
            return

        lines = file_readlines(self.path_res_file)

        try:
            header_end = lines.index([l for l in lines if 'END OF HEADER' in l][0]) + 1
        except IndexError:
            return

        # DDG: the header's (possibly wrapped) "SATELLITE LIST" lines enumerate every PRN that
        # actually contributed an observation to the solution -- a direct, per-run indicator of
        # which GNSS systems were used, sourced from the same file already fetched for residuals
        # rather than guessing from the RINEX header or the (requested, not necessarily used)
        # Frequency combination config line.
        systems = set()
        for line in lines[:header_end]:
            if 'SATELLITE LIST' in line:
                for prn in line.split()[:-2]:
                    if prn and prn[0].isalpha():
                        systems.add(prn[0])
        if systems:
            self.systems_used = ''.join(sorted(systems))

        elevations, residuals = [], []

        for line in lines[header_end:]:
            if not line.strip() or line.lstrip().startswith('TIM'):
                continue
            parts = line.split()
            if len(parts) < 8:
                continue
            try:
                resid = float(parts[1])  # LC (phase) residual, meters
                elev  = float(parts[6])  # elevation, degrees
            except ValueError:
                continue
            elevations.append(elev)
            residuals.append(resid * 1000.0)  # meters -> millimeters

        if not elevations:
            return

        elevations = numpy.array(elevations)
        residuals  = numpy.array(residuals)

        elev_bin = numpy.clip(numpy.round(elevations).astype(int), 0, 90)

        bins  = numpy.arange(0, 91)
        means = numpy.full(91, numpy.nan)
        stds  = numpy.full(91, numpy.nan)

        for deg in bins:
            mask = elev_bin == deg
            if numpy.any(mask):
                means[deg] = numpy.nanmean(residuals[mask])
                stds[deg]  = numpy.nanstd(residuals[mask])

        self.elevation_bins          = bins
        self.elevation_residuals     = means
        self.elevation_residuals_std = stds

    def exec_ppp(self):

        result, message = self.__exec_ppp__(False)

        if not result:
            if self.sp3altrn and self.orbit_type not in self.sp3altrn:
                # maybe a bad orbit, fall back to alternative and retry once
                self.get_orbits(self.sp3altrn)
                self.config_session()
                result, message = self.__exec_ppp__(False)

            if not result:
                raise pyRunPPPException(message)

        self.parse_summary()
        self.load_record()
        self.parse_res_file()


_PPP_ENGINES = {'gpspace': GPSPACE, 'pride': PRIDE}


def RunPPP(*args, **kwargs):
    """
    Factory that dispatches to the concrete PPP engine selected by options['ppp_engine']
    (default 'gpspace'). Keeps every existing RunPPP(...) call site unchanged: the returned
    object exposes the same attributes/methods regardless of which engine produced it.
    """
    options = kwargs.get('options')
    if options is None and len(args) > 2:
        options = args[2]

    engine_name = str(options.get('ppp_engine', 'gpspace')).strip().lower() if options else 'gpspace'

    try:
        engine_cls = _PPP_ENGINES[engine_name]
    except KeyError:
        raise pyRunPPPException("Unknown PPP engine '%s' in configuration (expected one of: %s)"
                                % (engine_name, ', '.join(_PPP_ENGINES)))

    return engine_cls(*args, **kwargs)
