# Patch three gnssmultipath bugs blocking real multi-GNSS RINEX QC backfills

- **Date:** 2026-11-17
- **Branch:** master

## Summary

After the `PRN / # OF OBS` blank-field fixes (`2026-11-17-prn-obs-blank-fix.md`,
`2026-11-17-prn-obs-multiline-fix.md`) landed, Demian re-ran `RinexQC.py usa.gisa -backfill -force
-limit 10` against the real archive and got `ok=0 failed=10` — but with two *new* error signatures
(`IndexError: list index out of range`, `KeyError: 2880`), not the "pop from empty list" crash just
fixed. Traced both directly against the real failing files (via `zcat ... | crx2rnx -` on the
actual archive, per Demian's pointer, rather than guessing from the traceback) to three distinct,
genuine bugs inside `gnssmultipath==2.2.0` itself — none of them caused by or related to GeoDE's
own sanitization. Full root-cause analysis and rationale in
[decisions/0004-patch-gnssmultipath-multisystem-bugs.md](../decisions/0004-patch-gnssmultipath-multisystem-bugs.md);
summary:

1. `rinexReadObsFileHeader211` (`readers/readRinexObs.py`) silently collapses its `GNSSsystems`
   dict down to whichever system was last encountered in the header, on any file with >9 declared
   obs types (multi-line `PRN / # OF OBS`) and more than one GNSS system — caused the `IndexError`
   (and an intermittent `KeyError: 1` elsewhere).
2. `SignalAnalyzer._build_lli_phase_matrix` (`signalAnalysis.py`) assumes a dense epoch-keyed dict
   but a real receiver gap leaves it short — caused the `KeyError: 2880`.
3. `filter_common_gnss_keys` (`GNSS_MultipathAnalysis.py`) filters `GNSSsystems` by value without
   renumbering keys to stay contiguous, breaking later code that assumes `GNSSsystems[sys + 1]` —
   caused the intermittent `KeyError: 1` cleanly once bug 1 was fixed and the real key ordering
   could actually be observed.

Also discovered and ruled out a false alarm along the way: a comment in the installed
`readRinexObs.py` looked exactly like a prior undocumented hand-patch (same explanatory style this
project's commits use) — diffing against a clean `pip download gnssmultipath==2.2.0 --no-binary
:all:` confirmed it's genuinely upstream's own wording, not a local edit. No mystery patch to
chase; said so directly rather than letting it stand as an open question.

## Fix

`scripts/patch_gnssmultipath.py` (new): idempotent, targeted string-replacement patches to the
three functions above, run once after `conda env update`. Each patched file gets a
`# GeoDE patch: see knowledge/decisions/0004` marker so re-runs are no-ops and the patch itself
fails loudly (non-zero exit, `SKIP` lines) if gnssmultipath's source no longer matches what's
expected (e.g. after a version bump). `INSTALL.md` updated to document running it after any
environment (re)build.

Applied to the real `geode` conda env directly (not just tested in isolation) — this unblocks
Demian's actual backfill, not just a future one.

## Tests executed and results

No GeoDE-side code changed (pure third-party patch), so no `geode/tests/` changes. Verification
was entirely against the real files and the real pipeline:

- Isolated repro, before any fix: `gisa2920.26o` (DOY 292, 2026) → `IndexError`;
  `gisa2900.26o` (DOY 290, 2026, with the real `brdc2900.26n` nav file) → `KeyError: 1` then,
  after fixing bug 1 only, `KeyError: 2880`.
- After all three fixes (first via live monkeypatching to iterate quickly, then via the actual
  patch script against the real installed files): both files process cleanly end-to-end
  (`geode.rinex_qc.runner.run()` reaches "Analysis complete" / the deliberately-missing-nav-file
  `FileNotFoundError`, matching the same conclusive-test pattern used for the `pyRinex.py` fixes).
  Re-ran the DOY 290 case 3 times in a row to confirm determinism (the `GNSSsystems` key *order*
  genuinely varies run to run for reasons not fully understood, but all three fixes are correct
  regardless of that order).
- **Real backfill re-run**, same command and same 10 files Demian originally ran:
  `RinexQC.py usa.gisa -backfill -force -limit 10` → `ok=6 partial=3 failed=1` (was `ok=0
  failed=10`). The 3 "partial" rows are pre-existing, correct behavior (`systems_without_orbits`
  genuinely non-empty for those days) and not something this round touched. The 1 remaining
  failure (DOY 296) is the known, scoped follow-up below.

## Known issues / limitations

- A **fourth** bug, in the same family, is documented but deliberately not fixed here: the
  per-epoch satellite loop in `readRinexObs211` doesn't skip satellites whose system was excluded
  via `desiredGNSSsystems` (crashes with `KeyError` on the system letter, and would misalign
  `Obs`/`LLI`/`SS` row indices if naively skipped without also compacting the row index). Only
  reachable when orbit coverage is partial (GeoDE then calls gnssmultipath with a system subset).
  A row-compaction fix was drafted and confirmed to clear that `KeyError`, but it immediately
  exposed a **fifth** bug (`ValueError` in event-flag handling, only reachable once the fourth is
  fixed) that isn't root-caused yet. Demian explicitly chose to ship the three confirmed fixes now
  rather than keep chasing this chain — see `knowledge/known-issues.md` for the scoped writeup.
- `scripts/patch_gnssmultipath.py` must be re-run after any environment rebuild; there's no
  automatic hook for this yet (not wired into `conda env update`, CI, or `dbConnection.py`'s own
  migration-style idempotency pattern). If `RinexQC.py` starts producing the original two crash
  signatures again after an environment change, this is the first thing to check.

## Next steps

- Demian re-runs the full `--backfill -force` (no `-limit`) against `usa.gisa`'s whole archive to
  confirm this holds at scale, then moves on to other stations.
- Whenever someone has time: root-cause the fifth bug (`ValueError` in event-flag handling) and
  finish the fourth bug's row-compaction fix, add both to the same patch script, update ADR-0004
  and `known-issues.md` accordingly.
- Consider filing these upstream (gnssmultipath's GitHub) regardless of whether GeoDE keeps its own
  patch — not done in this session.
