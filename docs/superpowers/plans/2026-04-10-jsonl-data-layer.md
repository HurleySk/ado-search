# JSONL Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-item markdown files with JSONL as the portable source of truth, making the SQLite FTS5 index a derived cache that auto-rebuilds from JSONL.

**Architecture:** Sync writes fetched items into sorted JSONL files (one per entity type). The SQLite DB is rebuilt from JSONL on demand via mtime comparison. The `show` command renders markdown from DB data instead of reading `.md` files.

**Tech Stack:** Python 3.10+, SQLite FTS5, stdlib `json`, `tempfile`, `os`

---

### Task 1: Create `jsonl.py` — JSONL read/write/merge module

**Files:**
- Create: `src/ado_search/jsonl.py`
- Create: `tests/test_jsonl.py`

- [ ] **Step 1: Write failing tests for JSONL read/write**

```python
# tests/test_jsonl.py
import json
from pathlib import Path

from ado_search.jsonl import read_jsonl, write_jsonl, merge_jsonl


def test_write_jsonl_sorts_by_key(tmp_path):
    path = tmp_path / "items.jsonl"
    items = {3: {"id": 3, "title": "C"}, 1: {"id": 1, "title": "A"}, 2: {"id": 2, "title": "B"}}
    write_jsonl(path, items, sort_key="id")
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    assert json.loads(lines[0])["id"] == 1
    assert json.loads(lines[1])["id"] == 2
    assert json.loads(lines[2])["id"] == 3


def test_read_jsonl_returns_dict(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text(
        '{"id": 1, "title": "A"}\n{"id": 2, "title": "B"}\n',
        encoding="utf-8",
    )
    result = read_jsonl(path, key="id")
    assert result == {1: {"id": 1, "title": "A"}, 2: {"id": 2, "title": "B"}}


def test_read_jsonl_missing_file(tmp_path):
    path = tmp_path / "missing.jsonl"
    result = read_jsonl(path, key="id")
    assert result == {}


def test_merge_jsonl_adds_and_updates(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"id": 1, "title": "Old"}\n{"id": 2, "title": "Keep"}\n', encoding="utf-8")
    new_items = {1: {"id": 1, "title": "Updated"}, 3: {"id": 3, "title": "New"}}
    merged = merge_jsonl(path, new_items, key="id")
    assert merged[1]["title"] == "Updated"
    assert merged[2]["title"] == "Keep"
    assert merged[3]["title"] == "New"


def test_merge_jsonl_with_removals(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"id": 1, "title": "A"}\n{"id": 2, "title": "B"}\n', encoding="utf-8")
    new_items = {1: {"id": 1, "title": "A"}}
    merged = merge_jsonl(path, new_items, key="id", remove_keys={2})
    assert 2 not in merged
    assert 1 in merged


def test_write_jsonl_atomic(tmp_path):
    """Write should be atomic — original file intact if process crashes mid-write."""
    path = tmp_path / "items.jsonl"
    path.write_text('{"id": 1, "title": "Original"}\n', encoding="utf-8")
    # Normal write should succeed and replace
    write_jsonl(path, {1: {"id": 1, "title": "New"}}, sort_key="id")
    assert json.loads(path.read_text(encoding="utf-8").strip())["title"] == "New"


def test_write_jsonl_string_sort_key(tmp_path):
    path = tmp_path / "pages.jsonl"
    items = {
        "/Z/Page": {"path": "/Z/Page", "title": "Z"},
        "/A/Page": {"path": "/A/Page", "title": "A"},
    }
    write_jsonl(path, items, sort_key="path")
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert json.loads(lines[0])["path"] == "/A/Page"
    assert json.loads(lines[1])["path"] == "/Z/Page"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_jsonl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ado_search.jsonl'`

- [ ] **Step 3: Implement `jsonl.py`**

```python
# src/ado_search/jsonl.py
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_jsonl(path: Path, *, key: str) -> dict[Any, dict]:
    """Read a JSONL file into a dict keyed by the given field. Returns {} if file missing."""
    if not path.exists():
        return {}
    items: dict[Any, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                obj = json.loads(line)
                items[obj[key]] = obj
    return items


def write_jsonl(path: Path, items: dict[Any, dict], *, sort_key: str) -> None:
    """Write items dict to a JSONL file, sorted by sort_key. Uses atomic write."""
    sorted_items = sorted(items.values(), key=lambda x: x[sort_key])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for item in sorted_items:
                f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
        # Atomic replace (on Windows, need to remove target first)
        if path.exists():
            path.unlink()
        os.rename(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def merge_jsonl(
    path: Path,
    new_items: dict[Any, dict],
    *,
    key: str,
    remove_keys: set | None = None,
) -> dict[Any, dict]:
    """Load existing JSONL, merge in new items, optionally remove keys. Returns merged dict."""
    existing = read_jsonl(path, key=key)
    existing.update(new_items)
    if remove_keys:
        for k in remove_keys:
            existing.pop(k, None)
    return existing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jsonl.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/jsonl.py tests/test_jsonl.py
git commit -m "feat: add jsonl module for read/write/merge with atomic writes"
```

