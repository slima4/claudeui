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
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# Full context window size (200k for Claude models)
CONTEXT_LIMIT = 200_000

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
RED = "\033[31m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
GRAY = "\033[90m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(s):
    """Return display width of a string, ignoring ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))


def _get_terminal_cols():
    """Get real terminal width, even when running as a piped subprocess.

    Walks up the process tree to find an ancestor with a TTY, then queries
    that TTY device for the actual terminal dimensions.
    """
    import fcntl, struct, termios
    try:
        pid = os.getpid()
        for _ in range(10):
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,tty="],
                capture_output=True, text=True, timeout=1,
            )
            parts = result.stdout.split()
            if len(parts) < 2:
                break
            ppid, tty = parts[0], parts[1]
            if tty not in ("??", ""):
                fd = os.open(f"/dev/{tty}", os.O_RDONLY)
                try:
                    res = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
                    return struct.unpack("HHHH", res)[1]
                finally:
                    os.close(fd)
            pid = int(ppid)
            if pid <= 1:
                break
    except Exception:
        pass
    return shutil.get_terminal_size().columns


# Widget system — left-side 3-row animation area
# Select via custom.widget in claudeui.json, or STATUSLINE_WIDGET env var (default: matrix)
# Built-in: matrix, bars, progress, none
# Custom: drop a .py file with a render(frame, ratio) function into widgets/

# Settings from ~/.claude/claudeui.json
_SETTINGS_CACHE = None
_SETTINGS_MTIME = 0


def load_settings():
    """Load shared settings from ~/.claude/claudeui.json.

    Re-reads the file if it has been modified since last load.
    """
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    path = os.path.join(os.path.expanduser("~"), ".claude", "claudeui.json")
    try:
        mtime = os.path.getmtime(path)
        if _SETTINGS_CACHE is not None and mtime == _SETTINGS_MTIME:
            return _SETTINGS_CACHE
        with open(path, "r") as f:
            _SETTINGS_CACHE = json.load(f)
        _SETTINGS_MTIME = mtime
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _SETTINGS_CACHE = {}
    return _SETTINGS_CACHE


def get_setting(*keys, default=None):
    """Get a nested setting value. e.g. get_setting('sparkline', 'mode')."""
    cfg = load_settings()
    for key in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(key)
        else:
            return default
    return cfg if cfg is not None else default


def is_visible(line, component):
    """Check if a statusline component is enabled in custom config."""
    return get_setting("custom", line, component, default=True)


def _load_widget(name):
    """Load a widget by name from the widgets/ directory."""
    if name == "none":
        return None
    widgets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "widgets")
    widget_path = os.path.join(widgets_dir, f"{name}.py")
    if not os.path.exists(widget_path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"widgets.{name}",
                                                   widget_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "render", None)


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
        "thinking_count": 0,
        "context_history": [],
        "recent_tools": [],
        "current_turn_file_edits": {},
        "turns_since_compact": 0,
        "context_at_last_compact": 0,
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
        # Reset current turn event counter on new user message
        if obj.get("type") == "user" and "message" in obj:
            content = obj["message"].get("content", [])
            # Only count turns with actual user text, not just tool results
            has_text = False
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        has_text = True
                        break
            elif isinstance(content, str) and content.strip():
                has_text = True
            if has_text:
                result["turn_count"] += 1
                result["turns_since_compact"] += 1
                result["recent_tools"] = []
                result["current_turn_file_edits"] = {}

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

            # Capture post-compaction baseline from first usage after compact
            if result["context_at_last_compact"] == -1:
                keys = ["input_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens", "output_tokens"]
                result["context_at_last_compact"] = sum(
                    usage.get(k, 0) for k in keys
                )

            # Per-turn token spend for sparkline
            out_tok = usage.get("output_tokens", 0)
            if out_tok > 0:
                result["context_history"].append(out_tok)

        # Compact count — also record a 0 in context history (visual cliff)
        if obj.get("type") == "summary" or (
            obj.get("type") == "system"
            and obj.get("subtype") == "compact_boundary"
        ):
            result["compact_count"] += 1
            result["context_history"].append(None)
            result["turns_since_compact"] = 0
            result["context_at_last_compact"] = -1  # sentinel: next usage will set baseline

        # Tool calls, thinking, errors, files, and subagents
        if obj.get("type") == "assistant" and "message" in obj:
            content = obj["message"].get("content", [])
            has_thinking = False
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "thinking":
                        has_thinking = True
                    if block.get("type") == "tool_use":
                        result["tool_calls"] += 1
                        inp = block.get("input", {})
                        tool_name = block.get("name", "")

                        # Recent tool activity (for line 3)
                        file_arg = ""
                        for key in ("file_path", "path"):
                            if key in inp and isinstance(inp[key], str):
                                file_arg = os.path.basename(inp[key])
                                break
                        if file_arg:
                            result["recent_tools"].append(
                                f"{tool_name} {file_arg}"
                            )
                        else:
                            cmd = inp.get("command", "")
                            if cmd:
                                # Show first word of command
                                short = cmd.split()[0] if cmd.split() else ""
                                result["recent_tools"].append(
                                    f"{tool_name} {short}"
                                )
                            else:
                                result["recent_tools"].append(tool_name)

                        # Files touched
                        for key in ("file_path", "path"):
                            if key in inp and isinstance(inp[key], str):
                                result["files_touched"].add(inp[key])
                                # Track edits per file this turn
                                if tool_name in ("Edit", "Write",
                                                  "MultiEdit"):
                                    fname = os.path.basename(inp[key])
                                    result["current_turn_file_edits"][fname] = (
                                        result["current_turn_file_edits"]
                                        .get(fname, 0) + 1
                                    )

                        # Sub-agent tracking
                        if tool_name in ("Task", "Agent"):
                            task_id = block.get("id", "")
                            if task_id:
                                active_subagents.add(task_id)
            if has_thinking:
                result["thinking_count"] += 1

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
        now = datetime.now(timezone.utc)
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
    """Build a colored sparkline showing per-turn token spend.

    Scaled relative to the peak value in the data so the shape
    reveals which turns were expensive vs cheap.

    Args:
        values: list of output-token counts (None = compaction event).
        width: max number of characters to render.

    Returns:
        ANSI-colored sparkline string.
    """
    if not values:
        return ""
    # Keep only the last 3 compaction markers; replace older ones with 0
    none_indices = [i for i, v in enumerate(values) if v is None]
    keep_set = set(none_indices[-3:])
    cleaned = []
    for i, v in enumerate(values):
        if v is None and i not in keep_set:
            cleaned.append(0)
        else:
            cleaned.append(v)
    values = cleaned

    # Display mode: "tail" (last N turns) or "merge" (downsample all)
    mode = get_setting("sparkline", "mode", default="tail")
    if mode == "merge":
        merge_size = get_setting("sparkline", "merge_size", default=2)
        # Merge consecutive turns into buckets of merge_size
        merged = []
        for i in range(0, len(values), merge_size):
            bucket = values[i:i + merge_size]
            if None in bucket:
                merged.append(None)
            else:
                merged.append(sum(v for v in bucket if v is not None))
        values = merged
        if len(values) > width:
            values = values[-width:]
    else:
        # Tail: show only the most recent turns at full resolution
        if len(values) > width:
            values = values[-width:]

    blocks = "▁▂▃▄▅▆▇█"
    peak = max((v for v in values if v is not None), default=1)
    scale = peak if peak > 0 else 1
    chars = []
    for v in values:
        if v is None:
            chars.append(f"{MAGENTA}↓{RESET}")
            continue
        r = v / scale
        idx = int(r * (len(blocks) - 1))
        idx = max(0, min(idx, len(blocks) - 1))
        if r < 0.25:
            color = GREEN
        elif r < 0.50:
            color = CYAN
        elif r < 0.75:
            color = YELLOW
        else:
            color = ORANGE
        chars.append(f"{color}{blocks[idx]}{RESET}")
    return "".join(chars)



def build_progress_bar(ratio, length=20):
    """Build a colored progress bar string."""
    filled = int(length * min(ratio, 1.0))
    bar = "█" * filled + "░" * (length - filled)

    if ratio < 0.50:
        color = GREEN
    elif ratio < 0.70:
        color = YELLOW
    elif ratio < 0.80:
        color = ORANGE
    else:
        color = RED

    pct = ratio * 100
    return f"{color}{bar}{RESET} {color}{pct:.0f}%{RESET}"


def main():
    compact_mode = "--compact" in sys.argv

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
    ctx_used = metrics["context_tokens"]
    ratio = ctx_used / CONTEXT_LIMIT if CONTEXT_LIMIT > 0 else 0
    bar = build_progress_bar(ratio)
    tokens_str = format_tokens(int(ctx_used))
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

    # Per-turn token spend sparkline (relative to peak)
    sparkline_part = build_sparkline(metrics["context_history"])

    # Compaction prediction (turns remaining until auto-compaction)
    # Claude compacts at ~83% by default; user can override via env var
    compact_prediction = ""
    turns_since = metrics["turns_since_compact"]
    if turns_since >= 2 and ratio > 0 and ratio < 1.0:
        compact_pct = 83
        env_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "")
        if env_pct.isdigit() and 1 <= int(env_pct) <= 100:
            compact_pct = int(env_pct)
        compact_ceiling = CONTEXT_LIMIT * compact_pct / 100
        # Average context growth per turn since last compaction
        baseline = metrics["context_at_last_compact"]
        growth_since = ctx_used - baseline if baseline > 0 else ctx_used
        growth_per_turn = growth_since / max(turns_since, 1)
        remaining_tokens = compact_ceiling - ctx_used
        if growth_per_turn > 0 and remaining_tokens > 0:
            turns_left = int(remaining_tokens / growth_per_turn)
            if turns_left <= 5:
                pred_color = RED
            elif turns_left <= 15:
                pred_color = ORANGE
            elif turns_left <= 30:
                pred_color = YELLOW
            else:
                pred_color = GREEN
            compact_prediction = (
                f"{pred_color}~{turns_left}{RESET} {GRAY}turns left{RESET}"
            )

    dim = GRAY
    sep = f" {dim}│{RESET} "

    # Line 1: session core
    line1_parts = []
    if is_visible("line1", "model"):
        line1_parts.append(f"{BOLD}{MAGENTA}{model}{RESET}")
    if is_visible("line1", "context_bar"):
        ctx_part = f"{bar}"
        if is_visible("line1", "token_count"):
            ctx_part += f" {CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx_part += f" {dim}│{RESET} {compact_prediction}"
        line1_parts.append(ctx_part)
    elif is_visible("line1", "token_count"):
        ctx_part = f"{CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx_part += f" {dim}│{RESET} {compact_prediction}"
        line1_parts.append(ctx_part)
    elif compact_prediction and is_visible("line1", "compact_prediction"):
        line1_parts.append(compact_prediction)
    if sparkline_part and is_visible("line1", "sparkline"):
        line1_parts.append(sparkline_part)
    if is_visible("line1", "cost"):
        line1_parts.append(f"{YELLOW}{cost_str}{RESET}")
    if is_visible("line1", "duration"):
        line1_parts.append(f"{WHITE}{duration_str}{RESET}")
    if is_visible("line1", "compact_count"):
        line1_parts.append(
            f"{CYAN}{metrics['compact_count']}{RESET}{dim}x{RESET}compact"
        )
    if is_visible("line1", "session_id"):
        line1_parts.append(f"{dim}#{RESET}{GRAY}{session_id}{RESET}")

    # Line 2: project telemetry
    line2_parts = []
    if is_visible("line2", "cwd"):
        line2_parts.append(f"{GREEN}{cwd}{RESET}")
    if branch_part and is_visible("line2", "git_branch"):
        line2_parts.append(branch_part)
    if is_visible("line2", "turns"):
        line2_parts.append(
            f"{CYAN}{metrics['turn_count']}{RESET} {dim}turns{RESET}"
        )
    if is_visible("line2", "files"):
        line2_parts.append(
            f"{CYAN}{len(metrics['files_touched'])}{RESET} {dim}files{RESET}"
        )
    if is_visible("line2", "errors"):
        line2_parts.append(f"{error_part.split(' ')[0]} {dim}err{RESET}")
    if is_visible("line2", "cache"):
        line2_parts.append(f"{cache_part.split(' ')[0]} {dim}cache{RESET}")
    if metrics["thinking_count"] > 0 and is_visible("line2", "thinking"):
        line2_parts.append(
            f"{MAGENTA}{metrics['thinking_count']}{RESET}{dim}x{RESET} {dim}think{RESET}"
        )
    if cost_per_turn and is_visible("line2", "cost_per_turn"):
        line2_parts.append(cost_per_turn)
    if subagent_part and is_visible("line2", "agents"):
        line2_parts.append(
            f"{CYAN}{metrics['subagent_count']}{RESET} {dim}agents{RESET}"
        )

    # Line 3+: live activity trace (wraps to extra lines if needed)
    line3_lines = []
    recent = metrics["recent_tools"]
    file_edits = metrics["current_turn_file_edits"]
    trail_items = []
    if recent and is_visible("line3", "tool_trace"):
        for t in recent[-6:]:
            p = t.split()
            if len(p) >= 2:
                trail_items.append(f"{dim}{p[0].lower()}{RESET} {GREEN}{p[-1]}{RESET}")
            else:
                trail_items.append(f"{dim}{p[0].lower()}{RESET}")
    file_edit_parts = []
    if file_edits and is_visible("line3", "file_edits"):
        top = sorted(file_edits.items(), key=lambda x: -x[1])[:3]
        file_edit_parts = [
            f"{YELLOW}{n}{RESET}{dim}×{c}{RESET}" for n, c in top
        ]

    # Wrap trail items across lines based on terminal width
    term_cols = _get_terminal_cols()
    widget_offset = 10  # widget (7) + padding (3)
    max_width = term_cols - widget_offset
    arrow = f" {dim}\u2192{RESET} "
    arrow_vis = 4  # " → " visible width

    if trail_items or file_edit_parts:
        cur_line_parts = []
        cur_width = 1  # leading space
        for i, item in enumerate(trail_items):
            item_width = _visible_len(item)
            joiner_width = arrow_vis if cur_line_parts else 0
            if cur_line_parts and cur_width + joiner_width + item_width > max_width:
                line3_lines.append(f" {arrow.join(cur_line_parts)}")
                cur_line_parts = [item]
                cur_width = 1 + item_width
            else:
                cur_line_parts.append(item)
                cur_width += joiner_width + item_width
        if cur_line_parts:
            tail = arrow.join(cur_line_parts)
            if file_edit_parts:
                edit_str = " ".join(file_edit_parts)
                edit_width = _visible_len(edit_str)
                sep_width = _visible_len(sep)
                if cur_width + sep_width + edit_width <= max_width:
                    tail += f"{sep}{edit_str}"
                else:
                    line3_lines.append(f" {tail}")
                    tail = f" {edit_str}"
            line3_lines.append(f" {tail}")
        elif file_edit_parts:
            line3_lines.append(f" {' '.join(file_edit_parts)}")

    # ── Compact mode: single line with essentials ──
    if compact_mode:
        compact_parts = []
        if is_visible("line1", "model"):
            compact_parts.append(f"{BOLD}{MAGENTA}{model}{RESET}")
        if is_visible("line1", "context_bar") and is_visible("line1", "token_count"):
            compact_parts.append(
                f"{bar} {CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
            )
        elif is_visible("line1", "context_bar"):
            compact_parts.append(f"{bar}")
        elif is_visible("line1", "token_count"):
            compact_parts.append(
                f"{CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
            )
        if is_visible("line1", "sparkline") and sparkline_part:
            compact_spark = build_sparkline(metrics["context_history"], width=10)
            if compact_spark:
                compact_parts.append(compact_spark)
        if is_visible("line1", "cost"):
            compact_parts.append(f"{YELLOW}{cost_str}{RESET}")
        if is_visible("line1", "duration"):
            compact_parts.append(f"{WHITE}{duration_str}{RESET}")
        if is_visible("line2", "turns"):
            compact_parts.append(
                f"{CYAN}{metrics['turn_count']}{RESET} {dim}turns{RESET}"
            )
        if is_visible("line1", "compact_count"):
            compact_parts.append(
                f"{CYAN}{metrics['compact_count']}{RESET}{dim}x{RESET}compact"
            )
        if metrics["tool_errors"] > 0 and is_visible("line2", "errors"):
            compact_parts.append(error_part)
        if compact_parts:
            print(f" {sep.join(compact_parts)}")
        return

    # ── Full mode: 3 lines ──

    # Widget: config takes precedence, then env var
    widget_name = (get_setting("custom", "widget", default=None)
                   or os.environ.get("STATUSLINE_WIDGET", "matrix"))
    widget_fn = _load_widget(widget_name)

    line1_str = f" {sep.join(line1_parts)}" if line1_parts else ""
    line2_str = f" {sep.join(line2_parts)}" if line2_parts else ""

    if widget_fn:
        wdg = widget_fn(frame=metrics["tool_calls"], ratio=ratio)
        print(f" {wdg[0]}{line1_str}")
        print(f" {wdg[1]}{line2_str}")
        first_extra = line3_lines[0] if line3_lines else ""
        print(f" {wdg[2]}{first_extra}")
        for extra_line in line3_lines[1:]:
            print(f"         {extra_line}")
    else:
        if line1_str:
            print(f"{line1_str}")
        if line2_str:
            print(f"{line2_str}")
        for extra_line in line3_lines:
            print(f"{extra_line}")


if __name__ == "__main__":
    main()
