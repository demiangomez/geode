# Fix: az/el/MP and SNR arrays could silently disagree in shape

- **Date:** 2026-11-18
- **Branch:** master

## Summary

A real `--backfill -force` run against `usa.gisa`'s whole archive (5207 files, the first full run
since the detrending fix the previous day) surfaced a new, different error on a number of files:

```
ValueError: operands could not be broadcast together with shapes (785,16) (786,16)
```

at `aggregate.aggregate_signal()`'s `snr_valid = valid & np.isfinite(snr) & (snr != 0)`. Unlike the
previous day's crash, this one was already caught by `pipeline.process_one_file()`'s own outer
exception handler and correctly recorded as `status='failed'` with the full traceback as
`message` — the batch didn't lose other files' data, this file's QC just didn't get written.

Root cause: `runner._build_systems()` pivots `az`/`el`/`MP`/slip-flags from the `--qcmode` call's
dataframe and SNR from the separate plain `rnxcsv` call's dataframe, independently, each via
`pandas.pivot_table(index='time', ...)`. Each pivot's row count comes from the unique `time`
values *within that specific dataframe* — confirmed on real files (DOY 285-288/2015, among others)
that the two separate `ringo rnxcsv` invocations don't always agree on the exact set of epochs
written out for the same system, off by one or two rows. Nothing before this assumed otherwise, so
the mismatch only surfaced as a shape error deep inside `aggregate_signal()`, with no earlier
assertion to catch it closer to the source.

## Fix

`geode/rinex_qc/runner.py`: `_build_systems()` now computes one explicit `time_index` per system
(`pd.Index(sorted(set(sub['time']) | set(obs_sub['time'])))` — the union of both calls' times) and
passes it into every `_pivot()` call for that system (`az`, `el`, each band's `MP`/slip, and SNR).
`_pivot()` now reindexes rows against this shared index, not just columns against `prns` as
before, so every array for a system ends up the same shape by construction regardless of which
epochs either individual `rnxcsv` call happened to include.

## Tests executed and results

- `geode/tests/test_rinex_qc_runner.py`: added `test_pivot_reindexes_to_the_given_time_index_not_just_its_own`
  (a dataframe missing one time entirely still reindexes to the full shared index, filling `NaN`
  for the missing row); updated the three existing direct `_pivot()` call sites for the new
  required `time_index` parameter.
- Full `geode/tests/` suite: 230 passed, 0 failed, 127 skipped (unrelated).
- **Real re-verification**: re-ran DOY 285-288/2015 (from the real failures, identified via
  `SELECT ... WHERE message LIKE '%broadcast together%'` against the live `rinex_qc_run` table)
  through both `runner.run()` directly and the full `pipeline.process_one_file()` — all four now
  return `status='ok'` with sane per-signal stats, no shape error.

## Next steps

- Demian re-runs the real `--backfill -force` again; this and the previous day's detrending fix
  together should clear both error classes seen in the first full-archive attempt.
