"""Which keyboard routes actually exist and actually land, measured on this machine.

WHY THIS EXISTS
    `send_hotkey` is one of dsh-cua's only two hard-gated paths, and it is worse than
    that. `bridge.send_hotkey` calls `_ensure_visible` FIRST (bridge.py:573), which does
    `ShowWindow(SW_RESTORE)` + `SetForegroundWindow`, and only THEN checks whether the
    target ended up in the foreground (bridge.py:574). So today a hotkey has exactly two
    outcomes: it steals the user's foreground, or it is refused with
    "target-not-foreground". There is no third outcome.

    That steal is invisible to the arbiter by construction — arbiter.py states it
    outright: "Programmatic activation (SetForegroundWindow inside _ensure_visible) is
    not itself an input event". `GetLastInputInfo` cannot see it, so the hard gate does
    not cover it.

    cua-driver routes hotkeys without any foreground swap at all: UIA `AcceleratorKey`
    invocation for XAML/WinUI/UWP hosts, `PostMessage(WM_KEYDOWN/UP)` for legacy Win32
    keys without modifiers, and `SendInput` + a foreground swap (restored afterwards)
    only for legacy Win32 apps that bind accelerators via `TranslateAccelerator`.
    Its docs are explicit that the target "does NOT need to be frontmost in any branch".

    Before any of that is believed or implemented, it has to be measured HERE. cua's
    platform-support page documents macOS-first reasoning; its Windows page lists which
    toolkits have which route. Those lists are the driver's detectors on ITS test
    machines, not this one. This script produces this machine's own table.

WHAT IT MEASURES
    inventory   Read-only. Walks the UIA control-view tree of every visible titled
                top-level window and records which elements publish an
                `AcceleratorKey`/`AccessKey`. Injects no input and takes no focus, and
                asserts that — with a watcher thread sampling `GetForegroundWindow`
                continuously, because comparing the foreground before and after cannot see
                a transient steal. `--compare-views` additionally walks every window with
                RawViewWalker and reports what the control view hides.

    The remaining phases need a purpose-built fixture and are added separately:
    fixture     Which posted message forms actually change a control's state, on a
                window this test owns and that never takes focus.
    accelerator Whether a `TranslateAccelerator` menu / a `GetKeyState` app / a
                message-only app each see a posted Ctrl+F.
    calculator  A real UWP/WinUI host: does a posted key move its display?
    chromium    A real Chromium host in an isolated profile: does a posted key reach
                page content?

HOW TO READ THE OUTPUT
    "has AcceleratorKey" is the claim that matters. If real apps on this desktop publish
    accelerator keys, the semantic route is available and is strictly better than any
    key delivery, because it invokes the command instead of synthesising the keystroke
    that would have invoked it. If they do not, the semantic route is a dead end here
    and the honest answer is that a hotkey still needs the gated path.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from ctypes import wintypes

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# --- UIA property ids we read -------------------------------------------------
# Documented values from UIAutomationClient.h. Read via GetCurrentPropertyValue so a
# missing property raises a catchable error instead of an attribute error.
UIA_ACCELERATOR_KEY = 30006
UIA_ACCESS_KEY = 30007
UIA_NAME = 30005

LRESULT = ctypes.c_longlong
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# --- keyboard message constants ----------------------------------------------
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_CLOSE = 0x0010
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_F = 0x46
VK_G = 0x47
SW_MINIMIZE = 6
SW_RESTORE = 9
MAPVK_VK_TO_VSC = 0

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR,
                                 wintypes.LPCWSTR]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetKeyboardState.restype = wintypes.BOOL
user32.SetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.SendMessageW.restype = LRESULT
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.GetKeyboardState.restype = wintypes.BOOL
user32.GetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]


def scancode_of(vk: int) -> int:
    return user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) & 0xFF


def lparam_down(vk: int, *, context: bool = False) -> int:
    """KEYDOWN lParam: repeat=1, scancode in bits 16-23, context bit 29 for syskeys."""
    lp = 1 | (scancode_of(vk) << 16)
    if context:
        lp |= 1 << 29
    return lp


def lparam_up(vk: int, *, context: bool = False) -> int:
    return lparam_down(vk, context=context) | (1 << 30) | (1 << 31)


def foreground_root() -> int:
    """Top-level (root) window of the current foreground window.

    The same reduction dsh-cua uses: a child control is in the foreground when its
    root is, and a bare hwnd comparison would report a spurious change whenever focus
    moved between two controls of the same window.
    """
    h = user32.GetForegroundWindow()
    if not h:
        return 0
    GA_ROOT = 2
    return user32.GetAncestor(wintypes.HWND(h), GA_ROOT) or h


class ForegroundWatch:
    """Sample `GetForegroundWindow` continuously while a probe runs.

    Reading the foreground before and after a probe cannot see a TRANSIENT steal: the window
    can come forward and be pushed back between the two reads, and the probe still reports
    "unchanged". Audit A built one of these to test this harness's own claim that the
    foreground does not change during the de-gated probes, sampled 284 000-296 000 times per
    probe, and found a single distinct value — a much stronger statement than the sampled
    comparison this harness shipped. This is that watcher.

    Read-only: `GetForegroundWindow` injects nothing, activates nothing and takes no focus.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.first = 0
        self.samples = 0
        self.other_samples = 0
        self.others: list[int] = []

    def _run(self) -> None:
        # No sleep: the point is to catch a steal that lasts less than the probe's settle
        # time. ctypes releases the GIL around the call, so this loop runs flat out.
        while not self._stop.is_set():
            h = user32.GetForegroundWindow()
            self.samples += 1
            if h != self.first:
                self.other_samples += 1
                if h not in self.others and len(self.others) < 8:
                    self.others.append(h)

    def __enter__(self) -> "ForegroundWatch":
        self.first = user32.GetForegroundWindow()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def report(self) -> dict:
        return {"samples": self.samples,
                "distinct": [self.first, *self.others],
                "changed": self.other_samples > 0,
                "other_samples": self.other_samples}


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def exe_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    if not pid.value:
        return "?"
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return f"pid:{pid.value}"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)
    return f"pid:{pid.value}"


def visible_titled_windows() -> list[dict]:
    out: list[dict] = []

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(wintypes.HWND(hwnd)):
            title = window_title(hwnd)
            if title:
                out.append({"hwnd": hwnd, "title": title,
                            "class": window_class(hwnd), "exe": exe_name(hwnd)})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def sample_windows(windows: list[dict], limit: int) -> tuple[list[dict], int]:
    """Every visible titled window, capped at `limit` — NOT one per (exe, class).

    Sampling one window per (exe, class) was the first version's shortcut, and its docstring
    asserted that "walking every Edge window would be pointless". Audit C measured the
    opposite: of three Edge windows, the two not sampled held 4 of the 18 control-view keys
    and 21 of the 35 raw-view keys. A per-class sample produces a per-class answer, and the
    question here is what THIS desktop publishes.

    Returns (sample, dropped_by_cap) so a truncated walk can say so instead of quietly
    looking like a census.
    """
    return windows[:limit], max(0, len(windows) - limit)


