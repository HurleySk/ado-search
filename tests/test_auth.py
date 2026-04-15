from pathlib import Path

from ado_search.auth import build_az_cli_command, build_download_command, build_powershell_command


def test_build_az_cli_command_wiql():
    cmd = build_az_cli_command(
        "query",
        wiql="SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'",
        org="https://dev.azure.com/contoso",
        project="MyProject",
    )
    assert cmd[0] == "az"
    assert "boards" in cmd
    assert "query" in cmd
    assert "--wiql" in cmd
    assert "--output" in cmd
    assert "json" in cmd


def test_build_az_cli_command_show():
    cmd = build_az_cli_command(
        "show",
        work_item_id=12345,
        org="https://dev.azure.com/contoso",
        project="MyProject",
    )
    assert "az" == cmd[0]
    assert "work-item" in cmd
    assert "show" in cmd
    assert "--id" in cmd
    assert "12345" in cmd


def test_build_powershell_command_wiql():
    cmd = build_powershell_command(
        "query",
        wiql="SELECT [System.Id] FROM WorkItems",
        org="https://dev.azure.com/contoso",
        project="MyProject",
    )
    assert cmd[0] == "pwsh"
    assert "-Command" in cmd
    ps_script = cmd[cmd.index("-Command") + 1]
    assert "Invoke-RestMethod" in ps_script
    assert "Get-AzAccessToken" in ps_script


def test_build_az_cli_command_wiki_list():
    cmd = build_az_cli_command(
        "wiki-list",
        org="https://dev.azure.com/contoso",
        project="MyProject",
    )
    assert "wiki" in cmd
    assert "list" in cmd


def test_build_az_cli_command_odata_query():
    cmd = build_az_cli_command(
        "odata-query",
        url="https://analytics.dev.azure.com/contoso/MyProject/_odata/v4.0-preview/WorkItems?$top=100",
        org="https://dev.azure.com/contoso",
        project="MyProject",
    )
    assert cmd[0] == "az"
    assert "rest" in cmd
    assert "--url" in cmd
    url = cmd[cmd.index("--url") + 1]
    assert "analytics.dev.azure.com" in url


def test_build_powershell_command_odata_query():
    cmd = build_powershell_command(
        "odata-query",
        url="https://analytics.dev.azure.com/contoso/MyProject/_odata/v4.0-preview/WorkItems?$top=100",
        org="https://dev.azure.com/contoso",
        project="MyProject",
    )
    assert cmd[0] == "pwsh"
    ps_script = cmd[cmd.index("-Command") + 1]
    assert "Invoke-RestMethod" in ps_script
    assert "analytics.dev.azure.com" in ps_script


def test_build_az_cli_command_wiki_page_show():
    cmd = build_az_cli_command(
        "wiki-page-show",
        wiki="MyWiki",
        path="/Getting-Started",
        org="https://dev.azure.com/contoso",
        project="MyProject",
    )
    assert cmd[0] == "az"
    assert "rest" in cmd
    assert "--url" in cmd
    url = cmd[cmd.index("--url") + 1]
    assert "MyWiki" in url
    # Path and includeContent are passed via --url-parameters
    assert "--url-parameters" in cmd
    params = cmd[cmd.index("--url-parameters") + 1:]
    param_str = " ".join(params)
    assert "Getting-Started" in param_str
    assert "includeContent=true" in param_str


def test_build_az_cli_command_create():
    cmd = build_az_cli_command(
        "create",
        org="https://dev.azure.com/contoso",
        project="MyProject",
        title="New Bug",
        work_item_type="Bug",
        fields=["System.Description=A bug description"],
    )
    assert cmd[0] == "az"
    assert "work-item" in cmd
    assert "create" in cmd
    assert "--title" in cmd
    assert cmd[cmd.index("--title") + 1] == "New Bug"
    assert "--type" in cmd
    assert cmd[cmd.index("--type") + 1] == "Bug"
    assert "--fields" in cmd
    assert "System.Description=A bug description" in cmd


