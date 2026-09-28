"""Does Chromium build its a11y tree for a VISIBLE but NOT FOREGROUND window?

The blank this fills. Two earlier probes compared "minimized, never queried" against
"restored" — `verify-minimized-tree.py` and `verify-key-routing.py`'s phase D — and BOTH
restored with `SW_RESTORE`, which ACTIVATES the window. So in every tree reading on record,
VISIBLE and FOREGROUND moved together, and the two explanations were never separated:

    (a) Chromium needs the window VISIBLE / shown   -> a second display helps
    (b) Chromium needs the window FOREGROUND/ACTIVE -> a second display helps nothing

That is not a cosmetic gap. A virtual display can make a window visible without making it the
active window; nothing can make it active without taking the focus off the human's screen.

WHAT REVISION 1 GOT WRONG (kept here because the corrections are the design):

  1. Its verdict function printed "while it was NEVER the foreground window" for a reading
     whose own recorded state said `foreground=True`. The state was recorded precisely so a
     label could not lie, and the verdict then ignored it. The verdict is now GATED on the
     recorded state and refuses to conclude from a reading that did not reach the state it
     claims.
  2. `GetForegroundWindow()` is NOT a usable definition of "foreground" here. Measured:
     revision 1's cold instance was reported as the foreground window (`GetForegroundWindow()
     == hwnd`) while a posted WM_CHAR was still dropped, exactly as in the deactivated cases —
     so the API bit and Chromium's own idea of activation disagree. `GetGUIThreadInfo`
     `hwndActive` is the oracle used instead, and its agreement with `GetForegroundWindow` on a
     genuinely foreground window was verified first (`tests/diagnose-instruments.py`).
  3. `on_top_pct` sampled `WindowFromPoint` at points derived from `GetWindowRect` while the
     process was DPI-unaware (screen reported as 1707x1067, physically 2560x1600), so a
     coordinate-space mismatch was a live possibility. Tested and REFUTED: `WindowFromPoint`
     answers `None` for a point inside the physical screen but outside the virtual one, so the
     two calls agree and the metric was real. The process is now per-monitor DPI aware anyway,
     so the log's coordinates are physical and comparable with the rest of the session. The
     reading it produced was true for a different reason: the foreground window is itself a
     maximized `Chrome_WidgetWin_1` covering the whole screen.
  4. `HWND_TOP` + `SWP_NOACTIVATE` did not lift the probe's window above that maximized window
     (on_top stayed 0.0%), so revision 1 never actually measured an unoccluded window. The
     raise now escalates to `HWND_TOPMOST` and REPORTS which step achieved the state.

WHAT REVISION 2 GOT WRONG (found by reading its own output):

  5. The gating fix worked: it marked A5 UNINFORMATIVE instead of asserting. But the warm
     instance's yield failed (`yielded ... False`, window still ACTIVE), so its three
     "deactivated" readings never happened — because THE YIELD CAN ONLY BE SPENT ONCE PER
     PROCESS, and the cold instance had already spent it. Each instance is therefore its own
     process run when the instances matter separately (`--only=cold` / `--only=warm`).
  6. Killing the window that holds the foreground makes Windows activate some other window, and
     the teardown yielded only AFTER killing — so the run left the user's desktop focused on an
     unrelated window (`restored to 2953482: False (now 5901614)`). The teardown now yields
     BEFORE killing, and reports whether it worked.
  7. It raised before its one fresh-build reading, so "shown" and "unoccluded" changed together
     and the decisive reading could not say which one built the page. The covered-shown reading
     is now taken FIRST, before any raise.

INSTRUMENTS. Every reading records what the OS says the state IS — IsIconic, IsWindowVisible,
GetForegroundWindow, GetGUIThreadInfo(hwndActive/hwndFocus), and the share of the window's area
whose TOPMOST window is the window itself, sampled with WindowFromPoint — never what the probe
intended it to be. Each read and each key probe runs inside `ForegroundWatch`, because a UIA
walk that activated the window would make its own "not active" label a lie.

The key probe's oracle is the WINDOW TITLE, not UIA: in the state under test the tree may be
absent, so a UIA oracle could not tell "the key never reached the page" apart from "the page has
no tree to read the outcome out of". The warm FOREGROUND probe doubles as the oracle's positive
control; if it fails, the deactivated results are reported as inconclusive rather than negative.

ORDER: the COLD instance runs FIRST. It has to yield the foreground before the COLD window is
minimized (revision 1 yielded after, and the call failed), and my process only has the right to
yield while it started the process that currently holds the foreground — which is true right
after a launch. Both instances' launches activate their own window, so the warm instance still
gets its free foreground reading afterwards.

Read-only with respect to everything except the two Edge instances this script launches and
kills. It never activates a window: every show and every raise uses SWP_NOACTIVATE. The previous
foreground is restored in `finally` and the restoration is reported.
"""
from __future__ import annotations

