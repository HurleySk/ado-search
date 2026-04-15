from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote

ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"

# Operation constants — single source of truth for operation names
OP_QUERY = "query"
OP_SHOW = "show"
OP_WIKI_LIST = "wiki-list"
OP_WIKI_PAGE_LIST = "wiki-page-list"
OP_WIKI_PAGE_SHOW = "wiki-page-show"
OP_COMMENTS = "comments"
OP_UPDATES = "updates"
OP_ODATA_QUERY = "odata-query"
OP_ATTACHMENT = "attachment"
OP_CREATE = "create"
OP_UPDATE = "update"
OP_ADD_COMMENT = "add-comment"


@dataclass
class OperationDef:
    """Defines an ADO REST API operation."""
    method: str = "GET"
    # URL path template — interpolated with resolve_url kwargs
    path: str = ""
    # Extra query parameters appended to the URL
    query_params: list[str] = field(default_factory=list)
    # If True, the 'url' kwarg is used as-is (e.g., OData)
    raw_url: bool = False
    # If True, the request has a JSON body (only for POST operations)
    has_body: bool = False
    # For az CLI: use az rest (True) or a specific az subcommand (False)
    use_az_rest: bool = True
    # For az CLI non-rest: the subcommand parts
    az_cli_cmd: list[str] = field(default_factory=list)
    # For az CLI non-rest: builds extra args from kwargs (org, project already handled)
    az_cli_args: Callable[..., list[str]] | None = None
    # For az CLI non-rest: whether to include --project in the command
    az_cli_include_project: bool = True
    # For az rest: pass query_params via --url-parameters instead of query string
    az_rest_url_parameters: bool = False


def _resolve_url(op: OperationDef, *, org: str, project: str, **kwargs) -> str:
    """Build the full API URL from an operation definition."""
    if op.raw_url:
        return kwargs.get("url", "")

    url_project = quote(project or "", safe="")
    url_wiki = quote(kwargs.get("wiki") or "", safe="")
    work_item_id = kwargs.get("work_item_id", "")
    work_item_type = kwargs.get("work_item_type", "")
    path = kwargs.get("path", "")

    api_path = (
        op.path
        .replace("{url_project}", url_project)
        .replace("{url_wiki}", url_wiki)
        .replace("{work_item_id}", str(work_item_id))
        .replace("{work_item_type}", quote(str(work_item_type), safe=""))
    )

    api_url = f"{org}/{api_path}"

    # Build query params
    params = list(op.query_params)
    if "{path}" in " ".join(op.query_params):
        params = [p.replace("{path}", path) for p in params]
    if "{encoded_path}" in " ".join(op.query_params):
        params = [p.replace("{encoded_path}", quote(path or "", safe="")) for p in params]

    if params:
        api_url += "?" + "&".join(params)

    return api_url


OPERATIONS: dict[str, OperationDef] = {
    OP_QUERY: OperationDef(
        method="POST",
        path="{url_project}/_apis/wit/wiql",
        query_params=["api-version=7.1"],
        has_body=True,
        use_az_rest=False,
        az_cli_cmd=["boards", "query"],
        az_cli_args=lambda wiql, **_: ["--wiql", wiql],
    ),
    OP_SHOW: OperationDef(
        path="{url_project}/_apis/wit/workitems/{work_item_id}",
        query_params=["$expand=all", "api-version=7.1"],
        use_az_rest=False,
        az_cli_cmd=["boards", "work-item", "show"],
        az_cli_args=lambda work_item_id, **_: ["--id", str(work_item_id)],
        az_cli_include_project=False,
    ),
    OP_WIKI_LIST: OperationDef(
        path="{url_project}/_apis/wiki/wikis",
        query_params=["api-version=7.1"],
        use_az_rest=False,
        az_cli_cmd=["devops", "wiki", "list"],
        az_cli_args=lambda **_: [],
    ),
    OP_WIKI_PAGE_LIST: OperationDef(
        path="{url_project}/_apis/wiki/wikis/{url_wiki}/pages",
        query_params=["path=/", "recursionLevel=full", "api-version=7.1"],
        az_rest_url_parameters=True,
    ),
    OP_WIKI_PAGE_SHOW: OperationDef(
        path="{url_project}/_apis/wiki/wikis/{url_wiki}/pages",
        query_params=["path={path}", "includeContent=true", "api-version=7.1"],
        az_rest_url_parameters=True,
    ),
    OP_UPDATES: OperationDef(
        path="{url_project}/_apis/wit/workitems/{work_item_id}/updates",
        query_params=["api-version=7.1"],
    ),
    OP_COMMENTS: OperationDef(
        path="{url_project}/_apis/wit/workitems/{work_item_id}/comments",
        query_params=["api-version=7.1-preview.4"],
        use_az_rest=False,
        az_cli_cmd=["devops", "invoke"],
        az_cli_args=lambda work_item_id, **_: [
            "--area", "wit", "--resource", "comments",
            "--route-parameters", f"id={work_item_id}",
            "--api-version", "7.1-preview.4",
        ],
        az_cli_include_project=False,
    ),
    OP_ODATA_QUERY: OperationDef(
        raw_url=True,
    ),
    OP_ATTACHMENT: OperationDef(
        raw_url=True,
    ),
    OP_CREATE: OperationDef(
        method="POST",
        path="{url_project}/_apis/wit/workitems/${work_item_type}",
        query_params=["api-version=7.1"],
        has_body=True,
        use_az_rest=False,
        az_cli_cmd=["boards", "work-item", "create"],
        az_cli_args=lambda title, work_item_type, fields=None, **_: [
            "--title", title, "--type", work_item_type,
            *(["--fields", *fields] if fields else []),
        ],
    ),
    OP_UPDATE: OperationDef(
        method="PATCH",
        path="{url_project}/_apis/wit/workitems/{work_item_id}",
        query_params=["api-version=7.1"],
        has_body=True,
        use_az_rest=False,
        az_cli_cmd=["boards", "work-item", "update"],
        az_cli_args=lambda work_item_id, title=None, fields=None, **_: [
            "--id", str(work_item_id),
            *(["--title", title] if title else []),
            *(["--fields", *fields] if fields else []),
        ],
        az_cli_include_project=False,
    ),
    OP_ADD_COMMENT: OperationDef(
        method="POST",
        path="{url_project}/_apis/wit/workitems/{work_item_id}/comments",
        query_params=["api-version=7.1-preview.4"],
        has_body=True,
    ),
}


