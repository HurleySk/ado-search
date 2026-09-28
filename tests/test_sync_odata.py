import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ado_search.db import Database
from ado_search.jsonl import read_jsonl
from ado_search.runner import CommandResult
from ado_search.sync_odata import build_odata_url, odata_to_ado_format, sync_via_odata
from ado_search.markdown import extract_work_item_metadata

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_odata_to_ado_format_basic():
    odata_item = {
        "WorkItemId": 12345,
        "Title": "Test bug",
        "WorkItemType": "Bug",
        "State": "Active",
        "Priority": 1,
        "TagNames": "sso,auth,p1",
        "CreatedDate": "2026-03-15T10:00:00Z",
        "ChangedDate": "2026-04-01T14:30:00Z",
        "Description": "<p>Description here</p>",
        "Microsoft_VSTS_Common_AcceptanceCriteria": "<p>Criteria</p>",
        "ParentWorkItemId": 100,
        "Area": {"AreaPath": "Proj\\Auth"},
        "Iteration": {"IterationPath": "Proj\\Sprint 1"},
        "AssignedTo": {"UniqueName": "user@co.com"},
    }
    ado = odata_to_ado_format(odata_item)
    assert ado["id"] == 12345
    assert ado["fields"]["System.Title"] == "Test bug"
    assert ado["fields"]["System.Tags"] == "sso; auth; p1"
    assert ado["fields"]["System.AssignedTo"]["uniqueName"] == "user@co.com"
    assert ado["fields"]["System.Parent"] == 100

    # Verify it works with extract_work_item_metadata
    meta = extract_work_item_metadata(ado)
    assert meta["id"] == 12345
    assert meta["tags"] == "sso,auth,p1"
    assert meta["assigned_to"] == "user@co.com"


def test_odata_to_ado_format_null_fields():
    odata_item = {
        "WorkItemId": 999,
        "Title": "No assignee",
        "WorkItemType": "Task",
        "State": "New",
        "Priority": 3,
        "TagNames": None,
        "CreatedDate": "2026-01-01T00:00:00Z",
        "ChangedDate": "2026-01-01T00:00:00Z",
        "Description": None,
        "Microsoft_VSTS_Common_AcceptanceCriteria": None,
        "ParentWorkItemId": None,
        "Area": None,
        "Iteration": None,
        "AssignedTo": None,
    }
    ado = odata_to_ado_format(odata_item)
    assert ado["id"] == 999
    assert ado["fields"]["System.AssignedTo"] == ""
    assert ado["fields"]["System.Tags"] == ""
    assert ado["fields"]["System.AreaPath"] == ""
    assert ado["fields"]["System.Description"] == ""

    # Should not crash extract_work_item_metadata
    meta = extract_work_item_metadata(ado)
    assert meta["assigned_to"] == ""


def test_odata_to_ado_format_produces_valid_record():
    with open(FIXTURE_DIR / "odata_workitems_page1.json") as f:
        data = json.load(f)
    from ado_search.sync_common import prepare_work_item
    for odata_item in data["value"]:
        ado = odata_to_ado_format(odata_item)
        record = prepare_work_item(ado, comments=None)
        assert record["id"] == odata_item["WorkItemId"]
        assert "title" in record


def test_build_odata_url_full_sync():
    url = build_odata_url(
        "https://dev.azure.com/contoso", "MyProject",
        work_item_types=["Bug", "User Story"],
        area_paths=[], states=[], last_sync="",
    )
    assert "analytics.dev.azure.com/contoso/MyProject" in url
    assert "$select=" in url
    assert "$expand=" in url
    assert "WorkItemType" in url
    assert "ChangedDate%20gt" not in url and "ChangedDate gt" not in url


def test_build_odata_url_incremental():
    url = build_odata_url(
        "https://dev.azure.com/contoso", "MyProject",
        work_item_types=["Bug"],
        area_paths=[], states=[], last_sync="2026-04-01",
    )
    assert "ChangedDate" in url
    assert "2026-04-01" in url


def test_build_odata_url_with_filters():
    url = build_odata_url(
        "https://dev.azure.com/contoso", "MyProject",
        work_item_types=["Bug"],
        area_paths=["MyProject\\Auth"],
        states=["Active", "New"],
        last_sync="",
    )
    assert "AreaPath" in url
    assert "State" in url


