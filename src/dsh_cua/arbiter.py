"""Admission gate for mutating computer-use operations — the coexistence layer.

WHY THIS EXISTS
    The agent shares ONE cursor, ONE keyboard and ONE desktop with the user. The raw
    input path (SetCursorPos + mouse_event / keybd_event) injects into the same input
    stream the user is typing and moving in, and two agent sessions can race each
    other for the pointer. Three gates, layered (design sources: spec/06 §2.6 —
    read-only grading + pointer serialization; spec/08 §6 — the ZCode broker's
    read-only whitelist / central admission; spec/05 §6.2 — GetLastInputInfo yield):

      1. READ-ONLY GRADING    read-only operations never enter this module at all
                              (list_windows / find_window / get_window_rect /
                              capture_window / skyshot / read_element /
                              find_elements / element_at_point / cursor_pos /
                              clipboard_read / list_displays).
      2. MUTATING SERIALIZATION  every state-changing operation first takes a named
                              Win32 mutex (Local\\dsh-cua-input-arbiter), so multiple
                              agent processes (dsh spawns one MCP server per session)
                              can never mutate at the same time. Win32 mutexes are
                              thread-recursive, so nested gates inside one process
                              are safe. A crashed holder surfaces as WAIT_ABANDONED,
                              which we treat as acquired and report.
      3. HUMAN-CONTENTION YIELD  before injecting PHYSICAL input (raw click, global
                              hotkey), wait until the machine has been input-quiet
                              for QUIET_MS (GetLastInputInfo). Still not quiet after
                              MAX_WAIT_MS → refuse with reason "user-active" instead
                              of fighting the user for the cursor. Element actions
                              and PostMessage-based typing inject no physical input,
                              so they take the mutex but skip this wait (hard=False).

    Known limits, stated honestly:
      * The yield is a cooperation protocol, not a hard guarantee: input arriving in
        the milliseconds between the final check and the injection cannot be seen.
        A second, tighter check (FINAL_QUIET_MS) right before injection narrows the
        window but cannot close it.
      * GetLastInputInfo cannot tell the user's input from our own injected input, so
        raw-path bursts self-throttle to ~QUIET_MS per operation. Safe direction.
      * Programmatic activation (SetForegroundWindow inside _ensure_visible) is not
        itself an input event; a focus steal between the quiet check and the click is
        possible. The element-first doctrine is the real mitigation.

    Kill switch: DSH_CUA_ARBITER=0 disables every gate (tests / emergencies).
    Tunables: DSH_CUA_QUIET_MS (400), DSH_CUA_MAX_WAIT_MS (2000),
    DSH_CUA_MUTEX_TIMEOUT_MS (10000).
"""
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

ENABLED = os.environ.get("DSH_CUA_ARBITER", "1") != "0"
QUIET_MS = int(os.environ.get("DSH_CUA_QUIET_MS", "400"))
MAX_WAIT_MS = int(os.environ.get("DSH_CUA_MAX_WAIT_MS", "2000"))
MUTEX_TIMEOUT_MS = int(os.environ.get("DSH_CUA_MUTEX_TIMEOUT_MS", "10000"))
FINAL_QUIET_MS = 150

MUTEX_NAME = "Local\\dsh-cua-input-arbiter"

_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL
kernel32.GetTickCount.restype = wintypes.DWORD
# The mutex lives in kernel32, not user32.
kernel32.CreateMutexW.argtypes = [wintypes.HANDLE, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel32.ReleaseMutex.restype = wintypes.BOOL

_hmutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
_mutex_create_error = None if _hmutex else "CreateMutexW failed"
# Degradation path if the named mutex cannot be created (fails closed per process,
# serializes only within this process): a plain in-process lock.
_fallback_lock = threading.Lock()

# Optional forensic log of lock ordering (JSON lines): set DSH_CUA_ARBITER_LOG=<path>.
# Never fatal: logging problems must not break the gate.
_LOG_PATH = os.environ.get("DSH_CUA_ARBITER_LOG") or None


def _log(event: str, **fields) -> None:
    if not _LOG_PATH:
        return
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "event": event,
                                "t": time.time(), **fields},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