---

### Task 2: Expand DB schema with full-text columns and `reindex_from_jsonl()`

**Files:**
- Modify: `src/ado_search/db.py:46-79` (schema), `81-111` (upsert_work_item), `113-132` (upsert_wiki_page)
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for new DB columns and reindex**

Add to `tests/test_db.py`:

```python
def test_upsert_work_item_stores_full_text(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 1, "title": "Test", "type": "Bug", "state": "Active",
        "area": "A", "iteration": "I", "assigned_to": "u@e.com",
        "tags": "t1", "priority": 1, "parent_id": None,
        "created": "2025-01-01", "updated": "2025-01-02",
        "description_snippet": "short", "description": "full description text",
        "acceptance_criteria": "full AC text",
    })
    conn = db._connect()
    row = conn.execute("SELECT description, acceptance_criteria FROM work_items WHERE id = 1").fetchone()
    assert row["description"] == "full description text"
    assert row["acceptance_criteria"] == "full AC text"
    db.close()


def test_upsert_wiki_page_stores_content(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_wiki_page({
        "path": "/Test", "title": "Test", "updated": "2025-01-01",
        "description_snippet": "short", "content": "# Full page content",
    })
    conn = db._connect()
    row = conn.execute("SELECT content FROM wiki_pages WHERE path = '/Test'").fetchone()
    assert row["content"] == "# Full page content"
    db.close()


def test_get_work_item_by_id(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 42, "title": "Test Item", "type": "Task", "state": "New",
        "area": "A", "iteration": "I", "assigned_to": "",
        "tags": "", "priority": 2, "parent_id": None,
        "created": "2025-01-01", "updated": "2025-01-02",
        "description_snippet": "s", "description": "full desc",
        "acceptance_criteria": "ac",
    })
    item = db.get_work_item(42)
    assert item is not None
    assert item["title"] == "Test Item"
    assert item["description"] == "full desc"
    assert db.get_work_item(999) is None
    db.close()


def test_get_wiki_page_by_path(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_wiki_page({
        "path": "/Arch/Overview", "title": "Overview", "updated": "2025-01-01",
        "description_snippet": "s", "content": "# Overview\nBody",
    })
    page = db.get_wiki_page("/Arch/Overview")
    assert page is not None
    assert page["content"] == "# Overview\nBody"
    assert db.get_wiki_page("/missing") is None
    db.close()


def test_reindex_from_jsonl(tmp_path):
    import json
    db = Database(tmp_path / "index.db")
    db.initialize()
    wi_path = tmp_path / "work-items.jsonl"
    wiki_path = tmp_path / "wiki-pages.jsonl"
    wi_path.write_text(
        json.dumps({"id": 1, "title": "Item One", "type": "Bug", "state": "Active",
                     "area": "A", "iteration": "I", "assigned_to": "", "tags": "t1",
                     "priority": 1, "parent_id": None, "created": "2025-01-01",
                     "updated": "2025-01-02", "description": "desc", "acceptance_criteria": "ac"}) + "\n",
        encoding="utf-8",
    )
    wiki_path.write_text(
        json.dumps({"path": "/P", "title": "Page", "updated": "2025-01-01", "content": "body"}) + "\n",
        encoding="utf-8",
    )
    db.reindex_from_jsonl(wi_path, wiki_path)
    results = db.search_work_items("Item One")
    assert len(results) == 1
    assert results[0]["id"] == 1
    wiki_results = db.search_wiki("Page")
    assert len(wiki_results) == 1
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py::test_upsert_work_item_stores_full_text tests/test_db.py::test_reindex_from_jsonl -v`
Expected: FAIL (missing columns, missing methods)

- [ ] **Step 3: Update DB schema and methods**

In `src/ado_search/db.py`, update `initialize()` to add the new columns:

