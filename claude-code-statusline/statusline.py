#!/usr/bin/env python3
"""
Claude Code Status Line — Context Window Monitor

Displays real-time context window usage, model info, git branch,
session cost, duration, compact count, working file count,
git diff stats, and tool error rate.

Usage:
    Configure in .claude/settings.local.json:
    {
      "statusLine": {
        "type": "command",
        "command": "python3 /path/to/statusline.py"
      }
    }

Reads JSON from stdin (provided by Claude Code) containing session data.
"""

import json
import os
import subprocess
import sys
from datetime import datetime

# Full context window size (200k for Claude models)
CONTEXT_LIMIT = 200_000

# Overhead multiplier for system prompts, tool definitions, CLAUDE.md, etc.
# These tokens are part of the context but not reported in usage data.
# Empirically ~1.2x based on comparing with Claude Code's built-in indicator.
OVERHEAD_MULTIPLIER = 1.2

# Pricing per million tokens (as of 2025)
# https://docs.anthropic.com/en/docs/about-claude/models
MODEL_PRICING = {
    # Claude 4.6 / 4.5
    "claude-opus-4-6": {"input": 15.0, "cache_read": 1.5, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "cache_read": 0.30, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "cache_read": 0.08, "output": 4.0},
    # Claude 3.5
    "claude-sonnet-3-5": {"input": 3.0, "cache_read": 0.30, "output": 15.0},
    "claude-haiku-3-5": {"input": 0.80, "cache_read": 0.08, "output": 4.0},
}

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[38;5;208m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
GRAY = "\033[90m"


def get_git_branch():
    """Read current git branch from .git/HEAD."""
    try:
        git_head = os.path.join(os.getcwd(), ".git", "HEAD")
        if not os.path.exists(git_head):
            return ""
        with open(git_head, "r") as f:
            ref = f.read().strip()
        if ref.startswith("ref: refs/heads/"):
            return ref[len("ref: refs/heads/"):]
        return ref[:8]  # detached HEAD — show short hash
    except Exception:
        return ""


def get_git_diff_stat():
    """Get git working tree diff stats (+added/-deleted lines)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--shortstat"],
            capture_output=True, text=True, timeout=3
        )
        stat = result.stdout.strip()
        if not stat:
            return ""

        insertions = 0
        deletions = 0
        for part in stat.split(","):
            part = part.strip()
            if "insertion" in part:
                insertions = int(part.split()[0])
            elif "deletion" in part:
                deletions = int(part.split()[0])

        parts = []
        if insertions:
            parts.append(f"{GREEN}+{insertions}{RESET}")
        if deletions:
            parts.append(f"{RED}-{deletions}{RESET}")
        return " ".join(parts) if parts else ""
    except Exception:
        return ""


def get_model_pricing(model_id):
    """Get pricing for a model, falling back to sonnet rates."""
    for key, pricing in MODEL_PRICING.items():
        if key in model_id:
            return pricing
    return MODEL_PRICING["claude-sonnet-4-6"]


def parse_transcript(transcript_path):
    """Parse transcript file to extract all session metrics."""
    result = {
        "context_tokens": 0,
        "input_tokens_total": 0,
        "cache_read_tokens_total": 0,
        "output_tokens_total": 0,
        "compact_count": 0,
        "files_touched": set(),
        "session_start": None,
        "tool_calls": 0,
        "tool_errors": 0,
        "subagent_count": 0,
        "turn_count": 0,
        "context_history": [],
    }

    try:
        with open(transcript_path, "r") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return result

    # Reverse pass — find most recent context usage
    # Stop at summary (compaction) entries: pre-compact usage is stale
    context_found = False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # If we hit a compaction before finding usage, context was reset
        if (
            obj.get("type") == "summary"
            or (obj.get("type") == "system"
                and obj.get("subtype") == "compact_boundary")
        ):
            break

        if (
            not context_found
            and obj.get("type") == "assistant"
            and "message" in obj
            and "usage" in obj["message"]
        ):
            usage = obj["message"]["usage"]
            keys = [
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
            ]
            if all(k in usage for k in keys):
                result["context_tokens"] = sum(usage[k] for k in keys)
                context_found = True
                break

    # Forward pass — cumulative metrics
    active_subagents = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Session start
        if result["session_start"] is None and "timestamp" in obj:
            result["session_start"] = obj["timestamp"]

        # Turn count (each user message = one turn)
        if obj.get("type") == "user" and "message" in obj:
            content = obj["message"].get("content", [])
            # Only count turns with actual user text, not just tool results
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        result["turn_count"] += 1
                        break
            elif isinstance(content, str) and content.strip():
                result["turn_count"] += 1

        # Token usage for cost + context history
        if (
            obj.get("type") == "assistant"
            and "message" in obj
            and "usage" in obj["message"]
        ):
            usage = obj["message"]["usage"]
            result["input_tokens_total"] += usage.get("input_tokens", 0)
            result["cache_read_tokens_total"] += usage.get(
                "cache_read_input_tokens", 0
            )
            result["output_tokens_total"] += usage.get("output_tokens", 0)

            # Context snapshot for sparkline
            ctx_keys = [
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
            ]
            if all(k in usage for k in ctx_keys):
                result["context_history"].append(
                    sum(usage[k] for k in ctx_keys)
                )

        # Compact count — also record a 0 in context history (visual cliff)
        if obj.get("type") == "summary" or (
            obj.get("type") == "system"
            and obj.get("subtype") == "compact_boundary"
        ):
            result["compact_count"] += 1
            result["context_history"].append(0)

        # Tool calls, errors, files, and subagents from assistant messages
        if obj.get("type") == "assistant" and "message" in obj:
            content = obj["message"].get("content", [])
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        result["tool_calls"] += 1
                        inp = block.get("input", {})
                        tool_name = block.get("name", "")

                        # Files touched
                        for key in ("file_path", "path"):
                            if key in inp and isinstance(inp[key], str):
                                result["files_touched"].add(inp[key])

                        # Sub-agent tracking
                        if tool_name in ("Task", "Agent"):
                            task_id = block.get("id", "")
                            if task_id:
                                active_subagents.add(task_id)

        # Tool errors from user messages (tool_result blocks)
        if obj.get("type") == "user" and "message" in obj:
            content = obj["message"].get("content", [])
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("is_error")
                    ):
                        result["tool_errors"] += 1

    result["subagent_count"] = len(active_subagents)

    return result


def format_tokens(n):
    """Format token count as human-readable string (e.g., 84.2k)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_cost(cost):
    """Format cost in dollars."""
    if cost < 0.01:
        return "<$0.01"
    return f"${cost:.2f}"


def format_duration(start_timestamp):
    """Format session duration from ISO timestamp."""
    if not start_timestamp:
        return "0m"
    try:
        start_str = start_timestamp.replace("Z", "+00:00")
        start = datetime.fromisoformat(start_str)
        now = datetime.now(start.tzinfo)
        delta = now - start
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 60:
            return f"{total_minutes}m"
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours}h {minutes:02d}m"
    except Exception:
        return "?m"


