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
2. Data is stored as **sorted JSONL files** — git-friendly, diffable, one file per entity type
3. A **SQLite FTS5 index** is derived from the JSONL and auto-rebuilt when stale
4. Agents search the index, then use `show` to render full content — minimal context

### Git-Friendly Storage

Sync produces two text files that are safe to commit, push, and pull:

- `work-items.jsonl` — one JSON object per line, sorted by ID
- `wiki-pages.jsonl` — one JSON object per line, sorted by path

The SQLite index (`index.db`) is `.gitignore`d — it auto-rebuilds from JSONL on first search or show.

## Synced Fields

Each work item includes: id, title, type, state, area, iteration, assigned_to, tags, priority, parent_id, created, updated, description, acceptance_criteria, story_points, and state_history (state transitions with timestamps).

Story points are sourced from `StoryPoints` or `Effort` fields. State history tracks every state change (e.g., New → Active → Resolved → Closed) with date and author.

## Commands

| Command | Description |
|---------|-------------|
| `ado-search init` | Configure organization, project, and auth |
| `ado-search sync` | Pull latest data from Azure DevOps |
| `ado-search search "query"` | Full-text search with filters |
| `ado-search show <id>` | Display full content of an item |

## Configuration

Default sync includes Bug, User Story, Epic, and Feature work item types. To include Tasks or customize:

```toml
# .ado-search/config.toml
[sync]
work_item_types = ["Bug", "User Story", "Task", "Epic", "Feature"]
include_comments = false
```

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
