from pathlib import Path

from ado_search.db import Database
from ado_search.search import search, format_results


def _seed_db(db: Database) -> None:
    db.initialize()
    db.upsert_work_item({
        "id": 100, "title": "Login SSO bug", "type": "Bug", "state": "Active",
        "area": "Proj\\Auth", "iteration": "Sprint 1", "assigned_to": "a@co.com",
        "tags": "sso,auth", "priority": 1, "parent_id": None,
        "created": "2026-01-01", "updated": "2026-01-15",
        "description_snippet": "SSO redirect fails with 403",
    })
    db.upsert_work_item({
        "id": 200, "title": "MFA feature", "type": "User Story", "state": "New",
        "area": "Proj\\Auth", "iteration": "Sprint 2", "assigned_to": "b@co.com",
        "tags": "mfa", "priority": 2, "parent_id": None,
        "created": "2026-02-01", "updated": "2026-02-10",
        "description_snippet": "Add MFA to login flow",
    })
    db.upsert_wiki_page({
        "path": "/Auth-Guide", "title": "Auth Guide", "updated": "2026-03-01",
        "description_snippet": "Guide to authentication and SSO setup",
    })


def test_search_work_items(tmp_path):
    db = Database(tmp_path / "index.db")
    _seed_db(db)
    results = search(db, "SSO", data_dir=tmp_path)
    assert any(r["id"] == "100" or r["id"] == 100 for r in results)
    db.close()


def test_search_with_type_filter(tmp_path):
    db = Database(tmp_path / "index.db")
    _seed_db(db)
    results = search(db, "auth", data_dir=tmp_path, type_filter="Bug")
    ids = [r["id"] for r in results]
    assert 100 in ids or "100" in ids
    assert 200 not in ids and "200" not in ids
    db.close()


def test_format_compact(tmp_path):
    results = [
        {"id": 100, "title": "Login SSO bug", "type": "Bug", "state": "Active",
         "file_path": "work-items/100.md", "source": "work_item"},
    ]
    output = format_results(results, fmt="compact", data_dir=tmp_path)
    assert "#100" in output
    assert "Bug" in output
    assert "Active" in output


def test_format_paths(tmp_path):
    results = [
        {"id": 100, "title": "x", "type": "Bug", "state": "Active",
         "file_path": "work-items/100.md", "source": "work_item"},
        {"id": "/Auth", "title": "y", "type": "", "state": "",
         "file_path": "wiki/Auth.md", "source": "wiki"},
    ]
    output = format_results(results, fmt="paths", data_dir=tmp_path)
    lines = output.strip().split("\n")
    assert len(lines) == 2
    assert "work-items/100.md" in lines[0]


def test_format_json(tmp_path):
    import json
    results = [
        {"id": 100, "title": "Test", "type": "Bug", "state": "Active",
         "file_path": "work-items/100.md", "source": "work_item"},
    ]
    output = format_results(results, fmt="json", data_dir=tmp_path)
    parsed = json.loads(output)
    assert len(parsed) == 1
    assert parsed[0]["id"] == 100
