# ado-search

Sync and search Azure DevOps work items and wiki pages locally for AI agents.

## Install

```bash
pip install ado-search
```

Or from source:

```bash
pip install git+https://github.com/HurleySk/ado-search.git
```

## Quick Start

```bash
# Configure (requires az login first)
ado-search init --org https://dev.azure.com/yourorg --project YourProject

# Pull data
ado-search sync

# Search
ado-search search "login bug"
ado-search search "auth" --type Bug --state Active
ado-search search "setup guide" --format paths
```

## How It Works

1. **Sync** pulls work items and wiki pages from Azure DevOps
   - Tries **OData analytics** first (fetches all items in one call — fast)
   - Falls back to **az devops CLI** if analytics isn't available
2. Content is stored as compact **markdown files** (one per item)
3. Metadata is indexed in **SQLite with FTS5** for fast full-text search
4. Agents search the index, then read only the files they need — minimal context

## Commands

| Command | Description |
|---------|-------------|
| `ado-search init` | Configure organization, project, and auth |
| `ado-search sync` | Pull latest data from Azure DevOps |
| `ado-search search "query"` | Full-text search with filters |
| `ado-search show <id>` | Display full content of an item |

## Auth Methods

- **az-cli** (default): Uses `az devops` commands. Requires `az login`.
- **az-powershell**: Uses Azure PowerShell + REST. Requires `Connect-AzAccount`.
- **pat**: Uses a Personal Access Token via REST. For cross-cloud or service account scenarios.

Set via `ado-search init --auth-method <method>` or in `config.toml`.

```bash
# PAT example (token can also be set via ADO_PAT env var)
ado-search init --org https://dev.azure.com/yourorg --project YourProject --auth-method pat --pat <token>
```

## Search Formats

```bash
ado-search search "query"                          # compact (default)
ado-search search "query" --format detail           # with description snippets
ado-search search "query" --format json             # machine-readable
ado-search search "query" --format paths            # file paths only (for agent piping)
ado-search search "query" --type Bug --state Active # filtered
```

## Prerequisites

- Python 3.10+
- Azure CLI with `azure-devops` extension (`az extension add --name azure-devops`)
- `az login` completed
