# GeoDE – Campaign Plan Mode (Phase 1 + 2)
## Implementation Instructions for Claude Code

---

## Critical Working Rule — Ask Before Implementing

GeoDE is a large, complex codebase with many existing utilities, modules, and helpers.
**Before implementing anything from scratch, always search the codebase first and ask:**

> "I need to do X. Does GeoDE already have a module, utility, or helper for this?
> I found [list what you found]. Should I use one of these or implement new?"

This applies specifically to (but is not limited to):
- Database connections and query helpers
- HTTP request utilities
- Configuration/settings loading
- Logging
- File path handling
- Any geo or coordinate math utilities
- Argument parsing patterns (check how other scripts in `com/` handle this)

Do not assume something doesn't exist just because you haven't seen it yet.
When in doubt, `grep -r` the codebase before writing new code.

---

## Goal

Add `com/CampaignPlanner.py` to GeoDE — a standalone Python script consistent with
the other tools in the `com/` directory. It:

1. Accepts inputs via command-line switches and/or a JSON config file
   (switches take priority over JSON when both are provided)
2. Connects to the GeoDE PostgreSQL database using the existing DB connection module
3. Calls free OSM-based web APIs for geocoding and routing
4. Computes an optimally ordered, multi-day visit plan
5. Writes a single self-contained HTML file as output — no server needed,
   opens by double-clicking in any browser

---

## Step 0 — Explore Before You Code

Before writing a single line, perform these explorations and confirm your findings:

### 0a. Understand the `com/` directory conventions
- Read several existing scripts in `com/` to understand structure and style
- Note how they handle argument parsing (argparse? custom? what switch naming style?)
- Note how they handle logging, printing progress, and exiting on error
- **Match these conventions exactly** in `CampaignPlanner.py`

### 0b. Find and inspect `setup.py`
- Read `setup.py` to understand how existing `com/` scripts are registered
- Add `CampaignPlanner.py` to the appropriate entry point list following the exact
  same pattern as the other entries
- Ask before touching `setup.py` if anything is unclear

### 0c. Find the database connection module
- Search for GeoDE's existing DB connection module (may be `dbConnection`,
  `db_connection`, `database`, or similar)
- Understand how it is imported and used in other `com/` scripts
- **Use this module.** Do not implement a new database connection.

### 0d. Find any existing geo/coordinate utilities
Search for existing implementations of haversine distance, coordinate helpers,
or any existing use of the OSRM or Nominatim APIs. Use them if found.

### 0e. Find any existing HTTP request wrappers
Check if GeoDE wraps `requests` with retry logic, logging, or custom headers.
Use it if found.

### 0f. Confirm the `stations` table columns
Find existing queries against the `stations` table in the codebase to confirm
exact column names. Expected columns:
```
"NetworkCode", "StationCode", "StationName", lat, lon
```

---

## Deliverables

```
com/
    CampaignPlanner.py              ← new script, consistent with other com/ tools

geode/campaign_planner/
    __init__.py
    services.py                     ← pure Python business logic (no DB, no I/O)
    report.py                       ← HTML generation

setup.py                            ← add CampaignPlanner entry point (existing file)

example_campaign.json               ← example config file (place wherever fits conventions)
```

`geode/campaign_planner/` follows the same pattern as other supporting modules
under `geode/`. Confirm this by checking what other `com/` scripts import from
`geode/` before creating the package structure.

---

## Input: JSON Config File + Command-Line Switches

### JSON config format (`example_campaign.json`)

```json
{
    "_comment": "Station IDs are NetworkCode.StationCode as they appear in GeoDE",
    "start_city": "Buenos Aires, Argentina",
    "end_city": "San Juan, Argentina",
    "stations": ["arg.RWSN", "arg.UNSJ", "arg.VMOL", "arg.LJAR"],
    "time_on_site_minutes": 120,
    "day_start": "08:00",
    "hard_stop": "20:00",
    "fuel_cost_per_km": 0.15,
    "start_date": "2025-09-01",
    "output_file": "campaign_plan.html"
}
```

### Command-line switches

Match the argument naming style found in other `com/` scripts. The expected switches are:

| Switch | Type | Maps to config key |
|---|---|---|
| `--config` | path | *(loads JSON file)* |
| `--start-city` | string | `start_city` |
| `--end-city` | string | `end_city` |
| `--stations` | string, one or more | `stations` (space-separated list of `NET.CODE`) |
| `--time-on-site` | int (minutes) | `time_on_site_minutes` |
| `--day-start` | string `HH:MM` | `day_start` |
| `--hard-stop` | string `HH:MM` | `hard_stop` |
| `--fuel-cost` | float | `fuel_cost_per_km` |
| `--start-date` | string `YYYY-MM-DD` | `start_date` |
| `--output` | path | `output_file` |

### Merge logic (switches override JSON)

