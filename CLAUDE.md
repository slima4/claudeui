# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Toolbox is a collection of standalone utilities for AI coding assistants. Each tool lives in its own subdirectory with its own README.

## Tools

### claude-code-statusline

Real-time status bar for Claude Code. Single-file script.

- Entry point: `claude-code-statusline/statusline.py`
- Reads session JSON from stdin (provided by Claude Code's `statusLine` feature)
- Parses the transcript JSONL file for token usage, compaction events, tool calls, errors, turns, cache ratio, and thinking blocks
- Three-line layout: line 1 (session core + sparkline), line 2 (project telemetry), line 3 (live tool trace + file edits)
- Pluggable widget system on the left (3×7 grid): `matrix`, `hex`, `bars`, `progress`, `none`
- Widget selection via `STATUSLINE_WIDGET` env var (default: `matrix`)
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
- Hotkeys: `s` stats, `d` details, `l` log viewer, `e` export, `o` sessions, `?` help
- Log viewer: `f` cycles filter (all/errors/bash/edits/search/compactions), `a` toggles live auto-scroll

### claude-code-hooks

Claude Code hooks for automatic in-session context. Three hook scripts:

- `claude-code-hooks/session-heatmap.py` — SessionStart: shows file activity hotspots
- `claude-code-hooks/post-edit-deps.py` — PostToolUse (Edit|Write): shows reverse dependencies
- `claude-code-hooks/pre-edit-churn.py` — PreToolUse (Edit|Write): warns about high-churn files
- Configured via `hooks` in `~/.claude/settings.json`

## Conventions

- Each tool is self-contained in its own directory with a README.md
- Python 3.8+, stdlib only — no external dependencies
- All tools parse Claude Code's JSONL transcript format from `~/.claude/projects/`
- MIT licensed
