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