def build_az_cli_command(
    operation: str,
    *,
    org: str,
    project: str,
    wiql: str | None = None,
    work_item_id: int | None = None,
    wiki: str | None = None,
    path: str | None = None,
    url: str | None = None,
    work_item_type: str | None = None,
    title: str | None = None,
    fields: list[str] | None = None,
    body: str | bytes | None = None,
    content_type: str | None = None,
) -> list[str]:
    op = OPERATIONS.get(operation)
    if op is None:
        raise ValueError(f"Unknown operation: {operation}")

    kwargs = dict(wiql=wiql, work_item_id=work_item_id, wiki=wiki, path=path, url=url,
                  work_item_type=work_item_type, title=title, fields=fields)

    # Operations that use specific az CLI subcommands
    if not op.use_az_rest and op.az_cli_args is not None:
        cmd = ["az", *op.az_cli_cmd, *op.az_cli_args(**kwargs), "--org", org]
        if op.az_cli_include_project:
            cmd.extend(["--project", project])
        cmd.extend(["--output", "json"])
        return cmd

    # Operations that use az rest
    api_url = _resolve_url(op, org=org, project=project, **kwargs)

    # Some operations pass query params via --url-parameters instead of query string
    if op.az_rest_url_parameters:
        url_project = quote(project or "", safe="")
        url_wiki = quote(wiki or "", safe="")
        base_url = f"{org}/{url_project}/_apis/wiki/wikis/{url_wiki}/pages"
        url_params = [p.replace("{path}", path or "") for p in op.query_params]
        return ["az", "rest", "--method", "get",
                "--resource", ADO_RESOURCE_ID,
                "--url", base_url,
                "--url-parameters", *url_params,
                "--output", "json"]

    cmd = ["az", "rest", "--method", op.method.lower(),
           "--resource", ADO_RESOURCE_ID,
           "--url", api_url]
    if body is not None:
        cmd.extend(["--body", body if isinstance(body, str) else body.decode("utf-8")])
    if content_type:
        cmd.extend(["--headers", f"Content-Type={content_type}"])
    cmd.extend(["--output", "json"])
    return cmd


