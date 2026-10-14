"""
Unit tests for the pure parts of station_merge and for the paths of RinexStruct.rename_rinex_paths.

The database operations are not covered here: they need a GeoDE database.
"""

import datetime
import os
from types import SimpleNamespace

import pytest

from geode import station_merge as sm
from geode import pyArchiveStruct
from geode.pyDate import Date


class TestReplaceInArray:

    def test_replaces_origin_with_destination(self):
        assert sm.replace_in_array(['arg.a', 'usa.o', 'arg.b'], 'usa.o', 'arg.d') == ['arg.a', 'arg.d', 'arg.b']

    def test_does_not_repeat_destination(self):
        assert sm.replace_in_array(['usa.o', 'arg.d'], 'usa.o', 'arg.d') == ['arg.d']
        assert sm.replace_in_array(['arg.d', 'usa.o'], 'usa.o', 'arg.d') == ['arg.d']

    def test_empty_or_none(self):
        assert sm.replace_in_array(None, 'a.b', 'c.d') == []
        assert sm.replace_in_array([], 'a.b', 'c.d') == []

    def test_untouched_without_origin(self):
        assert sm.replace_in_array(['arg.a'], 'usa.o', 'arg.d') == ['arg.a']


class TestReplaceInSubnet:

    def test_replaces_station_and_alias_at_the_same_index(self):
        assert sm.replace_in_subnet(['arg.a', 'usa.o'], ['a', 'o'], 'usa.o', 'arg.d', 'dest') == \
            (['arg.a', 'arg.d'], ['a', 'dest'])

    def test_drops_origin_if_destination_is_already_there(self):
        assert sm.replace_in_subnet(['usa.o', 'arg.d'], ['o', 'd'], 'usa.o', 'arg.d', 'd') == (['arg.d'], ['d'])

    def test_aliases_shorter_than_stations(self):
        assert sm.replace_in_subnet(['usa.o'], [], 'usa.o', 'arg.d', 'd') == (['arg.d'], ['d'])


class TestRetargetMediaName:

    def test_changes_station_folder(self):
        assert sm.retarget_media_name('stations/usa/o/visits/2020-05-05/images/a.png', ('usa', 'o'),
                                      ('arg', 'd')) == 'stations/arg/d/visits/2020-05-05/images/a.png'

    def test_other_station_or_empty_is_not_retargeted(self):
        assert sm.retarget_media_name('stations/usa/other/images/a.png', ('usa', 'o'), ('arg', 'd')) is None
        assert sm.retarget_media_name('stations/usa/oo/images/a.png', ('usa', 'o'), ('arg', 'd')) is None
        assert sm.retarget_media_name('', ('usa', 'o'), ('arg', 'd')) is None


class TestDates:

    def test_day_bound(self):
        assert sm.day_bound(None, 0) == 0
        assert sm.day_bound(None, sm.NO_END_DATE) == sm.NO_END_DATE
        assert sm.day_bound(datetime.date(2020, 3, 1), 0) == 2020 * 1000 + 61

    def test_as_date_accepts_pydate_and_datetime(self):
        assert sm.as_date(None) is None
        assert sm.as_date(datetime.date(2001, 7, 16)) == datetime.date(2001, 7, 16)
        assert sm.as_date(datetime.datetime(2001, 7, 16, 12)) == datetime.date(2001, 7, 16)
        assert sm.as_date(Date(year=2001, doy=197)) == datetime.date(2001, 7, 16)


