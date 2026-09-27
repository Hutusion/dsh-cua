"""Does Explorer lose its tree on EVERY minimize, the way Chromium does not?

Round-2 review reported a controlled A/B: build the tree while visible (40 nodes, polled until
stable), then minimize -> **8 nodes**, restore -> **40**. That would mean Explorer's degradation is
driven by `IsIconic` itself, whereas Chromium's is driven by *when the tree was built* — so B2
("the variable is when the tree was built, not IsIconic") would hold for Chromium only.

The reviewer minimized a window on the live desktop to get that. This launches its OWN Explorer
window in a fresh temp directory instead:

  * every CabinetWClass window is enumerated BEFORE launching, and only a window that was not
    there before is ever touched;
  * if `explorer.exe <dir>` reuses an existing window instead of creating one, this reports that
    and RETURNS WITHOUT TESTING, rather than minimizing a window the user may be using;
  * the window is closed with WM_CLOSE. `taskkill` is never used — killing explorer.exe would take
    the user's taskbar and desktop with it.

It also measures the state the Chromium finding rests on, which the reviewer did not: minimized
BEFORE anything has queried the window, versus minimized after the tree was built.

Read-only with respect to everything except the Explorer window this script opens and closes.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes as w

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from dsh_cua import uia  # noqa: E402

user32 = ctypes.windll.user32
SW_MINIMIZE = 6
SW_RESTORE = 9
WM_CLOSE = 0x0010
WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


def title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(w.HWND(hwnd), buf, 512)
    return buf.value


def class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(w.HWND(hwnd), buf, 256)
    return buf.value


def explorer_windows() -> set[int]:
    """Top-level windows of the Explorer shell: the folder windows are CabinetWClass."""
    out: set[int] = set()

    def cb(h, _lp):
        if class_of(int(h)) == "CabinetWClass":
            out.add(int(h))
        return True

    user32.EnumWindows.argtypes = [WNDENUMPROC, w.LPARAM]
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def walk(hwnd: int, view: str = "control") -> dict:
    """One tree walk with NO warm-up, so "never queried" stays true."""
    from dsh_cua import uia as u
    obj = u._uia()
    walker = obj.RawViewWalker if view == "raw" else obj.ControlViewWalker
    records: list[dict] = []
    truncated = False
    index = 0

    def visit(el, depth: int) -> None:
        nonlocal index, truncated
        if len(records) >= 4000 or depth > 30:
            truncated = True
            return
        node = u._node_of(el, include_values=False)
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
            if len(records) >= 4000:
                truncated = True
                return
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                return

    try:
        visit(obj.ElementFromHandle(w.HWND(hwnd)), 0)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"nodes": len(records), "truncated": truncated,
            "named": sum(1 for r in records if r["name"].strip()),
            "list_items": sum(1 for r in records if r["role"] == "list_item")}


def stable_walk(hwnd: int, tries: int = 6, settle: float = 0.6) -> dict:
    """Walk until two consecutive readings agree — the reviewer's "polled until stable"."""
    last = None
    for _ in range(tries):
        cur = walk(hwnd)
        if last is not None and cur.get("nodes") == last.get("nodes"):
            return cur
        last = cur
        time.sleep(settle)
    return last or {}


def report(label: str, r: dict) -> dict:
    if "error" in r:
        print(f"  {label:<34} ERROR {r['error'][:60]}")
        return r
    print(f"  {label:<34} nodes={r['nodes']:<5} named={r['named']:<5} "
          f"list_item={r['list_items']:<4} truncated={r['truncated']}")
    return r


