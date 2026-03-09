# Claude Code Session Manager — Browse, Search, and Export Sessions

CLI tool for listing, inspecting, comparing, resuming, and exporting Claude Code sessions.

## Commands

### `list` — Browse recent sessions

```bash
# List the 20 most recent sessions
python3 session-manager.py list

# Filter by project
python3 session-manager.py list --project my-app

# Last 7 days, show up to 50
python3 session-manager.py list --days 7 --limit 50
```

Example output:

```
  Recent Sessions
  ───────────────────────────────────────────────────────────────────────────────────────────────
  ID                   When Duration     Cost  Msgs   ⟳ Project              Branch          Model
  ───────────────────────────────────────────────────────────────────────────────────────────────
  a1b2c3d4      today 09:15      45m $  2.31    34   0 my-web-app           feature/auth    opus-4-6
  e5f6a7b8  yesterday 14:20    2h10m $ 12.47   156   1 my-web-app           main            sonnet-4-6
  c9d0e1f2     Mar 04 10:05      18m $  0.52    12   0 backend-app          fix/timeout     opus-4-6
  13a4b5c6     Mar 03 16:30    5h22m $ 45.80   287   2 my-web-app           feature/dashboard opus-4-6
  d7e8f9a0     Mar 02 09:00    1h05m $  8.14    89   0 docs-site            main            sonnet-4-6
  ───────────────────────────────────────────────────────────────────────────────────────────────
  Total cost: $69.24
```

### `show` — Session details

```bash
python3 session-manager.py show abc12345
```

Shows full metadata for a session including the `claude --resume` command to continue it.

### `resume` — Continue a session

```bash
python3 session-manager.py resume abc12345
```

Launches Claude Code with `--resume` for the matched session.

### `diff` — Compare two sessions

```bash
python3 session-manager.py diff abc12345 def67890
```

Side-by-side comparison of two sessions: model, branch, duration, messages, compactions, and cost. Highlights differences.

### `export` — Export session transcript

```bash
# Export as readable markdown
python3 session-manager.py export abc12345

# Export as JSON
python3 session-manager.py export abc12345 --json

# Save to file
python3 session-manager.py export abc12345 > session.md
```

Markdown export includes all user/assistant messages and tool calls. JSON export includes raw conversation entries.

Compaction boundaries are marked in the export:

```
---

**⚡ Compaction #1** (2026-03-08 06:48) — manual, 165,703 tokens before

---
```

Search for `⚡ Compaction` to find where Claude compressed the conversation. Everything above the marker was condensed into a summary for the next segment.

## Session ID matching

All commands accept a session ID **prefix** — you only need enough characters to uniquely identify the session (typically 6-8).

## Requirements

- Python 3.8+
- No external dependencies
- `claude` CLI on PATH (for `resume` command)

## License

MIT
