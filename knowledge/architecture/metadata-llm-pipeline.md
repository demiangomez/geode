# Metadata LLM-Assisted Pipeline (`geode/metadata/`)

Full design docs already live in the package — read them first, this file only indexes +
flags drift: [`geode/metadata/DESIGN.md`](../../geode/metadata/DESIGN.md),
[`geode/metadata/PLANNER_PROMPT.md`](../../geode/metadata/PLANNER_PROMPT.md).

## Problem

Station operators publish their own GNSS metadata (IGS site logs, `.stninfo`, NGL format); GeoDE's
own `stationinfo` table drifts from that published metadata over time (firmware swaps, antenna
changes, undocumented gaps). This package detects and reconciles that drift, using Claude as — per
DESIGN.md — "the reasoning engine" while GeoDE's own parsers handle file parsing.

## Two distinct Claude-backed components

- **`StationMetadataComparator`** (`comparator.py`, prompted via `prompts.py`'s `SYSTEM_PROMPT`) —
  compares a DB-derived `StationMetadataBundle` against a file-derived one, returns a
  `ComparisonReport` of `Finding`s (`NEW_SESSION`, `ORPHAN_SESSION`, firmware/antenna changes, ...),
  each tagged `INSERT`/`UPDATE`/`REVIEW`/`NO_ACTION`.
- **`StationInfoPlanner`** (`planner.py`, prompted via `planner_prompt.py`'s
  `PLANNER_SYSTEM_PROMPT`) — invoked only when a direct SQL apply hits a timeline conflict
  (`StationInfoOverlapException`) or the user supplies free-text instructions. Returns an ordered
  list of `PlannerOperation`s (INSERT/UPDATE/DELETE), executed in one transaction by
  `execute_plan()`.

Both use `claude-sonnet-4-6`, `temperature=0`, ephemeral prompt caching, exponential-backoff retry
on 429/5xx. A fast path (file-hash → session-hash-vs-`stationinfo_audit` → bundle equality) skips
the API call entirely when nothing changed.

## CLI usage

- `com/SyncMetadata.py` — the only script using `StationMetadataComparator`. Downloads metadata,
  runs the fast-path checks, writes findings to `stationinfo_audit`. **Discovers and logs only —
  never applies anything itself.**
- `com/StationInfoEdit.py` — the only script using `StationInfoPlanner`/`execute_plan`. A Textual
  TUI reviewing audit rows and applying INSERT/UPDATE/REVIEW findings (SQL-first, planner as
  fallback for conflicts).
- `com/ConvertMetadata.py` and `com/CloseStationInfo.py` import only `geode.metadata.igslog` /
  `station_info` (plain parsing/CRUD) — **they do not use Claude at all**, despite living in the
  same package.

## Known doc/code drift

- DESIGN.md's "Trust model" section proposes auto-applying INSERT/UPDATE only for high-trust
  sources (e.g. official IGN-Ar/RAMSAC logs). **No such per-source trust tier exists in the code.**
  `StationInfoEdit.py`'s batch-apply mode applies all pending INSERT findings uniformly regardless
  of source; `SyncMetadata.py` never applies anything. Don't assume this gating exists when working
  on auto-apply behavior — check before relying on it, and if someone asks for it, it needs to be
  built, not fixed.
- The schema DESIGN.md proposes (`sources_metadata`, `sources_servers.metadata_source_id`,
  `sources_stations.metadata_hash`, `stationinfo_audit`) **is** fully implemented — no drift there.

## Related, not LLM-based: `geode/campaign_planner/`

See [campaign-clustering-elasticity.md](campaign-clustering-elasticity.md) — the *shipped* active
campaign planner doesn't use an LLM at all; only the unimplemented "passive campaign" design
proposes one.
