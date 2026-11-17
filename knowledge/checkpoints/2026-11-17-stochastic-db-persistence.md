# Persist stochastic_noise to the DB; rename stochastic_signal.py/StochasticSignal -> stochastic.py/Stochastic

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Follow-up to the same day's
[2026-11-17-stochastic-model-values-default-fix.md](2026-11-17-stochastic-model-values-default-fix.md).
Previously, `StochasticSignal.p.params[component]` held only the signal (a 1D array, one value
per day of `time_vector_cont_mjd`), and the noise lived in a separate, DB-unaware plain attribute
(`noise_params`) that was always reset to zero on a cached DB load (since reconstructing it
correctly needed re-running `estimate_noise_model()`). The user asked to store both consistently:
`params[component]` is now a `(2, n)` array — row 0 signal, row 1 noise — saved to and restored
from the same `etms` row (`object='stochastic'`) as a single unit, like any other function's
params. Also renamed `geode/etm/etm_functions/stochastic_signal.py` to `stochastic.py` per the
user's explicit request, since the module now stores both components, not just the signal.
Initially left the class named `StochasticSignal` (only the file renamed) and flagged the
inconsistency rather than silently extending the rename — the user then asked for the class to be
renamed too, so it's now `Stochastic` as well, everywhere (import in `etm_engine.py`,
instantiation, docstrings/comments, `__repr__`). The one thing deliberately **not** renamed: the
`crc32(f'stochastic_signal'...)` hash-seed string in `rehash()` — it's an arbitrary cache-key
literal, not tied to the class name, and changing it would invalidate every station's existing
cached GLS/collocation hash, forcing an unnecessary re-fit everywhere.

## Files / modules affected

- `geode/etm/etm_functions/stochastic_signal.py` → `geode/etm/etm_functions/stochastic.py`
  (`git mv`, history preserved). `StochasticSignal` renamed to `Stochastic`:
  - Dropped `noise_params`/`set_noise_params()`; replaced with `set_params(stochastic_signal,
    stochastic_noise)`, which stores both as `self.p.params[component] = np.vstack([signal_row,
    noise_row])`, normalizing any missing/wrong-shape input to zero.
  - `eval()` reads row `_SIGNAL_ROW` (0), new `eval_noise()` reads row `_NOISE_ROW` (1).
  - Fixed `get_design_ts()` in passing: it was unreachable dead code (StochasticSignal.fit is
    always `False`, so `DesignMatrix.matrix` never calls it — confirmed by reading
    `design_matrix.py`'s `active_functions = [f for f in self.functions if f.fit]` filter) that
    could not have actually worked if it ever were called (it indexed the plain list
    `self.p.params` with a boolean array, which Python lists don't support). Now returns the
    `(len(ts), 0)` "no columns" shape `DesignMatrix` uses elsewhere for an inactive function,
    rather than reproducing the broken logic. No behavior change (still never called).
- `geode/etm/core/etm_engine.py` — import path updated to `..etm_functions.stochastic`, class
  reference updated to `Stochastic`.
- `geode/etm/least_squares/least_squares.py` — `EtmFit.load_results_to_functions()`: replaced the
  two-call `f.p.params = [...]; f.set_noise_params([...])` with one `f.set_params([signal...],
  [noise...])` call.
- `geode/etm/data/etm_database.py`:
  - `load_parameters_db()`: after the generic `funct.p.params[i] = np.array(obj['params'][i])`
    load, if `object == 'stochastic'`, checks whether the loaded array is `(2, n)` (current
    format: split into `signal_i`/`noise_i`) or anything else (legacy row saved before this
    change: treat as signal-only, noise = zeros, and normalize `funct.p.params[i]` to `(2, n)` in
    place so `eval_noise()` doesn't crash on an old row). Sets both
    `etm_results.results[i].stochastic_signal` and `.stochastic_noise` from this.
  - Removed the now-stale comment claiming `stochastic_noise` "is not reconstructed on a cached
    load" — it now is, straight from the DB row, no recomputation needed.
  - `save_parameters_db()` needed **no change** — it's already generic
    (`cnn.insert('etms', **funct.get_parameter_dict())` per function), and `get_parameter_dict()`
    already serializes whatever shape `self.p.params[i]` is via `.tolist()`.
- `geode/etm/core/data_classes.py` — updated `AdjustmentResults.stochastic_noise`'s comment (no
  longer "not reconstructed on a cached DB load").
- `knowledge/architecture/etm-stacker.md` — updated the `etm_functions/` directory map entry.

## Architectural decisions

- **No DB schema/migration change needed.** `etms.params` is declared `numeric[]` (confirmed by
  reading `database/gnss_data_dump.sql`, not edited) — Postgres array columns aren't fixed to a
  particular number of dimensions at the type level, only required to be internally rectangular
  per value. The existing storage convention (one row per component, each a 1D list — see
  `dbConnection._decimal_to_float`'s "list of lists" comment) already relies on this; nesting one
  level deeper (component → [signal_row, noise_row] → day) is the same mechanism, confirmed by
  testing `get_parameter_dict()`'s `.tolist()` round-trip on a `(2, n)` array.
- **Backward compatibility with pre-existing cached rows** (saved before this change, 1D
  signal-only): handled by shape-sniffing in `load_parameters_db()` rather than a migration or a
  version flag — simplest option given this is diagnostic data that gets silently regenerated
  (signal recomputed correctly, noise defaults to zero) the next time a station's ETM is
  reprocessed from scratch.
- **Class renamed too, on explicit follow-up request.** First pass only renamed the module (per
  the literal ask) and flagged that the class name was now inconsistent with it; the user
  confirmed they wanted the class renamed as well, so `StochasticSignal` → `Stochastic`
  everywhere it's referenced (confirmed via grep after: only `etm_engine.py` imports/instantiates
  the class directly, plus a handful of comments/docstrings in `stochastic.py` itself,
  `data_classes.py`, and `etm_database.py`).

## Tests executed and results

- `python -m pytest geode/tests/ -q` → 208 passed, 127 skipped (unchanged, re-run after both the
  params restructuring and the class rename).
- Manual script: constructed a `Stochastic` with explicit signal/noise arrays, confirmed
  `self.p.params[i].shape == (2, 10)`; confirmed `eval()`/`eval_noise()` read the right row;
  round-tripped through `get_parameter_dict()` (what actually gets inserted) and reconstructed a
  `(2, n)` array from the serialized nested list, matching the original values; simulated a
  legacy 1D DB row and confirmed the shape-sniffing fallback normalizes it to `(2, n)` with noise
  zeroed, without crashing `eval_noise()`; confirmed `set_params(None, None)` (nothing estimated
  yet) still normalizes cleanly to zero. Re-ran the same checks after the class rename.
- **Not tested: an actual DB round-trip** (no live Postgres connection in this environment) — the
  psycopg2 adaptation of a 3-level nested Python list into a Postgres array was reasoned about
  (standard `cursor.execute(query, values)` parameter binding, no GeoDE-specific array handling
  code in `Cnn.insert()`) but not exercised against a real database.

## Known issues / limitations

- No live-DB verification of the save/load round-trip (see Tests). Worth doing on the next real
  `-lsq gls` run against the database, with `-no_save` removed so a fresh fit actually writes
  `object='stochastic'` with signal+noise, followed by a second run that loads it back from cache.

## Next steps

- Run a real `-lsq gls` fit against the DB (no `-no_save`), then a second run that hits the cache,
  and confirm `stochastic_noise` comes back correctly (non-zero, matching the fresh fit) rather
  than defaulting to zero.
