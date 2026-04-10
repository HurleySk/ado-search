import os
from pathlib import Path

from ado_search.config import load_config, save_config, default_config


def test_default_config_has_required_keys():
    cfg = default_config()
    assert cfg["organization"]["url"] == ""
    assert cfg["organization"]["project"] == ""
    assert cfg["auth"]["method"] == "az-cli"
    assert cfg["sync"]["work_item_types"] == ["Bug", "User Story", "Epic", "Feature"]
    assert cfg["sync"]["area_paths"] == []
    assert cfg["sync"]["states"] == []
    assert cfg["sync"]["wiki_names"] == []
    assert cfg["sync"]["last_sync"] == ""
    assert cfg["sync"]["performance"]["max_concurrent"] == 5


def test_save_and_load_roundtrip(tmp_path):
    cfg = default_config()
    cfg["organization"]["url"] = "https://dev.azure.com/contoso"
    cfg["organization"]["project"] = "MyProject"
    config_path = tmp_path / "config.toml"
    save_config(cfg, config_path)
    loaded = load_config(config_path)
    assert loaded["organization"]["url"] == "https://dev.azure.com/contoso"
    assert loaded["organization"]["project"] == "MyProject"
    assert loaded["auth"]["method"] == "az-cli"


def test_load_config_file_not_found(tmp_path):
    config_path = tmp_path / "nonexistent.toml"
    try:
        load_config(config_path)
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass
