from ado_search.db import Database


def test_create_schema(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    results = db.search_work_items("anything")
    assert results == []
    db.close()


def test_upsert_work_item(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 12345,
        "title": "Login fails with SSO redirect",
        "type": "Bug",
        "state": "Active",
        "area": "MyProject\\Auth",
        "iteration": "Sprint 42",
        "assigned_to": "jdoe@contoso.com",
        "tags": "sso,authentication,p1",
        "priority": 1,
        "parent_id": 12300,
        "created": "2026-03-15",
        "updated": "2026-04-01",
        "description_snippet": "When a user attempts to log in via SSO",
    })
    results = db.search_work_items("SSO login")
    assert len(results) == 1
    assert results[0]["id"] == 12345
    assert results[0]["title"] == "Login fails with SSO redirect"
    db.close()


def test_upsert_work_item_updates_existing(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 100,
        "title": "Original title",
        "type": "Bug",
        "state": "New",
        "area": "",
        "iteration": "",
        "assigned_to": "",
        "tags": "",
        "priority": 2,
        "parent_id": None,
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "description_snippet": "Original description",
    })
    db.upsert_work_item({
        "id": 100,
        "title": "Updated title",
        "type": "Bug",
        "state": "Active",
        "area": "",
        "iteration": "",
        "assigned_to": "",
        "tags": "",
        "priority": 2,
        "parent_id": None,
        "created": "2026-01-01",
        "updated": "2026-02-01",
        "description_snippet": "Updated description",
    })
    results = db.search_work_items("Updated")
    assert len(results) == 1
    assert results[0]["title"] == "Updated title"
    db.close()


def test_upsert_wiki_page(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_wiki_page({
        "path": "/Getting-Started",
        "title": "Getting Started",
        "updated": "2026-04-01",
        "description_snippet": "Welcome to the project wiki",
    })
    results = db.search_wiki("Getting Started")
    assert len(results) == 1
    assert results[0]["path"] == "/Getting-Started"
    db.close()


def test_search_with_filters(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 1,
        "title": "Auth bug in login",
        "type": "Bug",
        "state": "Active",
        "area": "MyProject\\Auth",
        "iteration": "",
        "assigned_to": "alice@co.com",
        "tags": "auth,p1",
        "priority": 1,
        "parent_id": None,
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "description_snippet": "Login auth bug",
    })
    db.upsert_work_item({
        "id": 2,
        "title": "Auth feature request",
        "type": "User Story",
        "state": "New",
        "area": "MyProject\\Auth",
        "iteration": "",
        "assigned_to": "bob@co.com",
        "tags": "auth",
        "priority": 2,
        "parent_id": None,
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "description_snippet": "Auth feature",
    })
    results = db.search_work_items("auth", type_filter="Bug")
    assert len(results) == 1
    assert results[0]["id"] == 1
    results = db.search_work_items("auth", state_filter="New")
    assert len(results) == 1
    assert results[0]["id"] == 2
    db.close()


def test_delete_work_item(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 999,
        "title": "To be deleted",
        "type": "Task",
        "state": "Removed",
        "area": "",
        "iteration": "",
        "assigned_to": "",
        "tags": "",
        "priority": 3,
        "parent_id": None,
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "description_snippet": "Will be deleted",
    })
    db.delete_work_item(999)
    results = db.search_work_items("deleted")
    assert results == []
    db.close()


def test_get_all_work_item_ids(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    for wid in [10, 20, 30]:
        db.upsert_work_item({
            "id": wid,
            "title": f"Item {wid}",
            "type": "Task",
            "state": "Active",
            "area": "",
            "iteration": "",
            "assigned_to": "",
            "tags": "",
            "priority": 2,
            "parent_id": None,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "description_snippet": f"Item {wid}",
        })
    ids = db.get_all_work_item_ids()
    assert sorted(ids) == [10, 20, 30]
    db.close()
