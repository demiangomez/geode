"""
Tests of the station package (ScanArchive -export / -import, geode/station_package.py).

The pure parts always run. The tests marked "database" need a disposable GeoDE database: set GEODE_TEST_DB_CFG
to a gnss_data.cfg whose [postgres] section points to an empty database created from database/gnss_data_dump.sql.
They create (and remove) a station tst.pk01 with its rows, catalogs and files.
"""

import datetime
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from decimal import Decimal
from types import SimpleNamespace

import argparse
import numpy
import pytest

from geode import station_package as sp
from geode import station_merge
from geode import pyArchiveStruct
from geode import pyDate
from geode import pyRinexName
from geode.Utils import str2bool, get_country_code

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCAN_ARCHIVE = os.path.join(REPO, 'com', 'ScanArchive.py')


def load_scan_archive():
    spec = importlib.util.spec_from_file_location('ScanArchive', SCAN_ARCHIVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------------------------- pure parts

class TestEncoder:

    def test_dates_and_numbers(self):
        data = {
            'date':     datetime.date(2021, 3, 4),
            'datetime': datetime.datetime(2020, 1, 1, 10, 20, 30),
            'micro':    datetime.datetime(2020, 1, 1, 10, 20, 30, 123456),
            'tz':       datetime.datetime(2020, 1, 1, 10, 20, 30, tzinfo=datetime.timezone.utc),
            'time':     datetime.time(8, 0),
            'decimal':  Decimal('-34.123456789'),
            'numpy':    numpy.float64(1.5),
            'array':    numpy.array([1, 2]),
        }
        assert json.loads(sp.to_json(data)) == {
            'date':     '2021-03-04',
            'datetime': '2020-01-01 10:20:30',
            'micro':    '2020-01-01 10:20:30.123456',
            'tz':       '2020-01-01 10:20:30+00:00',
            'time':     '08:00:00',
            'decimal':  -34.123456789,
            'numpy':    1.5,
            'array':    [1, 2],
        }

    def test_datetime_without_microseconds_keeps_the_old_format(self):
        assert json.loads(sp.to_json(datetime.datetime(2001, 7, 16, 0, 0, 0))) == '2001-07-16 00:00:00'

    def test_scan_archive_uses_the_same_encoder(self):
        assert load_scan_archive().Encoder is sp.Encoder

    def test_unknown_type_fails(self):
        with pytest.raises(TypeError):
            sp.to_json({'x': object()})


class TestStr2Bool:

    @pytest.mark.parametrize('value', ['true', 'True', 'TRUE', 'yes', 'y', 't', '1'])
    def test_true(self, value):
        assert str2bool(value) is True

    @pytest.mark.parametrize('value', ['false', 'False', 'no', 'n', 'f', '0'])
    def test_false(self, value):
        assert str2bool(value) is False

    def test_invalid(self):
        with pytest.raises(argparse.ArgumentTypeError):
            str2bool('arg.abcd')


def run_scan_archive(*args):
    return subprocess.run([sys.executable, SCAN_ARCHIVE] + list(args), capture_output=True, text=True, timeout=300)


class TestArguments:
    """They are checked before gnss_data.cfg is read."""

    def test_help(self):
        result = run_scan_archive('--help')
        assert result.returncode == 0, result.stderr
        for option in ('-export', '-images', '-attachments', '-import'):
            assert option in result.stdout

    def test_export_takes_true_or_false(self):
        """-export false used to arrive as the string 'false', which is truthy: the package was dataless."""
        result = run_scan_archive('arg.abcd', '-export', 'maybe')
        assert result.returncode == 2 and "expected true or false, got 'maybe'" in result.stderr

    def test_media_options_only_with_export(self):
        result = run_scan_archive('arg.abcd', '-images')
        assert result.returncode == 2 and '-images and -attachments can only be used with -export.' in result.stderr


class TestInventory:

    def test_every_table_of_station_merge_is_exported_or_left_out_with_a_reason(self):
        exported = {table for table, _, _ in sp.STATION_TABLES}
        assert set(station_merge.OWNED_TABLES) <= exported | set(sp.NOT_EXPORTED)
        assert not exported & set(sp.NOT_EXPORTED)

    def test_media_tables_of_station_merge_have_a_name(self):
        for table, _ in station_merge.STATION_MEDIA + station_merge.VISIT_MEDIA:
            assert table in sp.MEDIA_TABLE_NAMES

    def test_studio_tables_include_the_ones_of_station_merge(self):
        assert set(station_merge.API_TABLES) <= set(sp.STUDIO_TABLES)


class TestHelpers:

    def test_natural_key(self):
        assert sp.natural_key({'name': 'a', 'id': 3}, ('name',)) == 'a'
        assert sp.natural_key({'AntennaCode': 'A', 'RadomeCode': 'NONE', 'api_id': 1},
                              ('AntennaCode', 'RadomeCode')) == {'AntennaCode': 'A', 'RadomeCode': 'NONE'}

    def test_drop_columns(self):
        assert sp.drop_columns({'a': 1, 'b': 2, 'c': 3}, ('b', 'x')) == {'a': 1, 'c': 3}

    @pytest.mark.parametrize('name', ['rinex/abcd0010.20d.Z', 'media/stations/arg/abcd/images/a b.jpg',
                                      'station.json'])
    def test_safe_members(self, name):
        assert sp.is_safe_member(name)

    @pytest.mark.parametrize('name', ['', '/etc/passwd', '../x', 'media/../../x', 'media//x', 'C:/x', 'a\\b',
                                      'media/./x'])
    def test_unsafe_members(self, name):
        assert not sp.is_safe_member(name)

    def test_paths(self):
        assert sp.package_name('arg', 'abcd') == 'arg.abcd.zip'
        assert sp.rinex_path('abcd0010.20d.Z') == 'rinex/abcd0010.20d.Z'
        assert sp.media_path('stations/arg/abcd/images/a.jpg') == 'media/stations/arg/abcd/images/a.jpg'


class TestManifestCheck:

    def manifest(self, **kwargs):
        return dict({'format': sp.FORMAT, 'format_version': sp.FORMAT_VERSION, 'data': sp.STATION_FILE,
                     'files': []}, **kwargs)

    def test_valid(self):
        sp.check_manifest(self.manifest())

    def test_other_format(self):
        with pytest.raises(sp.StationPackageException):
            sp.check_manifest(self.manifest(format='something'))

    def test_newer_version(self):
        with pytest.raises(sp.StationPackageException, match='newer'):
            sp.check_manifest(self.manifest(format_version=sp.FORMAT_VERSION + 1))

    def test_unsafe_path(self):
        with pytest.raises(sp.StationPackageException, match='Invalid file path'):
            sp.check_manifest(self.manifest(files=[{'path': '../../etc/cron.d/x'}]))


class TestReadPackage:

    def test_old_package_without_manifest(self, tmp_path):
        path = tmp_path / 'arg.abcd.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('arg.abcd.json', json.dumps({'NetworkCode': 'arg', 'StationCode': 'abcd',
                                                     'StationInfo': []}))
            zf.writestr('abcd0010.20d.Z', b'x')
        manifest, data = sp.read_package(str(path))
        assert manifest is None
        assert data['StationCode'] == 'abcd'

    def test_not_a_zip(self, tmp_path):
        path = tmp_path / 'x.zip'
        path.write_bytes(b'not a zip')
        with pytest.raises(sp.StationPackageException, match='not a valid zip'):
            sp.read_package(str(path))

    def test_zip_without_station(self, tmp_path):
        path = tmp_path / 'x.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('readme.txt', 'x')
        with pytest.raises(sp.StationPackageException, match='not a station package'):
            sp.read_package(str(path))

    def test_verify_files_detects_changes(self, tmp_path):
        path = tmp_path / 'x.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('rinex/a.Z', b'abc')
        manifest = {'files': [{'path': 'rinex/a.Z', 'size': 3, 'sha256': '0' * 64},
                              {'path': 'rinex/b.Z', 'size': 1, 'sha256': '0' * 64}]}
        problems = sp.verify_files(str(path), manifest)
        assert len(problems) == 2


class TestExportWithoutMediaFolder:

    def test_media_needs_the_media_folder(self):
        with pytest.raises(sp.StationPackageException, match='media folder'):
            sp.StationExport(None, 'arg', 'abcd', images=True)
        with pytest.raises(sp.StationPackageException, match='media folder'):
            sp.StationExport(None, 'arg', 'abcd', attachments=True)


def write_package(path, station_data, files=None, sha=None):
    """sha: a wrong sha256 for station.json."""
    files = dict(files or {})
    station_json = sp.to_json(station_data)
    items = [{'path': name, 'kind': kind, 'size': len(content),
              'sha256': hashlib.sha256(content).hexdigest()} for name, (kind, content) in files.items()]
    items.append({'path': sp.STATION_FILE, 'kind': sp.FILE_DATA, 'size': len(station_json),
                  'sha256': sha or hashlib.sha256(station_json).hexdigest()})
    manifest = {'format': sp.FORMAT, 'format_version': sp.FORMAT_VERSION, 'data': sp.STATION_FILE,
                'generator': {'software': 'test'}, 'NetworkCode': station_data.get('NetworkCode'),
                'StationCode': station_data.get('StationCode'), 'files': items}
    with zipfile.ZipFile(path, 'w') as zf:
        for name, (_, content) in files.items():
            zf.writestr(name, content)
        zf.writestr(sp.STATION_FILE, station_json)
        zf.writestr(sp.MANIFEST_FILE, sp.to_json(manifest))
    return str(path)


def write_format0_package(path, data, files=None):
    """Zip of format 0: NET.STNM.json and the CRINEZ files at the root."""
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('%s.%s.json' % (data['NetworkCode'], data['StationCode']), json.dumps(data))
        for name, content in (files or {}).items():
            zf.writestr(name, content)
    return str(path)


FORMAT0_STATIONINFO = {'ReceiverCode': 'TRIMBLE NETR9', 'ReceiverSerial': '5001', 'ReceiverFirmware': '4.85',
                       'AntennaCode': 'TRM59800.00', 'AntennaSerial': '1441', 'AntennaHeight': 0.0083,
                       'AntennaNorth': 0, 'AntennaEast': 0, 'HeightCode': 'DHARP', 'RadomeCode': 'SCIS',
                       'DateStart': '2019 365 00 00 00', 'DateEnd': '2020 002 23 59 59', 'ReceiverVers': '4.85',
                       'Comments': 'old package'}


def format0_rinex_row(day, network='tst', station='pk01'):
    return {'api_id': 10 + day, 'NetworkCode': network, 'StationCode': station, 'ObservationYear': 2020,
            'ObservationMonth': 1, 'ObservationDay': day, 'ObservationDOY': day,
            'ObservationFYear': 2020 + (day - 0.5) / 366, 'ObservationSTime': '2020-01-%02d 00:00:00' % day,
            'ObservationETime': '2020-01-%02d 23:59:30' % day, 'ReceiverType': 'TRIMBLE NETR9',
            'ReceiverSerial': '5001', 'ReceiverFw': '4.85', 'AntennaType': 'TRM59800.00', 'AntennaSerial': '1441',
            'AntennaDome': 'SCIS', 'Filename': '%s%03d0.20o' % (station, day), 'Interval': 30,
            'AntennaOffset': 0.0083, 'Completion': 0.999}


# what the -export of format 0 writes for a station with coordinates and OTL
FORMAT0_STATION = {'NetworkCode': 'tst', 'StationCode': 'pk01', 'lat': -34.123456789, 'lon': -58.987654321,
                   'height': 25.12345, 'x': 2765120.123, 'y': -4449250.456, 'z': -3626405.789,
                   'otl': '$$ otl coefficients', 'dome': '41201M001', 'max_dist': 20, 'marker': 1,
                   'country_code': 'ARG', 'StationInfo': [FORMAT0_STATIONINFO], 'files': 1,
                   'rinex': [format0_rinex_row(1)]}


