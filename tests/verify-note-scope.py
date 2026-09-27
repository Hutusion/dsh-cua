"""Is the `minimized` note still an over-warning on an application that keeps its tree?

Round-2 review measured Notepad: minimized with a COMPLETE tree (35 elements, same as restored)
while the note still fired and explained it with Chromium's mechanism. The note has since been
rewritten to be mechanism-neutral, but it still fires for every minimized window — so the honest
question is whether that remaining behaviour is a warning or a lie.

This launches its OWN Notepad, minimizes it before anything reads it, and compares the trees. It
never touches the user's windows. A separate question — Explorer — is deliberately NOT tested
here: that would mean minimizing the user's shell window.

Read-only with respect to everything except the Notepad this script launches and kills.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as w

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


def tops() -> list[int]:
    out: list[int] = []

    def cb(h, _lp):
        if user32.IsWindowVisible(h) and title_of(h):
            out.append(int(h))
        return True

    user32.EnumWindows.argtypes = [WNDENUMPROC, w.LPARAM]
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def nodes(r: dict) -> int:
    return int(r.get("node_count") or 0)


def main() -> int:
    before = set(tops())
    proc = subprocess.Popen(["notepad.exe"],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    target = 0
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not target:
            for h in tops():
                if h not in before and exe_of(h).lower().startswith("notepad"):
                    target = uia._root_hwnd(h)
                    break
            time.sleep(0.2)
        if not target:
            print("Notepad window never appeared — NOT MEASURED")
            return 1
        print(f"own Notepad hwnd={target} class={class_of(target)!r} "
              f"title={title_of(target)[:40]!r}\n")

        # Minimize BEFORE anything reads it, which is the state the round-2 finding used.
        user32.ShowWindow(w.HWND(target), SW_MINIMIZE)
        time.sleep(1.5)
        mini = uia.skyshot(target, disable_diff=True)
        print(f"minimized (never queried)")
        print(f"  minimized flag : {mini.get('minimized')}")
        print(f"  note present   : {'note' in mini}")
        print(f"  node_count     : {nodes(mini)}")

        user32.ShowWindow(w.HWND(target), SW_RESTORE)
        time.sleep(1.5)
        rest = uia.skyshot(target, disable_diff=True)
        print(f"\nrestored")
        print(f"  minimized flag : {rest.get('minimized')}")
        print(f"  note present   : {'note' in rest}")
        print(f"  node_count     : {nodes(rest)}")

        same = nodes(mini) == nodes(rest)
        print(f"\nVERDICT")
        if same and mini.get("minimized") and "note" in mini:
            print(f"  The note fires on an application whose tree is COMPLETE "
                  f"({nodes(mini)} == {nodes(rest)} elements), so it is not a per-app claim.")
            print("  That is the accepted trade, not a fix: the note states that a small tree is")
            print("  not proof of an empty window, which is true here too — it is a conditional")
            print("  caution, not a diagnosis. Distinguishing the two cases from the tree alone")
            print("  was not possible: this window and the chrome-only Edge window have almost")
            print("  the same node count.")
        elif not same:
            print(f"  Notepad's tree DID change with state ({nodes(mini)} minimized vs "
                  f"{nodes(rest)} restored) — the round-2 finding does not reproduce here.")
        else:
            print(f"  note absent on a minimized window: {mini.get('minimized')}")
        return 0
    finally:
        try:
            if target:
                user32.PostMessageW(w.HWND(target), 0x0010, 0, 0)  # WM_CLOSE
                time.sleep(0.4)
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
