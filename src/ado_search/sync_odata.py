from __future__ import annotations

from urllib.parse import quote, urlparse

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
