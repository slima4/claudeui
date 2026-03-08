# Claude Code Monitor — Live Session Dashboard

A standalone terminal dashboard that monitors your active Claude Code session in real time.

```
1001000110100101101001011001101001100101101001011001101001100101
────────────────────────────────────────────────────────────
  ● claude-opus-4-6  │  90980d3d  │  2h 15m 03s  │  ACTIVE  │  ⏱ 12s

  CONTEXT
  ████████████████░░░░░░░░░░░░░░  53.2%  106.4k/200.0k
  ▁▂▃▃▄▅▅▆▆▇▇↓▂▃▃▄▄▅▅▆▆▇↓▁▂▃▃▄▄▅▅▆
  Compactions: 2  │  Turns left: ~31  │  Since compact: 28

  COST
  $45.20 total  │  ~$0.85/turn  │  $0.34/min  │  $412.50 saved
  Input: $0.01  │  Cache: $38.40  │  Output: $6.79
  ██████████████████████████████████████████████████
  ■input 0%  ■cache 85%  ■output 15%

  CURRENT
  12 tools  │  Edit:5  Read:4  Bash:3
  read monitor.py → edit monitor.py → bash python3 │ monitor.py×5
  monitor.py(4r/5e) README.md(1r/1e)
  0 errors  │  1 thinking

  SESSION
  53 turns  │  312 tools  │  Bash:98  Edit:87  Read:72
  statusline.py(42r/38e) monitor.py(31r/31e) README.md(20r/25e)
  3 errors  │  12 thinking (4%)  │  98% cache  │  2 agents

  LOG
  14:32:01  read monitor.py
  14:32:03  edit monitor.py
  14:32:05  bash python3
  14:32:10  ⚡ compaction #2
  14:32:15  error: File not found

  ────────────────────────────────────────────────────────────
  [s] stats  [d] details  [l] log  [e] export  [o] sessions  [?] help  [q] quit
```

## Usage

```bash
# Auto-detect the active session in current directory
python3 claude-code-monitor/monitor.py

# Monitor a specific session by ID
python3 claude-code-monitor/monitor.py abc12345

# List recent sessions
python3 claude-code-monitor/monitor.py --list
```

Run it in a separate terminal while Claude Code is working.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `s` | Run **session-stats** — full cost breakdown, token sparkline, tool usage |
| `d` | Run **session-manager show** — detailed session view |
| `l` | **Event log** — scrollable log with filtering and live auto-scroll |
| `e` | **Export** session as markdown file |
| `o` | Run **session-manager list** — browse all recent sessions |
| `?` | Toggle **help overlay** with all features and shortcuts |
| `q` | Quit the monitor |

Press any key after viewing a report to return to the live dashboard.

## Features

- **Matrix rain header** — animated binary rain at 100ms, smooth cursor-positioned updates
- **Turn timer** — `⏱ 12s` shows how long Claude has been working on the current response, color-coded by duration
- **Live duration** — elapsed time updates every second
- **Activity indicator** — `● ACTIVE` / `● WORKING` / `○ IDLE 30s` based on transcript staleness
- **Token breakdown bar** — visual bar showing input/cache/output proportions with percentages
- **Current vs Session** — split activity view: current turn (this question/answer) and full session totals
- **Mini event log** — last 8 timestamped events: tool calls, errors, compactions
- **Full log viewer** — `l` opens scrollable log with `f` to filter (all/errors/bash/edits/search/compactions) and `a` for live auto-scroll
- **Green pulse** — separator flashes bright green when new data arrives
- **Compaction alert** — `⚡ JUST COMPACTED` highlight after compaction events
- **Cost burn rate** — `$/min` alongside per-turn average cost
- **Live tool trace** — last 5 tool calls shown as `read file → edit file → bash python3`
- **Last error message** — displayed inline with word wrapping
- **Terminal resize** — adapts bar width, sparkline, and layout to terminal size
- **Auto-follow** — detects and switches to new sessions when current one goes idle
- **Interactive hotkeys** — launch stats, details, sessions, export without leaving the monitor
- **Help overlay** — press `?` for full feature and shortcut reference
- **Alternate screen buffer** — clean terminal, tool output stays in normal scrollback
- **Context bar + sparkline** — colored by usage threshold, compaction markers (`↓`)
- **Compaction prediction** — estimated turns until next auto-compaction

## Requirements

- Python 3.8+, stdlib only — no external dependencies
- Optional: sibling tools (`claude-code-session-stats/`, `claude-code-session-manager/`) for hotkey integration
