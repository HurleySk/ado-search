from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def default_config() -> dict:
    return {
        "organization": {
            "url": "",
            "project": "",
        },
        "auth": {
            "method": "az-cli",
        },
        "sync": {
            "work_item_types": ["Bug", "User Story", "Epic", "Feature"],
            "area_paths": [],
            "states": [],
            "wiki_names": [],
            "include_comments": False,
            "include_attachments": False,
            "last_sync": "",
            "performance": {
                "max_concurrent": 5,
            },
        },
    }


def save_config(config: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = _dict_to_toml(config)
    path.write_text(lines, encoding="utf-8")


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    """Quote keys that are not bare TOML keys (e.g. project names with spaces)."""
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _toml_str(value: str) -> str:
    """TOML basic string. JSON string escapes are a subset of TOML's."""
    return json.dumps(value, ensure_ascii=False)


def _dict_to_toml(d: dict, prefix: str = "") -> str:
    """Minimal TOML serializer for our config structure."""
    lines: list[str] = []
    tables: list[tuple[str, dict]] = []

    for key, value in d.items():
        if isinstance(value, dict):
            k = _toml_key(key)
            full_key = k if not prefix else f"{prefix}.{k}"
            tables.append((full_key, value))
        elif isinstance(value, list):
            items = ", ".join(_toml_str(v) if isinstance(v, str) else str(v) for v in value)
            lines.append(f"{_toml_key(key)} = [{items}]")
        elif isinstance(value, str):
            lines.append(f"{_toml_key(key)} = {_toml_str(value)}")
        elif isinstance(value, bool):
            lines.append(f"{_toml_key(key)} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{_toml_key(key)} = {value}")

    result = "\n".join(lines)
    for table_key, table_val in tables:
        section = _dict_to_toml(table_val, prefix=table_key)
        result += f"\n\n[{table_key}]\n{section}"

    return result.strip() + "\n"
