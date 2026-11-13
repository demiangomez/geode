# GAMIT/GLOBK Integration & Parallel Processing

## `geode/gamit/` package

| File | Purpose |
|---|---|
| `gamit_config.py` | `GamitConfiguration(ReadOptions)` — parses the per-run `session.cfg` (`[gamit]`, `[network]`, `[Archive]`), validates `process_defaults`/`sestbl.` templates exist, merges in the global `gnss_data.cfg`. |
| `gamit_session.py` | `GamitSession` — one GAMIT sub-network-for-one-day *problem definition*: builds the directory structure, OTL list, station info, `.apr`/`sittbl.`, links GAMIT tables, copies `sestbl.`/`process.defaults`/ATX, generates `sitedef`, parses the resulting SINEX. Owns a `GamitTask`. |
| `gamit_task.py` | `GamitTask` — the *executable unit*: copies the prepared session folder to a (possibly remote) node, fixes symlinks, fetches orbits/RINEX, invokes `sh_gamit` via `subprocess`, parses the summary, combines systems. |
| `globk_task.py` | `Globk` — combines multiple sessions' `h*.glx` H-files via GLOBK (writes/runs `globk.sh`), parses the resulting SINEX. Skipped when a day only has one session. |
| `parse_ztd.py` | `ParseZtdTask.execute()` — parses `.znd` zenith-delay files, upserts into `gamit_ztd`. |
| `station.py` | `Station`/`StationInstance`/`StationCollection` — station metadata needed to build a session (coordinates, RINEX availability, duplicate-code aliasing). This is the actual station/network *entity model*, distinct from `geode/network.py`'s clustering logic. |
| `ztd.py` | `Ztd`/`ZtdSoln` — reads `gamit_ztd` back out, fits polynomial+periodic models for QC/plotting (reuses `pyETM`). |

**Session vs. task:** a session is the staged-but-not-yet-run problem; a task is the dispatched,
executing unit. **GAMIT vs. GLOBK:** GAMIT solves each regional sub-network session independently
per day; GLOBK combines that day's sessions' H-files into one polyhedron/SINEX solution (only
needed when a day was split into >1 sub-network).

## `com/ParallelGamit.py` — main orchestrator

High-level flow: parse CLI args (date range/DOY list, exclude/check-mode, purge, dry-run, KML,
`--noparallel`) → open DB, resolve station aliases for duplicate codes → load
`GamitConfiguration(session.cfg)` → build a `pyJobServer.JobServer` (validates every compute node
before any work starts — GAMIT tables for the target year/EOP, software sync) → resolve station
list, optionally purge existing solutions → `execute_gamit()` (builds `GamitSession`/`GamitTask`
objects via `Network`/clustering, submits `GamitTask.start(...)` jobs; callback inserts into
`gamit_stats`, logs FATAL errors) → `execute_globk()` (groups sessions by day, submits
`Globk.execute()` jobs for multi-session days) → `exec_parse_ztd()` (submits `ParseZtdTask.execute()`
jobs).

GAMIT/GLOBK are **external binaries**, never Python-bound — GeoDE shells out
(`subprocess.Popen`/`subprocess.run`) to `sh_gamit` and `globk`. Templates from
`configuration_files/` (`process.defaults`, `sestbl.`) are copied near-verbatim into each session's
`tables/` directory before invocation.

## `com/ScanArchive.py` — ingestion precursor

Walks the archive tree, inserts RINEX records, scans/ingests station-info metadata, computes OTL
coefficients, runs PPP (via `pyPPP.RunPPP`) to populate `ppp_soln`/`ppp_antenna_residuals`. Has no
knowledge of GAMIT sessions — it's the data-preparation step `ParallelGamit.py` depends on (see
[overview.md](overview.md)).

## Parallel dispatch — `pyJobServer.py` / `pyRunWithRetry.py`

`JobServer` wraps **dispy** (coroutine networking via **pycos**):

- Startup: builds a `dispy.JobCluster(test_node, servers, ...)`, discovers nodes, runs a
  `test_node()` self-check on each remote node (imports, DB connectivity, archive paths, required
  executables, PPP/ATX files) — **failing nodes abort startup**.
- `create_cluster(function, deps, callback, ..., modules=...)`: ships the target function plus
  listed `deps` (e.g. `GamitTask`/`GamitSession`/`Globk` classes) to nodes via dispy's serialization;
  `cluster.send_file('gnss_data.cfg', node)` ships the DB config separately. Built with
  `reentrant=True` — jobs on disconnected/dead nodes get automatically rescheduled elsewhere.
- `submit(*args)` pickles args and sends to an available node; when `run_parallel` is disabled, runs
  synchronously in-process instead.
- Results arrive via dispy's `callback` mechanism (`job.result`/`job.exception` per finished job),
  not polling.

**Retry logic** is mostly in `pyRunWithRetry.RunCommand.run_shell()`: wraps `subprocess.run` with up
to 2 retries on `TimeoutExpired` and `OSError` errno 35 (resource temporarily unavailable).

## ZTD workflow

GAMIT writes per-station zenith delays into a GLOBK `.znd` file per session →
`geode/gamit/parse_ztd.py` parses `ATM_ZEN X` lines, resolves station aliases, upserts into
`gamit_ztd` (run as a parallel job at the end of a `ParallelGamit.py` run) → either
`geode/gamit/ztd.py` (analysis/plotting) or `com/Ztd2trp.py` (BERNESE `.TRP` export, a separate
standalone step) consume the DB table.

## Configuration files

- `configuration_files/gnss_data.cfg` — global config consumed by `geode/pyOptions.ReadOptions`
  (`[postgres]`, `[archive]`, `[otl]`, `[ppp]`). `GamitConfiguration` inherits all of this.
- `configuration_files/process.defaults` / `sestbl.` — **native GAMIT/GLOBK settings files**, not
  parsed by Python beyond existence-checking (validated via the session-level `session.cfg`'s
  `[gamit]` keys `process_defaults`/`sestbl`) and copied into each session folder with a single
  line-rewrite (blanking the scratch directory).
