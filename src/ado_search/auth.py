from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"

# Operation constants — single source of truth for operation names
OP_QUERY = "query"
OP_SHOW = "show"
OP_WIKI_LIST = "wiki-list"
OP_WIKI_PAGE_LIST = "wiki-page-list"
OP_WIKI_PAGE_SHOW = "wiki-page-show"
OP_COMMENTS = "comments"
OP_ODATA_QUERY = "odata-query"


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


def _resolve_url(op: OperationDef, *, org: str, project: str, **kwargs) -> str:
    """Build the full API URL from an operation definition."""
    if op.raw_url:
        return kwargs.get("url", "")

    url_project = quote(project or "", safe="")
    url_wiki = quote(kwargs.get("wiki") or "", safe="")
    work_item_id = kwargs.get("work_item_id", "")
    path = kwargs.get("path", "")

    api_path = (
        op.path
        .replace("{url_project}", url_project)
        .replace("{url_wiki}", url_wiki)
        .replace("{work_item_id}", str(work_item_id))
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
    ),
    OP_SHOW: OperationDef(
        path="{url_project}/_apis/wit/workitems/{work_item_id}",
        query_params=["$expand=all", "api-version=7.1"],
        use_az_rest=False,
        az_cli_cmd=["boards", "work-item", "show"],
    ),
    OP_WIKI_LIST: OperationDef(
        path="{url_project}/_apis/wiki/wikis",
        query_params=["api-version=7.1"],
        use_az_rest=False,
        az_cli_cmd=["devops", "wiki", "list"],
    ),
    OP_WIKI_PAGE_LIST: OperationDef(
        path="{url_project}/_apis/wiki/wikis/{url_wiki}/pages",
        query_params=["path=/", "recursionLevel=full", "api-version=7.1"],
    ),
    OP_WIKI_PAGE_SHOW: OperationDef(
        path="{url_project}/_apis/wiki/wikis/{url_wiki}/pages",
        query_params=["path={path}", "includeContent=true", "api-version=7.1"],
    ),
    OP_COMMENTS: OperationDef(
        path="{url_project}/_apis/wit/workitems/{work_item_id}/comments",
        query_params=["api-version=7.1-preview.4"],
        use_az_rest=False,
        az_cli_cmd=["devops", "invoke"],
    ),
    OP_ODATA_QUERY: OperationDef(
        raw_url=True,
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
) -> list[str]:
    op = OPERATIONS.get(operation)
    if op is None:
        raise ValueError(f"Unknown operation: {operation}")

    base = ["az"]

    # Operations that use specific az CLI subcommands
    if not op.use_az_rest:
        if operation == OP_QUERY:
            return [*base, *op.az_cli_cmd,
                    "--wiql", wiql,
                    "--org", org, "--project", project,
                    "--output", "json"]
        if operation == OP_SHOW:
            return [*base, *op.az_cli_cmd,
                    "--id", str(work_item_id),
                    "--org", org,
                    "--output", "json"]
        if operation == OP_WIKI_LIST:
            return [*base, *op.az_cli_cmd,
                    "--org", org, "--project", project,
                    "--output", "json"]
        if operation == OP_COMMENTS:
            return [*base, *op.az_cli_cmd,
                    "--area", "wit", "--resource", "comments",
                    "--route-parameters", f"id={work_item_id}",
                    "--org", org,
                    "--api-version", "7.1-preview.4",
                    "--output", "json"]

    # Operations that use az rest
    api_url = _resolve_url(op, org=org, project=project, wiki=wiki, path=path,
                           work_item_id=work_item_id, url=url)

    # For wiki page operations, az CLI uses --url-parameters instead of query string
    if operation in (OP_WIKI_PAGE_LIST, OP_WIKI_PAGE_SHOW):
        url_project = quote(project or "", safe="")
        url_wiki = quote(wiki or "", safe="")
        base_url = f"{org}/{url_project}/_apis/wiki/wikis/{url_wiki}/pages"
        url_params = list(op.query_params)
        if operation == OP_WIKI_PAGE_SHOW:
            url_params = [p.replace("{path}", path or "") for p in url_params]
        return [*base, "rest", "--method", "get",
                "--resource", ADO_RESOURCE_ID,
                "--url", base_url,
                "--url-parameters", *url_params,
                "--output", "json"]

    return [*base, "rest", "--method", "get",
            "--resource", ADO_RESOURCE_ID,
            "--url", api_url,
            "--output", "json"]


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
) -> list[str]:
    op = OPERATIONS.get(operation)
    if op is None:
        raise ValueError(f"Unknown operation: {operation}")

    token_expr = f"(Get-AzAccessToken -ResourceUrl '{ADO_RESOURCE_ID}').Token"
    headers = '@{Authorization = "Bearer $token"; "Content-Type" = "application/json"}'

    api_url = _resolve_url(op, org=org, project=project, wiki=wiki, path=path,
                           work_item_id=work_item_id, url=url)

    # Escape for PowerShell
    if op.raw_url:
        safe_url = _escape_ps(api_url)
    else:
        safe_url = api_url  # already URL-encoded, no PS escaping needed

    if op.has_body and operation == OP_QUERY:
        body = '{{"query": "{wiql}"}}'.replace("{wiql}", _escape_ps(wiql))
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"$body = '{body}'; "
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
                           work_item_id=work_item_id, url=url)

    creds = base64.b64encode(f":{pat}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }

    body = None
    if op.has_body and operation == OP_QUERY:
        body = json.dumps({"query": wiql}).encode()

    method = op.method
    req = Request(api_url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {operation}: {e.read().decode('utf-8', errors='replace')[:500]}")
