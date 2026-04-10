from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ado_search.db import Database
from ado_search.markdown import work_item_to_markdown, extract_work_item_metadata


def detect_deletions(
    *,
    remote_keys: set,
    get_local_keys: Callable[[], set],
    delete_batch_fn: Callable[[list], None],
    path_fn: Callable[[Any], Path],
) -> list:
    """Remove local items whose keys are absent from remote_keys.

    Args:
        remote_keys: Set of IDs/paths that still exist remotely.
        get_local_keys: Returns the set of locally-known keys (from DB).
        delete_batch_fn: Called once with all orphan keys to remove them from the DB.
        path_fn: Maps a key to its markdown file path on disk.

    Returns:
        List of orphaned keys that were deleted.
    """
    orphans = get_local_keys() - remote_keys
    for key in orphans:
        md_path = path_fn(key)
        if md_path.exists():
            md_path.unlink()
    if orphans:
        delete_batch_fn(list(orphans))
    return list(orphans)


def write_work_item(
    raw: dict,
    *,
    comments: list[dict] | None,
    data_dir: Path,
    db: Database,
) -> None:
    """Convert a work item dict to markdown, write to disk, and upsert into DB."""
    item_id = raw["id"]
    meta = extract_work_item_metadata(raw)
    md = work_item_to_markdown(raw, comments=comments, meta=meta)

    md_path = data_dir / "work-items" / f"{item_id}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")

    db.upsert_work_item(meta)
