"""JSONL read/write/merge utilities."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield items from a JSONL file one at a time."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_jsonl(path: Path, *, key: str) -> dict[Any, dict]:
    """Read a JSONL file into a dict keyed by the given field.

    Returns {} if the file does not exist.
    """
    return {obj[key]: obj for obj in iter_jsonl(path)}


def read_jsonl_item(path: Path, *, key: str, value: Any) -> dict | None:
    """Scan JSONL for a single item where item[key] == value. Returns early."""
    for item in iter_jsonl(path):
        if item.get(key) == value:
            return item
    return None


def write_jsonl(path: Path, items: dict[Any, dict], *, sort_key: str) -> None:
    """Write items dict to a JSONL file, sorted by sort_key.

    Uses an atomic write: writes to a temp file in the same directory, then
    renames. On Windows, unlinks the target before rename if it already exists.
    Parent directories are created as needed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    sorted_items = sorted(items.values(), key=lambda obj: obj[sort_key])

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            for obj in sorted_items:
                f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

        # Windows requires unlinking the target before rename
        if path.exists():
            path.unlink()
        os.rename(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def merge_jsonl(
    path: Path,
    new_items: dict[Any, dict],
    *,
    key: str,
    remove_keys: set | None = None,
) -> dict[Any, dict]:
    """Load existing JSONL from path, merge new_items, remove remove_keys.

    Returns the merged dict. Does NOT write — caller is responsible for writing.
    """
    existing = read_jsonl(path, key=key)
    existing.update(new_items)
    if remove_keys:
        for k in remove_keys:
            existing.pop(k, None)
    return existing
