# Changelog

## [1.8.1] — 2026-04-28

### Added

- **`--full` flag on `sync`** — ignores `last_sync` and re-fetches all items from ADO. Useful after schema changes (e.g., adding `closed_date`) to backfill fields on existing items.

## [1.8.0] — 2026-04-28

### Added

- **`closed_date` field on work items** — syncs `Microsoft.VSTS.Common.ClosedDate` from ADO as a first-class field on both OData and WIQL/REST sync paths. Stored in JSONL and SQLite. The `children --include-closed-date` flag now reads this field directly instead of requiring state history from the Updates API, giving closed dates for all items after a single sync.

## [1.7.0] — 2026-04-28

### Added

- **`ado-search children <parent_id>`** — query work item hierarchy from local SQLite. Shows direct children by default; `--recursive` walks the full tree via recursive CTE. Output formats: `compact` (tabular), `tree` (indented hierarchy), `json`. Supports `--type` and `--state` filters. `--include-closed-date` enriches output with the date each item transitioned to Closed (from state history).
- **`idx_work_items_parent_id` index** — B-tree index on `parent_id` column for efficient hierarchy queries.

## [1.6.0] — 2026-04-24

### Added

- **`--include-attachments` flag on `fetch` and `sync`** — override the config setting per-invocation to download file attachments and inline images. Useful for targeted fetches where you need attachments for specific work items without enabling them globally. When passed, the flag `or`s with the config value — if either is true, attachments are downloaded. Log output includes "(with attachments)" when active.

## [1.5.0] — 2026-04-22

### Added

- **`--parent <id>` on `create`** — sets a parent-child hierarchy link when creating a work item. For PAT/PowerShell auth, the relation is included in the create payload. For az-cli auth, a follow-up `add-link` call is made after creation. Example: `ado-search create --type Task --title "Subtask" --parent 62434`.

## [1.4.0] — 2026-04-21

### Added

- **`ado-search grep <pattern>`** — regex pattern matching across work item fields. Searches title, full description, and comments by default. Supports `--field` scoping to any combination of fields, metadata pre-filters (`--type`, `--state`, `--area`, `--assigned-to`, `--tag`), case-insensitive matching (`-i`), configurable context window (`-C`), and result limit (`-n`).
- **Three output formats for grep:** compact (default, shows context snippets around matches), brief (`--brief`, shows only item IDs and matched field names), and JSON (`--format json`, structured output for scripting).
- **Exit codes follow grep convention:** 0 for matches found, 1 for no matches, 2 for errors.

## [1.3.0] — 2026-04-21

### Changed

- **SQLite indexes on filter columns** — `search` queries that filter by type, state, area, or assigned-to now hit B-tree indexes instead of full-table scans. Speeds up filtered searches on large datasets.
- **Batch state change inserts** — `upsert_state_changes()` uses `executemany()` instead of per-row `execute()` calls, reducing SQLite round-trips during sync.
- **Skip redundant deletes during reindex** — full reindex no longer issues per-item `DELETE FROM work_item_state_changes` after the table has already been cleared. Mirrors the existing `_skip_fts_delete` optimization.
- **Parallel attachment downloads** — file attachments and inline images are now downloaded concurrently with `asyncio.gather()` instead of sequentially, bounded by the existing semaphore.
- **Single semaphore acquire per work item fetch** — `fetch_item()` now holds the concurrency semaphore once for the show + updates/comments calls instead of acquiring it twice with a gap between.
- **Exponential ID range probing** — `_find_id_range_start()` uses exponential probing + binary search instead of linear 10K-chunk scanning. Reduces worst-case API calls from ~20 to ~8 for projects with high starting work item IDs.

## [1.2.0] — 2026-04-20

### Added

- **`ado-search add-link <source> <target> --type <type>`** — create a link between two work items. Supports friendly type names (`related`, `parent`, `child`, `duplicate`, `duplicate-of`, `depends-on`, `predecessor`, `successor`) or raw ADO relation type strings. Supports `--comment` and `--dry-run`.
- **`ado-search list-links <id>`** — fetch and display work item links live from Azure DevOps. Shows link type name and target work item ID for each relation (excludes attachments, hyperlinks, and artifact links).
- **`ado-search list-comments <id>`** — fetch and display work item comments live from Azure DevOps. Shows author, date, and plain-text body for each comment without requiring a prior sync with `include_comments`.

### Changed

- Internal refactoring: extracted shared helpers (`_check_and_refetch`, `_open_db`, `_upsert_fts`) to reduce code duplication across CLI commands, write operations, and database indexing. No behavior changes.

## [1.1.0] — 2026-04-20

### Added

- **`--reason` option on `create` and `update`** — sets `Microsoft.VSTS.Common.ResolvedReason` directly, e.g., `ado-search update 67154 --state Closed --reason Duplicate`. Previously required `--field "Microsoft.VSTS.Common.ResolvedReason=Duplicate"`.

## [1.0.1] — 2026-04-17

### Fixed

- **Sync crash on duplicate state changes** — `reindex_from_jsonl` failed with `sqlite3.IntegrityError: UNIQUE constraint failed` when a work item had multiple state transitions to the same state on the same day (date truncated to day precision). Changed `INSERT` to `INSERT OR REPLACE` in `upsert_state_changes()` so duplicate composite keys are silently resolved.

## [1.0.0] — 2026-04-17

### Fixed

- **Wiki page sync broken with PAT auth** — `_resolve_url()` was not URL-encoding wiki page path values in query parameters. When paths contained spaces (common in project wikis), Python's `urlopen` raised `InvalidURL`, causing every page fetch to fail silently. Now encodes path values with `quote(path, safe="/")` so slashes are preserved but spaces and special characters are properly percent-encoded.

## [0.10.0] — 2026-04-15

### Added

- **`@file` input for HTML fields** — `--description @desc.html` and `--acceptance-criteria @criteria.html` now read content from a file. Works on both `create` and `update` commands. Use `@@literal` to pass a string that starts with `@`.
- **`ado-search add-comment`** — new command to post a comment on a work item. Accepts inline HTML text or `@file.html`. Supports `--dry-run`. After posting, the work item is re-fetched so the comment appears in local search immediately.

## [0.9.0] — 2026-04-14

### Added

- **`ado-search create`** — create new work items directly from the CLI. Supports `--type`, `--title`, `--state`, `--description`, `--assigned-to`, `--tags`, `--priority`, `--story-points`, and more. Works with all three auth methods (az-cli, az-powershell, PAT).
- **`ado-search update`** — update existing work items by ID. All fields are optional; at least one must be provided. Supports the same field options as `create`.
- **`--field Key=Value`** repeatable option on both commands for setting arbitrary ADO fields (including custom fields) beyond the named options.
- **`--dry-run`** on both commands to preview changes without writing to ADO.
- New module `write_workitems.py` with `FIELD_MAP`, `build_json_patch()`, `resolve_fields()`, and async create/update functions.
- Generalized body handling in `auth.py` — `pat_request()` and `build_powershell_command()` now accept `body` and `content_type` parameters, enabling `application/json-patch+json` for work item mutations.

### Changed

- `_fetch_item()` in `sync_workitems.py` renamed to `fetch_item()` (public API) so it can be reused by the write pipeline for post-mutation re-fetch.

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
