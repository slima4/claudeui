#!/usr/bin/env python3
"""ClaudeTUI — statusline mode switcher and component configurator."""
import curses
import json
import os
import sys


SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".claude", "claudeui.json")

# ANSI colors
GREEN = "\033[92m"
RED = "\033[31m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


# ── Help text ──────────────────────────────────────────────────────


MAIN_HELP = f"""\
{BOLD}claudetui mode{RESET} — statusline mode switcher for ClaudeTUI

{BOLD}Usage:{RESET}
  claudetui mode {CYAN}<command>{RESET} [options]

{BOLD}Commands:{RESET}
  {CYAN}full{RESET}           Switch to 3-line statusline (all metrics)
  {CYAN}compact{RESET}        Switch to 1-line statusline (essentials)
  {CYAN}custom{RESET}         Configure which components to show

{BOLD}Options:{RESET}
  {CYAN}-h{RESET}, {CYAN}--help{RESET}     Show this help message

{BOLD}Examples:{RESET}
  claudetui mode                  {DIM}# show current mode{RESET}
  claudetui mode full             {DIM}# 3-line with everything{RESET}
  claudetui mode compact          {DIM}# 1-line essentials{RESET}
  claudetui mode custom           {DIM}# interactive configurator{RESET}
  claudetui mode custom -l        {DIM}# show what's hidden{RESET}

{DIM}Config: ~/.claude/claudeui.json{RESET}
{DIM}Docs:   https://github.com/slima4/claude-tui{RESET}
"""

CUSTOM_HELP = f"""\
{BOLD}claudetui mode custom{RESET} — configure statusline components

{BOLD}Usage:{RESET}
  claudetui mode custom [options]

  Run without options to open the interactive configurator.
  Use arrow keys to navigate, space to toggle, s to save.

{BOLD}Options:{RESET}
  {CYAN}-l{RESET}, {CYAN}--list{RESET}               Show current configuration
  {CYAN}-w{RESET}, {CYAN}--widget{RESET} {DIM}<name>{RESET}       Set widget (matrix, hex, bars, progress, none)
  {CYAN}-b{RESET}, {CYAN}--buffer{RESET} {DIM}<chars>{RESET}      Right edge buffer in chars (0-60, default: 30)
  {CYAN}-p{RESET}, {CYAN}--preset{RESET} {DIM}<name>{RESET}       Apply preset (all, minimal, focused)
      {CYAN}--hide{RESET} {DIM}<components>{RESET}   Hide components (comma-separated)
      {CYAN}--show{RESET} {DIM}<components>{RESET}   Show components (comma-separated)
  {CYAN}-h{RESET}, {CYAN}--help{RESET}               Show this help message

{BOLD}Components:{RESET}
  {DIM}Line 1:{RESET} model, context_bar, token_count, compact_prediction,
          sparkline, cost, duration, compact_count, session_id
  {DIM}Line 2:{RESET} cwd, git_branch, turns, files, errors, cache,
          thinking, cost_per_turn, agents
  {DIM}Line 3:{RESET} tool_trace, file_edits

{BOLD}Presets:{RESET}
  {CYAN}all{RESET}        Show all components
  {CYAN}minimal{RESET}    Only essentials (context bar, duration, turns, errors)
  {CYAN}focused{RESET}    Hide noise (model, tokens, cost, session ID, cwd, agents)

{BOLD}Examples:{RESET}
  claudetui mode custom                         {DIM}# interactive TUI{RESET}
  claudetui mode custom -l                      {DIM}# list hidden components{RESET}
  claudetui mode custom -p focused              {DIM}# apply focused preset{RESET}
  claudetui mode custom -w hex                  {DIM}# switch to hex widget{RESET}
  claudetui mode custom --hide model,cost       {DIM}# hide model and cost{RESET}
  claudetui mode custom --show model            {DIM}# show model again{RESET}
  claudetui mode custom -b 20                   {DIM}# set buffer to 20 chars{RESET}
  claudetui mode custom -p all -w none          {DIM}# reset all, no widget{RESET}
"""


