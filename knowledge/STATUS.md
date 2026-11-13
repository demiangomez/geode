# Current Status

Living document — update opportunistically when priorities shift materially, not after every
commit. Keep entries short; move resolved items out rather than accumulating a history here (that
belongs in [checkpoints/](checkpoints/)).

## Active focus (as of 2026-11-13)

- **ETM Stacker spatial-stacking stability.** Per-station velocity/postseismic-decay instability
  for sparsely-constrained stations is not resolved by reweighting the tie strength — the field a
  station ties to is itself built from other stations' independently-ambiguous fits. See
  [decisions/0001-sparse-station-stacking-approach.md](decisions/0001-sparse-station-stacking-approach.md).
  The direct-data-injection approach was implemented on branch `stacker_refactor` and is **not
  merged**; `master` currently only has detection/plotting groundwork
  (`geode/etm/stacker/sparse_station_constraints.md` is a spec, not shipped code). Check which
  branch is checked out before assuming either state.
- **Multi-engine table naming migration** (`gamit_soln`→`soln_gamit` etc., see
  [architecture/database-schema.md](architecture/database-schema.md)) is fully planned
  (`database/migration_plan_engine_table_naming.md`) but **not applied**. Needed before adding a
  second PPP engine or PAGES support.

## Outstanding / roadmap items surfaced during the 2026-11 knowledge-base assessment

- `geode/metadata/` DESIGN.md's trust-tier auto-apply gating was never implemented (see
  [architecture/metadata-llm-pipeline.md](architecture/metadata-llm-pipeline.md)) — flagged, not
  scheduled.
- `geode/elasticity/elastic_gridder.py` has a dead, broken import — low priority (nothing calls
  it), cheap to fix whenever someone's in that file.
- No tests exist for `etm_engine`, `DesignMatrix`, any `AdjustmentStrategy`, or anything under
  `geode/etm/stacker/`, `geode/gamit/`, or `geode/dbConnection.py`.
- The "passive campaign planner" (`geode/campaign_planner/passive_campaign_plan_mode_instructions.md`)
  is design-only, unimplemented.
- `DLN_func.py`/`green_func.py` (newer, more rigorous dislocation Love-number model) isn't wired
  into the Stacker yet — `elastic_interpolation`/`diskload`/`rectloadhs` are what's actually used
  today.

## Known branches with architecture-relevant work (check before assuming `master` has something)

- `stacker_refactor` — sparse-station direct-injection + adaptive-weighting constraints (not
  merged; see above).
- `PRIDE`, `etm_refactor`, `etm_stacker_euler_pole`, `flicker_noise` — exist locally; not
  characterized as part of this assessment. If you're about to work on anything these names
  suggest, check their diff against `master` first rather than assuming the feature is/isn't there.
