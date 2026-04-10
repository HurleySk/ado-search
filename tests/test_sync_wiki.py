import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from ado_search.db import Database
from ado_search.jsonl import read_jsonl, write_jsonl
from ado_search.runner import CommandResult
from ado_search.sync_wiki import sync_wiki, _flatten_wiki_pages


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_flatten_wiki_pages():
    tree = {
        "id": 1,
        "path": "/",
        "subPages": [
            {"id": 2, "path": "/Getting-Started", "subPages": []},
            {"id": 3, "path": "/Architecture", "subPages": [
                {"id": 4, "path": "/Architecture/Overview", "subPages": []},
            ]},
        ],
    }
    flat = _flatten_wiki_pages(tree)
    paths = [p["path"] for p in flat]
    assert "/Getting-Started" in paths
    assert "/Architecture" in paths
    assert "/Architecture/Overview" in paths
    assert "/" not in paths


def test_wiki_deletion_detection_via_jsonl(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()

    db = Database(data_dir / "index.db")
    db.initialize()

    # Pre-populate JSONL with an orphan page
    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    orphan_record = {
        "path": "/Old-Page", "title": "Old Page", "updated": "2026-01-01",
        "content": "Orphaned page content",
    }
    current_record = {
        "path": "/Getting-Started", "title": "Getting Started", "updated": "2026-01-01",
        "content": "Current page",
    }
    write_jsonl(wiki_jsonl, {
        "/Old-Page": orphan_record,
        "/Getting-Started": current_record,
    }, sort_key="path")

    # Simulate a sync where only /Getting-Started exists remotely
    wiki_list = json.dumps([{"id": 1, "name": "MyWiki"}])
    page_list = json.dumps({
        "id": 1,
        "path": "/",
        "subPages": [
            {"id": 2, "path": "/Getting-Started", "subPages": []},
        ],
    })
    page_content = json.dumps({
        "content": "# Getting Started\nWelcome!",
        "dateModified": "2026-04-01T00:00:00Z",
    })

    call_count = {"n": 0}
    responses = [wiki_list, page_list, page_content]

    async def fake_run(cmd, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return CommandResult(
            command=cmd, returncode=0,
            stdout=responses[idx], stderr="",
        )

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_wiki(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            wiki_names=[],
            max_concurrent=2,
            dry_run=False,
        ))

    assert stats["fetched"] == 1

    # Orphan /Old-Page should be gone from JSONL
    pages = read_jsonl(wiki_jsonl, key="path")
    assert "/Old-Page" not in pages
    assert "/Getting-Started" in pages

    # Reindex and verify DB reflects orphan removal
    wi_jsonl = data_dir / "work-items.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
    assert db.get_wiki_page("/Old-Page") is None
    assert db.get_wiki_page("/Getting-Started") is not None

    db.close()


def test_sync_wiki_writes_jsonl_and_indexes(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()

    db = Database(data_dir / "index.db")
    db.initialize()

    wiki_list = json.dumps([{"id": 1, "name": "MyWiki"}])
    page_list = (FIXTURE_DIR / "wiki_page_list.json").read_text()
    page_content = (FIXTURE_DIR / "wiki_page_content.json").read_text()

    call_count = {"n": 0}
    responses = [wiki_list, page_list, page_content]

    async def fake_run(cmd, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return CommandResult(
            command=cmd, returncode=0,
            stdout=responses[idx], stderr="",
        )

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_wiki(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            wiki_names=[],
            max_concurrent=2,
            dry_run=False,
        ))

    assert stats["fetched"] == 1

    # Check JSONL file
    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    assert wiki_jsonl.exists()
    pages = read_jsonl(wiki_jsonl, key="path")
    assert len(pages) >= 1
    # The fixture has /Getting-Started
    page_paths = list(pages.keys())
    assert any("Getting-Started" in p for p in page_paths)

    # Reindex and verify search works
    wi_jsonl = data_dir / "work-items.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
    results = db.search_wiki("Getting Started")
    assert len(results) >= 1

    db.close()