# ── Settings (full/compact mode) ──────────────────────────────────


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_settings(settings):
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS_PATH)


def show_current():
    settings = load_settings()
    cmd = settings.get("statusLine", {}).get("command", "")
    if not cmd:
        print(f"  {RED}\u2717{RESET} No statusline configured. Run claudetui setup first.")
        return
    mode = "compact" if "--compact" in cmd else "full"
    print(f"  Current mode: {BOLD}{CYAN}{mode}{RESET}")
    print()
    print(f"  {DIM}Run claudetui mode --help for usage info{RESET}")


def set_mode(mode):
    settings = load_settings()
    current_cmd = settings.get("statusLine", {}).get("command", "")
    if not current_cmd:
        print(f"  {RED}\u2717{RESET} No statusline configured. Run claudetui setup first.")
        sys.exit(1)

    base_cmd = current_cmd.replace(" --compact", "").strip()
    cmd = f"{base_cmd} --compact" if mode == "compact" else base_cmd

    settings["statusLine"] = {"type": "command", "command": cmd}
    save_settings(settings)
    print(f"  {GREEN}\u2713{RESET} Statusline mode: {BOLD}{CYAN}{mode}{RESET}")
    print(f"  {DIM}Restart Claude Code for changes to take effect.{RESET}")


# ── Config (custom components) ─────────────────────────────────────


# (id, line, name, preview_text, color_pair_index)
# color pairs: 1=green, 2=red, 3=cyan, 4=yellow, 5=magenta, 0=white/default
COMPONENTS = [
    ("model",              "line1", "Model",           "Opus 4.6",                          5),
    ("context_bar",        "line1", "Context bar",     "\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591 42%", 1),
    ("token_count",        "line1", "Token count",     "65.5k/200.0k",                      3),
    ("compact_prediction", "line1", "Compact predict", "~24 turns left",                    3),
    ("sparkline",          "line1", "Sparkline",       "sparkline",                          3),
    ("cost",               "line1", "Cost",            "$2.34",                              4),
    ("duration",           "line1", "Duration",        "12m",                                0),
    ("compact_count",      "line1", "Compact count",   "0x compact",                         3),
    ("session_id",         "line1", "Session ID",      "#a1b2c3d4",                          0),
    ("cwd",                "line2", "Directory",       os.path.basename(os.getcwd()),         0),
    ("git_branch",         "line2", "Git branch",      "main +42 -17",                       3),
    ("turns",              "line2", "Turns",           "18 turns",                            3),
    ("files",              "line2", "Files",           "5 files",                             3),
    ("errors",             "line2", "Errors",          "0 err",                               1),
    ("cache",              "line2", "Cache %",         "82% cache",                           3),
    ("thinking",           "line2", "Thinking",        "4x think",                            5),
    ("cost_per_turn",      "line2", "Cost/turn",       "~$0.13/turn",                         4),
    ("agents",             "line2", "Agents",          "2 agents",                            3),
    ("tool_trace",         "line3", "Tool trace",      "read \u2192 edit \u2192 bash \u2192 edit", 0),
    ("file_edits",         "line3", "File edits",      "statusline.py\u00d73 README.md\u00d71",   0),
]

COMPONENT_IDS = {c[0] for c in COMPONENTS}

# Sparkline preview: (char, color_pair) matching statusline.py colors
# <0.25 green(1), <0.50 cyan(3), <0.75 yellow(4), >=0.75 red(2)
SPARKLINE_PREVIEW = [
    ("\u2584", 3), ("\u2581", 1), ("\u2586", 4), ("\u2583", 3),
    ("\u2581", 1), ("\u2583", 1), ("\u2581", 1), ("\u2585", 3),
    ("\u2588", 2), ("\u2581", 1), ("\u2581", 1), ("\u2581", 1),
    ("\u2585", 3), ("\u2583", 3), ("\u2585", 3), ("\u2582", 1),
    ("\u2581", 1), ("\u2582", 1), ("\u2583", 1), ("\u2581", 1),
]

