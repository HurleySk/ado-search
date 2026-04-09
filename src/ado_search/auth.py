from __future__ import annotations

ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"


def build_az_cli_command(
    operation: str,
    *,
    org: str,
    project: str,
    wiql: str | None = None,
    work_item_id: int | None = None,
    wiki: str | None = None,
    path: str | None = None,
) -> list[str]:
    base = ["az"]

    if operation == "query":
        return [*base, "boards", "query",
                "--wiql", wiql,
                "--org", org, "--project", project,
                "--output", "json"]

    if operation == "show":
        return [*base, "boards", "work-item", "show",
                "--id", str(work_item_id),
                "--org", org,
                "--output", "json"]

    if operation == "wiki-list":
        return [*base, "devops", "wiki", "list",
                "--org", org, "--project", project,
                "--output", "json"]

    if operation == "wiki-page-list":
        # az devops wiki page show doesn't return subPages recursively,
        # so use az rest with the wiki pages API and recursionLevel=full
        # Note: & must be escaped for Windows cmd.exe batch file processing
        api_url = f"{org}/{project}/_apis/wiki/wikis/{wiki}/pages"
        return [*base, "rest", "--method", "get",
                "--resource", ADO_RESOURCE_ID,
                "--url", api_url,
                "--url-parameters", "path=/", "recursionLevel=full", "api-version=7.1",
                "--output", "json"]

    if operation == "wiki-page-show":
        api_url = f"{org}/{project}/_apis/wiki/wikis/{wiki}/pages"
        return [*base, "rest", "--method", "get",
                "--resource", ADO_RESOURCE_ID,
                "--url", api_url,
                "--url-parameters", f"path={path}", "includeContent=true", "api-version=7.1",
                "--output", "json"]

    if operation == "comments":
        return [*base, "devops", "invoke",
                "--area", "wit", "--resource", "comments",
                "--route-parameters", f"id={work_item_id}",
                "--org", org,
                "--api-version", "7.1-preview.4",
                "--output", "json"]

    raise ValueError(f"Unknown operation: {operation}")


def build_powershell_command(
    operation: str,
    *,
    org: str,
    project: str,
    wiql: str | None = None,
    work_item_id: int | None = None,
    wiki: str | None = None,
    path: str | None = None,
) -> list[str]:
    token_expr = f"(Get-AzAccessToken -ResourceUrl '{ADO_RESOURCE_ID}').Token"
    headers = '@{Authorization = "Bearer $token"; "Content-Type" = "application/json"}'

    safe_org = _escape_ps(org)
    safe_project = _escape_ps(project)
    safe_wiki = _escape_ps(wiki)

    if operation == "query":
        api_url = f"{safe_org}/{safe_project}/_apis/wit/wiql?api-version=7.1"
        body = '{{"query": "{wiql}"}}'.replace("{wiql}", _escape_ps(wiql))
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"$body = '{body}'; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Post -Headers $headers -Body $body | ConvertTo-Json -Depth 10"
        )
    elif operation == "show":
        api_url = f"{safe_org}/{safe_project}/_apis/wit/workitems/{work_item_id}?$expand=all&api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "wiki-list":
        api_url = f"{safe_org}/{safe_project}/_apis/wiki/wikis?api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "wiki-page-list":
        api_url = f"{safe_org}/{safe_project}/_apis/wiki/wikis/{safe_wiki}/pages?recursionLevel=full&api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "wiki-page-show":
        safe_path = _escape_ps(path)
        encoded_path = safe_path.replace("/", "%2F") if safe_path else ""
        api_url = f"{safe_org}/{safe_project}/_apis/wiki/wikis/{safe_wiki}/pages?path={encoded_path}&includeContent=true&api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "comments":
        api_url = f"{safe_org}/{safe_project}/_apis/wit/workitems/{work_item_id}/comments?api-version=7.1-preview.4"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    else:
        raise ValueError(f"Unknown operation: {operation}")

    return ["pwsh", "-NoProfile", "-Command", script]


def _escape_ps(s: str | None) -> str:
    if s is None:
        return ""
    return s.replace('"', '`"').replace("'", "''")


def build_command(operation: str, auth_method: str, **kwargs) -> list[str]:
    """Dispatch to az-cli or PowerShell command builder based on auth method."""
    if auth_method == "az-cli":
        return build_az_cli_command(operation, **kwargs)
    return build_powershell_command(operation, **kwargs)
