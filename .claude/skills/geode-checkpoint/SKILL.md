---
name: geode-checkpoint
description: Write a development checkpoint for GeoDE at the end of a significant task — summary of changes, files/modules affected, decisions, tests run, known issues, and next steps. Use when wrapping up a non-trivial GeoDE task, or when the user asks to "checkpoint" or "log" the session's work.
---

# GeoDE Checkpoint

Write one file to `knowledge/checkpoints/YYYY-MM-DD-<short-slug>.md` (use today's date and a
3-5 word kebab-case slug of the task, e.g. `2026-11-13-stacker-sparse-stations.md`). Don't edit a
prior checkpoint — each task gets its own file (append-only history, avoids merge conflicts).

Only do this for genuinely significant work — a real feature, a bug fix with non-obvious root
cause, an architectural change, or a multi-step investigation. Skip it for trivial one-line fixes
or pure Q&A sessions with no code change.

## Required sections

```markdown
# <Task title>

- **Date:** YYYY-MM-DD
- **Branch:** <git branch>

## Summary

1-3 sentences: what changed and why, in plain language.

## Files / modules affected

List paths, grouped by subsystem if it spans more than one.

## Architectural decisions

Only if a real decision was made (not just "I wrote a function"). If one was significant enough
to outlive this task, write a proper ADR under `knowledge/decisions/` instead/in addition, and
link it here.

## Tests executed and results

What was run (pytest targets, manual verification steps) and the outcome. If nothing was tested,
say so explicitly — don't omit this section.

## Known issues / limitations

Anything left imperfect, deliberately deferred, or discovered-but-not-fixed. Cross-reference
`knowledge/known-issues.md` if it belongs there too (add it there, not just here, if it's a
standing issue rather than specific to this task).

## Next steps

Concrete, actionable — not "keep improving this." If there's a clear next session's worth of
work, say what it is.
```

## After writing the checkpoint

- If a finding belongs in `knowledge/known-issues.md` (a bug, doc drift, dead code — something
  that will matter to someone other than this task), add it there too.
- If `knowledge/STATUS.md`'s "Active focus" section is now stale because of this task, update it —
  but keep it short; move resolved items out rather than letting it grow.
- Do **not** promote a one-off debugging observation or isolated experiment into an ADR or an
  `architecture/*.md` edit automatically. That promotion is a deliberate judgment call — make it
  explicitly, and only for conclusions that generalize beyond this one task.
- Do not commit anything automatically. Let the user review and commit the checkpoint themselves
  unless they've explicitly asked you to commit.
