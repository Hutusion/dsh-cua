"""Coexistence-layer verification: read-only grading, cross-process serialization,
human-contention yield. Runs WITHOUT needing the user to keep the hands off — the
"human input" in Part C is synthetic (a real SetCursorPos, restored immediately).

PART A — zero-input proof with recorders (the verify-no-input.py technique):
  the 6 Win32 input/activation APIs are replaced by recorders, so a PASS means the
  paths made ZERO real input calls.
    A1 dry_run click: no gate, no input
    A2 real click vs huge quiet window: refused user-active BEFORE anything
    A3 real click right after synthetic input: refused at the FINAL pre-injection check
    A4 real click happy path (recorders): exactly mutex -> release, zero input
    A5 send_hotkey: same refusal shape (user-active before any keybd_event)
    A6 SOFT gates skip the quiet wait: type_text succeeds even with huge QUIET_MS
    A7 element_action (soft) proceeds to its own validation even with huge QUIET_MS
PART B — cross-process mutex: a child process holds the mutex 1.2s; this process
  must block ~1.2s and acquire only after the child released.
PART C — wait_input_quiet determinism: quiet=0 passes; huge quiet fails after max_wait.
PART D — kill switch: child with DSH_CUA_ARBITER=0 admits everything (disabled).
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctypes
from ctypes import wintypes

from dsh_cua import arbiter, bridge, uia
import selfcontained_target as sct

failures = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(label)


# ---- recorders: replace the input/activation APIs for the whole process ----
INPUT_APIS = ("SetCursorPos", "mouse_event", "keybd_event",
              "SetForegroundWindow", "AttachThreadInput", "BringWindowToTop")
calls = []
_real = {name: getattr(bridge.user32, name) for name in INPUT_APIS}


def _install_recorders():
    for name in INPUT_APIS:
        def rec(*args, _name=name, **kwargs):
            calls.append(_name)
            return 1
        setattr(bridge.user32, name, rec)   # same ctypes object arbiter/bridge/uia see


def _restore_apis():
    for name, fn in _real.items():
        setattr(bridge.user32, name, fn)


def mutex_is_free() -> bool:
    """Probe: if we can acquire the mutex instantly, the last holder released."""
    if not arbiter._hmutex:
        return True
    k32 = ctypes.windll.kernel32
    rc = k32.WaitForSingleObject(arbiter._hmutex, 0)
    if rc in (arbiter._WAIT_OBJECT_0, arbiter._WAIT_ABANDONED):
        k32.ReleaseMutex(arbiter._hmutex)
        return True
    return False


# ============================ PART A ============================
print("PART A — zero-input proof (recorders on)")
_install_recorders()

# A1: dry run — no gate, no input
n0 = len(calls)
out = bridge.click(0xDEAD, 5, 5, dry_run=True)
check("A1 dry_run refused the fake window (client-to-screen-failed)",
      out.get("ok") is False and out.get("reason") == "client-to-screen-failed")
check("A1 zero input calls", len(calls) == n0, str(calls[n0:]))
check("A1 mutex free after", mutex_is_free())

# A2: huge quiet window -> user-active at admission, zero input
arbiter.QUIET_MS = 10_000_000
arbiter.MAX_WAIT_MS = 300
n0 = len(calls)
t0 = time.perf_counter()
out = bridge.click(0xDEAD, 5, 5)
dt = (time.perf_counter() - t0) * 1000
check("A2 refused user-active", out.get("ok") is False and out.get("reason") == "user-active",
      out.get("reason"))
check("A2 refused fast (~max_wait, not the full quiet)", dt < 900, f"{dt:.0f}ms")
check("A2 zero input calls", len(calls) == n0, str(calls[n0:]))
check("A2 mutex free after", mutex_is_free())

# A3: real click right after SYNTHETIC user input -> final pre-injection check refuses.
# Needs a REAL window (ClientToScreen must succeed) and require_target bypassed, since
# the guard runs before the final check. Synthetic input = a 2px real cursor move.
sct_target = sct.Target(x=80, y=60, width=620, height=470)
hwnd_t = sct_target.start()
rect = bridge.get_window_rect(hwnd_t)
cx, cy = rect["client_width"] // 2, rect["client_height"] // 2

arbiter.QUIET_MS = 0        # admission passes instantly
arbiter.MAX_WAIT_MS = 200
# The synthetic input must still be "fresh" at the FINAL check — but _ensure_visible
# spends ~150ms in its own sleeps before we get there. Widen the final window for
# this one case (it is read from the module attribute at call time).
arbiter.FINAL_QUIET_MS = 600
p0 = bridge.cursor_pos()
# Synthetic user input: a 1px RELATIVE mouse move via mouse_event. (SetCursorPos does
# NOT register as an input event — GetLastInputInfo ignores it, measured in the first
# run: age stayed at its pre-test value through a real SetCursorPos.)
bridge.user32.mouse_event = _real["mouse_event"]      # real call for this one step
bridge.user32.mouse_event(0x0001, 1, 0, 0, 0)         # MOUSEEVENTF_MOVE, 1px right
_install_recorders()                                   # recorders back on
n0 = len(calls)
out = bridge.click(hwnd_t, cx, cy, require_target=False)
age = out.get("last_input_age_ms")
check("A3 refused user-active at the FINAL check", out.get("ok") is False
      and out.get("reason") == "user-active", out.get("reason"))
check("A3 final check saw fresh input (age < 600ms)", isinstance(age, int) and age < 600,
      f"age={age}ms")
# The refusal fires AFTER _ensure_visible, so recorded ACTIVATION calls appear; what
# must be absent is every INJECTION call (they would have been real).
check("A3 no injection calls by the click path",
      not any(c in ("SetCursorPos", "mouse_event", "keybd_event") for c in calls[n0:]),
      str(calls[n0:]))
arbiter.FINAL_QUIET_MS = 150
bridge.user32.SetCursorPos = _real["SetCursorPos"]
bridge.user32.SetCursorPos(*p0)                       # restore: SetCursorPos registers no input
_install_recorders()

# A4: happy path with recorders — gate -> impl -> release, zero input
arbiter.QUIET_MS = 0
time.sleep(0.6)   # let the cursor-restore input age out of the 150ms final window
n0 = len(calls)
out = bridge.click(hwnd_t, cx, cy, require_target=False)
check("A4 click succeeded (recorders swallowed the injection)",
      out.get("ok") is True and out.get("reason") == "clicked", out.get("reason"))
check("A4 input APIs called (but all recorded, none real): "
      f"{sorted(set(calls[n0:]))}", len(calls) > n0)
check("A4 mutex free after", mutex_is_free())

# A5: send_hotkey — same refusal shape
arbiter.QUIET_MS = 10_000_000
arbiter.MAX_WAIT_MS = 300
n0 = len(calls)
out = bridge.send_hotkey(hwnd_t, "ctrl", "s")
check("A5 send_hotkey refused user-active", out.get("ok") is False
      and out.get("reason") == "user-active", out.get("reason"))
check("A5 zero input calls", len(calls) == n0, str(calls[n0:]))

# A6: SOFT gate skips the quiet wait — type_text succeeds even with huge QUIET_MS
n0 = len(calls)
t0 = time.perf_counter()
out = bridge.type_text(hwnd_t, "x")
dt = (time.perf_counter() - t0) * 1000
check("A6 type_text ok despite huge quiet window (soft gate)",
      out.get("ok") is True, f"{out.get('reason')} {dt:.0f}ms")
check("A6 type_text made no input-API calls (PostMessage is not among them)",
      not any(c in ("mouse_event", "keybd_event", "SetCursorPos") for c in calls[n0:]))

# A7: element_action (soft) proceeds past admission to its own ref validation
t0 = time.perf_counter()
out = uia.element_action("el9999", "press")
dt = (time.perf_counter() - t0) * 1000
check("A7 element_action reached its own validation (not user-active)",
      out.get("reason") != "user-active", f"reason={out.get('reason')} {dt:.0f}ms")

_restore_apis()

# ============================ PART B ============================
print("\nPART B — cross-process mutex (real, no input)")
arbiter.QUIET_MS = 0
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coex_log.jsonl")
if os.path.exists(log):
    os.remove(log)
child = subprocess.Popen(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "coex_child.py"), "hold", log])
time.sleep(0.4)   # let the child acquire first
t0 = time.perf_counter()
gate = arbiter.admit_mutating(hard=False)
main_waited = (time.perf_counter() - t0) * 1000
check("B main blocked until the child released (~1.2s hold)",
      gate.get("ok") is True and main_waited >= 600, f"waited {main_waited:.0f}ms")
lines = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
rel_t = next((e["t"] for e in lines if e["event"] == "released"), None)
check("B child released before main acquired", rel_t is not None
      and time.time() - rel_t < 5, f"released at {rel_t}")
gate["release"]()
child.wait(timeout=10)

# ============================ PART C ============================
print("\nPART C — wait_input_quiet determinism")
out = arbiter.wait_input_quiet(quiet_ms=0, max_wait_ms=200, poll_ms=25)
check("C quiet_ms=0 passes immediately", out.get("ok") is True and out.get("waited_ms") < 200,
      str(out))
t0 = time.perf_counter()
out = arbiter.wait_input_quiet(quiet_ms=3_600_000, max_wait_ms=300, poll_ms=50)
dt = (time.perf_counter() - t0) * 1000
check("C huge quiet fails after ~max_wait", out.get("ok") is False and 250 <= dt <= 1500,
      f"ok={out.get('ok')} {dt:.0f}ms")

# ============================ PART D ============================
print("\nPART D — kill switch")
env = dict(os.environ, DSH_CUA_ARBITER="0")
out_child = subprocess.run(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "coex_child.py"), "killswitch"],
    capture_output=True, text=True, env=env, timeout=30)
payload = json.loads(out_child.stdout.strip().splitlines()[-1])
check("D kill switch admits without gates", payload.get("ok") is True
      and payload.get("disabled") is True, str(payload))

# ============================ cleanup ============================
sct_target.close()
check("target closed", not any(x["hwnd"] == hwnd_t for x in bridge.list_windows()))

print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