def build_powershell_command(
    operation: str,
    *,
    org: str,
    project: str,
    wiql: str | None = None,
    work_item_id: int | None = None,
    wiki: str | None = None,
    path: str | None = None,
    url: str | None = None,
    work_item_type: str | None = None,
    body: str | bytes | None = None,
    content_type: str | None = None,
    **_extra,
) -> list[str]:
    op = OPERATIONS.get(operation)
    if op is None:
        raise ValueError(f"Unknown operation: {operation}")

    token_expr = f"(Get-AzAccessToken -ResourceUrl '{ADO_RESOURCE_ID}').Token"
    ct = content_type or "application/json"
    headers = f'@{{Authorization = "Bearer $token"; "Content-Type" = "{ct}"}}'

    api_url = _resolve_url(op, org=org, project=project, wiki=wiki, path=path,
                           work_item_id=work_item_id, url=url,
                           work_item_type=work_item_type)

    # Escape for PowerShell
    if op.raw_url:
        safe_url = _escape_ps(api_url)
    else:
        safe_url = api_url  # already URL-encoded, no PS escaping needed

    method = op.method.capitalize()  # Get, Post, Patch

    if body is not None:
        body_str = body.decode("utf-8") if isinstance(body, bytes) else body
        escaped_body = _escape_ps(body_str)
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"$body = '{escaped_body}'; "
            f"Invoke-RestMethod -Uri '{safe_url}' -Method {method} -Headers $headers -Body $body | ConvertTo-Json -Depth 10"
        )
    elif op.has_body and operation == OP_QUERY:
        wiql_body = '{{"query": "{wiql}"}}'.replace("{wiql}", _escape_ps(wiql))
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"$body = '{wiql_body}'; "
            f"Invoke-RestMethod -Uri '{safe_url}' -Method Post -Headers $headers -Body $body | ConvertTo-Json -Depth 10"
        )
    else:
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{safe_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )

    return ["pwsh", "-NoProfile", "-Command", script]


def _escape_ps(s: str | None) -> str:
    if s is None:
        return ""
    return s.replace('"', '`"').replace("'", "''")


def build_command(operation: str, auth_method: str, **kwargs) -> list[str]:
    """Dispatch to az-cli or PowerShell command builder based on auth method."""
    if auth_method == "az-cli":
        return build_az_cli_command(operation, **kwargs)
    if auth_method == "pat":
        raise ValueError("PAT auth uses direct HTTP, not shell commands. Use pat_request() instead.")
    return build_powershell_command(operation, **kwargs)


def get_pat(config: dict | None = None) -> str:
    """Get PAT from env var or config. Env var takes precedence."""
    import os
    pat = os.environ.get("ADO_PAT", "")
    if not pat and config:
        pat = config.get("auth", {}).get("pat", "")
    if not pat:
        raise ValueError("No PAT found. Set ADO_PAT env var or auth.pat in config.toml")
    return pat


def pat_request(
    operation: str,
    *,
    org: str,
    project: str,
    pat: str,
    wiql: str | None = None,
    work_item_id: int | None = None,
    wiki: str | None = None,
    path: str | None = None,
    url: str | None = None,
    work_item_type: str | None = None,
    body: str | bytes | None = None,
    content_type: str | None = None,
    **_extra,
) -> dict | list:
    """Make a direct HTTP request using PAT Basic auth. Returns parsed JSON."""
    import base64
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    op = OPERATIONS.get(operation)
    if op is None:
        raise ValueError(f"Unknown operation: {operation}")

    api_url = _resolve_url(op, org=org, project=project, wiki=wiki, path=path,
                           work_item_id=work_item_id, url=url,
                           work_item_type=work_item_type)

    creds = base64.b64encode(f":{pat}".encode()).decode()
    ct = content_type or "application/json"
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": ct,
    }

    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else body.encode("utf-8")
    elif op.has_body and operation == OP_QUERY:
        data = json.dumps({"query": wiql}).encode()

    method = op.method
    req = Request(api_url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {operation}: {e.read().decode('utf-8', errors='replace')[:500]}")


def pat_download_binary(*, url: str, pat: str, dest_path: Path) -> None:
    """Download a binary file using PAT auth, streaming to disk."""
    import base64
    import shutil
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    creds = base64.b64encode(f":{pat}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}"}
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=120) as resp:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                shutil.copyfileobj(resp, f)
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} downloading {url}: {e.read().decode('utf-8', errors='replace')[:500]}")


def build_download_command(
    url: str, dest_path: Path, auth_method: str, org: str,
) -> list[str]:
    """Build a shell command to download a binary file."""
    if auth_method == "az-cli":
        return [
            "az", "rest", "--method", "get",
            "--resource", ADO_RESOURCE_ID,
            "--url", url,
            "--output-file", str(dest_path),
        ]
    # az-powershell
    token_expr = f"(Get-AzAccessToken -ResourceUrl '{ADO_RESOURCE_ID}').Token"
    safe_url = _escape_ps(url)
    safe_dest = str(dest_path).replace("'", "''")
    script = (
        f"$token = {token_expr}; "
        f"$headers = @{{Authorization = \"Bearer $token\"}}; "
        f"Invoke-WebRequest -Uri '{safe_url}' -Headers $headers -OutFile '{safe_dest}'"
    )
    return ["pwsh", "-NoProfile", "-Command", script]
