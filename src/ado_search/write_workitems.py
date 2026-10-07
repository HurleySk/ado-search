from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from ado_search.auth import OP_ADD_COMMENT, OP_ADD_LINK, OP_CREATE, OP_IDENTITY_LOOKUP, OP_SHOW, OP_UPDATE
from ado_search.runner import run_operation
from ado_search.sync_common import finalize_jsonl


def resolve_value(text: str | None) -> str | None:
    """Resolve a CLI value that may reference a file via ``@path``.

    * ``None`` → ``None``
    * ``@some/file.html`` → contents of that file (UTF-8)
    * ``@@literal`` → ``@literal`` (escape hatch)
    * anything else → returned as-is
    """
    if text is None:
        return None
    if text.startswith("@@"):
        return text[1:]  # strip leading @, keep the rest
    if text.startswith("@"):
        path = Path(text[1:])
        if not path.is_file():
            raise click.BadParameter(
                f"File not found: {path} (start the text with @@ if it begins with an @mention)"
            )
        return path.read_text(encoding="utf-8")
    return text


# Maps CLI option names to ADO field reference names
FIELD_MAP = {
    "title":               "System.Title",
    "description":         "System.Description",
    "acceptance_criteria":  "Microsoft.VSTS.Common.AcceptanceCriteria",
    "state":               "System.State",
    "reason":              "Microsoft.VSTS.Common.ResolvedReason",
    "area":                "System.AreaPath",
    "iteration":           "System.IterationPath",
    "assigned_to":         "System.AssignedTo",
    "tags":                "System.Tags",
    "priority":            "Microsoft.VSTS.Common.Priority",
    "story_points":        "Microsoft.VSTS.Scheduling.StoryPoints",
}


LINK_TYPE_MAP: dict[str, str] = {
    "related":      "System.LinkTypes.Related",
    "parent":       "System.LinkTypes.Hierarchy-Reverse",
    "child":        "System.LinkTypes.Hierarchy-Forward",
    "duplicate":    "System.LinkTypes.Duplicate-Forward",
    "duplicate-of": "System.LinkTypes.Duplicate-Reverse",
    "depends-on":   "System.LinkTypes.Dependency-Forward",
    "successor":    "System.LinkTypes.Dependency-Forward",
    "predecessor":  "System.LinkTypes.Dependency-Reverse",
}


def _build_link_url(org: str, project: str, target_id: int) -> str:
    """Build the ADO REST API URL for a target work item (used in relation payloads)."""
    return f"{org}/{project}/_apis/wit/workItems/{target_id}"


def build_json_patch(fields: dict[str, Any]) -> list[dict]:
    """Convert {ado_field: value} to JSON Patch operations for ADO REST API."""
    return [
        {"op": "add", "path": f"/fields/{k}", "value": v}
        for k, v in fields.items()
        if v is not None
    ]


def build_az_fields(fields: dict[str, Any]) -> list[str]:
    """Convert {ado_field: value} to 'Key=Value' strings for az boards --fields."""
    return [f"{k}={v}" for k, v in fields.items() if v is not None]


