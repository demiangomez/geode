# Bootstrap the GeoDE persistent knowledge base

- **Date:** 2026-11-13
- **Branch:** master

## Summary

Built a version-controlled, persistent knowledge-management system for GeoDE so future sessions
don't have to rediscover the architecture from scratch: a `knowledge/` directory (architecture
docs, living status/known-issues files, an ADR log, dated checkpoints), a root `CLAUDE.md`, two
project skills (`/geode-checkpoint`, `/geode-audit`), a `SessionStart` + `PostToolUse` hook pair,
and a non-destructive audit script. Along the way, found and fixed real doc/code drift (stale
Sphinx references, a broken toctree, a stale README link) using the audit script itself as the
verification tool.

## Files / modules affected

**New — knowledge base:**
- `knowledge/INDEX.md`, `knowledge/STATUS.md`, `knowledge/known-issues.md`
- `knowledge/architecture/{overview,core-library,database-schema,etm-stacker,gamit-globk,metadata-llm-pipeline,campaign-clustering-elasticity,external-django-app,cli-reference-map}.md`
- `knowledge/decisions/{TEMPLATE,0001-sparse-station-stacking-approach}.md`
- `knowledge/scripts/audit.py`
- `knowledge/checkpoints/` (this file)

**New — Claude Code integration:**
- `CLAUDE.md` (repo root)
- `.claude/settings.json` (SessionStart + PostToolUse hooks)
- `.claude/hooks/session_start.sh`, `.claude/hooks/schema_or_deps_reminder.py`
- `.claude/skills/geode-checkpoint/SKILL.md`, `.claude/skills/geode-audit/SKILL.md`

**Modified:**
- `.gitignore` — `.claude/` was entirely blanket-ignored (nothing under it was version-controlled);
  narrowed to ignore only `.claude/settings.local.json` and `.claude/scheduled_tasks.lock` so
  project-level Claude Code config can actually be committed.
- `README.md` — stale GitHub support link (`Parallel.GAMIT` → `geode`).
- `docs/modules.rst` — stale title (`Parallel.GAMIT` → `GeoDE`).
- `docs/pgamit.rst` → renamed to `docs/geode.rst` (file name didn't match what `modules.rst`'s
  toctree expected — a broken reference) and had ~20 dead/stale module entries removed or renamed.
- `docs/com.rst` — 2 dead entries removed, 10 real-but-undocumented CLI scripts added.
- `knowledge/architecture/cli-reference-map.md` — fixed during the audit's own self-test (a row
  that incorrectly implied `com/ConvertRaw.py` exists; added `UpdateEarthquakes.py` and a few other
  missing scripts).

## Architectural decisions

- Knowledge base lives at top-level `knowledge/`, not under `.claude/` or inside `docs/` — kept
  distinct from the Sphinx API-reference tree and visible to any contributor, not just Claude
  sessions. Confirmed with the user before building.
- `.gitignore` fix scoped narrowly (only the two genuinely-personal/ephemeral files stay ignored)
  rather than un-ignoring `.claude/` wholesale with no exclusions.
- Migrated the technical substance of a prior private Claude-memory entry (sparse-station stacking
  investigation) into `knowledge/decisions/0001-sparse-station-stacking-approach.md`, stripped of
  session/interpersonal narrative — repo ADRs carry technical reasoning only; collaboration-style
  preferences stay in Claude's private cross-session memory, not the repo.
- Did not attempt a full `sphinx-apidoc` regeneration for `geode/etm/`, `geode/gamit/`, etc.
  (60+ files, and Sphinx isn't installed in this environment to verify a build) — scoped the doc
  fix to removing/correcting definitively dead or unambiguous entries, and logged the larger
  regeneration as a deferred follow-up in `knowledge/known-issues.md` rather than guessing at
  module mappings I couldn't verify.

## Tests executed and results

- `python knowledge/scripts/audit.py` run repeatedly during development to find and fix the
  script's own false positives (overly broad path-matching regex, a reverse-check that scanned the
  wrong markdown column). Final clean run: 2 warnings left, both legitimate advisories, not bugs
  (a cross-repo GPSPACE link worth a human glance, and harmless stale `*.egg-info` build cruft).
- No Python test suite changes were made; `geode/tests/` untouched. Did not run `pytest` or
  `sphinx-build` (Sphinx isn't installed in this environment) — **the `docs/*.rst` edits are
  unverified against an actual Sphinx build.** Recommend running `sphinx-build` before trusting
  the generated HTML renders cleanly.

## Known issues / limitations

- See `knowledge/known-issues.md` for the full list (egg-info cruft, `elastic_gridder.py`'s broken
  import, metadata trust-tier doc/code drift, the deferred full Sphinx apidoc regeneration, the
  10 newly-documented `com/` scripts missing their `sphinx_argparse_cli` blocks pending
  verification that each exposes a `main()`).
- The knowledge base itself is new and unvalidated by real use — its structure/granularity may
  need adjusting once a few real sessions actually try to use it to onboard onto a task.

## Next steps

- Use the knowledge base on a real task next session and see whether `knowledge/INDEX.md` actually
  gets you to the right doc fast enough; adjust granularity if not.
- Decide whether to verify/add the `sphinx_argparse_cli` blocks for the 10 newly-documented `com/`
  scripts in `docs/com.rst`.
- Decide whether/when to tackle the full `geode/etm/` + `geode/gamit/` + other-subpackage Sphinx
  apidoc regeneration (needs Sphinx installed to verify).
- Nothing has been committed — review the diff and commit when ready.