```python
def initialize(self) -> None:
    conn = self._connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS work_items (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            type TEXT NOT NULL,
            state TEXT NOT NULL,
            area TEXT,
            iteration TEXT,
            assigned_to TEXT,
            tags TEXT,
            priority INTEGER,
            parent_id INTEGER,
            created TEXT,
            updated TEXT,
            description TEXT DEFAULT '',
            acceptance_criteria TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS wiki_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            updated TEXT,
            content TEXT DEFAULT ''
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
            item_type,
            item_id UNINDEXED,
            title,
            description_snippet,
            tags
        );
    """)
    # Add columns if upgrading from older schema
    for col, default in [("description", "''"), ("acceptance_criteria", "''")]:
        try:
            conn.execute(f"ALTER TABLE work_items ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass  # column already exists
    try:
        conn.execute("ALTER TABLE wiki_pages ADD COLUMN content TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
```

Update `upsert_work_item()` to include the new columns:

```python
def upsert_work_item(self, item: dict) -> None:
    conn = self._connect()
    conn.execute(
        """INSERT INTO work_items
           (id, title, type, state, area, iteration, assigned_to, tags,
            priority, parent_id, created, updated, description, acceptance_criteria)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, type=excluded.type, state=excluded.state,
            area=excluded.area, iteration=excluded.iteration,
            assigned_to=excluded.assigned_to, tags=excluded.tags,
            priority=excluded.priority, parent_id=excluded.parent_id,
            created=excluded.created, updated=excluded.updated,
            description=excluded.description,
            acceptance_criteria=excluded.acceptance_criteria
        """,
        (
            item["id"], item["title"], item["type"], item["state"],
            item["area"], item["iteration"], item["assigned_to"],
            item["tags"], item["priority"], item["parent_id"],
            item["created"], item["updated"],
            item.get("description", ""), item.get("acceptance_criteria", ""),
        ),
    )
    conn.execute(
        "DELETE FROM search_index WHERE item_type = 'work_item' AND item_id = ?",
        (str(item["id"]),),
    )
    conn.execute(
        "INSERT INTO search_index (item_type, item_id, title, description_snippet, tags) VALUES (?, ?, ?, ?, ?)",
        ("work_item", str(item["id"]), item["title"], item.get("description_snippet", ""), item["tags"]),
    )
    if not self._in_batch:
        conn.commit()
```

Update `upsert_wiki_page()` to include content:

```python
def upsert_wiki_page(self, page: dict) -> None:
    conn = self._connect()
    conn.execute(
        """INSERT INTO wiki_pages (path, title, updated, content)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(path) DO UPDATE SET
            title=excluded.title, updated=excluded.updated, content=excluded.content
        """,
        (page["path"], page["title"], page["updated"], page.get("content", "")),
    )
    conn.execute(
        "DELETE FROM search_index WHERE item_type = 'wiki' AND item_id = ?",
        (page["path"],),
    )
    conn.execute(
        "INSERT INTO search_index (item_type, item_id, title, description_snippet, tags) VALUES (?, ?, ?, ?, ?)",
        ("wiki", page["path"], page["title"], page.get("description_snippet", ""), ""),
    )
    if not self._in_batch:
        conn.commit()
```

Add `get_work_item()`, `get_wiki_page()`, and `reindex_from_jsonl()` methods:

```python
def get_work_item(self, item_id: int) -> dict | None:
    conn = self._connect()
    row = conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None

def get_wiki_page(self, path: str) -> dict | None:
    conn = self._connect()
    row = conn.execute("SELECT * FROM wiki_pages WHERE path = ?", (path,)).fetchone()
    return dict(row) if row else None

def reindex_from_jsonl(self, work_items_path: Path, wiki_pages_path: Path) -> None:
    """Rebuild the entire DB index from JSONL files."""
    import json
    conn = self._connect()
    conn.execute("DELETE FROM work_items")
    conn.execute("DELETE FROM wiki_pages")
    conn.execute("DELETE FROM search_index")
    conn.commit()

    with self.batch():
        if work_items_path.exists():
            with open(work_items_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        item = json.loads(line)
                        item.setdefault("description_snippet", item.get("description", "")[:500])
                        self.upsert_work_item(item)
        if wiki_pages_path.exists():
            with open(wiki_pages_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        page = json.loads(line)
                        page.setdefault("description_snippet", page.get("content", "")[:500])
                        self.upsert_wiki_page(page)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/db.py tests/test_db.py
git commit -m "feat: expand DB schema with full-text columns and reindex_from_jsonl"
```

---

### Task 3: Update `sync_common.py` — accumulate items instead of writing files

**Files:**
- Modify: `src/ado_search/sync_common.py`

The key change: `write_work_item()` no longer writes `.md` files. It returns the metadata dict for JSONL accumulation. Deletion detection no longer touches files on disk.

- [ ] **Step 1: Write failing test for new `prepare_work_item()` function**

Add to `tests/test_jsonl.py`:

