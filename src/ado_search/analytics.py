"""Live, read-only queries against the Azure DevOps Analytics OData endpoint."""
from __future__ import annotations

import json
from urllib.parse import quote, urljoin, urlparse

import click

from ado_search.auth import OP_ANALYTICS

ANALYTICS_BASE = "https://analytics.dev.azure.com"
ANALYTICS_VERSION = "v4.0-preview"
# OData punctuation that must reach Analytics as-is; everything else is percent-encoded.
ODATA_SAFE = "$(),'/=&"
MAX_PAGES = 1000


def org_name(org_url: str) -> str:
    """Organization name from the configured URL.

    ``https://dev.azure.com/FERC-ALM`` gives ``FERC-ALM``;
    ``https://contoso.visualstudio.com`` gives ``contoso``.
    """
    parsed = urlparse((org_url or "").strip())
    host = (parsed.hostname or "").lower()
    if host.endswith(".visualstudio.com"):
        return host.split(".")[0]
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        raise click.ClickException(f"Cannot read the organization name from {org_url!r}")
    return segments[0]


def build_analytics_url(org_url: str, entity: str, *, project: str | None = None,
                        query: str | None = None) -> str:
    """``{base}/{org}[/{project}]/_odata/v4.0-preview/{entity}[?{query}]``.

    The query is the raw OData query string. Spaces, backslashes and other
    characters are percent-encoded; OData punctuation in ``ODATA_SAFE`` is kept.
    """
    entity = (entity or "").strip().strip("/")
    if not entity:
        raise click.ClickException("An Analytics entity set is required, e.g. WorkItems")
    url = f"{ANALYTICS_BASE}/{quote(org_name(org_url), safe='')}"
    if project:
        url += f"/{quote(project, safe='')}"
    url += f"/_odata/{ANALYTICS_VERSION}/{quote(entity, safe=ODATA_SAFE)}"
    query = (query or "").strip().lstrip("?")
    if query:
        url += "?" + quote(query, safe=ODATA_SAFE)
    return url


def _next_url(current: str, link: str | None) -> str | None:
    """Absolute @odata.nextLink, refused if it leaves the Analytics host (the PAT goes with it)."""
    if not link:
        return None
    url = urljoin(current, link)
    if urlparse(url).netloc.lower() != urlparse(ANALYTICS_BASE).netloc:
        raise click.ClickException(f"Refusing @odata.nextLink outside {ANALYTICS_BASE}: {url}")
    return url


async def _get(conn, url: str):
    from ado_search.runner import run_operation_full

    result = await run_operation_full(
        conn.auth_method, OP_ANALYTICS, org=conn.org, project=conn.project, pat=conn.pat, url=url,
    )
    if result.returncode != 0:
        raise click.ClickException(result.stderr or "Analytics request failed")
    try:
        return json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError:
        raise click.ClickException(f"Analytics returned a non-JSON response: {result.stdout[:300]}")


async def query_analytics(conn, entity: str, *, query: str | None = None,
                          max_rows: int | None = None) -> dict:
    """Run one Analytics query, following @odata.nextLink.

    Returns ``{"value": rows, "pages": n, "truncated": bool}``; ``truncated``
    means ``max_rows`` stopped the read while more rows were available.
    """
    url = build_analytics_url(conn.org, entity, project=conn.project or None, query=query)
    rows: list = []
    pages = 0
    while url:
        if pages >= MAX_PAGES:
            raise click.ClickException(f"Analytics {entity}: more than {MAX_PAGES} pages")
        data = await _get(conn, url)
        pages += 1
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            rows.extend(data["value"])
            url = _next_url(url, data.get("@odata.nextLink"))
        else:
            # A single entity (WorkItems(42)) or a bare $count number
            rows.append(data)
            url = None
        if max_rows is not None and len(rows) >= max_rows:
            return {"value": rows[:max_rows], "pages": pages,
                    "truncated": len(rows) > max_rows or url is not None}
    return {"value": rows, "pages": pages, "truncated": False}
