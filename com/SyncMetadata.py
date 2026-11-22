#!/usr/bin/env python
# Suppress deprecation warnings from paramiko/cryptography before imports
import warnings
from cryptography.utils import CryptographyDeprecationWarning
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

"""
Project: Geodetic Database Engine (GeoDE)
Date: May 2026
Author: Demian D. Gomez

Batch metadata synchronization runner.

Downloads metadata files (IGS site logs, station info) for stations configured
with metadata sources, compares them against the database using Claude, and
records findings in the stationinfo_audit table.

Usage examples:
    SyncMetadata.py all                    # All stations with metadata sources
    SyncMetadata.py arg.all                # All stations in ARG network
    SyncMetadata.py arg.unro arg.srlp      # Specific stations
    SyncMetadata.py -f station_list.txt    # From file
    SyncMetadata.py --dry-run all          # Preview without changes
"""

import argparse
import ftplib
import hashlib
import logging
import os
import socket
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, NamedTuple
from zlib import crc32 as zlib_crc32

import paramiko
import requests
from tqdm import tqdm

# GeoDE imports
from geode import dbConnection
from geode.Utils import add_version_argument, process_stnlist, station_list_help, SmartHelpFormatter
from geode.metadata.comparator import StationMetadataComparator, ComparatorError
from geode.metadata.serializers import (
    bundle_from_db,
    bundle_from_file,
    StationMetadataBundle,
)
from geode.metadata.station_info import StationInfoRecord
from geode.metadata.report import Finding, ComparisonReport, ReportParseError


class TqdmLoggingHandler(logging.Handler):
    """
    Logging handler that uses tqdm.write() to avoid disrupting progress bars.
    """
    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
        except Exception:
            self.handleError(record)


def setup_logging(level=logging.INFO, use_tqdm=True):
    """
    Setup logging for SyncMetadata.

    Args:
        level: Logging level
        use_tqdm: If True, use tqdm-compatible handler
    """
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create handler (tqdm-compatible or standard)
    if use_tqdm:
        handler = TqdmLoggingHandler()
    else:
        handler = logging.StreamHandler()

    # Simple format like ETM module
    if level == logging.DEBUG:
        handler.setFormatter(logging.Formatter(' -- %(name)s: %(message)s'))
    else:
        handler.setFormatter(logging.Formatter(' -- %(message)s'))

    root_logger.addHandler(handler)

    # Suppress verbose debug logging from Anthropic SDK and HTTP libraries
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return root_logger


logger = logging.getLogger(__name__)

# Constants
CONFIG_FILE = "gnss_data.cfg"
SERVER_TIMEOUT = 30  # seconds


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class MetadataSource:
    """Metadata source configuration for a station."""
    network_code: str
    station_code: str
    server_id: int
    metadata_source_id: int
    protocol: str
    fqdn: str
    username: Optional[str]
    password: Optional[str]
    path: str
    format: str
    current_hash: Optional[int]  # Current metadata_hash from DB


class DownloadResult(NamedTuple):
    """Result of a metadata file download."""
    success: bool
    local_path: Optional[str]
    file_hash: Optional[int]
    error: Optional[str]


# =============================================================================
# Hash Computation
# =============================================================================

def compute_file_hash(file_path: str) -> int:
    """
    Compute CRC32 hash of file contents.

    Args:
        file_path: Path to the file

    Returns:
        CRC32 hash as signed integer (matching Utils.crc32 convention)
    """
    with open(file_path, 'rb') as f:
        data = f.read()
    x = zlib_crc32(data)
    return x - ((x & 0x80000000) << 1)


# =============================================================================
# Protocol Implementations
# =============================================================================

class ProtocolBase:
    """Base class for download protocols."""

    def __init__(self, fqdn: str, username: Optional[str], password: Optional[str]):
        self.fqdn = fqdn
        self.username = username
        self.password = password

    def connect(self):
        raise NotImplementedError

    def download(self, remote_path: str, local_path: str) -> Optional[str]:
        """Download file. Returns error message or None on success."""
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError


