import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError

import pytest
from click.testing import CliRunner

from ado_search.auth import (
    OP_TEST_SUITE_CASES, OP_WIKI_PAGE_PUT, OPERATIONS, _resolve_url, build_full_request_command,
    pat_request_full,
)
from ado_search.cli import main
from ado_search.config import default_config, save_config
from ado_search.runner import CommandResult, run_operation_full
from ado_search import testplans
from ado_search.testplans import (
    Conn, case_record, list_plans, list_runs, list_suite_cases, order_suites, parse_parameters,
    parse_steps, point_record, suite_record,
)

ORG = "https://dev.azure.com/contoso"

STEPS_XML = (
    '<steps id="0" last="5">'
    '<step id="2" type="ValidateStep">'
    '<parameterizedString isformatted="true">&lt;DIV&gt;&lt;P&gt;Open &lt;B&gt;WorkItems&lt;/B&gt;&lt;/P&gt;&lt;/DIV&gt;</parameterizedString>'
    '<parameterizedString isformatted="true">&lt;P&gt;Ensure the grid shows&lt;/P&gt;</parameterizedString>'
    '<description/></step>'
    '<compref id="3" ref="76502">'
    '<step id="4" type="ActionStep"><parameterizedString isformatted="true">Click Save</parameterizedString>'
    '<parameterizedString isformatted="true"></parameterizedString></step>'
    '</compref>'
    '</steps>'
)


def test_resolve_url_fills_plan_suite_and_extra_params():
    url = _resolve_url(OPERATIONS[OP_TEST_SUITE_CASES], org=ORG, project="Wave 3",
                       plan_id=10, suite_id=11, params=["witFields=System.Title", "continuationToken=a%3Bb"])
    assert url == (f"{ORG}/Wave%203/_apis/testplan/Plans/10/Suites/11/TestCase?returnIdentityRef=true"
                   "&expand=true&api-version=7.1&witFields=System.Title&continuationToken=a%3Bb")


def test_pat_request_full_keeps_headers_and_returns_http_errors():
    response = MagicMock(status=200, headers={"X-MS-ContinuationToken": "tok", "ETag": '"v1"'})
    response.read.return_value = b'{"value": []}'
    response.__enter__.return_value = response
    with patch("urllib.request.urlopen", return_value=response) as mock_open:
        out = pat_request_full(OP_WIKI_PAGE_PUT, org=ORG, project="P", pat="x", wiki="P.wiki",
                               path="/QA SOP", body='{"content": "x"}', extra_headers={"If-Match": '"v0"'})
    req = mock_open.call_args.args[0]
    assert req.get_method() == "PUT"
    assert req.full_url == f"{ORG}/P/_apis/wiki/wikis/P.wiki/pages?path=/QA%20SOP&api-version=7.1"
    assert req.get_header("If-match") == '"v0"'
    assert out.status == 200 and out.headers["x-ms-continuationtoken"] == "tok"

    err = HTTPError("u", 412, "Precondition Failed", {"ETag": '"v2"'}, None)
    err.read = lambda: b'{"message": "conflict"}'
    with patch("urllib.request.urlopen", side_effect=err):
        out = pat_request_full(OP_WIKI_PAGE_PUT, org=ORG, project="P", pat="x", wiki="w", path="/a", body="{}")
    assert out.status == 412 and "conflict" in out.text and out.headers["etag"] == '"v2"'


@pytest.mark.parametrize("auth,token_part", [("az-cli", "az account get-access-token"),
                                             ("az-powershell", "Get-AzAccessToken")])
def test_build_full_request_command(tmp_path, auth, token_part):
    body = tmp_path / "b.json"
    cmd = build_full_request_command(OP_WIKI_PAGE_PUT, auth, org=ORG, project="P", wiki="w", path="/a",
                                     body_file=body, extra_headers={"If-Match": '"v1"'})
    script = cmd[-1]
    assert cmd[:3] == ["pwsh", "-NoProfile", "-Command"]
    assert token_part in script
    assert "-Method Put" in script and "-SkipHttpErrorCheck" in script
    assert f"-InFile '{body}'" in script
    assert "'If-Match' = '\"v1\"'" in script