def resolve_fields(
    *,
    title: str | None = None,
    description: str | None = None,
    acceptance_criteria: str | None = None,
    state: str | None = None,
    reason: str | None = None,
    area: str | None = None,
    iteration: str | None = None,
    assigned_to: str | None = None,
    tags: str | None = None,
    priority: int | None = None,
    story_points: float | None = None,
    extra_fields: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Map named CLI options through FIELD_MAP and merge --field Key=Value entries.

    Named options take precedence over extra_fields for the same ADO field.
    """
    # Start with extra_fields (lower precedence)
    result: dict[str, Any] = {}
    for entry in extra_fields:
        if "=" not in entry:
            continue
        k, _, v = entry.partition("=")
        result[k.strip()] = v.strip()

    # Named options override
    named = {
        "title": title,
        "description": description,
        "acceptance_criteria": acceptance_criteria,
        "state": state,
        "reason": reason,
        "area": area,
        "iteration": iteration,
        "assigned_to": assigned_to,
        "tags": tags,
        "priority": priority,
        "story_points": story_points,
    }
    for cli_name, value in named.items():
        if value is not None:
            ado_field = FIELD_MAP[cli_name]
            result[ado_field] = value

    return result


async def create_work_item(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    work_item_type: str,
    title: str,
    field_values: dict[str, Any],
    parent: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Create a work item in ADO and merge into local JSONL store.

    Returns the normalized JSONL record for the new item.
    """
    if dry_run:
        click.echo(f"Would create {work_item_type}: {title}")
        if field_values:
            for k, v in field_values.items():
                click.echo(f"  {k} = {v}")
        if parent:
            click.echo(f"  Parent: #{parent}")
        return {}

    # Build the full field set (title is always included)
    all_fields = {FIELD_MAP["title"]: title, **field_values}

    if auth_method == "az-cli":
        # az boards work-item create uses --title, --type, --fields Key=Value
        az_fields = build_az_fields({k: v for k, v in all_fields.items()
                                     if k != FIELD_MAP["title"]})
        result = await run_operation(
            auth_method, OP_CREATE,
            org=org, project=project, pat=pat,
            title=title, work_item_type=work_item_type,
            fields=az_fields or None,
        )
    else:
        # PAT and powershell use JSON Patch body
        patch = build_json_patch(all_fields)
        if parent:
            target_url = _build_link_url(org, project, parent)
            patch.append({
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": LINK_TYPE_MAP["parent"],
                    "url": target_url,
                    "attributes": {},
                },
            })
        body = json.dumps(patch)
        result = await run_operation(
            auth_method, OP_CREATE,
            org=org, project=project, pat=pat,
            work_item_type=work_item_type,
            body=body,
            content_type="application/json-patch+json",
        )

    item_id = result.parse_json()["id"] if result.returncode == 0 else 0

    # az-cli can't set relations during create — add parent link as follow-up
    if parent and auth_method == "az-cli" and item_id:
        await add_link(
            org=org, project=project, auth_method=auth_method, pat=pat,
            data_dir=data_dir, source_id=item_id, target_id=parent,
            link_type="parent",
        )

    return await _check_and_refetch(result, "creating work item", item_id,
                                    org=org, project=project, auth_method=auth_method,
                                    pat=pat, data_dir=data_dir)


async def update_work_item(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    work_item_id: int,
    field_values: dict[str, Any],
    dry_run: bool = False,
) -> dict:
    """Update a work item in ADO and refresh local JSONL store.

    Returns the normalized JSONL record for the updated item.
    """
    if dry_run:
        click.echo(f"Would update work item #{work_item_id}:")
        for k, v in field_values.items():
            click.echo(f"  {k} = {v}")
        return {}

    # For az-cli, split title out (it has its own --title flag)
    field_values = dict(field_values)  # don't mutate caller's dict
    title_value = field_values.pop(FIELD_MAP["title"], None)

    if auth_method == "az-cli":
        az_fields = build_az_fields(field_values)
        result = await run_operation(
            auth_method, OP_UPDATE,
            org=org, project=project, pat=pat,
            work_item_id=work_item_id,
            title=title_value,
            fields=az_fields or None,
        )
    else:
        all_fields = field_values
        if title_value is not None:
            all_fields[FIELD_MAP["title"]] = title_value
        patch = build_json_patch(all_fields)
        body = json.dumps(patch)
        result = await run_operation(
            auth_method, OP_UPDATE,
            org=org, project=project, pat=pat,
            work_item_id=work_item_id,
            body=body,
            content_type="application/json-patch+json",
        )

    return await _check_and_refetch(result, f"updating work item #{work_item_id}",
                                    work_item_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


_MENTION_START_RE = re.compile(r'(?<![="\w@.])@(?=\w)')
_MENTION_EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_MENTION_TOKEN_RE = re.compile(r"\([\w.'-]+\)|[\w'-]+(?:\.[\w'-]+)*\.?")
_MENTION_SEP_RE = re.compile(r" |\xa0|&nbsp;")
_MENTION_MAX_TOKENS = 5
# Existing anchors, code blocks, and tags are never scanned for mentions.
_MENTION_PROTECTED_RE = re.compile(
    r"<(a|code|pre)\b[^>]*>.*?</\1\s*>|<[^>]*>", re.IGNORECASE | re.DOTALL,
)


@dataclass
class _Mention:
    start: int  # index of the "@"
    spans: list[tuple[str, int]]  # (query text, end index), longest first


@dataclass
class MentionResolution:
    text: str
    mentioned: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)