class TestConstructor:

    def make(self, **kwargs):
        archive = SimpleNamespace(Config=SimpleNamespace(archive_path='/archive'))
        return sm.StationMerge(None, ('arg', 'abcd'), ('arg', 'efgh'), archive=archive, **kwargs)

    def test_ids_and_bounds(self):
        merge = self.make(start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2020, 1, 31))
        assert (merge.origin_id, merge.destination_id) == ('arg.abcd', 'arg.efgh')
        assert (merge.low, merge.high) == (2020001, 2020031)

    def test_same_station_is_rejected(self):
        merge = sm.StationMerge(None, ('arg', 'abcd'), ('arg', 'abcd'), archive=object())
        with pytest.raises(sm.StationMergeException):
            merge.preview()

    def test_inverted_dates_are_rejected(self):
        merge = self.make(start_date=datetime.date(2020, 2, 1), end_date=datetime.date(2020, 1, 1))
        with pytest.raises(sm.StationMergeException):
            merge.preview()


class TestRenameRinexPaths:

    LEVELS = [
        {'rinex_col_in': 'NetworkCode',     'TotalChars': 3, 'isnumeric': '0'},
        {'rinex_col_in': 'ObservationYear', 'TotalChars': 4, 'isnumeric': '1'},
        {'rinex_col_in': 'ObservationDOY',  'TotalChars': 3, 'isnumeric': '1'},
    ]

    def archive(self):
        archive = pyArchiveStruct.RinexStruct.__new__(pyArchiveStruct.RinexStruct)
        archive.levels = self.LEVELS
        archive.Config = SimpleNamespace(archive_path='/archive')
        return archive

    def record(self, network='arg'):
        return {'NetworkCode': network, 'StationCode': 'abcd', 'ObservationYear': 2020.0,
                'ObservationDOY': 7.0, 'Filename': 'abcd0070.20o'}

    def test_folder_follows_the_levels(self):
        assert self.archive().archive_folder(self.record()) == 'arg/2020/007'

    def test_paths_change_station_code(self):
        src, dst, filename = self.archive().rename_rinex_paths(self.record(), 'arg', 'wxyz')
        assert src == os.path.join('/archive', 'arg/2020/007', 'abcd0070.20d.Z')
        assert dst == os.path.join('/archive', 'arg/2020/007', 'wxyz0070.20d.Z')
        assert filename == 'wxyz0070.20o'

    def test_paths_change_network_folder(self):
        _, dst, _ = self.archive().rename_rinex_paths(self.record(), 'usa', 'wxyz')
        assert dst == os.path.join('/archive', 'usa/2020/007', 'wxyz0070.20d.Z')

    def test_invalid_filename_raises(self):
        record = dict(self.record(), Filename='not-a-rinex')
        with pytest.raises(Exception):
            self.archive().rename_rinex_paths(record, 'arg', 'wxyz')


class TestBuildRinexPath:
    """build_rinex_path of an existing rinex keeps the folder it had before archive_folder was extracted."""

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def ntuples(self):
            return len(self.rows)

        def dictresult(self):
            return self.rows

    class FakeCnn:
        def __init__(self, rows):
            self.rows = rows
            self.commands = []

        def query(self, command):
            self.commands.append(command)
            return TestBuildRinexPath.FakeResult(self.rows)

    def archive(self, rows):
        archive = pyArchiveStruct.RinexStruct.__new__(pyArchiveStruct.RinexStruct)
        archive.levels = TestRenameRinexPaths.LEVELS
        archive.Config = SimpleNamespace(archive_path='/archive')
        archive.cnn = self.FakeCnn(rows)
        return archive

    ROW = {'NetworkCode': 'arg', 'ObservationYear': 2020.0, 'ObservationDOY': 7.0, 'Filename': 'abcd0070.20o'}

    def test_path_with_filename(self):
        archive = self.archive([self.ROW])
        assert archive.build_rinex_path('arg', 'abcd', 2020, 7) == 'arg/2020/007/abcd0070.20d.Z'
        assert 'FROM rinex_proc' in archive.cnn.commands[0]

    def test_path_without_filename(self):
        assert self.archive([self.ROW]).build_rinex_path('arg', 'abcd', 2020, 7, with_filename=False) == 'arg/2020/007'

    def test_specific_file_queries_rinex_table(self):
        archive = self.archive([self.ROW])
        assert archive.build_rinex_path('arg', 'abcd', 2020, 7, filename='abcd0070.20o') == 'arg/2020/007/abcd0070.20d.Z'
        assert 'FROM rinex WHERE' in archive.cnn.commands[0]

    def test_missing_rinex_returns_none(self):
        assert self.archive([]).build_rinex_path('arg', 'abcd', 2020, 7) is None

    def test_same_folder_as_the_original_inline_formula(self):
        archive = self.archive([self.ROW])
        original = "/".join('{key:0{width}{type}}'.format(key=self.ROW[level['rinex_col_in']],
                                                          width=level['TotalChars'],
                                                          type='.0f' if level['isnumeric'] == '1' else 's')
                            for level in archive.levels)
        assert archive.build_rinex_path('arg', 'abcd', 2020, 7, with_filename=False) == original


