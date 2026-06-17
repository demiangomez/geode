# GeoDE – Passive Campaign Planner (Design Specification)

## Status: NOT YET IMPLEMENTED — Design only

---

## Background

GeoDE already has `com/CampaignPlanner.py` for **active** campaigns: the field team drives to
each station, spends time there, and leaves. That tool is complete.

This document specifies a second, harder module for **passive benchmark campaigns**: the team
deploys GNSS receivers at sites, leaves them unattended for 48+ hours, then returns to collect
and redeploy. The spatial and temporal reasoning is fundamentally different.

---

## The Problem

- **N instruments** (e.g. 5), **S stations** (S >> N typically, e.g. 30–50)
- Each station needs exactly one instrument deployed for at least **D hours** (usually 48 h)
- The team can only carry all N instruments at once (or a subset on partial trips)
- Instruments become eligible for collection per-instrument, not per-wave — instrument #1
  (deployed Day 1 at 08:00) is ready before instrument #5 (deployed Day 2 at 15:00)
- **Partial collection is normal**: the team may grab 2 receivers, redeploy them at new sites,
  return for the remaining 3, and redeploy those. All within one driving day if time allows.
- Collect + redeploy can be chained: collect at A → deploy at B → collect at C → deploy at D,
  all in one day's route, using the instrument just collected at A to deploy at B

---

## Reference: Spreadsheet Grid Format

The field team historically plans campaigns in a spreadsheet:
- **Columns** = instruments (receivers), one per column
- **Rows** = calendar days
- **Cell encoding**:
  - `SITE/XXXX` — installation day; XXXX is the file sequence number for data management
  - `SITE-N` — Nth day of continuous occupation (N = 1, 2, 3…)
  - `SITE-N-R` — last day of occupation, receiver retrieved this day
  - `-` — receiver is free / in hand
- The `-R` suffix tells the team exactly which receivers are available the following morning

This grid is the primary output artifact. The driving timeline is secondary.

---

## Agreed Architecture

**Claude API owns the planning logic. Deterministic code owns geometry and rendering.**

The interface between them is a structured JSON grid returned by Claude.

### Why Claude API (not a deterministic scheduler)

The problem is too constraint-heavy for clean heuristics and too large for brute force.
More importantly, real campaigns always involve soft constraints that are impossible to encode
formally but trivial to express in natural language:

- "Receiver 3 has antenna issues in cold weather — assign it to lower-elevation sites"
- "ACOL must come before MORR because of road access in that valley"
- "We lost receiver 2 on day 4 — redistribute its remaining sites"

Claude handles these naturally. A deterministic scheduler cannot.

### Data Flow

```
User inputs
  ├── Station list (NetworkCode.StationCode — resolved from GeoDE DB)
  ├── Instrument inventory (count; optionally: model, known quirks per unit)
  ├── Campaign constraints (min_occupation_hours, work hours, start date)
  └── Free-text notes (soft constraints, access issues, priorities)
        │
        ▼
OSRM matrix builder (deterministic code)
  Computes N×N drive-time and distance matrix for all stations + origin/destination
        │
        ▼
Prompt builder (deterministic code)
  Constructs a structured prompt:
    - Plain-language explanation of deployment/collection rules
    - The drive-time matrix (as a compact table)
    - Campaign constraints (instrument count, min occupation, work hours)
    - Any free-text notes from the user
    - Output format specification (JSON grid + reasoning)
        │
        ▼
Claude API call  ◄──── iterative refinement loop (user feedback → revised call)
        │
        ▼
Structured response: JSON grid + reasoning text
        │
        ▼
Validator (deterministic code)
  Checks grid for violations:
    - Same receiver in two places on the same day
    - Station collected before min_occupation_hours elapsed
    - More receivers deployed than num_instruments
    - Any station visited more than once
  If violations found: either re-prompt Claude automatically or flag to user
        │
        ▼
Renderer (deterministic code)
  - Section 1: Instrument Grid (HTML table, spreadsheet-like)
  - Section 2: Driving Timeline (day-by-day, action-typed stops)
  - Section 3: Leaflet map (deploy routes vs. collect routes)
```

---

## New Input Parameters

These extend the existing `CampaignPlanner.py` JSON config and CLI switches:

