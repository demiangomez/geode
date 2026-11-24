# GeoDE

Python/PostgreSQL framework for parallel GNSS data processing (GAMIT/GLOBK, PPP, trajectory
modeling). CLI scripts in `com/`, library in `geode/`.

**Start here:** [knowledge/INDEX.md](knowledge/INDEX.md) — the project knowledge base. Read it
before diving into a subsystem; it links to the right `knowledge/architecture/*.md` file instead
of making you rediscover the codebase from scratch.

## Hard rules

- **Never modify `geode/pyETM.py`.** Legacy, superseded by `geode/etm/`. All ETM work goes there.
- **`geode/pyStack.py` is legacy but still load-bearing** — don't assume it's dead just because
  `pyETM.py` is. See [knowledge/architecture/etm-stacker.md](knowledge/architecture/etm-stacker.md).
- **Schema changes go through `run_db_migrations()`** in `geode/dbConnection.py` (idempotent,
  existence-checked DDL — no version table, no framework). Never hand-edit
  `database/gnss_data_dump.sql`; it's a `pg_dump` snapshot, not a migration source.
- **Check `git branch` before trusting "current" architecture claims.** Work on the ETM Stacker in
  particular has previously existed only on feature branches and been reverted on `master` — see
  [knowledge/STATUS.md](knowledge/STATUS.md).
- **No REST API or frontend lives in this repo.** The web UI ("GeoDE Studio"/"GeoDE Desktop") is a
  separate Django app in another repository, sharing this project's Postgres database. See
  [knowledge/architecture/external-django-app.md](knowledge/architecture/external-django-app.md).

## Maintaining the knowledge base

- Evaluating whether to add a new dependency? See
  [knowledge/workflows.md](knowledge/workflows.md) first — there's a consistent pattern for this.
- End of a significant task: write a checkpoint (`knowledge/checkpoints/YYYY-MM-DD-slug.md`) — see
  `/geode-checkpoint`.
- Found a bug, doc drift, or dead code while working on something else: add it to
  `knowledge/known-issues.md` rather than fixing it as a side quest, unless asked.
- A real architectural decision (not a one-off debugging observation): add an ADR under
  `knowledge/decisions/` using `knowledge/decisions/TEMPLATE.md`.
- Periodically run `/geode-audit` (or `python knowledge/scripts/audit.py`) to check the knowledge
  base against the actual repo.
