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
    wi_dir = data_dir / "work-items"
    wi_dir.mkdir(parents=True)
    (wi_dir / "100.md").write_text("---\nid: 100\ntitle: Test\n---\n\nDescription here\n")

    runner = CliRunner()
    result = runner.invoke(main, ["show", "100",
        "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 0
    assert "Description here" in result.output
