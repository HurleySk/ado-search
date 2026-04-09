import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from ado_search.db import Database
from ado_search.runner import CommandResult
from ado_search.sync_workitems import sync_work_items, build_wiql_query


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
    # ChangedDate should only appear in ORDER BY, not in WHERE (no incremental filter)
    assert "ChangedDate >" not in q
    assert "ORDER BY [System.ChangedDate] DESC" in q


def test_build_wiql_query_incremental():
    q = build_wiql_query(
        work_item_types=["Bug"],
        area_paths=[],
        states=[],
        last_sync="2026-04-01T00:00:00Z",
    )
    assert "ChangedDate" in q
    assert "2026-04-01T00:00:00Z" in q


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


def test_sync_work_items_writes_files_and_indexes(tmp_path):
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
        if "query" in cmd_str and "--wiql" in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=wiql_result, stderr="")
        if "12345" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_12345, stderr="")
        if "12346" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_12346, stderr="")
        # Comments requests
        return CommandResult(command=cmd, returncode=0, stdout=comments_json, stderr="")

    with patch("ado_search.sync_workitems.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_work_items(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            db=db,
            work_item_types=["Bug", "User Story"],
            area_paths=[],
            states=[],
            last_sync="",
            max_concurrent=2,
            dry_run=False,
        ))

    assert stats["fetched"] == 2
    assert stats["errors"] == 0
    assert (data_dir / "work-items" / "12345.md").exists()
    assert (data_dir / "work-items" / "12346.md").exists()

    results = db.search_work_items("SSO login")
    assert len(results) >= 1
    results = db.search_work_items("MFA")
    assert len(results) >= 1

    db.close()
