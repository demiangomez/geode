# ADR-0002: Migrate GeoDE to Python ≥3.11

- **Status:** accepted
- **Date:** 2026-11-16
- **Branch (if not master):** —

## Context

Building the RINEX QC module (see `knowledge/checkpoints/2026-11-16-python-311-migration.md` for
the session this came out of) required adopting `gnssmultipath==2.2.0` as the QC computation
engine. `gnssmultipath` declares `numpy>=2.1.0` (every release back to 1.6.1 does — this is not a
version-specific quirk), which conflicts with GeoDE's `numpy==1.26.4` pin.

Tracing the actual conflict (in disposable conda environments, never the working `geode` env)
showed numpy wasn't the real constraint: `usgs-libcomcat` (a dependency used only by
`com/UpdateEarthquakes.py`, for two calls — `libcomcat.search.search()` and
`get_product_bytes()`, no rupture-modeling features) pulls in `esi-utils-rupture`, whose
`numpy<2.0` pin only lifts at version ≥1.0.8 — which itself requires Python ≥3.11. GeoDE targeted
Python 3.10 (`environment.yml`, `pyproject.toml`).

## Decision

Bump GeoDE's minimum Python to 3.11 (`pyproject.toml: requires-python = ">=3.11"`,
`environment.yml: python=3.11`), bump `numpy` to `2.4.6`, and remove two dependencies that had no
path forward under the new numpy/Python version regardless of how they were reached:

- **`basemap==1.4.0`** — removed. Its only live usage (`geode/clustering/plots.py`, 6
  `Basemap(...)` calls for polar-stereographic/geostationary projections) was ported to `cartopy`
  (already a GeoDE dependency, already used the same way in
  `geode/etm/visualization/field_plot/map_renderer.py`). A second, dead `Basemap` import in
  `com/TrajectoryFit.py` (0 call sites) was simply deleted.
- **`usgs-libcomcat`** — removed. `com/UpdateEarthquakes.py` was rewritten to call the USGS ComCat
  FDSN event web service and detail/product feed directly via `requests` (already a dependency),
  replicating exactly the two calls it used. Verified against the live USGS API (real M6+ event
  search, a real moment-tensor QuakeML fetch and parse, and the no-moment-tensor code path).

## Rationale

Two other paths were considered and rejected before settling on the full migration (see
Alternatives below). The migration itself was de-risked empirically, in disposable conda
environments cloned/created for this purpose only, before ever touching the real `geode` env:

- The single highest-risk item going in was `netifaces` (used by `dispy`/`pycos.netpycos` — the
  networking layer under `pyJobServer`, GeoDE's entire parallel-dispatch backbone — for node/
  interface discovery), which has no prebuilt wheel for Python 3.11+ on PyPI. It turned out to be
  a non-issue: `netifaces` is wrapped in `try/except ImportError` in `pycos/netpycos.py` (only
  affects IPv6 discovery, which GeoDE's cluster setup — explicit per-node IPs from
  `gnss_data.cfg`, not broadcast discovery — doesn't rely on), and it also builds cleanly from
  source under Python 3.11 regardless.
- Dropping `usgs-libcomcat` (rather than chasing its GDAL/`openquake.engine` pin further, or
  bumping Python further to satisfy it) was the actual blocker that mattered: `esi-utils-rupture`
  ≥1.0.8 transitively requires `openquake.engine>=3.23`, which pins `GDAL~=3.13.1` to an exact
  system `libgdal` version — a large, open-ended dependency chain (a full seismic-hazard-analysis
  platform) to support two simple REST calls GeoDE doesn't even use the hazard features of.
- Verification before cutover: full `geode/tests/` suite (167 passed, 0 failed, 127 skipped —
  DB-dependent) and a manual end-to-end `dispy` cluster smoke test (real `dispynode`, real job
  submit/dispatch/callback) both passed in a disposable spike env first, then again against the
  real, rebuilt `geode` env after cutover.

## Consequences

- Unblocks adding `gnssmultipath` as a dependency (tracked in its own ADR, since it's a separate
  "should we add this package" decision — this ADR is only the prerequisite that makes it
  possible).
- `com/UpdateEarthquakes.py` no longer depends on `libcomcat`'s retry/pagination/parsing
  conveniences — the replacement is a direct, minimal translation of the same two calls, not a
  general-purpose ComCat client.
- `geode/clustering/plots.py`'s polar-stereographic views (`npstere`/`spstere` → `NorthPolarStereo`/
  `SouthPolarStereo`) no longer approximate Basemap's `boundinglat` framing — they show the full
  globe (`ax.set_global()`) instead of a boundinglat-clipped view, since a lon/lat `set_extent` box
  on a polar azimuthal projection produced a degenerate (blank) extent. Visually different framing,
  same information content.
- `cartopy` downloads Natural Earth coastline/lake shapefiles over the network on first use
  (cached after); `basemap` bundled its own low-resolution coastline data. Only relevant for a
  fully air-gapped deployment, none known to exist.
- **Gap found after the fact (2026-11-17):** this ADR's own `dispy` smoke test didn't pass a
  `cluster_status` callback to `dispy.JobCluster()`, so it missed that `dispy`/`pycos` both still
  call the Python-3.11-removed `inspect.getargspec()` in that exact code path -- which
  `pyJobServer.py` always exercises (`cluster_status=self.check_cluster`). This broke **every**
  parallel `JobServer` repo-wide (`ArchiveService.py`, `ScanArchive.py`, `ParallelGamit.py`, ...),
  not just RinexQC, and only surfaced on a real deployment run. Fixed with a monkeypatch shim in
  `pyJobServer.py`; see
  [checkpoints/2026-11-17-dispy-getargspec-fix.md](../checkpoints/2026-11-17-dispy-getargspec-fix.md).
- Every other pinned/unpinned dependency was re-verified empirically (full install + `pip check` +
  test suite) rather than individually re-checked by metadata lookups — see the checkpoint for the
  full list.
- The old Python-3.10 `geode` conda env is kept as `geode-py310-backup` pending Demian's
  confirmation that the Python-3.11 env is working well in practice, then should be removed.

## Alternatives considered

- **Isolate `gnssmultipath` in its own venv/subprocess**, leaving GeoDE's Python version, numpy
  pin, and `usgs-libcomcat` untouched entirely. Rejected (after being offered twice) in favor of
  fixing the underlying conflict — Demian chose to invest in the larger, correct fix rather than a
  workaround specific to one new dependency.
- **Keep chasing the GDAL/`openquake.engine` pin** (install an exact matching `libgdal` via
  conda-forge and continue resolving whatever constraint came next). Rejected once it became clear
  `openquake.engine` is a large platform with its own deep dependency tree, pulled in only to
  support two REST calls — an open-ended effort for no real benefit over rewriting those two calls.
