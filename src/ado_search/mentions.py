"""Live read of work item comments that @mention the authenticated user."""
from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import click

from ado_search.auth import OP_COMMENTS, OP_CONNECTION_DATA, OP_GET_FIELDS, OP_QUERY
from ado_search.markdown import strip_html

RECENT_MENTIONS_WIQL = (
    "SELECT [System.Id] FROM WorkItems WHERE [System.Id] IN (@RecentMentions) "
    "ORDER BY [System.ChangedDate] DESC"
)

_ANCHOR_RE = re.compile(
    r"""<a\b[^>]*?\bdata-vss-mention\s*=\s*(["'])version:[^,"']*,\s*([^"']*?)\s*\1[^>]*>(.*?)</a\s*>""",
    re.IGNORECASE | re.DOTALL,
)
_FRACTION_RE = re.compile(r"\.(\d+)")
_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


@dataclass
class Identity:
    """The authenticated user, as ``_apis/connectionData`` reports it."""
    id: str
    display_name: str
    names: list[str] = field(default_factory=list)  # @name forms for the text fallback
    email: str = ""


def parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 datetime as UTC; a value without an offset is taken as UTC.

    Accepts ADO's variable-length fractions (``.27Z``, ``.1234567Z``) on Python 3.10.
    """
    s = value.strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    s = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s, count=1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def identity_from_connection_data(data: dict) -> Identity:
    user = data.get("authenticatedUser") or {}
    user_id = user.get("id") or ""
    if not user_id:
        raise click.ClickException("connectionData returned no authenticated user id")
    custom = user.get("customDisplayName") or ""
    provider = user.get("providerDisplayName") or ""
    display = custom or provider or user_id
    account = ((user.get("properties") or {}).get("Account") or {}).get("$value") or ""
    email = account if "@" in account else ""
    names: list[str] = []
    for name in (custom, provider, _SUFFIX_RE.sub("", custom), _SUFFIX_RE.sub("", provider), email):
        name = name.strip()
        if name and name.casefold() not in (n.casefold() for n in names):
            names.append(name)
    names.sort(key=len, reverse=True)
    return Identity(id=user_id, display_name=display, names=names, email=email)


def _name_pattern(names: list[str]) -> re.Pattern[str] | None:
    alts = [r"\s+".join(re.escape(w) for w in n.split()) for n in names if n.split()]
    if not alts:
        return None
    return re.compile(r"(?<![\w@.])@(?:" + "|".join(alts) + r")(?![\w])", re.IGNORECASE)


def mentions_identity(text_html: str, me: Identity) -> bool:
    """True when a comment mentions ``me``.

    A comment with mention anchors (``data-vss-mention="version:2.0,<id>"``)
    matches only on the anchor id. A comment with no anchors at all falls back
    to an ``@Display Name`` (or ``@email``) match in its plain text.
    """
    anchors = _ANCHOR_RE.findall(text_html or "")
    if anchors:
        return any(a_id.casefold() == me.id.casefold() for _, a_id, _ in anchors)
    pattern = _name_pattern(me.names)
    return bool(pattern and pattern.search(strip_html(text_html or "")))


def comment_plain_text(text_html: str) -> str:
    """Strip HTML from a comment, rendering mention anchors as ``@Name``."""
    def _mention(m: re.Match[str]) -> str:
        name = strip_html(m.group(3)).strip().lstrip("@").strip()
        return f"@{html.escape(name, quote=False)}" if name else ""

    text = strip_html(_ANCHOR_RE.sub(_mention, text_html or ""))
    return text.replace("\xa0", " ")


def _identity_value(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("uniqueName") or value.get("displayName") or ""
    return value or ""


def _is_own(comment: dict, me: Identity) -> bool:
    author = comment.get("createdBy") or {}
    if (author.get("id") or "").casefold() == me.id.casefold():
        return True
    return bool(me.email) and (author.get("uniqueName") or "").casefold() == me.email.casefold()


def mention_records(
    item: dict, comments: list[dict], me: Identity, *,
    since: datetime | None = None, include_own: bool = False,
) -> list[dict]:
    """One record per comment on ``item`` that mentions ``me``."""
    fields = item.get("fields") or {}
    records = []
    for c in comments:
        text_html = c.get("text") or ""
        if not include_own and _is_own(c, me):
            continue
        if not mentions_identity(text_html, me):
            continue
        created = parse_utc(c["createdDate"]) if c.get("createdDate") else None
        if since is not None:
            stamps = [created] if created else []
            if c.get("modifiedDate"):
                stamps.append(parse_utc(c["modifiedDate"]))
            if not stamps or max(stamps) <= since:
                continue
        records.append({
            "work_item_id": item.get("id"),
            "title": fields.get("System.Title") or "",
            "state": fields.get("System.State") or "",
            "type": fields.get("System.WorkItemType") or "",
            "assigned_to": _identity_value(fields.get("System.AssignedTo")),
            "comment_id": c.get("id"),
            "author": (c.get("createdBy") or {}).get("displayName") or "",
            "created_date": format_utc(created) if created else "",
            "text": comment_plain_text(text_html),
        })
    return records


async def get_identity(*, org: str, auth_method: str, pat: str = "") -> Identity:
    from ado_search.runner import fetch_and_parse

    data = await fetch_and_parse(
        auth_method, OP_CONNECTION_DATA, "connectionData", org=org, project="", pat=pat,
    )
    if isinstance(data, str):
        raise click.ClickException(data)
    return identity_from_connection_data(data)


async def recent_mention_ids(*, org: str, project: str, auth_method: str, pat: str = "") -> list[int]:
    from ado_search.runner import run_operation
    from ado_search.sync_workitems import _parse_query_result

    result = await run_operation(
        auth_method, OP_QUERY, org=org, project=project, pat=pat, wiql=RECENT_MENTIONS_WIQL,
    )
    if result.returncode != 0:
        raise click.ClickException(f"Failed to query @RecentMentions: {result.stderr}")
    try:
        return _parse_query_result(result.stdout)
    except (ValueError, KeyError, TypeError) as e:
        raise click.ClickException(f"Invalid @RecentMentions query result: {e}")


async def _item_mentions(
    item_id: int, me: Identity, *, org: str, project: str, auth_method: str, pat: str,
    semaphore: asyncio.Semaphore, since: datetime | None, include_own: bool,
) -> tuple[list[dict], str | None]:
    from ado_search.runner import fetch_and_parse

    item = await fetch_and_parse(
        auth_method, OP_GET_FIELDS, f"#{item_id}",
        org=org, project="", pat=pat, semaphore=semaphore, work_item_id=item_id,
    )
    if isinstance(item, str):
        return [], item
    item_project = (item.get("fields") or {}).get("System.TeamProject") or project
    data = await fetch_and_parse(
        auth_method, OP_COMMENTS, f"comments for #{item_id}",
        org=org, project=item_project, pat=pat, semaphore=semaphore, work_item_id=item_id,
    )
    if isinstance(data, str):
        return [], data
    records = mention_records(item, data.get("comments") or [], me,
                              since=since, include_own=include_own)
    return records, None


async def find_mentions(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    since: datetime | None = None,
    include_own: bool = False,
    max_concurrent: int = 5,
) -> tuple[Identity, list[dict], list[str]]:
    """Comments that @mention the authenticated user, newest first.

    Returns ``(identity, records, errors)``; ``errors`` names work items whose
    details or comments could not be read. Reads only: nothing is written to
    ADO or to the local store. ADO keeps @RecentMentions for 30 days.
    """
    me, ids = await asyncio.gather(
        get_identity(org=org, auth_method=auth_method, pat=pat),
        recent_mention_ids(org=org, project=project, auth_method=auth_method, pat=pat),
    )
    semaphore = asyncio.Semaphore(max(1, max_concurrent))
    results = await asyncio.gather(*[
        _item_mentions(i, me, org=org, project=project, auth_method=auth_method, pat=pat,
                       semaphore=semaphore, since=since, include_own=include_own)
        for i in ids
    ])
    records = [r for recs, _ in results for r in recs]
    errors = [err for _, err in results if err]
    records.sort(key=lambda r: (r["created_date"], r["comment_id"] or 0), reverse=True)
    return me, records, errors


def format_mentions(records: list[dict], me: Identity, *, total: int, since: datetime | None) -> str:
    window = f"since {format_utc(since)}" if since else "in the last 30 days"
    if not records:
        return f"No comments mentioning {me.display_name} {window}."
    count = f"{len(records)} of {total}" if total > len(records) else str(len(records))
    lines = [f"Comments mentioning {me.display_name} {window}, newest first ({count}):"]
    for r in records:
        assigned = r["assigned_to"] or "unassigned"
        lines.append("")
        lines.append(f"#{r['work_item_id']} [{r['type']}] {r['title']} ({r['state']}; {assigned})")
        lines.append(f"  [{r['created_date']}] {r['author']} (comment id {r['comment_id']}):")
        for line in r["text"].split("\n"):
            lines.append(f"    {line}")
    return "\n".join(lines)
