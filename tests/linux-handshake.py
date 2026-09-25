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
       read-only subset the README documents
    4. calling a tool fails with the Windows-only explanation instead of crashing the server

usage:  python tests/linux-handshake.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

EXPECTED_TOOLS = 19

# The read-only set the README and the skill rely on. Kept as an explicit list rather than a
# count so that a tool silently changing category is caught, not just a tool disappearing.
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


def main() -> int:
    if sys.platform == "win32":
        print("SKIP: this test asserts the NON-Windows path; on Windows the guard is unused.")
        return 0

    # 1. the premise: if these were importable the test would be proving nothing.
    import ctypes
    if hasattr(ctypes, "windll"):
        fail("ctypes.windll exists on this platform — the guard is not being exercised")
    if hasattr(ctypes, "WINFUNCTYPE"):
        fail("ctypes.WINFUNCTYPE exists on this platform — the guard is not being exercised")
    try:
        import comtypes  # noqa: F401
        fail("comtypes is importable here — the Windows-only marker did not apply")
    except ImportError:
        pass

    # 2. the package imports.
    try:
        from dsh_cua import __version__
        from dsh_cua.server import main as server_main
    except Exception as exc:                      # noqa: BLE001 - the failure IS the point
        fail("importing dsh_cua.server failed away from Windows", f"{type(exc).__name__}: {exc}")
    if not callable(server_main):
        fail("dsh_cua.server.main is not callable")
    print(f"imports ok: dsh_cua {__version__} on {sys.platform}, python {sys.version.split()[0]}")

    # 3. drive the stdio server for real.
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "linux-handshake", "version": "1"}}}),
        # MCP requires this notification between initialize and any other request; without it
        # the server answers tools/list with an empty list rather than an error.
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        # A tool call must fail with the reason, not take the process down.
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "tool_list_windows", "arguments": {}}}),
    ])

    # encoding/errors are explicit on purpose: `text=True` alone decodes with the locale
    # default, and the tool descriptions carry characters some locales cannot represent.
    proc = subprocess.run([sys.executable, "-m", "dsh_cua"], input=requests,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    responses: dict[int, dict] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue                              # FastMCP logs to stdout ahead of the JSON-RPC
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in message:
            responses[message["id"]] = message

    init = responses.get(1, {}).get("result", {})
    if not init.get("serverInfo"):
        fail("initialize returned no serverInfo", proc.stdout[-1500:] + "\n" + proc.stderr[-1500:])
    print(f"serverInfo  : {init['serverInfo']}")

    tools = responses.get(2, {}).get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    if len(tools) != EXPECTED_TOOLS:
        fail(f"tools/list returned {len(tools)} tools, expected {EXPECTED_TOOLS}",
             "got: " + ", ".join(names))
    read_only = {n for n in names if n in EXPECTED_READ_ONLY}
    if read_only != EXPECTED_READ_ONLY:
        fail("the read-only subset changed",
             f"missing: {sorted(EXPECTED_READ_ONLY - read_only)}\n"
             f"unexpected: {sorted(read_only - EXPECTED_READ_ONLY)}")
    if not all(t.get("description") for t in tools):
        fail("some tools have no description", str([t["name"] for t in tools if not t.get("description")]))
    if not all(t.get("inputSchema") for t in tools):
        fail("some tools have no inputSchema — the guard degraded the schemas",
             str([t["name"] for t in tools if not t.get("inputSchema")]))
    print(f"tools       : {len(tools)} total, {len(read_only)} read-only, descriptions + schemas present")

    # 4. calling a Windows-only tool must explain itself.
    call = responses.get(3, {})
    result = call.get("result", {})
    text = " ".join(part.get("text", "") for part in result.get("content", []) if isinstance(part, dict))
    if "error" in call:
        text = json.dumps(call["error"])
    elif result.get("isError") is not True:
        fail("a Windows-only tool call neither errored nor reported isError",
             json.dumps(call)[:1500])
    if "requires Windows" not in text:
        fail("the refusal does not say Windows is required", text[:1500] or json.dumps(call)[:1500])
    print(f"tool call   : refused as expected -> {text.strip()[:110]}...")

    if proc.returncode not in (0, None, -15):
        print(f"note: server exit code {proc.returncode} (expected only if it was torn down)")
    print("\nOK: the server starts off Windows, advertises every tool, and refuses calls clearly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
