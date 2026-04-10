# src/ado_search/sync_common.py
from __future__ import annotations

from ado_search.markdown import extract_work_item_metadata, strip_html


def detect_deletions(
    *,
    remote_keys: set,
    local_keys: set,
) -> set:
    """Return keys present locally but absent remotely."""
    return local_keys - remote_keys


def prepare_work_item(
    raw: dict,
    *,
    comments: list[dict] | None = None,
) -> dict:
    """Extract metadata from a raw ADO work item into a flat JSONL-ready dict."""
    meta = extract_work_item_metadata(raw)
    record = {
        "id": meta["id"],
        "title": meta["title"],
        "type": meta["type"],
        "state": meta["state"],
        "area": meta["area"],
        "iteration": meta["iteration"],
        "assigned_to": meta["assigned_to"],
        "tags": meta["tags"],
        "priority": meta["priority"],
        "parent_id": meta["parent_id"],
        "created": meta["created"],
        "updated": meta["updated"],
        "description": meta["description_full"],
        "acceptance_criteria": meta["acceptance_criteria"],
    }
    if comments:
        record["comments"] = [
            {
                "author": c.get("createdBy", {}).get("displayName", "Unknown"),
                "date": c.get("createdDate", "")[:10],
                "text": strip_html(c.get("text", "")),
            }
            for c in comments
        ]
    else:
        record["comments"] = []
    return record
