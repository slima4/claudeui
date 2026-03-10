# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ClaudeUI is a collection of standalone utilities for Claude Code. Each tool lives in its own subdirectory with its own README.

## Tools

### claude-code-statusline

Real-time status bar for Claude Code. Single-file script.

- Entry point: `claude-code-statusline/statusline.py`
- Reads session JSON from stdin (provided by Claude Code's `statusLine` feature)
- Parses the transcript JSONL file for token usage, compaction events, tool calls, errors, turns, cache ratio, and thinking blocks
- Three modes: `full` (3-line with sparkline, telemetry, tool trace), `compact` (1-line essentials), `custom` (configurable components)
- Mode switch: `claude-ui-mode full|compact|custom` (single Python script: `claude-ui-mode.py`), or `--compact` flag
- Custom mode: `claude-ui-mode custom` launches curses TUI (arrow keys + space) to toggle individual components per line
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

Custom slash commands for in-session analytics. Markdown files installed to `~/.claude/commands/ui/`.

- Commands: `session` (full report), `cost` (spending breakdown), `perf` (tool efficiency), `context` (growth curve)
- Each command instructs Claude to read the current transcript JSONL and present formatted analysis
- No external dependencies — commands are pure markdown prompts
- Transcript path resolved via: `~/.claude/projects/$(pwd | sed 's|/|-|g; s|^-||')/*.jsonl`

### claude-code-monitor

Live session dashboard for a separate terminal. Single-file script.

- Entry point: `claude-code-monitor/monitor.py`
- Self-contained — all parsing inlined, no imports from other tools
- Watches transcript file for changes, refreshes on file change
- Args: none (auto-detect), `<session-id>`, or `--list`
- Hotkeys: `s` stats, `d` details, `l` log viewer, `e` export, `o` sessions, `c` config, `?` help
- Log viewer: `f` cycles filter (all/errors/bash/edits/search/agents/compactions), `a` toggles live auto-scroll
- Agent tracking: logs spawns/completions in event log; CURRENT section shows active/total agents per turn

### claude-code-hooks

Claude Code hooks for automatic in-session context. Three hook scripts:

- `claude-code-hooks/session-heatmap.py` — SessionStart: shows file activity hotspots
- `claude-code-hooks/post-edit-deps.py` — PostToolUse (Edit|Write): shows reverse dependencies
- `claude-code-hooks/pre-edit-churn.py` — PreToolUse (Edit|Write): warns about high-churn files
- Configured via `hooks` in `~/.claude/settings.json`

### Shared Settings

- Config file: `~/.claude/claudeui.json` — shared between statusline and monitor
- Hot-reloads: both tools re-read on file change, no restart needed
- Settings: `sparkline.mode` (`"tail"` or `"merge"`), `sparkline.merge_size` (turns per bar, default: 2)
- Config loader: `load_settings()` / `get_setting(*keys, default=...)` in each tool (self-contained, no shared imports)

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

`claude-ui-mode.py` and `claude-ui-monitor` can be tested directly without changing settings:
```bash
python3 claude-ui-mode.py custom     # test the configurator TUI
python3 claude-ui-mode.py --help     # test CLI
python3 claude-code-monitor/monitor.py  # test the monitor
```

## Conventions

- Each tool is self-contained in its own directory with a README.md
- Python 3.8+, stdlib only — no external dependencies
- All tools parse Claude Code's JSONL transcript format from `~/.claude/projects/`
- MIT licensed