```python
from ado_search.sync_common import prepare_work_item


def test_prepare_work_item_returns_jsonl_record():
    raw = {
        "id": 100,
        "fields": {
            "System.Title": "Test",
            "System.WorkItemType": "Bug",
            "System.State": "Active",
            "System.AreaPath": "Area",
            "System.IterationPath": "Iter",
            "System.AssignedTo": {"uniqueName": "u@e.com"},
            "System.Tags": "t1; t2",
            "Microsoft.VSTS.Common.Priority": 1,
            "System.Parent": None,
            "System.CreatedDate": "2025-01-01T00:00:00Z",
            "System.ChangedDate": "2025-01-02T00:00:00Z",
            "System.Description": "<p>Desc</p>",
            "Microsoft.VSTS.Common.AcceptanceCriteria": "<p>AC</p>",
        },
    }
    record = prepare_work_item(raw, comments=[])
    assert record["id"] == 100
    assert record["title"] == "Test"
    assert record["description"] == "Desc"
    assert record["acceptance_criteria"] == "AC"
    assert record["tags"] == "t1,t2"
    assert record["comments"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jsonl.py::test_prepare_work_item_returns_jsonl_record -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Rewrite `sync_common.py`**

```python
# src/ado_search/sync_common.py
from __future__ import annotations

from typing import Any, Callable

from ado_search.markdown import extract_work_item_metadata


def detect_deletions(
    *,
    remote_keys: set,
    local_keys: set,
) -> set:
    """Return keys present locally but absent remotely."""
    return local_keys - remote_keys


def prepare_work_item(
    raw: dict,
    *,
    comments: list[dict] | None = None,
) -> dict:
    """Extract metadata from a raw ADO work item into a flat JSONL-ready dict."""
    meta = extract_work_item_metadata(raw)
    record = {
        "id": meta["id"],
        "title": meta["title"],
        "type": meta["type"],
        "state": meta["state"],
        "area": meta["area"],
        "iteration": meta["iteration"],
        "assigned_to": meta["assigned_to"],
        "tags": meta["tags"],
        "priority": meta["priority"],
        "parent_id": meta["parent_id"],
        "created": meta["created"],
        "updated": meta["updated"],
        "description": meta["description_full"],
        "acceptance_criteria": meta["acceptance_criteria"],
    }
    if comments:
        from ado_search.markdown import strip_html
        record["comments"] = [
            {
                "author": c.get("createdBy", {}).get("displayName", "Unknown"),
                "date": c.get("createdDate", "")[:10],
                "text": strip_html(c.get("text", "")),
            }
            for c in comments
        ]
    else:
        record["comments"] = []
    return record
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_jsonl.py::test_prepare_work_item_returns_jsonl_record -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/sync_common.py tests/test_jsonl.py
git commit -m "refactor: replace write_work_item with prepare_work_item for JSONL accumulation"
```

---

### Task 4: Update `sync_workitems.py` — accumulate into dict, write JSONL

**Files:**
- Modify: `src/ado_search/sync_workitems.py`
- Modify: `tests/test_sync_workitems.py`

- [ ] **Step 1: Update `_fetch_and_write_item` to return a record instead of writing files**

Rename to `_fetch_item` and return the prepared record:

```python
async def _fetch_item(
    item_id: int,
    *,
    auth_method: str,
    org: str,
    project: str,
    pat: str = "",
    semaphore: asyncio.Semaphore,
    include_comments: bool = False,
) -> dict | str:
    """Fetch a single work item. Returns JSONL record dict or error string."""
    async with semaphore:
        raw = await fetch_and_parse(
            auth_method, OP_SHOW, f"#{item_id}",
            org=org, project=project, pat=pat, work_item_id=item_id,
        )
        if isinstance(raw, str):
            return raw

    comments = []
    if include_comments:
        async with semaphore:
            comments = await _fetch_comments(item_id, auth_method, org, project, pat=pat)

    return prepare_work_item(raw, comments=comments)