def test_run_operation_full_pwsh_parses_envelope():
    envelope = json.dumps({"status": 200, "headers": {"x-ms-continuationtoken": "t"}, "content": '{"value": [1]}'})
    with patch("ado_search.runner.run_command", new_callable=AsyncMock,
               return_value=CommandResult(["pwsh"], 0, envelope, "")):
        result = asyncio.run(run_operation_full("az-cli", OP_TEST_SUITE_CASES, org=ORG, project="P",
                                                plan_id=1, suite_id=2))
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"value": [1]}
    assert result.headers == {"x-ms-continuationtoken": "t"}

    envelope = json.dumps({"status": 404, "headers": {}, "content": "missing"})
    with patch("ado_search.runner.run_command", new_callable=AsyncMock,
               return_value=CommandResult(["pwsh"], 0, envelope, "")):
        result = asyncio.run(run_operation_full("az-cli", OP_TEST_SUITE_CASES, org=ORG, project="P"))
    assert result.returncode == 1 and "HTTP 404" in result.stderr

    envelope = json.dumps({"status": 203, "headers": {}, "content": "<html>sign in</html>"})
    with patch("ado_search.runner.run_command", new_callable=AsyncMock,
               return_value=CommandResult(["pwsh"], 0, envelope, "")):
        result = asyncio.run(run_operation_full("az-cli", OP_TEST_SUITE_CASES, org=ORG, project="P"))
    assert result.returncode == 1 and "not signed in" in result.stderr


def test_parse_steps_html_shared_steps_and_expected():
    assert parse_steps(STEPS_XML) == [
        {"action": "Open WorkItems", "expected": "Ensure the grid shows"},
        {"shared_steps": 76502},
        {"action": "Click Save", "expected": None},
    ]
    assert parse_steps(None) == []
    assert parse_steps("<steps") == [{"raw": "<steps"}]


def test_parse_parameters():
    xml = '<parameters><param name="Release" bind="default"/><param name="Library" bind="default"/></parameters>'
    assert parse_parameters(xml) == ["Release", "Library"]
    assert parse_parameters("") == []


def test_case_record_unwraps_identity_ref_and_steps():
    raw = {
        "order": 3,
        "workItem": {"id": 76237, "name": "ATMS+_WorkItems", "workItemFields": [
            {"System.CreatedBy": {"distinctDisplayName": "T R <t@x>", "identityRef": {"displayName": "T R"}}},
            {"Microsoft.VSTS.TCM.Steps": STEPS_XML},
            {"Microsoft.VSTS.Common.Priority": 2},
        ]},
        "pointAssignments": [{"configurationName": "Windows 11", "tester": {"displayName": "T R"}}],
    }
    rec = case_record(raw, ["System.CreatedBy", "Microsoft.VSTS.TCM.Steps", "Microsoft.VSTS.Common.Priority",
                            "System.Tags"])
    assert rec["id"] == 76237 and rec["title"] == "ATMS+_WorkItems"
    assert rec["fields"]["System.CreatedBy"] == "T R"
    assert rec["fields"]["Microsoft.VSTS.TCM.Steps"][1] == {"shared_steps": 76502}
    assert rec["fields"]["System.Tags"] is None
    assert rec["assignments"] == [{"configuration": "Windows 11", "tester": "T R"}]


def test_order_suites_depth_first_with_paths():
    raw = [
        {"id": 3, "name": "Story", "suiteType": "requirementTestSuite", "parentSuite": {"id": 2}, "requirementId": 99},
        {"id": 1, "name": "Root", "suiteType": "staticTestSuite"},
        {"id": 2, "name": "Sprint", "suiteType": "staticTestSuite", "parentSuite": {"id": 1}},
        {"id": 4, "name": "Regression", "suiteType": "dynamicTestSuite", "parentSuite": {"id": 1},
         "queryString": "select x"},
    ]
    ordered = order_suites([suite_record(s) for s in raw])
    assert [(s["id"], s["depth"]) for s in ordered] == [(1, 0), (2, 1), (3, 2), (4, 1)]
    assert ordered[2]["path"] == "Root/Sprint/Story" and ordered[2]["requirement_id"] == 99
    assert ordered[3]["query"] == "select x"