class FakeReadRinex:
    """pyRinex.ReadRinex without crx2rnx nor gfzrnx: a day of observations every 30 s, from the name of the file."""
    completion = 0.5

    def __init__(self, NetworkCode, StationCode, origin_file):
        name = pyRinexName.RinexNameFormat(origin_file)
        name.StationCode = StationCode
        with open(origin_file, 'rb') as f:
            self.content = f.read()
        self.datetime_firstObs = name.date.datetime()
        self.datetime_lastObs = self.datetime_firstObs + datetime.timedelta(hours=23, minutes=59, seconds=30)
        self.date = pyDate.Date(datetime=self.datetime_firstObs +
                                (self.datetime_lastObs - self.datetime_firstObs) / 2)
        self.crinez = name.to_rinex_format(pyRinexName.TYPE_CRINEZ, no_path=True)
        self.record = {'NetworkCode': NetworkCode, 'StationCode': StationCode, 'ObservationYear': name.date.year,
                       'ObservationMonth': name.date.month, 'ObservationDay': name.date.day,
                       'ObservationDOY': name.date.doy, 'ObservationFYear': name.date.fyear,
                       'ObservationSTime': self.datetime_firstObs, 'ObservationETime': self.datetime_lastObs,
                       'ReceiverType': 'TRIMBLE NETR9', 'ReceiverSerial': '5001', 'ReceiverFw': '4.85',
                       'AntennaType': 'TRM59800.00', 'AntennaSerial': '1441', 'AntennaDome': 'SCIS',
                       'Filename': name.to_rinex_format(pyRinexName.TYPE_RINEX, no_path=True), 'Interval': 30,
                       'AntennaOffset': 0.0083, 'Completion': self.completion}

    def apply_file_naming_convention(self):
        pass

    def compress_local_copyto(self, path):
        target = os.path.join(path, self.crinez)
        with open(target, 'wb') as f:
            f.write(self.content)
        return target

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestImportHelpers:

    def test_parse_timestamp(self):
        assert sp.parse_timestamp(None) is None
        assert sp.parse_timestamp('2020-01-03 10:00:00.123456') == datetime.datetime(2020, 1, 3, 10, 0, 0, 123456)
        value = datetime.datetime(2020, 1, 1)
        assert sp.parse_timestamp(value) is value

    def test_overlaps(self):
        day = datetime.datetime
        assert sp.overlaps(day(2020, 1, 1), day(2020, 1, 3), day(2020, 1, 2), None)
        assert sp.overlaps(day(2020, 1, 5), None, day(2020, 1, 2), None)
        assert sp.overlaps(day(2020, 1, 2), day(2020, 1, 2), day(2020, 1, 2), day(2020, 1, 9))
        assert not sp.overlaps(day(2020, 1, 1), day(2020, 1, 2), day(2020, 1, 2), None)
        assert not sp.overlaps(day(2020, 1, 1), day(2020, 1, 2), day(2020, 1, 3), day(2020, 1, 4))

    def test_format0_to_package(self):
        data = sp.format0_to_package(json.loads(json.dumps(FORMAT0_STATION)))
        assert (data['NetworkCode'], data['StationCode']) == ('tst', 'pk01')
        assert data['station'] == {'lat': -34.123456789, 'lon': -58.987654321, 'height': 25.12345,
                                   'auto_x': 2765120.123, 'auto_y': -4449250.456, 'auto_z': -3626405.789,
                                   'Harpos_coeff_otl': '$$ otl coefficients', 'dome': '41201M001', 'max_dist': 20,
                                   'marker': 1, 'country_code': 'ARG'}
        record = data['tables']['stationinfo'][0]
        assert (record['DateStart'], record['DateEnd']) == ('2019-12-31 00:00:00', '2020-01-02 23:59:59')
        assert record['ReceiverCode'] == 'TRIMBLE NETR9' and record['Comments'] == 'old package'
        assert 'NetworkCode' not in record and 'StationCode' not in record
        # the files are read again: their records are not used
        assert 'rinex' not in data['tables']
        assert data['studio'] is None

    def test_format0_rinex_files(self, tmp_path):
        path = write_format0_package(tmp_path / 'tst.pk01.zip', FORMAT0_STATION,
                                     {'pk010010.20d.Z': b'day 1', 'PK0100ARG_R_20200020000_01D_30S_MO.crx.gz': b'day 2',
                                      'notes.txt': b'not a rinex', 'old/pk010030.20d.Z': b'not at the root'})
        package = sp.StationImport(None, path)
        assert package.rinex_files == ['PK0100ARG_R_20200020000_01D_30S_MO.crx.gz', 'pk010010.20d.Z']

    def test_observation_span(self):
        date, first, last = sp.observation_span(format0_rinex_row(3))
        assert (first, last) == (datetime.datetime(2020, 1, 3), datetime.datetime(2020, 1, 3, 23, 59, 30))
        assert date.datetime() == datetime.datetime(2020, 1, 3, 11, 59, 45)
        # without its times, the day as pyDate takes it: noon
        date, first, last = sp.observation_span(dict(format0_rinex_row(3), ObservationSTime=None))
        assert first == last == date.datetime() == datetime.datetime(2020, 1, 3, 12)

    def test_stationinfo_record(self):
        row = dict(sp.format0_to_package(json.loads(json.dumps(FORMAT0_STATION)))['tables']['stationinfo'][0],
                   DateEnd=None, AntennaSerial=None)
        record = sp.stationinfo_record(row, 'tst', 'pk01')
        assert record.DateStart.datetime() == datetime.datetime(2019, 12, 31)
        assert record.to_database_dict()['DateEnd'] is None
        assert (record.NetworkCode, record.StationCode, record.ReceiverCode) == ('tst', 'pk01', 'TRIMBLE NETR9')
        assert row['DateStart'] == '2019-12-31 00:00:00'

    def test_check_custom_fields(self):
        assert sp.check_custom_fields(None) == {}
        fields = {'ngs_pid': 'AB1234', 'installation_date': '2015-06-01', 'antenna_height': 1.5, 'rtn_names': None}
        assert sp.check_custom_fields(fields) is fields
        with pytest.raises(sp.StationPackageException, match='object'):
            sp.check_custom_fields(['ngs_pid'])
        with pytest.raises(sp.StationPackageException, match="'periods'"):
            sp.check_custom_fields({'ngs_pid': 'AB1234', 'periods': ['2019', '2025']})

    def test_check_agencies(self):
        assert sp.check_agencies({}) == []
        assert sp.check_agencies({'agencies': None}) == []
        host = {'name': ' ODOT ', 'type': 'Host', 'rtn_names': ['Ohio RTN'], 'contacts': []}
        owner = {'name': 'Lake County', 'type': 'owner'}
        assert sp.check_agencies({'agencies': [host, owner]}) == [owner, dict(host, name='ODOT', type='host')]
        # any role: the import checks it against agency_roles
        assert sp.check_agencies({'agencies': [{'name': 'NGS', 'type': 'instrument'}]})[0]['type'] == 'instrument'

    @pytest.mark.parametrize('data, message', [
        ({'agencies': 'ODOT'}, 'agencies must be a list'),
        ({'agencies': ['ODOT']}, 'agencies must be a list'),
        ({'agencies': [{'type': 'owner'}]}, 'needs a "name"'),
        ({'agencies': [{'name': ' ', 'type': 'owner'}]}, 'needs a "name"'),
        ({'agencies': [{'name': 'ODOT'}]}, '"type" must be its role'),
        ({'agencies': [{'name': 'ODOT', 'type': 'x' * 51}]}, '"type" must be its role'),
        ({'agencies': [{'name': 'ODOT', 'type': 'host'}, {'name': 'odot ', 'type': 'owner'}]}, 'more than once'),
        ({'agencies': [{'name': 'ODOT', 'type': 'owner'}, {'name': 'NGS', 'type': 'owner'}]}, 'one owner at most'),
        ({'agency': 'ODOT', 'agencies': ['NGS']}, '"agency" is not read any more'),
    ])
    def test_invalid_agencies(self, data, message):
        with pytest.raises(sp.StationPackageException, match=message):
            sp.check_agencies(data)

    def test_invalid_agencies_are_refused(self, tmp_path):
        path = write_package(tmp_path / 'x.zip', {'NetworkCode': 'ngs', 'StationCode': 'abcd', 'agencies': 'ODOT'})
        with pytest.raises(sp.StationPackageException, match='agencies must be'):
            sp.StationImport(None, path)

    def test_invalid_custom_fields_are_refused(self, tmp_path):
        path = write_package(tmp_path / 'x.zip', {'NetworkCode': 'ngs', 'StationCode': 'abcd',
                                                   'custom_fields': {'rtn': {'name': 'ODOT'}}})
        with pytest.raises(sp.StationPackageException, match="'rtn'"):
            sp.StationImport(None, path)

    @pytest.mark.parametrize('lat, lon, country', [(40.0, -83.0, 'USA'), (-34.6, -58.4, 'ARG'), (0.0, -30.0, None)])
    def test_country_code(self, lat, lon, country):
        assert get_country_code(lat, lon) == country

    def test_format0_without_station_code(self):
        with pytest.raises(sp.StationPackageException, match='StationCode'):
            sp.format0_to_package({'NetworkCode': 'tst'})

    def test_coordinates_from_ecef_when_lat_is_missing(self, tmp_path):
        data = {key: value for key, value in FORMAT0_STATION.items() if key not in ('lat', 'lon', 'height')}
        package = sp.StationImport(None, write_format0_package(tmp_path / 'tst.pk01.zip', data))
        assert package.format0
        lat, lon, height = package.coordinates()
        assert lat == pytest.approx(-34.8, abs=0.1) and lon == pytest.approx(-58.1, abs=0.1)

    def test_no_coordinates(self, tmp_path):
        path = write_package(tmp_path / 'x.zip', {'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs',
                                                   'StationCode': 'abcd',
                                                   'station': {'StationName': 'Outside', 'marker': 1,
                                                               'country_code': 'USA'}})
        package = sp.StationImport(None, path)
        assert not package.format0 and package.coordinates() is None
        assert package.source == ('ngs', 'abcd')

    def test_damaged_package(self, tmp_path):
        path = write_package(tmp_path / 'x.zip', {'NetworkCode': 'ngs', 'StationCode': 'abcd'}, sha='0' * 64)
        with pytest.raises(sp.StationPackageException, match='damaged'):
            sp.StationImport(None, path)

    def test_package_without_station_code(self, tmp_path):
        path = write_package(tmp_path / 'x.zip', {'NetworkCode': 'ngs'})
        with pytest.raises(sp.StationPackageException, match='StationCode'):
            sp.StationImport(None, path)


class TestAgencyColumns:
    def test_rtn_names(self):
        assert sp.rtn_names('Ohio RTN; Lake RTN;  ; ohio rtn') == ['Ohio RTN', 'Lake RTN']
        assert sp.rtn_names([' Ohio RTN ']) == ['Ohio RTN']
        assert sp.rtn_names(' ; ') is None
        with pytest.raises(ValueError, match='longer than 100'):
            sp.rtn_names(['x' * 101])
        with pytest.raises(ValueError, match='list of names'):
            sp.rtn_names(3)

    def test_agency_columns(self):
        columns, problems = sp.agency_columns(odot('owner'))
        assert columns == {'rtn_names': ['Ohio RTN'], 'primary_contact_name': 'John Smith',
                           'primary_contact_email': 'john@dot.example.gov',
                           'primary_contact_phone': '+1-614-555-0123', 'secondary_contact_name': 'Jane Doe',
                           'secondary_contact_email': 'jane@dot.example.gov'}
        assert problems == []
        contacts = [{'grade': 1, 'type': 'email', 'value': 'first@dot.example.gov'},
                    {'grade': 1, 'type': 'email', 'name': 'Ana', 'value': 'second@dot.example.gov'},
                    {'grade': 2, 'type': 'phone', 'name': 'Jane', 'value': '+1-614-555-0100'},
                    {'grade': 1, 'type': 'fax', 'value': '123'}]
        columns, problems = sp.agency_columns({'name': 'ODOT', 'rtn_names': 7, 'contacts': contacts})
        assert columns == {'primary_contact_email': 'first@dot.example.gov', 'primary_contact_name': 'Ana',
                           'secondary_contact_name': 'Jane'}
        assert [(what, value) for what, _, value in problems] == [('rtn_names', 7), ('contact', contacts[3])]

    def test_contacts_of_a_row(self):
        row = {'primary_contact_name': 'John Smith', 'primary_contact_email': 'john@dot.example.gov',
               'primary_contact_phone': '+1-614-555-0123', 'secondary_contact_name': None,
               'secondary_contact_email': None}
        assert sp.contacts_of(row) == [
            {'grade': 1, 'type': 'email', 'name': 'John Smith', 'value': 'john@dot.example.gov'},
            {'grade': 1, 'type': 'phone', 'name': 'John Smith', 'value': '+1-614-555-0123'}]

    def test_contacts(self):
        assert sp.agency_contact({'grade': 1, 'type': 'Email', 'name': ' Ana ', 'value': ' ana@dot.example.gov '}) \
            == {'grade': 1, 'type': 'email', 'name': 'Ana', 'value': 'ana@dot.example.gov'}
        assert sp.agency_contact({'grade': '2', 'type': 'phone', 'value': '+1-614-555-0100'})['name'] is None
        for contact, error in (({'grade': 3, 'type': 'email', 'value': 'a@b.c'}, 'grade'),
                               ({'grade': 1, 'type': 'fax', 'value': '123'}, 'type'),
                               ({'grade': 1, 'type': 'email', 'value': ''}, 'has no email'),
                               ({'grade': 1, 'type': 'email', 'value': 'not an email'}, 'not an email'),
                               ('ana@example.org', 'object')):
            with pytest.raises(ValueError, match=error):
                sp.agency_contact(contact)

    def test_same_value(self):
        assert sp.same_value(Decimal('41.405800000'), 41.4058)
        assert not sp.same_value(Decimal('41.405800000'), 41.4059)
        assert sp.same_value(2765120.12345, 2765120.12349)
        assert sp.same_value(' Toledo Airport', 'toledo airport')
        assert sp.same_value(['Ohio RTN', 'Lake RTN'], ['lake rtn', 'ohio rtn'])
        assert sp.same_value(datetime.date(2026, 12, 1), '2026-12-01')
        assert not sp.same_value(None, 'x') and sp.same_value(None, None)


