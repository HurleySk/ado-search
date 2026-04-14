import json
from pathlib import Path

from ado_search.db import Database


def test_upsert_state_changes(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_state_changes(42, [
        {"from": "New", "to": "Active", "date": "2026-01-15", "by": "alice@co.com"},
        {"from": "Active", "to": "Resolved", "date": "2026-02-03", "by": "alice@co.com"},
    ])
    changes = db.get_state_changes(42)
    assert len(changes) == 2
    assert changes[0]["from_state"] == "New"
    assert changes[0]["to_state"] == "Active"
    assert changes[0]["changed_date"] == "2026-01-15"
    assert changes[1]["from_state"] == "Active"
    assert changes[1]["to_state"] == "Resolved"
    db.close()


def test_upsert_state_changes_replaces(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_state_changes(42, [
        {"from": "New", "to": "Active", "date": "2026-01-15", "by": "alice@co.com"},
    ])
    db.upsert_state_changes(42, [
        {"from": "New", "to": "Active", "date": "2026-01-15", "by": "alice@co.com"},
        {"from": "Active", "to": "Resolved", "date": "2026-02-03", "by": "alice@co.com"},
    ])
    changes = db.get_state_changes(42)
    assert len(changes) == 2
    db.close()


def test_get_state_changes_empty(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    changes = db.get_state_changes(999)
    assert changes == []
    db.close()


def test_get_all_state_changes(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_state_changes(1, [
        {"from": "New", "to": "Active", "date": "2026-01-10", "by": "a@co.com"},
    ])
    db.upsert_state_changes(2, [
        {"from": "New", "to": "Closed", "date": "2026-01-12", "by": "b@co.com"},
    ])
    all_changes = db.get_all_state_changes()
    assert len(all_changes) == 2
    item_ids = {c["item_id"] for c in all_changes}
    assert item_ids == {1, 2}
    db.close()


def test_delete_work_item_cleans_state_changes(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 1, "title": "Test", "type": "Bug", "state": "Active",
        "area": "", "iteration": "", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-01-02", "description_snippet": "test", "story_points": None,
    })
    db.upsert_state_changes(1, [
        {"from": "New", "to": "Active", "date": "2026-01-15", "by": "a@co.com"},
    ])
    db.delete_work_item(1)
    assert db.get_state_changes(1) == []
    db.close()


def test_reindex_loads_state_history(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()

    wi_path = tmp_path / "work-items.jsonl"
    wiki_path = tmp_path / "wiki-pages.jsonl"
    wiki_path.write_text("", encoding="utf-8")

    wi_data = {
        "id": 1, "title": "Test", "type": "Bug", "state": "Resolved",
        "area": "A", "iteration": "I", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-02-03", "description": "", "acceptance_criteria": "",
        "story_points": 3.0,
        "state_history": [
            {"from": "New", "to": "Active", "date": "2026-01-15", "by": "a@co.com"},
            {"from": "Active", "to": "Resolved", "date": "2026-02-03", "by": "a@co.com"},
        ],
    }
    wi_path.write_text(json.dumps(wi_data) + "\n", encoding="utf-8")

    db.reindex_from_jsonl(wi_path, wiki_path)

    changes = db.get_state_changes(1)
    assert len(changes) == 2
    assert changes[0]["from_state"] == "New"
    assert changes[1]["to_state"] == "Resolved"
    db.close()
