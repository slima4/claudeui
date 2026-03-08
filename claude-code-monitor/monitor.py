#!/usr/bin/env python3
"""Live session monitor — runs in a separate terminal window.

Usage:
    python3 monitor.py              # auto-detect latest session
    python3 monitor.py <session-id> # monitor specific session
    python3 monitor.py --list       # list recent sessions

Hotkeys:
    s  session stats        d  session details
    l  event log            e  export session
    o  list sessions        ?  help overlay
    q  quit
"""

import json
import os
import select
import shutil
import textwrap
import subprocess
import sys
import termios
import time
import tty
import signal
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ── Pricing and limits ──────────────────────────────────────────────

MODEL_PRICING = {
    "claude-opus-4-6": {"input": 15.0, "cache_read": 1.5, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "cache_read": 0.30, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "cache_read": 0.08, "output": 4.0},
}
CONTEXT_LIMIT = 200_000
OVERHEAD_MULTIPLIER = 1.2
_original_termios = None


# ── ANSI codes ─────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[38;5;208m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
GRAY = "\033[90m"
CLEAR = "\033[2J\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
ERASE_LINE = "\033[2K"
ALT_SCREEN_ON = "\033[?1049h"
ALT_SCREEN_OFF = "\033[?1049l"

# Matrix colors
M_DARK = "\033[38;2;0;59;0m"
M_MID = "\033[38;2;3;160;98m"
M_BRIGHT = "\033[38;2;0;255;65m"

# Activity pulse colors
PULSE_NEW = "\033[38;2;0;255;65m"  # bright green flash
PULSE_IDLE = "\033[38;2;80;80;80m"  # dim gray


# ── Transcript parsing ──────────────────────────────────────────────

def find_transcript(cwd=None):
    """Find the most recent transcript for the given working directory."""
    if cwd is None:
        cwd = os.getcwd()
    projects_dir = Path.home() / ".claude" / "projects"
    project_name = "-" + cwd.replace("/", "-").lstrip("-")
    project_dir = projects_dir / project_name
    if not project_dir.exists():
        project_name = cwd.replace("/", "-").lstrip("-")
        project_dir = projects_dir / project_name
    if not project_dir.exists():
        return None
    jsonl_files = sorted(project_dir.glob("*.jsonl"),
                         key=lambda f: f.stat().st_mtime, reverse=True)
    return str(jsonl_files[0]) if jsonl_files else None


