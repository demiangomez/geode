# External Integration: the Django Web App ("GeoDE Studio"/"GeoDE Desktop")

**Out of scope for this repo's code — documented here only as an integration boundary.** Its
source is not available from this repository; everything below is inferred from this repo's side
of the integration (shared DB tables + the handful of `geode` code paths that read them).

## What it is

`README.md` and `GeoDE_User_Manual.md` §6 describe a map-based web interface ("GeoDE Desktop")
for station monitoring and metadata management, calling it "Django-based." It is **not** a
REST API or React/Vite app living in this repository — there is no web server, API route, or
frontend build tooling anywhere in `geode`.

## Shared database footprint

It owns a distinct, Django-migration-managed slice of the shared `gnss_data` database:
`api_*` (campaigns, person, role, stationtype, stationmeta, stationstatus, visits, images,
attached files, endpoints/clusters, ...), `auth_*`, `django_*` (migrations, content_type, session,
admin_log), `auditlog_logentry`. This repo's `run_db_migrations()` never touches these tables —
don't add migrations for them here.

## Where `geode` code reads from it

- `geode/station_selector.py`'s `StationSelector._check_studio_tables()` conditionally joins
  `api_stationtype`/`api_stationmeta` to support `NET:TYPE` filters
  (`ARG:CONTINUOUS`/`CHL:CAMPAIGN`/`USA:CORS`) — these filters silently degrade/fail if the Django
  app's tables aren't present in the target database.
- `station_merge.py` cascades REST-API-owned data (roles/images/media) when deleting a merged-away
  origin station — i.e. it's aware of and updates some `api_*` rows, even though this repo doesn't
  own their schema.

## Implications for this repo's knowledge base

- Don't document REST endpoints, Django models, or frontend components here — we have no source to
  verify them against, and doing so would just be fabrication.
- When a task depends on `api_*`/Django-side behavior (e.g. "why doesn't `TYPE` filtering work on
  this database"), the likely answer lives in the other repo, not here — say so rather than
  guessing.
- If that repo ever becomes available in this environment, this file should be rewritten (not just
  appended to) based on its actual source.
