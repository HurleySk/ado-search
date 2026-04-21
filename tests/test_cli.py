import json
from pathlib import Path
from unittest.mock import patch, AsyncMock

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
