# Multi-project sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one ado-search data dir sync and search many Azure DevOps projects, and expose a `project` filter through ado-search-mcp.

**Architecture:**
- A new `projects.py` owns project resolution, per-project watermarks, and area-path → project helpers.
- The existing single-project sync paths run once per project.
- `finalize_jsonl` scopes orphan deletion to the project being synced.
- The OData page loop stops relying on `nextLink`.
- `work_items.project` is indexed, and `search` / `grep` filter on it.

**Tech Stack:** Python ≥3.10, click, sqlite3, pytest (+pytest-asyncio, `asyncio_mode = auto`); TypeScript / zod for the MCP.

**Spec:** `docs/superpowers/specs/2026-09-28-multi-project-design.md`

## Global Constraints

- Backward compatible: an existing config with only `organization.project` and `sync.last_sync` must sync and search exactly as before.
- Python floor stays 3.10 (`tomli` fallback already present). No new runtime dependencies.
- Public repo: tests use placeholder names only (`contoso`, `Alpha`, `Beta`, `MyProject`). No real org, project, or person names.
- Run tests with `.venv/Scripts/python -m pytest` (venv already created at `.venv`; CI uses `python -m pytest`).
- Release version `1.14.0`; MCP version `0.2.0`.
- Wiki sync stays single-project (default project only).

## Review Focus

1. **A full sync of one project must not delete another project's items**, including old records that predate the `project` field. Covered in Task 3 (`test_finalize_full_sync_scope_keeps_other_projects`, `test_finalize_scope_falls_back_to_area_for_old_records`).
2. **The first multi-project run after upgrading from a legacy config** must reuse the legacy `last_sync` for the legacy project only, and must never apply it to a newly added project. Covered in Task 1 (`test_watermark_for_*`, `test_record_watermark_migrates_legacy_before_overwriting`).
3. **One project failing (401, bad name, WIQL error) must not abort the others or advance its own watermark.** Covered in Task 6 (`test_sync_failure_in_one_project_continues`).
4. **Pagination failure mid-way through a full OData sync must not finalize partial data**, because that would delete every item not yet fetched. Covered in Task 4 (`test_sync_via_odata_raises_on_pagination_failure`).
5. **Project names with spaces and area paths with backslashes survive `save_config`**, which runs after every sync. Covered in Task 1 (`test_save_quotes_non_bare_keys`, `test_save_escapes_backslashes_in_strings`).

---

### Task 1: Project resolution, watermarks, and TOML quoting

**Files:**
- Create: `src/ado_search/projects.py`
- Modify: `src/ado_search/config.py:49-73` (`_dict_to_toml`)
- Modify: `src/ado_search/auth.py:10-26` (op constant), `auth.py:86-193` (`OPERATIONS`)
- Test: `tests/test_projects.py` (new), `tests/test_config.py`

**Interfaces:**
- Produces (in `ado_search.projects`):
  - `ALL_PROJECTS = "*"`
  - `configured_projects(cfg: dict) -> list[str]`
  - `expand_projects(projects: list[str], remote_names: list[str]) -> list[str]`
  - `default_project(cfg: dict) -> str` (raises `ValueError`)
  - `watermark_for(cfg: dict, project: str) -> str`
  - `record_watermark(cfg: dict, project: str, date: str) -> None`
  - `project_from_area(area: str) -> str`
  - `record_project(record: dict) -> str`
  - `async fetch_remote_projects(*, auth_method: str, org: str, pat: str = "") -> list[str]`
- Produces (in `ado_search.auth`): `OP_PROJECT_LIST = "project-list"`, `PROJECT_LIST_TOP = 1000`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_save_quotes_non_bare_keys(tmp_path):
    cfg = default_config()
    cfg["sync"]["last_sync_by_project"] = {"Wave Two": "2026-09-01", "Alpha": "2026-09-02"}
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded["sync"]["last_sync_by_project"] == {"Wave Two": "2026-09-01", "Alpha": "2026-09-02"}


def test_save_escapes_backslashes_in_strings(tmp_path):
    cfg = default_config()
    cfg["sync"]["area_paths"] = ["Alpha\\Web", 'Alpha\\"quoted"']
    cfg["organization"]["projects"] = ["Alpha", "Beta Two"]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded["sync"]["area_paths"] == ["Alpha\\Web", 'Alpha\\"quoted"']
    assert loaded["organization"]["projects"] == ["Alpha", "Beta Two"]
```

Create `tests/test_projects.py`:

```python
import asyncio
import json
from unittest.mock import patch

import pytest

from ado_search.config import default_config
from ado_search.projects import (
    ALL_PROJECTS,
    configured_projects,
    default_project,
    expand_projects,
    fetch_remote_projects,
    project_from_area,
    record_project,
    record_watermark,
    watermark_for,
)
from ado_search.runner import CommandResult


def _cfg(project="", projects=None, last_sync="", by_project=None):
    cfg = default_config()
    cfg["organization"]["url"] = "https://dev.azure.com/contoso"
    cfg["organization"]["project"] = project
    if projects is not None:
        cfg["organization"]["projects"] = projects
    cfg["sync"]["last_sync"] = last_sync
    if by_project is not None:
        cfg["sync"]["last_sync_by_project"] = by_project
    return cfg


def test_configured_projects_legacy_single():
    assert configured_projects(_cfg(project="Alpha")) == ["Alpha"]


def test_configured_projects_list_wins_over_legacy():
    assert configured_projects(_cfg(project="Alpha", projects=["Beta", "Gamma"])) == ["Beta", "Gamma"]


def test_configured_projects_empty():
    assert configured_projects(_cfg()) == []


def test_expand_projects_star_uses_remote_sorted():
    assert expand_projects([ALL_PROJECTS], ["beta", "Alpha"]) == ["Alpha", "beta"]


def test_expand_projects_without_star_is_identity():
    assert expand_projects(["Alpha"], ["Beta"]) == ["Alpha"]


def test_default_project_prefers_legacy():
    assert default_project(_cfg(project="Alpha", projects=["Beta"])) == "Alpha"


def test_default_project_first_explicit_entry():
    assert default_project(_cfg(projects=["*", "Beta"])) == "Beta"


def test_default_project_star_only_raises():
    with pytest.raises(ValueError, match="organization.project"):
        default_project(_cfg(projects=["*"]))


def test_watermark_for_per_project_entry():
    cfg = _cfg(project="Alpha", last_sync="2026-01-01", by_project={"Beta": "2026-02-02"})
    assert watermark_for(cfg, "Beta") == "2026-02-02"


def test_watermark_for_legacy_project_falls_back_to_last_sync():
    cfg = _cfg(project="Alpha", projects=["Alpha", "Beta"], last_sync="2026-01-01")
    assert watermark_for(cfg, "Alpha") == "2026-01-01"


def test_watermark_for_new_project_is_empty():
    cfg = _cfg(project="Alpha", projects=["Alpha", "Beta"], last_sync="2026-01-01")
    assert watermark_for(cfg, "Beta") == ""


def test_record_watermark_migrates_legacy_before_overwriting():
    cfg = _cfg(project="Alpha", projects=["Alpha", "Beta"], last_sync="2026-01-01")
    record_watermark(cfg, "Beta", "2026-09-28")
    assert cfg["sync"]["last_sync_by_project"] == {"Alpha": "2026-01-01", "Beta": "2026-09-28"}
    assert cfg["sync"]["last_sync"] == "2026-09-28"
    # Alpha was not synced this run, so its own watermark is untouched
    assert watermark_for(cfg, "Alpha") == "2026-01-01"


def test_project_from_area():
    assert project_from_area("Alpha\\Web\\API") == "Alpha"
    assert project_from_area("Alpha") == "Alpha"
    assert project_from_area("") == ""


