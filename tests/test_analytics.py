import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError

import click
import pytest
from click.testing import CliRunner

from ado_search.analytics import build_analytics_url, org_name, query_analytics
from ado_search.cli import main
from ado_search.config import default_config, save_config
from ado_search.runner import CommandResult
from ado_search.testplans import Conn

ORG = "https://dev.azure.com/FERC-ALM"
BASE = "https://analytics.dev.azure.com/FERC-ALM"


def _conn(project=""):
    return Conn(org=ORG, project=project, auth_method="pat", pat="x")


def _page(rows, next_link=None):
    body = {"@odata.context": "ctx", "value": rows}
    if next_link:
        body["@odata.nextLink"] = next_link
    return CommandResult(["m"], 0, json.dumps(body), "")


@pytest.mark.parametrize("url,name", [
    ("https://dev.azure.com/FERC-ALM", "FERC-ALM"),
    ("https://dev.azure.com/FERC-ALM/", "FERC-ALM"),
    ("https://contoso.visualstudio.com", "contoso"),
])
def test_org_name(url, name):
    assert org_name(url) == name


def test_org_name_without_org_is_an_error():
    with pytest.raises(click.ClickException):
        org_name("https://dev.azure.com/")


def test_build_url_without_project_is_org_level():
    assert build_analytics_url(ORG, "Projects") == f"{BASE}/_odata/v4.0-preview/Projects"


def test_build_url_encodes_project_and_query_but_keeps_odata_punctuation():
    query = r"$select=IterationPath,StartDate&$filter=startswith(IterationPath,'ALM\ALM O and M\OM Sprint')"
    url = build_analytics_url(ORG, "Iterations", project="Wave 3", query=query)
    assert url == (
        f"{BASE}/Wave%203/_odata/v4.0-preview/Iterations"
        "?$select=IterationPath,StartDate"
        "&$filter=startswith(IterationPath,'ALM%5CALM%20O%20and%20M%5COM%20Sprint')"
    )


def test_build_url_keeps_apply_and_strips_leading_question_mark():
    query = "?$apply=filter(WorkItemType eq 'Bug')/groupby((State),aggregate($count as Count))"
    url = build_analytics_url(ORG, "WorkItems", project="ALM", query=query)
    assert url == (
        f"{BASE}/ALM/_odata/v4.0-preview/WorkItems"
        "?$apply=filter(WorkItemType%20eq%20'Bug')/groupby((State),aggregate($count%20as%20Count))"
    )


def test_build_url_encodes_plus_hash_and_percent():
    url = build_analytics_url(ORG, "WorkItems", query="$filter=Title eq 'C++ #1 50%'")
    assert url.endswith("?$filter=Title%20eq%20'C%2B%2B%20%231%2050%25'")


def test_query_follows_next_link_until_done():
    link = f"{BASE}/ALM/_odata/v4.0-preview/WorkItems?$select=WorkItemId&$skiptoken=2"
    pages = [_page([{"WorkItemId": 1}, {"WorkItemId": 2}], link), _page([{"WorkItemId": 3}])]
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, side_effect=pages) as op:
        out = asyncio.run(query_analytics(_conn("ALM"), "WorkItems", query="$select=WorkItemId"))
    assert [r["WorkItemId"] for r in out["value"]] == [1, 2, 3]
    assert out["pages"] == 2 and out["truncated"] is False
    assert op.call_args_list[0].args[1] == "analytics"
    assert op.call_args_list[0].kwargs["url"] == f"{BASE}/ALM/_odata/v4.0-preview/WorkItems?$select=WorkItemId"
    assert op.call_args_list[1].kwargs["url"] == link


def test_query_max_rows_stops_before_next_page():
    link = f"{BASE}/_odata/v4.0-preview/WorkItems?$skiptoken=3"
    pages = [_page([{"id": 1}, {"id": 2}, {"id": 3}], link), _page([{"id": 4}])]
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, side_effect=pages) as op:
        out = asyncio.run(query_analytics(_conn(), "WorkItems", max_rows=2))
    assert out["value"] == [{"id": 1}, {"id": 2}]
    assert out["truncated"] is True and op.call_count == 1


def test_query_max_rows_reached_on_the_last_page_is_not_truncated():
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock,
               return_value=_page([{"id": 1}, {"id": 2}])):
        out = asyncio.run(query_analytics(_conn(), "WorkItems", max_rows=2))
    assert len(out["value"]) == 2 and out["truncated"] is False


def test_query_refuses_next_link_to_another_host():
    pages = [_page([{"id": 1}], "https://evil.example.com/steal")]
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, side_effect=pages):
        with pytest.raises(click.ClickException, match="Refusing"):
            asyncio.run(query_analytics(_conn(), "WorkItems"))


