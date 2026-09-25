"""Prove the server starts, and advertises every tool, away from Windows.

Why this exists
    0.3.0 raised RuntimeError while importing the package on any non-Windows platform, so the
    server could not start there at all. That blocks anything that enumerates a server by
    running it and reading `tools/list` — Glama builds and runs every server inside a Linux
    sandbox before its listing becomes discoverable, and withholds the listing when that
    fails. 0.3.1 guards the Win32 imports in `dsh_cua/server.py` instead.

    This is the regression test for that guard, and it is only meaningful OFF Windows: on
    Windows the Win32 modules import normally and the guard is never exercised. It therefore
    SKIPS (exit 0) on Windows rather than pretending to pass, and CI runs it on ubuntu-latest.

It asserts four things:
    1. the premise — no Win32 module is importable here (`ctypes.windll`, `comtypes`)
    2. the package imports and `dsh_cua.server.main` is callable
    3. `initialize` answers, and `tools/list` returns the full tool set with the same
       read-only subset the README documents, schemas included
    4. calling EVERY read-only tool answers cleanly — either the Windows-only refusal, or a
       real structured result, for the tools that are not Win32-backed
       (`tool_clipboard_read` goes through pyperclip and really runs). One tool was not
       enough: this file used to call only `tool_list_windows` while 0.3.2 shipped
       `tool_list_displays` raising `module 'ctypes' has no attribute 'BOOL'` on every
       Windows machine. The schema was perfect, and this test stayed green.

Why the requests are sent one at a time
    The first version piped all four messages in at once and closed stdin. That raced the
    server's own EOF shutdown: the reply to the last request was sometimes never written, so
    the test passed on the author's machine and failed on the runner. Reading each reply
    before sending the next removes the race — and an intermittently failing test is worse
    than no test, because it trains everyone to ignore it.

usage:  python tests/linux-handshake.py
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

EXPECTED_TOOLS = 19

# The read-only set the README and the skill rely on. An explicit list rather than a count,
# so that a tool silently changing category is caught, not just a tool disappearing.
EXPECTED_READ_ONLY = {
    "tool_capture_window", "tool_clipboard_read", "tool_coexistence_status",
    "tool_cursor_position", "tool_element_at_point", "tool_find_elements",
    "tool_find_window", "tool_get_window_rect", "tool_list_displays",
    "tool_list_windows", "tool_read_element", "tool_skyshot",
}


def fail(message: str, detail: str = "") -> None:
    print(f"FAIL: {message}")
    if detail:
        print(detail)
    sys.exit(1)


def minimal_args(schema: dict) -> dict:
    """The smallest argument set that passes schema validation, so the call reaches the tool.

    Derived from the schema rather than hard-coded, because the values are never used: off
    Windows the tool body raises before touching them. Deriving them means a tool that gains a
    required argument keeps being exercised here instead of quietly dropping out of the loop.
    """
    properties = (schema or {}).get("properties") or {}
    args: dict = {}
    for name in (schema or {}).get("required") or []:
        spec = properties.get(name) or {}
        if spec.get("enum"):
            args[name] = spec["enum"][0]
        else:
            args[name] = {"integer": 0, "number": 0, "boolean": False,
                          "array": [], "object": {}}.get(spec.get("type"), "0")
    return args


class StdioServer:
    """A stdio MCP server, driven request by request."""

    def __init__(self, argv: list[str]) -> None:
        # bufsize=1 keeps the pipe line-buffered in text mode; encoding/errors are explicit
        # because the tool descriptions carry characters a non-UTF-8 locale cannot represent.
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.replies: queue.Queue[dict] = queue.Queue()
        self.seen: list[dict] = []
        self.stderr: list[str] = []
        # Both pipes must be drained continuously: a full pipe buffer blocks the child.
        threading.Thread(target=self._drain_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stdout(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue                      # FastMCP logs ahead of the JSON-RPC
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in message:
                self.seen.append(message)
                self.replies.put(message)

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def await_id(self, wanted: int, timeout: float = 90) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail(f"no reply to request id={wanted} within {timeout:.0f}s",
                     self.diagnostics())
            try:
                message = self.replies.get(timeout=remaining)
            except queue.Empty:
                fail(f"no reply to request id={wanted} within {timeout:.0f}s",
                     self.diagnostics())
            if message.get("id") == wanted:
                return message
            # A reply to an earlier request: keep waiting for the one we asked for.

    def diagnostics(self) -> str:
        lines = ["replies seen: " + (json.dumps(self.seen)[:800] if self.seen else "(none)"),
                 f"process alive: {self.proc.poll() is None}  exit: {self.proc.poll()}"]
        if self.stderr:
            lines.append("stderr tail:")
            lines.extend("  " + line for line in self.stderr[-30:])
        return "\n".join(lines)

    def close(self) -> None:
        try:
            self.proc.stdin.close()           # EOF: the server shuts itself down
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def classify_reply(called: dict) -> tuple[str, str]:
    """Judge one tools/call reply: ("refused" | "answered" | "bad", detail).

    Split out of main() so it can be exercised without a Linux runner, because the first
    version of this loop asserted that EVERY read-only tool refuses with the Windows-only
    message — and CI disproved that on its very first run. `tool_clipboard_read` is not
    Win32-backed: it goes through pyperclip and really runs, answering `{"success": false}` on a
    bare runner. That is a correct answer, not a refusal.

    The intentional refusal and a genuine bug arrive in the SAME shapes — FastMCP renders an
    exception raised inside a tool as `Error executing tool <name>: ...` in the content, and also
    uses the JSON-RPC `error` member — so the message is what separates them, never the shape:

        "... requires Windows ..."                  -> the guard working as designed
        a JSON object/array                         -> a clean answer (success or failure is the
                                                       tool's business, not this test's)
        anything else, in either shape              -> a defect, and the reason this test exists

    The JSON-RPC branch is checked first and is NOT waved through as an answer: an error object
    is itself JSON, and treating a stray protocol error as a reply is exactly the kind of false
    green this whole file exists to avoid.
    """
    if "error" in called:
        text = json.dumps(called["error"]).strip()
        if "requires Windows" in text:
            return "refused", ""
        return "bad", f"JSON-RPC error: {text[:200]}"

    result = called.get("result") or {}
    text = " ".join(part.get("text", "") for part in result.get("content", [])
                    if isinstance(part, dict)).strip()

    if "requires Windows" in text:
        return "refused", ""
    if text.startswith("{") or text.startswith("["):
        try:
            json.loads(text)
        except json.JSONDecodeError:
            return "bad", f"the answer is not valid JSON: {text[:200]}"
        return "answered", ""
    return "bad", ("neither refused as Windows-only nor answered: "
                   f"{text[:200] or json.dumps(called)[:200]}")


def main() -> int:
    if sys.platform == "win32":
        print("SKIP: this test asserts the NON-Windows path; on Windows the guard is unused.")
        return 0

    # 1. the premise: if these held, the test would be proving nothing.
    import ctypes
    if hasattr(ctypes, "windll"):
        fail("ctypes.windll exists on this platform — the guard is not being exercised")
    if hasattr(ctypes, "WINFUNCTYPE"):
        fail("ctypes.WINFUNCTYPE exists on this platform — the guard is not being exercised")
    try:
        import comtypes  # noqa: F401
        fail("comtypes is importable here — the Windows-only dependency marker did not apply")
    except ImportError:
        pass

    # 2. the package imports.
    try:
        from dsh_cua import __version__
        from dsh_cua.server import main as server_main
    except Exception as exc:                  # noqa: BLE001 - the failure IS the point
        fail("importing dsh_cua.server failed away from Windows", f"{type(exc).__name__}: {exc}")
    if not callable(server_main):
        fail("dsh_cua.server.main is not callable")
    print(f"imports ok  : dsh_cua {__version__} on {sys.platform}, python {sys.version.split()[0]}")

    # 3. drive the stdio server for real, one request at a time.
    server = StdioServer([sys.executable, "-m", "dsh_cua"])
    try:
        server.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "linux-handshake", "version": "1"}}})
        init = server.await_id(1)
        info = init.get("result", {}).get("serverInfo")
        if not info:
            fail("initialize returned no serverInfo", json.dumps(init)[:800] + "\n" + server.diagnostics())
        print(f"serverInfo  : {info}")

        # The version a client is shown must be OURS, not the MCP SDK's. FastMCP has no
        # `version` parameter and never passes one to the low-level Server, which then falls
        # back to pkg_version("mcp") — so 0.1.0 through 0.3.3 all reported the SDK's version
        # (e.g. "1.28.1") as if it were this server's.
        if info and info.get("version") != __version__:
            fail("serverInfo.version is not dsh_cua.__version__",
                 f"server says {info.get('version')!r}, package is {__version__!r}")

        # MCP requires this notification between initialize and any other request; without it
        # the server answers tools/list with an empty list rather than an error.
        server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listing = server.await_id(2)
        tools = listing.get("result", {}).get("tools", [])
        names = sorted(t["name"] for t in tools)
        if len(tools) != EXPECTED_TOOLS:
            fail(f"tools/list returned {len(tools)} tools, expected {EXPECTED_TOOLS}",
                 "got: " + ", ".join(names) + "\n" + server.diagnostics())
        read_only = {n for n in names if n in EXPECTED_READ_ONLY}
        if read_only != EXPECTED_READ_ONLY:
            fail("the read-only subset changed",
                 f"missing: {sorted(EXPECTED_READ_ONLY - read_only)}\n"
                 f"unexpected: {sorted(read_only - EXPECTED_READ_ONLY)}")
        missing_desc = [t["name"] for t in tools if not t.get("description")]
        if missing_desc:
            fail("some tools have no description", str(missing_desc))
        missing_schema = [t["name"] for t in tools if not t.get("inputSchema")]
        if missing_schema:
            fail("some tools have no inputSchema — the guard degraded the schemas",
                 str(missing_schema))
        print(f"tools       : {len(tools)} total, {len(read_only)} read-only, "
              "descriptions + schemas present")

        # 4. EVERY read-only tool must answer cleanly. Two outcomes are correct off Windows and
        #    both are accepted (see classify_reply): the Win32-backed tools refuse with the
        #    Windows-only explanation, and tools that are not Win32-backed really run. What must
        #    never happen is an unhandled exception — 0.3.2's tool_list_displays produced exactly
        #    that on Windows, and this loop is what would have caught it.
        schemas = {t["name"]: (t.get("inputSchema") or {}) for t in tools}
        bad: list[str] = []
        refused = answered = 0
        for index, name in enumerate(sorted(EXPECTED_READ_ONLY), start=3):
            server.send({"jsonrpc": "2.0", "id": index, "method": "tools/call",
                         "params": {"name": name, "arguments": minimal_args(schemas[name])}})
            called = server.await_id(index)
            verdict, detail = classify_reply(called)
            if verdict == "bad":
                bad.append(f"{name} -> {detail}")
            elif verdict == "refused":
                refused += 1
            else:
                answered += 1
        if bad:
            fail("a read-only tool did not answer cleanly", "\n".join(bad))
        print(f"tool calls  : {len(EXPECTED_READ_ONLY)} read-only tools answered cleanly "
              f"({refused} refused as Windows-only, {answered} answered for real)")
    finally:
        server.close()

    print("\nOK: the server starts off Windows, advertises every tool, and refuses calls clearly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