import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

# Per-monitor DPI awareness, before ANY window call, so that GetSystemMetrics, GetWindowRect and
# WindowFromPoint all speak physical pixels. (They already agreed with each other unaware —
# `diagnose-instruments.py` refuted the suspicion that they did not — but this makes the printed
# coordinates comparable with the rest of the session, where the screen is 2560x1600.)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

# The harness's filename has dashes, so it cannot be imported by name.
_spec = importlib.util.spec_from_file_location(
    "verify_key_routing", os.path.join(HERE, "verify-key-routing.py"))
kr = importlib.util.module_from_spec(_spec)
sys.modules["verify_key_routing"] = kr
_spec.loader.exec_module(kr)

user32 = ctypes.windll.user32


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


# Restypes are declared, not inferred: WindowFromPoint, GetAncestor and hwndActive return
# HANDLES, and a default c_int restype truncates them to 32 bits and would compare unequal.
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
SW_MINIMIZE = 6
SW_SHOWNOACTIVATE = 4
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

SETTLE = 1.5
MAX_NODES = 4000
MAX_DEPTH = 30


def root_of(h) -> int:
    return (user32.GetAncestor(wintypes.HWND(h), GA_ROOT) or h) if h else 0


# --------------------------------------------------------------------------------------------
# what the OS says the state IS
# --------------------------------------------------------------------------------------------
def on_top_pct(hwnd: int, rect) -> float | None:
    """Share of a 12x12 grid over the window's rect whose TOPMOST window is this window.

    Occlusion is the other confound: "visible" (`IsWindowVisible`) is not "uncovered", and a
    covered window is a plausible reason for a tree to be withheld. `WindowFromPoint` is the
    OS's own answer to "what is on top here". None when the rect is off-screen (a minimized
    window sits at -32000), where the question has no meaning.
    """
    if rect.left <= -30000 or rect.top <= -30000:
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    n = 12
    hit = 0
    for i in range(n):
        for j in range(n):
            x = rect.left + (rect.right - rect.left) * (2 * i + 1) // (2 * n)
            y = rect.top + (rect.bottom - rect.top) * (2 * j + 1) // (2 * n)
            if root_of(user32.WindowFromPoint(wintypes.POINT(x, y))) == hwnd:
                hit += 1
    return round(100.0 * hit / (n * n), 1)


def state_of(hwnd: int) -> dict:
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    fg = kr.foreground_root()
    tid = user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    got = bool(user32.GetGUIThreadInfo(tid, ctypes.byref(gti)))
    return {"iconic": bool(user32.IsIconic(wintypes.HWND(hwnd))),
            "visible": bool(user32.IsWindowVisible(wintypes.HWND(hwnd))),
            "fg": fg, "is_fg": fg == hwnd,
            # The oracle that matters: what the WINDOW'S OWN THREAD calls active. Chromium drops
            # a posted key when its window is not active even though GetForegroundWindow may
            # still name it, so `is_fg` alone would mislabel exactly the readings under test.
            "gtinfo": got,
            "active": bool(got and root_of(gti.hwndActive) == hwnd),
            "focused": bool(got and root_of(gti.hwndFocus) == hwnd),
            "topmost": bool(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
                            & WS_EX_TOPMOST),
            "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
            "on_top_pct": on_top_pct(hwnd, r)}


