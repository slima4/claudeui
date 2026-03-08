# Claude Code Status Line — Context Window Monitor

A lightweight Python script that adds a real-time context window usage indicator to [Claude Code](https://docs.anthropic.com/en/docs/claude-code)'s status line.

```
 ai-toolbox | main | Opus 4.6 | ████████░░░░░░░░░░░░ 42% 65.5k/156.0k | $0.34 | 12m | 0x compact | 5 files | a1b2c3d4
```

## Features

- **Context usage** — progress bar with color coding (green → yellow → orange → red)
- **Session cost** — estimated USD cost based on model pricing (input, cache read, output tokens)
- **Session duration** — how long since the first message
- **Compact count** — how many times auto-compaction has fired
- **Working files** — number of unique files Claude has read or edited
- **Model name**, **git branch**, and **session ID**

## Why?

Claude Code has a 200k token context window but provides no visibility into how much of it you've used — until it's too late and auto-compaction kicks in. This status line helps you:

- Decide when to `/compact` or start a new session
- Track how much a session is costing you
- See how many compactions have happened (high count = consider a fresh session)
- Understand how many files are in play

## Requirements

- Python 3.8+
- Claude Code with `statusLine` support

No external dependencies — stdlib only.

## Installation

### Option 1: Clone the repository

```bash
git clone https://github.com/slima4/ai-toolbox.git
```

Add to your Claude Code settings (`.claude/settings.local.json` in your project, or `~/.claude/settings.json` globally):

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /absolute/path/to/ai-toolbox/claude-code-statusline/statusline.py"
  }
}
```

### Option 2: Copy the script

Download `statusline.py` anywhere you like and point the config at it:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/scripts/statusline.py"
  }
}
```

## How it works

Claude Code passes session metadata as JSON via stdin to status line commands. The script:

1. Reads session JSON (model, workspace, transcript path, session ID)
2. Parses the transcript file in two passes:
   - **Reverse pass**: finds the most recent `usage` block for current context size
   - **Forward pass**: accumulates total tokens for cost, counts compactions, and collects touched file paths
3. Calculates cost using per-model pricing (Opus, Sonnet, Haiku)
4. Renders everything into a single colored status line

## Supported models

| Model | Input | Cache Read | Output |
| ----- | ----- | ---------- | ------ |
| Claude Opus 4.6 | $15/M | $1.50/M | $75/M |
| Claude Sonnet 4.6 | $3/M | $0.30/M | $15/M |
| Claude Haiku 4.5 | $0.80/M | $0.08/M | $4/M |

Unknown models fall back to Sonnet pricing.

## Context bar color thresholds

| Usage | Color  | Meaning                        |
| ----- | ------ | ------------------------------ |
| < 50% | Green  | Plenty of room                 |
| < 75% | Yellow | Getting busy, plan accordingly |
| < 90% | Orange | Consider compacting soon       |
| ≥ 90% | Red    | Auto-compaction imminent       |

## Limitations

- Token count is an **estimate** based on the last API response's usage data — it may not perfectly reflect the internal context state
- After auto-compaction, the numbers may not reset immediately until the next API response
- Sub-agent token usage may not be fully captured
- Cost is estimated from transcript data — actual billing may differ slightly

## Credits

Inspired by the discussion in [anthropics/claude-code#516](https://github.com/anthropics/claude-code/issues/516) and the community solutions shared there.

## License

MIT
