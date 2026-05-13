from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from ado_search.auth import OP_ADD_LINK, OP_UPLOAD_ATTACHMENT
from ado_search.runner import run_command, run_operation
from ado_search.write_workitems import _check_and_refetch


async def upload_attachment(
    work_item_id: int,
    file_path: Path,
    *,
    org: str,
    project: str,
    auth_method: str,
    pat: str = "",
    data_dir: Path,
    dry_run: bool = False,
) -> dict:
    """Upload a file as an attachment to an ADO work item.

    Step 1: POST binary to the attachments endpoint → returns attachment URL.
    Step 2: PATCH work item with an AttachedFile relation pointing at that URL.
    """
    if dry_run:
        click.echo(f"[dry-run] Would upload {file_path.name!r} to work item #{work_item_id}")
        return {}

    # ADO attachments API only accepts application/octet-stream for binary uploads
    content_type = "application/octet-stream"

    # Step 1: Upload the binary file
    if auth_method == "pat":
        file_bytes = file_path.read_bytes()
        upload_result = await run_operation(
            auth_method, OP_UPLOAD_ATTACHMENT,
            org=org, project=project, pat=pat,
            path=file_path.name,
            body=file_bytes,
            content_type=content_type,
        )
    else:
        # For shell-based auth methods, use Invoke-RestMethod -InFile to preserve
        # binary integrity (az rest --body and pwsh body-string both corrupt binary).
        from ado_search.auth import OPERATIONS, _resolve_url, build_upload_command
        op = OPERATIONS[OP_UPLOAD_ATTACHMENT]
        upload_url = _resolve_url(op, org=org, project=project, path=file_path.name)
        cmd = build_upload_command(upload_url, file_path, auth_method, content_type=content_type)
        upload_result = await run_command(cmd)

    if upload_result.returncode != 0:
        raise RuntimeError(f"Attachment upload failed: {upload_result.stderr}")

    attachment_url = upload_result.parse_json()["url"]

    # Step 2: PATCH the work item to link the uploaded attachment
    patch = [
        {
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "AttachedFile",
                "url": attachment_url,
                "attributes": {"name": file_path.name},
            },
        }
    ]
    link_result = await run_operation(
        auth_method, OP_ADD_LINK,
        org=org, project=project, pat=pat,
        work_item_id=work_item_id,
        body=json.dumps(patch),
        content_type="application/json-patch+json",
    )

    return await _check_and_refetch(
        link_result,
        f"attaching {file_path.name!r} to #{work_item_id}",
        work_item_id,
        org=org, project=project, auth_method=auth_method, pat=pat, data_dir=data_dir,
    )
