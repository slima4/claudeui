# Claude Code Status Line — Context Window Monitor

A lightweight Python script that adds a real-time context window usage indicator to [Claude Code](https://docs.anthropic.com/en/docs/claude-code)'s status line.

```
 Opus 4.6 │ ████████░░░░░░░░░░░░ 42% 65.5k/200.0k │ ▁▂▃▅▆▇█▃▄▅ │ $2.34 │ 12m │ 0x compact │ #a1b2c3d4
 ai-toolbox │ main +42 -17 │ t:18 │ f:5 │ err:0 │ cache:82% │ think:4 │ ~$0.13/turn │ agents:1
 r»statusline.py>e»statusline.py>b»python3>e»README.md │ statusline.py×3 README.md×1
```

Three-line layout:

- **Line 1** — session core: model, context bar, sparkline, cost, duration, compactions, session ID
- **Line 2** — project telemetry: directory, git branch + diff, turns, files, errors, cache ratio, thinking count, cost/turn, agents
- **Line 3** — live activity trace: recent tool calls (`r»read`, `e»edit`, `b»bash`) and file edit counts (shown only during active turns)

## Features

- **Context usage** — progress bar with color coding (green → yellow → orange → red)
- **Context sparkline** — visual history of context growth; compactions show as cliffs (`█▁▂▃`)
- **Session cost** — estimated USD cost based on model pricing (input, cache read, output tokens)
- **Cost per turn** — average cost per conversation turn
- **Session duration** — how long since the first message
- **Compact count** — how many times auto-compaction has fired
- **Turn count** — number of user messages in the session
- **Thinking count** — how many responses used extended thinking
- **Working files** — number of unique files Claude has read or edited
- **Cache hit ratio** — percentage of input tokens served from cache (green ≥70%, yellow ≥40%, orange <40%)
- **Git diff stats** — `+lines -lines` changed in the working tree
- **Tool errors** — count of failed tool calls this session
- **Sub-agent count** — number of spawned sub-agents (shown when > 0)
- **Live activity trace** — last 6 tool calls with file targets, plus edit counts per file this turn
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
4. Renders a three-line status bar with live activity trace

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
