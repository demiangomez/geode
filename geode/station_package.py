"""
Project: Geodetic Database Engine (GeoDE)
Date: 11/06/2026

Station package: a zip with everything needed to reproduce a station in another GeoDE system (ScanArchive -export
and -import). The format is described in docs/station_package_format.md.

The import does not use pyArchiveStruct.insert_rinex: it opens its own transaction, and dbConnection.begin_transac
rolls back the one already open.
"""

import contextlib
import copy
import datetime
import hashlib
import json
import math
import os
import platform
import re
import shutil
import sys
import tempfile
import zipfile
from decimal import Decimal

# deps
import numpy

# app
from . import __version__
from . import pyArchiveStruct
from . import pyDate
from . import pyEvents
from . import pyRinex
from . import pyRinexName
from . import station_merge
from .Utils import ecef2lla, get_country_code
from .metadata.station_info import StationInfo, StationInfoException, StationInfoRecord

FORMAT         = 'geode-station-package'
FORMAT_VERSION = 1

MANIFEST_FILE = 'manifest.json'
STATION_FILE  = 'station.json'
RINEX_DIR     = 'rinex'
MEDIA_DIR     = 'media'

# (table, columns left out, order): the surrogate keys are left out, they change from one database to another
STATION_TABLES = (
    ('stationinfo',      ('api_id',),              '"DateStart"'),
    ('rinex',            ('api_id',),              '"ObservationYear", "ObservationDOY", "Interval", "Completion"'),
    ('stationalias',     ('api_id',),              '"StationAlias"'),
    ('etm_params',       ('uid',),                 'uid'),
    ('sources_stations', ('api_id',),              'try_order'),
    ('events',           ('event_id',),            '"EventDate", event_id'),
)

# every table of station_merge.OWNED_TABLES is either in STATION_TABLES or here
NOT_EXPORTED = dict(
    [(table, 'solutions and time series: they are computed again from the RINEX files')
     for table, _ in station_merge.TIME_TABLES],
    etms='ETM results: they are computed again',
    locks='locks of the processing: they only make sense in the origin database',
    data_source='old table of the data sources, replaced by sources_stations',
    rinex_qc_run='RINEX quality control: ArchiveService runs RinexQC again on the RINEX records that are imported',
)

STATION_DROP = ('api_id', 'NetworkCode', 'StationCode')

# roles of agency_roles: "owner" operates the station (one at most); "host", an agency that sends the data of a
# station another agency operates
AGENCIES       = 'agencies'
AGENCY_TABLES  = ('agencies', 'agency_roles', 'agency_stations')
AGENCY_NAME_LENGTH = 200
ROLE_LENGTH    = 50
OWNER = 'owner'
HOST  = 'host'

AGENCY_KEYS     = ('name', 'type', 'description', 'rtn_names', 'contacts')
CONTACT_GRADES  = (1, 2)
CONTACT_TYPES   = ('email', 'phone')
# GeoDE keeps no phone for the secondary contact
CONTACT_COLUMNS = {(1, 'email'): 'primary_contact_email', (1, 'phone'): 'primary_contact_phone',
                   (2, 'email'): 'secondary_contact_email'}
CONTACT_NAMES   = {1: 'primary_contact_name', 2: 'secondary_contact_name'}
# NOT NULL columns of agencies: a new agency needs them
PRIMARY_CONTACT = ('primary_contact_name', 'primary_contact_email', 'primary_contact_phone')
RTN_NAME_LENGTH = 100
CONTACT_NAME_LENGTH    = 150
CONTACT_VALUE_LENGTH   = 254

# (name in the package, table, natural key, columns left out). Rows point to catalogs by natural key, never by id:
# the ids change from one database to another.
CATALOGS = (
    ('networks',              'networks',               ('NetworkCode',),                     ('api_id',)),
    ('receivers',             'receivers',              ('ReceiverCode',),                    ('api_id',)),
    ('antennas',              'antennas',               ('AntennaCode', 'RadomeCode'),        ('api_id',)),
    ('gamit_htc',             'gamit_htc',              ('AntennaCode', 'HeightCode'),        ('api_id',)),
    ('sources_formats',       'sources_formats',        ('format',),                          ('api_id',)),
    ('countries',             'api_country',            ('three_digits_code',),               ('id',)),
    ('station_status_colors', 'api_stationstatuscolor', ('color',),                           ('id',)),
    ('station_statuses',      'api_stationstatus',      ('name',),                            ('id', 'color_id')),
    ('monument_types',        'api_monumenttype',       ('name',),                            ('id',)),
    ('station_types',         'api_stationtype',        ('name',),                            ('id',)),
    ('station_roles',         'api_stationrole',        ('name',),                            ('id',)),
    ('people',                'api_person',             ('first_name', 'last_name', 'email'), ('id', 'user_id')),
    ('campaigns',             'api_campaigns',          ('name', 'start_date', 'end_date'),   ('id',)),
)
CATALOG_BY_NAME = {name: (table, key, drop) for name, table, key, drop in CATALOGS}

CATALOG_MEDIA = {
    'monument_types': 'photo_path',
    'station_types':  'icon',
    'people':         'photo',
}

# a database without GeoDE Studio (the REST API) does not have these tables
STUDIO_TABLES = station_merge.API_TABLES + ('api_stationmeta', 'api_visits_people', 'api_person', 'api_stationrole',
                                            'api_stationstatus', 'api_stationstatuscolor', 'api_monumenttype',
                                            'api_stationtype', 'api_campaigns')

MEDIA_TABLE_NAMES = {
    'api_stationimages':        'images',
    'api_stationattachedfiles': 'attached_files',
    'api_visitimages':          'images',
    'api_visitattachedfiles':   'attached_files',
    'api_visitgnssdatafiles':   'gnss_data_files',
}
IMAGE_TABLES = ('api_stationimages', 'api_visitimages')

# keys, and the values the REST API computes again after the import
STATION_META_DROP = ('id', 'station_id', 'status_id', 'monument_type_id', 'station_type_id', 'has_gaps',
                     'has_gaps_last_update_datetime', 'has_gaps_update_needed', 'has_stationinfo')
STATION_META_FILES = ('navigation_file',)

VISIT_DROP  = ('id', 'station_id', 'campaign_id')
VISIT_FILES = ('log_sheet_file', 'navigation_file')

FILE_RINEX = 'rinex'
FILE_IMAGE = 'image'
FILE_ATTACHMENT = 'attachment'
FILE_DATA = 'data'

HASH_CHUNK = 1024 * 1024


class StationPackageException(Exception):
    """
    The message is for the user. table, record and data name the record that made the whole package fail (for
    example the stations row), for the list of what was not imported.
    """

    def __init__(self, value, table=None, record=None, data=None):
        self.value  = value
        self.table  = table
        self.record = record
        self.data   = data

    def __str__(self):
        return str(self.value)


class Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime.datetime):
            # keeps microseconds and time zone, so the database reads it back unchanged
            return o.isoformat(sep=' ')
        if isinstance(o, (datetime.date, datetime.time)):
            return o.isoformat()
        if isinstance(o, numpy.generic):
            return o.item()
        if isinstance(o, numpy.ndarray):
            return o.tolist()
        return super(Encoder, self).default(o)


def to_json(data):
    """Sorted keys, so two exports of the same station are equal."""
    return json.dumps(data, indent=4, sort_keys=True, cls=Encoder, ensure_ascii=False).encode('utf-8')


def drop_columns(row, columns):
    return {key: value for key, value in row.items() if key not in columns}


def natural_key(row, key):
    if len(key) == 1:
        return row[key[0]]
    return {column: row[column] for column in key}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def package_name(NetworkCode, StationCode):
    return '%s.%s.zip' % (NetworkCode, StationCode)


def media_path(name):
    return '%s/%s' % (MEDIA_DIR, name)


def rinex_path(filename):
    return '%s/%s' % (RINEX_DIR, filename)


def is_safe_member(name):
    """True if a path of the package stays inside the folder it is extracted to."""
    if not name or name.startswith('/') or '\\' in name or ':' in name.split('/')[0]:
        return False
    return all(part not in ('', '.', '..') for part in name.split('/'))


