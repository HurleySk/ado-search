from __future__ import annotations

import json
from pathlib import Path

from ado_search.db import Database


def search(
    db: Database,
    query: str,
    *,
    data_dir: Path,
    type_filter: str | None = None,
    state_filter: str | None = None,
    area_filter: str | None = None,
    assigned_to_filter: str | None = None,
    tag_filter: str | None = None,
    limit: int = 20,
) -> list[dict]:
    results: list[dict] = []

    wi_results = db.search_work_items(
        query,
        type_filter=type_filter,
        state_filter=state_filter,
        area_filter=area_filter,
        assigned_to_filter=assigned_to_filter,
        tag_filter=tag_filter,
        limit=limit,
    )
    for r in wi_results:
        results.append({
            "id": r["id"],
            "title": r["title"],
            "type": r["type"],
            "state": r["state"],
            "file_path": f"work-items/{r['id']}.md",
            "source": "work_item",
            "description_snippet": r.get("description_snippet", ""),
        })

    # Search wiki only if no work-item-specific filters
    if not any([type_filter, state_filter, assigned_to_filter]):
        wiki_results = db.search_wiki(query, limit=limit)
        for r in wiki_results:
            clean_path = r["path"].lstrip("/")
            results.append({
                "id": r["path"],
                "title": r["title"],
                "type": "Wiki",
                "state": "",
                "file_path": f"wiki/{clean_path}.md",
                "source": "wiki",
                "description_snippet": r.get("description_snippet", ""),
            })

    return results[:limit]


def format_results(results: list[dict], *, fmt: str = "compact", data_dir: Path) -> str:
    if fmt == "json":
        return json.dumps(results, indent=2)

    if fmt == "paths":
        return "\n".join(
            (data_dir / r["file_path"]).as_posix() for r in results
        )

    lines: list[str] = []
    for r in results:
        if r["source"] == "work_item":
            id_str = f"#{r['id']}"
        else:
            id_str = r["id"]

        if fmt == "detail":
            snippet = r.get("description_snippet", "")[:200]
            lines.append(
                f"  {id_str:<8} {r['type']:<12} {r['state']:<10} {r['title']}"
            )
            if snippet:
                lines.append(f"           {snippet}")
            lines.append(f"           {r['file_path']}")
        else:  # compact
            lines.append(
                f"  {id_str:<8} {r['type']:<12} {r['state']:<10} {r['title']:<45} {r['file_path']}"
            )

    return "\n".join(lines)
