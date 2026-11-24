# Fix: blank PRN / # OF OBS field crashes gnssmultipath on real CORS station

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Demian's `--backfill` run on `usa.gisa` (after the dispy fix got `JobServer` working) failed on
every single file with `gnssmultipath failed: pop from empty list` -- a different bug than the
`TIME SYSTEM` one from the Python 3.11 migration. Asked for a sample header; `usa.gisa`'s files
are mixed GPS/GLONASS RINEX 2.11 (Trimble NetR9, CORS/NGS/NOAA), and GPS satellites' `PRN / # OF
OBS` record has a **blank** (not `0`) count for the P1 observable -- legal RINEX 2 (this receiver
tracks C1 but, under Anti-Spoofing, never decodes true P1 on GPS), but `gnssmultipath`'s
`readRinexObs.py` tokenizes that record by `line.split(' ')` and `.pop(0)`-ing tokens off the
result. A blank 6-character field doesn't become an empty-string token -- it just vanishes -- so a
GPS line with one missing field has one fewer token than the code expects, and some later
`.pop(0)` in the same parsing loop eventually pops an empty list.

## Fix

`geode/pyRinex.py`: new module-level `_fix_prn_obs_blanks(line)`, wired into `check_header()`'s
"not a required record, pass through" branch for any line ending in `PRN / # OF OBS`. Splits the
line into its fixed 6-character count fields (after the 3-char PRN), finds the last field that
actually has a value, and zero-fills only the *interior* blanks (between two populated fields) --
trailing blanks past the last populated one are left alone, since those are normal padding when a
file declares fewer than 9 observation types (nothing to "fix" there, zero-filling them would
claim observations for codes the file never declared at all).

## Tests executed and results

- `geode/tests/test_pyrinex_prn_obs.py` (new, no external-tool dependency): interior blank
  zero-filled, a fully-populated line left byte-identical, trailing blanks past the declared obs
  count left alone, multiple interior blanks in one line all fixed, label/length preserved exactly.
- `geode/tests/test_pyrinex_header.py`: new end-to-end test using a minimal real-shaped mixed
  GPS/GLONASS fixture (header fields taken directly from Demian's pasted `usa.gisa` sample) through
  the actual `pyRinex.ReadRinex` pipeline -- confirms the fix is applied to the GPS line and the
  already-correct GLONASS line is untouched.
- **Direct confirmation against the real failure**: ran the fixed header's output through
  `geode.rinex_qc.runner.run()` with a deliberately nonexistent nav file path. Execution proceeded
  all the way through `gnssmultipath` reading and parsing the *observation* file (where it used to
  crash) and only failed at the expected point -- `FileNotFoundError` trying to open the missing
  *navigation* file. This is the conclusive test: the observation-header crash is gone; what's left
  is an unrelated, expected error from a path I deliberately didn't give a real file.
- Full `geode/tests/` suite: 217 passed, 0 failed, 127 skipped (unrelated).

## Known issues / limitations

- Not yet confirmed against the *actual* `usa.gisa` files in a real backfill run (this fix was
  built and verified from the header sample Demian pasted, not the live files) -- next step is
  Demian re-running `--backfill` (now benefiting from the earlier `--limit`/traceback-capture
  commit if anything else turns up).
- Only `PRN / # OF OBS` was fixed. `# OF SATELLITES` (a single count, no per-field blanks possible)
  wasn't a problem and needed no change. If another RINEX 2 header record with the same
  "legally-blank-but-naively-tokenized" shape turns up, the fix pattern here (find the last
  populated field, zero-fill interior blanks only) is the one to reuse.

## Next steps

- Demian re-runs `RinexQC.py usa.gisa -backfill` (after `git pull`) to confirm real files process
  successfully now.
