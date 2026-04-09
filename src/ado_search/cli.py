import click


@click.group()
@click.version_option(package_name="ado-search")
def main():
    """Sync and search Azure DevOps data for AI agents."""
    pass
