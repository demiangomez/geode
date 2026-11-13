---
name: geode-audit
description: Run the GeoDE knowledge-base consistency audit (knowledge/scripts/audit.py) and turn its findings into a reviewed report — flagging doc/code drift, stale Sphinx references, missing CLI documentation coverage, and repo hygiene issues. Use when the user asks to audit, validate, or check the knowledge base, or periodically as part of maintenance.
---

# GeoDE Knowledge Base Audit

1. Run the audit script from the repo root:

   ```bash
   python knowledge/scripts/audit.py
   ```

2. The script is **non-destructive** — it only reads files and prints a markdown report. It never
   modifies anything itself.

3. Read every warning and verify it's a real issue before acting — the script does simple
   pattern-matching (file-path existence, Sphinx `automodule` targets, markdown link resolution,
   CLI script coverage), so it can have false positives (e.g. a code snippet that happens to look
   like a file path). Don't mechanically "fix" every warning; use judgment.

4. For each genuine finding:
   - If it's a small, clearly-correct fix (e.g. a single stale link, a clearly dead reference),
     fix it directly and mention what you changed.
   - If it's ambiguous or requires a judgment call the user should make (e.g. which of several
     possible current modules a stale doc reference should now point to), report it and ask rather
     than guessing.
   - Log anything you're not fixing right now in `knowledge/known-issues.md` (only if it isn't
     already there) rather than letting it disappear once the audit report scrolls away.

5. Report a short summary to the user: how many findings, how many you fixed vs. left for review,
   and point them at `knowledge/known-issues.md` for anything deferred.

6. Do not commit any fixes automatically — let the user review the diff first, unless they've
   explicitly asked you to commit.

## Extending the audit

If you find a *new class* of drift this script doesn't catch (e.g. a new kind of stale reference),
prefer adding a check function to `knowledge/scripts/audit.py` (follow the existing
`check_*(root, report)` pattern) over doing a one-off manual grep — it should get more thorough
over time, not stay static.
