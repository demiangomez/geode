"""
Batch selection and parallel dispatch for RinexQC's incremental/backfill
modes and the ArchiveService.py QC hook -- the one place that decides
which rinex rows need a QC run and fans them out via pyJobServer. Both
com/RinexQC.py and ArchiveService.py call this; neither reimplements it.
"""
import os

from tqdm import tqdm

from geode import dbConnection
from geode import pyArchiveStruct
from geode import pyOptions
from geode.rinex_qc import pipeline, writer

ERRORS_LOG = 'errors_RinexQC.log'


def find_pending(cnn, config, station_list=None, force=False, newest_first=True, limit=None):
    """
    Rinex rows that need a QC run under config's config_hash.

    :param station_list: list of {'NetworkCode', 'StationCode'} dicts
        (e.g. from geode.Utils.process_stnlist) to restrict to, or None
        for the whole archive.
    :param force: if True, select every matching row regardless of
        whether it already has a current-config run (used to reprocess
        on purpose); if False (default), only rows with no run yet.
    :param newest_first: backfill wants newest-first (recent data
        available early); an ArchiveService incremental pass doesn't
        care, since it's always a small, recently-inserted set.
    :param limit: cap the number of rows returned (e.g. to try a handful
        of files before committing to a multi-thousand-file batch).
    :return: list of dicts (NetworkCode, StationCode, ObservationYear,
        ObservationDOY, Filename, rinex_id), ordered as requested.
    """
    filters = []
    params = [pipeline.TOOL_NAME, pipeline.TOOL_VERSION, config.config_hash]

    if station_list:
        pairs = ', '.join(['(%s, %s)'] * len(station_list))
        filters.append('(rinex."NetworkCode", rinex."StationCode") IN (%s)' % pairs)
        for stn in station_list:
            params += [stn['NetworkCode'], stn['StationCode']]

    if not force:
        filters.append('rinex_qc_run.id IS NULL')

    where_sql = (' AND ' + ' AND '.join(filters)) if filters else ''
    order_sql = ('ORDER BY rinex."ObservationSTime" DESC' if newest_first else
                'ORDER BY rinex."NetworkCode", rinex."StationCode", rinex."ObservationSTime"')
    limit_sql = ''
    if limit is not None:
        limit_sql = '\n        LIMIT %s'
        params.append(limit)

    return cnn.query("""
        SELECT rinex."NetworkCode", rinex."StationCode", rinex."ObservationYear",
               rinex."ObservationDOY", rinex."Filename", rinex.api_id AS rinex_id
        FROM rinex
        LEFT JOIN rinex_qc_run
            ON rinex_qc_run.rinex_id = rinex.api_id
           AND rinex_qc_run.tool = %s AND rinex_qc_run.tool_version = %s
           AND rinex_qc_run.config_hash = %s
        WHERE TRUE""" + where_sql + "\n        " + order_sql + limit_sql, params).dictresult()


def run_qc_job(record, config, force):
    """
    The per-file job: resolve the archive path, run the pipeline, write
    the result to the database. Runs on a (possibly remote) worker node
    under pyJobServer -- opens its own DB connection, matching
    ArchiveService.py's own per-file-job pattern.
    :return: (status, 'NetworkCode.StationCode', message) for the caller's callback/logging.
    """
    cnn = dbConnection.Cnn('gnss_data.cfg')
    Config = pyOptions.ReadOptions('gnss_data.cfg')
    archive = pyArchiveStruct.RinexStruct(cnn)

    station = '%s.%s' % (record['NetworkCode'], record['StationCode'])

    try:
        rel_path = archive.build_rinex_path(record['NetworkCode'], record['StationCode'],
                                            record['ObservationYear'], record['ObservationDOY'],
                                            filename=record['Filename'])
        origin_file = os.path.join(Config.archive_path, rel_path)

        result = pipeline.process_one_file(
            origin_file, config, Config,
            NetworkCode=record['NetworkCode'], StationCode=record['StationCode'],
            ObservationYear=record['ObservationYear'], ObservationDOY=record['ObservationDOY'],
            rinex_id=record['rinex_id'])

        writer.write_db(cnn, result.run, result.signals, force=force)
        return result.status, station, result.message
    finally:
        cnn.close()


def callback_handle(job):
    if job.result is not None:
        status, station, message = job.result
        if status == pipeline.STATUS_FAILED:
            tqdm.write(' -- %s: %s' % (station, message))
    elif job.exception:
        tqdm.write(' -- unhandled error, see %s' % ERRORS_LOG)
        with open(ERRORS_LOG, 'a') as f:
            f.write(str(job.exception) + '\n')


def process_batch(JobServer, candidates, config, force=False, desc='RinexQC'):
    """
    Dispatch run_qc_job() over candidates via JobServer (created and
    owned by the caller -- ArchiveService.py reuses the JobServer it
    already built for its own per-file PPP jobs rather than starting a
    second one).
    :return: dict of status -> count.
    """
    if not candidates:
        return {}

    counts = {pipeline.STATUS_OK: 0, pipeline.STATUS_PARTIAL: 0, pipeline.STATUS_FAILED: 0}

    def counting_callback(job):
        callback_handle(job)
        if job.result is not None:
            counts[job.result[0]] += 1

    pbar = tqdm(total=len(candidates), desc='%-30s' % (' >> ' + desc), ncols=160, disable=None)

    JobServer.create_cluster(
        run_qc_job, callback=counting_callback, progress_bar=pbar,
        modules=('geode.dbConnection', 'geode.pyOptions', 'geode.pyArchiveStruct',
                'geode.rinex_qc.config', 'geode.rinex_qc.pipeline', 'geode.rinex_qc.prepare',
                'geode.rinex_qc.orbits', 'geode.rinex_qc.runner', 'geode.rinex_qc.signals',
                'geode.rinex_qc.aggregate', 'geode.rinex_qc.writer', 'os'))

    for record in candidates:
        JobServer.submit(record, config, force)

    JobServer.wait()
    pbar.close()
    JobServer.close_cluster()

    return counts
