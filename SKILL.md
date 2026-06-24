# patchwork

Mine your codebase. Generate CONVENTIONS.md. Stop AI agents from making up your style.

## What it does

patchwork scans your source code using AST analysis and pattern mining to detect:
- **Naming conventions** (functions, classes, variables, files) — with confidence scores and real examples
- **Import style** (absolute/relative, path aliases, barrel files)
- **Project structure** (source root, test layout, feature vs layer organisation, monorepo)
- **Error handling** (try/except, Result types, logging framework, custom exceptions)
- **Testing** (framework, assertion style, mocking library, coverage tool)
- **API patterns** (response shape, route style, ORM, async pattern)
- **Git workflow** (commit style, branch naming, co-change pairs)
- **Tech stack** (frameworks, linters, formatters, package manager, build tools)

It writes all findings to **CONVENTIONS.md** — a single source of truth that AI agents read before writing any code.

## Commands

### /patchwork

Scan the current project and generate CONVENTIONS.md.

```
/patchwork
```

### /patchwork update

Re-scan and update CONVENTIONS.md (preserves any manual edits).

```
/patchwork update
```

### /patchwork show

Print conventions to terminal without writing a file.

```
/patchwork show
```

### /patchwork diff

Show what would change in CONVENTIONS.md.

```
/patchwork diff
```

### /patchwork check \<name\> \<kind\> \<lang\>

Check if a symbol name follows project conventions.

```
/patchwork check getUserById function typescript
```

### /patchwork agents-md

Generate AGENTS.md instead of CONVENTIONS.md.

```
/patchwork agents-md
```

### /patchwork claude-md

Append detected conventions to CLAUDE.md.

```
/patchwork claude-md
```

## Installation

```bash
pip install patchwork-conventions
```

Or with full language support:

```bash
pip install 'patchwork-conventions[full]'
```

## Usage

### CLI

```bash
# Scan current directory
patchwork scan

# Scan specific path
patchwork scan /path/to/project

# Output as JSON
patchwork scan --json

# Generate AGENTS.md
patchwork scan --agents-md

# Append to CLAUDE.md
patchwork scan --claude-md

# Auto-watch mode
patchwork watch

# Check a name
patchwork show
```

### MCP Server

```bash
# Start MCP server (stdio mode for Claude Code)
patchwork serve --stdio

# Start MCP server (HTTP mode)
patchwork serve --port 3742
```

Add to Claude Code settings:

```json
{
  "mcpServers": {
    "patchwork": {
      "command": "patchwork",
      "args": ["serve", "--stdio", "/path/to/project"]
    }
  }
}
```

### Python API

```python
from patchwork import scan
from patchwork.scanner import ScanOptions
from pathlib import Path

report = scan(ScanOptions(root=Path(".")))
print(report.to_markdown())
print(report.to_json())
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `patchwork_scan` | Full scan → returns CONVENTIONS.md content |
| `patchwork_naming` | Naming conventions for a specific language |
| `patchwork_structure` | Project structure summary |
| `patchwork_stack` | Tech stack detection |
| `patchwork_errors` | Error handling conventions |
| `patchwork_testing` | Testing conventions |
| `patchwork_git` | Git workflow conventions |
| `patchwork_check` | Check a symbol name against conventions |

## Skill invocation

When the user types `/patchwork`, run:

```bash
patchwork scan .
```

Then show a summary of the key findings and the path to the generated CONVENTIONS.md.

When the user types `/patchwork check <name> <kind> <lang>`, run:

```bash
patchwork show --lang <lang>
```

And verify the name against the detected convention for that kind/lang.

## What makes this different from argus/sourcebook

- **Deep AST analysis** via tree-sitter (not just filesystem + package.json)
- **Confidence scores** — knows when conventions are inconsistent
- **Real examples** from your actual code, not generic descriptions  
- **Counter-examples** — shows where conventions are broken
- **Co-change analysis** — files that always change together
- **Convention checking** — validates proposed names before you write them
- **MCP tools** — agents can query specific convention categories on demand
- **Zero LLM required** for base scan (pure static analysis)
