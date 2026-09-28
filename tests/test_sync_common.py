from pathlib import Path

from ado_search.jsonl import read_jsonl, write_jsonl
from ado_search.sync_common import finalize_jsonl, prepare_work_item, split_results


def test_split_results_separates_records_and_errors():
    results = [
        {"id": 1, "title": "A"},
        "Error fetching #2",
        {"id": 3, "title": "C"},
    ]
    records, errors = split_results(results, key="id")
    assert set(records.keys()) == {1, 3}
    assert errors == ["Error fetching #2"]


def test_split_results_empty():
    records, errors = split_results([], key="id")
    assert records == {}
    assert errors == []


def test_finalize_jsonl_incremental_merge(tmp_path):
    jsonl = tmp_path / "items.jsonl"
    write_jsonl(jsonl, {1: {"id": 1, "v": "old"}}, sort_key="id")

    finalize_jsonl(
        jsonl, {2: {"id": 2, "v": "new"}},
        key="id", sort_key="id", is_incremental=True,
    )

    items = read_jsonl(jsonl, key="id")
    assert 1 in items
    assert 2 in items


def test_finalize_jsonl_full_sync_removes_orphans(tmp_path):
    jsonl = tmp_path / "items.jsonl"
    write_jsonl(jsonl, {
        1: {"id": 1, "v": "keep"},
        2: {"id": 2, "v": "orphan"},
    }, sort_key="id")

    orphans = finalize_jsonl(
        jsonl, {1: {"id": 1, "v": "updated"}},
        key="id", sort_key="id", is_incremental=False,
    )

    assert orphans == {2}
    items = read_jsonl(jsonl, key="id")
    assert 1 in items
    assert 2 not in items


def test_finalize_jsonl_with_remote_keys(tmp_path):
    """Wiki pattern: remote_keys preserves existing non-orphaned items."""
    jsonl = tmp_path / "pages.jsonl"
    write_jsonl(jsonl, {
        "/a": {"path": "/a", "content": "existing-a"},
        "/b": {"path": "/b", "content": "existing-b"},
        "/orphan": {"path": "/orphan", "content": "gone"},
    }, sort_key="path")

    orphans = finalize_jsonl(
        jsonl, {"/a": {"path": "/a", "content": "fetched-a"}},
        key="path", sort_key="path", is_incremental=False,
        remote_keys={"/a", "/b"},
    )

    assert orphans == {"/orphan"}
    items = read_jsonl(jsonl, key="path")
    assert "/orphan" not in items
    assert items["/a"]["content"] == "fetched-a"  # fetched overrides existing
    assert items["/b"]["content"] == "existing-b"  # kept from existing


def test_finalize_jsonl_no_existing_file(tmp_path):
    jsonl = tmp_path / "items.jsonl"
    finalize_jsonl(
        jsonl, {1: {"id": 1, "v": "new"}},
        key="id", sort_key="id", is_incremental=False,
    )
    items = read_jsonl(jsonl, key="id")
    assert items == {1: {"id": 1, "v": "new"}}


def test_prepare_work_item_captures_custom_notes():
    raw = {
        "id": 75542,
        "fields": {
            "System.Title": "T",
            "Custom.DevelopmentNotes": "<p>Use alm_docketnamereplacement</p>",
            "Custom.Notes": "<p>Client note</p>",
        },
    }
    record = prepare_work_item(raw)
    assert record["dev_notes"] == "Use alm_docketnamereplacement"
    assert record["notes"] == "Client note"


def test_prepare_work_item_custom_notes_absent():
    record = prepare_work_item({"id": 1, "fields": {"System.Title": "T"}})
    assert record["dev_notes"] == ""
    assert record["notes"] == ""


def test_prepare_work_item_takes_team_project():
    raw = {"id": 1, "fields": {
        "System.Title": "t", "System.WorkItemType": "Bug", "System.State": "New",
        "System.AreaPath": r"Alpha\Web", "System.TeamProject": "Beta",
    }}
    assert prepare_work_item(raw)["project"] == "Beta"


def test_prepare_work_item_falls_back_to_area_root():
    raw = {"id": 2, "fields": {
        "System.Title": "t", "System.WorkItemType": "Bug", "System.State": "New",
        "System.AreaPath": r"Alpha\Web",
    }}
    assert prepare_work_item(raw)["project"] == "Alpha"
