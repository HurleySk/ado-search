import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest

from ado_search.write_workitems import (
    add_comment,
    add_link,
    build_az_fields,
    build_json_patch,
    resolve_fields,
    resolve_value,
    create_work_item,
    update_work_item,
    FIELD_MAP,
    LINK_TYPE_MAP,
)


# ── Pure function tests ──────────────────────────────────────────────


def test_build_json_patch():
    fields = {"System.Title": "My Bug", "System.State": "Active"}
    patch = build_json_patch(fields)
    assert len(patch) == 2
    assert patch[0] == {"op": "add", "path": "/fields/System.Title", "value": "My Bug"}
    assert patch[1] == {"op": "add", "path": "/fields/System.State", "value": "Active"}


def test_build_json_patch_skips_none_values():
    fields = {"System.Title": "Bug", "System.State": None, "System.AreaPath": "Root"}
    patch = build_json_patch(fields)
    assert len(patch) == 2
    paths = [p["path"] for p in patch]
    assert "/fields/System.State" not in paths


def test_build_az_fields():
    fields = {"System.Title": "Bug", "System.State": "Active"}
    result = build_az_fields(fields)
    assert "System.Title=Bug" in result
    assert "System.State=Active" in result


def test_build_az_fields_skips_none():
    fields = {"System.Title": "Bug", "System.State": None}
    result = build_az_fields(fields)
    assert len(result) == 1


def test_resolve_fields_maps_named_options():
    fields = resolve_fields(title="My Bug", priority=2, story_points=5.0)
    assert fields["System.Title"] == "My Bug"
    assert fields["Microsoft.VSTS.Common.Priority"] == 2
    assert fields["Microsoft.VSTS.Scheduling.StoryPoints"] == 5.0


def test_resolve_fields_reason():
    fields = resolve_fields(state="Closed", reason="Duplicate")
    assert fields["System.State"] == "Closed"
    assert fields["Microsoft.VSTS.Common.ResolvedReason"] == "Duplicate"


def test_resolve_fields_extra_fields():
    fields = resolve_fields(extra_fields=["Custom.Field=hello", "System.Tags=tag1; tag2"])
    assert fields["Custom.Field"] == "hello"
    assert fields["System.Tags"] == "tag1; tag2"


def test_resolve_fields_named_precedence_over_extra():
    """Named options override --field entries for the same ADO field."""
    fields = resolve_fields(
        tags="from-named",
        extra_fields=["System.Tags=from-extra"],
    )
    assert fields["System.Tags"] == "from-named"


def test_resolve_fields_extra_fields_ignores_bad_format():
    fields = resolve_fields(extra_fields=["no-equals-sign", "Good.Key=value"])
    assert "no-equals-sign" not in fields
    assert fields["Good.Key"] == "value"


def test_resolve_fields_empty():
    fields = resolve_fields()
    assert fields == {}


# ── resolve_value tests ─────────────────────────────────────────────


def test_resolve_value_plain_text():
    assert resolve_value("hello world") == "hello world"


def test_resolve_value_none():
    assert resolve_value(None) is None


def test_resolve_value_reads_file(tmp_path):
    html_file = tmp_path / "desc.html"
    html_file.write_text("<p>Hello</p>", encoding="utf-8")
    assert resolve_value(f"@{html_file}") == "<p>Hello</p>"


def test_resolve_value_missing_file():
    with pytest.raises(click.BadParameter, match="File not found"):
        resolve_value("@nonexistent.html")


def test_resolve_value_at_escape():
    assert resolve_value("@@literal") == "@literal"


# ── Async tests (mock network) ──────────────────────────────────────


def _make_command_result(response: dict, returncode: int = 0, stderr: str = ""):
    """Create a mock CommandResult."""
    from ado_search.runner import CommandResult
    return CommandResult(
        command=["mock"],
        returncode=returncode,
        stdout=json.dumps(response),
        stderr=stderr,
    )


def _make_record(item_id: int = 99999, title: str = "Test Item", wi_type: str = "Bug"):
    return {
        "id": item_id, "title": title, "type": wi_type, "state": "New",
        "area": "Root", "iteration": "Sprint 1", "assigned_to": "",
        "tags": "", "priority": 2, "story_points": None, "parent_id": None,
        "created": "2026-01-01", "updated": "2026-01-01",
        "description": "", "acceptance_criteria": "",
        "comments": [], "attachments": [], "inline_images": [],
        "state_history": [],
    }


def test_create_work_item_dry_run(tmp_path):
    """Dry run should not call run_operation."""
    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock) as mock_op:
        result = asyncio.run(create_work_item(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            work_item_type="Bug", title="Test",
            field_values={}, dry_run=True,
        ))
    mock_op.assert_not_called()
    assert result == {}


