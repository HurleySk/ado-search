from __future__ import annotations

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


def _dict_to_toml(d: dict, prefix: str = "") -> str:
    """Minimal TOML serializer for our config structure."""
    lines: list[str] = []
    tables: list[tuple[str, dict]] = []

    for key, value in d.items():
        if isinstance(value, dict):
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            tables.append((full_key, value))
        elif isinstance(value, list):
            items = ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in value)
            lines.append(f"{key} = [{items}]")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        elif isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")

    result = "\n".join(lines)
    for table_key, table_val in tables:
        section = _dict_to_toml(table_val, prefix=table_key)
        result += f"\n\n[{table_key}]\n{section}"

    return result.strip() + "\n"