def _summ(records: list[dict]) -> dict:
    return {"nodes": len(records),
            "named": sum(1 for r in records if r["name"].strip()),
            "documents": [r["name"] for r in records if r["role"] == "document"]}


def read(hwnd: int, label: str) -> dict:
    """One tree reading, with the state the OS reports and proof the walk took no focus."""
    print(f"\n  [{label}]")
    time.sleep(SETTLE)
    with kr.ForegroundWatch() as fw:
        before = state_of(hwnd)
        ctl, ctl_trunc = kr._walk_view(hwnd, "control", MAX_NODES, MAX_DEPTH, False)
        raw, raw_trunc = kr._walk_view(hwnd, "raw", MAX_NODES, MAX_DEPTH, False)
        after = state_of(hwnd)
    watch = fw.report()
    c, r = _summ(ctl), _summ(raw)
    print(f"    iconic={str(before['iconic']):<5} visible={str(before['visible']):<5} "
          f"ACTIVE={str(before['active']):<5} fg={str(before['is_fg']):<5} "
          f"topmost={str(before['topmost']):<5} on_top={before['on_top_pct']}%  "
          f"rect={before['rect']}")
    print(f"    control: nodes={c['nodes']:<5} named={c['named']:<5} "
          f"documents={len(c['documents'])}   raw: nodes={r['nodes']:<5} "
          f"named={r['named']:<5} documents={len(r['documents'])}"
          + (f"   TRUNCATED ctl={ctl_trunc} raw={raw_trunc}"
             if (ctl_trunc or raw_trunc) else ""))
    print(f"    control-view document names: {c['documents']!r}")
    if r["documents"] != c["documents"]:
        print(f"    raw-view document names    : {r['documents']!r}")
    print(f"    foreground during the walk : {watch['samples']} samples, "
          f"distinct={watch['distinct']}, CHANGED={watch['changed']}")
    if before["is_fg"] != before["active"]:
        print(f"    NOTE: GetForegroundWindow and the window's own hwndActive DISAGREE here "
              f"(is_fg={before['is_fg']} active={before['active']})")
    if before != after:
        print(f"    NOTE: the state moved during the walk (iconic {before['iconic']}->"
              f"{after['iconic']}, active {before['active']}->{after['active']})")
    return {"label": label, "state": before, "state_after": after, "watch": watch,
            "control": c, "raw": r}


# --------------------------------------------------------------------------------------------
# the one key probe, with a UIA-independent oracle
# --------------------------------------------------------------------------------------------
def key_probe(hwnd: int, label: str) -> dict:
    """Post one WM_CHAR '5' and read the WINDOW TITLE as the oracle.

    Independent of UIA on purpose: in the state under test the page tree may be absent, so a UIA
    oracle could not tell "the character never reached the page" apart from "the page has no tree
    to read the outcome out of". The page's own script writes the outcome into `document.title`,
    which the browser copies to the window title.
    """
    st = state_of(hwnd)
    before = kr.window_title(hwnd)
    with kr.ForegroundWatch() as fw:
        accepted = kr._post(hwnd, kr.WM_CHAR, ord("5"), 0)
        time.sleep(1.2)
    after = kr.window_title(hwnd)
    watch = fw.report()
    landed = before != after
    print(f"    POST WM_CHAR '5'  accepted={accepted}  title="
          f"{'CHANGED' if landed else 'NO CHANGE'}  ACTIVE={st['active']} "
          f"is_fg={st['is_fg']}  foreground_CHANGED={watch['changed']} "
          f"({watch['samples']} samples)")
    if landed:
        print(f"      before={before!r}")
        print(f"      after ={after!r}")
    return {"label": label, "accepted": accepted, "before": before, "after": after,
            "landed": landed, "active": st["active"], "is_fg": st["is_fg"],
            "fg_changed": watch["changed"]}


