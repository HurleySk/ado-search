# Changelog

## [0.8.0] — 2026-04-14

### Added

- **Attachments sync** — optionally download file attachments from work items and store them locally in `.ado-search/attachments/{id}/`. Enable with `include_attachments = true` in config. Attachment filenames are indexed in FTS5 and searchable.
- **Inline image extraction** — images embedded in Description and Acceptance Criteria HTML fields are downloaded to `.ado-search/attachments/{id}/inline/` and referenced as `[image: path]` in stripped text output.
- `ado-search show` now displays `## Attachments` and `## Inline Images` sections when present.
- New auth helpers: `pat_download_binary()` and `build_download_command()` for streaming binary downloads via PAT, az-cli, or az-powershell.
- Incremental attachment sync — existing files with correct size are skipped on re-sync.

### Changed

- When `include_attachments = true`, OData analytics fast path is skipped in favor of WIQL/REST (OData responses don't include relations where attachments live).

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
