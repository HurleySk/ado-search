from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from ado_search.auth import build_az_cli_command, build_powershell_command
from ado_search.db import Database
from ado_search.markdown import work_item_to_markdown, extract_work_item_metadata
from ado_search.runner import run_command, CommandResult


def build_wiql_query(
    *,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
) -> str:
    types_clause = ", ".join(f"'{t}'" for t in work_item_types)
    conditions = [f"[System.WorkItemType] IN ({types_clause})"]

    if last_sync:
        conditions.append(f"[System.ChangedDate] > '{last_sync}'")

    if area_paths:
        area_clauses = " OR ".join(
            f"[System.AreaPath] UNDER '{a}'" for a in area_paths
        )
        conditions.append(f"({area_clauses})")

    if states:
        state_clause = ", ".join(f"'{s}'" for s in states)
        conditions.append(f"[System.State] IN ({state_clause})")

    where = " AND ".join(conditions)
    return f"SELECT [System.Id] FROM WorkItems WHERE {where} ORDER BY [System.ChangedDate] DESC"


def _build_command(operation: str, auth_method: str, **kwargs) -> list[str]:
    if auth_method == "az-cli":
        return build_az_cli_command(operation, **kwargs)
    return build_powershell_command(operation, **kwargs)


async def _fetch_comments(
    work_item_id: int, auth_method: str, org: str, project: str,
) -> list[dict]:
    cmd = _build_command(
        "comments", auth_method, org=org, project=project, work_item_id=work_item_id,
    )
    result = await run_command(cmd)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        return data.get("comments", [])
    except (json.JSONDecodeError, KeyError):
        return []


async def _fetch_and_write_item(
    item_id: int,
    *,
    auth_method: str,
    org: str,
    project: str,
    data_dir: Path,
    db: Database,
    semaphore: asyncio.Semaphore,
) -> str | None:
    """Fetch a single work item and write it. Returns error message or None."""
    async with semaphore:
        cmd = _build_command(
            "show", auth_method, org=org, project=project, work_item_id=item_id,
        )
        result = await run_command(cmd)
        if result.returncode != 0:
            return f"Failed to fetch #{item_id}: {result.stderr}"

        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"Invalid JSON for #{item_id}"

        comments = await _fetch_comments(item_id, auth_method, org, project)
        md = work_item_to_markdown(raw, comments=comments)
        meta = extract_work_item_metadata(raw)

        md_path = data_dir / "work-items" / f"{item_id}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")

        db.upsert_work_item(meta)

        return None


def detect_deletions(
    *,
    remote_ids: set[int],
    db: Database,
    data_dir: Path,
) -> list[int]:
    """Remove local items that no longer exist in ADO. Returns deleted IDs."""
    local_ids = set(db.get_all_work_item_ids())
    orphans = local_ids - remote_ids

    for item_id in orphans:
        md_path = data_dir / "work-items" / f"{item_id}.md"
        if md_path.exists():
            md_path.unlink()
        db.delete_work_item(item_id)

    return list(orphans)


async def sync_work_items(
    *,
    org: str,
    project: str,
    auth_method: str,
    data_dir: Path,
    db: Database,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
    max_concurrent: int = 5,
    dry_run: bool = False,
) -> dict:
    wiql = build_wiql_query(
        work_item_types=work_item_types,
        area_paths=area_paths,
        states=states,
        last_sync=last_sync,
    )

    cmd = _build_command("query", auth_method, org=org, project=project, wiql=wiql)
    result = await run_command(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"WIQL query failed: {result.stderr}")

    data = json.loads(result.stdout)
    item_ids = [wi["id"] for wi in data.get("workItems", [])]

    if dry_run:
        click.echo(f"Would fetch {len(item_ids)} work items: {item_ids[:20]}...")
        return {"fetched": 0, "errors": 0, "dry_run": True, "would_fetch": len(item_ids)}

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _fetch_and_write_item(
            item_id,
            auth_method=auth_method,
            org=org,
            project=project,
            data_dir=data_dir,
            db=db,
            semaphore=semaphore,
        )
        for item_id in item_ids
    ]

    errors: list[str] = []
    results = await asyncio.gather(*tasks)
    for err in results:
        if err is not None:
            errors.append(err)
            click.echo(f"  Warning: {err}", err=True)

    # Detect deletions only on full sync (incremental doesn't have all IDs)
    if not dry_run and not last_sync:
        deleted = detect_deletions(
            remote_ids=set(item_ids),
            db=db,
            data_dir=data_dir,
        )
        if deleted:
            click.echo(f"  Removed {len(deleted)} orphaned items")

    return {"fetched": len(item_ids) - len(errors), "errors": len(errors)}
