# GeoDE station package, format version 1

A station package is a zip file holding everything needed to reproduce a station in another GeoDE system.
`ScanArchive.py -export` writes it and `ScanArchive.py -import` reads it. Other programs can write it too (see
[Packages made outside GeoDE](#packages-made-outside-geode)). The code that writes and reads it lives in
`geode/station_package.py`.

## Zip layout

```
NET.STNM.zip
├── manifest.json          what the package holds (always present)
├── station.json           the rows of the station (the "station file")
├── rinex/                 RINEX files: the CRINEZ files of the archive, as they are stored there
│   └── abcd0010.20d.Z
└── media/                 files of the GeoDE Studio media folder, with their relative path
    ├── stations/arg/abcd/images/front.jpg
    ├── stations/arg/abcd/visits/2021-03-04/log_sheet_file/sheet.pdf
    └── monument_type_photos/pillar.png
```

- A zip without `manifest.json` is a package of format 0, the one `-export` wrote before this one: one
  `NET.STNM.json` file plus the RINEX files at the root. `-import` still reads it (see [Format 0](#format-0)).
- Every path in the zip is relative and uses `/`. A path that is absolute or contains `..`, `.` or an empty part
  makes the package invalid.
- `rinex/` is missing from a dataless package (`-export true`). `media/` only holds the kinds of files that were
  asked for (`-images`, `-attachments`).

## Versioning

- `format` is always `"geode-station-package"`. `format_version` is an integer, `1` for this document.
- A reader refuses a package with a `format_version` greater than the one it knows.
- New optional keys can be added without changing the version. Readers ignore keys they do not know.
- Removing or renaming a key, or changing what a value means, increases `format_version`.

## manifest.json

| Key | Type | Content |
|---|---|---|
| `format` | string | `"geode-station-package"` |
| `format_version` | integer | `1` |
| `created` | string | UTC time the package was made, ISO 8601 (`2026-11-06T15:48:26+00:00`) |
| `generator` | object | Who made it: `software` (`geode-gnss`, `geode-upload-portal`...), `version`, `host` |
| `NetworkCode`, `StationCode` | string | Codes of the station in the system that made the package |
| `data` | string | Name of the station file in the zip: `"station.json"` |
| `contents` | object | Booleans: `rinex` (RINEX files included), `images`, `attachments`, `studio` (GeoDE Studio data present) |
| `counts` | object | Number of rows per table and of files per kind. Informative: the reader does not trust it |
| `files` | array | One object per file of the zip, `station.json` included: `path`, `kind`, `size` (bytes), `sha256` (hex) |

`kind` is one of `rinex`, `image`, `attachment` or `data`. The reader checks the size and the sha256 of every file
before it changes anything.

## station.json

```json
{
    "format": "geode-station-package",
    "format_version": 1,
    "NetworkCode": "arg",
    "StationCode": "abcd",
    "station":  { ... },
    "tables":   { "stationinfo": [...], "rinex": [...], "stationalias": [...], "etm_params": [...],
                  "sources_stations": [...], "events": [...] },
    "studio":   { ... } or null,
    "catalogs": { "networks": [...], "receivers": [...], ... },
    "agencies": [ { "name": "Ohio DOT", "type": "owner", "rtn_names": ["Ohio RTN"], "contacts": [...] },
                  { "name": "NGS", "type": "host", ... } ],
    "custom_fields": { "ngs_pid": "JV3192", "installation_date": "2015-06-15", ... }
}
```

`agencies` and `custom_fields` are optional (a reader of format version 1 that does not know them ignores
them). `NetworkCode` is optional too: a package made by another program may not have one, and the network of a new
station is the one the administrator gives to the import (`{default net}`, see Import rules).

### Rules for every row

- The rows are the rows of the database table, with the column names of the database.
- Surrogate keys (`api_id`, `id`, `uid`, `event_id`, `server_id`, `station_id`, `visit_id`...) are never written:
  they change from one database to another.
- The rows of a table of the station do not repeat `NetworkCode` and `StationCode`: they are the codes of the
  station of the package.
- A reference to a catalog uses the natural key of the catalog (see [Catalogs](#catalogs)): its value when the key
  has one column, an object with the columns of the key otherwise.
- Values:
  - numbers are JSON numbers;
  - timestamps are `"YYYY-MM-DD HH:MM:SS"`, plus `.ffffff` when there are microseconds and `+HH:MM` when the
    column has a time zone;
  - dates are `"YYYY-MM-DD"` and times are `"HH:MM:SS"`;
  - arrays are JSON arrays;
  - `null` is SQL NULL.
- A reader ignores columns that do not exist in its database. A missing column takes its default value.

### station

The row of `stations` without `api_id`, `NetworkCode` and `StationCode`: `StationName`, `auto_x`, `auto_y`,
`auto_z`, `lat`, `lon`, `height`, `Harpos_coeff_otl`, `max_dist`, `dome`, `country_code` (three letters, see
`countries`), `marker`, `alias`, `plate`, `DateStart`, `DateEnd`. `DateStart` and `DateEnd` are computed again from
the RINEX records by the destination database. `country_code` may be left out: the importer takes it from the
coordinates of the station, and refuses the station when it cannot (see Import rules).

### tables

| Key | Table | Order | Notes |
|---|---|---|---|
| `stationinfo` | `stationinfo` | `DateStart` | `DateEnd` null: record still open. `ReceiverCode`, (`AntennaCode`, `RadomeCode`) and (`AntennaCode`, `HeightCode`) point to `receivers`, `antennas` and `gamit_htc` |
| `rinex` | `rinex` | year, doy, interval, completion | Present in dataless packages too. The CRINEZ file of a row is `rinex/` + the CRINEZ name of `Filename`. A file of `rinex/` without its row is read by the importer (see Import rules) |
| `stationalias` | `stationalias` | | |
| `etm_params` | `etm_params` | | Parameters of the ETM set by the users (jumps, relaxations, frequencies) |
| `sources_stations` | `sources_stations` | `try_order` | `server` holds the row of `sources_servers` (credentials included) and, in it, `metadata_source` holds the row of `sources_metadata` (or null) |
| `events` | `events` | `EventDate` | Events of the station |

A table that does not exist in the origin database is left out of `tables`.

### studio

`null` when the origin database has no GeoDE Studio (REST API) tables. Otherwise:

| Key | Content |
|---|---|
| `station_meta` | Row of `api_stationmeta`: `status`, `monument_type` and `station_type` are references by name. `has_gaps`, `has_gaps_last_update_datetime`, `has_gaps_update_needed` and `has_stationinfo` are left out: the destination computes them again |
| `images` | Rows of `api_stationimages` (`image`, `name`, `description`) |
| `attached_files` | Rows of `api_stationattachedfiles` (`file`, `filename`, `description`) |
| `people_roles` | `{"person": <person>, "role": <role name>}` from `api_rolepersonstation` |
| `visits` | Rows of `api_visits`. `campaign` is a reference (or null) and `people` a list of references. Each visit has `images`, `attached_files` and `gnss_data_files` (rows of `api_visitimages`, `api_visitattachedfiles`, `api_visitgnssdatafiles`) |

### agencies

The agencies of the station, in the tables of `database_migrations/028_create_agency_tables.py`: `agencies` (one
row per agency, with its contacts), `agency_roles` (the roles: `owner` and `host`, and the ones added later) and
`agency_stations` (one row per station and agency, with its role and a free description; `station_id` is the
`api_id` of the station).

| Role | Meaning |
|---|---|
| `owner` | The agency that operates the station. One at most per station: the import keeps the one a station has |
| `host` | An agency that sends the data of a station another agency operates |

`agencies` is an array of objects, the owner first:

| Key | Type | Column | Content |
|---|---|---|---|
| `name` | string, required | `agencies.name` | At most 200 characters. Two names that differ only in case are the same agency, and an array has each agency once |
| `type` | string, required | `agency_roles.name` | Its role: a name of `agency_roles` (any case). One `owner` at most |
| `description` | string or null | `agency_stations.description` | What the agency is for the station, free text |
| `rtn_names` | array of strings, a string with the names separated by `;`, or null | `agencies.rtn_names` | Real-Time Networks operated by the agency. Each name at most 100 characters |
| `contacts` | array of contacts | `agencies.primary_contact_*`, `secondary_contact_*` | One contact per way of reaching a person |

A contact is `{"grade": 1 | 2, "type": "email" | "phone", "name": string or null, "value": string}`: grade 1 is the
technical (primary) contact and grade 2 the secondary one, so a person with an email and a phone is two contacts with
the same name. `value` is the email address (at most 254 characters) or the phone number. GeoDE keeps the first one
of each grade and type: the name, email and phone of the primary contact and the name and email of the secondary
one (it keeps no phone for the secondary contact). A new agency needs its primary contact complete (name, email and
phone, `NOT NULL` columns). Only `name` and `type` are required for an agency the destination already has.

The export writes every agency of the station with its role, its description, its RTN names and its contacts. An
origin database without the tables of the agencies does not write the key. The key `agency` of the first draft of
this format (the owner alone, and `agencies` as names) is refused.

### custom_fields

Optional fields of the station that are not columns of GeoDE, kept apart from the standard ones: an object of
name -> value, where each value is a string, a number or null (a field with a list of predefined values holds one
of them). Objects and lists are refused. GeoDE will have custom fields, each with a type (integer, string, floating
point number or list of predefined values) and enabled by country or by country and network; the importer will load
this block into them. Until then it reads and checks the block, does not load it, and says so with a warning.

The names are chosen by the program that makes the package. The ones of the Station Workbook:

| Name | Column of the Stations sheet | Value |
|---|---|---|
| `ngs_pid` | NGS PID (if applicable) | Two letters and four digits, or left out |
| `reference_point_description` | Reference Point Description | Text |
| `installation_date` | Installation Date | `"YYYY-MM-DD"` |
| `rtn_names` | RTN Name(s) | The names separated by `; ` (a value cannot be a list) |

They are customizable fields that GeoDE will show in the metadata of the station.

### Media files

A media column (`image`, `file`, `navigation_file`, `log_sheet_file`, `photo`, `photo_path`, `icon`) holds the
path of the file relative to the media folder, the same value Django stores. The file is in the zip at `media/` +
that path, if its kind was included:

- `image` (`-images`): pictures of the station and its visits, photos of people, monument types and station type
  icons.
- `attachment` (`-attachments`): attached files of the station and its visits, the navigation file of the station,
  log sheets, navigation files and GNSS data files of the visits.

The rows are always written, even when their files are not in the package.

### catalogs

Rows of the catalogs used by the station, without their surrogate keys. The importer creates the ones its database
does not have and never changes the ones it has.

| Key | Table | Natural key | Notes |
|---|---|---|---|
| `networks` | `networks` | `NetworkCode` | |
| `receivers` | `receivers` | `ReceiverCode` | |
| `antennas` | `antennas` | `AntennaCode`, `RadomeCode` | |
| `gamit_htc` | `gamit_htc` | `AntennaCode`, `HeightCode` | |
| `sources_formats` | `sources_formats` | `format` | |
| `countries` | `api_country` | `three_digits_code` | `stations.country_code` |
| `station_status_colors` | `api_stationstatuscolor` | `color` | |
| `station_statuses` | `api_stationstatus` | `name` | `color` is a reference |
| `monument_types` | `api_monumenttype` | `name` | `photo_path` is a media file |
| `station_types` | `api_stationtype` | `name` | `icon` is a media file |
| `station_roles` | `api_stationrole` | `name` | |
| `people` | `api_person` | `first_name`, `last_name`, `email` | `user_id` is not exported: users stay in their system |
| `campaigns` | `api_campaigns` | `name`, `start_date`, `end_date` | `default_people` is not exported |

`sources_servers` and `sources_metadata` travel inside `sources_stations`. `sources_servers` has a natural key (see
Import rules); `sources_metadata` is reused when a row has the same values in every column.

## What is not exported

- Solutions and time series: `ppp_soln`, `ppp_soln_excl`, `gamit_soln`, `gamit_soln_excl`, `gamit_ztd`, `stacks`,
  `apr_coords` and `aws_sync`. The destination computes them again from the RINEX files.
- `etms` (ETM results, computed again), `locks` (processing locks) and `data_source` (old table replaced by
  `sources_stations`).
- RINEX quality control: `rinex_qc_run`, `rinex_qc_signal` and `rinex_qc_signal_bins`. The next `ArchiveService`
  run computes them for the imported RINEX records (`RinexQC`, unless `--no_rinex_qc`).
- `api_stationmetagaps` (computed again by GeoDE Studio).
- Users and permissions of GeoDE Studio.

The list comes from the inventory of `geode/station_merge.py`. A test checks that every table of a station there is
either exported or listed here. `agency_stations` is exported outside `tables`, by name, in `agencies`: it is keyed
by the `api_id` of the station and the database deletes its rows with the station (`ON DELETE CASCADE`), so a merge
that deletes the origin does not carry its agencies over.

## Import rules

`ScanArchive.py dummy -import {default net} {zipfiles}` reads both formats
(`StationImport` in `geode/station_package.py`). The name of a zip file does not matter: what the importer reads is
in `manifest.json` and `station.json`.

- **Network.** `{default net}` is the network of a new station: the administrator who runs the import assigns it.
  A package without `NetworkCode`, or of another network, is imported into `{default net}` too, with a warning that
  says it. An existing station keeps its network.

- Before anything changes, the size and sha256 of every file are checked against the manifest.
- **One transaction per package.** Catalogs, station, tables, GeoDE Studio data and RINEX rows are written in a
  single transaction. The RINEX rows are inserted as they come in the package, not through
  `pyArchiveStruct.insert_rinex` (it opens its own transaction, and `dbConnection.begin_transac` rolls back the one
  already open); a RINEX file without its row is archived with the same steps as `insert_rinex`, inside that
  transaction. The RINEX files and the media files are copied while the transaction is open.
- **A record that fails does not stop the import.** Each record of the package (a catalog row, a station info
  record, a RINEX row, an alias, a data source, a visit, a picture...) goes in a `SAVEPOINT`. If it fails, only that
  record is rolled back, with the files it copied, and the import goes on until the last record. For example, a
  station info record with a receiver that is neither in `receivers` nor in the package is left out, and the other
  records are imported.
- **A package that fails leaves nothing.** When the package itself fails (it is damaged, the station is refused, its
  `stations` row cannot be inserted, the commit fails), the whole transaction is rolled back and the files and
  folders created are removed. The run goes on with the next package.
- **What was not imported** is listed in the output, one line per record (its table, the record: file name, start
  date, natural key... and the reason) or package.
- The destination is chosen as before: spatial check against the stations of the database (with the coordinates of
  the package, or the ones computed from `auto_x`, `auto_y`, `auto_z` when `lat` is missing; skipped when the
  package has no coordinates) and a question to the user.
- **New station** (it must not exist): gets everything in the package. `DateStart` and `DateEnd` of `stations` are
  computed again from its RINEX rows. A package of another network moves the media files to the folder of the
  destination station (`stations/<net>/<stn>/...`).
- **Existing station: it keeps its data.** GeoDE decides that the station exists (same code, within its
  `max_dist`), and then its `stations` row, station info records, GeoDE Studio data, alias, ETM parameters and events
  stay as they are: those of the package are ignored (the client, 2026-11-17). It only gets:
  - the RINEX files it does not have (same year, day, interval and completion);
  - for each RINEX file of the package (also one the station already had) whose date has no station info record,
    the record of the package that covers its first epoch, inserted with `StationInfo.insert_station_info` (which
    adjusts the records around it or refuses an overlap), as the `-import` of format 0 did. A warning says which
    RINEX files have no record in the database nor in the package. A package without RINEX files adds no station
    info;
  - the data sources (`sources_stations`) whose server it does not use yet, after the ones it has (`try_order`),
    and the metadata source of a server that has none: they are where OSU downloads its RINEX from;
  - the agencies it does not have, and its owner when it has none (see Agencies below).

  When the station code of the destination is different, the RINEX names take it.
- **Existing station: same place.** The data is added to an existing station only if the coordinates of the package
  are no farther from it than the `max_dist` of the existing station (20 m when it has none), the rule ArchiveService
  uses for the RINEX files. Otherwise it is not the same station: the package is not imported, and the output
  says the distance. When the package or the station has no coordinates the distance cannot
  be checked (warning).
- The `stations` trigger creates the row of `api_stationmeta`. The importer updates that row and does not insert a
  second one (`station_unique`). It only inserts it if the trigger did not create it.
- Missing catalogs are created. Existing ones are used as they are, never changed. A station status whose color is
  not in the destination is not created (reported). A country of the station that is neither in `api_country` nor
  in the package stops the import of that package.
- A new station without `country_code` gets the country that contains its coordinates (`Utils.get_country_code`,
  with the Natural Earth map shipped with GeoDE, so no internet access is needed), with a warning. There is no
  "nearest country": when the package has no coordinates, no country contains them (for example a coastal station
  that falls just outside the coastline of the map) or that country is not in `api_country`, the station is
  refused. The package is not imported, with the reason. Add
  `country_code` to the package to import it.
- `custom_fields` is checked but not loaded yet (warning), for new and existing stations.
- **Agencies.** They are matched by name without distinguishing case (the name stored is the one the destination
  already has) and created when they do not exist. A new station gets them with their roles. An existing station
  gets the agencies it does not have yet, and the owner of the package only if the station has none (an agency it
  already had with another role becomes its owner). When it has another owner, that one is kept and the owner of the
  package becomes a `host`, with a warning. An agency the station already has keeps its role. Nothing is removed.
- **Contacts and RTN names.** A new agency is created with the ones of the package. An existing one is only filled
  in: a column it does not have is set; one it has with another value is kept (warning). A contact or an RTN name
  that is not valid (for example a contact of type `fax`) is left out alone (reported). Keys of the
  object that GeoDE does not keep (for example `submission_date`) give a warning.
  Each agency is one record of the import: one that fails (a name longer than 200 characters, a new agency without
  its primary contact, a role that is not in `agency_roles`) is not imported (reported) and the rest is. A
  destination database without the tables of the agencies imports the station without them, with a warning.
- `sources_servers` has a natural key: `protocol`, `fqdn`, `path` and `username`. A server of the package without
  `username` (a server that needs no credentials) is also the server of the destination with
  the same `protocol`, `fqdn` and `path` that already has a username. A server of the destination with the
  same key is used as it is, never changed: when the package has another password or format for it, the importer
  says which ones in a warning (never the values). The output and the errors never show a password. Otherwise the server is created. `sources_metadata` is reused
  when a row has the same values in every column; otherwise it is created.
- **Metadata source.** In GeoDE the metadata source hangs from the server (`sources_servers.metadata_source_id`):
  every station of a server shares it, and `SyncMetadata` reads it for each of them. The metadata source of a
  station goes in the server of its data source (`server.metadata_source`). A server without a metadata source gets
  the one of the package; a server that has another one keeps it (warning). So two stations of the same RINEX server
  with different metadata servers cannot both be kept: the first one imported wins.
- **Formats.** `format` of `sources_servers`, `sources_stations` and `sources_metadata` points to `sources_formats`
  (only `DEFAULT_FORMAT` exists unless another program adds formats). A format that is neither in the destination
  nor in the `sources_formats` catalog of the package gives a warning and the row gets `DEFAULT_FORMAT`.
  `SyncMetadata` detects the format of a metadata file by its content (IGS site log, station.info or NGL), so the
  Metadata Format of the Station Workbook does not need its own value: `IGS site log`, `GAMIT station.info` and
  `Other` all go as `DEFAULT_FORMAT`. A file that is none of those three cannot be read by `SyncMetadata`.
- **Monument and station types.** `monument_type` and `station_type` of `api_stationmeta` are names of
  `api_monumenttype` and `api_stationtype`. A name the destination does not have (and that does not come in the
  catalogs of the package) is left empty (reported, table `monument_types` or `station_types`).
  The Monument Type list of the Station Workbook is the list of names of GeoDE, so this only happens with an
  outdated workbook.
- The path templates of `sources_servers`, `sources_metadata` and `sources_stations` use the tags of
  `pyRinexName.path_replace_tags` (`${station}`, `${year}`, `${doy}`, `${RINEX2}`...), and they end with the name of
  the file: `DownloadSources` takes the file name from the end of the path. The importer does not translate other
  placeholders: a program that reads another format translates them.
- An alias already used by another station (as code or alias) is not imported (reported).
- The RINEX rows of a dataless package are not imported (one warning). In a package with RINEX files, a row whose
  file is missing, or whose file is already in the archive without its RINEX row, is not imported (reported); check
  the archive with `ScanArchive -rinex`.
- **RINEX files without their row** (the files of format 0, or of a package made by another program) are read with
  `pyRinex.ReadRinex`, as `ArchiveService` does (it needs `crx2rnx` and `gfzrnx`), and archived with the naming
  convention of GeoDE. One the station already has (same year, day, interval and completion) is not copied. A file
  that cannot be read, or whose name is not a RINEX name of letters, digits, `_` and `.`, is not imported
  (reported).
- Media files are copied to the media folder (`[archive] media` of `gnss_data.cfg`) with the owner and group of
  that folder, like the files GeoDE Studio saves (`user_id_to_save_files`, `group_id_to_save_files`), so the REST
  API can change or delete them. The import must run as root or as the owner of the media folder. A name already
  taken in the media folder gets a `_imported<n>` suffix.
- One `info` event is added to the destination station with the origin of the package.

### Format 0

A zip without `manifest.json` (one `NET.STNM.json` plus the RINEX files at the root) is still read: its station
columns (`lat`, `lon`, `height`, `x`, `y`, `z`, `otl`, `dome`, `max_dist`, `marker`, `country_code`) and
`StationInfo` records are converted to this format and imported with the same rules. Its RINEX files are read again
with `pyRinex`, as its `-import` did: the `rinex` rows of the json are not used. Keys missing in the file stay empty
(the `-import` of format 0 failed with a `KeyError` when `lat` was missing).

## Packages made outside GeoDE

A package made by another program follows the same rules. Every key not listed here can be left out, or set to an
empty list or null:

- `manifest.json`: `format`, `format_version`, `generator`, `StationCode`, `data`, `contents` (`rinex` true when
  the zip has RINEX files), and `files` with the size and sha256 of every file. `NetworkCode` only when the program
  knows it.
- `station.json`: `format`, `format_version`, `StationCode` (`NetworkCode` only when it is known), and `station`
  with at least `StationName` (`lat`, `lon`, `height`, `auto_x`, `auto_y`, `auto_z` when they are known).
  `country_code` is not needed when the coordinates are known: the importer takes it from them.
  `marker` (monument and receiver digits of the RINEX 3 names, for example `01` in `AAAA01USA`) may be left out:
  the station gets NULL, like the stations already in GeoDE, and `DownloadSources` reads NULL as `00`.
- `custom_fields` with the data of the station that has no column in GeoDE (see custom_fields).
- `agencies`: the agency that sends the station, with its contacts: the `owner` when it operates the station, a
  `host` when another agency does (see agencies).
- `tables.sources_stations` with the servers of the station and, in each server, its metadata source.
- `studio.station_meta` with `comments` and `monument_type` (a name of `api_monumenttype`).
- `tables.stationinfo`, plus the `receivers`, `antennas` and `gamit_htc` rows of the codes it uses. The importer
  creates them only if the destination does not have them.

- `rinex/` with the RINEX files of the station (kind `rinex`), without rows in `tables.rinex`: a RINEX row needs
  data read from the file (start and end time, completion, receiver), so the importer reads them (see Import rules).

A station made outside GeoDE has no OTL coefficients: run `ScanArchive -otl` after the import.

A `station.json` made by another program, for a station with one RINEX file:

```json
{
    "format": "geode-station-package",
    "format_version": 1,
    "StationCode": "bbb1",
    "station": {
        "StationName": "District 6 Office",
        "lat": 40.0023,
        "lon": -83.0158,
        "height": 280.3
    },
    "agencies": [{
        "name": "Example State Department of Transportation",
        "type": "owner",
        "rtn_names": ["Example State RTN"],
        "contacts": [
            {"grade": 1, "type": "email", "name": "John Smith", "value": "john.smith@dot.example.gov"},
            {"grade": 1, "type": "phone", "name": "John Smith", "value": "+1-614-555-0123"},
            {"grade": 2, "type": "email", "name": "Jane Doe", "value": "jane.doe@dot.example.gov"}
        ]
    }],
    "tables": {
        "stationinfo": [
            {"DateStart": "2018-01-01 00:00:00", "DateEnd": null,
             "ReceiverCode": "SEPT POLARX5", "ReceiverSerial": "3021567", "ReceiverFirmware": "5.4.0",
             "AntennaCode": "SEPCHOKE_B3E6", "RadomeCode": "NONE", "AntennaSerial": "CR620010",
             "AntennaHeight": 0, "AntennaNorth": 0, "AntennaEast": 0, "HeightCode": "DHARP",
             "AntennaDAZ": 0, "Comments": null}
        ],
        "sources_stations": [
            {"try_order": 1, "path": null, "format": null,
             "server": {"protocol": "ftp", "fqdn": "ftp.dot.example.gov", "username": null, "password": null,
                        "path": "/gnss/rinex/${station}/${year}/${doy}/${RINEX3_30}",
                        "format": "DEFAULT_FORMAT",
                        "metadata_source": {"protocol": "https", "fqdn": "gnss.dot.example.gov", "username": null,
                                            "password": null, "path": "/sitelogs/${station}.log",
                                            "format": "DEFAULT_FORMAT"}}}
        ]
    },
    "studio": {
        "station_meta": {"comments": "Antenna on the roof of the district office",
                         "monument_type": "Steel mounted on building"}
    },
    "custom_fields": {
        "reference_point_description": "Antenna reference point (no marker)",
        "installation_date": "2018-01-01",
        "rtn_names": "Example State RTN; Example Regional RTN"
    }
}
```

and its `manifest.json`:

```json
{
    "format": "geode-station-package",
    "format_version": 1,
    "created": "2026-12-01T15:00:00+00:00",
    "generator": {"software": "another-program", "version": "1.0"},
    "StationCode": "bbb1",
    "data": "station.json",
    "contents": {"rinex": true, "images": false, "attachments": false, "studio": true},
    "files": [
        {"path": "rinex/BBB100USA_R_20253350000_01D_30S_MO.crx.gz", "kind": "rinex", "size": 1834221,
         "sha256": "<sha256 of the file>"},
        {"path": "station.json", "kind": "data", "size": 2310, "sha256": "<sha256 of station.json>"}
    ]
}
```