```

- [ ] **Step 2: Update `sync_work_items` to accumulate records and write JSONL**

```python
async def sync_work_items(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    db: Database,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
    max_concurrent: int = 5,
    include_comments: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    from ado_search.sync_odata import sync_via_odata

    click.echo("  Trying OData analytics (fast path)...")
    odata_result = await sync_via_odata(
        org=org, project=project, auth_method=auth_method, pat=pat,
        data_dir=data_dir, db=db,
        work_item_types=work_item_types, area_paths=area_paths,
        states=states, last_sync=last_sync, dry_run=dry_run,
    )

    if odata_result is not None:
        return odata_result

    click.echo("  OData not available, using WIQL fallback...")

    item_ids = await _discover_work_item_ids(
        auth_method=auth_method, org=org, project=project, pat=pat,
        work_item_types=work_item_types, area_paths=area_paths,
        states=states, last_sync=last_sync,
    )

    click.echo(f"  Found {len(item_ids)} work items to sync")

    if dry_run:
        click.echo(f"Would fetch {len(item_ids)} work items: {item_ids[:20]}...")
        return {"fetched": 0, "errors": 0, "dry_run": True, "would_fetch": len(item_ids)}

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _fetch_item(
            item_id, auth_method=auth_method, org=org, project=project,
            pat=pat, semaphore=semaphore, include_comments=include_comments,
        )
        for item_id in item_ids
    ]

    results = await asyncio.gather(*tasks)

    # Separate successes from errors
    fetched_records: dict[int, dict] = {}
    errors: list[str] = []
    for r in results:
        if isinstance(r, str):
            errors.append(r)
            click.echo(f"  Warning: {r}", err=True)
        else:
            fetched_records[r["id"]] = r

    # Merge with existing JSONL (incremental) or write fresh (full sync)
    from ado_search.jsonl import merge_jsonl, write_jsonl
    wi_jsonl = data_dir / "work-items.jsonl"

    if last_sync:
        all_items = merge_jsonl(wi_jsonl, fetched_records, key="id")
    else:
        # Full sync — detect orphans by comparing against existing JSONL
        from ado_search.jsonl import read_jsonl
        existing = read_jsonl(wi_jsonl, key="id")
        orphan_ids = set(existing.keys()) - set(fetched_records.keys())
        if orphan_ids:
            click.echo(f"  Removing {len(orphan_ids)} orphaned items")
        all_items = fetched_records

    write_jsonl(wi_jsonl, all_items, sort_key="id")

    # Rebuild DB index from JSONL
    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)

    return {"fetched": len(fetched_records), "errors": len(errors)}
```

- [ ] **Step 3: Remove `detect_work_item_deletions` function** (no longer needed — orphan detection is now JSONL-based)

Delete the `detect_work_item_deletions` function and the import of `detect_deletions` from `sync_common`.

Update imports at top of file:
```python
from ado_search.sync_common import prepare_work_item
```

- [ ] **Step 4: Update tests**

```python
# tests/test_sync_workitems.py
# Update test_sync_work_items_writes_files_and_indexes to check JSONL instead of .md files:

