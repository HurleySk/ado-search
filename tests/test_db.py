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


def test_delete_wiki_page(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_wiki_page({
        "path": "/ToDelete",
        "title": "To Delete",
        "updated": "2026-01-01",
        "description_snippet": "Will be deleted",
    })
    db.delete_wiki_page("/ToDelete")
    results = db.search_wiki("Delete")
    assert results == []
    db.close()


def test_get_all_wiki_paths(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    for p in ["/Page-A", "/Page-B"]:
        db.upsert_wiki_page({
            "path": p, "title": p.lstrip("/"), "updated": "2026-01-01",
            "description_snippet": "test",
        })
    paths = db.get_all_wiki_paths()
    assert sorted(paths) == ["/Page-A", "/Page-B"]
    db.close()


def test_batch_commits_once(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    with db.batch():
        for i in range(10):
            db.upsert_work_item({
                "id": i, "title": f"Item {i}", "type": "Task", "state": "Active",
                "area": "", "iteration": "", "assigned_to": "", "tags": "",
                "priority": 2, "parent_id": None, "created": "2026-01-01",
                "updated": "2026-01-01", "description_snippet": f"Item {i}",
            })
    # All 10 should be searchable after batch completes
    ids = db.get_all_work_item_ids()
    assert len(ids) == 10
    db.close()


def test_search_special_characters(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 1, "title": "Test item", "type": "Bug", "state": "Active",
        "area": "", "iteration": "", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-01-01", "description_snippet": "test",
    })
    # These should not crash
    results = db.search_work_items('he said "hello')
    assert isinstance(results, list)
    results = db.search_work_items("foo AND bar")
    assert isinstance(results, list)
    results = db.search_work_items("test*")
    assert isinstance(results, list)
    db.close()


def test_upsert_work_item_stores_full_text(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 1, "title": "Test", "type": "Bug", "state": "Active",
        "area": "A", "iteration": "I", "assigned_to": "u@e.com",
        "tags": "t1", "priority": 1, "parent_id": None,
        "created": "2025-01-01", "updated": "2025-01-02",
        "description_snippet": "short", "description": "full description text",
        "acceptance_criteria": "full AC text",
    })
    conn = db._connect()
    row = conn.execute("SELECT description, acceptance_criteria FROM work_items WHERE id = 1").fetchone()
    assert row["description"] == "full description text"
    assert row["acceptance_criteria"] == "full AC text"
    db.close()


def test_upsert_wiki_page_stores_content(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_wiki_page({
        "path": "/Test", "title": "Test", "updated": "2025-01-01",
        "description_snippet": "short", "content": "# Full page content",
    })
    conn = db._connect()
    row = conn.execute("SELECT content FROM wiki_pages WHERE path = '/Test'").fetchone()
    assert row["content"] == "# Full page content"
    db.close()


def test_get_work_item_by_id(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 42, "title": "Test Item", "type": "Task", "state": "New",
        "area": "A", "iteration": "I", "assigned_to": "",
        "tags": "", "priority": 2, "parent_id": None,
        "created": "2025-01-01", "updated": "2025-01-02",
        "description_snippet": "s", "description": "full desc",
        "acceptance_criteria": "ac",
    })
    item = db.get_work_item(42)
    assert item is not None
    assert item["title"] == "Test Item"
    assert item["description"] == "full desc"
    assert db.get_work_item(999) is None
    db.close()


def test_get_wiki_page_by_path(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_wiki_page({
        "path": "/Arch/Overview", "title": "Overview", "updated": "2025-01-01",
        "description_snippet": "s", "content": "# Overview\nBody",
    })
    page = db.get_wiki_page("/Arch/Overview")
    assert page is not None
    assert page["content"] == "# Overview\nBody"
    assert db.get_wiki_page("/missing") is None
    db.close()


def test_upsert_work_item_with_story_points(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 1, "title": "Story with points", "type": "User Story",
        "state": "Active", "area": "", "iteration": "", "assigned_to": "",
        "tags": "", "priority": 2, "parent_id": None,
        "created": "2026-01-01", "updated": "2026-01-02",
        "description_snippet": "test", "story_points": 5.0,
    })
    item = db.get_work_item(1)
    assert item is not None
    assert item["story_points"] == 5.0
    db.close()


def test_upsert_work_item_null_story_points(tmp_path):
    db = Database(tmp_path / "index.db")
    db.initialize()
    db.upsert_work_item({
        "id": 2, "title": "No points", "type": "Bug",
        "state": "Active", "area": "", "iteration": "", "assigned_to": "",
        "tags": "", "priority": 1, "parent_id": None,
        "created": "2026-01-01", "updated": "2026-01-02",
        "description_snippet": "test", "story_points": None,
    })
    item = db.get_work_item(2)
    assert item is not None
    assert item["story_points"] is None
    db.close()


def test_reindex_from_jsonl(tmp_path):
    import json
    db = Database(tmp_path / "index.db")
    db.initialize()
    wi_path = tmp_path / "work-items.jsonl"
    wiki_path = tmp_path / "wiki-pages.jsonl"
    wi_path.write_text(
        json.dumps({"id": 1, "title": "Item One", "type": "Bug", "state": "Active",
                     "area": "A", "iteration": "I", "assigned_to": "", "tags": "t1",
                     "priority": 1, "parent_id": None, "created": "2025-01-01",
                     "updated": "2025-01-02", "description": "desc", "acceptance_criteria": "ac"}) + "\n",
        encoding="utf-8",
    )
    wiki_path.write_text(
        json.dumps({"path": "/P", "title": "Page", "updated": "2025-01-01", "content": "body"}) + "\n",
        encoding="utf-8",
    )
    db.reindex_from_jsonl(wi_path, wiki_path)
    results = db.search_work_items("Item One")
    assert len(results) == 1
    assert results[0]["id"] == 1
    wiki_results = db.search_wiki("Page")
    assert len(wiki_results) == 1
    db.close()


def test_reindex_includes_attachment_filenames_in_search(tmp_path):
    import json
    db = Database(tmp_path / "index.db")
    db.initialize()
    wi_path = tmp_path / "work-items.jsonl"
    wiki_path = tmp_path / "wiki-pages.jsonl"
    wi_path.write_text(
        json.dumps({
            "id": 1, "title": "Bug report", "type": "Bug", "state": "Active",
            "area": "A", "iteration": "I", "assigned_to": "", "tags": "",
            "priority": 1, "parent_id": None, "created": "2025-01-01",
            "updated": "2025-01-02", "description": "some bug",
            "acceptance_criteria": "",
            "attachments": [
                {"name": "screenshot.png", "size": 1000, "guid": "g1", "local_path": "attachments/1/screenshot.png"},
                {"name": "repro-steps.docx", "size": 2000, "guid": "g2", "local_path": "attachments/1/repro-steps.docx"},
            ],
        }) + "\n",
        encoding="utf-8",
    )
    wiki_path.write_text("", encoding="utf-8")
    db.reindex_from_jsonl(wi_path, wiki_path)
    # Searching by attachment filename should find the work item
    results = db.search_work_items("screenshot.png")
    assert len(results) == 1
    assert results[0]["id"] == 1
    results = db.search_work_items("repro-steps.docx")
    assert len(results) == 1
    db.close()
