import asyncio
import json
from unittest.mock import AsyncMock, patch

import click
import pytest

from ado_search.fields import DEFAULT_FIELDS, get_fields, pick_fields
from ado_search.runner import CommandResult

DESCRIPTION = "<p><strong>What’s wrong.</strong> <code>a &gt; b</code></p>\n<ul><li>x</li></ul>"

ITEM = {
    "id": 42,
    "fields": {
        "System.WorkItemType": "Bug",
        "System.Title": "Broken",
        "System.State": "New",
        "System.AssignedTo": {
            "displayName": "Jane Doe", "uniqueName": "Jane.Doe@co.com", "id": "abc",
        },
        "System.Tags": "Data Load; Prod",
        "Microsoft.VSTS.Scheduling.StoryPoints": 5.0,
        "System.Description": DESCRIPTION,
        "Microsoft.VSTS.Common.AcceptanceCriteria": "<div>Residual = 0</div>",
        "System.CreatedBy": {"displayName": "Builder"},
    },
}


def test_pick_fields_defaults_in_order_with_raw_html():
    values = pick_fields(ITEM, DEFAULT_FIELDS)
    assert list(values) == list(DEFAULT_FIELDS)
    assert values["System.Description"] == DESCRIPTION
    assert values["Microsoft.VSTS.Common.AcceptanceCriteria"] == "<div>Residual = 0</div>"
    assert values["Microsoft.VSTS.Scheduling.StoryPoints"] == 5.0


def test_pick_fields_identity_becomes_unique_name():
    values = pick_fields(ITEM, ["System.AssignedTo", "System.CreatedBy"])
    assert values == {"System.AssignedTo": "Jane.Doe@co.com", "System.CreatedBy": "Builder"}


def test_pick_fields_missing_is_none_and_match_ignores_case():
    values = pick_fields(ITEM, ["system.title", "Custom.Nope"])
    assert values == {"system.title": "Broken", "Custom.Nope": None}


def _run_get_fields(side_effect, **kwargs):
    with patch("ado_search.runner.run_operation", new_callable=AsyncMock, side_effect=side_effect) as mock_op:
        result = asyncio.run(get_fields(
            org="https://dev.azure.com/co", project="P",
            auth_method="pat", pat="fake", work_item_id=42, **kwargs,
        ))
    return result, mock_op


def _ok(*args, **kwargs):
    return CommandResult(command=["mock"], returncode=0, stdout=json.dumps(ITEM), stderr="")


def test_get_fields_reads_live_item():
    result, mock_op = _run_get_fields(_ok)
    mock_op.assert_called_once()
    assert mock_op.call_args.args == ("pat", "get-fields")
    assert mock_op.call_args.kwargs["work_item_id"] == 42
    assert "body" not in mock_op.call_args.kwargs
    assert result["System.Description"] == DESCRIPTION
    assert list(result) == list(DEFAULT_FIELDS)


def test_get_fields_requested_fields_only():
    result, _ = _run_get_fields(_ok, fields=["System.Title", "System.Description"])
    assert result == {"System.Title": "Broken", "System.Description": DESCRIPTION}


def test_get_fields_error_raises():
    def fail(*args, **kwargs):
        return CommandResult(command=["mock"], returncode=1, stdout="", stderr="HTTP 404")

    with pytest.raises(click.ClickException, match="HTTP 404"):
        _run_get_fields(fail)
