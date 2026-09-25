"""Rehearse linux-handshake.py's reply classifier against REAL recorded payloads.

Why this exists
    linux-handshake.py calls every read-only tool and judges each reply. Its first version
    asserted that all twelve refuse with the Windows-only message — and CI disproved that on its
    very first run: `tool_clipboard_read` is not Win32-backed, it goes through pyperclip and
    really answered `{"success": false}` on the bare runner. That test SKIPs on Windows by
    design, so the claim could not be checked on the machine it was written on, and the runner
    found it instead.

    The fix was to make the risky part locally testable: the judgement is now the pure function
    `classify_reply()`, and this file drives it with payloads quoted verbatim from real captures.
    It runs anywhere, so the next change to that logic fails here rather than in CI.

    The hard part it pins down: a working refusal and a real defect arrive in the SAME shapes,
    because FastMCP renders an exception raised inside a tool both as `Error executing tool
    <name>: ...` in the content AND as a JSON-RPC `error` member. Only the message separates
    them — so case 4 below, the actual 0.3.2 defect, must keep being reported as bad.

usage:  python tests/check-handshake-classifier.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "handshake", os.path.join(REPO, "tests", "linux-handshake.py"))
handshake = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
spec.loader.exec_module(handshake)                          # type: ignore[union-attr]
classify = handshake.classify_reply


def content(text: str, is_error: bool = False) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return {"jsonrpc": "2.0", "id": 4, "result": result}


# 1. VERBATIM from CI run 36104899618, linux-introspection, tool_clipboard_read (id=4).
#    This is the payload that broke the first version of the loop. It is a real answer.
CLIPBOARD_ON_RUNNER = content(json.dumps({
    "success": False,
    "error": ("clipboard read failed: Pyperclip could not find a copy/paste mechanism for your "
              "system. For more information, please visit "
              "https://pyperclip.readthedocs.io/en/latest/index.html#not-implemented-error\n"
              "On Linux, you can run `sudo apt-get install xclip`, `sudo apt-get install "
              "xselect` (on X11) or `sudo apt-get install wl-clipboard` (on Wayland) to install "
              "a copy/paste mechanism."),
}, indent=2))

# 2. The intended refusal, as FastMCP renders a tool that raised.
REFUSAL_IN_CONTENT = content(
    "Error executing tool tool_capture_window: the Win32 bridge: this tool requires Windows.",
    is_error=True)

# 3. The same refusal arriving through the JSON-RPC `error` member instead.
REFUSAL_AS_RPC_ERROR = {"jsonrpc": "2.0", "id": 3,
                        "error": {"code": -32603,
                                  "message": "the Win32 bridge: this tool requires Windows"}}

# 4. THE DEFECT. VERBATIM from a live call to mcp__win32__tool_list_displays on 0.3.2 — the whole
#    reason the loop exists. Same shape as case 2, different message.
F1_DEFECT = content(
    "Error executing tool tool_list_displays: module 'ctypes' has no attribute 'BOOL'",
    is_error=True)

# 5. A protocol error that is not a refusal — an error object is JSON, so it must not be waved
#    through as an answer by the JSON branch.
RPC_ERROR_NOT_REFUSAL = {"jsonrpc": "2.0", "id": 5,
                         "error": {"code": -32601, "message": "Method not found"}}

# 6. A real answer from a tool that works everywhere (shape of tool_list_windows).
REAL_ANSWER = content(json.dumps({"success": True, "count": 0, "windows": []}))

# 7. Empty content.
EMPTY = {"jsonrpc": "2.0", "id": 7, "result": {"content": []}}

# 8. Non-JSON text that is not a refusal either.
PLAIN_TEXT = content("something went sideways")

CASES = [
    ("clipboard_read's real runner answer (the payload that broke v1)", CLIPBOARD_ON_RUNNER, "answered"),
    ("a Win32-backed tool refusing, rendered in content", REFUSAL_IN_CONTENT, "refused"),
    ("a Win32-backed tool refusing, as a JSON-RPC error", REFUSAL_AS_RPC_ERROR, "refused"),
    ("F1: the 0.3.2 tool_list_displays defect must be caught", F1_DEFECT, "bad"),
    ("a JSON-RPC protocol error is not an answer", RPC_ERROR_NOT_REFUSAL, "bad"),
    ("a real tool answer", REAL_ANSWER, "answered"),
    ("empty content", EMPTY, "bad"),
    ("non-JSON text that is not a refusal", PLAIN_TEXT, "bad"),
]


def main() -> int:
    failures = 0
    for label, payload, expected in CASES:
        verdict, detail = classify(payload)
        ok = verdict == expected
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"         expected={expected}  got={verdict}" + (f"  ({detail})" if detail else ""))
    print()
    if failures:
        print(f"FAILED: {failures} of {len(CASES)}")
        return 1
    print(f"ALL {len(CASES)} CASES PASSED — including the F1 defect, which must still be "
          "reported as bad")
    return 0


if __name__ == "__main__":
    sys.exit(main())