# --------------------------------------------------------------------------------------------
# window manipulation that never activates
# --------------------------------------------------------------------------------------------
def corner_rect() -> tuple[int, int, int, int]:
    """A small rect in the bottom-right, so raising the window intrudes as little as possible."""
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    cw, ch = 900, 560
    return max(0, w - cw - 40), max(0, h - ch - 100), cw, ch


def place(hwnd: int) -> None:
    """Move the window to the corner WITHOUT activating or restacking it.

    SWP_NOZORDER with hWndInsertAfter=HWND_TOP means the insert-after is ignored, so this does
    not raise the window. Revision 1 called this while the cold window was still minimized and
    the position did not survive the show, so it now runs after the window is on screen.
    """
    x, y, w, h = corner_rect()
    user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOP), x, y, w, h,
                        SWP_NOACTIVATE | SWP_NOZORDER)
    time.sleep(0.4)


def raise_unoccluded(hwnd: int) -> list[dict]:
    """Get the window to the top of the z-order WITHOUT activating it, escalating as needed.

    SWP_NOACTIVATE is the whole mechanism: it is what makes "shown and unoccluded but not the
    active window" reachable at all, which is the state a second display would put this window
    in. HWND_TOP failed in revision 1 because the foreground window is a maximized
    Chrome_WidgetWin_1 covering the whole screen, so HWND_TOPMOST is tried next — and each step
    is reported, because "the raise worked" is exactly the kind of thing this probe exists not
    to assume.
    """
    steps = []
    for name, after in (("HWND_TOP", HWND_TOP), ("HWND_TOPMOST", HWND_TOPMOST)):
        ok = bool(user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(after), 0, 0, 0, 0,
                                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))
        time.sleep(0.5)
        st = state_of(hwnd)
        steps.append({"step": name, "returned": ok, "on_top_pct": st["on_top_pct"],
                      "active": st["active"], "topmost": st["topmost"]})
        print(f"    SetWindowPos({name}, SWP_NOACTIVATE) -> {ok}; on_top={st['on_top_pct']}% "
              f"ACTIVE={st['active']} topmost={st['topmost']}")
        if (st["on_top_pct"] or 0) >= 99:
            break
    return steps


def undo_topmost(hwnd: int) -> None:
    if user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST:
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def show_no_activate(hwnd: int) -> dict:
    """Get the window on screen without giving it the focus; report which call was needed.

    `SW_SHOWNOACTIVATE` is the documented "show, do not activate" call, but it is described as
    using "most recent size and position" and does not say whether it un-minimizes. So it is
    tried, then VERIFIED with `IsIconic`, and `SetWindowPos(SWP_SHOWWINDOW|SWP_NOACTIVATE)` is
    the fallback. Which one worked is printed rather than assumed: a window still minimized here
    would answer this probe's question under "shown" semantics it never had.
    """
    user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOWNOACTIVATE)
    time.sleep(0.6)
    used = "ShowWindow(SW_SHOWNOACTIVATE)"
    if user32.IsIconic(wintypes.HWND(hwnd)):
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOP), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        time.sleep(0.6)
        used = ("SetWindowPos(SWP_SHOWWINDOW|SWP_NOACTIVATE) "
                "[SW_SHOWNOACTIVATE left it minimized]")
    return {"used": used, "still_iconic": bool(user32.IsIconic(wintypes.HWND(hwnd)))}


