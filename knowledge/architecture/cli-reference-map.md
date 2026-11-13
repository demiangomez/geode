# CLI → Subsystem Quick Map

Which library subsystem each `com/*.py` script actually touches, for fast triage ("which doc do I
need for this script"). Full usage/examples are in `GeoDE_User_Manual.md`; this is a dependency
lookup, not a usage guide.

| Script | Primary subsystem(s) | Notes |
|---|---|---|
| `ArchiveService.py` | `pyArchiveStruct`, `pyOptions` | Archive ingestion (locks, visit files) |
| `DownloadSources.py`, `DownloadSourcesFill.py` | `proto_download`, `pyOptions` | Raw data retrieval |
| `ScanArchive.py` | `pyArchiveStruct`, `pyPPP`, `pyOTL` | RINEX+PPP+OTL ingestion; precursor to `ParallelGamit.py` |
| `IntegrityCheck.py` | `dbConnection`, `pyArchiveStruct` | DB/archive consistency checks + fixes |
| `ParallelGamit.py` | `geode/gamit/`, `network.py`, `geode/clustering`, `pyJobServer` | Main GAMIT/GLOBK orchestrator |
| `ModelProcessing.py` | `geode/clustering` | Subnetwork sizing / runtime estimation |
| `Ztd2trp.py` | `geode/gamit/ztd.py` (via `gamit_ztd` table) | BERNESE `.TRP` export |
| `PlotETM.py`, `AlterETM.py`, `CreateEtmFunctions.py`, `PrintVelocities.py` | `geode/etm/` (new engine) | — |
| `EtmStacker.py` | `geode/etm/stacker/` (+ `etm_stacker_vce.py`) | Spatial field stacking |
| `FixPlate.py`, `PlotMapView.py`, `QueryETM.py`, `TrajectoryFit.py`, `NEQStack.py` | `pyETM` (legacy) | Do not extend `pyETM.py` itself — see [etm-stacker.md](etm-stacker.md) |
| `Stacker.py` | `pyETM` + `pyStack` (both legacy) | DRA-era stacking pipeline |
| `DRA.py` | `geode.etm.core.*` (new) + `pyStack` (legacy) | Mixed: new per-station fitting, legacy polyhedron combination |
| `SyncMetadata.py` | `geode/metadata/comparator.py` | LLM-assisted discovery only, never applies |
| `StationInfoEdit.py` | `geode/metadata/planner.py` | LLM-assisted apply (planner is the conflict-fallback, not the default path) |
| `ConvertMetadata.py`, `CloseStationInfo.py` | `geode/metadata/igslog.py`, `station_info.py` | No LLM involved despite the package |
| `CampaignPlanner.py` | `geode/campaign_planner/planner.py` | No LLM involved (today) |
| `MergeStation.py` | `station_merge.py` | — |
| `StationReport.py`, `StationKmz.py`, `GenerateKml.py` | `geode/reports/` | — |
| `GenerateSinex.py`, `WeeklyCombination.py`, `NEQStack.py` | `pyStack`/`snxParse` | Legacy stacking lineage |
| `S-score.py` | `pyOkada` (s-score) | — |
| `LocateRinex.py` | `pyPPP` | Single-RINEX PPP wrapper |
| `ConvertTrimble.py` | `geode/ConvertRaw.py` (library module — no standalone `com/` script wraps it) | Raw→RINEX conversion |
| `SyncOrbits.py` | `pyProducts` | — |
| `ConvertDate.py` | `pyDate` | — |
| `OTL_FES2014b.py` | `pyOTL` | — |
| `UpdateEarthquakes.py` | `dbConnection`, `earthquakes` table | Refreshes the `earthquakes` table (feeds `s_score.ScoreTable`, see [etm-stacker.md](etm-stacker.md)) |
| `ApplyCountryCode.py` | `Utils` (country lookup) | — |
| `DownloadSourcesFill.py` | `proto_download` | FTP-server probing variant of `DownloadSources.py` |
| `ReadPackage.py` | `dbConnection` | Reads archive packages created by another GeoDE instance |

For anything not listed (it changes as scripts are added/removed), grep the script's own imports —
this table is a shortcut, not a substitute for checking.