WIDGETS = ["matrix", "hex", "bars", "progress", "none"]

PRESETS = {
    "all": {},
    "minimal": {
        "line1": {"model": False, "token_count": False, "cost": False,
                  "compact_prediction": False, "compact_count": False,
                  "session_id": False},
        "line2": {"cwd": False, "git_branch": False, "cache": False,
                  "cost_per_turn": False, "agents": False, "thinking": False,
                  "files": False},
        "line3": {"file_edits": False},
    },
    "focused": {
        "line1": {"model": False, "token_count": False, "cost": False,
                  "session_id": False},
        "line2": {"cwd": False, "cost_per_turn": False, "agents": False},
    },
}


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


def get_toggle(custom, comp_id, line):
    return custom.get(line, {}).get(comp_id, True)


def set_toggle(custom, comp_id, line, value):
    if line not in custom:
        custom[line] = {}
    custom[line][comp_id] = value


def get_widget(custom):
    w = custom.get("widget", os.environ.get("STATUSLINE_WIDGET", "matrix"))
    return w if w in WIDGETS else "matrix"


def apply_preset(custom, preset_name):
    preset = PRESETS.get(preset_name, {})
    for comp_id, line, _, _, _ in COMPONENTS:
        set_toggle(custom, comp_id, line, True)
    for line, overrides in preset.items():
        for comp_id, value in overrides.items():
            set_toggle(custom, comp_id, line, value)


def find_component(name):
    """Find a component by ID. Returns (comp_id, line) or None."""
    for comp_id, line, _, _, _ in COMPONENTS:
        if comp_id == name:
            return comp_id, line
    return None


# ── Curses TUI ─────────────────────────────────────────────────────


def build_menu():
    items = []
    current_line = None
    line_labels = {
        "line1": "Line 1 \u2014 Session Core",
        "line2": "Line 2 \u2014 Project Telemetry",
        "line3": "Line 3 \u2014 Activity Trace",
    }
    for i, (comp_id, line, name, preview, color) in enumerate(COMPONENTS):
        if line != current_line:
            items.append({"type": "header", "label": line_labels[line]})
            current_line = line
        items.append({
            "type": "component", "index": i,
            "comp_id": comp_id, "line": line,
            "name": name, "preview": preview, "color": color,
        })
    items.append({"type": "header", "label": ""})
    items.append({"type": "widget", "label": "Widget"})
    items.append({"type": "preset", "label": "Preset"})
    items.append({"type": "buffer", "label": "Buffer"})
    items.append({"type": "header", "label": ""})
    items.append({"type": "save", "label": "Save & exit"})
    return items