# --------------------------------------------------------------------------------------------
# the two instances
# --------------------------------------------------------------------------------------------
def launch(exe: str, page: str, marker: str, workdir: str):
    proc = subprocess.Popen(
        [exe, f"--user-data-dir={os.path.join(workdir, 'profile')}", "--no-first-run",
         "--no-default-browser-check", "--disable-sync", page],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    hwnd = 0
    deadline = time.time() + 45.0
    while time.time() < deadline and not hwnd:
        for w in kr.visible_titled_windows():
            if w["exe"].lower() == "msedge.exe" and w["title"].startswith(marker):
                hwnd = w["hwnd"]
                break
        time.sleep(0.15)
    return proc, hwnd


def kill(proc, hwnd: int, workdir: str) -> None:
    try:
        if hwnd:
            undo_topmost(hwnd)
    except Exception:
        pass
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
    try:
        proc.wait(timeout=3)
    except Exception:
        pass
    for _ in range(10):
        shutil.rmtree(workdir, ignore_errors=True)
        if not os.path.exists(workdir):
            return
        time.sleep(0.3)
    print(f"  WARNING: temp profile not removed: {workdir}")


def give_foreground_back(fg_original: int) -> bool:
    """Hand the focus back to whatever had it; the foreground lock makes this one-way.

    Retried a few times because the system's foreground lock timeout can refuse an immediate
    change, and the result is verified rather than assumed — revision 1 called this AFTER
    minimizing the window, and the call simply failed.
    """
    if not fg_original:
        return False
    for attempt in range(3):
        user32.SetForegroundWindow(wintypes.HWND(fg_original))
        time.sleep(0.4 + 0.3 * attempt)
        if kr.foreground_root() == fg_original:
            return True
    return False


def run_cold(exe: str, page: str, marker: str, fg_original: int,
             workdir: str) -> tuple[list[dict], list[dict]]:
    print("=" * 78)
    print("COLD (first, so this process still has the right to yield the foreground) —")
    print("minimized before ANY query, so the page tree has never been built in any window")
    print("state. The tree can therefore only be built by the query that runs while the")
    print("window is shown and NOT the active window.")
    print("=" * 78)
    proc, hwnd = launch(exe, page, marker, workdir)
    reads: list[dict] = []
    keys: list[dict] = []
    try:
        if not hwnd:
            print("  window never appeared — NOT measured")
            return reads, keys
        print(f"  hwnd={hwnd} class={kr.window_class(hwnd)} title={kr.window_title(hwnd)!r}")
        print(f"  foreground at launch == our window: {kr.foreground_root() == hwnd}")
        # Yield FIRST, while this process started the process holding the foreground and so has
        # the right to hand it back. Nothing has queried this window yet.
        back = give_foreground_back(fg_original)
        st = state_of(hwnd)
        print(f"  yielded the foreground to {fg_original}: {back} "
              f"(ACTIVE={st['active']} is_fg={st['is_fg']})")
        # Only now minimize: revision 1 minimized first and then could not give the foreground
        # back at all, which is how its "not foreground" reading came out foreground.
        user32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)
        time.sleep(0.6)
        st = state_of(hwnd)
        print(f"  minimized: iconic={st['iconic']} ACTIVE={st['active']} is_fg={st['is_fg']}")

        reads.append(read(hwnd, "B1 minimized, never queried (negative control: expect no page)"))

        shown = show_no_activate(hwnd)
        print(f"\n  {shown['used']}")
        place(hwnd)
        st = state_of(hwnd)
        print(f"  after showing, BEFORE any raise: iconic={st['iconic']} "
              f"visible={st['visible']} ACTIVE={st['active']} is_fg={st['is_fg']} "
              f"on_top={st['on_top_pct']}% rect={st['rect']}")
        # Read BEFORE raising. Revision 2 raised first, so "shown" and "unoccluded" changed
        # together and the fresh-build reading could not say which one built the page. Here the
        # covered-shown reading is taken first, and since nothing has queried this window yet it
        # is the one that decides the mechanism.
        reads.append(read(hwnd, "B2 shown, NOT active, never queried, NOT raised  <-- the blank"))
        keys.append(key_probe(hwnd, "B3 shown, NOT active, covered"))

        print()
        raise_unoccluded(hwnd)
        st = state_of(hwnd)
        print(f"  after raising: on_top={st['on_top_pct']}% ACTIVE={st['active']} "
              f"topmost={st['topmost']}")
        reads.append(read(hwnd, "B4 shown, NOT active, unoccluded (survival only)"))
        keys.append(key_probe(hwnd, "B5 shown, NOT active, unoccluded"))
        return reads, keys
    finally:
        # Yield BEFORE killing: killing the window that holds the foreground makes Windows
        # activate some other window, and revision 2 left the user's desktop focused on an
        # unrelated 'Token Monitor' window because of exactly that ordering.
        yielded = give_foreground_back(fg_original)
        print(f"  (teardown) yielded the foreground before killing: {yielded}")
        kill(proc, hwnd, workdir)