def test_build_az_cli_command_update():
    cmd = build_az_cli_command(
        "update",
        org="https://dev.azure.com/contoso",
        project="MyProject",
        work_item_id=12345,
        title="Updated Title",
        fields=["System.State=Active"],
    )
    assert "update" in cmd
    assert "--id" in cmd
    assert "12345" in cmd
    assert "--title" in cmd
    assert cmd[cmd.index("--title") + 1] == "Updated Title"
    assert "--fields" in cmd
    assert "System.State=Active" in cmd


def test_build_az_cli_command_update_no_title():
    """Update without --title should not include --title flag."""
    cmd = build_az_cli_command(
        "update",
        org="https://dev.azure.com/contoso",
        project="MyProject",
        work_item_id=12345,
        fields=["System.State=Active"],
    )
    assert "--title" not in cmd
    assert "--id" in cmd


def test_build_powershell_command_create():
    cmd = build_powershell_command(
        "create",
        org="https://dev.azure.com/contoso",
        project="MyProject",
        work_item_type="Bug",
        body='[{"op":"add","path":"/fields/System.Title","value":"Test"}]',
        content_type="application/json-patch+json",
    )
    assert cmd[0] == "pwsh"
    ps_script = cmd[cmd.index("-Command") + 1]
    assert "application/json-patch+json" in ps_script
    assert "-Method Post" in ps_script
    assert "System.Title" in ps_script


def test_build_powershell_command_update():
    cmd = build_powershell_command(
        "update",
        org="https://dev.azure.com/contoso",
        project="MyProject",
        work_item_id=12345,
        body='[{"op":"add","path":"/fields/System.State","value":"Active"}]',
        content_type="application/json-patch+json",
    )
    ps_script = cmd[cmd.index("-Command") + 1]
    assert "-Method Patch" in ps_script
    assert "application/json-patch+json" in ps_script


def test_build_az_cli_command_add_comment():
    """add-comment uses az rest with POST method and body."""
    cmd = build_az_cli_command(
        "add-comment",
        org="https://dev.azure.com/contoso",
        project="MyProject",
        work_item_id=12345,
        body='{"text": "<p>Nice work!</p>"}',
        content_type="application/json",
    )
    assert cmd[0] == "az"
    assert "rest" in cmd
    assert "--method" in cmd
    assert cmd[cmd.index("--method") + 1] == "post"
    assert "--body" in cmd
    body = cmd[cmd.index("--body") + 1]
    assert '"text"' in body
    assert "--url" in cmd
    url = cmd[cmd.index("--url") + 1]
    assert "/comments" in url
    assert "12345" in url


def test_build_powershell_command_add_comment():
    cmd = build_powershell_command(
        "add-comment",
        org="https://dev.azure.com/contoso",
        project="MyProject",
        work_item_id=12345,
        body='{"text": "<p>Nice work!</p>"}',
        content_type="application/json",
    )
    assert cmd[0] == "pwsh"
    ps_script = cmd[cmd.index("-Command") + 1]
    assert "-Method Post" in ps_script
    assert "application/json" in ps_script
    assert "/comments" in ps_script


def test_build_download_command_az_cli():
    cmd = build_download_command(
        "https://dev.azure.com/contoso/_apis/wit/attachments/abc-123",
        Path("/tmp/file.png"),
        "az-cli",
        "https://dev.azure.com/contoso",
    )
    assert cmd[0] == "az"
    assert "rest" in cmd
    assert "--output-file" in cmd
    assert "--url" in cmd


def test_build_download_command_powershell():
    cmd = build_download_command(
        "https://dev.azure.com/contoso/_apis/wit/attachments/abc-123",
        Path("/tmp/file.png"),
        "az-powershell",
        "https://dev.azure.com/contoso",
    )
    assert cmd[0] == "pwsh"
    ps_script = cmd[cmd.index("-Command") + 1]
    assert "Invoke-WebRequest" in ps_script
    assert "-OutFile" in ps_script
    assert "Get-AzAccessToken" in ps_script
