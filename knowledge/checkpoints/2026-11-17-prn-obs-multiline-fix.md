# Fix: blank continuation-line PRN / # OF OBS record still crashed gnssmultipath

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Follow-up to `2026-11-17-prn-obs-blank-fix.md`. Demian re-ran `--backfill` on `usa.gisa` after
that fix shipped and still hit `gnssmultipath failed: pop from empty list` on every file ("It
seems this package is very unreliable"). He pasted a second real header sample
(`gisa0860.26d.Z`): this file declares **11** observation types, so RINEX 2's `PRN / # OF OBS`
record wraps onto a second physical line per satellite with a blank PRN field (same as `# /
TYPES OF OBSERV` itself). GLONASS satellite R01 has zero observations on *both* codes that land
on its continuation line (S2, S5) — making that entire physical line blank, not just one field
between two populated ones.

The first fix (`_fix_prn_obs_blanks`, a pure line-local function) explicitly left *trailing*
blanks — after the last populated field on a line — untouched, reasoning they were normal padding
for a file declaring fewer than 9 obs types. That reasoning doesn't hold for a continuation line:
when nothing on the line is populated, there's no "last populated field" to anchor interior vs.
trailing, so the function treated the whole line as trailing padding and left it blank. Confirmed
the actual crash point by reading the installed `gnssmultipath/readers/readRinexObs.py` directly
(not just inferring from the traceback): `rinexReadObsFileHeader211` tokenizes each `PRN / # OF
OBS` line with `line.split(' ')` and `.pop(0)`s the first token as the system letter. A wholly
blank line produces an empty list after filtering, so `.pop(0)` is the literal crash.

## Fix

`geode/pyRinex.py`: replaced `_fix_prn_obs_blanks()` with a stateful `_PrnObsFixer` class,
instantiated once per `check_header()` call. It tracks:
- `total_obs_types`, parsed from `# / TYPES OF OBSERV`'s count field (blank on *that* record's
  own continuation lines — only the first physical line carries the count).
- `_consumed`, a running count of obs-type slots seen for the current satellite, reset to 0
  whenever a new PRN appears (non-blank PRN field) and incremented by 9 (or fewer, on the last
  line) after each physical line.

For any physical `PRN / # OF OBS` line, `real_slots = min(9, total_obs_types - consumed)` — the
number of slots on *this* line that are genuinely observation counts (should be `0` if blank) as
opposed to unused padding. This works whether or not any slot on the line already has a value,
fixing the wholly-blank-continuation-line case the first fix couldn't handle. Falls back to the
old content-sniffing behavior (last populated field marks the boundary) if `# / TYPES OF OBSERV`
was somehow never seen.

Also fixed a provenance gap found while verifying this: the `PRN / # OF OBS` branch in
`check_header()` never set `self.header_fixed = True` (the `TIME SYSTEM` branch does). Since
`rinex_qc_run.header_fixed` is read straight from this attribute
(`geode/rinex_qc/pipeline.py:94-96`), every file that needed only this fix would have under-reported
`header_fixed=False` in its QC provenance. Now set whenever `_PrnObsFixer` actually changes a line.

## Tests executed and results

- `geode/tests/test_pyrinex_prn_obs.py` (rewritten for the new class): interior blank, fully
  populated line (byte-identical), trailing padding on a file with fewer declared types, a wholly
  blank continuation line, a partially populated continuation line, PRN-reset between satellites,
  and the no-`# / TYPES OF OBSERV`-seen fallback. 7/7 pass.
- `geode/tests/test_pyrinex_header.py`: new
  `test_prn_obs_blank_continuation_line_fixed_for_real_multiline_system_file`, built
  programmatically (not hand-typed — a hand-typed attempt at this fixture is what cost the most
  time here; computing fixed-width fields in Python and asserting them against the known-good
  7-obs-type fixture first caught the mistake before it reached a committed test) rather than
  pasted raw text, since a terminal/copy-paste round-trip silently drops trailing whitespace and
  real RINEX records depend on exact column position. Confirms: (1) the raw, unfixed fixture
  reproduces the real `IndexError: pop from empty list` directly against
  `gnssmultipath.readers.readRinexObs.rinexReadObsFileHeader211`; (2) after `pyRinex.ReadRinex`,
  `header_fixed is True` and R01's continuation line is correctly zero-filled; (3) the same
  `rinexReadObsFileHeader211` call now succeeds on the fixed file. 1/1 pass.
- **Direct confirmation against the real failure** (manual, not a committed test): ran the fixed
  11-obs-type file through `geode.rinex_qc.runner.run()` with a deliberately nonexistent nav file
  path. `gnssmultipath` read the entire observation file — header *and* all 240 epochs of data —
  and only failed at the expected `FileNotFoundError` on the missing nav file. Same conclusive
  pattern used to verify the first fix.
- Full `geode/tests/` suite: 220 passed, 0 failed, 127 skipped (unrelated — missing optional deps).

## Known issues / limitations

- Still only verified against synthetic fixtures built from Demian's two pasted header samples,
  not the literal `usa.gisa` archive files — next step is Demian re-running `--backfill` against
  the real files.
- If a third shape of this bug turns up (e.g. a file declaring >18 obs types, wrapping to a third
  physical line), `_PrnObsFixer`'s `real_slots = min(9, total_obs_types - consumed)` logic should
  already generalize — it doesn't assume exactly two physical lines — but hasn't been exercised
  against that case.

## Next steps

- Demian re-runs `RinexQC.py usa.gisa -backfill -force` (after `git pull`) to confirm the real
  archive files — both the single-line (7-obs-type) and multi-line (11-obs-type) shapes — now
  process successfully end to end.
- Once `usa.gisa` backfills cleanly, move on to the dynamic visualization backend module (deferred
  until backfill succeeds, per Demian's own sequencing).
