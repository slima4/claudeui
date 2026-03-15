#!/usr/bin/env python3
"""ClaudeTUI API Sniffer — intercept and log Claude Code API calls.

A transparent HTTP proxy that captures all API requests/responses between
Claude Code and Anthropic's servers. Uses ANTHROPIC_BASE_URL to redirect
traffic through localhost — no TLS interception or certificates needed.

Usage:
    claudetui sniffer                  # start on default port 7735
    claudetui sniffer --port 8080      # custom port
    claudetui sniffer --full           # log complete request/response bodies
    claudetui sniffer --quiet          # no terminal output, log only
"""

import argparse
import errno
import http.client
import http.server
import json
import os
import signal
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────

UPSTREAM_HOST = "api.anthropic.com"
UPSTREAM_PORT = 443
DEFAULT_PORT = 7735
LOG_DIR = Path.home() / ".claude" / "api-sniffer"
PORT_DIR = LOG_DIR  # port files stored as .port.{PORT}

REDACT_HEADERS = {"x-api-key", "authorization"}

# ANSI
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GRAY = "\033[90m"

# Pricing (per 1M tokens)
MODEL_PRICING = {
    "claude-opus-4": {"input": 15.0, "cache_read": 1.5, "cache_write": 18.75, "output": 75.0},
    "claude-sonnet-4": {"input": 3.0, "cache_read": 0.30, "cache_write": 3.75, "output": 15.0},
    "claude-haiku-4": {"input": 0.80, "cache_read": 0.08, "cache_write": 1.0, "output": 4.0},
}

# Shared SSL context — reused across all requests (avoids reloading CA bundle)
_SSL_CTX = ssl.create_default_context()


def _match_pricing(model_id):
    """Match a model ID to pricing tier."""
    if not model_id:
        return MODEL_PRICING["claude-sonnet-4"]
    m = model_id.lower()
    for key in MODEL_PRICING:
        if key.replace("-", "") in m.replace("-", ""):
            return MODEL_PRICING[key]
    return MODEL_PRICING["claude-sonnet-4"]


def _format_tokens(n):
    """Format token count: 1234 -> '1.2k', 1234567 -> '1.2M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _format_bytes(n):
    """Format byte count: 1234 -> '1.2KB', 1234567 -> '1.2MB'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f}KB"
    return f"{n}B"


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _calc_cost(usage, model_id):
    """Calculate cost from usage dict."""
    pricing = _match_pricing(model_id)
    input_t = usage.get("input_tokens", 0)
    cache_r = usage.get("cache_read_input_tokens", 0)
    cache_w = usage.get("cache_creation_input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    return (
        input_t * pricing["input"]
        + cache_r * pricing["cache_read"]
        + cache_w * pricing["cache_write"]
        + output_t * pricing["output"]
    ) / 1_000_000


# ── Request/Response Summarizers ─────────────────────────────────────

def _summarize_request(body_bytes, full=False):
    """Extract key fields from request body for logging."""
    try:
        body = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw_length": len(body_bytes)}

    if full:
        return body

    summary = {}
    for key in ("model", "max_tokens", "stream", "temperature", "top_p",
                "top_k", "stop_sequences", "metadata"):
        if key in body:
            summary[key] = body[key]

    # System prompt — just length
    system = body.get("system")
    if system:
        if isinstance(system, str):
            summary["system_length"] = len(system)
        elif isinstance(system, list):
            summary["system_length"] = sum(
                len(b.get("text", "")) for b in system if isinstance(b, dict)
            )

    # Messages — count and approximate size (avoid re-serializing)
    messages = body.get("messages", [])
    summary["message_count"] = len(messages)
    summary["body_length"] = len(body_bytes)

    # Tools — just names
    tools = body.get("tools", [])
    if tools:
        summary["tool_count"] = len(tools)
        summary["tool_names"] = [t.get("name", "?") for t in tools if isinstance(t, dict)]

    return summary


