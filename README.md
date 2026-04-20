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

Each work item includes: id, title, type, state, area, iteration, assigned_to, tags, priority, parent_id, created, updated, description, acceptance_criteria, story_points, state_history, attachments, and inline_images.

Story points are sourced from `StoryPoints` or `Effort` fields. State history tracks every state change (e.g., New → Active → Resolved → Closed) with date and author.

### Attachments & Inline Images

When `include_attachments = true`, sync downloads:

- **File attachments** — stored in `.ado-search/attachments/{work_item_id}/`
- **Inline images** — images embedded in Description/Acceptance Criteria HTML, stored in `.ado-search/attachments/{work_item_id}/inline/`

Attachment filenames are indexed and searchable. Inline images are referenced as `[image: path]` in text output so agents can locate them. Downloads are incremental — existing files with correct size are skipped on re-sync.

Note: attachments require the WIQL/REST sync path (OData doesn't include relations), so OData fast path is skipped when attachments are enabled.

## Commands

| Command | Description |
|---------|-------------|
| `ado-search init` | Configure organization, project, and auth |
| `ado-search sync` | Pull latest data from Azure DevOps |
| `ado-search search "query"` | Full-text search with filters |
| `ado-search show <id>` | Display full content of an item |
| `ado-search create` | Create a new work item |
| `ado-search update <id>` | Update an existing work item |
| `ado-search add-comment <id> <text>` | Add a comment to a work item |
| `ado-search add-link <source> <target>` | Add a link between two work items |
| `ado-search list-links <id>` | List links on a work item (live) |
| `ado-search list-comments <id>` | List comments on a work item (live) |

## Create & Update

Create and update work items directly from the CLI:

```bash
# Create a new bug
ado-search create --type Bug --title "Login button broken" --state New --priority 1

# Create with description and tags
ado-search create --type "User Story" --title "Add dark mode" \
  --description "Users want a dark theme option" \
  --tags "ui; theme" --story-points 5

# Update a work item
ado-search update 12345 --state Active --assigned-to "user@example.com"

# Close as duplicate (--reason sets Microsoft.VSTS.Common.ResolvedReason)
ado-search update 67154 --state Closed --reason Duplicate

# Set arbitrary ADO fields (including custom fields)
ado-search create --type Task --title "Research" --field "Custom.Effort=3"
ado-search update 12345 --field "System.AreaPath=Project\Team" --field "Custom.Sprint=Sprint 5"

# Preview without writing
ado-search create --type Bug --title "Test" --dry-run
```

### HTML Content from Files

Description, acceptance criteria, and comment text accept HTML. For multi-line content, use the `@file` convention to read from a file:

```bash
# Set description from an HTML file
ado-search create --type Bug --title "Rendering issue" --description @bug-details.html

# Update acceptance criteria from a file
ado-search update 12345 --acceptance-criteria @criteria.html

# Add a comment from a file
ado-search add-comment 12345 @review-notes.html

# Inline HTML still works
ado-search add-comment 12345 "<p>Looks good!</p>"

# Escape a literal @ with @@
ado-search update 12345 --description "@@mention is not a file reference"
```

After create/update/add-comment/add-link, the item is automatically re-fetched and merged into the local JSONL store so it appears in search immediately.

## Links

```bash
# Add a parent link
ado-search add-link 12345 67890 --type parent

# Add a related link with a comment
ado-search add-link 12345 67891 --type related --comment "See also this item"

# Link types: related, parent, child, duplicate, duplicate-of, depends-on, predecessor, successor
ado-search add-link 12345 67892 --type depends-on

# Use a raw ADO relation type
ado-search add-link 12345 67893 --type "System.LinkTypes.Related"

# Preview without creating
ado-search add-link 12345 67890 --type child --dry-run

# List all links on a work item (live from ADO)
ado-search list-links 12345

# List all comments on a work item (live from ADO)
ado-search list-comments 12345
```

## Configuration

Default sync includes Bug, User Story, Epic, and Feature work item types. To include Tasks or customize:

```toml
# .ado-search/config.toml
[sync]
work_item_types = ["Bug", "User Story", "Task", "Epic", "Feature"]
include_comments = false
include_attachments = false  # set to true to download file attachments and inline images
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
