# GeoDE User Manual
## Geodetic Database Engine - Complete Operations Guide

### Version 1.0

---

## Table of Contents

1. [Introduction](#introduction)
2. [System Architecture](#system-architecture)
3. [Installation and Configuration](#installation-and-configuration)
4. [Data Processing Workflow](#data-processing-workflow)
5. [Command-Line Tools Reference](#command-line-tools-reference)
6. [Web Interface Guide](#web-interface-guide)
7. [Error Handling and Troubleshooting](#error-handling-and-troubleshooting)
8. [Database Management](#database-management)
9. [Best Practices](#best-practices)
10. [Appendices](#appendices)

---

## 1. Introduction

### 1.1 What is GeoDE?

**GeoDE (Geodesy Database Engine)** is a comprehensive Python framework designed for automated GNSS data processing, analysis, and management. Originally developed by Demian Gomez and contributors, GeoDE provides an end-to-end solution for handling large-scale geodetic datasets with robust metadata tracking and comprehensive quality control tools.

### 1.2 Key Features

- **Multi-Software Integration**: Seamlessly integrates GAMIT/GLOBK, GPSPACE, and M-PAGES (NGS) processing engines
- **Parallel Processing**: Distributes geodetic processing jobs across multiple compute nodes
- **PostgreSQL Integration**: Centralized storage and management of RINEX data, station metadata, and processing results
- **GeoDE Desktop Web Interface**: Interactive map-based visualization for monitoring station networks
- **Automated Quality Control**: Built-in detection of metadata inconsistencies and data gaps
- **Multi-Format Support**: Handles RINEX 2/3, Hatanaka compression, and some raw receiver formats

### 1.3 System Requirements

**Hardware Requirements:**
- Database Server: Minimum 8GB RAM, recommended 16GB+ for large networks
- Processing Nodes: Minimum 8GB RAM per node
- Storage: Varies by network size (estimate ~100MB per station-year for RINEX + products)

**Software Dependencies:**
- Python 3.10
- PostgreSQL 12 or higher
- GAMIT/GLOBK 10.71 or higher
- GFZRNX
- rnx2crx/crx2rnx (Hatanaka compression tools)
- GPSPACE
- GeoDE Desktop (installed from a separate repository)

---

## 2. System Architecture

### 2.1 Components Overview

GeoDE consists of two main components:

1. **GeoDE Command Line Interface (CLI)**: Core processing engine for parallel GNSS analysis
2. **GeoDE Desktop**: A web frontend for station monitoring and metadata management

### 2.2 Data Flow Architecture
GeoDE implements a robust data flow architecture designed to ensure data integrity and consistency throughout the entire processing pipeline. 
The system uses a two-stage approach: a **repository** for initial data collection and validation, followed by permanent **archival** based on verified station identity.

### Repository-Archive Concept

The **repository** serves as a staging area where all incoming RINEX data is collected and undergoes validation before permanent storage. This separation between repository and archive is critical for maintaining data quality:

- **Initial Collection**: Files downloaded from external sources are placed in the repository (`data_in`)
- **Coordinate Verification**: Each file undergoes PPP processing (or autonomous positioning as fallback) to determine the station's geographic coordinates
- **Spatial Coherence Check**: The computed coordinates are compared against the database to verify the file truly belongs to the claimed station

Only after passing the validation checks is the file moved to the permanent **archive**, where it is organized using both the station name (NetworkCode.StationCode) and its verified position. This dual verification mechanism—matching both station code and geographic location—prevents data misassignment and ensures that files claiming to be from station X are actually observed at station X's location.

> **Key Principle:** GeoDE never blindly trusts filename conventions. Every file must prove its identity through coordinate verification before entering the permanent archive. This approach catches common issues such as:
> - Incorrectly named files
> - Swapped station codes
> - Data from relocated stations
> - Files from nearby stations with similar names

### Processing Flow Diagram
```
External Sources → DownloadSources.py → Repository (data_in)
                                              ↓
                                      ArchiveService.py
                                        ├─→ PPP/Autonomous Positioning
                                        └─→ Spatial Coherence Check
                                              ↓
                                    ┌─────────┴─────────┐
                                    ↓                   ↓
                            Archive + Database    data_in_retry/
                                                  data_rejected/
                                    ↓
                              ScanArchive.py (PPP)
                                    ↓
                            PPP Solutions Table
                                    ↓
                            IntegrityCheck.py
                                    ↓
                        Quality Controlled Data
```

### 2.3 Directory Structure
```
Working Directory/
├── gnss_data.cfg          # Main configuration file
├── errors_*.log           # Error logs
└── production/            # Processing outputs

Archive Structure/
├── {network}/
│   ├── {station}/
│   │   ├── {year}/
│   │   │   └── {doy}/
│   │   │       └── {rinex_files}

Repository Structure/
├── data_in/               # Incoming RINEX files
├── data_in_retry/         # Files requiring retry
│   ├── multidays_found/
│   ├── wrong_date_found/
│   ├── coord_conflicts/
│   ├── rinex_issues/
│   ├── station_info_exception/
│   ├── otl_exception/
│   └── sp3_exception/
└── data_rejected/         # Rejected files
    ├── bad_rinex/
    ├── no_ppp_solution/
    └── duplicate_insert/
```

---

## 3. Installation and Configuration

### 3.1 Database Setup

#### 3.1.1 Deploy Database Skeleton
```bash
psql -U your_username -d gnss_data < database/gnss_data_dump.sql
```

#### 3.1.2 Configure Required Tables

Import the following CSV files into your database using pgAdmin or psql:

**1. System Keys** (`keys.csv`)
```sql
COPY keys FROM '/path/to/keys.csv' DELIMITER ',' CSV HEADER;
```

**2. RINEX Storage Structure** (`rinex_tank_struct.csv`)
Structure can be configured by the user. Once a structure is selected it cannot be changed.
Recommended structure:
- Level 1: KeyCode = network
- Level 2: KeyCode = year
- Level 3: KeyCode = doy
```sql
COPY rinex_tank_struct FROM '/path/to/rinex_tank_struct.csv' DELIMITER ',' CSV HEADER;
```

**3. Equipment Tables**
```sql
COPY receivers FROM '/path/to/receivers.csv' DELIMITER ',' CSV HEADER;
COPY antennas FROM '/path/to/antennas.csv' DELIMITER ',' CSV HEADER;
COPY gamit_htc FROM '/path/to/gamit_htc.csv' DELIMITER ',' CSV HEADER;
```

### 3.2 CLI Installation
```bash
pip install geode
```

**Note**: Use Python 3.10 and consider using a virtual environment.

### 3.3 Configuration File

Create `gnss_data.cfg` in your working directory:
```ini
[postgres]
hostname = your.server.fqdn
username = gnss_data_user
password = your_password
database = gnss_data
format_scripts_path = /path/to/format_scripts

[archive]
# RINEX tank location
path = /archive/path
repository = /repository/path
# Ionex files (required for GAMIT)
ionex = /orbits/ionex/$year
# Broadcast orbits
brdc = /orbits/brdc/$year
# Precise orbits
sp3 = /orbits/sp3/$gpsweek

# Compute nodes for parallel processing
node_list = node1,node2,node3,node4

# Orbit precedence (IGS convention)
sp3_ac = COD,IGS
sp3_cs = OPS,R03,MGX
sp3_st = FIN,SNX,RAP

[otl]
grdtab = /path/to/gamit/bin/grdtab
otlgrid = /path/to/gamit/tables/otl.grid
otlmodel = FES2014b

[ppp]
ppp_path = /path/to/PPP_NRCAN
ppp_exe = /path/to/PPP_NRCAN/ppp
institution = Your Institution Name
info = Your Division/Lab Name

# Reference frames
frames = IGS20,
IGS20 = 1987_1,
atx = /path/to/igs20_2335_plus.atx
```

### 3.4 Download Source Configuration

GeoDE uses a two-tier system for managing download sources:

#### 3.4.1 Database Tables

**sources_servers**: Contains server connection information
While it is possible to use Postgres (PgAdmin or psql) to insert connection information, we recommend to use GeoDE Desktop for these tasks
```sql
INSERT INTO sources_servers (protocol, fqdn, username, password, path, format)
VALUES ('ftp', 'cddis.nasa.gov', 'anonymous', 'email@domain.com',
        '/gnss/data/daily/$year/$doy/$yy{d}', 'DEFAULT_FORMAT');
```
For protocols, see [Supported Protocols](#416 Supported Protocols)

**sources_stations**: Links stations to servers with priority
```sql
INSERT INTO sources_stations ("NetworkCode", "StationCode", try_order, server_id, path, format)
VALUES ('igs', 'algo', 1, 1, NULL, NULL);
```
Fields `path` and `format` allow overriding the default fields in sources_servers for a specific station

#### 3.4.2 Format Scripts

For non-standard data formats, create processing scripts in `format_scripts_path`:

**Example: `custom_format.py`**
#@todo: add a custom format script example
```python
#!/bin/bash
# $1 = downloaded file path
# $2 = filename
# $3 = temporary directory (output location)

# Process and output RINEX files to $3
your_conversion_tool $1 -o $3/
```

---

## 4. Data Processing Workflow

### 4.1 Step 1: Data Download (DownloadSources.py)

#### 4.1.1 Purpose

DownloadSources.py retrieves RINEX data from external servers and places it in the repository for processing.

#### 4.1.2 Operation Overview

1. Queries database for station list and download sources
2. Creates parallel download connections to configured servers
3. Downloads RINEX files to `repository/data_in/{network}.{station}/`
4. Applies format conversion scripts if needed
5. Validates downloaded files

#### 4.1.3 Basic Usage
```bash
# Download data for specific stations
DownloadSources.py igs.algo igs.amc2

# Download for all stations in a network
DownloadSources.py igs.all

# Download data for a date range
DownloadSources.py igs.algo -date 2024/01/01 2024/01/31

# Download last N days
DownloadSources.py igs.algo -win 7

# Run without parallelization
DownloadSources.py igs.algo -win 7 -np
```

#### 4.1.4 Download Process Details

**Connection Management:**
- Maintains persistent connections to servers
- Automatically reconnects on timeout (max 8 retries)
- Refreshes connections every 2 seconds during idle
- Connection timeout: 20 seconds
- Reconnection interval: 3 seconds

**File Processing:**
- Files are first downloaded to station-specific directories
- Format scripts (if specified) process raw data
- Converted files are compressed using Hatanaka compression
- Original files are removed after successful conversion

**Source Priority:**
- Stations can have multiple sources with `try_order` priority
- If download from source N fails, attempts source N+1
- Process continues until file is found or sources exhausted

#### 4.1.5 Download Statistics

At completion, DownloadSources.py displays:
- `db_no_info`: Files skipped (no station info for date)
- `db_exists`: Files already in database
- `not_found`: Files not found in any source
- `process_ok`: Files successfully downloaded and processed
- `process_error`: Files with processing errors
- `ok`: Files downloaded without additional processing

#### 4.1.6 Supported Protocols

- **FTP**: Standard FTP connections (passive mode)
- **FTPA**: FTP active mode connections
- **SFTP**: SSH File Transfer Protocol
- **HTTP**: Standard HTTP downloads
- **HTTPS**: Secure HTTP downloads

**Special Handling:**
- NASA CDDIS: Preserves Authorization headers through redirects
- GAGE: Uses `es sso access --token` for authentication
- CSN: Implements challenge-token hash verification

### 4.2 Step 2: Data Ingestion (ArchiveService.py)

#### 4.2.1 Purpose

ArchiveService.py is the core data ingestion engine that:
1. Scans the repository for new RINEX files
2. Validates file quality and metadata
3. Runs PPP for coordinate determination
4. Manages station information locks
5. Moves files to permanent archive
6. Updates database records

#### 4.2.2 Operation Overview
```
Repository (data_in) → Scan → PPP → Validation → Archive + Database
                                ↓
                         Error Handling
                                ↓
                    data_rejected or data_in_retry
```

#### 4.2.3 Basic Usage
```bash
# Standard operation (scans repository)
ArchiveService.py

# Process visit files from field campaigns
ArchiveService.py -visits

# Purge locked files and temporary stations
ArchiveService.py -purge

# Run without parallelization
ArchiveService.py -np
```

#### 4.2.4 File Processing Pipeline

**1. Initial Scan**
- Scans `repository/data_in/` for RINEX files
- Checks against locks table to avoid reprocessing
- Validates RINEX filename format

**2. File Reading**
- Extracts station code, year, DOY from filename
- Reads RINEX header information
- Validates observation timespan

**3. PPP Coordinate Determination**

ArchiveService attempts to determine station coordinates using:

a. **Primary Method: PPP Processing**
   - Uses precise orbits and clocks
   - Applies ocean loading corrections
   - Requires valid RINEX header coordinates

b. **Fallback Method: Autonomous Positioning**
   - Uses broadcast ephemeris
   - Relaxed chi-square limit (1000)
   - Applied when PPP fails

**4. Spatial Coherence Check**

The service verifies file belongs to claimed station:
```python
Result, match, closest = verify_spatial_coherence(cnn, StationCode)
```

**Possible Outcomes:**

- **Single Match**: File matches exactly one station → Insert to database
- **Wrong Station Code**: File matches different station → Move to retry
- **Multiple Candidates**: Ambiguous match → Move to retry
- **No Match**: New station detected → Create lock and temporary network

#### 4.2.5 Temporary Networks and Locks

When a new station is detected:

1. **Temporary Network Creation**
   - Network code format: `???`, `?00`, `?01`, ..., `?ff`
   - Maximum 256 temporary networks
   - Prevents automatic processing until metadata assigned

2. **Lock Mechanism**
   - File recorded in `locks` table
   - Prevents repeated processing
   - User must:
     - Assign proper network code
     - Add station metadata
     - Remove lock

3. **Station Information**
   - Automatic coordinate calculation
   - OTL coefficients computed
   - Country code determined via reverse geocoding

**Example Lock Resolution:**
```sql
-- View locked files
SELECT * FROM locks;

-- After adding station metadata:
DELETE FROM locks WHERE filename = 'specific_file.crz';

-- Update network code:
UPDATE stations SET "NetworkCode" = 'real_net' 
WHERE "NetworkCode" = '???' AND "StationCode" = 'stn1';
```

#### 4.2.6 Error Categories and Handling

**Category 1: Bad RINEX Files** → `data_rejected/bad_rinex/YYYY/DDD/`

Files moved here when:
- Corrupted or unreadable RINEX
- Single epoch observations
- Cannot determine autonomous coordinates
- Unreasonable geodetic height (> 9000m or < -400m)

**Category 2: RINEX Issues** → `data_in_retry/rinex_issues/YYYY/DDD/`

Files moved here for temporary problems:
- Missing station information
- RINEX header issues
- Incomplete metadata

**Category 3: Coordinate Conflicts** → `data_in_retry/coord_conflicts/YYYY/DDD/`

Files moved here when:
- PPP coordinate doesn't match claimed station
- File matches nearby station with different code
- Multiple possible station matches

**Example Output:**
```
amc20010.24d matches coordinate of igs.amc2 (distance = 3.421 m) 
but filename indicates it is amc0. Verify file belongs to igs.amc2,
rename and try again.

Rename script:
  mv data_in_retry/coord_conflicts/2024/001/amc20010.24d \
     data_in_retry/coord_conflicts/2024/001/amc22410.24d
     
SQL insert if new station:
  INSERT INTO stations ("NetworkCode", "StationCode", 
      "auto_x", "auto_y", "auto_z", "lat", "lon", "height") 
  VALUES ('???','amc0', 4075123.456, -4596234.567, 1234567.890,
          -45.123456, 170.234567, 234.567);
```

**Category 4: No PPP Solution** → `data_rejected/no_ppp_solution/YYYY/DDD/`

Files moved here when:
- Both PPP and autonomous positioning fail
- Missing or corrupted orbit files
- Insufficient observation data quality

**Category 5: Multiday Files** → `data_in_retry/multidays_found/YYYY/DDD/`

When a RINEX file contains multiple days:
- File is split into individual daily files
- Each day file moved to retry folder
- Original file deleted
- Event logged for tracking

**Category 6: Wrong Date** → `data_in_retry/wrong_date_found/YYYY/DDD/`

Files moved here when:
- Archive folder date ≠ RINEX observation date
- Allows ArchiveService to reprocess with correct date

**Category 7: Station Information Problems** → `data_in_retry/station_info_exception/YYYY/DDD/`

Files moved here when:
- No valid station information record for observation date
- Antenna/receiver metadata conflicts
- Height code issues

**Category 8: OTL Calculation Errors** → `data_in_retry/otl_exception/YYYY/DDD/`

Files moved here when:
- Ocean loading coefficient calculation fails
- Invalid station coordinates for OTL
- GAMIT grdtab execution errors

**Category 9: Orbit Product Issues** → `data_in_retry/sp3_exception/YYYY/DDD/`

Files moved here when:
- Cannot download/find required orbit files
- Corrupted SP3 or clock files
- Broadcast ephemeris unavailable

**Category 10: Duplicate Insertions** → `data_rejected/duplicate_insert/YYYY/DDD/`

Files moved here when:
- File with same interval and completion exists
- Database constraint violation
- Prevents duplicate data

#### 4.2.7 Visit File Processing

Field campaign data can be automatically processed:
```bash
ArchiveService.py -visits
```

**Process:**
1. Queries `api_visitgnssdatafiles` table
2. Converts proprietary formats to RINEX
3. Merges RINEX files from same day/interval
4. Moves to `data_in/` for standard processing

**Supported Formats:**
- Configured in `ConvertRaw.py`
- Custom format handlers can be added
- Automatic file type detection

#### 4.2.8 Event Logging

All ArchiveService operations are logged to `events` table:

**Event Types:**
- `info`: Successful operations
- `warn`: Warnings (e.g., multiday files, duplicates)
- `error`: Processing failures

**View Recent Events:**
Events can be viewes using a SQL command or through GeoDE Desktop. We recommend the latter, where events for each station can be found in the events section of the web interface.
```sql
SELECT * FROM events 
WHERE "EventDate" >= NOW() - INTERVAL '1 day'
ORDER BY "EventDate" DESC;
```

#### 4.2.9 Performance Considerations

**Parallelization:**
- Distributes files across configured nodes
- Each node processes independently
- Database transactions ensure consistency

**Batch Processing:**
- Progress bars show real-time status
- Errors logged without stopping batch
- Summary statistics at completion

**Resource Management:**
- Temporary files cleaned automatically
- Failed jobs don't block queue
- Automatic retry on transient failures

### 4.3 Step 3: PPP Solutions (ScanArchive.py)

#### 4.3.1 Purpose

ScanArchive.py provides comprehensive archive management:
1. Scan archive for RINEX files and add to database
2. Calculate Ocean Loading coefficients
3. Insert/update station information
4. Run PPP on archived RINEX files
5. Export/import stations between systems
6. Verify data integrity

#### 4.3.2 Basic Operations

#@todo: need to change to new OTL program
**Calculate OTL Coefficients:**
```bash
# Calculate for stations without OTL
ScanArchive.py igs.algo igs.amc2 -otl
```

**Insert Station Information:**
```bash
# Scan archive for station.info files
ScanArchive.py igs.all -stninfo

# Insert from specific file
ScanArchive.py igs.algo -stninfo /path/to/station.info igs

# Read from standard input
cat station.info | ScanArchive.py igs.algo -stninfo stdin igs
```

**Run PPP Processing:**
#@todo: explain what hash is
```bash
# Process all RINEX without PPP solutions
ScanArchive.py igs.algo -ppp

# Process specific date range
ScanArchive.py igs.algo -ppp 2024/01/01 2024/01/31

# Check and recalculate if hash mismatch
ScanArchive.py igs.algo -ppp 2024/01/01 2024/01/31 hash
```

**Hash Management:**
```bash
# Rehash without recalculating PPP
ScanArchive.py igs.algo -rehash 2024/01/01 2024/01/31
```

#### 4.3.3 PPP Processing Details

**Hash Verification System:**

GeoDE uses CRC32 hashes to track station information changes:
```python
hash = station_info_hash + crc32(orbit_filename)
```

**When hash mismatch detected:**
- PPP solution marked for recalculation
- Ensures solutions reflect current metadata
- Tracks orbit file used for solution

**PPP Execution Process:**

1. **Load Station Information**
   - Query metadata for observation date
   - Apply height tolerance if specified
   - Validate antenna/receiver records

2. **RINEX Header Normalization**
   - Update approximate coordinates
   - Apply antenna offsets
   - Correct receiver/antenna codes

3. **PPP Processing**
   - Apply ocean loading corrections
   - Use precise orbits/clocks
   - Apply meteorological data if available
   - Clock interpolation enabled

4. **Spatial Coherence Verification**
   - Verify solution matches station location
   - Check against database coordinates
   - Flag suspicious coordinates

5. **Database Update**
   - Insert PPP solution record
   - Create event log entry
   - Update station statistics

**Multiday File Handling:**

If RINEX spans multiple days:
1. Split into separate daily files
2. Move to `data_in_retry/multidays_found/`
3. Delete original multiday file
4. Remove database record
5. Files will be reprocessed individually

**Station Information Tolerance:**
#@todo: check -tol switch
```bash
# Allow 2-hour gaps in station information
ScanArchive.py igs.algo -ppp -tol 2
```

Useful for:
- Early survey campaigns
- Stations with incomplete metadata
- Temporary equipment changes

#### 4.3.4 Export/Import Operations

**Export Station:**
```bash
# Full export with RINEX data
ScanArchive.py igs.algo -export

# Dataless export (metadata only)
ScanArchive.py igs.algo -export true
```

**Export Contents:**
- Station information records
- Coordinates and OTL coefficients
- RINEX file list
- RINEX data (unless dataless)

**Import Station:**
```bash
# Import with default network assignment
ScanArchive.py dummy -import ars station1.zip station2.zip

# Import with wildcards
ScanArchive.py dummy -import ars *.zip
```

**Import Process:**
1. Extract ZIP contents
2. Read JSON metadata
3. Perform spatial coherence check
4. Present options to user:
   - Insert as new station
   - Merge with existing station
   - Skip import

**Interactive Prompts:**
```
Found external station igs.algo in network igs (distance 0.123 m)
Insert data to this station?: y/n

External station igs.algo not found. 
Possible match is igs.algo: 45.234 m
(i)nsert new/(a)dd to existing station?: i/a

Multiple matches found:
  net1.algo (1)
  net2.algo (2)
(i)nsert new/(number): 1
```

#### 4.3.5 Extract RINEX from Archive
```bash
# Get specific station-day
ScanArchive.py igs.algo -get 2024/01/15
```

**Process:**
1. Locates RINEX in archive
2. Normalizes header with station information
3. Compresses and copies to current directory
4. Applies proper naming conventions

#### 4.3.6 Event Summary

At completion, ScanArchive.py displays:
```
Summary of events for this run:
-- info    : 45
-- errors  : 2
-- warnings: 7
```

**Event categories tracked:**
- Successful operations
- File rejections
- Metadata updates
- Processing errors

### 4.4 Step 4: Integrity Checking (IntegrityCheck.py)

#### 4.4.1 Purpose

IntegrityCheck.py provides comprehensive validation tools:
1. Verify RINEX file existence in archive
2. Check station information consistency
3. Validate spatial coherence of PPP solutions
4. Detect data gaps
5. Manage station merges and renames
6. Exclude or delete problematic solutions

#### 4.4.2 RINEX Integrity Checks

**Report Mode:**
```bash
# Check files exist in archive
IntegrityCheck.py igs.algo -rinex report -d 2024/01/01 2024/12/31
```

**Fix Mode:**
```bash
# Remove database records for missing files
IntegrityCheck.py igs.algo -rinex fix -d 2024/01/01 2024/12/31
```

**What it does:**
- Verifies each database record has corresponding archive file
- Reports missing files
- In fix mode: removes orphaned database records
- Deletes associated PPP/GAMIT solutions
- Logs events for tracking

#### 4.4.3 Station Information Checks

**Consistency Check:**
```bash
IntegrityCheck.py igs.algo -stnc
```

**Validates:**
1. **Session End Records**
   - Only one record should have DateEnd = 9999 999
   - Reports multiple open-ended records

2. **Overlapping Records**
   - Detects conflicting date ranges
   - Identifies ambiguous metadata periods

3. **RINEX Coverage**
   - Checks if RINEX files fall outside station info window
   - Reports missing metadata for observation periods

4. **Equipment Validation**
   - Verifies antenna codes exist in ANTEX file
   - Checks against configured reference frame ATX
   - Warns about missing radome specifications

5. **Data Gaps**
   - Identifies gaps between station info records
   - Counts RINEX files falling in gaps
   - Suggests metadata updates

**Example Output:**
```
igs.algo: There is more than one station info entry with 
Session Stop = 9999 999
Session Start -> 2020 045, 2023 128

igs.algo: There are conflicting records in the station 
information table
   2020 001 -> 2020 100 conflicts 2020 050 -> 2020 150
   
igs.algo: TRM59800.00 NONE -> Not found in ANTEX file 
igs20_2335_plus.atx (IGS20) - dome not checked

igs.algo: There is a gap with 45 RINEX file(s) between 
the following station information records: 
2021 001 -> 2021 365 :: 2022 010 -> 2023 001
```

**RINEX-Station Info Comparison:**
```bash
IntegrityCheck.py igs.algo -stnr -d 2024/01/01 2024/12/31
```

**Checks:**
- Receiver serial numbers in RINEX vs. database
- Identifies metadata mismatches
- Suggests equipment changes or RINEX errors

**Example Output:**
```
Warning! igs.algo from 2024 045 to 2024 087: 
RINEX SN 12345 != Station Information 54321 
Possible change in station or bad RINEX metadata.
```

#### 4.4.4 Spatial Coherence Validation
```bash
IntegrityCheck.py igs.algo -sc noop -d 2024/01/01 2024/12/31
```

**Options:**
- `noop`: Report only, no action
- `exclude`: Add to exclusion table
- `delete`: Remove PPP solutions

**Validates:**
- PPP coordinates match claimed station
- Solutions within 100m of database coordinates
- Identifies swapped station codes
- Detects gross coordinate errors

**Example Output:**
```
Warning! Solution for igs.algo 2024 045 does not match 
its station code. Best match: igs.alg2

Warning! Solution for igs.amc2 2024 123 was found closer 
to igs.amc0. Distance to igs.amc0: 45.234 m. 
Distance to igs.amc2: 156.789 m

Warning! PPP for igs.algo 2024 234 had no match within 
100 m. Closest station is igs.alg2 (5.234 km)
PPP solution: -12.345678 98.765432
```

#### 4.4.5 Data Gap Analysis

**Simple Gap Report:**
```bash
# Show gaps >= 5 days
IntegrityCheck.py igs.algo -g 5 -d 2020/01/01 2024/12/31
```

**Output:**
```
Data gaps in igs.algo follow:
igs.algo gap in data found 2023 045 -> 2023 067 (23 days)
igs.algo gap in data found 2024 123 -> 2024 145 (23 days)
```

**Graphical Visualization:**
```bash
IntegrityCheck.py igs.algo -gg -d 2024/01/01 2024/12/31
```

**Visual Output:**
```
igs.algo: (First and last observation: 2024 001 - 2024 365)

2024:
    001>████████████████████ ███ ████████████<120
    121>██████████ ████████████████████████<240
    241>████████████████████████████<365

Legend: █ = data present,   = data missing
        Each character = 2 days
```

**Count RINEX Files:**
```bash
IntegrityCheck.py igs.all -rnx_count -d 2024/01/01 2024/12/31
```

**Output:**
```
 2024   1   45
 2024   2   47
 2024   3   46
 ...
```
Format: Year DOY Count

#### 4.4.6 Station Information Operations

**Print Station Information:**
```bash
# Short format (screen-friendly)
IntegrityCheck.py igs.algo -print short

# Long format (full station.info)
IntegrityCheck.py igs.algo -print long
```

**Propose Station Information:**
```bash
# Generate station.info from RINEX headers
IntegrityCheck.py igs.algo -stnp

# Ignore records <= 7 days
IntegrityCheck.py igs.algo -stnp 7
```

**Uses:**
- Quick metadata generation for new stations
- Verify RINEX header consistency
- Identify equipment changes

#### 4.4.7 Station Rename/Merge

**Use Case:** Merge two stations or rename a station
```bash
# Rename igs.old_code to igs.new_code
# Date range limits which files are moved
IntegrityCheck.py igs.old_code -r igs.new_code \
    -d 2024/01/01 2024/12/31 -del_stn
```

**Process:**
1. Verifies destination station exists
2. Transfers RINEX files (renames in archive)
3. Updates database records (NetworkCode, StationCode, Filename)
4. Handles duplicate files (reports, removes from source)
5. Merges station information as needed
6. Optionally deletes empty source station

**RINEX Filename Handling:**
- Properly handles RINEX 2 and RINEX 3 formats
- Updates station code in filename
- Preserves compression type
- Maintains archive structure

**Station Information Merge:**
- Checks if destination has metadata for date range
- Copies source station info if missing in destination
- Warns about incomplete metadata transfers
- Adjusts DateStart/DateEnd if needed

**Deletion Criteria:**
- Only deletes if `-del_stn` flag used
- Only if source station completely empty
- Backs up station information in events table
- Removes from all related tables

**Example Output:**
```
Beginning transfer of 365 rinex files from igs.old_code to igs.new_code
Station will be deleted at the end of the process if no further RINEX files are found.

Processing: 100%|████████████| 365/365

igs.old_code.2024001.crz successfully moved to igs.new_code
igs.old_code.2024002.crz could not be moved (already exists in destination)
...

Station successfully deleted after rename/merge process.
```

#### 4.4.8 Solution Management

**Exclude Solutions:**
```bash
# Add solutions to exclusion table
IntegrityCheck.py igs.algo -es 2024/01/01 2024/01/31
```

**Uses:**
- Temporarily remove bad solutions from processing
- Preserve data while investigating issues
- Solutions remain in database but marked excluded

**Delete RINEX and Solutions:**
```bash
# Delete files with completion <= 0.3
IntegrityCheck.py igs.algo -del 2024/01/01 2024/01/31 0.3
```

**WARNING:** This operation cannot be undone!

**Deletes:**
- RINEX files from archive
- Database records (rinex table)
- PPP solutions
- GAMIT solutions
- All associated data

**Use Cases:**
- Remove low-quality data (< 12 hours)
- Clean up test data
- Free archive space

#### 4.4.9 Best Practices for Integrity Checking

**Regular Checks:**
```bash
# Weekly integrity check script
#!/bin/bash

# Check RINEX file existence
IntegrityCheck.py all -rinex report -d $(date -d '7 days ago' +%Y/%m/%d) $(date +%Y/%m/%d)

# Verify station information consistency
IntegrityCheck.py all -stnc

# Check spatial coherence of recent solutions
IntegrityCheck.py all -sc noop -d $(date -d '7 days ago' +%Y/%m/%d) $(date +%Y/%m/%d)
```

**After Metadata Updates:**
```bash
# Verify changes don't create conflicts
IntegrityCheck.py network.station -stnc

# Check hash values
ScanArchive.py network.station -ppp $(date +%Y/%m/%d) $(date +%Y/%m/%d) hash
```

**Before Major Operations:**
```bash
# Verify data integrity before station merge
IntegrityCheck.py source.station -rinex report
IntegrityCheck.py dest.station -rinex report
IntegrityCheck.py source.station -stnc
IntegrityCheck.py dest.station -stnc
```

---

## 5. Command-Line Tools Reference

### 5.1 Station List Syntax

All CLI tools support flexible station selection:

**Basic Syntax:**
```bash
# Single station
tool network.station

# Multiple stations
tool net1.stn1 net2.stn2

# All stations in network
tool network.all

# All stations in database
tool all

# Country code (ISO 3166)
tool ARG USA

# From file
tool @station_list.txt
```

**Wildcards (PostgreSQL regex):**
```bash
# Character ranges
tool ars.at1[3-5]      # Matches at13, at14, at15

# Match any string
tool ars.at%           # Matches at01, at02, atxx, etc.

# OR operator
tool ars.at1[1|2]      # Matches at11, at12

# Single character wildcard
tool ars._t01          # Matches aat01, bat01, etc.
```

**Exclusions:**
```bash
# Exclude specific station
tool igs.all *igs.algo

# Exclude network
tool all *igs.all

# Exclude country
tool all *USA

# In files, use - instead of *
# station_list.txt:
igs.all
-igs.algo
-igs.amc2
```

### 5.2 Date Specification

**Supported Formats:**
```bash
# Calendar date
-d 2024/01/15

# Year and day-of-year
-d 2024.015

# GPS week and day
-d 2295-1

# Fractional year
-d 2024.0410958904

# Date ranges
-d 2024/01/01 2024/12/31
-d 2024.001 2024.365
```

**Relative Dates:**
```bash
# Last N days
-win 7
-win 30
```

### 5.3 DownloadSources.py

**Synopsis:**
```bash
DownloadSources.py stnlist [options]
```

**Options:**

| Option | Arguments | Description |
|--------|-----------|-------------|
| `-date` | start [end] | Date range (yyyy.doy or yyyy/mm/dd) |
| `-win` | days | Download last N days |
| `-np` | - | Disable parallelization |

**Examples:**
```bash
# Download last week for IGS network
DownloadSources.py igs.all -win 7

# Download specific month
DownloadSources.py igs.algo igs.amc2 -date 2024/01/01 2024/01/31

# Download single day without parallel
DownloadSources.py arg.lpgs -date 2024.123 -np
```

**Output Interpretation:**
```
>> Selected 45 stations
>> Found 1200 files to download

Download: 100%|███████████| 1200/1200 
[files: db_no_info=50 db_exists=800 not_found=12 
 process_ok=300 process_error=5 ok=33]
[servers: active=8 idle=2 stopped=0]

>> Finished all Downloads and Processing
```

### 5.4 ArchiveService.py

**Synopsis:**
```bash
ArchiveService.py [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `-purge` | Delete temporary networks and locks |
| `-visits` | Process field campaign visit files |
| `-np` | Disable parallelization |

**Examples:**
```bash
# Standard operation
ArchiveService.py

# Process visits and regular files
ArchiveService.py -visits

# Clean temporary stations and locks
ArchiveService.py -purge

# Sequential processing
ArchiveService.py -np
```

**Understanding Output:**
```
>> Creating station directories in data_in
>> Processing visits: 100%|████| 45/45
>> Done processing visits
>> Merging RINEX files: 100%|████| 45/45
>> Repository CRINEZ scan: 450 files found
>> Checking locks table: 12 files locked
>> Found 438 files to process

Processing repository: 100%|████████| 438/438

-- New station lpgs was found. Please assign network 
   and remove locks.

Summary of events for this run:
-- info    : 425
-- errors  : 8
-- warnings: 5
```

### 5.5 ScanArchive.py

**Synopsis:**
```bash
ScanArchive.py stnlist [options]
```

**Options:**

| Option | Arguments | Description |
|--------|-----------|-------------|
| `-rinex` | 0\|1 | Scan archive (0=use stnlist, 1=ignore) |
| `-otl` | - | Calculate OTL coefficients |
| `-stninfo` | [file] [net] | Insert station information |
| `-export` | [true] | Export station (true=dataless) |
| `-import` | net files... | Import station ZIP files |
| `-get` | date | Extract RINEX from archive |
| `-ppp` | [dates] [hash] | Run PPP solutions |
| `-rehash` | [dates] | Update hash without PPP |
| `-tol` | hours | Station info gap tolerance |
| `-np` | - | Disable parallelization |

**Examples:**
```bash
# Scan archive for network (stations must exist in DB)
ScanArchive.py igs.all -rinex 0

# Scan all files, create stations automatically
ScanArchive.py all -rinex 1

# Calculate OTL for stations without coefficients
ScanArchive.py igs.algo igs.amc2 -otl

# Insert station info from archive
ScanArchive.py igs.all -stninfo

# Insert from specific file
ScanArchive.py igs.algo -stninfo /data/station.info igs

# Process PPP for date range
ScanArchive.py igs.algo -ppp 2024/01/01 2024/12/31

# Process PPP with hash check
ScanArchive.py igs.all -ppp 2024/01/01 2024/01/31 hash

# Rehash solutions without recalculation
ScanArchive.py igs.algo -rehash 2024/01/01 2024/12/31

# Allow 2-hour gaps in station info
ScanArchive.py igs.lpgs -ppp -tol 2

# Export station with data
ScanArchive.py igs.algo -export

# Export metadata only
ScanArchive.py igs.algo -export true

# Import stations
ScanArchive.py dummy -import ars station1.zip station2.zip

# Get specific file
ScanArchive.py igs.algo -get 2024/01/15
```

### 5.6 IntegrityCheck.py

**Synopsis:**
```bash
IntegrityCheck.py stnlist [options]
```

**Options:**

| Option | Arguments | Description |
|--------|-----------|-------------|
| `-d` | dates | Date range filter |
| `-rinex` | fix\|report | Check RINEX file existence |
| `-rnx_count` | - | Count RINEX files per day |
| `-stnr` | - | Check RINEX vs station info |
| `-stnc` | - | Check station info consistency |
| `-stnp` | [days] | Propose station info from RINEX |
| `-g` | [days] | Show data gaps (min size) |
| `-gg` | - | Graphical gap visualization |
| `-sc` | noop\|exclude\|delete | Spatial coherence check |
| `-print` | long\|short | Print station information |
| `-r` | net.stn | Rename/merge station |
| `-del_stn` | - | Delete empty station after rename |
| `-es` | dates | Exclude solutions |
| `-del` | dates completion | Delete RINEX/solutions |
| `-np` | - | Disable parallelization |

**Examples:**
```bash
# Check and fix missing files
IntegrityCheck.py igs.all -rinex fix -d 2024/01/01 2024/12/31

# Report missing files only
IntegrityCheck.py igs.algo -rinex report -d 2024/01/01 2024/12/31

# Count files per day
IntegrityCheck.py igs.all -rnx_count -d 2024/01/01 2024/12/31

# Check receiver serial numbers
IntegrityCheck.py igs.algo -stnr -d 2024/01/01 2024/12/31

# Check station info consistency
IntegrityCheck.py igs.all -stnc

# Propose station info from RINEX
IntegrityCheck.py igs.algo -stnp 7

# Show gaps >= 10 days
IntegrityCheck.py igs.algo -g 10 -d 2020/01/01 2024/12/31

# Visual gap display
IntegrityCheck.py igs.algo -gg -d 2024/01/01 2024/12/31

# Check spatial coherence (report only)
IntegrityCheck.py igs.all -sc noop -d 2024/01/01 2024/12/31

# Check and exclude bad solutions
IntegrityCheck.py igs.algo -sc exclude -d 2024/01/01 2024/12/31

# Print short station info
IntegrityCheck.py igs.algo -print short

# Print full station info
IntegrityCheck.py igs.algo -print long

# Merge stations
IntegrityCheck.py igs.old -r igs.new -d 2024/01/01 2024/12/31 -del_stn

# Exclude solutions for date range
IntegrityCheck.py igs.algo -es 2024/01/01 2024/01/31

# Delete low-completion files
IntegrityCheck.py igs.algo -del 2024/01/01 2024/01/31 0.3
```

### 5.7 AlterETM.py

**Synopsis:**
```bash
AlterETM.py stnlist [options]
```

**Purpose:** Modify Extended Trajectory Model parameters

**Options:**

| Option | Arguments | Description |
|--------|-----------|-------------|
| `-fun` | type args... | Function type and arguments |
| `-soln` | ppp\|gamit | Solution type to affect |
| `-print` | - | Print current parameters |

**Function Types:**

**Polynomial (`p`):**
```bash
# Set polynomial terms
AlterETM.py igs.algo -fun p 2  # Constant velocity
AlterETM.py igs.algo -fun p 3  # Velocity + acceleration
```

**Jump (`j`):**
```bash
# Add jump: j {+|-} {0|1} date [relax_times]
AlterETM.py igs.algo -fun j + 0 2024/02/15       # Mechanical jump
AlterETM.py igs.algo -fun j + 1 2024/02/15 30,60 # Geophysical with relaxation

# Remove jump
AlterETM.py igs.algo -fun j - 0 2024/02/15
```

**Periodic (`q`):**
```bash
# Add periods (days): 1 yr = 365.25 days
AlterETM.py igs.algo -fun q 365.25,182.625
```

**Bulk Earthquake Removal (`t`):**
```bash
# Remove earthquakes <= magnitude from stack
AlterETM.py igs.algo -fun t 6.5 stack_name
```

**Remove Mechanical Jumps (`m`):**
```bash
# Remove all mechanical jumps
AlterETM.py igs.algo -fun m stack_name

# Remove jumps after date
AlterETM.py igs.algo -fun m stack_name 2024/01/01

# Remove jumps in date range
AlterETM.py igs.algo -fun m stack_name 2024/01/01 2024/12/31

# Remove jumps for N days after date
AlterETM.py igs.algo -fun m stack_name 2024/01/01 90
```

**Examples:**
```bash
# Print current parameters
AlterETM.py igs.algo -print

# Add earthquake jump with relaxation
AlterETM.py igs.algo -fun j + 1 2024/02/15 30,60,120

# Modify only GAMIT ETM
AlterETM.py igs.algo -fun j + 0 2024/02/15 -soln gamit

# Add annual and semi-annual signals
AlterETM.py igs.algo -fun q 365.25,182.625

# Set 3-term polynomial
AlterETM.py igs.algo -fun p 3
```

### 5.8 PlotETM.py

**Synopsis:**
```bash
PlotETM.py stnlist [options]
```

**Purpose:** Plot time series with Extended Trajectory Model fitting

**Options:**

| Option | Description |
|--------|-------------|
| `-nop` | Do not produce plots |
| `-nom` | Do not show missing data |
| `-nm` | Plot without fitting model |
| `-r` | Plot residuals |
| `-dir path` | Output directory |
| `-json 0\|1\|2` | Export to JSON (0=params, 1=ts, 2=both) |
| `-gui` | Interactive mode |
| `-rj` | Remove jumps before plotting |
| `-rp` | Remove polynomial before plotting |
| `-win dates\|N` | Time window |
| `-q model\|solution` | Query ETM values |
| `-gamit stack` | Plot GAMIT time series |
| `-lang ENG\|ESP` | Language |
| `-hist` | Plot histogram |
| `-file filename` | External data file |
| `-format fields` | File format specification |
| `-outliers` | Plot outliers panel |
| `-dj` | Plot detected jumps |
| `-vel` | Output velocity |
| `-seasonal` | Output seasonal terms |
| `-quiet` | Suppress messages |

**Examples:**
```bash
# Basic plot
PlotETM.py igs.algo

# Interactive plot
PlotETM.py igs.algo -gui

# Plot residuals
PlotETM.py igs.algo -r

# Plot without model
PlotETM.py igs.algo -nm

# Save to specific directory
PlotETM.py igs.algo -dir /output/plots/

# Export to JSON
PlotETM.py igs.algo -json 2

# Window last 365 epochs
PlotETM.py igs.algo -win 365

# Window date range
PlotETM.py igs.algo -win 2024/01/01 2024/12/31

# Query ETM at specific date
PlotETM.py igs.algo -q model 2024/06/15

# Plot GAMIT solution
PlotETM.py igs.algo -gamit stack_name

# Spanish language
PlotETM.py igs.algo -lang ESP

# Remove jumps from plot
PlotETM.py igs.algo -rj

# Output velocity
PlotETM.py igs.algo -vel

# Plot from external file
PlotETM.py igs.algo -file data.txt -format year,doy,x,y,z
```

---

## 6. Web Interface Guide

### 6.1 Accessing the Web Interface

The web interface provides an intuitive way to:
- Monitor station networks on interactive maps
- Manage station metadata
- View and edit equipment history
- Track site visits
- Check RINEX file status
- Visualize data completeness

**URL Format:**
```
https://your-server/geode/
```

### 6.2 Login and Authentication

![Login Page](path/to/login-image)

**Features:**
- Secure credential-based access
- User role management
- Session persistence

### 6.3 Map Interface

#### 6.3.1 Overview

The map interface is the primary navigation tool for GeoDE.

**Key Components:**
- **Search Bar**: Quick station lookup by code
- **Filters**: Network and country filters
- **Map Controls**: Zoom, pan, layer selection
- **Station Markers**: Color-coded status indicators

#### 6.3.2 Station Markers

**Marker Types:**
- 🟢 **Station symbol**: Station with complete, validated metadata
- 🔴 **Red Triangle**: Station requiring attention (errors/warnings)

**Marker Interaction:**
- Click marker to view station popup
- Popup shows:
  - Station code and network
  - Current status
  - Error messages (if any)
  - Link to detailed view

#### 6.3.3 Filters

**Network Filter:**
```
Filter stations by network code(s)
Example: igs, arg, rms
```

**Country Filter:**
```
Filter by ISO country code
Example: ARG, USA, BRA
```

**Combined Filters:**
- Multiple filters applied with AND logic
- Real-time marker updates
- Filter persistence across sessions

### 6.4 Station Detail View

#### 6.4.1 Information Tab

**Station Summary:**
- Station Code (4-letter identifier)
- Network Code
- Country Code (ISO 3166)
- Geodetic Coordinates (Latitude, Longitude, Height)
- Cartesian Coordinates (X, Y, Z)

**Status Information:**
- Station Type (Campaign, Continuous, etc.)
- Monument Name and Photo
- Current Status (Active Online, Active Offline, Destroyed)
- Battery Status
- Communications Status

**Data Availability:**
- First RINEX observation date
- Last RINEX observation date
- Last gaps update timestamp

**Files:**
- Navigation file (KMZ with route to station)
- Attached documents
- Site photos

#### 6.4.2 Metadata Tab

**Equipment Information:**
- Current receiver model and serial
- Current antenna model and serial
- Antenna height and height code
- Radome code
- Installation dates

**Coordinates:**
- Geodetic (Lat, Lon, Height)
- Cartesian (X, Y, Z)
- Reference frame information

**Edit Functionality:**
- Click "Edit" button to modify
- Web form for data entry
- Validation before saving

### 6.5 Equipment History

#### 6.5.1 Equipment Table

**Columns:**
- **RX Code**: Receiver model
- **RX Serial**: Receiver serial number
- **RX FW**: Firmware version
- **ANT Code**: Antenna model
- **ANT Serial**: Antenna serial number
- **Height**: Antenna height
- **North/East**: Antenna offsets
- **HC**: Height code (e.g., DHARP)
- **RAD**: Radome code
- **Date Start**: Equipment installation date
- **Date End**: Equipment removal date
- **Comments**: Additional notes

**Actions:**
- ✏️ **Modify**: Edit existing record
- ➕ **Add**: Create new equipment record
- 🗑️ **Delete**: Remove record (with confirmation)

**Pagination:**
- Navigate through multiple pages
- Configurable items per page

#### 6.5.2 Add/Edit Equipment

**Form Fields:**
- Receiver Code (must exist in database)
- Receiver Serial
- Receiver Firmware
- Antenna Code (must exist in database)
- Antenna Serial
- Antenna Height
- Antenna North Offset
- Antenna East Offset
- Height Code
- Radome Code
- Date Start (DOY or Gregorian)
- Date End (DOY or Gregorian)
- Comments (optional)

**Date Format Toggle:**
- ☑️ DOY: Year and Day-of-Year
- ☐ DOY: Gregorian calendar picker

**Validation:**
- Equipment codes verified against database
- Date range consistency checked
- Overlap detection
- Serial number format validation

### 6.6 Visits Management

#### 6.6.1 Visits List

Each visit entry displays:
- Visit date
- Associated campaign
- Site photos
- Equipment condition
- Installation notes

**Actions:**
- ➕ **Add Visit**: Create new visit record
- 🗑️ **Delete**: Remove visit (with confirmation)

#### 6.6.2 Add Visit Form

**Required Fields:**
- **Date**: Visit date (mm/dd/yyyy)
- **Campaign**: Associated project/campaign
- **People**: Personnel involved

**Optional Fields:**
- **Log Sheet File**: PDF documentation (Browse to upload)
- **Navigation File**: KMZ/KML route file
- **Comments**: Additional notes

**Photo Upload:**
- Multiple photos supported
- Automatic thumbnail generation
- Photo metadata extraction

### 6.7 RINEX Management

#### 6.7.1 RINEX Table

**Status Indicators:**

**Row Colors:**
- **Green**: Complete metadata, no errors
- **Red**: Critical errors requiring attention
- **Light Red**: Errors but < 12 hours data (won't process)
- **Gray**: < 12 hours of data

**Icon Indicators:**
- ⚠️ **Yellow**: Minor inconsistencies (informational)
- ❗ **Red**: Critical errors (must fix before processing)

**Columns:**
- Date (Year, Month, Day, DOY)
- Start Time
- End Time
- Receiver (Type, Serial, Firmware)
- Antenna (Type, Serial, Radome)
- Filename
- Interval (seconds)
- Height
- Completion (fraction of 24 hours)

#### 6.7.2 RINEX Actions

**Action Buttons:**
- **V** (View): Display associated station information
- **E** (Edit): Modify station information for this file
- **+** (Add): Create new station information record
- **↥** (Extend Up): Extend previous record to cover this date
- **↧** (Extend Down): Extend next record to cover this date

**Batch Actions:**
- Select multiple files (checkboxes)
- Apply action to selection
- Progress indicator

#### 6.7.3 RINEX Filters

**Filter Window:**

**Time Filters:**
- F Year: Fractional year
- Year: Calendar year
- Observation Time: Date/time range
- DOY: Day of year

**Equipment Filters:**
- Antenna Dome: Specific dome code
- Antenna Offset: Offset range
- Antenna Serial: Serial number
- Antenna Type: Model code
- Receiver FW: Firmware version
- Receiver Serial: Serial number
- Receiver Type: Model code

**Data Filters:**
- Completion: Min/max completion fraction
- Interval: Sampling interval (seconds)

**Filter Actions:**
- **Apply Filters**: Execute filter
- **Clean Filters**: Reset all filters

**Show Errors Only:**
- ☑️ Checkbox to display only problematic files
- Useful for focused error resolution

### 6.8 People Management

**Purpose:** Track personnel associated with stations

**Fields:**
- Name
- Organization
- Role
- Contact information
- Associated stations/visits

**Uses:**
- Site visit documentation
- Responsibility tracking
- Contact management

### 6.9 Navigation and Search

#### 6.9.1 Search Functionality

**Search Bar:**
```
Search for station code
```

**Features:**
- Real-time search
- Autocomplete suggestions
- Wildcard support
- Case-insensitive

#### 6.9.2 Station Lists Sidebar

**Lists Available:**
- All Stations
- Stations by Network
- Stations by Country
- Stations with Errors
- Recently Updated
- Custom Lists

**List Actions:**
- Create custom lists
- Export lists
- Share lists

---

## 7. Error Handling and Troubleshooting

### 7.1 Common Error Messages

#### 7.1.1 ArchiveService Errors

**"Could not determine coordinate"**
```
Error: Both PPP and sh_rx2apr failed to obtain a coordinate
```

**Causes:**
- Missing or corrupted broadcast ephemeris
- Insufficient observation data
- Invalid RINEX header coordinates

**Solutions:**
1. Check orbit file availability:
```bash
   ls /orbits/brdc/2024/brdc0010.24n
```
2. Verify RINEX has valid header coordinates
3. Manually add approximate coordinates to header

**"Unreasonable geodetic height"**
```
Error: unreasonable geodetic height (15234.567)
```

**Causes:**
- Bad PPP solution
- Corrupted RINEX data
- Wrong station location in header

**Solutions:**
1. Check RINEX header coordinates
2. Verify station is not in excluded list
3. Re-download RINEX file

**"Station matches different coordinate"**
```
Warning: amc20010.24d matches igs.amc2 (distance = 3.4 m) 
but filename indicates amc0
```

**Causes:**
- Wrong station code in filename
- File belongs to different station
- Nearby stations with similar codes

**Solutions:**
1. Verify which station file truly belongs to
2. Rename file:
```bash
   cd data_in_retry/coord_conflicts/2024/001/
   mv amc00010.24d amc20010.24d
```
3. Or create new station if legitimate:
```sql
   INSERT INTO stations ("NetworkCode", "StationCode", 
       "auto_x", "auto_y", "auto_z") 
   VALUES ('???', 'amc0', x, y, z);
```

**"Multiday RINEX file"**
```
Warning: algo0010.24d was a multi-day rinex file
```

**Causes:**
- RINEX spans multiple days
- Incorrect session duration

**Solutions:**
- Files automatically split and moved to retry folder
- Wait for next ArchiveService run to process
- If recurring, check data source settings

#### 7.1.2 ScanArchive Errors

**"Station information hash mismatch"**
```
Error: Hash value does not match Station Information hash
```

**Causes:**
- Station metadata changed after PPP
- Equipment record modified
- Reference frame update

**Solutions:**
```bash
# Recalculate PPP
ScanArchive.py network.station -ppp 2024/01/01 2024/12/31 hash
```

**"No station information for date"**
```
Error: No station information record found for 2024 045
```

**Causes:**
- Missing metadata for observation period
- Gap in station information records
- Station information DateEnd before RINEX date

**Solutions:**
1. Check station information:
```bash
   IntegrityCheck.py network.station -print short
```
2. Add missing record via web interface
3. Use tolerance for campaign data:
```bash
   ScanArchive.py network.station -ppp -tol 24
```

**"Height code not found"**
```
Error: pyStationInfoHeightCodeNotFound
```

**Causes:**
- Invalid height code in station information
- Height code not in gamit_htc table
- Missing antenna calibration

**Solutions:**
1. Verify height code:
```sql
   SELECT * FROM gamit_htc WHERE "HeightCode" = 'DHARP';
```
2. Update station information with valid code
3. Add missing height code to gamit_htc table

#### 7.1.3 IntegrityCheck Errors

**"File not found in archive"**
```
Warning: File algo0010.24d.gz exists in database but 
not found in archive
```

**Causes:**
- File manually deleted
- Disk corruption
- Incorrect archive path

**Solutions:**
```bash
# Fix mode removes orphaned records
IntegrityCheck.py network.station -rinex fix -d 2024/01/01 2024/12/31
```

**"PPP solution spatial incoherence"**
```
Warning: Solution for igs.algo does not match its 
station code. Best match: igs.alg2
```

**Causes:**
- Swapped station codes
- Wrong file processed for station
- Gross coordinate error

**Solutions:**
```bash
# Exclude solution
IntegrityCheck.py network.station -sc exclude -d 2024/01/01

# Or delete and reprocess
IntegrityCheck.py network.station -sc delete -d 2024/01/01
```

### 7.2 Database Issues

#### 7.2.1 Connection Errors

**"Could not connect to database"**

**Solutions:**
1. Check PostgreSQL is running:
```bash
   systemctl status postgresql
```
2. Verify connection parameters in `gnss_data.cfg`
3. Test connection:
```bash
   psql -h hostname -U username -d database
```
4. Check firewall rules

**"Too many connections"**

**Solutions:**
1. Check current connections:
```sql
   SELECT count(*) FROM pg_stat_activity;
```
2. Increase max_connections in postgresql.conf
3. Close idle connections
4. Reduce parallel processing nodes

#### 7.2.2 Lock Issues

**"Station locked"**

**Causes:**
- File in locks table from previous run
- Station has temporary network code (???)

**Solutions:**
```sql
-- View locks
SELECT * FROM locks;

-- Remove specific lock
DELETE FROM locks WHERE filename = 'file.crz';

-- Remove all locks (after fixing metadata)
DELETE FROM locks;
```

#### 7.2.3 Transaction Errors

**"Database transaction failed"**

**Solutions:**
1. Check database logs:
```bash
   tail -f /var/log/postgresql/postgresql-*.log
```
2. Verify disk space
3. Check for deadlocks:
```sql
   SELECT * FROM pg_locks WHERE granted = false;
```

### 7.3 Processing Issues

#### 7.3.1 PPP Failures

**"PPP execution failed"**

**Causes:**
- Missing orbit files
- Corrupted RINEX data
- Insufficient observations

**Solutions:**
1. Check orbit availability:
```bash   ls /orbits/sp3/2295/*.sp3
   ls /orbits/sp3/2295/*.clk
```

Verify RINEX quality:
```
bash   gfzrnx -finp file.24d
```

Check PPP log in events table:
```sql   SELECT * FROM events 
   WHERE "EventType" = 'error' 
   AND "Description" LIKE '%PPP%'
   ORDER BY "EventDate" DESC;
```
"Clock interpolation error"
Causes:

Missing clock files
Clock file time span mismatch
Corrupted clock data

Solutions:

Download missing products:
```bash   wget https://cddis.nasa.gov/archive/gnss/products/2295/
```

Check clock file coverage:
```bash   grep 'AS ' clk_file.clk | head -1
   grep 'AS ' clk_file.clk | tail -1
```
   
7.3.2 GAMIT Processing
"sh_rx2apr failed"
Causes:

Missing broadcast ephemeris
Invalid RINEX format
Insufficient satellites

Solutions:

Verify broadcast file:
```
bash   ls /orbits/brdc/2024/brdc0010.24n
```
Check RINEX epochs:
```
bash   grep '> 2024' file.24o | wc -l
```
7.3.3 Parallelization Issues
"Node not responding"
Causes:

Network connectivity issues
Node overloaded
SSH authentication failure

Solutions:

Test node connectivity:
```
bash   ssh user@node1 hostname
```

Check node load:
```
bash   ssh user@node1 uptime
```
Verify SSH keys configured
Reduce parallel jobs:
```
ini   # In gnss_data.cfg
   node_list = node1,node2  # Remove problematic nodes
```
"Cluster creation failed"
Causes:

Dispy not installed on nodes
Firewall blocking ports
Python version mismatch

Solutions:

Install dispy on all nodes:
```
bash   pip install dispy
```

Open required ports (51347-51350)
Verify Python 3.10 on all nodes

7.4 File System Issues
7.4.1 Disk Space
"No space left on device"
Solutions:

Check disk usage:
```
bash   df -h /archive
   df -h /repository
```
Clean retry folders:
```
bash   find /repository/data_in_retry -type f -mtime +30 -delete
```
Clean rejected files:
```
bash   find /repository/data_rejected -type f -mtime +90 -delete
```
Archive old data to tape/cloud storage

7.4.2 Permission Issues
"Permission denied"
Causes:

Incorrect file ownership
Missing write permissions
SELinux restrictions

Solutions:

Check ownership:
```
bash   ls -la /archive
   ls -la /repository
```
Fix permissions:
```
bash   chown -R username:group /archive
   chmod -R 755 /archive
```
Check SELinux:
```
bash   getenforce
   sestatus
```
7.4.3 Archive Structure
"Archive path not found"
Causes:

Incorrect path in configuration
Missing directory structure
Mount point not mounted

Solutions:

Verify archive_path in config
Create directory structure:
```
bash   mkdir -p /archive/{network}/{station}/{year}/{doy}
```
Check mounts:

bash   mount | grep archive
7.5 Web Interface Issues
7.5.1 Login Problems
"Authentication failed"
Solutions:

Reset password via admin
Check database user table
Clear browser cookies
Verify LDAP/AD configuration (if used)

7.5.2 Map Not Loading
"Map tiles not displaying"
Causes:

JavaScript errors
Network connectivity
OpenStreetMap API issues

Solutions:

Check browser console (F12)
Verify internet connectivity
Clear browser cache
Try different browser

7.5.3 Station Data Not Updating
"Changes not reflected"
Causes:

Browser cache
Database replication lag
Transaction not committed

Solutions:

Hard refresh (Ctrl+F5)
Check recent events:
```
sql   SELECT * FROM events 
   ORDER BY "EventDate" DESC LIMIT 20;
```
Verify database connection in web config

7.6 Data Quality Issues
7.6.1 Inconsistent Metadata
"RINEX header doesn't match database"
Diagnosis:
```
bash# Compare RINEX header to station info
IntegrityCheck.py network.station -stnr -d 2024/01/01 2024/12/31
```
Solutions:

Update station information
Fix RINEX header manually
Re-download from different source

7.6.2 Coordinate Outliers
"PPP coordinate unrealistic"
Diagnosis:
```
bash# Check spatial coherence
IntegrityCheck.py network.station -sc noop -d 2024/01/01 2024/12/31
```

# Plot time series
PlotETM.py network.station -gui
Solutions:

Exclude outlier:

bash   IntegrityCheck.py network.station -es 2024/045 2024/045

Recalculate with stricter criteria
Verify RINEX file quality

7.6.3 Time Series Gaps
"Missing observations"
Diagnosis:
```
bash# Visualize gaps
IntegrityCheck.py network.station -gg -d 2020/01/01 2024/12/31
```

# Identify gap periods
IntegrityCheck.py network.station -g 5 -d 2020/01/01 2024/12/31
Solutions:

Check data sources:
```
sql   SELECT * FROM sources_stations 
   WHERE "NetworkCode" = 'net' AND "StationCode" = 'stn';
```
Download missing data:

```
bash   DownloadSources.py network.station -date 2024/045 2024/050
```

Check station status (decommissioned, relocated)

7.7 Log File Analysis
7.7.1 Error Logs
ArchiveService Errors:
bash# View recent errors
tail -100 errors_ArchiveService.log

# Search for specific station
grep "igs.algo" errors_ArchiveService.log

# Count error types
grep -c "pyRinexException" errors_ArchiveService.log
ScanArchive Errors:
```
bash# View recent errors
tail -100 errors_pyScanArchive.log
```

# Identify PPP failures
grep "PPP" errors_pyScanArchive.log | less
7.7.2 Event Database
Query Recent Events:
```sql-- All errors in last 24 hours
SELECT * FROM events 
WHERE "EventType" = 'error' 
  AND "EventDate" >= NOW() - INTERVAL '1 day'
ORDER BY "EventDate" DESC;

-- Warnings for specific station
SELECT * FROM events 
WHERE "NetworkCode" = 'igs' 
  AND "StationCode" = 'algo'
  AND "EventType" = 'warn'
ORDER BY "EventDate" DESC
LIMIT 50;

-- Count events by type
SELECT "EventType", COUNT(*) 
FROM events 
WHERE "EventDate" >= NOW() - INTERVAL '7 days'
GROUP BY "EventType";
7.7.3 Execution Logs
Track Script Runs:
sql-- Recent executions
SELECT * FROM executions 
ORDER BY exec_date DESC 
LIMIT 20;

-- Execution frequency
SELECT script, COUNT(*), 
       MAX(exec_date) as last_run
FROM executions 
GROUP BY script;
```
8. Database Management
8.1 Database Schema Overview
8.1.1 Core Tables
stations

NetworkCode, StationCode (primary key)
Coordinates (X, Y, Z, Lat, Lon, Height)
OTL coefficients
Country code, marker, dome

rinex

NetworkCode, StationCode, ObservationYear, ObservationDOY
Filename, Interval, Completion
Observation times
File metadata

rinex_proc (view)

Extended rinex table with receiver/antenna information
Used for processing queries

ppp_soln

PPP solutions for each station-day
Coordinates (X, Y, Z)
Uncertainties
Reference frame
Hash for metadata tracking

gamit_soln

GAMIT processing solutions
Session-based results
Residuals and statistics

station_info

Equipment history
Receiver and antenna records
Installation dates
Height codes and offsets

8.1.2 Metadata Tables
antennas

Antenna codes
IGS calibrations
Model specifications

receivers

Receiver codes
Manufacturer information
Firmware versions

gamit_htc

Height code definitions
Horizontal and vertical offsets
Antenna-specific calibrations

networks

Network codes and names
Network metadata

8.1.3 Download Management
sources_servers

Server connection information
Protocol, hostname, credentials
Default paths and formats

sources_stations

Station-server associations
Priority ordering
Path and format overrides

sources_formats

Format script definitions
Conversion specifications

8.1.4 Processing Tables
locks

Locked files (temporary stations)
Prevents reprocessing

events

Processing event logs
Error, warning, info messages
Timestamped history

executions

Script execution tracking
Run timestamps

etms / etm_params

Extended Trajectory Models
Polynomial, jump, periodic parameters

stacks

GAMIT processing stacks
Stack definitions and members

8.2 Common Database Queries
8.2.1 Station Queries
List all stations:
```
sqlSELECT "NetworkCode", "StationCode", 
       lat, lon, height, country_code
FROM stations
ORDER BY "NetworkCode", "StationCode";
Find stations by country:
sqlSELECT * FROM stations 
WHERE country_code = 'ARG'
ORDER BY "StationCode";
Stations missing OTL:
sqlSELECT "NetworkCode", "StationCode" 
FROM stations 
WHERE "Harpos_coeff_otl" IS NULL
   OR auto_x IS NULL;
```
Count stations by network:
```sql
SELECT "NetworkCode", COUNT(*) as station_count
FROM stations
GROUP BY "NetworkCode"
ORDER BY station_count DESC;
8.2.2 RINEX Queries
RINEX file count:
sqlSELECT COUNT(*) as total_files,
       COUNT(DISTINCT "NetworkCode" || '.' || "StationCode") as unique_stations,
       MIN("ObservationSTime") as first_obs,
       MAX("ObservationSTime") as last_obs
FROM rinex;
Files per station:
sqlSELECT "NetworkCode", "StationCode", COUNT(*) as file_count
FROM rinex
WHERE "ObservationYear" = 2024
GROUP BY "NetworkCode", "StationCode"
ORDER BY file_count DESC
LIMIT 20;```

Low completion files:
```sql
SELECT "NetworkCode", "StationCode", 
       "ObservationYear", "ObservationDOY",
       "Completion", "Filename"
FROM rinex
WHERE "Completion" < 0.5
  AND "ObservationYear" = 2024
ORDER BY "Completion";
```
Find gaps for station:
```sql
WITH dates AS (
  SELECT generate_series(
    '2024-01-01'::date, 
    '2024-12-31'::date, 
    '1 day'::interval
  )::date AS date
),
station_dates AS (
  SELECT "ObservationSTime"::date as date
  FROM rinex
  WHERE "NetworkCode" = 'igs' AND "StationCode" = 'algo'
)
SELECT dates.date as missing_date
FROM dates
LEFT JOIN station_dates ON dates.date = station_dates.date
WHERE station_dates.date IS NULL
ORDER BY dates.date;
```
8.2.3 PPP Queries
Stations without PPP:
```sql
SELECT DISTINCT r."NetworkCode", r."StationCode"
FROM rinex r
LEFT JOIN ppp_soln p ON 
    r."NetworkCode" = p."NetworkCode" AND
    r."StationCode" = p."StationCode" AND
    r."ObservationYear" = p."Year" AND
    r."ObservationDOY" = p."DOY"
WHERE p."NetworkCode" IS NULL
  AND r."Completion" >= 0.5
ORDER BY r."NetworkCode", r."StationCode";
```
PPP solution statistics:
```sql
SELECT "NetworkCode", "StationCode",
       COUNT(*) as solution_count,
       AVG("Sigma_X") as avg_sigma_x,
       AVG("Sigma_Y") as avg_sigma_y,
       AVG("Sigma_Z") as avg_sigma_z
FROM ppp_soln
WHERE "Year" = 2024
GROUP BY "NetworkCode", "StationCode"
HAVING COUNT(*) > 300
ORDER BY avg_sigma_x DESC;
```
Hash mismatches:
```sql
SELECT p."NetworkCode", p."StationCode", p."Year", p."DOY"
FROM ppp_soln p
JOIN rinex_proc r ON
    p."NetworkCode" = r."NetworkCode" AND
    p."StationCode" = r."StationCode" AND
    p."Year" = r."ObservationYear" AND
    p."DOY" = r."ObservationDOY"
WHERE p.hash != (
    SELECT hash FROM station_info 
    WHERE "NetworkCode" = p."NetworkCode" 
      AND "StationCode" = p."StationCode"
      AND "DateStart" <= (p."Year" || ' ' || p."DOY")::text
      AND "DateEnd" >= (p."Year" || ' ' || p."DOY")::text
    LIMIT 1
);
8.2.4 Station Information Queries
Equipment history:
sqlSELECT "DateStart", "DateEnd",
       "ReceiverCode", "ReceiverSerial",
       "AntennaCode", "AntennaSerial",
       "HeightCode", "RadomeCode"
FROM station_info
WHERE "NetworkCode" = 'igs' AND "StationCode" = 'algo'
ORDER BY "DateStart";
```
Current equipment:
```sql
SELECT s."NetworkCode", s."StationCode",
       si."ReceiverCode", si."AntennaCode"
FROM stations s
JOIN station_info si ON 
    s."NetworkCode" = si."NetworkCode" AND
    s."StationCode" = si."StationCode"
WHERE si."DateEnd"::text = '9999 999'
ORDER BY s."NetworkCode", s."StationCode";
```
Antenna usage statistics:
```sql
SELECT "AntennaCode", COUNT(*) as usage_count,
       COUNT(DISTINCT "NetworkCode" || '.' || "StationCode") as station_count
FROM station_info
GROUP BY "AntennaCode"
ORDER BY usage_count DESC
LIMIT 20;
```
Stations with overlapping metadata:
```sql
SELECT a."NetworkCode", a."StationCode",
       a."DateStart" as start1, a."DateEnd" as end1,
       b."DateStart" as start2, b."DateEnd" as end2
FROM station_info a
JOIN station_info b ON
    a."NetworkCode" = b."NetworkCode" AND
    a."StationCode" = b."StationCode" AND
    a.id != b.id AND
    a."DateStart" < b."DateEnd" AND
    a."DateEnd" > b."DateStart"
ORDER BY a."NetworkCode", a."StationCode", a."DateStart";
```
8.2.5 Event Queries
Recent errors by station:
```sql
SELECT "NetworkCode", "StationCode", "Year", "DOY",
       LEFT("Description", 100) as error_summary
FROM events
WHERE "EventType" = 'error'
  AND "EventDate" >= NOW() - INTERVAL '7 days'
ORDER BY "EventDate" DESC
LIMIT 50;
```
Error frequency:
```sql
SELECT DATE("EventDate") as event_day,
       "EventType",
       COUNT(*) as event_count
FROM events
WHERE "EventDate" >= NOW() - INTERVAL '30 days'
GROUP BY DATE("EventDate"), "EventType"
ORDER BY event_day DESC, "EventType";
```
Multiday file events:
```sql
SELECT * FROM events
WHERE "Description" LIKE '%multi-day%'
ORDER BY "EventDate" DESC
LIMIT 50;
```
8.3 Database Maintenance
8.3.1 Vacuum and Analyze
Regular maintenance:
```sql
-- Vacuum entire database
VACUUM ANALYZE;

-- Vacuum specific tables
VACUUM ANALYZE rinex;
VACUUM ANALYZE ppp_soln;
VACUUM ANALYZE events;

-- Full vacuum (requires exclusive lock)
VACUUM FULL events;
```
Automated vacuum:
```sql
-- Check autovacuum settings
SELECT name, setting FROM pg_settings 
WHERE name LIKE 'autovacuum%';

-- Enable autovacuum
ALTER TABLE rinex SET (autovacuum_enabled = true);
```
8.3.2 Index Maintenance
Check index usage:
```sql
SELECT schemaname, tablename, indexname,
       idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan;
```
Rebuild indexes:
```sql
REINDEX TABLE rinex;
REINDEX TABLE ppp_soln;
REINDEX TABLE station_info;
```
8.3.3 Backup and Recovery
Database backup:
```bash# Full backup
pg_dump -h hostname -U username -d gnss_data -F c -f backup_$(date +%Y%m%d).dump

# Compressed backup
pg_dump -h hostname -U username -d gnss_data | gzip > backup_$(date +%Y%m%d).sql.gz

# Schema only
pg_dump -h hostname -U username -d gnss_data --schema-only > schema.sql

# Data only
pg_dump -h hostname -U username -d gnss_data --data-only > data.sql
```
Table-specific backup:
```bash# Backup specific tables
pg_dump -h hostname -U username -d gnss_data -t stations -t rinex > critical_tables.sql

# Backup events (for archival)
pg_dump -h hostname -U username -d gnss_data -t events \
    --where="\"EventDate\" < '2023-01-01'" > events_archive_2022.sql
```
Restore:
```bash# Restore full backup
pg_restore -h hostname -U username -d gnss_data -c backup_20240101.dump

# Restore from SQL
psql -h hostname -U username -d gnss_data < backup.sql

# Restore specific table
pg_restore -h hostname -U username -d gnss_data -t stations backup.dump
```
8.3.4 Performance Tuning
Connection pooling:

```ini# postgresql.conf
max_connections = 200
shared_buffers = 4GB
effective_cache_size = 12GB
maintenance_work_mem = 1GB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 20MB
min_wal_size = 1GB
max_wal_size = 4GB
```
Query optimization:
```sql-- Analyze query plan
EXPLAIN ANALYZE
SELECT * FROM rinex_proc
WHERE "NetworkCode" = 'igs' AND "ObservationYear" = 2024;

-- Create useful indexes
CREATE INDEX idx_rinex_network_year ON rinex("NetworkCode", "ObservationYear");
CREATE INDEX idx_ppp_station_year ON ppp_soln("NetworkCode", "StationCode", "Year");
CREATE INDEX idx_events_date_type ON events("EventDate", "EventType");
```
8.3.5 Data Archival
Archive old events:
sql-- Create archive table
CREATE TABLE events_archive (LIKE events INCLUDING ALL);

-- Move old events
INSERT INTO events_archive
SELECT * FROM events
WHERE "EventDate" < '2022-01-01';

DELETE FROM events
WHERE "EventDate" < '2022-01-01';

-- Vacuum to reclaim space
VACUUM FULL events;
Partition large tables:
sql-- Create partitioned table
CREATE TABLE rinex_partitioned (
    LIKE rinex INCLUDING ALL
) PARTITION BY RANGE ("ObservationYear");

-- Create yearly partitions
CREATE TABLE rinex_y2023 PARTITION OF rinex_partitioned
    FOR VALUES FROM (2023) TO (2024);
CREATE TABLE rinex_y2024 PARTITION OF rinex_partitioned
    FOR VALUES FROM (2024) TO (2025);

-- Migrate data
INSERT INTO rinex_partitioned SELECT * FROM rinex;

9. Best Practices
9.1 Data Processing Workflow
9.1.1 Daily Operations
Recommended daily script:
```bash#!/bin/bash
# Daily GeoDE processing script

LOG_DIR="/var/log/geode"
DATE=$(date +%Y%m%d)

# 1. Download new data
echo "$(date): Starting downloads" >> $LOG_DIR/daily_$DATE.log
DownloadSources.py all -win 3 2>&1 | tee -a $LOG_DIR/download_$DATE.log

# 2. Process repository
echo "$(date): Processing repository" >> $LOG_DIR/daily_$DATE.log
ArchiveService.py 2>&1 | tee -a $LOG_DIR/archive_$DATE.log

# 3. Run PPP on new files
echo "$(date): Running PPP" >> $LOG_DIR/daily_$DATE.log
ScanArchive.py all -ppp $(date -d '3 days ago' +%Y/%m/%d) $(date +%Y/%m/%d) \
    2>&1 | tee -a $LOG_DIR/ppp_$DATE.log

# 4. Check for errors
ERROR_COUNT=$(grep -c "EventType.*error" $LOG_DIR/*_$DATE.log)
if [ $ERROR_COUNT -gt 0 ]; then
    echo "$(date): $ERROR_COUNT errors detected" | mail -s "GeoDE Errors" admin@example.com
fi

echo "$(date): Daily processing complete" >> $LOG_DIR/daily_$DATE.log
```
9.1.2 Weekly Maintenance
Weekly integrity checks:
```bash#!/bin/bash
# Weekly integrity check script

WEEK_AGO=$(date -d '7 days ago' +%Y/%m/%d)
TODAY=$(date +%Y/%m/%d)

# Check RINEX file existence
IntegrityCheck.py all -rinex report -d $WEEK_AGO $TODAY > weekly_rinex_check.txt

# Verify station information
IntegrityCheck.py all -stnc > weekly_stninfo_check.txt

# Check spatial coherence
IntegrityCheck.py all -sc noop -d $WEEK_AGO $TODAY > weekly_spatial_check.txt

# Email results
if [ -s weekly_rinex_check.txt ] || \
   [ -s weekly_stninfo_check.txt ] || \
   [ -s weekly_spatial_check.txt ]; then
    cat weekly_*.txt | mail -s "Weekly GeoDE Integrity Report" admin@example.com
fi
```
9.1.3 Monthly Tasks
Monthly activities:

Review and clean retry folders
Archive old event logs
Update equipment tables (new receivers/antennas)
Verify backup integrity
Review processing statistics
Update documentation

Monthly cleanup script:
```bash#!/bin/bash
# Monthly cleanup

# Archive events older than 1 year
psql -d gnss_data -c "
    INSERT INTO events_archive 
    SELECT * FROM events 
    WHERE \"EventDate\" < NOW() - INTERVAL '1 year';
    
    DELETE FROM events 
    WHERE \"EventDate\" < NOW() - INTERVAL '1 year';
"

# Clean old retry files (> 90 days)
find /repository/data_in_retry -type f -mtime +90 -delete
find /repository/data_rejected -type f -mtime +90 -delete

# Vacuum database
psql -d gnss_data -c "VACUUM ANALYZE;"

# Generate monthly statistics
psql -d gnss_data -c "
    SELECT 
        DATE_TRUNC('month', \"EventDate\") as month,
        \"EventType\",
        COUNT(*) as count
    FROM events
    WHERE \"EventDate\" >= NOW() - INTERVAL '1 month'
    GROUP BY month, \"EventType\"
    ORDER BY month, \"EventType\";
" > monthly_stats_$(date +%Y%m).txt
```
9.2 Station Management
9.2.1 Adding New Stations

**We recommend using GeoDE Desktop for the following operations, but SQL commands can also be used.**

Procedure:

Create network (if new):

```sql   INSERT INTO networks ("NetworkCode", "NetworkName")
   VALUES ('ars', 'My Network Name');
```
Add station record:

```sql   INSERT INTO stations ("NetworkCode", "StationCode")
   VALUES ('ars', 'stn1');
```
Configure download sources:

```sql   INSERT INTO sources_stations 
   ("NetworkCode", "StationCode", try_order, server_id)
   VALUES ('ars', 'stn1', 1, 1);
```
Download initial data:

```bash   DownloadSources.py ars.stn1 -date 2024/01/01 2024/12/31
```

Process repository:

```bash   ArchiveService.py
```

Add station information via web interface
Calculate OTL:
```
bash   ScanArchive.py ars.stn1 -otl
```

Run initial PPP:
```
bash   ScanArchive.py ars.stn1 -ppp
```
9.2.2 Decommissioning Stations
Procedure:

Update station status in web interface
Stop downloads (optional):

```sql   DELETE FROM sources_stations 
   WHERE "NetworkCode" = 'net' AND "StationCode" = 'stn';
```
Document decommission in events:

```sql   INSERT INTO events ("NetworkCode", "StationCode", "EventType", "Description")
   VALUES ('net', 'stn', 'info', 'Station decommissioned on 2024-12-31');
```

DO NOT delete station record (preserves historical data)

9.2.3 Station Merges
When to merge:

Station code changed
Station relocated within 100m
Network reorganization
Duplicate stations discovered

Merge procedure:

```bash# 1. Verify stations exist
IntegrityCheck.py old.stn -print short
IntegrityCheck.py new.stn -print short

# 2. Check for conflicts
IntegrityCheck.py old.stn -stnc
IntegrityCheck.py new.stn -stnc

# 3. Backup data
pg_dump -t station_info -t rinex -t ppp_soln \
    --where="\"NetworkCode\"='old' AND \"StationCode\"='stn'" \
    > backup_old_stn.sql

# 4. Perform merge
IntegrityCheck.py old.stn -r new.stn -d 2020/01/01 2024/12/31 -del_stn

# 5. Verify merge
IntegrityCheck.py new.stn -rinex report
IntegrityCheck.py new.stn -stnc
```

9.3 Quality Control
9.3.1 Automated QC Checks
Daily QC script:
```
bash#!/bin/bash
# Automated quality control

QC_DIR="/var/log/geode/qc"
DATE=$(date +%Y%m%d)

# Check for spatial outliers
IntegrityCheck.py all -sc noop -d $(date -d '1 day ago' +%Y/%m/%d) $(date +%Y/%m/%d) \
    > $QC_DIR/spatial_$DATE.txt

# Identify low-completion files
psql -d gnss_data -t -c "
    SELECT \"NetworkCode\" || '.' || \"StationCode\", 
           \"ObservationYear\", \"ObservationDOY\", \"Completion\"
    FROM rinex
    WHERE \"Completion\" < 0.7 
      AND \"ObservationSTime\" >= NOW() - INTERVAL '1 day'
" > $QC_DIR/lowcomp_$DATE.txt

# Check for missing PPP
psql -d gnss_data -t -c "
    SELECT r.\"NetworkCode\" || '.' || r.\"StationCode\",
           r.\"ObservationYear\", r.\"ObservationDOY\"
    FROM rinex r
    LEFT JOIN ppp_soln p ON 
        r.\"NetworkCode\" = p.\"NetworkCode\" AND
        r.\"StationCode\" = p.\"StationCode\" AND
        r.\"ObservationYear\" = p.\"Year\" AND
        r.\"ObservationDOY\" = p.\"DOY\"
    WHERE p.\"NetworkCode\" IS NULL
      AND r.\"Completion\" >= 0.5
      AND r.\"ObservationSTime\" >= NOW() - INTERVAL '1 day'
" > $QC_DIR/missing_ppp_$DATE.txt

# Email if issues found
if [ -s $QC_DIR/spatial_$DATE.txt ] || \
   [ -s $QC_DIR/lowcomp_$DATE.txt ] || \
   [ -s $QC_DIR/missing_ppp_$DATE.txt ]; then
    {
        echo "Quality Control Report - $DATE"
        echo "================================"
        echo ""
        echo "Spatial Outliers:"
        cat $QC_DIR/spatial_$DATE.txt
        echo ""
        echo "Low Completion Files:"
        cat $QC_DIR/lowcomp_$DATE.txt
        echo ""
        echo "Missing PPP Solutions:"
        cat $QC_DIR/missing_ppp_$DATE.txt
    } | mail -s "GeoDE QC Report $DATE" qc@example.com
fi
```
9.3.2 Manual QC Procedures
Monthly QC checklist:

 Review error logs
 Check station information completeness
 Verify coordinate time series for outliers
 Inspect data gap patterns
 Review hash mismatch cases
 Validate new equipment entries
 Check download source availability
 Verify backup completion

QC report generation:
```
bash# Generate comprehensive QC report
{
    echo "=== GeoDE Quality Control Report ==="
    echo "Generated: $(date)"
    echo ""
    
    echo "=== Database Statistics ==="
    psql -d gnss_data -c "
        SELECT 
            (SELECT COUNT(*) FROM stations) as total_stations,
            (SELECT COUNT(*) FROM rinex) as total_rinex,
            (SELECT COUNT(*) FROM ppp_soln) as total_ppp,
            (SELECT COUNT(DISTINCT \"NetworkCode\" || '.' || \"StationCode\") 
             FROM rinex WHERE \"ObservationYear\" = $(date +%Y)) as active_2024;
    "
    
    echo ""
    echo "=== Recent Errors (Last 7 Days) ==="
    psql -d gnss_data -c "
        SELECT \"EventType\", COUNT(*) 
        FROM events 
        WHERE \"EventDate\" >= NOW() - INTERVAL '7 days'
        GROUP BY \"EventType\"
        ORDER BY COUNT(*) DESC;
    "
    
    echo ""
    echo "=== Stations Without OTL ==="
    psql -d gnss_data -c "
        SELECT \"NetworkCode\", \"StationCode\" 
        FROM stations 
        WHERE \"Harpos_coeff_otl\" IS NULL 
        LIMIT 20;
    "
    
    echo ""
    echo "=== Top Error-Generating Stations ==="
    psql -d gnss_data -c "
        SELECT \"NetworkCode\" || '.' || \"StationCode\" as station, 
               COUNT(*) as error_count
        FROM events 
        WHERE \"EventType\" = 'error' 
          AND \"EventDate\" >= NOW() - INTERVAL '30 days'
        GROUP BY \"NetworkCode\", \"StationCode\"
        ORDER BY error_count DESC
        LIMIT 10;
    "
    
    echo ""
    echo "=== Data Completeness (Last 30 Days) ==="
    psql -d gnss_data -c "
        SELECT 
            DATE(\"ObservationSTime\") as date,
            COUNT(*) as files,
            COUNT(DISTINCT \"NetworkCode\" || '.' || \"StationCode\") as stations,
            ROUND(AVG(\"Completion\")::numeric, 3) as avg_completion
        FROM rinex
        WHERE \"ObservationSTime\" >= NOW() - INTERVAL '30 days'
        GROUP BY DATE(\"ObservationSTime\")
        ORDER BY date DESC
        LIMIT 30;
    "
    
} > qc_report_$(date +%Y%m).txt
```
9.4 Performance Optimization
9.4.1 Processing Speed
Parallel processing configuration:
```
ini# gnss_data.cfg - Optimize for your cluster
```

# More nodes = faster processing
node_list = node1,node2,node3,node4,node5,node6

# Ensure network connectivity
# ping node1
# ssh node1 hostname
Database connection pooling:

```python# For high-frequency operations, use connection pooling
# Add to Python scripts:
from geode import dbConnection

# Reuse connections
cnn = dbConnection.Cnn("gnss_data.cfg")
# ... perform operations ...
# Don't close until all operations complete
cnn.close()
```
Disk I/O optimization:

```bash# Use SSD for database
# /var/lib/postgresql on SSD

# Use spinning disk for archive
# /archive on RAID array

# Separate repository from archive
# /repository on fast local disk
```
9.4.2 Resource Management
Monitor system resources:
```bash# Check CPU usage
top -b -n 1 | head -20

# Check memory
free -h

# Check disk I/O
iostat -x 2 5

# Check network
iftop -i eth0
```
Limit parallel jobs:
```bash# For systems with limited resources
DownloadSources.py all -win 7 -np  # Sequential downloads
ArchiveService.py -np              # Sequential processing
ScanArchive.py all -ppp -np        # Sequential PPP
```
Database query optimization:
```sql-- Add indexes for common queries
CREATE INDEX IF NOT EXISTS idx_rinex_completion 
    ON rinex("Completion") WHERE "Completion" >= 0.5;

CREATE INDEX IF NOT EXISTS idx_rinex_obstime 
    ON rinex("ObservationSTime");

CREATE INDEX IF NOT EXISTS idx_events_recent 
    ON events("EventDate") WHERE "EventDate" >= NOW() - INTERVAL '30 days';

-- Analyze tables regularly
ANALYZE rinex;
ANALYZE ppp_soln;
ANALYZE events;
```
9.4.3 Archive Management
Archive growth estimation:
```bash# Estimate archive size
du -sh /archive

# Size per network
du -sh /archive/*

# Largest stations
du -sh /archive/*/*/ | sort -h | tail -20
```

---

## 10. Appendices

### Appendix A: Station List File Format

**Example: stations.txt**
```
# Lines starting with # are comments
# One station per line in format network.station

# IGS stations
igs.algo
igs.amc2
igs.bhr3

# Regional network
arg.lpgs
arg.riog
arg.uyba

# Exclude specific station
-igs.algo

# Wildcards
arg.%    # All Argentine stations
igs.a%   # All IGS stations starting with 'a'

# Country codes
USA
ARG
BRA
```
Appendix B: Configuration File Template
Complete gnss_data.cfg template:
```ini[postgres]
# Database connection parameters
hostname = your.database.server
username = gnss_data
password = your_secure_password
database = gnss_data

# Format scripts location
format_scripts_path = /path/to/format_scripts

[archive]
# Main archive path
path = /data/archive

# Repository for incoming data
repository = /data/repository

# Orbit products
ionex = /data/products/ionex/$year
brdc = /data/products/brdc/$year
sp3 = /data/products/sp3/$gpsweek

# Compute nodes (comma-separated)
node_list = node1,node2,node3,node4

# Orbit precedence
sp3_ac = COD,IGS,JPL,GFZ
sp3_cs = OPS,R03,MGX
sp3_st = FIN,SNX,RAP

[otl]
# Ocean loading configuration
grdtab = /opt/gamit/bin/grdtab
otlgrid = /opt/gamit/tables/otl.grid
otlmodel = FES2014b

[ppp]
# PPP configuration
ppp_path = /opt/PPP
ppp_exe = /opt/PPP/ppp
institution = Your Institution
info = Your Lab/Division

# Reference frames (comma-separated)
frames = IGS20,IGS14,

# Frame definitions (EPOCH format)
IGS20 = 1987_1,
IGS14 = 1987_1,2023_1

# ATX files (same order as frames)
atx = /data/products/atx/igs20.atx,/data/products/atx/igs14.atx
```

### Appendix C: Database Schema Diagram

**Key Relationships:**
```
stations (1) ----< (N) rinex
    |                    |
    |                    |
    |                    v
    |               (1) ppp_soln
    |
    v
(N) station_info
    |
    +---- receivers
    +---- antennas
    +---- gamit_htc

stations (1) ----< (N) sources_stations (N) >---- (1) sources_servers

networks (1) ----< (N) stations

events (N) >---- (1) stations (optional FK)
```
Core Constraints:

stations: PRIMARY KEY (NetworkCode, StationCode)
rinex: PRIMARY KEY (NetworkCode, StationCode, ObservationYear, ObservationDOY, Filename)
ppp_soln: PRIMARY KEY (NetworkCode, StationCode, Year, DOY, ReferenceFrame)
station_info: PRIMARY KEY (id), UNIQUE (NetworkCode, StationCode, DateStart)

### Appendix F: Troubleshooting Checklist

**Processing not working:**
- [ ] Database connection successful?
- [ ] All dependencies installed? (`which gfzrnx`, `which ppp`)
- [ ] Configuration file correct? (paths, credentials)
- [ ] Sufficient disk space?
- [ ] Check recent events table
- [ ] Review error logs
- [ ] Verify file permissions

**No data downloading:**
- [ ] Download sources configured in database?
- [ ] Network connectivity to servers?
- [ ] Authentication credentials valid?
- [ ] Check server_id references
- [ ] Verify protocol (FTP/SFTP/HTTP)
- [ ] Test manual download
- [ ] Review download error logs

**PPP failures:**
- [ ] Orbit files available?
- [ ] Station information exists for date?
- [ ] RINEX file quality adequate?
- [ ] Hash values match?
- [ ] Check ppp executable path
- [ ] Review PPP log output
- [ ] Verify ATX file exists

**Web interface issues:**
- [ ] Apache/Nginx running?
- [ ] Database connection from web server?
- [ ] Static files served correctly?
- [ ] JavaScript console errors?
- [ ] Clear browser cache
- [ ] Check web server logs
- [ ] Verify user permissions

### Appendix G: Glossary

**Terms:**
- **ANTEX (ATX)**: Antenna exchange format containing calibrations
- **BRDC**: Broadcast ephemeris (navigation data)
- **CRINEZ**: Compressed RINEX (Hatanaka compression)
- **DOY**: Day of Year (1-366)
- **ETM**: Extended Trajectory Model (position time series model)
- **GAMIT**: GPS At MIT - processing software
- **GLOBK**: Global Kalman filter for combining solutions
- **OTL**: Ocean Tide Loading corrections
- **PPP**: Precise Point Positioning
- **RINEX**: Receiver Independent Exchange Format
- **SP3**: Standard Product #3 (precise orbits)
- **Station Information**: Equipment metadata (receiver, antenna, etc.)

**File Extensions:**
- `.??o`: RINEX observation file
- `.??n`: RINEX navigation file
- `.??d`: Hatanaka-compressed RINEX
- `.crx`: RINEX 3 compressed
- `.sp3`: Precise orbit file
- `.clk`: Clock correction file
- `.erp`: Earth rotation parameters
- `.atx`: Antenna calibration file

### Appendix H: Support and Resources

**Online Resources:**
- GeoDE Repository: https://github.com/demiangomez/Parallel.GAMIT
- GAMIT/GLOBK: http://www-gpsg.mit.edu/gg/
- IGS Products: https://igs.org/products/
- RINEX Format: https://files.igs.org/pub/data/format/

**Mailing Lists:**
- GAMIT Help: gamit-help@mit.edu
- IGS Community: https://igs.org/contact/

**Citation:**
```
Gomez, D.D., et al. (2024). GeoDE: Geodesy Database Engine for 
automated GNSS processing and analysis. GitHub repository. 
https://github.com/demiangomez/Parallel.GAMIT

Document Version: 1.0
Last Updated: January 2025
Authors: GeoDE Development Team
Contact: https://github.com/demiangomez/Parallel.GAMIT/issues
</document>
````
