from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from ado_search.config import default_config, load_config, save_config
from ado_search.db import Database
from ado_search.search import search, format_results


def _default_data_dir() -> Path:
    return Path.cwd() / ".ado-search"


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

    db = Database(data_path / "index.db")
    db.initialize()
    db.close()

    (data_path / "work-items").mkdir(exist_ok=True)
    (data_path / "wiki").mkdir(exist_ok=True)

    click.echo(f"Initialized ado-search at {data_path}")
    click.echo(f"  Organization: {org}")
    click.echo(f"  Project: {project}")
    click.echo(f"  Auth method: {auth_method}")


@main.command()
@click.option("--data-dir", type=click.Path(exists=True), default=None)
@click.option("--dry-run", is_flag=True, help="Show what would be synced without writing")
def sync(data_dir: str | None, dry_run: bool):
    """Sync work items and wiki pages from Azure DevOps."""
    data_path = Path(data_dir) if data_dir else _default_data_dir()
    config_path = data_path / "config.toml"

    if not config_path.exists():
        click.echo("Error: Not initialized. Run 'ado-search init' first.", err=True)
        raise SystemExit(1)

    cfg = load_config(config_path)
    org = cfg["organization"]["url"]
    project = cfg["organization"]["project"]
    auth_method = cfg["auth"]["method"]
    sync_cfg = cfg["sync"]

    # Resolve PAT from config or env var
    pat = ""
    if auth_method == "pat":
        from ado_search.auth import get_pat
        pat = get_pat(cfg)

    db = Database(data_path / "index.db")
    db.initialize()

    try:
        from ado_search.sync_workitems import sync_work_items
        from ado_search.sync_wiki import sync_wiki

        click.echo("Syncing work items...")
        wi_stats = asyncio.run(sync_work_items(
            org=org, project=project, auth_method=auth_method, pat=pat,
            data_dir=data_path, db=db,
            work_item_types=sync_cfg.get("work_item_types", []),
            area_paths=sync_cfg.get("area_paths", []),
            states=sync_cfg.get("states", []),
            last_sync=sync_cfg.get("last_sync", ""),
            max_concurrent=sync_cfg.get("performance", {}).get("max_concurrent", 5),
            dry_run=dry_run,
        ))
        click.echo(f"  Work items: {wi_stats['fetched']} synced, {wi_stats['errors']} errors")

        click.echo("Syncing wiki pages...")
        wiki_stats = asyncio.run(sync_wiki(
            org=org, project=project, auth_method=auth_method, pat=pat,
            data_dir=data_path, db=db,
            wiki_names=sync_cfg.get("wiki_names", []),
            max_concurrent=sync_cfg.get("performance", {}).get("max_concurrent", 5),
            dry_run=dry_run,
        ))
        click.echo(f"  Wiki pages: {wiki_stats['fetched']} synced, {wiki_stats['errors']} errors")

        if not dry_run:
            cfg["sync"]["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            save_config(cfg, config_path)
            click.echo("Sync complete.")

    finally:
        db.close()


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

    if not (data_path / "index.db").exists():
        click.echo("Error: No index found. Run 'ado-search init' and 'ado-search sync' first.", err=True)
        raise SystemExit(1)

    db = Database(data_path / "index.db")
    db.initialize()

    try:
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

    finally:
        db.close()


@main.command()
@click.argument("item_id")
@click.option("--data-dir", type=click.Path(), default=None)
def show(item_id: str, data_dir: str | None):
    """Show full content of a work item or wiki page."""
    data_path = Path(data_dir) if data_dir else _default_data_dir()

    wi_path = data_path / "work-items" / f"{item_id}.md"
    if wi_path.exists():
        click.echo(wi_path.read_text(encoding="utf-8"))
        return

    wiki_path = data_path / "wiki" / f"{item_id.lstrip('/')}.md"
    if wiki_path.exists():
        click.echo(wiki_path.read_text(encoding="utf-8"))
        return

    click.echo(f"Error: Item '{item_id}' not found in work-items or wiki.", err=True)
    raise SystemExit(1)
