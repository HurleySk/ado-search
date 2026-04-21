"""Regex pattern matching across work item fields."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldMatch:
    """A single regex match within a work item field."""
    field: str
    text_matched: str
    context: str
    offset: int
    comment_author: str | None = None
    comment_date: str | None = None


@dataclass
class GrepResult:
    """All matches for a single work item."""
    item_id: int
    title: str
    item_type: str
    state: str
    matches: list[FieldMatch] = field(default_factory=list)


_SIMPLE_FIELDS = {
    "title", "description", "acceptance_criteria", "tags",
    "assigned_to", "area", "iteration",
}


def extract_field_text(
    item: dict, field_name: str,
) -> list[tuple[str, str | None, str | None]]:
    """Extract searchable text from a work item field.

    Returns a list of (text, comment_author_or_None, comment_date_or_None).
    """
    if field_name in _SIMPLE_FIELDS:
        val = item.get(field_name, "")
        return [(val, None, None)] if val else []

    if field_name == "comments":
        comments = item.get("comments")
        if not comments:
            return []
        return [
            (c.get("text", ""), c.get("author"), c.get("date"))
            for c in comments
        ]

    if field_name == "state_history":
        history = item.get("state_history")
        if not history:
            return []
        return [
            (f"{h.get('from', '')} -> {h.get('to', '')} by {h.get('by', '')} on {h.get('date', '')}",
             None, None)
            for h in history
        ]

    return []
