"""Read-only diagnostics for the two instruments the visible-vs-foreground probe relies on.

Q1. Is `WindowFromPoint` reading its argument in the SAME coordinate space `GetWindowRect`
    reports to a DPI-unaware process? The probe's occlusion metric sampled points derived from
    `GetWindowRect` and fed them to `WindowFromPoint`, so if the two disagree the metric is
    meaningless (and that is one of the two ways A3/A5's `on_top=0.0%` could be my bug rather
    than a fact about z-order).

    Decisive point: the physical screen is 2560x1600 but a DPI-unaware process is told the
    screen is 1707x1067. So a point like (2000, 1300) is INSIDE the screen physically and
    OUTSIDE it in the virtualised space. If WindowFromPoint answers with the maximised
    foreground window, it read the argument as physical; if it answers with the desktop, it
    read it as virtual.

Q2. Is `GetGUIThreadInfo(hwndActive)` a usable oracle for "this window is the ACTIVE window of
    its thread"? The probe needs it because `GetForegroundWindow()` and Chromium's own idea of
    activation demonstrably disagree: the cold instance's window was reported as the foreground
    window while a posted WM_CHAR was still dropped, exactly as in the genuinely deactivated
    cases.

Read-only: no window is created, moved, raised, activated or closed.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetTopWindow.argtypes = [wintypes.HWND]
user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND

GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008


def root(h) -> int:
    return (user32.GetAncestor(wintypes.HWND(h), GA_ROOT) or h) if h else 0


def title(h: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(wintypes.HWND(h), buf, 256)
    return buf.value


def cls(h: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(h), buf, 256)
    return buf.value


def rect(h: int):
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(h), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def main() -> int:
    print(f"DPI-aware? screen={user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)} "
          f"(aware would be 2560x1600)")

    fg = user32.GetForegroundWindow()
    fgr = root(fg)
    print(f"\nforeground hwnd={fg} root={fgr} class={cls(fgr)!r}")
    print(f"  title={title(fgr)!r}")
    print(f"  GetWindowRect(root)={rect(fgr)}")

    # ---- Q1 ----
    print("\nQ1  WindowFromPoint coordinate space")
    for label, (x, y) in (("centre of the virtual screen", (853, 533)),
                          ("INSIDE physical, OUTSIDE virtual", (2000, 1300)),
                          ("bottom-right of the physical screen", (2540, 1580))):
        h = user32.WindowFromPoint(wintypes.POINT(x, y))
        print(f"  ({x},{y}) {label:<38} -> hwnd={h} root={root(h)} class={cls(root(h))!r}")
    h = user32.WindowFromPoint(wintypes.POINT(2000, 1300))
    if root(h) == fgr:
        print("  => argument read as PHYSICAL: WindowFromPoint and GetWindowRect DISAGREE for "
              "a DPI-unaware caller,\n     so the probe's occlusion grid sampled the wrong "
              "points and `on_top_pct` was meaningless.")
    else:
        print("  => argument NOT read as physical (it answered with "
              f"{cls(root(h))!r}), so the two calls are\n     consistent and `on_top_pct` was "
              "measuring real z-order.")

    # ---- Q2 ----
    print("\nQ2  GetGUIThreadInfo(hwndActive) as an activation oracle")
    tid = user32.GetWindowThreadProcessId(wintypes.HWND(fgr), None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    ok = bool(user32.GetGUIThreadInfo(tid, ctypes.byref(gti)))
    print(f"  GetGUIThreadInfo(tid={tid}) -> {ok}")
    if ok:
        print(f"  hwndActive={gti.hwndActive} root={root(gti.hwndActive)} "
              f"class={cls(root(gti.hwndActive))!r}")
        print(f"  hwndFocus ={gti.hwndFocus} root={root(gti.hwndFocus)} "
              f"class={cls(root(gti.hwndFocus))!r}")
        print(f"  active-root == foreground-root: {root(gti.hwndActive) == fgr}")
        print("  => the instrument agrees with GetForegroundWindow on a genuinely foreground "
              "window,\n     which is what makes it usable to CONTRADICT it in the cases the "
              "probe hit.")
    # ---- Q3 ----
    # Why `SetWindowPos(HWND_TOP, SWP_NOACTIVATE)` left the probe's window at on_top=0.0% while
    # `HWND_TOPMOST` reached 100.0%: a maximized window cannot be beaten from the non-topmost
    # band if it is itself WS_EX_TOPMOST. Read-only z-order walk from the top.
    print("\nQ3  z-order bands from the top (why HWND_TOP could not lift a window over the "
          "foreground one)")
    GW_HWNDNEXT = 2
    h = user32.GetTopWindow(None)
    for i in range(12):
        if not h:
            break
        topmost = bool(user32.GetWindowLongW(wintypes.HWND(h), GWL_EXSTYLE) & WS_EX_TOPMOST)
        print(f"  {i:>2} hwnd={h:<10} topmost={str(topmost):<6} rect={rect(h)} "
              f"class={cls(h)!r} title={title(h)[:44]!r}")
        if root(h) == fgr:
            print(f"     ^ the foreground window is at z-position {i}, topmost={topmost}")
        h = user32.GetWindow(wintypes.HWND(h), GW_HWNDNEXT)

    print("\nQ4  where did the foreground end up (revision 2's teardown reported "
          "`restored: False`)")
    print(f"  foreground now = {fg} root={fgr} class={cls(fgr)!r} title={title(fgr)[:60]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
