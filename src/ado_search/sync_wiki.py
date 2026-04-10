from __future__ import annotations

import asyncio
from pathlib import Path

import click

from ado_search.auth import OP_WIKI_LIST, OP_WIKI_PAGE_LIST, OP_WIKI_PAGE_SHOW
from ado_search.db import Database
from ado_search.markdown import wiki_page_to_markdown
from ado_search.runner import SyncResult, fetch_and_parse, run_operation
from ado_search.sync_common import detect_deletions


def _flatten_wiki_pages(tree: dict) -> list[dict]:
    """Recursively flatten wiki page tree, excluding root."""
    pages: list[dict] = []
    for sub in tree.get("subPages", []):
        if sub.get("path") and sub["path"] != "/":
            pages.append(sub)
        pages.extend(_flatten_wiki_pages(sub))
    return pages


def _wiki_path_to_filepath(wiki_name: str, page_path: str) -> Path:
    """Convert wiki path like /Architecture/Overview to wiki/Architecture/Overview.md."""
    clean = page_path.lstrip("/")
    return Path("wiki") / f"{clean}.md"


async def _fetch_and_write_page(
    wiki_name: str,
    page_path: str,
    *,
    auth_method: str,
    org: str,
    project: str,
    pat: str = "",
    data_dir: Path,
    db: Database,
    semaphore: asyncio.Semaphore,
) -> str | None:
    data = await fetch_and_parse(
        auth_method, OP_WIKI_PAGE_SHOW, f"wiki page {page_path}",
        org=org, project=project, pat=pat, semaphore=semaphore,
        wiki=wiki_name, path=page_path,
    )
    if isinstance(data, str):
        return data

    content = data.get("content", "")
    title = page_path.split("/")[-1].replace("-", " ")
    updated = data.get("dateModified", "")[:10]

    md = wiki_page_to_markdown(title, content)

    file_path = data_dir / _wiki_path_to_filepath(wiki_name, page_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(md, encoding="utf-8")

    snippet = content[:500] if content else ""
    db.upsert_wiki_page({
        "path": page_path,
        "title": title,
        "updated": updated,
        "description_snippet": snippet,
    })

    return None


def detect_wiki_deletions(
    *,
    remote_paths: set[str],
    db: Database,
    data_dir: Path,
) -> list[str]:
    """Remove local wiki pages that no longer exist in ADO. Returns deleted paths."""
    return detect_deletions(
        remote_keys=remote_paths,
        get_local_keys=lambda: set(db.get_all_wiki_paths()),
        delete_batch_fn=db.delete_wiki_pages_batch,
        path_fn=lambda page_path: data_dir / "wiki" / f"{page_path.lstrip('/')}.md",
    )


async def _list_wiki_pages(
    wiki_name: str,
    *,
    auth_method: str,
    org: str,
    project: str,
    pat: str = "",
) -> tuple[str, list[dict] | None]:
    """List pages for a single wiki. Returns (wiki_name, pages) or (wiki_name, None) on error."""
    result = await run_operation(auth_method, OP_WIKI_PAGE_LIST, org=org, project=project, pat=pat, wiki=wiki_name)
    if result.returncode != 0:
        click.echo(f"  Warning: Failed to list pages for wiki {wiki_name}", err=True)
        return wiki_name, None

    tree = result.parse_json()
    # REST API returns root page directly; az CLI may wrap in {"value": [...]}
    # or {"page": {...}}
    if isinstance(tree, dict) and "page" in tree:
        tree = tree["page"]
    if isinstance(tree, dict) and "value" in tree:
        pages_list = []
        for item in tree["value"]:
            if item.get("path") and item["path"] != "/":
                pages_list.append(item)
            pages_list.extend(_flatten_wiki_pages(item))
        return wiki_name, pages_list

    return wiki_name, _flatten_wiki_pages(tree)


async def sync_wiki(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    db: Database,
    wiki_names: list[str],
    max_concurrent: int = 5,
    dry_run: bool = False,
) -> SyncResult:
    result = await run_operation(auth_method, OP_WIKI_LIST, org=org, project=project, pat=pat)
    if result.returncode != 0:
        raise RuntimeError(f"Wiki list failed: {result.stderr}")

    wikis = result.parse_json()
    if isinstance(wikis, dict):
        wikis = wikis.get("value", [])

    if wiki_names:
        wikis = [w for w in wikis if w["name"] in wiki_names]

    if not wikis:
        return {"fetched": 0, "errors": 0}

    # Stage 1: Enumerate pages from all wikis concurrently
    page_list_results = await asyncio.gather(*[
        _list_wiki_pages(
            w["name"], auth_method=auth_method, org=org, project=project, pat=pat,
        )
        for w in wikis
    ])

    all_remote_paths: set[str] = set()
    all_page_tasks: list[tuple[str, str]] = []  # (wiki_name, page_path)
    enum_errors = 0

    for wiki_name, pages in page_list_results:
        if pages is None:
            enum_errors += 1
            continue

        if dry_run:
            paths = [p["path"] for p in pages]
            click.echo(f"Would fetch {len(pages)} wiki pages from {wiki_name}: {paths[:10]}...")
            continue

        for page in pages:
            all_remote_paths.add(page["path"])
            all_page_tasks.append((wiki_name, page["path"]))

    if dry_run:
        return {"fetched": 0, "errors": enum_errors}

    # Stage 2: Fetch all pages across all wikis with a shared semaphore
    total_fetched = 0
    total_errors = enum_errors

    with db.batch():
        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = [
            _fetch_and_write_page(
                wiki_name, page_path,
                auth_method=auth_method, org=org, project=project, pat=pat,
                data_dir=data_dir, db=db, semaphore=semaphore,
            )
            for wiki_name, page_path in all_page_tasks
        ]

        results = await asyncio.gather(*tasks)
        for err in results:
            if err is not None:
                total_errors += 1
                click.echo(f"  Warning: {err}", err=True)
            else:
                total_fetched += 1

    if enum_errors == 0:
        deleted = detect_wiki_deletions(
            remote_paths=all_remote_paths,
            db=db,
            data_dir=data_dir,
        )
        if deleted:
            click.echo(f"  Removed {len(deleted)} orphaned wiki pages")
    elif enum_errors > 0 and all_remote_paths:
        click.echo("  Skipping orphan detection due to wiki enumeration errors", err=True)

    return {"fetched": total_fetched, "errors": total_errors}