class TestRinexStructOperations:
    """check_rinex_files, delete_rinex and rename_station with a fake connection and archive."""

    class Date:
        fyear = 2020.0

        def yyyymmdd(self):
            return '2020-01-01'

        def yyyyddd(self):
            return '2020 001'

    def archive(self, rows, tmp_path):
        archive = pyArchiveStruct.RinexStruct.__new__(pyArchiveStruct.RinexStruct)
        archive.levels = TestRenameRinexPaths.LEVELS
        archive.Config = SimpleNamespace(archive_path=str(tmp_path))
        archive.cnn = TestBuildRinexPath.FakeCnn(rows)
        archive.cnn.insert_event = lambda event: archive.cnn.commands.append(('event', event))
        return archive

    ROW = dict(TestBuildRinexPath.ROW, StationCode='abcd')

    def test_check_reports_missing_file_without_removing(self, tmp_path):
        archive = self.archive([self.ROW], tmp_path)
        archive.remove_rinex = lambda rnx: pytest.fail('must not remove')

        missing = archive.check_rinex_files('arg', 'abcd', self.Date(), self.Date())

        assert missing == [str(tmp_path / 'arg/2020/007/abcd0070.20d.Z')]

    def test_check_ignores_existing_file(self, tmp_path):
        folder = tmp_path / 'arg/2020/007'
        folder.mkdir(parents=True)
        (folder / 'abcd0070.20d.Z').write_bytes(b'')
        archive = self.archive([self.ROW], tmp_path)

        assert archive.check_rinex_files('arg', 'abcd', self.Date(), self.Date(), fix=True) == []

    def test_check_fix_removes_record_and_inserts_event(self, tmp_path):
        archive = self.archive([self.ROW], tmp_path)
        removed = []
        archive.remove_rinex = removed.append

        archive.check_rinex_files('arg', 'abcd', self.Date(), self.Date(), fix=True)

        assert removed == [self.ROW]
        assert archive.cnn.commands[-1][0] == 'event'

    def test_delete_rinex_removes_every_record_and_keeps_going_after_a_failure(self, tmp_path):
        rows = [dict(self.ROW, ObservationDOY=d) for d in (1, 2)]
        archive = self.archive(rows, tmp_path)
        calls = []

        def remove(rnx):
            calls.append(rnx['ObservationDOY'])
            if rnx['ObservationDOY'] == 1:
                raise pyArchiveStruct.dbConnection.dbErrDelete('boom')

        archive.remove_rinex = remove

        archive.delete_rinex([{'NetworkCode': 'arg', 'StationCode': 'abcd'}], self.Date(), self.Date(), 0.5)

        assert calls == [1, 2]
        assert '"Completion" <= 0.500000' in archive.cnn.commands[0]

    def test_rename_station_uses_station_merge_with_itself_as_archive(self, tmp_path, monkeypatch):
        archive = self.archive([], tmp_path)
        seen = {}

        class FakeMerge:
            def __init__(self, cnn, origin, destination, start, end, delete_origin, archive=None):
                seen.update(cnn=cnn, origin=origin, destination=destination, delete=delete_origin, archive=archive)

            def preview(self):
                return {'rinex': {'to_move': 1, 'to_delete': 0}, 'origin': 'arg.abcd', 'destination': 'arg.wxyz'}

            def execute(self):
                seen['executed'] = True
                return {'origin': 'arg.abcd', 'destination': 'arg.wxyz', 'executed': True, 'origin_deleted': True}

        monkeypatch.setattr(sm, 'StationMerge', FakeMerge)

        archive.rename_station('arg', 'abcd', 'arg', 'wxyz', self.Date(), self.Date(), True)

        assert seen['archive'] is archive and seen['cnn'] is archive.cnn
        assert seen['origin'] == ('arg', 'abcd') and seen['destination'] == ('arg', 'wxyz') and seen['delete']

    def test_rename_station_reports_merge_errors_instead_of_raising(self, tmp_path, monkeypatch, capsys):
        archive = self.archive([], tmp_path)

        class FailingMerge:
            def __init__(self, *args, **kwargs):
                raise sm.StationMergeException('no way')

        monkeypatch.setattr(sm, 'StationMerge', FailingMerge)

        archive.rename_station('arg', 'abcd', 'arg', 'wxyz', None, None)

        assert 'no way' in capsys.readouterr().out


    def test_rename_station_dry_run_only_previews(self, tmp_path, monkeypatch, capsys):
        archive = self.archive([], tmp_path)

        class PreviewOnlyMerge:
            def __init__(self, *args, **kwargs):
                pass

            def preview(self):
                return {'origin': 'arg.abcd', 'destination': 'arg.wxyz', 'rinex': {'to_move': 3}}

            def execute(self):
                pytest.fail('a dry run must not execute the merge')

        monkeypatch.setattr(sm, 'StationMerge', PreviewOnlyMerge)

        archive.rename_station('arg', 'abcd', 'arg', 'wxyz', None, None, dry_run=True)

        assert '3 to move' in capsys.readouterr().out