```json
{
    "num_instruments":             5,
    "min_occupation_hours":        48.0,
    "install_time_minutes":        45,
    "collect_time_minutes":        20,
    "return_to_base_during_wait":  false
}
```

| Parameter | Type | Description |
|---|---|---|
| `num_instruments` | int | Total receivers available |
| `min_occupation_hours` | float | Minimum time each site must be occupied |
| `install_time_minutes` | int | Time to set up one receiver (leveling, config) |
| `collect_time_minutes` | int | Time to pack up one receiver (faster than install) |
| `return_to_base_during_wait` | bool | `true` = drive back to origin during wait; `false` = stay in field (adds lodging, saves travel) |

---

## JSON Grid Format (Claude API Response)

Claude should return a grid structured as follows:

```json
{
    "reasoning": "I grouped ACOL, MORR, and TINO in wave 1 because they form a tight cluster ~50 km apart...",
    "grid": [
        {
            "date": "2025-09-01",
            "instruments": {
                "RX1": {"site": "arg.unsj", "state": "install", "file_seq": 1001},
                "RX2": {"site": "arg.vmol", "state": "install", "file_seq": 1002},
                "RX3": {"site": "arg.rwsn", "state": "install", "file_seq": 1003},
                "RX4": {"site": "arg.ljar", "state": "install", "file_seq": 1004},
                "RX5": {"site": null,       "state": "free",    "file_seq": null}
            }
        },
        {
            "date": "2025-09-02",
            "instruments": {
                "RX1": {"site": "arg.unsj", "state": "occupied", "file_seq": 1001},
                ...
            }
        },
        {
            "date": "2025-09-03",
            "instruments": {
                "RX1": {"site": "arg.unsj", "state": "collect+install", "new_site": "arg.newx", "file_seq": 1005},
                ...
            }
        }
    ]
}
```

### State values

| State | Meaning |
|---|---|
| `install` | Receiver deployed at site this day (first day) |
| `occupied` | Receiver at site, mid-occupation (not yet eligible for collection) |
| `collect` | Receiver collected this day, returned to hand |
| `collect+install` | Collected from current site, immediately redeployed at `new_site` |
| `free` | Receiver in hand / not deployed |

---

## Iterative Refinement Loop

After the first Claude response, the user can send corrections in plain language:

- "Wave 2 has too much driving on day 3 — can you spread it out?"
- "Swap ACOL and MORR between wave 1 and wave 2"
- "Receiver 5 failed on day 4 — redistribute its remaining sites to the other units"

Each refinement call sends: the drive-time matrix + the current grid + the user's comment.
Claude returns a revised grid + updated reasoning.

The UI for this could be as simple as a text field under the rendered plan with a "Refine" button.

---

## Report Sections

### Section 1 — Instrument Grid

HTML table, matching the spreadsheet mental model:
- Rows = calendar days
- Columns = receiver IDs (RX1…RXN)
- Cells show site name + state, color-coded:
  - Green = install day
  - Blue = occupied (mid-occupation)
  - Orange = collect day
  - Purple = collect+install (handoff)
  - White/blank = free
- Shaded spans visually show occupation duration
- Bold border on collect/collect+install days (the `-R` days from the spreadsheet)
- Tooltip on hover: exact install timestamp, eligibility time, collected timestamp

### Section 2 — Driving Timeline

Extends the current day-by-day stop table with new action types per stop:

| Stop | Type | Receiver | Arrive | Depart | Drive here | Km | Notes |
|---|---|---|---|---|---|---|---|
| arg.unsj | **Install** | RX1 | 09:15 | 10:00 | 45 min | 87 km | |
| arg.vmol | **Install** | RX2 | 11:30 | 12:15 | 90 min | 120 km | |
| arg.unsj | **Collect+Install** | RX1→RX1 | 08:30 | 09:10 | 30 min | 45 km | |
| arg.newx | **Install** | RX1 | 10:45 | 11:30 | 95 min | 110 km | |

New stop types: `Install`, `Collect`, `Collect+Install`, `Travel` (no instrument action)

### Section 3 — Leaflet Map

- Deploy route segments: solid lines, one color per wave
- Collect route segments: dashed lines, matching wave color
- Markers: site color indicates current state (deployed / collected / pending)
- Toggle layers: show/hide deploy routes, collect routes, individual waves

### Summary additions

