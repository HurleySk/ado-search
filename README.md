# ado-search

Sync and search Azure DevOps work items and wiki pages locally for AI agents.

## Install

```bash
pip install ado-search
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

1. **Sync** pulls work items and wiki pages via `az devops` CLI (or Azure PowerShell)
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

Set via `ado-search init --auth-method az-powershell` or in `config.toml`.
