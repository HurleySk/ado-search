from __future__ import annotations

import asyncio
import re as re_module
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import click

from ado_search.config import default_config, load_config, save_config
from ado_search.db import Database
from ado_search.search import search, format_results


def _default_data_dir() -> Path:
    return Path.cwd() / ".ado-search"


class _Conn:
    """Resolved connection info from config."""
    __slots__ = ("cfg", "org", "project", "auth_method", "pat", "data_path")

    def __init__(self, cfg, org, project, auth_method, pat, data_path):
        self.cfg = cfg
        self.org = org
        self.project = project
        self.auth_method = auth_method
        self.pat = pat
        self.data_path = data_path


def _load_conn(data_dir: str | None) -> _Conn:
    """Load config, resolve PAT, and return connection info.

    Exits with error if not initialized.
    """
    data_path = Path(data_dir) if data_dir else _default_data_dir()
    config_path = data_path / "config.toml"

    if not config_path.exists():
        click.echo("Error: Not initialized. Run 'ado-search init' first.", err=True)
        raise SystemExit(1)

    cfg = load_config(config_path)
    org = cfg["organization"]["url"]
    project = cfg["organization"]["project"]
    auth_method = cfg["auth"]["method"]

    pat = ""
    if auth_method == "pat":
        from ado_search.auth import get_pat
        pat = get_pat(cfg)

    return _Conn(cfg, org, project, auth_method, pat, data_path)


def _ensure_index(data_path: Path, db: Database, *, force: bool = False) -> None:
    """Rebuild DB index from JSONL if stale or missing."""
    db_path = data_path / "index.db"
    wi_jsonl = data_path / "work-items.jsonl"
    wiki_jsonl = data_path / "wiki-pages.jsonl"

    if not wi_jsonl.exists() and not wiki_jsonl.exists():
        return

    needs_reindex = force
    if not needs_reindex:
        db_mtime = db_path.stat().st_mtime
        for jsonl in [wi_jsonl, wiki_jsonl]:
            if jsonl.exists() and jsonl.stat().st_mtime > db_mtime:
                needs_reindex = True
                break

    if needs_reindex:
        db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)


@contextmanager
def _open_db(data_path: Path):
    """Open and initialize the database, closing it on exit."""
    db = Database(data_path / "index.db")
    db.initialize()
    try:
        yield db
    finally:
        db.close()


@click.group()
@click.version_option(package_name="ado-search")
def main():
    """Sync and search Azure DevOps data for AI agents."""
    pass


@main.command()
@click.option("--org", prompt="Organization URL", help="e.g. https://dev.azure.com/contoso")
@click.option("--project", prompt="Project name", help="Azure DevOps project name")
@click.option("--auth-method", type=click.Choice(["az-cli", "az-powershell", "pat"]),
              default="az-cli", help="Authentication method")
@click.option("--pat", default=None, help="Personal access token (or set ADO_PAT env var)")
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
def init(org: str, project: str, auth_method: str, pat: str | None, data_dir: str | None):
    """Initialize ado-search configuration."""
    data_path = Path(data_dir) if data_dir else _default_data_dir()
    data_path.mkdir(parents=True, exist_ok=True)

    cfg = default_config()
    cfg["organization"]["url"] = org
    cfg["organization"]["project"] = project
    cfg["auth"]["method"] = auth_method
    if pat:
        cfg["auth"]["pat"] = pat

    config_path = data_path / "config.toml"
    save_config(cfg, config_path)

    with _open_db(data_path):
        pass

    click.echo(f"Initialized ado-search at {data_path}")
    click.echo(f"  Organization: {org}")
    click.echo(f"  Project: {project}")
    click.echo(f"  Auth method: {auth_method}")


