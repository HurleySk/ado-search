import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from ado_search.db import Database
from ado_search.runner import CommandResult
from ado_search.sync_odata import build_odata_url, odata_to_ado_format, sync_via_odata
from ado_search.markdown import extract_work_item_metadata, work_item_to_markdown

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


def test_odata_to_ado_format_produces_valid_markdown():
    with open(FIXTURE_DIR / "odata_workitems_page1.json") as f:
        data = json.load(f)
    for odata_item in data["value"]:
        ado = odata_to_ado_format(odata_item)
        md = work_item_to_markdown(ado, comments=None)
        assert f"id: {odata_item['WorkItemId']}" in md
        assert "## Description" in md or not odata_item.get("Description")


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

    with patch("ado_search.sync_odata.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            db=db,
            work_item_types=["Bug"],
            area_paths=[], states=[], last_sync="",
            dry_run=False,
        ))

    assert stats is not None
    assert stats["fetched"] == 1
    assert stats["errors"] == 0
    assert 100 in stats["fetched_ids"]
    assert (data_dir / "work-items" / "100.md").exists()

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

    with patch("ado_search.sync_odata.run_command", side_effect=fake_run):
        result = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            db=db,
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

    with patch("ado_search.sync_odata.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir, db=db,
            work_item_types=["Bug", "Task"],
            area_paths=[], states=[], last_sync="",
        ))

    assert stats["fetched"] == 2
    assert {1, 2} == stats["fetched_ids"]
    db.close()


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

    with patch("ado_search.sync_odata.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_via_odata(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir, db=db,
            work_item_types=["Bug"],
            area_paths=[], states=[], last_sync="",
            dry_run=True,
        ))

    assert stats["fetched"] == 0
    assert stats["dry_run"] is True
    assert not (data_dir / "work-items" / "1.md").exists()
    db.close()
