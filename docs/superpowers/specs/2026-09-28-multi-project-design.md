# Multi-project sync (ado-search 1.14.0)

## Why

One Azure DevOps org often hosts several projects. ado-search syncs exactly one (`organization.project`), so searching across projects means keeping several data dirs. This release lets a single data dir sync and search many projects. The MCP wrapper (ado-search-mcp) exposes a `project` filter.

Everything is backward compatible. Existing single-project configs and data dirs keep working unchanged.

Non-goal: sprint and analytics history (revisions, iterations, teams). That lives in the separate `sprint-forecast` project, which has its own Analytics extraction.

## Config

```toml
[organization]
url = "https://dev.azure.com/contoso"
project = "Alpha"               # legacy / default project (still supported)
projects = ["Alpha", "Beta"]    # new; ["*"] = every project the credentials can see

[sync]
last_sync = "2026-08-31"        # legacy watermark (read as fallback, still written)

[sync.last_sync_by_project]     # new; per-project watermark (YYYY-MM-DD)
"Alpha" = "2026-09-28"
```

- `resolve_projects(cfg, list_remote)`:
  - uses `projects` if it's non-empty; otherwise `[project]`
  - expands `"*"` through REST `GET {org}/_apis/projects` (new op `OP_PROJECT_LIST`; follows the `x-ms-continuationtoken` / `continuationToken` paging)
- `default_project(cfg)`: `organization.project` if set, otherwise the first explicit entry in `projects`.
  - Write commands (create, update, comments, links, attachments, fetch) and wiki sync use the default project, the same as today.
  - If only `["*"]` is configured, they exit with a clear error asking for `organization.project`.
- Watermark for project P:
  - `last_sync_by_project[P]` when present
  - otherwise the legacy `last_sync`, **only** if P is the legacy `organization.project`
  - otherwise empty, meaning a full sync of P
- `_dict_to_toml` quotes keys that aren't bare TOML keys (`[A-Za-z0-9_-]+`), so project names with spaces round-trip.

## Sync flow

`ado-search sync [--project NAME ...] [--full]`

`--project` (repeatable) limits the run to those projects, which must be among the resolved ones.

For each project P, the existing OData or WIQL path runs, with these changes:

- **`project` on records.** Taken from `System.TeamProject` on the WIQL/REST path, or P on the OData path. Records without it fall back to the first segment of the area path, and reindex backfills old JSONL records the same way.
- **Project-scoped orphan detection.** A full sync of P may only drop orphans *whose project is P*. Today a full sync drops every record it didn't fetch, which would delete other projects' items. `finalize_jsonl` gains an optional `scope` predicate that limits which existing records are orphan candidates.
- **`state_history` preservation.** When an incoming record has no `state_history` (the OData path doesn't fetch updates) and the existing record has one, the existing one is kept. Without this, an OData sync erases history captured earlier by a WIQL sync.
- **Watermark.** After P syncs successfully, `last_sync_by_project[P]` is set to today (UTC date). The legacy `last_sync` is also written, for downgrade compatibility.

Wiki sync runs once, for the default project, as it does today. Multi-project wiki is a non-goal: page paths collide across projects.

## OData paging fix

`sync_via_odata` requests `$top=5000&$skip=0` and follows only `@odata.nextLink`. Analytics doesn't return a nextLink for client-driven `$top`, so a project with more than 5,000 matching items is silently truncated. Syncing several projects makes this much more likely to bite.

The page loop becomes:
- if the page has `@odata.nextLink`, follow it
- otherwise, if the page is full (`len(value) == top`), request `$skip += top`
- otherwise, stop

The same loop is used for dry-run counting.

## Index and search

- `work_items.project TEXT` column, added through the existing `ALTER TABLE` migration loop, plus `idx_work_items_project`.
- `Database.search_work_items` and `get_filtered_ids` take `project_filter` (exact match). `search()` passes it through.
- The CLI's `search` and `grep` get `--project`.
- Compact search output shows the project when the data dir holds more than one.

## ado-search-mcp

An optional `project` string param on `ado_search` and `ado_grep`, passed through as `--project`. README and version bump (0.2.0).

## Testing

pytest, in the existing style (mocked `run_operation`, fixtures under `tests/fixtures/`):

- config:
  - project resolution (legacy, list, `*` expansion with continuation)
  - `default_project` error
  - per-project watermark fallback rules
  - TOML key quoting round-trip
- OData paging: nextLink, skip-paging, short last page, empty first page
- `finalize_jsonl` scope: a full sync of A leaves B's items intact
- `state_history` preservation on incremental merge
- `project` extraction (REST, OData, area-path fallback) and reindex backfill
- DB: `project_filter` in `search_work_items` and `get_filtered_ids`
- CLI:
  - `sync` loops over projects, uses the per-project watermark, and restricts with `--project`
  - `search --project`
  - `grep --project`

## CI/CD

- New `.github/workflows/ci.yml`: pytest on push and PR, Python 3.10–3.13 on ubuntu, plus one Windows job.
- The existing `publish.yml` (PyPI on a `v*` tag) is unchanged. The release is tagged `v1.14.0`.