class StationExport(object):
    """Reads only from the database, the archive and the media folder."""

    def __init__(self, cnn, NetworkCode, StationCode, archive=None, media_root=None, rinex=True, images=False,
                 attachments=False):
        """rinex=False makes a dataless package: only the database rows."""
        self.cnn          = cnn
        self.station      = (NetworkCode, StationCode)
        self.archive      = archive
        self.media_root   = media_root
        self.with_rinex   = rinex
        self.with_images  = images
        self.with_attachments = attachments

        if (images or attachments) and not media_root:
            raise StationPackageException(
                'The media folder is unknown: add "media = <folder>" to the [archive] section of gnss_data.cfg '
                'to export pictures or attached files.')

        self.warnings = []
        self.files    = []
        self._files_added = set()
        self._existing = None
        self._refs     = {name: {} for name, _, _, _ in CATALOGS}

    def _rows(self, sql, params=None):
        return self.cnn.query(sql, params).dictresult()

    def _tables(self):
        if self._existing is None:
            names = {table for table, _, _ in STATION_TABLES} | set(STUDIO_TABLES) | \
                    {table for _, table, _, _ in CATALOGS} | {'stations', 'sources_servers', 'sources_metadata'} | \
                    set(AGENCY_TABLES)
            self._existing = {row['table_name'] for row in self._rows(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_name = ANY(%s)", (list(names),))}
        return self._existing

    def has_studio(self):
        return all(table in self._tables() for table in STUDIO_TABLES)

    def has_agencies(self):
        return all(table in self._tables() for table in AGENCY_TABLES)

    def _agencies(self, station_row):
        return [{'name':        row['name'],
                 'type':        row['role'],
                 'description': row['description'],
                 'rtn_names':   row['rtn_names'],
                 'contacts':    contacts_of(row)}
                for row in self._rows(
                    'SELECT a.*, r.name AS role, s.description FROM agency_stations s '
                    'JOIN agencies a ON a.api_id = s.agency_id JOIN agency_roles r ON r.api_id = s.agency_role_id '
                    'WHERE s.station_id = %s ORDER BY lower(r.name) <> %s, lower(a.name)',
                    (station_row['api_id'], OWNER))]

    def _station_rows(self, table, drop, order):
        rows = self._rows('SELECT * FROM "%s" WHERE "NetworkCode" = %%s AND "StationCode" = %%s ORDER BY %s'
                          % (table, order), self.station)
        return [drop_columns(row, drop + ('NetworkCode', 'StationCode')) for row in rows]

    def _add_file(self, arcname, path, kind):
        if arcname in self._files_added:
            return True
        if not os.path.isfile(path):
            self.warnings.append('File not found, left out of the package: %s' % path)
            return False
        self._files_added.add(arcname)
        self.files.append((arcname, path, kind))
        return True

    def _media(self, name, kind):
        if name and ((kind == FILE_IMAGE and self.with_images) or
                     (kind == FILE_ATTACHMENT and self.with_attachments)):
            self._add_file(media_path(name), os.path.join(self.media_root, name), kind)
        return name

    def _ref(self, catalog, row):
        """Remembers a catalog row used by the station and returns its reference."""
        _, key, _ = CATALOG_BY_NAME[catalog]
        reference = natural_key(row, key)
        self._refs[catalog][json.dumps(reference, sort_keys=True, cls=Encoder)] = row
        return reference

    def _ref_by(self, catalog, where, params):
        table, _, _ = CATALOG_BY_NAME[catalog]
        if table not in self._tables():
            return None
        rows = self._rows('SELECT * FROM "%s" WHERE %s' % (table, where), params)
        return self._ref(catalog, rows[0]) if rows else None

    def _catalogs(self):
        for status in list(self._refs['station_statuses'].values()):
            color = self._ref_by('station_status_colors', 'id = %s', (status['color_id'],))
            status['color'] = color

        catalogs = {}
        for name, _, key, drop in CATALOGS:
            rows = [drop_columns(row, drop) for row in self._refs[name].values()]
            if name in CATALOG_MEDIA:
                for row in rows:
                    self._media(row[CATALOG_MEDIA[name]], FILE_IMAGE)
            catalogs[name] = sorted(rows, key=lambda row: json.dumps(natural_key(row, key), cls=Encoder))
        return catalogs

    def collect(self):
        """The content of station.json."""
        rows = self._rows('SELECT * FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s', self.station)
        if not rows:
            raise StationPackageException('Station %s.%s does not exist.' % self.station)
        station_row = rows[0]

        if not (station_row['lat'] is not None and station_row['auto_x'] is not None and
                station_row['Harpos_coeff_otl']):
            self.warnings.append('Station %s.%s has incomplete coordinates or OTL coefficients: the package is '
                                 'exported anyway.' % self.station)

        self._ref_by('networks', '"NetworkCode" = %s', (self.station[0],))
        if station_row.get('country_code'):
            self._ref_by('countries', 'three_digits_code = %s', (station_row['country_code'],))

        tables = {}
        for table, drop, order in STATION_TABLES:
            if table in self._tables():
                tables[table] = self._station_rows(table, drop, order)

        self._stationinfo_catalogs(tables.get('stationinfo', []))
        tables['rinex'] = self._rinex(tables.get('rinex', []))
        if 'sources_stations' in tables:
            self._sources(tables['sources_stations'])

        studio = self._studio(station_row['api_id']) if self.has_studio() else None

        data = {
            'format':         FORMAT,
            'format_version': FORMAT_VERSION,
            'NetworkCode':    self.station[0],
            'StationCode':    self.station[1],
            'station':        drop_columns(station_row, STATION_DROP),
            'tables':         tables,
            'studio':         studio,
            'catalogs':       self._catalogs(),
        }
        if self.has_agencies():
            data[AGENCIES] = self._agencies(station_row)
        return data

    def _stationinfo_catalogs(self, records):
        for record in records:
            self._ref_by('receivers', '"ReceiverCode" = %s', (record['ReceiverCode'],))
            self._ref_by('antennas', '"AntennaCode" = %s AND "RadomeCode" = %s',
                         (record['AntennaCode'], record['RadomeCode']))
            if record.get('HeightCode'):
                self._ref_by('gamit_htc', '"AntennaCode" = %s AND "HeightCode" = %s',
                             (record['AntennaCode'], record['HeightCode']))

    def _rinex(self, records):
        if not self.with_rinex:
            return records
        archive = self.archive if self.archive is not None else pyArchiveStruct.RinexStruct(self.cnn)
        for record in records:
            try:
                name = pyRinexName.RinexNameFormat(record['Filename']).to_rinex_format(pyRinexName.TYPE_CRINEZ,
                                                                                     no_path=True)
            except pyRinexName.RinexNameException:
                self.warnings.append('Invalid RINEX name %s: file left out of the package.' % record['Filename'])
                continue
            folder = archive.archive_folder(dict(record, NetworkCode=self.station[0], StationCode=self.station[1]))
            self._add_file(rinex_path(name), os.path.join(archive.Config.archive_path, folder, name), FILE_RINEX)
        return records

    def _sources(self, sources):
        has_metadata = 'sources_metadata' in self._tables()
        for source in sources:
            server = self._rows('SELECT * FROM sources_servers WHERE server_id = %s', (source.pop('server_id'),))[0]
            metadata_id = server.pop('metadata_source_id', None)
            server = drop_columns(server, ('server_id',))
            server['metadata_source'] = None
            if has_metadata and metadata_id is not None:
                metadata = self._rows('SELECT * FROM sources_metadata WHERE id = %s', (metadata_id,))
                server['metadata_source'] = drop_columns(metadata[0], ('id',)) if metadata else None
            source['server'] = server

            for item in (source, server, server['metadata_source'] or {}):
                if item.get('format'):
                    self._ref_by('sources_formats', 'format = %s', (item['format'],))

    def _person(self, person_id):
        return self._ref_by('people', 'id = %s', (person_id,))

    def _studio(self, api_id):
        studio = {}

        meta = self._rows('SELECT * FROM api_stationmeta WHERE station_id = %s', (api_id,))
        if meta:
            meta = meta[0]
            row = drop_columns(meta, STATION_META_DROP)
            row['status'] = self._ref_by('station_statuses', 'id = %s', (meta['status_id'],)) \
                if meta['status_id'] is not None else None
            row['monument_type'] = self._ref_by('monument_types', 'id = %s', (meta['monument_type_id'],)) \
                if meta['monument_type_id'] is not None else None
            row['station_type'] = self._ref_by('station_types', 'id = %s', (meta['station_type_id'],)) \
                if meta['station_type_id'] is not None else None
            for column in STATION_META_FILES:
                self._media(row.get(column), FILE_ATTACHMENT)
            studio['station_meta'] = row
        else:
            studio['station_meta'] = None

        for table, column in station_merge.STATION_MEDIA:
            kind = FILE_IMAGE if table in IMAGE_TABLES else FILE_ATTACHMENT
            rows = [drop_columns(row, ('id', 'station_id')) for row in self._rows(
                'SELECT * FROM "%s" WHERE station_id = %%s ORDER BY "%s"' % (table, column), (api_id,))]
            for row in rows:
                self._media(row[column], kind)
            studio[MEDIA_TABLE_NAMES[table]] = rows

        studio['people_roles'] = [
            {'person': self._person(row['person_id']),
             'role':   self._ref_by('station_roles', 'id = %s', (row['role_id'],))}
            for row in self._rows('SELECT person_id, role_id FROM api_rolepersonstation WHERE station_id = %s '
                                  'ORDER BY id', (api_id,))]

        visits = []
        for visit in self._rows('SELECT * FROM api_visits WHERE station_id = %s ORDER BY date', (api_id,)):
            row = drop_columns(visit, VISIT_DROP)
            row['campaign'] = self._ref_by('campaigns', 'id = %s', (visit['campaign_id'],)) \
                if visit['campaign_id'] is not None else None
            row['people'] = [self._person(person['person_id']) for person in self._rows(
                'SELECT person_id FROM api_visits_people WHERE visits_id = %s ORDER BY id', (visit['id'],))]
            for column in VISIT_FILES:
                self._media(row.get(column), FILE_ATTACHMENT)
            for table, column in station_merge.VISIT_MEDIA:
                kind = FILE_IMAGE if table in IMAGE_TABLES else FILE_ATTACHMENT
                children = [drop_columns(child, ('id', 'visit_id')) for child in self._rows(
                    'SELECT * FROM "%s" WHERE visit_id = %%s ORDER BY "%s"' % (table, column), (visit['id'],))]
                for child in children:
                    self._media(child[column], kind)
                row[MEDIA_TABLE_NAMES[table]] = children
            visits.append(row)
        studio['visits'] = visits

        return studio

    def manifest(self, data, files):
        tables = data['tables']
        studio = data['studio'] or {}
        counts = {table: len(rows) for table, rows in tables.items()}
        if data.get(AGENCIES) is not None:
            counts[AGENCIES] = len(data[AGENCIES])
        counts['rinex_files'] = sum(1 for item in files if item['kind'] == FILE_RINEX)
        counts['image_files'] = sum(1 for item in files if item['kind'] == FILE_IMAGE)
        counts['attachment_files'] = sum(1 for item in files if item['kind'] == FILE_ATTACHMENT)
        if studio:
            counts['visits'] = len(studio['visits'])
            counts['images'] = len(studio['images']) + sum(len(visit['images']) for visit in studio['visits'])
            counts['attached_files'] = (len(studio['attached_files']) +
                                        sum(len(visit['attached_files']) + len(visit['gnss_data_files'])
                                            for visit in studio['visits']))

        return {
            'format':         FORMAT,
            'format_version': FORMAT_VERSION,
            'created':        datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            'generator':      {'software': 'geode-gnss', 'version': __version__, 'host': platform.node()},
            'NetworkCode':    self.station[0],
            'StationCode':    self.station[1],
            'data':           STATION_FILE,
            'contents':       {'rinex':       self.with_rinex,
                               'images':      self.with_images,
                               'attachments': self.with_attachments,
                               'studio':      data['studio'] is not None},
            'counts':         counts,
            'files':          files,
        }

    def write(self, folder='.'):
        data = self.collect()
        zip_path = os.path.join(folder, package_name(*self.station))
        tmp_path = zip_path + '.part'
        files = []

        try:
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for arcname, path, kind in self.files:
                    files.append(self._write_file(zf, arcname, path, kind))

                station_json = to_json(data)
                zf.writestr(STATION_FILE, station_json)
                files.append({'path': STATION_FILE, 'kind': FILE_DATA, 'size': len(station_json),
                              'sha256': hashlib.sha256(station_json).hexdigest()})

                manifest = self.manifest(data, files)
                zf.writestr(MANIFEST_FILE, to_json(manifest))
            os.replace(tmp_path, zip_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        return {'zip': zip_path, 'manifest': manifest, 'warnings': self.warnings}

    @staticmethod
    def _write_file(zf, arcname, path, kind):
        """RINEX and pictures are already compressed: only the attachments are deflated."""
        info = zipfile.ZipInfo.from_file(path, arcname)
        info.compress_type = zipfile.ZIP_DEFLATED if kind == FILE_ATTACHMENT else zipfile.ZIP_STORED
        digest = hashlib.sha256()
        size = 0
        with open(path, 'rb') as source, zf.open(info, 'w', force_zip64=True) as target:
            for chunk in iter(lambda: source.read(HASH_CHUNK), b''):
                digest.update(chunk)
                size += len(chunk)
                target.write(chunk)
        return {'path': arcname, 'kind': kind, 'size': size, 'sha256': digest.hexdigest()}


def read_package(path):
    """(manifest, station data). A package of format 0 has no manifest: (None, its NET.STNM.json)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if MANIFEST_FILE not in names:
                station_files = [name for name in names if name.endswith('.json') and '/' not in name]
                if not station_files:
                    raise StationPackageException('%s is not a station package: it has no %s nor a station '
                                                  'json file.' % (path, MANIFEST_FILE))
                return None, json.loads(zf.read(station_files[0]).decode('utf-8'))

            manifest = json.loads(zf.read(MANIFEST_FILE).decode('utf-8'))
            check_manifest(manifest)
            if manifest['data'] not in names:
                raise StationPackageException('%s: the manifest points to %s, which is not in the package.'
                                              % (path, manifest['data']))
            return manifest, json.loads(zf.read(manifest['data']).decode('utf-8'))
    except zipfile.BadZipFile:
        raise StationPackageException('%s is not a valid zip file.' % path)


def check_manifest(manifest):
    if manifest.get('format') != FORMAT:
        raise StationPackageException('Unknown package format: %s' % manifest.get('format'))
    version = manifest.get('format_version')
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise StationPackageException('Station package version %s is newer than the one this GeoDE reads (%d): '
                                      'update geode-gnss.' % (version, FORMAT_VERSION))
    for item in manifest.get('files', []):
        if not is_safe_member(item.get('path', '')):
            raise StationPackageException('Invalid file path in the package: %s' % item.get('path'))


def verify_files(path, manifest):
    problems = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for item in manifest['files']:
            if item['path'] not in names:
                problems.append('%s is listed in the manifest but is not in the package' % item['path'])
                continue
            digest = hashlib.sha256()
            size = 0
            with zf.open(item['path']) as f:
                for chunk in iter(lambda: f.read(HASH_CHUNK), b''):
                    digest.update(chunk)
                    size += len(chunk)
            if size != item['size'] or digest.hexdigest() != item['sha256']:
                problems.append('%s does not match its size or sha256 in the manifest' % item['path'])
    return problems


def export_stations(cnn, stations, folder='.', archive=None, media_root=None, rinex=True, images=False,
                    attachments=False, progress=None):
    """One package per station: a station that fails gives {'station', 'error'} and the export goes on."""
    if archive is None and rinex:
        archive = pyArchiveStruct.RinexStruct(cnn)

    results = []
    for stn in stations:
        station_id = '%s.%s' % (stn['NetworkCode'], stn['StationCode'])
        if progress:
            progress(station_id)
        try:
            export = StationExport(cnn, stn['NetworkCode'], stn['StationCode'], archive=archive,
                                   media_root=media_root, rinex=rinex, images=images, attachments=attachments)
            result = export.write(folder)
            result['station'] = station_id
        except StationPackageException as e:
            result = {'station': station_id, 'error': str(e)}
        results.append(result)
    return results


# ---------------------------------------------------------------------------------------------- import

# never inserted: the destination database gives its own
SURROGATE_KEYS = ('api_id', 'id', 'uid', 'event_id', 'server_id', 'station_id', 'visit_id', 'visits_id')

# the trigger of rinex computes them again
STATION_COMPUTED = ('DateStart', 'DateEnd')

# the ones the trigger of stations uses, for when it did not create the row (a "?" network or no trigger)
STATION_META_DEFAULTS = {
    'remote_access_link': '', 'has_battery': False, 'battery_description': '', 'has_communications': False,
    'communications_description': '', 'comments': '', 'navigation_file': '', 'navigation_filename': '',
    'has_gaps': False, 'has_gaps_last_update_datetime': None, 'has_gaps_update_needed': True,
    'has_stationinfo': False,
}
DEFAULT_STATION_STATUS = 'Active Online'

STATION_META_REFERENCES = (
    ('status_id',        'station_statuses', 'status'),
    ('monument_type_id', 'monument_types',   'monument_type'),
    ('station_type_id',  'station_types',    'station_type'),
)

# not columns of api_visits
VISIT_KEYS = ('campaign', 'people') + tuple(MEDIA_TABLE_NAMES[table] for table, _ in station_merge.VISIT_MEDIA)

# NET.STNM.json of format 0 (no manifest): key -> column of stations
FORMAT0_STATION = (
    ('StationName', 'StationName'), ('lat', 'lat'), ('lon', 'lon'), ('height', 'height'), ('x', 'auto_x'),
    ('y', 'auto_y'), ('z', 'auto_z'), ('otl', 'Harpos_coeff_otl'), ('dome', 'dome'), ('max_dist', 'max_dist'),
    ('marker', 'marker'), ('country_code', 'country_code'),
)

# the file names ReadRinex runs its shell commands with
SAFE_RINEX_NAME = re.compile(r'[A-Za-z0-9_.]+')

FAR_FUTURE = datetime.datetime(9999, 1, 1)

SERVER_KEY = ('protocol', 'fqdn', 'path', 'username')

# the only row DownloadSources creates; SyncMetadata detects the format of a metadata file by its content
DEFAULT_FORMAT = 'DEFAULT_FORMAT'

# meters, the default of pyPPP.PPPSpatialCheck and pyETM
DEFAULT_MAX_DIST = 20

# fields with no column in GeoDE (e.g. an NGS PID): read, not loaded until GeoDE has them
CUSTOM_FIELDS = 'custom_fields'


def great_circle_distance(lat1, lon1, lat2, lon2):
    """Meters between two points in degrees, with the formula and radius of pyPPP.PPPSpatialCheck."""
    lat1, lon1, lat2, lon2 = (math.radians(value) for value in (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * math.asin(math.sqrt(a)) * 6371000


def format_meters(value):
    """Never '20.000 m': a reader used to the dot as thousands separator takes it for 20000 m."""
    return ('%.3f' % value).rstrip('0').rstrip('.') + ' m'


HIDDEN = '(hidden)'


def error_message(error):
    """In one line and without the "Failing row contains" of PostgreSQL: the row may be a server with its password."""
    message = ' '.join(str(error).split())
    return re.sub(r' ?DETAIL: Failing row contains \(.*\)\.?', '', message) or type(error).__name__


def hide_passwords(data):
    if isinstance(data, dict):
        return {key: HIDDEN if key == 'password' and value is not None else hide_passwords(value)
                for key, value in data.items()}
    if isinstance(data, list):
        return [hide_passwords(value) for value in data]
    return data


def not_imported(package, station, table, record, error, data=None):
    return {'package': os.path.basename(package), 'station': station, 'table': table, 'record': record,
            'error': error, 'data': hide_passwords(data)}


def check_custom_fields(fields):
    if fields is None:
        return {}
    if not isinstance(fields, dict):
        raise StationPackageException('%s must be an object of name: value.' % CUSTOM_FIELDS)
    invalid = sorted(name for name, value in fields.items()
                     if not name or isinstance(value, (dict, list)))
    if invalid:
        raise StationPackageException('Invalid %s: %s (each one needs a name and a string, number or null value).'
                                      % (CUSTOM_FIELDS, ', '.join(repr(name) for name in invalid)))
    return fields


def check_agencies(data):
    """Only the shape: the values are checked per agency (agency_columns), so a wrong one leaves out only its agency."""
    if 'agency' in data:
        raise StationPackageException('"agency" is not read any more: the owner goes in %s with "type": "%s".'
                                      % (AGENCIES, OWNER))
    items = data.get(AGENCIES)
    if items is None:
        return []
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise StationPackageException('%s must be a list of agencies: {"name", "type", "rtn_names", "contacts"}.'
                                      % AGENCIES)
    agencies, seen = [], set()
    for item in items:
        name = item.get('name')
        if not isinstance(name, str) or not name.strip():
            raise StationPackageException('Every agency of %s needs a "name".' % AGENCIES)
        name = name.strip()
        role = item.get('type')
        if not isinstance(role, str) or not role.strip() or len(role.strip()) > ROLE_LENGTH:
            raise StationPackageException('Agency %s: "type" must be its role, a name of agency_roles (%s, %s...).'
                                          % (name, OWNER, HOST))
        if name.lower() in seen:
            raise StationPackageException('Agency %s appears more than once in %s.' % (name, AGENCIES))
        seen.add(name.lower())
        agencies.append(dict(item, name=name, type=role.strip().lower()))
    if sum(1 for agency in agencies if agency['type'] == OWNER) > 1:
        raise StationPackageException('A station has one owner at most: %s has more than one.' % AGENCIES)
    return sorted(agencies, key=lambda agency: agency['type'] != OWNER)


def _text(value, length, what):
    if not isinstance(value, str):
        raise ValueError('%s must be text.' % what)
    value = value.strip()
    if len(value) > length:
        raise ValueError('%s is longer than %d characters.' % (what, length))
    return value


def rtn_names(value):
    """A list of names, or a text with the names separated by ';'. None when empty."""
    if value is None or value == '' or value == []:
        return None
    if isinstance(value, str):
        value = value.split(';')
    if not isinstance(value, list):
        raise ValueError('rtn_names must be a list of names or a text with the names separated by ";".')
    names, seen = [], set()
    for name in (_text(name, RTN_NAME_LENGTH, 'The name of an RTN') for name in value):
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names or None


def agency_contact(contact):
    if not isinstance(contact, dict):
        raise ValueError('A contact must be an object with grade, type, name and value.')
    grade = contact.get('grade')
    if isinstance(grade, str) and grade.strip().isdigit():
        grade = int(grade)
    if isinstance(grade, bool) or grade not in CONTACT_GRADES:
        raise ValueError('The grade of a contact must be 1 (technical contact) or 2 (secondary contact).')
    kind = str(contact.get('type') or '').strip().lower()
    if kind not in CONTACT_TYPES:
        raise ValueError('The type of a contact must be email or phone.')
    value = _text(contact.get('value') or '', CONTACT_VALUE_LENGTH, 'The %s of the contact' % kind)
    if not value:
        raise ValueError('The contact has no %s.' % kind)
    if kind == 'email' and ('@' not in value or ' ' in value):
        raise ValueError('%s is not an email address.' % value)
    name = contact.get('name')
    name = _text(name, CONTACT_NAME_LENGTH, 'The name of the contact') or None if name is not None else None
    return {'grade': grade, 'type': kind, 'name': name, 'value': value}


def agency_columns(agency):
    """
    Columns of agencies for an agency of the package: the first contact of each grade and type wins. A value that
    is not valid is left out alone and goes to problems as (what, reason, value).
    """
    columns, problems = {}, []
    try:
        names = rtn_names(agency.get('rtn_names'))
        if names:
            columns['rtn_names'] = names
    except ValueError as e:
        problems.append(('rtn_names', str(e), agency.get('rtn_names')))
    contacts = agency.get('contacts') or []
    if not isinstance(contacts, list):
        problems.append(('contacts', 'contacts must be a list of contacts.', contacts))
        contacts = []
    for item in contacts:
        try:
            contact = agency_contact(item)
        except ValueError as e:
            problems.append(('contact', str(e), item))
            continue
        column = CONTACT_COLUMNS.get((contact['grade'], contact['type']))
        if column:
            columns.setdefault(column, contact['value'])
        if contact['name']:
            columns.setdefault(CONTACT_NAMES[contact['grade']], contact['name'])
    return columns, problems


def contacts_of(agency):
    return [{'grade': grade, 'type': kind, 'name': agency.get(CONTACT_NAMES[grade]), 'value': agency[column]}
            for (grade, kind), column in CONTACT_COLUMNS.items() if agency.get(column)]


def same_value(current, new):
    """
    Same for the user: numbers within 1e-9 (degrees) or a tenth of a millimeter, text ignoring case and the spaces
    around it, lists as sets, dates and timestamps by their value.
    """
    if current is None or new is None:
        return current is new
    if isinstance(current, (list, tuple)) or isinstance(new, (list, tuple)):
        def items(value):
            values = value if isinstance(value, (list, tuple)) else [value]
            return {str(item).strip().lower() for item in values}
        return items(current) == items(new)
    if isinstance(current, (int, float, Decimal)) and not isinstance(current, bool):
        try:
            return math.isclose(float(current), float(new), rel_tol=1e-12, abs_tol=1e-9 if abs(float(current)) < 400
                                else 1e-4)
        except (TypeError, ValueError):
            return False
    if isinstance(current, datetime.date):
        if isinstance(new, str):
            try:
                new = parse_timestamp(new) if isinstance(current, datetime.datetime) else \
                    datetime.date.fromisoformat(new[:10])
            except ValueError:
                return False
        return current == new
    if isinstance(current, str) and isinstance(new, str):
        return current.strip().lower() == new.strip().lower()
    return current == new


def parse_timestamp(value):
    if value is None or isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value))


def overlaps(start, end, other_start, other_end):
    """Station info sessions; an end of None is still open."""
    if start == other_start:
        return True
    return (min(end or FAR_FUTURE, other_end or FAR_FUTURE) - max(start, other_start)).total_seconds() > 0


def format0_stationinfo_row(record, NetworkCode, StationCode):
    """Row of stationinfo of a record of format 0, whose dates are 'YYYY DDD HH MM SS' (pyDate)."""
    record = dict(record)
    for key in ('DateStart', 'DateEnd'):
        if isinstance(record.get(key), str):
            try:
                record[key] = datetime.datetime.fromisoformat(record[key])
            except ValueError:
                pass
    row = StationInfoRecord(NetworkCode, StationCode, _record=record).to_database_dict()
    for key in ('DateStart', 'DateEnd'):
        if isinstance(row.get(key), datetime.datetime):
            row[key] = row[key].strftime('%Y-%m-%d %H:%M:%S')
    return drop_columns(row, ('NetworkCode', 'StationCode'))


def format0_to_package(data):
    """
    station.json of a package of format 0. A key the file lacks stays missing (no 'lat' if it is incomplete). Its
    RINEX records are not used: the files are read again, as the -import of format 0 did.
    """
    NetworkCode = str(data.get('NetworkCode') or '').lower()
    StationCode = str(data.get('StationCode') or '').lower()
    if not StationCode:
        raise StationPackageException('The station json file of the package has no StationCode.')

    records = data.get('StationInfo') or []
    if isinstance(records, dict):
        records = records.get('records') or []

    return {
        'format':         FORMAT,
        'format_version': 0,
        'NetworkCode':    NetworkCode,
        'StationCode':    StationCode,
        'station':        {column: data[key] for key, column in FORMAT0_STATION if key in data},
        'tables':         {'stationinfo': [format0_stationinfo_row(record, NetworkCode, StationCode)
                                           for record in records]},
        'studio':         None,
        'catalogs':       {},
    }


def row_member(row):
    """Path in the package of the file of a RINEX record, or None if its name is not valid."""
    try:
        name = pyRinexName.RinexNameFormat(row['Filename'])
        return rinex_path(name.to_rinex_format(pyRinexName.TYPE_CRINEZ, no_path=True))
    except Exception:
        return None


def observation_span(record):
    """(date, first epoch, last epoch) of a RINEX record: the date is the middle of the observations, as in pyRinex."""
    first, last = parse_timestamp(record.get('ObservationSTime')), parse_timestamp(record.get('ObservationETime'))
    if first is None or last is None:
        first = last = pyDate.Date(year=int(record['ObservationYear']), doy=int(record['ObservationDOY'])).datetime()
    return pyDate.Date(datetime=first + (last - first) / 2), first, last


def is_rinex_name(name):
    # RinexNameFormat raises a plain Exception for an impossible date
    try:
        pyRinexName.RinexNameFormat(name)
    except Exception:
        return False
    return True


def stationinfo_record(row, NetworkCode, StationCode):
    """StationInfoRecord of a stationinfo row of the package; a missing value takes the default of the record."""
    values = {key: value for key, value in row.items() if value is not None}
    for key in ('DateStart', 'DateEnd'):
        if key in values:
            values[key] = parse_timestamp(values[key])
    return StationInfoRecord.from_dict(values, NetworkCode, StationCode)


class StationImport(object):
    """
    One transaction per package: if it fails, the files it copied are removed too. The network of the destination
    comes from the caller: a package may have no NetworkCode.
    """

    def __init__(self, cnn, path, archive=None, media_root=None):
        """Reads the package and checks its files against the manifest. Changes nothing."""
        self.cnn        = cnn
        self.path       = path
        self.archive    = archive
        self.media_root = media_root

        self.manifest, data = read_package(path)
        with zipfile.ZipFile(path) as zf:
            self.names = set(zf.namelist())

        if self.manifest is None:
            self.format0 = True
            self.data    = format0_to_package(data)
            self.kinds   = {}
            # the -import of format 0 took the RINEX files at the root of the zip
            self.rinex_files = sorted(name for name in self.names if '/' not in name and is_rinex_name(name))
        else:
            problems = verify_files(path, self.manifest)
            if problems:
                raise StationPackageException('%s is damaged: %s.' % (path, '; '.join(problems)))
            if not data.get('StationCode'):
                raise StationPackageException('%s: %s has no StationCode.' % (path, self.manifest['data']))
            self.format0 = False
            self.data    = data
            self.kinds   = {item['path']: item['kind'] for item in self.manifest['files']}
            self.rinex_files = sorted(name for name, kind in self.kinds.items() if kind == FILE_RINEX)

        for key, default in (('station', {}), ('tables', {}), ('studio', None), ('catalogs', {})):
            if self.data.get(key) is None:
                self.data[key] = default
        self.data[CUSTOM_FIELDS] = check_custom_fields(self.data.get(CUSTOM_FIELDS))
        self.agencies = check_agencies(self.data)

        self.source      = (str(self.data.get('NetworkCode') or '').lower(), str(self.data['StationCode']).lower())
        self.destination = None
        self.zf          = None
        self._existing   = None
        self._columns_of = {}

    @property
    def NetworkCode(self):
        return self.source[0]

    @property
    def StationCode(self):
        return self.source[1]

    @property
    def source_id(self):
        return '%s.%s' % self.source if self.NetworkCode else self.StationCode

    def coordinates(self):
        """(lat, lon, height) for the spatial check, or None: a station made outside GeoDE may have none."""
        station = self.data['station']
        if station.get('lat') is not None and station.get('lon') is not None:
            return float(station['lat']), float(station['lon']), float(station.get('height') or 0)
        if all(station.get(key) is not None for key in ('auto_x', 'auto_y', 'auto_z')):
            lat, lon, height = ecef2lla([float(station['auto_x']), float(station['auto_y']),
                                         float(station['auto_z'])])
            return float(lat[0]), float(lon[0]), float(height[0])
        return None

    def _rows(self, sql, params=None):
        return self.cnn.query(sql, params).dictresult()

    def _tables(self):
        if self._existing is None:
            names = {table for table, _, _ in STATION_TABLES} | set(STUDIO_TABLES) | \
                    {table for _, table, _, _ in CATALOGS} | {'stations', 'sources_servers', 'sources_metadata'} | \
                    set(AGENCY_TABLES)
            self._existing = {row['table_name'] for row in self._rows(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_name = ANY(%s)", (list(names),))}
        return self._existing

    def has_studio(self):
        return all(table in self._tables() for table in STUDIO_TABLES)

    def _columns(self, table):
        if table not in self._columns_of:
            self._columns_of[table] = set(self.cnn.get_columns(table))
        return self._columns_of[table]

    def _insert(self, table, values, returning=None):
        """The values whose column the table does not have are ignored."""
        columns = self._columns(table)
        fields = [field for field in values if field in columns]
        sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % (table, ', '.join('"%s"' % field for field in fields),
                                                      ', '.join(['%s'] * len(fields)))
        if returning:
            sql += ' RETURNING "%s"' % returning
        rows = self._rows(sql, [values[field] for field in fields])
        return rows[0][returning] if returning else None

    def _update(self, table, values, key, key_value):
        columns = self._columns(table)
        fields = [field for field in values if field in columns]
        if fields:
            self.cnn.query('UPDATE "%s" SET %s WHERE "%s" = %%s'
                           % (table, ', '.join('"%s" = %%s' % field for field in fields), key),
                           [values[field] for field in fields] + [key_value])

    def _insert_station_row(self, table, row, **resolved):
        values = drop_columns(row, SURROGATE_KEYS + ('NetworkCode', 'StationCode'))
        values.update(resolved, NetworkCode=self.destination[0], StationCode=self.destination[1])
        self._insert(table, values)
        self._count(table)

    def _count(self, table):
        self.report['rows'][table] = self.report['rows'].get(table, 0) + 1

    def _skip(self, table, reason, row=None, record=None):
        skipped = self.report['skipped'].setdefault(table, {})
        skipped[reason] = skipped.get(reason, 0) + 1
        self.report['not_imported'].append(not_imported(self.path, self.report['destination'], table, record,
                                                        reason, row))

    @contextlib.contextmanager
    def _record(self, table, row, record=None):
        """
        A SAVEPOINT per record: if it fails, only the record is rolled back, with its files and report entries, and
        it is reported as not imported. Raise StationPackageException inside to leave it out with a reason.
        """
        self._savepoints += 1
        savepoint = 'package_record_%d' % self._savepoints
        undo  = len(self._undo)
        state = copy.deepcopy({key: self.report[key] for key in ('rows', 'files', 'created')})
        ids   = copy.deepcopy(self._ids)
        self.cnn.query('SAVEPOINT %s' % savepoint)
        try:
            yield
        except Exception as e:
            self.cnn.query('ROLLBACK TO SAVEPOINT %s' % savepoint)
            self._undo_files(undo)
            self.report.update(state)
            self._ids = ids
            self._skip(table, error_message(e), row, record)
        else:
            self.cnn.query('RELEASE SAVEPOINT %s' % savepoint)

    def _warn(self, message):
        if message not in self.report['warnings']:
            self.report['warnings'].append(message)

    def _makedirs(self, folder, media):
        missing = []
        while folder and not os.path.isdir(folder):
            missing.append(folder)
            folder = os.path.dirname(folder)
        for path in reversed(missing):
            os.mkdir(path)
            self._undo.append(('dir', path))
            if media:
                self._chown(path)

    def _copy_member(self, member, target, media=False):
        """The target must not exist: it is removed again if the import fails."""
        self._makedirs(os.path.dirname(target), media)
        with self.zf.open(member) as source:
            with open(target, 'xb') as destination:
                self._undo.append(('file', target))
                shutil.copyfileobj(source, destination, HASH_CHUNK)
        if media:
            self._chown(target)

    def _undo_files(self, start=0):
        for kind, path in reversed(self._undo[start:]):
            try:
                if kind == 'file':
                    os.remove(path)
                else:
                    os.rmdir(path)
            except OSError as e:
                sys.stderr.write(' -- Could not remove %s after the failed import: %s\n' % (path, e))
        del self._undo[start:]

    def _check_media(self):
        """Before the transaction: the media files must get the owner of the media folder."""
        self.media_owner = None
        if self.report['existing'] or not self.has_studio() or \
           not any(name.startswith(MEDIA_DIR + '/') for name in self.names):
            return
        if not self.media_root or not os.path.isdir(self.media_root):
            raise StationPackageException(
                'The package has pictures or attached files but the media folder of GeoDE Studio is unknown or '
                'does not exist (%s): set "media = <folder>" in the [archive] section of gnss_data.cfg.'
                % self.media_root)
        info = os.stat(self.media_root)
        self.media_owner = (info.st_uid, info.st_gid)
        if os.geteuid() not in (0, info.st_uid):
            raise StationPackageException(
                'The media files must belong to the owner of the media folder %s (uid %d), like the files GeoDE '
                'Studio saves: run the import as root or as that user.' % (self.media_root, info.st_uid))

    def _chown(self, path):
        uid, gid = self.media_owner
        try:
            if os.geteuid() == 0:
                os.chown(path, uid, gid)
            elif os.stat(path).st_gid != gid:
                os.chown(path, -1, gid)
        except OSError as e:
            self._warn('Could not give the group %d to the media files (%s): GeoDE Studio may not be able to '
                       'change them.' % (gid, e))

    def _place_media(self, name, retarget=True):
        """
        Returns the name for the database: the folder of the destination station, with a suffix if it is taken. A
        file that is not in the package (its kind was not exported) keeps its name, as in the origin.
        """
        if not name:
            return name
        new_name = (station_merge.retarget_media_name(name, self.source, self.destination) if retarget else None) \
            or name
        if not is_safe_member(name) or not is_safe_member(new_name):
            raise StationPackageException('Invalid name of a media file in the package: %s' % name)

        member = media_path(name)
        if member not in self.names:
            return new_name

        base, extension = os.path.splitext(new_name)
        counter = 1
        while os.path.lexists(os.path.join(self.media_root, new_name)):
            new_name = '%s_imported%d%s' % (base, counter, extension)
            counter += 1

        self._copy_member(member, os.path.join(self.media_root, new_name), media=True)
        kind = self.kinds.get(member, FILE_ATTACHMENT)
        self.report['files'][kind] = self.report['files'].get(kind, 0) + 1
        return new_name

    def _ref_key(self, catalog, reference):
        _, key, _ = CATALOG_BY_NAME[catalog]
        values = reference if isinstance(reference, dict) else {key[0]: reference}
        return json.dumps({column: values.get(column) for column in key}, sort_keys=True, cls=Encoder)

    def _lookup(self, catalog, reference):
        """id of the catalog row of the reference in the destination (True for catalogs without id), or None."""
        if reference is None:
            return None
        table, key, _ = CATALOG_BY_NAME[catalog]
        if table not in self._tables():
            return None
        ref_key = self._ref_key(catalog, reference)
        if ref_key in self._ids[catalog]:
            return self._ids[catalog][ref_key]

        values = json.loads(ref_key)
        if any(values[column] is None for column in key):
            return None
        has_id = 'id' in self._columns(table)
        rows = self._rows('SELECT %s FROM "%s" WHERE %s LIMIT 1'
                          % ('id' if has_id else '1 AS found', table,
                             ' AND '.join('"%s" = %%s' % column for column in key)),
                          [values[column] for column in key])
        if not rows:
            return None
        self._ids[catalog][ref_key] = rows[0]['id'] if has_id else True
        return self._ids[catalog][ref_key]

    def _ensure_network(self):
        NetworkCode = self.destination[0]
        if self._rows('SELECT 1 FROM networks WHERE "NetworkCode" = %s', (NetworkCode,)):
            return
        row = next((row for row in self.data['catalogs'].get('networks') or []
                    if str(row.get('NetworkCode', '')).lower() == NetworkCode), {})
        self._insert('networks', dict(drop_columns(row, SURROGATE_KEYS), NetworkCode=NetworkCode))
        self.report['created'].setdefault('networks', []).append(NetworkCode)

    def _ensure_catalogs(self, names=None):
        """Existing catalog rows are used as they are, never changed."""
        for name, table, key, _ in CATALOGS:
            if name == 'networks' or (names and name not in names) or table not in self._tables():
                continue
            for row in self.data['catalogs'].get(name) or []:
                self._ensure_catalog(name, table, key, row)

    def _ensure_catalog(self, name, table, key, row):
        if any(row.get(column) is None for column in key):
            self._skip(name, 'The row has no %s (the key of the catalog).' % ', '.join(key), row)
            return
        reference = natural_key(row, key)
        if self._lookup(name, reference) is not None:
            return

        with self._record(name, row, reference):
            values = drop_columns(row, SURROGATE_KEYS)
            if name == 'station_statuses':
                color = values.pop('color', None)
                values['color_id'] = self._lookup('station_status_colors', color)
                if values['color_id'] is None:
                    raise StationPackageException('Its color %s is not in this database.' % color)
            if name in CATALOG_MEDIA:
                column = CATALOG_MEDIA[name]
                values[column] = self._place_media(values.get(column), retarget=False)

            new_id = self._insert(table, values, returning='id' if 'id' in self._columns(table) else None)
            self._ids[name][self._ref_key(name, reference)] = new_id if new_id is not None else True
            self.report['created'].setdefault(name, []).append(reference)

    def execute(self, NetworkCode, StationCode=None, existing=False):
        """
        A record that fails goes to report['not_imported']; a failure of the package itself (the station is refused,
        the commit fails) rolls back everything and raises.
        """
        self.destination = (NetworkCode.lower(), (StationCode or self.StationCode).lower())
        self._ids  = {name: {} for name, _, _, _ in CATALOGS}
        self._undo = []
        self._savepoints = 0
        self.report = {'package': self.path, 'format0': self.format0, 'existing': existing,
                       'source': self.source_id, 'destination': '%s.%s' % self.destination,
                       'rows': {}, 'skipped': {}, 'created': {}, 'files': {FILE_RINEX: 0}, 'warnings': [],
                       'not_imported': [], 'owner': None, 'filled': {}}

        self._check_media()

        with zipfile.ZipFile(self.path) as zf:
            self.zf = zf
            try:
                self.cnn.begin_transac()

                found = self._rows('SELECT api_id, lat, lon, max_dist FROM stations WHERE "NetworkCode" = %s AND '
                                   '"StationCode" = %s '
                                   'FOR UPDATE', self.destination)
                if existing and not found:
                    raise StationPackageException('Station %s does not exist.' % self.report['destination'])
                if not existing and found:
                    raise StationPackageException('Station %s already exists and is never overwritten: add the '
                                                  'data to it instead.' % self.report['destination'])

                if self.NetworkCode and self.NetworkCode != self.destination[0]:
                    self._warn('The package is of station %s: it was imported into %s.'
                               % (self.report['source'], self.report['destination']))
                elif not self.NetworkCode and not existing:
                    self._warn('The package has no NetworkCode: the station was created in network %s.'
                               % self.destination[0])

                if existing:
                    self._check_distance(found[0])
                    self._add_to_station(found[0]['api_id'])
                else:
                    self._create_station()
                if self.data[CUSTOM_FIELDS]:
                    self._warn('The package has %d custom field(s) (%s): GeoDE has no custom fields yet, so they '
                               'were not imported.' % (len(self.data[CUSTOM_FIELDS]),
                                                       ', '.join(sorted(self.data[CUSTOM_FIELDS]))))
                self._add_event()

                self.cnn.commit_transac()
            except Exception:
                self.cnn.rollback_transac()
                self._undo_files()
                raise
            finally:
                self.zf = None

        return self.report

    def _create_station(self):
        self._ensure_network()
        self._ensure_catalogs()

        station = drop_columns(self.data['station'], SURROGATE_KEYS + STATION_COMPUTED +
                               ('NetworkCode', 'StationCode'))
        country = station.get('country_code')
        if country and 'api_country' in self._tables() and self._lookup('countries', country) is None:
            raise self._station_refused('Country %s of the station is not in api_country and the package does not '
                                        'include it.' % country)
        if not country:
            station['country_code'] = self._country_from_coordinates()
        if not station.get('Harpos_coeff_otl'):
            self._warn('The station has no OTL coefficients: run ScanArchive %s -otl after the import.'
                       % self.report['destination'])

        # the trigger of stations creates its api_stationmeta row
        api_id = self._insert('stations', dict(station, NetworkCode=self.destination[0],
                                               StationCode=self.destination[1]), returning='api_id')
        self._count('stations')

        tables = self.data['tables']
        self._insert_stationinfo(tables.get('stationinfo') or [])
        self._insert_rinex(tables.get('rinex') or [])
        self._insert_alias(tables.get('stationalias') or [])
        for table, label in (('etm_params', 'object'), ('events', 'EventDate')):
            if table in self._tables():
                for row in tables.get(table) or []:
                    with self._record(table, row, row.get(label)):
                        self._insert_station_row(table, row)
        self._insert_sources(tables.get('sources_stations') or [])
        self._import_agencies()

        if self.data['studio'] is not None:
            if self.has_studio():
                self._insert_studio(api_id)
            else:
                self._warn('This database has no GeoDE Studio tables: the Studio data of the package was not '
                           'imported.')

    def _country_from_coordinates(self):
        """A station without country_code gets the country of its coordinates; without them, it is refused."""
        position = self.coordinates()
        if position is None:
            raise self._station_refused('The station has no country_code nor coordinates, so its country cannot be '
                                        'computed: the station was not imported.')
        try:
            country = get_country_code(position[0], position[1])
        except Exception as e:
            raise self._station_refused('The country of the station could not be computed from its coordinates '
                                        '(%s): the station was not imported.' % error_message(e))
        if country is None:
            raise self._station_refused('No country contains the coordinates of the station (%.6f, %.6f), so its '
                                        'country_code cannot be computed: the station was not imported.'
                                        % position[:2])
        if 'api_country' in self._tables() and self._lookup('countries', country) is None:
            raise self._station_refused('Country %s of the coordinates of the station is not in api_country: the '
                                        'station was not imported.' % country)
        self._warn('The station has no country_code: %s assigned from its coordinates.' % country)
        return country

    def _check_distance(self, existing):
        """
        The rule ArchiveService uses for the RINEX files (pyPPP.PPPSpatialCheck), checked again here so that no
        caller can skip it.
        """
        position = self.coordinates()
        if position is None or existing['lat'] is None or existing['lon'] is None:
            self._warn('The package or station %s has no coordinates: the distance between them was not checked.'
                       % self.report['destination'])
            return
        max_dist = float(existing['max_dist']) if existing['max_dist'] is not None else DEFAULT_MAX_DIST
        distance = great_circle_distance(position[0], position[1], float(existing['lat']), float(existing['lon']))
        if distance > max_dist:
            raise self._station_refused('The coordinates of the package are %s away from station %s, more than its '
                                        'max_dist (%s): it is not the same station, so nothing was added to it.'
                                        % (format_meters(distance), self.report['destination'],
                                           format_meters(max_dist)))

    def _station_refused(self, reason):
        """The whole package fails, and its stations row is what was not imported."""
        return StationPackageException(reason, table='stations', record=self.report['destination'],
                                       data=self.data['station'])

    def _add_to_station(self, api_id):
        """
        GeoDE already decided it is the same station: it keeps its data. It gets the RINEX files it does not have,
        the station info record of the package for each RINEX file whose date it has none for, and the data sources
        and agencies it does not have.
        """
        self._ensure_catalogs(('receivers', 'antennas', 'gamit_htc', 'sources_formats'))
        tables = self.data['tables']
        self._insert_rinex(tables.get('rinex') or [])
        self._insert_sources(tables.get('sources_stations') or [], existing=True)
        self._import_agencies()
        self._warn('Station %s already existed: it kept its data and only got the RINEX files, the station info of '
                   'their dates, the data sources and the agencies it did not have.' % self.report['destination'])

    def _missing_reference(self, catalog, key, reference):
        table, _, _ = CATALOG_BY_NAME[catalog]
        self._skip(catalog, '%s %s is not in %s of this database: the station was imported without it.'
                   % (key.replace('_', ' ').capitalize(), json.dumps(reference, cls=Encoder), table),
                   {key: reference}, json.dumps(reference, cls=Encoder))

    def _insert_stationinfo(self, rows):
        sessions = []
        for row in rows:
            with self._record('stationinfo', row, row.get('DateStart')):
                start, end = parse_timestamp(row.get('DateStart')), parse_timestamp(row.get('DateEnd'))
                if start is None:
                    raise StationPackageException('The record has no DateStart.')
                if any(overlaps(start, end, other_start, other_end) for other_start, other_end in sessions):
                    raise StationPackageException('It overlaps another station info record of the package.')
                self._check_equipment(row)
                self._insert_station_row('stationinfo', row)
                sessions.append((start, end))

    def _check_equipment(self, row):
        """Foreign keys: the receiver and the antenna must be in their catalogs."""
        receiver = row.get('ReceiverCode')
        if receiver and 'receivers' in self._tables() and self._lookup('receivers', receiver) is None:
            raise StationPackageException('Receiver %s is not in the receivers catalog of this database and the '
                                          'package does not include it.' % receiver)
        antenna, radome = row.get('AntennaCode'), row.get('RadomeCode')
        if not antenna or 'antennas' not in self._tables():
            return
        if radome:
            if self._lookup('antennas', {'AntennaCode': antenna, 'RadomeCode': radome}) is None:
                raise StationPackageException('Antenna %s with radome %s is not in the antennas catalog of this '
                                              'database and the package does not include it.' % (antenna, radome))
        elif not self._rows('SELECT 1 FROM antennas WHERE "AntennaCode" = %s LIMIT 1', (antenna,)):
            raise StationPackageException('Antenna %s is not in the antennas catalog of this database and the '
                                          'package does not include it.' % antenna)

    def _dataless(self):
        return not self.format0 and not (self.manifest.get('contents') or {}).get('rinex', True)

    def _insert_rinex(self, rows):
        """
        A RINEX file with its record (a package of -export) is copied as it is; one without a record is read again
        with pyRinex, as the -import of format 0 did. An existing station also gets, for each RINEX file of the
        package, the station info record of the package that covers it if the station has none for its date.
        """
        if rows and self._dataless():
            # the files were left out on purpose: one warning, not one error per row
            self._warn('Dataless package: its %d RINEX record(s) were not imported (the files are not in the '
                       'package).' % len(rows))
            return
        self._without_stationinfo = []
        recorded = set()
        for row in rows:
            recorded.add(row_member(row))
            observed = None
            with self._record('rinex', row, row.get('Filename')):
                observed = self._insert_rinex_row(row)
            if observed and self.report['existing']:
                self._stationinfo_for_rinex(row.get('Filename'), *observed)
        for member in self.rinex_files:
            if member in recorded:
                continue
            observed = None
            with self._record('rinex', {'file': member}, member):
                observed = self._read_rinex(member)
            if observed and self.report['existing']:
                self._stationinfo_for_rinex(os.path.basename(member), *observed)
        if self._without_stationinfo:
            self._warn('%d RINEX file(s) have no valid station info record in the database nor in the package, so '
                       'they are in the database without metadata: %s.'
                       % (len(self._without_stationinfo), ', '.join(self._without_stationinfo)))

    def _has_rinex(self, record):
        """The check of pyArchiveStruct.insert_rinex."""
        return bool(self._rows(
            'SELECT 1 FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s AND "ObservationYear" = %s '
            'AND "ObservationDOY" = %s AND "Interval" = %s AND "Completion" = %s',
            (record['NetworkCode'], record['StationCode'], record['ObservationYear'], record['ObservationDOY'],
             record['Interval'], float('%.3f' % float(record['Completion'])))))

    def _insert_rinex_row(self, row):
        """(date, first epoch, last epoch) of the RINEX file, which the station has now or already had."""
        member = row_member(row)
        if member is None:
            raise StationPackageException('Invalid RINEX file name.')
        if member not in self.names:
            raise StationPackageException('The file %s is not in the package.' % member)

        name = pyRinexName.RinexNameFormat(row['Filename'])
        name.StationCode = self.destination[1]
        crinez = name.to_rinex_format(pyRinexName.TYPE_CRINEZ, no_path=True)

        record = drop_columns(row, SURROGATE_KEYS + ('NetworkCode', 'StationCode'))
        record.update(NetworkCode=self.destination[0], StationCode=self.destination[1],
                      Filename=name.to_rinex_format(pyRinexName.TYPE_RINEX, no_path=True))
        observed = observation_span(record)

        if self._has_rinex(record):
            self._skip('rinex', 'The station already has this RINEX file.', row, row.get('Filename'))
            return observed

        if self.archive is None:
            self.archive = pyArchiveStruct.RinexStruct(self.cnn)
        target = os.path.join(self.archive.Config.archive_path, self.archive.archive_folder(record), crinez)
        if os.path.lexists(target):
            raise StationPackageException('%s is already in the archive without a RINEX row: check it with '
                                          'ScanArchive -rinex.' % target)

        self._copy_member(member, target)
        self.report['files'][FILE_RINEX] += 1
        self._insert('rinex', record)
        self._count('rinex')
        return observed

    def _read_rinex(self, member):
        """
        The steps of pyArchiveStruct.insert_rinex, inside the transaction of the package. Returns (date, first
        epoch, last epoch) of the RINEX file, which the station has now or already had.
        """
        filename = os.path.basename(member)
        if not SAFE_RINEX_NAME.fullmatch(filename):
            raise StationPackageException('Invalid RINEX file name.')
        if self.archive is None:
            self.archive = pyArchiveStruct.RinexStruct(self.cnn)

        folder = tempfile.mkdtemp()
        try:
            origin = os.path.join(folder, filename)
            with self.zf.open(member) as source, open(origin, 'xb') as target:
                shutil.copyfileobj(source, target, HASH_CHUNK)

            with pyRinex.ReadRinex(self.destination[0], self.destination[1], origin) as rinexinfo:
                observed = (rinexinfo.date, rinexinfo.datetime_firstObs, rinexinfo.datetime_lastObs)
                record = rinexinfo.record
                if self._has_rinex(record):
                    self._skip('rinex', 'The station already has this RINEX file.', {'file': member}, member)
                    return observed

                rinexinfo.apply_file_naming_convention()
                path = os.path.join(self.archive.Config.archive_path,
                                    self.archive.build_rinex_path(record['NetworkCode'], record['StationCode'],
                                                                  record['ObservationYear'],
                                                                  record['ObservationDOY'], with_filename=False,
                                                                  rinexobj=rinexinfo))
                self._makedirs(path, False)
                before = set(os.listdir(path))
                archived = rinexinfo.compress_local_copyto(path)
                if os.path.basename(archived) not in before:
                    self._undo.append(('file', archived))

                record['Filename'] = pyRinexName.RinexNameFormat(archived).to_rinex_format(pyRinexName.TYPE_RINEX,
                                                                                          no_path=True)
                self._insert('rinex', record)
                self.cnn.insert_event(pyEvents.Event(
                    Description='A new RINEX was added to the archive, filename %s, interval %i, and completion %.3f'
                                % (record['Filename'], record['Interval'], record['Completion']),
                    NetworkCode=record['NetworkCode'], StationCode=record['StationCode'],
                    Year=record['ObservationYear'], DOY=record['ObservationDOY']))
                self.report['files'][FILE_RINEX] += 1
                self._count('rinex')
                return observed
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _stationinfo_for_rinex(self, filename, date, first, last):
        """What the -import of format 0 does for each RINEX file (pyStationInfo.insert_station_info)."""
        NetworkCode, StationCode = self.destination
        try:
            StationInfo(self.cnn, NetworkCode, StationCode, date)
            return
        except StationInfoException:
            pass

        inserted = False
        for row in self.data['tables'].get('stationinfo') or []:
            start, end = parse_timestamp(row.get('DateStart')), parse_timestamp(row.get('DateEnd')) or FAR_FUTURE
            if start is None or not start <= first < end:
                continue
            with self._record('stationinfo', row, row.get('DateStart')):
                self._check_equipment(row)
                StationInfo(self.cnn, NetworkCode, StationCode, allow_empty=True).insert_station_info(
                    stationinfo_record(row, NetworkCode, StationCode))
                self._count('stationinfo')
                inserted = True
                if last > end:
                    self._warn('RINEX file %s ends after the station info record %s of the package: maybe its '
                               'receiver or antenna changed in the middle.' % (filename, row.get('DateStart')))
        if not inserted:
            self._without_stationinfo.append(filename)

    def _insert_alias(self, rows):
        if 'stationalias' not in self._tables():
            return
        for row in rows:
            alias = row.get('StationAlias')
            if not alias:
                continue
            with self._record('stationalias', row, alias):
                # the trigger of stationalias refuses an alias that is a station code; StationAlias is unique
                if self._rows('SELECT 1 FROM stations WHERE "StationCode" = %s UNION ALL '
                              'SELECT 1 FROM stationalias WHERE "StationAlias" = %s', (alias, alias)):
                    raise StationPackageException('Another station uses this alias as station code or alias.')
                self._insert_station_row('stationalias', row)

    def _match_or_insert(self, table, values, key):
        columns = self._columns(table)
        values = {column: value for column, value in drop_columns(values, SURROGATE_KEYS).items()
                  if column in columns}
        rows = self._rows('SELECT "%s" AS key FROM "%s" WHERE %s LIMIT 1'
                          % (key, table, ' AND '.join('"%s" IS NOT DISTINCT FROM %%s' % column for column in values)),
                          list(values.values()))
        if rows:
            return rows[0]['key']
        new_key = self._insert(table, values, returning=key)
        self.report['created'].setdefault(table, []).append(values.get('fqdn'))
        return new_key

    def _insert_sources(self, rows, existing=False):
        """An existing station only gets the sources whose server it does not use yet."""
        if not rows or 'sources_stations' not in self._tables():
            return
        current = {row['server_id']: row for row in self._rows(
            'SELECT server_id, try_order, path, format FROM sources_stations WHERE "NetworkCode" = %s AND '
            '"StationCode" = %s', self.destination)}
        orders = {row['try_order'] for row in current.values()}
        for row in rows:
            server = row.get('server')
            label = '%s://%s%s' % (server.get('protocol'), server.get('fqdn'), server.get('path') or '') \
                if isinstance(server, dict) else None
            added = None
            with self._record('sources_stations', row, label):
                if not isinstance(server, dict) or not server.get('protocol') or not server.get('fqdn'):
                    raise StationPackageException('The data source has no server (protocol and fqdn).')
                server_id = self._source_server(server, label)
                if server_id in current:
                    if existing and any(not same_value(current[server_id][column], row.get(column))
                                        for column in ('path', 'format') if row.get(column) is not None):
                        self._warn('Station %s already gets its data from %s with another path or format: the '
                                   'existing source was kept.' % (self.report['destination'], label))
                else:
                    values = drop_columns(row, ('server',))
                    if values.get('format') is not None:
                        values['format'] = self._known_format(values['format'], label)
                    order = values.get('try_order')
                    if order is None or order in orders:
                        order = max(orders | {0}) + 1
                    values['try_order'] = order
                    self._insert_station_row('sources_stations', values, server_id=server_id)
                    added = dict(values, server_id=server_id)
            if added is not None:
                # only once the record was kept: a rolled back one leaves its try_order free
                current[added['server_id']] = added
                orders.add(added['try_order'])

    def _known_format(self, value, label):
        """Foreign key of sources_formats: an unknown format gives a warning and DEFAULT_FORMAT."""
        if value is None or 'sources_formats' not in self._tables() or self._lookup('sources_formats', value):
            return value
        self._warn('Format %s of %s is not in sources_formats: %s used instead.' % (value, label, DEFAULT_FORMAT))
        return DEFAULT_FORMAT if self._lookup('sources_formats', DEFAULT_FORMAT) else None

    def _find_server(self, values):
        """A server of the package without username (no credentials needed) also matches one that has a username."""
        rows = self._rows('SELECT * FROM sources_servers WHERE %s ORDER BY server_id LIMIT 1'
                          % ' AND '.join('"%s" IS NOT DISTINCT FROM %%s' % column for column in SERVER_KEY),
                          [values.get(column) for column in SERVER_KEY])
        if not rows and values.get('username') is None:
            rows = self._rows('SELECT * FROM sources_servers WHERE protocol = %s AND fqdn = %s AND '
                              'path IS NOT DISTINCT FROM %s ORDER BY server_id LIMIT 1',
                              [values.get(column) for column in ('protocol', 'fqdn', 'path')])
        return rows[0] if rows else None

    def _source_server(self, server, label):
        """
        An existing server is never changed, but one without a metadata source gets the one of the package. In
        GeoDE every station of a server shares its metadata source.
        """
        values = drop_columns(server, SURROGATE_KEYS + ('metadata_source', 'metadata_source_id'))
        metadata = server.get('metadata_source')
        has_metadata = 'sources_metadata' in self._tables()
        existing = self._find_server(values)
        if existing:
            different = sorted(column for column, value in values.items()
                               if column in existing and column not in SERVER_KEY and value is not None and
                               not same_value(existing[column], value))
            if metadata and has_metadata:
                if existing['metadata_source_id'] is None:
                    metadata_id = self._metadata_source(metadata, label)
                    self.cnn.query('UPDATE sources_servers SET metadata_source_id = %s WHERE server_id = %s',
                                   (metadata_id, existing['server_id']))
                    self.report['filled'].setdefault('sources_servers', []).append(
                        'metadata source of %s' % label)
                elif not self._same_metadata(existing['metadata_source_id'], metadata):
                    different.append('metadata source')
            if different:
                self._warn('Server %s (user %s) is already in this database with another %s: the existing one is '
                           'used, it was not changed.' % (label, existing.get('username'), ', '.join(different)))
            return existing['server_id']

        if values.get('format') is not None:
            values['format'] = self._known_format(values['format'], label)
        metadata_id = self._metadata_source(metadata, label) if metadata and has_metadata else None
        server_id = self._insert('sources_servers', dict(values, metadata_source_id=metadata_id),
                                 returning='server_id')
        self.report['created'].setdefault('sources_servers', []).append(values.get('fqdn'))
        return server_id

    def _metadata_source(self, metadata, label):
        if not isinstance(metadata, dict) or not metadata.get('protocol') or not metadata.get('fqdn'):
            raise StationPackageException('The metadata source of %s has no protocol and fqdn.' % label)
        metadata = dict(metadata)
        if metadata.get('format') is not None:
            metadata['format'] = self._known_format(metadata['format'], 'the metadata source of %s' % label)
        return self._match_or_insert('sources_metadata', metadata, 'id')

    def _same_metadata(self, metadata_id, metadata):
        if metadata_id is None:
            return False
        rows = self._rows('SELECT * FROM sources_metadata WHERE id = %s', (metadata_id,))
        return bool(rows) and all(rows[0][column] == value
                                  for column, value in drop_columns(metadata, SURROGATE_KEYS).items()
                                  if column in rows[0])

    def _has_agencies(self):
        return all(table in self._tables() for table in AGENCY_TABLES)

    def _agency_id(self, agency, columns):
        name = agency['name']
        if len(name) > AGENCY_NAME_LENGTH:
            raise StationPackageException('The name of the agency is longer than %d characters.'
                                          % AGENCY_NAME_LENGTH)
        rows = self._rows('SELECT * FROM agencies WHERE lower(name) = lower(%s)', (name,))
        if rows:
            self._fill_agency(rows[0], columns)
            return rows[0]['api_id']
        missing = [column for column in PRIMARY_CONTACT if not columns.get(column)]
        if missing:
            raise StationPackageException('A new agency needs a technical contact (grade 1) with a name, an email '
                                          'and a phone; it has no %s.' % ', '.join(missing))
        api_id = self._insert('agencies', dict(columns, name=name), returning='api_id')
        self.report['created'].setdefault('agencies', []).append(name)
        return api_id

    def _fill_agency(self, current, columns):
        fill, different = {}, []
        for column, value in columns.items():
            if current.get(column) in (None, '', []):
                fill[column] = value
            elif not same_value(current[column], value):
                different.append(column)
        if fill:
            self._update('agencies', fill, 'api_id', current['api_id'])
            self._count('agencies (details)')
        if different:
            self._warn('Agency %s already has another %s: the existing value was kept.'
                       % (current['name'], ', '.join(different)))

    def _import_agencies(self):
        """
        The owner of the package only becomes the owner of a station without one; otherwise it is a host. Nothing
        the station or an agency already has is removed.
        """
        if not self.agencies:
            return
        if not self._has_agencies():
            self._warn('This database has no tables for the agencies (%s): the agencies of the package were not '
                       'imported.' % ', '.join(AGENCY_TABLES))
            return

        api_id = self._rows('SELECT api_id FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s',
                            self.destination)[0]['api_id']
        roles = {row['name'].lower(): row['api_id'] for row in self._rows('SELECT api_id, name FROM agency_roles')}
        linked = {row['key']: row for row in self._rows(
            'SELECT lower(a.name) AS key, a.name, lower(r.name) AS role FROM agency_stations s '
            'JOIN agencies a ON a.api_id = s.agency_id JOIN agency_roles r ON r.api_id = s.agency_role_id '
            'WHERE s.station_id = %s', (api_id,))}
        owner = next((row['name'] for row in linked.values() if row['role'] == OWNER), None)

        for agency in self.agencies:
            name, role = agency['name'], agency['type']
            with self._record(AGENCIES, agency, '%s %s' % (role, name)):
                if role == OWNER and owner is not None and owner.lower() != name.lower():
                    self._warn('Station %s already has the owner %s: %s, the owner in the package, is a %s of it.'
                               % (self.report['destination'], owner, name, HOST))
                    role = HOST
                if role not in roles:
                    raise StationPackageException('The role %s is not in agency_roles (%s).'
                                                  % (role, ', '.join(sorted(roles))))
                unknown = sorted(key for key in agency if key not in AGENCY_KEYS)
                if unknown:
                    self._warn('Agency %s has keys GeoDE does not keep (%s): they were not imported.'
                               % (name, ', '.join(unknown)))
                columns, problems = agency_columns(agency)
                for what, reason, value in problems:
                    self._skip(AGENCIES, reason, {what: value}, '%s: %s' % (name, what))
                agency_id = self._agency_id(agency, columns)
                current = linked.get(name.lower())
                if current is None:
                    self._associate(api_id, agency_id, roles[role], agency.get('description'))
                elif role == OWNER and owner is None:
                    self.cnn.query('UPDATE agency_stations SET agency_role_id = %s '
                                   'WHERE station_id = %s AND agency_id = %s', (roles[OWNER], api_id, agency_id))
                if role == OWNER:
                    self.report['owner'] = owner = name
                linked.setdefault(name.lower(), {'name': name, 'role': role})

    def _associate(self, api_id, agency_id, role_id, description=None):
        values = {'station_id': api_id, 'agency_id': agency_id, 'agency_role_id': role_id}
        if isinstance(description, str) and description.strip():
            values['description'] = description.strip()
        self._insert('agency_stations', values)
        self._count('agency_stations')

    def _insert_studio(self, api_id):
        studio = self.data['studio']
        if studio.get('station_meta') is not None:
            with self._record('api_stationmeta', studio['station_meta'], 'station metadata'):
                self._station_meta(api_id, studio['station_meta'])

        for table, column in station_merge.STATION_MEDIA:
            for row in studio.get(MEDIA_TABLE_NAMES[table]) or []:
                with self._record(table, row, row.get(column)):
                    values = drop_columns(row, SURROGATE_KEYS)
                    values[column] = self._place_media(values.get(column))
                    values['station_id'] = api_id
                    self._insert(table, values)
                    self._count(table)

        for item in studio.get('people_roles') or []:
            with self._record('api_rolepersonstation', item, item.get('role')):
                person = self._lookup('people', item.get('person'))
                role = self._lookup('station_roles', item.get('role'))
                if person is None or role is None:
                    raise StationPackageException('The person or the role is not in this database.')
                self._insert('api_rolepersonstation', {'person_id': person, 'role_id': role, 'station_id': api_id})
                self._count('api_rolepersonstation')

        for visit in studio.get('visits') or []:
            with self._record('api_visits', visit, visit.get('date')):
                self._insert_visit(api_id, visit)

    def _station_meta(self, api_id, meta):
        values = drop_columns(meta, SURROGATE_KEYS + STATION_META_DROP + tuple(key for _, _, key in
                                                                              STATION_META_REFERENCES))
        # null is a value the package does not have: the text columns of api_stationmeta are NOT NULL
        values = {column: value for column, value in values.items() if value is not None}
        for column, catalog, key in STATION_META_REFERENCES:
            if key not in meta:
                continue
            values[column] = self._lookup(catalog, meta[key])
            if meta[key] is not None and values[column] is None:
                if column == 'status_id':
                    self._warn('%s %s of the station is not in this database: left empty.' % (key, meta[key]))
                    # the default status of the destination stays
                    del values[column]
                else:
                    self._missing_reference(catalog, key, meta[key])
        for column in STATION_META_FILES:
            if values.get(column):
                values[column] = self._place_media(values[column])

        if self._rows('SELECT 1 FROM api_stationmeta WHERE station_id = %s', (api_id,)):
            # the trigger of stations created it, and station_id is unique
            self._update('api_stationmeta', values, 'station_id', api_id)
        else:
            if 'status_id' not in values:
                values['status_id'] = self._lookup('station_statuses', DEFAULT_STATION_STATUS)
            self._insert('api_stationmeta', dict(STATION_META_DEFAULTS, **values, station_id=api_id))
        self._count('api_stationmeta')

    def _insert_visit(self, api_id, visit):
        values = drop_columns(visit, SURROGATE_KEYS + VISIT_KEYS)
        values['campaign_id'] = self._lookup('campaigns', visit.get('campaign'))
        if visit.get('campaign') is not None and values['campaign_id'] is None:
            self._warn('Campaign %s of the visit of %s is not in this database: left empty.'
                       % (visit['campaign'], visit.get('date')))
        for column in VISIT_FILES:
            if values.get(column):
                values[column] = self._place_media(values[column])
        values['station_id'] = api_id
        visit_id = self._insert('api_visits', values, returning='id')
        self._count('api_visits')

        people = set()
        for reference in visit.get('people') or []:
            person = self._lookup('people', reference)
            if person is None:
                self._skip('api_visits_people', 'The person is not in this database.', reference, visit.get('date'))
            elif person not in people:
                people.add(person)
                with self._record('api_visits_people', reference, visit.get('date')):
                    self._insert('api_visits_people', {'visits_id': visit_id, 'person_id': person})
                    self._count('api_visits_people')

        for table, column in station_merge.VISIT_MEDIA:
            for row in visit.get(MEDIA_TABLE_NAMES[table]) or []:
                with self._record(table, row, row.get(column)):
                    child = drop_columns(row, SURROGATE_KEYS)
                    child[column] = self._place_media(child.get(column))
                    child['visit_id'] = visit_id
                    self._insert(table, child)
                    self._count(table)

    def _add_event(self):
        generator = (self.manifest or {}).get('generator') or {}
        origin = 'format 0 package' if self.format0 else '%s %s on %s, %s' % (
            generator.get('software', '?'), generator.get('version', '?'), generator.get('host', '?'),
            self.manifest.get('created', '?'))
        rows = self.report['rows']
        event = pyEvents.Event(
            Description='Station package %s of %s (%s) imported into this station%s: %d station info record(s) '
                        'and %d RINEX file(s) added.'
                        % (os.path.basename(self.path), self.report['source'], origin,
                           ' (existing station)' if self.report['existing'] else '',
                           rows.get('stationinfo', 0), rows.get('rinex', 0)),
            NetworkCode=self.destination[0], StationCode=self.destination[1])
        with self._record('events', event.db_dict(), 'event of the import'):
            self.cnn.insert_event(event)


def format_import_report(report):
    lines = [' -- %s imported into %s (%s)' % (os.path.basename(report['package']), report['destination'],
                                              'existing station' if report['existing'] else 'new station')]
    rows = report['rows']
    if rows:
        lines.append('    rows: ' + ', '.join('%s %d' % (table, count) for table, count in sorted(rows.items())))
    files = report['files']
    lines.append('    files: %d RINEX, %d picture(s), %d attached file(s)'
                 % (files.get(FILE_RINEX, 0), files.get(FILE_IMAGE, 0), files.get(FILE_ATTACHMENT, 0)))
    if report.get('owner'):
        lines.append('    owner: %s' % report['owner'])
    for table, columns in sorted((report.get('filled') or {}).items()):
        lines.append('    filled in (%s): %s' % (table, ', '.join(columns)))
    for catalog, references in sorted(report['created'].items()):
        lines.append('    created in %s: %s' % (catalog, ', '.join(json.dumps(reference, cls=Encoder)
                                                                for reference in references)))
    for entry in report['not_imported']:
        record = entry['record']
        if record is not None and not isinstance(record, str):
            record = json.dumps(record, sort_keys=True, cls=Encoder)
        lines.append('    not imported: %s: %s' % (' '.join(filter(None, (entry['table'], record))), entry['error']))
    for warning in report['warnings']:
        lines.append('    warning: ' + warning)
    return lines
