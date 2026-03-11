#!/usr/bin/env python3
"""ClaudeTUI — unified CLI for Claude Code utilities."""
import os
import sys

VERSION = "0.3.0"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SUBCOMMANDS = {
    "monitor":   ("python", "claude-code-monitor/monitor.py",                  "Live session dashboard"),
    "stats":     ("python", "claude-code-session-stats/session-stats.py",       "Post-session analytics"),
    "sessions":  ("python", "claude-code-session-manager/session-manager.py",   "Browse, compare, export sessions"),
    "mode":      ("python", "claude-ui-mode.py",                                "Switch statusline mode (full/compact/custom)"),
    "setup":     ("bash",   "install.sh",                                       "Configure statusline, hooks, and commands"),
    "uninstall": ("bash",   "uninstall.sh",                                     "Remove ClaudeTUI configuration"),
}

HELP = """\
claudetui — CLI for ClaudeTUI (Claude Code utilities)

Usage:
  claudetui <command> [args...]

Commands:
  monitor     Live session dashboard (separate terminal)
  stats       Post-session analytics
  sessions    Browse, compare, resume, export sessions
  mode        Switch statusline mode (full/compact/custom)
  setup       Configure statusline, hooks, and commands
  uninstall   Remove ClaudeTUI configuration

Options:
  -h, --help     Show this help
  -v, --version  Show version

Examples:
  claudetui monitor              # live dashboard
  claudetui stats --days 7 -s    # weekly summary
  claudetui sessions list        # browse sessions
  claudetui mode compact         # switch to 1-line statusline
  claudetui mode custom          # interactive configurator\
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(HELP)
        sys.exit(0)

    if sys.argv[1] in ("-v", "--version"):
        print(f"claudetui {VERSION}")
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd not in SUBCOMMANDS:
        print(f"claudetui: unknown command '{cmd}'\n", file=sys.stderr)
        print(HELP, file=sys.stderr)
        sys.exit(2)

    kind, target, _ = SUBCOMMANDS[cmd]
    target_path = os.path.join(SCRIPT_DIR, target)

    if not os.path.exists(target_path):
        print(f"claudetui: {target} not found at {target_path}", file=sys.stderr)
        print("Run 'claudetui setup' to install.", file=sys.stderr)
        sys.exit(1)

    if kind == "bash":
        os.environ["INSTALL_DIR"] = SCRIPT_DIR
        os.execvp("bash", ["bash", target_path] + args)
    else:
        os.execvp("python3", ["python3", target_path] + args)


if __name__ == "__main__":
    main()