class TestImportReport:

    def test_each_record_not_imported_is_listed(self):
        report = {'package': '/data/ngs.aaa1.zip', 'destination': 'ngs.aaa1', 'existing': False, 'rows': {},
                  'files': {}, 'created': {}, 'warnings': [], 'not_imported': [
                      sp.not_imported('ngs.aaa1.zip', 'ngs.aaa1', 'stationinfo', '2020-01-01 00:00:00',
                                      'Receiver X is not in the receivers catalog.', {'ReceiverCode': 'X'}),
                      sp.not_imported('ngs.aaa1.zip', 'ngs.aaa1', 'receivers', {'ReceiverCode': 'X'}, 'error'),
                      sp.not_imported('ngs.aaa1.zip', 'ngs.aaa1', 'rinex', None, 'Bad file.')]}
        lines = sp.format_import_report(report)
        assert '    not imported: stationinfo 2020-01-01 00:00:00: Receiver X is not in the receivers catalog.' \
            in lines
        assert '    not imported: receivers {"ReceiverCode": "X"}: error' in lines
        assert '    not imported: rinex: Bad file.' in lines

    def test_error_message(self):
        assert sp.error_message(Exception('ERROR:  insert failed\nDETAIL:  Key (x)=(1)\n')) == \
            'ERROR: insert failed DETAIL: Key (x)=(1)'
        assert sp.error_message(KeyError()) == 'KeyError'
        assert sp.error_message(Exception('ERROR:  null value in column "fqdn"\nDETAIL:  Failing row contains '
                                          '(7, ftp, null, osu, s3cret).\n')) == 'ERROR: null value in column "fqdn"'

    def test_the_report_hides_the_passwords(self):
        row = {'try_order': 1, 'server': {'username': 'osu', 'password': 's3cret', 'metadata_source': {
            'username': None, 'password': 'm3ta'}}}
        entry = sp.not_imported('ngs.pk01.zip', 'ngs.pk01', 'sources_stations', 'ftp://x', 'error', [row])
        assert entry['data'] == [{'try_order': 1, 'server': {'username': 'osu', 'password': sp.HIDDEN,
                                                             'metadata_source': {'username': None,
                                                                                 'password': sp.HIDDEN}}}]
        assert row['server']['password'] == 's3cret'


