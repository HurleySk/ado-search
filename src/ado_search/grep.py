"""Regex pattern matching across work item fields."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ado_search.jsonl import iter_jsonl


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


def _build_context(text: str, start: int, end: int, context_chars: int) -> str:
    """Build a context snippet around a match span."""
    ctx_start = max(0, start - context_chars)
    ctx_end = min(len(text), end + context_chars)
    snippet = text[ctx_start:ctx_end]
    prefix = "..." if ctx_start > 0 else ""
    suffix = "..." if ctx_end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def match_field(
    pattern: re.Pattern,
    field_name: str,
    text: str,
    *,
    context_chars: int = 60,
    comment_author: str | None = None,
    comment_date: str | None = None,
) -> list[FieldMatch]:
    """Apply a compiled regex to a text string and return all matches."""
    results: list[FieldMatch] = []
    for m in pattern.finditer(text):
        results.append(FieldMatch(
            field=field_name,
            text_matched=m.group(),
            context=_build_context(text, m.start(), m.end(), context_chars),
            offset=m.start(),
            comment_author=comment_author,
            comment_date=comment_date,
        ))
    return results


DEFAULT_FIELDS = ["title", "description", "comments"]


def grep_work_items(
    *,
    jsonl_path: Path,
    pattern: re.Pattern,
    fields: list[str] | None = None,
    candidate_ids: set[int] | None = None,
    context_chars: int = 60,
    limit: int = 50,
) -> tuple[list[GrepResult], list[str]]:
    """Scan JSONL records for regex matches across specified fields.

    Returns (results, warnings).
    """
    if fields is None:
        fields = list(DEFAULT_FIELDS)

    results: list[GrepResult] = []
    warnings: list[str] = []
    comments_warned = False

    for item in iter_jsonl(jsonl_path):
        item_id = item.get("id")
        if candidate_ids is not None and item_id not in candidate_ids:
            continue

        item_matches: list[FieldMatch] = []

        for field_name in fields:
            if field_name == "comments" and "comments" not in item and not comments_warned:
                warnings.append(
                    "Comments not synced — run `ado-search sync --include-comments` for full results"
                )
                comments_warned = True

            for text, author, date in extract_field_text(item, field_name):
                item_matches.extend(match_field(
                    pattern, field_name, text,
                    context_chars=context_chars,
                    comment_author=author,
                    comment_date=date,
                ))

        if item_matches:
            results.append(GrepResult(
                item_id=item_id,
                title=item.get("title", ""),
                item_type=item.get("type", ""),
                state=item.get("state", ""),
                matches=item_matches,
            ))
            if len(results) >= limit:
                break

    return results, warnings