def test_query_http_error_raises_with_status_and_body():
    err = CommandResult(["m"], 1, "", 'HTTP 400 for analytics: {"error":{"message":"VS403483: bad"}}')
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, return_value=err):
        with pytest.raises(click.ClickException, match="HTTP 400.*VS403483"):
            asyncio.run(query_analytics(_conn(), "WorkItems", query="$select=Bogus"))


def test_query_single_value_response_becomes_one_row():
    single = CommandResult(["m"], 0, json.dumps({"WorkItemId": 42, "Title": "x"}), "")
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, return_value=single):
        out = asyncio.run(query_analytics(_conn(), "WorkItems(42)"))
    assert out["value"] == [{"WorkItemId": 42, "Title": "x"}]


def _config(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = default_config()
    cfg["organization"]["url"] = ORG
    cfg["organization"]["project"] = "Wave 3"
    cfg["auth"] = {"method": "pat", "pat": "fake"}
    save_config(cfg, data_dir / "config.toml")


def _response(body):
    response = MagicMock(status=200, headers={})
    response.read.return_value = json.dumps(body).encode("utf-8")
    response.__enter__.return_value = response
    return response


def test_analytics_command_prints_json_and_counts_to_stderr(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _config(data_dir)
    link = f"{BASE}/ALM/_odata/v4.0-preview/Iterations?$skiptoken=1"
    responses = [_response({"value": [{"IterationPath": "ALM\\OM Sprint 1"}], "@odata.nextLink": link}),
                 _response({"value": [{"IterationPath": "ALM\\OM Sprint 2"}]})]
    with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
        result = CliRunner().invoke(main, [
            "analytics", "Iterations", "--project", "ALM",
            "--query", r"$select=IterationPath&$filter=startswith(IterationPath,'ALM\OM')",
            "--data-dir", str(data_dir),
        ])
    assert result.exit_code == 0, result.output
    first = mock_open.call_args_list[0].args[0]
    assert first.get_method() == "GET"
    assert first.full_url == (f"{BASE}/ALM/_odata/v4.0-preview/Iterations"
                              "?$select=IterationPath&$filter=startswith(IterationPath,'ALM%5COM')")
    assert first.get_header("Authorization").startswith("Basic ")
    assert mock_open.call_args_list[1].args[0].full_url == link
    assert json.loads(result.stdout) == {"value": [{"IterationPath": "ALM\\OM Sprint 1"},
                                                   {"IterationPath": "ALM\\OM Sprint 2"}]}
    assert result.stderr.strip() == "2 row(s) from Iterations (2 page(s))"


def test_analytics_command_ignores_configured_project(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _config(data_dir)
    with patch("urllib.request.urlopen", return_value=_response({"value": []})) as mock_open:
        result = CliRunner().invoke(main, ["analytics", "Projects", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert mock_open.call_args.args[0].full_url == f"{BASE}/_odata/v4.0-preview/Projects"


def test_analytics_command_out_and_max_rows_write_nothing_else(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _config(data_dir)
    out = tmp_path / "rows.json"
    page = _response({"value": [{"Title": "What’s next"}, {"Title": "b"}, {"Title": "c"}],
                      "@odata.nextLink": f"{BASE}/_odata/v4.0-preview/WorkItems?$skiptoken=3"})
    with patch("urllib.request.urlopen", return_value=page) as mock_open:
        result = CliRunner().invoke(main, ["analytics", "WorkItems", "--max-rows", "2", "--out", str(out),
                                           "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert mock_open.call_count == 1
    assert result.stdout == ""
    assert "2 row(s) from WorkItems (1 page(s)), stopped at --max-rows 2, written to" in result.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == {"value": [{"Title": "What’s next"}, {"Title": "b"}]}
    assert sorted(p.name for p in data_dir.iterdir()) == ["config.toml"]


def test_analytics_command_http_error_exits_nonzero_with_body(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _config(data_dir)
    err = HTTPError("u", 400, "Bad Request", {}, None)
    err.read = lambda: b'{"error":{"code":"0","message":"VS403483: The query specified in the URI is not valid"}}'
    with patch("urllib.request.urlopen", side_effect=err):
        result = CliRunner().invoke(main, ["analytics", "WorkItems", "--query", "$select=Bogus",
                                           "--data-dir", str(data_dir)])
    assert result.exit_code == 1
    assert "HTTP 400" in result.stderr and "VS403483: The query specified in the URI is not valid" in result.stderr
    assert result.stdout == ""
