from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from ado_search.auth import OP_COMMENTS, OP_QUERY, OP_SHOW, OP_UPDATES
from ado_search.runner import SyncResult, fetch_and_parse, run_operation
from ado_search.sync_common import extract_state_history, finalize_jsonl, prepare_work_item, split_results


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


async def _fetch_updates(
    work_item_id: int, auth_method: str, org: str, project: str, pat: str = "",
) -> list[dict]:
    data = await fetch_and_parse(
        auth_method, OP_UPDATES, f"updates for #{work_item_id}",
        org=org, project=project, pat=pat, work_item_id=work_item_id,
    )
    if isinstance(data, str):
        return []
    return data.get("value", [])


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
    include_attachments: bool = False,
    data_dir: Path | None = None,
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
    updates = []
    async with semaphore:
        coros = [_fetch_updates(item_id, auth_method, org, project, pat=pat)]
        if include_comments:
            coros.append(_fetch_comments(item_id, auth_method, org, project, pat=pat))
        results = await asyncio.gather(*coros)
        updates = results[0]
        if include_comments:
            comments = results[1]

    # Attachment and inline image handling (before prepare_work_item strips HTML)
    att_metadata: list[dict] = []
    img_metadata: list[dict] = []
    if include_attachments and data_dir is not None:
        from ado_search.attachments import (
            extract_attachments, extract_inline_images, rewrite_inline_images,
            download_work_item_attachments, download_work_item_inline_images,
        )

        # Extract attachment metadata from relations
        file_attachments = extract_attachments(raw)

        # Extract inline images from raw HTML fields (before stripping)
        fields = raw.get("fields", {})
        desc_html = fields.get("System.Description", "") or ""
        ac_html = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""
        desc_images = extract_inline_images(desc_html)
        ac_images = extract_inline_images(ac_html)

        # Download attachments and inline images
        att_metadata = await download_work_item_attachments(
            item_id, file_attachments,
            data_dir=data_dir, auth_method=auth_method, org=org, pat=pat,
            semaphore=semaphore,
        )
        desc_map, desc_img_meta = await download_work_item_inline_images(
            item_id, desc_images,
            data_dir=data_dir, auth_method=auth_method, org=org, pat=pat,
            semaphore=semaphore, source_field="description",
        )
        ac_map, ac_img_meta = await download_work_item_inline_images(
            item_id, ac_images,
            data_dir=data_dir, auth_method=auth_method, org=org, pat=pat,
            semaphore=semaphore, source_field="acceptance_criteria",
        )
        img_metadata = desc_img_meta + ac_img_meta

        # Rewrite inline image URLs in raw HTML before prepare_work_item strips it
        if desc_map:
            fields["System.Description"] = rewrite_inline_images(desc_html, desc_map)
        if ac_map:
            fields["Microsoft.VSTS.Common.AcceptanceCriteria"] = rewrite_inline_images(ac_html, ac_map)

    record = prepare_work_item(
        raw, comments=comments,
        attachments=att_metadata, inline_images=img_metadata,
    )
    record["state_history"] = extract_state_history(updates)
    return record


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


async def fetch_specific_work_items(
    *,
    item_ids: list[int],
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    max_concurrent: int = 5,
    dry_run: bool = False,
    include_attachments: bool = False,
) -> SyncResult:
    """Fetch specific work items by ID and merge them into the local store."""
    if dry_run:
        click.echo(f"Would fetch {len(item_ids)} work items: {item_ids}")
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
            include_attachments=include_attachments,
            data_dir=data_dir,
        )
        for item_id in item_ids
    ]

    results = await asyncio.gather(*tasks)

    fetched_records, errors = split_results(results, key="id")
    for e in errors:
        click.echo(f"  Warning: {e}", err=True)

    wi_jsonl = data_dir / "work-items.jsonl"
    finalize_jsonl(
        wi_jsonl, fetched_records,
        key="id", sort_key="id", is_incremental=True,
    )

    return {"fetched": len(fetched_records), "errors": len(errors)}


async def sync_work_items(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
    max_concurrent: int = 5,
    include_comments: bool = False,
    include_attachments: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    # OData doesn't include relations (attachments), so skip when attachments enabled
    if not include_attachments:
        from ado_search.sync_odata import sync_via_odata

        click.echo("  Trying OData analytics (fast path)...")
        odata_result = await sync_via_odata(
            org=org, project=project, auth_method=auth_method, pat=pat,
            data_dir=data_dir,
            work_item_types=work_item_types, area_paths=area_paths,
            states=states, last_sync=last_sync, dry_run=dry_run,
        )

        if odata_result is not None:
            return odata_result

        click.echo("  OData not available, using WIQL fallback...")
    else:
        click.echo("  Attachments enabled, using WIQL (REST) path...")

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
            include_attachments=include_attachments,
            data_dir=data_dir,
        )
        for item_id in item_ids
    ]

    results = await asyncio.gather(*tasks)

    fetched_records, errors = split_results(results, key="id")
    for e in errors:
        click.echo(f"  Warning: {e}", err=True)

    wi_jsonl = data_dir / "work-items.jsonl"
    finalize_jsonl(
        wi_jsonl, fetched_records,
        key="id", sort_key="id", is_incremental=bool(last_sync),
    )

    return {"fetched": len(fetched_records), "errors": len(errors)}
