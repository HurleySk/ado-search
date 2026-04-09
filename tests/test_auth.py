from ado_search.auth import build_az_cli_command, build_powershell_command


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
