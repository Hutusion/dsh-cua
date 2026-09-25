"""Call every read-only tool for real, through the MCP stdio server.

Why this exists
    0.3.2 shipped `tool_list_displays` broken on every Windows machine — it raised
    `module 'ctypes' has no attribute 'BOOL'` unconditionally, while its schema was
    perfect and the README advertised it as safe to call at any time. No test invoked it.
    CI cannot: it runs on Linux, where every tool only ever refuses. The Windows scripts
    here call the three or four tools they happen to need.

    A tool that always raises therefore stays invisible until a user calls it. This script
    calls all twelve read-only tools and requires each one to answer, so the next such
    defect fails here instead of in someone's session.

Safe to run on a machine in use
    Read-only, by construction: no mutating call, no synthesized input, no cursor move.
    Two things to know anyway —
      * tool_capture_window writes a screenshot; it goes to a temp file that is deleted.
      * tool_clipboard_read reads the real clipboard. Only success and character count are
        asserted or printed, never the content.

usage:  python tests/verify-tools-readonly.py          # against src/ in this repo
        WIN32_MCP_DIR=<dir> python tests/verify-tools-readonly.py   # against a live checkout
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.environ.get("WIN32_MCP_DIR")

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(label)


class Server:
    """Minimal MCP stdio client: one request, one reply, no threads.

    Requests are sent one at a time and each reply is read before the next is written.
    Batching them and closing stdin races the server's own EOF shutdown, which drops the
    last reply — an intermittent failure is worse than no test.
    """

    def __init__(self) -> None:
        if LIVE:
            argv = [sys.executable, os.path.join(LIVE, "server.py")]
            env = {**os.environ, "PYTHONPATH": LIVE}
        else:
            argv = [sys.executable, "-m", "dsh_cua"]
            env = {**os.environ, "PYTHONPATH": os.path.join(REPO, "src")}
        env["PYTHONIOENCODING"] = "utf-8"
        self._err = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
        self._id = 0

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def _read_reply(self, want_id: int, timeout_s: float = 30.0) -> dict:
        assert self.proc.stdout
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        return {"__timeout__": True, "stderr": (self._stderr())}

    def _stderr(self) -> str:
        try:
            self._err.seek(0)
            return self._err.read()[-1500:]
        except Exception:  # noqa: BLE001
            return "<unreadable>"

    def notify(self) -> None:
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name: str, arguments: dict, timeout_s: float = 30.0) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
        return self._read_reply(self._id, timeout_s)

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def result_of(reply: dict, tool: str) -> dict:
    """Turn a tools/call reply into {'ok': bool, 'text': str, 'detail': str}."""
    if reply.get("__timeout__"):
        return {"ok": False, "text": "", "detail": f"no reply (stderr: {reply['stderr'][-400:]})"}
    if "error" in reply:
        return {"ok": False, "text": "", "detail": json.dumps(reply["error"])[:300]}
    result = reply.get("result", {})
    text = " ".join(p.get("text", "") for p in result.get("content", [])
                    if isinstance(p, dict))
    if result.get("isError") is True:
        return {"ok": False, "text": text, "detail": f"isError: {text.strip()[:300]}"}
    if not text.strip():
        return {"ok": False, "text": text, "detail": "empty result"}
    try:
        return {"ok": True, "text": text, "data": json.loads(text), "detail": ""}
    except json.JSONDecodeError:
        return {"ok": True, "text": text, "data": None, "detail": ""}


def main() -> int:
    print(f"server      : {LIVE or os.path.join(REPO, 'src')}")
    server = Server()
    try:
        server._send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "verify-tools-readonly", "version": "1"}}})
        init = server._read_reply(0)
        if init.get("__timeout__"):
            print("FAIL: initialize got no reply — the server did not start")
            print(init["stderr"])
            return 1
        server.notify()
        print(f"serverInfo  : {init.get('result', {}).get('serverInfo')}\n")

        # ---- 1. the two no-argument status tools
        print("group 1 — process and input-arbiter status")
        for name in ("tool_coexistence_status", "tool_cursor_position"):
            r = result_of(server.call(name, {}), name)
            check(f"{name} answers", r["ok"], r["detail"])
        if r["ok"] and r["data"]:
            check("tool_cursor_position reports a screen size",
                  isinstance(r["data"].get("screen"), list) and len(r["data"]["screen"]) == 2,
                  str(r["data"].get("screen")))

        # ---- 2. monitors: the tool 0.3.2 shipped broken
        print("\ngroup 2 — displays (the 0.3.2 regression)")
        r = result_of(server.call("tool_list_displays", {}), "tool_list_displays")
        check("tool_list_displays answers", r["ok"], r["detail"])
        data = r.get("data")
        monitors = data.get("displays") if isinstance(data, dict) else data
        monitors = monitors if isinstance(monitors, list) else []
        check("it returns at least one monitor", len(monitors) >= 1, f"{len(monitors)} found")
        if isinstance(data, dict) and monitors:
            check("its 'count' agrees with the array it returned",
                  data.get("count") == len(monitors),
                  f"count={data.get('count')} vs {len(monitors)} entries")
        if monitors:
            check("exactly one monitor is primary",
                  sum(1 for m in monitors if m.get("primary")) == 1)
            check("every monitor has a positive size and a device name",
                  all(m.get("width", 0) > 0 and m.get("height", 0) > 0 and m.get("device")
                      for m in monitors), json.dumps(monitors)[:300])

        # ---- 3. window enumeration, then address one of them
        print("\ngroup 3 — windows")
        r = result_of(server.call("tool_list_windows", {}), "tool_list_windows")
        check("tool_list_windows answers", r["ok"], r["detail"])
        windows = (r.get("data") or {}).get("windows") if r["ok"] else []
        windows = windows or []
        check("it lists at least one window", len(windows) >= 1, f"{len(windows)} found")

        # Prefer the desktop: it exists in every interactive session and has a real element
        # tree. Falling back to "largest window" alone once picked a 1-node IME window,
        # which made the skyshot assertion below meaningless.
        usable = [w for w in windows
                  if w.get("width", 0) > 200 and w.get("height", 0) > 200
                  and w.get("x", 0) > -30000]
        target = next((w for w in usable if w.get("title") == "Program Manager"),
                      usable[0] if usable else None)
        if not target:
            print()
            print("FAILED: no window to address, so the remaining groups were skipped")
            return 1
        hwnd = target["hwnd"]
        print(f"  target window: hwnd={hwnd} {target.get('title')!r}")

        r = result_of(server.call("tool_find_window", {"title": str(target.get("title") or "")[:12]}), "tool_find_window")
        check("tool_find_window answers", r["ok"], r["detail"])
        r = result_of(server.call("tool_get_window_rect", {"hwnd": hwnd}), "tool_get_window_rect")
        check("tool_get_window_rect answers", r["ok"], r["detail"])
        if r["ok"] and r["data"]:
            check("it reports a client size and a DPI block",
                  "client_width" in r["data"] and "dpi" in r["data"],
                  json.dumps(r["data"].get("dpi"))[:200])

        # hwnd=0 means "whatever is in the foreground" — server.py routes that through its own
        # foreground_window() helper. That helper is the path that was broken OFF Windows, so it
        # gets exercised ON Windows too: a guard added there must not break the real call.
        r = result_of(server.call("tool_get_window_rect", {"hwnd": 0}),
                      "tool_get_window_rect")
        check("hwnd=0 falls back to the foreground window instead of failing", r["ok"], r["detail"])

        r = result_of(server.call("tool_skyshot", {"hwnd": hwnd}), "tool_skyshot")
        check("tool_skyshot answers", r["ok"], r["detail"])
        if r["ok"] and r["data"]:
            check("the shot carries a text tree", bool(r["data"].get("text", "").strip()),
                  f"{r['data'].get('node_count')} nodes")

        r = result_of(server.call("tool_find_elements", {"hwnd": hwnd, "role": "window"}), "tool_find_elements")
        check("tool_find_elements answers", r["ok"], r["detail"])
        if r["ok"] and r["data"]:
            print(f"       (matched {r['data'].get('matched')} window-role elements)")

        # ---- 4. hit-test one point, then re-read the element it named
        print("\ngroup 4 — the element path")
        rect = target
        pt = (rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)
        r = result_of(server.call("tool_element_at_point", {"x": pt[0], "y": pt[1]}), "tool_element_at_point")
        check("tool_element_at_point answers", r["ok"], r["detail"])
        ref = (r.get("data") or {}).get("ref") if r["ok"] else None
        if ref:
            r2 = result_of(server.call("tool_read_element", {"ref": ref}), "tool_read_element")
            check("tool_read_element answers for the ref it just returned", r2["ok"], r2["detail"])
        else:
            check("tool_element_at_point returned a ref", False, r["detail"] or "no ref in result")

        # ---- 5. screenshot to a temp file, then clean up
        print("\ngroup 5 — screenshot and clipboard")
        shot = os.path.join(tempfile.gettempdir(), "verify-tools-readonly.png")
        r = result_of(server.call("tool_capture_window",
                                  {"hwnd": hwnd, "save_path": shot, "max_dim": 400}),
                      "tool_capture_window")
        check("tool_capture_window answers", r["ok"], r["detail"])
        check("it wrote the file it was asked for", os.path.exists(shot),
              f"{shot} ({os.path.getsize(shot) if os.path.exists(shot) else 0} bytes)")
        if os.path.exists(shot):
            os.remove(shot)

        # Content is deliberately not printed or stored.
        r = result_of(server.call("tool_clipboard_read", {}), "tool_clipboard_read")
        check("tool_clipboard_read answers", r["ok"], r["detail"])
        if r["ok"] and r["data"]:
            check("it reports a text field and a length",
                  "text" in r["data"] and isinstance(r["data"].get("length"), int),
                  f"length={r['data'].get('length')} (content not shown)")
    finally:
        server.close()

    print()
    if failures:
        print(f"FAILED: {len(failures)} — " + "; ".join(failures))
        return 1
    print("ALL CHECKS PASSED: every read-only tool answered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