def _reassemble_sse(raw_bytes):
    """Parse SSE byte stream into structured response summary."""
    model = ""
    stop_reason = ""
    usage = {}
    block_types = []
    tool_names = []
    event_count = 0

    # Process line-by-line from bytes to avoid full decode + split copy
    for raw_line in raw_bytes.split(b"\n"):
        if not raw_line.startswith(b"data: "):
            continue
        try:
            data = json.loads(raw_line[6:])
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        event_count += 1
        etype = data.get("type", "")

        if etype == "message_start":
            msg = data.get("message", {})
            model = msg.get("model", "")
            usage = msg.get("usage", {})
        elif etype == "content_block_start":
            block = data.get("content_block", {})
            btype = block.get("type", "unknown")
            block_types.append(btype)
            if btype == "tool_use":
                tool_names.append(block.get("name", "?"))
        elif etype == "message_delta":
            delta_usage = data.get("usage", {})
            usage.update(delta_usage)
            delta = data.get("delta", {})
            if "stop_reason" in delta:
                stop_reason = delta["stop_reason"]

    return {
        "model": model,
        "stop_reason": stop_reason,
        "usage": usage,
        "content_blocks": block_types,
        "tool_names": tool_names,
        "event_count": event_count,
    }


# ── Session Tracker ──────────────────────────────────────────────────

class SessionTracker:
    """Detect sub-agents by tool availability.

    Main session always has the 'Agent' tool in its tool list. Sub-agents
    never do. Claude Code uses the SAME metadata session ID for sub-agents,
    so we can't distinguish by session ID — tool presence is the reliable
    signal. Sub-agents are grouped by model + system_length for labeling.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._agent_counter = 0
        self._contexts = {}          # (model_key, sys_len_bucket) -> agent_num

    def check(self, tool_names, system_length=0, model=""):
        """Return (label, is_new) based on tool availability.

        Returns:
            ("main", False) for main session (has Agent tool)
            ("agent.1", True) for first request from a new sub-agent
            ("agent.1", False) for known sub-agent context
            ("", False) for requests without tools (e.g. count_tokens)
        """
        if not tool_names:
            return ("", False)

        # Main session always has the Agent tool
        if "Agent" in tool_names:
            return ("main", False)

        with self._lock:
            # Sub-agent: group by model family + system_length bucket (2k range)
            # so haiku-explore (sys=3898) != haiku-websearch (sys=194)
            parts = model.split("-")[:3]
            model_key = "-".join(parts) if parts else model
            bucket = (model_key, system_length // 2000)

            if bucket in self._contexts:
                return (f"agent.{self._contexts[bucket]}", False)

            self._agent_counter += 1
            self._contexts[bucket] = self._agent_counter
            return (f"agent.{self._agent_counter}", True)

    @property
    def agent_count(self):
        with self._lock:
            return self._agent_counter


# ── Compaction Detection ─────────────────────────────────────────────

def _extract_session_id(metadata):
    """Extract short session ID from metadata user_id field."""
    if not metadata or not isinstance(metadata, dict):
        return ""
    user_id = metadata.get("user_id", "")
    if "_session_" in user_id:
        return user_id.split("_session_", 1)[1][:8]
    return ""


class CompactionDetector:
    """Detect compaction by comparing consecutive main-session requests.

    Only tracks main session requests (sub-agents are ignored). Tracks per
    session ID so a new Claude Code session connecting to the same sniffer
    doesn't trigger a false compaction.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}  # session_id -> (prev_msg_count, prev_body_length)

    def check(self, request_summary, is_main_session, session_id=""):
        """Return True if this main-session request looks post-compaction."""
        if not is_main_session:
            return False

        msg_count = request_summary.get("message_count", 0)
        body_length = request_summary.get("body_length", 0)
        key = session_id or "_default"

        with self._lock:
            prev_msg, prev_body = self._sessions.get(key, (0, 0))
            self._sessions[key] = (msg_count, body_length)

            if prev_msg > 5 and msg_count < prev_msg * 0.5:
                return True
            if prev_body > 10_000 and body_length < prev_body * 0.3:
                return True

        return False


# ── Sniffer Handler ──────────────────────────────────────────────────

class SnifferHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that forwards requests to Anthropic API and logs them."""

    def do_POST(self):
        self._forward()

    def do_GET(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_OPTIONS(self):
        self._forward()

    def do_HEAD(self):
        self._forward()

    def _forward(self):
        request_id = self.server.next_id()
        start_time = time.monotonic()
        timestamp = _now_iso()

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Summarize request — always parse a logic summary for tracking,
        # use full body only for logging when --full is active
        logic_summary = _summarize_request(body, full=False)
        request_summary = (_summarize_request(body, full=True)
                           if self.server.full_bodies else logic_summary)
        is_streaming = False
        model_id = ""
        system_length = 0
        session_id = ""
        req_tool_names = []
        if isinstance(logic_summary, dict):
            is_streaming = logic_summary.get("stream", False)
            model_id = logic_summary.get("model", "")
            system_length = logic_summary.get("system_length", 0)
            session_id = _extract_session_id(logic_summary.get("metadata"))
            req_tool_names = logic_summary.get("tool_names", [])

        req_entry = {
            "type": "request",
            "id": request_id,
            "timestamp": timestamp,
            "method": self.command,
            "path": self.path,
            "headers": self._clean_headers(dict(self.headers)),
            "body": request_summary,
        }
        self.server.write_log(req_entry)

        # Forward to upstream
        conn = None
        try:
            conn = http.client.HTTPSConnection(
                UPSTREAM_HOST, UPSTREAM_PORT,
                context=_SSL_CTX,
                timeout=300,
            )
            fwd_headers = {}
            for key, val in self.headers.items():
                lk = key.lower()
                if lk not in ("host", "transfer-encoding", "accept-encoding"):
                    fwd_headers[key] = val
            fwd_headers["Host"] = UPSTREAM_HOST

            conn.request(self.command, self.path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
        except Exception as e:
            if conn:
                conn.close()
            # Upstream connection failed
            error_body = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)

            err_entry = {
                "type": "error",
                "id": request_id,
                "timestamp": _now_iso(),
                "error": str(e),
                "latency_ms": int((time.monotonic() - start_time) * 1000),
            }
            self.server.write_log(err_entry)
            self.server.print_line(
                request_id, self.command, self.path, model_id,
                0, 0, 0, (time.monotonic() - start_time) * 1000,
                error=str(e), status=502,
            )
            return

        # Send response status and headers to client
        self.send_response(resp.status)
        skip_headers = {"transfer-encoding", "connection"}
        content_type = ""
        for key, val in resp.getheaders():
            if key.lower() not in skip_headers:
                self.send_header(key, val)
            if key.lower() == "content-type":
                content_type = val
        self.end_headers()

        # Detect SSE: check request body flag + response content-type
        is_sse = is_streaming or "text/event-stream" in content_type

        buffer = bytearray()
        try:
            if is_sse:
                # SSE streaming — use read1() for non-blocking chunk reads
                while True:
                    try:
                        chunk = resp.read1(8192)
                    except AttributeError:
                        chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    buffer.extend(chunk)
            else:
                # Non-streaming — read all at once
                data = resp.read()
                self.wfile.write(data)
                buffer.extend(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected
        finally:
            conn.close()

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # Build response log entry
        resp_entry = {
            "type": "response",
            "id": request_id,
            "timestamp": _now_iso(),
            "status": resp.status,
            "latency_ms": latency_ms,
            "streaming": is_sse,
        }

        if is_sse:
            assembled = _reassemble_sse(bytes(buffer))
            resp_model = assembled.get("model", "") or model_id
            resp_usage = assembled.get("usage", {})
            resp_entry.update(assembled)
        else:
            resp_model = model_id
            resp_usage = {}
            try:
                resp_body = json.loads(bytes(buffer))
                resp_usage = resp_body.get("usage", {})
                resp_model = resp_body.get("model", model_id)
            except (json.JSONDecodeError, UnicodeDecodeError):
                resp_body = {"raw_length": len(buffer)}
            resp_entry["model"] = resp_model
            resp_entry["usage"] = resp_usage
            resp_entry["body"] = resp_body if self.server.full_bodies else {"length": len(buffer)}

        # Session / sub-agent tracking (must run before compaction detection)
        session_label, is_new_agent = self.server.session_tracker.check(
            req_tool_names, system_length=system_length,
            model=resp_model or model_id)
        is_main = session_label == "main"

        # Compaction detection (main session only, per session ID)
        is_compaction = self.server.compaction_detector.check(
            logic_summary, is_main_session=is_main,
            session_id=session_id)
        if is_compaction:
            resp_entry["is_compaction"] = True

        self.server.write_log(resp_entry)

        # Extract metadata for display
        if is_sse:
            stop_reason = assembled.get("stop_reason", "")
            block_types = assembled.get("content_blocks", [])
            tool_names = assembled.get("tool_names", [])
        else:
            stop_reason = resp_body.get("stop_reason", "") if isinstance(resp_body, dict) else ""
            content = resp_body.get("content", []) if isinstance(resp_body, dict) else []
            block_types = [b.get("type", "") for b in content if isinstance(b, dict)]
            tool_names = [b.get("name", "?") for b in content
                         if isinstance(b, dict) and b.get("type") == "tool_use"]

        # Terminal output
        input_t = resp_usage.get("input_tokens", 0)
        cache_r = resp_usage.get("cache_read_input_tokens", 0)
        cache_w = resp_usage.get("cache_creation_input_tokens", 0)
        output_t = resp_usage.get("output_tokens", 0)
        total_in = input_t + cache_r + cache_w
        cache_ratio = cache_r / (cache_r + cache_w) if (cache_r + cache_w) > 0 else 0

        self.server.print_line(
            request_id, self.command, self.path, resp_model,
            total_in, output_t, _calc_cost(resp_usage, resp_model),
            latency_ms, req_bytes=len(body), resp_bytes=len(buffer),
            stop_reason=stop_reason, block_types=block_types,
            tool_names=tool_names, cache_ratio=cache_ratio,
            session_label=session_label,
            is_new_agent=is_new_agent,
            is_compaction=is_compaction, status=resp.status,
        )

    def _clean_headers(self, headers):
        """Remove sensitive headers for logging."""
        if self.server.redact_keys:
            return {k: v for k, v in headers.items()
                    if k.lower() not in REDACT_HEADERS}
        return headers

    def log_message(self, format, *args):
        """Suppress default HTTP server log output."""
        pass


# ── Sniffer Server ───────────────────────────────────────────────────

class SnifferServer(http.server.ThreadingHTTPServer):
    """Threaded HTTP server with logging and state tracking."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, log_path, full_bodies=False, redact_keys=True,
                 quiet=False):
        super().__init__(("127.0.0.1", port), SnifferHandler)
        self.port = port
        self.log_path = log_path
        self.full_bodies = full_bodies
        self.redact_keys = redact_keys
        self.quiet = quiet
        self.compaction_detector = CompactionDetector()
        self.session_tracker = SessionTracker()

        self._lock = threading.Lock()
        self._counter = 0
        self._total_cost = 0.0
        self._total_in = 0
        self._total_out = 0
        self._total_req_bytes = 0
        self._total_resp_bytes = 0

        # Open log file with restricted permissions
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self._log_file = os.fdopen(fd, "w")

    def next_id(self):
        with self._lock:
            self._counter += 1
            return self._counter

    def write_log(self, entry):
        with self._lock:
            self._log_file.write(json.dumps(entry, separators=(",", ":")) + "\n")
            self._log_file.flush()

    def print_line(self, req_id, method, path, model, total_in, total_out,
                   cost, latency_ms, req_bytes=0, resp_bytes=0,
                   stop_reason="", block_types=None, tool_names=None,
                   cache_ratio=0, session_label="", is_new_agent=False,
                   error=None, is_compaction=False, status=200):
        """Print one-line summary to terminal."""
        if self.quiet:
            return

        # Shorten model name
        short_model = model
        for prefix in ("claude-", "anthropic-"):
            short_model = short_model.replace(prefix, "")
        # Remove date suffix like -20260301
        parts = short_model.rsplit("-", 1)
        if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
            short_model = parts[0]

        traffic = f"  {DIM}{_format_bytes(req_bytes)}/{_format_bytes(resp_bytes)}{RESET}"

        # Content block type abbreviations
        _BLOCK_ABBREV = {
            "thinking": "T",
            "text": "t",
            "tool_use": "U",
            "server_tool_use": "S",
            "web_search_tool_result": "W",
            "mcp_tool_use": "M",
            "mcp_tool_result": "m",
        }
        block_abbrevs = []
        for bt in (block_types or []):
            block_abbrevs.append(_BLOCK_ABBREV.get(bt, "?"))
        blocks_str = f"  {DIM}[{''.join(block_abbrevs)}]{RESET}" if block_abbrevs else ""

        # Cache ratio
        if total_in == 0:
            cache_str = ""
        elif cache_ratio == 0:
            cache_str = f"  {RED}0%c{RESET}"
        else:
            cache_str = f"  {DIM}{cache_ratio:.0%}c{RESET}"

        # Stop reason tag
        stop_str = ""
        if stop_reason == "max_tokens":
            stop_str = f"  {RED}max_tokens{RESET}"
        elif stop_reason == "tool_use" and tool_names:
            stop_str = f"  {DIM}{','.join(tool_names)}{RESET}"
        elif stop_reason == "tool_use":
            stop_str = f"  {DIM}tool{RESET}"

        # Session label
        session_str = ""
        if is_new_agent:
            session_str = f"  {CYAN}{BOLD}+{session_label}{RESET}"
        elif session_label and session_label != "main":
            session_str = f"  {CYAN}{session_label}{RESET}"

        with self._lock:
            self._total_cost += cost
            self._total_in += total_in
            self._total_out += total_out
            self._total_req_bytes += req_bytes
            self._total_resp_bytes += resp_bytes

            if error:
                print(f"  {RED}#{req_id:<3}{RESET} {method} {path}  "
                      f"{RED}ERROR: {error[:60]}{RESET}")
            elif status >= 400:
                print(f"  {RED}#{req_id:<3}{RESET} {method} {path}  "
                      f"{short_model}  {RED}{status}{RESET}  "
                      f"{latency_ms:.0f}ms")
            else:
                compact_tag = f"  {YELLOW}compaction{RESET}" if is_compaction else ""
                print(f"  {GREEN}#{req_id:<3}{RESET} {method} {path}  "
                      f"{CYAN}{short_model}{RESET}  "
                      f"{_format_tokens(total_in)}{DIM}->{RESET}{_format_tokens(total_out)}  "
                      f"{DIM}${cost:.3f}{RESET}  "
                      f"{DIM}{latency_ms:.0f}ms{RESET}"
                      f"{traffic}"
                      f"{cache_str}"
                      f"{blocks_str}"
                      f"{stop_str}"
                      f"{session_str}"
                      f"{compact_tag}")

    def print_summary(self):
        """Print final summary on shutdown."""
        if self.quiet:
            return
        with self._lock:
            print()
            agents = self.session_tracker.agent_count
            agent_str = f"  {DIM}|{RESET}  {agents} sub-agent{'s' if agents != 1 else ''}" if agents else ""
            print(f"  {BOLD}Summary:{RESET} {self._counter} requests  "
                  f"{DIM}|{RESET}  ${self._total_cost:.3f}  "
                  f"{DIM}|{RESET}  {_format_tokens(self._total_in)} in  "
                  f"{DIM}|{RESET}  {_format_tokens(self._total_out)} out  "
                  f"{DIM}|{RESET}  {_format_bytes(self._total_req_bytes)} sent  "
                  f"{DIM}|{RESET}  {_format_bytes(self._total_resp_bytes)} recv"
                  f"{agent_str}")
            print(f"  {DIM}Log: {self.log_path}{RESET}")
            print()

    def close(self):
        self._log_file.close()


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="claudetui sniffer",
        description="Intercept and log Claude Code API calls.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Proxy port (default: {DEFAULT_PORT})")
    parser.add_argument("--full", action="store_true",
                        help="Log complete request/response bodies (large files)")
    parser.add_argument("--no-redact", action="store_true",
                        help="Don't redact API keys from logs")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress terminal output")
    args = parser.parse_args()

    # Create log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"sniffer-{timestamp}.jsonl"

    # Warn about --no-redact
    if args.no_redact:
        print(f"  {RED}{BOLD}WARNING:{RESET} API keys will be written to log files!")
        print()

    # Start server
    try:
        server = SnifferServer(
            port=args.port,
            log_path=log_path,
            full_bodies=args.full,
            redact_keys=not args.no_redact,
            quiet=args.quiet,
        )
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f"  {RED}Port {args.port} is already in use.{RESET}")
            print(f"  Try: claudetui sniffer --port {args.port + 1}")
            sys.exit(1)
        raise

    # Write port file so other tools can discover the sniffer
    port_file = PORT_DIR / f".port.{args.port}"
    port_file.write_text(str(args.port))

    if not args.quiet:
        print()
        print(f"  {BOLD}ClaudeTUI API Sniffer{RESET} {DIM}— listening on "
              f"http://127.0.0.1:{args.port}{RESET}")
        print()
        print(f"  {BOLD}Use:{RESET}  "
              f"{CYAN}ANTHROPIC_BASE_URL=http://localhost:{args.port} claude{RESET}")
        print(f"  {BOLD}Log:{RESET}  {DIM}{log_path}{RESET}")
        print()
        if args.full:
            print(f"  {YELLOW}Full body logging enabled — log files may be large{RESET}")
            print()

    # Handle Ctrl+C gracefully
    def shutdown(sig, frame):
        if not args.quiet:
            print(f"\n  {DIM}Shutting down...{RESET}")
        port_file.unlink(missing_ok=True)
        server.print_summary()
        server.close()
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    server.serve_forever()


if __name__ == "__main__":
    main()