def inventory_window(hwnd: int, max_nodes: int, max_depth: int,
                     include_offscreen: bool = False) -> dict:
    """Walk one window's control-view tree and count accelerator-key publishers.

    `include_offscreen` is the one lever that measurably matters. Audit C's ablation, and an
    independent re-measurement here, agree that raising the node/depth caps changes nothing
    (137 nodes either way on a real page) while offscreen pruning costs 6 of 14 keys — all
    of them page content, because a page's scrolled-out rows are offscreen. So the default
    reports what the shipped `skyshot` sees, and `--include-offscreen` reports the census.
    """
    from dsh_cua import uia  # imported late: only this phase needs COM

    # Wake the provider before walking. Chromium/Electron build their accessibility tree
    # lazily, and an un-warmed window can report an empty tree — which reads as "this app
    # publishes nothing" when it means "nothing has been built yet". Three Electron apps
    # were reported as zeros in the first draft for exactly this reason (audit C).
    try:
        uia.warm_up(hwnd)
    except Exception:
        pass

    t0 = time.perf_counter()
    try:
        records, truncated = uia._walk(hwnd, max_nodes=max_nodes,
                                       max_depth=max_depth,
                                       include_offscreen=include_offscreen,
                                       include_values=False)
    except Exception as exc:  # a window that dies mid-walk is not a test failure
        return {"nodes": 0, "error": f"{type(exc).__name__}: {exc}"}

    accel: list[dict] = []
    access: list[dict] = []
    for rec in records:
        el = rec["element"]
        for prop, sink in ((UIA_ACCELERATOR_KEY, accel), (UIA_ACCESS_KEY, access)):
            try:
                val = el.GetCurrentPropertyValue(prop)
            except Exception:
                continue
            if isinstance(val, str) and val.strip():
                sink.append({"role": rec["role"], "name": rec["name"][:40],
                             "key": val.strip()[:24]})
    return {
        "nodes": len(records),
        "truncated": truncated,
        "accelerator_keys": accel,
        "access_keys": access,
        "walk_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def phase_inventory(args) -> int:
    print("=" * 78)
    print("PHASE A — AcceleratorKey inventory (read-only: no input, no focus change)")
    print("=" * 78)

    fg_watch = ForegroundWatch()
    fg_before = foreground_root()
    wins = visible_titled_windows()
    print(f"visible titled top-level windows: {len(wins)}")
    sample, dropped = sample_windows(wins, args.windows)
    print(f"walking every one of them (no per-app sampling): {len(sample)}"
          + (f"   *** {dropped} DROPPED BY THE --windows CAP — NOT A CENSUS ***"
             if dropped else ""))

    with fg_watch:
        rows = []
        total_accel = 0
        for w in sample:
            info = inventory_window(w["hwnd"], args.max_nodes, args.max_depth,
                                    args.include_offscreen)
            n_accel = len(info.get("accelerator_keys", []))
            total_accel += n_accel
            rows.append({**w, **info})
            flag = f"  <-- {n_accel} AcceleratorKey" if n_accel else ""
            print(f"  {w['exe']:<22} {w['class'][:26]:<26} nodes={info.get('nodes', 0):<5}"
                  f" accel={n_accel:<3}{flag}")

    fg_after = foreground_root()
    watch = fg_watch.report()
    print("\n" + "-" * 78)
    print(f"total elements walked:      {sum(r.get('nodes', 0) for r in rows)}")
    print(f"total AcceleratorKey values:{total_accel}")
    print(f"foreground before/after:    {fg_before} -> {fg_after}"
          f"  {'(unchanged — read-only confirmed)' if fg_before == fg_after else '(CHANGED!)'}")
    # Before/after sampling cannot see a transient steal; the watcher can.
    print(f"foreground watched:         {watch['samples']} samples over the whole phase, "
          f"{len(watch['distinct'])} distinct value(s) — "
          f"{'a TRANSIENT CHANGE occurred' if watch['changed'] else 'never changed'}")

    view_results = []
    if args.compare_views:
        view_results = _print_view_comparison(sample, args.max_nodes, args.max_depth,
                                              args.include_offscreen)

    print("\n  THIS NUMBER IS A LOWER BOUND, NOT A CENSUS.")
    print("    * offscreen subtrees are pruned by _walk — cost 6 of 14 keys on one real page,")
    print("      all of them page CONTENT; Chromium maps aria-keyshortcuts -> AcceleratorKey")
    print("      (`--include-offscreen` is the lever; `find_elements` already defaults to it)")
    print("  Per-app sampling is gone — this walks every visible titled window. A wide raw-view")
    print("  walk of this desktop found 35 keys where a per-app sample reported 8, and that gap")
    print("  was the SAMPLING, not the walker. Raising the node/depth caps changed nothing.")

    if total_accel:
        print("\nAccelerator keys published on this desktop:")
        for r in rows:
            for a in r["accelerator_keys"]:
                print(f"  {r['exe']:<22} {a['role']:<12} {a['name']!r:<28} {a['key']!r}")
    else:
        print("\nNO AcceleratorKey published by any sampled window.")
        print("  => the semantic accelerator route is NOT available on these apps.")

    if args.json:
        # `named` and `hidden_named` are sets — JSON has no set, and a sorted list is what a
        # reader wants anyway.
        views_json = [{"hwnd": r["hwnd"], "exe": r["exe"], "iconic": r["iconic"],
                       "hidden_named": sorted(r["hidden_named"]),
                       "lost_if_raw": sorted(r["lost_if_raw"]),
                       "control": {**r["control"], "named": sorted(r["control"]["named"])},
                       "raw": {**r["raw"], "named": sorted(r["raw"]["named"])}}
                      for r in view_results]
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"phase": "inventory", "sampled": len(sample),
                       "windows_dropped_by_cap": dropped,
                       "foreground_watch": watch,
                       "views": views_json,
                       "rows": [{k: v for k, v in r.items() if k != "element"}
                                for r in rows]}, fh, ensure_ascii=False, indent=2)
        print(f"\nwrote {args.json}")
    return 0


# --- Phase B: the owned accelerator fixture -----------------------------------
#
# Everything below runs against a window this test owns, OUT of process and created with
# WS_EX_NOACTIVATE. Both properties are load-bearing:
#   * it never takes focus, so any foreground change is caused by the probe and nothing
#     else — a fixture that stole focus would invalidate the measurement it exists for;
#   * it is a real neighbouring process, so `GetKeyState` reports what an actual
#     background application would see. In-process it would report state the harness
#     shares with itself, and the AttachThreadInput probe would degenerate to a no-op.

FIXTURE = os.path.join(_HERE, "accelerator_target.py")
COUNTERS = ("menu_accel", "keydown_message", "keydown_getkeystate", "syskey",
            "char_count")


def _reap(proc, frame: int = 0) -> None:
    """Close the fixture window and GUARANTEE the process dies. Both steps guarded.

    The guarding is not defensive habit — it is the fix for an observed leak. An earlier
    version called `user32.PostMessageW(..., WM_CLOSE, ...)` with `WM_CLOSE` undefined; the
    NameError propagated OUT of the `finally` block, so `proc.terminate()` — the next
    statement — never ran. A stray `dsh cua accelerator target` window then survived for
    hours, and the `inventory` phase later sampled it as though it were a real application
    (audit C, side finding 4). One failure in cleanup must not skip the rest of cleanup.
    """
    if frame:
        try:
            user32.PostMessageW(wintypes.HWND(frame), WM_CLOSE, 0, 0)
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _read_state(path: str) -> dict:
    """Read the fixture's state file, tolerating the instant before its first write."""
    for _ in range(24):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            time.sleep(0.05)
    return {}


def _edit_child(frame: int) -> int:
    return user32.FindWindowExW(wintypes.HWND(frame), None, "EDIT", None)


