# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ClaudeTUI is a collection of standalone utilities for Claude Code. Each tool lives in its own subdirectory with its own README.

## Tools

### claude-code-statusline

Real-time status bar for Claude Code. Single-file script.

- Entry point: `claude-code-statusline/statusline.py`
- Reads session JSON from stdin (provided by Claude Code's `statusLine` feature)
- Parses the transcript JSONL file for token usage, compaction events, tool calls, errors, turns, cache ratio, and thinking blocks
- Three modes: `full` (3-line with sparkline, telemetry, tool trace), `compact` (1-line essentials), `custom` (configurable components)
- Mode switch: `claudetui mode full|compact|custom` (single Python script: `claude-ui-mode.py`), or `--compact` flag
- Custom mode: `claudetui mode custom` launches curses TUI (arrow keys + space) to toggle individual components per line
- Custom config stored in `~/.claude/claudeui.json` under `"custom"` key, with `is_visible(line, component)` helper
- CLI flags: `--hide`, `--show`, `--widget`, `--preset`, `--list` for non-interactive configuration
- Presets: `all` (everything visible), `minimal` (essentials only), `focused` (hides model, token count, cost, session ID, cwd, cost/turn, agents)
- Pluggable widget system on the left (3×7 grid): `matrix`, `hex`, `bars`, `progress`, `none`
- Widget selection via `custom.widget` in claudeui.json or `STATUSLINE_WIDGET` env var (config takes precedence)
- Widget functions: `widget_fn(frame, ratio) -> list[str]` returning 3 rows
- Compaction entries use `{"type": "system", "subtype": "compact_boundary"}` in transcript JSONL
- Thinking blocks use `{"type": "thinking"}` in assistant message content (token counts redacted)

### claude-code-session-stats

Post-session analytics tool. Single-file script.

- Entry point: `claude-code-session-stats/session-stats.py`
- CLI tool — parses transcript JSONL files from `~/.claude/projects/`
- Generates cost breakdown, token sparkline, tool usage, file activity reports

### claude-code-session-manager

Session browser and manager. Single-file script.

- Entry point: `claude-code-session-manager/session-manager.py`
- Subcommands: `list`, `show`, `resume`, `diff`, `export`
- Reads from `~/.claude/projects/` directory structure

### claude-code-commands

Custom slash commands for in-session analytics. Markdown files installed to `~/.claude/commands/tui/`.

- Commands: `session` (full report), `cost` (spending breakdown), `perf` (tool efficiency), `context` (growth curve)
- Each command instructs Claude to read the current transcript JSONL and present formatted analysis
- No external dependencies — commands are pure markdown prompts
- Transcript path resolved via: `~/.claude/projects/$(pwd | sed 's|/|-|g; s|^-||')/*.jsonl`

### claude-code-monitor

Live session dashboard for a separate terminal.

- Entry point: `claude-code-monitor/monitor.py`
- Shared library: `claude-code-monitor/lib.py` (transcript parsing, formatting, constants, pricing)
- Chart module: `claude-code-monitor/chart.py` (efficiency chart rendering and segment building)
- Tests: `claude-code-monitor/test_monitor.py` (run with `python3 -v`)
- Watches transcript file for changes, refreshes on file change
- Args: none (auto-detect), `<session-id>`, `--list`, or `--chart [session-id]`
- Hotkeys: `s` stats, `d` details, `l` log viewer, `w` efficiency chart, `e` export, `o` sessions, `c` config, `?` help
- Efficiency chart: `w` hotkey or `claudetui chart` standalone — 4-component bar chart: system (cyan), summary (yellow), useful (green), headroom (gray). Press `?` for info overlay. Live updates via transcript file polling
- Log viewer: `f` cycles filter (all/errors/bash/edits/search/agents/skills/compactions), `a` toggles live auto-scroll
- Agent tracking: logs spawns/completions in event log; CURRENT section shows active/total agents per turn
- Skill tracking: logs skill invocations in event log; CURRENT section shows active skill while running

### claude-code-sniffer

API call interceptor proxy. Self-contained single-file script.

- Entry point: `claude-code-sniffer/sniffer.py`
- Transparent HTTP proxy using `ANTHROPIC_BASE_URL=http://localhost:PORT`
- Receives plain HTTP from Claude Code, forwards to `https://api.anthropic.com` over HTTPS
- Captures raw request/response bodies, HTTP headers, latency, SSE streaming events
- Console shows: tokens, cost, latency, traffic size, cache ratio, content block types, tool names, sub-agents
- Content block types: `T`=thinking, `t`=text, `U`=tool_use, `S`=server_tool_use, `W`=web_search_tool_result, `M`/`m`=mcp
- Sub-agent tracking: detects new session IDs, labels as `+agent.1` (new) / `agent.1` (known)
- Compaction detection: per-session, same-model comparison of message history size
- Logs to `~/.claude/api-sniffer/sniffer-{timestamp}.jsonl`
- CLI: `claudetui sniffer [--port PORT] [--full] [--no-redact] [--quiet]`
- Launch helper: `claudetui sniff [--port PORT] [claude args...]` — auto-detects sniffer port, falls back to direct launch
- Multi-port: each sniffer writes `~/.claude/api-sniffer/.port.{PORT}`, cleaned up on shutdown
- API keys redacted from logs by default; log files created with `0o600` permissions

### claude-code-hooks

Claude Code hooks for automatic in-session context. Three hook scripts:

- `claude-code-hooks/session-heatmap.py` — SessionStart: shows file activity hotspots
- `claude-code-hooks/post-edit-deps.py` — PostToolUse (Edit|Write): shows reverse dependencies
- `claude-code-hooks/pre-edit-churn.py` — PreToolUse (Edit|Write): warns about high-churn files
- Configured via `hooks` in `~/.claude/settings.json`

### Shared Settings

- Config file: `~/.claude/claudeui.json` — shared between statusline and monitor
- Hot-reloads: both tools re-read on file change, no restart needed
- Settings: `sparkline.mode` (`"tail"` or `"merge"`), `sparkline.merge_size` (turns per bar, default: 2), `monitor.log_lines` (0–50, default: 8, 0 = off)
- Config loader: `load_settings()` / `get_setting(*keys, default=...)` in each tool (statusline is self-contained; monitor imports from `lib.py`)

## Testing

```bash
python3 claude-code-monitor/test_monitor.py -v
```

Tests cover: transcript parsing, waste model (headroom + summary), segment building, chart rendering (horizontal/vertical), and format helpers. Run before and after refactoring to verify no regressions.

## Local Development

To test local changes to the statusline or hooks, update `~/.claude/settings.json` to point to your local repo instead of the installed path:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /path/to/your/repo/claude-code-statusline/statusline.py"
  }
}
```

The same applies to hook commands — replace the installed path with your local repo path. Remember to restore the original path when done testing (or re-run `./install.sh`).

`claudetui` and its subcommands can be tested directly without changing settings:
```bash
python3 claudetui.py monitor         # test the monitor
python3 claudetui.py chart           # test efficiency chart standalone
python3 claudetui.py mode custom     # test the configurator TUI
python3 claudetui.py mode --help     # test CLI
python3 claudetui.py --help          # test dispatcher
```

## Conventions

- Each tool is self-contained in its own directory with a README.md
- Python 3.8+, stdlib only — no external dependencies
- All tools parse Claude Code's JSONL transcript format from `~/.claude/projects/`
- MIT licensed
