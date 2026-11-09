import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from ado_search.cli import main
from ado_search.config import default_config, save_config
from ado_search.runner import CommandResult
from ado_search.testplans import Conn
from ado_search.wiki_pages import normalize_content, resolve_wiki, update_page

ORG = "https://dev.azure.com/contoso"


def _conn():
    return Conn(org=ORG, project="Sandbox", auth_method="pat", pat="x")


def _page(content, etag='"v1"'):
    return CommandResult(["m"], 0, json.dumps({"path": "/QA SOP", "content": content}), "", {"etag": etag})


def test_normalize_content_strips_bom_and_crlf():
    assert normalize_content("﻿# A\r\nb\rc") == "# A\nb\nc"


def test_update_page_dry_run_returns_diff_and_writes_nothing():
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock,
               side_effect=[_page("# Old\nline\n")]) as op:
        out = asyncio.run(update_page(_conn(), "Sandbox.wiki", "/QA SOP", "# New\r\nline\n", dry_run=True))
    assert op.call_count == 1
    assert out["changed"] and out["dry_run"] and not out["created"]
    assert "-# Old" in out["diff"] and "+# New" in out["diff"]


def test_update_page_sends_if_match_and_comment():
    put = CommandResult(["m"], 0, json.dumps({"path": "/QA SOP"}), "", {"etag": '"v2"'})
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock,
               side_effect=[_page("old\n"), put]) as op:
        out = asyncio.run(update_page(_conn(), "Sandbox.wiki", "/QA SOP", "new\n", comment="Finalize SOP"))
    kwargs = op.call_args_list[1].kwargs
    assert op.call_args_list[1].args[1] == "wiki-page-put"
    assert kwargs["extra_headers"] == {"If-Match": '"v1"'}
    assert kwargs["params"] == ["comment=Finalize%20SOP"]
    assert json.loads(kwargs["body"]) == {"content": "new\n"}
    assert out["etag"] == '"v2"' and out["changed"]


def test_update_page_no_change_skips_put():
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock,
               side_effect=[_page("same\n")]) as op:
        out = asyncio.run(update_page(_conn(), "w", "/QA SOP", "same\r\n"))
    assert op.call_count == 1 and not out["changed"]


def test_update_page_missing_needs_create():
    missing = CommandResult(["m"], 1, "", "HTTP 404 for wiki-page-show: not found")
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, side_effect=[missing]):
        with pytest.raises(click.ClickException, match="--create"):
            asyncio.run(update_page(_conn(), "w", "/New", "x"))
    put = CommandResult(["m"], 0, "{}", "", {"etag": '"v1"'})
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock,
               side_effect=[missing, put]) as op:
        out = asyncio.run(update_page(_conn(), "w", "/New", "x", create=True))
    assert out["created"] and op.call_args_list[1].kwargs["extra_headers"] is None


def test_resolve_wiki_needs_single_wiki():
    two = CommandResult(["m"], 0, json.dumps({"value": [{"name": "a"}, {"name": "b"}]}), "")
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, return_value=two):
        with pytest.raises(click.ClickException, match="2 wikis"):
            asyncio.run(resolve_wiki(_conn(), None))
    one = CommandResult(["m"], 0, json.dumps({"value": [{"name": "Sandbox.wiki"}]}), "")
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, return_value=one):
        assert asyncio.run(resolve_wiki(_conn(), None)) == "Sandbox.wiki"


def test_wiki_update_command_dry_run(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    cfg = default_config()
    cfg["organization"]["url"] = ORG
    cfg["organization"]["project"] = "Default"
    cfg["auth"] = {"method": "pat", "pat": "fake"}
    save_config(cfg, data_dir / "config.toml")
    page = tmp_path / "sop.md"
    page.write_text("# QA SOP\nnew\n", encoding="utf-8")

    response = MagicMock(status=200, headers={"ETag": '"v1"'})
    response.read.return_value = json.dumps({"content": "# QA SOP\nold\n"}).encode()
    response.__enter__.return_value = response
    with patch("urllib.request.urlopen", return_value=response) as mock_open:
        result = CliRunner().invoke(main, ["wiki-update", "/QA SOP", str(page), "--project", "Sandbox",
                                           "--wiki", "Sandbox.wiki", "--dry-run", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert mock_open.call_count == 1 and mock_open.call_args.args[0].get_method() == "GET"
    assert "+new" in result.output and "Dry run: would update Sandbox/Sandbox.wiki:/QA SOP" in result.output
