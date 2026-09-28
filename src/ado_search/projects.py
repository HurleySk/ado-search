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


def same_project(a: str, b: str) -> bool:
    """Azure DevOps project names are case-insensitive."""
    return a.casefold() == b.casefold()


def _watermark_key(by_project: dict, project: str) -> str:
    return next((k for k in by_project if same_project(k, project)), project)


def watermark_for(cfg: dict, project: str) -> str:
    """Incremental-sync watermark for one project ("" means full sync)."""
    sync = cfg.get("sync", {})
    by_project = sync.get("last_sync_by_project") or {}
    key = _watermark_key(by_project, project)
    if key in by_project:
        return by_project[key]
    legacy = cfg.get("organization", {}).get("project", "")
    if project and legacy and same_project(project, legacy):
        return sync.get("last_sync", "")
    return ""


def record_watermark(cfg: dict, project: str, date: str) -> None:
    """Record a successful sync of one project.

    The legacy ``last_sync`` only ever tracks the legacy project, so syncing
    another project never advances it (and older versions keep reading a
    correct value). Its old value is copied into the per-project table first.
    """
    sync = cfg.setdefault("sync", {})
    by_project = sync.setdefault("last_sync_by_project", {})
    legacy = cfg.get("organization", {}).get("project", "")
    if legacy and _watermark_key(by_project, legacy) not in by_project and sync.get("last_sync"):
        by_project[legacy] = sync["last_sync"]
    by_project[_watermark_key(by_project, project)] = date
    if legacy and same_project(project, legacy):
        sync["last_sync"] = date


def project_from_area(area: str) -> str:
    """The first segment of an ADO area path is always the project name."""
    return area.split("\\", 1)[0] if area else ""


def record_project(record: dict) -> str:
    return record.get("project") or project_from_area(record.get("area") or "")


def in_project(record: dict, project: str) -> bool:
    """Full-sync orphan scope: the record belongs to ``project`` or has no project."""
    owner = record_project(record)
    return not owner or same_project(owner, project)


def area_paths_for(area_paths: list[str], project: str) -> list[str]:
    """The configured area paths under ``project`` (an area path starts with its project)."""
    return [a for a in area_paths if same_project(project_from_area(a), project)]


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
