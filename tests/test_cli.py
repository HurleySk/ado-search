import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from click.testing import CliRunner

from ado_search.cli import main
from ado_search.runner import CommandResult


def test_init_creates_config(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["init",
        "--org", "https://dev.azure.com/contoso",
        "--project", "MyProject",
        "--auth-method", "az-cli",
        "--data-dir", str(tmp_path / ".ado-search"),
    ])
    assert result.exit_code == 0
    config_path = tmp_path / ".ado-search" / "config.toml"
    assert config_path.exists()
    content = config_path.read_text()
    assert "contoso" in content
    assert "MyProject" in content


def test_search_no_data_dir(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["search", "test",
        "--data-dir", str(tmp_path / "nonexistent"),
    ])
    assert result.exit_code != 0


def test_show_work_item(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()

    # Seed JSONL
    wi_jsonl = data_dir / "work-items.jsonl"
    wi_jsonl.write_text(json.dumps({
        "id": 12345, "title": "Test Item", "type": "Bug", "state": "Active",
        "area": "A", "iteration": "I", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2025-01-01",
        "updated": "2025-01-02", "description": "Description here",
        "acceptance_criteria": "", "comments": [],
    }) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["show", "12345", "--data-dir", str(data_dir)])
    assert result.exit_code == 0
    assert "Test Item" in result.output
    assert "Description here" in result.output


def test_grep_finds_matches(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    wi_jsonl.write_text(json.dumps({
        "id": 100, "title": "Server IP 10.0.0.1 issue", "type": "Bug", "state": "Active",
        "area": "A", "iteration": "I", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-01-02", "description": "The server 10.0.0.1 is down",
        "acceptance_criteria": "",
    }) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, [
        "grep", r"\d+\.\d+\.\d+\.\d+", "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 0
    assert "#100" in result.output
    assert "10.0.0.1" in result.output


def test_grep_no_matches_exit_code_1(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    wi_jsonl.write_text(json.dumps({
        "id": 100, "title": "Test item", "type": "Bug", "state": "Active",
        "area": "", "iteration": "", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-01-01", "description": "nothing special",
        "acceptance_criteria": "",
    }) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, [
        "grep", "zzzznotfound", "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 1


def test_grep_invalid_regex_exit_code_2(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    wi_jsonl.write_text(json.dumps({
        "id": 1, "title": "X", "type": "Bug", "state": "Active",
        "area": "", "iteration": "", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-01-01", "description": "",
        "acceptance_criteria": "",
    }) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, [
        "grep", "[invalid", "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 2


def test_grep_brief_format(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    wi_jsonl.write_text(json.dumps({
        "id": 100, "title": "Bug with SSO", "type": "Bug", "state": "Active",
        "area": "", "iteration": "", "assigned_to": "", "tags": "",
        "priority": 1, "parent_id": None, "created": "2026-01-01",
        "updated": "2026-01-01", "description": "SSO is broken",
        "acceptance_criteria": "",
    }) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, [
        "grep", "SSO", "--brief", "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 0
    assert "#100" in result.output
    assert "[" in result.output


def test_grep_with_metadata_filter(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    wi_jsonl = data_dir / "work-items.jsonl"
    items = [
        {"id": 1, "title": "Bug A", "type": "Bug", "state": "Active",
         "area": "", "iteration": "", "assigned_to": "", "tags": "",
         "priority": 1, "parent_id": None, "created": "2026-01-01",
         "updated": "2026-01-01", "description": "test pattern here",
         "acceptance_criteria": ""},
        {"id": 2, "title": "Story B", "type": "User Story", "state": "Active",
         "area": "", "iteration": "", "assigned_to": "", "tags": "",
         "priority": 2, "parent_id": None, "created": "2026-01-01",
         "updated": "2026-01-01", "description": "test pattern here too",
         "acceptance_criteria": ""},
    ]
    with wi_jsonl.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")

    runner = CliRunner()
    result = runner.invoke(main, [
        "grep", "pattern", "--type", "Bug", "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 0
    assert "#1" in result.output
    assert "#2" not in result.output


def _seed_two_projects(data_dir):
    from ado_search.jsonl import write_jsonl
    base = {"type": "Bug", "state": "Active", "iteration": "", "assigned_to": "", "tags": "",
            "priority": 2, "parent_id": None, "created": "2026-01-01", "updated": "2026-01-01",
            "description": "", "acceptance_criteria": "", "comments": []}
    write_jsonl(data_dir / "work-items.jsonl", {
        1: {**base, "id": 1, "title": "payment timeout", "project": "Alpha", "area": "Alpha"},
        2: {**base, "id": 2, "title": "payment retry", "project": "Beta", "area": "Beta"},
    }, sort_key="id")


def test_search_project_filter(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    _seed_two_projects(data_dir)
    result = CliRunner().invoke(main, ["search", "payment", "--project", "Beta",
                                       "--format", "json", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    ids = [r["id"] for r in json.loads(result.output)]
    assert ids == [2]


def test_grep_project_filter(tmp_path):
    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    _seed_two_projects(data_dir)
    result = CliRunner().invoke(main, ["grep", "payment", "--field", "title", "--project", "Alpha",
                                       "--format", "json", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert [r["id"] for r in json.loads(result.output)] == [1]


from ado_search.config import default_config, load_config, save_config


def _write_config(data_dir, *, project="", projects=None, by_project=None, area_paths=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = default_config()
    if area_paths is not None:
        cfg["sync"]["area_paths"] = area_paths
    cfg["organization"]["url"] = "https://dev.azure.com/contoso"
    cfg["organization"]["project"] = project
    if projects is not None:
        cfg["organization"]["projects"] = projects
    if by_project is not None:
        cfg["sync"]["last_sync_by_project"] = by_project
    save_config(cfg, data_dir / "config.toml")


def _run_sync(data_dir, *extra, wi_side_effect=None, wiki_side_effect=None):
    wi = AsyncMock(return_value={"fetched": 1, "errors": 0}, side_effect=wi_side_effect)
    wiki = AsyncMock(return_value={"fetched": 0, "errors": 0}, side_effect=wiki_side_effect)
    with (
        patch("ado_search.sync_workitems.sync_work_items", wi),
        patch("ado_search.sync_wiki.sync_wiki", wiki),
    ):
        result = CliRunner().invoke(main, ["sync", "--data-dir", str(data_dir), *extra])
    return result, wi, wiki


def test_sync_multiple_projects_uses_per_project_watermark(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"],
                  by_project={"Alpha": "2026-09-01"})
    result, wi, wiki = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    calls = {c.kwargs["project"]: c.kwargs["last_sync"] for c in wi.call_args_list}
    assert calls == {"Alpha": "2026-09-01", "Beta": ""}
    assert wiki.call_args.kwargs["project"] == "Alpha"
    saved = load_config(data_dir / "config.toml")
    assert set(saved["sync"]["last_sync_by_project"]) == {"Alpha", "Beta"}


def test_sync_project_option_restricts(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])
    result, wi, wiki = _run_sync(data_dir, "--project", "Beta")
    assert result.exit_code == 0, result.output
    assert [c.kwargs["project"] for c in wi.call_args_list] == ["Beta"]
    wiki.assert_not_called()


def test_sync_unknown_project_errors(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha")
    result, wi, _ = _run_sync(data_dir, "--project", "Nope")
    assert result.exit_code == 2
    assert "Nope" in result.output
    wi.assert_not_called()


def test_sync_star_expands_remote_projects(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, projects=["*"])
    remote = AsyncMock(return_value=["Alpha", "Beta"])
    with patch("ado_search.projects.fetch_remote_projects", remote):
        result, wi, wiki = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    assert [c.kwargs["project"] for c in wi.call_args_list] == ["Alpha", "Beta"]
    wiki.assert_not_called()  # no default project with ["*"]


def test_sync_failure_in_one_project_continues(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])

    async def flaky(**kwargs):
        if kwargs["project"] == "Alpha":
            raise RuntimeError("WIQL query failed: 401")
        return {"fetched": 1, "errors": 0}

    result, wi, _ = _run_sync(data_dir, wi_side_effect=flaky)
    assert result.exit_code == 1
    assert "Alpha" in result.output
    saved = load_config(data_dir / "config.toml")
    assert set(saved["sync"]["last_sync_by_project"]) == {"Beta"}


def test_write_command_requires_default_project(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, projects=["*"])
    result = CliRunner().invoke(main, ["list-comments", "1", "--data-dir", str(data_dir)])
    assert result.exit_code == 1
    assert "organization.project" in result.output


def _calls(wi, arg="last_sync"):
    return {c.kwargs["project"]: c.kwargs[arg] for c in wi.call_args_list}


def test_sync_of_other_project_leaves_default_unsynced(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])
    _run_sync(data_dir, "--project", "Beta")
    result, wi, _ = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    assert _calls(wi)["Alpha"] == ""


def test_failed_default_project_gets_full_sync_next_run(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])

    async def alpha_fails(**kwargs):
        if kwargs["project"] == "Alpha":
            raise RuntimeError("WIQL query failed: 401")
        return {"fetched": 1, "errors": 0}

    _run_sync(data_dir, wi_side_effect=alpha_fails)
    result, wi, _ = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    assert _calls(wi)["Alpha"] == ""


def test_sync_project_option_ignores_case(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])
    result, wi, _ = _run_sync(data_dir, "--project", "beta")
    assert result.exit_code == 0, result.output
    assert [c.kwargs["project"] for c in wi.call_args_list] == ["Beta"]


def test_sync_star_with_lowercase_default_still_syncs_wiki(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="alpha", projects=["*"])
    remote = AsyncMock(return_value=["Alpha", "Beta"])
    with patch("ado_search.projects.fetch_remote_projects", remote):
        result, _, wiki = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    wiki.assert_called_once()


def test_sync_scopes_area_paths_to_their_project(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"], area_paths=["Alpha\Web"])
    result, wi, _ = _run_sync(data_dir)
    assert result.exit_code == 0, result.output
    assert _calls(wi, "area_paths") == {"Alpha": ["Alpha\Web"], "Beta": []}


def test_sync_unexpected_error_in_one_project_continues(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])

    async def alpha_breaks(**kwargs):
        if kwargs["project"] == "Alpha":
            raise ValueError("Expecting value: line 1 column 1")
        return {"fetched": 1, "errors": 0}

    result, _, _ = _run_sync(data_dir, wi_side_effect=alpha_breaks)
    assert result.exit_code == 1
    assert "Alpha" in result.output
    saved = load_config(data_dir / "config.toml")
    assert set(saved["sync"]["last_sync_by_project"]) == {"Beta"}


def test_sync_wiki_failure_keeps_project_watermarks(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha", projects=["Alpha", "Beta"])
    result, _, _ = _run_sync(data_dir, wiki_side_effect=RuntimeError("wiki list failed: 403"))
    assert result.exit_code == 1
    assert "wiki" in result.output.lower()
    saved = load_config(data_dir / "config.toml")
    assert set(saved["sync"]["last_sync_by_project"]) == {"Alpha", "Beta"}


def test_list_comments_shows_comment_ids(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha")
    comments = {"comments": [
        {"id": 13950321, "text": "<p>First</p>", "createdDate": "2026-10-07T12:00:00Z",
         "createdBy": {"displayName": "Jane Doe"}},
    ]}
    with patch("ado_search.runner.fetch_and_parse", AsyncMock(return_value=comments)):
        result = CliRunner().invoke(main, ["list-comments", "42", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert "[2026-10-07] Jane Doe (id 13950321):" in result.output
    assert "First" in result.output


def _invoke_edit_comment(data_dir, *args):
    edit = AsyncMock(return_value={"id": 42})
    with patch("ado_search.write_workitems.edit_comment", edit):
        result = CliRunner().invoke(main, ["edit-comment", *args, "--data-dir", str(data_dir)])
    return result, edit


def test_edit_comment_command(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha")
    result, edit = _invoke_edit_comment(data_dir, "42", "678", "@@Jane Doe, retest", "--strict-mentions")
    assert result.exit_code == 0, result.output
    assert "Edited comment 678 on work item #42" in result.output
    kwargs = edit.call_args.kwargs
    assert (kwargs["work_item_id"], kwargs["comment_id"]) == (42, 678)
    assert kwargs["text"] == "@Jane Doe, retest"
    assert kwargs["project"] == "Alpha"
    assert kwargs["resolve_mentions_flag"] is True
    assert kwargs["strict_mentions"] is True
    assert kwargs["dry_run"] is False


def test_edit_comment_command_reads_file_and_flags(tmp_path):
    data_dir = tmp_path / ".ado-search"
    _write_config(data_dir, project="Alpha")
    body = tmp_path / "c.html"
    body.write_text("<p>From file</p>", encoding="utf-8")
    result, edit = _invoke_edit_comment(data_dir, "42", "678", f"@{body}", "--no-mentions", "--dry-run")
    assert result.exit_code == 0, result.output
    kwargs = edit.call_args.kwargs
    assert kwargs["text"] == "<p>From file</p>"
    assert kwargs["resolve_mentions_flag"] is False
    assert kwargs["dry_run"] is True
    assert "Edited comment" not in result.output


_LIVE_ITEM = {"id": 42, "fields": {
    "System.Title": "Broken",
    "System.AssignedTo": {"displayName": "Jane Doe", "uniqueName": "Jane.Doe@co.com"},
    "System.Description": "<p><strong>What’s wrong.</strong></p>\n<ul><li>x</li></ul>",
}}


def _invoke_get_fields(data_dir, *args, item=_LIVE_ITEM):
    cfg = default_config()
    cfg["organization"]["url"] = "https://dev.azure.com/contoso"
    cfg["auth"] = {"method": "pat", "pat": "fake"}
    save_config(cfg, data_dir / "config.toml")
    response = MagicMock()
    response.read.return_value = json.dumps(item).encode("utf-8")
    response.__enter__.return_value = response
    with patch("urllib.request.urlopen", return_value=response) as mock_open:
        result = CliRunner().invoke(main, ["get-fields", *args, "--data-dir", str(data_dir)])
    return result, mock_open


def test_get_fields_prints_raw_json(tmp_path):
    data_dir = tmp_path / ".ado-search"
    result, mock_open = _invoke_get_fields(
        data_dir, "42", "--field", "System.Description", "--field", "System.AssignedTo",
    )
    assert result.exit_code == 0, result.output
    req = mock_open.call_args.args[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://dev.azure.com/contoso/_apis/wit/workitems/42?api-version=7.1"
    assert json.loads(result.output) == {
        "System.Description": "<p><strong>What’s wrong.</strong></p>\n<ul><li>x</li></ul>",
        "System.AssignedTo": "Jane.Doe@co.com",
    }


def test_get_fields_default_fields(tmp_path):
    from ado_search.fields import DEFAULT_FIELDS

    result, _ = _invoke_get_fields(tmp_path / ".ado-search", "42")
    assert result.exit_code == 0, result.output
    values = json.loads(result.output)
    assert list(values) == list(DEFAULT_FIELDS)
    assert values["System.Title"] == "Broken"
    assert values["System.Tags"] is None


def test_get_fields_out_writes_utf8_file_and_no_store(tmp_path):
    data_dir = tmp_path / ".ado-search"
    out = tmp_path / "fields.json"
    result, _ = _invoke_get_fields(data_dir, "42", "--field", "System.Description", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "Wrote 1 field(s) of work item #42" in result.output
    assert "<p>" not in result.output
    assert "What’s".encode("utf-8") in out.read_bytes()
    assert json.loads(out.read_text(encoding="utf-8"))["System.Description"].startswith("<p><strong>")
    assert sorted(p.name for p in data_dir.iterdir()) == ["config.toml"]


def test_get_fields_http_error_exits_nonzero(tmp_path):
    from urllib.error import HTTPError

    data_dir = tmp_path / ".ado-search"
    data_dir.mkdir()
    cfg = default_config()
    cfg["organization"]["url"] = "https://dev.azure.com/contoso"
    cfg["auth"] = {"method": "pat", "pat": "fake"}
    save_config(cfg, data_dir / "config.toml")
    err = HTTPError("u", 404, "Not Found", {}, None)
    err.read = lambda: b"TF401232: Work item 9 does not exist"
    with patch("urllib.request.urlopen", side_effect=err):
        result = CliRunner().invoke(main, ["get-fields", "9", "--data-dir", str(data_dir)])
    assert result.exit_code == 1
    assert "HTTP 404" in result.output
