from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from ado_search.auth import OP_COMMENTS, OP_QUERY, OP_SHOW
from ado_search.db import Database
from ado_search.runner import SyncResult, fetch_and_parse, run_operation
from ado_search.jsonl import merge_jsonl, read_jsonl, write_jsonl
from ado_search.sync_common import prepare_work_item


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
    work_item_id: int, auth_method: str, org: str, project: str, pat: str = "",
) -> list[dict]:
    data = await fetch_and_parse(
        auth_method, OP_COMMENTS, f"comments for #{work_item_id}",
        org=org, project=project, pat=pat, work_item_id=work_item_id,
    )
    if isinstance(data, str):
        return []
    return data.get("comments", [])


async def _fetch_item(
    item_id: int,
    *,
    auth_method: str,
    org: str,
    project: str,
    pat: str = "",
    semaphore: asyncio.Semaphore,
    include_comments: bool = False,
) -> dict | str:
    """Fetch a single work item. Returns JSONL record dict or error string."""
    async with semaphore:
        raw = await fetch_and_parse(
            auth_method, OP_SHOW, f"#{item_id}",
            org=org, project=project, pat=pat, work_item_id=item_id,
        )
        if isinstance(raw, str):
            return raw

    comments = []
    if include_comments:
        async with semaphore:
            comments = await _fetch_comments(item_id, auth_method, org, project, pat=pat)

    return prepare_work_item(raw, comments=comments)


ID_CHUNK_SIZE = 10000  # query in chunks of 10K IDs to stay under the 20K result limit


def _parse_query_result(stdout: str) -> list[int]:
    """Parse work item IDs from az boards query output."""
    if not stdout.strip():
        return []
    data = json.loads(stdout)
    # ConvertTo-Json can double-serialize — unwrap if we got a string
    if isinstance(data, str):
        data = json.loads(data)
    # az CLI returns a flat list; REST API returns {"workItems": [...]}
    if isinstance(data, list):
        items = data
    else:
        items = data.get("workItems", [])
    return [wi["id"] for wi in items]


async def _run_wiql(
    auth_method: str, org: str, project: str, pat: str, **query_kwargs,
) -> tuple[int, list[int] | str]:
    """Run a WIQL query. Returns (0, ids) on success or (returncode, stderr) on failure."""
    wiql = build_wiql_query(**query_kwargs, project=project)
    result = await run_operation(auth_method, OP_QUERY, org=org, project=project, pat=pat, wiql=wiql)
    if result.returncode != 0:
        return result.returncode, result.stderr
    return 0, _parse_query_result(result.stdout)


async def _find_id_range_start(
    auth_method: str, org: str, project: str, pat: str, **query_kwargs,
) -> int | None:
    """Probe ID ranges to find the first work items. Returns min ID or None."""
    for probe_start in range(0, 200000, ID_CHUNK_SIZE):
        rc, data = await _run_wiql(
            auth_method, org, project, pat,
            **query_kwargs, min_id=probe_start, max_id=probe_start + ID_CHUNK_SIZE,
        )
        if rc == 0 and data:
            range_start = min(data)
            click.echo(f"  First items found at ID ~{range_start}")
            return range_start
    return None


async def _paginate_by_id_range(
    auth_method: str, org: str, project: str, pat: str,
    range_start: int, **query_kwargs,
) -> list[int]:
    """Walk ID ranges from range_start, halving chunks that exceed the 20K limit."""
    all_ids: list[int] = []
    min_id = range_start - 1
    empty_chunks = 0

    while empty_chunks < 3:
        rc, data = await _run_wiql(
            auth_method, org, project, pat,
            **query_kwargs, min_id=min_id, max_id=min_id + ID_CHUNK_SIZE,
        )

        if rc != 0:
            stderr = data  # _run_wiql returns stderr on failure
            if "VS402337" in stderr:
                click.echo(f"    Chunk {min_id}-{min_id + ID_CHUNK_SIZE} too large, halving...")
                half = ID_CHUNK_SIZE // 2
                for sub_start in [min_id, min_id + half]:
                    sub_rc, sub_data = await _run_wiql(
                        auth_method, org, project, pat,
                        **query_kwargs, min_id=sub_start, max_id=sub_start + half,
                    )
                    if sub_rc == 0:
                        all_ids.extend(sub_data)
                min_id += ID_CHUNK_SIZE
                empty_chunks = 0
                continue
            raise RuntimeError(f"WIQL query failed: {stderr}")

        if not data:
            empty_chunks += 1
            min_id += ID_CHUNK_SIZE
            continue

        empty_chunks = 0
        all_ids.extend(data)
        click.echo(f"  Discovered {len(all_ids)} work items so far (IDs {min_id+1}-{min_id + ID_CHUNK_SIZE})...")
        min_id += ID_CHUNK_SIZE

    return all_ids