def find_latest_transcript():
    """Find the most recently modified transcript across all projects."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    latest = None
    latest_mtime = 0
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            mtime = jsonl.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest = str(jsonl)
    return latest


def parse_transcript(path):
    """Parse a transcript JSONL file into a comprehensive report dict."""
    r = {
        "path": path, "model": "", "session_id": Path(path).stem[:8],
        "start_time": None, "end_time": None,
        "turns": 0, "responses": 0,
        "compact_count": 0, "compact_events": [],
        "tokens": {"input": 0, "cache_read": 0, "cache_creation": 0, "output": 0},
        "context_history": [], "per_response": [],
        "tool_counts": Counter(), "tool_errors": 0, "tool_error_details": [],
        "files_read": Counter(), "files_edited": Counter(),
        "thinking_count": 0, "subagent_count": 0, "turns_since_compact": 0,
        "recent_tools": [],  # last N tool calls for live trace
        "last_error_msg": "",
        # Current turn (current question/answer)
        "turn_tool_counts": Counter(),
        "turn_tool_errors": 0,
        "turn_files_read": Counter(),
        "turn_files_edited": Counter(),
        "turn_thinking": 0,
        # Turn timer
        "last_user_ts": None,   # timestamp of last user message
        "last_assist_ts": None, # timestamp of last assistant response
        "waiting_for_response": False,
        # Event log
        "event_log": [],  # list of (timestamp_str, description)
    }
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return r

    subagents = set()
    current_turn = 0
    last_context = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = obj.get("timestamp")
        etype = obj.get("type", "")
        if ts:
            if r["start_time"] is None:
                r["start_time"] = ts
            r["end_time"] = ts
        if not r["model"] and etype == "assistant" and "message" in obj:
            r["model"] = obj["message"].get("model", "")

        # User turns
        if etype == "user" and not obj.get("isMeta"):
            content = obj.get("message", {}).get("content", "")
            has_text = False
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        has_text = True
                        break
            elif isinstance(content, str) and content.strip():
                has_text = True
            if has_text:
                current_turn += 1
                r["turns"] += 1
                r["turns_since_compact"] += 1
                r["last_user_ts"] = ts
                r["waiting_for_response"] = True
                r["recent_tools"] = []
                r["turn_tool_counts"] = Counter()
                r["turn_tool_errors"] = 0
                r["turn_files_read"] = Counter()
                r["turn_files_edited"] = Counter()
                r["turn_thinking"] = 0

        # Tool errors
        if etype == "user" and "message" in obj:
            content = obj.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and block.get("is_error")):
                        r["tool_errors"] += 1
                        r["turn_tool_errors"] += 1
                        # Capture error message
                        err_content = block.get("content", "")
                        if isinstance(err_content, list):
                            for eb in err_content:
                                if isinstance(eb, dict) and eb.get("type") == "text":
                                    err_content = eb.get("text", "")
                                    break
                        if isinstance(err_content, str) and err_content:
                            r["last_error_msg"] = err_content[:300]
                        r["tool_error_details"].append({
                            "turn": current_turn,
                            "error": r["last_error_msg"],
                        })
                        err_msg = r["last_error_msg"] if r["last_error_msg"] else "unknown"
                        r["event_log"].append((ts, f"error: {err_msg}"))

        # Assistant responses
        if etype == "assistant" and "message" in obj:
            msg = obj["message"]
            content = msg.get("content", [])
            usage = msg.get("usage", {})
            r["responses"] += 1
            r["last_assist_ts"] = ts
            r["waiting_for_response"] = False
            has_thinking = False
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "thinking":
                            has_thinking = True
                        if block.get("type") == "tool_use":
                            name = block.get("name", "unknown")
                            r["tool_counts"][name] += 1
                            r["turn_tool_counts"][name] += 1
                            inp = block.get("input", {})
                            if name in ("Task", "Agent"):
                                tid = block.get("id", "")
                                if tid:
                                    subagents.add(tid)
                            fp = inp.get("file_path", inp.get("path", ""))
                            # Build tool trace entry + event log
                            if fp:
                                fname = os.path.basename(fp)
                                if name in ("Edit", "Write", "MultiEdit"):
                                    r["files_edited"][fname] += 1
                                    r["turn_files_edited"][fname] += 1
                                else:
                                    r["files_read"][fname] += 1
                                    r["turn_files_read"][fname] += 1
                                trace_entry = f"{name.lower()} {fname}"
                                r["recent_tools"].append(trace_entry)
                                r["event_log"].append((ts, trace_entry))
                            else:
                                cmd = inp.get("command", "")
                                if cmd:
                                    cmd_short = cmd.split()[0] if cmd else ""
                                    trace_entry = f"{name.lower()} {cmd_short}"
                                    # Full command for event log (clean up multiline)
                                    cmd_clean = cmd.replace("\n", " ").strip()
                                    r["event_log"].append((ts, f"$ {cmd_clean}"))
                                else:
                                    query = inp.get("pattern", inp.get("query", inp.get("prompt", "")))
                                    if query:
                                        q_clean = str(query).replace("\n", " ").strip()[:80]
                                        trace_entry = f"{name.lower()} {q_clean}"
                                        r["event_log"].append((ts, f"{name.lower()}: {q_clean}"))
                                    else:
                                        trace_entry = name.lower()
                                        r["event_log"].append((ts, trace_entry))
                                r["recent_tools"].append(trace_entry)
            if has_thinking:
                r["thinking_count"] += 1
                r["turn_thinking"] += 1
            if usage:
                inp_t = usage.get("input_tokens", 0)
                cache_r = usage.get("cache_read_input_tokens", 0)
                cache_c = usage.get("cache_creation_input_tokens", 0)
                out_t = usage.get("output_tokens", 0)
                r["tokens"]["input"] += inp_t
                r["tokens"]["cache_read"] += cache_r
                r["tokens"]["cache_creation"] += cache_c
                r["tokens"]["output"] += out_t
                ctx = inp_t + cache_r + cache_c + out_t
                last_context = ctx
                r["context_history"].append(ctx)
                r["per_response"].append({
                    "turn": current_turn, "ctx": ctx, "output": out_t,
                    "timestamp": ts,
                })

        # Compaction
        if (etype == "summary" or
                (etype == "system" and obj.get("subtype") == "compact_boundary")):
            r["compact_count"] += 1
            r["context_history"].append(None)
            r["event_log"].append((ts, f"⚡ compaction #{r['compact_count']}"))
            r["compact_events"].append({
                "turn": current_turn,
                "context_before": last_context,
                "turns_since_last": r["turns_since_compact"],
            })
            r["turns_since_compact"] = 0

    r["subagent_count"] = len(subagents)
    r["last_context"] = last_context
    r["recent_tools"] = r["recent_tools"][-5:]
    r["full_log"] = list(r["event_log"])  # full log for viewer
    r["event_log"] = r["event_log"][-8:]  # truncated for dashboard
    return r


def get_pricing(model):
    for key, p in MODEL_PRICING.items():
        if key in model:
            return p
    return MODEL_PRICING["claude-sonnet-4-6"]


def calc_cost(tokens, pricing):
    c_input = tokens["input"] * pricing["input"] / 1_000_000
    c_cache = tokens["cache_read"] * pricing["cache_read"] / 1_000_000
    c_output = tokens["output"] * pricing["output"] / 1_000_000
    return {"input": c_input, "cache_read": c_cache, "output": c_output,
            "total": c_input + c_cache + c_output}


def format_duration_live(start_ts):
    """Format duration from start to NOW (live updating)."""
    try:
        start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - start).total_seconds())
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        if h > 0:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m}m {s:02d}s"
    except Exception:
        return "—"


def format_event_time(ts_str):
    """Format timestamp to HH:MM:SS for event log."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%H:%M:%S")
    except Exception:
        return "??:??:??"


