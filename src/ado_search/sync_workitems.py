from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from ado_search.auth import build_command
from ado_search.db import Database
from ado_search.markdown import work_item_to_markdown, extract_work_item_metadata
from ado_search.runner import run_command, CommandResult


WIQL_PAGE_SIZE = 20000


def build_wiql_query(
    *,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
    project: str = "",
    min_id: int = 0,
    max_id: int = 0,
) -> str:
    types_clause = ", ".join(f"'{t}'" for t in work_item_types)
    conditions = [f"[System.WorkItemType] IN ({types_clause})"]

    if project:
        conditions.append(f"[System.TeamProject] = '{project}'")

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

    if min_id > 0:
        conditions.append(f"[System.Id] > {min_id}")

    if max_id > 0:
        conditions.append(f"[System.Id] <= {max_id}")

    where = " AND ".join(conditions)
    return f"SELECT [System.Id] FROM WorkItems WHERE {where} ORDER BY [System.Id] ASC"


async def _fetch_comments(
    work_item_id: int, auth_method: str, org: str, project: str,
) -> list[dict]:
    cmd = build_command(
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
        cmd = build_command(
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


ID_CHUNK_SIZE = 10000  # query in chunks of 10K IDs to stay under the 20K result limit


def _parse_query_result(stdout: str) -> list[int]:
    """Parse work item IDs from az boards query output."""
    if not stdout.strip():
        return []
    data = json.loads(stdout)
    # az CLI returns a flat list; REST API returns {"workItems": [...]}
    if isinstance(data, list):
        items = data
    else:
        items = data.get("workItems", [])
    return [wi["id"] for wi in items]


async def _discover_work_item_ids(
    *,
    auth_method: str,
    org: str,
    project: str,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
) -> list[int]:
    """Discover all work item IDs, paginating by ID range to avoid the 20K WIQL limit."""
    # First, try without chunking (works for incremental syncs and small repos)
    wiql = build_wiql_query(
        work_item_types=work_item_types,
        area_paths=area_paths,
        states=states,
        last_sync=last_sync,
        project=project,
    )
    cmd = build_command("query", auth_method, org=org, project=project, wiql=wiql)
    result = await run_command(cmd)

    if result.returncode == 0:
        ids = _parse_query_result(result.stdout)
        click.echo(f"  Found {len(ids)} work items")
        return ids

    # If we hit the 20K limit, paginate by ID ranges
    if "VS402337" not in result.stderr:
        raise RuntimeError(f"WIQL query failed: {result.stderr}")

    click.echo("  Large dataset detected, paginating by ID range...")

    # Find the ID boundaries first
    # Get min ID
    wiql_min = build_wiql_query(
        work_item_types=work_item_types, area_paths=area_paths,
        states=states, last_sync=last_sync, project=project,
    )
    # Use a small probe to find the first item
    for probe_start in range(0, 200000, ID_CHUNK_SIZE):
        probe_wiql = build_wiql_query(
            work_item_types=work_item_types, area_paths=area_paths,
            states=states, last_sync=last_sync, project=project,
            min_id=probe_start, max_id=probe_start + ID_CHUNK_SIZE,
        )
        cmd = build_command("query", auth_method, org=org, project=project, wiql=probe_wiql)
        probe_result = await run_command(cmd)
        if probe_result.returncode == 0:
            probe_ids = _parse_query_result(probe_result.stdout)
            if probe_ids:
                range_start = min(probe_ids)
                click.echo(f"  First items found at ID ~{range_start}")
                break
    else:
        click.echo("  Could not find any work items in ID range 0-200000")
        return []

    # Now paginate from the discovered start
    all_ids: list[int] = []
    min_id = range_start - 1  # start just before the first known ID
    empty_chunks = 0

    while empty_chunks < 3:
        wiql = build_wiql_query(
            work_item_types=work_item_types,
            area_paths=area_paths,
            states=states,
            last_sync=last_sync,
            project=project,
            min_id=min_id,
            max_id=min_id + ID_CHUNK_SIZE,
        )
        cmd = build_command("query", auth_method, org=org, project=project, wiql=wiql)
        result = await run_command(cmd)

        if result.returncode != 0:
            if "VS402337" in result.stderr:
                click.echo(f"    Chunk {min_id}-{min_id + ID_CHUNK_SIZE} too large, halving...")
                # Halve the chunk to fit under the limit
                half = ID_CHUNK_SIZE // 2
                for sub_start in [min_id, min_id + half]:
                    sub_wiql = build_wiql_query(
                        work_item_types=work_item_types, area_paths=area_paths,
                        states=states, last_sync=last_sync, project=project,
                        min_id=sub_start, max_id=sub_start + half,
                    )
                    sub_cmd = build_command("query", auth_method, org=org, project=project, wiql=sub_wiql)
                    sub_result = await run_command(sub_cmd)
                    if sub_result.returncode == 0:
                        all_ids.extend(_parse_query_result(sub_result.stdout))
                min_id += ID_CHUNK_SIZE
                empty_chunks = 0
                continue
            raise RuntimeError(f"WIQL query failed: {result.stderr}")

        page_ids = _parse_query_result(result.stdout)

        if not page_ids:
            empty_chunks += 1
            min_id += ID_CHUNK_SIZE
            continue

        empty_chunks = 0
        all_ids.extend(page_ids)
        click.echo(f"  Discovered {len(all_ids)} work items so far (IDs {min_id+1}-{min_id + ID_CHUNK_SIZE})...")
        min_id += ID_CHUNK_SIZE

    click.echo(f"  Found {len(all_ids)} total work items")
    return all_ids


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
    item_ids = await _discover_work_item_ids(
        auth_method=auth_method,
        org=org,
        project=project,
        work_item_types=work_item_types,
        area_paths=area_paths,
        states=states,
        last_sync=last_sync,
    )

    click.echo(f"  Found {len(item_ids)} work items to sync")

    if dry_run:
        click.echo(f"Would fetch {len(item_ids)} work items: {item_ids[:20]}...")
        return {"fetched": 0, "errors": 0, "dry_run": True, "would_fetch": len(item_ids)}

    with db.batch():
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
        if not last_sync:
            deleted = detect_deletions(
                remote_ids=set(item_ids),
                db=db,
                data_dir=data_dir,
            )
            if deleted:
                click.echo(f"  Removed {len(deleted)} orphaned items")

    return {"fetched": len(item_ids) - len(errors), "errors": len(errors)}