- Number of waves (full + partial)
- Total dead time (waiting for instruments to complete occupation)
- Instrument utilization % (time deployed / total campaign duration)
- If `return_to_base_during_wait = true`: extra travel days + cost shown separately

---

## Prompt Engineering Notes

The prompt to Claude should include:

1. **Rules section** (plain language):
   - Each station must have exactly one receiver for at least `min_occupation_hours`
   - You have `num_instruments` receivers total
   - Receivers become eligible for collection exactly `min_occupation_hours` after installation
   - Partial collection is allowed: you can pick up any subset of eligible receivers in one trip
   - A collected receiver can be immediately redeployed at a new site in the same trip
   - Work day is `day_start` to `hard_stop`; install takes `install_time_minutes`, collect takes `collect_time_minutes`
   - Minimize total campaign duration (days from first install to last collection)

2. **Drive-time matrix**: compact table of drive times in minutes between all station pairs + origin

3. **Station list**: with any access notes or soft constraints the user provided

4. **Output format**: the JSON grid schema described above, plus a reasoning paragraph

5. **Validation hint**: remind Claude to verify no receiver appears at two sites on the same day

---

## Validator Checks

Before accepting Claude's grid, validate:

```python
def validate_grid(grid, num_instruments, min_occupation_hours):
    errors = []
    deployed = {}   # rx_id -> (site, install_datetime)

    for day in grid:
        for rx_id, cell in day["instruments"].items():
            if cell["state"] in ("install", "collect+install"):
                # Check not already deployed
                if rx_id in deployed:
                    errors.append(f"{rx_id} installed at {cell['site']} but still deployed at {deployed[rx_id][0]}")
                deployed[rx_id] = (cell["site"], day["date"])

            if cell["state"] in ("collect", "collect+install"):
                # Check min occupation elapsed
                if rx_id in deployed:
                    install_dt = deployed.pop(rx_id)[1]
                    elapsed = (parse(day["date"]) - parse(install_dt)).total_seconds() / 3600
                    if elapsed < min_occupation_hours:
                        errors.append(f"{rx_id} at {cell['site']} collected after only {elapsed:.1f}h (need {min_occupation_hours}h)")

    return errors
```

If errors found, either:
- Re-prompt Claude automatically with the error list appended
- Or show the errors to the user and let them decide whether to refine or accept anyway

---

## Implementation Order (Recommended)

1. **OSRM matrix builder**: compute N×N drive-time matrix for a station list. This is pure geometry and reusable.
2. **Prompt builder + Claude API call**: send matrix + constraints, parse JSON grid response.
3. **Validator**: check the returned grid for violations.
4. **Grid renderer**: HTML table (Section 1 of the report).
5. **Driving timeline generator**: convert the grid into day-by-day stop sequences (Section 2). This adapts the existing `compute_plan` logic in `services.py`.
6. **Map renderer**: extend `report.py` to show deploy vs. collect routes (Section 3).
7. **Refinement UI**: text input + "Refine" button → follow-up Claude call.

Steps 1–3 can be built and tested without any rendering. Steps 4–6 can be built against a hardcoded test grid. Only Step 7 requires both to work together.

---

## Files to Create / Modify

```
com/
    PassiveCampaignPlanner.py       ← new script (parallel to CampaignPlanner.py)

geode/campaign_planner/
    passive_services.py             ← OSRM matrix builder, prompt builder, validator,
                                       grid-to-timeline converter
    passive_report.py               ← HTML generation for grid + timeline + map

setup.py                            ← add PassiveCampaignPlanner entry point
```

Consider whether `PassiveCampaignPlanner.py` should be a subcommand of `CampaignPlanner.py`
(e.g. `--mode passive`) or a standalone script. The existing active planner is self-contained,
so a standalone script is probably cleaner.

---

## Open Questions

- [ ] Should `PassiveCampaignPlanner.py` be standalone or a `--mode` flag on `CampaignPlanner.py`?
- [ ] File sequence numbers (the `/XXXX` in the spreadsheet cells) — should the planner assign
      these, or are they generated by GAMIT/post-processing and just recorded here?
- [ ] Is the instrument count always fixed for a campaign, or can instruments be added/removed mid-campaign?
- [ ] For `return_to_base_during_wait = false`, where does the team stay? Is lodging cost tracked per-city or a flat rate?
- [ ] Should the Claude API call use a specific model, or let the user configure it?