def test_record_project_prefers_field_then_area():
    assert record_project({"project": "Beta", "area": "Alpha\\X"}) == "Beta"
    assert record_project({"area": "Alpha\\X"}) == "Alpha"
    assert record_project({"project": "", "area": ""}) == ""


def test_fetch_remote_projects_parses_names():
    payload = json.dumps({"count": 2, "value": [{"name": "Beta"}, {"name": "Alpha"}]})
    seen = []

    async def fake_run(cmd, **kwargs):
        seen.append(" ".join(str(c) for c in cmd))
        return CommandResult(command=cmd, returncode=0, stdout=payload, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        names = asyncio.run(fetch_remote_projects(
            auth_method="az-cli", org="https://dev.azure.com/contoso",
        ))

    assert names == ["Alpha", "Beta"]
    assert "https://dev.azure.com/contoso/_apis/projects" in seen[0]


def test_fetch_remote_projects_raises_on_failure():
    async def fake_run(cmd, **kwargs):
        return CommandResult(command=cmd, returncode=1, stdout="", stderr="401 Unauthorized")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="project list"):
            asyncio.run(fetch_remote_projects(
                auth_method="az-cli", org="https://dev.azure.com/contoso",
            ))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_projects.py tests/test_config.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'ado_search.projects'`. The two config tests fail on TOML parse (`Wave Two` bare key) or the backslash escape.

- [ ] **Step 3: Implement**

In `src/ado_search/config.py`, add `import json` and `import re` at the top. Then replace `_dict_to_toml` with:

```python
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    """Quote keys that are not bare TOML keys (e.g. project names with spaces)."""
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _toml_str(value: str) -> str:
    """TOML basic string. JSON string escapes are a subset of TOML's."""
    return json.dumps(value, ensure_ascii=False)


def _dict_to_toml(d: dict, prefix: str = "") -> str:
    """Minimal TOML serializer for our config structure."""
    lines: list[str] = []
    tables: list[tuple[str, dict]] = []

    for key, value in d.items():
        if isinstance(value, dict):
            k = _toml_key(key)
            full_key = k if not prefix else f"{prefix}.{k}"
            tables.append((full_key, value))
        elif isinstance(value, list):
            items = ", ".join(_toml_str(v) if isinstance(v, str) else str(v) for v in value)
            lines.append(f"{_toml_key(key)} = [{items}]")
        elif isinstance(value, str):
            lines.append(f"{_toml_key(key)} = {_toml_str(value)}")
        elif isinstance(value, bool):
            lines.append(f"{_toml_key(key)} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{_toml_key(key)} = {value}")

    result = "\n".join(lines)
    for table_key, table_val in tables:
        section = _dict_to_toml(table_val, prefix=table_key)
        result += f"\n\n[{table_key}]\n{section}"

    return result.strip() + "\n"
```

In `src/ado_search/auth.py`, add after `OP_IDENTITY_LOOKUP = "identity-lookup"`:

```python
OP_PROJECT_LIST = "project-list"

PROJECT_LIST_TOP = 1000
```

Add this entry to the end of `OPERATIONS`:

```python
    OP_PROJECT_LIST: OperationDef(
        path="_apis/projects",
        query_params=[f"$top={PROJECT_LIST_TOP}", "api-version=7.1"],
    ),
```

Create `src/ado_search/projects.py`:

```python
"""Project resolution, per-project sync watermarks, and project helpers."""
from __future__ import annotations

import click

from ado_search.auth import OP_PROJECT_LIST, PROJECT_LIST_TOP
from ado_search.runner import fetch_and_parse

ALL_PROJECTS = "*"


def configured_projects(cfg: dict) -> list[str]:
    """Projects to sync: organization.projects if set, else the legacy single project."""
    org = cfg.get("organization", {})
    projects = [p for p in (org.get("projects") or []) if p]
    if projects:
        return projects
    legacy = org.get("project", "")
    return [legacy] if legacy else []


def expand_projects(projects: list[str], remote_names: list[str]) -> list[str]:
    """Replace a "*" entry with every project the credentials can see."""
    if ALL_PROJECTS not in projects:
        return list(projects)
    return sorted(set(remote_names), key=str.lower)


def default_project(cfg: dict) -> str:
    """Project used by write commands and wiki sync."""
    org = cfg.get("organization", {})
    if org.get("project"):
        return org["project"]
    for p in org.get("projects") or []:
        if p and p != ALL_PROJECTS:
            return p
    raise ValueError(
        'No default project. Set organization.project in config.toml '
        '(projects = ["*"] has no default).'
    )


def watermark_for(cfg: dict, project: str) -> str:
    """Incremental-sync watermark for one project ("" means full sync)."""
    sync = cfg.get("sync", {})
    by_project = sync.get("last_sync_by_project") or {}
    if project in by_project:
        return by_project[project]
    if project and project == cfg.get("organization", {}).get("project", ""):
        return sync.get("last_sync", "")
    return ""


def record_watermark(cfg: dict, project: str, date: str) -> None:
    """Record a successful sync of one project.

    The legacy project's old watermark is copied into the per-project table
    first, so bumping the legacy ``last_sync`` never skips its changes.
    """
    sync = cfg.setdefault("sync", {})
    by_project = sync.setdefault("last_sync_by_project", {})
    legacy = cfg.get("organization", {}).get("project", "")
    if legacy and legacy not in by_project and sync.get("last_sync"):
        by_project[legacy] = sync["last_sync"]
    by_project[project] = date
    sync["last_sync"] = date


def project_from_area(area: str) -> str:
    """The first segment of an ADO area path is always the project name."""
    return area.split("\\", 1)[0] if area else ""


def record_project(record: dict) -> str:
    return record.get("project") or project_from_area(record.get("area") or "")


async def fetch_remote_projects(*, auth_method: str, org: str, pat: str = "") -> list[str]:
    data = await fetch_and_parse(
        auth_method, OP_PROJECT_LIST, "project list", org=org, project="", pat=pat,
    )
    if isinstance(data, str):
        raise RuntimeError(data)
    names = sorted((p["name"] for p in data.get("value", [])), key=str.lower)
    if len(names) >= PROJECT_LIST_TOP:
        click.echo(
            f"  Warning: project list capped at {PROJECT_LIST_TOP}; list projects explicitly",
            err=True,
        )
    return names
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_projects.py tests/test_config.py tests/test_auth.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/projects.py src/ado_search/config.py src/ado_search/auth.py tests/test_projects.py tests/test_config.py
git commit -m "feat: project resolution, per-project watermarks, safe TOML quoting"
```

---

### Task 2: `project` field on work item records

**Files:**
- Modify: `src/ado_search/markdown.py:56-98` (`extract_work_item_metadata`)
- Modify: `src/ado_search/sync_common.py:14-56` (`prepare_work_item`)
- Modify: `src/ado_search/sync_odata.py:92-128` (`odata_to_ado_format`), `sync_odata.py:202-212` (`_process_page`)
- Test: `tests/test_sync_common.py`, `tests/test_sync_odata.py`

**Interfaces:**
- Consumes: `project_from_area` (Task 1)
- Produces:
  - Every record from `prepare_work_item` has `"project": str`
  - `odata_to_ado_format(odata_item: dict, project: str = "") -> dict` sets `System.TeamProject`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_common.py`:

```python
def test_prepare_work_item_takes_team_project():
    from ado_search.sync_common import prepare_work_item
    raw = {"id": 1, "fields": {
        "System.Title": "t", "System.WorkItemType": "Bug", "System.State": "New",
        "System.AreaPath": "Alpha\\Web", "System.TeamProject": "Beta",
    }}
    assert prepare_work_item(raw)["project"] == "Beta"


def test_prepare_work_item_falls_back_to_area_root():
    from ado_search.sync_common import prepare_work_item
    raw = {"id": 2, "fields": {
        "System.Title": "t", "System.WorkItemType": "Bug", "System.State": "New",
        "System.AreaPath": "Alpha\\Web",
    }}
    assert prepare_work_item(raw)["project"] == "Alpha"
```

Append to `tests/test_sync_odata.py`:

```python
def test_odata_to_ado_format_sets_project():
    from ado_search.sync_common import prepare_work_item
    item = {"WorkItemId": 5, "Title": "x", "WorkItemType": "Bug", "State": "New",
            "Area": {"AreaPath": "Other\\Area"}}
    ado = odata_to_ado_format(item, project="Beta")
    assert ado["fields"]["System.TeamProject"] == "Beta"
    assert prepare_work_item(ado)["project"] == "Beta"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_sync_common.py tests/test_sync_odata.py -q`
Expected: FAIL. `KeyError: 'project'` and `TypeError: odata_to_ado_format() got an unexpected keyword argument 'project'`.

- [ ] **Step 3: Implement**

In `markdown.py` `extract_work_item_metadata`, add this to the returned dict right after `"id": raw["id"],`:

```python
        "project": fields.get("System.TeamProject", "") or "",
```

In `sync_common.py`, add `from ado_search.projects import project_from_area` to the imports. In `prepare_work_item`, add right after `"id": meta["id"],`:

```python
        "project": meta["project"] or project_from_area(meta["area"]),
```

In `sync_odata.py`, change the signature to `def odata_to_ado_format(odata_item: dict, project: str = "") -> dict:`. Add this as the first entry of the `"fields"` dict:

```python
            "System.TeamProject": project,
```

In `sync_via_odata._process_page`, change `ado_format = odata_to_ado_format(item)` to:

```python
                ado_format = odata_to_ado_format(item, project=project)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/markdown.py src/ado_search/sync_common.py src/ado_search/sync_odata.py tests/test_sync_common.py tests/test_sync_odata.py
git commit -m "feat: record the owning project on every work item"
```

---

### Task 3: Project-scoped orphan detection and state_history carry-forward

**Files:**
- Modify: `src/ado_search/sync_common.py:105-139` (`finalize_jsonl`)
- Modify: `src/ado_search/sync_odata.py:230-234` (finalize call)
- Modify: `src/ado_search/sync_workitems.py:328-376` (`_fetch_and_finalize`), `sync_workitems.py:457-464` (`sync_work_items` call)
- Test: `tests/test_sync_common.py`, `tests/test_sync_odata.py`

**Interfaces:**
- Consumes: `record_project` (Task 1)
- Produces:
  - `finalize_jsonl(jsonl_path, fetched_records, *, key, sort_key, is_incremental, remote_keys=None, scope: Callable[[dict], bool] | None = None) -> set`
  - `_fetch_and_finalize(..., scope_project: str | None = None)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_common.py`:

```python
from ado_search.projects import record_project


def _rec(i, project=None, area="", **extra):
    r = {"id": i, "title": f"item {i}", "area": area}
    if project is not None:
        r["project"] = project
    r.update(extra)
    return r


def test_finalize_full_sync_scope_keeps_other_projects(tmp_path):
    path = tmp_path / "work-items.jsonl"
    write_jsonl(path, {1: _rec(1, "Alpha"), 2: _rec(2, "Beta"), 3: _rec(3, "Alpha")}, sort_key="id")
    orphans = finalize_jsonl(
        path, {1: _rec(1, "Alpha")}, key="id", sort_key="id", is_incremental=False,
        scope=lambda r: record_project(r) == "Alpha",
    )
    assert orphans == {3}
    assert set(read_jsonl(path, key="id")) == {1, 2}


def test_finalize_scope_falls_back_to_area_for_old_records(tmp_path):
    path = tmp_path / "work-items.jsonl"
    write_jsonl(path, {7: _rec(7, area="Beta\\Web"), 8: _rec(8, area="Alpha\\Web")}, sort_key="id")
    finalize_jsonl(
        path, {}, key="id", sort_key="id", is_incremental=False,
        scope=lambda r: record_project(r) == "Alpha",
    )
    assert set(read_jsonl(path, key="id")) == {7}


def test_finalize_scope_treats_projectless_records_as_in_scope(tmp_path):
    # Records with no project and no area predate this change; the sync scope
    # lambdas treat "" as belonging to the project being synced (old behavior).
    path = tmp_path / "work-items.jsonl"
    write_jsonl(path, {5: _rec(5, area=""), 6: _rec(6, "Beta")}, sort_key="id")
    finalize_jsonl(path, {}, key="id", sort_key="id", is_incremental=False,
                   scope=lambda r: record_project(r) in ("", "Alpha"))
    assert set(read_jsonl(path, key="id")) == {6}


def test_finalize_full_sync_without_scope_drops_all_unfetched(tmp_path):
    path = tmp_path / "work-items.jsonl"
    write_jsonl(path, {1: _rec(1, "Alpha"), 2: _rec(2, "Beta")}, sort_key="id")
    finalize_jsonl(path, {1: _rec(1, "Alpha")}, key="id", sort_key="id", is_incremental=False)
    assert set(read_jsonl(path, key="id")) == {1}


def test_finalize_incremental_preserves_state_history(tmp_path):
    path = tmp_path / "work-items.jsonl"
    history = [{"from": "New", "to": "Active", "date": "2026-01-02", "by": "a"}]
    write_jsonl(path, {1: _rec(1, "Alpha", state_history=history)}, sort_key="id")
    finalize_jsonl(path, {1: _rec(1, "Alpha", title="renamed")}, key="id", sort_key="id", is_incremental=True)
    item = read_jsonl(path, key="id")[1]
    assert item["title"] == "renamed"
    assert item["state_history"] == history


def test_finalize_does_not_override_fresh_state_history(tmp_path):
    path = tmp_path / "work-items.jsonl"
    old = [{"from": "New", "to": "Active", "date": "2026-01-02", "by": "a"}]
    write_jsonl(path, {1: _rec(1, "Alpha", state_history=old)}, sort_key="id")
    finalize_jsonl(path, {1: _rec(1, "Alpha", state_history=[])}, key="id", sort_key="id", is_incremental=True)
    assert read_jsonl(path, key="id")[1]["state_history"] == []
```

Append to `tests/test_sync_odata.py`:

```python
def test_sync_via_odata_full_sync_keeps_other_project_items(tmp_path):
    from ado_search.jsonl import write_jsonl
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    write_jsonl(wi_jsonl, {
        900: {"id": 900, "title": "beta item", "project": "Beta", "area": "Beta"},
        901: {"id": 901, "title": "stale alpha", "project": "MyProject", "area": "MyProject"},
    }, sort_key="id")
    page = json.dumps({"value": [{"WorkItemId": 100, "Title": "new", "WorkItemType": "Bug",
                                  "State": "New", "Area": {"AreaPath": "MyProject"}}]})

    async def fake_run(cmd, **kwargs):
        return CommandResult(command=cmd, returncode=0, stdout=page, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso", project="MyProject", auth_method="az-cli",
            data_dir=data_dir, work_item_types=["Bug"], area_paths=[], states=[],
            last_sync="", dry_run=False,
        ))

    items = read_jsonl(wi_jsonl, key="id")
    assert set(items) == {100, 900}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_sync_common.py tests/test_sync_odata.py -q`
Expected: FAIL. `TypeError: finalize_jsonl() got an unexpected keyword argument 'scope'`, the state_history preservation test fails, and the OData test fails with `{100}`.

- [ ] **Step 3: Implement**