```python
# 1. Start with defaults
config = DEFAULT_CONFIG.copy()

# 2. Load JSON if --config provided, merge over defaults
if args.config:
    with open(args.config) as f:
        json_config = json.load(f)
    # Strip keys starting with "_" (comments)
    config.update({k: v for k, v in json_config.items() if not k.startswith("_")})

# 3. Apply any explicit switches on top (only if the user actually passed them)
if args.start_city is not None:
    config["start_city"] = args.start_city
if args.end_city is not None:
    config["end_city"] = args.end_city
# ... etc for all switches
```

### Defaults

```python
DEFAULT_CONFIG = {
    "time_on_site_minutes": 120,
    "day_start": "08:00",
    "hard_stop": "20:00",
    "fuel_cost_per_km": 0.0,
    "start_date": None,   # required — no default
    "output_file": "campaign_plan.html",
}
```

Required fields (no default, must come from JSON or switch):
`start_city`, `end_city`, `stations`, `start_date`

Validate all inputs before making any API calls. Print all validation errors at once,
then exit cleanly.

---

## services.py — Pure Business Logic

No database access, no file I/O. Just functions that take data and return data.

### `haversine_km(a: dict, b: dict) -> float`

Standard haversine. `a` and `b` have `"lat"` and `"lon"` keys (decimal degrees).
Returns distance in km. (Check Step 0d — may already exist in GeoDE.)

### `order_stations_tsp(origin: dict, stations: list) -> list`

Nearest-neighbor greedy TSP heuristic starting from `origin`.
Use `haversine_km` for straight-line distances only — do NOT call OSRM here.

```python
def order_stations_tsp(origin, stations):
    remaining = list(stations)
    ordered = []
    current = origin
    while remaining:
        nearest = min(remaining, key=lambda s: haversine_km(current, s))
        ordered.append(nearest)
        remaining.remove(nearest)
        current = nearest
    return ordered
```

### `geocode_city(city_name: str) -> dict`

Call Nominatim (free, no API key):
```
GET https://nominatim.openstreetmap.org/search
    ?q={city_name}&format=json&limit=1
User-Agent: GeoDE-CampaignPlanner/1.0
```
Return `{"name": str, "lat": float, "lon": float}`.
Raise `ValueError` with a clear message if not found. 10-second timeout.

### `fetch_osrm_leg(point_a: dict, point_b: dict) -> dict`

Call the OSRM public routing API (free, no key):
```
GET https://router.project-osrm.org/route/v1/driving/{lon_a},{lat_a};{lon_b},{lat_b}
    ?overview=full&geometries=geojson
User-Agent: GeoDE-CampaignPlanner/1.0
```
Return:
```python
{
    "distance_km": float,
    "drive_minutes": int,
    "geometry": [[lon, lat], ...]
}
```
Raise `RuntimeError` on HTTP error or timeout. 10-second timeout.

### `compute_plan(params: dict, ordered_waypoints: list, legs: list) -> dict`

`params` — validated config values.
`ordered_waypoints` — `[origin] + ordered_stations + [destination]`, each a dict
with `"name"`, `"lat"`, `"lon"`, `"type"` (`"origin"` | `"station"` | `"destination"`).
`legs` — list of OSRM leg dicts in waypoint order, pre-fetched by the main script.

#### Multi-day scheduling algorithm

```
current_time = datetime(start_date, day_start_time)
hard_stop_dt = datetime(start_date, hard_stop_time)
overnight_position = origin
day_number = 1
current_day_stops = []
days = []
```

For each leg `i` (from `waypoints[i]` to `waypoints[i+1]`):

```
arrival = current_time + timedelta(minutes=legs[i].drive_minutes)
is_last = i == len(legs) - 1

IF is_last:
    record destination stop with arrival time
    close day → append to days
    break

# This stop is a GNSS station
departure = arrival + timedelta(minutes=time_on_site)

IF arrival > hard_stop_dt:
    # Can't reach this station today
    close current day (even if empty — note "travel day")
    start new day from overnight_position
    re-fetch OSRM leg: overnight_position → waypoints[i+1]
    recompute arrival with new day's start time
    continue loop with re-fetched leg (do not advance i)

ELIF departure > hard_stop_dt:
    # Can arrive but site work overruns
    record stop with WARNING: "Work extends past stop time"
    close day after this station
    overnight_position = this station
    start new day from this station

ELSE:
    # Normal
    record stop
    current_time = departure
    overnight_position = this station
```

**Key rule:** a new day always starts from `overnight_position` — the last station
reached the previous day (or origin if no stations were visited yet).
When a day is cut, the OSRM leg from the overnight position to the next unvisited
station must be re-fetched (call `fetch_osrm_leg` again).

#### Return structure

