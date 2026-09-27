"""Is B3's reason 1 real, or was it an artifact of ZCode being MINIMIZED?

B3 is the entire justification for leaving shipped `uia._walk` on `ControlViewWalker`: reason 1 is
that on ZCode the control view published 77 NAMED elements while the raw view published only 26, so
"neither view is a superset" and switching walkers would lose readable content.

But both readings of ZCode so far — mine and the reviewer's — were taken with ZCode **minimized**
(`iconic=True`), and by now two independent measurements have established that a minimized window
does not expose its full tree (Explorer 44 -> 8; Edge chrome-only). So reason 1 is confounded by
exactly the variable this session has been chasing. If ZCode's raw view recovers when restored, the
justification for leaving `_walk` alone collapses, and the shipped decision is wrong.

This restores ZCode's window, measures both views until the tree is stable, then puts the window
back the way it was found — including re-minimizing it and restoring the previous foreground.

Read-only apart from ZCode's window state, which is restored.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes as w
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from dsh_cua import uia  # noqa: E402

user32 = ctypes.windll.user32
SW_MINIMIZE = 6
SW_RESTORE = 9
WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


def title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(w.HWND(hwnd), buf, 512)
    return buf.value


def class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(w.HWND(hwnd), buf, 256)
    return buf.value


def exe_of(hwnd: int) -> str:
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(w.HWND(hwnd), ctypes.byref(pid))
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid.value)
    if not h:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(1024)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        k.CloseHandle(h)
    return "?"


def walk(hwnd: int, view: str) -> dict:
    obj = uia._uia()
    walker = obj.RawViewWalker if view == "raw" else obj.ControlViewWalker
    records: list[dict] = []
    truncated = False
    index = 0

    def visit(el, depth: int) -> None:
        nonlocal index, truncated
        if len(records) >= 20000 or depth > 40:
            truncated = True
            return
        node = uia._node_of(el, include_values=False)
        if node["offscreen"] and depth > 0:
            return
        records.append({"role": node["role"], "name": node["name"]})
        index += 1
        try:
            child = walker.GetFirstChildElement(el)
        except Exception:
            return
        while child is not None:
            visit(child, depth + 1)
            if len(records) >= 20000:
                truncated = True
                return
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                return

    visit(obj.ElementFromHandle(w.HWND(hwnd)), 0)
    named = [r["name"] for r in records if r["name"].strip()]
    return {"nodes": len(records), "truncated": truncated,
            "named": named, "named_set": set(named),
            "roles": Counter(r["role"] for r in records)}


def pair(hwnd: int) -> dict:
    c = walk(hwnd, "control")
    r = walk(hwnd, "raw")
    return {"control": c, "raw": r, "hidden": r["named_set"] - c["named_set"],
            "lost_if_raw": c["named_set"] - r["named_set"]}


def stable_pair(hwnd: int, tries: int = 6, settle: float = 0.8) -> dict:
    """Measure until the control-view node count stops moving."""
    last = None
    for _ in range(tries):
        cur = pair(hwnd)
        n = cur["control"]["nodes"], cur["raw"]["nodes"]
        if last == n:
            return cur
        last = n
        time.sleep(settle)
    return cur


def show(label: str, p: dict) -> None:
    c, r = p["control"], p["raw"]
    print(f"  {label:<30} control={c['nodes']:<5} named={len(c['named']):<5}"
          f" | raw={r['nodes']:<5} named={len(r['named']):<5}"
          f" | named hidden by control={len(p['hidden']):<4}"
          f" named LOST if raw={len(p['lost_if_raw'])}")


def main() -> int:
    targets = []
    def cb(h, _lp):
        if user32.IsWindowVisible(h) and exe_of(int(h)).lower() == "zcode.exe":
            targets.append(int(h))
        return True

    user32.EnumWindows.argtypes = [WNDENUMPROC, w.LPARAM]
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not targets:
        print("no ZCode window is open — B3 cannot be tested this way right now")
        return 2

    hwnd = uia._root_hwnd(targets[0])
    was_iconic = bool(user32.IsIconic(w.HWND(hwnd)))
    fg_before = user32.GetForegroundWindow()
    print(f"ZCode hwnd={hwnd} class={class_of(hwnd)!r} title={title_of(hwnd)[:40]!r}")
    print(f"found minimized: {was_iconic}   (this is the confound being removed)\n")

    try:
        if was_iconic:
            user32.ShowWindow(w.HWND(hwnd), SW_RESTORE)
            print("restored it to measure a window whose tree can actually build")
            time.sleep(2.5)

        print("RESTORED (the state B3's reason 1 should have been measured in):")
        rest = stable_pair(hwnd)
        show("restored, stable", rest)

        # Same window, same instant, minimized — so the contribution of `IsIconic` is visible.
        user32.ShowWindow(w.HWND(hwnd), SW_MINIMIZE)
        time.sleep(2.0)
        print("\nMINIMIZED (the state both earlier readings were taken in):")
        mini = stable_pair(hwnd, tries=3, settle=1.0)
        show("minimized, stable", mini)

        user32.ShowWindow(w.HWND(hwnd), SW_RESTORE)
        time.sleep(2.5)
        print("\nRESTORED AGAIN (to confirm it is reproducible, not a one-off):")
        rest2 = stable_pair(hwnd, tries=3, settle=1.0)
        show("restored again", rest2)

        print("\n" + "-" * 74)
        print("VERDICT — is 'neither view is a superset' real, or a minimization artifact?")
        rc, rr = len(rest["control"]["named"]), len(rest["raw"]["named"])
        mc, mr = len(mini["control"]["named"]), len(mini["raw"]["named"])
        if rr < rc:
            print(f"  reason 1 SURVIVES: restored, raw named ({rr}) is still < control named "
                  f"({rc}), by {rc - rr}.")
            print(f"  Switching to RawViewWalker would lose {len(rest['lost_if_raw'])} named "
                  f"elements on a window that is not minimized, so leaving `_walk` alone is")
            print("  justified on a measurement that is NOT confounded by minimization.")
        elif rr >= rc:
            verdict = ("EQUAL" if rr == rc else f"GREATER ({rr} > {rc})")
            print(f"  reason 1 DOES NOT SURVIVE: restored, raw named is {verdict}.")
            print(f"  The earlier {mc} vs {mr} reading was taken while the window was minimized,")
            print("  and raw recovers to at least control when the tree can build. The claim")
            print("  'neither view is a superset' is then an artifact, and the decision to keep")
            print("  ControlViewWalker needs a different justification or must be revisited.")
        print(f"\n  for reference, minimized: control {mc} named vs raw {mr} named")
        print(f"  lost-if-raw, restored: {sorted(rest['lost_if_raw'])[:6]}")
        print(f"  hidden-by-control, restored: {len(rest['hidden'])} named, e.g. "
              f"{sorted(rest['hidden'])[:5]}")
        print(f"  roles, restored control: {dict(rest['control']['roles'].most_common(6))}")
        return 0
    finally:
        # Put the window back exactly the way it was found.
        try:
            user32.ShowWindow(w.HWND(hwnd), SW_MINIMIZE if was_iconic else SW_RESTORE)
            time.sleep(0.5)
            now_iconic = bool(user32.IsIconic(w.HWND(hwnd)))
            print(f"\nrestored the window state: was_minimized={was_iconic} "
                  f"is_minimized_now={now_iconic} "
                  f"{'OK' if now_iconic == was_iconic else '<-- NOT RESTORED'}")
        except Exception as exc:
            print(f"\nstate restore failed: {type(exc).__name__}: {exc}")
        if fg_before:
            user32.SetForegroundWindow(w.HWND(fg_before))


if __name__ == "__main__":
    raise SystemExit(main())
