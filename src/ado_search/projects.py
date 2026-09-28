"""Project resolution, per-project sync watermarks, and project helpers."""
from __future__ import annotations

import click

from ado_search.auth import OP_PROJECT_LIST, PROJECT_LIST_TOP
from ado_search.runner import fetch_and_parse

ALL_PROJECTS = "*"


def configured_projects(cfg: dict) -> list[str]:
    """Projects to sync: organization.projects if set, else the legacy single project."""
    org = cfg.get("organization", {})
    projects = [p for p in (org.get("projects") or []) if p]
    if projects:
        return projects
    legacy = org.get("project", "")
    return [legacy] if legacy else []


def expand_projects(projects: list[str], remote_names: list[str]) -> list[str]:
    """Replace a "*" entry with every project the credentials can see."""
    if ALL_PROJECTS not in projects:
        return list(projects)
    return sorted(set(remote_names), key=str.lower)


def default_project(cfg: dict) -> str:
    """Project used by write commands and wiki sync."""
    org = cfg.get("organization", {})
    if org.get("project"):
        return org["project"]
    for p in org.get("projects") or []:
        if p and p != ALL_PROJECTS:
            return p
    raise ValueError(
        'No default project. Set organization.project in config.toml '
        '(projects = ["*"] has no default).'
    )


def watermark_for(cfg: dict, project: str) -> str:
    """Incremental-sync watermark for one project ("" means full sync)."""
    sync = cfg.get("sync", {})
    by_project = sync.get("last_sync_by_project") or {}
    if project in by_project:
        return by_project[project]
    if project and project == cfg.get("organization", {}).get("project", ""):
        return sync.get("last_sync", "")
    return ""


def record_watermark(cfg: dict, project: str, date: str) -> None:
    """Record a successful sync of one project.

    The legacy project's old watermark is copied into the per-project table
    first, so bumping the legacy ``last_sync`` never skips its changes.
    """
    sync = cfg.setdefault("sync", {})
    by_project = sync.setdefault("last_sync_by_project", {})
    legacy = cfg.get("organization", {}).get("project", "")
    if legacy and legacy not in by_project and sync.get("last_sync"):
        by_project[legacy] = sync["last_sync"]
    by_project[project] = date
    sync["last_sync"] = date


def project_from_area(area: str) -> str:
    """The first segment of an ADO area path is always the project name."""
    return area.split("\\", 1)[0] if area else ""


def record_project(record: dict) -> str:
    return record.get("project") or project_from_area(record.get("area") or "")


async def fetch_remote_projects(*, auth_method: str, org: str, pat: str = "") -> list[str]:
    data = await fetch_and_parse(
        auth_method, OP_PROJECT_LIST, "project list", org=org, project="", pat=pat,
    )
    if isinstance(data, str):
        raise RuntimeError(data)
    names = sorted((p["name"] for p in data.get("value", [])), key=str.lower)
    if len(names) >= PROJECT_LIST_TOP:
        click.echo(
            f"  Warning: project list capped at {PROJECT_LIST_TOP}; list projects explicitly",
            err=True,
        )
    return names
