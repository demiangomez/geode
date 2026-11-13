# GeoDE Knowledge Base — Index

Central map of persistent project knowledge for the GeoDE (Geodetic Database Engine) repository.
Read this first in any new session; it links to everything else so you only load what a task
actually needs. This index is for developer/architecture knowledge — **not** a duplicate of the
user-facing manual. For "how do I run X" questions, see [GeoDE_User_Manual.md](../GeoDE_User_Manual.md)
first.

## Scope note (read this once)

GeoDE (this repo) is a Python/PostgreSQL framework: CLI scripts (`com/`) + library (`geode/`) +
DB schema (`database/`). **There is no REST API or frontend (React/Vite/Django) in this repo.**
A separate Django web app ("GeoDE Studio"/"GeoDE Desktop") shares the same `gnss_data` Postgres
database from its own repository, which is not available here. See
[architecture/external-django-app.md](architecture/external-django-app.md) for what we know about
that boundary. Do not invent or assume API/frontend code exists in this repo.

## How to use this index at session start

1. Check `git branch`/`git status` — architectural work-in-progress sometimes lives on a feature
   branch, not `master` (see [decisions/](decisions/) for branch-scoped notes before assuming a
   described feature is on your current branch).
2. Skim this index, then open only the `architecture/*.md` file(s) relevant to the task.
3. Check [STATUS.md](STATUS.md) for current priorities/outstanding work, and
   [decisions/](decisions/) for any ADR touching the area you're working in.
4. Check [known-issues.md](known-issues.md) for doc/code drift or latent bugs already found in
   that area, so you don't rediscover them.

## Architecture docs (one per subsystem)

| Doc | Covers |
|---|---|
| [architecture/overview.md](architecture/overview.md) | Repo-wide map: `com/` vs `geode/`, processing pipeline, legacy-vs-active modules |
| [architecture/core-library.md](architecture/core-library.md) | `geode/dbConnection.py`, `pyOptions.py`, `pyDate.py`, `pyRinex*.py`, `pyArchiveStruct.py`, `pyProducts.py`, `pyPPP.py`, `station_selector.py`, `station_merge.py`, and smaller shared utils |
| [architecture/database-schema.md](architecture/database-schema.md) | Table inventory, migration mechanism, what's owned here vs. by the Django app |
| [architecture/etm-stacker.md](architecture/etm-stacker.md) | `geode/etm/` (trajectory model engine) and `geode/etm/stacker/` (spatial stacking); legacy `pyETM.py`/`pyStack.py` relationship |
| [architecture/gamit-globk.md](architecture/gamit-globk.md) | `geode/gamit/`, `com/ParallelGamit.py`, parallel dispatch (`pyJobServer`/dispy), ZTD workflow |
| [architecture/metadata-llm-pipeline.md](architecture/metadata-llm-pipeline.md) | `geode/metadata/` Claude-assisted IGS log / station-info reconciliation |
| [architecture/campaign-clustering-elasticity.md](architecture/campaign-clustering-elasticity.md) | `geode/campaign_planner/`, `geode/clustering/`, `geode/elasticity/` + `pyOkada.py` |
| [architecture/external-django-app.md](architecture/external-django-app.md) | The sibling web app's DB footprint and integration points, as seen from this repo |
| [architecture/cli-reference-map.md](architecture/cli-reference-map.md) | Which `com/*.py` script imports which library subsystem (quick lookup table) |

## Living docs

- [workflows.md](workflows.md) — repeatable process patterns (e.g. evaluating a new dependency)
- [STATUS.md](STATUS.md) — current development focus, outstanding tasks, roadmap notes
- [known-issues.md](known-issues.md) — known bugs, doc/code drift, dead code (non-sensitive, technical only)
- [decisions/](decisions/) — ADRs: architectural decisions with rationale and consequences
- [checkpoints/](checkpoints/) — dated development-session summaries (append-only, one file per entry)

## Existing documentation this knowledge base does NOT duplicate

- [GeoDE_User_Manual.md](../GeoDE_User_Manual.md) — operator-facing manual: install, full CLI workflow
  with examples, web interface guide, troubleshooting, DB backup/restore.
- `docs/` — Sphinx autodoc + `sphinx-argparse-cli` reference, generated from docstrings/CLI `--help`.
  Partially stale (see [known-issues.md](known-issues.md)).
- Design docs living next to the code they describe (kept there, linked from the relevant
  `architecture/*.md`): `geode/metadata/DESIGN.md` + `PLANNER_PROMPT.md`,
  `geode/campaign_planner/*_instructions.md`, `geode/etm/stacker/sparse_station_constraints.md`,
  `database/migration_plan_engine_table_naming.md`.

## Maintenance

- Run `python knowledge/scripts/audit.py` periodically (or via `/geode-audit`) for a non-destructive
  consistency report between this knowledge base and the actual repo.
- Write a checkpoint (`knowledge/checkpoints/YYYY-MM-DD-slug.md`) at the end of significant tasks —
  see `/geode-checkpoint`.
- Promote a checkpoint's lasting conclusion into an ADR or an `architecture/*.md` edit deliberately;
  don't auto-promote one-off debugging observations into permanent rules.