def run_warm(exe: str, page: str, marker: str, fg_original: int,
             workdir: str) -> tuple[list[dict], list[dict]]:
    print("\n" + "=" * 78)
    print("WARM — launched foreground, first query runs ACTIVE (positive control and oracle")
    print("       validation), then the same window is read again deactivated, then raised")
    print("       without activation, then minimized.")
    print("=" * 78)
    proc, hwnd = launch(exe, page, marker, workdir)
    reads: list[dict] = []
    keys: list[dict] = []
    try:
        if not hwnd:
            print("  window never appeared — NOT measured")
            return reads, keys
        print(f"  hwnd={hwnd} class={kr.window_class(hwnd)} title={kr.window_title(hwnd)!r}")
        print(f"  foreground at launch == our window: {kr.foreground_root() == hwnd}")
        place(hwnd)

        # The active read has to come FIRST and for free: the window launches into the
        # foreground, and once this process gives that away it cannot take it back.
        reads.append(read(hwnd, "A1 ACTIVE, shown, first query ever (positive control)"))
        keys.append(key_probe(hwnd, "A2 ACTIVE  <- validates the title oracle"))

        back = give_foreground_back(fg_original)
        st = state_of(hwnd)
        print(f"\n  yielded the foreground to {fg_original}: {back} "
              f"(now {kr.foreground_root()}, ACTIVE={st['active']} is_fg={st['is_fg']})")

        reads.append(read(hwnd, "A3 shown, NOT active, z-order unchanged (may be covered)"))
        keys.append(key_probe(hwnd, "A4 shown, NOT active, behind"))

        print()
        raise_unoccluded(hwnd)
        reads.append(read(hwnd, "A5 shown, NOT active, RAISED unoccluded"))
        keys.append(key_probe(hwnd, "A6 shown, NOT active, unoccluded"))

        user32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)
        reads.append(read(hwnd, "A7 minimized, after the tree was already built"))
        return reads, keys
    finally:
        yielded = give_foreground_back(fg_original)
        print(f"  (teardown) yielded the foreground before killing: {yielded}")
        kill(proc, hwnd, workdir)


