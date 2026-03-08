# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Toolbox is a collection of standalone utilities for AI coding assistants. Each tool lives in its own subdirectory with its own README.

## Tools

### claude-code-statusline

Real-time status line for Claude Code. Single-file script.

- Entry point: `claude-code-statusline/statusline.py`
- Reads session JSON from stdin (provided by Claude Code's `statusLine` feature)
- Parses the transcript JSONL file for token usage, compaction events, tool calls, and errors

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
