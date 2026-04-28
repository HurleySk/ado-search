from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import quote, urlparse

import click

from ado_search.auth import OP_ODATA_QUERY
from ado_search.runner import SyncResult, run_operation
from ado_search.sync_common import finalize_jsonl, prepare_work_item

ODATA_PAGE_SIZE = 5000
ODATA_BASE = "https://analytics.dev.azure.com"

ODATA_SELECT = ",".join([
    "WorkItemId", "Title", "WorkItemType", "State", "Priority",
    "TagNames", "CreatedDate", "ChangedDate",
    "Description", "Microsoft_VSTS_Common_AcceptanceCriteria",
    "ParentWorkItemId", "StoryPoints",
    "Microsoft_VSTS_Common_ClosedDate",
])

ODATA_EXPAND = ",".join([
    "Area($select=AreaPath)",
    "Iteration($select=IterationPath)",
    "AssignedTo($select=UniqueName)",
])


def build_odata_url(
    org: str,
    project: str,
    *,
    work_item_types: list[str],
    area_paths: list[str],
    states: list[str],
    last_sync: str,
    top: int = ODATA_PAGE_SIZE,
    skip: int = 0,
) -> str:
    """Build an OData analytics URL for querying WorkItems."""
    # Extract org name from URL (e.g., "pcxhub-acms" from "https://dev.azure.com/pcxhub-acms")
    parsed = urlparse(org)
    org_name = parsed.path.lstrip("/")

    base_url = f"{ODATA_BASE}/{org_name}/{quote(project, safe='')}/_odata/v4.0-preview/WorkItems"

    # Build filter clauses
    filter_parts: list[str] = []

    if work_item_types:
        types_list = ",".join(f"'{t}'" for t in work_item_types)
        filter_parts.append(f"WorkItemType in ({types_list})")

    if area_paths:
        area_clauses = " or ".join(
            f"startswith(Area/AreaPath, '{p}')" for p in area_paths
        )
        filter_parts.append(f"({area_clauses})")

    if states:
        states_list = ",".join(f"'{s}'" for s in states)
        filter_parts.append(f"State in ({states_list})")

    if last_sync:
        filter_parts.append(f"ChangedDate gt {last_sync}")

    filter_str = " and ".join(filter_parts)

    # Build query string manually so we control encoding
    params: list[str] = []
    params.append(f"$select={quote(ODATA_SELECT, safe=',')}")
    params.append(f"$expand={quote(ODATA_EXPAND, safe=',$()/')}")
    params.append(f"$top={top}")
    params.append(f"$skip={skip}")
    if filter_str:
        _filter_safe_chars = "',/()"
        params.append(f"$filter={quote(filter_str, safe=_filter_safe_chars)}")

    return base_url + "?" + "&".join(params)


def odata_to_ado_format(odata_item: dict) -> dict:
    """Transform OData analytics response item to ADO REST API format."""
    assigned_to = odata_item.get("AssignedTo")
    if assigned_to and isinstance(assigned_to, dict):
        assigned_field = {"uniqueName": assigned_to.get("UniqueName", "")}
    else:
        assigned_field = ""

    # OData TagNames is comma-separated; ADO uses semicolon-separated
    tags = odata_item.get("TagNames", "") or ""
    if tags:
        tags = "; ".join(t.strip() for t in tags.split(",") if t.strip())

    return {
        "id": odata_item["WorkItemId"],
        "fields": {
            "System.Title": odata_item.get("Title", ""),
            "System.WorkItemType": odata_item.get("WorkItemType", ""),
            "System.State": odata_item.get("State", ""),
            "System.AreaPath": (odata_item.get("Area") or {}).get("AreaPath", ""),
            "System.IterationPath": (odata_item.get("Iteration") or {}).get("IterationPath", ""),
            "System.AssignedTo": assigned_field,
            "System.Tags": tags,
            "Microsoft.VSTS.Common.Priority": odata_item.get("Priority"),
            "System.Parent": odata_item.get("ParentWorkItemId"),
            "System.CreatedDate": odata_item.get("CreatedDate", ""),
            "System.ChangedDate": odata_item.get("ChangedDate", ""),
            "System.Description": odata_item.get("Description", "") or "",
            "Microsoft.VSTS.Common.AcceptanceCriteria": odata_item.get("Microsoft_VSTS_Common_AcceptanceCriteria", "") or "",
            "Microsoft.VSTS.Scheduling.StoryPoints": odata_item.get("StoryPoints"),
            "Microsoft.VSTS.Common.ClosedDate": odata_item.get("Microsoft_VSTS_Common_ClosedDate", ""),
        },
    }


async def sync_via_odata(
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
    dry_run: bool = False,
) -> SyncResult | None:
    """Sync work items via OData analytics. Returns stats dict or None if OData unavailable."""
    url = build_odata_url(
        org, project,
        work_item_types=work_item_types,
        area_paths=area_paths,
        states=states,
        last_sync=last_sync,
    )

    # Probe first page to check if OData is available
    result = await run_operation(
        auth_method, OP_ODATA_QUERY, org=org, project=project, pat=pat, url=url, retries=1,
    )

    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        if any(s in stderr_lower for s in ["403", "401", "forbidden", "unauthorized", "not available"]):
            return None  # OData not available — signal fallback
        raise RuntimeError(f"OData query failed: {result.stderr}")

    # Parse first page
    if not result.stdout.strip():
        return {"fetched": 0, "errors": 0}

    data = result.parse_json()
    next_link = data.get("@odata.nextLink")

    if dry_run:
        all_ids = [item.get("WorkItemId", 0) for item in data.get("value", [])]
        while next_link:
            result = await run_operation(
                auth_method, OP_ODATA_QUERY, org=org, project=project, pat=pat, url=next_link,
            )
            if result.returncode != 0:
                break
            page_data = result.parse_json()
            all_ids.extend(item.get("WorkItemId", 0) for item in page_data.get("value", []))
            next_link = page_data.get("@odata.nextLink")
        click.echo(f"Would process {len(all_ids)} work items: {all_ids[:20]}...")
        return {"fetched": 0, "errors": 0, "dry_run": True, "would_fetch": len(all_ids)}

    # Process items as each page arrives (reduces peak memory)
    fetched = 0
    errors = 0
    fetched_records: dict[int, dict] = {}

    def _process_page(items: list[dict]) -> None:
        nonlocal fetched, errors
        for item in items:
            try:
                ado_format = odata_to_ado_format(item)
                record = prepare_work_item(ado_format, comments=None)
                fetched_records[record["id"]] = record
                fetched += 1
            except Exception as e:
                click.echo(f"  Warning: Failed to process item: {e}", err=True)
                errors += 1

    _process_page(data.get("value", []))

    while next_link:
        result = await run_operation(
            auth_method, OP_ODATA_QUERY, org=org, project=project, pat=pat, url=next_link,
        )
        if result.returncode != 0:
            click.echo(f"  Warning: OData pagination failed: {result.stderr}", err=True)
            break
        page_data = result.parse_json()
        _process_page(page_data.get("value", []))
        next_link = page_data.get("@odata.nextLink")
        click.echo(f"  Processed {fetched} items via OData...")

    click.echo(f"  OData: {fetched} work items processed")

    wi_jsonl = data_dir / "work-items.jsonl"
    finalize_jsonl(
        wi_jsonl, fetched_records,
        key="id", sort_key="id", is_incremental=bool(last_sync),
    )

    return {"fetched": fetched, "errors": errors}
