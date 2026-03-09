#!/usr/bin/env bash
set -euo pipefail

# ── Claude UI Uninstaller ────────────────────────────────────────────

INSTALL_DIR="${INSTALL_DIR:-${CLAUDE_UI_HOME:-$HOME/.claude-ui}}"
CLAUDE_DIR="$HOME/.claude"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
COMMANDS_DIR="$CLAUDE_DIR/commands"
BIN_DIR="$HOME/.local/bin"

RED='\033[91m'
GREEN='\033[92m'
YELLOW='\033[93m'
CYAN='\033[96m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

echo ""
echo -e "${YELLOW}${BOLD}  Claude UI Uninstaller${RESET}"
echo ""

# Remove CLI wrappers
for cmd in claude-ui-monitor claude-stats claude-sessions claude-ui-mode claude-ui-uninstall; do
    if [ -f "$BIN_DIR/$cmd" ]; then
        rm "$BIN_DIR/$cmd"
        echo -e "  ${GREEN}✓${RESET} Removed $BIN_DIR/$cmd"
    fi
done

# Remove commands symlink
if [ -L "$COMMANDS_DIR/ui" ]; then
    rm "$COMMANDS_DIR/ui"
    echo -e "  ${GREEN}✓${RESET} Removed slash commands"
fi

# Clean settings.json
if [ -f "$SETTINGS_FILE" ]; then
    python3 << 'PYEOF'
import json
import os

settings_file = os.path.expanduser("~/.claude/settings.json")
install_dir = os.environ.get("INSTALL_DIR", os.path.expanduser("~/.claude-ui"))

# Match against all known install locations
match_paths = {install_dir, os.path.realpath(install_dir)}
# Also match brew paths and common install locations
for p in ["/opt/homebrew/opt/claudeui", "/opt/homebrew/Cellar/claudeui",
          "/usr/local/opt/claudeui", "/usr/local/Cellar/claudeui",
          os.path.expanduser("~/.claude-ui")]:
    match_paths.add(p)

with open(settings_file) as f:
    settings = json.load(f)

def is_our_command(cmd):
    return any(p in cmd for p in match_paths)

# Remove statusline if it points to our install
sl = settings.get("statusLine", {})
if is_our_command(sl.get("command", "")):
    del settings["statusLine"]
    print(f"  \033[92m✓\033[0m Removed statusline config")

# Remove our hooks
hooks = settings.get("hooks", {})
for event in list(hooks.keys()):
    hooks[event] = [
        rule for rule in hooks[event]
        if not any(
            is_our_command(h.get("command", ""))
            for h in rule.get("hooks", [])
        )
    ]
    if not hooks[event]:
        del hooks[event]

if hooks:
    settings["hooks"] = hooks
elif "hooks" in settings:
    del settings["hooks"]

print(f"  \033[92m✓\033[0m Cleaned hooks config")

with open(settings_file, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
PYEOF
fi

# Remove install directory (only if it's our clone, not a symlink to user's repo)
if [ -d "$INSTALL_DIR" ] && [ ! -L "$INSTALL_DIR" ]; then
    echo ""
    echo -e "  ${YELLOW}Remove $INSTALL_DIR? (y/N)${RESET}"
    read -r answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        rm -rf "$INSTALL_DIR"
        echo -e "  ${GREEN}✓${RESET} Removed $INSTALL_DIR"
    else
        echo -e "  ${DIM}Kept $INSTALL_DIR${RESET}"
    fi
elif [ -L "$INSTALL_DIR" ]; then
    rm "$INSTALL_DIR"
    echo -e "  ${GREEN}✓${RESET} Removed symlink $INSTALL_DIR"
fi

echo ""
echo -e "  ${GREEN}${BOLD}Uninstalled.${RESET} Restart Claude Code for changes to take effect."
echo ""