async def test_sync_work_items_writes_jsonl_and_indexes(data_dir, db):
    """Sync should produce JSONL and populate DB index."""
    wiql_result = json.dumps({"workItems": [{"id": 12345}]})
    item_json = (FIXTURE_DIR / "work_item_12345.json").read_text()
    comments_json = json.dumps({"comments": []})

    async def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "analytics.dev.azure.com" in cmd_str:
            return CommandResult(command=cmd, returncode=1, stdout="", stderr="403 Forbidden")
        if "query" in cmd_str and "--wiql" in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=wiql_result, stderr="")
        if "12345" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_json, stderr="")
        return CommandResult(command=cmd, returncode=0, stdout=comments_json, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = await sync_work_items(
            org="https://dev.azure.com/contoso", project="MyProject",
            auth_method="az-cli", data_dir=data_dir, db=db,
            work_item_types=["Bug"], area_paths=[], states=[], last_sync="",
        )

    assert stats["fetched"] == 1
    assert stats["errors"] == 0

    # JSONL file should exist
    jsonl_path = data_dir / "work-items.jsonl"
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["id"] == 12345

    # DB should be populated
    results = db.search_work_items("SSO login")
    assert len(results) == 1
    assert results[0]["id"] == 12345


# Update test_deletion_detection for JSONL-based orphan detection:

async def test_deletion_detection_via_jsonl(data_dir, db):
    """Full sync should remove items from JSONL that no longer exist remotely."""
    from ado_search.jsonl import write_jsonl
    # Pre-populate JSONL with an item that won't be in the remote set
    wi_jsonl = data_dir / "work-items.jsonl"
    write_jsonl(wi_jsonl, {
        999: {"id": 999, "title": "Deleted", "type": "Bug", "state": "Removed",
              "area": "", "iteration": "", "assigned_to": "", "tags": "",
              "priority": 1, "parent_id": None, "created": "2025-01-01",
              "updated": "2025-01-01", "description": "", "acceptance_criteria": "",
              "comments": []},
    }, sort_key="id")

    wiql_result = json.dumps({"workItems": [{"id": 12345}]})
    item_json = (FIXTURE_DIR / "work_item_12345.json").read_text()
    comments_json = json.dumps({"comments": []})

    async def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "analytics.dev.azure.com" in cmd_str:
            return CommandResult(command=cmd, returncode=1, stdout="", stderr="403 Forbidden")
        if "--wiql" in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=wiql_result, stderr="")
        if "12345" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_json, stderr="")
        return CommandResult(command=cmd, returncode=0, stdout=comments_json, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        await sync_work_items(
            org="https://dev.azure.com/contoso", project="MyProject",
            auth_method="az-cli", data_dir=data_dir, db=db,
            work_item_types=["Bug"], area_paths=[], states=[], last_sync="",
        )

    # Only item 12345 should remain in JSONL
    from ado_search.jsonl import read_jsonl
    items = read_jsonl(wi_jsonl, key="id")
    assert 12345 in items
    assert 999 not in items

    # DB should only have 12345
    assert db.search_work_items("Deleted") == []
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_sync_workitems.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/ado_search/sync_workitems.py tests/test_sync_workitems.py
git commit -m "feat: sync_workitems writes JSONL instead of individual markdown files"
```

---

### Task 5: Update `sync_odata.py` — accumulate into dict, write JSONL

**Files:**
- Modify: `src/ado_search/sync_odata.py`
- Modify: `tests/test_sync_odata.py`

- [ ] **Step 1: Update `sync_via_odata` to return fetched records instead of writing files**

Replace the `_process_page` / `write_work_item` pattern with accumulating into a `fetched_records` dict:

```python
async def sync_via_odata(
    *,
    org: str, project: str, auth_method: str, pat: str = "",
    data_dir: Path, db: Database,
    work_item_types: list[str], area_paths: list[str],
    states: list[str], last_sync: str, dry_run: bool = False,
) -> SyncResult | None:
    # ... (probe/parse first page unchanged through line 157) ...

    if dry_run:
        # ... (unchanged) ...

    fetched = 0
    errors = 0
    fetched_records: dict[int, dict] = {}

    def _process_page(items: list[dict]) -> None:
        nonlocal fetched, errors
        for item in items:
            try:
                ado_format = odata_to_ado_format(item)
                record = prepare_work_item(ado_format, comments=None)
                fetched_records[record["id"]] = record
                fetched += 1
            except Exception as e:
                click.echo(f"  Warning: Failed to process item: {e}", err=True)
                errors += 1

    _process_page(data.get("value", []))

    while next_link:
        result = await run_operation(
            auth_method, OP_ODATA_QUERY, org=org, project=project, pat=pat, url=next_link,
        )
        if result.returncode != 0:
            click.echo(f"  Warning: OData pagination failed: {result.stderr}", err=True)
            break
        page_data = result.parse_json()
        _process_page(page_data.get("value", []))
        next_link = page_data.get("@odata.nextLink")
        click.echo(f"  Processed {fetched} items via OData...")

    click.echo(f"  OData: {fetched} work items processed")

    # Write JSONL and rebuild index
    from ado_search.jsonl import merge_jsonl, read_jsonl, write_jsonl
    wi_jsonl = data_dir / "work-items.jsonl"

    if last_sync:
        all_items = merge_jsonl(wi_jsonl, fetched_records, key="id")
    else:
        existing = read_jsonl(wi_jsonl, key="id")
        orphan_ids = set(existing.keys()) - set(fetched_records.keys())
        if orphan_ids:
            click.echo(f"  Removing {len(orphan_ids)} orphaned items")
        all_items = fetched_records

    write_jsonl(wi_jsonl, all_items, sort_key="id")

    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)

    return {"fetched": fetched, "errors": errors}
```

Update imports:
```python
from ado_search.sync_common import prepare_work_item
```

Remove `write_work_item` import.

- [ ] **Step 2: Update tests to check JSONL instead of .md files**

In `tests/test_sync_odata.py`, update `test_sync_via_odata_success`:
```python
# Replace:
assert (data_dir / "work-items" / "100.md").exists()
# With:
jsonl_path = data_dir / "work-items.jsonl"
assert jsonl_path.exists()
items = read_jsonl(jsonl_path, key="id")
assert 100 in items
```

Similarly update `test_sync_via_odata_pagination` and `test_sync_via_odata_dry_run`.

Remove `assert 100 in stats["fetched_ids"]` checks (field removed).

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_sync_odata.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/ado_search/sync_odata.py tests/test_sync_odata.py
git commit -m "feat: sync_odata writes JSONL instead of individual markdown files"
```

---

### Task 6: Update `sync_wiki.py` — accumulate into dict, write JSONL

**Files:**
- Modify: `src/ado_search/sync_wiki.py`
- Modify: `tests/test_sync_wiki.py`