class ProtocolFTP(ProtocolBase):
    """FTP download protocol."""

    def __init__(self, *args, passive: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.ftp = ftplib.FTP(timeout=SERVER_TIMEOUT)
        self.passive = passive

    def connect(self):
        self.ftp.connect(self.fqdn)
        if self.username and self.password:
            self.ftp.login(self.username, self.password)
        else:
            self.ftp.login()
        self.ftp.set_pasv(self.passive)

    def download(self, remote_path: str, local_path: str) -> Optional[str]:
        try:
            with open(local_path, 'wb') as f:
                self.ftp.retrbinary(f"RETR {remote_path}", f.write)
            return None
        except ftplib.error_perm as e:
            return str(e)
        except Exception as e:
            return str(e)

    def disconnect(self):
        try:
            self.ftp.quit()
        except Exception:
            pass


class ProtocolSFTP(ProtocolBase):
    """SFTP download protocol."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transport = None
        self.sftp = None

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(SERVER_TIMEOUT)
        s.connect((self.fqdn, 22))
        self.transport = paramiko.Transport(s)
        self.transport.connect(username=self.username, password=self.password)
        self.sftp = paramiko.SFTPClient.from_transport(self.transport)

    def download(self, remote_path: str, local_path: str) -> Optional[str]:
        try:
            self.sftp.get(remote_path, local_path)
            return None
        except IOError as e:
            return str(e)

    def disconnect(self):
        if self.sftp:
            self.sftp.close()
        if self.transport:
            self.transport.close()


class ProtocolHTTP(ProtocolBase):
    """HTTP/HTTPS download protocol."""

    def __init__(self, *args, protocol: str = 'http', **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        if self.username and self.password:
            self.session.auth = (self.username, self.password)
        self.base_url = f"{protocol}://{self.fqdn}"

    def connect(self):
        pass  # Session handles connections

    def download(self, remote_path: str, local_path: str) -> Optional[str]:
        try:
            # Ensure path starts with /
            if not remote_path.startswith('/'):
                remote_path = '/' + remote_path
            url = self.base_url + remote_path
            r = self.session.get(url, timeout=SERVER_TIMEOUT)
            if r.status_code == 200:
                with open(local_path, 'wb') as f:
                    f.write(r.content)
                return None
            elif r.status_code == 404:
                return f"404 Not Found: {url}"
            else:
                return f"HTTP {r.status_code}: {url}"
        except requests.RequestException as e:
            return str(e)

    def disconnect(self):
        self.session.close()


def get_protocol(protocol_name: str, fqdn: str,
                 username: Optional[str], password: Optional[str]) -> ProtocolBase:
    """Factory function to create protocol handler."""
    protocol_name = protocol_name.upper()
    if protocol_name == 'FTP':
        return ProtocolFTP(fqdn, username, password)
    elif protocol_name == 'FTPA':
        return ProtocolFTP(fqdn, username, password, passive=False)
    elif protocol_name == 'SFTP':
        return ProtocolSFTP(fqdn, username, password)
    elif protocol_name == 'HTTP':
        return ProtocolHTTP(fqdn, username, password, protocol='http')
    elif protocol_name == 'HTTPS':
        return ProtocolHTTP(fqdn, username, password, protocol='https')
    else:
        raise ValueError(f"Unknown protocol: {protocol_name}")


# =============================================================================
# Database Functions
# =============================================================================

def get_metadata_sources(cnn: dbConnection.Cnn,
                         stations: List[dict]) -> List[MetadataSource]:
    """
    Query metadata sources for the given stations.

    Args:
        cnn: Database connection
        stations: List of station dicts with NetworkCode, StationCode

    Returns:
        List of MetadataSource objects for stations with configured metadata sources
    """
    sources = []

    for stn in stations:
        net = stn['NetworkCode']
        sta = stn['StationCode']

        # Query sources_stations joined with sources_servers and sources_metadata
        result = cnn.query_float(f"""
            SELECT
                st."NetworkCode",
                st."StationCode",
                st.server_id,
                sv.metadata_source_id,
                COALESCE(sm.protocol, sv.protocol) AS protocol,
                COALESCE(sm.fqdn, sv.fqdn) AS fqdn,
                COALESCE(sm.username, sv.username) AS username,
                COALESCE(sm."password", sv."password") AS password,
                COALESCE(sm."path", sv."path") AS path,
                COALESCE(sm."format", sv."format") AS format,
                st.metadata_hash
            FROM sources_stations st
            JOIN sources_servers sv USING (server_id)
            LEFT JOIN sources_metadata sm ON sv.metadata_source_id = sm.id
            WHERE st."NetworkCode" = '{net}'
              AND st."StationCode" = '{sta}'
              AND sv.metadata_source_id IS NOT NULL
            ORDER BY st.try_order ASC
            LIMIT 1
        """, as_dict=True)

        if result:
            r = result[0]
            sources.append(MetadataSource(
                network_code=r['NetworkCode'],
                station_code=r['StationCode'],
                server_id=r['server_id'],
                metadata_source_id=r['metadata_source_id'],
                protocol=r['protocol'],
                fqdn=r['fqdn'],
                username=r.get('username'),
                password=r.get('password'),
                path=r['path'] or '',
                format=r['format'] or 'DEFAULT_FORMAT',
                current_hash=r.get('metadata_hash'),
            ))

    return sources


def update_metadata_hash(cnn: dbConnection.Cnn,
                         network_code: str,
                         station_code: str,
                         server_id: int,
                         new_hash: int):
    """
    Update the metadata_hash for a station's source entry.

    Args:
        cnn: Database connection
        network_code: Station network code
        station_code: Station code
        server_id: Server ID from sources_stations
        new_hash: New CRC32 hash value
    """
    cnn.query(f"""
        UPDATE sources_stations
        SET metadata_hash = {new_hash}
        WHERE "NetworkCode" = '{network_code}'
          AND "StationCode" = '{station_code}'
          AND server_id = {server_id}
    """)


def check_for_new_sessions(cnn: dbConnection.Cnn,
                           network_code: str,
                           station_code: str,
                           file_bundle: 'StationMetadataBundle') -> bool:
    """
    Check if file_bundle contains any sessions not already in audit table.

    Used to determine if we need to clear audit and re-analyze with Claude.
    If all session hashes from the file exist in audit, the session data
    hasn't actually changed (even if file hash changed due to unrelated
    metadata like operator name).

    Args:
        cnn: Database connection
        network_code: Station network code
        station_code: Station code
        file_bundle: StationMetadataBundle from external file

    Returns:
        True if there are new/changed sessions requiring analysis
    """
    station_id = f"{network_code}.{station_code}"

    for session in file_bundle.sessions:
        try:
            result = cnn.query_float(f"""
                SELECT 1 FROM stationinfo_audit
                WHERE "NetworkCode" = '{network_code}'
                  AND "StationCode" = '{station_code}'
                  AND session_hash = {session.hash}
                LIMIT 1
            """, as_dict=True)
        except Exception:
            # Table might not exist yet - treat as new
            logger.debug(f"{station_id}: Session hash {session.hash} check failed (table may not exist)")
            return True

        if not result:
            logger.debug(
                f"{station_id}: New session detected (hash={session.hash}): "
                f"{session.DateStart} -> {session.DateEnd}"
            )
            return True

    # All sessions exist in audit table
    logger.debug(f"{station_id}: All {len(file_bundle.sessions)} sessions already in audit")
    return False


def clear_station_audit(cnn: dbConnection.Cnn,
                        network_code: str,
                        station_code: str) -> int:
    """
    Clear all audit records for a station.

    Called when session data has changed - previous audit conclusions
    may no longer be valid since Claude needs full context for comparison.
    Re-running comparison will generate fresh audit entries.

    Args:
        cnn: Database connection
        network_code: Station network code
        station_code: Station code

    Returns:
        Number of records deleted
    """
    result = cnn.query(f"""
        DELETE FROM stationinfo_audit
        WHERE "NetworkCode" = '{network_code}'
          AND "StationCode" = '{station_code}'
        RETURNING api_id
    """)
    return result.ntuples()


def upsert_audit(cnn: dbConnection.Cnn,
                 network_code: str,
                 station_code: str,
                 finding: Finding):
    """
    Insert or update an audit entry for a finding.

    Never overwrites human dispositions (APPLIED/DISMISSED/DEFERRED).
    Only updates rows that are still unsettled (NULL or NO_ACTION).

    Args:
        cnn: Database connection
        network_code: Station network code
        station_code: Station code
        finding: Finding object from comparison
    """
    import json

    # Escape strings for SQL
    def sql_escape(s: Optional[str]) -> str:
        if s is None:
            return 'NULL'
        # Use dollar quoting for safe string handling
        return f"$${s}$$"

    # Convert dict to JSONB SQL literal
    def jsonb_escape(d: Optional[dict]) -> str:
        if d is None:
            return 'NULL'
        # Use dollar quoting with JSON string
        return f"$${json.dumps(d)}$$::jsonb"

    disposition = "'NO_ACTION'" if finding.action == 'NO_ACTION' else 'NULL'
    db_record = jsonb_escape(finding.db_record)  # Now JSONB: {"DateStart": "..."}
    db_field_values = jsonb_escape(finding.db_field_values)
    file_field_values = jsonb_escape(finding.file_field_values)

    # Insert new entry
    cnn.query(f"""
        INSERT INTO stationinfo_audit (
            "NetworkCode", "StationCode", session_hash,
            finding_type, action_required,
            db_record, claude_summary,
            db_field_values, file_field_values,
            disposition
        ) VALUES (
            '{network_code}', '{station_code}', {finding.hash},
            '{finding.finding_type}', '{finding.action}',
            {db_record},
            {sql_escape(finding.description)},
            {db_field_values}, {file_field_values},
            {disposition}
        )
    """)


# =============================================================================
# Download Functions
# =============================================================================

# Station-specific placeholders that indicate a per-station path
STATION_PLACEHOLDERS = {'{station}', '{stn}', '{STATION}', '{STN}'}


def is_static_path(template: str) -> bool:
    """
    Check if a path template is static (no station-specific variables).

    Static paths point to shared files containing data for multiple stations
    (e.g., a single station.info file for an entire network).

    Args:
        template: Path template string

    Returns:
        True if path has no station-specific placeholders
    """
    return not any(placeholder in template for placeholder in STATION_PLACEHOLDERS)


def expand_path_template(template: str,
                         network_code: str,
                         station_code: str) -> str:
    """
    Expand path template with station-specific values.

    Supports placeholders:
        {network} or {net} - network code
        {station} or {stn} - station code (lowercase)
        {STATION} or {STN} - station code (uppercase)

    Args:
        template: Path template string
        network_code: Network code
        station_code: Station code

    Returns:
        Expanded path string
    """
    return template.format(
        network=network_code,
        net=network_code,
        station=station_code.lower(),
        stn=station_code.lower(),
        STATION=station_code.upper(),
        STN=station_code.upper(),
    )


class DownloadCache:
    """
    Cache for downloaded metadata files during a sync session.

    Caches files from static paths (no station-specific variables) to avoid
    re-downloading large shared files like network-wide station.info files.

    Thread-safe for concurrent access.
    """

    def __init__(self):
        # Key: (fqdn, path) tuple, Value: DownloadResult
        self._cache: Dict[tuple, DownloadResult] = {}
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def get_cache_key(self, source: MetadataSource) -> Optional[tuple]:
        """
        Get cache key for a source, or None if not cacheable.

        Only static paths (no station variables) are cacheable.
        """
        if is_static_path(source.path):
            # For static paths, the path doesn't need expansion
            return (source.fqdn, source.path)
        return None

    def get(self, key: tuple) -> Optional[DownloadResult]:
        """Get cached download result, or None if not cached."""
        with self._lock:
            result = self._cache.get(key)
            if result:
                self._hits += 1
            return result

    def put(self, key: tuple, result: DownloadResult) -> None:
        """Store download result in cache."""
        with self._lock:
            self._cache[key] = result
            self._misses += 1

    @property
    def stats(self) -> Dict[str, int]:
        """Return cache statistics."""
        with self._lock:
            return {
                'hits': self._hits,
                'misses': self._misses,
                'cached_files': len(self._cache),
            }


def download_metadata_file(source: MetadataSource,
                           temp_dir: str,
                           cache: Optional[DownloadCache] = None) -> DownloadResult:
    """
    Download metadata file for a station.

    For static paths (no station variables), checks cache first and stores
    result for reuse by subsequent stations from the same source.

    Args:
        source: MetadataSource configuration
        temp_dir: Directory for temporary files
        cache: Optional DownloadCache for static path caching

    Returns:
        DownloadResult with success status, local path, hash, or error
    """
    station_id = f"{source.network_code}.{source.station_code}"

    # Check cache for static paths
    cache_key = cache.get_cache_key(source) if cache else None
    if cache_key:
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"[{station_id}] Using cached file: {cached.local_path}")
            return cached

    # Expand path template
    remote_path = expand_path_template(
        source.path,
        source.network_code,
        source.station_code
    )

    # Create local temp file
    # For static paths, use a generic name based on server; for dynamic, use station_id
    suffix = Path(remote_path).suffix or '.log'
    if cache_key:
        # Static path - use hash of (fqdn, path) for unique but reusable filename
        import hashlib
        path_hash = hashlib.md5(f"{source.fqdn}{source.path}".encode()).hexdigest()[:8]
        local_path = os.path.join(temp_dir, f"shared_{path_hash}{suffix}")
    else:
        local_path = os.path.join(temp_dir, f"{station_id}{suffix}")

    try:
        # Get protocol handler
        proto = get_protocol(
            source.protocol,
            source.fqdn,
            source.username,
            source.password
        )

        # Connect and download
        proto.connect()
        error = proto.download(remote_path, local_path)
        proto.disconnect()

        if error:
            result = DownloadResult(
                success=False,
                local_path=None,
                file_hash=None,
                error=f"Download failed: {error}"
            )
        else:
            # Compute hash
            file_hash = compute_file_hash(local_path)
            result = DownloadResult(
                success=True,
                local_path=local_path,
                file_hash=file_hash,
                error=None
            )

        # Cache successful downloads of static paths
        if cache_key and result.success:
            cache.put(cache_key, result)
            logger.debug(f"[{station_id}] Cached shared file: {local_path}")

        return result

    except Exception as e:
        return DownloadResult(
            success=False,
            local_path=None,
            file_hash=None,
            error=f"Connection error: {e}"
        )


# =============================================================================
# Comparison Processing
# =============================================================================

def process_station(cnn: dbConnection.Cnn,
                    comparator: StationMetadataComparator,
                    source: MetadataSource,
                    temp_dir: str,
                    dry_run: bool = False,
                    cache: Optional[DownloadCache] = None) -> dict:
    """
    Process a single station: download, compare, and record findings.

    Args:
        cnn: Database connection
        comparator: StationMetadataComparator instance
        source: MetadataSource configuration
        temp_dir: Temporary directory for downloads
        dry_run: If True, skip writing to database
        cache: Optional DownloadCache for static path caching

    Returns:
        Dict with processing results
    """
    station_id = f"{source.network_code}.{source.station_code}"
    result = {
        'station': station_id,
        'status': 'unknown',
        'findings': [],
        'error': None,
    }

    # Download metadata file (uses cache for static paths)
    download = download_metadata_file(source, temp_dir, cache)

    if not download.success:
        result['status'] = 'download_failed'
        result['error'] = download.error
        return result

    # Layer 1: Check file hash (cheapest fast-path)
    if source.current_hash is not None and download.file_hash == source.current_hash:
        result['status'] = 'unchanged'
        return result

    try:
        # Build bundles (format is auto-detected from file content)
        db_bundle = bundle_from_db(cnn, source.network_code, source.station_code)
        file_bundle = bundle_from_file(
            download.local_path,
            source.network_code,
            source.station_code
        )

        # Check if file has any sessions
        if not file_bundle.sessions:
            result['status'] = 'empty_file'
            result['error'] = "No sessions found in metadata file"
            return result

        # Layer 1b: Check if session data actually changed
        # File hash changed but session hashes might be the same (e.g., operator name change)
        # Only clear audit and re-analyze if there are new/changed sessions
        if not dry_run:
            has_new_sessions = check_for_new_sessions(
                cnn, source.network_code, source.station_code, file_bundle
            )
            if has_new_sessions:
                cleared = clear_station_audit(cnn, source.network_code, source.station_code)
                if cleared > 0:
                    logger.debug(f"{station_id}: Cleared {cleared} audit record(s) due to session change")
            else:
                # No actual session changes - update file hash and skip Claude
                logger.debug(f"{station_id}: File changed but session data unchanged, skipping")
                update_metadata_hash(cnn, source.network_code, source.station_code,
                                     source.server_id, download.file_hash)
                result['status'] = 'unchanged'
                return result

        # Compare using Claude (fast-path skips if bundles are equal)
        report = comparator.compare(db_bundle, file_bundle, "metadata file")

        result['status'] = 'compared'
        result['findings'] = report.findings

        # Write findings to audit table
        if not dry_run:
            for finding in report.findings:
                upsert_audit(cnn, source.network_code, source.station_code,
                             finding)

            # Update metadata hash
            update_metadata_hash(cnn, source.network_code, source.station_code,
                                 source.server_id, download.file_hash)

        # Log actionable findings
        if report.needs_attention:
            result['summary'] = report.summary
            for finding in report.findings:
                if finding.action == 'NO_ACTION':
                    continue
                # Get DateStart from db_record or file_field_values
                date_start = ''
                if finding.db_record and isinstance(finding.db_record, dict):
                    date_start = finding.db_record.get('DateStart', '')[:10]
                elif finding.file_field_values:
                    date_start = finding.file_field_values.get('DateStart', '')[:10]
                # Truncate description to 50 chars
                desc = (finding.description[:50] + '...') if len(finding.description) > 50 else finding.description
                log_msg = f"  [{station_id}] {date_start} {finding.finding_type}: {finding.action} - {desc}"
                if finding.action == 'REVIEW':
                    logger.warning(log_msg)
                else:
                    logger.info(log_msg)

    except ComparatorError as e:
        result['status'] = 'comparator_error'
        result['error'] = str(e)
    except ReportParseError as e:
        result['status'] = 'parse_error'
        result['error'] = str(e)
    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)
        logger.exception(f"[{station_id}] Unexpected error")

    return result


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Synchronize station metadata from external sources',
        formatter_class=SmartHelpFormatter
    )

    parser.add_argument(
        'stnlist',
        type=str,
        nargs='+',
        help=SmartHelpFormatter.RAW_MARKER + station_list_help()
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview mode: download and compare but do not write to database'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress progress output'
    )

    parser.add_argument(
        '--api-key',
        type=str,
        metavar='KEY',
        help='Anthropic API key (default: ANTHROPIC_API_KEY env var)'
    )

    parser.add_argument(
        '--model',
        type=str,
        default='claude-sonnet-4-6',
        metavar='MODEL',
        help='Claude model to use (default: claude-sonnet-4-6)'
    )

    parser.add_argument(
        '-np', '--no-parallel',
        action='store_true',
        help='Disable parallel processing (default: use up to 10 workers)'
    )

    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=4,
        metavar='N',
        help='Number of parallel workers (default: 4, ignored with -np)'
    )

    add_version_argument(parser)

    args = parser.parse_args()

    # Configure logging (use tqdm-compatible handler unless quiet mode)
    if args.verbose:
        setup_logging(level=logging.DEBUG, use_tqdm=not args.quiet)
    elif args.quiet:
        setup_logging(level=logging.WARNING, use_tqdm=False)
    else:
        setup_logging(level=logging.INFO, use_tqdm=True)

    # Build station list
    stnlist_in = args.stnlist

    # Connect to database
    try:
        cnn = dbConnection.Cnn(CONFIG_FILE)
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        sys.exit(1)

    # Process station list
    if stnlist_in == ['all']:
        # Get all stations that have metadata sources configured
        result = cnn.query_float("""
            SELECT DISTINCT st."NetworkCode", st."StationCode"
            FROM sources_stations st
            JOIN sources_servers sv USING (server_id)
            WHERE sv.metadata_source_id IS NOT NULL
            ORDER BY st."NetworkCode", st."StationCode"
        """, as_dict=True)
        stations = [{'NetworkCode': r['NetworkCode'], 'StationCode': r['StationCode']}
                    for r in result]
    else:
        # Use standard station list processing
        try:
            stations = process_stnlist(cnn, stnlist_in,
                                       print_summary=not args.quiet,
                                       summary_title='Stations to sync:')
        except Exception as e:
            logger.error(f"Error processing station list: {e}")
            sys.exit(1)

    if not stations:
        logger.warning("No stations found matching the criteria")
        sys.exit(0)

    # Get metadata sources for stations
    sources = get_metadata_sources(cnn, stations)

    if not sources:
        logger.warning("No stations have metadata sources configured")
        sys.exit(0)

    logger.info(f"Found {len(sources)} station(s) with metadata sources\n")

    # Initialize comparator
    try:
        comparator = StationMetadataComparator(
            api_key=args.api_key,
            model=args.model
        )
    except ComparatorError as e:
        logger.error(f"Comparator initialization failed: {e}")
        sys.exit(1)

    # Process stations with progress bar
    stats = {
        'unchanged': 0,
        'compared': 0,
        'download_failed': 0,
        'empty_file': 0,
        'error': 0,
        'findings': {
            'INSERT': 0,
            'UPDATE': 0,
            'REVIEW': 0,
            'NO_ACTION': 0,
        }
    }

    # Thread-local storage for database connections
    thread_local = threading.local()

    def get_thread_connection():
        """Get or create a thread-local database connection."""
        if not hasattr(thread_local, 'cnn'):
            thread_local.cnn = dbConnection.Cnn(CONFIG_FILE)
        return thread_local.cnn

    def process_station_worker(source, temp_dir, download_cache, dry_run, api_key, model):
        """Worker function that uses thread-local database connection."""
        thread_cnn = get_thread_connection()
        # Create comparator per thread (uses thread-safe HTTP client)
        thread_comparator = StationMetadataComparator(api_key=api_key, model=model)
        return process_station(
            thread_cnn, thread_comparator, source, temp_dir,
            dry_run=dry_run,
            cache=download_cache
        )

    def update_stats(result):
        """Update stats from a result (called from main thread)."""
        status = result['status']
        if status in stats:
            stats[status] += 1
        else:
            stats['error'] += 1

        # Count findings by action
        for finding in result.get('findings', []):
            action = finding.action
            if action in stats['findings']:
                stats['findings'][action] += 1

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create download cache for static paths (shared files)
        download_cache = DownloadCache()

        if args.no_parallel:
            # Serial processing
            pbar = tqdm(
                sources,
                desc='Processing',
                disable=args.quiet,
                dynamic_ncols=True,
            )

            for source in pbar:
                station_id = f"{source.network_code}.{source.station_code}"
                pbar.set_postfix_str(station_id)

                result = process_station(
                    cnn, comparator, source, temp_dir,
                    dry_run=args.dry_run,
                    cache=download_cache
                )

                update_stats(result)

                # Log errors
                if result.get('error') and not args.quiet:
                    tqdm.write(f"[{station_id}] {result['status']}: {result['error']}")
        else:
            # Parallel processing with ThreadPoolExecutor
            num_workers = min(args.workers, len(sources))
            logger.info(f"Using {num_workers} parallel workers")

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                # Submit all tasks
                future_to_source = {
                    executor.submit(
                        process_station_worker,
                        source, temp_dir, download_cache,
                        args.dry_run, args.api_key, args.model
                    ): source
                    for source in sources
                }

                # Process results as they complete with progress bar
                pbar = tqdm(
                    as_completed(future_to_source),
                    total=len(sources),
                    desc='Processing',
                    disable=args.quiet,
                    dynamic_ncols=True,
                )

                for future in pbar:
                    source = future_to_source[future]
                    station_id = f"{source.network_code}.{source.station_code}"

                    try:
                        result = future.result()
                        update_stats(result)

                        # Log errors
                        if result.get('error') and not args.quiet:
                            tqdm.write(f"[{station_id}] {result['status']}: {result['error']}")
                    except Exception as e:
                        stats['error'] += 1
                        if not args.quiet:
                            tqdm.write(f"[{station_id}] Worker exception: {e}")

    # Print summary
    if not args.quiet:
        cache_stats = download_cache.stats
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Unchanged (hash match):  {stats['unchanged']}")
        print(f"  Compared (API called):   {stats['compared']}")
        print(f"  Download failed:         {stats['download_failed']}")
        print(f"  Empty file:              {stats['empty_file']}")
        print(f"  Errors:                  {stats['error']}")
        print()
        print("Download cache (shared files):")
        print(f"  Cached files:  {cache_stats['cached_files']}")
        print(f"  Cache hits:    {cache_stats['hits']}")
        print(f"  Cache misses:  {cache_stats['misses']}")
        print()
        print("Findings by action:")
        print(f"  INSERT:    {stats['findings']['INSERT']}")
        print(f"  UPDATE:    {stats['findings']['UPDATE']}")
        print(f"  REVIEW:    {stats['findings']['REVIEW']}")
        print(f"  NO_ACTION: {stats['findings']['NO_ACTION']}")

        if args.dry_run:
            print("\n[DRY RUN] No changes written to database")


if __name__ == '__main__':
    main()
