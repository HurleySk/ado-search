# src/ado_search/sync_common.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from ado_search.jsonl import merge_jsonl, read_jsonl, write_jsonl
from ado_search.markdown import extract_work_item_metadata, strip_html



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


def split_results(
    results: list,
    *,
    key: str,
) -> tuple[dict[Any, dict], list[str]]:
    """Split asyncio.gather results into (records_dict, error_strings)."""
    records: dict[Any, dict] = {}
    errors: list[str] = []
    for r in results:
        if isinstance(r, str):
            errors.append(r)
        else:
            records[r[key]] = r
    return records, errors


def finalize_jsonl(
    jsonl_path: Path,
    fetched_records: dict[Any, dict],
    *,
    key: str,
    sort_key: str,
    is_incremental: bool,
    remote_keys: set | None = None,
) -> set:
    """Write JSONL with orphan detection. Returns set of orphaned keys.

    For incremental syncs, merges fetched_records into existing JSONL.
    For full syncs, detects orphans by comparing existing keys against
    remote_keys (if provided) or fetched_records keys.
    """
    if is_incremental:
        all_items = merge_jsonl(jsonl_path, fetched_records, key=key)
        write_jsonl(jsonl_path, all_items, sort_key=sort_key)
        return set()

    existing = read_jsonl(jsonl_path, key=key)
    compare_keys = remote_keys if remote_keys is not None else set(fetched_records.keys())
    orphans = set(existing.keys()) - compare_keys
    if orphans:
        click.echo(f"  Removing {len(orphans)} orphaned items")

    if remote_keys is not None:
        # Wiki pattern: keep non-orphaned existing, overlay fetched
        all_items = {k: v for k, v in existing.items() if k in compare_keys}
        all_items.update(fetched_records)
    else:
        all_items = fetched_records

    write_jsonl(jsonl_path, all_items, sort_key=sort_key)
    return orphans
