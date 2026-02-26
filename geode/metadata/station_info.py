"""
Project: Geodesy Database Engine (GeoDE)
Date: 02/16/2017
Author: Demian D. Gomez
Refactored: 2025 - Modern Python 3 with dataclasses
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Union

import numpy as np

from ..dbConnection import Cnn
from ..pyDate import Date
from ..pyEvents import Event
from ..Utils import (
    file_readlines, crc32, stationID,
    determine_frame, parse_atx_antennas
)

from ..metadata import igslog


class StationInfoException(Exception):
    """Base exception for station info errors."""

    def __init__(self, value: str):
        self.value = value
        self.event = Event(Description=value, EventType='error')
        super().__init__(value)

    def __str__(self) -> str:
        return str(self.value)


class StationInfoHeightCodeNotFound(StationInfoException):
    """Exception for missing height code translations."""
    pass


class StationInfoNoRecordFound(StationInfoException):
    pass


@dataclass
class StationInfoRecord:
    """
    Represents a single station information record.

    Modern dataclass implementation replacing the original Bunch-based approach.
    """
    NetworkCode: Optional[str] = None
    StationCode: Optional[str] = None
    ReceiverCode: str = ''
    ReceiverSerial: Optional[str] = None
    ReceiverFirmware: Optional[str] = None
    AntennaCode: str = ''
    AntennaSerial: Optional[str] = None
    AntennaHeight: float = 0.0
    AntennaNorth: float = 0.0
    AntennaEast: float = 0.0
    HeightCode: str = ''
    RadomeCode: str = ''
    DateStart: Optional[Date] = None
    DateEnd: Optional[Date] = None
    ReceiverVers: Optional[str] = None
    AntennaDAZ: Optional[float] = 0.0
    Comments: Optional[str] = ''
    hash: Optional[int] = field(default=None, init=False)

    # Internal field to pass record data during initialization
    _record: Optional[Any] = field(default=None, repr=False, compare=False)

    RECORD_FORMAT = (
        ' {:4.4}  {:16.16}  {:19.19}{:19.19}{:7.4f}  {:5.5}  {:7.4f}  {:7.4f}  '
        '{:20.20}  {:20.20}  {:>5.5}  {:20.20}  {:15.15}  {:5.5}  {:20.20}  {:8.1f} {}'
    )

    # Field specification for parsing fixed-width records
    # Format: (field_name, start_pos, end_pos)
    # Positions calculated from fieldwidths: (1, 6, 18, 19, 19, 9, 7, 9, 9, 22, 22, 7, 22, 17, 7, 20, 8, 100)
    # Note: Python slicing is [start:end) - end is exclusive
    _FIELD_SPEC = (
        ('StationCode', 1, 7),          #  6 chars: positions 1-6
        ('StationName', 7, 25),         # 18 chars: positions 7-24
        ('DateStart', 25, 44),          # 19 chars: positions 25-43
        ('DateEnd', 44, 63),            # 19 chars: positions 44-62
        ('AntennaHeight', 63, 72),      #  9 chars: positions 63-71
        ('HeightCode', 72, 79),         #  7 chars: positions 72-78
        ('AntennaNorth', 79, 88),       #  9 chars: positions 79-87
        ('AntennaEast', 88, 97),        #  9 chars: positions 88-96
        ('ReceiverCode', 97, 119),      # 22 chars: positions 97-118
        ('ReceiverVers', 119, 141),     # 22 chars: positions 119-140
        ('ReceiverFirmware', 141, 148), #  7 chars: positions 141-147
        ('ReceiverSerial', 148, 170),   # 22 chars: positions 148-169
        ('AntennaCode', 170, 187),      # 17 chars: positions 170-186
        ('RadomeCode', 187, 194),       #  7 chars: positions 187-193
        ('AntennaSerial', 194, 214),    # 20 chars: positions 194-213
        ('AntennaDAZ', 214, 222),       #  8 chars: positions 214-233
        ('Comments', 222, None),        # Rest of line: position 222 onward
    )

    def __post_init__(self):
        """
        Process record data if provided during initialization.

        This maintains backward compatibility with the old API:
        StationInfoRecord(NetworkCode, StationCode, record)
        """
        if self._record is not None:
            self._parse_and_update(self._record)
            # Clear the internal field after processing
            object.__setattr__(self, '_record', None)

        # Calculate hash after all fields are set
        self._calculate_hash()

    def _parse_and_update(self, record: Any) -> None:
        """
        Parse record (string or dict) and update instance fields.

        Args:
            record: Either a string (fixed-width format) or dict with field values
        """
        if isinstance(record, str):
            parsed = self._parse_fixed_width_record(record)
        elif isinstance(record, dict):
            parsed = record
        else:
            return

        # Update fields from parsed data
        for key, value in parsed.items():
            if key in ('AntennaNorth', 'AntennaEast', 'AntennaHeight', 'AntennaDAZ'):
                try:
                    object.__setattr__(self, key, float(value))
                except (ValueError, TypeError):
                    pass
            elif key == 'DateStart' and value:
                object.__setattr__(self, key, Date(stninfo=value))
            elif key == 'DateEnd' and value:
                object.__setattr__(self, key, Date(stninfo=value))
            elif key == 'StationCode' and value:
                object.__setattr__(self, key, value.lower())
            elif hasattr(self, key):
                object.__setattr__(self, key, value)

    @staticmethod
    def _parse_fixed_width_record(record: str, use_antenna_daz: bool = True) -> Dict[str, str]:
        """
        Parse a fixed-width station info record string.

        Args:
            record: Fixed-width format string

        Returns:
            Dictionary of parsed fields
        """
        if not record or record[0] != ' ' or len(record) < 77:
            return {}

        if not use_antenna_daz:
            fields = list(StationInfoRecord._FIELD_SPEC)
            # remove AntennaDAZ and add comments with different start
            fields.remove(StationInfoRecord._FIELD_SPEC[15])
            fields.remove(StationInfoRecord._FIELD_SPEC[16])
            fields.append(('Comments', 214, None))
            parsed = {'AntennaDAZ': 0.0}
        else:
            parsed = {}
            fields = list(StationInfoRecord._FIELD_SPEC)

        for field_name, start, end in fields:
            value = (record[start:end] if end else record[start:]).strip()
            if value:
                parsed[field_name] = value
            else:
                if field_name == 'AntennaDAZ':
                    parsed[field_name] = 0.0
                else:
                    parsed[field_name] = ''

        return parsed

    def _calculate_hash(self) -> None:
        """Create a hash using fields that affect antenna position."""
        hash_string = (
            f'{self.AntennaNorth:.4f} {self.AntennaEast:.4f} '
            f'{self.AntennaHeight:.4f} {self.HeightCode} '
            f'{self.AntennaCode} {self.RadomeCode} {self.ReceiverCode}'
        )
        self.hash = crc32(hash_string)

    @classmethod
    def from_string(
        cls,
        record: str,
        network_code: Optional[str] = None,
        station_code: Optional[str] = None
    ) -> Optional[StationInfoRecord]:
        """
        Parse a station info record from fixed-width string format.

        Uses slice-based parsing instead of struct for better readability
        and maintainability.

        Args:
            record: Station info record string (fixed-width format)
            network_code: Network code to override parsed value
            station_code: Station code to override parsed value

        Returns:
            StationInfoRecord instance or None if invalid format
        """
        # Validate basic format
        if not record or record[0] != ' ' or len(record) < 77:
            return None

        # Parse using slice positions (more readable than struct)
        parsed = cls._parse_fixed_width_record(record)

        # check the number of elements to figure out if AntennaDAZ is present or not
        try:
            _ = float(parsed['AntennaDAZ'])
        except ValueError:
            # parse again without the AntennaDAZ
            parsed = cls._parse_fixed_width_record(record, use_antenna_daz=False)

        return cls.from_dict(parsed, network_code, station_code)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], network_code: Optional[str] = None,
                  station_code: Optional[str] = None) -> StationInfoRecord:
        """
        Create a StationInfoRecord from a dictionary.

        Args:
            data: Dictionary with record data
            network_code: Network code
            station_code: Station code

        Returns:
            StationInfoRecord instance
        """
        # Convert numeric fields
        for key in ('AntennaNorth', 'AntennaEast', 'AntennaHeight', 'AntennaDAZ'):
            if key in data and data[key]:
                try:
                    data[key] = float(data[key])
                except (ValueError, TypeError):
                    data[key] = 0.0

        # Convert dates
        if 'DateStart' in data:
            data['DateStart'] = Date(stninfo=data['DateStart'])
        if 'DateEnd' in data:
            data['DateEnd'] = Date(stninfo=data['DateEnd'])

        # Convert station code to lowercase
        if 'StationCode' in data:
            data['StationCode'] = data['StationCode'].lower()

        # Use provided network/station codes
        if network_code:
            data['NetworkCode'] = network_code
        if station_code:
            data['StationCode'] = station_code

        # Remove fields not in dataclass
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        return cls(**filtered_data)

    def to_database_dict(self) -> Dict[str, Any]:
        """Convert record to database-compatible dictionary."""
        result = {}

        for key in [item[0] for item in self._FIELD_SPEC if item[0] != 'StationName'] + ['NetworkCode']:
            value = getattr(self, key)

            if key == 'DateStart' and value:
                result[key] = value.datetime()
            elif key == 'DateEnd':
                result[key] = value.datetime() if value and value.year else None
            else:
                result[key] = value

        return result

    def to_json(self) -> Dict[str, Any]:
        """Convert record to JSON-serializable dictionary."""
        data = self.to_database_dict()
        data['DateStart'] = str(self.DateStart) if self.DateStart else None
        data['DateEnd'] = str(self.DateEnd) if self.DateEnd else None
        return data

    def __str__(self) -> str:
        """Format record as station info string."""
        return self.RECORD_FORMAT.format(
            (self.StationCode or '').upper(),
            '',
            str(self.DateStart) if self.DateStart else '',
            str(self.DateEnd) if self.DateEnd else '',
            self.AntennaHeight,
            str(self.HeightCode),
            self.AntennaNorth,
            self.AntennaEast,
            str(self.ReceiverCode),
            str(self.ReceiverVers),
            str(self.ReceiverFirmware),
            str(self.ReceiverSerial),
            str(self.AntennaCode),
            str(self.RadomeCode),
            str(self.AntennaSerial),
            self.AntennaDAZ,
            str(self.Comments.replace('\n', ' ') if self.Comments else '')
        )

    def __repr__(self) -> str:
        return f'StationInfoRecord({str(self)})'

    def __getitem__(self, key: str) -> Any:
        """Support dictionary-style access for backward compatibility."""
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Support dictionary-style setting for backward compatibility."""
        setattr(self, key, value)