def interactive_curses(custom):
    menu = build_menu()
    selectable = [i for i, m in enumerate(menu) if m["type"] != "header"]
    cursor_idx = 0
    preset_names = list(PRESETS.keys()) + ["custom"]
    preset_idx = len(preset_names) - 1  # start on "custom"

    def draw(stdscr):
        nonlocal cursor_idx, preset_idx
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        # Pair 6: vivid green for logo — use 256-color 46 if available
        if curses.COLORS >= 256:
            curses.init_pair(6, 46, -1)
        else:
            curses.init_pair(6, curses.COLOR_GREEN, -1)

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            widget = get_widget(custom)

            # ASCII art title — "Claude" white, "UI" green
            logo_claude = [
                " ██████╗ ██╗      █████╗ ██╗   ██╗██████╗ ███████╗",
                "██╔════╝ ██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝",
                "██║      ██║     ███████║██║   ██║██║  ██║█████╗  ",
                "██║      ██║     ██╔══██║██║   ██║██║  ██║██╔══╝  ",
                "╚██████╗ ███████╗██║  ██║╚██████╔╝██████╔╝███████╗",
                " ╚═════╝ ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝",
            ]
            logo_ui = [
                "████████╗██╗   ██╗██╗",
                "╚══██╔══╝██║   ██║██║",
                "   ██║   ██║   ██║██║",
                "   ██║   ██║   ██║██║",
                "   ██║   ╚██████╔╝██║",
                "   ╚═╝    ╚═════╝ ╚═╝",
            ]
            x = 2
            try:
                for i, (cl, ui) in enumerate(zip(logo_claude, logo_ui)):
                    stdscr.addstr(1 + i, x, cl, curses.A_BOLD)
                    stdscr.addstr(1 + i, x + len(cl), ui,
                                  curses.color_pair(6) | curses.A_BOLD)
            except curses.error:
                pass

            subtitle = "Statusline Configurator"
            try:
                stdscr.addstr(8, x + 2, subtitle, curses.A_DIM)
            except curses.error:
                pass

            # Hints
            row = 10
            try:
                stdscr.addstr(row, x, "\u2191\u2193", curses.color_pair(3))
                stdscr.addstr(" navigate  ")
                stdscr.addstr("Space", curses.color_pair(3))
                stdscr.addstr(" toggle  ")
                stdscr.addstr("\u2190\u2192", curses.color_pair(3))
                stdscr.addstr(" widget/preset  ")
                stdscr.addstr("s", curses.color_pair(3))
                stdscr.addstr(" save  ")
                stdscr.addstr("q", curses.color_pair(3))
                stdscr.addstr(" quit")
            except curses.error:
                pass

            row = 12
            current_sel = selectable[cursor_idx]

            for idx, item in enumerate(menu):
                if row >= h - 1:
                    break

                is_selected = (idx == current_sel)

                if item["type"] == "header":
                    if item["label"]:
                        try:
                            stdscr.addstr(row, x, item["label"], curses.A_BOLD)
                        except curses.error:
                            pass
                    row += 1
                    continue

                if item["type"] == "component":
                    enabled = get_toggle(custom, item["comp_id"], item["line"])
                    mark = "\u2713" if enabled else "\u2717"
                    mark_color = curses.color_pair(1) if enabled else curses.color_pair(2)
                    preview_attr = (curses.color_pair(item["color"]) | curses.A_BOLD
                                    if enabled else curses.A_DIM)

                    try:
                        if is_selected:
                            stdscr.addstr(row, x, "\u25b8 ",
                                          curses.color_pair(3) | curses.A_BOLD)
                        else:
                            stdscr.addstr(row, x, "  ")
                        stdscr.addstr(mark + " ", mark_color | curses.A_BOLD)
                        stdscr.addstr(f"{item['name']:<18s}",
                                      curses.A_BOLD if is_selected else 0)
                        if item["comp_id"] == "sparkline":
                            for ch, cp in SPARKLINE_PREVIEW:
                                attr = (curses.color_pair(cp) | curses.A_BOLD
                                        if enabled else curses.A_DIM)
                                stdscr.addstr(ch, attr)
                        else:
                            stdscr.addstr(item["preview"], preview_attr)
                    except curses.error:
                        pass
                    row += 1

                elif item["type"] == "widget":
                    try:
                        if is_selected:
                            stdscr.addstr(row, x, "\u25b8 ",
                                          curses.color_pair(3) | curses.A_BOLD)
                        else:
                            stdscr.addstr(row, x, "  ")
                        stdscr.addstr("Widget: ",
                                      curses.A_BOLD if is_selected else 0)
                        stdscr.addstr("\u25c2 ",
                                      curses.color_pair(3) | curses.A_BOLD)
                        for j, wn in enumerate(WIDGETS):
                            if j > 0:
                                stdscr.addstr("  ", curses.A_DIM)
                            if wn == widget:
                                stdscr.addstr(f"[{wn}]",
                                              curses.color_pair(3) | curses.A_BOLD)
                            else:
                                stdscr.addstr(wn, curses.A_DIM)
                        stdscr.addstr(" \u25b8",
                                      curses.color_pair(3) | curses.A_BOLD)
                    except curses.error:
                        pass
                    row += 1

                elif item["type"] == "preset":
                    try:
                        if is_selected:
                            stdscr.addstr(row, x, "\u25b8 ",
                                          curses.color_pair(3) | curses.A_BOLD)
                        else:
                            stdscr.addstr(row, x, "  ")
                        stdscr.addstr("Preset: ",
                                      curses.A_BOLD if is_selected else 0)
                        stdscr.addstr("\u25c2 ",
                                      curses.color_pair(3) | curses.A_BOLD)
                        for j, name in enumerate(preset_names):
                            if j > 0:
                                stdscr.addstr("  ", curses.A_DIM)
                            if j == preset_idx:
                                stdscr.addstr(f"[{name}]",
                                              curses.color_pair(3) | curses.A_BOLD)
                            else:
                                stdscr.addstr(name, curses.A_DIM)
                        stdscr.addstr(" \u25b8",
                                      curses.color_pair(3) | curses.A_BOLD)
                    except curses.error:
                        pass
                    row += 1

                elif item["type"] == "buffer":
                    buf_val = custom.get("buffer", 30)
                    try:
                        if is_selected:
                            stdscr.addstr(row, x, "\u25b8 ",
                                          curses.color_pair(3) | curses.A_BOLD)
                        else:
                            stdscr.addstr(row, x, "  ")
                        stdscr.addstr("Buffer: ",
                                      curses.A_BOLD if is_selected else 0)
                        stdscr.addstr("\u25c2 ",
                                      curses.color_pair(3) | curses.A_BOLD)
                        stdscr.addstr(f"{buf_val}",
                                      curses.color_pair(3) | curses.A_BOLD)
                        stdscr.addstr(" \u25b8",
                                      curses.color_pair(3) | curses.A_BOLD)
                        stdscr.addstr(f"  chars from edge",
                                      curses.A_DIM)
                    except curses.error:
                        pass
                    row += 1

                elif item["type"] == "save":
                    try:
                        if is_selected:
                            stdscr.addstr(row, x, "\u25b8 ",
                                          curses.color_pair(1) | curses.A_BOLD)
                        else:
                            stdscr.addstr(row, x, "  ")
                        stdscr.addstr("Save & exit",
                                      curses.color_pair(1) | curses.A_BOLD
                                      if is_selected else curses.color_pair(1))
                    except curses.error:
                        pass
                    row += 1

            stdscr.refresh()
            key = stdscr.getch()

            if key == curses.KEY_UP or key == ord("k"):
                if cursor_idx > 0:
                    cursor_idx -= 1
            elif key == curses.KEY_DOWN or key == ord("j"):
                if cursor_idx < len(selectable) - 1:
                    cursor_idx += 1
            elif key == ord(" "):
                item = menu[selectable[cursor_idx]]
                if item["type"] == "component":
                    cid, ln = item["comp_id"], item["line"]
                    set_toggle(custom, cid, ln, not get_toggle(custom, cid, ln))
                    preset_idx = len(preset_names) - 1  # → custom
                elif item["type"] == "widget":
                    wi = WIDGETS.index(widget)
                    custom["widget"] = WIDGETS[(wi + 1) % len(WIDGETS)]
                elif item["type"] == "preset":
                    preset_idx = (preset_idx + 1) % len(preset_names)
                    if preset_names[preset_idx] != "custom":
                        apply_preset(custom, preset_names[preset_idx])
                elif item["type"] == "buffer":
                    custom["buffer"] = min(60, custom.get("buffer", 30) + 5)
            elif key == curses.KEY_LEFT or key == curses.KEY_RIGHT:
                item = menu[selectable[cursor_idx]]
                direction = 1 if key == curses.KEY_RIGHT else -1
                if item["type"] == "widget":
                    wi = WIDGETS.index(widget)
                    custom["widget"] = WIDGETS[(wi + direction) % len(WIDGETS)]
                elif item["type"] == "preset":
                    preset_idx = (preset_idx + direction) % len(preset_names)
                    if preset_names[preset_idx] != "custom":
                        apply_preset(custom, preset_names[preset_idx])
                elif item["type"] == "buffer":
                    buf = custom.get("buffer", 30)
                    custom["buffer"] = max(0, min(60, buf + direction * 5))
            elif key == ord("\n"):
                item = menu[selectable[cursor_idx]]
                if item["type"] == "component":
                    cid, ln = item["comp_id"], item["line"]
                    set_toggle(custom, cid, ln, not get_toggle(custom, cid, ln))
                    preset_idx = len(preset_names) - 1  # → custom
                elif item["type"] == "widget":
                    wi = WIDGETS.index(widget)
                    custom["widget"] = WIDGETS[(wi + 1) % len(WIDGETS)]
                elif item["type"] == "preset":
                    preset_idx = (preset_idx + 1) % len(preset_names)
                    if preset_names[preset_idx] != "custom":
                        apply_preset(custom, preset_names[preset_idx])
                elif item["type"] == "buffer":
                    custom["buffer"] = min(60, custom.get("buffer", 30) + 5)
                elif item["type"] == "save":
                    return True
            elif key == ord("s"):
                return True
            elif key == ord("q") or key == 27:
                return False
            elif key == ord("1"):
                preset_idx = 0
                apply_preset(custom, preset_names[0])
            elif key == ord("2"):
                preset_idx = 1
                apply_preset(custom, preset_names[1])
            elif key == ord("3"):
                preset_idx = 2
                apply_preset(custom, preset_names[2])

    return curses.wrapper(draw)


