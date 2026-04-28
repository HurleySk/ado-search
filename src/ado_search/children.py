"""Hierarchical work item queries."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ado_search.db import Database


_TYPE_ORDER = {"Epic": 0, "Feature": 1, "User Story": 2, "Task": 3, "Bug": 4}


@dataclass
class ChildItem:
    id: int
    type: str
    state: str
    title: str
    assigned_to: str
    area: str
    iteration: str
    tags: str
    parent_id: int | None
    depth: int = 1
    closed_date: str | None = None


def query_children(
    db: Database,
    parent_id: int,
    *,
    recursive: bool = False,
    type_filter: str | None = None,
    state_filter: str | None = None,
    include_closed_date: bool = False,
) -> list[ChildItem]:
    rows = db.get_children(
        parent_id, recursive=recursive,
        type_filter=type_filter, state_filter=state_filter,
    )
    items = [
        ChildItem(
            id=r["id"],
            type=r["type"],
            state=r["state"],
            title=r["title"],
            assigned_to=r["assigned_to"] or "",
            area=r["area"] or "",
            iteration=r["iteration"] or "",
            tags=r["tags"] or "",
            parent_id=r["parent_id"],
            depth=r.get("depth", 1),
        )
        for r in rows
    ]
    if include_closed_date and items:
        closed = db.get_closed_dates([it.id for it in items])
        for it in items:
            it.closed_date = closed.get(it.id)
    return items


def _build_tree_lines(
    items: list[ChildItem],
    parent_id: int,
) -> list[str]:
    """Build indented tree lines via DFS."""
    children_of: dict[int | None, list[ChildItem]] = defaultdict(list)
    for it in items:
        children_of[it.parent_id].append(it)

    # Sort children within each group by type order then id
    for kids in children_of.values():
        kids.sort(key=lambda x: (_TYPE_ORDER.get(x.type, 99), x.id))

    lines: list[str] = []

    def _walk(pid: int, indent: int) -> None:
        for it in children_of.get(pid, []):
            prefix = "  " * indent
            closed = f" (closed {it.closed_date})" if it.closed_date else ""
            lines.append(f"{prefix}#{it.id} {it.type} [{it.state}] — {it.title}{closed}")
            _walk(it.id, indent + 1)

    _walk(parent_id, 0)
    return lines


def format_children(
    items: list[ChildItem],
    *,
    fmt: str = "compact",
    parent_id: int,
) -> str:
    if fmt == "json":
        return json.dumps([asdict(it) for it in items], indent=2)

    if fmt == "tree":
        lines = _build_tree_lines(items, parent_id)
        if not lines:
            return ""
        lines.append("")
        lines.append(_summary(items, parent_id))
        return "\n".join(lines)

    # compact (tabular)
    has_closed = any(it.closed_date for it in items)
    lines: list[str] = []
    for it in items:
        closed_col = f"  {it.closed_date or '':<12}" if has_closed else ""
        lines.append(
            f"  #{it.id:<8} {it.type:<14} {it.state:<14} "
            f"{it.title:<45.45} {it.assigned_to:<25.25} "
            f"{it.tags}{closed_col}"
        )
    lines.append("")
    lines.append(_summary(items, parent_id))
    return "\n".join(lines)


def _summary(items: list[ChildItem], parent_id: int) -> str:
    counts: dict[str, int] = {}
    for it in items:
        counts[it.type] = counts.get(it.type, 0) + 1
    parts = [f"{v} {k}{'s' if v != 1 else ''}" for k, v in
             sorted(counts.items(), key=lambda x: _TYPE_ORDER.get(x[0], 99))]
    return f"{len(items)} items under #{parent_id} ({', '.join(parts)})"