class StationInfo:
    """
    Station information manager for GNSS stations.

    Handles loading, validation, and manipulation of station metadata including
    receivers, antennas, and their configurations over time.
    """

    HEADER = (
        '*SITE  Station Name      Session Start      Session Stop       '
        'Ant Ht   HtCod  Ant N    Ant E    Receiver Type         Vers                  '
        'SwVer  Receiver SN           Antenna Type     Dome   Antenna SN            AntDAZ  '
    )

    def __init__(
        self,
        cnn: Optional[Cnn] = None,
        NetworkCode: Optional[str] = None,
        StationCode: Optional[str] = None,
        date: Optional[Date] = None,
        allow_empty: bool = False,
        h_tolerance: int = 0
    ):
        """
        Initialize StationInfo.

        Args:
            cnn: Database connection
            NetworkCode: Network code
            StationCode: Station code
            date: Date to find matching record
            allow_empty: Allow empty station info
            h_tolerance: Hour tolerance for gaps in station info
        """
        self.cnn = cnn
        self.NetworkCode = NetworkCode
        self.StationCode = StationCode
        self.allow_empty = allow_empty
        self.date = date
        self.records: List[StationInfoRecord] = []
        self.current_record = StationInfoRecord(NetworkCode, StationCode)
        self.record_count = 0

        if NetworkCode and StationCode and cnn:
            if self._load_records():
                if date:
                    self._find_matching_record(date, h_tolerance)

    def _load_records(self) -> bool:
        """
        Load station info records from database.

        Returns:
            True if records found, False otherwise

        Raises:
            StationInfoException: If no records found and allow_empty is False
        """
        result = self.cnn.query(
            f'SELECT * FROM stationinfo WHERE "NetworkCode" = \'{self.NetworkCode}\' '
            f'AND "StationCode" = \'{self.StationCode}\' ORDER BY "DateStart"'
        )

        if result.ntuples() == 0:
            if not self.allow_empty:
                raise StationInfoException(
                    f'Could not find ANY valid station info entry for {stationID(self)}'
                )
            self.record_count = 0
            return False

        self.records = [
            StationInfoRecord.from_dict(record, self.NetworkCode, self.StationCode)
            for record in result.dictresult()
        ]
        self.record_count = result.ntuples()
        return True

    def _find_matching_record(self, date: Date, h_tolerance: int = 0) -> None:
        """
        Find the station info record matching the given date.

        Args:
            date: Date to match
            h_tolerance: Hour tolerance for gaps

        Raises:
            StationInfoException: If no matching record found
        """
        target_dt = date.datetime()
        tolerance = datetime.timedelta(hours=h_tolerance)

        for record in self.records:
            start_dt = record.DateStart.datetime()
            end_dt = record.DateEnd.datetime()

            if start_dt - tolerance <= target_dt <= end_dt + tolerance:
                self.current_record = record
                return

        raise StationInfoException(
            f'Could not find a matching station.info record for {stationID(self)} '
            f'{date.yyyymmdd()} ({date.yyyyddd()})'
        )

    def load_stationinfo_records(self) -> bool:
        """Legacy method name for backward compatibility."""
        return self._load_records()

    def antenna_check(self, frames: List[Dict]) -> List[Dict]:
        """
        Check that antennas exist in ATX files.

        Args:
            frames: List of reference frames with ATX files

        Returns:
            List of missing antenna entries
        """
        missing = []
        atx = {frame['name']: parse_atx_antennas(frame['atx']) for frame in frames}

        for record in self.records:
            frame_name, atx_file = determine_frame(frames, record.DateStart)

            if record.AntennaCode not in atx[frame_name]:
                missing.append({
                    'record': record,
                    'atx_file': os.path.basename(atx_file),
                    'frame': frame_name
                })

        return missing

    def check_coverage(self, date: Union[Date, List]) -> None:
        """
        check that the station info covers the date being requested

        Args:
            date: date or list of dates to check

        Returns:
            None

        Raises:
            StationInfoNoRecordFound: If no record for the query date was found for the station
        """
        if isinstance(date, Date):
            date = [date]

        for d in date:
            try:
                self._find_matching_record(d)
            except StationInfoException as e:
                raise StationInfoNoRecordFound(str(e)) from e

    def station_info_gaps(self) -> List[Dict]:
        """
        Check for gaps in station info or data outside coverage.

        Returns:
            List of gap entries with rinex counts and affected records
        """
        gaps = []

        # Check gaps between consecutive records
        if len(self.records) > 1:
            for end_rec, start_rec in zip(self.records[:-1], self.records[1:]):
                sdate = start_rec.DateStart
                edate = end_rec.DateEnd

                if (sdate.datetime() - edate.datetime()).total_seconds() > 1:
                    result = self.cnn.query(
                        f'SELECT count(*) as rcount FROM rinex_proc '
                        f'WHERE "NetworkCode" = \'{self.NetworkCode}\' '
                        f'AND "StationCode" = \'{self.StationCode}\' '
                        f'AND "ObservationETime" > \'{edate.strftime()}\' '
                        f'AND "ObservationSTime" < \'{sdate.strftime()}\' '
                        f'AND "Completion" >= 0.5'
                    )

                    count = result.dictresult()[0]['rcount']
                    if count != 0:
                        gaps.append({
                            'rinex_count': count,
                            'record_start': start_rec,
                            'record_end': end_rec
                        })

        # Check for RINEX data outside station info window
        result = self.cnn.query(
            f'SELECT min("ObservationSTime") as first_obs, '
            f'max("ObservationETime") as last_obs FROM rinex_proc '
            f'WHERE "NetworkCode" = \'{self.NetworkCode}\' '
            f'AND "StationCode" = \'{self.StationCode}\' '
            f'AND "Completion" >= 0.5'
        )

        rnxtbl = result.dictresult()[0]

        if rnxtbl['first_obs'] and self.records:
            if rnxtbl['first_obs'] < self.records[0].DateStart.datetime():
                gaps.append({
                    'rinex_count': 1,
                    'record_start': self.records[0],
                    'record_end': None
                })

            if rnxtbl['last_obs'] > self.records[-1].DateEnd.datetime():
                gaps.append({
                    'rinex_count': 1,
                    'record_start': None,
                    'record_end': self.records[-1]
                })

        return gaps

    def parse_station_info(self, stninfo_file_list) -> List[StationInfoRecord]:
        """
        Parse station information from file or list.

        Args:
            stninfo_file_list: File path or list of station info records

        Returns:
            List of StationInfoRecord instances
        """
        if isinstance(stninfo_file_list, list):
            stninfo = stninfo_file_list
        else:
            _, ext = os.path.splitext(stninfo_file_list)

            if ext.lower() == '.log':
                stninfo = self._parse_log_file(stninfo_file_list)
            elif ext.lower() == '.ngl':
                stninfo = self._parse_ngl_file(stninfo_file_list)
            else:
                stninfo = file_readlines(stninfo_file_list)

        records = []
        for line in stninfo:
            record = StationInfoRecord.from_string(
                line, self.NetworkCode, self.StationCode
            )
            if record and record.DateStart:
                records.append(record)

        return records

    @ staticmethod
    def _parse_log_file(filename: str) -> List[str]:
        """Parse IGS log file format."""
        logfile = igslog.parse_igs_log_file(filename)
        stninfo = []

        for row in logfile:
            end_date = (
                str(Date(datetime=row[3]))
                if row[3].year < 2100
                else '9999 999 00 00 00'
            )

            record = StationInfoRecord.RECORD_FORMAT.format(
                row[0],  # station code
                row[1],  # station name
                str(Date(datetime=row[2])),  # session start
                end_date,  # session end
                float(row[4]) if isinstance(row[4], float) else 0.0,
                row[5],  # height code
                float(row[6]) if isinstance(row[6], float) else 0.0,
                float(row[7]) if isinstance(row[7], float) else 0.0,
                row[8],  # receiver type
                row[9],  # receiver firmware
                row[10],  # software version
                row[11],  # receiver serial
                row[12],  # antenna type
                row[13],  # radome
                row[14],  # antenna serial
                0.0, # AntennaDAZ
                row[15],  # comment
            )
            stninfo.append(record)

        return stninfo

    def _parse_ngl_file(self, filename: str) -> List[str]:
        """Parse NGL format file for the current station only."""
        records = []
        current_start_date = None
        antenna_toggle = "UNKNOWN"

        target_station = self.StationCode.lower() if self.StationCode else None

        with open(filename, 'r') as f:
            for line in f:
                parts = line.split()

                # Need at least 3 columns (station, date, and code)
                if len(parts) < 3:
                    continue

                station_code = parts[0].lower()

                # Skip stations that are not the one being analyzed
                if target_station and station_code != target_station:
                    continue

                # Skip lines with column 3 = 2 (earthquakes)
                if parts[2] == '2':
                    continue

                date_str = parts[1]
                comment = parts[3] if len(parts) > 3 else ''

                event_date = datetime.datetime.strptime(date_str, "%y%b%d")

                # Initialize first start date
                if current_start_date is None:
                    current_start_date = datetime.datetime(1990, 1, 1)

                # Create record if date changes
                if current_start_date != event_date:
                    record = create_record(
                        station_code,
                        current_start_date,
                        event_date,
                        antenna_toggle,
                        comment
                    )
                    records.append(record)

                    antenna_toggle = (
                        "Unknown antenna"
                        if antenna_toggle == "UNKNOWN"
                        else "UNKNOWN"
                    )

                current_start_date = event_date

        # Close final record
        if current_start_date is not None:
            record = create_record(
                target_station,
                current_start_date,
                datetime.datetime.now(),
                antenna_toggle,
                ""
            )
            records.append(record)

        return records

    def to_dharp(self, record: StationInfoRecord) -> StationInfoRecord:
        """
        Convert height code to DHARP.

        Args:
            record: Record to convert

        Returns:
            Converted record

        Raises:
            StationInfoHeightCodeNotFound: If conversion not possible
        """
        if record.HeightCode == 'DHARP':
            return record

        htc = self.cnn.query_float(
            f'SELECT * FROM gamit_htc WHERE "AntennaCode" = \'{record.AntennaCode}\' '
            f'AND "HeightCode" = \'{record.HeightCode}\'',
            as_dict=True
        )

        if not htc:
            raise StationInfoHeightCodeNotFound(
                f'{stationID(self)}: {record.AntennaCode} -> '
                f'Could not translate height code {record.HeightCode} to DHARP. '
                f'Check the height codes table.'
            )

        h_offset = float(htc[0]['h_offset'])
        v_offset = float(htc[0]['v_offset'])

        record.AntennaHeight = np.sqrt(
            np.square(record.AntennaHeight) - np.square(h_offset)
        ) - v_offset

        comment_addition = f'\nChanged from {record.HeightCode} to DHARP by pyStationInfo.\n'
        record.Comments = (
            record.Comments + comment_addition if record.Comments else comment_addition
        )
        record.HeightCode = 'DHARP'

        return record

    def return_stninfo(
        self,
        record: Optional[StationInfoRecord] = None,
        no_dharp_translate: bool = False
    ) -> str:
        """
        Return station info as formatted string.

        Args:
            record: Specific record to format, or None for all
            no_dharp_translate: Skip DHARP conversion

        Returns:
            Formatted station info string
        """
        records = [record] if record else self.records

        if no_dharp_translate:
            return '\n'.join(str(r) for r in records)

        return '\n'.join(str(self.to_dharp(r)) for r in records)

    def return_stninfo_short(
        self, record: Optional[StationInfoRecord] = None
    ) -> str:
        """
        Return abbreviated station info format.

        Args:
            record: Specific record or None for all

        Returns:
            Shortened station info string
        """
        stninfo_lines = self.return_stninfo(record=record).split('\n')

        return '\n'.join(
            f' {self.NetworkCode.upper()}.{line[1:110]} [...] {line[160:]}'
            for line in stninfo_lines
        )

    def overlaps(self, qrecord: StationInfoRecord) -> List[StationInfoRecord]:
        """
        Find records that overlap with the query record.

        Args:
            qrecord: Record to check for overlaps

        Returns:
            List of overlapping records
        """
        overlaps = []
        q_start = qrecord.DateStart.datetime()
        q_end = qrecord.DateEnd.datetime()

        for record in self.records:
            r_start = record.DateStart.datetime()
            r_end = record.DateEnd.datetime()

            earliest_end = min(q_end, r_end)
            latest_start = max(q_start, r_start)

            if (earliest_end - latest_start).total_seconds() > 0:
                overlaps.append(record)

        return overlaps

    def delete_station_info(self, record: StationInfoRecord) -> None:
        """Delete a station info record."""
        event = Event(
            Description=f'{record.DateStart.strftime()} has been deleted:\n{str(record)}',
            StationCode=self.StationCode,
            NetworkCode=self.NetworkCode
        )

        self.cnn.insert_event(event)
        self.cnn.delete('stationinfo', **record.to_database_dict())
        self._load_records()

    def update_station_info(self,
                            record: StationInfoRecord,
                            new_record: StationInfoRecord) -> None:
        """Update an existing station info record."""
        record.NetworkCode = new_record.NetworkCode = self.NetworkCode

        if not (self.NetworkCode and self.StationCode):
            return

        # Check for overlaps
        overlaps = self.overlaps(new_record)
        for overlap in overlaps:
            if overlap.DateStart.datetime() != record.DateStart.datetime():
                raise StationInfoException(
                    f'Record {record.DateStart} -> {record.DateEnd} '
                    f'overlaps with existing station.info records: '
                    f'{overlap.DateStart} -> {overlap.DateEnd}'
                )

        # Insert event
        event = Event(
            Description=(
                f'{record.DateStart.strftime()} has been updated:\n'
                f'{str(new_record)}\n'
                f'+++++++++++++++++++++++++++++++++++++\n'
                f'Previous record:\n{str(record)}\n'
            ),
            NetworkCode=self.NetworkCode,
            StationCode=self.StationCode
        )
        self.cnn.insert_event(event)

        # Update DateStart if changed
        if (new_record.DateStart.datetime() - record.DateStart.datetime()).seconds != 0:
            self.cnn.query(
                f'UPDATE stationinfo SET "DateStart" = \'{new_record.DateStart.strftime()}\' '
                f'WHERE "NetworkCode" = \'{self.NetworkCode}\' '
                f'AND "StationCode" = \'{self.StationCode}\' '
                f'AND "DateStart" = \'{record.DateStart.strftime()}\''
            )

        self.cnn.update(
            'stationinfo',
            new_record.to_database_dict(),
            NetworkCode=self.NetworkCode,
            StationCode=self.StationCode,
            DateStart=new_record.DateStart.datetime()
        )

        self._load_records()

    def insert_station_info(self, record: StationInfoRecord) -> None:
        """Insert a new station info record."""
        record.NetworkCode = self.NetworkCode

        if not (self.NetworkCode and self.StationCode):
            raise StationInfoException(
                'Cannot insert record without initializing pyStationInfo '
                'with NetworkCode and StationCode'
            )

        # Check if record already exists
        result = self.cnn.query(
            f'SELECT * FROM stationinfo WHERE "NetworkCode" = \'{self.NetworkCode}\' '
            f'AND "StationCode" = \'{self.StationCode}\' '
            f'AND "DateStart" = \'{record.DateStart.strftime()}\''
        )

        if result.ntuples() != 0:
            raise StationInfoException(
                f'Record {record.DateStart} -> {record.DateEnd} '
                f'already exists in station.info'
            )

        # Check for overlaps
        overlaps = self.overlaps(record)

        if overlaps:
            self._handle_insert_overlaps(record, overlaps)
        else:
            # No overlaps, simple insert
            self.cnn.insert('stationinfo', **record.to_database_dict())

            event = Event(
                Description=f'A new station information record was added:\n{str(record)}',
                StationCode=self.StationCode,
                NetworkCode=self.NetworkCode
            )
            self.cnn.insert_event(event)

        self._load_records()

    def _handle_insert_overlaps(self,
                                record: StationInfoRecord,
                                overlaps: List[StationInfoRecord]) -> None:
        """Handle insertion when overlaps exist."""
        # Extend initial date if applicable
        if (len(overlaps) == len(self.records) and
                record.DateStart.datetime() < self.records[0].DateStart.datetime()):

            if self.records_are_equal(record, self.records[0]):
                self.cnn.query(
                    f'UPDATE stationinfo SET "DateStart" = \'{record.DateStart.strftime()}\' '
                    f'WHERE "NetworkCode" = \'{self.NetworkCode}\' '
                    f'AND "StationCode" = \'{self.StationCode}\' '
                    f'AND "DateStart" = \'{self.records[0].DateStart.strftime()}\''
                )

                event = Event(
                    Description=(
                        f'The start date of the station information record '
                        f'{self.records[0].DateStart.strftime()} has been modified to '
                        f'{record.DateStart.strftime()}'
                    ),
                    StationCode=self.StationCode,
                    NetworkCode=self.NetworkCode
                )
                self.cnn.insert_event(event)
            else:
                # New different record
                record.DateEnd = Date(
                    datetime=self.records[0].DateStart.datetime() -
                    datetime.timedelta(seconds=1)
                )
                self.cnn.insert('stationinfo', **record.to_database_dict())

                event = Event(
                    Description=f'A new station information record was added:\n{str(record)}',
                    StationCode=self.StationCode,
                    NetworkCode=self.NetworkCode
                )
                self.cnn.insert_event(event)

        elif (len(overlaps) == 1 and
              overlaps[0] == self.records[-1] and
              not self.records[-1].DateEnd.year):
            # Overlap with last session
            new_end_date = record.DateStart.datetime() - datetime.timedelta(seconds=1)
            self.cnn.update(
                'stationinfo',
                {'DateEnd': new_end_date},
                **self.records[-1].to_database_dict()
            )

            self.cnn.insert('stationinfo', **record.to_database_dict())

            event = Event(
                Description=(
                    f'A new station information record was added:\n'
                    f'{self.return_stninfo(record)}\n'
                    f'The DateEnd value of previous last record was updated to {new_end_date}'
                ),
                StationCode=self.StationCode,
                NetworkCode=self.NetworkCode
            )
            self.cnn.insert_event(event)
        else:
            # Unhandled overlap
            overlap_strs = [
                f'{o.DateStart} -> {o.DateEnd}' for o in overlaps
            ]
            raise StationInfoException(
                f'Record {record.DateStart} -> {record.DateEnd} '
                f'overlaps with existing station.info records: {" ".join(overlap_strs)}'
            )

    def rinex_based_stninfo(self, ignore: int) -> str:
        """
        Build station info based on RINEX headers.

        Args:
            ignore: Number of changes to ignore before creating new record

        Returns:
            Formatted station info string
        """
        result = self.cnn.query(
            f'SELECT * FROM rinex WHERE "NetworkCode" = \'{self.NetworkCode}\' '
            f'AND "StationCode" = \'{self.StationCode}\' '
            f'ORDER BY "ObservationSTime"'
        )

        rnxtbl = result.dictresult()
        if not rnxtbl:
            return ""

        rnx = rnxtbl[0]
        rec_serial = rnx['ReceiverSerial']
        ant_serial = rnx['AntennaSerial']
        ant_height = rnx['AntennaOffset']
        rad_code = rnx['AntennaDome']
        start_date = rnx['ObservationSTime']

        stninfo = []
        count = 0

        for i, rnx in enumerate(rnxtbl):
            if (rec_serial != rnx['ReceiverSerial'] or
                ant_serial != rnx['AntennaSerial'] or
                ant_height != rnx['AntennaOffset'] or
                rad_code != rnx['AntennaDome']):

                count += 1

                if count > ignore:
                    vers = rnx['ReceiverFw'][:22]

                    record = StationInfoRecord.from_dict(
                        rnx, self.NetworkCode, self.StationCode
                    )
                    record.DateStart = Date(datetime=start_date)
                    record.DateEnd = Date(
                        datetime=rnxtbl[i - count]['ObservationETime']
                    )
                    record.HeightCode = 'DHARP'
                    record.ReceiverVers = vers[:5]
                    record.ReceiverFirmware = '-----'
                    record.ReceiverCode = rnx['ReceiverType']
                    record.AntennaCode = rnx['AntennaType']

                    stninfo.append(str(record))

                    rec_serial = rnx['ReceiverSerial']
                    ant_serial = rnx['AntennaSerial']
                    ant_height = rnx['AntennaOffset']
                    rad_code = rnx['AntennaDome']
                    start_date = rnxtbl[i - count + 1]['ObservationSTime']
                    count = 0

            elif (rec_serial == rnx['ReceiverSerial'] and
                  ant_serial == rnx['AntennaSerial'] and
                  ant_height == rnx['AntennaOffset'] and
                  rad_code == rnx['AntennaDome'] and
                  count > 0):
                # Reset counter if changes didn't exceed ignore threshold
                count = 0

        # Insert last record with 9999
        record = StationInfoRecord(self.NetworkCode, self.StationCode)
        record.DateStart = Date(datetime=start_date)
        record.DateEnd = Date(stninfo=None)
        record.HeightCode = 'DHARP'
        record.ReceiverFirmware = '-----'
        record.ReceiverCode = rnx['ReceiverType']
        record.AntennaCode = rnx['AntennaType']

        stninfo.append(str(record))

        return '\n'.join(stninfo) + '\n'

    def to_json(self) -> List[Dict[str, Any]]:
        """Convert all records to JSON format."""
        return [r.to_json() for r in self.records]

    @staticmethod
    def records_are_equal(record1: StationInfoRecord,
                          record2: StationInfoRecord) -> bool:
        """
        Check if two records have identical equipment configuration.

        Args:
            record1: First record
            record2: Second record

        Returns:
            True if records match in key fields
        """
        return (
            record1.ReceiverCode == record2.ReceiverCode and
            record1.ReceiverSerial == record2.ReceiverSerial and
            record1.AntennaCode == record2.AntennaCode and
            record1.AntennaSerial == record2.AntennaSerial and
            record1.AntennaHeight == record2.AntennaHeight and
            record1.AntennaNorth == record2.AntennaNorth and
            record1.AntennaEast == record2.AntennaEast and
            record1.HeightCode == record2.HeightCode and
            record1.RadomeCode == record2.RadomeCode
        )

    def __eq__(self, other: object) -> bool:
        """
        Check equality based on current record configuration.

        Args:
            other: Other StationInfo to compare

        Returns:
            True if current records match

        Raises:
            StationInfoException: If comparing with non-StationInfo object
        """
        if not isinstance(other, StationInfo):
            raise StationInfoException(
                f'type: {type(other)} is invalid. '
                f'Can only compare pyStationInfo.StationInfo objects'
            )

        return (
                self.current_record.AntennaCode == other.current_record.AntennaCode and
                self.current_record.AntennaHeight == other.current_record.AntennaHeight and
                self.current_record.AntennaNorth == other.current_record.AntennaNorth and
                self.current_record.AntennaEast == other.current_record.AntennaEast and
                self.current_record.AntennaSerial == other.current_record.AntennaSerial and
                self.current_record.ReceiverCode == other.current_record.ReceiverCode and
                self.current_record.ReceiverSerial == other.current_record.ReceiverSerial and
                self.current_record.RadomeCode == other.current_record.RadomeCode
        )

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.records = None

    def __getstate__(self) -> Dict[str, Any]:
        """
        Prepare object for pickling.

        Returns:
            State dictionary without unpicklable connection
        """
        state = self.__dict__.copy()
        state.pop('cnn', None)
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        """
        Restore object from pickle.

        Args:
            state: Pickled state dictionary
        """
        self.__dict__.update(state)
        self.cnn = None


def create_record(
    station_code: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    antenna_code: str,
    comment: str
) -> str:
    """
    Create a formatted station info record string.

    Args:
        station_code: 4-character station code
        start_date: Session start datetime
        end_date: Session end datetime
        antenna_code: Antenna code
        comment: Comment text

    Returns:
        Formatted station info record string
    """
    return StationInfoRecord.RECORD_FORMAT.format(
        station_code,
        "",  # station name (blank)
        str(Date(datetime=start_date)),
        str(Date(datetime=end_date)),
        0.0,  # antenna height
        "DHARP",  # height code
        0.0,  # antenna north
        0.0,  # antenna east
        "",  # receiver code
        "",  # blank
        "",  # blank
        "",  # blank
        antenna_code,
        "NONE",  # radome
        "12345",  # serial
        0.0, # antenna daz
        comment.replace('\n', ' ') if comment else ''
    )
