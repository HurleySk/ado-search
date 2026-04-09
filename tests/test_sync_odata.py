import json
from pathlib import Path

from ado_search.sync_odata import build_odata_url, odata_to_ado_format
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
