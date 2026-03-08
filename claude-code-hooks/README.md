# Claude Code Hooks — Integrated Development Intelligence

A set of [Claude Code hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) that provide real-time context about file activity, dependencies, and code churn — automatically, inside Claude Code.

## Hooks

### `session-heatmap.py` — SessionStart

When a session starts, shows the top hotspot files for the current project based on recent session activity.

```
📊 File hotspots (last 14 days, 8 sessions):
  ███ src/config.ts (43e/12r)
  ▆▆▆ src/pages/dashboard/index.vue (39e/8r)
  ▅▅▅ src/locales/en.json (21e/15r)
  ▄▄▄ src/utils/constants.ts (19e/5r)
  ▃▃▃ .github/workflows/ci.yml (11e/7r)
```

Helps you immediately see what's been actively worked on and where hotspots are.

### `post-edit-deps.py` — PostToolUse (Edit|Write)

After Claude edits or creates a file, scans the project for files that import or reference it.

```
⚠️ 10+ file(s) depend on validation.ts:
  → app/composables/useAuth.ts
  → app/composables/useNotifications.ts
  → app/components/ui/ChangePasswordForm.vue
  → app/pages/reset-password.vue
  ... (truncated)
Consider checking these files for compatibility.
```

Helps catch breaking changes before they cause issues downstream.

### `pre-edit-churn.py` — PreToolUse (Edit|Write)

Before Claude edits a file, checks if it's been frequently modified across multiple sessions. Warns about high-churn files.

```
🔥 High churn: nuxt.config.ts has been edited 77 times across 15 sessions
in the last 14 days. Consider if this file needs refactoring rather than
more patches.
```

Signals when a file might need a different approach (refactoring, splitting) instead of more incremental edits.

## Installation

### Option 1: Add to global settings

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/claude-code-hooks/session-heatmap.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/claude-code-hooks/pre-edit-churn.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/claude-code-hooks/post-edit-deps.py"
          }
        ]
      }
    ]
  }
}
```

### Option 2: Add to project settings

Add to `.claude/settings.local.json` in your project to enable hooks only for that project.

## How it works

- **Heatmap**: Parses Claude Code transcript JSONL files from `~/.claude/projects/` to count file reads/edits across sessions. Scores files with `edits × 3 + reads × 1`.
- **Dependency check**: Walks the project source tree and searches for import/reference patterns matching the edited file name. Scans up to 2000 files, returns up to 10 dependents.
- **Churn detection**: Counts how many sessions and total edits a file has accumulated over the last 14 days. Warns when a file crosses the threshold (3+ sessions or 10+ total edits).

All hooks output to stdout, which Claude Code injects into the conversation context. They never block operations (always exit 0).

## Configuration

Thresholds can be adjusted by editing the constants at the top of each script:

| Script | Constant | Default | Description |
| ------ | -------- | ------- | ----------- |
| `session-heatmap.py` | `DAYS_LOOKBACK` | 14 | How many days of history to analyze |
| `pre-edit-churn.py` | `CHURN_THRESHOLD_SESSIONS` | 3 | Warn if edited in N+ sessions |
| `pre-edit-churn.py` | `CHURN_THRESHOLD_EDITS` | 10 | Warn if edited N+ times total |
| `post-edit-deps.py` | `MAX_DEPENDENTS` | 10 | Max dependents to show |
| `post-edit-deps.py` | `MAX_SCAN_FILES` | 2000 | Max files to scan |

## Requirements

- Python 3.8+
- No external dependencies

## License

MIT