def _mention_spans(text: str, pos: int) -> list[tuple[str, int]]:
    """Candidate name spans starting at ``pos`` (just after ``@``), longest first.

    A name is up to five words separated by single spaces, e.g.
    ``Jane Q. Doe (CTR)``; an email address is a single span.
    """
    m = _MENTION_EMAIL_RE.match(text, pos)
    if m:
        return [(m.group(0), m.end())]
    ends: list[int] = []
    i = pos
    while len(ends) < _MENTION_MAX_TOKENS:
        tok = _MENTION_TOKEN_RE.match(text, i)
        if not tok:
            break
        ends.append(tok.end())
        sep = _MENTION_SEP_RE.match(text, tok.end())
        if not sep:
            break
        i = sep.end()
    spans: list[tuple[str, int]] = []
    for end in reversed(ends):
        spans.append((text[pos:end], end))
        if text[end - 1] == "." and end - 1 > pos:
            spans.append((text[pos:end - 1], end - 1))
    return spans


def _find_mentions(text: str) -> list[_Mention]:
    """Locate @mentions outside HTML tags, existing anchors, and code blocks."""
    protected = [m.span() for m in _MENTION_PROTECTED_RE.finditer(text)]
    mentions: list[_Mention] = []
    for m in _MENTION_START_RE.finditer(text):
        if any(a <= m.start() < b for a, b in protected):
            continue
        spans = _mention_spans(text, m.end())
        if spans:
            mentions.append(_Mention(m.start(), spans))
    return mentions


def _mention_words(value: str) -> list[str]:
    return value.replace("&nbsp;", " ").replace("\xa0", " ").casefold().split()


def _identity_matches(query: str, identity: dict, *, exact: bool = False) -> bool:
    """True when ``query`` names ``identity`` by whole words, not a bare prefix.

    ``Jane`` and ``Jane Doe`` match ``Jane Doe (CTR)``; ``Jan`` does not. The
    mail address, or its local part, also matches.
    """
    q = _mention_words(query)
    name = _mention_words(identity.get("displayName", ""))
    mail = identity.get("mail", "").casefold()
    if q and mail and " ".join(q) in (mail, mail.split("@")[0]):
        return True
    if exact:
        bare = [w for w in name if not (w.startswith("(") and w.endswith(")"))]
        return q in (name, bare)
    return bool(q) and name[:len(q)] == q


async def _lookup_identities(
    query: str, *, org: str, auth_method: str, pat: str,
) -> list[dict]:
    """Search ADO identities via the Identity Picker API."""
    body = json.dumps({
        "query": query.replace("&nbsp;", " ").replace("\xa0", " "),
        "identityTypes": ["user"],
        "operationScopes": ["ims"],
        "properties": ["DisplayName", "Mail"],
        "options": {"MinResults": 5, "MaxResults": 10},
    })
    result = await run_operation(
        auth_method, OP_IDENTITY_LOOKUP,
        org=org, project="", pat=pat,
        body=body, content_type="application/json",
    )
    if result.returncode != 0:
        return []
    try:
        data = result.parse_json()
    except (json.JSONDecodeError, ValueError):
        return []
    identities = (data.get("results") or [{}])[0].get("identities") or []
    return [
        {
            "localId": i.get("localId", ""),
            "displayName": i.get("displayName", ""),
            "mail": i.get("mail") or "",
        }
        for i in identities
        if i.get("localId")
    ]


def _mention_html(identity: dict) -> str:
    return (
        f'<a href="#" data-vss-mention="version:2.0,{identity["localId"]}">'
        f'@{html.escape(identity["displayName"], quote=False)}</a>'
    )


