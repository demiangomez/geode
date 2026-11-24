# Fix: dispy/pycos break every parallel JobServer on Python 3.11

- **Date:** 2026-11-17
- **Branch:** master

## Summary

Demian ran `RinexQC.py usa.gisa -backfill` for real (the first real-deployment test of the
Python 3.11 migration's parallel-dispatch path) and hit an immediate crash building the
`JobServer`:

```
AttributeError: module 'inspect' has no attribute 'getargspec'
...
Exception: Invalid "cluster_status" function; it must take excatly 3 arguments
```

`dispy` 4.15.2 (latest on PyPI) and `pycos` 4.12.2 both still call the long-removed
`inspect.getargspec()` (deprecated since Python 3.0, finally removed in 3.11) to validate the
`job_status`/`cluster_status` callbacks passed to `dispy.JobCluster()`. `pyJobServer.py` always
passes `cluster_status=self.check_cluster` — so **every** `JobServer(..., run_parallel=True)`
(the default) was broken on Python 3.11, repo-wide, not just for RinexQC. `ArchiveService.py`,
`ScanArchive.py`, `ParallelGamit.py` — anything that builds a parallel `JobServer` — would have hit
this identically.

This is a real gap in the 2026-11-16 Python 3.11 migration's own validation: that session's dispy
smoke test called `dispy.JobCluster()` **without** a `cluster_status` callback, so it never
exercised the code path that actually broke (`pyJobServer.py`'s own usage always sets one). The
migration checkpoint explicitly flagged "the actual pyJobServer/dispy parallel dispatch path ...
wasn't exercised" as a known gap for exactly this reason — this is that gap surfacing.

## Fix

`geode/pyJobServer.py` now shims `inspect.getargspec` back onto the `inspect` module, before
`import dispy`, guarded by `hasattr(inspect, 'getargspec')` (so it's a no-op if a future
dispy/pycos release fixes this upstream, or on a Python version where it still exists):

```python
if not hasattr(inspect, 'getargspec'):
    _ArgSpec = collections.namedtuple('ArgSpec', ['args', 'varargs', 'keywords', 'defaults'])
    def _getargspec_shim(func):
        spec = inspect.getfullargspec(func)
        return _ArgSpec(spec.args, spec.varargs, spec.varkw, spec.defaults)
    inspect.getargspec = _getargspec_shim
```

This is a monkeypatch on the stdlib `inspect` module itself (not just dispy's local reference), so
it also fixes the identical bug inside `pycos` (3 more call sites, for its optional `transform`/
`categorize` channel features) for free.

## Tests executed and results

- Directly replicated dispy's own validation assertions (`len(args.args) == 4` for a bound
  3-argument method, `args.varargs is None`, `args.keywords is None`, `args.defaults is None`)
  against the shimmed `inspect.getargspec()` output for a `check_cluster(self, status, node, job)`-
  shaped method and a `job_status(self, job)`-shaped one -- both match exactly.
- **Real end-to-end test against the user's own already-running `dispynode` process**
  (`140.254.252.156:9701`, found already up during this session -- not started by this session, not
  touched/restarted): built a real `dispy.JobCluster(square, nodes=['140.254.252.156'],
  cluster_status=<bound method>, ...)` -- the exact call shape that crashed -- submitted 5 jobs,
  got correct results back (`[0, 1, 4, 9, 16]`).
- Scanned both `dispy` and `pycos` for other known Python-3.11/3.12-removed stdlib APIs
  (`collections.Callable/Mapping/Iterable/Sequence`, `asyncio.coroutine`, `.isAlive()`,
  `time.clock()`, `platform.linux_distribution()`) -- none found. `inspect.getargspec` was the only
  one, confirmed at exactly 5 call sites (2 in dispy, 3 in pycos), all covered by this single shim.
- Full `geode/tests/` suite: 211 passed, 0 failed, 127 skipped (unrelated).

## Known issues / limitations

- Not re-tested: the full `pyJobServer.JobServer.__init__()` path itself (`test_node()` and
  everything downstream), since this sandbox still lacks the GAMIT/PPP/ATX files that function
  checks even with `--noparallel` (same constraint noted in the Phase 3 checkpoint). This fix
  specifically targets the `dispy.JobCluster()` construction call that was actually failing in
  Demian's traceback, verified against a real running node -- but the very next step in
  `JobServer.__init__` (`test_node()` checks) still needs a real deployment to exercise.
- `dispy`/`pycos` appear unmaintained (4.15.2/4.12.2 are the latest PyPI releases, with no sign of
  a Python-3.11-aware update); this shim is a permanent fixture until that changes or GeoDE moves
  off dispy, not a temporary workaround for a soon-to-be-fixed upstream bug.

## Next steps

- Demian re-runs `RinexQC.py usa.gisa -backfill` to confirm `JobServer` now builds successfully and
  the backfill actually proceeds.
- If anything else dispy/pycos-related surfaces during that real run, it's likely another
  Python-3.11-removed-API gap in the same family -- check `inspect`/`collections`/`asyncio` usage
  in both packages first.
