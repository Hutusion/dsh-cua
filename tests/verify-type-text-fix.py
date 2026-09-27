"""Verify the type_text / _ensure_visible fixes against the audited defects.

Audited defects (audit B):
  1. `bridge.type_text(top_level_hwnd, ...)` posted WM_CHAR into nothing and returned
     ok=True — a silent failure on 5/5 targets.
  2. `bridge.type_text` discarded `_ensure_visible`'s rich result, so the raise was invisible.
  3. `_ensure_visible(child_hwnd)` always reported {"raised": false, "method": "failed"}
     because it compared a ROOT reduction against a child hwnd.

This script asserts the fixed behaviour, including the honest-failure case.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

user32 = ctypes.windll.user32
user32.GetWindowLongW.restype = ctypes.c_int
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR,
                                 wintypes.LPCWSTR]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SendMessageW.restype = ctypes.c_longlong
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM]

WM_GETTEXT, WM_GETTEXTLENGTH = 0x000D, 0x000E
GWL_STYLE, ES_READONLY = -16, 0x0800

FIXTURE = os.path.join(HERE, "accelerator_target.py")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"         {detail}")


def text_of(hwnd: int) -> str:
    n = int(user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0))
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXT, n + 1,
                        ctypes.cast(buf, ctypes.c_void_p).value)
    return buf.value


def children(hwnd: int) -> list[int]:
    """Child controls, using bridge's OWN ctypes callback class.

    `ctypes.WINFUNCTYPE` mints a new class per call, and importing `dsh_cua.bridge` rebinds
    `user32.EnumChildWindows.argtypes` to its own class — so a separately-minted class is
    rejected with "expected WinFunctionType instance instead of WinFunctionType". Set the
    argtypes at call time from bridge's class. (Auditor B hit this same trap.)
    """
    from dsh_cua import bridge
    out: list[int] = []

    def cb(child, _lp):
        out.append(int(child))
        return True

    proto = bridge.EnumWindowsProc
    user32.EnumChildWindows.argtypes = [wintypes.HWND, proto, wintypes.LPARAM]
    user32.EnumChildWindows(wintypes.HWND(hwnd), proto(cb), 0)
    return out


def is_ro(hwnd: int) -> bool:
    return bool(int(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE)) & ES_READONLY)


def main() -> int:
    from dsh_cua import bridge

    # Assert we are testing the EDITED source and not the installed PyPI package. The first
    # run of this script silently imported site-packages/dsh_cua because of a wrong
    # sys.path entry, and only failed later on a missing attribute.
    expect = os.path.normcase(os.path.abspath(
        os.path.join(HERE, "..", "src", "dsh_cua", "bridge.py")))
    got = os.path.normcase(os.path.abspath(bridge.__file__))
    print(f"bridge loaded from: {got}")
    if got != expect:
        print(f"  REFUSING TO RUN: expected {expect}")
        return 2
    if not hasattr(bridge, "_is_text_control"):
        print("  REFUSING TO RUN: edited source not loaded")
        return 2
    print()
    state = os.path.join(os.environ.get("TEMP", "."), "vt-fix-state.json")
    title = f"vt-fix-target {os.getpid()}"
    proc = subprocess.Popen([sys.executable, FIXTURE, "--json", state, "--title", title],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frame = 0
    try:
        for _ in range(120):
            frame = user32.FindWindowW(None, title)
            if frame:
                break
            time.sleep(0.05)
        if not frame:
            print("fixture never appeared")
            return 1
        edits = [c for c in children(frame) if bridge._is_text_control(c)]
        for _ in range(40):
            if edits:
                break
            time.sleep(0.05)
            edits = [c for c in children(frame) if bridge._is_text_control(c)]
        ro = [c for c in edits if is_ro(c)]
        rw = [c for c in edits if not is_ro(c)]
        print(f"fixture frame={frame}  text controls={edits}  writable={rw}  read-only={ro}\n")
        if not rw:
            print("no writable control in fixture — cannot test")
            return 1
        edit = rw[0]
        original_fg = int(user32.GetForegroundWindow())
        print(f"  EDIT starts as {text_of(edit)!r}\n")

        # 1. The audited silent failure: a TOP-LEVEL hwnd must now resolve and land.
        before = text_of(edit)
        r = bridge.type_text(frame, "AAA")
        after = text_of(edit)
        check("1. type_text(TOP-LEVEL hwnd) resolves the control and LANDS",
              r.get("resolved_by") is not None and r.get("target_hwnd") == edit
              and after != before and r.get("effect_verified") is True,
              f"resolved_by={r.get('resolved_by')!r} target_hwnd={r.get('target_hwnd')} "
              f"(edit={edit}) effect_verified={r.get('effect_verified')} "
              f"text {before!r} -> {after!r}")

        # 2. The receipt must now carry the raise + foreground change.
        check("2. receipt carries raise + foreground_changed",
              isinstance(r.get("raise"), dict) and "raised" in r["raise"]
              and "foreground_changed" in r,
              f"raise={r.get('raise')} foreground_changed={r.get('foreground_changed')}")

        # 3. An explicit control target still works.
        before = text_of(edit)
        r2 = bridge.type_text(edit, "BBB")
        check("3. type_text(control hwnd) still lands",
              text_of(edit) != before and r2.get("effect_verified") is True,
              f"resolved_by={r2.get('resolved_by')!r} text {before!r} -> {text_of(edit)!r}")

        # 4. A read-only control must be reported as a FAILURE, not a silent success.
        #    Needs selfcontained_target (it owns an ES_READONLY edit); accelerator_target
        #    has only one writable edit, which is why the first run skipped this.
        sys.path.insert(0, HERE)
        import selfcontained_target as sct
        tgt = sct.Target(x=80, y=80, width=520, height=420)
        try:
            tgt.start()
            r3 = bridge.type_text(tgt.h_ro, "ZZZ")
            check("4. read-only control reports no-effect instead of ok",
                  r3.get("ok") is False and r3.get("reason") == "no-effect"
                  and r3.get("effect_verified") is False,
                  f"ok={r3.get('ok')} reason={r3.get('reason')!r} "
                  f"effect_verified={r3.get('effect_verified')} "
                  f"err={str(r3.get('error'))[:80]!r}")
        finally:
            tgt.close()

        # 5. _ensure_visible must no longer under-report a CHILD target. Measured from a
        #    BACKGROUND state, so it has to do real work rather than report
        #    "already-foreground" because an earlier test left the fixture in front.
        #    Handing the foreground back needs the project's own workaround: a bare
        #    SetForegroundWindow from this process is refused by the foreground lock, which
        #    is what made the first attempt at this test vacuous (started_background=False).
        bridge._ensure_visible(original_fg)
        time.sleep(0.4)
        was_bg = bridge.foreground_root() != bridge._root(edit)
        r4 = bridge._ensure_visible(edit)
        check("5. _ensure_visible(child) from BACKGROUND reports raised=True",
              was_bg and r4.get("raised") is True and r4.get("method") != "failed",
              f"started_background={was_bg} -> {r4}")

        # 6. The MCP tool must carry the receipt through (was: only success+text_length).
        from dsh_cua import server
        r5 = server.tool_type_text(text="CCC", hwnd=frame)
        check("6. tool_type_text carries effect_verified through",
              "effect_verified" in r5 and r5.get("effect_verified") is True,
              f"keys={sorted(r5.keys())} effect_verified={r5.get('effect_verified')}")
    finally:
        if frame:
            user32.PostMessageW(wintypes.HWND(frame), 0x0010, 0, 0)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print("\n" + "=" * 74)
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} passed" + (f"  FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
