"""A Win32 window that reports WHICH keyboard route reached it, and records what arrived.

WHY THIS EXISTS
    The question is not "does PostMessage deliver a key" — it plainly does, it puts the
    message in the target's queue. The question is which applications ACT on one, because
    that is what decides whether a hotkey can be de-gated from the physical-input path.

    Three plausible behaviours have to be separable, and a real app cannot tell you which
    one it exhibits:

      1. message-only    the handler trusts the message it was given (lParam scancode).
                         A posted key should satisfy this.
      2. getkeystate     the handler asks `GetKeyState`/`GetAsyncKeyState` whether Ctrl is
                         down. A posted key CANNOT satisfy this: PostMessage does not
                         update the OS keyboard state, so a background window always reads
                         "not pressed". This is the behaviour cua-driver documents for
                         `TranslateAccelerator` (LibreOffice, FAR, classic Notepad).
      3. accel-table     the message loop calls `TranslateAccelerator`, which places the
                         same `GetKeyState` requirement at the loop rather than the
                         handler. Same outcome, different place to look for it.

    A window this test owns separates them by construction: each route increments its own
    counter, so the answer is which counter moved, not whether "it worked".

    Out-of-process on purpose. In-process, `GetKeyState` would report a state the harness
    itself shares, and the `AttachThreadInput` probe — which needs a *second* thread to
    attach to — would degenerate into a no-op. The app must be a real neighbour, so the
    state file is the oracle rather than a Python attribute (the same reason
    `selfcontained_target.py` reads control state instead of trusting its own flag).

    WS_EX_NOACTIVATE + SW_SHOWNOACTIVATE: it never takes focus. Every probe here is a
    foreground-preservation claim, so a fixture that stole focus would invalidate its own
    measurement.

USAGE
    accelerator_target.py --json <state-file> [--title <title>] [--x N --y N]

    The state file is rewritten after every keyboard event. Fields:
      menu_accel        WM_COMMAND from the Ctrl+F accelerator table (route 3)
      keydown_message   WM_KEYDOWN handler that trusts lParam only (route 1)
      keydown_getkeystate  WM_KEYDOWN handler that consults GetKeyState (route 2)
      char_count        WM_CHAR messages received
      log               the last 40 keyboard messages verbatim, so "did not act" can be
                        distinguished from "never arrived"
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_OVERLAPPED = 0x00000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
ES_LEFT = 0x0000
ES_AUTOHSCROLL = 0x0080
ES_MULTILINE = 0x0004
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4

WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETFONT = 0x0030
WM_COMMAND = 0x0111
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E

DEFAULT_GUI_FONT = 17
ID_EDIT = 100
ID_MENU_FIRE = 2001

FVIRTKEY = 0x01
FSHIFT = 0x04
FCONTROL = 0x08
FALT = 0x10

MF_STRING = 0x00000000

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_F = 0x46
# Only Ctrl+F is in the accelerator table. Ctrl+G deliberately is not, so a probe can
# reach the WM_KEYDOWN handler without TranslateAccelerator consuming the message first —
# otherwise "route 3 fired" hides whether route 2 would have.
VK_G = 0x47

CLASS_NAME = "DshCuaAccelTarget"
DEFAULT_TITLE = "dsh cua accelerator target"

LRESULT = ctypes.c_longlong
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class ACCEL(ctypes.Structure):
    """Win32 ACCEL. ctypes' default alignment matches the SDK's (BYTE, pad, WORD, WORD)."""
    _fields_ = [("fVirt", wintypes.BYTE),
                ("key", wintypes.WORD),
                ("cmd", wintypes.WORD)]


# Explicit prototypes: ctypes defaults pointer-sized args to c_int, and a 64-bit LPARAM
# arriving through the window procedure then raises "int too long to convert".
_LRESULT = ctypes.c_longlong
user32.DefWindowProcW.restype = _LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.SendMessageW.restype = _LRESULT
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = _LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.CreateMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR]
user32.CreateAcceleratorTableW.restype = wintypes.HANDLE
user32.CreateAcceleratorTableW.argtypes = [ctypes.POINTER(ACCEL), ctypes.c_int]
user32.TranslateAcceleratorW.restype = ctypes.c_int
user32.TranslateAcceleratorW.argtypes = [wintypes.HWND, wintypes.HANDLE,
                                         ctypes.POINTER(wintypes.MSG)]
user32.GetKeyState.restype = ctypes.c_short
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.GetStockObject.argtypes = [ctypes.c_int]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class State:
    """Counters plus a verbatim log, written to disk after every keyboard event."""

    def __init__(self, path: str):
        self.path = path
        self.seq = 0
        self.menu_accel = 0
        self.keydown_message = 0
        self.keydown_getkeystate = 0
        self.syskey = 0
        self.char_count = 0
        self.log: list[dict] = []

    def note(self, msg: int, wparam: int, lparam: int, *, acted: str = "-"):
        if msg in (WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_COMMAND):
            # A monotonic sequence number, because the log is ring-buffered: the reader
            # cannot diff a capped list by position, and "which event arrived during THIS
            # probe" is the entire question.
            self.seq += 1
            self.log.append({"seq": self.seq, "msg": hex(msg),
                             "wparam": hex(wparam & 0xFFFFFFFF),
                             "lparam": hex(lparam & 0xFFFFFFFF), "acted": acted})
            del self.log[:-60]
        self.flush()

    def flush(self):
        blob = {"seq": self.seq,
                "menu_accel": self.menu_accel,
                "keydown_message": self.keydown_message,
                "keydown_getkeystate": self.keydown_getkeystate,
                "syskey": self.syskey,
                "char_count": self.char_count,
                "log": self.log,
                "updated": round(time.time(), 3)}
        # Temp-then-replace: the reader polls this file, and a torn read would look
        # exactly like the interesting failure.
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, ensure_ascii=False)
        os.replace(tmp, self.path)


class AcceleratorTarget:
    def __init__(self, state_path: str, title: str = DEFAULT_TITLE,
                 x: int = 60, y: int = 40, width: int = 560, height: int = 240):
        self.state = State(state_path)
        self.title = title
        self.x, self.y, self.w, self.h = x, y, width, height
        self.hwnd = None
        self.h_edit = None

    # --- the message loop -------------------------------------------------
    def run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wndproc)

        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, wintypes.LPCWSTR(32512))
        wc.hbrBackground = wintypes.HBRUSH(16)
        wc.lpszClassName = CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))

        # A real menu bar, so TranslateAccelerator has a command to fire at.
        hmenu = user32.CreateMenu()
        user32.AppendMenuW(hmenu, MF_STRING, ID_MENU_FIRE, "&Fire\tCtrl+F")

        self.hwnd = user32.CreateWindowExW(
            WS_EX_NOACTIVATE, CLASS_NAME, self.title,
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
            self.x, self.y, self.w, self.h, None, wintypes.HMENU(hmenu), hinst, None)

        font = gdi32.GetStockObject(DEFAULT_GUI_FONT)
        self.h_edit = user32.CreateWindowExW(
            0, "EDIT", "EDIT-CONTENT",
            WS_CHILD | WS_VISIBLE | ES_LEFT | ES_AUTOHSCROLL | ES_MULTILINE | WS_BORDER,
            20, 20, 500, 120, wintypes.HWND(self.hwnd), wintypes.HMENU(ID_EDIT), hinst, None)
        user32.SendMessageW(wintypes.HWND(self.h_edit), WM_SETFONT,
                            wintypes.WPARAM(font), 1)

        # Ctrl+F, routed through the accelerator table in the loop below.
        accels = (ACCEL * 1)()
        accels[0].fVirt = FVIRTKEY | FCONTROL
        accels[0].key = VK_F
        accels[0].cmd = ID_MENU_FIRE
        self.h_accel = user32.CreateAcceleratorTableW(accels, 1)

        user32.ShowWindow(wintypes.HWND(self.hwnd), SW_SHOWNOACTIVATE)
        user32.UpdateWindow(wintypes.HWND(self.hwnd))
        self.state.flush()
        print(f"HWND={self.hwnd}", flush=True)

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            # Route 3: the classic loop. TranslateAccelerator decides whether to turn
            # this WM_KEYDOWN into a WM_COMMAND, and it does so by reading the keyboard
            # state — which a posted message never set.
            if self.h_accel and user32.TranslateAcceleratorW(
                    wintypes.HWND(self.hwnd), self.h_accel, ctypes.byref(msg)):
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _edit_text(self) -> str:
        n = user32.SendMessageW(wintypes.HWND(self.h_edit), WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.SendMessageW(wintypes.HWND(self.h_edit), WM_GETTEXT, n + 1,
                            ctypes.cast(buf, ctypes.c_void_p).value)
        return buf.value

    # --- routes -----------------------------------------------------------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_COMMAND:
            cid = wparam & 0xFFFF
            code = (wparam >> 16) & 0xFFFF
            # An accelerator-originated WM_COMMAND carries notification code 1, a menu
            # click carries 0. Checking only for 0 silently swallowed the single event
            # this fixture exists to detect: the first run reported "accelerator never
            # fired" while the raw log showed WM_COMMAND 0x107D1 (id 2001, code 1).
            if cid == ID_MENU_FIRE and code in (0, 1):
                self.state.menu_accel += 1
                self.state.note(msg, wparam, lparam, acted=f"menu_accel(code={code})")
                return 0
            self.state.note(msg, wparam, lparam)
            return 0

        if msg == WM_KEYDOWN:
            vk = wparam & 0xFFFF
            # Both routes are evaluated on the SAME message and neither short-circuits the
            # other. An early return here would make route 2 unreachable whenever route 1
            # matched, and the whole point is to see them disagree.
            acted: list[str] = []
            # Route 1: trust the message. lParam bits 16-23 carry the scancode.
            scancode = (lparam >> 16) & 0xFF
            if vk in (VK_F, VK_G) and scancode:
                self.state.keydown_message += 1
                acted.append("keydown_message")
            # Route 2: ask the OS. A background window reads "not pressed".
            if vk in (VK_F, VK_G) and (user32.GetKeyState(VK_CONTROL) & 0x8000):
                self.state.keydown_getkeystate += 1
                acted.append("keydown_getkeystate")
            self.state.note(msg, wparam, lparam, acted="+".join(acted) or "-")
            return 0

        if msg == WM_CHAR:
            self.state.char_count += 1
            self.state.note(msg, wparam, lparam, acted=f"char={chr(wparam & 0xFFFF)!r}")
            return user32.DefWindowProcW(wintypes.HWND(hwnd), msg, wparam, lparam)

        if msg == WM_SYSKEYDOWN:
            vk = wparam & 0xFFFF
            acted = []
            if vk == VK_F:
                self.state.syskey += 1
                acted.append("syskey")
            self.state.note(msg, wparam, lparam, acted="+".join(acted) or "-")
            return 0

        if msg in (WM_KEYUP, WM_SYSKEYUP):
            self.state.note(msg, wparam, lparam)
            return 0

        if msg == WM_CLOSE:
            user32.DestroyWindow(wintypes.HWND(hwnd))
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(wintypes.HWND(hwnd), msg, wparam, lparam)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="state file to rewrite on every event")
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--x", type=int, default=60)
    ap.add_argument("--y", type=int, default=40)
    args = ap.parse_args()
    AcceleratorTarget(args.json, args.title, args.x, args.y).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
