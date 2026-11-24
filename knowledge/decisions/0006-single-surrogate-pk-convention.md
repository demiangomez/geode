# ADR-0006: Single surrogate primary key (`api_id`) for new tables

- **Status:** accepted
- **Date:** 2026-11-20
- **Branch (if not master):** —

## Context

GeoDE's Postgres database is shared with the sibling Django app ("GeoDE Studio" — see
[architecture/external-django-app.md](../architecture/external-django-app.md)), which needs a
stable integer id to reference rows from its API layer. Most of GeoDE's older tables key on a
natural business value directly (e.g. `stations` on `(NetworkCode, StationCode)`), but newer
tables added specifically for the API layer need a surrogate id that survives renames of whatever
natural key the table also has.

Twice now, a new table ended up with two id-like columns instead of one, before being corrected:
- `agencies` first used `name` as the primary key (matching the natural-key convention of older
  tables), then was changed to a `SERIAL` surrogate — see
  [checkpoints/2026-11-19-agency-tables.md](../checkpoints/2026-11-19-agency-tables.md).
- `agency_roles` was given an explicit `id SERIAL PRIMARY KEY` **plus** a separate `api_id`
  surrogate column (mirroring the Django app's `api_stationrole` "id + name" shape), then
  corrected on 2026-11-20 to drop `id` entirely and make `api_id` itself the primary key — see
  [checkpoints/2026-11-20-migration-split.md](../checkpoints/2026-11-20-migration-split.md).

Both corrections landed only because a human caught the redundant second id column after the fact.
This ADR exists so the next new table gets it right the first time.

## Decision

A new table that needs an API-facing surrogate id gets **exactly one** such column, named
`api_id`, declared `SERIAL PRIMARY KEY` (or an equivalent explicit sequence when `SERIAL` isn't
usable, e.g. widening an existing column — see `stations.api_id` in
[`003_add_stations_plate_column.py`](../../geode/database_migrations/) era migrations). `api_id` is
simultaneously the table's real primary key and the id the Django/API layer uses — never a second,
separate `id` column alongside it.

If the table also has a natural key that should remain unique but must not cascade renames into
other tables (e.g. `agencies.name`), give it a plain `UNIQUE` constraint, not the primary key.

Other tables FK to `<table>.api_id`, not to any natural key, when referencing a table that has one.

## Rationale

- A second id column is pure redundancy once `api_id` exists: every row already has exactly one
  stable integer identity, so a table-internal `id` serves no purpose `api_id` doesn't already
  serve, and it doubles the number of id-like columns a reader has to disambiguate.
- Matching the Django app's own `id + name` table shape (e.g. `api_stationrole`) was tried for
  `agency_roles` on the theory that it should "look like" its Django counterpart, but that
  reasoning doesn't hold: `agency_roles` isn't a Django-migrated table, it's a GeoDE-owned table
  that merely needs to be *readable* by the Django layer via `api_id`, the same as every other
  GeoDE-owned table other tables in this schema already FK to.
- Keeping the convention to one name (`api_id`) rather than "whatever the table's PK happens to be
  called" makes the FK target predictable without checking each table's definition first.

## Consequences

- Every future `CREATE TABLE` in `geode/database_migrations/` that needs an API-facing id follows
  this directly instead of re-deriving it (or getting corrected) per table.
- A table that does NOT need to be read by the Django/API layer has no obligation to add `api_id`
  at all — this convention only applies once that need exists.
- Existing older tables that key on a natural value are not retroactively changed by this ADR; it
  governs new tables going forward.

## Alternatives considered

- Dual id (natural key or a table-internal `id` as PK, plus a separate `api_id` surrogate) —
  rejected; tried for both `agencies` and `agency_roles` and reversed both times once reviewed.
- Naming the surrogate column `id` instead of `api_id` — rejected; `api_id` signals explicitly that
  the column exists for the Django/API layer and is the FK target other tables use, which a bare
  `id` doesn't communicate.