class StubCnn:
    """Answers every query with the same rows."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, sql, params=None):
        return SimpleNamespace(dictresult=lambda: self.rows)


@pytest.fixture(scope='module')
def scan_archive():
    return load_scan_archive()


@pytest.fixture
def answer_yes(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda prompt: 'i' if '(i)nsert' in prompt else 'y')


class TestChooseDestination:

    package = SimpleNamespace(StationCode='abcd', source=('ngs', 'abcd'), source_id='ngs.abcd',
                              coordinates=lambda: None)

    def test_yes_inserts_a_new_station(self, scan_archive, monkeypatch):
        monkeypatch.setattr('builtins.input', lambda prompt: 'y')
        assert scan_archive.choose_destination(StubCnn([]), self.package, 'ngs') == ('ngs', 'abcd', False)

    def test_yes_adds_to_the_existing_station(self, scan_archive, monkeypatch):
        monkeypatch.setattr('builtins.input', lambda prompt: 'y')
        assert scan_archive.choose_destination(StubCnn([{'?column?': 1}]), self.package, 'ngs') == \
            ('ngs', 'abcd', True)

    def test_no_skips(self, scan_archive, monkeypatch):
        monkeypatch.setattr('builtins.input', lambda prompt: 'n')
        assert scan_archive.choose_destination(StubCnn([]), self.package, 'ngs') is None


# ---------------------------------------------------------------------------------------------- database

DB_CFG = os.environ.get('GEODE_TEST_DB_CFG')
database = pytest.mark.skipif(not DB_CFG, reason='GEODE_TEST_DB_CFG is not set (needs a disposable GeoDE database)')

NET, STN = 'tst', 'pk01'

MEDIA_FILES = {
    'stations/tst/pk01/images/front.jpg':                                  b'front picture',
    'stations/tst/pk01/attached_files/log.txt':                            b'site log',
    'stations/tst/pk01/navigation_file/route.kml':                         b'<kml/>',
    'stations/tst/pk01/visits/2021-03-04/images/visit.jpg':                b'visit picture',
    'stations/tst/pk01/visits/2021-03-04/attached_files/notes.pdf':        b'notes',
    'stations/tst/pk01/visits/2021-03-04/gnss_data_files/raw.T02':         b'raw data',
    'stations/tst/pk01/visits/2021-03-04/log_sheet_file/sheet.pdf':        b'log sheet',
    'monument_type_photos/pillar.png':                                     b'pillar',
    'station_type_icons/continuous.png':                                   b'icon',
    'person_photos/ana.png':                                               b'ana',
}
RINEX_FILES = {
    'tst/2020/001/pk010010.20d.Z': b'rinex day 1',
    'tst/2020/002/pk010020.20d.Z': b'rinex day 2',
}


def remove_station(cnn, network, station):
    rows = cnn.query('SELECT api_id FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s',
                     (network, station)).dictresult()
    if rows:
        api_id = rows[0]['api_id']
        visits = [row['id'] for row in cnn.query('SELECT id FROM api_visits WHERE station_id = %s',
                                                 (api_id,)).dictresult()]
        for table in ('api_visitimages', 'api_visitattachedfiles', 'api_visitgnssdatafiles'):
            cnn.query('DELETE FROM "%s" WHERE visit_id = ANY(%%s)' % table, (visits,))
        cnn.query('DELETE FROM api_visits_people WHERE visits_id = ANY(%s)', (visits,))
        cnn.query('DELETE FROM api_visits WHERE station_id = %s', (api_id,))
    for table in ('rinex', 'stationinfo', 'stationalias', 'etm_params', 'sources_stations', 'events'):
        cnn.query('DELETE FROM "%s" WHERE "NetworkCode" = %%s AND "StationCode" = %%s' % table, (network, station))
    cnn.query('DELETE FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s', (network, station))


def remove_agencies(cnn):
    """Their associations go with them."""
    cnn.query("DELETE FROM agencies WHERE name ILIKE 'test agency%'")


CONTACT = {'primary_contact_name': 'Ana Perez', 'primary_contact_email': 'ana@agency.example.gov',
           'primary_contact_phone': '+1-614-555-0100'}


def insert_agency(cnn, name, **columns):
    values = dict(CONTACT, name=name, **columns)
    return cnn.query('INSERT INTO agencies (%s) VALUES (%s) RETURNING api_id'
                     % (', '.join(values), ', '.join(['%s'] * len(values))), list(values.values())).dictresult()[0]['api_id']


def remove_fixture(cnn):
    remove_station(cnn, NET, STN)
    cnn.query("DELETE FROM sources_servers WHERE fqdn = 'data.example.org'")
    cnn.query("DELETE FROM sources_metadata WHERE fqdn = 'meta.example.org'")
    cnn.query("DELETE FROM api_campaigns WHERE name = 'Test campaign'")
    cnn.query("DELETE FROM api_person WHERE email = 'ana@example.org'")
    remove_agencies(cnn)


def add_agencies(cnn, network, station, owner=None, hosts=()):
    def agency_id(name):
        rows = cnn.query('SELECT api_id FROM agencies WHERE name = %s', (name,)).dictresult()
        return rows[0]['api_id'] if rows else insert_agency(cnn, name)

    for name, role in ([(owner, 'owner')] if owner else []) + [(name, 'host') for name in hosts]:
        cnn.query('INSERT INTO agency_stations (station_id, agency_id, agency_role_id) SELECT s.api_id, %s, r.api_id '
                  'FROM stations s, agency_roles r WHERE s."NetworkCode" = %s AND s."StationCode" = %s AND r.name = %s',
                  (agency_id(name), network, station, role))


def station_agencies(cnn, network=NET, station=STN):
    """(owner, hosts) of a station of the database."""
    rows = cnn.query('SELECT a.name, r.name AS role FROM agency_stations sa JOIN agencies a ON a.api_id = sa.agency_id '
                     'JOIN agency_roles r ON r.api_id = sa.agency_role_id JOIN stations s ON s.api_id = sa.station_id '
                     'WHERE s."NetworkCode" = %s AND s."StationCode" = %s ORDER BY a.name',
                     (network, station)).dictresult()
    owners = [row['name'] for row in rows if row['role'] == 'owner']
    return owners[0] if owners else None, sorted(row['name'] for row in rows if row['role'] == 'host')


def create_fixture(cnn, archive_root, media_root):
    remove_fixture(cnn)
    q = cnn.query

    if not q('SELECT 1 FROM keys').dictresult():
        q("""INSERT INTO keys ("KeyCode", "TotalChars", rinex_col_out, rinex_col_in, isnumeric) VALUES
             ('network', 3, 'NetworkCode', 'NetworkCode', '0'), ('year', 4, 'ObservationYear', 'ObservationYear', '1'),
             ('doy', 3, 'ObservationDOY', 'ObservationDOY', '1')""")
        q("""INSERT INTO rinex_tank_struct ("Level", "KeyCode") VALUES (1, 'network'), (2, 'year'), (3, 'doy')""")

    q("""INSERT INTO networks ("NetworkCode", "NetworkName") VALUES ('tst', 'Test network')
         ON CONFLICT DO NOTHING""")
    q("""INSERT INTO api_country (name, two_digits_code, three_digits_code) VALUES ('Argentina', 'AR', 'ARG')
         ON CONFLICT DO NOTHING""")
    q("""INSERT INTO receivers ("ReceiverCode", "ReceiverDescription") VALUES ('TRIMBLE NETR9', 'Trimble NetR9')
         ON CONFLICT DO NOTHING""")
    q("""INSERT INTO antennas ("AntennaCode", "RadomeCode", "AntennaDescription")
         VALUES ('TRM59800.00', 'SCIS', 'Choke ring') ON CONFLICT DO NOTHING""")
    q("""INSERT INTO gamit_htc ("AntennaCode", "HeightCode", v_offset, h_offset)
         VALUES ('TRM59800.00', 'DHARP', 0, 0) ON CONFLICT DO NOTHING""")
    q("""INSERT INTO api_stationstatuscolor (color) VALUES ('green-icon') ON CONFLICT DO NOTHING""")
    q("""INSERT INTO api_stationstatus (name, color_id)
         SELECT 'Active Online', id FROM api_stationstatuscolor WHERE color = 'green-icon' ON CONFLICT DO NOTHING""")
    q("""INSERT INTO api_monumenttype (name, photo_path) VALUES ('Pillar', 'monument_type_photos/pillar.png')
         ON CONFLICT DO NOTHING""")
    q("""INSERT INTO api_stationtype (name, icon, search_icon_on_assets_folder)
         VALUES ('Continuous', 'station_type_icons/continuous.png', FALSE) ON CONFLICT DO NOTHING""")
    q("""INSERT INTO api_stationrole (name) VALUES ('Owner') ON CONFLICT DO NOTHING""")
    q("""INSERT INTO sources_formats (format) VALUES ('DEFAULT_FORMAT') ON CONFLICT DO NOTHING""")

    q("""INSERT INTO stations ("NetworkCode", "StationCode", "StationName", auto_x, auto_y, auto_z,
         "Harpos_coeff_otl", lat, lon, height, max_dist, dome, country_code, marker)
         VALUES ('tst', 'pk01', 'Package test', 2765120.123, -4449250.456, -3626405.789, '$$ otl coefficients',
         -34.123456789, -58.987654321, 25.12345, 20, '41201M001', 'ARG', 1)""")

    q("""INSERT INTO stationinfo ("NetworkCode", "StationCode", "ReceiverCode", "ReceiverSerial", "ReceiverFirmware",
         "AntennaCode", "AntennaSerial", "AntennaHeight", "AntennaNorth", "AntennaEast", "HeightCode", "RadomeCode",
         "DateStart", "DateEnd", "ReceiverVers", "Comments") VALUES
         ('tst', 'pk01', 'TRIMBLE NETR9', '5001', '4.85', 'TRM59800.00', '1441', 0.0083, 0, 0, 'DHARP', 'SCIS',
          '2019-12-31 00:00:00', '2020-01-01 23:59:59', '4.85', 'first'),
         ('tst', 'pk01', 'TRIMBLE NETR9', '5001', '4.93', 'TRM59800.00', '1441', 0.0083, 0, 0, 'DHARP', 'SCIS',
          '2020-01-02 00:00:00', NULL, '4.93', 'second')""")

    for day in (1, 2):
        q("""INSERT INTO rinex ("NetworkCode", "StationCode", "ObservationYear", "ObservationMonth", "ObservationDay",
             "ObservationDOY", "ObservationFYear", "ObservationSTime", "ObservationETime", "ReceiverType",
             "ReceiverSerial", "ReceiverFw", "AntennaType", "AntennaSerial", "AntennaDome", "Filename", "Interval",
             "AntennaOffset", "Completion") VALUES ('tst', 'pk01', 2020, 1, %s, %s, %s, %s, %s, 'TRIMBLE NETR9',
             '5001', '4.85', 'TRM59800.00', '1441', 'SCIS', %s, 30, 0.0083, 0.999)""",
          (day, day, 2020 + (day - 0.5) / 366, datetime.datetime(2020, 1, day),
           datetime.datetime(2020, 1, day, 23, 59, 30), 'pk01%03d0.20o' % day))

    q("""INSERT INTO stationalias ("NetworkCode", "StationCode", "StationAlias") VALUES ('tst', 'pk01', 'pk1a')""")
    q("""INSERT INTO etm_params ("NetworkCode", "StationCode", soln, object, terms, frequencies, jump_type,
         relaxation, "Year", "DOY", action) VALUES ('tst', 'pk01', 'ppp', 'jump', NULL, NULL, 1, '{0.5,1}', 2020, 2,
         '+')""")
    q("""INSERT INTO events ("EventDate", "EventType", "NetworkCode", "StationCode", "Year", "DOY", "Description")
         VALUES ('2020-01-03 10:00:00.123456', 'info', 'tst', 'pk01', 2020, 1, 'first event'),
                ('2020-01-04 10:00:00', 'warn', 'tst', 'pk01', NULL, NULL, 'second event')""")

    metadata_id = q("""INSERT INTO sources_metadata (protocol, fqdn, username, password, path, format)
                       VALUES ('https', 'meta.example.org', NULL, NULL, '/logs/${station}.log', 'DEFAULT_FORMAT')
                       RETURNING id""").dictresult()[0]['id']
    server_id = q("""INSERT INTO sources_servers (protocol, fqdn, username, password, path, format,
                     metadata_source_id) VALUES ('ftp', 'data.example.org', 'user', 's3cret', '/data',
                     'DEFAULT_FORMAT', %s) RETURNING server_id""", (metadata_id,)).dictresult()[0]['server_id']
    q("""INSERT INTO sources_stations ("NetworkCode", "StationCode", try_order, server_id, path, format)
         VALUES ('tst', 'pk01', 1, %s, '/data/pk01', NULL)""", (server_id,))
    add_agencies(cnn, NET, STN, owner='Test agency founder', hosts=('Test agency host',))

    api_id = q('SELECT api_id FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s',
               (NET, STN)).dictresult()[0]['api_id']
    # the row of api_stationmeta comes from the trigger of stations
    q("""UPDATE api_stationmeta SET monument_type_id = (SELECT id FROM api_monumenttype WHERE name = 'Pillar'),
         station_type_id = (SELECT id FROM api_stationtype WHERE name = 'Continuous'), comments = 'roof',
         navigation_file = 'stations/tst/pk01/navigation_file/route.kml', navigation_filename = 'route.kml',
         has_battery = TRUE, battery_description = '12 V' WHERE station_id = %s""", (api_id,))
    person_id = q("""INSERT INTO api_person (first_name, last_name, email, phone, address, photo, institution,
                     position) VALUES ('Ana', 'Perez', 'ana@example.org', '', '', 'person_photos/ana.png', 'IGN', '')
                     RETURNING id""").dictresult()[0]['id']
    q("""INSERT INTO api_rolepersonstation (person_id, station_id, role_id)
         SELECT %s, %s, id FROM api_stationrole WHERE name = 'Owner'""", (person_id, api_id))
    q("""INSERT INTO api_stationimages (image, name, description, station_id)
         VALUES ('stations/tst/pk01/images/front.jpg', 'front.jpg', 'front view', %s)""", (api_id,))
    q("""INSERT INTO api_stationattachedfiles (file, filename, description, station_id)
         VALUES ('stations/tst/pk01/attached_files/log.txt', 'log.txt', 'site log', %s)""", (api_id,))
    campaign_id = q("""INSERT INTO api_campaigns (name, start_date, end_date) VALUES ('Test campaign', '2021-03-01',
                       '2021-03-10') RETURNING id""").dictresult()[0]['id']
    visit_id = q("""INSERT INTO api_visits (date, log_sheet_file, log_sheet_filename, navigation_file,
                    navigation_filename, campaign_id, station_id, comments) VALUES ('2021-03-04',
                    'stations/tst/pk01/visits/2021-03-04/log_sheet_file/sheet.pdf', 'sheet.pdf', '', '', %s, %s,
                    'antenna checked') RETURNING id""", (campaign_id, api_id)).dictresult()[0]['id']
    q('INSERT INTO api_visits_people (visits_id, person_id) VALUES (%s, %s)', (visit_id, person_id))
    q("""INSERT INTO api_visitimages (image, name, description, visit_id)
         VALUES ('stations/tst/pk01/visits/2021-03-04/images/visit.jpg', 'visit.jpg', '', %s)""", (visit_id,))
    q("""INSERT INTO api_visitattachedfiles (file, filename, description, visit_id)
         VALUES ('stations/tst/pk01/visits/2021-03-04/attached_files/notes.pdf', 'notes.pdf', '', %s)""",
      (visit_id,))
    q("""INSERT INTO api_visitgnssdatafiles (file, filename, description, visit_id, size)
         VALUES ('stations/tst/pk01/visits/2021-03-04/gnss_data_files/raw.T02', 'raw.T02', '', %s, '8 B')""",
      (visit_id,))

    for root, files in ((archive_root, RINEX_FILES), (media_root, MEDIA_FILES)):
        for name, content in files.items():
            path = os.path.join(root, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(content)


def make_archive(cnn, archive_root):
    archive = pyArchiveStruct.RinexStruct.__new__(pyArchiveStruct.RinexStruct)
    archive.cnn = cnn
    archive.levels = cnn.query('SELECT rinex_tank_struct.*, keys.* FROM rinex_tank_struct '
                               'LEFT JOIN keys ON keys."KeyCode" = rinex_tank_struct."KeyCode" '
                               'ORDER BY "Level"').dictresult()
    archive.Config = SimpleNamespace(archive_path=archive_root)
    return archive


@pytest.fixture(scope='module')
def cnn():
    from geode import dbConnection
    # the connection runs database_migrations, which create the tables of the agencies
    connection = dbConnection.Cnn(DB_CFG)
    yield connection
    remove_fixture(connection)
    connection.close()


@pytest.fixture
def station(cnn, tmp_path):
    archive_root = str(tmp_path / 'archive')
    media_root = str(tmp_path / 'media')
    create_fixture(cnn, archive_root, media_root)

    archive = make_archive(cnn, archive_root)

    out = tmp_path / 'out'
    out.mkdir()
    return SimpleNamespace(archive=archive, media_root=media_root, out=str(out))


@pytest.fixture
def read_rinex(monkeypatch):
    monkeypatch.setattr(sp.pyRinex, 'ReadRinex', FakeReadRinex)


@database
class TestExport:

    def export(self, cnn, station, **options):
        result = sp.StationExport(cnn, NET, STN, archive=station.archive, media_root=station.media_root,
                                  **options).write(station.out)
        manifest, data = sp.read_package(result['zip'])
        return result, manifest, data

    def test_full_package(self, cnn, station):
        result, manifest, data = self.export(cnn, station, images=True, attachments=True)

        assert result['zip'] == os.path.join(station.out, 'tst.pk01.zip')
        assert result['warnings'] == []
        assert sp.verify_files(result['zip'], manifest) == []
        assert manifest == result['manifest']
        assert manifest['contents'] == {'rinex': True, 'images': True, 'attachments': True, 'studio': True}

        paths = {item['path']: item['kind'] for item in manifest['files']}
        assert paths == dict(
            [(sp.rinex_path(os.path.basename(name)), sp.FILE_RINEX) for name in RINEX_FILES] +
            [(sp.media_path(name), sp.FILE_IMAGE if name.endswith(('.jpg', '.png')) else sp.FILE_ATTACHMENT)
             for name in MEDIA_FILES] +
            [(sp.STATION_FILE, sp.FILE_DATA)])

        with zipfile.ZipFile(result['zip']) as zf:
            assert zf.read('rinex/pk010010.20d.Z') == b'rinex day 1'
            assert zf.read('media/stations/tst/pk01/images/front.jpg') == b'front picture'

        assert data['NetworkCode'] == NET and data['StationCode'] == STN
        assert data['station']['lat'] == -34.123456789
        assert data['station']['country_code'] == 'ARG'
        assert 'api_id' not in data['station']
        contacts = [{'grade': 1, 'type': 'email', 'name': 'Ana Perez', 'value': 'ana@agency.example.gov'},
                    {'grade': 1, 'type': 'phone', 'name': 'Ana Perez', 'value': '+1-614-555-0100'}]
        empty = {'description': None, 'rtn_names': None, 'contacts': contacts}
        assert 'agency' not in data
        assert data['agencies'] == [dict(empty, name='Test agency founder', type='owner'),
                                    dict(empty, name='Test agency host', type='host')]

        tables = data['tables']
        assert [row['Comments'] for row in tables['stationinfo']] == ['first', 'second']
        assert tables['stationinfo'][1]['DateEnd'] is None
        assert [row['Filename'] for row in tables['rinex']] == ['pk010010.20o', 'pk010020.20o']
        assert all('api_id' not in row and 'NetworkCode' not in row for row in tables['rinex'])
        assert tables['stationalias'] == [{'StationAlias': 'pk1a'}]
        assert tables['etm_params'][0]['relaxation'] == [0.5, 1.0]
        assert [row['EventDate'] for row in tables['events']] == ['2020-01-03 10:00:00.123456',
                                                                  '2020-01-04 10:00:00']
        server = tables['sources_stations'][0]['server']
        assert server['password'] == 's3cret'
        assert server['metadata_source']['fqdn'] == 'meta.example.org'
        assert 'server_id' not in tables['sources_stations'][0]

        studio = data['studio']
        meta = studio['station_meta']
        assert meta['monument_type'] == 'Pillar' and meta['station_type'] == 'Continuous'
        assert meta['status'] == 'Active Online'
        assert meta['navigation_file'] == 'stations/tst/pk01/navigation_file/route.kml'
        assert 'has_gaps' not in meta and 'station_id' not in meta
        person = {'first_name': 'Ana', 'last_name': 'Perez', 'email': 'ana@example.org'}
        assert studio['people_roles'] == [{'person': person, 'role': 'Owner'}]
        assert studio['images'][0]['image'] == 'stations/tst/pk01/images/front.jpg'
        visit = studio['visits'][0]
        assert visit['date'] == '2021-03-04'
        assert visit['people'] == [person]
        assert visit['campaign'] == {'name': 'Test campaign', 'start_date': '2021-03-01', 'end_date': '2021-03-10'}
        assert [len(visit[name]) for name in ('images', 'attached_files', 'gnss_data_files')] == [1, 1, 1]

        catalogs = data['catalogs']
        assert [row['NetworkCode'] for row in catalogs['networks']] == ['tst']
        assert [row['ReceiverCode'] for row in catalogs['receivers']] == ['TRIMBLE NETR9']
        assert catalogs['antennas'] == [{'AntennaCode': 'TRM59800.00', 'RadomeCode': 'SCIS',
                                         'AntennaDescription': 'Choke ring'}]
        assert [row['HeightCode'] for row in catalogs['gamit_htc']] == ['DHARP']
        assert [row['three_digits_code'] for row in catalogs['countries']] == ['ARG']
        assert catalogs['station_statuses'] == [{'name': 'Active Online', 'color': 'green-icon'}]
        assert [row['color'] for row in catalogs['station_status_colors']] == ['green-icon']
        assert [row['name'] for row in catalogs['monument_types']] == ['Pillar']
        assert [row['name'] for row in catalogs['station_types']] == ['Continuous']
        assert [row['name'] for row in catalogs['station_roles']] == ['Owner']
        assert [row['email'] for row in catalogs['people']] == ['ana@example.org']
        assert 'user_id' not in catalogs['people'][0]
        assert [row['name'] for row in catalogs['campaigns']] == ['Test campaign']
        assert [row['format'] for row in catalogs['sources_formats']] == ['DEFAULT_FORMAT']

        counts = manifest['counts']
        assert (counts['rinex'], counts['rinex_files'], counts['image_files'], counts['attachment_files']) == \
            (2, 2, 5, 5)
        assert (counts['visits'], counts['images'], counts['attached_files']) == (1, 2, 3)
        assert counts['agencies'] == 2

    def test_dataless_package_keeps_the_rows(self, cnn, station):
        result, manifest, data = self.export(cnn, station, rinex=False)
        assert [item['path'] for item in manifest['files']] == [sp.STATION_FILE]
        assert manifest['contents']['rinex'] is False
        assert len(data['tables']['rinex']) == 2
        assert data['studio']['images'][0]['image'] == 'stations/tst/pk01/images/front.jpg'

    def test_only_pictures(self, cnn, station):
        result, manifest, _ = self.export(cnn, station, rinex=False, images=True)
        assert {item['kind'] for item in manifest['files']} == {sp.FILE_IMAGE, sp.FILE_DATA}

    def test_missing_files_are_warnings(self, cnn, station):
        os.remove(os.path.join(station.archive.Config.archive_path, 'tst/2020/002/pk010020.20d.Z'))
        os.remove(os.path.join(station.media_root, 'stations/tst/pk01/images/front.jpg'))
        result, manifest, data = self.export(cnn, station, images=True)
        assert len(result['warnings']) == 2
        assert manifest['counts']['rinex_files'] == 1
        assert len(data['tables']['rinex']) == 2

    def test_station_without_coordinates_is_exported(self, cnn, station):
        cnn.query('UPDATE stations SET lat = NULL, lon = NULL, height = NULL WHERE "NetworkCode" = %s AND '
                  '"StationCode" = %s', (NET, STN))
        result, _, data = self.export(cnn, station, rinex=False)
        assert data['station']['lat'] is None
        assert any('incomplete coordinates' in warning for warning in result['warnings'])

    def test_unknown_station(self, cnn, station):
        with pytest.raises(sp.StationPackageException, match='does not exist'):
            sp.StationExport(cnn, NET, 'zzzz', archive=station.archive).collect()

    def test_two_exports_have_the_same_data(self, cnn, station):
        first = self.export(cnn, station, images=True)[2]
        second = self.export(cnn, station, images=True)[2]
        assert first == second

    def test_export_stations(self, cnn, station):
        results = sp.export_stations(cnn, [{'NetworkCode': NET, 'StationCode': STN},
                                           {'NetworkCode': NET, 'StationCode': 'zzzz'}],
                                     folder=station.out, archive=station.archive)
        assert results[0]['zip'].endswith('tst.pk01.zip')
        assert results[1] == {'station': 'tst.zzzz', 'error': 'Station tst.zzzz does not exist.'}


def api_id(cnn, network=NET, station=STN):
    rows = cnn.query('SELECT api_id FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s',
                     (network, station)).dictresult()
    return rows[0]['api_id'] if rows else None


def count(cnn, sql, params=()):
    return cnn.query('SELECT count(*) AS n FROM ' + sql, params).dictresult()[0]['n']


def read(path):
    with open(path, 'rb') as f:
        return f.read()


# catalog rows that remove_fixture leaves: their pictures are not copied again by the import
KEPT_CATALOG_FILES = ('monument_type_photos/pillar.png', 'station_type_icons/continuous.png')


@database
class TestImport:

    def export(self, cnn, station):
        result = sp.StationExport(cnn, NET, STN, archive=station.archive, media_root=station.media_root,
                                  images=True, attachments=True).write(station.out)
        return result['zip']

    @staticmethod
    def destination(cnn, tmp_path):
        """Empty archive and media folder of another GeoDE system."""
        root = tmp_path / 'destination'
        for folder in ('archive', 'media', 'out'):
            (root / folder).mkdir(parents=True)
        return SimpleNamespace(archive=make_archive(cnn, str(root / 'archive')), media_root=str(root / 'media'),
                               out=str(root / 'out'))

    @staticmethod
    def run(cnn, path, place, network=NET, station=None, existing=False):
        package = sp.StationImport(cnn, path, archive=place.archive, media_root=place.media_root)
        return package.execute(network, station, existing=existing)

    def test_round_trip(self, cnn, station, tmp_path):
        package = self.export(cnn, station)
        _, original = sp.read_package(package)
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)

        report = self.run(cnn, package, destination)

        assert report['rows']['rinex'] == 2 and report['rows']['stationinfo'] == 2
        assert report['files'] == {sp.FILE_RINEX: 2, sp.FILE_IMAGE: 3, sp.FILE_ATTACHMENT: 5}
        assert {'people', 'campaigns', 'sources_servers', 'sources_metadata'} <= set(report['created'])
        assert 'receivers' not in report['created']
        # remove_fixture removed the agencies too: created again, by name
        assert sorted(report['created']['agencies']) == ['Test agency founder', 'Test agency host']
        assert report['owner'] == 'Test agency founder' and report['rows']['agency_stations'] == 2
        assert station_agencies(cnn) == ('Test agency founder', ['Test agency host'])

        # the trigger of stations created the row: it was updated, not inserted twice
        meta = cnn.query('SELECT comments, has_battery, battery_description FROM api_stationmeta '
                         'WHERE station_id = %s', (api_id(cnn),)).dictresult()
        assert meta == [{'comments': 'roof', 'has_battery': True, 'battery_description': '12 V'}]

        archive_root = destination.archive.Config.archive_path
        for name, content in RINEX_FILES.items():
            assert read(os.path.join(archive_root, name)) == content
        for name, content in MEDIA_FILES.items():
            path = os.path.join(destination.media_root, name)
            if name in KEPT_CATALOG_FILES:
                assert not os.path.exists(path)
            else:
                assert read(path) == content
        assert os.stat(os.path.join(destination.media_root, 'stations/tst/pk01/images/front.jpg')).st_uid == \
            os.stat(destination.media_root).st_uid

        again = sp.StationExport(cnn, NET, STN, archive=destination.archive, media_root=destination.media_root,
                                 images=True, attachments=True).write(destination.out)
        _, imported = sp.read_package(again['zip'])
        events = imported['tables']['events']
        import_events = [event for event in events if event['Description'].startswith('Station package')]
        assert len(import_events) == 1
        imported['tables']['events'] = [event for event in events if event not in import_events]
        assert imported == original

    def test_existing_station_is_not_overwritten(self, cnn, station):
        package = self.export(cnn, station)
        cnn.query('UPDATE stations SET "StationName" = %s WHERE "NetworkCode" = %s AND "StationCode" = %s',
                  ('Changed', NET, STN))
        cnn.query('UPDATE api_stationmeta SET comments = %s WHERE station_id = %s', ('changed', api_id(cnn)))
        cnn.query('DELETE FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s AND "ObservationDOY" = 2',
                  (NET, STN))
        day2 = os.path.join(station.archive.Config.archive_path, 'tst/2020/002/pk010020.20d.Z')
        os.remove(day2)

        report = self.run(cnn, package, station, existing=True)

        assert report['rows'] == {'rinex': 1}
        assert report['skipped'] == {'rinex': {'The station already has this RINEX file.': 1}}
        assert [(entry['table'], entry['record']) for entry in report['not_imported']] == [('rinex', 'pk010010.20o')]
        assert read(day2) == b'rinex day 2'
        assert count(cnn, 'rinex WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN)) == 2
        assert cnn.query('SELECT "StationName" FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s',
                         (NET, STN)).dictresult()[0]['StationName'] == 'Changed'
        assert cnn.query('SELECT comments FROM api_stationmeta WHERE station_id = %s',
                         (api_id(cnn),)).dictresult()[0]['comments'] == 'changed'
        assert count(cnn, 'api_stationimages WHERE station_id = %s', (api_id(cnn),)) == 1
        assert count(cnn, 'stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN)) == 2

    @staticmethod
    def stationinfo(cnn):
        return [(row['DateStart'], row['DateEnd']) for row in cnn.query(
            'SELECT "DateStart", "DateEnd" FROM stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s '
            'ORDER BY "DateStart"', (NET, STN)).dictresult()]

    def test_existing_station_gets_the_station_info_of_its_rinex(self, cnn, station):
        """
        As the -import of format 0: for each RINEX file of the package whose date has no station info, the record of
        the package that covers it. Also for a RINEX file the station already had.
        """
        package = self.export(cnn, station)
        cnn.query('DELETE FROM stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN))
        cnn.query('DELETE FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s AND "ObservationDOY" = 2',
                  (NET, STN))
        os.remove(os.path.join(station.archive.Config.archive_path, 'tst/2020/002/pk010020.20d.Z'))

        report = self.run(cnn, package, station, existing=True)

        assert report['rows'] == {'rinex': 1, 'stationinfo': 2}
        assert report['skipped'] == {'rinex': {'The station already has this RINEX file.': 1}}
        assert self.stationinfo(cnn) == [(datetime.datetime(2019, 12, 31), datetime.datetime(2020, 1, 1, 23, 59, 59)),
                                         (datetime.datetime(2020, 1, 2), None)]
        assert count(cnn, "events WHERE \"NetworkCode\" = %s AND \"StationCode\" = %s AND \"Description\" LIKE "
                     "'A new station information record was added%%'", (NET, STN)) == 2

    def test_existing_station_and_a_package_without_rinex(self, cnn, station):
        """Without RINEX files there is no date to look for: the station info of the package is not used."""
        package = sp.StationExport(cnn, NET, STN, rinex=False).write(station.out)['zip']
        cnn.query('DELETE FROM stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN))

        report = self.run(cnn, package, station, existing=True)

        assert 'stationinfo' not in report['rows'] and self.stationinfo(cnn) == []
        assert any(warning.startswith('Dataless package') for warning in report['warnings'])

    def test_format0_package_into_an_existing_station(self, cnn, station, tmp_path, read_rinex):
        cnn.query('DELETE FROM stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN))
        cnn.query('DELETE FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN))
        for name in RINEX_FILES:
            os.remove(os.path.join(station.archive.Config.archive_path, name))
        path = write_format0_package(tmp_path / 'tst.pk01.zip', FORMAT0_STATION,
                                     {'pk010010.20d.Z': b'day 1', 'pk010030.20d.Z': b'day 3'})

        report = sp.StationImport(cnn, path, archive=station.archive,
                                  media_root=station.media_root).execute(NET, existing=True)

        assert report['rows'] == {'rinex': 2, 'stationinfo': 1}
        # day 3 is after the record of the package
        assert self.stationinfo(cnn) == [(datetime.datetime(2019, 12, 31), datetime.datetime(2020, 1, 2, 23, 59, 59))]
        assert '1 RINEX file(s) have no valid station info record in the database nor in the package, so they are ' \
               'in the database without metadata: pk010030.20d.Z.' in report['warnings']
        rows = cnn.query('SELECT "Filename", "Completion" FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s '
                         'ORDER BY "ObservationDOY"', (NET, STN)).dictresult()
        assert [(row['Filename'], float(row['Completion'])) for row in rows] == \
            [('pk010010.20o', FakeReadRinex.completion), ('pk010030.20o', FakeReadRinex.completion)]
        assert read(os.path.join(station.archive.Config.archive_path, 'tst/2020/003/pk010030.20d.Z')) == b'day 3'

    def test_rinex_file_without_its_record(self, cnn, station, tmp_path, read_rinex):
        """A package made by another program brings the files without their records: GeoDE reads them."""
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        unsafe = 'rinex/x$(id)pk010040.20d.Z'
        path = write_package(tmp_path / 'x.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': NET, 'StationCode': STN,
            'station': {'StationName': 'Outside', 'lat': -34.6, 'lon': -58.4, 'height': 25.0, 'country_code': 'ARG'},
            'tables': {'stationinfo': [dict(FORMAT0_STATIONINFO, DateStart='2020-01-01 00:00:00', DateEnd=None)]}},
            {'rinex/PK0100ARG_R_20200030000_01D_30S_MO.crx.gz': (sp.FILE_RINEX, b'day 3'),
             unsafe: (sp.FILE_RINEX, b'day 4')})

        report = self.run(cnn, path, destination)

        assert report['rows'] == {'stations': 1, 'stationinfo': 1, 'rinex': 1}
        assert report['files'][sp.FILE_RINEX] == 1
        [entry] = report['not_imported']
        assert (entry['table'], entry['record'], entry['error']) == ('rinex', unsafe, 'Invalid RINEX file name.')
        rows = cnn.query('SELECT "Filename", "Completion" FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s',
                         (NET, STN)).dictresult()
        assert [(row['Filename'], float(row['Completion'])) for row in rows] == \
            [('PK0100ARG_R_20200030000_01D_30S_MO.rnx', FakeReadRinex.completion)]
        assert read(os.path.join(destination.archive.Config.archive_path,
                                 'tst/2020/003/PK0100ARG_R_20200030000_01D_30S_MO.crx.gz')) == b'day 3'
        assert count(cnn, "events WHERE \"NetworkCode\" = %s AND \"StationCode\" = %s AND \"Description\" LIKE "
                     "'A new RINEX was added to the archive%%'", (NET, STN)) == 1

    @pytest.mark.parametrize('max_dist, meters, added', [
        (20, 15, True),
        (10, 15, False),
        (None, 15, True),
        (None, 25, False),
    ])
    def test_existing_station_within_its_max_dist(self, cnn, station, tmp_path, max_dist, meters, added):
        cnn.query('UPDATE stations SET max_dist = %s WHERE "NetworkCode" = %s AND "StationCode" = %s',
                  (max_dist, NET, STN))
        # the fixture station is at -34.123456789, -58.987654321: the package is `meters` north of it
        path = write_package(tmp_path / 'x.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': NET, 'StationCode': STN,
            'station': {'lat': -34.123456789 + meters / (6371000 * numpy.pi / 180), 'lon': -58.987654321}})
        events = count(cnn, 'events')

        if added:
            report = self.run(cnn, path, station, existing=True)
            assert not any('max_dist' in warning for warning in report['warnings'])
            assert count(cnn, 'events') == events + 1
        else:
            message = r'are %d m away from station %s\.%s, more than its max_dist \(%d m\)' % \
                (meters, NET, STN, max_dist or sp.DEFAULT_MAX_DIST)
            with pytest.raises(sp.StationPackageException, match=message) as refused:
                self.run(cnn, path, station, existing=True)
            assert (refused.value.table, refused.value.record) == ('stations', '%s.%s' % (NET, STN))
            assert count(cnn, 'events') == events
            assert not cnn.active_transaction

    def test_format_meters(self):
        assert [sp.format_meters(value) for value in (20, 20.0, 0.5, 25.0041, 10000, 0)] == \
            ['20 m', '20 m', '0.5 m', '25.004 m', '10000 m', '0 m']

    def test_great_circle_distance(self):
        assert sp.great_circle_distance(-34.0, -58.0, -34.0, -58.0) == 0
        assert sp.great_circle_distance(0.0, 0.0, 1.0, 0.0) == pytest.approx(6371000 * numpy.pi / 180)

    def test_new_station_that_exists_is_refused(self, cnn, station):
        package = self.export(cnn, station)
        events = count(cnn, 'events')
        with pytest.raises(sp.StationPackageException, match='already exists'):
            self.run(cnn, package, station)
        assert count(cnn, 'events') == events
        assert not cnn.active_transaction

    def test_failed_record_does_not_stop_the_import(self, cnn, station, tmp_path):
        package = self.export(cnn, station)
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        archive_root = destination.archive.Config.archive_path
        # a file of day 2 already in the archive, without its RINEX row
        stray = os.path.join(archive_root, 'tst/2020/002/pk010020.20d.Z')
        os.makedirs(os.path.dirname(stray))
        with open(stray, 'wb') as f:
            f.write(b'stray file')

        report = self.run(cnn, package, destination)

        assert report['rows']['rinex'] == 1 and report['files'][sp.FILE_RINEX] == 1
        assert report['rows']['stationinfo'] == 2 and report['rows']['api_visits'] == 1
        [entry] = report['not_imported']
        assert (entry['package'], entry['station'], entry['table'], entry['record']) == \
            (os.path.basename(package), 'tst.pk01', 'rinex', 'pk010020.20o')
        assert 'already in the archive without a RINEX row' in entry['error'] and entry['data']['ObservationDOY'] == 2
        assert read(stray) == b'stray file'
        assert read(os.path.join(archive_root, 'tst/2020/001/pk010010.20d.Z')) == b'rinex day 1'
        assert count(cnn, 'rinex WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN)) == 1
        assert not cnn.active_transaction

    def test_failed_record_removes_its_files(self, cnn, station, tmp_path, monkeypatch):
        package = self.export(cnn, station)
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        insert = sp.StationImport._insert

        def failing_insert(self, table, values, returning=None):
            if table == 'api_stationimages':
                raise sp.StationPackageException('simulated failure')
            return insert(self, table, values, returning)
        monkeypatch.setattr(sp.StationImport, '_insert', failing_insert)

        report = self.run(cnn, package, destination)

        assert report['skipped']['api_stationimages'] == {'simulated failure': 1}
        assert report['files'][sp.FILE_IMAGE] == 2
        assert not os.path.exists(os.path.join(destination.media_root, 'stations/tst/pk01/images/front.jpg'))
        assert count(cnn, 'api_stationimages WHERE station_id = %s', (api_id(cnn),)) == 0
        assert count(cnn, 'api_stationattachedfiles WHERE station_id = %s', (api_id(cnn),)) == 1

    def test_unknown_equipment_is_reported(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        unknown = dict(FORMAT0_STATIONINFO, ReceiverCode='UNKNOWN RX', DateStart='2021-01-01 00:00:00', DateEnd=None)
        path = write_package(tmp_path / 'x.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Outside', 'marker': 0, 'country_code': 'ARG'},
            'tables': {'stationinfo': [dict(FORMAT0_STATIONINFO, DateStart='2020-01-01 00:00:00',
                                            DateEnd='2020-12-31 23:59:59'), unknown]}})

        report = self.run(cnn, path, self.destination(cnn, tmp_path))

        assert report['rows'] == {'stations': 1, 'stationinfo': 1}
        [entry] = report['not_imported']
        assert entry['table'] == 'stationinfo' and entry['record'] == '2021-01-01 00:00:00'
        assert entry['error'] == 'Receiver UNKNOWN RX is not in the receivers catalog of this database and the ' \
                                 'package does not include it.'
        assert count(cnn, 'stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN)) == 1

    @pytest.mark.parametrize('radome, error', [
        (None, 'Antenna UNKNOWN ANT is not in the antennas catalog of this database and the package does not '
               'include it.'),
        ('NONE', 'Antenna UNKNOWN ANT with radome NONE is not in the antennas catalog of this database and the '
                 'package does not include it.')])
    def test_unknown_antenna_is_reported(self, cnn, station, tmp_path, radome, error):
        """Also without a radome, which is NOT NULL in stationinfo: the report says the antenna is unknown."""
        remove_fixture(cnn)
        unknown = dict(FORMAT0_STATIONINFO, AntennaCode='UNKNOWN ANT', RadomeCode=radome,
                       DateStart='2021-01-01 00:00:00', DateEnd=None)
        path = write_package(tmp_path / 'x.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Outside', 'marker': 0, 'country_code': 'ARG'},
            'tables': {'stationinfo': [dict(FORMAT0_STATIONINFO, DateStart='2020-01-01 00:00:00',
                                            DateEnd='2020-12-31 23:59:59'), unknown]}})

        report = self.run(cnn, path, self.destination(cnn, tmp_path))

        assert report['rows'] == {'stations': 1, 'stationinfo': 1}
        [entry] = report['not_imported']
        assert (entry['table'], entry['record'], entry['error']) == ('stationinfo', '2021-01-01 00:00:00', error)

    def test_failed_package_leaves_nothing(self, cnn, station, tmp_path, monkeypatch):
        package = self.export(cnn, station)
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        archive_root = destination.archive.Config.archive_path

        # a failure of the package itself, after every record was done
        def crash(self):
            raise RuntimeError('simulated crash')
        monkeypatch.setattr(sp.StationImport, '_add_event', crash)

        with pytest.raises(RuntimeError, match='simulated crash'):
            self.run(cnn, package, destination)

        assert not cnn.active_transaction
        assert api_id(cnn) is None
        assert count(cnn, "api_person WHERE email = 'ana@example.org'") == 0
        assert count(cnn, "sources_servers WHERE fqdn = 'data.example.org'") == 0
        assert count(cnn, 'stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN)) == 0
        assert os.listdir(archive_root) == []
        assert os.listdir(destination.media_root) == []

    def test_import_into_another_network(self, cnn, station, tmp_path):
        package = self.export(cnn, station)
        destination = self.destination(cnn, tmp_path)
        try:
            report = self.run(cnn, package, destination, network='tsx')

            assert report['created']['networks'] == ['tsx']
            assert report['skipped']['stationalias'] == \
                {'Another station uses this alias as station code or alias.': 1}
            assert 'people' not in report['created'] and 'sources_servers' not in report['created']
            new_id = api_id(cnn, 'tsx', STN)
            images = cnn.query('SELECT image FROM api_stationimages WHERE station_id = %s', (new_id,)).dictresult()
            assert images == [{'image': 'stations/tsx/pk01/images/front.jpg'}]
            assert read(os.path.join(destination.media_root, 'stations/tsx/pk01/images/front.jpg')) == \
                b'front picture'
            assert read(os.path.join(destination.archive.Config.archive_path, 'tsx/2020/001/pk010010.20d.Z')) == \
                b'rinex day 1'
            assert count(cnn, 'api_stationmeta WHERE station_id = %s', (new_id,)) == 1
        finally:
            remove_station(cnn, 'tsx', STN)
            cnn.query("DELETE FROM networks WHERE \"NetworkCode\" = 'tsx'")

    def test_format0_package(self, cnn, station, tmp_path, read_rinex):
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        path = write_format0_package(tmp_path / 'tst.pk01.zip', FORMAT0_STATION, {'pk010010.20d.Z': b'old rinex'})

        report = sp.StationImport(cnn, path, archive=destination.archive,
                                  media_root=destination.media_root).execute(NET)

        assert report['format0'] and report['rows'] == {'stations': 1, 'stationinfo': 1, 'rinex': 1}
        assert report['not_imported'] == []
        row = cnn.query('SELECT lat, lon, height, auto_x, dome, marker, country_code FROM stations WHERE '
                        '"NetworkCode" = %s AND "StationCode" = %s', (NET, STN)).dictresult()[0]
        assert row == {'lat': -34.123456789, 'lon': -58.987654321, 'height': 25.12345, 'auto_x': 2765120.123,
                       'dome': '41201M001', 'marker': 1, 'country_code': 'ARG'}
        info = cnn.query('SELECT "DateStart", "DateEnd", "Comments" FROM stationinfo WHERE "NetworkCode" = %s '
                         'AND "StationCode" = %s', (NET, STN)).dictresult()
        assert info == [{'DateStart': datetime.datetime(2019, 12, 31), 'DateEnd': datetime.datetime(2020, 1, 2, 23, 59, 59),
                         'Comments': 'old package'}]
        rinex = cnn.query('SELECT "Filename", "Completion" FROM rinex WHERE "NetworkCode" = %s AND '
                          '"StationCode" = %s', (NET, STN)).dictresult()
        assert [(r['Filename'], float(r['Completion'])) for r in rinex] == [('pk010010.20o', FakeReadRinex.completion)]
        assert read(os.path.join(destination.archive.Config.archive_path, 'tst/2020/001/pk010010.20d.Z')) == \
            b'old rinex'

    def test_station_made_outside_geode(self, cnn, station, tmp_path):
        """No RINEX, no OTL, only the name, marker and country of the station."""
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        server = {'protocol': 'FTP', 'fqdn': 'ftp.example.gov', 'username': None, 'password': None,
                  'path': '/gnss/rinex/${station}/${year}/${doy}/${station}${doy}0.${year2d}d.Z',
                  'format': 'DEFAULT_FORMAT',
                  'metadata_source': {'protocol': 'FTP', 'fqdn': 'ftp.example.gov',
                                      'path': '/gnss/metadata/${STATION}.log'}}
        path = write_package(tmp_path / 'x.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Outside', 'marker': 2, 'country_code': 'ARG'},
            'tables': {'stationinfo': [dict(FORMAT0_STATIONINFO, DateStart='2020-01-01 00:00:00', DateEnd=None)],
                       'sources_stations': [{'try_order': 1, 'path': None, 'format': None, 'server': server}]}})

        try:
            report = self.run(cnn, path, destination)

            assert report['rows'] == {'stations': 1, 'stationinfo': 1, 'sources_stations': 1}
            paths = cnn.query("SELECT sv.path AS data, md.path AS metadata FROM sources_stations st "
                              "JOIN sources_servers sv USING (server_id) "
                              "JOIN sources_metadata md ON md.id = sv.metadata_source_id "
                              "WHERE st.\"NetworkCode\" = %s AND st.\"StationCode\" = %s", (NET, STN)).dictresult()
            assert paths == [{'data': '/gnss/rinex/${station}/${year}/${doy}/${station}${doy}0.${year2d}d.Z',
                              'metadata': '/gnss/metadata/${STATION}.log'}]
            assert any('-otl' in warning for warning in report['warnings'])
        finally:
            remove_station(cnn, NET, STN)
            cnn.query("DELETE FROM sources_servers WHERE fqdn = 'ftp.example.gov'")
            cnn.query("DELETE FROM sources_metadata WHERE fqdn = 'ftp.example.gov'")

    def test_station_made_outside_geode_rows(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        path = write_package(tmp_path / 'x.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Outside', 'lat': -34.6, 'lon': -58.4, 'height': 25.0},
            'custom_fields': {'ngs_pid': 'AB1234', 'installation_date': '2015-06-01'}})
        report = self.run(cnn, path, self.destination(cnn, tmp_path))
        row = cnn.query('SELECT "StationName", marker, country_code FROM stations WHERE "NetworkCode" = %s AND '
                        '"StationCode" = %s', (NET, STN)).dictresult()[0]
        # no marker: NULL, like the stations GeoDE already has (process_stnlist reads it as 00)
        assert row == {'StationName': 'Outside', 'marker': None, 'country_code': 'ARG'}
        assert 'The station has no country_code: ARG assigned from its coordinates.' in report['warnings']
        assert any('2 custom field(s) (installation_date, ngs_pid)' in warning for warning in report['warnings'])
        assert count(cnn, 'api_stationmeta WHERE station_id = %s', (api_id(cnn),)) == 1

    def test_server_natural_key(self, cnn, station, tmp_path):
        """A server is the same server when protocol, fqdn, path and username are equal."""
        remove_fixture(cnn)
        cnn.query("INSERT INTO sources_servers (protocol, fqdn, username, password, path) "
                  "VALUES ('FTP', 'ftp.example.gov', 'agency', 'old-password', '/rinex/${RINEX2}')")
        same = {'protocol': 'FTP', 'fqdn': 'ftp.example.gov', 'username': 'agency', 'password': 'new-password',
                'path': '/rinex/${RINEX2}'}
        other_user = dict(same, username='other')
        path = write_package(tmp_path / 'x.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Outside', 'marker': 0, 'country_code': 'ARG'},
            'tables': {'sources_stations': [{'try_order': 1, 'server': same},
                                            {'try_order': 2, 'server': other_user}]}})
        try:
            report = self.run(cnn, path, self.destination(cnn, tmp_path))

            servers = cnn.query("SELECT username, password FROM sources_servers WHERE fqdn = 'ftp.example.gov' "
                                "ORDER BY server_id").dictresult()
            assert servers == [{'username': 'agency', 'password': 'old-password'},
                               {'username': 'other', 'password': 'new-password'}]
            assert report['created']['sources_servers'] == ['ftp.example.gov']
            [warning] = [warning for warning in report['warnings'] if 'already in this database' in warning]
            assert 'user agency' in warning and 'password' in warning and 'new-password' not in warning
            assert count(cnn, 'sources_stations WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN)) == 2
        finally:
            remove_station(cnn, NET, STN)
            cnn.query("DELETE FROM sources_servers WHERE fqdn = 'ftp.example.gov'")

    @pytest.mark.parametrize('station_row, error', [
        ({'lat': 40.0, 'lon': -83.0}, 'Country USA of the coordinates of the station is not in api_country'),
        ({'lat': 0.0, 'lon': -30.0}, 'No country contains the coordinates of the station (0.000000, -30.000000)'),
        ({'StationName': 'No coordinates'}, 'The station has no country_code nor coordinates'),
    ])
    def test_station_without_a_country_is_refused(self, cnn, station, tmp_path, station_row, error):
        remove_fixture(cnn)
        path = write_package(tmp_path / 'x.zip', {'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs',
                                                   'StationCode': STN, 'station': station_row})
        with pytest.raises(sp.StationPackageException, match=re.escape(error)) as refused:
            self.run(cnn, path, self.destination(cnn, tmp_path))
        assert (refused.value.table, refused.value.record) == ('stations', '%s.%s' % (NET, STN))
        assert refused.value.data == station_row
        assert api_id(cnn) is None

    def test_scan_archive_batch_report(self, cnn, station, tmp_path, scan_archive, answer_yes, capsys):
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        broken = tmp_path / 'broken.zip'
        broken.write_bytes(b'not a zip')
        good = write_package(tmp_path / 'ngs.pk01.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Outside', 'marker': 0, 'country_code': 'ARG'},
            'tables': {'stationinfo': [dict(FORMAT0_STATIONINFO, ReceiverCode='UNKNOWN RX',
                                            DateStart='2020-01-01 00:00:00', DateEnd=None)]}})

        failed = scan_archive.import_station(cnn, [NET, str(broken), str(good)], archive=destination.archive,
                                             media_root=destination.media_root)

        assert api_id(cnn) is not None
        assert [[entry[key] for key in ('package', 'station', 'table', 'record')] for entry in failed] == \
            [['broken.zip', '', 'package', 'broken.zip'],
             ['ngs.pk01.zip', 'tst.pk01', 'stationinfo', '2020-01-01 00:00:00']]
        assert 'is not a valid zip file' in failed[0]['error'] and 'UNKNOWN RX' in failed[1]['error']
        output = capsys.readouterr().out
        assert 'not imported: stationinfo 2020-01-01 00:00:00: Receiver UNKNOWN RX' in output
        assert '2 record(s) or package(s) were not imported (listed above).' in output

    def test_scan_archive_station_without_a_country(self, cnn, station, tmp_path, scan_archive, answer_yes):
        """The stations row of a station whose country cannot be computed is what was not imported."""
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        ocean = write_package(tmp_path / 'ngs.pk01.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Buoy', 'lat': 0.0, 'lon': -30.0, 'height': 0.0}})

        failed = scan_archive.import_station(cnn, [NET, str(ocean)], archive=destination.archive,
                                             media_root=destination.media_root)
        assert api_id(cnn) is None
        assert [[entry[key] for key in ('package', 'station', 'table', 'record')] for entry in failed] == \
            [['ngs.pk01.zip', 'tst.pk01', 'stations', 'tst.pk01']]
        assert failed[0]['error'].startswith('No country contains the coordinates of the station (0.000000, '
                                             '-30.000000)')
        data = failed[0]['data']
        assert (data['StationName'], data['lat'], data['lon']) == ('Buoy', 0.0, -30.0)

    def test_scan_archive_everything_imported(self, cnn, station, tmp_path, scan_archive, answer_yes, capsys):
        remove_fixture(cnn)
        destination = self.destination(cnn, tmp_path)
        good = write_package(tmp_path / 'ngs.pk01.zip', {
            'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
            'station': {'StationName': 'Outside', 'marker': 0, 'country_code': 'ARG'}})
        assert scan_archive.import_station(cnn, [NET, str(good)], archive=destination.archive,
                                           media_root=destination.media_root) == []
        assert 'Every record of the package(s) was imported.' in capsys.readouterr().out

    def test_unknown_country_is_refused(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        path = write_package(tmp_path / 'x.zip', {'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs',
                                                   'StationCode': STN, 'station': {'country_code': 'ZZZ'}})
        with pytest.raises(sp.StationPackageException, match='ZZZ'):
            self.run(cnn, path, self.destination(cnn, tmp_path))
        assert api_id(cnn) is None


@database
class TestAgencies:
    """Agencies of a station (agency_stations): one owner at most, and hosts."""

    run = staticmethod(TestImport.run)
    destination = staticmethod(TestImport.destination)

    @staticmethod
    def outside_package(tmp_path, agencies=None, **station):
        data = {'format': sp.FORMAT, 'format_version': 1, 'NetworkCode': 'ngs', 'StationCode': STN,
                'station': dict({'StationName': 'Outside', 'marker': 0, 'country_code': 'ARG'}, **station)}
        if agencies is not None:
            data['agencies'] = agencies
        return write_package(tmp_path / 'ngs.pk01.zip', data)

    @staticmethod
    def agency(name, role, **keys):
        """With its technical contact: a new agency needs it."""
        return dict({'name': name, 'type': role, 'contacts': [
            {'grade': 1, 'type': 'email', 'name': 'Ana Perez', 'value': 'ana@agency.example.gov'},
            {'grade': 1, 'type': 'phone', 'name': 'Ana Perez', 'value': '+1-614-555-0100'}]}, **keys)

    def test_roles_of_the_database(self, cnn):
        roles = {row['name'] for row in cnn.query('SELECT name FROM agency_roles').dictresult()}
        assert {sp.OWNER, sp.HOST} <= roles

    def test_new_station_matches_the_agencies_without_case(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        insert_agency(cnn, 'Test agency ODOT')
        path = self.outside_package(tmp_path, agencies=[self.agency('Test agency NGS', 'host',
                                                                   description='RINEX server'),
                                                       {'name': 'test agency odot', 'type': 'owner'}])

        report = self.run(cnn, path, self.destination(cnn, tmp_path))

        assert station_agencies(cnn) == ('Test agency ODOT', ['Test agency NGS'])
        assert report['created']['agencies'] == ['Test agency NGS']
        assert report['owner'] == 'test agency odot' and report['rows']['agency_stations'] == 2
        assert count(cnn, "agencies WHERE name ILIKE 'test agency%%'") == 2
        assert count(cnn, "agency_stations WHERE description = 'RINEX server'") == 1
        assert report['not_imported'] == []
        assert any('owner: test agency odot' in line for line in sp.format_import_report(report))

    def test_a_new_agency_needs_its_technical_contact(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        path = self.outside_package(tmp_path, agencies=[{'name': 'Test agency NGS', 'type': 'owner', 'contacts': [
            {'grade': 1, 'type': 'email', 'name': 'Ana Perez', 'value': 'ana@ngs.example.gov'}]}])

        report = self.run(cnn, path, self.destination(cnn, tmp_path))

        assert api_id(cnn) is not None and station_agencies(cnn) == (None, [])
        [entry] = report['not_imported']
        assert (entry['table'], entry['record']) == ('agencies', 'owner Test agency NGS')
        assert entry['error'] == 'A new agency needs a technical contact (grade 1) with a name, an email and a ' \
                                 'phone; it has no primary_contact_phone.'

    def test_role_the_database_does_not_have(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        path = self.outside_package(tmp_path, agencies=[self.agency('Test agency NGS', 'instrument')])
        report = self.run(cnn, path, self.destination(cnn, tmp_path))
        [entry] = report['not_imported']
        assert entry['error'].startswith('The role instrument is not in agency_roles (')
        assert count(cnn, "agencies WHERE name ILIKE 'test agency%%'") == 0

    def test_existing_station_with_another_owner(self, cnn, station, tmp_path):
        path = self.outside_package(tmp_path, agencies=[self.agency('Test agency other', 'owner'),
                                                       {'name': 'test agency HOST', 'type': 'host'},
                                                       self.agency('Test agency new', 'host'),
                                                       {'name': 'TEST AGENCY FOUNDER', 'type': 'host'}])

        report = self.run(cnn, path, station, existing=True)

        assert station_agencies(cnn) == ('Test agency founder', ['Test agency host', 'Test agency new',
                                                                 'Test agency other'])
        assert report['rows'] == {'agency_stations': 2}
        assert report['owner'] is None
        assert 'Station tst.pk01 already has the owner Test agency founder: Test agency other, the owner in the ' \
               'package, is a host of it.' in report['warnings']
        assert report['created'] == {'agencies': ['Test agency other', 'Test agency new']}

    def test_owner_that_already_hosts_the_station(self, cnn, station, tmp_path):
        path = self.outside_package(tmp_path, agencies=[{'name': 'Test agency host', 'type': 'owner'}])
        report = self.run(cnn, path, station, existing=True)
        assert station_agencies(cnn) == ('Test agency founder', ['Test agency host'])
        assert 'Test agency host, the owner in the package, is a host of it.' in ' '.join(report['warnings'])

    def test_existing_station_without_owner_gets_it(self, cnn, station, tmp_path):
        cnn.query("DELETE FROM agency_stations s USING agency_roles r WHERE r.api_id = s.agency_role_id "
                  "AND r.name = 'owner' AND s.station_id = %s", (api_id(cnn),))
        path = self.outside_package(tmp_path, agencies=[{'name': 'Test agency host', 'type': 'owner'}])

        report = self.run(cnn, path, station, existing=True)

        assert station_agencies(cnn) == ('Test agency host', [])
        assert report['owner'] == 'Test agency host' and 'agencies' not in report['created']

    def test_agency_that_fails_does_not_stop_the_import(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        long_name = 'Test agency ' + 'x' * sp.AGENCY_NAME_LENGTH
        long_host = 'Test agency host ' + 'x' * sp.AGENCY_NAME_LENGTH
        path = self.outside_package(tmp_path, agencies=[self.agency(long_name, 'owner'),
                                                       self.agency(long_host, 'host'),
                                                       self.agency('Test agency NGS', 'host')])

        report = self.run(cnn, path, self.destination(cnn, tmp_path))

        assert api_id(cnn) is not None
        assert station_agencies(cnn) == (None, ['Test agency NGS'])
        assert [(entry['table'], entry['record']) for entry in report['not_imported']] == \
            [('agencies', 'owner ' + long_name), ('agencies', 'host ' + long_host)]
        assert all('longer than 200 characters' in entry['error'] for entry in report['not_imported'])

    def test_database_without_the_tables_of_the_agencies(self, cnn, station, tmp_path, monkeypatch):
        monkeypatch.setattr(sp.StationExport, 'has_agencies', lambda self: False)
        result = sp.StationExport(cnn, NET, STN, archive=station.archive, rinex=False).write(station.out)
        _, data = sp.read_package(result['zip'])
        assert 'agency' not in data and 'agencies' not in data

        remove_fixture(cnn)
        monkeypatch.setattr(sp.StationImport, '_has_agencies', lambda self: False)
        path = self.outside_package(tmp_path, agencies=[self.agency('Test agency NGS', 'owner')])
        report = self.run(cnn, path, self.destination(cnn, tmp_path))
        assert api_id(cnn) is not None
        assert any('no tables for the agencies' in warning for warning in report['warnings'])
        assert count(cnn, "agencies WHERE name ILIKE 'test agency%%'") == 0

    def test_merge_does_not_carry_the_agencies(self, cnn, station, tmp_path, monkeypatch):
        """A merge that deletes the origin deletes its agencies with it (ON DELETE CASCADE): they are not moved."""
        from geode.station_merge import StationMerge
        # gamit_projects and reference_frames come from the migrations, not from the dump of the test database
        monkeypatch.setattr(StationMerge, '_array_targets', lambda self: [])
        try:
            for code, owner, hosts in (('pk02', None, ('Test agency host',)),
                                       ('pk03', 'Test agency founder', ('Test agency other',))):
                remove_station(cnn, 'tst', code)
                cnn.query('INSERT INTO stations ("NetworkCode", "StationCode") VALUES (%s, %s)', ('tst', code))
                add_agencies(cnn, 'tst', code, owner=owner, hosts=hosts)
            origin_id = cnn.query('SELECT api_id FROM stations WHERE "NetworkCode" = \'tst\' AND "StationCode" = '
                                  '\'pk03\'').dictresult()[0]['api_id']
            report = StationMerge(cnn, ('tst', 'pk03'), ('tst', 'pk02'), delete_origin=True,
                                  archive=make_archive(cnn, str(tmp_path / 'archive'))).execute()
            assert report['origin_deleted']
            assert station_agencies(cnn, 'tst', 'pk02') == (None, ['Test agency host'])
            assert count(cnn, 'agency_stations WHERE station_id = %s', (origin_id,)) == 0
        finally:
            for code in ('pk02', 'pk03'):
                remove_station(cnn, 'tst', code)


OUTSIDE_FQDN = 'outside.example.gov'


def odot(role):
    return {'name': 'Test agency ODOT', 'type': role, 'rtn_names': ['Ohio RTN'],
            'contacts': [{'grade': 1, 'type': 'email', 'name': 'John Smith', 'value': 'john@dot.example.gov'},
                         {'grade': 1, 'type': 'phone', 'name': 'John Smith', 'value': '+1-614-555-0123'},
                         {'grade': 2, 'type': 'email', 'name': 'Jane Doe', 'value': 'jane@dot.example.gov'}]}


def outside_station(**changes):
    """station.json of a package made by another program: no NetworkCode, no OTL, no RINEX."""
    data = {
        'format': sp.FORMAT, 'format_version': 1, 'StationCode': STN,
        'station': {'StationName': 'Toledo Airport', 'lat': -34.6, 'lon': -58.4, 'height': 25.0,
                    'dome': '49534M001'},
        'agencies': [odot('owner')],
        'tables': {
            'stationinfo': [dict(FORMAT0_STATIONINFO, DateStart='2015-06-15 00:00:00', DateEnd=None,
                                 Comments='Antenna replaced after the storm')],
            'sources_stations': [{'path': None, 'format': None, 'server': {
                'protocol': 'ftp', 'fqdn': OUTSIDE_FQDN, 'username': None, 'password': None,
                'path': '/gnss/rinex/${station}/${year}/${doy}/', 'format': 'DEFAULT_FORMAT',
                'metadata_source': {'protocol': 'https', 'fqdn': OUTSIDE_FQDN, 'username': None, 'password': None,
                                    'path': '/gnss/metadata/${station}.log', 'format': 'DEFAULT_FORMAT'}}}]},
        'studio': {'station_meta': {'comments': 'Located on airport terminal roof', 'monument_type': 'Pillar'}},
        'custom_fields': {'ngs_pid': 'JV3192', 'reference_point_description': 'Top of the pillar marker',
                          'installation_date': '2015-06-15', 'rtn_names': 'Ohio RTN'},
    }
    for key, value in changes.items():
        data[key] = value
    return data


def remove_outside(cnn):
    cnn.query('DELETE FROM sources_stations WHERE server_id IN (SELECT server_id FROM sources_servers '
              'WHERE fqdn = %s)', (OUTSIDE_FQDN,))
    cnn.query('DELETE FROM sources_servers WHERE fqdn = %s', (OUTSIDE_FQDN,))
    cnn.query('DELETE FROM sources_metadata WHERE fqdn = %s', (OUTSIDE_FQDN,))


def agency_row(cnn, name):
    rows = cnn.query('SELECT * FROM agencies WHERE lower(name) = lower(%s)', (name,)).dictresult()
    return rows[0] if rows else None


ODOT_CONTACTS = {'primary_contact_name': 'John Smith', 'primary_contact_email': 'john@dot.example.gov',
                 'primary_contact_phone': '+1-614-555-0123', 'secondary_contact_name': 'Jane Doe',
                 'secondary_contact_email': 'jane@dot.example.gov'}


def contact_columns(row):
    return {column: row[column] for column in ODOT_CONTACTS}


@database
class TestPackageMadeOutsideGeode:
    run = staticmethod(TestImport.run)
    destination = staticmethod(TestImport.destination)

    @pytest.fixture(autouse=True)
    def clean(self, cnn):
        remove_outside(cnn)
        yield
        remove_outside(cnn)
        remove_station(cnn, NET, STN)
        remove_agencies(cnn)

    def test_package_without_network(self, cnn, station, tmp_path):
        """A zip named after the station only, without NetworkCode: the station goes to {default net}."""
        remove_fixture(cnn)
        path = write_package(tmp_path / 'pk01.zip', outside_station())

        report = self.run(cnn, path, self.destination(cnn, tmp_path), network='tst')

        assert report['source'] == STN and report['destination'] == 'tst.pk01'
        assert 'The package has no NetworkCode: the station was created in network tst.' in report['warnings']
        assert report['not_imported'] == []
        assert report['rows'] == {'stations': 1, 'stationinfo': 1, 'sources_stations': 1, 'agency_stations': 1,
                                  'api_stationmeta': 1}
        assert station_agencies(cnn) == ('Test agency ODOT', [])
        row = cnn.query('SELECT "StationName", dome, country_code FROM stations WHERE "NetworkCode" = %s '
                        'AND "StationCode" = %s', (NET, STN)).dictresult()[0]
        assert (row['StationName'], row['dome'], row['country_code']) == ('Toledo Airport', '49534M001', 'ARG')

        agency = agency_row(cnn, 'Test agency ODOT')
        assert agency['rtn_names'] == ['Ohio RTN'] and contact_columns(agency) == ODOT_CONTACTS

        source = cnn.query('SELECT sv.path AS data, md.path AS metadata, st.try_order FROM sources_stations st '
                           'JOIN sources_servers sv USING (server_id) JOIN sources_metadata md '
                           'ON md.id = sv.metadata_source_id WHERE st."NetworkCode" = %s AND st."StationCode" = %s',
                           (NET, STN)).dictresult()
        assert source == [{'data': '/gnss/rinex/${station}/${year}/${doy}/',
                           'metadata': '/gnss/metadata/${station}.log', 'try_order': 1}]
        meta = cnn.query('SELECT m.comments, t.name FROM api_stationmeta m JOIN api_monumenttype t '
                         'ON t.id = m.monument_type_id WHERE m.station_id = %s', (api_id(cnn),)).dictresult()
        assert meta == [{'comments': 'Located on airport terminal roof', 'name': 'Pillar'}]
        assert cnn.query('SELECT "Comments" FROM stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s',
                         (NET, STN)).dictresult() == [{'Comments': 'Antenna replaced after the storm'}]
        assert any('4 custom field(s)' in warning for warning in report['warnings'])

    def test_round_trip_with_the_details_of_the_agency(self, cnn, station, tmp_path):
        path = write_package(tmp_path / 'pk01.zip', outside_station())
        self.run(cnn, path, self.destination(cnn, tmp_path), network='tsx')
        try:
            exported = sp.StationExport(cnn, 'tsx', STN, rinex=False).write(str(tmp_path))
            _, data = sp.read_package(exported['zip'])
            assert data['agencies'] == [dict(odot('owner'), description=None)]
            remove_station(cnn, 'tsx', STN)
            remove_agencies(cnn)

            self.run(cnn, exported['zip'], self.destination(cnn, tmp_path / 'again'), network='tsx')
            agency = agency_row(cnn, 'Test agency ODOT')
            assert agency['rtn_names'] == ['Ohio RTN'] and contact_columns(agency) == ODOT_CONTACTS
        finally:
            remove_station(cnn, 'tsx', STN)
            cnn.query("DELETE FROM networks WHERE \"NetworkCode\" = 'tsx'")

    def test_unknown_monument_type_goes_to_the_report(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        data = outside_station(studio={'station_meta': {'comments': 'roof', 'monument_type': 'Deep drilled'}})
        report = self.run(cnn, write_package(tmp_path / 'pk01.zip', data), self.destination(cnn, tmp_path))

        [entry] = report['not_imported']
        assert (entry['table'], entry['record']) == ('monument_types', '"Deep drilled"')
        assert entry['error'] == 'Monument type "Deep drilled" is not in api_monumenttype of this database: the ' \
                                 'station was imported without it.'
        meta = cnn.query('SELECT comments, monument_type_id FROM api_stationmeta WHERE station_id = %s',
                         (api_id(cnn),)).dictresult()
        assert meta == [{'comments': 'roof', 'monument_type_id': None}]

    def test_metadata_without_comments(self, cnn, station, tmp_path):
        """Comments null in the package, and the column of api_stationmeta is NOT NULL."""
        remove_fixture(cnn)
        data = outside_station(studio={'station_meta': {'comments': None, 'monument_type': 'Pillar'}})
        report = self.run(cnn, write_package(tmp_path / 'pk01.zip', data), self.destination(cnn, tmp_path))

        assert report['not_imported'] == []
        meta = cnn.query('SELECT m.comments, t.name FROM api_stationmeta m JOIN api_monumenttype t '
                         'ON t.id = m.monument_type_id WHERE m.station_id = %s', (api_id(cnn),)).dictresult()
        assert meta == [{'comments': '', 'name': 'Pillar'}]

    def test_unknown_format_of_a_metadata_source(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        data = outside_station()
        data['tables']['sources_stations'][0]['server']['metadata_source']['format'] = 'IGS site log'
        report = self.run(cnn, write_package(tmp_path / 'pk01.zip', data), self.destination(cnn, tmp_path))
        assert report['not_imported'] == []
        assert any('Format IGS site log of the metadata source of ftp://outside.example.gov' in warning and
                   'DEFAULT_FORMAT used instead' in warning for warning in report['warnings'])
        assert count(cnn, "sources_metadata WHERE fqdn = %s AND format = 'DEFAULT_FORMAT'", (OUTSIDE_FQDN,)) == 1

    def test_existing_station_keeps_its_data(self, cnn, station, tmp_path):
        """tst.pk01 (owned by "Test agency founder") only gets the data sources and agencies it does not have."""
        cnn.query('UPDATE stations SET dome = NULL WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN))
        cnn.query("UPDATE api_stationmeta SET comments = '', monument_type_id = NULL WHERE station_id = %s",
                  (api_id(cnn),))
        sessions = count(cnn, 'stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN))
        # 12 m north of the fixture station, within its max_dist
        data = outside_station(agencies=[dict(odot('owner'), name='test agency FOUNDER'), odot('host')])
        data['station'] = {'StationName': 'Toledo Airport', 'lat': -34.123456789 + 12 / (6371000 * numpy.pi / 180),
                           'lon': -58.987654321, 'dome': '49534M001'}
        data['tables']['sources_stations'].append({'try_order': 1, 'path': '/data/pk01', 'format': None, 'server': {
            'protocol': 'ftp', 'fqdn': 'data.example.org', 'username': 'user', 'password': None, 'path': '/data'}})

        report = self.run(cnn, write_package(tmp_path / 'pk01.zip', data), station, existing=True)

        assert report['not_imported'] == [] and report['filled'] == {}
        assert report['rows'] == {'sources_stations': 1, 'agency_stations': 1, 'agencies (details)': 1}
        row = cnn.query('SELECT "StationName", dome, lat FROM stations WHERE "NetworkCode" = %s AND '
                        '"StationCode" = %s', (NET, STN)).dictresult()[0]
        assert (row['StationName'], row['dome'], float(row['lat'])) == ('Package test', None, -34.123456789)
        assert count(cnn, 'stationinfo WHERE "NetworkCode" = %s AND "StationCode" = %s', (NET, STN)) == sessions
        meta = cnn.query('SELECT comments, monument_type_id FROM api_stationmeta WHERE station_id = %s',
                         (api_id(cnn),)).dictresult()
        assert meta == [{'comments': '', 'monument_type_id': None}]
        assert 'Station tst.pk01 already existed: it kept its data and only got the RINEX files, the station info of ' \
               'their dates, the data sources and the agencies it did not have.' in report['warnings']

        sources = cnn.query('SELECT sv.fqdn, st.try_order FROM sources_stations st JOIN sources_servers sv '
                            'USING (server_id) WHERE st."NetworkCode" = %s AND st."StationCode" = %s '
                            'ORDER BY st.try_order', (NET, STN)).dictresult()
        assert sources == [{'fqdn': 'data.example.org', 'try_order': 1}, {'fqdn': OUTSIDE_FQDN, 'try_order': 2}]
        assert station_agencies(cnn) == ('Test agency founder', ['Test agency ODOT', 'Test agency host'])
        founder = agency_row(cnn, 'Test agency founder')
        assert founder['rtn_names'] == ['Ohio RTN'] and founder['primary_contact_email'] == 'ana@agency.example.gov'
        assert founder['secondary_contact_email'] == 'jane@dot.example.gov'
        assert 'Agency Test agency founder already has another primary_contact_email, primary_contact_name, ' \
               'primary_contact_phone: the existing value was kept.' in report['warnings']

    def test_an_owner_the_database_has_is_matched_by_name(self, cnn, station, tmp_path):
        """Matched by its name alone, the agency keeps what it has; the agency that sends the package is a host."""
        remove_fixture(cnn)
        insert_agency(cnn, 'Test agency neighbor', rtn_names=['Lake RTN'])
        data = outside_station(agencies=[odot('host'), {'name': 'Test agency NEIGHBOR', 'type': 'owner'}])

        report = self.run(cnn, write_package(tmp_path / 'pk01.zip', data), self.destination(cnn, tmp_path))

        assert report['not_imported'] == []
        assert station_agencies(cnn) == ('Test agency neighbor', ['Test agency ODOT'])
        assert report['rows']['agency_stations'] == 2
        assert report['created']['agencies'] == ['Test agency ODOT']
        assert contact_columns(agency_row(cnn, 'Test agency ODOT')) == ODOT_CONTACTS
        neighbor = agency_row(cnn, 'Test agency neighbor')
        assert (neighbor['name'], neighbor['rtn_names'], neighbor['primary_contact_email']) == \
            ('Test agency neighbor', ['Lake RTN'], 'ana@agency.example.gov')

    def test_an_existing_agency_is_only_filled_in(self, cnn, station, tmp_path):
        remove_fixture(cnn)
        insert_agency(cnn, 'Test agency ODOT')
        data = outside_station()
        data['agencies'][0]['contacts'].append({'grade': 2, 'type': 'fax', 'value': '123'})
        data['agencies'][0]['submission_date'] = '2026-12-01'

        report = self.run(cnn, write_package(tmp_path / 'pk01.zip', data), self.destination(cnn, tmp_path))

        agency = agency_row(cnn, 'test agency odot')
        assert agency['rtn_names'] == ['Ohio RTN']
        assert contact_columns(agency) == dict(CONTACT, secondary_contact_name='Jane Doe',
                                               secondary_contact_email='jane@dot.example.gov')
        warnings = '\n'.join(report['warnings'])
        assert 'Agency Test agency ODOT already has another primary_contact_email, primary_contact_name, ' \
               'primary_contact_phone: the existing value was kept.' in warnings
        assert 'Agency Test agency ODOT has keys GeoDE does not keep (submission_date)' in warnings
        [entry] = report['not_imported']
        assert (entry['table'], entry['record']) == ('agencies', 'Test agency ODOT: contact')
        assert 'email or phone' in entry['error']

    def test_metadata_source_hangs_from_the_server(self, cnn, station, tmp_path):
        """A server without a metadata source gets the one of the package; a server with another one keeps it."""
        remove_fixture(cnn)
        cnn.query("INSERT INTO sources_servers (protocol, fqdn, username, password, path) "
                  "VALUES ('ftp', %s, 'osu', 'secret', '/gnss/rinex/${station}/${year}/${doy}/')", (OUTSIDE_FQDN,))

        report = self.run(cnn, write_package(tmp_path / 'pk01.zip', outside_station()),
                          self.destination(cnn, tmp_path))

        # the server of the package has no credentials: it is the one OSU filled them in
        servers = cnn.query('SELECT sv.username, md.path FROM sources_servers sv JOIN sources_metadata md '
                            'ON md.id = sv.metadata_source_id WHERE sv.fqdn = %s', (OUTSIDE_FQDN,)).dictresult()
        assert servers == [{'username': 'osu', 'path': '/gnss/metadata/${station}.log'}]
        assert report['filled']['sources_servers'] == \
            ['metadata source of ftp://outside.example.gov/gnss/rinex/${station}/${year}/${doy}/']
        assert 'sources_servers' not in report['created']

        data = outside_station(StationCode='pk02', agencies=None)
        data['tables']['sources_stations'][0]['server']['metadata_source']['path'] = '/other/${station}.log'
        try:
            report = self.run(cnn, write_package(tmp_path / 'pk02.zip', data),
                              self.destination(cnn, tmp_path / 'second'), station='pk02')
            assert any('Server ftp://outside.example.gov' in warning and 'another metadata source' in warning
                       for warning in report['warnings'])
            assert count(cnn, 'sources_metadata WHERE fqdn = %s', (OUTSIDE_FQDN,)) == 1
        finally:
            remove_station(cnn, NET, 'pk02')

    def test_scan_archive_imports_a_package_without_network(self, cnn, station, tmp_path, scan_archive, answer_yes):
        remove_fixture(cnn)
        path = write_package(tmp_path / 'pk01.zip', outside_station())
        destination = self.destination(cnn, tmp_path)
        assert scan_archive.import_station(cnn, [NET, path], archive=destination.archive,
                                           media_root=destination.media_root) == []
        assert api_id(cnn) is not None
