# src/ado_search/sync_common.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import click

from ado_search.jsonl import read_jsonl, write_jsonl
from ado_search.markdown import CUSTOM_TEXT_FIELDS, extract_work_item_metadata, strip_html
from ado_search.projects import project_from_area



def prepare_work_item(
    raw: dict,
    *,
    comments: list[dict] | None = None,
    attachments: list[dict] | None = None,
    inline_images: list[dict] | None = None,
) -> dict:
    """Extract metadata from a raw ADO work item into a flat JSONL-ready dict."""
    meta = extract_work_item_metadata(raw)
    record = {
        "id": meta["id"],
        "project": meta["project"] or project_from_area(meta["area"]),
        "title": meta["title"],
        "type": meta["type"],
        "state": meta["state"],
        "area": meta["area"],
        "iteration": meta["iteration"],
        "assigned_to": meta["assigned_to"],
        "tags": meta["tags"],
        "priority": meta["priority"],
        "story_points": meta.get("story_points"),
        "parent_id": meta["parent_id"],
        "closed_date": meta["closed_date"],
        "created": meta["created"],
        "updated": meta["updated"],
        "description": meta["description_full"],
        "acceptance_criteria": meta["acceptance_criteria"],
    }
    for key in CUSTOM_TEXT_FIELDS.values():
        record[key] = meta.get(key, "")
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
    record["attachments"] = attachments or []
    record["inline_images"] = inline_images or []
    return record


def extract_state_history(updates: list[dict]) -> list[dict]:
    """Extract state transitions from work item update records."""
    history = []
    for update in updates:
        fields = update.get("fields", {})
        state_change = fields.get("System.State")
        if not state_change or "oldValue" not in state_change:
            continue

        changed_date_field = fields.get("System.ChangedDate", {})
        changed_date = (changed_date_field.get("newValue", "") or "")[:10]

        changed_by_field = fields.get("System.ChangedBy", {})
        changed_by_val = changed_by_field.get("newValue", "")
        if isinstance(changed_by_val, dict):
            changed_by = changed_by_val.get("uniqueName", changed_by_val.get("displayName", ""))
        elif isinstance(changed_by_val, str):
            changed_by = changed_by_val
        else:
            changed_by = ""

        history.append({
            "from": state_change["oldValue"],
            "to": state_change["newValue"],
            "date": changed_date,
            "by": changed_by,
        })
    return history


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


def _carry_forward_state_history(existing: dict[Any, dict], fetched: dict[Any, dict]) -> None:
    """Keep previously captured state history when a fetch path doesn't provide one."""
    for k, rec in fetched.items():
        if "state_history" in rec:
            continue
        old = existing.get(k)
        if old and old.get("state_history"):
            rec["state_history"] = old["state_history"]


def finalize_jsonl(
    jsonl_path: Path,
    fetched_records: dict[Any, dict],
    *,
    key: str,
    sort_key: str,
    is_incremental: bool,
    remote_keys: set | None = None,
    scope: Callable[[dict], bool] | None = None,
) -> set:
    """Write JSONL with orphan detection. Returns set of orphaned keys.

    Incremental syncs merge fetched_records into the existing JSONL. Full syncs
    drop existing records missing from remote_keys (if given) or fetched_records;
    when ``scope`` is given, only existing records for which it returns True are
    orphan candidates (e.g. the project being synced).
    """
    existing = read_jsonl(jsonl_path, key=key)
    _carry_forward_state_history(existing, fetched_records)

    if is_incremental:
        existing.update(fetched_records)
        write_jsonl(jsonl_path, existing, sort_key=sort_key)
        return set()

    candidates = {k for k, v in existing.items() if scope(v)} if scope else set(existing)
    compare_keys = remote_keys if remote_keys is not None else set(fetched_records.keys())
    orphans = candidates - compare_keys
    if orphans:
        click.echo(f"  Removing {len(orphans)} orphaned items")

    all_items = {k: v for k, v in existing.items() if k not in orphans}
    all_items.update(fetched_records)
    write_jsonl(jsonl_path, all_items, sort_key=sort_key)
    return orphans
