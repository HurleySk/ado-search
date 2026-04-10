# JSONL Data Layer for ado-search

**Date:** 2026-04-10
**Version:** 0.5.0 (next after 0.4.0 performance release)

## Problem

The current sync output — thousands of individual `.md` files plus a binary SQLite DB — is hostile to git. A moderately sized ADO project produces thousands of files that bloat repos, create noisy diffs, and make the binary `index.db` un-diffable and merge-conflict-prone. Since apps and agents consume this data across environments (CI, teammates, fresh clones), the data needs to be easily committable, pushable, and pullable.

## Design

Replace individual markdown files with JSONL as the portable source of truth. The SQLite FTS5 index becomes a derived cache, `.gitignore`d and auto-rebuilt from JSONL on demand.

### Committed artifacts

Only three files live in git:

- `config.toml` — sync configuration (unchanged)
- `work-items.jsonl` — one JSON object per line, sorted by `id`
- `wiki-pages.jsonl` — one JSON object per line, sorted by `path`

A repo with 5,000 work items produces a single JSONL file of roughly 5-15 MB.

### .gitignore additions

```
index.db
work-items/
wiki/
```

### JSONL schemas

**Work item line:**

```json
{"id": 12345, "title": "Fix login bug", "type": "Bug", "state": "Active", "area": "App\\Auth", "iteration": "Sprint 5", "assigned_to": "user@example.com", "tags": "security,auth", "priority": 2, "parent_id": 100, "created": "2025-01-15", "updated": "2025-03-20", "description": "Full stripped-text description", "acceptance_criteria": "Full stripped-text AC", "comments": [{"author": "Name", "date": "2025-03-01", "text": "..."}]}
```

**Wiki page line:**

```json
{"path": "/Architecture/Overview", "title": "Overview", "updated": "2025-02-10", "content": "Full wiki page content (already markdown)"}
```

All text fields store stripped/processed text (HTML already removed), not raw ADO HTML. This keeps JSONL human-readable and avoids reprocessing on reindex.

Sorting (by `id` for work items, by `path` for wiki) is required for stable git diffs — only changed items produce diff lines.

### Sync flow

```
ado-search sync
  1. Fetch from ADO (OData fast path or WIQL fallback — unchanged)
  2. Load existing JSONL into memory (if incremental sync)
  3. Merge fetched items into the in-memory dict (keyed by id/path)
  4. Write work-items.jsonl sorted by id (atomic: write temp file, then rename)
  5. Write wiki-pages.jsonl sorted by path (same atomic write)
  6. Rebuild index.db from JSONL
  7. Update last_sync in config.toml (unchanged)
```

For full syncs, step 2 is skipped — the JSONL is written fresh.

For incremental syncs, existing JSONL is loaded as a dict, updated entries are merged in, deleted entries are removed (using the same orphan detection as today), and the result is rewritten sorted.

### Auto-reindex

When `ado-search search` or `ado-search show` is called, if:
- `index.db` does not exist, OR
- either JSONL file has a newer mtime than `index.db`

...the index is silently rebuilt before executing the query. No explicit command needed.

### Reindex process

1. Read JSONL files line by line
2. `json.loads()` each line into the appropriate `db.upsert_*` call
3. Entire operation wrapped in `db.batch()` for a single transaction
4. Touch `index.db` mtime to mark it current

### DB schema changes

Add `description` (full text) and `acceptance_criteria` columns to `work_items` table so `show` can render markdown from the DB without re-reading JSONL:

```sql
ALTER TABLE work_items ADD COLUMN description TEXT DEFAULT '';
ALTER TABLE work_items ADD COLUMN acceptance_criteria TEXT DEFAULT '';
```

Add `content` column to `wiki_pages`:

```sql
ALTER TABLE wiki_pages ADD COLUMN content TEXT DEFAULT '';
```

The FTS5 `search_index.description_snippet` remains a 500-char truncation for search result display.

### `show` command changes

Instead of reading `work-items/12345.md` from disk, `show` queries the DB for the full record and renders markdown on the fly using the existing `work_item_to_markdown()` / `wiki_page_to_markdown()` functions.

### Deletion handling

On **full sync** (no `last_sync` set), orphan detection compares fetched IDs/paths against what's in the current JSONL. Orphaned entries are excluded from the rewritten JSONL and deleted from the DB. No file deletions needed since individual `.md` files no longer exist.

On **incremental sync**, no orphan detection runs (same as current behavior) — only new/changed items are merged into the existing JSONL.

### Migration from v0.4.0

No automated migration. First sync under the new version:
- Writes JSONL files (new)
- Rebuilds DB with new schema columns
- Old `work-items/` and `wiki/` directories become orphans — users can delete them manually or add them to `.gitignore`

A changelog note is sufficient.

## Files to modify

| File | Change |
|------|--------|
| `sync_common.py` | Replace `write_work_item()` file I/O with JSONL dict accumulation |
| `sync_workitems.py` | Collect items in memory, write JSONL at end of sync |
| `sync_wiki.py` | Same pattern for wiki pages |
| `sync_odata.py` | Same pattern — accumulate into dict instead of writing files |
| `db.py` | Add `description`, `acceptance_criteria`, `content` columns; add `reindex_from_jsonl()` method |
| `cli.py` | Add auto-reindex check before search/show; update `show` to render from DB |
| `search.py` | Trigger auto-reindex if needed |
| `jsonl.py` (new) | Read/write/merge JSONL files with atomic writes and sorted output |
| `pyproject.toml` | Version bump to 0.5.0 |
| `__init__.py` | Version bump to 0.5.0 |

## Verification

1. `ado-search sync` produces `work-items.jsonl` and `wiki-pages.jsonl`, no `.md` files
2. `ado-search search <query>` auto-rebuilds DB if missing, returns correct results
3. `ado-search show <id>` renders markdown from DB data
4. Delete `index.db`, run search again — auto-reindex succeeds
5. Run `git diff` after incremental sync — only changed items appear as diff lines
6. All existing tests pass (updated for new data flow)
7. `python -m build` succeeds