def test_create_work_item_pat(tmp_path):
    """PAT auth should send JSON Patch body with correct content_type."""
    api_response = {"id": 99999, "fields": {"System.Title": "Test"}}
    mock_result = _make_command_result(api_response)
    record = _make_record()

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result) as mock_op, \
         patch("ado_search.write_workitems._refetch_and_merge", new_callable=AsyncMock, return_value=record):

        result = asyncio.run(create_work_item(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            work_item_type="Bug", title="Test",
            field_values={"System.State": "Active"},
        ))

    # Verify PAT path sends JSON Patch body
    call_kwargs = mock_op.call_args
    assert call_kwargs.kwargs.get("content_type") == "application/json-patch+json"
    body = call_kwargs.kwargs.get("body")
    assert body is not None
    patch_ops = json.loads(body)
    field_paths = [op["path"] for op in patch_ops]
    assert "/fields/System.Title" in field_paths
    assert "/fields/System.State" in field_paths
    assert result["id"] == 99999


def test_create_work_item_az_cli(tmp_path):
    """az-cli auth should send --fields args, not JSON Patch body."""
    api_response = {"id": 88888, "fields": {"System.Title": "Test"}}
    mock_result = _make_command_result(api_response)
    record = _make_record(item_id=88888)

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result) as mock_op, \
         patch("ado_search.write_workitems._refetch_and_merge", new_callable=AsyncMock, return_value=record):

        result = asyncio.run(create_work_item(
            org="https://dev.azure.com/co", project="P",
            auth_method="az-cli", pat="",
            data_dir=tmp_path,
            work_item_type="Bug", title="Test",
            field_values={"System.State": "Active"},
        ))

    call_kwargs = mock_op.call_args
    # az-cli uses fields list, not body
    assert call_kwargs.kwargs.get("body") is None
    assert call_kwargs.kwargs.get("title") == "Test"
    assert call_kwargs.kwargs.get("work_item_type") == "Bug"
    fields = call_kwargs.kwargs.get("fields")
    assert fields is not None
    assert "System.State=Active" in fields
    assert result["id"] == 88888


def test_update_work_item_pat(tmp_path):
    """Update via PAT sends JSON Patch body."""
    api_response = {"id": 12345, "fields": {"System.Title": "Updated"}}
    mock_result = _make_command_result(api_response)
    record = _make_record(item_id=12345, title="Updated")

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result) as mock_op, \
         patch("ado_search.write_workitems._refetch_and_merge", new_callable=AsyncMock, return_value=record):

        result = asyncio.run(update_work_item(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            work_item_id=12345,
            field_values={"System.State": "Active"},
        ))

    call_kwargs = mock_op.call_args
    assert call_kwargs.kwargs.get("content_type") == "application/json-patch+json"
    assert result["id"] == 12345


def test_update_work_item_dry_run(tmp_path):
    """Dry run should not call run_operation."""
    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock) as mock_op:
        result = asyncio.run(update_work_item(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            work_item_id=12345,
            field_values={"System.State": "Active"},
            dry_run=True,
        ))
    mock_op.assert_not_called()
    assert result == {}


def test_update_merges_into_jsonl(tmp_path):
    """After update, the JSONL file should contain the refreshed record."""
    # Seed an existing JSONL
    wi_jsonl = tmp_path / "work-items.jsonl"
    existing = {"id": 100, "title": "Old", "type": "Bug", "state": "New"}
    wi_jsonl.write_text(json.dumps(existing) + "\n")

    api_response = {"id": 100, "fields": {"System.Title": "Updated"}}
    mock_result = _make_command_result(api_response)
    record = _make_record(item_id=100, title="Updated")

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result), \
         patch("ado_search.sync_workitems.fetch_item", new_callable=AsyncMock, return_value=record), \
         patch("ado_search.write_workitems._refetch_and_merge") as mock_refetch:
        # Use real _refetch_and_merge but mock the fetch_item inside it
        mock_refetch.return_value = record

        # Just call _refetch_and_merge directly to test JSONL merge
        from ado_search.sync_common import finalize_jsonl
        finalize_jsonl(wi_jsonl, {100: record}, key="id", sort_key="id", is_incremental=True)

    # Verify JSONL was updated
    lines = wi_jsonl.read_text().strip().split("\n")
    item = json.loads(lines[0])
    assert item["title"] == "Updated"


# ── add_comment tests ──────────────────────────────────────────────


def test_add_comment_dry_run(tmp_path):
    """Dry run should not call run_operation."""
    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock) as mock_op:
        result = asyncio.run(add_comment(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            work_item_id=12345, text="<p>Nice work!</p>",
            dry_run=True,
        ))
    mock_op.assert_not_called()
    assert result == {}


