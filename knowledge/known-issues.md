# Known Issues: Doc/Code Drift, Dead Code, Gaps

Technical findings only — purely factual, no session narrative. Append new findings as they're
confirmed; remove an entry once it's actually fixed (and note the fix in a checkpoint, not here).

## Naming drift: Parallel.GAMIT → GeoDE (mostly fixed 2026-11-13)

The project was renamed from "Parallel.GAMIT" to "GeoDE," and further restructured from an even
older `pgamit` package layout. Most of the fallout was fixed as part of the knowledge-base
initiative (see `knowledge/checkpoints/2026-11-13-knowledge-base-bootstrap.md`):

- `docs/modules.rst` toctree title fixed ("Parallel.GAMIT" → "GeoDE").
- `docs/pgamit.rst` renamed to `docs/geode.rst` (its toctree reference in `modules.rst` said
  `geode`, but the file was literally named `pgamit.rst` — a broken toctree entry). Dead entries
  removed (`geode.StationList`, `geode.cluster`, `geode.dbConnection_old`, `geode.plots`, and an
  entire stale `pgamit.*` series pointing at a package that no longer exists). 11 entries with a
  confident current 1:1 top-level equivalent were renamed (`pyOTL`, `pyOkada`, `pyOptions`,
  `pyPPP`, `pyParseAntex`, `pyProducts`, `pyRinex`, `pyRinexName`, `pyRunWithRetry`, `pyStack`,
  `snxParse`).
- `docs/com.rst` had 2 dead entries (`CompareDBs`, `gamit_stats` — neither corresponds to a real
  `com/` script) removed, and 10 real scripts that had never been documented added
  (`CampaignPlanner`, `ConvertMetadata`, `CreateEtmFunctions`, `EtmStacker`, `MergeStation`,
  `ModelProcessing`, `PrintVelocities`, `StationKmz`, `StationReport`, `SyncMetadata`). The newly
  added entries only have the `.. automodule::` block, not the `.. sphinx_argparse_cli::` block
  the other entries have — **someone should verify each of these 10 scripts actually exposes a
  `main()` function matching the expected signature before adding that block** (not verified here).
- `README.md`'s "Support" link fixed (`Parallel.GAMIT` → `geode`).

**Deliberately deferred, not done:** `docs/geode.rst` still only documents flat `geode/` modules
(as it always did) — it has no entries at all for the `etm/`, `gamit/`, `metadata/`,
`campaign_planner/`, `clustering/`, `elasticity/`, `reports/` subpackages (60+ files). A handful of
entries with no confident current equivalent were deleted rather than guessed at
(`pyGamitConfig`/`pyGamitSession`/`pyGamitTask`/`pyGlobkTask` — now `geode/gamit/*`;
`pyStation`/`pyStationInfo`/`pyZTD`/`pyParseZTD` — now `geode/gamit/station.py` /
`geode/metadata/station_info.py` / `geode/gamit/ztd.py` / `parse_ztd.py`; `pyVoronoi`,
`pyTerminal`, `pyTrimbleT0x`, `pyStatic1d`). A proper fix is to run `sphinx-apidoc` fresh against
the current `geode`/`com` packages (Sphinx isn't installed in this environment, so this wasn't
attempted) and manually re-merge the `sphinx_argparse_cli` blocks per `com/` script, since
`sphinx-apidoc` doesn't know about those custom directives.

## Repo hygiene

- Two stale duplicate `*.egg-info` directories exist: `geode.egg-info` (old package name `geode`)
  alongside the current `geode_gnss.egg-info` (`geode-gnss`, matching `pyproject.toml`). Both are
  gitignored build artifacts — harmless, but `geode.egg-info` is leftover cruft from before the
  PyPI package rename and can be deleted without consequence whenever someone's cleaning up.

## Dead/broken code

- `geode/elasticity/elastic_gridder.py` imports `from elastic_interpolation import
  interpolate_at_points` — missing the `geode.elasticity.` package prefix (the function does exist,
  just at the wrong import path). Would raise `ModuleNotFoundError` if ever executed. Currently
  dead: no other module in the repo imports `elastic_gridder`.

## Doc/code drift

- `geode/metadata/DESIGN.md`'s "Trust model" section (per-source auto-apply trust tiers, e.g.
  official IGN-Ar/RAMSAC logs auto-applying INSERT/UPDATE) is **not implemented**. Current code
  applies pending INSERT findings uniformly regardless of source
  (`com/StationInfoEdit.py --apply-pending`). See
  [architecture/metadata-llm-pipeline.md](architecture/metadata-llm-pipeline.md).

## Dependency bookkeeping

- `pyproject.toml` and `environment.yml` both declare dependencies independently; nothing keeps
  them in sync automatically. When adding/removing a dependency, update both or expect drift.

## Database

- `run_db_migrations()` (`geode/dbConnection.py`) is the only schema-change mechanism and has no
  version table — purely existence-checked idempotent DDL. Verify its call site is actually active
  before assuming new migrations will run; it has been observed commented out in the past.
- `gamit_antenna_residuals`'s FK to `gamit_stats` (`(project, subnet, year, doy, system)`) only
  declares `ON DELETE CASCADE`, not `ON UPDATE CASCADE` — the same shape that caused
  `ppp_antenna_residuals` -> `ppp_soln` to crash `station_merge` (fixed 2026-11-13, see
  [checkpoints/2026-11-13-ppp-antenna-residuals-fk.md](checkpoints/2026-11-13-ppp-antenna-residuals-fk.md)).
  Currently harmless because `gamit_stats` is not in `station_merge.TIME_TABLES` and nothing
  `UPDATE`s its key columns in place. Revisit if `gamit_stats` ever becomes mutable by a
  merge-like code path.