def build_sparkline(values, width=20):
    """Build a sparkline string from a list of values."""
    if not values:
        return ""
    # Downsample if too many points
    if len(values) > width:
        step = len(values) / width
        sampled = []
        for i in range(width):
            idx = int(i * step)
            sampled.append(values[idx])
        values = sampled

    blocks = "▁▂▃▄▅▆▇█"
    max_val = max(values) if max(values) > 0 else 1
    chars = []
    for v in values:
        idx = int(v / max_val * (len(blocks) - 1))
        chars.append(blocks[idx])
    return "".join(chars)


def build_progress_bar(ratio, length=20):
    """Build a colored progress bar string."""
    filled = int(length * min(ratio, 1.0))
    bar = "█" * filled + "░" * (length - filled)

    if ratio < 0.50:
        color = GREEN
    elif ratio < 0.75:
        color = YELLOW
    elif ratio < 0.90:
        color = ORANGE
    else:
        color = RED

    pct = ratio * 100
    return f"{color}{bar}{RESET} {color}{pct:.0f}%{RESET}"


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("statusline: no data")
        return

    model = data.get("model", {}).get("display_name", "unknown")
    model_id = data.get("model", {}).get("id", "")
    cwd = os.path.basename(data.get("workspace", {}).get("current_dir", ""))
    transcript_path = data.get("transcript_path", "")
    session_id = data.get("session_id", "")[:8]

    # Parse transcript for all metrics
    metrics = parse_transcript(transcript_path)

    # Context usage bar
    estimated_total = metrics["context_tokens"] * OVERHEAD_MULTIPLIER
    ratio = estimated_total / CONTEXT_LIMIT if CONTEXT_LIMIT > 0 else 0
    bar = build_progress_bar(ratio)
    tokens_str = format_tokens(int(estimated_total))
    limit_str = format_tokens(CONTEXT_LIMIT)

    # Session cost
    pricing = get_model_pricing(model_id)
    cost = (
        metrics["input_tokens_total"] * pricing["input"] / 1_000_000
        + metrics["cache_read_tokens_total"] * pricing["cache_read"] / 1_000_000
        + metrics["output_tokens_total"] * pricing["output"] / 1_000_000
    )
    cost_str = format_cost(cost)

    # Session duration
    duration_str = format_duration(metrics["session_start"])

    # Git branch + diff
    branch = get_git_branch()
    diff_stat = get_git_diff_stat()
    branch_part = ""
    if branch:
        branch_part = f"{GREEN}{branch}{RESET}"
        if diff_stat:
            branch_part += f" {diff_stat}"

    # Cache hit ratio
    total_input = (
        metrics["input_tokens_total"] + metrics["cache_read_tokens_total"]
    )
    if total_input > 0:
        cache_ratio = metrics["cache_read_tokens_total"] / total_input
        cache_pct = int(cache_ratio * 100)
        if cache_pct >= 70:
            cache_color = GREEN
        elif cache_pct >= 40:
            cache_color = YELLOW
        else:
            cache_color = ORANGE
        cache_part = f"{cache_color}{cache_pct}%{RESET} cache"
    else:
        cache_part = f"{GRAY}0%{RESET} cache"

    # Error rate
    if metrics["tool_errors"] > 0:
        err_color = RED if metrics["tool_errors"] > 5 else ORANGE
        error_part = f"{err_color}{metrics['tool_errors']}{RESET} err"
    else:
        error_part = f"{GREEN}0{RESET} err"

    # Sub-agents
    subagent_part = ""
    if metrics["subagent_count"] > 0:
        subagent_part = f"{CYAN}{metrics['subagent_count']}{RESET} agents"

    # Cost per turn
    cost_per_turn = ""
    if metrics["turn_count"] > 0:
        cpt = cost / metrics["turn_count"]
        cost_per_turn = f"{GRAY}~{format_cost(cpt)}/turn{RESET}"

    sep = f" {GRAY}|{RESET} "

    # Context sparkline
    sparkline = build_sparkline(metrics["context_history"])
    sparkline_part = ""
    if sparkline:
        # Color the sparkline based on current ratio
        if ratio < 0.50:
            spark_color = GREEN
        elif ratio < 0.75:
            spark_color = YELLOW
        elif ratio < 0.90:
            spark_color = ORANGE
        else:
            spark_color = RED
        sparkline_part = f"{spark_color}{sparkline}{RESET}"

    # Line 1: session essentials
    line1_parts = [
        f"{BOLD}{MAGENTA}{model}{RESET}",
        f"{bar} {CYAN}{tokens_str}{RESET}/{GRAY}{limit_str}{RESET}",
    ]
    if sparkline_part:
        line1_parts.append(sparkline_part)
    line1_parts.extend([
        f"{YELLOW}{cost_str}{RESET}",
        f"{WHITE}{duration_str}{RESET}",
        f"{CYAN}{metrics['compact_count']}{RESET}x compact",
        f"{GRAY}{session_id}{RESET}",
    ])

    # Line 2: project context
    line2_parts = [f"{WHITE}{cwd}{RESET}"]
    if branch_part:
        line2_parts.append(branch_part)
    line2_parts.extend([
        f"{CYAN}{metrics['turn_count']}{RESET} turns",
        f"{CYAN}{len(metrics['files_touched'])}{RESET} files",
        error_part,
        cache_part,
    ])
    if cost_per_turn:
        line2_parts.append(cost_per_turn)
    if subagent_part:
        line2_parts.append(subagent_part)

    print(f" {sep.join(line1_parts)}")
    print(f" {sep.join(line2_parts)}")


if __name__ == "__main__":
    main()