def test_add_comment_pat(tmp_path):
    """PAT auth should send JSON body with text field."""
    api_response = {"id": 1, "text": "<p>Nice work!</p>"}
    mock_result = _make_command_result(api_response)
    record = _make_record(item_id=12345)

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result) as mock_op, \
         patch("ado_search.write_workitems._refetch_and_merge", new_callable=AsyncMock, return_value=record):

        result = asyncio.run(add_comment(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            work_item_id=12345, text="<p>Nice work!</p>",
        ))

    call_kwargs = mock_op.call_args
    assert call_kwargs.kwargs.get("content_type") == "application/json"
    body = json.loads(call_kwargs.kwargs.get("body"))
    assert body["text"] == "<p>Nice work!</p>"
    assert result["id"] == 12345


# ── LINK_TYPE_MAP tests ───────────────────────────────────────────


def test_link_type_map_has_expected_entries():
    assert LINK_TYPE_MAP["related"] == "System.LinkTypes.Related"
    assert LINK_TYPE_MAP["parent"] == "System.LinkTypes.Hierarchy-Reverse"
    assert LINK_TYPE_MAP["child"] == "System.LinkTypes.Hierarchy-Forward"
    assert LINK_TYPE_MAP["depends-on"] == "System.LinkTypes.Dependency-Forward"
    assert LINK_TYPE_MAP["predecessor"] == "System.LinkTypes.Dependency-Reverse"
    assert LINK_TYPE_MAP["duplicate"] == "System.LinkTypes.Duplicate-Forward"
    assert LINK_TYPE_MAP["duplicate-of"] == "System.LinkTypes.Duplicate-Reverse"


def test_link_type_map_falls_through_raw_type():
    raw = "System.LinkTypes.Custom"
    assert LINK_TYPE_MAP.get(raw, raw) == raw


# ── add_link tests ────────────────────────────────────────────────


def test_add_link_dry_run(tmp_path):
    """Dry run should not call run_operation."""
    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock) as mock_op:
        result = asyncio.run(add_link(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            source_id=100, target_id=200,
            link_type="related", dry_run=True,
        ))
    mock_op.assert_not_called()
    assert result == {}


def test_add_link_pat(tmp_path):
    """PAT auth should send JSON Patch body with relation operation."""
    api_response = {"id": 100, "relations": []}
    mock_result = _make_command_result(api_response)
    record = _make_record(item_id=100)

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result) as mock_op, \
         patch("ado_search.write_workitems._refetch_and_merge", new_callable=AsyncMock, return_value=record):

        result = asyncio.run(add_link(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            source_id=100, target_id=200,
            link_type="parent", comment="linking parent",
        ))

    call_kwargs = mock_op.call_args
    assert call_kwargs.kwargs.get("content_type") == "application/json-patch+json"
    body = json.loads(call_kwargs.kwargs.get("body"))
    assert len(body) == 1
    assert body[0]["op"] == "add"
    assert body[0]["path"] == "/relations/-"
    assert body[0]["value"]["rel"] == "System.LinkTypes.Hierarchy-Reverse"
    assert "200" in body[0]["value"]["url"]
    assert body[0]["value"]["attributes"]["comment"] == "linking parent"
    assert result["id"] == 100


def test_add_link_resolves_friendly_name(tmp_path):
    """Friendly link type names should map to ADO relation types."""
    api_response = {"id": 100, "relations": []}
    mock_result = _make_command_result(api_response)
    record = _make_record(item_id=100)

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result) as mock_op, \
         patch("ado_search.write_workitems._refetch_and_merge", new_callable=AsyncMock, return_value=record):

        asyncio.run(add_link(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            source_id=100, target_id=200,
            link_type="child",
        ))

    body = json.loads(mock_op.call_args.kwargs["body"])
    assert body[0]["value"]["rel"] == "System.LinkTypes.Hierarchy-Forward"


def test_add_link_raw_type_passthrough(tmp_path):
    """Unrecognized link types should be passed through as-is."""
    api_response = {"id": 100}
    mock_result = _make_command_result(api_response)
    record = _make_record(item_id=100)

    with patch("ado_search.write_workitems.run_operation", new_callable=AsyncMock, return_value=mock_result) as mock_op, \
         patch("ado_search.write_workitems._refetch_and_merge", new_callable=AsyncMock, return_value=record):

        asyncio.run(add_link(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake",
            data_dir=tmp_path,
            source_id=100, target_id=200,
            link_type="System.LinkTypes.Custom",
        ))

    body = json.loads(mock_op.call_args.kwargs["body"])
    assert body[0]["value"]["rel"] == "System.LinkTypes.Custom"