In `sync_common.py`, add `from typing import Any, Callable` (replacing `from typing import Any`) and `from ado_search.jsonl import read_jsonl, write_jsonl` (drop `merge_jsonl`). Then replace `finalize_jsonl` with:

```python
def _carry_forward_state_history(existing: dict[Any, dict], fetched: dict[Any, dict]) -> None:
    """Keep previously captured state history when a fetch path doesn't provide one."""
    for k, rec in fetched.items():
        if "state_history" in rec:
            continue
        old = existing.get(k)
        if old and old.get("state_history"):
            rec["state_history"] = old["state_history"]


def finalize_jsonl(
    jsonl_path: Path,
    fetched_records: dict[Any, dict],
    *,
    key: str,
    sort_key: str,
    is_incremental: bool,
    remote_keys: set | None = None,
    scope: Callable[[dict], bool] | None = None,
) -> set:
    """Write JSONL with orphan detection. Returns set of orphaned keys.

    Incremental syncs merge fetched_records into the existing JSONL. Full syncs
    drop existing records missing from remote_keys (if given) or fetched_records;
    when ``scope`` is given, only existing records for which it returns True are
    orphan candidates (e.g. the project being synced).
    """
    existing = read_jsonl(jsonl_path, key=key)
    _carry_forward_state_history(existing, fetched_records)

    if is_incremental:
        existing.update(fetched_records)
        write_jsonl(jsonl_path, existing, sort_key=sort_key)
        return set()

    candidates = {k for k, v in existing.items() if scope(v)} if scope else set(existing)
    compare_keys = remote_keys if remote_keys is not None else set(fetched_records.keys())
    orphans = candidates - compare_keys
    if orphans:
        click.echo(f"  Removing {len(orphans)} orphaned items")

    all_items = {k: v for k, v in existing.items() if k not in orphans}
    all_items.update(fetched_records)
    write_jsonl(jsonl_path, all_items, sort_key=sort_key)
    return orphans
```

Before relying on the unified branch, check that it matches the old behavior:
- Without `scope` or `remote_keys`, kept = existing ∩ fetched, overwritten by fetched, so it equals `fetched_records`. That's the old behavior.
- With `remote_keys` (wiki), kept = existing ∩ remote_keys plus fetched. Also the old behavior.

The sync scope rule is `record_project(r) in ("", project)`. A record with no project and no area predates this change and can't be attributed, so it stays an orphan candidate for every project. That's the old single-project behavior, and it's what `tests/test_sync_workitems.py::test_deletion_detection_via_jsonl` relies on: its orphan has `"area": ""`.

In `sync_odata.py`, add `from ado_search.projects import record_project`. Change the `finalize_jsonl` call to:

```python
    finalize_jsonl(
        wi_jsonl, fetched_records,
        key="id", sort_key="id", is_incremental=bool(last_sync),
        scope=lambda r: record_project(r) in ("", project),
    )
```

In `sync_workitems.py`, add `from ado_search.projects import record_project`. Add the parameter `scope_project: str | None = None,` to `_fetch_and_finalize` after `is_incremental`, and change its `finalize_jsonl` call to:

```python
    finalize_jsonl(
        data_dir / "work-items.jsonl", fetched_records,
        key="id", sort_key="id", is_incremental=is_incremental,
        scope=(lambda r: record_project(r) in ("", scope_project)) if scope_project else None,
    )
```

In `sync_work_items`, add `scope_project=project,` to the final `_fetch_and_finalize(...)` call (currently at `sync_workitems.py:457`). Leave `fetch_specific_work_items` (`sync_workitems.py:396`) unchanged: it's incremental, so it never orphans.

Finally, `merge_jsonl` in `jsonl.py` no longer has callers in `src/`. Keep it, since `tests/test_jsonl.py` covers it as public API.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass, including `test_deletion_detection_via_jsonl` and the wiki orphan tests (`remote_keys` path, no scope).

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/sync_common.py src/ado_search/sync_odata.py src/ado_search/sync_workitems.py tests/test_sync_common.py tests/test_sync_odata.py
git commit -m "fix: scope orphan deletion to the synced project; keep state history on OData merges"
```

---

### Task 4: OData paging without nextLink

**Files:**
- Modify: `src/ado_search/sync_odata.py:131-236` (`sync_via_odata`), plus a new `next_page_url`
- Test: `tests/test_sync_odata.py`

**Interfaces:**
- Produces: `next_page_url(page: dict, *, top: int, skip: int, build_url: Callable[[int], str]) -> tuple[str | None, int]`
- `sync_via_odata` now raises `RuntimeError` if a later page fails. Callers (Task 6) catch it per project.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_odata.py`:

```python
import re

import ado_search.sync_odata as sync_odata_mod
from ado_search.sync_odata import next_page_url


def test_next_page_url_prefers_next_link():
    url, skip = next_page_url({"value": [1, 2], "@odata.nextLink": "https://x/next"},
                              top=2, skip=0, build_url=lambda s: f"skip={s}")
    assert (url, skip) == ("https://x/next", 0)


def test_next_page_url_full_page_advances_skip():
    assert next_page_url({"value": [1, 2]}, top=2, skip=4,
                         build_url=lambda s: f"skip={s}") == ("skip=6", 6)


def test_next_page_url_short_or_empty_page_stops():
    assert next_page_url({"value": [1]}, top=2, skip=0, build_url=str) == (None, 0)
    assert next_page_url({"value": []}, top=2, skip=0, build_url=str) == (None, 0)


def _odata_item(i):
    return {"WorkItemId": i, "Title": f"item {i}", "WorkItemType": "Bug", "State": "New",
            "Area": {"AreaPath": "MyProject"}}


def test_sync_via_odata_pages_with_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_odata_mod, "ODATA_PAGE_SIZE", 2)
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    pages = {0: [1, 2], 2: [3, 4], 4: [5]}
    skips = []

    async def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        skip = int(re.search(r"\$skip=(\d+)", cmd_str).group(1))
        skips.append(skip)
        body = json.dumps({"value": [_odata_item(i) for i in pages[skip]]})
        return CommandResult(command=cmd, returncode=0, stdout=body, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso", project="MyProject", auth_method="az-cli",
            data_dir=data_dir, work_item_types=["Bug"], area_paths=[], states=[],
            last_sync="", dry_run=False,
        ))

    assert stats["fetched"] == 5
    assert skips == [0, 2, 4]
    assert set(read_jsonl(data_dir / "work-items.jsonl", key="id")) == {1, 2, 3, 4, 5}


def test_sync_via_odata_raises_on_pagination_failure(tmp_path, monkeypatch):
    from ado_search.jsonl import write_jsonl
    monkeypatch.setattr(sync_odata_mod, "ODATA_PAGE_SIZE", 2)
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    write_jsonl(wi_jsonl, {9: {"id": 9, "title": "keep me", "project": "MyProject"}}, sort_key="id")

    async def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "$skip=0" in cmd_str:
            body = json.dumps({"value": [_odata_item(1), _odata_item(2)]})
            return CommandResult(command=cmd, returncode=0, stdout=body, stderr="")
        return CommandResult(command=cmd, returncode=1, stdout="", stderr="500 Internal Server Error")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="pagination"):
            asyncio.run(sync_via_odata(
                org="https://dev.azure.com/contoso", project="MyProject", auth_method="az-cli",
                data_dir=data_dir, work_item_types=["Bug"], area_paths=[], states=[],
                last_sync="", dry_run=False,
            ))

    # Nothing finalized: the existing item was not deleted
    assert set(read_jsonl(wi_jsonl, key="id")) == {9}
```

