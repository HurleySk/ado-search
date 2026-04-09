import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from ado_search.db import Database
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


def test_sync_wiki_writes_files_and_indexes(tmp_path):
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

    with patch("ado_search.sync_wiki.run_command", side_effect=fake_run):
        stats = asyncio.run(sync_wiki(
            org="https://dev.azure.com/contoso",
            project="MyProject",
            auth_method="az-cli",
            data_dir=data_dir,
            db=db,
            wiki_names=[],
            max_concurrent=2,
            dry_run=False,
        ))

    assert stats["fetched"] == 1
    assert (data_dir / "wiki" / "Getting-Started.md").exists()

    results = db.search_wiki("Getting Started")
    assert len(results) >= 1

    db.close()
