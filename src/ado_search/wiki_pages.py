"""Live read and update of single wiki pages, in any project."""
from __future__ import annotations

import difflib
import json

import click

from ado_search.auth import OP_WIKI_LIST, OP_WIKI_PAGE_LIST, OP_WIKI_PAGE_PUT, OP_WIKI_PAGE_SHOW
from ado_search.testplans import Conn


def normalize_content(text: str) -> str:
    """Strip a BOM and use LF line endings, as ADO stores wiki markdown."""
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


async def resolve_wiki(conn: Conn, wiki: str | None) -> str:
    """Return ``wiki`` or, when omitted, the project's only wiki."""
    if wiki:
        return wiki
    data, _ = await conn.call(OP_WIKI_LIST)
    wikis = [w.get("name") for w in (data.get("value", []) if isinstance(data, dict) else data)]
    if len(wikis) != 1:
        raise click.ClickException(
            f"Project {conn.project!r} has {len(wikis)} wikis ({', '.join(wikis) or 'none'}); pass --wiki")
    return wikis[0]


async def list_pages(conn: Conn, wiki: str) -> list[str]:
    data, _ = await conn.call(OP_WIKI_PAGE_LIST, wiki=wiki)
    paths: list[str] = []

    def walk(node):
        if node.get("path") and node["path"] != "/":
            paths.append(node["path"])
        for child in node.get("subPages") or []:
            walk(child)

    walk(data)
    return paths


async def get_page(conn: Conn, wiki: str, path: str) -> tuple[str, str, dict]:
    """Return (content, ETag, page JSON) for one page."""
    data, headers = await conn.call(OP_WIKI_PAGE_SHOW, wiki=wiki, path=path)
    return data.get("content") or "", headers.get("etag", ""), data


async def page_exists(conn: Conn, wiki: str, path: str) -> tuple[bool, str, str]:
    """Like get_page, but a missing page returns (False, "", "") instead of raising."""
    from ado_search.runner import run_operation_full
    result = await run_operation_full(conn.auth_method, OP_WIKI_PAGE_SHOW, org=conn.org,
                                      project=conn.project, pat=conn.pat, wiki=wiki, path=path)
    if result.returncode != 0:
        if "HTTP 404" in result.stderr:
            return False, "", ""
        raise click.ClickException(result.stderr)
    data = json.loads(result.stdout)
    return True, data.get("content") or "", result.headers.get("etag", "")


def page_diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"live:{path}", tofile=f"new:{path}",
    ))


async def update_page(conn: Conn, wiki: str, path: str, content: str, *,
                      comment: str | None = None, create: bool = False,
                      dry_run: bool = False) -> dict:
    """Replace a page's content (If-Match on the ETag read just before), or create it with ``create``.

    Returns {"changed", "diff", "created", "etag", "dry_run"}.
    """
    from urllib.parse import quote

    content = normalize_content(content)
    exists, old, etag = await page_exists(conn, wiki, path)
    if not exists and not create:
        raise click.ClickException(f"Page {path!r} does not exist in wiki {wiki!r}; pass --create to add it")
    diff = page_diff(normalize_content(old), content, path)
    out = {"changed": bool(diff) or not exists, "diff": diff, "created": not exists,
           "etag": etag, "dry_run": dry_run}
    if dry_run or not out["changed"]:
        return out

    params = [f"comment={quote(comment, safe='')}"] if comment else []
    _, headers = await conn.call(
        OP_WIKI_PAGE_PUT, wiki=wiki, path=path, params=params,
        body=json.dumps({"content": content}),
        extra_headers={"If-Match": etag} if exists else None,
    )
    out["etag"] = headers.get("etag", "")
    return out