Add `import pytest` at the top of `tests/test_sync_odata.py` if it isn't already there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_sync_odata.py -q`
Expected: FAIL. `ImportError: cannot import name 'next_page_url'`.

- [ ] **Step 3: Implement**

In `sync_odata.py`, add `from typing import Callable`. Add below `build_odata_url`:

```python
def next_page_url(
    page: dict, *, top: int, skip: int, build_url: Callable[[int], str],
) -> tuple[str | None, int]:
    """Return (url, skip) for the next page, or (None, skip) when done.

    Analytics only returns @odata.nextLink for server-driven paging. With a
    client-driven $top it returns none, so advance $skip while pages are full.
    """
    link = page.get("@odata.nextLink")
    if link:
        return link, skip
    if len(page.get("value", [])) >= top:
        return build_url(skip + top), skip + top
    return None, skip
```

Replace the body of `sync_via_odata` from `def _url` through the end with:

```python
    top = ODATA_PAGE_SIZE

    def _url(extra_select: list[str] | None, skip: int = 0) -> str:
        return build_odata_url(
            org, project,
            work_item_types=work_item_types,
            area_paths=area_paths,
            states=states,
            last_sync=last_sync,
            top=top,
            skip=skip,
            extra_select=extra_select,
        )

    async def _get(url: str, retries: int = 3):
        return await run_operation(
            auth_method, OP_ODATA_QUERY, org=org, project=project, pat=pat, url=url, retries=retries,
        )

    def _is_unavailable(stderr: str) -> bool:
        lowered = stderr.lower()
        return any(s in lowered for s in ["403", "401", "forbidden", "unauthorized", "not available"])

    # Probe first page to check if OData is available
    extra: list[str] | None = ODATA_CUSTOM_SELECT
    result = await _get(_url(extra), retries=1)

    if result.returncode != 0 and not _is_unavailable(result.stderr):
        click.echo("  Custom fields unavailable in analytics, retrying without them...")
        extra = None
        result = await _get(_url(extra), retries=1)

    if result.returncode != 0:
        if _is_unavailable(result.stderr):
            return None  # OData not available — signal fallback
        raise RuntimeError(f"OData query failed: {result.stderr}")

    if not result.stdout.strip():
        return {"fetched": 0, "errors": 0}

    data = result.parse_json()

    def _next(page: dict, skip: int) -> tuple[str | None, int]:
        return next_page_url(page, top=top, skip=skip, build_url=lambda s: _url(extra, s))

    next_url, skip = _next(data, 0)

    if dry_run:
        all_ids = [item.get("WorkItemId", 0) for item in data.get("value", [])]
        while next_url:
            result = await _get(next_url)
            if result.returncode != 0:
                break
            page_data = result.parse_json()
            all_ids.extend(item.get("WorkItemId", 0) for item in page_data.get("value", []))
            next_url, skip = _next(page_data, skip)
        click.echo(f"Would process {len(all_ids)} work items: {all_ids[:20]}...")
        return {"fetched": 0, "errors": 0, "dry_run": True, "would_fetch": len(all_ids)}

    # Process items as each page arrives (reduces peak memory)
    fetched = 0
    errors = 0
    fetched_records: dict[int, dict] = {}

    def _process_page(items: list[dict]) -> None:
        nonlocal fetched, errors
        for item in items:
            try:
                ado_format = odata_to_ado_format(item, project=project)
                record = prepare_work_item(ado_format, comments=None)
                fetched_records[record["id"]] = record
                fetched += 1
            except Exception as e:
                click.echo(f"  Warning: Failed to process item: {e}", err=True)
                errors += 1

    _process_page(data.get("value", []))

    while next_url:
        result = await _get(next_url)
        if result.returncode != 0:
            # Finalizing partial data would drop unfetched items (full sync)
            # or skip them past the watermark (incremental). Abort instead.
            raise RuntimeError(f"OData pagination failed after {fetched} items: {result.stderr}")
        page_data = result.parse_json()
        _process_page(page_data.get("value", []))
        next_url, skip = _next(page_data, skip)
        click.echo(f"  Processed {fetched} items via OData...")

    click.echo(f"  OData: {fetched} work items processed")

    wi_jsonl = data_dir / "work-items.jsonl"
    finalize_jsonl(
        wi_jsonl, fetched_records,
        key="id", sort_key="id", is_incremental=bool(last_sync),
        scope=lambda r: record_project(r) in ("", project),
    )

    return {"fetched": fetched, "errors": errors}
```