def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def get_terminal_width():
    """Get terminal width, default 80."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


# ── Dashboard rendering ─────────────────────────────────────────────

def color_ratio(ratio):
    """Get color for a context ratio."""
    if ratio < 0.50:
        return GREEN
    elif ratio < 0.75:
        return YELLOW
    elif ratio < 0.90:
        return ORANGE
    return RED


def build_bar(ratio, width=30):
    """Build a colored progress bar."""
    filled = int(width * min(ratio, 1.0))
    bar = "█" * filled + "░" * (width - filled)
    return f"{color_ratio(ratio)}{bar}{RESET}"


def build_sparkline(values, width=50):
    """Build colored sparkline from context history."""
    if not values:
        return ""
    cleaned = [v if v is not None else 0 for v in values]
    none_set = {i for i, v in enumerate(values) if v is None}

    if len(cleaned) > width:
        step = len(cleaned) / width
        sampled = []
        none_sampled = set()
        for i in range(width):
            start = int(i * step)
            end = int((i + 1) * step)
            sampled.append(max(cleaned[start:end]))
            for j in range(start, end):
                if j in none_set:
                    none_sampled.add(i)
        cleaned = sampled
        none_set = none_sampled

    blocks = "▁▂▃▄▅▆▇█"
    chars = []
    for i, v in enumerate(cleaned):
        if i in none_set:
            chars.append(f"{MAGENTA}↓{RESET}")
            continue
        r = v / CONTEXT_LIMIT
        idx = max(0, min(int(r * (len(blocks) - 1)), len(blocks) - 1))
        chars.append(f"{color_ratio(r)}{blocks[idx]}{RESET}")
    return "".join(chars)


def render_matrix_header(frame, width=60, active=True):
    """Render just the matrix rain header line.

    When active=True, animates the rain. When False, shows a static dim line.
    """
    rain = "10110010011101001011100101100110100"
    speeds = [1, 3, 2, 1, 2, 3, 1, 2, 3, 1, 2, 3, 2, 1, 3]
    if active:
        m_colors = [M_DARK, M_DARK, M_MID, M_MID, M_BRIGHT]
    else:
        m_colors = [M_DARK, M_DARK, M_DARK]
    line = ""
    for c in range(width):
        f = frame if active else 0
        idx = c * 5 - f * speeds[c % len(speeds)]
        ch = rain[idx % len(rain)]
        cidx = c * 3 - f * speeds[c % len(speeds)]
        mc = m_colors[cidx % len(m_colors)]
        line += f"{mc}{ch}{RESET}"
    return line


def render_dashboard(r, idle_secs, just_updated, term_width):
    """Render dashboard lines (without matrix header).

    Args:
        r: parsed transcript dict
        idle_secs: seconds since last transcript change
        just_updated: True if data just changed (for pulse effect)
        term_width: terminal width for adaptive layout
    """
    pricing = get_pricing(r["model"])
    cost = calc_cost(r["tokens"], pricing)
    estimated = r["last_context"] * OVERHEAD_MULTIPLIER
    ratio = estimated / CONTEXT_LIMIT if estimated > 0 else 0
    duration = format_duration_live(r["start_time"])
    w = min(term_width - 2, 80)  # content width, cap at 80
    bar_width = max(20, min(w - 30, 40))
    bar = build_bar(ratio, bar_width)
    spark_width = max(20, min(w - 10, 60))
    scaled_history = [v * OVERHEAD_MULTIPLIER if v is not None else None
                      for v in r["context_history"]]
    sparkline = build_sparkline(scaled_history, spark_width)

    # Compaction prediction
    turns_left = "—"
    if r["turns_since_compact"] >= 2 and ratio > 0 and ratio < 1.0:
        growth = estimated / max(r["turns_since_compact"], 1)
        remaining = CONTEXT_LIMIT - estimated
        if growth > 0:
            tl = int(remaining / growth)
            c = color_ratio(1.0 - tl / 100 if tl < 100 else 0)
            turns_left = f"{c}~{tl}{RESET}"

    # Cache savings
    cache_without = r["tokens"]["cache_read"] * pricing["input"] / 1_000_000
    cache_actual = r["tokens"]["cache_read"] * pricing["cache_read"] / 1_000_000
    saved = cache_without - cache_actual

    # Cache ratio
    total_input = r["tokens"]["input"] + r["tokens"]["cache_read"]
    cache_pct = int(r["tokens"]["cache_read"] / total_input * 100) if total_input > 0 else 0

    # Cost per turn
    cpt = cost["total"] / r["turns"] if r["turns"] > 0 else 0

    # Cost per minute (burn rate)
    cost_per_min = ""
    if r["start_time"]:
        try:
            start = datetime.fromisoformat(r["start_time"].replace("Z", "+00:00"))
            elapsed_min = (datetime.now(timezone.utc) - start).total_seconds() / 60
            if elapsed_min > 1:
                cpm = cost["total"] / elapsed_min
                cost_per_min = f"  {DIM}│{RESET}  {ORANGE}${cpm:.2f}/min{RESET}"
        except Exception:
            pass

    # Activity status
    if idle_secs < 5:
        status_dot = f"{GREEN}●{RESET}"
        status_text = f"{GREEN}ACTIVE{RESET}"
    elif idle_secs < 30:
        status_dot = f"{YELLOW}●{RESET}"
        status_text = f"{YELLOW}WORKING{RESET}"
    elif idle_secs < 120:
        status_dot = f"{ORANGE}○{RESET}"
        status_text = f"{ORANGE}IDLE {int(idle_secs)}s{RESET}"
    else:
        idle_m = int(idle_secs / 60)
        status_dot = f"{GRAY}○{RESET}"
        status_text = f"{GRAY}IDLE {idle_m}m{RESET}"

    # Separator color — pulse on new data
    sep_color = PULSE_NEW if just_updated else M_MID
    sep = f"{sep_color}{'─' * w}{RESET}"

    # Turn timer
    turn_timer = ""
    if r["waiting_for_response"] and r["last_user_ts"]:
        try:
            user_dt = datetime.fromisoformat(r["last_user_ts"].replace("Z", "+00:00"))
            turn_secs = int((datetime.now(timezone.utc) - user_dt).total_seconds())
            if turn_secs < 60:
                tt = f"{turn_secs}s"
            elif turn_secs < 3600:
                tt = f"{turn_secs // 60}m {turn_secs % 60:02d}s"
            else:
                tt = f"{turn_secs // 3600}h {(turn_secs % 3600) // 60}m"
            # Color: green <30s, yellow <2m, orange <5m, red >5m
            if turn_secs < 30:
                tc = GREEN
            elif turn_secs < 120:
                tc = YELLOW
            elif turn_secs < 300:
                tc = ORANGE
            else:
                tc = RED
            turn_timer = f"  {DIM}│{RESET}  {tc}⏱ {tt}{RESET}"
        except Exception:
            pass

    lines = []
    lines.append(sep)
    lines.append(f"  {status_dot} {BOLD}{MAGENTA}{r['model'] or 'unknown'}{RESET}  {DIM}│{RESET}  {GRAY}{r['session_id']}{RESET}  {DIM}│{RESET}  {WHITE}{duration}{RESET}  {DIM}│{RESET}  {status_text}{turn_timer}")
    lines.append("")

    # Context section
    lines.append(f"  {BOLD}CONTEXT{RESET}")
    lines.append(f"  {bar}  {color_ratio(ratio)}{ratio * 100:.1f}%{RESET}  {CYAN}{format_tokens(int(estimated))}{RESET}{DIM}/{RESET}{GRAY}{format_tokens(CONTEXT_LIMIT)}{RESET}")
    lines.append(f"  {sparkline}")
    compact_line = f"  {DIM}Compactions:{RESET} {CYAN}{r['compact_count']}{RESET}  {DIM}│{RESET}  {DIM}Turns left:{RESET} {turns_left}  {DIM}│{RESET}  {DIM}Since compact:{RESET} {CYAN}{r['turns_since_compact']}{RESET}"

    # Compaction alert — highlight if just happened
    if r["compact_events"]:
        last_compact = r["compact_events"][-1]
        if last_compact["turns_since_last"] == 0 and r["turns_since_compact"] <= 2:
            compact_line += f"  {BOLD}{YELLOW}⚡ JUST COMPACTED{RESET}"

    lines.append(compact_line)
    lines.append("")

    # Cost section
    lines.append(f"  {BOLD}COST{RESET}")
    lines.append(f"  {YELLOW}${cost['total']:.2f}{RESET} total  {DIM}│{RESET}  ~{GRAY}${cpt:.2f}/turn{RESET}{cost_per_min}  {DIM}│{RESET}  {GREEN}${saved:.2f} saved{RESET}")
    lines.append(f"  {DIM}Input:{RESET} ${cost['input']:.2f}  {DIM}│{RESET}  {DIM}Cache:{RESET} ${cost['cache_read']:.2f}  {DIM}│{RESET}  {DIM}Output:{RESET} ${cost['output']:.2f}")

    # Token breakdown bar
    tok_total = r["tokens"]["input"] + r["tokens"]["cache_read"] + r["tokens"]["output"]
    if tok_total > 0:
        tb_width = max(20, min(w - 10, 50))
        inp_frac = r["tokens"]["input"] / tok_total
        cache_frac = r["tokens"]["cache_read"] / tok_total
        out_frac = r["tokens"]["output"] / tok_total
        inp_w = max(1, int(tb_width * inp_frac)) if inp_frac > 0.005 else 0
        out_w = max(1, int(tb_width * out_frac)) if out_frac > 0.005 else 0
        cache_w = tb_width - inp_w - out_w
        tok_bar = f"{CYAN}{'█' * inp_w}{RESET}{GREEN}{'█' * cache_w}{RESET}{YELLOW}{'█' * out_w}{RESET}"
        tok_legend = f"{CYAN}■{RESET}{DIM}input {inp_frac:.0%}{RESET}  {GREEN}■{RESET}{DIM}cache {cache_frac:.0%}{RESET}  {YELLOW}■{RESET}{DIM}output {out_frac:.0%}{RESET}"
        lines.append(f"  {tok_bar}")
        lines.append(f"  {tok_legend}")

    lines.append("")

    # ── Activity: Current turn (this question/answer) ──
    lines.append(f"  {BOLD}CURRENT{RESET}")

    turn_tools = sum(r["turn_tool_counts"].values())
    turn_top3 = r["turn_tool_counts"].most_common(3)
    turn_tools_str = "  ".join(f"{DIM}{t}:{RESET}{CYAN}{c}{RESET}" for t, c in turn_top3)
    lines.append(f"  {CYAN}{turn_tools}{RESET} tools  {DIM}│{RESET}  {turn_tools_str}")

    # Live tool trace + file edit counts (same format as statusline)
    if r["recent_tools"] or r["turn_files_edited"]:
        parts = []
        if r["recent_tools"]:
            trail = []
            for t in r["recent_tools"][-6:]:
                p = t.split()
                if len(p) >= 2:
                    trail.append(f"{DIM}{p[0].lower()}{RESET} {GREEN}{p[-1]}{RESET}")
                else:
                    trail.append(f"{DIM}{p[0].lower()}{RESET}")
            parts.append(f" {DIM}→{RESET} ".join(trail))
        if r["turn_files_edited"]:
            top = sorted(r["turn_files_edited"].items(), key=lambda x: -x[1])[:3]
            parts.append(" ".join(
                f"{YELLOW}{n}{RESET}{DIM}×{c}{RESET}" for n, c in top
            ))
        lines.append(f"  {f' {DIM}│{RESET} '.join(parts)}")

    # Current turn files
    turn_all_files = set(list(r["turn_files_read"].keys()) + list(r["turn_files_edited"].keys()))
    turn_top_files = sorted(
        turn_all_files,
        key=lambda f: -(r["turn_files_read"].get(f, 0) + r["turn_files_edited"].get(f, 0))
    )[:5]
    if turn_top_files:
        file_parts = []
        for f in turn_top_files:
            reads = r["turn_files_read"].get(f, 0)
            edits = r["turn_files_edited"].get(f, 0)
            file_parts.append(f"{GREEN}{f}{RESET}{DIM}({reads}r/{edits}e){RESET}")
        lines.append(f"  {' '.join(file_parts)}")

    turn_err_color = RED if r["turn_tool_errors"] > 3 else ORANGE if r["turn_tool_errors"] > 0 else GREEN
    lines.append(f"  {turn_err_color}{r['turn_tool_errors']}{RESET} errors  {DIM}│{RESET}  {MAGENTA}{r['turn_thinking']}{RESET} thinking")

    # Last error detail
    if r["last_error_msg"]:
        err_text = r["last_error_msg"]
        max_line = w - 4
        label = f"  {DIM}Last error:{RESET} "
        first_line_max = max_line - 12
        lines.append(f"{label}{RED}{err_text[:first_line_max]}{RESET}")
        remaining = err_text[first_line_max:]
        while remaining:
            lines.append(f"  {RED}{remaining[:max_line]}{RESET}")
            remaining = remaining[max_line:]

    lines.append("")

    # ── Activity: Session totals ──
    lines.append(f"  {BOLD}{DIM}SESSION{RESET}")
    total_tools = sum(r["tool_counts"].values())
    top3 = r["tool_counts"].most_common(3)
    tools_str = "  ".join(f"{DIM}{t}:{RESET}{GRAY}{c}{RESET}" for t, c in top3)
    lines.append(f"  {GRAY}{r['turns']}{RESET} {DIM}turns{RESET}  {DIM}│{RESET}  {GRAY}{total_tools}{RESET} {DIM}tools{RESET}  {DIM}│{RESET}  {tools_str}")

    # Session files
    top_files = sorted(
        set(list(r["files_read"].keys()) + list(r["files_edited"].keys())),
        key=lambda f: -(r["files_read"].get(f, 0) + r["files_edited"].get(f, 0))
    )[:5]
    if top_files:
        file_parts = []
        for f in top_files:
            reads = r["files_read"].get(f, 0)
            edits = r["files_edited"].get(f, 0)
            file_parts.append(f"{DIM}{f}({reads}r/{edits}e){RESET}")
        lines.append(f"  {' '.join(file_parts)}")

    err_color = RED if r["tool_errors"] > 5 else ORANGE if r["tool_errors"] > 0 else GREEN
    think_pct = r["thinking_count"] / max(r["responses"], 1) * 100
    session_stats = f"  {err_color}{r['tool_errors']}{RESET} {DIM}errors{RESET}  {DIM}│{RESET}  {GRAY}{r['thinking_count']}{RESET} {DIM}thinking ({think_pct:.0f}%){RESET}  {DIM}│{RESET}  {GRAY}{cache_pct}%{RESET} {DIM}cache{RESET}"
    if r["subagent_count"] > 0:
        session_stats += f"  {DIM}│{RESET}  {GRAY}{r['subagent_count']}{RESET} {DIM}agents{RESET}"
    lines.append(session_stats)

    # ── Mini event log ──
    if r["event_log"]:
        lines.append("")
        lines.append(f"  {BOLD}{DIM}LOG{RESET}")
        max_desc = w - 14  # 2 indent + 10 timestamp + 2 gap
        indent = " " * 14  # align continuation with description start
        for evt_ts, evt_desc in r["event_log"]:
            t = format_event_time(evt_ts) if evt_ts else "??:??:??"
            # Color events by type
            if evt_desc.startswith("error:"):
                evt_color = RED
            elif evt_desc.startswith("⚡"):
                evt_color = YELLOW
            elif evt_desc.startswith("$"):
                evt_color = CYAN
            elif "edit" in evt_desc or "write" in evt_desc:
                evt_color = GREEN
            elif evt_desc.startswith("grep:") or evt_desc.startswith("glob:"):
                evt_color = MAGENTA
            else:
                evt_color = GRAY
            wrapped = textwrap.wrap(evt_desc, width=max_desc, break_long_words=True, break_on_hyphens=False)
            if not wrapped:
                wrapped = [evt_desc]
            lines.append(f"  {DIM}{t}{RESET}  {evt_color}{wrapped[0]}{RESET}")
            for cont in wrapped[1:]:
                lines.append(f"  {indent}{evt_color}{cont}{RESET}")

    lines.append("")
    lines.append(f"  {sep_color}{'─' * w}{RESET}")
    lines.append(f"  {DIM}[s]{RESET} stats  {DIM}[d]{RESET} details  {DIM}[l]{RESET} log  {DIM}[e]{RESET} export  {DIM}[o]{RESET} sessions  {DIM}[?]{RESET} help  {DIM}[q]{RESET} quit")

    return lines


def render_help_overlay(term_width):
    """Render help overlay."""
    w = min(term_width - 4, 60)
    lines = []
    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {BOLD}  KEYBOARD SHORTCUTS{RESET}")
    lines.append(f"  {'─' * w}")
    lines.append("")
    shortcuts = [
        ("s", "Session stats — full cost breakdown, token sparkline, tool usage"),
        ("d", "Session details — detailed session view from session-manager"),
        ("l", "Event log — scrollable, f to filter, a for live auto-scroll"),
        ("e", "Export session — save session as markdown"),
        ("o", "List sessions — browse all recent sessions"),
        ("?", "Toggle this help overlay"),
        ("q", "Quit the monitor"),
    ]
    for key, desc in shortcuts:
        lines.append(f"    {BOLD}{CYAN}{key}{RESET}   {desc}")
    lines.append("")
    features = [
        "Live duration updates every second",
        "Activity indicator: ● ACTIVE / ● WORKING / ○ IDLE",
        "Green pulse on separator when new data arrives",
        "⚡ JUST COMPACTED alert after compaction events",
        "Cost burn rate ($/min) alongside per-turn cost",
        "Live tool trace — last 5 tool calls",
        "Last error message displayed inline",
        "Auto-follow — switches to new session when current ends",
        "Adapts to terminal width",
    ]
    lines.append(f"  {BOLD}  FEATURES{RESET}")
    lines.append(f"  {'─' * w}")
    lines.append("")
    for feat in features:
        lines.append(f"    {DIM}•{RESET} {feat}")
    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {DIM}Press any key to close{RESET}")
    return lines


FILTER_NAMES = ["all", "errors", "bash", "edits", "search", "compactions"]

FILTER_MATCHERS = {
    "all": lambda d: True,
    "errors": lambda d: d.startswith("error:"),
    "bash": lambda d: d.startswith("$"),
    "edits": lambda d: any(w in d for w in ("edit ", "write ")),
    "search": lambda d: any(d.startswith(p) for p in ("grep:", "glob:", "read ")),
    "compactions": lambda d: d.startswith("⚡"),
}


def _build_log_lines(raw_log, max_desc, filter_name="all"):
    """Build formatted display lines from raw event log."""
    indent = " " * 14
    matcher = FILTER_MATCHERS.get(filter_name, FILTER_MATCHERS["all"])
    lines = []
    event_count = 0
    for evt_ts, evt_desc in raw_log:
        if not matcher(evt_desc):
            continue
        event_count += 1
        t = format_event_time(evt_ts) if evt_ts else "??:??:??"
        if evt_desc.startswith("error:"):
            evt_color = RED
        elif evt_desc.startswith("⚡"):
            evt_color = YELLOW
        elif evt_desc.startswith("$"):
            evt_color = CYAN
        elif "edit" in evt_desc or "write" in evt_desc:
            evt_color = GREEN
        elif evt_desc.startswith("grep:") or evt_desc.startswith("glob:"):
            evt_color = MAGENTA
        else:
            evt_color = GRAY
        wrapped = textwrap.wrap(evt_desc, width=max_desc, break_long_words=True, break_on_hyphens=False)
        if not wrapped:
            wrapped = [evt_desc]
        lines.append(f"  {DIM}{t}{RESET}  {evt_color}{wrapped[0]}{RESET}")
        for cont in wrapped[1:]:
            lines.append(f"  {indent}{evt_color}{cont}{RESET}")
    return lines, event_count


def show_log_viewer(transcript_path, term_width):
    """Interactive log viewer with filtering and auto-scroll."""
    out = sys.stdout
    w = min(term_width - 4, 100)
    max_desc = w - 14

    filter_idx = 0  # index into FILTER_NAMES
    auto_follow = True
    last_mtime = 0
    raw_log = []
    log_lines = []
    event_count = 0
    total = 0
    scroll_pos = 0
    needs_rebuild = True
    needs_redraw = True

    while True:
        # Reload transcript if file changed or first run
        try:
            mtime = os.stat(transcript_path).st_mtime
        except FileNotFoundError:
            mtime = last_mtime
        if mtime != last_mtime:
            last_mtime = mtime
            r = parse_transcript(transcript_path)
            raw_log = r.get("full_log", [])
            needs_rebuild = True

        if needs_rebuild:
            filter_name = FILTER_NAMES[filter_idx]
            log_lines, event_count = _build_log_lines(raw_log, max_desc, filter_name)
            total = len(log_lines)
            term_h = shutil.get_terminal_size().lines
            page_size = max(1, term_h - 5)
            max_scroll = max(0, total - page_size)
            if auto_follow:
                scroll_pos = max_scroll
            else:
                scroll_pos = min(scroll_pos, max_scroll)
            needs_rebuild = False
            needs_redraw = True

        # Render only when needed
        if needs_redraw:
            visible = log_lines[scroll_pos:scroll_pos + page_size]
            filter_name = FILTER_NAMES[filter_idx]
            filter_label = f"  filter: {BOLD}{filter_name}{RESET}" if filter_name != "all" else ""
            follow_label = f"  {GREEN}● LIVE{RESET}" if auto_follow else ""
            header = f"  {BOLD}LOG{RESET}  {DIM}({event_count} events){RESET}{filter_label}{follow_label}"
            pos_info = f"{scroll_pos + 1}-{min(scroll_pos + page_size, total)}/{total}" if total > 0 else "0/0"
            footer = f"  {DIM}j/k ↑/↓  ^D/^U  ^F/^B  g/G  f filter  a live  q close{RESET}  {DIM}{pos_info}{RESET}"

            buf = CLEAR + header + "\n"
            buf += f"  {'─' * w}\n"
            buf += "\n".join(visible) + "\n"
            pad = page_size - len(visible)
            if pad > 0:
                buf += "\n" * pad
            buf += f"  {'─' * w}\n"
            buf += footer
            out.write(buf)
            out.flush()
            needs_redraw = False

        # Wait for key or auto-refresh (1s when following, blocking when not)
        timeout = 1.0 if auto_follow else 60.0
        deadline = time.time() + timeout
        got_key = False
        while time.time() < deadline:
            wait = max(0.01, deadline - time.time())
            if select.select([sys.stdin], [], [], wait)[0]:
                raw = os.read(sys.stdin.fileno(), 8).decode("utf-8", errors="ignore")
                if raw in ("q", "Q", "\x1b"):
                    return
                elif raw in ("\x1b[A", "k", "K"):  # up
                    scroll_pos = max(0, scroll_pos - 1)
                    auto_follow = False
                    got_key = True
                    break
                elif raw in ("\x1b[B", "j", "J"):  # down
                    scroll_pos = min(max(0, total - page_size), scroll_pos + 1)
                    if scroll_pos >= max(0, total - page_size):
                        auto_follow = True
                    got_key = True
                    break
                elif raw in ("\x1b[5~", "\x02"):  # page up / Ctrl+B
                    scroll_pos = max(0, scroll_pos - page_size)
                    auto_follow = False
                    got_key = True
                    break
                elif raw in ("\x1b[6~", "\x06"):  # page down / Ctrl+F
                    scroll_pos = min(max(0, total - page_size), scroll_pos + page_size)
                    if scroll_pos >= max(0, total - page_size):
                        auto_follow = True
                    got_key = True
                    break
                elif raw == "\x04":  # Ctrl+D — half page down
                    scroll_pos = min(max(0, total - page_size), scroll_pos + page_size // 2)
                    if scroll_pos >= max(0, total - page_size):
                        auto_follow = True
                    got_key = True
                    break
                elif raw == "\x15":  # Ctrl+U — half page up
                    scroll_pos = max(0, scroll_pos - page_size // 2)
                    auto_follow = False
                    got_key = True
                    break
                elif raw == "g":  # top
                    scroll_pos = 0
                    auto_follow = False
                    got_key = True
                    break
                elif raw == "G":  # bottom
                    scroll_pos = max(0, total - page_size)
                    auto_follow = True
                    got_key = True
                    break
                elif raw in ("f", "F"):  # cycle filter
                    filter_idx = (filter_idx + 1) % len(FILTER_NAMES)
                    needs_rebuild = True
                    got_key = True
                    break
                elif raw in ("a", "A"):  # toggle auto-follow
                    auto_follow = not auto_follow
                    if auto_follow:
                        scroll_pos = max(0, total - page_size)
                    got_key = True
                    break
            else:
                # No input — if auto-follow, check for file changes
                if auto_follow:
                    try:
                        new_mtime = os.stat(transcript_path).st_mtime
                    except FileNotFoundError:
                        new_mtime = last_mtime
                    if new_mtime != last_mtime:
                        needs_rebuild = True
                        break
        if got_key:
            needs_redraw = True
        elif not needs_rebuild:
            # Auto-follow timeout — check for new data
            if auto_follow:
                needs_rebuild = True


# ── Session management ──────────────────────────────────────────────

def list_sessions():
    """List recent sessions across all projects."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        print("No sessions found.")
        return

    sessions = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            mtime = jsonl.stat().st_mtime
            size = jsonl.stat().st_size
            sessions.append((jsonl, project_dir.name, jsonl.stem[:8], mtime, size))

    sessions.sort(key=lambda x: -x[3])

    print(f"\n  {BOLD}Recent Sessions{RESET}\n")
    print(f"  {'ID':<10} {'Project':<40} {'Size':>8}  {'Modified'}")
    print(f"  {'─' * 10} {'─' * 40} {'─' * 8}  {'─' * 20}")
    for path, project, sid, mtime, size in sessions[:15]:
        dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        size_str = format_tokens(size)
        proj_short = project.replace("-Users-", "~/").replace("-", "/")
        print(f"  {sid:<10} {proj_short:<40} {size_str:>8}  {dt}")

    print(f"\n  {DIM}Usage: python3 monitor.py <session-id>{RESET}\n")