async def _discover_work_item_ids(
    *,
    auth_method: str,
    org: str,
    project: str,
    pat: str = "",
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
) -> list[int]:
    """Discover all work item IDs, paginating by ID range to avoid the 20K WIQL limit."""
    query_kwargs = dict(
        work_item_types=work_item_types, area_paths=area_paths,
        states=states, last_sync=last_sync,
    )

    # Try without chunking (works for incremental syncs and small repos)
    rc, data = await _run_wiql(auth_method, org, project, pat, **query_kwargs)
    if rc == 0:
        click.echo(f"  Found {len(data)} work items")
        return data

    if "VS402337" not in data:  # data is stderr on failure
        raise RuntimeError(f"WIQL query failed: {data}")

    click.echo("  Large dataset detected, paginating by ID range...")

    range_start = await _find_id_range_start(auth_method, org, project, pat, **query_kwargs)
    if range_start is None:
        click.echo("  Could not find any work items in ID range 0-200000")
        return []

    all_ids = await _paginate_by_id_range(
        auth_method, org, project, pat, range_start, **query_kwargs,
    )
    click.echo(f"  Found {len(all_ids)} total work items")
    return all_ids


async def sync_work_items(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    db: Database,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
    max_concurrent: int = 5,
    include_comments: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    # Try OData analytics fast path
    from ado_search.sync_odata import sync_via_odata

    click.echo("  Trying OData analytics (fast path)...")
    odata_result = await sync_via_odata(
        org=org, project=project, auth_method=auth_method, pat=pat,
        data_dir=data_dir, db=db,
        work_item_types=work_item_types, area_paths=area_paths,
        states=states, last_sync=last_sync, dry_run=dry_run,
    )

    if odata_result is not None:
        return odata_result

    click.echo("  OData not available, using WIQL fallback...")

    item_ids = await _discover_work_item_ids(
        auth_method=auth_method,
        org=org,
        project=project,
        pat=pat,
        work_item_types=work_item_types,
        area_paths=area_paths,
        states=states,
        last_sync=last_sync,
    )

    click.echo(f"  Found {len(item_ids)} work items to sync")

    if dry_run:
        click.echo(f"Would fetch {len(item_ids)} work items: {item_ids[:20]}...")
        return {"fetched": 0, "errors": 0, "dry_run": True, "would_fetch": len(item_ids)}

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _fetch_item(
            item_id,
            auth_method=auth_method,
            org=org,
            project=project,
            pat=pat,
            semaphore=semaphore,
            include_comments=include_comments,
        )
        for item_id in item_ids
    ]

    results = await asyncio.gather(*tasks)

    fetched_records: dict[int, dict] = {}
    errors: list[str] = []
    for r in results:
        if isinstance(r, str):
            errors.append(r)
            click.echo(f"  Warning: {r}", err=True)
        else:
            fetched_records[r["id"]] = r

    # Write JSONL
    wi_jsonl = data_dir / "work-items.jsonl"

    if last_sync:
        all_items = merge_jsonl(wi_jsonl, fetched_records, key="id")
    else:
        # Full sync — orphan detection via JSONL comparison
        existing = read_jsonl(wi_jsonl, key="id")
        orphan_ids = set(existing.keys()) - set(fetched_records.keys())
        if orphan_ids:
            click.echo(f"  Removing {len(orphan_ids)} orphaned items")
        all_items = fetched_records

    write_jsonl(wi_jsonl, all_items, sort_key="id")

    # Rebuild DB index
    wiki_jsonl = data_dir / "wiki-pages.jsonl"
    db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)

    return {"fetched": len(fetched_records), "errors": len(errors)}
