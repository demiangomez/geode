# Architecture Overview

## Identity

GeoDE ("Geodetic Database Engine") was originally named **Parallel.GAMIT**. The rename is not yet
fully propagated through the repo — see [known-issues.md](../known-issues.md). Actual git remote:
`https://github.com/demiangomez/geode`.

Package name on PyPI is `geode-gnss` (see `pyproject.toml`); the import path is `geode`.

## Two-layer structure

- **`com/`** — ~39 CLI scripts, installed as console scripts via `setup.py`'s `scripts=[...]` list
  (there is no `[project.scripts]` entry-point table in `pyproject.toml` — installation relies on
  the legacy `setup.py scripts=` mechanism). Each is a thin `argparse` entry point; business logic
  lives in `geode/`.
- **`geode/`** — the library. Flat core modules directly under `geode/`, plus subpackages for each
  major subsystem: `etm/`, `gamit/`, `metadata/`, `campaign_planner/`, `clustering/`, `elasticity/`,
  `reports/`, `tests/`.

See [cli-reference-map.md](cli-reference-map.md) for which script uses which subsystem.

## No REST API / frontend in this repo

A separate Django web app ("GeoDE Studio"/"GeoDE Desktop") provides the map-based web UI described
in `README.md` and `GeoDE_User_Manual.md` §6. It lives in its own repository (not available here)
and shares this project's Postgres database. See
[external-django-app.md](external-django-app.md).

## Processing pipeline (ops-level; full detail in `GeoDE_User_Manual.md`)

```
DownloadSources.py  →  ArchiveService.py  →  ScanArchive.py  →  ParallelGamit.py  →  ETM fitting  →  Stacker  →  reports/exports
   (fetch raw data)     (ingest to archive)   (RINEX+PPP        (GAMIT/GLOBK         (geode/etm,      (geode/etm/   (PlotETM,
                                                into DB)          network solve,       per station)     stacker,      StationReport,
                                                                  via geode/gamit)                       spatial       KML/KMZ,
                                                                                                          stacking)     Ztd2trp, ...)
```

- `ScanArchive.py` is the data-preparation precursor to `ParallelGamit.py`: it populates RINEX
  records, station info, and PPP coordinates (via GPSPACE or PRIDE, see
  [core-library.md](core-library.md)) that GAMIT sessions depend on. `ParallelGamit.py` has no
  knowledge of RINEX ingestion; `ScanArchive.py` has no knowledge of GAMIT sessions.
- ZTD (zenith tropospheric delay) is a side-output of the GAMIT step: parsed from `.znd` files by
  `geode/gamit/parse_ztd.py` into the `gamit_ztd` table, consumed later by `geode/gamit/ztd.py`
  (analysis) or `com/Ztd2trp.py` (BERNESE `.TRP` export) — independently of the main pipeline.

## Legacy vs. active modules

| Module | Status | Notes |
|---|---|---|
| `geode/pyETM.py` | **Legacy — do not modify.** | Superseded by `geode/etm/` for all new work. Still imported by `com/FixPlate.py`, `PlotMapView.py`, `QueryETM.py`, `TrajectoryFit.py`, `NEQStack.py`, and (mixed with the new engine) `com/Stacker.py`, `com/DRA.py`. |
| `geode/pyStack.py` | **Legacy but still load-bearing — not fully superseded.** | Implements the DRA (polyhedron/network-combination) layer, a different problem than `geode/etm/stacker/` (spatial field stacking of ETM parameters). `com/DRA.py` uses the *new* `geode.etm` engine for per-station fitting **and** `pyStack.Polyhedron`/`np_array_vertices` for combination — both at once. `com/Stacker.py` also still uses it. Don't assume it can be deleted or ignored just because `pyETM.py` is deprecated. |
| `geode/etm/` | Active, actively developed. | See [etm-stacker.md](etm-stacker.md). |
| `geode/network.py` | Active. | GAMIT subnetwork clustering orchestration (Delaunay backbone + cluster recovery), not a general station/network data model — that's `geode/gamit/station.py`. |

## Dependencies / environment

Two parallel dependency declarations exist and must be kept in sync by hand: `pyproject.toml`
(`[project] dependencies`, used by `pip install geode-gnss`) and `environment.yml` (conda/micromamba
env used in CI and recommended for development, see `INSTALL.md`). There is no single source of
truth between them — check both when adding/removing a dependency.

External (non-Python) tool dependencies, not installed by pip/conda: GAMIT/GLOBK, GFZRNX,
rnx2crx/crx2rnx, GPSPACE (and optionally PRIDE PPP-AR). These must be on `PATH` on every compute
node used for parallel processing (checked at startup by `pyJobServer.test_node`, see
[gamit-globk.md](gamit-globk.md)).

## Testing & CI

- `geode/tests/` (pytest): `test_pydate.py`, `test_comparator.py`, `test_make_clusters.py`,
  `test_station_info.py`, `test_station_merge.py`, `test_noise_covariance.py` +
  `test_noise_covariance_jitter.py`, `test_version.py`.
- Coverage gaps: nothing tests `etm_engine`, `DesignMatrix`, the `AdjustmentStrategy` classes, any
  of `geode/etm/stacker/`, `geode/gamit/`, `geode/dbConnection.py`, or any `com/*.py` CLI script
  end-to-end.
- `test_noise_covariance.py` is gated behind an external, not-checked-in reference-vector directory
  (`GEODE_NOISE_TEST_VECTORS`) — it `skipif`s without it.
- CI (`.github/workflows/python-package-conda.yml`): micromamba env from `environment.yml`, flake8
  (hard-fails only on syntax errors `E9,F63,F7`; everything else is advisory), `pytest`, coverage
  report. Triggers on push to `master`/`releases/**` and on PR.
