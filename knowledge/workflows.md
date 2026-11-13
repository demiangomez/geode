# Recurring Workflows

Repeatable patterns for things that come up more than once, so each one doesn't get re-decided
from scratch. Add a new section here when a one-off decision turns out to recur.

## Evaluating whether to add a new package/dependency

**Do this in a session rooted in this repo** (open a new Claude Code session with `geode` as the
working directory, same as any other session) — not a disconnected session elsewhere. `CLAUDE.md`
and `knowledge/` only auto-load when the session is rooted here, and that context (pinned
dependency versions, overlapping existing modules, legacy gotchas) is exactly what prevents a
naive evaluation from missing something — e.g. proposing a new elastic-deformation library without
knowing `geode/elasticity/` already has four models of differing integration status (see
[architecture/campaign-clustering-elasticity.md](architecture/campaign-clustering-elasticity.md)).

1. **Scouting** (optional, no repo context needed): read the candidate's docs, license,
   maintenance status. This part genuinely doesn't need to happen "in" geode.
2. **Fit evaluation** — once you're testing it against real GeoDE code/data or considering what it
   would replace:
   - Isolate the spike: either a disposable branch (`git checkout -b spike/<package-name>`,
     delete if rejected) or an isolated worktree if you want the main working tree completely
     untouched.
   - Before writing spike code, check `knowledge/architecture/*.md` and `knowledge/known-issues.md`
     for overlap — don't re-propose something that already exists or re-hit a known dead end.
   - Check `pyproject.toml` and `environment.yml` for version conflicts with existing pins.
   - Write the minimal spike needed to answer "does this actually solve the problem," not a full
     integration.
3. **Record the decision as an ADR** under `knowledge/decisions/` (`TEMPLATE.md`) **regardless of
   outcome.** Accepted or rejected, the "why" is the valuable part — a rejected ADR saves a future
   session from re-evaluating the same thing without that context.
4. **If accepted:** update *both* `pyproject.toml` and `environment.yml` (they don't stay in sync
   automatically — see `known-issues.md`), update the relevant `architecture/*.md` if it changes
   how a subsystem works, write a checkpoint, then merge the branch.
5. **If rejected:** delete the spike branch/worktree, keep only the ADR.