- [ ] **Step 1: Update `_fetch_and_write_page` to return a record instead of writing files**

Rename to `_fetch_page`, return a dict:

```python
async def _fetch_page(
    wiki_name: str,
    page_path: str,
    *,
    auth_method: str,
    org: str,
    project: str,
    pat: str = "",
    semaphore: asyncio.Semaphore,
) -> dict | str:
    """Fetch a wiki page. Returns JSONL record dict or error string."""
    data = await fetch_and_parse(
        auth_method, OP_WIKI_PAGE_SHOW, f"wiki page {page_path}",
        org=org, project=project, pat=pat, semaphore=semaphore,
        wiki=wiki_name, path=page_path,
    )
    if isinstance(data, str):
        return data

    content = data.get("content", "")
    title = page_path.split("/")[-1].replace("-", " ")
    updated = data.get("dateModified", "")[:10]

    return {
        "path": page_path,
        "title": title,
        "updated": updated,
        "content": content,
    }
```

- [ ] **Step 2: Update `sync_wiki` to accumulate records and write JSONL**

Replace the `db.batch()` + file-writing pattern with record accumulation:

```python
# Stage 2: Fetch all pages
semaphore = asyncio.Semaphore(max_concurrent)
tasks = [
    _fetch_page(
        wiki_name, page_path,
        auth_method=auth_method, org=org, project=project, pat=pat,
        semaphore=semaphore,
    )
    for wiki_name, page_path in all_page_tasks
]

results = await asyncio.gather(*tasks)

fetched_records: dict[str, dict] = {}
for r in results:
    if isinstance(r, str):
        total_errors += 1
        click.echo(f"  Warning: {r}", err=True)
    else:
        fetched_records[r["path"]] = r
        total_fetched += 1

# Write JSONL
from ado_search.jsonl import read_jsonl, write_jsonl
wiki_jsonl = data_dir / "wiki-pages.jsonl"

if enum_errors == 0:
    existing = read_jsonl(wiki_jsonl, key="path")
    orphan_paths = set(existing.keys()) - all_remote_paths
    if orphan_paths:
        click.echo(f"  Removing {len(orphan_paths)} orphaned wiki pages")
    # Keep only remote pages
    all_pages = {k: v for k, v in existing.items() if k in all_remote_paths}
    all_pages.update(fetched_records)
else:
    # Partial failure — merge without deleting
    from ado_search.jsonl import merge_jsonl
    all_pages = merge_jsonl(wiki_jsonl, fetched_records, key="path")

write_jsonl(wiki_jsonl, all_pages, sort_key="path")

# Rebuild DB index
wi_jsonl = data_dir / "work-items.jsonl"
db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
```

Remove `detect_wiki_deletions`, `detect_deletions` import, `_wiki_path_to_filepath`, and all file-writing code.

- [ ] **Step 3: Update tests**

Update `test_sync_wiki_writes_files_and_indexes` to check JSONL instead of `.md` files.
Update `test_wiki_deletion_detection` to verify JSONL-based orphan removal.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sync_wiki.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/sync_wiki.py tests/test_sync_wiki.py
git commit -m "feat: sync_wiki writes JSONL instead of individual markdown files"
```

---

### Task 7: Update `cli.py` — auto-reindex, update `show` and `init`

**Files:**
- Modify: `src/ado_search/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_e2e.py`

- [ ] **Step 1: Add `_ensure_index` helper for auto-reindex**

```python
def _ensure_index(data_path: Path, db: Database) -> None:
    """Rebuild DB index from JSONL if stale or missing."""
    db_path = data_path / "index.db"
    wi_jsonl = data_path / "work-items.jsonl"
    wiki_jsonl = data_path / "wiki-pages.jsonl"

    if not wi_jsonl.exists() and not wiki_jsonl.exists():
        return  # nothing to index

    needs_reindex = not db_path.exists()
    if not needs_reindex:
        db_mtime = db_path.stat().st_mtime
        for jsonl in [wi_jsonl, wiki_jsonl]:
            if jsonl.exists() and jsonl.stat().st_mtime > db_mtime:
                needs_reindex = True
                break

    if needs_reindex:
        db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
