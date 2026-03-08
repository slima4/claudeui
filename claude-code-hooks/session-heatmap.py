#!/usr/bin/env python3
"""
SessionStart Hook — File Activity Heatmap

Shows top hotspot files for the current project when a session starts.
Helps you immediately see what's been actively worked on.

Hook event: SessionStart
Output: Shown to Claude as context at session start.
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DAYS_LOOKBACK = 14


def get_projects_dir():
    return Path.home() / ".claude" / "projects"


def find_project_transcripts(cwd):
    """Find transcripts for the current project."""
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        return []

    # Match project dir by cwd
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


def parse_activity(transcript_path):
    """Extract file edit/read activity from a transcript."""
    edits = Counter()
    reads = Counter()
    session_id = ""

    try:
        with open(transcript_path, "r") as f:
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
                    inp = block.get("input", {})
                    fp = inp.get("file_path", inp.get("path", ""))
                    if not fp:
                        continue

                    if tool in ("Edit", "Write"):
                        edits[fp] += 1
                    elif tool == "Read":
                        reads[fp] += 1

    except (FileNotFoundError, PermissionError):
        pass

    return edits, reads


def shorten_path(filepath, cwd, max_len=55):
    """Shorten file path relative to cwd."""
    if filepath.startswith(cwd):
        filepath = filepath[len(cwd):].lstrip("/")
    home = str(Path.home())
    if filepath.startswith(home):
        filepath = "~" + filepath[len(home):]
    if len(filepath) > max_len:
        filepath = "..." + filepath[-(max_len - 3):]
    return filepath


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    cwd = data.get("cwd", "")
    if not cwd:
        return

    transcripts = find_project_transcripts(cwd)
    if not transcripts:
        return

    all_edits = Counter()
    all_reads = Counter()

    for t in transcripts:
        edits, reads = parse_activity(t)
        all_edits.update(edits)
        all_reads.update(reads)

    # Score: edits weighted 3x
    activity = Counter()
    for f in set(list(all_edits.keys()) + list(all_reads.keys())):
        activity[f] = all_edits[f] * 3 + all_reads[f]

    if not activity:
        return

    top = activity.most_common(8)
    max_score = top[0][1]

    blocks = " ▁▂▃▄▅▆▇█"
    lines = []
    lines.append(f"📊 File hotspots (last {DAYS_LOOKBACK} days, {len(transcripts)} sessions):")

    for filepath, score in top:
        short = shorten_path(filepath, cwd)
        ratio = score / max_score if max_score > 0 else 0
        bar_idx = int(ratio * (len(blocks) - 1))
        bar = blocks[bar_idx] * 3
        e = all_edits[filepath]
        r = all_reads[filepath]
        lines.append(f"  {bar} {short} ({e}e/{r}r)")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
