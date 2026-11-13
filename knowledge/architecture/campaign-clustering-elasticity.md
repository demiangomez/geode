# Campaign Planner, Clustering & Elasticity

Three independent subpackages, grouped here only because each is small — they don't share code
with each other (clustering ↔ elasticity: zero mutual imports).

## `geode/campaign_planner/` — field-visit logistics (no LLM, today)

A `CAMPAIGN` station (per the README's `CHL:CAMPAIGN` station-type filter) is a non-continuous GNSS
monument that must be physically visited and surveyed, unlike `CONTINUOUS`/`CORS` stations.

`plan_campaign()` (`planner.py`) is a pure, web-safe function (raises `CampaignPlannerError`,
never calls `sys.exit` — deliberately, for future reuse from a web view): resolves GeoDE station
coordinates and/or free-form "new site" waypoints, geocodes start/end cities, orders stops with a
nearest-neighbour TSP (`services.order_stations_tsp`), fetches driving legs from a public OSRM
instance, schedules a multi-day visit plan respecting work hours, renders a self-contained HTML
report with an embedded Leaflet map. **No LLM anywhere in this path** — deterministic
geocoding+TSP+routing+templating. `com/CampaignPlanner.py` is a thin CLI wrapper around it.

Two plan modes, documented in-package: [`campaign_plan_mode_instructions.md`](../../geode/campaign_planner/campaign_plan_mode_instructions.md)
describes the implemented "active" mode above. [`passive_campaign_plan_mode_instructions.md`](../../geode/campaign_planner/passive_campaign_plan_mode_instructions.md)
opens with **"Status: NOT YET IMPLEMENTED — Design only"** and describes a harder problem
(deploying N instruments across S≫N unattended sites needing ≥48h occupation each, async
collect/redeploy chaining) — for *that* harder scheduling problem, the design explicitly proposes
"Claude API owns the planning logic, deterministic code owns geometry and rendering." If asked to
work on "the passive campaign planner," there is no existing implementation to extend — this is a
greenfield build against that design doc.

## `geode/clustering/` — GAMIT subnetwork partitioning (geometric only)

Stations are grouped purely by geographic proximity into size-constrained subnetworks — not by
tectonic plate or kinematic behavior, and **unrelated to the Stacker's leave-one-out k-matrix**
(different subsystem, different purpose, no shared imports).

- `agglomerative.py` — `DeterministicClustering.constrained_agglomerative()`: spatially-constrained
  agglomerative clustering with centroid snapping, target cluster size ± tolerance.
- `cluster.py` — `BisectingQMeans` (constrained bisecting k-means) alternative, plus
  `overcluster()`/`prune()`/`select_central_point()` for building overlapping "tie" clusters so
  adjacent subnetworks share stations (needed for inter-subnetwork network-adjustment ties).

Rationale: GAMIT can't process arbitrarily large networks in one run. `com/ModelProcessing.py` is
the sole consumer — clusters the day's active stations, estimates per-cluster GAMIT run time from a
quadratic fit on cluster size, emits KMZ/plots for QA.

## `geode/elasticity/` + `geode/pyOkada.py` — elastic Earth-deformation models

Several independent models of differing vintage/integration status:

| Module | Model | Integration status |
|---|---|---|
| `elastic_interpolation.py` | Sandwell & Wessel (2016) "gpsgridder": thin-plate-spline interpolation of 2-D velocity fields via elastic Green's functions | **Integrated** — imported by `geode/etm/stacker/grid_system.py` |
| `diskload.py` | Bevis, Melini & Spada (2016): layered-Earth response to a circular disk load | **Integrated** — same import site |
| `rectloadhs.py` | Becker & Bevis (2002), Love's problem: half-space response to rectangular pressure loads | **Integrated** — same import site |
| `DLN_func.py` / `green_func.py` | Zhou, Pan & Bevis (2019) dislocation Love numbers for a layered, transversely-isotropic, self-gravitating Earth (newer, 2025, more rigorous than Okada) | **Not integrated** — no inbound references found anywhere else in the repo |
| `elastic_gridder.py` | — | **Dead code with a latent bug**: `from elastic_interpolation import interpolate_at_points` is missing the `geode.elasticity.` package prefix — would raise `ModuleNotFoundError` if ever executed. Nothing currently imports this module, so it's never been hit. See [known-issues.md](../known-issues.md). |
| `pyStatic1d.py` | Wraps an external `stat0A` binary for 1-D Green's-function files | Belongs to the older "Parallel.Archive" lineage, unrelated to the ETM/elasticity pipeline proper |
| `geode/pyOkada.py` | Okada (1985) homogeneous elastic half-space dislocation (ported from M. Bevis's MATLAB) + "s-score" earthquake-relevance scoring (Wells & Coppersmith 1994 fault-dimension scaling) | **Integrated** — see below |

### Connection to ETM's earthquake handling (real, load-bearing)

`geode/etm/stacker/grid_system.py` imports `get_qpw`/`get_radius`/`spline2dgreen`
(`elastic_interpolation`), `compute_diskload`/`load_love_numbers` (`diskload`), and
`mrectloadhs_dif` (`rectloadhs`) to back the Stacker's three selectable vertical-interpolation
methods (`diskload`/`spline2d`/`rectload`, exposed as a CLI choice in `com/EtmStacker.py`).
`geode/etm/stacker/constraints/sw_okada.py` (the `SWOkada` leave-one-out mixin used by
`CoseismicConstraint`/`PostseismicConstraint`) combines `pyOkada.okada` as physics-based
regularization with the Green's-function/spline interpolation above to build the leave-one-out
K-matrix for interpolating coseismic/postseismic amplitudes across the spatial grid. In short: the
Stacker's spatial handling of earthquake jumps is literally built on `pyOkada` +
`elastic_interpolation`, not a separate model.
