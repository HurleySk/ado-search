from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import click

from ado_search.auth import OP_ADD_COMMENT, OP_CREATE, OP_UPDATE
from ado_search.runner import run_operation
from ado_search.sync_common import finalize_jsonl


def resolve_value(text: str | None) -> str | None:
    """Resolve a CLI value that may reference a file via ``@path``.

    * ``None`` → ``None``
    * ``@some/file.html`` → contents of that file (UTF-8)
    * ``@@literal`` → ``@literal`` (escape hatch)
    * anything else → returned as-is
    """
    if text is None:
        return None
    if text.startswith("@@"):
        return text[1:]  # strip leading @, keep the rest
    if text.startswith("@"):
        path = Path(text[1:])
        if not path.is_file():
            raise click.BadParameter(f"File not found: {path}")
        return path.read_text(encoding="utf-8")
    return text


# Maps CLI option names to ADO field reference names
FIELD_MAP = {
    "title":               "System.Title",
    "description":         "System.Description",
    "acceptance_criteria":  "Microsoft.VSTS.Common.AcceptanceCriteria",
    "state":               "System.State",
    "area":                "System.AreaPath",
    "iteration":           "System.IterationPath",
    "assigned_to":         "System.AssignedTo",
    "tags":                "System.Tags",
    "priority":            "Microsoft.VSTS.Common.Priority",
    "story_points":        "Microsoft.VSTS.Scheduling.StoryPoints",
}


def build_json_patch(fields: dict[str, Any]) -> list[dict]:
    """Convert {ado_field: value} to JSON Patch operations for ADO REST API."""
    return [
        {"op": "add", "path": f"/fields/{k}", "value": v}
        for k, v in fields.items()
        if v is not None
    ]


def build_az_fields(fields: dict[str, Any]) -> list[str]:
    """Convert {ado_field: value} to 'Key=Value' strings for az boards --fields."""
    return [f"{k}={v}" for k, v in fields.items() if v is not None]