def test_sync_via_odata_success(tmp_path):
    data_dir = tmp_path / ".ado-search"
    (data_dir / "work-items").mkdir(parents=True)

    db = Database(data_dir / "index.db")
    db.initialize()

    odata_response = json.dumps({
        "value": [
            {
                "WorkItemId": 100,
                "Title": "Test item",
                "WorkItemType": "Bug",
                "State": "Active",
                "Priority": 1,
                "TagNames": "test",
                "CreatedDate": "2026-01-01T00:00:00Z",
                "ChangedDate": "2026-01-15T00:00:00Z",
                "Description": "Test description",
                "Microsoft_VSTS_Common_AcceptanceCriteria": "",
                "ParentWorkItemId": None,
                "Area": {"AreaPath": "Proj"},
                "Iteration": {"IterationPath": "Proj\\Sprint 1"},
                "AssignedTo": {"UniqueName": "a@co.com"},
            }
        ]
    })

    async def fake_run(cmd, **kwargs):
        return CommandResult(command=cmd, returncode=0, stdout=odata_response, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            work_item_types=["Bug"],
            area_paths=[], states=[], last_sync="",
            dry_run=False,
        ))

    assert stats is not None
    assert stats["fetched"] == 1
    assert stats["errors"] == 0

    # Check JSONL
    wi_jsonl = data_dir / "work-items.jsonl"
    assert wi_jsonl.exists()
    items = read_jsonl(wi_jsonl, key="id")
    assert 100 in items
    assert items[100]["title"] == "Test item"

    # Reindex and verify search works
    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
    results = db.search_work_items("Test")
    assert len(results) >= 1
    db.close()


def test_sync_via_odata_returns_none_on_403(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir(parents=True)

    db = Database(data_dir / "index.db")
    db.initialize()

    async def fake_run(cmd, **kwargs):
        return CommandResult(
            command=cmd, returncode=1, stdout="",
            stderr="Forbidden(VS403527: Access to data from the Analytics OData endpoint is not available)"
        )

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        result = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            work_item_types=["Bug"],
            area_paths=[], states=[], last_sync="",
        ))

    assert result is None  # Signals fallback
    db.close()


def test_sync_via_odata_pagination(tmp_path):
    data_dir = tmp_path / ".ado-search"
    (data_dir / "work-items").mkdir(parents=True)

    db = Database(data_dir / "index.db")
    db.initialize()

    page1 = json.dumps({
        "value": [{
            "WorkItemId": 1, "Title": "Item 1", "WorkItemType": "Bug",
            "State": "Active", "Priority": 1, "TagNames": "",
            "CreatedDate": "2026-01-01T00:00:00Z", "ChangedDate": "2026-01-01T00:00:00Z",
            "Description": "", "Microsoft_VSTS_Common_AcceptanceCriteria": "",
            "ParentWorkItemId": None, "Area": None, "Iteration": None, "AssignedTo": None,
        }],
        "@odata.nextLink": "https://analytics.dev.azure.com/contoso/MyProject/_odata/v4.0-preview/WorkItems?$skip=5000"
    })
    page2 = json.dumps({
        "value": [{
            "WorkItemId": 2, "Title": "Item 2", "WorkItemType": "Task",
            "State": "New", "Priority": 2, "TagNames": "",
            "CreatedDate": "2026-01-01T00:00:00Z", "ChangedDate": "2026-01-01T00:00:00Z",
            "Description": "", "Microsoft_VSTS_Common_AcceptanceCriteria": "",
            "ParentWorkItemId": None, "Area": None, "Iteration": None, "AssignedTo": None,
        }]
    })

    call_count = {"n": 0}
    async def fake_run(cmd, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        stdout = page1 if idx == 0 else page2
        return CommandResult(command=cmd, returncode=0, stdout=stdout, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            work_item_types=["Bug", "Task"],
            area_paths=[], states=[], last_sync="",
        ))

    assert stats["fetched"] == 2

    # Check JSONL has both items
    wi_jsonl = data_dir / "work-items.jsonl"
    items = read_jsonl(wi_jsonl, key="id")
    assert 1 in items
    assert 2 in items

    db.close()


def test_odata_to_ado_format_includes_story_points():
    odata_item = {
        "WorkItemId": 100, "Title": "Test", "WorkItemType": "User Story",
        "State": "Active", "Priority": 2, "TagNames": "",
        "CreatedDate": "2026-01-01", "ChangedDate": "2026-01-02",
        "Description": "", "Microsoft_VSTS_Common_AcceptanceCriteria": "",
        "ParentWorkItemId": None, "StoryPoints": 8.0,
        "Area": {"AreaPath": "Proj\\Team"},
        "Iteration": {"IterationPath": "Proj\\Sprint 1"},
        "AssignedTo": {"UniqueName": "u@e.com"},
    }
    result = odata_to_ado_format(odata_item)
    assert result["fields"]["Microsoft.VSTS.Scheduling.StoryPoints"] == 8.0