def input_age_ms():
    """Milliseconds since the last physical input event (mouse/keyboard), or None.

    Unsigned subtraction handles GetTickCount's ~49.7-day wrap."""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    return (kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF


def input_recently(ms: int = FINAL_QUIET_MS) -> dict:
    """True when an input event happened within the last `ms` milliseconds."""
    age = input_age_ms()
    return {"recent": bool(age is not None and age < ms), "last_input_age_ms": age}


def wait_input_quiet(quiet_ms: int = None, max_wait_ms: int = None,
                     poll_ms: int = 100) -> dict:
    """Block until the machine has been input-quiet for `quiet_ms`, or give up.

    Deterministic under test: pass explicit values (a huge `quiet_ms` always fails
    after `max_wait_ms` without needing a human to type)."""
    quiet_ms = QUIET_MS if quiet_ms is None else quiet_ms
    max_wait_ms = MAX_WAIT_MS if max_wait_ms is None else max_wait_ms
    t0 = time.perf_counter()
    while True:
        age = input_age_ms()
        waited = round((time.perf_counter() - t0) * 1000, 1)
        if age is None or age >= quiet_ms:
            return {"ok": True, "waited_ms": waited, "last_input_age_ms": age}
        if waited >= max_wait_ms:
            return {"ok": False, "waited_ms": waited, "last_input_age_ms": age}
        time.sleep(poll_ms / 1000.0)


def _release():
    if _hmutex:
        kernel32.ReleaseMutex(_hmutex)
    else:
        try:
            _fallback_lock.release()
        except RuntimeError:
            pass
    _log("released")


def admit_mutating(hard: bool) -> dict:
    """Admission for one mutating operation.

    hard=True  → mutex + human-contention yield (physical input is about to be
                 injected). hard=False → mutex only (element actions, PostMessage
                 typing, clipboard writes, app launches).

    Returns {"ok": True, "release": callable, ...info} or
            {"ok": False, "reason": "arbiter-busy"|"user-active", "error": ...}.
    The caller MUST call release() exactly once when ok — pair it with try/finally.
    """
    if not ENABLED:
        return {"ok": True, "disabled": True, "release": lambda: None,
                "waited_ms": 0, "quiet_waited_ms": 0}
    t0 = time.perf_counter()
    acquired = False
    abandoned = False
    if _hmutex:
        rc = kernel32.WaitForSingleObject(_hmutex, MUTEX_TIMEOUT_MS)
        if rc in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            acquired = True
            abandoned = (rc == _WAIT_ABANDONED)
    else:
        acquired = _fallback_lock.acquire(timeout=MUTEX_TIMEOUT_MS / 1000.0)
    waited_ms = round((time.perf_counter() - t0) * 1000, 1)
    if not acquired:
        return {"ok": False, "reason": "arbiter-busy", "waited_ms": waited_ms,
                "error": (f"another agent session holds the mutating lock; gave up "
                          f"after {waited_ms}ms of {MUTEX_TIMEOUT_MS}ms. Nothing was "
                          f"sent. Wait and retry, or tell the user another automation "
                          f"is running.")}
    _log("acquired", hard=hard, waited_ms=waited_ms, abandoned=abandoned,
         named_mutex=bool(_hmutex))
    quiet = {"ok": True, "waited_ms": 0, "last_input_age_ms": None}
    if hard:
        quiet = wait_input_quiet()
        if not quiet.get("ok"):
            _release()
            return {"ok": False, "reason": "user-active",
                    "waited_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "last_input_age_ms": quiet.get("last_input_age_ms"),
                    "error": (f"the user is actively using the machine (last input "
                              f"{quiet['last_input_age_ms']}ms ago; waited "
                              f"{quiet['waited_ms']}ms of {MAX_WAIT_MS}ms and it never "
                              f"went quiet). Nothing was injected. Do not retry "
                              f"immediately and do not force it: switch to the element "
                              f"path (no input injection), or tell the user what you "
                              f"need to do by hand.")}
    return {"ok": True, "release": _release, "waited_ms": waited_ms,
            "quiet_waited_ms": quiet.get("waited_ms", 0), "abandoned": abandoned,
            "named_mutex": bool(_hmutex)}


def receipt(gate: dict) -> dict:
    """Uniform gate receipt for every mutating result — success or failure.

    Callers need to tell "passed the gate and it was free" apart from "no gate was
    taken" and from "some path skipped the gate". Attaching a receipt only when the
    wait was long (the earlier behaviour) left the ordinary case indistinguishable from
    a bypass, so this is now attached unconditionally, and `taken` says which of the
    three it was. It carries no callable: `release` stays with the caller.
    """
    if not isinstance(gate, dict):
        return {"taken": False, "reason": "no gate taken",
                "waited_ms": 0.0, "quiet_waited_ms": 0.0}
    return {
        "taken": bool(gate.get("ok")),
        "waited_ms": round(float(gate.get("waited_ms") or 0), 1),
        "quiet_waited_ms": round(float(gate.get("quiet_waited_ms") or 0), 1),
        "named_mutex": bool(gate.get("named_mutex", _hmutex)),
        "abandoned": bool(gate.get("abandoned", False)),
        "disabled": bool(gate.get("disabled", not ENABLED)),
        **({"reason": gate["reason"]} if gate.get("reason") else {}),
    }


def no_gate(reason: str) -> dict:
    """Receipt for a call that by design takes no gate (a dry run)."""
    return {"taken": False, "reason": reason, "waited_ms": 0.0, "quiet_waited_ms": 0.0}


def state() -> dict:
    """Current policy — exposed as a read-only MCP tool for transparency."""
    return {"enabled": ENABLED, "quiet_ms": QUIET_MS, "max_wait_ms": MAX_WAIT_MS,
            "mutex_timeout_ms": MUTEX_TIMEOUT_MS, "final_quiet_ms": FINAL_QUIET_MS,
            "named_mutex": bool(_hmutex), "mutex_name": MUTEX_NAME,
            "degraded": not bool(_hmutex), "mutex_create_error": _mutex_create_error,
            "last_input_age_ms": input_age_ms()}
