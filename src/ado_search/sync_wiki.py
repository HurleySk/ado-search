from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from ado_search.auth import build_az_cli_command, build_powershell_command
from ado_search.db import Database
from ado_search.markdown import wiki_page_to_markdown
from ado_search.runner import run_command


def _build_command(operation: str, auth_method: str, **kwargs) -> list[str]:
    if auth_method == "az-cli":
        return build_az_cli_command(operation, **kwargs)
    return build_powershell_command(operation, **kwargs)


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
    data_dir: Path,
    db: Database,
    semaphore: asyncio.Semaphore,
) -> str | None:
    async with semaphore:
        cmd = _build_command(
            "wiki-page-show", auth_method,
            org=org, project=project, wiki=wiki_name, path=page_path,
        )
        result = await run_command(cmd)
        if result.returncode != 0:
            return f"Failed to fetch wiki page {page_path}: {result.stderr}"

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"Invalid JSON for wiki page {page_path}"

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


async def sync_wiki(
    *,
    org: str,
    project: str,
    auth_method: str,
    data_dir: Path,
    db: Database,
    wiki_names: list[str],
    max_concurrent: int = 5,
    dry_run: bool = False,
) -> dict:
    cmd = _build_command("wiki-list", auth_method, org=org, project=project)
    result = await run_command(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Wiki list failed: {result.stderr}")

    wikis = json.loads(result.stdout)
    if isinstance(wikis, dict):
        wikis = wikis.get("value", [])

    if wiki_names:
        wikis = [w for w in wikis if w["name"] in wiki_names]

    total_fetched = 0
    total_errors = 0

    for wiki in wikis:
        wiki_name = wiki["name"]

        cmd = _build_command(
            "wiki-page-list", auth_method,
            org=org, project=project, wiki=wiki_name,
        )
        result = await run_command(cmd)
        if result.returncode != 0:
            click.echo(f"  Warning: Failed to list pages for wiki {wiki_name}", err=True)
            total_errors += 1
            continue

        tree = json.loads(result.stdout)
        if isinstance(tree, dict) and "value" in tree:
            pages_list = []
            for item in tree["value"]:
                pages_list.append(item)
                pages_list.extend(_flatten_wiki_pages(item))
            pages = [p for p in pages_list if p.get("path") and p["path"] != "/"]
        else:
            pages = _flatten_wiki_pages(tree)

        if dry_run:
            paths = [p["path"] for p in pages]
            click.echo(f"Would fetch {len(pages)} wiki pages from {wiki_name}: {paths[:10]}...")
            continue

        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = [
            _fetch_and_write_page(
                wiki_name, page["path"],
                auth_method=auth_method, org=org, project=project,
                data_dir=data_dir, db=db, semaphore=semaphore,
            )
            for page in pages
        ]

        results = await asyncio.gather(*tasks)
        for err in results:
            if err is not None:
                total_errors += 1
                click.echo(f"  Warning: {err}", err=True)
            else:
                total_fetched += 1

    return {"fetched": total_fetched, "errors": total_errors}
