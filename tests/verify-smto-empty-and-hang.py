"""Two things that must be measured, not assumed, before fixing the read-back.

(1) Is `SendMessageTimeoutW`'s return value a SUCCESS FLAG, or `result != 0`?

The non-empty case already rules out "it is the result" (WM_GETTEXTLENGTH answers 12, the return
value was 1). But "it is `result != 0`" is still consistent with everything measured so far, and
that reading would make an EMPTY control look unreadable — turning the most common case (typing
into an empty field) into `effect_verified: null`. Only a genuinely empty CROSS-PROCESS control
can exclude it. Two earlier attempts to empty the fixture's EDIT silently failed (a temporary
buffer passed to PostMessageW, then SetWindowTextW) and the probe reported that rather than
measuring the non-empty case twice; this uses charmap.exe, whose RICHEDIT50W starts empty.

(2) Does the timeout bound a target that does not pump messages, and does the CURRENT shipped
   implementation block on it? The second half runs in a CHILD PROCESS, because if the current
   implementation really does block, this probe would block with it — that is the whole claim.

ctypes note: every callback here is built with `bridge.EnumWindowsProc`, because `bridge` assigns
its OWN WINFUNCTYPE class to the shared `user32.EnumWindows` argtypes at import time. Building a
callback from any other module's WINFUNCTYPE raises "expected WinFunctionType instance instead of
WinFunctionType" — a trap this project's own audit B hit.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import POINTER, byref
from ctypes import wintypes as w

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = HERE
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from dsh_cua import bridge  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
SMTO_ABORTIFHUNG = 0x0002
THREAD_SUSPEND_RESUME = 0x0002
WM_CLOSE = 0x0010

user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.SendMessageTimeoutW.argtypes = [
    w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, POINTER(ctypes.c_size_t)]
user32.EnumWindows.argtypes = [bridge.EnumWindowsProc, w.LPARAM]
user32.EnumChildWindows.argtypes = [w.HWND, bridge.EnumWindowsProc, w.LPARAM]


def smto(hwnd: int, msg: int, wp: int, lp: int, timeout: int):
    """(elapsed_ms, return_value, lpdwResult) for one SendMessageTimeoutW."""
    res = ctypes.c_size_t(0)
    t0 = time.perf_counter()
    ret = user32.SendMessageTimeoutW(w.HWND(hwnd), msg, wp, lp, SMTO_ABORTIFHUNG, timeout,
                                     byref(res))
    return (time.perf_counter() - t0) * 1000.0, ret, res.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(w.HWND(hwnd), buf, 256)
    return buf.value


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(w.HWND(hwnd), buf, 512)
    return buf.value


def exe_of(hwnd: int) -> str:
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(w.HWND(hwnd), byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)
    return "?"


def top_windows() -> list[int]:
    out: list[int] = []

    def cb(h, _lp):
        if user32.IsWindowVisible(h) and window_title(h):
            out.append(int(h))
        return True

    user32.EnumWindows(bridge.EnumWindowsProc(cb), 0)
    return out


def descendants(hwnd: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def cb(child, _lp):
        out.append((int(child), window_class(int(child))))
        return True

    user32.EnumChildWindows(w.HWND(hwnd), bridge.EnumWindowsProc(cb), 0)
    return out


def wait_for_edit(frame: int, timeout: float = 6.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        edit = user32.FindWindowExW(w.HWND(frame), None, "EDIT", None)
        if edit:
            return int(edit)
        time.sleep(0.05)
    return 0


def find_charmap_edit(timeout: float = 20.0):
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "charmap.exe")
    if not os.path.exists(exe):
        print("  charmap.exe not present — part 1 cannot run on this machine")
        return 0, 0, None
    before = set(top_windows())
    proc = subprocess.Popen([exe], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.time() + timeout
    while time.time() < deadline:
        for win in top_windows():
            if win in before or exe_of(win).lower() != "charmap.exe":
                continue
            for child, cls in descendants(win):
                low = cls.lower()
                if low.startswith("richedit") or low == "edit":
                    return win, child, proc
        time.sleep(0.2)
    return 0, 0, proc


def part1() -> None:
    print("=" * 78)
    print("PART 1 — is the return value a success flag, or `result != 0`?")
    print("=" * 78)
    frame, edit, proc = find_charmap_edit()
    try:
        if not edit:
            print("  could not obtain an empty cross-process text control — NOT MEASURED")
            return
        print(f"  charmap frame={frame} edit={edit} class={window_class(edit)}")
        ms, ret, res = smto(edit, bridge.WM_GETTEXTLENGTH, 0, 0, 1500)
        print(f"  EMPTY control   WM_GETTEXTLENGTH -> ret={ret} lpdwResult={res} ({ms:.0f} ms)")

        # Same control, same call, only the content differs — and it is filled through the
        # SHIPPED path, which doubles as a live check of the fix under review.
        receipt = None
        try:
            receipt = bridge.type_text(edit, "XY")
        except Exception as exc:
            print(f"  (type_text raised: {type(exc).__name__}: {exc})")
        time.sleep(0.4)
        ms2, ret2, res2 = smto(edit, bridge.WM_GETTEXTLENGTH, 0, 0, 1500)
        print(f"  NON-EMPTY       WM_GETTEXTLENGTH -> ret={ret2} lpdwResult={res2} ({ms2:.0f} ms)")
        if receipt:
            print(f"  type_text: ok={receipt.get('ok')} "
                  f"effect_verified={receipt.get('effect_verified')} "
                  f"resolved_by={receipt.get('resolved_by')!r}")

        print("\n  VERDICT")
        if ret != 0 and res == 0:
            print("    ret is a SUCCESS FLAG: an empty control answers successfully (result 0)")
            print("    and still returns non-zero. `ret == 0` is safe as 'did not answer'.")
        elif ret == 0:
            print("    ret is NOT a plain success flag — an empty control returned 0. Guarding")
            print("    on `ret == 0` would misreport every empty control as unreadable.")
        else:
            print(f"    inconclusive: ret={ret} lpdwResult={res}")
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


def part2() -> None:
    print()
    print("=" * 78)
    print("PART 2 — does the timeout bound a target that does not pump messages?")
    print("=" * 78)
    state = os.path.join(tempfile.gettempdir(), "smto2-state.json")
    try:
        os.remove(state)
    except OSError:
        pass
    title = f"dsh cua smto hang probe {os.getpid()}"
    fx = 0
    proc = subprocess.Popen(
        [sys.executable, os.path.join(TESTS, "accelerator_target.py"),
         "--json", state, "--title", title],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not fx:
            fx = user32.FindWindowW(None, title)
            time.sleep(0.05)
        edit = wait_for_edit(fx) if fx else 0
        if not edit:
            print("  fixture/EDIT never appeared — NOT MEASURED")
            return
        print(f"  fixture frame={fx} edit={edit}")
        ms, ret, res = smto(edit, bridge.WM_GETTEXTLENGTH, 0, 0, 1200)
        print(f"  responsive target   -> ret={ret} lpdwResult={res} ({ms:.0f} ms)")

        tid = user32.GetWindowThreadProcessId(w.HWND(edit), None)
        handle = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
        if not handle:
            print(f"  could not open thread {tid} — hang case NOT MEASURED")
            return
        kernel32.SuspendThread.argtypes = [w.HANDLE]
        kernel32.ResumeThread.argtypes = [w.HANDLE]
        suspended = False
        child_env = dict(os.environ)
        child_env["SMTO_TARGET"] = str(edit)
        try:
            prev = kernel32.SuspendThread(handle)
            suspended = prev != 0xFFFFFFFF
            print(f"  suspended UI thread {tid} (previous count {prev})")
            time.sleep(0.2)
            ms, ret, res = smto(edit, bridge.WM_GETTEXTLENGTH, 0, 0, 1200)
            print(f"  NON-PUMPING target  -> ret={ret} lpdwResult={res} ({ms:.0f} ms)")
            bounded = ret == 0 and ms < 4000
            print(f"\n  VERDICT: the bounded call "
                  f"{'returned and reported failure' if bounded else 'did NOT bound'} "
                  f"({ms:.0f} ms, ret={ret})")
            if bounded:
                print("    A non-pumping target is bounded and reported as a read failure. The")
                print("    synchronous SendMessageW it replaces has no such bound — and it sits")
                print("    inside type_text's `try`, so a block there also means")
                print("    `finally: gate['release']()` never runs and the cross-process mutex")
                print("    stays held for every later mutating call from every session.")

            # What the CURRENT shipped implementation does in the same state. In a child
            # process, because if the claim is true this call never returns.
            print("\n  current shipped _read_control_text() against the same non-pumping "
                  "control:")
            code = ("import ctypes,sys;sys.path.insert(0,r'%s');"
                    "from dsh_cua import bridge;"
                    "print('RETURNED', repr(bridge._read_control_text(int(sys.argv[1]))))"
                    % os.path.join(HERE, "..", "src"))
            t0 = time.perf_counter()
            try:
                out = subprocess.run([sys.executable, "-c", code, str(edit)],
                                     capture_output=True, text=True, timeout=8,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                el = time.perf_counter() - t0
                print(f"    returned in {el:.1f}s: {out.stdout.strip() or out.stderr.strip()}")
            except subprocess.TimeoutExpired:
                el = time.perf_counter() - t0
                print(f"    *** STILL BLOCKED after {el:.1f}s -> killed. This is the defect: "
                      f"unbounded ***")
            print("    (the child also proves the block is not an artifact of this process)")
        finally:
            if suspended:
                kernel32.ResumeThread(handle)
                print(f"  resumed thread {tid}")
            kernel32.CloseHandle(handle)
    finally:
        try:
            if fx:
                user32.PostMessageW(w.HWND(fx), WM_CLOSE, 0, 0)
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main() -> int:
    part1()
    part2()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
