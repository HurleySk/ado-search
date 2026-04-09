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
                "--org", org, "--project", project,
                "--output", "json"]

    if operation == "wiki-list":
        return [*base, "devops", "wiki", "list",
                "--org", org, "--project", project,
                "--output", "json"]

    if operation == "wiki-page-list":
        return [*base, "devops", "wiki", "page", "list",
                "--wiki", wiki,
                "--org", org, "--project", project,
                "--output", "json"]

    if operation == "wiki-page-show":
        return [*base, "devops", "wiki", "page", "show",
                "--wiki", wiki, "--path", path,
                "--org", org, "--project", project,
                "--output", "json"]

    if operation == "comments":
        return [*base, "boards", "work-item", "relation", "show",
                "--id", str(work_item_id),
                "--org", org, "--project", project,
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

    if operation == "query":
        api_url = f"{org}/{project}/_apis/wit/wiql?api-version=7.1"
        body = '{{"query": "{wiql}"}}'.replace("{wiql}", _escape_ps(wiql))
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"$body = '{body}'; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Post -Headers $headers -Body $body"
        )
    elif operation == "show":
        api_url = f"{org}/{project}/_apis/wit/workitems/{work_item_id}?$expand=all&api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "wiki-list":
        api_url = f"{org}/{project}/_apis/wiki/wikis?api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "wiki-page-list":
        api_url = f"{org}/{project}/_apis/wiki/wikis/{wiki}/pages?recursionLevel=full&api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "wiki-page-show":
        encoded_path = path.replace("/", "%2F") if path else ""
        api_url = f"{org}/{project}/_apis/wiki/wikis/{wiki}/pages?path={encoded_path}&includeContent=true&api-version=7.1"
        script = (
            f"$token = {token_expr}; "
            f"$headers = {headers}; "
            f"Invoke-RestMethod -Uri '{api_url}' -Method Get -Headers $headers | ConvertTo-Json -Depth 10"
        )
    elif operation == "comments":
        api_url = f"{org}/{project}/_apis/wit/workitems/{work_item_id}/comments?api-version=7.1-preview.4"
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
