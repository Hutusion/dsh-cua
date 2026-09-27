"""Why does `skyshot` return chrome-only for some minimized Chromium windows?

Measured on the user's Edge window (hwnd 133162, 16 tabs, minimized): `skyshot` returns 34
nodes at the default and 68 with `include_offscreen=True`, and **zero `document` nodes** at
either setting — browser chrome only, no page. But another minimized Edge window (hwnd
659026, 6 tabs) returns a named page document, and a window this test launched and then
minimized keeps its page document too. So minimization alone is not the trigger.

Hypothesis: Chromium builds the ACTIVE TAB's accessibility tree only for a visible window.
A window that is minimized before anyone has queried the active tab never gets that tree
built, and `uia.warm_up` cannot force it while the window is minimized — so `skyshot`
reports chrome only. A window minimized AFTER its tree was built keeps reporting it.

Test: launch a NORMAL Edge window, minimize it BEFORE any UIA query (so the tree is never
built), measure, then restore and measure again. If the hypothesis holds, the minimized
reading has no document and the restored reading does.

Read-only with respect to everything except the window this script launches and kills.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

# The harness's filename has dashes, so it cannot be imported by name.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "verify_key_routing",
    os.path.join(HERE, "verify-key-routing.py"))
kr = importlib.util.module_from_spec(_spec)
sys.modules["verify_key_routing"] = kr
_spec.loader.exec_module(kr)

user32 = ctypes.windll.user32
EDGE = next((p for p in kr.EDGE_PATHS if os.path.exists(p)), "")


def main() -> int:
    if not EDGE:
        print("no Edge binary")
        return 1
    marker = f"KRW-{os.getpid()}"
    workdir = tempfile.mkdtemp(prefix="dsh-cua-krw-")
    page = os.path.join(workdir, "kr.html")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(f"""<!doctype html><meta charset="utf-8"><title>{marker} PAGE</title>
<body style="font:16px sans-serif"><h1>{marker} heading</h1>
<input id="t" style="width:400px"><p>page body text for the tree</p></body>""")

    fg_original = kr.foreground_root()
    proc = subprocess.Popen(
        [EDGE, f"--user-data-dir={os.path.join(workdir, 'profile')}", "--no-first-run",
         "--no-default-browser-check", "--disable-sync", page],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    hwnd = 0
    try:
        deadline = time.time() + 45
        while time.time() < deadline and not hwnd:
            for w in kr.visible_titled_windows():
                if w["exe"].lower() == "msedge.exe" and w["title"].startswith(marker):
                    hwnd = w["hwnd"]
                    break
            time.sleep(0.1)
        if not hwnd:
            print("window never appeared")
            return 1

        def shot(label: str, wait: float = 1.5) -> dict:
            time.sleep(wait)
            recs, _ = kr._walk_view(hwnd, "control", 4000, 30, False)
            docs = [r["name"] for r in recs if r["role"] == "document"]
            named = [r["name"] for r in recs if r["name"].strip()]
            print(f"  {label:<34} iconic={str(bool(user32.IsIconic(hwnd))):<6} "
                  f"nodes={len(recs):<5} documents={len(docs):<3} named={len(named)}")
            return {"nodes": len(recs), "documents": docs}

        print(f"hwnd {hwnd}  {marker}\n")
        # Minimize FIRST, before any query — so Chromium has never been asked for this
        # tab's tree while the window was visible.
        user32.ShowWindow(wintypes.HWND(hwnd), kr.SW_MINIMIZE)
        a = shot("minimized BEFORE any query")
        user32.ShowWindow(wintypes.HWND(hwnd), kr.SW_RESTORE)
        b = shot("restored (tree can now build)")
        user32.ShowWindow(wintypes.HWND(hwnd), kr.SW_MINIMIZE)
        c = shot("minimized again, after it was built")

        print()
        print(f"  minimized-before-query has a document: {bool(a['documents'])}")
        print(f"  restored has a document:               {bool(b['documents'])}")
        print(f"  minimized-after-build has a document:  {bool(c['documents'])}")
        if not a["documents"] and b["documents"]:
            print("  => HYPOTHESIS HOLDS: the page tree is built only for a visible window, "
                  "and cannot be\n     built while minimized — so `skyshot` is chrome-only "
                  "until the window is restored once.")
        else:
            print("  => hypothesis NOT confirmed by this run")
        return 0
    finally:
        try:
            pid = wintypes.DWORD()
            if hwnd:
                user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            if pid.value:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid.value)],
                               capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        if fg_original:
            user32.SetForegroundWindow(wintypes.HWND(fg_original))
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
