from __future__ import annotations

import re
from html.parser import HTMLParser


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "div", "br", "li") and self._parts:
            self._parts.append("\n")
        if tag == "img":
            attrs_dict = dict(attrs)
            src = attrs_dict.get("src", "")
            if src and src.startswith("attachments/"):
                self._parts.append(f"[image: {src}]")

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


SNIPPET_LENGTH = 500


def make_snippet(text: str) -> str:
    """Create a description snippet from text."""
    return (text or "")[:SNIPPET_LENGTH]


def strip_html(html: str) -> str:
    if not html:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def extract_work_item_metadata(raw: dict) -> dict:
    fields = raw.get("fields", {})
    assigned = fields.get("System.AssignedTo")
    assigned_to = ""
    if isinstance(assigned, dict):
        assigned_to = assigned.get("uniqueName", assigned.get("displayName", ""))
    elif isinstance(assigned, str):
        assigned_to = assigned

    tags_raw = fields.get("System.Tags", "")
    tags = ",".join(t.strip() for t in tags_raw.split(";") if t.strip())

    description = strip_html(fields.get("System.Description", ""))
    snippet = make_snippet(description)

    created_raw = fields.get("System.CreatedDate", "")
    updated_raw = fields.get("System.ChangedDate", "")

    sp = fields.get("Microsoft.VSTS.Scheduling.StoryPoints")
    return {
        "id": raw["id"],
        "title": fields.get("System.Title", ""),
        "type": fields.get("System.WorkItemType", ""),
        "state": fields.get("System.State", ""),
        "area": fields.get("System.AreaPath", ""),
        "iteration": fields.get("System.IterationPath", ""),
        "assigned_to": assigned_to,
        "tags": tags,
        "priority": fields.get("Microsoft.VSTS.Common.Priority"),
        "story_points": sp if sp is not None else fields.get("Microsoft.VSTS.Scheduling.Effort"),
        "parent_id": fields.get("System.Parent"),
        "closed_date": (fields.get("Microsoft.VSTS.Common.ClosedDate") or "")[:10] or "",
        "created": created_raw[:10] if created_raw else "",
        "updated": updated_raw[:10] if updated_raw else "",
        "description_snippet": snippet,
        "description_full": description,
        "acceptance_criteria": strip_html(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")),
    }


def _format_size(size: int) -> str:
    """Format byte count as human-readable size."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def work_item_to_markdown(
    raw: dict,
    *,
    comments: list[dict] | None = None,
    meta: dict | None = None,
    attachments: list[dict] | None = None,
    inline_images: list[dict] | None = None,
) -> str:
    if meta is None:
        meta = extract_work_item_metadata(raw)

    lines = [
        "---",
        f"id: {meta['id']}",
        f"title: {meta['title']}",
        f"type: {meta['type']}",
        f"state: {meta['state']}",
        f"area: {meta['area']}",
        f"iteration: {meta['iteration']}",
        f"assigned_to: {meta['assigned_to']}",
        f"tags: [{meta['tags']}]",
        f"priority: {meta['priority']}",
        f"parent_id: {meta['parent_id']}",
        f"created: {meta['created']}",
        f"updated: {meta['updated']}",
        "---",
        "",
    ]

    if meta["description_full"]:
        lines.append("## Description")
        lines.append(meta["description_full"])
        lines.append("")

    if meta["acceptance_criteria"]:
        lines.append("## Acceptance Criteria")
        lines.append(meta["acceptance_criteria"])
        lines.append("")

    if comments:
        lines.append("## Comments")
        for c in comments:
            author = c.get("createdBy", {}).get("displayName", "Unknown")
            date = c.get("createdDate", "")[:10]
            text = strip_html(c.get("text", ""))
            lines.append(f"### {date} — {author}")
            lines.append(text)
            lines.append("")

    if attachments:
        lines.append("## Attachments")
        for a in attachments:
            size_str = _format_size(a.get("size", 0)) if a.get("size") else ""
            path = a.get("local_path", "")
            name = a.get("name", "unknown")
            if size_str:
                lines.append(f"- {name} ({size_str}) \u2192 {path}")
            else:
                lines.append(f"- {name} \u2192 {path}")
        lines.append("")

    if inline_images:
        lines.append("## Inline Images")
        for img in inline_images:
            field = img.get("source_field", "")
            path = img.get("local_path", "")
            label = f"{field} image" if field else "image"
            lines.append(f"- {label}: {path}")
        lines.append("")

    return "\n".join(lines)


def wiki_page_to_markdown(title: str, content: str) -> str:
    if content.startswith("# "):
        return content
    return f"# {title}\n\n{content}"