def test_point_record_drops_placeholder_date():
    rec = point_record({"id": 5, "testCaseReference": {"id": 7, "name": "c"}, "configuration": {"name": "W11"},
                        "tester": {"displayName": "T"}, "results": {
                            "outcome": "passed", "lastTestRunId": 0,
                            "lastResultDetails": {"dateCompleted": "0001-01-01T00:00:00Z"}}})
    assert rec["last_run_date"] is None and rec["last_run_id"] is None and rec["outcome"] == "passed"


def _conn():
    return Conn(org=ORG, project="Wave 3", auth_method="pat", pat="x")


def test_list_suite_cases_follows_continuation_tokens():
    pages = [
        CommandResult(["m"], 0, json.dumps({"value": [{"workItem": {"id": 1, "name": "a"}}]}), "",
                      {"x-ms-continuationtoken": "next;1"}),
        CommandResult(["m"], 0, json.dumps({"value": [{"workItem": {"id": 2, "name": "b"}}]}), "", {}),
    ]
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, side_effect=pages) as op:
        cases = asyncio.run(list_suite_cases(_conn(), 10, 11, ["System.Title"]))
    assert [c["id"] for c in cases] == [1, 2]
    assert op.call_args_list[0].kwargs["params"] == ["witFields=System.Title"]
    assert op.call_args_list[1].kwargs["params"] == ["witFields=System.Title", "continuationToken=next%3B1"]


def test_list_runs_pages_with_skip_and_limit(monkeypatch):
    monkeypatch.setattr(testplans, "SKIP_PAGE", 2)
    pages = [
        CommandResult(["m"], 0, json.dumps({"value": [{"id": 1}, {"id": 2}]}), ""),
        CommandResult(["m"], 0, json.dumps({"value": [{"id": 3}]}), ""),
    ]
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, side_effect=pages) as op:
        runs = asyncio.run(list_runs(_conn(), plan_id=7))
    assert [r["id"] for r in runs] == [1, 2, 3]
    assert op.call_args_list[1].kwargs["params"] == ["planId=7", "$top=2", "$skip=2"]


def test_list_plans_owner_filter_and_error():
    page = CommandResult(["m"], 0, json.dumps({"value": [
        {"id": 2, "name": "B", "owner": {"displayName": "Tahmina Rahman (CTR)"}},
        {"id": 1, "name": "A", "owner": {"displayName": "Someone Else"}},
    ]}), "")
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock, return_value=page):
        plans = asyncio.run(list_plans(_conn(), owner="tahmina"))
    assert [p["id"] for p in plans] == [2]

    import click
    with patch("ado_search.runner.run_operation_full", new_callable=AsyncMock,
               return_value=CommandResult(["m"], 1, "", "HTTP 401 for test-plan-list")):
        with pytest.raises(click.ClickException, match="401"):
            asyncio.run(list_plans(_conn()))


def test_test_plans_command_uses_project_override(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    cfg = default_config()
    cfg["organization"]["url"] = ORG
    cfg["organization"]["project"] = "Default"
    cfg["auth"] = {"method": "pat", "pat": "fake"}
    save_config(cfg, data_dir / "config.toml")
    response = MagicMock(status=200, headers={})
    response.read.return_value = json.dumps({"value": [
        {"id": 5, "name": "Regression", "state": "Active", "owner": {"displayName": "QA Lead"},
         "rootSuite": {"id": 6}, "startDate": "2026-01-01T00:00:00Z"}]}).encode()
    response.__enter__.return_value = response
    with patch("urllib.request.urlopen", return_value=response) as mock_open:
        result = CliRunner().invoke(main, ["test-plans", "--project", "Other Proj", "--json",
                                           "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert mock_open.call_args.args[0].full_url.startswith(f"{ORG}/Other%20Proj/_apis/testplan/plans?")
    assert json.loads(result.output)[0] == {
        "id": 5, "name": "Regression", "state": "Active", "owner": "QA Lead", "area": None,
        "iteration": None, "start": "2026-01-01", "end": None, "root_suite_id": 6, "updated": None,
        "updated_by": None, "description": None,
    }