(The previous `_probe` helper is replaced by `_get`. The first request keeps `retries=1`, as before.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/sync_odata.py tests/test_sync_odata.py
git commit -m "fix: page OData results by \$skip when Analytics returns no nextLink"
```

---

### Task 5: Index `project`; `--project` on search and grep

**Files:**
- Modify: `src/ado_search/db.py:50-172` (`initialize`, `upsert_work_item`), `db.py:246-346` (`search_work_items`, `get_filtered_ids`)
- Modify: `src/ado_search/search.py` (`search`, `format_results`)
- Modify: `src/ado_search/cli.py:197-320` (`search_cmd`, `grep_cmd`)
- Test: `tests/test_db.py`, `tests/test_search.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `project_from_area` (Task 1)
- Produces:
  - `Database.search_work_items(..., project_filter: str | None = None)`
  - `Database.get_filtered_ids(..., project_filter: str | None = None)`
  - `search(..., project_filter: str | None = None)`; result dicts gain `"project"`
  - CLI `search --project`, `grep --project`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
import sqlite3


def _item(i, title, project=None, area=""):
    item = {
        "id": i, "title": title, "type": "Bug", "state": "Active", "area": area,
        "iteration": "", "assigned_to": "", "tags": "", "priority": 2,
        "parent_id": None, "created": "2026-01-01", "updated": "2026-01-01",
        "description_snippet": title,
    }
    if project is not None:
        item["project"] = project
    return item


def test_project_column_and_backfill(db):
    db.upsert_work_item(_item(1, "login bug", project="Beta", area="Alpha\\Web"))
    db.upsert_work_item(_item(2, "login crash", area="Alpha\\Web"))
    assert db.get_work_item(1)["project"] == "Beta"
    assert db.get_work_item(2)["project"] == "Alpha"


def test_search_work_items_project_filter(db):
    db.upsert_work_item(_item(1, "login bug", project="Alpha"))
    db.upsert_work_item(_item(2, "login crash", project="Beta"))
    rows = db.search_work_items("login", project_filter="Beta")
    assert [r["id"] for r in rows] == [2]
    assert rows[0]["project"] == "Beta"


def test_get_filtered_ids_project_filter(db):
    db.upsert_work_item(_item(1, "a", project="Alpha"))
    db.upsert_work_item(_item(2, "b", project="Beta"))
    assert db.get_filtered_ids(project_filter="Alpha") == {1}


def test_initialize_migrates_old_db_without_project(tmp_path):
    from ado_search.db import Database
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE work_items (id INTEGER PRIMARY KEY, title TEXT NOT NULL,
        type TEXT NOT NULL, state TEXT NOT NULL, area TEXT, iteration TEXT, assigned_to TEXT,
        tags TEXT, priority INTEGER, parent_id INTEGER, created TEXT, updated TEXT)""")
    conn.commit()
    conn.close()
    old = Database(path)
    old.initialize()
    cols = {r[1] for r in old._connect().execute("PRAGMA table_info(work_items)")}
    idx = {r[1] for r in old._connect().execute("PRAGMA index_list(work_items)")}
    old.close()
    assert "project" in cols
    assert "idx_work_items_project" in idx
```

Append to `tests/test_search.py`:

```python
def test_format_results_shows_project_when_mixed(tmp_path):
    from ado_search.search import format_results
    results = [
        {"id": 1, "title": "a", "type": "Bug", "state": "New", "project": "Alpha",
         "file_path": "work-items.jsonl#id=1", "source": "work_item"},
        {"id": 2, "title": "b", "type": "Bug", "state": "New", "project": "Beta",
         "file_path": "work-items.jsonl#id=2", "source": "work_item"},
    ]
    out = format_results(results, fmt="compact", data_dir=tmp_path)
    assert "[Alpha] a" in out and "[Beta] b" in out


def test_format_results_hides_project_when_single(tmp_path):
    from ado_search.search import format_results
    results = [{"id": 1, "title": "a", "type": "Bug", "state": "New", "project": "Alpha",
                "file_path": "work-items.jsonl#id=1", "source": "work_item"}]
    assert "[Alpha]" not in format_results(results, fmt="compact", data_dir=tmp_path)
```

Append to `tests/test_cli.py`:

```python
def _seed_two_projects(data_dir):
    from ado_search.jsonl import write_jsonl
    base = {"type": "Bug", "state": "Active", "iteration": "", "assigned_to": "", "tags": "",
            "priority": 2, "parent_id": None, "created": "2026-01-01", "updated": "2026-01-01",
            "description": "", "acceptance_criteria": "", "comments": []}
    write_jsonl(data_dir / "work-items.jsonl", {
        1: {**base, "id": 1, "title": "payment timeout", "project": "Alpha", "area": "Alpha"},
        2: {**base, "id": 2, "title": "payment retry", "project": "Beta", "area": "Beta"},
    }, sort_key="id")


def test_search_project_filter(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    _seed_two_projects(data_dir)
    result = CliRunner().invoke(main, ["search", "payment", "--project", "Beta",
                                       "--format", "json", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    ids = [r["id"] for r in json.loads(result.output)]
    assert ids == [2]


def test_grep_project_filter(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    _seed_two_projects(data_dir)
    result = CliRunner().invoke(main, ["grep", "payment", "--field", "title", "--project", "Alpha",
                                       "--format", "json", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert [r["id"] for r in json.loads(result.output)] == [1]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_db.py tests/test_search.py tests/test_cli.py -q`
Expected: FAIL on `project` key, the `project_filter` kwarg, and `--project` "No such option".

Before implementing, check what `grep --format json` actually prints: open `src/ado_search/grep.py:165-225` (`format_grep_results`). If the JSON shape isn't a list of objects with `"id"`, adapt the assertion in `test_grep_project_filter` to that shape (for example, if each result is `{"id": ..., "matches": [...]}`, the assertion above is already right).

- [ ] **Step 3: Implement**

`db.py`:
- Add `from ado_search.projects import project_from_area`.
- In the `CREATE TABLE IF NOT EXISTS work_items` DDL, add `project TEXT DEFAULT ''` after `notes TEXT DEFAULT ''`.
- In the migration list, add `("project", "TEXT", "''"),`.
- After the migration `for` loop and the `wiki_pages` ALTER, before `conn.commit()`, add:

```python
        conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_project ON work_items(project)")
```

Replace the SQL and params in `upsert_work_item` with:

```python
        conn.execute(
            """INSERT INTO work_items
               (id, title, type, state, area, iteration, assigned_to, tags,
                priority, parent_id, closed_date, created, updated,
                description, acceptance_criteria, story_points,
                dev_notes, notes, project)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, type=excluded.type, state=excluded.state,
                area=excluded.area, iteration=excluded.iteration,
                assigned_to=excluded.assigned_to, tags=excluded.tags,
                priority=excluded.priority, parent_id=excluded.parent_id,
                closed_date=excluded.closed_date,
                created=excluded.created, updated=excluded.updated,
                description=excluded.description,
                acceptance_criteria=excluded.acceptance_criteria,
                story_points=excluded.story_points,
                dev_notes=excluded.dev_notes, notes=excluded.notes,
                project=excluded.project
            """,
            (
                item["id"], item["title"], item["type"], item["state"],
                item["area"], item["iteration"], item["assigned_to"],
                item["tags"], item["priority"], item["parent_id"],
                item.get("closed_date", ""),
                item["created"], item["updated"],
                item.get("description", ""), item.get("acceptance_criteria", ""),
                item.get("story_points"),
                item.get("dev_notes", ""), item.get("notes", ""),
                item.get("project") or project_from_area(item.get("area") or ""),
            ),
        )
```

In `search_work_items`:
- add the parameter `project_filter: str | None = None,` after `tag_filter`
- add `w.project` to the SELECT list (after `w.updated`)
- before `sql += " ORDER BY rank LIMIT ?"`, add:

```python
        if project_filter:
            sql += " AND w.project = ?"
            params.append(project_filter)
```

In `get_filtered_ids`:
- add `project_filter: str | None = None,`
- change the guard to `if not any([type_filter, state_filter, area_filter, assigned_to_filter, tag_filter, project_filter]):`
- before executing, add:

```python
        if project_filter:
            sql += " AND project = ?"
            params.append(project_filter)
```

`search.py`:
- Add `project_filter: str | None = None,` to `search`, and pass `project_filter=project_filter` to `db.search_work_items`.
- Add `"project": r.get("project", ""),` to each work-item result dict.
- Change the wiki guard to `if not any([type_filter, state_filter, assigned_to_filter, project_filter]):`.

In `format_results`, compute `mixed` before the loop and prefix titles:

```python
    projects = {r.get("project") for r in results if r["source"] == "work_item" and r.get("project")}
    mixed = len(projects) > 1

    lines: list[str] = []
    for r in results:
        if r["source"] == "work_item":
            id_str = f"#{r['id']}"
        else:
            id_str = r["id"]
        title = f"[{r['project']}] {r['title']}" if mixed and r.get("project") else r["title"]
```

Then use `title` in place of `r['title']` in both the `detail` and `compact` f-strings.

`cli.py`:
- **search_cmd**: add `@click.option("--project", "project_filter", default=None, help="Filter by project")` below the `--tag` option. Add `project_filter` to the function signature after `tag_filter`, and pass `project_filter=project_filter` to `search(...)`.
- **grep_cmd**: add the same option below its `--tag` option and add `project_filter` to the signature after `tag_filter`. Change `has_filters = any([type_filter, state_filter, area_filter, assigned_to, tag_filter, project_filter])` and add `project_filter=project_filter,` to the `db.get_filtered_ids(...)` call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/db.py src/ado_search/search.py src/ado_search/cli.py tests/test_db.py tests/test_search.py tests/test_cli.py
git commit -m "feat: index work item project; add --project to search and grep"
```

---

### Task 6: Multi-project `sync`

**Files:**
- Modify: `src/ado_search/cli.py:21-97` (`_Conn`, `_load_conn`, `_conn_db`), `cli.py:144-194` (`sync`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from `ado_search.projects` (Task 1); `sync_work_items` raising `RuntimeError` per project (Task 4)
- Produces:
  - `_load_conn(data_dir, *, require_project: bool = True)`
  - `_conn_db(data_dir, *, require_project: bool = True)`
  - `ado-search sync --project NAME` (repeatable)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
from ado_search.config import default_config, load_config, save_config


def _write_config(data_dir, *, project="", projects=None, by_project=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = default_config()
    cfg["organization"]["url"] = "https://dev.azure.com/contoso"
    cfg["organization"]["project"] = project
    if projects is not None:
        cfg["organization"]["projects"] = projects
    if by_project is not None:
        cfg["sync"]["last_sync_by_project"] = by_project
    save_config(cfg, data_dir / "config.toml")


def _run_sync(data_dir, *extra, wi_side_effect=None):
    wi = AsyncMock(return_value={"fetched": 1, "errors": 0}, side_effect=wi_side_effect)
    wiki = AsyncMock(return_value={"fetched": 0, "errors": 0})
    with patch("ado_search.sync_workitems.sync_work_items", wi), \
         patch("ado_search.sync_wiki.sync_wiki", wiki):
        result = CliRunner().invoke(main, ["sync", "--data-dir", str(data_dir), *extra])
    return result, wi, wiki


def test_sync_multiple_projects_uses_per_project_watermark(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"],
                  by_project={"Alpha": "2026-09-01"})
    result, wi, wiki = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    calls = {c.kwargs["project"]: c.kwargs["last_sync"] for c in wi.call_args_list}
    assert calls == {"Alpha": "2026-09-01", "Beta": ""}
    assert wiki.call_args.kwargs["project"] == "Alpha"
    saved = load_config(data_dir / "config.toml")
    assert set(saved["sync"]["last_sync_by_project"]) == {"Alpha", "Beta"}


def test_sync_project_option_restricts(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])
    result, wi, wiki = _run_sync(data_dir, "--project", "Beta")
    assert result.exit_code == 0, result.output
    assert [c.kwargs["project"] for c in wi.call_args_list] == ["Beta"]
    wiki.assert_not_called()


def test_sync_unknown_project_errors(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha")
    result, wi, _ = _run_sync(data_dir, "--project", "Nope")
    assert result.exit_code == 2
    assert "Nope" in result.output
    wi.assert_not_called()


def test_sync_star_expands_remote_projects(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, projects=["*"])
    remote = AsyncMock(return_value=["Alpha", "Beta"])
    with patch("ado_search.projects.fetch_remote_projects", remote):
        result, wi, wiki = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    assert [c.kwargs["project"] for c in wi.call_args_list] == ["Alpha", "Beta"]
    wiki.assert_not_called()  # no default project with ["*"]


def test_sync_failure_in_one_project_continues(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])

    async def flaky(**kwargs):
        if kwargs["project"] == "Alpha":
            raise RuntimeError("WIQL query failed: 401")
        return {"fetched": 1, "errors": 0}

    result, wi, _ = _run_sync(data_dir, wi_side_effect=flaky)
    assert result.exit_code == 1
    assert "Alpha" in result.output
    saved = load_config(data_dir / "config.toml")
    assert set(saved["sync"]["last_sync_by_project"]) == {"Beta"}


def test_write_command_requires_default_project(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, projects=["*"])
    result = CliRunner().invoke(main, ["list-comments", "1", "--data-dir", str(data_dir)])
    assert result.exit_code == 1
    assert "organization.project" in result.output
```

(`patch`, `AsyncMock`, `CliRunner`, `json`, and `main` are already imported at the top of `tests/test_cli.py`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: FAIL. Only one project is synced, `--project` is "No such option", and `list-comments` hits `KeyError`/network rather than the clean error.

- [ ] **Step 3: Implement**

In `cli.py`, replace `_load_conn` and `_conn_db` with:

```python
def _load_conn(data_dir: str | None, *, require_project: bool = True) -> _Conn:
    """Load config, resolve PAT, and return connection info.

    Exits with error if not initialized, or if a default project is required
    but the config only lists projects = ["*"].
    """
    from ado_search.projects import default_project

    data_path = Path(data_dir) if data_dir else _default_data_dir()
    config_path = data_path / "config.toml"

    if not config_path.exists():
        click.echo("Error: Not initialized. Run 'ado-search init' first.", err=True)
        raise SystemExit(1)

    cfg = load_config(config_path)
    org = cfg["organization"]["url"]
    try:
        project = default_project(cfg)
    except ValueError as e:
        if require_project:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)
        project = ""
    auth_method = cfg["auth"]["method"]

    pat = ""
    if auth_method == "pat":
        from ado_search.auth import get_pat
        pat = get_pat(cfg)

    return _Conn(cfg, org, project, auth_method, pat, data_path)
```

```python
@contextmanager
def _conn_db(data_dir: str | None, *, require_project: bool = True):
    """Load connection, open DB, ensure index is current."""
    conn = _load_conn(data_dir, require_project=require_project)
    with _open_db(conn.data_path) as db:
        _ensure_index(conn.data_path, db)
        yield conn, db
```

Replace the `sync` command with:

```python
@main.command()
@click.option("--data-dir", type=click.Path(exists=True), default=None)
@click.option("--dry-run", is_flag=True, help="Show what would be synced without writing")
@click.option("--include-attachments", is_flag=True, default=False,
              help="Download attachments (overrides config when set)")
@click.option("--full", is_flag=True, help="Ignore last_sync and re-fetch all items")
@click.option("--project", "only_projects", multiple=True,
              help="Sync only this project (repeatable; must be configured)")
def sync(data_dir: str | None, dry_run: bool, include_attachments: bool, full: bool,
         only_projects: tuple[str, ...]):
    """Sync work items and wiki pages from Azure DevOps."""
    from ado_search.projects import (
        ALL_PROJECTS, configured_projects, expand_projects,
        fetch_remote_projects, record_watermark, watermark_for,
    )

    with _conn_db(data_dir, require_project=False) as (conn, db):
        sync_cfg = conn.cfg["sync"]
        effective_attachments = include_attachments or sync_cfg.get("include_attachments", False)

        projects = configured_projects(conn.cfg)
        if ALL_PROJECTS in projects:
            remote = asyncio.run(fetch_remote_projects(
                auth_method=conn.auth_method, org=conn.org, pat=conn.pat,
            ))
            projects = expand_projects(projects, remote)
        if only_projects:
            unknown = sorted(set(only_projects) - set(projects))
            if unknown:
                click.echo(f"Error: not a configured project: {', '.join(unknown)}", err=True)
                raise SystemExit(2)
            projects = [p for p in projects if p in only_projects]
        if not projects:
            click.echo("Error: no projects configured.", err=True)
            raise SystemExit(1)

        from ado_search.sync_workitems import sync_work_items
        from ado_search.sync_wiki import sync_wiki

        suffix = " (with attachments)" if effective_attachments else ""
        failed: list[str] = []
        for project in projects:
            last_sync = "" if full else watermark_for(conn.cfg, project)
            click.echo(f"Syncing work items for {project}...{suffix}")
            try:
                wi_stats = asyncio.run(sync_work_items(
                    org=conn.org, project=project,
                    auth_method=conn.auth_method, pat=conn.pat,
                    data_dir=conn.data_path,
                    work_item_types=sync_cfg.get("work_item_types", []),
                    area_paths=sync_cfg.get("area_paths", []),
                    states=sync_cfg.get("states", []),
                    last_sync=last_sync,
                    max_concurrent=sync_cfg.get("performance", {}).get("max_concurrent", 5),
                    include_comments=sync_cfg.get("include_comments", False),
                    include_attachments=effective_attachments,
                    dry_run=dry_run,
                ))
            except RuntimeError as e:
                click.echo(f"  Error syncing {project}: {e}", err=True)
                failed.append(project)
                continue
            click.echo(f"  Work items: {wi_stats['fetched']} synced, {wi_stats['errors']} errors")
            if not dry_run:
                record_watermark(conn.cfg, project, datetime.now(timezone.utc).strftime("%Y-%m-%d"))

        if conn.project and conn.project in projects:
            click.echo("Syncing wiki pages...")
            wiki_stats = asyncio.run(sync_wiki(
                org=conn.org, project=conn.project,
                auth_method=conn.auth_method, pat=conn.pat,
                data_dir=conn.data_path,
                wiki_names=sync_cfg.get("wiki_names", []),
                max_concurrent=sync_cfg.get("performance", {}).get("max_concurrent", 5),
                dry_run=dry_run,
            ))
            click.echo(f"  Wiki pages: {wiki_stats['fetched']} synced, {wiki_stats['errors']} errors")
        elif not conn.project:
            click.echo("Skipping wiki pages (no default project; set organization.project)")

        if not dry_run:
            wi_jsonl = conn.data_path / "work-items.jsonl"
            wiki_jsonl = conn.data_path / "wiki-pages.jsonl"
            db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
            save_config(conn.cfg, conn.data_path / "config.toml")

        if failed:
            click.echo(f"Sync finished with errors in: {', '.join(failed)}", err=True)
            raise SystemExit(1)
        if not dry_run:
            click.echo("Sync complete.")
```

Note: `CliRunner` mixes stderr into `result.output` by default in the installed click version. If `test_sync_failure_in_one_project_continues` can't see `"Alpha"` in the output, check `result.stderr` as well: `assert "Alpha" in result.output + (result.stderr or "")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass, including the existing `tests/test_e2e.py::test_full_workflow` (legacy single-project config).

- [ ] **Step 5: Commit**

```bash
git add src/ado_search/cli.py tests/test_cli.py
git commit -m "feat: sync multiple projects with per-project watermarks and --project"
```

---

### Task 7: Docs, version, CI, release

**Files:**
- Modify: `pyproject.toml` (version), `CHANGELOG.md`, `README.md` (Configuration section, `## Configuration` at line ~186)
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Version, changelog, README**

In `pyproject.toml`, set `version = "1.14.0"`.

Prepend to `CHANGELOG.md` below `# Changelog`:

```markdown
## [1.14.0] - 2026-09-28

### Added

- **Multiple projects per data dir** -- `organization.projects = ["A", "B"]` (or `["*"]` for every project the credentials can see) syncs each project into the same `work-items.jsonl` / `index.db`. The legacy `organization.project` still works and remains the default project for write commands and wiki sync. `sync --project NAME` (repeatable) limits a run to specific projects. Each project has its own watermark in `[sync.last_sync_by_project]`; the legacy `last_sync` is migrated on first use.
- **`project` field** on every work item record (from `System.TeamProject`, falling back to the area-path root), indexed in `index.db`.
- **`--project` filter** on `search` and `grep`. Compact search output prefixes titles with `[project]` when results span projects; JSON output always includes `project`.

### Fixed

- **OData sync truncated at 5,000 items** -- Analytics returns no `@odata.nextLink` for client-driven `$top`, so only the first page was fetched. Pages are now requested by `$skip` while full. A failed page now aborts the sync instead of finalizing partial data.
- **Full sync could delete other projects' items** -- orphan detection is now scoped to the project being synced.
- **OData sync erased `state_history`** captured earlier by the WIQL path; existing history is now kept when the incoming record has none.
- **`save_config` wrote invalid TOML** for keys with spaces and strings containing backslashes (e.g. area paths); keys are quoted and strings escaped.
```

In `README.md`, insert after the Configuration section's TOML block (before `## Auth Methods`):

````markdown
### Multiple Projects

One data dir can hold several projects from the same organization:

```toml
[organization]
url = "https://dev.azure.com/yourorg"
project = "Alpha"              # default project for create/update/comments/links and wiki
projects = ["Alpha", "Beta"]   # or ["*"] for every project you can see
```

```bash
ado-search sync                    # syncs every configured project
ado-search sync --project Beta     # just one
ado-search search "timeout" --project Beta
ado-search grep "retry" --project Alpha
```

Each project keeps its own incremental watermark under `[sync.last_sync_by_project]`. Wiki pages are synced for the default project only.
````

- [ ] **Step 2: CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest]
        python-version: ["3.10", "3.11", "3.12", "3.13"]
        include:
          - os: windows-latest
            python-version: "3.13"
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: pip install -e ".[dev]"
      - name: Test
        run: python -m pytest -q
```

- [ ] **Step 3: Full suite, then commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

```bash
git add pyproject.toml CHANGELOG.md README.md .github/workflows/ci.yml
git commit -m "release 1.14.0: multi-project sync"
```

- [ ] **Step 4: Live verification against a real org (read-only on the ADO side)**

Create a scratch data dir outside any repo. Copy a working `config.toml` into it, set `projects = ["*"]`, and keep `organization.project` as the default. Then:

```bash
.venv/Scripts/ado-search sync --data-dir <scratch>
.venv/Scripts/ado-search search "<common word>" --data-dir <scratch> --format json
.venv/Scripts/ado-search search "<common word>" --project <one project> --data-dir <scratch>
```

Expected:
- Every visible project syncs, or is reported as an error and the rest continue.
- JSON results carry `project`.
- The filtered search returns only that project.
- `config.toml` gains `[sync.last_sync_by_project]` with quoted keys, and reloads cleanly.

- [ ] **Step 5: Release**

Merge `feat/multi-project` into `master` (fast-forward) and push. The new CI workflow runs on the push. Once it's green, tag and push to trigger `publish.yml` (PyPI):

```bash
git checkout master && git merge --ff-only feat/multi-project && git push origin master
gh run watch -R HurleySk/ado-search $(gh run list -R HurleySk/ado-search -w CI -L 1 --json databaseId -q '.[0].databaseId')
git tag v1.14.0 && git push origin v1.14.0
gh run watch -R HurleySk/ado-search $(gh run list -R HurleySk/ado-search -w "Publish to PyPI" -L 1 --json databaseId -q '.[0].databaseId')
```

---

### Task 8: ado-search-mcp `project` param (repo `../ado-search-mcp`)

**Files:**
- Modify: `../ado-search-mcp/src/registerTools.ts:6-29` (`addOptionalFilters`, `FILTER_SCHEMAS`)
- Modify: `../ado-search-mcp/package.json`, `../ado-search-mcp/server.json` (version 0.2.0), `../ado-search-mcp/README.md`
- Create: `../ado-search-mcp/.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `ado-search search|grep --project` (Task 5, ado-search ≥1.14.0)
- Produces: optional `project` input on the `ado_search` and `ado_grep` tools

- [ ] **Step 1: Implement**

In `registerTools.ts`, add `project?: string;` to the `addOptionalFilters` params type, and add as the last line of its body:

```ts
  if (params.project) args.push("--project", params.project);
```

Add to `FILTER_SCHEMAS`:

```ts
  project: z.string().optional().describe("Filter by Azure DevOps project name (requires ado-search >= 1.14)"),
```

(`ado_search` and `ado_grep` spread `FILTER_SCHEMAS` and call `addOptionalFilters`. `ado_children` picks its filters explicitly, so it's unchanged.)

Bump `"version"` to `"0.2.0"` in `package.json`, and in both version fields of `server.json`. Add a README note under the tools list: "`ado_search` and `ado_grep` accept an optional `project` filter for data dirs that sync several projects (ado-search ≥ 1.14)."

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-node@v5
        with:
          node-version: 22
          cache: npm
      - run: npm ci
      - run: npm run build
```

- [ ] **Step 2: Build**

Run: `npm run build` (in `../ado-search-mcp`)
Expected: `tsc` exits 0.

- [ ] **Step 3: Smoke test through the CLI the server wraps**

With ado-search 1.14 installed in the environment the MCP uses, run `ado-search search "<word>" --project <name> --format json --data-dir <scratch>`. That's the exact argv the tool builds. Confirm it returns only that project.

- [ ] **Step 4: Commit and push**

```bash
git add src/registerTools.ts package.json server.json README.md .github/workflows/ci.yml
git commit -m "feat: project filter on ado_search and ado_grep"
git push origin master
```

Watch the CI run: `gh run watch -R HurleySk/ado-search-mcp $(gh run list -R HurleySk/ado-search-mcp -L 1 --json databaseId -q '.[0].databaseId')`.
