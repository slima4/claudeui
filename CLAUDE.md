# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Toolbox is a collection of standalone utilities for AI coding assistants. Each tool lives in its own subdirectory with its own README.

## Tools

### claude-code-statusline

A Python 3.8+ script (stdlib only, no dependencies) that provides a real-time status line for Claude Code showing context window usage, session cost, compaction count, and working file count.

- Entry point: `claude-code-statusline/statusline.py`
- Reads session JSON from stdin (provided by Claude Code's `statusLine` feature)
- Parses the transcript JSONL file for token usage, compaction events, and tool calls
- No build step, no tests — single-file script

## Conventions

- Each tool is self-contained in its own directory with a README.md
- No external dependencies unless absolutely necessary — prefer stdlib
- MIT licensed
