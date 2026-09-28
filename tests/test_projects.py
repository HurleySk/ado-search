import asyncio
import json
from unittest.mock import patch

import pytest

from ado_search.config import default_config
from ado_search.projects import (
    ALL_PROJECTS,
    area_paths_for,
    configured_projects,
    default_project,
    expand_projects,
    fetch_remote_projects,
    in_project,
    project_from_area,
    record_project,
    record_watermark,
    same_project,
    watermark_for,
)
from ado_search.runner import CommandResult


def _cfg(project="", projects=None, last_sync="", by_project=None):
    cfg = default_config()
    cfg["organization"]["url"] = "https://dev.azure.com/contoso"
    cfg["organization"]["project"] = project
    if projects is not None:
        cfg["organization"]["projects"] = projects
    cfg["sync"]["last_sync"] = last_sync
    if by_project is not None:
        cfg["sync"]["last_sync_by_project"] = by_project
    return cfg


def test_configured_projects_legacy_single():
    assert configured_projects(_cfg(project="Alpha")) == ["Alpha"]


def test_configured_projects_list_wins_over_legacy():
    assert configured_projects(_cfg(project="Alpha", projects=["Beta", "Gamma"])) == ["Beta", "Gamma"]


def test_configured_projects_empty():
    assert configured_projects(_cfg()) == []


def test_expand_projects_star_uses_remote_sorted():
    assert expand_projects([ALL_PROJECTS], ["beta", "Alpha"]) == ["Alpha", "beta"]


def test_expand_projects_without_star_is_identity():
    assert expand_projects(["Alpha"], ["Beta"]) == ["Alpha"]


def test_default_project_prefers_legacy():
    assert default_project(_cfg(project="Alpha", projects=["Beta"])) == "Alpha"


def test_default_project_first_explicit_entry():
    assert default_project(_cfg(projects=["*", "Beta"])) == "Beta"


def test_default_project_star_only_raises():
    with pytest.raises(ValueError, match="organization.project"):
        default_project(_cfg(projects=["*"]))


def test_watermark_for_per_project_entry():
    cfg = _cfg(project="Alpha", last_sync="2026-01-01", by_project={"Beta": "2026-02-02"})
    assert watermark_for(cfg, "Beta") == "2026-02-02"


def test_watermark_for_legacy_project_falls_back_to_last_sync():
    cfg = _cfg(project="Alpha", projects=["Alpha", "Beta"], last_sync="2026-01-01")
    assert watermark_for(cfg, "Alpha") == "2026-01-01"


def test_watermark_for_new_project_is_empty():
    cfg = _cfg(project="Alpha", projects=["Alpha", "Beta"], last_sync="2026-01-01")
    assert watermark_for(cfg, "Beta") == ""


def test_record_watermark_migrates_legacy_before_overwriting():
    cfg = _cfg(project="Alpha", projects=["Alpha", "Beta"], last_sync="2026-01-01")
    record_watermark(cfg, "Beta", "2026-09-28")
    assert cfg["sync"]["last_sync_by_project"] == {"Alpha": "2026-01-01", "Beta": "2026-09-28"}
    # last_sync only tracks the legacy project (1.13 reads it for Alpha)
    assert cfg["sync"]["last_sync"] == "2026-01-01"
    # Alpha was not synced this run, so its own watermark is untouched
    assert watermark_for(cfg, "Alpha") == "2026-01-01"


def test_record_watermark_other_project_never_advances_unsynced_default():
    cfg = _cfg(project="Alpha", projects=["Alpha", "Beta"])
    record_watermark(cfg, "Beta", "2026-09-28")
    assert cfg["sync"]["last_sync"] == ""
    assert watermark_for(cfg, "Alpha") == ""


def test_record_watermark_legacy_project_updates_last_sync_ignoring_case():
    cfg = _cfg(project="Alpha", last_sync="2026-01-01")
    record_watermark(cfg, "alpha", "2026-09-28")
    assert cfg["sync"]["last_sync"] == "2026-09-28"
    assert watermark_for(cfg, "Alpha") == "2026-09-28"


def test_watermark_for_ignores_case():
    cfg = _cfg(project="myproject", last_sync="2026-01-01", by_project={"Beta": "2026-02-02"})
    assert watermark_for(cfg, "MyProject") == "2026-01-01"
    assert watermark_for(cfg, "beta") == "2026-02-02"


def test_record_watermark_reuses_existing_key_ignoring_case():
    cfg = _cfg(by_project={"Beta": "2026-02-02"})
    record_watermark(cfg, "beta", "2026-09-28")
    assert cfg["sync"]["last_sync_by_project"] == {"Beta": "2026-09-28"}


def test_same_project_ignores_case():
    assert same_project("MyProject", "myproject")
    assert not same_project("Alpha", "Beta")


def test_in_project_scope():
    assert in_project({"project": "MyProject"}, "myproject")
    assert in_project({"area": r"myproject\Team"}, "MyProject")
    assert in_project({"area": ""}, "Alpha")  # projectless records are in scope
    assert not in_project({"project": "Beta"}, "Alpha")


def test_area_paths_for_keeps_only_this_projects_paths():
    paths = [r"Alpha\Web", r"beta\Api", "Beta"]
    assert area_paths_for(paths, "Beta") == [r"beta\Api", "Beta"]
    assert area_paths_for(paths, "Gamma") == []


def test_project_from_area():
    assert project_from_area(r"Alpha\Web\API") == "Alpha"
    assert project_from_area("Alpha") == "Alpha"
    assert project_from_area("") == ""


def test_record_project_prefers_field_then_area():
    assert record_project({"project": "Beta", "area": r"Alpha\X"}) == "Beta"
    assert record_project({"area": r"Alpha\X"}) == "Alpha"
    assert record_project({"project": "", "area": ""}) == ""


def test_fetch_remote_projects_parses_names():
    payload = json.dumps({"count": 2, "value": [{"name": "Beta"}, {"name": "Alpha"}]})
    seen = []

    async def fake_run(cmd, **kwargs):
        seen.append(" ".join(str(c) for c in cmd))
        return CommandResult(command=cmd, returncode=0, stdout=payload, stderr="")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        names = asyncio.run(fetch_remote_projects(
            auth_method="az-cli", org="https://dev.azure.com/contoso",
        ))

    assert names == ["Alpha", "Beta"]
    assert "https://dev.azure.com/contoso/_apis/projects" in seen[0]


def test_fetch_remote_projects_raises_on_failure():
    async def fake_run(cmd, **kwargs):
        return CommandResult(command=cmd, returncode=1, stdout="", stderr="401 Unauthorized")

    with patch("ado_search.runner.run_command", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="project list"):
            asyncio.run(fetch_remote_projects(
                auth_method="az-cli", org="https://dev.azure.com/contoso",
            ))
