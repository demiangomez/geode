# Core Library (`geode/` top-level modules)

Shared modules used across CLI tools and subsystems. For subpackages (`etm/`, `gamit/`,
`metadata/`, `campaign_planner/`, `clustering/`, `elasticity/`), see their dedicated docs.

## Database access — `dbConnection.py`

`Cnn` wraps `psycopg2`, deliberately mimicking the old `pygresql` API it replaced. Key pieces:

- Connection: reads `[postgres]` from a `gnss_data.cfg`-style file, retries up to 3x, autocommits,
  uses `RealDictCursor`. Registers adapters so `DECIMAL`/decimal-arrays come back as `float`, plus
  a numpy-array adapter.
- Query helpers: `query()`, `query_float()` (legacy/deprecated, also casts Decimal→float), `get()`
  (dynamic `SELECT` from a filter dict, exactly one row or `DatabaseError`), `get_columns()`
  (introspects `information_schema.columns`).
- Write helpers: `insert()`, `insert_many()` (bulk via `execute_values`), `update()`, `delete()` —
  all dynamically build SQL from `**kwargs`/dicts, filtered to actual table columns, raising
  `dbErrInsert`/`dbErrUpdate`/`dbErrDelete` and rolling back on failure.
- Transactions: `begin_transac()` / `commit_transac()` / `rollback_transac()`, tracked via
  `self.active_transaction` (rolled back in `__del__`).
- Event logging: `insert_event()`/`insert_warning()`/`insert_error()`/`insert_info()` write to the
  `events` table, tagging the caller via `inspect.stack()`.

### Schema migrations — there is no migration framework

`run_db_migrations()` wraps `_run_db_migrations()` in a Postgres advisory lock (id
`MIGRATIONS_LOCK_ID`) so concurrent connections don't race on DDL. `_run_db_migrations()` is one
long, hand-written, idempotent "check-then-apply" script: each migration checks for an existing
column/table/index (via `get_columns()` / `information_schema.tables` / `pg_indexes`) before
issuing `ALTER TABLE`/`CREATE TABLE`/`CREATE INDEX`. **There is no migration-version table.**
`CREATE INDEX CONCURRENTLY` migrations run outside a transaction (Postgres requirement); everything
else is wrapped in `begin_transac()`/`commit_transac()`.

See [database-schema.md](database-schema.md) for what's actually in the schema today and the
unapplied rename plan. When adding a new migration, follow the exact existence-check idiom already
there — don't introduce a different pattern (e.g. a version table) without an ADR.

## Configuration — `pyOptions.py`

`ReadOptions` parses the `gnss_data.cfg` INI file: `[archive]` (RINEX tank path, repository,
broadcast/SP3 orbit paths + AC-type precedence lists), `[otl]` (`grdtab`, `otlgrid`), `[ppp]`
(PPP engine path/exe, default engine `gpspace`, institution, ATX). Builds `self.Frames`
(reference-frame name/ATX/date-range triples), derived repository subpaths, and `self.sp3types`.
Can write an empty template config via `Utils.create_empty_cfg()` if the file is missing.

Depended on directly by most `com/*.py` entry points and by `pyArchiveStruct.py`, `pyJobServer.py`,
`pyProducts.py`, `pyRinex.py`. `geode/gamit/gamit_config.py`'s `GamitConfiguration` subclasses
`ReadOptions` so GAMIT runs inherit the same global config plus their own `session.cfg`.

## Archive structure vs. network clustering — don't confuse these two

- **`pyArchiveStruct.py`** (`RinexStruct`) — the RINEX **archive directory layout**. Reads the
  DB-defined path template (`rinex_tank_struct`/`keys` tables) and exposes insert/remove/rename/scan
  operations on the archive, plus path building/parsing (`build_rinex_path`,
  `parse_archive_keys`).
- **`network.py`** (`Network`) — GAMIT **double-difference subnetwork clustering**, not a general
  station/network model. Builds/recovers processing sub-networks from a `StationCollection`
  (defined in `geode/gamit/station.py`, not here) via bisecting Q-means + Delaunay backbone +
  overlap/tie logic; its only consumer is `com/ParallelGamit.py`.

## RINEX handling — `pyRinexName.py` / `pyRinex.py`

- `pyRinexName.py` (`RinexNameFormat`) — naming only: parses/constructs RINEX 2
  (`ssssdddf.yyo`-style) and RINEX 3 (long `SSSSMRCCC_R_YYYYDDDHHMM_...`) filenames, detects
  type/version, converts between CRINEZ/RINEX/RINEZ/CRINEX representations, expands archive-path
  template tags (`${year}`, `${doy}`, `${RINEX2}`, ...).