def _wait_for_edit(frame: int, timeout: float = 5.0) -> int:
    """Wait for the fixture's EDIT child to exist before measuring against it.

    `FindWindowW` returns as soon as the TOP-LEVEL window exists, and the fixture creates
    its children afterwards — so reading the child immediately is a race. It was lost:
    Phase E printed "EDIT child hwnd=None", took '<no EDIT child>' as the baseline, then
    read real text next and reported LANDED for a call that had not landed. A baseline
    that is an error string turns a missing control into a passing test.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        edit = _edit_child(frame)
        if edit:
            return edit
        time.sleep(0.05)
    return 0


def _edit_text(frame: int) -> str:
    edit = _edit_child(frame)
    if not edit:
        return "<no EDIT child>"
    n = user32.SendMessageW(wintypes.HWND(edit), WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(wintypes.HWND(edit), WM_GETTEXT, n + 1,
                        ctypes.cast(buf, ctypes.c_void_p).value)
    return buf.value


def _post(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
    """Post one message; return whether PostMessage ACCEPTED it.

    The BOOL is kept rather than discarded because an audit found that these probes recorded
    whether the observable CHANGED but never whether the message was even accepted — which
    conflates "delivered and the application ignored it" with "never entered the queue".
    That distinction is the whole difference between an application result and an instrument
    artifact, and it is what made two negative results in the first draft untrustworthy.
    """
    return bool(user32.PostMessageW(wintypes.HWND(hwnd), msg, wparam, lparam))


def _ctrl_combo(frame: int, vk: int = VK_F) -> list:
    """Post Ctrl+<vk> down/up. Returns PostMessage's return value for each message."""
    return [
        _post(frame, WM_KEYDOWN, VK_CONTROL, lparam_down(VK_CONTROL)),
        _post(frame, WM_KEYDOWN, vk, lparam_down(vk)),
        _post(frame, WM_KEYUP, vk, lparam_up(vk)),
        _post(frame, WM_KEYUP, VK_CONTROL, lparam_up(VK_CONTROL)),
    ]


def _ctrl_combo_with_attached_state(frame: int, vk: int = VK_F) -> dict:
    """The classic workaround: share the target's queue, then set its keyboard state.

    `SetKeyboardState` only reaches the CALLING thread's queue, so alone it cannot help a
    background window. `AttachThreadInput` makes the two threads share one queue, which is
    the one documented way to make `GetKeyState` agree with a posted modifier. Whether
    `TranslateAccelerator` then fires is the measurement, not the assumption.
    """
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(wintypes.HWND(frame), ctypes.byref(pid))
    cur = kernel32.GetCurrentThreadId()

    saved = (ctypes.c_ubyte * 256)()
    user32.GetKeyboardState(saved)
    attached = bool(user32.AttachThreadInput(cur, tid, True))
    harness_readback = False
    try:
        staged = (ctypes.c_ubyte * 256)(*saved)
        staged[VK_CONTROL] = 0x80
        user32.SetKeyboardState(staged)
        # NOTE: this is a read-back of the HARNESS's own queue immediately after the
        # harness's own SetKeyboardState. It returns True even with NO attach, so it says
        # nothing whatsoever about the target. It is reported as a diagnostic only; the
        # target-side evidence is the fixture's own counters (menu_accel,
        # keydown_getkeystate), which are incremented inside the fixture's window procedure.
        harness_readback = bool(user32.GetKeyState(VK_CONTROL) & 0x8000)
        arrival = _ctrl_combo(frame, vk)
        time.sleep(0.30)
    finally:
        user32.SetKeyboardState(saved)
        user32.AttachThreadInput(cur, tid, False)
    return {"attached": attached, "arrival": arrival,
            "harness_side_readback_not_target_evidence": harness_readback,
            "harness_tid": cur, "target_tid": tid}