@main.command()
@click.option("--data-dir", type=click.Path(exists=True), default=None)
@click.option("--dry-run", is_flag=True, help="Show what would be synced without writing")
def sync(data_dir: str | None, dry_run: bool):
    """Sync work items and wiki pages from Azure DevOps."""
    conn = _load_conn(data_dir)
    sync_cfg = conn.cfg["sync"]

    with _open_db(conn.data_path) as db:
        from ado_search.sync_workitems import sync_work_items
        from ado_search.sync_wiki import sync_wiki

        click.echo("Syncing work items...")
        wi_stats = asyncio.run(sync_work_items(
            org=conn.org, project=conn.project,
            auth_method=conn.auth_method, pat=conn.pat,
            data_dir=conn.data_path,
            work_item_types=sync_cfg.get("work_item_types", []),
            area_paths=sync_cfg.get("area_paths", []),
            states=sync_cfg.get("states", []),
            last_sync=sync_cfg.get("last_sync", ""),
            max_concurrent=sync_cfg.get("performance", {}).get("max_concurrent", 5),
            include_comments=sync_cfg.get("include_comments", False),
            include_attachments=sync_cfg.get("include_attachments", False),
            dry_run=dry_run,
        ))
        click.echo(f"  Work items: {wi_stats['fetched']} synced, {wi_stats['errors']} errors")

        click.echo("Syncing wiki pages...")
        wiki_stats = asyncio.run(sync_wiki(
            org=conn.org, project=conn.project,
            auth_method=conn.auth_method, pat=conn.pat,
            data_dir=conn.data_path,
            wiki_names=sync_cfg.get("wiki_names", []),
            max_concurrent=sync_cfg.get("performance", {}).get("max_concurrent", 5),
            dry_run=dry_run,
        ))
        click.echo(f"  Wiki pages: {wiki_stats['fetched']} synced, {wiki_stats['errors']} errors")

        if not dry_run:
            wi_jsonl = conn.data_path / "work-items.jsonl"
            wiki_jsonl = conn.data_path / "wiki-pages.jsonl"
            db.reindex_from_jsonl(wi_jsonl, wiki_jsonl)
            conn.cfg["sync"]["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            save_config(conn.cfg, conn.data_path / "config.toml")
            click.echo("Sync complete.")


@main.command("search")
@click.argument("query")
@click.option("--type", "type_filter", default=None, help="Filter by work item type")
@click.option("--state", "state_filter", default=None, help="Filter by state")
@click.option("--area", "area_filter", default=None, help="Filter by area path (prefix match)")
@click.option("--assigned-to", default=None, help="Filter by assignee email")
@click.option("--tag", "tag_filter", default=None, help="Filter by tag")
@click.option("--limit", default=20, type=int, help="Max results (default 20)")
@click.option("--format", "fmt", type=click.Choice(["compact", "detail", "json", "paths"]),
              default="compact", help="Output format")
@click.option("--data-dir", type=click.Path(), default=None)
def search_cmd(query, type_filter, state_filter, area_filter, assigned_to, tag_filter,
               limit, fmt, data_dir):
    """Search indexed Azure DevOps data."""
    data_path = Path(data_dir) if data_dir else _default_data_dir()

    wi_jsonl = data_path / "work-items.jsonl"
    wiki_jsonl = data_path / "wiki-pages.jsonl"
    if not wi_jsonl.exists() and not wiki_jsonl.exists():
        click.echo("Error: No data found. Run 'ado-search sync' first.", err=True)
        raise SystemExit(1)

    db_is_new = not (data_path / "index.db").exists()
    with _open_db(data_path) as db:
        _ensure_index(data_path, db, force=db_is_new)

        results = search(
            db, query, data_dir=data_path,
            type_filter=type_filter, state_filter=state_filter,
            area_filter=area_filter, assigned_to_filter=assigned_to,
            tag_filter=tag_filter, limit=limit,
        )

        if not results:
            click.echo(f'No results for "{query}"')
            return

        if fmt != "json":
            click.echo(f'Results for "{query}" ({len(results)} matches):')

        click.echo(format_results(results, fmt=fmt, data_dir=data_path))


VALID_GREP_FIELDS = [
    "title", "description", "acceptance_criteria", "comments",
    "tags", "assigned_to", "area", "iteration", "state_history",
]


@main.command("grep")
@click.argument("pattern")
@click.option("--field", "-f", "fields", multiple=True,
              type=click.Choice(VALID_GREP_FIELDS, case_sensitive=False),
              help="Fields to search (repeatable, default: title,description,comments)")
@click.option("--type", "-t", "type_filter", default=None, help="Filter by work item type")
@click.option("--state", "-s", "state_filter", default=None, help="Filter by state")
@click.option("--area", "-a", "area_filter", default=None, help="Filter by area path (prefix)")
@click.option("--assigned-to", default=None, help="Filter by assignee email")
@click.option("--tag", "tag_filter", default=None, help="Filter by tag")
@click.option("--brief", "-b", is_flag=True, help="Show only item IDs and matched field names")
@click.option("--format", "fmt", type=click.Choice(["compact", "brief", "json"]),
              default=None, help="Output format (default: compact)")
@click.option("--ignore-case", "-i", is_flag=True, help="Case-insensitive matching")
@click.option("--context", "-C", "context_chars", type=int, default=60,
              help="Characters of context around each match (default: 60)")
@click.option("--limit", "-n", type=int, default=50, help="Max results (default: 50)")
@click.option("--data-dir", type=click.Path(), default=None)
def grep_cmd(pattern, fields, type_filter, state_filter, area_filter, assigned_to,
             tag_filter, brief, fmt, ignore_case, context_chars, limit, data_dir):
    """Search work items with regex patterns across fields."""
    if brief and fmt is None:
        fmt = "brief"
    elif fmt is None:
        fmt = "compact"

    flags = re_module.IGNORECASE if ignore_case else 0
    try:
        compiled = re_module.compile(pattern, flags)
    except re_module.error as e:
        click.echo(f"Error: Invalid regex pattern: {e}", err=True)
        raise SystemExit(2)

    data_path = Path(data_dir) if data_dir else _default_data_dir()
    wi_jsonl = data_path / "work-items.jsonl"

    if not wi_jsonl.exists():
        click.echo("Error: No data found. Run 'ado-search sync' first.", err=True)
        raise SystemExit(2)

    candidate_ids = None
    has_filters = any([type_filter, state_filter, area_filter, assigned_to, tag_filter])
    if has_filters:
        db_is_new = not (data_path / "index.db").exists()
        with _open_db(data_path) as db:
            _ensure_index(data_path, db, force=db_is_new)
            candidate_ids = db.get_filtered_ids(
                type_filter=type_filter, state_filter=state_filter,
                area_filter=area_filter, assigned_to_filter=assigned_to,
                tag_filter=tag_filter,
            )
        if candidate_ids is not None and not candidate_ids:
            click.echo("No items match the specified filters.")
            raise SystemExit(1)

    from ado_search.grep import grep_work_items, format_grep_results

    results, warnings = grep_work_items(
        jsonl_path=wi_jsonl,
        pattern=compiled,
        fields=list(fields) if fields else None,
        candidate_ids=candidate_ids,
        context_chars=context_chars,
        limit=limit,
    )

    for w in warnings:
        click.echo(f"Warning: {w}", err=True)

    if not results:
        click.echo("No matches found.")
        raise SystemExit(1)

    click.echo(format_grep_results(results, fmt=fmt))


@main.command()
@click.argument("item_id")
@click.option("--data-dir", type=click.Path(), default=None)
def show(item_id: str, data_dir: str | None):
    """Show full content of a work item or wiki page."""
    data_path = Path(data_dir) if data_dir else _default_data_dir()

    db_is_new = not (data_path / "index.db").exists()
    with _open_db(data_path) as db:
        _ensure_index(data_path, db, force=db_is_new)

        # Try as work item ID
        try:
            wi_id = int(item_id)
            item = db.get_work_item(wi_id)
            if item:
                from ado_search.markdown import make_snippet, work_item_to_markdown
                meta = dict(item)
                meta["description_full"] = meta.pop("description", "")
                meta["description_snippet"] = make_snippet(meta["description_full"])
                # Load comments from JSONL (not stored in DB)
                comments = None
                attachments = None
                inline_images = None
                from ado_search.jsonl import read_jsonl_item
                wi_jsonl = data_path / "work-items.jsonl"
                if wi_jsonl.exists():
                    jsonl_item = read_jsonl_item(wi_jsonl, key="id", value=wi_id)
                    if jsonl_item:
                        if jsonl_item.get("comments"):
                            # Map from JSONL format to raw ADO format expected by markdown
                            comments = [
                                {"createdBy": {"displayName": c["author"]},
                                 "createdDate": c["date"],
                                 "text": c["text"]}
                                for c in jsonl_item["comments"]
                            ]
                        if jsonl_item.get("attachments"):
                            attachments = jsonl_item["attachments"]
                        if jsonl_item.get("inline_images"):
                            inline_images = jsonl_item["inline_images"]
                md = work_item_to_markdown(
                    {}, meta=meta, comments=comments,
                    attachments=attachments, inline_images=inline_images,
                )
                click.echo(md)
                return
        except ValueError:
            pass

        # Try as wiki path
        wiki_path = item_id if item_id.startswith("/") else f"/{item_id}"
        page = db.get_wiki_page(wiki_path)
        if page:
            from ado_search.markdown import wiki_page_to_markdown
            click.echo(wiki_page_to_markdown(page["title"], page["content"]))
            return

        click.echo(f"Error: Item '{item_id}' not found.", err=True)
        raise SystemExit(1)


@main.command()
@click.argument("ids", nargs=-1, type=int, required=True)
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
@click.option("--dry-run", is_flag=True, help="Preview without writing")
def fetch(ids: tuple[int, ...], data_dir: str | None, dry_run: bool):
    """Fetch specific work items by ID and add to local store."""
    conn = _load_conn(data_dir)

    with _open_db(conn.data_path) as db:
        from ado_search.sync_workitems import fetch_specific_work_items

        click.echo(f"Fetching {len(ids)} work item(s): {list(ids)}")
        stats = asyncio.run(fetch_specific_work_items(
            item_ids=list(ids),
            org=conn.org,
            project=conn.project,
            auth_method=conn.auth_method,
            pat=conn.pat,
            data_dir=conn.data_path,
            max_concurrent=conn.cfg["sync"].get("performance", {}).get("max_concurrent", 5),
            dry_run=dry_run,
            include_attachments=conn.cfg["sync"].get("include_attachments", False),
        ))

        if not dry_run:
            _ensure_index(conn.data_path, db, force=True)
            click.echo(f"Fetched {stats['fetched']} work item(s), {stats['errors']} error(s).")


@main.command()
@click.option("--type", "work_item_type", required=True, help="Work item type (Bug, User Story, etc.)")
@click.option("--title", required=True, help="Work item title")
@click.option("--description", default=None, help="Description (HTML or @file.html)")
@click.option("--acceptance-criteria", default=None, help="Acceptance criteria (HTML or @file.html)")
@click.option("--state", default=None, help="Initial state")
@click.option("--reason", default=None, help="Resolved/closed reason (e.g., Duplicate, Fixed)")
@click.option("--area", default=None, help="Area path")
@click.option("--iteration", default=None, help="Iteration path")
@click.option("--assigned-to", default=None, help="Assignee email or display name")
@click.option("--tags", default=None, help="Tags (semicolon-separated)")
@click.option("--priority", type=click.IntRange(1, 4), default=None, help="Priority (1-4)")
@click.option("--story-points", type=float, default=None, help="Story points / effort")
@click.option("--field", "extra_fields", multiple=True,
              help="Additional field as Key=Value (repeatable)")
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
@click.option("--dry-run", is_flag=True, help="Preview without creating")
def create(work_item_type, title, description, acceptance_criteria, state, reason,
           area, iteration, assigned_to, tags, priority, story_points, extra_fields,
           data_dir, dry_run):
    """Create a new work item in Azure DevOps."""
    conn = _load_conn(data_dir)

    from ado_search.write_workitems import create_work_item, resolve_fields, resolve_value

    description = resolve_value(description)
    acceptance_criteria = resolve_value(acceptance_criteria)

    field_values = resolve_fields(
        description=description, acceptance_criteria=acceptance_criteria,
        state=state, reason=reason, area=area, iteration=iteration,
        assigned_to=assigned_to, tags=tags, priority=priority,
        story_points=story_points, extra_fields=extra_fields,
    )

    with _open_db(conn.data_path) as db:
        record = asyncio.run(create_work_item(
            org=conn.org, project=conn.project,
            auth_method=conn.auth_method, pat=conn.pat,
            data_dir=conn.data_path,
            work_item_type=work_item_type, title=title,
            field_values=field_values, dry_run=dry_run,
        ))

        if not dry_run and record:
            _ensure_index(conn.data_path, db, force=True)
            click.echo(
                f"Created work item #{record['id']}: {record.get('title', title)} "
                f"({record.get('type', work_item_type)}, {record.get('state', 'New')})"
            )


@main.command()
@click.argument("work_item_id", type=int)
@click.option("--title", default=None, help="New title")
@click.option("--state", default=None, help="New state")
@click.option("--reason", default=None, help="Resolved/closed reason (e.g., Duplicate, Fixed)")
@click.option("--description", default=None, help="New description (HTML or @file.html)")
@click.option("--acceptance-criteria", default=None, help="New acceptance criteria (HTML or @file.html)")
@click.option("--area", default=None, help="New area path")
@click.option("--iteration", default=None, help="New iteration path")
@click.option("--assigned-to", default=None, help="New assignee")
@click.option("--tags", default=None, help="New tags (semicolon-separated)")
@click.option("--priority", type=int, default=None, help="New priority (1-4)")
@click.option("--story-points", type=float, default=None, help="New story points")
@click.option("--field", "extra_fields", multiple=True,
              help="Additional field as Key=Value (repeatable)")
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
@click.option("--dry-run", is_flag=True, help="Preview without updating")
def update(work_item_id, title, state, reason, description, acceptance_criteria, area,
           iteration, assigned_to, tags, priority, story_points, extra_fields,
           data_dir, dry_run):
    """Update an existing work item in Azure DevOps."""
    from ado_search.write_workitems import resolve_fields, resolve_value, update_work_item

    description = resolve_value(description)
    acceptance_criteria = resolve_value(acceptance_criteria)

    field_values = resolve_fields(
        title=title, description=description, acceptance_criteria=acceptance_criteria,
        state=state, reason=reason, area=area, iteration=iteration,
        assigned_to=assigned_to, tags=tags, priority=priority,
        story_points=story_points, extra_fields=extra_fields,
    )

    if not field_values:
        click.echo("Error: No fields to update. Provide at least one option.", err=True)
        raise SystemExit(1)

    conn = _load_conn(data_dir)

    with _open_db(conn.data_path) as db:
        record = asyncio.run(update_work_item(
            org=conn.org, project=conn.project,
            auth_method=conn.auth_method, pat=conn.pat,
            data_dir=conn.data_path,
            work_item_id=work_item_id,
            field_values=field_values, dry_run=dry_run,
        ))

        if not dry_run and record:
            _ensure_index(conn.data_path, db, force=True)
            click.echo(
                f"Updated work item #{record.get('id', work_item_id)}: "
                f"{record.get('title', '')} "
                f"({record.get('type', '')}, {record.get('state', '')})"
            )


@main.command("add-comment")
@click.argument("work_item_id", type=int)
@click.argument("text")
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
@click.option("--dry-run", is_flag=True, help="Preview without posting")
def add_comment_cmd(work_item_id, text, data_dir, dry_run):
    """Add a comment to an Azure DevOps work item.

    TEXT can be an inline HTML string or @path/to/file.html to read from a file.
    """
    from ado_search.write_workitems import add_comment, resolve_value

    text = resolve_value(text)
    conn = _load_conn(data_dir)

    with _open_db(conn.data_path) as db:
        record = asyncio.run(add_comment(
            org=conn.org, project=conn.project,
            auth_method=conn.auth_method, pat=conn.pat,
            data_dir=conn.data_path,
            work_item_id=work_item_id, text=text, dry_run=dry_run,
        ))

        if not dry_run and record:
            _ensure_index(conn.data_path, db, force=True)
            click.echo(f"Added comment to work item #{record.get('id', work_item_id)}")


@main.command("add-link")
@click.argument("source_id", type=int)
@click.argument("target_id", type=int)
@click.option("--type", "link_type", required=True,
              help="Link type: related, parent, child, duplicate, duplicate-of, depends-on, predecessor, successor (or raw ADO type)")
@click.option("--comment", default=None, help="Optional comment on the link")
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
@click.option("--dry-run", is_flag=True, help="Preview without creating the link")
def add_link_cmd(source_id, target_id, link_type, comment, data_dir, dry_run):
    """Add a link between two Azure DevOps work items.

    SOURCE_ID is the work item to modify. TARGET_ID is the work item to link to.
    """
    from ado_search.write_workitems import add_link

    conn = _load_conn(data_dir)

    with _open_db(conn.data_path) as db:
        record = asyncio.run(add_link(
            org=conn.org, project=conn.project,
            auth_method=conn.auth_method, pat=conn.pat,
            data_dir=conn.data_path,
            source_id=source_id, target_id=target_id,
            link_type=link_type, comment=comment, dry_run=dry_run,
        ))

        if not dry_run and record:
            _ensure_index(conn.data_path, db, force=True)
            click.echo(
                f"Added '{link_type}' link from #{source_id} to #{target_id}"
            )


@main.command("list-links")
@click.argument("work_item_id", type=int)
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
def list_links_cmd(work_item_id, data_dir):
    """List links on an Azure DevOps work item (live from ADO)."""
    conn = _load_conn(data_dir)

    from ado_search.auth import OP_SHOW
    from ado_search.runner import fetch_and_parse

    raw = asyncio.run(fetch_and_parse(
        conn.auth_method, OP_SHOW, f"#{work_item_id}",
        org=conn.org, project=conn.project, pat=conn.pat,
        work_item_id=work_item_id,
    ))

    if isinstance(raw, str):
        click.echo(f"Error: {raw}", err=True)
        raise SystemExit(1)

    relations = raw.get("relations") or []
    skip_types = {"AttachedFile", "Hyperlink", "ArtifactLink"}
    links = []
    for rel in relations:
        rel_type = rel.get("rel", "")
        if rel_type in skip_types:
            continue
        url = rel.get("url", "")
        if "_apis/wit/workItems/" not in url:
            continue
        attrs = rel.get("attributes", {})
        name = attrs.get("name", rel_type)
        target_id = url.rsplit("/", 1)[-1]
        link_comment = attrs.get("comment", "")
        links.append({"name": name, "rel": rel_type, "target_id": target_id, "comment": link_comment})

    if not links:
        click.echo(f"No links on work item #{work_item_id}")
        return

    click.echo(f"Links on work item #{work_item_id}:")
    for link in links:
        line = f"  [{link['name']}] #{link['target_id']}"
        if link["comment"]:
            line += f"  ({link['comment']})"
        click.echo(line)


@main.command("list-comments")
@click.argument("work_item_id", type=int)
@click.option("--data-dir", type=click.Path(), default=None,
              help="Data directory (default: ./.ado-search)")
def list_comments_cmd(work_item_id, data_dir):
    """List comments on an Azure DevOps work item (live from ADO)."""
    conn = _load_conn(data_dir)

    from ado_search.auth import OP_COMMENTS
    from ado_search.runner import fetch_and_parse
    from ado_search.markdown import strip_html

    data = asyncio.run(fetch_and_parse(
        conn.auth_method, OP_COMMENTS, f"comments for #{work_item_id}",
        org=conn.org, project=conn.project, pat=conn.pat,
        work_item_id=work_item_id,
    ))

    if isinstance(data, str):
        click.echo(f"Error: {data}", err=True)
        raise SystemExit(1)

    comments = data.get("comments", [])

    if not comments:
        click.echo(f"No comments on work item #{work_item_id}")
        return

    click.echo(f"Comments on work item #{work_item_id} ({len(comments)}):")
    for c in comments:
        author = c.get("createdBy", {}).get("displayName", "Unknown")
        date = c.get("createdDate", "")[:10]
        text = strip_html(c.get("text", ""))
        click.echo(f"\n  [{date}] {author}:")
        for line in text.split("\n"):
            click.echo(f"    {line}")