# ── CLI ────────────────────────────────────────────────────────────


def print_current(custom):
    """Print current custom configuration."""
    widget = get_widget(custom)
    buf_val = custom.get("buffer", 30)
    print(f"  {BOLD}Widget:{RESET} {CYAN}{widget}{RESET}")
    print(f"  {BOLD}Buffer:{RESET} {CYAN}{buf_val}{RESET} chars from edge")

    line_labels = {"line1": "Line 1", "line2": "Line 2", "line3": "Line 3"}

    for line_key in ("line1", "line2", "line3"):
        hidden = [
            name for cid, ln, name, _, _ in COMPONENTS
            if ln == line_key and not get_toggle(custom, cid, ln)
        ]
        if hidden:
            print(f"  {BOLD}{line_labels[line_key]}:{RESET}")
            for name in hidden:
                print(f"    {RED}\u2717{RESET} {name}")

    all_visible = all(
        get_toggle(custom, cid, ln) for cid, ln, _, _, _ in COMPONENTS
    )
    if all_visible:
        print(f"  {GREEN}\u2713{RESET} All components visible")
    print()


def parse_component_list(value):
    """Parse comma-separated component list, validating each name."""
    names = [n.strip() for n in value.split(",") if n.strip()]
    invalid = [n for n in names if n not in COMPONENT_IDS]
    if invalid:
        print(f"  {RED}\u2717{RESET} Unknown component(s): {', '.join(invalid)}")
        print()
        print(f"  {DIM}Available components:{RESET}")
        for comp_id, _, name, _, _ in COMPONENTS:
            print(f"    {CYAN}{comp_id:<22s}{RESET} {DIM}{name}{RESET}")
        print()
        sys.exit(1)
    return names


