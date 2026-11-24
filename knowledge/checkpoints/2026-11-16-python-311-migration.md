# Migrate GeoDE to Python 3.11, drop basemap and usgs-libcomcat

- **Date:** 2026-11-16
- **Branch:** master

## Summary

Started as a dependency evaluation for the RINEX QC module (`gnssmultipath==2.2.0` needs
`numpy>=2.1.0`, conflicting with GeoDE's `numpy==1.26.4` pin). Tracing the real blocker led to a
full Python 3.10→3.11 migration: see `knowledge/decisions/0002-python-311-migration.md` for the
decision and full rationale. This checkpoint is the session log — what was actually done, tested,
and verified.

## Files / modules affected

- `pyproject.toml`, `environment.yml` — `requires-python`/`python` bumped to `>=3.11`/`3.11`,
  `numpy` pin bumped to `2.4.6`, `basemap`/`usgs-libcomcat` removed, `netifaces` pin bumped to
  `0.11.0` (the version that builds under 3.11; `0.10.9` predates manylinux wheels for it, but
  builds fine from source regardless).
- `com/UpdateEarthquakes.py` — rewritten off `libcomcat` onto direct USGS ComCat REST calls
  (FDSN event query + detail/product feed via `requests`). Same public interface
  (`AddEarthquakes`), same retry-on-connection-error behavior, same moment-tensor QuakeML parsing.
- `geode/clustering/plots.py` — `mpl_toolkits.basemap.Basemap` → `cartopy.crs`/`cartopy.feature`.
  `Basemap(projection=...)` objects → `ccrs.NorthPolarStereo`/`SouthPolarStereo`/`Geostationary`
  (`Geostationary` needs an explicit `ccrs.Globe(semimajor_axis=6378137.00,
  semiminor_axis=6356752.3142)` to match Basemap's `rsphere=ref_r`). `proj(lons, lats)` →
  `proj.transform_points(ccrs.PlateCarree(), lons, lats)[:, :2]`. `fig.add_subplot(sub)` + 
  `proj.drawcoastlines()` → `fig.add_subplot(sub, projection=proj)` + `ax.coastlines()` +
  `ax.add_feature(cfeature.LAND/LAKES, ...)`.
- `com/TrajectoryFit.py` — deleted a dead `from mpl_toolkits.basemap import Basemap` import (0
  call sites in the file).
- `docs/conf.py` — removed the now-stale `libcomcat`/`libcomcat.search`/`libcomcat.exceptions`/
  `mpl_toolkits.basemap` entries from `MOCK_MODULES`.
- `README.md`, `GeoDE_User_Manual.md` — "Python 3.10" prerequisite/notes updated to 3.11 (4
  occurrences).
- `knowledge/decisions/0002-python-311-migration.md` — new ADR.

## Architectural decisions

See ADR-0002. Summary: bump Python rather than isolate `gnssmultipath` in its own venv (rejected
twice), and drop `usgs-libcomcat` rather than chase its `GDAL`/`openquake.engine` pin further
(that chain turned out to be the actual blocker, not numpy directly).

## Tests executed and results

All exploratory/risky steps ran in disposable conda environments first
(`geode-py311-spike`, a `--clone` of the original `geode` env, and a throwaway `py311-test`) —
never the working `geode` env — before anything was applied for real. Both environments were
removed after use.

- `netifaces==0.11.0`: builds from source under Python 3.11 (confirmed in the spike). Also
  confirmed it's optional in `dispy`/`pycos` (`try/except ImportError` in `pycos/netpycos.py`,
  only gates IPv6 interface discovery) — not a hard blocker either way.
- Full dependency install (`environment.yml` + `pyproject.toml`, minus `basemap`/
  `usgs-libcomcat`, numpy bumped) under Python 3.11: `pip check` → no broken requirements.
  `gnssmultipath==2.2.0` itself installed cleanly in this set (the actual point of the exercise).
- `com/UpdateEarthquakes.py` replacement verified against the **live USGS API**: real M6+ event
  search (16 events found over a 30-day window), a real moment-tensor QuakeML fetch + parse
  (nodal-plane strike/dip/rake extracted correctly), and the no-moment-tensor code path (2 of 16
  events correctly raised/handled `ProductNotFoundError`).
- `geode/clustering/plots.py` cartopy port: ran `plot_global_network` against synthetic
  station/cluster data, visually inspected the output PNG — all 6 projections (2 polar + 4
  geostationary) rendered coastlines/lakes/nodes/edges correctly. Found and fixed one real bug in
  the port along the way: a `set_extent` lon/lat box on the two polar-stereographic projections
  produced a blank/empty plot (a known cartopy gotcha for azimuthal projections) — switched to
  `ax.set_global()` instead, which renders correctly (see "Known issues" below for the resulting
  framing difference).
- `geode/tests/` full suite: **167 passed, 0 failed, 127 skipped** (DB-dependent, no local
  Postgres available) — run both in the disposable spike env and again against the real, rebuilt
  `geode` env after cutover. Identical results both times.
- Manual `dispy` parallel-dispatch smoke test (not covered by the existing test suite): started a
  real local `dispynode` daemon, built a `dispy.JobCluster`, submitted 5 jobs, confirmed correct
  results via `cluster.wait()`/`job.result`. Run both in the spike env and again against the real
  env post-cutover — both passed (`[0, 1, 4, 9, 16]`).
- Real env cutover: old `geode` conda env cloned to `geode-py310-backup` (kept, not deleted) before
  removing and recreating `geode` from the updated `environment.yml`, then `pip install -e .` for
  the package itself + remaining `pyproject.toml`-only dependencies. Re-ran `pip check` + the full
  test suite + the `dispy` smoke test against the real rebuilt env — all passed identically to the
  spike.

## Known issues / limitations

- `geode/clustering/plots.py`'s polar views no longer approximate Basemap's `boundinglat=10/-10`
  framing — they now show the full globe (`ax.set_global()`). Visually different crop, not a
  functional regression; revisit only if the exact framing matters to someone reading these plots.
- `cartopy` fetches Natural Earth coastline/lake shapefiles from the network on first use (cached
  locally after). `basemap` bundled this data. Irrelevant unless a fully air-gapped GeoDE
  deployment exists — none known.
- The other ~20 dependencies not called out above (scipy, scikit-learn, scikit-image, shapely,
  geopandas, obspy, h5py, paramiko, psycopg2-binary, cryptography, textual, rich,
  earthscope-cli/sdk, pysftp, simplekml, xmltodict, munch, requests, dirsync, geopy,
  country-converter, charset-normalizer, staticmap, weasyprint, pillow) were verified only by
  "installs clean + full test suite passes," not individually exercised feature-by-feature. If
  something in one of those subsystems breaks later, check here first before assuming it's
  unrelated to this migration. `geopandas` now resolves to using `pyogrio` instead of `fiona`+GDAL
  for its backend (a side effect of removing `usgs-libcomcat`'s `fiona>=1.8.20` pin) — no code in
  this repo calls `fiona` directly, so this should be transparent, but worth knowing if a
  geopandas-related bug ever surfaces.
- `pandas` (unpinned in `pyproject.toml`) resolved to a 3.x release (major version bump from the
  2.x that was likely installed before) — the test suite passed, but pandas 2→3 is a real major
  version jump; worth keeping in mind if a pandas-related bug surfaces in code this test suite
  doesn't cover.
- `geode-py310-backup` conda env exists as a rollback path. Remove it once the Python-3.11 `geode`
  env has been used in real work for a while without issues.

## Next steps

- Resume the RINEX QC module's own dependency evaluation (`gnssmultipath` as a new dependency) —
  it should now install in-process without conflict; write its own ADR per
  `knowledge/workflows.md`, referencing ADR-0002 for the Python-version prerequisite rather than
  re-explaining it.
- Decide when to remove `geode-py310-backup`.
