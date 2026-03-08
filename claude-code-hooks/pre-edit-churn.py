#!/usr/bin/env python3
"""
PreToolUse Hook — High Churn File Warning

Before editing a file, checks if it's been frequently modified across
multiple sessions. Warns about high-churn files that may need
refactoring instead of more patches.

Hook event: PreToolUse (matcher: Edit|Write)
Output: Warning shown to Claude before editing.
Exit code: Always 0 (never blocks, just warns).
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DAYS_LOOKBACK = 14
CHURN_THRESHOLD_SESSIONS = 3  # Warn if edited in 3+ sessions
CHURN_THRESHOLD_EDITS = 10    # Or if edited 10+ times total


def get_projects_dir():
    return Path.home() / ".claude" / "projects"


def find_project_transcripts(cwd):
    """Find transcripts for the current project."""
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        return []

    cwd_key = cwd.replace("/", "-").lstrip("-")
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_LOOKBACK)
    transcripts = []

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        if cwd_key not in project_dir.name and project_dir.name not in cwd_key:
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            mtime = datetime.fromtimestamp(
                jsonl_file.stat().st_mtime, tz=timezone.utc
            )
            if mtime < cutoff:
                continue
            transcripts.append(jsonl_file)

    return transcripts


def get_file_churn(file_path, transcripts):
    """Check how many sessions and total edits a file has across transcripts."""
    total_edits = 0
    sessions_with_edits = set()

    for t_path in transcripts:
        session_edits = 0
        session_id = ""

        try:
            with open(t_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if not session_id and "sessionId" in obj:
                        session_id = obj["sessionId"]

                    if obj.get("type") != "assistant" or "message" not in obj:
                        continue

                    content = obj["message"].get("content", [])
                    if not isinstance(content, list):
                        continue

                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        tool = block.get("name", "")
                        if tool not in ("Edit", "Write"):
                            continue
                        inp = block.get("input", {})
                        fp = inp.get("file_path", inp.get("path", ""))
                        if fp == file_path:
                            session_edits += 1

        except (FileNotFoundError, PermissionError):
            continue

        if session_edits > 0:
            total_edits += session_edits
            sessions_with_edits.add(session_id or str(t_path))

    return total_edits, len(sessions_with_edits)


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", tool_input.get("path", ""))
    if not file_path:
        return

    cwd = data.get("cwd", "")
    if not cwd:
        return

    transcripts = find_project_transcripts(cwd)
    if not transcripts:
        return

    total_edits, session_count = get_file_churn(file_path, transcripts)

    if session_count >= CHURN_THRESHOLD_SESSIONS or total_edits >= CHURN_THRESHOLD_EDITS:
        name = Path(file_path).name
        print(
            f"🔥 High churn: {name} has been edited {total_edits} times "
            f"across {session_count} sessions in the last {DAYS_LOOKBACK} days. "
            f"Consider if this file needs refactoring rather than more patches."
        )


if __name__ == "__main__":
    main()