async def resolve_mention_html(
    text: str, *, org: str, auth_method: str, pat: str,
) -> MentionResolution:
    """Replace ``@Name`` mentions with ADO mention HTML.

    Each mention takes the longest run of up to five words that names exactly
    one identity, so ``@Jane Doe (CTR), please`` consumes ``Jane Doe (CTR)``.
    A name shared by several people is reported as ambiguous and left as text.
    """
    mentions = _find_mentions(text)
    res = MentionResolution(text=text)
    if not mentions:
        return res

    queries = list(dict.fromkeys(q for m in mentions for q, _ in m.spans))
    found = await asyncio.gather(*(
        _lookup_identities(q, org=org, auth_method=auth_method, pat=pat) for q in queries
    ))
    lookup = dict(zip(queries, found))

    replacements: list[tuple[int, int, str]] = []
    for mention in mentions:
        for query, end in mention.spans:
            matches = list({
                i["localId"]: i for i in lookup[query] if _identity_matches(query, i)
            }.values())
            if len(matches) > 1:
                exact = [i for i in matches if _identity_matches(query, i, exact=True)]
                if len(exact) != 1:
                    res.ambiguous.setdefault(query, sorted(i["displayName"] for i in matches))
                    break
                matches = exact
            if matches:
                replacements.append((mention.start, end, _mention_html(matches[0])))
                if matches[0]["displayName"] not in res.mentioned:
                    res.mentioned.append(matches[0]["displayName"])
                break
        else:
            name = mention.spans[-1][0]
            if name not in res.unresolved:
                res.unresolved.append(name)

    for start, end, anchor in reversed(replacements):
        text = text[:start] + anchor + text[end:]
    res.text = text
    return res


def _report_mentions(res: MentionResolution) -> None:
    if res.mentioned:
        click.echo(f"Mentioned: {', '.join(res.mentioned)}", err=True)
    for name in res.unresolved:
        click.echo(f"Warning: Could not resolve @{name} — left as plain text", err=True)
    for name, options in res.ambiguous.items():
        click.echo(
            f"Warning: @{name} is ambiguous ({'; '.join(options)}) — left as plain text",
            err=True,
        )


async def resolve_mentions(
    text: str, *, org: str, auth_method: str, pat: str,
) -> str:
    """Detect @name patterns in text and replace with ADO mention HTML."""
    res = await resolve_mention_html(text, org=org, auth_method=auth_method, pat=pat)
    _report_mentions(res)
    return res.text