def main() -> int:
    existing = explorer_windows()
    print(f"Explorer folder windows already open: {len(existing)}  "
          f"(none of them will be touched)")

    tmp = tempfile.mkdtemp(prefix="dsh-cua-explorer-ab-")
    for i in range(3):
        open(os.path.join(tmp, f"sample-{i}.txt"), "w", encoding="utf-8").write(f"{i}\n")
    fg_before = user32.GetForegroundWindow()

    proc = subprocess.Popen(["explorer.exe", tmp],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    mine = 0
    try:
        deadline = time.time() + 25
        while time.time() < deadline and not mine:
            new = explorer_windows() - existing
            if new:
                mine = max(new)
                break
            time.sleep(0.25)
        if not mine:
            print("\nEXPLORER REUSED AN EXISTING WINDOW INSTEAD OF OPENING ONE.")
            print("NOT TESTED — minimizing a window the user may be using is exactly what this")
            print("probe refuses to do. Run it with the user's folder windows closed, or pass a")
            print("directory that forces a new window.")
            return 2

        print(f"\nmy own window: hwnd={mine} class={class_of(mine)!r} "
              f"title={title_of(mine)[:46]!r}")
        time.sleep(1.5)

        print("\ncontrol view:")
        # A — minimized BEFORE anything has queried it (no warm-up in `walk`)
        user32.ShowWindow(w.HWND(mine), SW_MINIMIZE)
        time.sleep(1.5)
        a = report("A minimized, never queried", walk(mine))
        # B — restored; the tree can now build, and should be stable
        user32.ShowWindow(w.HWND(mine), SW_RESTORE)
        time.sleep(1.5)
        b = report("B restored (stable)", stable_walk(mine))
        b2 = report("B2 restored again (control)", walk(mine))
        # C — minimized AGAIN, now with the tree already built
        user32.ShowWindow(w.HWND(mine), SW_MINIMIZE)
        time.sleep(1.5)
        c = report("C minimized, tree was built", stable_walk(mine))
        # D — restored once more
        user32.ShowWindow(w.HWND(mine), SW_RESTORE)
        time.sleep(1.5)
        d = report("D restored again", walk(mine))

        print("\nraw view (same states, for comparison):")
        rr = {}
        for state, action in (("restored", SW_RESTORE), ("minimized (built)", SW_MINIMIZE)):
            user32.ShowWindow(w.HWND(mine), action)
            time.sleep(1.5)
            rr[state] = report(f"raw {state}", walk(mine, "raw"))

        # The shipped path, which warms up first — the user-visible reading.
        user32.ShowWindow(w.HWND(mine), SW_RESTORE)
        time.sleep(1.5)
        shot_r = uia.skyshot(mine, disable_diff=True)
        print(f"\nshipped skyshot, restored       : node_count={shot_r.get('node_count')} "
              f"minimized={shot_r.get('minimized')}")
        user32.ShowWindow(w.HWND(mine), SW_MINIMIZE)
        time.sleep(1.5)
        shot_m = uia.skyshot(mine, disable_diff=True)
        print(f"shipped skyshot, minimized      : node_count={shot_m.get('node_count')} "
              f"minimized={shot_m.get('minimized')} note={'note' in shot_m}")

        print("\n" + "-" * 74)
        print("VERDICT")
        built, mini = b.get("nodes"), c.get("nodes")
        if built and mini and mini < built * 0.6:
            print(f"  REPRODUCED: Explorer loses its tree on every minimize "
                  f"({built} elements built -> {mini} minimized).")
            print(f"  And minimized-before-any-query was {a.get('nodes')} — "
                  f"{'no better' if a.get('nodes', 0) <= (mini or 0) else 'different'}.")
            print("  So for Explorer the state IS `IsIconic`, not when the tree was built: B2")
            print("  ('the variable is when the tree was built') holds for Chromium only, and the")
            print("  shipped note must stay mechanism-neutral.")
        elif built and mini and mini >= built * 0.6:
            print(f"  NOT REPRODUCED: Explorer kept {mini} of {built} elements when minimized.")
            print("  The round-2 Explorer measurement does not reproduce on this window, so B2")
            print("  may hold more generally than I scoped it — report this as a disagreement.")
        else:
            print(f"  INCONCLUSIVE: built={built} minimized={mini} "
                  f"never-queried={a.get('nodes')}")
        print(f"  raw: restored={rr.get('restored', {}).get('nodes')} "
              f"minimized={rr.get('minimized (built)', {}).get('nodes')}"
              f"   (a raw view that also collapses means the window stops publishing, "
              f"not a view-filter effect)")
        return 0
    finally:
        user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        try:
            if mine:
                user32.ShowWindow(w.HWND(mine), SW_RESTORE)
                time.sleep(0.4)
                user32.PostMessageW(w.HWND(mine), WM_CLOSE, 0, 0)
                time.sleep(1.0)
                gone = not user32.IsWindow(w.HWND(mine))
                print(f"\nclosed my own Explorer window: {gone}"
                      + ("" if gone else "  <-- STILL OPEN, please close it by hand"))
        except Exception as exc:
            print(f"\ncleanup: {type(exc).__name__}: {exc}")
        if fg_before:
            user32.SetForegroundWindow(w.HWND(fg_before))
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
