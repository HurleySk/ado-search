# Changelog

## [0.7.0] — 2026-04-14

### Added

- **Story points field** — syncs `Microsoft.VSTS.Scheduling.StoryPoints` (with `Effort` fallback) as `story_points: float | None` on each work item.
- **Work item state history** — fetches revision updates from the ADO Updates API and extracts state transitions. Stored as `state_history` in JSONL and indexed in a `work_item_state_changes` table. Enables downstream cycle time and velocity analysis.
- New `Database` methods: `upsert_state_changes()`, `get_state_changes()`, `get_all_state_changes()`.

### Fixed

- Work item deletion now cleans up associated state change records.
- Story points `0` is now preserved correctly (previously could fall through to Effort field).
- Comments and updates are fetched concurrently during sync for better performance.

## [0.6.0] — 2026-04-13

### Added

- `ado-search fetch <ID> [<ID> ...]` — new command to pull specific work items by ID directly from ADO and merge them into the local store. Useful when you have a known list of IDs and want to avoid a full sync. Supports `--dry-run` and `--data-dir` options consistent with other commands.

## [0.5.2] and earlier

Initial releases — sync, search, show commands.