```

- [ ] **Step 2: Update `search_cmd` to auto-reindex**

Replace the `index.db` existence check:

```python
@main.command("search")
# ... (options unchanged) ...
def search_cmd(query, type_filter, state_filter, area_filter, assigned_to, tag_filter,
               limit, fmt, data_dir):
    """Search indexed Azure DevOps data."""
    data_path = Path(data_dir) if data_dir else _default_data_dir()

    wi_jsonl = data_path / "work-items.jsonl"
    wiki_jsonl = data_path / "wiki-pages.jsonl"
    if not wi_jsonl.exists() and not wiki_jsonl.exists():
        click.echo("Error: No data found. Run 'ado-search sync' first.", err=True)
        raise SystemExit(1)

    db = Database(data_path / "index.db")
    db.initialize()

    try:
        _ensure_index(data_path, db)
        # ... (rest unchanged) ...
```

- [ ] **Step 3: Update `show` command to render from DB**

```python
@main.command()
@click.argument("item_id")
@click.option("--data-dir", type=click.Path(), default=None)
def show(item_id: str, data_dir: str | None):
    """Show full content of a work item or wiki page."""
    data_path = Path(data_dir) if data_dir else _default_data_dir()

    db = Database(data_path / "index.db")
    db.initialize()

    try:
        _ensure_index(data_path, db)

        # Try as work item ID
        try:
            wi_id = int(item_id)
            item = db.get_work_item(wi_id)
            if item:
                from ado_search.markdown import work_item_to_markdown
                meta = dict(item)
                meta["description_full"] = meta.pop("description", "")
                meta["description_snippet"] = meta["description_full"][:500]
                md = work_item_to_markdown({}, meta=meta)
                click.echo(md)
                return
        except ValueError:
            pass

        # Try as wiki path
        wiki_path = item_id if item_id.startswith("/") else f"/{item_id}"
        page = db.get_wiki_page(wiki_path)
        if page:
            from ado_search.markdown import wiki_page_to_markdown
            click.echo(wiki_page_to_markdown(page["title"], page["content"]))
            return

        click.echo(f"Error: Item '{item_id}' not found.", err=True)
        raise SystemExit(1)

    finally:
        db.close()
```

- [ ] **Step 4: Update `init` command — remove work-items/wiki directory creation**

```python
# Remove these lines from init():
(data_path / "work-items").mkdir(exist_ok=True)
(data_path / "wiki").mkdir(exist_ok=True)
```

- [ ] **Step 5: Update `search.py` — remove file_path references**

In `src/ado_search/search.py`, the `file_path` field in search results should no longer reference `.md` files. Keep it for backward compatibility but point to JSONL conceptually:

```python
# In search(), change work item file_path:
"file_path": f"work-items.jsonl#id={r['id']}",
# In search(), change wiki file_path:
"file_path": f"wiki-pages.jsonl#path={clean_path}",
```

- [ ] **Step 6: Update conftest.py — remove work-items/wiki directory creation**

```python
@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / ".ado-search"
    d.mkdir()
    return d
```

- [ ] **Step 7: Update `test_e2e.py` for new flow**

Update `test_full_workflow` to check JSONL output instead of `.md` files, and update `show` assertions.

- [ ] **Step 8: Update `test_cli.py`**

Update `test_init_creates_config` to not check for `work-items/` and `wiki/` dirs.
Update `test_show_work_item` to seed DB instead of writing `.md` files.

- [ ] **Step 9: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/ado_search/cli.py src/ado_search/search.py tests/conftest.py tests/test_cli.py tests/test_e2e.py
git commit -m "feat: auto-reindex from JSONL, show renders from DB, remove .md file dependencies"
```

---

### Task 8: Version bump and final cleanup

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `src/ado_search/__init__.py:1`

- [ ] **Step 1: Bump version**

In `pyproject.toml`, change `version = "0.4.0"` to `version = "0.5.0"`.
In `src/ado_search/__init__.py`, change `__version__ = "0.4.0"` to `__version__ = "0.5.0"`.

- [ ] **Step 2: Clean up unused code**

Remove `_wiki_path_to_filepath` from `sync_wiki.py` if not already removed.
Remove `detect_deletions` from `sync_common.py` if not already removed.
Remove old `detect_work_item_deletions` and `detect_wiki_deletions` if not already removed.
Remove `write_work_item` from `sync_common.py` if not already removed.
Verify no imports reference removed functions.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Build check**

Run: `python -m build`
Expected: sdist and wheel build successfully

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: bump version to 0.5.0, clean up unused file-based code"
```

---

### Verification Checklist

After all tasks complete:

1. `pytest tests/ -v` — all tests pass
2. `python -m build` — builds sdist and wheel
3. No references to `.md` file paths remain in sync code (grep for `write_text`, `work-items/`, `wiki/`)
4. `work-items.jsonl` produced by sync tests
5. `wiki-pages.jsonl` produced by sync tests
6. `show` command renders from DB, not files
7. Search auto-rebuilds index from JSONL when DB is missing
