from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import quote, urlparse

import click

from ado_search.auth import build_command
from ado_search.db import Database
from ado_search.markdown import work_item_to_markdown, extract_work_item_metadata
from ado_search.runner import run_command

ODATA_PAGE_SIZE = 5000
ODATA_BASE = "https://analytics.dev.azure.com"

ODATA_SELECT = ",".join([
    "WorkItemId", "Title", "WorkItemType", "State", "Priority",
    "TagNames", "CreatedDate", "ChangedDate",
    "Description", "Microsoft_VSTS_Common_AcceptanceCriteria",
    "ParentWorkItemId",
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

    base_url = f"{ODATA_BASE}/{org_name}/{project}/_odata/v4.0-preview/WorkItems"

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
        _filter_safe_chars = "',/() "
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
        },
    }


async def sync_via_odata(
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
    dry_run: bool = False,
) -> dict | None:
    """Sync work items via OData analytics. Returns stats dict or None if OData unavailable."""
    url = build_odata_url(
        org, project,
        work_item_types=work_item_types,
        area_paths=area_paths,
        states=states,
        last_sync=last_sync,
    )

    # Probe first page to check if OData is available
    cmd = build_command("odata-query", auth_method, org=org, project=project, url=url)
    result = await run_command(cmd, retries=1)  # Only 1 attempt for availability check

    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        if any(s in stderr_lower for s in ["403", "401", "forbidden", "unauthorized", "not available"]):
            return None  # OData not available — signal fallback
        raise RuntimeError(f"OData query failed: {result.stderr}")

    # Parse first page
    if not result.stdout.strip():
        return {"fetched": 0, "errors": 0, "fetched_ids": set()}

    data = json.loads(result.stdout)
    all_items = data.get("value", [])
    next_link = data.get("@odata.nextLink")

    # Follow pagination
    while next_link:
        cmd = build_command("odata-query", auth_method, org=org, project=project, url=next_link)
        result = await run_command(cmd)
        if result.returncode != 0:
            click.echo(f"  Warning: OData pagination failed: {result.stderr}", err=True)
            break
        page_data = json.loads(result.stdout)
        all_items.extend(page_data.get("value", []))
        next_link = page_data.get("@odata.nextLink")
        click.echo(f"  Fetched {len(all_items)} items via OData...")

    click.echo(f"  OData returned {len(all_items)} work items")

    if dry_run:
        ids = [item.get("WorkItemId", 0) for item in all_items]
        click.echo(f"Would process {len(all_items)} work items: {ids[:20]}...")
        return {"fetched": 0, "errors": 0, "dry_run": True, "would_fetch": len(all_items), "fetched_ids": set()}

    # Process items
    fetched = 0
    errors = 0
    fetched_ids: set[int] = set()

    with db.batch():
        for item in all_items:
            try:
                ado_format = odata_to_ado_format(item)
                item_id = ado_format["id"]
                fetched_ids.add(item_id)

                md = work_item_to_markdown(ado_format, comments=None)
                meta = extract_work_item_metadata(ado_format)

                md_path = data_dir / "work-items" / f"{item_id}.md"
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(md, encoding="utf-8")

                db.upsert_work_item(meta)
                fetched += 1
            except Exception as e:
                click.echo(f"  Warning: Failed to process item: {e}", err=True)
                errors += 1

    return {"fetched": fetched, "errors": errors, "fetched_ids": fetched_ids}