def find_session_by_id(session_id):
    """Find transcript path by session ID prefix."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            if jsonl.stem.startswith(session_id):
                return str(jsonl)
    return None


# ── Input handling ──────────────────────────────────────────────────

VALID_KEYS = frozenset("qQsSdDlLeEoO?")


def get_key():
    """Non-blocking key read. Drains buffer, returns last meaningful key or None."""
    key = None
    fd = sys.stdin.fileno()
    while select.select([sys.stdin], [], [], 0)[0]:
        byte = os.read(fd, 1).decode("utf-8", errors="ignore")
        if byte in VALID_KEYS:
            key = byte
    return key


# ── External tool runner ────────────────────────────────────────────

def find_tool_script(name):
    """Find a sibling tool script relative to this monitor script."""
    monitor_dir = Path(__file__).resolve().parent
    repo_dir = monitor_dir.parent
    candidates = {
        "stats": repo_dir / "claude-code-session-stats" / "session-stats.py",
        "manager": repo_dir / "claude-code-session-manager" / "session-manager.py",
    }
    return str(candidates.get(name, ""))


def run_tool(script_path, args):
    """Run an external tool script, pausing the monitor."""
    # Leave alt screen so tool output goes to normal buffer
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _original_termios)
    sys.stdout.write(ALT_SCREEN_OFF + SHOW_CURSOR)
    sys.stdout.flush()
    cmd = [sys.executable, script_path] + args
    try:
        subprocess.run(cmd)
    except Exception as e:
        print(f"\n{RED}Error running tool: {e}{RESET}")
    print(f"\n{DIM}Press any key to return to monitor...{RESET}")
    # Switch to cbreak for the "any key" wait
    tty.setcbreak(sys.stdin.fileno())
    os.read(sys.stdin.fileno(), 1)
    # Re-enter alt screen for monitor
    sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
    sys.stdout.flush()


def export_session(path, session_id):
    """Export current session as markdown."""
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _original_termios)
    sys.stdout.write(ALT_SCREEN_OFF + SHOW_CURSOR)
    sys.stdout.flush()

    manager = find_tool_script("manager")
    if os.path.exists(manager):
        export_path = f"{session_id}-export.md"
        try:
            with open(export_path, "w") as f:
                subprocess.run([sys.executable, manager, "export", session_id], stdout=f)
            print(f"\n{GREEN}Exported to {export_path}{RESET}")
        except Exception as e:
            print(f"\n{RED}Export failed: {e}{RESET}")
    else:
        print(f"\n{YELLOW}session-manager not found — cannot export{RESET}")

    print(f"\n{DIM}Press any key to return to monitor...{RESET}")
    tty.setcbreak(sys.stdin.fileno())
    os.read(sys.stdin.fileno(), 1)
    sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
    sys.stdout.flush()


# ── Main loop ───────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_sessions()
        return

    # Find transcript
    if len(sys.argv) > 1:
        path = find_session_by_id(sys.argv[1])
        if not path:
            print(f"Session '{sys.argv[1]}' not found. Use --list to see sessions.")
            sys.exit(1)
    else:
        path = find_transcript()
        if not path:
            print("No active session found. Use --list or pass a session ID.")
            sys.exit(1)

    session_id = Path(path).stem[:8]

    last_mtime = 0
    frame = 0
    running = True
    cached_body = None
    needs_full_redraw = True
    show_help = False
    last_data_time = time.time()
    last_duration_sec = -1  # track when to redraw for live duration
    just_updated = False
    update_flash_until = 0  # timestamp until which the pulse is shown

    def handle_sigint(sig, frame_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGWINCH, lambda s, f: None)  # handle terminal resize

    global _original_termios
    old_settings = termios.tcgetattr(sys.stdin)
    _original_termios = old_settings
    out = sys.stdout

    try:
        tty.setcbreak(sys.stdin.fileno())
        out.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
        out.flush()

        while running:
            try:
                now = time.time()
                term_width = get_terminal_width()

                # Check for keypress
                key = get_key()
                if key:
                    if key in ("q", "Q"):
                        break
                    elif key == "?" and not show_help:
                        show_help = True
                        help_lines = render_help_overlay(term_width)
                        out.write(CLEAR + "\n".join(help_lines))
                        out.flush()
                        # Wait for any key to close help
                        while running:
                            k = get_key()
                            if k:
                                break
                            time.sleep(0.05)
                        show_help = False
                        needs_full_redraw = True
                        continue
                    elif key in ("s", "S"):
                        script = find_tool_script("stats")
                        if os.path.exists(script):
                            run_tool(script, [session_id])
                            needs_full_redraw = True
                            cached_body = None
                    elif key in ("d", "D"):
                        script = find_tool_script("manager")
                        if os.path.exists(script):
                            run_tool(script, ["show", session_id])
                            needs_full_redraw = True
                            cached_body = None
                    elif key in ("l", "L"):
                        if r and r.get("full_log"):
                            show_log_viewer(path, term_width)
                            needs_full_redraw = True
                    elif key in ("e", "E"):
                        export_session(path, session_id)
                        needs_full_redraw = True
                        cached_body = None
                    elif key in ("o", "O"):
                        script = find_tool_script("manager")
                        if os.path.exists(script):
                            run_tool(script, ["list"])
                            needs_full_redraw = True
                            cached_body = None

                # Re-parse transcript only when file changes
                try:
                    mtime = os.stat(path).st_mtime
                except FileNotFoundError:
                    # Session file gone — try auto-follow
                    new_path = find_latest_transcript()
                    if new_path and new_path != path:
                        path = new_path
                        session_id = Path(path).stem[:8]
                        cached_body = None
                        needs_full_redraw = True
                    time.sleep(1)
                    continue

                if mtime != last_mtime or cached_body is None:
                    last_mtime = mtime
                    last_data_time = now
                    update_flash_until = now + 0.5  # pulse for 500ms
                    try:
                        r = parse_transcript(path)
                        idle_secs = now - last_data_time
                        lines = render_dashboard(r, idle_secs, True, term_width)
                        cached_body = "\n".join(lines)
                        needs_full_redraw = True
                    except Exception as e:
                        if cached_body is None:
                            cached_body = f"  {RED}Error: {e}{RESET}"

                # Live duration + idle status update every second
                elapsed = now - last_data_time if r else 0
                current_sec = int(now)
                if current_sec != last_duration_sec:
                    last_duration_sec = current_sec
                    if r:
                        just_updated = now < update_flash_until
                        idle_secs = now - last_data_time
                        lines = render_dashboard(r, idle_secs, just_updated, term_width)
                        cached_body = "\n".join(lines)
                        needs_full_redraw = True

                # Auto-follow: if idle for >5 min, check for newer sessions
                if elapsed > 300 and current_sec % 10 == 0:
                    new_path = find_latest_transcript()
                    if new_path and new_path != path:
                        new_mtime = os.stat(new_path).st_mtime
                        if new_mtime > last_mtime:
                            path = new_path
                            session_id = Path(path).stem[:8]
                            last_mtime = 0
                            cached_body = None
                            needs_full_redraw = True

                # Matrix animates only when Claude is actively working
                is_active = bool(r and r.get("waiting_for_response"))

                if needs_full_redraw:
                    matrix_line = render_matrix_header(frame, min(term_width, 80), active=is_active)
                    out.write(CLEAR + matrix_line + "\n" + cached_body)
                    out.flush()
                    needs_full_redraw = False
                elif is_active:
                    # Animate matrix header at 100ms
                    matrix_line = render_matrix_header(frame, min(term_width, 80), active=True)
                    out.write(f"\033[1;1H{ERASE_LINE}{matrix_line}")
                    out.flush()

                if is_active:
                    frame += 1
                    time.sleep(0.1)
                else:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                break
            except Exception as e:
                out.write(SHOW_CURSOR)
                out.flush()
                print(f"\n{RED}Error: {e}{RESET}")
                time.sleep(5)
                needs_full_redraw = True
    finally:
        out.write(SHOW_CURSOR + ALT_SCREEN_OFF)
        out.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    print(f"\n{DIM}Monitor stopped.{RESET}")


if __name__ == "__main__":
    main()