def phase_fixture(args) -> int:
    print("=" * 78)
    print("PHASE B — owned fixture: which keyboard routes actually land")
    print("=" * 78)

    state_path = args.json or os.path.join(tempfile.gettempdir(), "kr-state.json")
    title = f"dsh cua accelerator target {os.getpid()}"
    frame = 0
    # A leftover state file from a previous run gets read as this run's baseline and shows
    # up as NEGATIVE deltas — which is exactly what the first corrected run printed
    # ("char=-2"). Remove it, so _read_state has to wait for this run's own first write.
    for stale in (state_path, state_path + ".rows.json"):
        try:
            os.remove(stale)
        except OSError:
            pass
    proc = subprocess.Popen(
        [sys.executable, FIXTURE, "--json", state_path, "--title", title],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            frame = user32.FindWindowW(None, title)
            if frame:
                break
            time.sleep(0.05)
        if not frame:
            print("  FAILED: fixture window never appeared")
            return 1
        _wait_for_edit(frame)  # let the children exist before any probe reads them

        fg_start = foreground_root()
        print(f"  fixture hwnd        : {frame}")
        print(f"  fixture is foreground: {fg_start == frame}   "
              f"(must be False — WS_EX_NOACTIVATE)")
        print(f"  foreground at start  : {fg_start}\n")

        def char_to_frame():
            return {"arrival": [_post(frame, WM_CHAR, ord("X"), 0)]}

        def char_to_edit():
            edit = _edit_child(frame)
            if not edit:
                return {"arrival": []}
            return {"arrival": [bool(user32.PostMessageW(wintypes.HWND(edit),
                                                         WM_CHAR, ord("Y"), 0))]}

        def keydown_plain():
            return {"arrival": [_post(frame, WM_KEYDOWN, VK_F, lparam_down(VK_F)),
                                _post(frame, WM_KEYUP, VK_F, lparam_up(VK_F))]}

        def alt_f_syskey():
            return {"arrival": [
                _post(frame, WM_SYSKEYDOWN, VK_MENU, lparam_down(VK_MENU, context=True)),
                _post(frame, WM_SYSKEYDOWN, VK_F, lparam_down(VK_F, context=True)),
                _post(frame, WM_SYSKEYUP, VK_F, lparam_up(VK_F, context=True)),
                _post(frame, WM_SYSKEYUP, VK_MENU, lparam_up(VK_MENU, context=True))]}

        # Ctrl+F is in the fixture's accelerator table, Ctrl+G deliberately is not. Sending
        # both is what separates "TranslateAccelerator refused" from "the handler never saw
        # the key": without Ctrl+G, a fired accelerator consumes the message and route 2
        # stays unmeasured.
        probes = [
            ("WM_CHAR -> frame", char_to_frame),
            ("WM_CHAR -> EDIT child", char_to_edit),
            ("WM_KEYDOWN 'F', no modifier", keydown_plain),
            ("Ctrl+F posted (in accel table)",
             lambda: {"arrival": _ctrl_combo(frame, VK_F)}),
            ("Ctrl+G posted (not in accel table)",
             lambda: {"arrival": _ctrl_combo(frame, VK_G)}),
            ("Alt+F, WM_SYSKEYDOWN posted", alt_f_syskey),
            ("Ctrl+F + AttachThreadInput (accel)",
             lambda: _ctrl_combo_with_attached_state(frame, VK_F)),
            ("Ctrl+G + AttachThreadInput (GetKeyState)",
             lambda: _ctrl_combo_with_attached_state(frame, VK_G)),
        ]

        rows = []
        for name, fn in probes:
            before = _read_state(state_path)
            bc = {k: int(before.get(k, 0)) for k in COUNTERS}
            seq_before = int(before.get("seq", 0))
            fg_before = foreground_root()
            with ForegroundWatch() as fw:
                extra = fn() or {}
                time.sleep(args.settle)
            after = _read_state(state_path)
            ac = {k: int(after.get(k, 0)) for k in COUNTERS}
            fg_after = foreground_root()
            # Diff by sequence number, never by position: the fixture's log is ring
            # buffered, so a positional slice starts reporting the wrong events the
            # moment it wraps.
            events = [e for e in after.get("log", [])
                      if int(e.get("seq", 0)) > seq_before]
            rows.append({
                "probe": name,
                "delta": {k: ac[k] - bc[k] for k in COUNTERS},
                "fg_before": fg_before, "fg_after": fg_after,
                "fg_changed": fg_before != fg_after,
                "fg_watch": fw.report(),
                "edit_text": _edit_text(frame),
                "events": events,
                "arrival": extra.get("arrival"),
                "extra": extra,
            })

        hdr = (f"  {'probe':<44}{'accel':>6}{'kd_msg':>7}{'kd_gks':>7}{'syskey':>7}"
               f"{'char':>5}{'fg_chg':>7}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in rows:
            d = r["delta"]
            print(f"  {r['probe']:<44}{d['menu_accel']:>6}{d['keydown_message']:>7}"
                  f"{d['keydown_getkeystate']:>7}{d['syskey']:>7}{d['char_count']:>5}"
                  f"{str(r['fg_changed']):>7}")
        print("\n  legend: accel=TranslateAccelerator fired  kd_msg=handler trusted the "
              "message\n          kd_gks=handler consulted GetKeyState  char=WM_CHAR "
              "received")

        print("\n  PostMessage acceptance per probe ('delivered and ignored' vs 'never "
              "queued'):")
        for r in rows:
            print(f"    {r['probe']:<44} {r.get('arrival')}")

        print("\n  EDIT content after each probe (independent oracle, read via WM_GETTEXT):")
        for r in rows:
            print(f"    {r['probe']:<44} {r['edit_text']!r}")

        print("\n  events that ARRIVED, per probe (seq-diffed, ring-buffer safe):")
        for r in rows:
            evs = r["events"]
            if not evs:
                print(f"    {r['probe']:<48} (nothing arrived)")
                continue
            print(f"    {r['probe']:<48} {len(evs)} event(s)")
            for e in evs[:8]:
                print(f"        msg={e['msg']:<8} wparam={e['wparam']:<9} "
                      f"lparam={e['lparam']:<10} acted={e['acted']}")
            if len(evs) > 8:
                print(f"        ... {len(evs) - 8} more")

        any_fg = any(r["fg_changed"] for r in rows)
        any_watch_fg = any(r["fg_watch"]["changed"] for r in rows)
        print("\n" + "-" * 78)
        print(f"  foreground changed during ANY probe: {any_fg}"
              f"  {'(VIOLATION)' if any_fg else '(no probe stole focus)'}")

        # The sampled comparison above cannot see a steal that comes forward and goes back
        # between the two reads. This one can, so it is the stronger of the two and the one
        # a "no focus change" claim should rest on.
        print("\n  foreground WATCHED continuously during each probe "
              "(before/after cannot see a transient steal):")
        for r in rows:
            w = r["fg_watch"]
            print(f"    {r['probe']:<44}{w['samples']:>8} samples   "
                  + (f"CHANGED -> {w['distinct']}" if w["changed"] else "never changed"))
        print(f"  watcher saw a change in ANY probe: {any_watch_fg}"
              f"  {'(VIOLATION)' if any_watch_fg else '(no probe stole focus, transient or not)'}")

        if args.json:
            with open(args.json + ".rows.json", "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False, indent=2)
            print(f"  wrote {args.json}.rows.json")
        return 0
    finally:
        _reap(proc, frame)


# --- Phase C: a real UWP / XAML host ------------------------------------------
#
# The fixture proves the mechanism on a window this test controls. This phase asks the
# same question of an application nobody here controls, because the answer for a real
# XAML host need not be the answer for a synthetic Win32 one.

user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]


def _uia_records(hwnd: int, max_nodes: int = 500) -> list[dict]:
    from dsh_cua import uia
    try:
        records, _ = uia._walk(hwnd, max_nodes=max_nodes, max_depth=30,
                               include_offscreen=False, include_values=False)
        return records
    except Exception as exc:
        print(f"    (UIA walk failed: {type(exc).__name__}: {exc})")
        return []


def _walk_view(hwnd: int, view: str, max_nodes: int, max_depth: int,
               include_offscreen: bool = False) -> tuple[list[dict], bool]:
    """Walk one UIA view with `uia._walk`'s exact pruning and depth semantics.

    Only the walker differs from the shipped code, so a difference in the result is a
    difference of VIEW and not of walker code. `uia._walk` is hard-wired to
    ControlViewWalker, and the question "what does the control view hide?" cannot be asked
    without a raw-view walk to compare against.
    """
    from dsh_cua import uia
    obj = uia._uia()
    walker = obj.RawViewWalker if view == "raw" else obj.ControlViewWalker
    records: list[dict] = []
    truncated = False
    index = 0

    def visit(el, depth: int) -> None:
        nonlocal index, truncated
        if len(records) >= max_nodes or depth > max_depth:
            truncated = True
            return
        node = uia._node_of(el, include_values=False)
        # Same skip as _walk, root exempt: an invisible container's children are invisible.
        if node["offscreen"] and not include_offscreen and depth > 0:
            return
        records.append({"index": index, "depth": depth, "role": node["role"],
                        "name": node["name"], "offscreen": node["offscreen"],
                        "element": el})
        index += 1
        try:
            child = walker.GetFirstChildElement(el)
        except Exception:
            return
        while child is not None:
            visit(child, depth + 1)
            if len(records) >= max_nodes:
                truncated = True
                return
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                return

    visit(obj.ElementFromHandle(wintypes.HWND(hwnd)), 0)
    return records, truncated


def _view_pair(hwnd: int, max_nodes: int, max_depth: int,
               include_offscreen: bool = False) -> dict:
    """Both views of one window, plus how many NAMED elements the control view hides.

    Node counts alone cannot test `uia._walk`'s justification (that the extra raw-view nodes
    are "separators, groups and layout scaffolding"), because "450 extra nodes" is equally
    consistent with hiding real content. Names are what a caller can address, so the count
    that matters is named-minus-named.
    """
    out = {"iconic": bool(user32.IsIconic(wintypes.HWND(hwnd)))}
    for view in ("control", "raw"):
        recs, trunc = _walk_view(hwnd, view, max_nodes, max_depth, include_offscreen)
        out[view] = {"nodes": len(recs), "truncated": trunc,
                     "named": {r["name"] for r in recs if r["name"].strip()},
                     "documents": sum(1 for r in recs if r["role"] == "document"),
                     "unnamed": sum(1 for r in recs if not r["name"].strip())}
    out["hidden_named"] = out["raw"]["named"] - out["control"]["named"]
    # The OTHER direction, and the one that decides whether switching walkers is safe at all:
    # what the raw view would LOSE. "Neither view is a superset" was the justification for
    # keeping ControlViewWalker, and it was measured with the window minimized — so the direction
    # has to be printed for every window rather than argued from one reading.
    out["lost_if_raw"] = out["control"]["named"] - out["raw"]["named"]
    return out


def _print_view_comparison(windows: list[dict], max_nodes: int, max_depth: int,
                           include_offscreen: bool) -> list[dict]:
    """Control view vs raw view, per window, with IDENTICAL pruning both times."""
    print("\n" + "-" * 78)
    print(f"control view vs raw view (include_offscreen={include_offscreen}, "
          f"same pruning both times):")
    hdr = (f"  {'exe':<20}{'min':>6}{'ctl':>7}{'raw':>7}{'ctl nm':>8}{'raw nm':>8}"
           f"{'ctl doc':>9}{'raw doc':>9}{'hidden':>8}{'LOST':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    results = []
    for w in windows:
        v = _view_pair(w["hwnd"], max_nodes, max_depth, include_offscreen)
        v["hwnd"] = w["hwnd"]
        v["exe"] = w["exe"]
        results.append(v)
        print(f"  {w['exe'][:19]:<20}{str(v['iconic']):>6}{v['control']['nodes']:>7}"
              f"{v['raw']['nodes']:>7}{len(v['control']['named']):>8}"
              f"{len(v['raw']['named']):>8}{v['control']['documents']:>9}"
              f"{v['raw']['documents']:>9}{len(v['hidden_named']):>8}"
              f"{len(v['lost_if_raw']):>6}"
              + ("   <-- raw would LOSE named content" if v["lost_if_raw"] else ""))
    hid = [r for r in results if r["hidden_named"]]
    lost = [r for r in results if r["lost_if_raw"]]
    mini = [r for r in results if r["iconic"]]
    print(f"\n  windows where the control view hides a named element: {len(hid)} of "
          f"{len(results)}")
    print(f"  windows where the RAW view would lose a named element:  {len(lost)} of "
          f"{len(results)}   <-- if this is 0, raw is a superset and the walker choice is")
    print("      about SIZE, not about losing content")
    print(f"  of those, minimized: {len([r for r in hid if r['iconic']])}; "
          f"minimized windows overall: {len(mini)} of {len(results)}")
    print("  Read the two right-hand columns together: HIDDEN is what the control view leaves")
    print("  out, LOST is what the raw view would leave out. NEITHER is reliably 0, and that is")
    print("  the whole reason `_walk` keeps ControlViewWalker — but watch the 'min' column")
    print("  before believing a LOST count: a MINIMIZED window reports a much smaller raw view")
    print("  (ZCode read 54 named elements 'lost' while minimized and 0 once restored), so a")
    print("  LOST>0 taken on a minimized window is a measurement artifact, not a property of")
    print("  the walker.")
    return results


def _auto_id_record(records: list[dict], aid: str) -> dict | None:
    for rec in records:
        if (rec.get("automation_id") or "") == aid:
            return rec
    return None


user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]


def child_windows(hwnd: int) -> list[dict]:
    out: list[dict] = []

    def cb(child, _lp):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(child), ctypes.byref(pid))
        out.append({"hwnd": child, "class": window_class(child),
                    "title": window_title(child), "pid": pid.value})
        return True

    user32.EnumChildWindows(wintypes.HWND(hwnd), WNDENUMPROC(cb), 0)
    return out


def _best_uia_root(hwnd: int) -> tuple[int, list[dict]]:
    """Find the hwnd whose UIA tree is actually populated.

    A UWP app lives inside an `ApplicationFrameWindow` owned by ApplicationFrameHost.exe,
    and the app's own content sits in a child `Windows.UI.Core.CoreWindow`. Walking from
    the frame returns a single node, so the frame is not a usable root for these apps —
    which is worth knowing rather than reporting as "the app exposes no tree".
    """
    candidates = [("frame", hwnd)] + [(c["class"], c["hwnd"])
                                     for c in child_windows(hwnd)]
    best_hwnd, best_records = hwnd, _uia_records(hwnd)
    print("    UIA entry-point probe:")
    for label, h in candidates[:10]:
        recs = _uia_records(h, 600)
        print(f"      {label:<34} hwnd={h:<10} nodes={len(recs)}")
        if len(recs) > len(best_records):
            best_hwnd, best_records = h, recs
    return best_hwnd, best_records


def _name_of(rec: dict | None) -> str:
    if not rec:
        return "<not found>"
    try:
        return str(rec["element"].GetCurrentPropertyValue(UIA_NAME))
    except Exception:
        return rec.get("name", "<unreadable>")


def _find_calculator_frame() -> int:
    """The ApplicationFrameWindow whose CoreWindow child is actually Calculator.

    Matching on `ApplicationFrameHost.exe` alone is not enough: that process hosts EVERY
    packaged UWP app, so the first frame found can be some unrelated window. The first
    Phase C run did exactly that and reported a 1-node tree with no display element —
    which looked like a finding about UWP and was in fact a finding about the selector.
    """
    for w in visible_titled_windows():
        if w["class"] != "ApplicationFrameWindow":
            continue
        for c in child_windows(w["hwnd"]):
            if "CoreWindow" in c["class"] and exe_name(c["hwnd"]).lower().startswith(
                    "calculator"):
                return w["hwnd"]
    return 0


def _core_window(frame: int) -> int:
    """The app's own `Windows.UI.Core.CoreWindow` inside an ApplicationFrameWindow.

    This is the handle that matters for two independent reasons, both measured:

    * **Cleanup.** The frame's pid is `ApplicationFrameHost.exe`, which hosts EVERY
      packaged UWP app. Killing it by pid tears down all of them. An earlier version of
      `phase_calculator` did that while its comment claimed it did not, and a neighbouring
      UWP app (`Nahimic3.exe`) was present in the inventory before that run and gone after.
    * **Delivery.** A posted key addressed to the FRAME never reaches the app; the same key
      addressed to the CoreWindow moves its display while the app stays in the background.
      Probing only the frame is what produced a false "posted keys cannot drive a UWP app".
    """
    for c in child_windows(frame):
        if "CoreWindow" in c["class"]:
            return c["hwnd"]
    return 0


def phase_calculator(args) -> int:
    print("=" * 78)
    print("PHASE C — a real UWP/XAML host (Calculator): does the background route exist?")
    print("=" * 78)

    fg_original = foreground_root()
    core_hwnd = 0

    proc = subprocess.Popen(["calc.exe"], creationflags=getattr(subprocess,
                                                                "CREATE_NO_WINDOW", 0))
    try:
        hwnd = 0
        for _ in range(120):
            hwnd = _find_calculator_frame()
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            print("  FAILED: Calculator window never appeared")
            return 1
        time.sleep(1.2)  # let XAML finish building its tree
        core_hwnd = _core_window(hwnd)

        fg_after_launch = foreground_root()
        print(f"  calculator frame hwnd    : {hwnd}"
              f"  ({exe_name(hwnd)}, class {window_class(hwnd)})")
        print(f"  calculator CoreWindow    : {core_hwnd}"
              f"  ({exe_name(core_hwnd) if core_hwnd else '?'}, "
              f"class {window_class(core_hwnd) if core_hwnd else '?'})")
        print(f"  foreground before launch : {fg_original}")
        print(f"  foreground after launch  : {fg_after_launch}"
              f"  {'(launching the app took focus — unavoidable for a packaged app)' if fg_after_launch != fg_original else '(unchanged)'}")

        # Control measurement: the tree WHILE it is foreground. If the tree is only
        # populated when the window is active, every background reading below is
        # meaningless, and that difference is the finding.
        root, fg_records = _best_uia_root(hwnd)
        fg_accel = _accelerator_keys(fg_records)
        print(f"\n  [foreground] best root hwnd={root}  nodes={len(fg_records)}"
              f"  AcceleratorKey={len(fg_accel)}"
              f"  display={_name_of(_auto_id_record(fg_records, 'CalculatorResults'))!r}")

        # Hand the foreground back, using the AttachThreadInput trick Phase B established.
        restored = False
        if fg_original and fg_original != foreground_root():
            user32.SetForegroundWindow(wintypes.HWND(fg_original))
            time.sleep(0.4)
            restored = foreground_root() == fg_original
        print(f"  restored previous foreground: {restored}"
              f"  (now {foreground_root()})")

        time.sleep(0.8)
        bg_records = _uia_records(root)
        bg_accel = _accelerator_keys(bg_records)
        print(f"\n  [background] UIA nodes={len(bg_records)}  AcceleratorKey={len(bg_accel)}"
              f"  display={_name_of(_auto_id_record(bg_records, 'CalculatorResults'))!r}")

        if bg_accel:
            print("  background AcceleratorKeys:")
            for a in bg_accel:
                print(f"    {a['role']:<12} {a['name']!r:<30} {a['key']!r}")

        def display() -> str:
            return _name_of(_auto_id_record(_uia_records(root, 600),
                                            "CalculatorResults"))

        print("\n  posted-key probes against the BACKGROUND Calculator:")
        # Address BOTH windows. The frame is what a caller reaches from `list_windows`;
        # posting there does not reach the app. The CoreWindow is the app's own window.
        # Comparing the two IS the finding — probing only the frame produced a false
        # "posted keys cannot drive a UWP app".
        rows = []
        for tlabel, thwnd in (("frame", hwnd), ("CoreWindow", core_hwnd)):
            if not thwnd:
                continue
            for klabel, vk in (("VK_5", 0x35), ("VK_ESCAPE", 0x1B)):
                before_display = display()
                fg_before = foreground_root()
                _post(thwnd, WM_KEYDOWN, vk, lparam_down(vk))
                _post(thwnd, WM_KEYUP, vk, lparam_up(vk))
                time.sleep(args.settle + 0.4)
                rows.append({
                    "probe": f"{klabel} -> {tlabel}", "before": before_display,
                    "after": display(), "fg_changed": fg_before != foreground_root(),
                })
        for r in rows:
            changed = "CHANGED" if r["before"] != r["after"] else "no change"
            print(f"    {r['probe']:<22} {r['before']!r:<26} -> {r['after']!r:<26}"
                  f" {changed}  fg_changed={r['fg_changed']}")

        if args.json:
            with open(args.json + ".calculator.json", "w", encoding="utf-8") as fh:
                json.dump({"frame_hwnd": hwnd, "best_root": root,
                           "fg_original": fg_original,
                           "foreground_nodes": len(fg_records),
                           "background_nodes": len(bg_records),
                           "foreground_accel": fg_accel,
                           "background_accel": bg_accel,
                           "probes": rows, "restored_foreground": restored},
                          fh, ensure_ascii=False, indent=2)
            print(f"\n  wrote {args.json}.calculator.json")
        return 0
    finally:
        # Kill the APP's process — never the frame host.
        #
        # The frame's pid is `ApplicationFrameHost.exe`, which hosts EVERY packaged UWP app,
        # so `taskkill /F` on it tears down all of them at once. The previous version of
        # this block did exactly that while its comment claimed the opposite
        # ("Close only this Calculator, by pid — never every packaged app"). It was not
        # theoretical: Nahimic3.exe was in the inventory minutes before a run and gone
        # after it. Resolve the app's own window and refuse to kill by an unverified name.
        try:
            victim = core_hwnd or 0
            pid = wintypes.DWORD()
            if victim:
                user32.GetWindowThreadProcessId(wintypes.HWND(victim), ctypes.byref(pid))
            name = exe_name(victim).lower() if victim else ""
            if pid.value and name.startswith("calculator"):
                subprocess.run(["taskkill", "/F", "/PID", str(pid.value)],
                               capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                print(f"  cleanup: refusing to kill pid={pid.value or '?'} name={name!r} "
                      f"— not the Calculator app itself")
        except Exception:
            pass
        if fg_original:
            user32.SetForegroundWindow(wintypes.HWND(fg_original))


def _accelerator_keys(records: list[dict]) -> list[dict]:
    out = []
    for rec in records:
        try:
            val = rec["element"].GetCurrentPropertyValue(UIA_ACCELERATOR_KEY)
        except Exception:
            continue
        if isinstance(val, str) and val.strip():
            out.append({"role": rec["role"], "name": rec["name"][:32],
                        "key": val.strip()[:24]})
    return out


# --- Phase D: a real Chromium host --------------------------------------------
#
# Chromium is its own input stack — it does not consume keyboard the way a classic Win32
# control does, so nothing Phase B established transfers. Run it in an ISOLATED profile so
# nothing here touches the user's browser state, and use the document title as the oracle:
# it is readable with GetWindowText, needs no accessibility tree, and separates "a key
# EVENT arrived" from "a character was INSERTED", which fail differently.

EDGE_PATHS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")


def _edge_path() -> str:
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return p
    return ""


def _normal_window_view_ab(exe: str, page: str, marker: str) -> list[dict]:
    """Minimized vs restored on a NORMAL (tabbed) Edge window.

    The `--app` window in phase D refuted minimization as the cause of the control view's
    smaller tree, but an `--app` window has no tab strip by construction, so it cannot speak
    for the window class the desktop survey measured. This runs the same A/B on a normal
    tabbed window, and the question it answers is a shipped-behaviour question rather than a
    walker preference: if the page document disappears from the control view when the window
    is minimized, then `skyshot` silently reports chrome-only for a minimized browser.

    Returns [] (and says so) if the window never appears, rather than guessing.
    """
    if not exe or not page:
        return []
    workdir = tempfile.mkdtemp(prefix="dsh-cua-krv-")
    proc = subprocess.Popen(
        [exe, f"--user-data-dir={os.path.join(workdir, 'profile')}", "--no-first-run",
         "--no-default-browser-check", "--disable-sync", page],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    hwnd = 0
    out: list[dict] = []
    try:
        deadline = time.time() + 45.0
        while time.time() < deadline and not hwnd:
            for w in visible_titled_windows():
                # The `--app` window from phase D is still open and its title is ALSO
                # "<marker> START", so the tabbed one is identified by Edge's own title
                # suffix rather than by the marker alone.
                if (w["exe"].lower() == "msedge.exe" and w["title"].startswith(marker)
                        and "Microsoft" in w["title"]):
                    hwnd = w["hwnd"]
                    break
            time.sleep(0.15)
        if not hwnd:
            print("    (normal Edge window never appeared — NOT measured)")
            return []
        # Minimize BEFORE the first query, so the active tab's tree has never been built for
        # a VISIBLE window. That is the state the user's 16-tab Edge window was in, and it is
        # the only way to tell "the control view drops the page" apart from "Chromium never
        # built the page to begin with".
        user32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)
        time.sleep(1.5)
        for state, action in (("minimized (never queried)", None),
                              ("restored", SW_RESTORE),
                              ("minimized (after build)", SW_MINIMIZE)):
            if action is not None:
                user32.ShowWindow(wintypes.HWND(hwnd), action)
            time.sleep(1.5)
            v = _view_pair(hwnd, 4000, 30, False)
            v["state"] = f"normal/{state}"
            out.append(v)
        if out[0]["control"]["documents"] == 0 and out[1]["control"]["documents"] > 0:
            print("    => Chromium builds the active tab's tree only for a VISIBLE window: a")
            print("       minimized-before-query window has NO page, restoring builds it, and")
            print(f"       the tree then survives re-minimization "
                  f"({'document kept' if out[2]['control']['documents'] else 'document LOST'}).")
            print("       So a chrome-only skyshot means the page was never built, not that")
            print("       the control view hid it — and no read can build it while minimized.")
        else:
            print("    => NOT reproduced: a never-queried minimized window DID have its page")
        return out
    finally:
        # Our own isolated instance: same pid-scoped teardown phase D uses for its window.
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
        shutil.rmtree(workdir, ignore_errors=True)


def phase_chromium(args) -> int:
    print("=" * 78)
    print("PHASE D — a real Chromium host (Edge, isolated profile)")
    print("=" * 78)

    exe = _edge_path()
    if not exe:
        print("  SKIP: no Edge binary found")
        return 1

    marker = f"KRT-{os.getpid()}"
    workdir = tempfile.mkdtemp(prefix="dsh-cua-kr-")
    page = os.path.join(workdir, "kr.html")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(f"""<!doctype html><meta charset="utf-8"><title>{marker} START</title>
<body style="font:16px sans-serif">
<p>keyboard-routing probe page — safe to close</p>
<input id="t" style="width:420px;font-size:24px">
<script>
const MARK = "{marker}";
const t = document.getElementById('t');
t.focus();
document.addEventListener('keydown', e => {{ document.title = MARK + ' K=' + e.key; }});
t.addEventListener('input', () => {{ document.title = MARK + ' V=' + t.value; }});
</script></body>""")

    fg_original = foreground_root()
    profile = os.path.join(workdir, "profile")
    proc = subprocess.Popen(
        [exe, f"--user-data-dir={profile}", "--no-first-run",
         "--no-default-browser-check", "--disable-sync",
         f"--app=file:///{page.replace(os.sep, '/')}"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    hwnd = 0
    try:
        for _ in range(200):
            for w in visible_titled_windows():
                if w["exe"].lower() == "msedge.exe" and w["title"].startswith(marker):
                    hwnd = w["hwnd"]
                    break
            if hwnd:
                break
            time.sleep(0.15)
        if not hwnd:
            print("  FAILED: isolated Edge window never appeared")
            return 1
        time.sleep(1.0)

        print(f"  window    : {hwnd}  class={window_class(hwnd)}")
        print(f"  title     : {window_title(hwnd)!r}")
        print(f"  foreground: {foreground_root()}")
        # The window launches into the foreground, so the FOREGROUND pass is free — and it
        # has to run BEFORE the foreground is handed back. Measured the hard way: once this
        # process has given the foreground away, `SetForegroundWindow` cannot take it back
        # (the foreground lock), so probing foreground-first is the only ordering that needs
        # no raise at all. The first attempt restarted the window and then failed to raise it.
        launched_foreground = foreground_root() == hwnd
        if not launched_foreground:
            print("  NOT measuring the FOREGROUND case: this window did not finish launching "
                  "in the foreground, and a demoted process cannot raise it back")

        records = _uia_records(hwnd, 600)
        accel = _accelerator_keys(records)
        print(f"\n  UIA nodes={len(records)}  AcceleratorKey={len(accel)}")
        for a in accel[:8]:
            print(f"    {a['role']:<12} {a['name']!r:<30} {a['key']!r}")

        def title() -> str:
            return window_title(hwnd)

        def probe(label, sender):
            before = title()
            fg_before = foreground_root()
            with ForegroundWatch() as fw:
                extra = sender() or {}
                time.sleep(args.settle + 0.5)
            rows.append({"probe": label, "before": before, "after": title(),
                         "fg_changed": fg_before != foreground_root(),
                         "fg_watch": fw.report(), "extra": extra})

        rows: list[dict] = []

        def run_pass(label: str) -> None:
            probe(f"[{label}] WM_KEYDOWN '5' posted",
                  lambda: (_post(hwnd, WM_KEYDOWN, 0x35, lparam_down(0x35)),
                           _post(hwnd, WM_KEYUP, 0x35, lparam_up(0x35))))
            probe(f"[{label}] WM_CHAR '5' posted", lambda: _post(hwnd, WM_CHAR, ord("5"), 0))
            probe(f"[{label}] WM_KEYDOWN '5' + AttachThreadInput",
                  lambda: _attached_plain_key(hwnd, 0x35))

        # BOTH states. The first version of this phase handed the foreground back before
        # probing, then generalised from the deactivated case — and for Chromium the whole
        # answer is the ACTIVATION gate rather than the message route (audit C, instrument
        # gap 3). The window is this test's own, so the state can be an independent variable.
        if launched_foreground:
            run_pass("foreground")
        if fg_original:
            user32.SetForegroundWindow(wintypes.HWND(fg_original))
            time.sleep(0.4)
        print(f"  restored previous foreground: {foreground_root() == fg_original}")
        run_pass("background")

        print("\n  probes (title oracle: K=key event arrived, V=character inserted):")
        for r in rows:
            delta = "changed" if r["before"] != r["after"] else "NO CHANGE"
            w = r["fg_watch"]
            print(f"    {r['probe']:<50} {delta:<10} fg_changed={r['fg_changed']}"
                  f"  watched {w['samples']}x "
                  + ("CHANGED" if w["changed"] else "unchanged"))
            print(f"        before={r['before']!r}")
            print(f"        after ={r['after']!r}")

        # The view comparison runs LAST, on purpose: minimizing and restoring this window
        # would leave the oracle above measuring a window in a state the probes never saw.
        #
        # Why it exists: a desktop survey found the control view reporting fewer documents
        # than the raw view on 4 of 7 Chromium windows, and every one of those 4 was
        # minimized while both non-minimized windows were not. That is a correlation across
        # windows that differ in every other way too. This window is ours, so the state can
        # be an independent variable instead of a confound.
        print("\n  control view vs raw view, minimized vs restored (this window is ours to "
              "minimize):")
        views = []
        for state in ("restored", "minimized", "restored again"):
            if state == "minimized":
                user32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)
            elif state == "restored again":
                user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
            time.sleep(1.5)
            v = _view_pair(hwnd, 4000, 30, False)
            v["state"] = state
            views.append(v)
            print(f"    {state:<16} iconic={str(v['iconic']):<6}"
                  f" control={v['control']['nodes']:<6} named={len(v['control']['named']):<5}"
                  f" docs={v['control']['documents']:<3}"
                  f" | raw={v['raw']['nodes']:<6} named={len(v['raw']['named']):<5}"
                  f" docs={v['raw']['documents']:<3}"
                  f" hidden_named={len(v['hidden_named'])}")
        # Compare the three readings on their numbers, not on tuple identity: UIA walks are
        # not bit-stable, and the first version of this check called a 26-vs-25 difference
        # "the tree CHANGED with window state" — a one-node wobble read as an effect.
        counts = [(v["state"], v["control"]["nodes"]) for v in views[:3]]
        spread = max(c for _, c in counts) - min(c for _, c in counts)
        shown = ", ".join(f"{s}={c}" for s, c in counts)
        print(f"    => control-view node count by state: {shown} (spread {spread})")
        print("    => minimization is " + ("NOT" if spread <= 2 else "")
              + " what changes this tree")

        # ...but this is an --app window, which has no tab strip by construction, so it
        # cannot speak for a NORMAL browser window. That distinction is the whole question
        # behind the 34-node/no-document reading, so it gets its own controlled run.
        normal = _normal_window_view_ab(exe, page, marker)
        if normal:
            print("\n  same comparison on a NORMAL (tabbed) Edge window, since --app has no "
                  "tab strip:")
            for v in normal:
                print(f"    {v['state']:<16} iconic={str(v['iconic']):<6}"
                      f" control={v['control']['nodes']:<6}"
                      f" named={len(v['control']['named']):<5}"
                      f" docs={v['control']['documents']:<3}"
                      f" | raw={v['raw']['nodes']:<6}"
                      f" named={len(v['raw']['named']):<5}"
                      f" docs={v['raw']['documents']:<3}"
                      f" hidden_named={len(v['hidden_named'])}")
            views.extend(normal)

        if args.json:
            with open(args.json + ".chromium.json", "w", encoding="utf-8") as fh:
                json.dump({"hwnd": hwnd, "nodes": len(records), "accelerator_keys": accel,
                           "probes": rows,
                           "views": [{**{k: val for k, val in v.items()
                                         if k in ("state", "iconic")},
                                      "hidden_named": sorted(v["hidden_named"]),
                                      **{view: {**v[view], "named": sorted(v[view]["named"])}
                                         for view in ("control", "raw")}}
                                     for v in views]}, fh, ensure_ascii=False, indent=2)
            print(f"\n  wrote {args.json}.chromium.json")
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
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        if fg_original:
            user32.SetForegroundWindow(wintypes.HWND(fg_original))
        # Wait for the browser to actually exit before deleting the profile: Edge still
        # holds the directory open, and `ignore_errors=True` swallowed that failure and
        # leaked a `dsh-cua-kr-*` profile into %TEMP% on every run. Retry, then say so.
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        removed = False
        for _ in range(10):
            shutil.rmtree(workdir, ignore_errors=True)
            if not os.path.exists(workdir):
                removed = True
                break
            time.sleep(0.3)
        if not removed:
            print(f"  WARNING: temp profile not removed: {workdir}")


def _attached_plain_key(frame: int, vk: int) -> dict:
    """A single unmodified key, posted while the target's input queue is attached.

    Phase B showed the attach changes what the target's `GetKeyState` reports; this asks
    whether that is enough for a stack that does not read the Win32 key queue at all.
    """
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(wintypes.HWND(frame), ctypes.byref(pid))
    cur = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(cur, tid, True))
    arrival = []
    try:
        arrival = [
            _post(frame, WM_KEYDOWN, vk, lparam_down(vk)),
            _post(frame, WM_KEYUP, vk, lparam_up(vk)),
        ]
        time.sleep(0.30)
    finally:
        user32.AttachThreadInput(cur, tid, False)
    return {"attached": attached, "arrival": arrival}


# --- Phase E: does dsh-cua's own type_text land, and does its receipt say so? ---
#
# Phase B established a mechanism: `WM_CHAR` posted to a TOP-LEVEL window does not reach a
# child EDIT; posted to the EDIT, it does. `bridge.type_text` posts WM_CHAR to whatever
# hwnd it is handed, and `tool_type_text` defaults that to `foreground_window()` — a
# top-level window. So this is not academic. The question is whether the receipt can tell
# "posted" apart from "landed", which is the distinction this project exists to keep.

def phase_typetext(args) -> int:
    print("=" * 78)
    print("PHASE E — does bridge.type_text land, and does its receipt admit it?")
    print("=" * 78)

    from dsh_cua import bridge

    state_path = os.path.join(tempfile.gettempdir(), "kr-typetext-state.json")
    title = f"dsh cua typetext target {os.getpid()}"
    try:
        os.remove(state_path)
    except OSError:
        pass

    frame = 0
    fg_original = foreground_root()
    proc = subprocess.Popen(
        [sys.executable, FIXTURE, "--json", state_path, "--title", title],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            frame = user32.FindWindowW(None, title)
            if frame:
                break
            time.sleep(0.05)
        if not frame:
            print("  FAILED: fixture never appeared")
            return 1
        edit = _wait_for_edit(frame)
        if not edit:
            print("  FAILED: fixture's EDIT child never appeared — refusing to measure a "
                  "baseline that is an error string")
            return 1
        print(f"  frame hwnd={frame}   EDIT child hwnd={edit}")
        print(f"  EDIT starts as {_edit_text(frame)!r}\n")

        cases = [
            ("bridge.type_text(FRAME hwnd, 'AAA')  <- what the MCP tool defaults to",
             lambda: bridge.type_text(frame, "AAA")),
            ("bridge.type_text(EDIT hwnd, 'BBB')   <- the control that owns the text",
             lambda: bridge.type_text(edit, "BBB")),
        ]
        for label, fn in cases:
            before = _edit_text(frame)
            fg_before = foreground_root()
            with ForegroundWatch() as fw:
                receipt = fn() or {}
                time.sleep(0.4)
            after = _edit_text(frame)
            landed = before != after
            claims = bool(receipt.get("ok"))
            watch = fw.report()
            print(f"  {label}")
            print(f"      receipt   : ok={receipt.get('ok')} typed={receipt.get('typed')} "
                  f"reason={receipt.get('reason')!r}")
            print(f"      EDIT      : {before!r} -> {after!r}   "
                  f"{'LANDED' if landed else '*** DID NOT LAND ***'}")
            print(f"      fg changed: {fg_before != foreground_root()}   "
                  f"watched {watch['samples']}x, "
                  + (f"CHANGED -> {watch['distinct']}" if watch["changed"] else "never changed"))
            print(f"      SILENT FAILURE (receipt ok but nothing landed): "
                  f"{claims and not landed}\n")
        return 0
    finally:
        _reap(proc, frame)
        if fg_original:
            user32.SetForegroundWindow(wintypes.HWND(fg_original))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase",
                    choices=["inventory", "fixture", "calculator", "chromium", "typetext"],
                    help="inventory = read-only AcceleratorKey survey; "
                         "fixture = owned-window keyboard route matrix; "
                         "calculator = real UWP/XAML host; "
                         "chromium = real Chromium host in an isolated profile; "
                         "typetext = whether bridge.type_text lands and admits when it "
                         "does not")
    ap.add_argument("--windows", type=int, default=60,
                    help="max windows to walk. Every visible titled window is walked, not "
                         "one per (exe, class); the run says so if this cap bites")
    ap.add_argument("--compare-views", action="store_true",
                    help="inventory: also walk every window with RawViewWalker and report "
                         "what the control view hides. Identical pruning both times, so the "
                         "only difference is the view — which is the only way to test "
                         "_walk's claim that the extra raw-view nodes are scaffolding")
    ap.add_argument("--max-nodes", type=int, default=400,
                    help="UIA nodes per window (cross-process walks are slow)")
    ap.add_argument("--max-depth", type=int, default=25)
    ap.add_argument("--include-offscreen", action="store_true",
                    help="inventory: walk offscreen subtrees too. This is the one lever that "
                         "changes the AcceleratorKey count (6 of 14 keys on a real page live "
                         "offscreen); the default reports what the shipped skyshot sees")
    ap.add_argument("--settle", type=float, default=0.35,
                    help="seconds to let posted messages be processed before reading")
    ap.add_argument("--json", default="", help="also write raw results here")
    args = ap.parse_args()
    if args.phase == "inventory":
        return phase_inventory(args)
    if args.phase == "calculator":
        return phase_calculator(args)
    if args.phase == "chromium":
        return phase_chromium(args)
    if args.phase == "typetext":
        return phase_typetext(args)
    return phase_fixture(args)


if __name__ == "__main__":
    raise SystemExit(main())