def cmd_custom(args):
    """Handle the 'custom' subcommand."""
    cfg = load_config()
    custom = cfg.get("custom", {})

    if not args:
        # Interactive mode
        should_save = interactive_curses(custom)
        if should_save:
            cfg["custom"] = custom
            save_config(cfg)
            print()
            print(f"  {GREEN}\u2713{RESET} Configuration saved to {DIM}{CONFIG_PATH}{RESET}")
            print(f"  {DIM}Changes apply on next statusline refresh{RESET}")

            hidden = [
                name for comp_id, line, name, _, _ in COMPONENTS
                if not get_toggle(custom, comp_id, line)
            ]
            if hidden:
                print(f"  {DIM}Hidden: {', '.join(hidden)}{RESET}")
            else:
                print(f"  {DIM}All components visible{RESET}")
            print(f"  {DIM}Widget: {get_widget(custom)}{RESET}")
            print()
        else:
            print()
            print(f"  {DIM}No changes saved{RESET}")
            print()
        return

    # Parse CLI flags
    modified = False
    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ("-h", "--help"):
            print(CUSTOM_HELP)
            return

        elif arg in ("-l", "--list"):
            print_current(custom)
            i += 1
            continue

        elif arg in ("-b", "--buffer"):
            if i + 1 >= len(args):
                print(f"  {RED}\u2717{RESET} --buffer requires a value (0-60)")
                sys.exit(1)
            i += 1
            try:
                val = int(args[i])
            except ValueError:
                print(f"  {RED}\u2717{RESET} --buffer must be a number (0-60)")
                sys.exit(1)
            if val < 0 or val > 60:
                print(f"  {RED}\u2717{RESET} --buffer must be between 0 and 60")
                sys.exit(1)
            custom["buffer"] = val
            modified = True

        elif arg in ("-w", "--widget"):
            if i + 1 >= len(args):
                print(f"  {RED}\u2717{RESET} --widget requires a value")
                print(f"  {DIM}Available: {', '.join(WIDGETS)}{RESET}")
                sys.exit(1)
            i += 1
            w = args[i]
            if w not in WIDGETS:
                print(f"  {RED}\u2717{RESET} Unknown widget: {w}")
                print(f"  {DIM}Available: {', '.join(WIDGETS)}{RESET}")
                sys.exit(1)
            custom["widget"] = w
            modified = True

        elif arg in ("-p", "--preset"):
            if i + 1 >= len(args):
                print(f"  {RED}\u2717{RESET} --preset requires a value")
                print(f"  {DIM}Available: {', '.join(PRESETS.keys())}{RESET}")
                sys.exit(1)
            i += 1
            p = args[i]
            if p not in PRESETS:
                print(f"  {RED}\u2717{RESET} Unknown preset: {p}")
                print(f"  {DIM}Available: {', '.join(PRESETS.keys())}{RESET}")
                sys.exit(1)
            apply_preset(custom, p)
            modified = True

        elif arg == "--hide":
            if i + 1 >= len(args):
                print(f"  {RED}\u2717{RESET} --hide requires component names")
                sys.exit(1)
            i += 1
            for name in parse_component_list(args[i]):
                found = find_component(name)
                if found:
                    set_toggle(custom, found[0], found[1], False)
            modified = True

        elif arg == "--show":
            if i + 1 >= len(args):
                print(f"  {RED}\u2717{RESET} --show requires component names")
                sys.exit(1)
            i += 1
            for name in parse_component_list(args[i]):
                found = find_component(name)
                if found:
                    set_toggle(custom, found[0], found[1], True)
            modified = True

        else:
            print(f"  {RED}\u2717{RESET} Unknown option: {arg}")
            print(f"  {DIM}Run claudetui mode custom --help for usage{RESET}")
            sys.exit(1)

        i += 1

    if modified:
        cfg["custom"] = custom
        save_config(cfg)
        print(f"  {GREEN}\u2713{RESET} Configuration saved")
        print(f"  {DIM}Changes apply on next statusline refresh{RESET}")


# ── Main ───────────────────────────────────────────────────────────


def main():
    args = sys.argv[1:]

    if not args:
        show_current()
        return

    cmd = args[0]

    if cmd in ("-h", "--help"):
        print(MAIN_HELP)
    elif cmd == "full":
        set_mode("full")
    elif cmd == "compact":
        set_mode("compact")
    elif cmd == "custom":
        cmd_custom(args[1:])
    else:
        print(f"  {RED}\u2717{RESET} Unknown command: {cmd}")
        print()
        print(f"  {DIM}Run claudetui mode --help for usage{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
