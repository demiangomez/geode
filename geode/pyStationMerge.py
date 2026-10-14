"""
Project: Geodetic Database Engine (GeoDE)
Date: 10/06/2026

Rename / merge of a station into another one (origin -> destiny). It is the database-wide version of the original
IntegrityCheck --rename: the data of the origin station is moved to the destiny station, which must exist, and the
origin is deleted when it is left without RINEX files and it was asked to.

What is moved (only inside the date range, if given):
  * rinex: database rows (codes and filename) and the files of the archive.
  * the tables of GeoDE indexed by day (TIME_TABLES).
  * the stationinfo records the destiny does not have for the moved days.
  * the station codes inside the arrays of gamit_subnets.
What is only done when the origin station is deleted (they do not depend on a date range):
  * the station codes inside the arrays of ARRAY_TARGETS.
  * the data of the REST API (people roles, images, attached files, visits) and its media files.
  * the rest of the rows of the origin (OWNED_TABLES) are deleted with it.
The events of the origin are deleted with it, so a station created later with the same code does not inherit them:
the station information records of the origin and the summary of the merge are kept as events of the destiny.
When the origin is kept, nothing is deleted from the events table and one summary event is added to each station.

A RINEX file of the origin that the destiny already has (same day, interval and completion) is deleted from the origin,
as IntegrityCheck always did. The rows of the other tables that exist in both stations (same key) are not overwritten:
they stay in the origin and are reported as skipped.

All the changes of the database are made in one transaction. The files moved are restored if it fails and the
duplicated RINEX files are only removed from the archive after the commit.
"""
import datetime
import os
import shutil
import sys

# app
from . import pyArchiveStruct
from . import pyEvents
from .metadata import station_info

EXAMPLES_LIMIT = 20
NO_END_DATE    = 9999999

# (table, columns of the primary key besides NetworkCode and StationCode). All of them have
# "Year" and "DOY", used for the date range.
TIME_TABLES = (
    ('apr_coords',      ('Year', 'DOY')),
    ('aws_sync',        ('Year', 'DOY')),
    ('ppp_soln',        ('Year', 'DOY', 'ReferenceFrame')),
    ('ppp_soln_excl',   ('Year', 'DOY')),
    ('gamit_soln',      ('Project', 'Year', 'DOY')),
    ('gamit_soln_excl', ('Project', 'Year', 'DOY')),
    ('gamit_ztd',       ('Date', 'Project', 'Year', 'DOY')),
    ('stacks',          ('Year', 'DOY', 'name')),
)

# rows of the origin that are not moved: they are deleted with the station
OWNED_TABLES = tuple(table for table, _ in TIME_TABLES) + (
    'data_source', 'etms', 'etm_params', 'sources_stations', 'stationalias', 'locks')

# (table, array column, primary key) with "net.stn" codes as items
ARRAY_TARGETS = (
    ('gamit_projects',   'station_list',        'api_id'),
    ('reference_frames', 'euler_pole_stations', 'api_id'),
    ('campaign_plans',   'stations',            'id'),
)

# (table, media column) of the REST API: the files of the station and the files of each visit
STATION_MEDIA = (
    ('api_stationimages',        'image'),
    ('api_stationattachedfiles', 'file'),
)
VISIT_MEDIA = (
    ('api_visitimages',        'image'),
    ('api_visitattachedfiles', 'file'),
    ('api_visitgnssdatafiles', 'file'),
)

# tables of the REST API (Django) used here: a database without the API does not have them
API_TABLES = ('api_visits', 'api_rolepersonstation', 'api_stationimages', 'api_stationattachedfiles',
              'api_visitimages', 'api_visitattachedfiles', 'api_visitgnssdatafiles')

RINEX_SKIP_REASONS = {
    'destination_file_exists': 'a file with the new name already exists in the destination folder of the archive',
    'missing_in_archive':      'the file is not in the archive',
    'invalid_filename':        'the file name is not a valid RINEX name',
}