# --------------------------------------------------------------------------------------------
def verdict(reads: list[dict], keys: list[dict]) -> None:
    """Draw the conclusion ONLY from readings whose recorded state matches what they claim.

    This gating is the fix for revision 1's worst defect: its verdict asserted "NEVER the
    foreground window" about a reading whose own state said otherwise. A reading that did not
    reach its intended state is now printed as UNINFORMATIVE and excluded.
    """
    by = {r["label"][:2]: r for r in reads}
    print("\n" + "=" * 78)
    print("VERDICT — gated on the recorded OS state, never on the label")
    print("=" * 78)
    hdr = (f"  {'':<4}{'iconic':<8}{'vis':<6}{'ACTIVE':<8}{'is_fg':<7}{'on_top':<8}"
           f"{'ctl doc':<9}{'raw doc':<9}")
    print(hdr + "  label")
    for r in reads:
        s = r["state"]
        print(f"  {r['label'][:2]:<4}{str(s['iconic']):<8}{str(s['visible']):<6}"
              f"{str(s['active']):<8}{str(s['is_fg']):<7}{str(s['on_top_pct']):<8}"
              f"{len(r['control']['documents']):<9}{len(r['raw']['documents']):<9}"
              f"  {r['label'][4:]}")

    def shown_not_active(r: dict | None) -> bool:
        if not r:
            return False
        s = r["state"]
        return (not s["iconic"]) and s["visible"] and (not s["active"])

    def why_not(r: dict | None) -> str:
        if not r:
            return "reading missing"
        s = r["state"]
        bad = []
        if s["iconic"]:
            bad.append("still minimized")
        if not s["visible"]:
            bad.append("not visible")
        if s["active"]:
            bad.append("the window was ACTIVE")
        return ", ".join(bad) or "ok"

    print()
    b1, b2, b4 = by.get("B1"), by.get("B2"), by.get("B4")
    a1, a3, a5, a7 = by.get("A1"), by.get("A3"), by.get("A5"), by.get("A7")

    ctl_b1 = len(b1["control"]["documents"]) if b1 else -1
    ctl_b2 = len(b2["control"]["documents"]) if b2 else -1
    ctl_b4 = len(b4["control"]["documents"]) if b4 else -1
    if ctl_b1 != 0:
        print(f"  COLD negative control DID NOT HOLD (minimized/never-queried control-view "
              f"documents={ctl_b1},\n  expected 0), so this run cannot separate 'built while "
              f"shown' from 'was there all along'.")
        print("  The BLANK is UNANSWERED by this run.")
    elif not shown_not_active(b2):
        print(f"  B2 DID NOT REACH THE STATE: {why_not(b2)}.")
        print("  The window must be shown and NOT the active window for this to answer the")
        print("  blank, so B2 is UNINFORMATIVE and the BLANK stays UNANSWERED.")
    elif ctl_b2 > 0:
        print("  THE BLANK IS FILLED: a window minimized before any query — so its page tree had")
        print(f"  never been built — produced a named page as soon as it was SHOWN, while NOT the")
        print(f"  active window and never active anywhere in this instance. It did so even though")
        print(f"  it was NOT yet unoccluded (on_top={b2['state']['on_top_pct']}%), so the gate is")
        print("  being SHOWN, not activation and not z-order.")
        print("  A second display produces this state, so it DOES buy the read.")
    elif b4 and shown_not_active(b4) and ctl_b4 > 0:
        print("  THE BLANK IS FILLED, with a caveat: the never-queried window produced a page")
        print("  once it was SHOWN AND UNOCCLUDED, but not while it was shown and covered, so")
        print("  visibility is required in the stronger sense. A second display satisfies both.")
    else:
        print("  ACTIVATION IS THE GATE: the cold window was shown and unoccluded and not")
        print("  active, and still produced NO page, while A1 (active) did.")
        print("  A second display cannot reach the state Chromium needs.")
    if b4:
        print(f"  B4 (unoccluded, same instance, already built) on_top="
              f"{b4['state']['on_top_pct']}% ACTIVE={b4['state']['active']} "
              f"control documents={ctl_b4}.")

    print()
    if a1 and a3 and len(a1["control"]["documents"]) > 0:
        if len(a3["control"]["documents"]) > 0:
            print("  The tree SURVIVES deactivation: built while ACTIVE in A1, still named in A3")
            print("  with the window shown, not active"
                  + (f", and covered (on_top={a3['state']['on_top_pct']}%)"
                     if (a3["state"]["on_top_pct"] or 0) < 50 else "")
                  + ". So a page already read once stays readable.")
        else:
            print("  The tree is DROPPED on deactivation (A1 had a page, A3 did not).")
    if a5:
        print(f"  A5 (the unoccluded deactivated reading) reached on_top="
              f"{a5['state']['on_top_pct']}% ACTIVE={a5['state']['active']}, "
              f"control documents={len(a5['control']['documents'])}.")
        if not shown_not_active(a5):
            print(f"     -> UNINFORMATIVE: {why_not(a5)}")
        elif (a5["state"]["on_top_pct"] or 0) < 99:
            print("     -> the window could NOT be got unoccluded without activating it, so the")
            print("        occlusion dimension stays unmeasured above this value.")
    if a7:
        print(f"  Re-minimizing after the build does "
              f"{'NOT ' if len(a7['control']['documents']) > 0 else ''}drop it (A7 control "
              f"documents={len(a7['control']['documents'])}).")

    print()
    oracle = next((k for k in keys if k["label"].startswith("A2")), None)
    if oracle and not oracle["landed"]:
        print("  ORACLE NOT VALIDATED: the ACTIVE key probe did not change the title either, so")
        print("  the other key results cannot be read as 'the key did not land'.")
    elif oracle:
        print("  Title oracle validated by A2 (an ACTIVE POST landed).")
    for k in keys:
        print(f"    {k['label']:<40} ACTIVE={str(k['active']):<6} landed={str(k['landed']):<6} "
              f"post_accepted={k['accepted']}")
    deactivated = [k for k in keys if not k["active"]]
    if oracle and oracle["landed"] and deactivated and not any(k["landed"] for k in deactivated):
        print("  => Every POST to a NOT-active window was accepted by PostMessage and did")
        print("     nothing. An agent can LOOK at a Chromium page it does not activate, and")
        print("     cannot TYPE into it: the read half of a second display works, the act half")
        print("     does not.")


