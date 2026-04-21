import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from ado_search.db import Database
from ado_search.jsonl import read_jsonl, write_jsonl
from ado_search.runner import CommandResult
from ado_search.sync_common import extract_state_history
from ado_search.sync_workitems import sync_work_items, build_wiql_query, _find_id_range_start


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_build_wiql_query_full_sync():
    q = build_wiql_query(
        work_item_types=["Bug", "User Story"],
        area_paths=[],
        states=[],
        last_sync="",
    )
    assert "System.WorkItemType" in q
    assert "'Bug'" in q
    assert "'User Story'" in q
    # ChangedDate should only appear in WHERE for incremental, not in full sync
    assert "ChangedDate >" not in q
    assert "ORDER BY [System.Id] ASC" in q


def test_build_wiql_query_incremental():
    q = build_wiql_query(
        work_item_types=["Bug"],
        area_paths=[],
        states=[],
        last_sync="2026-04-01T00:00:00Z",
    )
    assert "ChangedDate" in q
    assert "2026-04-01T00:00:00Z" in q
    assert "ORDER BY [System.Id] ASC" in q


def test_build_wiql_query_with_min_id():
    q = build_wiql_query(
        work_item_types=["Bug"],
        area_paths=[],
        states=[],
        last_sync="",
        min_id=5000,
    )
    assert "[System.Id] > 5000" in q
    assert "ORDER BY [System.Id] ASC" in q


def test_build_wiql_query_with_filters():
    q = build_wiql_query(
        work_item_types=["Bug"],
        area_paths=["MyProject\\Auth"],
        states=["Active", "New"],
        last_sync="",
    )
    assert "AreaPath" in q
    assert "'MyProject\\Auth'" in q
    assert "State" in q
    assert "'Active'" in q


def test_sync_work_items_writes_jsonl_and_indexes(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    (data_dir / "work-items").mkdir()

    db = Database(data_dir / "index.db")
    db.initialize()

    wiql_result = json.dumps(
        json.loads((FIXTURE_DIR / "wiql_query_result.json").read_text())
    )
    item_12345 = (FIXTURE_DIR / "work_item_12345.json").read_text()
    item_12346 = (FIXTURE_DIR / "work_item_12346.json").read_text()
    comments_json = json.dumps({"comments": []})

    async def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        # OData probe — reject so it falls back to WIQL
        if "analytics.dev.azure.com" in cmd_str:
            return CommandResult(command=cmd, returncode=1, stdout="", stderr="403 Forbidden")
        if "query" in cmd_str and "--wiql" in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=wiql_result, stderr="")
        if "12345" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_12345, stderr="")
        if "12346" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_12346, stderr="")
        # Comments requests
        return CommandResult(command=cmd, returncode=0, stdout=comments_json, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_work_items(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            work_item_types=["Bug", "User Story"],
            area_paths=[],
            states=[],
            last_sync="",
            max_concurrent=2,
            dry_run=False,
        ))

    assert stats["fetched"] == 2
    assert stats["errors"] == 0

    # JSONL file should exist with both items
    wi_jsonl = data_dir / "work-items.jsonl"
    assert wi_jsonl.exists()
    items = read_jsonl(wi_jsonl, key="id")
    assert 12345 in items
    assert 12346 in items

    # Reindex and verify search works
    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
    results = db.search_work_items("SSO login")
    assert len(results) >= 1
    results = db.search_work_items("MFA")
    assert len(results) >= 1

    db.close()


def test_extract_state_history_from_updates():
    updates = [
        {
            "id": 1,
            "fields": {
                "System.State": {"oldValue": "New", "newValue": "Active"},
                "System.ChangedDate": {"newValue": "2026-01-15T10:00:00Z"},
                "System.ChangedBy": {"newValue": {"uniqueName": "alice@co.com"}},
            },
        },
        {
            "id": 2,
            "fields": {
                "System.Title": {"oldValue": "Old", "newValue": "New Title"},
            },
        },
        {
            "id": 3,
            "fields": {
                "System.State": {"oldValue": "Active", "newValue": "Resolved"},
                "System.ChangedDate": {"newValue": "2026-02-03T14:30:00Z"},
                "System.ChangedBy": {"newValue": {"uniqueName": "alice@co.com"}},
            },
        },
    ]
    history = extract_state_history(updates)
    assert len(history) == 2
    assert history[0] == {
        "from": "New", "to": "Active",
        "date": "2026-01-15", "by": "alice@co.com",
    }
    assert history[1] == {
        "from": "Active", "to": "Resolved",
        "date": "2026-02-03", "by": "alice@co.com",
    }