class StationMergeException(Exception):
    """The operation cannot be done (bad input or station state): the message is meant for the user."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return str(self.value)


def format_report(report):
    """
    Lines for the console describing the report of StationMerge.preview() or StationMerge.execute().
    :return: list of strings
    """
    rinex = report.get('rinex', {})
    lines = [' >> Merge of %s into %s (%s -> %s)' % (report['origin'], report['destination'],
                                                    report.get('start_date') or 'beginning',
                                                    report.get('end_date') or 'end'),
             ' -- RINEX: %i in range, %i to move, %i already in the destination (deleted from the origin), '
             '%i stay in the origin' % (rinex.get('in_range', 0), rinex.get('to_move', 0), rinex.get('to_delete', 0),
                                        rinex.get('remaining_at_origin', 0))]

    for reason, count in rinex.get('skipped', {}).items():
        if count:
            lines.append('    skipped %i: %s' % (count, RINEX_SKIP_REASONS[reason]))

    for item in report.get('tables', []):
        if item['to_move'] or item['skipped']:
            lines.append(' -- %s: %i to move, %i skipped (same key in both stations)'
                         % (item['table'], item['to_move'], item['skipped']))

    info = report.get('station_info')
    if info:
        lines.append(' -- station_info: %i record(s) to copy, %i skipped (overlap)'
                     % (info['to_copy'], info['skipped_overlap']))

    for item in report.get('arrays', []):
        if item['rows']:
            lines.append(' -- %s (%s): %i row(s) updated' % (item['table'], item['column'], item['rows']))

    if report.get('origin_will_be_deleted'):
        lines.append(' -- The origin station will be deleted at the end of the process.')
        for item in report.get('deleted_with_origin', []):
            if item['rows']:
                lines.append('    %s: %i row(s) deleted with the origin' % (item['table'], item['rows']))
        api_data = report.get('api_data')
        if api_data:
            lines.append('    API data moved: %i people/roles, %i images, %i attached files, %i visits'
                         % (api_data['people_roles'], api_data['images'], api_data['attached_files'],
                            api_data['visits']))
    elif report.get('delete_origin_requested'):
        lines.append(' -- The origin station will NOT be deleted (see warnings).')

    lines += [' -- Warning: ' + warning for warning in report.get('warnings', [])]
    lines += [' -- BLOCKED: ' + blocker for blocker in report.get('blockers', [])]

    if report.get('executed'):
        lines.append(' -- Merge done.' + (' The origin station was deleted.' if report.get('origin_deleted') else ''))

    return lines


def replace_in_array(items, origin_id, destination_id):
    """items with origin_id replaced by destination_id, without repeating destination_id."""
    result = []
    for item in items or []:
        item = destination_id if item == origin_id else item
        if item == destination_id and destination_id in result:
            continue
        result.append(item)
    return result


def replace_in_subnet(stations, aliases, origin_id, destination_id, destination_alias):
    """stations and aliases of a gamit_subnets row (same index, same station) with the origin
    replaced by the destiny."""
    aliases = list(aliases or [])
    new_stations, new_aliases = [], []
    for i, station in enumerate(stations or []):
        alias = aliases[i] if i < len(aliases) else None
        if station == origin_id:
            station, alias = destination_id, destination_alias
        if station == destination_id and station in new_stations:
            continue
        new_stations.append(station)
        new_aliases.append(alias)
    return new_stations, new_aliases


def retarget_media_name(name, origin, destination):
    """New name of a media file of the origin station ('stations/<net>/<stn>/...'), or None if the
    name is not under the folder of the origin."""
    prefix = 'stations/%s/%s/' % tuple(origin)
    if not name or not name.startswith(prefix):
        return None
    return 'stations/%s/%s/' % tuple(destination) + name[len(prefix):]


def as_date(date):
    """datetime.date of a datetime.date or a pyDate.Date (None stays None)."""
    if date is None:
        return None
    if isinstance(date, datetime.datetime):
        return date.date()
    if isinstance(date, datetime.date):
        return date
    return date.datetime().date()


def day_bound(date, default):
    """year * 1000 + doy of a date, to compare it with the Year and DOY columns of the tables."""
    return default if date is None else date.year * 1000 + date.timetuple().tm_yday


class StationMerge(object):

    def __init__(self, cnn, origin, destination, start_date=None, end_date=None, delete_origin=False,
                 archive=None, media_root=None):
        """
        :param cnn: dbConnection.Cnn
        :param origin: (NetworkCode, StationCode) of the station that gives its data
        :param destination: (NetworkCode, StationCode) of the station that receives it. It must exist
        :param start_date: first day to move (datetime.date or pyDate.Date). None: since the beginning
        :param end_date: last day to move (datetime.date or pyDate.Date). None: until the end
        :param delete_origin: delete the origin station if it is left without RINEX files
        :param archive: pyArchiveStruct.RinexStruct to use. None: one with the default configuration
        :param media_root: folder of the media files of the REST API. Needed to move the files of the images,
                           attached files and visits of the origin when it is deleted
        """
        self.cnn           = cnn
        self.origin        = tuple(origin)
        self.destination   = tuple(destination)
        self.start_date    = as_date(start_date)
        self.end_date      = as_date(end_date)
        self.delete_origin = delete_origin
        self.archive       = archive if archive is not None else pyArchiveStruct.RinexStruct(cnn)
        self.media_root    = media_root

        self.low  = day_bound(self.start_date, 0)
        self.high = day_bound(self.end_date, NO_END_DATE)

        self.origin_id      = '%s.%s' % self.origin
        self.destination_id = '%s.%s' % self.destination
        self._existing      = None

    def preview(self):
        """What execute() would do. Reads only."""
        self._validate()
        return self._analyze()['report']

    def execute(self):
        """
        Moves the data of the origin to the destiny.
        :return: the report of preview() plus 'executed' and 'origin_deleted'
        """
        self._validate()
        undo = []
        try:
            self.cnn.begin_transac()

            # the stations can be deleted by another operation while this one waits for the lock
            locked = self._rows('SELECT api_id FROM stations WHERE ("NetworkCode", "StationCode") IN '
                                '((%s, %s), (%s, %s)) FOR UPDATE', (*self.origin, *self.destination))
            if len(locked) != 2:
                raise StationMergeException(
                    'Station %s or %s no longer exists: it was deleted by another operation while this merge '
                    'was waiting its turn. Nothing was changed.' % (self.origin_id, self.destination_id))

            plan = self._analyze()
            report = plan['report']
            if report['blockers']:
                raise StationMergeException(' '.join(report['blockers']))

            self._move_rinex(plan, undo)
            self._move_time_tables()
            self._copy_station_info(plan)
            self._replace_subnets(plan)
            if plan['will_delete']:
                if self._has_api():
                    self._move_api_data(undo)
                self._replace_arrays()
                self._add_events(plan, deleted=True)
                self._delete_origin()
            else:
                self._add_events(plan, deleted=False)
            self._update_timespans(keep_origin=not plan['will_delete'])

            self.cnn.commit_transac()
        except Exception:
            self.cnn.rollback_transac()
            self._undo_file_moves(undo)
            raise

        # the database is final: now the duplicated files can go
        for path in plan['files_to_remove']:
            try:
                os.remove(path)
            except OSError as e:
                sys.stderr.write(' -- Could not remove %s after the merge: %s\n' % (path, e))

        report['executed']       = True
        report['origin_deleted'] = plan['will_delete']
        return report

    def _validate(self):
        if self.origin == self.destination:
            raise StationMergeException('The origin and destination stations must be different.')
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise StationMergeException('The start date must not be after the end date.')

        # the codes are matched without distinguishing case, then used as stored in the database
        resolved = []
        for label, (network, station) in (('origin', self.origin), ('destination', self.destination)):
            found = self._rows('SELECT "NetworkCode", "StationCode" FROM stations '
                               'WHERE lower("NetworkCode") = lower(%s) AND lower("StationCode") = lower(%s)',
                               (network, station))
            if not found:
                raise StationMergeException('The %s station %s.%s does not exist.' % (label, network, station))
            resolved.append((found[0]['NetworkCode'], found[0]['StationCode']))

        self.origin, self.destination = resolved
        self.origin_id      = '%s.%s' % self.origin
        self.destination_id = '%s.%s' % self.destination

        if self.origin == self.destination:
            raise StationMergeException('The origin and destination stations must be different.')

    def _rows(self, sql, params=None):
        return self.cnn.query(sql, params).dictresult()

    def _scalar(self, sql, params=None):
        return self.cnn.query(sql, params).getresult()[0][0]

    def _station_api_id(self, station):
        return self._scalar('SELECT api_id FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s',
                            station)

    def _time_tables(self):
        """TIME_TABLES that exist in this database (older ones lack some)."""
        self._load_existing_tables()
        return [(table, keys) for table, keys in TIME_TABLES if table in self._existing]

    def _owned_tables(self):
        self._load_existing_tables()
        return [table for table in OWNED_TABLES if table in self._existing]

    def _has_api(self):
        self._load_existing_tables()
        return all(table in self._existing for table in API_TABLES)

    def _load_existing_tables(self):
        if self._existing is None:
            names = {table for table, _ in TIME_TABLES} | set(OWNED_TABLES) | set(API_TABLES) | {'campaign_plans'}
            self._existing = {row['table_name'] for row in self._rows(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_name = ANY(%s)", (list(names),))}

    def _time_table_filter(self, table, keys, collisions):
        """FROM/WHERE of the rows of the origin in the date range that do (collisions=True) or
        do not exist in the destiny."""
        same_key = ' AND '.join('d."%s" = o."%s"' % (key, key) for key in keys)
        sql = ('FROM "%s" o WHERE o."NetworkCode" = %%s AND o."StationCode" = %%s '
               'AND o."Year" * 1000 + o."DOY" BETWEEN %%s AND %%s '
               'AND %s EXISTS (SELECT 1 FROM "%s" d WHERE d."NetworkCode" = %%s AND d."StationCode" = %%s AND %s)'
               % (table, '' if collisions else 'NOT', table, same_key))
        return sql, [*self.origin, self.low, self.high, *self.destination]

    def _analyze(self):
        report = {
            'origin':                self.origin_id,
            'destination':           self.destination_id,
            'start_date':            self.start_date.isoformat() if self.start_date else None,
            'end_date':              self.end_date.isoformat() if self.end_date else None,
            'delete_origin_requested': self.delete_origin,
            'warnings':              [],
            'blockers':              [],
        }
        plan = {'report': report, 'moves': [], 'duplicates': [], 'files_to_remove': [], 'info_copies': []}

        self._analyze_rinex(plan)

        tables = []
        for table, keys in self._time_tables():
            movable_sql, params = self._time_table_filter(table, keys, collisions=False)
            skipped_sql, skipped_params = self._time_table_filter(table, keys, collisions=True)
            tables.append({
                'table':   table,
                'to_move': self._scalar('SELECT count(*) ' + movable_sql, params),
                'skipped': self._scalar('SELECT count(*) ' + skipped_sql, skipped_params),
            })
        report['tables'] = tables

        self._analyze_station_info(plan)

        remaining = report['rinex']['remaining_at_origin']
        visit_collisions = self._visit_collisions() if self._has_api() else 0
        plan['will_delete'] = self.delete_origin and remaining == 0 and visit_collisions == 0
        if self.delete_origin and not plan['will_delete']:
            if remaining:
                report['warnings'].append(
                    'The origin station is not deleted because %d RINEX file(s) stay in it.' % remaining)
            if visit_collisions:
                report['warnings'].append(
                    'The origin station is not deleted because %d of its visits are on the same date as '
                    'a visit of the destination station.' % visit_collisions)
        report['origin_will_be_deleted'] = plan['will_delete']

        subnet_filter, subnet_params = self._subnets_filter(plan['will_delete'])
        report['arrays'] = [{
            'table': 'gamit_subnets', 'column': 'stations, alias, ties',
            'rows':  self._scalar('SELECT count(*) FROM gamit_subnets ' + subnet_filter, subnet_params)}]
        report['deleted_with_origin'] = []
        report['api_data']            = None

        if plan['will_delete']:
            for table, column, _ in self._array_targets():
                report['arrays'].append({
                    'table': table, 'column': column,
                    'rows':  self._scalar('SELECT count(*) FROM "%s" WHERE %%s = ANY("%s")' % (table, column),
                                          (self.origin_id,))})

            moved = {item['table']: item['to_move'] for item in tables}
            report['deleted_with_origin'].append({
                'table': 'events',
                'rows':  self._scalar('SELECT count(*) FROM events WHERE "NetworkCode" = %s AND "StationCode" = %s',
                                      self.origin)})
            for table in self._owned_tables():
                total = self._scalar('SELECT count(*) FROM "%s" WHERE "NetworkCode" = %%s AND "StationCode" = %%s'
                                     % table, self.origin)
                report['deleted_with_origin'].append({'table': table, 'rows': total - moved.get(table, 0)})

            report['api_data'] = self._api_data_counts() if self._has_api() else None
            if report['api_data']:
                self._check_name_collisions(report)
            if report['api_data'] and self.media_root is None and (
                    report['api_data']['images'] + report['api_data']['attached_files'] +
                    report['api_data']['visits']):
                report['blockers'].append(
                    'The media folder is unknown: the images, attached files and visits of the origin station '
                    'cannot be moved.')
        return plan

    def _analyze_rinex(self, plan):
        report   = plan['report']
        root     = self.archive.Config.archive_path
        writable = os.access(root, os.W_OK)

        rows = self._rows('SELECT * FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s '
                          'AND "ObservationYear" * 1000 + "ObservationDOY" BETWEEN %s AND %s '
                          'ORDER BY "ObservationYear", "ObservationDOY"', (*self.origin, self.low, self.high))

        in_destination = {
            (r['ObservationYear'], r['ObservationDOY'], r['Interval'], r['Completion'])
            for r in self._rows('SELECT "ObservationYear", "ObservationDOY", "Interval", "Completion" FROM rinex '
                                'WHERE "NetworkCode" = %s AND "StationCode" = %s '
                                'AND "ObservationYear" * 1000 + "ObservationDOY" BETWEEN %s AND %s',
                                (*self.destination, self.low, self.high))}

        skipped = dict.fromkeys(RINEX_SKIP_REASONS, 0)
        examples = []
        duplicate_examples = []
        planned_destinations = set()

        def skip(row, reason):
            skipped[reason] += 1
            if len(examples) < EXAMPLES_LIMIT:
                examples.append({'filename': row['Filename'],
                                 'year': int(row['ObservationYear']), 'doy': int(row['ObservationDOY']),
                                 'reason': RINEX_SKIP_REASONS[reason]})

        for row in rows:
            key = (row['ObservationYear'], row['ObservationDOY'], row['Interval'], row['Completion'])
            try:
                src_path, dst_path, db_filename = self.archive.rename_rinex_paths(row, *self.destination)
            except Exception:
                skip(row, 'invalid_filename')
                continue

            if key in in_destination:
                # the destiny has this RINEX: the one of the origin is deleted (after the commit)
                plan['duplicates'].append({'api_id': row['api_id']})
                if os.path.isfile(src_path):
                    plan['files_to_remove'].append(src_path)
                if len(duplicate_examples) < EXAMPLES_LIMIT:
                    duplicate_examples.append({'filename': row['Filename'], 'year': int(row['ObservationYear']),
                                               'doy': int(row['ObservationDOY'])})
            elif not os.path.isfile(src_path):
                skip(row, 'missing_in_archive')
            elif os.path.exists(dst_path) or dst_path in planned_destinations:
                skip(row, 'destination_file_exists')
            else:
                planned_destinations.add(dst_path)
                plan['moves'].append({'api_id': row['api_id'], 'record': row, 'year': int(row['ObservationYear']),
                                      'doy': int(row['ObservationDOY'])})

        total_at_origin = self._scalar('SELECT count(*) FROM rinex WHERE "NetworkCode" = %s AND "StationCode" = %s',
                                       self.origin)
        report['rinex'] = {
            'in_range':            len(rows),
            'to_move':             len(plan['moves']),
            'to_delete':           len(plan['duplicates']),
            'delete_examples':     duplicate_examples,
            'skipped':             skipped,
            'examples':            examples,
            'remaining_at_origin': total_at_origin - len(plan['moves']) - len(plan['duplicates']),
            'archive_writable':    writable,
        }
        if skipped['missing_in_archive']:
            report['blockers'].append(
                '%d RINEX file(s) of the origin are not in the archive. Check the [archive] path of the '
                'configuration file: the merge is blocked so the database does not end up out of sync '
                'with the archive.' % skipped['missing_in_archive'])
        if (plan['moves'] or plan['files_to_remove']) and not writable:
            report['blockers'].append(
                'The RINEX archive is read-only: the files cannot be renamed. Mount it with write '
                'permission to move RINEX files.')

    def _station_info_records(self, station):
        return station_info.StationInfo(self.cnn, station[0], station[1], allow_empty=True)

    def _analyze_station_info(self, plan):
        """Records of the origin to copy: they cover a day moved to the destiny, which has no record
        for it, and do not overlap any record of the destiny."""
        report = plan['report']
        report['station_info'] = {'to_copy': 0, 'skipped_overlap': 0}
        if not plan['moves']:
            return

        open_end = datetime.datetime.max

        def span(record):
            end = record.DateEnd
            return (record.DateStart.datetime(),
                    end.datetime() if end and end.year < 9999 else open_end)

        destination = self._station_info_records(self.destination)

        noon = datetime.timedelta(hours=12)
        days = {datetime.datetime(move['year'], 1, 1) + datetime.timedelta(days=move['doy'] - 1) + noon
                for move in plan['moves']}
        covered = [span(record) for record in destination.records]
        uncovered = {day for day in days if not any(s <= day <= e for s, e in covered)}

        for record in self._station_info_records(self.origin).records:
            start, end = span(record)
            if not any(start <= day <= end for day in uncovered):
                continue
            copy = station_info.StationInfoRecord.from_dict(record.to_database_dict(), *self.destination)
            if destination.overlaps(copy):
                report['station_info']['skipped_overlap'] += 1
                continue
            # later copies must not overlap this one either
            destination.records.append(copy)
            plan['info_copies'].append(copy)

        report['station_info']['to_copy'] = len(plan['info_copies'])
        if report['station_info']['skipped_overlap']:
            report['warnings'].append(
                '%d station information record(s) of the origin overlap records of the destination and '
                'are not copied.' % report['station_info']['skipped_overlap'])

    def _subnets_filter(self, all_dates):
        sql    = 'WHERE (%s = ANY(stations) OR %s = ANY(ties))'
        params = [self.origin_id, self.origin_id]
        if not all_dates:
            sql    += ' AND "Year" * 1000 + "DOY" BETWEEN %s AND %s'
            params += [self.low, self.high]
        return sql, params

    def _visit_collisions(self):
        return self._scalar('SELECT count(*) FROM api_visits o WHERE o.station_id = %s AND o.date IN '
                            '(SELECT date FROM api_visits WHERE station_id = %s)',
                            (self._station_api_id(self.origin), self._station_api_id(self.destination)))

    def _check_name_collisions(self, report):
        """Images and attached files are unique by name inside a station: a name that the destiny already
        has would make the database reject the move."""
        origin, destination = self._station_api_id(self.origin), self._station_api_id(self.destination)
        for table, column, label in (('api_stationimages', 'name', 'image'),
                                     ('api_stationattachedfiles', 'filename', 'attached file')):
            names = [row['name'] for row in self._rows(
                'SELECT "%s" AS name FROM "%s" WHERE station_id = %%s AND "%s" IN '
                '(SELECT "%s" FROM "%s" WHERE station_id = %%s) ORDER BY 1' % (column, table, column, column, table),
                (origin, destination))]
            if names:
                report['blockers'].append(
                    'The origin station has %d %s(s) whose name already exists in the destination station (%s). '
                    'Rename or delete them before merging.' % (len(names), label, ', '.join(names[:5])))

    def _api_data_counts(self):
        origin, destination = self._station_api_id(self.origin), self._station_api_id(self.destination)
        return {
            'people_roles':   self._scalar(
                'SELECT count(*) FROM api_rolepersonstation o WHERE o.station_id = %s AND NOT EXISTS ('
                'SELECT 1 FROM api_rolepersonstation d WHERE d.station_id = %s '
                'AND d.role_id = o.role_id AND d.person_id = o.person_id)', (origin, destination)),
            'images':         self._scalar('SELECT count(*) FROM api_stationimages WHERE station_id = %s', (origin,)),
            'attached_files': self._scalar('SELECT count(*) FROM api_stationattachedfiles WHERE station_id = %s',
                                           (origin,)),
            'visits':         self._scalar(
                'SELECT count(*) FROM api_visits WHERE station_id = %s AND date NOT IN '
                '(SELECT date FROM api_visits WHERE station_id = %s)', (origin, destination)),
            'metadata':       'The metadata of the destination station is kept; the one of the origin is deleted.',
        }

    def _move_file(self, source, destination, undo):
        if not os.path.isdir(os.path.dirname(destination)):
            os.makedirs(os.path.dirname(destination))
        shutil.move(source, destination)
        undo.append((destination, source))

    def _undo_file_moves(self, undo):
        for destination, source in reversed(undo):
            try:
                shutil.move(destination, source)
            except Exception:
                sys.stderr.write(' -- Station merge: could not restore %s to %s\n' % (destination, source))

    def _move_rinex(self, plan, undo):
        for move in plan['moves']:
            undo.append(self.archive.rename_rinex(move['record'], *self.destination)[::-1])
        for duplicate in plan['duplicates']:
            self.cnn.query('DELETE FROM rinex WHERE api_id = %s', (duplicate['api_id'],))

    def _move_time_tables(self):
        for table, keys in self._time_tables():
            movable_sql, params = self._time_table_filter(table, keys, collisions=False)
            where = movable_sql.split(' WHERE ', 1)[1]
            self.cnn.query('UPDATE "%s" AS o SET "NetworkCode" = %%s, "StationCode" = %%s WHERE %s' % (table, where),
                           (*self.destination, *params))

    def _copy_station_info(self, plan):
        if not plan['info_copies']:
            return
        destination = self._station_info_records(self.destination)
        for record in plan['info_copies']:
            destination.insert_station_info(record)

    def _destination_alias(self):
        rows = self._rows('SELECT "StationAlias" FROM stationalias WHERE "NetworkCode" = %s AND "StationCode" = %s',
                          self.destination)
        return rows[0]['StationAlias'] if rows else self.destination[1]

    def _replace_subnets(self, plan):
        sql, params = self._subnets_filter(plan['will_delete'])
        alias = self._destination_alias()
        for row in self._rows('SELECT api_id, stations, alias, ties FROM gamit_subnets ' + sql, params):
            stations, aliases = replace_in_subnet(row['stations'], row['alias'], self.origin_id,
                                                  self.destination_id, alias)
            ties = replace_in_array(row['ties'], self.origin_id, self.destination_id)
            self.cnn.query('UPDATE gamit_subnets SET stations = %s, alias = %s, ties = %s WHERE api_id = %s',
                           (stations, aliases, ties, row['api_id']))

    def _array_targets(self):
        self._load_existing_tables()
        return [target for target in ARRAY_TARGETS if target[0] != 'campaign_plans' or 'campaign_plans' in self._existing]

    def _replace_arrays(self):
        for table, column, pk in self._array_targets():
            for row in self._rows('SELECT "%s" AS pk, "%s" AS items FROM "%s" WHERE %%s = ANY("%s")'
                                  % (pk, column, table, column), (self.origin_id,)):
                self.cnn.query('UPDATE "%s" SET "%s" = %%s WHERE "%s" = %%s' % (table, column, pk),
                               (replace_in_array(row['items'], self.origin_id, self.destination_id), row['pk']))

    def _retarget_file(self, table, column, row_id, name, undo):
        """Moves the media file of the origin station to the folder of the destiny and keeps the new name
        in the database."""
        new_name = retarget_media_name(name, self.origin, self.destination)
        if new_name is None:
            return
        source      = os.path.join(self.media_root, name)
        destination = os.path.join(self.media_root, new_name)

        if os.path.isfile(source):
            base, extension = os.path.splitext(destination)
            counter = 1
            while os.path.exists(destination):
                destination = '%s_merged%d%s' % (base, counter, extension)
                counter += 1
            new_name = os.path.relpath(destination, self.media_root)
            self._move_file(source, destination, undo)

        self.cnn.query('UPDATE "%s" SET "%s" = %%s WHERE id = %%s' % (table, column), (new_name, row_id))

    def _move_api_data(self, undo):
        origin, destination = self._station_api_id(self.origin), self._station_api_id(self.destination)

        self.cnn.query('UPDATE api_rolepersonstation o SET station_id = %s WHERE o.station_id = %s AND NOT EXISTS ('
                       'SELECT 1 FROM api_rolepersonstation d WHERE d.station_id = %s '
                       'AND d.role_id = o.role_id AND d.person_id = o.person_id)', (destination, origin, destination))

        for table, column in STATION_MEDIA:
            for row in self._rows('SELECT id, "%s" AS name FROM "%s" WHERE station_id = %%s' % (column, table),
                                  (origin,)):
                self._retarget_file(table, column, row['id'], row['name'], undo)
            self.cnn.query('UPDATE "%s" SET station_id = %%s WHERE station_id = %%s' % table,
                           (destination, origin))

        visits = self._rows('SELECT id, log_sheet_file, navigation_file FROM api_visits WHERE station_id = %s '
                            'AND date NOT IN (SELECT date FROM api_visits WHERE station_id = %s)',
                            (origin, destination))
        for visit in visits:
            self._retarget_file('api_visits', 'log_sheet_file', visit['id'], visit['log_sheet_file'], undo)
            self._retarget_file('api_visits', 'navigation_file', visit['id'], visit['navigation_file'], undo)
            for table, column in VISIT_MEDIA:
                for row in self._rows('SELECT id, "%s" AS name FROM "%s" WHERE visit_id = %%s' % (column, table),
                                      (visit['id'],)):
                    self._retarget_file(table, column, row['id'], row['name'], undo)
            self.cnn.query('UPDATE api_visits SET station_id = %s WHERE id = %s', (destination, visit['id']))

    def _station_info_backup(self):
        stninfo = self._station_info_records(self.origin)
        return stninfo.return_stninfo() if stninfo.record_count else 'NO RECORDS FOUND'

    def _add_events(self, plan, deleted):
        moved      = len(plan['moves'])
        removed    = len(plan['duplicates'])
        range_text = '%s - %s' % (self.start_date or 'start', self.end_date or 'end')
        duplicates = ' %d RINEX file(s) already in the destination were deleted from the origin.' % removed \
            if removed else ''

        if deleted:
            # the events of the origin are deleted with it: what is worth keeping is registered in the destiny
            self.cnn.insert_event(pyEvents.Event(
                Description='Station %s was deleted from the database after merging it into this station. '
                            'Its station information records follow:\n%s'
                            % (self.origin_id, self._station_info_backup()),
                NetworkCode=self.destination[0], StationCode=self.destination[1]))
        else:
            self.cnn.insert_event(pyEvents.Event(
                Description='%d RINEX file(s) (%s) and their related data were merged into %s.%s'
                            % (moved, range_text, self.destination_id, duplicates),
                NetworkCode=self.origin[0], StationCode=self.origin[1]))

        self.cnn.insert_event(pyEvents.Event(
            Description='%d RINEX file(s) (%s) and their related data were merged from %s.'
                        % (moved, range_text, self.origin_id),
            NetworkCode=self.destination[0], StationCode=self.destination[1]))

    def _delete_origin(self):
        for table in self._owned_tables():
            self.cnn.query('DELETE FROM "%s" WHERE "NetworkCode" = %%s AND "StationCode" = %%s' % table, self.origin)
        self.cnn.query('DELETE FROM events WHERE "NetworkCode" = %s AND "StationCode" = %s', self.origin)
        # the trigger of stations deletes the rows of the REST API and the stationinfo of the station
        self.cnn.query('DELETE FROM stations WHERE "NetworkCode" = %s AND "StationCode" = %s', self.origin)

    def _update_timespans(self, keep_origin):
        for station in ((self.destination, self.origin) if keep_origin else (self.destination,)):
            self.cnn.query('SELECT update_station_timespan(%s, %s)', station)