def main() -> int:
    exe = kr._edge_path()
    if not exe:
        print("no Edge binary found — cannot run")
        return 1
    fg_original = kr.foreground_root()
    print(f"screen {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)} (DPI-aware)  "
          f"foreground before: {fg_original} ({kr.window_title(fg_original)!r})")
    print(f"edge: {exe}")

    reads: list[dict] = []
    keys: list[dict] = []
    # One instance per process is the configuration that has ALWAYS worked for the foreground
    # yield. The second instance in one process could never yield (measured twice: revision 1
    # and revision 2 both got `False` there), because the right to set the foreground is spent
    # by the first yield — so the instances are selectable.
    only = ""
    for a in sys.argv[1:]:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
    print(f"instances: {only or 'cold then warm'}")
    try:
        # One instance at a time, each with its OWN page file and profile: the two markers must
        # not collide, and the window identification matches on the title prefix.
        for tag, runner in (("cold", run_cold), ("warm", run_warm)):
            if only and tag != only:
                print(f"\n  (skipping the {tag} instance: --only={only})")
                continue
            workdir = tempfile.mkdtemp(prefix=f"dsh-cua-vf-{tag}-")
            marker = f"VF{tag[0].upper()}-{os.getpid()}"
            page = os.path.join(workdir, "vf.html")
            with open(page, "w", encoding="utf-8") as fh:
                fh.write(f"""<!doctype html><meta charset="utf-8"><title>{marker} START</title>
<body style="font:16px sans-serif">
<p>visible-vs-foreground probe page — safe to close</p>
<input id="t" style="width:420px;font-size:24px">
<script>
const MARK = "{marker}";
const t = document.getElementById('t');
t.focus();
document.addEventListener('keydown', e => {{ document.title = MARK + ' K=' + e.key; }});
t.addEventListener('input', () => {{ document.title = MARK + ' V=' + t.value; }});
</script></body>""")
            r, k = runner(exe, page, marker, fg_original, workdir)
            reads.extend(r)
            keys.extend(k)
            shutil.rmtree(workdir, ignore_errors=True)
        verdict(reads, keys)
        return 0
    finally:
        restored = give_foreground_back(fg_original)
        print(f"\nforeground restored to {fg_original}: {restored} "
              f"(now {kr.foreground_root()})")


if __name__ == "__main__":
    raise SystemExit(main())