def test_extract_state_history_empty():
    assert extract_state_history([]) == []


def test_extract_state_history_no_state_changes():
    updates = [{"id": 1, "fields": {"System.Title": {"oldValue": "A", "newValue": "B"}}}]
    assert extract_state_history(updates) == []


def test_deletion_detection_via_jsonl(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    (data_dir / "work-items").mkdir()

    db = Database(data_dir / "index.db")
    db.initialize()

    # Pre-populate JSONL with an orphan item (id=999) that won't be in the remote set
    wi_jsonl = data_dir / "work-items.jsonl"
    orphan_record = {
        "id": 999, "title": "Deleted item", "type": "Bug", "state": "Removed",
        "area": "", "iteration": "", "assigned_to": "", "tags": "",
        "priority": 3, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-01-01", "description": "Gone", "acceptance_criteria": "",
        "comments": [],
    }
    write_jsonl(wi_jsonl, {999: orphan_record}, sort_key="id")

    # The remote set only has item 12345 — so 999 should be removed after full sync
    wiql_result = json.dumps([{"id": 12345}])
    item_12345 = (FIXTURE_DIR / "work_item_12345.json").read_text()
    comments_json = json.dumps({"comments": []})

    async def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "analytics.dev.azure.com" in cmd_str:
            return CommandResult(command=cmd, returncode=1, stdout="", stderr="403 Forbidden")
        if "query" in cmd_str and "--wiql" in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=wiql_result, stderr="")
        if "12345" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_12345, stderr="")
        return CommandResult(command=cmd, returncode=0, stdout=comments_json, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_work_items(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            work_item_types=["Bug", "User Story"],
            area_paths=[],
            states=[],
            last_sync="",
            max_concurrent=2,
            dry_run=False,
        ))

    assert stats["fetched"] == 1
    assert stats["errors"] == 0

    # Orphan 999 should be gone from JSONL
    items = read_jsonl(wi_jsonl, key="id")
    assert 999 not in items
    assert 12345 in items

    # Reindex and verify DB reflects orphan removal
    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
    assert db.get_work_item(999) is None
    assert db.get_work_item(12345) is not None

    db.close()


async def test_find_id_range_start_exponential_probe():
    """Verify exponential + binary search finds first items with fewer probes."""
    call_count = 0

    async def fake_run_wiql(auth_method, org, project, pat, **kwargs):
        nonlocal call_count
        call_count += 1
        max_id = kwargs.get("max_id", 0)
        min_id = kwargs.get("min_id", 0)
        # Items exist in range 75000-80000
        ids = [i for i in range(75001, 75011) if min_id < i <= max_id]
        return 0, ids

    with patch("ado_search.sync_workitems._run_wiql", side_effect=fake_run_wiql):
        result = await _find_id_range_start(
            "az-cli", "https://dev.azure.com/contoso", "Proj", "",
            work_item_types=["Bug"], area_paths=[], states=[], last_sync="",
        )

    assert result == 75001
    # Exponential probing (10K, 20K, 40K, 80K) = 4 probes, then binary search ~3-4
    assert call_count < 12, f"Expected fewer than 12 probes, got {call_count}"


async def test_find_id_range_start_items_in_first_chunk():
    """Items in the first 10K chunk should be found with minimal probes."""
    async def fake_run_wiql(auth_method, org, project, pat, **kwargs):
        max_id = kwargs.get("max_id", 0)
        min_id = kwargs.get("min_id", 0)
        ids = [i for i in [100, 200, 500] if min_id < i <= max_id]
        return 0, ids

    with patch("ado_search.sync_workitems._run_wiql", side_effect=fake_run_wiql):
        result = await _find_id_range_start(
            "az-cli", "https://dev.azure.com/contoso", "Proj", "",
            work_item_types=["Bug"], area_paths=[], states=[], last_sync="",
        )

    assert result == 100


async def test_find_id_range_start_no_items():
    """Returns None when no items exist in any range."""
    async def fake_run_wiql(auth_method, org, project, pat, **kwargs):
        return 0, []

    with patch("ado_search.sync_workitems._run_wiql", side_effect=fake_run_wiql):
        result = await _find_id_range_start(
            "az-cli", "https://dev.azure.com/contoso", "Proj", "",
            work_item_types=["Bug"], area_paths=[], states=[], last_sync="",
        )

    assert result is None