def test_odata_to_ado_format_null_story_points():
    odata_item = {
        "WorkItemId": 101, "Title": "Bug", "WorkItemType": "Bug",
        "State": "New", "Priority": 1, "TagNames": "",
        "CreatedDate": "2026-01-01", "ChangedDate": "2026-01-02",
        "Description": "", "Microsoft_VSTS_Common_AcceptanceCriteria": "",
        "ParentWorkItemId": None,
        "Area": {"AreaPath": "Proj"},
        "Iteration": {"IterationPath": "Proj\\Sprint 1"},
        "AssignedTo": None,
    }
    result = odata_to_ado_format(odata_item)
    assert result["fields"].get("Microsoft.VSTS.Scheduling.StoryPoints") is None


def test_sync_via_odata_dry_run(tmp_path):
    data_dir = tmp_path / ".ado-search"
    (data_dir / "work-items").mkdir(parents=True)

    db = Database(data_dir / "index.db")
    db.initialize()

    odata_response = json.dumps({
        "value": [{
            "WorkItemId": 1, "Title": "Item", "WorkItemType": "Bug",
            "State": "Active", "Priority": 1, "TagNames": "",
            "CreatedDate": "2026-01-01T00:00:00Z", "ChangedDate": "2026-01-01T00:00:00Z",
            "Description": "", "Microsoft_VSTS_Common_AcceptanceCriteria": "",
            "ParentWorkItemId": None, "Area": None, "Iteration": None, "AssignedTo": None,
        }]
    })

    async def fake_run(cmd, **kwargs):
        return CommandResult(command=cmd, returncode=0, stdout=odata_response, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            work_item_types=["Bug"],
            area_paths=[], states=[], last_sync="",
            dry_run=True,
        ))

    assert stats["fetched"] == 0
    assert stats["dry_run"] is True
    # No JSONL should be written in dry run
    assert not (data_dir / "work-items.jsonl").exists()
    db.close()


def test_odata_to_ado_format_sets_project():
    from ado_search.sync_common import prepare_work_item
    item = {"WorkItemId": 5, "Title": "x", "WorkItemType": "Bug", "State": "New",
            "Area": {"AreaPath": r"Other\Area"}}
    ado = odata_to_ado_format(item, project="Beta")
    assert ado["fields"]["System.TeamProject"] == "Beta"
    assert prepare_work_item(ado)["project"] == "Beta"


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


def test_build_odata_url_orders_by_work_item_id():
    # $skip paging is only stable over a deterministic order
    url = build_odata_url(
        "https://dev.azure.com/contoso", "MyProject",
        work_item_types=[], area_paths=[], states=[], last_sync="", skip=5000,
    )
    assert "$orderby=WorkItemId" in url


def test_odata_to_ado_format_takes_project_case_from_area():
    item = {"WorkItemId": 5, "Title": "x", "WorkItemType": "Bug", "State": "New",
            "Area": {"AreaPath": "MyProject\Team"}}
    ado = odata_to_ado_format(item, project="myproject")
    assert ado["fields"]["System.TeamProject"] == "MyProject"


def test_sync_via_odata_full_sync_scope_ignores_project_case(tmp_path):
    from ado_search.jsonl import write_jsonl
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    write_jsonl(wi_jsonl, {
        900: {"id": 900, "title": "beta item", "project": "Beta", "area": "Beta"},
        901: {"id": 901, "title": "stale", "project": "MyProject", "area": "MyProject"},
    }, sort_key="id")
    page = json.dumps({"value": [{"WorkItemId": 100, "Title": "new", "WorkItemType": "Bug",
                                  "State": "New", "Area": {"AreaPath": "MyProject"}}]})

    async def fake_run(cmd, **kwargs):
        return CommandResult(command=cmd, returncode=0, stdout=page, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso", project="myproject", auth_method="az-cli",
            data_dir=data_dir, work_item_types=["Bug"], area_paths=[], states=[],
            last_sync="", dry_run=False,
        ))

    items = read_jsonl(wi_jsonl, key="id")
    assert set(items) == {100, 900}
    assert items[100]["project"] == "MyProject"
