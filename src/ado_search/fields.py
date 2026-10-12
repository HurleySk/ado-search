"""Live read of raw work item field values."""
from __future__ import annotations

from typing import Any, Iterable

import click

from ado_search.auth import OP_GET_FIELDS

DEFAULT_FIELDS = (
    "System.WorkItemType",
    "System.Title",
    "System.State",
    "System.AssignedTo",
    "System.Tags",
    "Microsoft.VSTS.Scheduling.StoryPoints",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
)


def pick_fields(item: dict, names: Iterable[str]) -> dict[str, Any]:
    """Return ``{name: value}`` for each requested field reference name.

    Names match case-insensitively. Values are returned exactly as ADO stores
    them (HTML intact), except identity fields, which become the person's
    uniqueName (email). A field ADO did not return is ``None``.
    """
    fields = {k.casefold(): v for k, v in (item.get("fields") or {}).items()}
    return {name: _plain(fields.get(name.casefold())) for name in names}


def _plain(value: Any) -> Any:
    if isinstance(value, dict) and ("uniqueName" in value or "displayName" in value):
        return value.get("uniqueName") or value.get("displayName")
    return value


async def get_fields(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    work_item_id: int,
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Fetch a work item live from ADO and return its raw field values.

    Reads only: nothing is written to ADO or to the local store.
    """
    from ado_search.runner import fetch_and_parse

    raw = await fetch_and_parse(
        auth_method, OP_GET_FIELDS, f"#{work_item_id}",
        org=org, project=project, pat=pat,
        work_item_id=work_item_id,
    )
    if isinstance(raw, str):
        raise click.ClickException(raw)
    return pick_fields(raw, list(fields) if fields else DEFAULT_FIELDS)