class TestFormatReport:

    def report(self, **extra):
        report = {'origin': 'arg.abcd', 'destination': 'arg.wxyz', 'start_date': None, 'end_date': None,
                  'rinex': {'in_range': 5, 'to_move': 3, 'to_delete': 1, 'remaining_at_origin': 1,
                            'skipped': {'missing_in_archive': 0, 'invalid_filename': 2, 'destination_file_exists': 0}},
                  'tables': [{'table': 'stationinfo', 'to_move': 4, 'skipped': 0},
                             {'table': 'etms', 'to_move': 0, 'skipped': 0}],
                  'station_info': {'to_copy': 1, 'skipped_overlap': 0},
                  'arrays': [{'table': 'gamit_subnets', 'column': 'stations', 'rows': 2}],
                  'warnings': [], 'blockers': []}
        report.update(extra)
        return '\n'.join(sm.format_report(report))

    def test_counts_and_skip_reasons(self):
        text = self.report()
        assert 'arg.abcd into arg.wxyz' in text
        assert '5 in range, 3 to move, 1 already in the destination' in text
        assert 'skipped 2: the file name is not a valid RINEX name' in text
        assert 'stationinfo: 4 to move' in text
        assert 'etms' not in text

    def test_blockers_and_warnings_are_shown(self):
        text = self.report(warnings=['careful'], blockers=['archive is read-only'])
        assert 'Warning: careful' in text and 'BLOCKED: archive is read-only' in text

    def test_deleted_origin_and_result(self):
        text = self.report(origin_will_be_deleted=True, deleted_with_origin=[{'table': 'events', 'rows': 7}],
                           executed=True, origin_deleted=True)
        assert 'origin station will be deleted' in text and 'events: 7 row(s) deleted' in text
        assert text.endswith('The origin station was deleted.')