```python
{
    "days": [
        {
            "day_number": int,
            "date": "YYYY-MM-DD",
            "stops": [
                {
                    "type": "origin" | "station" | "destination",
                    "name": str,
                    "lat": float,
                    "lon": float,
                    "arrival": "HH:MM" | None,
                    "departure": "HH:MM" | None,
                    "leg_km": float,
                    "leg_drive_minutes": int,
                    "leg_fuel_cost": float,
                    "warning": str | None,
                    "geometry": [[lon, lat], ...]
                }
            ],
            "day_total_km": float,
            "day_total_drive_minutes": int,
            "day_total_fuel_cost": float,
        }
    ],
    "summary": {
        "total_km": float,
        "total_drive_minutes": int,
        "total_fuel_cost": float,
        "total_days": int,
        "total_stations": int,
    }
}
```

---

## CampaignPlanner.py — Main Script

### Execution flow

1. Parse arguments (argparse, matching `com/` conventions found in Step 0a)
2. Merge JSON config + switches into final `config` dict
3. Validate all required fields; print all errors and exit if any
4. Connect to DB using GeoDE's existing connection module
5. Fetch station records for each `NetworkCode.StationCode` in `config["stations"]`
   — exit clearly listing any stations not found or with null coordinates
6. Geocode start and end cities (print progress: `"Geocoding Buenos Aires, Argentina..."`)
7. Order stations via `services.order_stations_tsp()`
8. Fetch all OSRM legs in order (print progress per leg:
   `"Routing: Buenos Aires → arg.UNSJ (leg 1/5)..."`)
   — sleep 0.5s between calls to respect the public API
9. Compute plan via `services.compute_plan()`
10. Generate HTML via `report.generate_html(plan, config)`
11. Write to `config["output_file"]`
12. Print: `"Plan written to campaign_plan.html — open it in your browser."`

### Logging

Use Python's `logging` module. Write full tracebacks to a log file
(follow whatever logging setup pattern exists in other `com/` scripts).
Show only friendly messages to stdout — no raw tracebacks.

---

## report.py — HTML Generation

`generate_html(plan: dict, config: dict) -> str`

Returns a complete, self-contained HTML string. Use Python triple-quoted strings —
no Jinja2 or external template engines.

### Self-contained requirements
- All CSS in `<style>` tags
- Leaflet loaded from CDN:
  ```html
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  ```
- All plan data embedded as a JSON literal in a `<script>` tag:
  ```html
  <script>const PLAN_DATA = /* json.dumps(plan) */;</script>
  ```
- No other external dependencies

### Page sections

**Header:** campaign title, date range, generated timestamp.

**Summary bar:** four highlighted boxes:
`Total km | Total driving time | Total fuel cost | Days`

**Leaflet map** (full width, ~450px tall):
- OSM base tiles
- One colored polyline per day (blue, red, green, orange, purple — cycle if more days)
- Circle markers: stations = red, origin/destination = blue
- Marker popups: name, arrival, departure, leg km
- Map auto-fits to all waypoints on load
- All map data read from `PLAN_DATA` in JavaScript — no data hardcoded in JS

**Day-by-day tables:** one section per day headed `"Day 1 — Monday, 1 September 2025"`.

Table columns:
```
Stop | Type | Arrive | Depart | Drive to here | Km | Fuel | Notes
```
Warning rows (overrun days, late arrivals) highlighted in amber.
Day totals row at the bottom of each table.

**Footer:** grand totals — km, drive time in h:mm, fuel cost.

**Styling:** clean minimal CSS, readable when printed, no frameworks.

---

## Error Handling Summary

| Situation | Behavior |
|---|---|
| Config file not found | Clear message, exit 1 |
| Invalid JSON | Show line/column, exit 1 |
| Missing required fields | List all missing, exit 1 |
| Station not in DB | List missing stations, exit 1 |
| Station has null lat/lon | List affected stations, exit 1 |
| Geocoding fails | Name the city, suggest being more specific, exit 1 |
| OSRM timeout/error | Name the failed leg, suggest retrying, exit 1 |
| Output file not writable | Show path and OS error, exit 1 |

All exits: friendly message to stdout, full traceback to log file only.

---

## Python Dependencies

Only `requests` beyond the standard library. Confirm it is already present in the
GeoDE environment before assuming it needs installing. All other code uses:
`json`, `sys`, `os`, `math`, `datetime`, `time`, `logging`, `argparse`

---

## Acceptance Criteria

1. `python com/CampaignPlanner.py --help` shows all switches with descriptions
2. `python com/CampaignPlanner.py --config example_campaign.json` produces a `.html` file
3. All parameters can be passed via switches instead of or in addition to JSON,
   with switches taking priority
4. Double-clicking the output HTML opens correctly in a browser
5. The Leaflet map shows the route with color-coded day segments and clickable markers
6. A campaign crossing 2+ days splits correctly at the hard stop time
7. The overnight re-routing (next day starts from last visited station) works correctly
8. The script is registered in `setup.py` following existing conventions
9. All errors print friendly messages and exit cleanly — no raw tracebacks to stdout