async def add_comment(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    work_item_id: int,
    text: str,
    resolve_mentions_flag: bool = True,
    strict_mentions: bool = False,
    dry_run: bool = False,
) -> dict:
    """Post a comment on an ADO work item and refresh local JSONL store.

    With ``strict_mentions``, an unresolved or ambiguous @mention aborts
    before anything is posted.

    Returns the normalized JSONL record for the work item.
    """
    if dry_run:
        preview = text[:200] + ("…" if len(text) > 200 else "")
        click.echo(f"Would add comment to work item #{work_item_id}:\n{preview}")
        return {}

    if resolve_mentions_flag:
        res = await resolve_mention_html(text, org=org, auth_method=auth_method, pat=pat)
        _report_mentions(res)
        if strict_mentions and (res.unresolved or res.ambiguous):
            raise click.ClickException(
                "Comment not posted: fix the @mentions above (use the full display "
                "name or email), write &#64; for a literal @, or pass --no-mentions."
            )
        text = res.text

    body = json.dumps({"text": text})

    result = await run_operation(
        auth_method, OP_ADD_COMMENT,
        org=org, project=project, pat=pat,
        work_item_id=work_item_id,
        body=body,
        content_type="application/json",
    )

    return await _check_and_refetch(result, f"adding comment to #{work_item_id}",
                                    work_item_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


async def add_link(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    source_id: int,
    target_id: int,
    link_type: str,
    comment: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Add a link between two ADO work items and refresh local JSONL store.

    link_type can be a friendly name (e.g. 'related', 'parent', 'child')
    or a raw ADO relation type string (e.g. 'System.LinkTypes.Related').

    Returns the normalized JSONL record for the source work item.
    """
    rel_type = LINK_TYPE_MAP.get(link_type.lower(), link_type)

    if dry_run:
        click.echo(f"Would add '{rel_type}' link from #{source_id} to #{target_id}")
        if comment:
            click.echo(f"  Comment: {comment}")
        return {}

    target_url = _build_link_url(org, project, target_id)
    value: dict[str, Any] = {
        "rel": rel_type,
        "url": target_url,
        "attributes": {},
    }
    if comment:
        value["attributes"]["comment"] = comment

    patch = [{"op": "add", "path": "/relations/-", "value": value}]
    body = json.dumps(patch)

    result = await run_operation(
        auth_method, OP_ADD_LINK,
        org=org, project=project, pat=pat,
        work_item_id=source_id,
        body=body,
        content_type="application/json-patch+json",
    )

    return await _check_and_refetch(result, f"adding link from #{source_id} to #{target_id}",
                                    source_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


async def remove_link(
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    source_id: int,
    target_id: int,
    link_type: str,
    dry_run: bool = False,
) -> dict:
    """Remove a link between two ADO work items and refresh local JSONL store.

    Fetches the source work item to find the matching relation index, then
    sends a JSON Patch remove operation.

    Returns the normalized JSONL record for the source work item.
    """
    rel_type = LINK_TYPE_MAP.get(link_type.lower(), link_type)

    show_result = await run_operation(
        auth_method, OP_SHOW,
        org=org, project=project, pat=pat,
        work_item_id=source_id,
    )
    if show_result.returncode != 0:
        click.echo(f"Error fetching work item #{source_id}: {show_result.stderr}", err=True)
        raise SystemExit(1)

    try:
        item = show_result.parse_json()
    except (json.JSONDecodeError, ValueError):
        click.echo(f"Error: invalid response for work item #{source_id}", err=True)
        raise SystemExit(1)

    relations = item.get("relations") or []
    target_suffix = f"/{target_id}"
    match_index = None
    for i, rel in enumerate(relations):
        if rel.get("rel") == rel_type and rel.get("url", "").endswith(target_suffix):
            match_index = i
            break

    if match_index is None:
        click.echo(
            f"Error: no '{rel_type}' link from #{source_id} to #{target_id} found",
            err=True,
        )
        raise SystemExit(1)

    if dry_run:
        click.echo(f"Would remove '{rel_type}' link from #{source_id} to #{target_id}")
        return {}

    patch = [{"op": "remove", "path": f"/relations/{match_index}"}]
    body = json.dumps(patch)

    result = await run_operation(
        auth_method, OP_ADD_LINK,
        org=org, project=project, pat=pat,
        work_item_id=source_id,
        body=body,
        content_type="application/json-patch+json",
    )

    return await _check_and_refetch(result, f"removing link from #{source_id} to #{target_id}",
                                    source_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


async def _check_and_refetch(
    result: "CommandResult",
    error_label: str,
    item_id: int,
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str,
    data_dir: Path,
) -> dict:
    """Check command result for errors, then refetch and merge into JSONL."""
    if result.returncode != 0:
        click.echo(f"Error {error_label}: {result.stderr}", err=True)
        raise SystemExit(1)
    return await _refetch_and_merge(item_id, org=org, project=project,
                                    auth_method=auth_method, pat=pat, data_dir=data_dir)


async def _refetch_and_merge(
    item_id: int,
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str,
    data_dir: Path,
) -> dict:
    """Re-fetch a work item and merge it into the local JSONL store."""
    from ado_search.sync_workitems import fetch_item

    semaphore = asyncio.Semaphore(1)
    record = await fetch_item(
        item_id,
        auth_method=auth_method,
        org=org,
        project=project,
        pat=pat,
        semaphore=semaphore,
    )

    if isinstance(record, str):
        click.echo(f"Warning: Item #{item_id} was modified but re-fetch failed: {record}", err=True)
        return {"id": item_id}

    wi_jsonl = data_dir / "work-items.jsonl"
    finalize_jsonl(wi_jsonl, {record["id"]: record}, key="id", sort_key="id", is_incremental=True)
    return record
