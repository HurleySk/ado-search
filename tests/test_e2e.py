# tests/test_e2e.py
"""End-to-end test: init → sync (mocked) → search → show."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from ado_search.cli import main
from ado_search.runner import CommandResult


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_full_workflow(tmp_path):
    data_dir = tmp_path / ".ado-search"
    runner = CliRunner()

    # 1. Init
    result = runner.invoke(main, [
        "init",
        "--org", "https://dev.azure.com/contoso",
        "--project", "MyProject",
        "--auth-method", "az-cli",
        "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 0

    # 2. Sync (mocked) - use smart mock that detects command type
    wiql_result = json.dumps({"workItems": [{"id": 12345}]})
    item_json = (FIXTURE_DIR / "work_item_12345.json").read_text()
    comments_json = json.dumps({"comments": []})
    wiki_list = json.dumps([{"id": 1, "name": "TestWiki"}])
    wiki_pages = (FIXTURE_DIR / "wiki_page_list.json").read_text()
    wiki_content = (FIXTURE_DIR / "wiki_page_content.json").read_text()

    wiki_call_count = {"n": 0}
    wiki_responses = [wiki_list, wiki_pages, wiki_content]

    async def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        # OData probe — return failure so it falls back to WIQL
        if "analytics.dev.azure.com" in cmd_str:
            return CommandResult(command=cmd, returncode=1, stdout="", stderr="403 Forbidden")
        # Work item commands
        if "--wiql" in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=wiql_result, stderr="")
        if "12345" in cmd_str and "comments" not in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=item_json, stderr="")
        if "comments" in cmd_str:
            return CommandResult(command=cmd, returncode=0, stdout=comments_json, stderr="")
        # Wiki commands — sequential responses
        if "wiki" in cmd_str:
            idx = wiki_call_count["n"]
            wiki_call_count["n"] += 1
            if idx < len(wiki_responses):
                return CommandResult(command=cmd, returncode=0, stdout=wiki_responses[idx], stderr="")
            return CommandResult(command=cmd, returncode=0, stdout="{}", stderr="")
        return CommandResult(command=cmd, returncode=0, stdout="{}", stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        result = runner.invoke(main, ["sync", "--data-dir", str(data_dir)])
        assert result.exit_code == 0, f"Sync failed: {result.output}"

    # 3. Search
    result = runner.invoke(main, ["search", "SSO", "--data-dir", str(data_dir)])
    assert result.exit_code == 0
    assert "12345" in result.output

    # 4. Search with paths format
    result = runner.invoke(main, ["search", "SSO", "--format", "paths", "--data-dir", str(data_dir)])
    assert result.exit_code == 0
    assert "work-items.jsonl" in result.output

    # 5. Show
    result = runner.invoke(main, ["show", "12345", "--data-dir", str(data_dir)])
    assert result.exit_code == 0
    assert "Login fails with SSO redirect" in result.output