- `pyRinex.py` (`RinexRecord`, `ReadRinex`) — file content/processing: Hatanaka
  compress/decompress (shells out to `crz2rnx`/`rnx2crz`), RINEX 2/3 conversion (`gfzrnx_lx`),
  header normalization, multi-day splitting, windowing/decimation, autonomous-coordinate
  determination (`teqc`/`gfzrnx`). All external-tool calls go through `pyRunWithRetry`.

## Dates — `pyDate.py`

`Date` is the canonical GNSS time type used pervasively (`pyRinex`, `pyOptions`, `pyProducts`,
`pyPPP`, `network.py`, ...). Flexible constructor normalizing any one of
year/doy/day/month/gpsweek/gpsweekday/fyear/mjd/datetime/stninfo into all the others. Module-level
helpers: `yeardoy2fyear`/`fyear2yeardoy`, `date2doy`/`doy2date`, `date2gpsDate`,
`gpsDate2mjd`/`mjd2date`, `parse_stninfo()` (handles the GAMIT station-info open-ended "9999 999"
sentinel).

## Orbit/clock products & PPP — `pyProducts.py` / `pyPPP.py`

- `pyProducts.py` — `OrbitalProduct` base class locates/copies/decompresses the
  best-precedence-matching product file from the local products archive, given a path template
  with `$year`/`$doy`/`$gpsweek`/`$gpswkday` tags. Subclasses: `GetSp3Orbits`, `GetClkFile`,
  `GetEOP`, broadcast ephemeris (`GetBrdcOrbits`/`GetBrdgOrbits`/`GetBrdmOrbits`), PPP-AR bias
  products (`GetObxFile`/`GetBiaFile`). `resolve_product_line()` picks a consistent
  AC/constellation/sample-rate line across SP3+CLK using precedence lists from `pyOptions`.
- `pyPPP.py` — `PPPEngine` (ABC) defines the shared workflow contract
  (`prepare_rinex()`/`stage()`/`exec_ppp()`). Two concrete engines: **`GPSPACE`** (wraps the
  external NRCan PPP software / CSRS-PPP — the engine the README calls "GPSPACE") and **`PRIDE`**
  (PPP-AR alternative). `RunPPP()` is the engine-agnostic factory, dispatching on
  `options['ppp_engine']` (default `'gpspace'`).

## Station selection & merging

- `station_selector.py` (`StationFilter`, `StationSelector`) implements the README's station-list
  syntax: country codes, `net.stn` wildcards, `-`/`*` exclusion, `NET:TYPE` (`ARG:CONTINUOUS`), and
  geographic filters `BBOX[...]`/`RADIUS[lat,lon,km]`/`LAT[...]`/`LON[...]`/`PLATE[code]` (plate
  lookup via `geopandas`/`shapely` against a bundled `PB2002_plates.json`). `TYPE` filters
  (`CONTINUOUS`/`CAMPAIGN`/`CORS`) only work when the Django app's `api_stationtype`/`api_stationmeta`
  tables are present — see [external-django-app.md](external-django-app.md).
- `station_merge.py` merges a duplicate "origin" station into a "destiny" station DB-wide: moves
  RINEX rows+files, day-indexed solution tables, station-info records, and fixes up station codes
  inside array columns (`gamit_subnets`, etc.). Runs in one transaction with file-move rollback on
  failure.

## Smaller utility modules (one-liners)

- `Utils.py` — coordinate transforms (ECEF↔LLA, local-geodetic), GPS-week/date parsing, country
  lookup from lat/lon, station-list parsing built on `station_selector`, misc helpers.
- `pyBunch.py` — `dict` subclass with attribute-style access (`bunchify`/`unbunchify`).
- `pyEvents.py` — `Event` record builder for the `events` table (used by `Cnn.insert_event`).
- `pyRunWithRetry.py` — `subprocess` wrapper with timeout/errno-35 retry for external geodetic tools.
- `pyOkada.py` — Okada (1985) elastic half-space dislocation model + the "s-score" earthquake
  relevance scoring; see [campaign-clustering-elasticity.md](campaign-clustering-elasticity.md) for
  how it backs the Stacker's coseismic/postseismic constraints.
- `pyOTL.py` — ocean tidal loading coefficients, via GAMIT's external `grdtab`.
- `pyParseAntex.py` — minimal legacy ANTEX parser (type/serial strings only).
- `atx_parser.py` — full ANTEX 1.4 parser, populates the `atx_files`/`antenna_calibration*` tables.
- `snxParse.py` — SINEX file parser/merger (`StationData`, `snxFileParser`, `snxStationMerger`).
- `proto_download.py` — download-protocol abstraction (`IProtocol` + FTP/FTPA/SFTP/HTTP/HTTPS impls).
- `ConvertRaw.py` — converts receiver-native raw formats (e.g. Trimble T0x) to RINEX.