def resolve_fields(
    *,
    title: str | None = None,
    description: str | None = None,
    acceptance_criteria: str | None = None,
    state: str | None = None,
    area: str | None = None,
    iteration: str | None = None,
    assigned_to: str | None = None,
    tags: str | None = None,
    priority: int | None = None,
    story_points: float | None = None,
    extra_fields: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Map named CLI options through FIELD_MAP and merge --field Key=Value entries.

    Named options take precedence over extra_fields for the same ADO field.
    """
    # Start with extra_fields (lower precedence)
    result: dict[str, Any] = {}
    for entry in extra_fields:
        if "=" not in entry:
            continue
        k, _, v = entry.partition("=")
        result[k.strip()] = v.strip()

    # Named options override
    named = {
        "title": title,
        "description": description,
        "acceptance_criteria": acceptance_criteria,
        "state": state,
        "area": area,
        "iteration": iteration,
        "assigned_to": assigned_to,
        "tags": tags,
        "priority": priority,
        "story_points": story_points,
    }
    for cli_name, value in named.items():
        if value is not None:
            ado_field = FIELD_MAP[cli_name]
            result[ado_field] = value

    return result


async def create_work_item(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    work_item_type: str,
    title: str,
    field_values: dict[str, Any],
    dry_run: bool = False,
) -> dict:
    """Create a work item in ADO and merge into local JSONL store.

    Returns the normalized JSONL record for the new item.
    """
    if dry_run:
        click.echo(f"Would create {work_item_type}: {title}")
        if field_values:
            for k, v in field_values.items():
                click.echo(f"  {k} = {v}")
        return {}

    # Build the full field set (title is always included)
    all_fields = {FIELD_MAP["title"]: title, **field_values}

    if auth_method == "az-cli":
        # az boards work-item create uses --title, --type, --fields Key=Value
        az_fields = build_az_fields({k: v for k, v in all_fields.items()
                                     if k != FIELD_MAP["title"]})
        result = await run_operation(
            auth_method, OP_CREATE,
            org=org, project=project, pat=pat,
            title=title, work_item_type=work_item_type,
            fields=az_fields or None,
        )
    else:
        # PAT and powershell use JSON Patch body
        patch = build_json_patch(all_fields)
        body = json.dumps(patch)
        result = await run_operation(
            auth_method, OP_CREATE,
            org=org, project=project, pat=pat,
            work_item_type=work_item_type,
            body=body,
            content_type="application/json-patch+json",
        )

    if result.returncode != 0:
        click.echo(f"Error creating work item: {result.stderr}", err=True)
        raise SystemExit(1)

    response = result.parse_json()
    item_id = response["id"]

    # Re-fetch through the standard pipeline for full normalization
    return await _refetch_and_merge(item_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


async def update_work_item(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    work_item_id: int,
    field_values: dict[str, Any],
    dry_run: bool = False,
) -> dict:
    """Update a work item in ADO and refresh local JSONL store.

    Returns the normalized JSONL record for the updated item.
    """
    if dry_run:
        click.echo(f"Would update work item #{work_item_id}:")
        for k, v in field_values.items():
            click.echo(f"  {k} = {v}")
        return {}

    # For az-cli, split title out (it has its own --title flag)
    field_values = dict(field_values)  # don't mutate caller's dict
    title_value = field_values.pop(FIELD_MAP["title"], None)

    if auth_method == "az-cli":
        az_fields = build_az_fields(field_values)
        result = await run_operation(
            auth_method, OP_UPDATE,
            org=org, project=project, pat=pat,
            work_item_id=work_item_id,
            title=title_value,
            fields=az_fields or None,
        )
    else:
        all_fields = field_values
        if title_value is not None:
            all_fields[FIELD_MAP["title"]] = title_value
        patch = build_json_patch(all_fields)
        body = json.dumps(patch)
        result = await run_operation(
            auth_method, OP_UPDATE,
            org=org, project=project, pat=pat,
            work_item_id=work_item_id,
            body=body,
            content_type="application/json-patch+json",
        )

    if result.returncode != 0:
        click.echo(f"Error updating work item #{work_item_id}: {result.stderr}", err=True)
        raise SystemExit(1)

    # Re-fetch through the standard pipeline for full normalization
    return await _refetch_and_merge(work_item_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


async def add_comment(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    work_item_id: int,
    text: str,
    dry_run: bool = False,
) -> dict:
    """Post a comment on an ADO work item and refresh local JSONL store.

    Returns the normalized JSONL record for the work item.
    """
    if dry_run:
        preview = text[:200] + ("…" if len(text) > 200 else "")
        click.echo(f"Would add comment to work item #{work_item_id}:\n{preview}")
        return {}

    body = json.dumps({"text": text})

    result = await run_operation(
        auth_method, OP_ADD_COMMENT,
        org=org, project=project, pat=pat,
        work_item_id=work_item_id,
        body=body,
        content_type="application/json",
    )

    if result.returncode != 0:
        click.echo(f"Error adding comment to #{work_item_id}: {result.stderr}", err=True)
        raise SystemExit(1)

    return await _refetch_and_merge(work_item_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


async def _refetch_and_merge(
    item_id: int,
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str,
    data_dir: Path,
) -> dict:
    """Re-fetch a work item and merge it into the local JSONL store."""
    from ado_search.sync_workitems import fetch_item

    semaphore = asyncio.Semaphore(1)
    record = await fetch_item(
        item_id,
        auth_method=auth_method,
        org=org,
        project=project,
        pat=pat,
        semaphore=semaphore,
    )

    if isinstance(record, str):
        click.echo(f"Warning: Item #{item_id} was modified but re-fetch failed: {record}", err=True)
        return {"id": item_id}

    wi_jsonl = data_dir / "work-items.jsonl"
    finalize_jsonl(wi_jsonl, {record["id"]: record}, key="id", sort_key="id", is_incremental=True)
    return record
