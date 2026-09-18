"""A self-contained target window for action verification.

WHY THIS EXISTS
    The first two verification attempts used Notepad. That was wrong twice over:
      * Win11 Notepad is tabbed and reuses one process, so `notepad.exe <file>` ADDS A
        TAB to whatever session the user already had open — it polluted their window
        with dead tabs.
      * Notepad takes focus on launch, so the "acts without focus" precondition could
        not be established reliably.
    A target the test owns removes both problems and never touches a user window.

WHAT IT PROVIDES
    A plain Win32 window with an EDIT field and a BUTTON, created with WS_EX_NOACTIVATE
    and shown with SW_SHOWNOACTIVATE, so it does NOT take focus. Pressing the button
    bumps a counter and writes it into a STATIC label, which makes the button's effect
    observable through UIA — so an action can be verified by effect, not by its own
    return value.

    This also means the one thing `Invoke` could never confirm (see spec/07 §5.5) can be
    confirmed here: a press whose effect we designed to be visible.
"""
import ctypes
import threading
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
WS_TABSTOP = 0x00010000
ES_LEFT = 0x0000
ES_AUTOHSCROLL = 0x0080
BS_PUSHBUTTON = 0x00000000
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_SETFONT = 0x0030
DEFAULT_GUI_FONT = 17
ID_EDIT = 100
ID_BUTTON = 101
ID_LABEL = 102
ID_ROEDIT = 103
ID_CHECK = 104
ID_LIST = 105
ID_COMBO = 106
ES_READONLY = 0x0800
BS_AUTOCHECKBOX = 0x00000003
CBS_DROPDOWNLIST = 0x0003
WS_VSCROLL = 0x00200000
LBS_NOTIFY = 0x0001
CB_ADDSTRING = 0x0143
LB_ADDSTRING = 0x0180
LB_SETCURSEL = 0x0186
LB_GETCURSEL = 0x0188
BM_GETCHECK = 0x00F0
CB_GETDROPPEDSTATE = 0x0157

CLASS_NAME = "DshCuaTestWindow"
WINDOW_TITLE = "dsh cua test target"

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


# Explicit prototypes. Without them ctypes defaults to c_int for pointer-sized
# parameters, and a 64-bit LPARAM coming back through the window procedure raises
# "int too long to convert".
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
user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.SendMessageW.restype = _LRESULT
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = _LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.GetStockObject.argtypes = [ctypes.c_int]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class Target:
    """A Win32 test window plus its observable state."""

    def __init__(self, x=60, y=40, width=620, height=470):
        self.hwnd = None
        self.click_count = 0
        self.label_text = "idle"
        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(x, y, width, height),
                                        daemon=True)

    # --- observable state, readable from the test thread -------------------
    def snapshot(self):
        """State read straight from the Win32 controls.

        Deliberately NOT a Python-side flag: reading the real control state gives an
        INDEPENDENT source of truth, so a UIA pattern reporting success can be
        cross-checked against what the control actually did.
        """
        def send(h, msg, wp=0, lp=0):
            if not h:
                return None
            return user32.SendMessageW(wintypes.HWND(h), msg, wp, lp)

        return {
            "click_count": self.click_count,
            "label_text": self.label_text,
            "checkbox_checked": send(self.h_check, BM_GETCHECK),
            "list_sel_index": send(self.h_list, LB_GETCURSEL),
            "combo_dropped": send(self.h_combo, CB_GETDROPPEDSTATE),
        }

    def start(self, timeout=10.0):
        self._thread.start()
        if not self.ready.wait(timeout):
            raise RuntimeError("target window did not come up")
        return self.hwnd

    def close(self):
        if self.hwnd:
            user32.PostMessageW(wintypes.HWND(self.hwnd), WM_CLOSE, 0, 0)
        self._thread.join(timeout=3)

    # --- window plumbing ---------------------------------------------------
    def _run(self, x, y, width, height):
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wndproc)   # keep alive
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, wintypes.LPCWSTR(32512))  # IDC_ARROW
        wc.hbrBackground = wintypes.HBRUSH(16)                          # COLOR_BTNFACE+1
        wc.lpszClassName = CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            WS_EX_NOACTIVATE, CLASS_NAME, WINDOW_TITLE,
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
            x, y, width, height, None, None, hinst, None)
        if not self.hwnd:
            self.ready.set()
            return

        font = gdi32.GetStockObject(DEFAULT_GUI_FONT)

        def child(cls, text, style, cid, cx, cy, cw, ch):
            h = user32.CreateWindowExW(0, cls, text, WS_CHILD | WS_VISIBLE | style,
                                       cx, cy, cw, ch, wintypes.HWND(self.hwnd),
                                       wintypes.HMENU(cid), hinst, None)
            user32.SendMessageW(wintypes.HWND(h), WM_SETFONT, wintypes.WPARAM(font), 1)
            return h

        self.h_edit = child("EDIT", "PLACEHOLDER", ES_LEFT | ES_AUTOHSCROLL | WS_BORDER,
                            ID_EDIT, 20, 20, 400, 26)
        # A read-only edit: its ValuePattern exists but SetValue is inert, which is the
        # silent-success case that bit us on a real title bar. Having one here means the
        # guard is exercised on every run instead of only in the field.
        self.h_ro = child("EDIT", "READONLY-CONTENT",
                          ES_LEFT | ES_AUTOHSCROLL | WS_BORDER | ES_READONLY,
                          ID_ROEDIT, 20, 100, 400, 26)
        self.h_button = child("BUTTON", "Press me", BS_PUSHBUTTON | WS_TABSTOP,
                              ID_BUTTON, 20, 60, 130, 32)
        self.h_label = child("STATIC", self.label_text, 0, ID_LABEL, 170, 66, 250, 24)

        # Controls that expose the OTHER patterns, so every action in ACTION_MAP can be
        # exercised here rather than on some application the test does not own.
        # Named buffers, not inline temporaries: a c_wchar_p built inside the call
        # expression can be collected before SendMessage reads it, which showed up as
        # garbled item names ('議丛ů') in the first run.
        def add_string(h, msg, s):
            buf = ctypes.create_unicode_buffer(s)
            user32.SendMessageW(wintypes.HWND(h), msg, 0,
                                ctypes.cast(buf, ctypes.c_void_p).value)

        self.h_check = child("BUTTON", "Enable thing", BS_AUTOCHECKBOX | WS_TABSTOP,
                             ID_CHECK, 20, 140, 160, 24)
        self.h_combo = child("COMBOBOX", "", CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP,
                             ID_COMBO, 20, 175, 220, 220)
        for s in ("alpha", "beta", "gamma"):
            add_string(self.h_combo, CB_ADDSTRING, s)
        self.h_list = child("LISTBOX", "", LBS_NOTIFY | WS_VSCROLL | WS_BORDER | WS_TABSTOP,
                            ID_LIST, 300, 140, 240, 150)
        # 30 items in a short box: the later ones are offscreen, which is what
        # scroll_into_view has to change.
        for i in range(30):
            add_string(self.h_list, LB_ADDSTRING, f"item {i:02d}")
        user32.SendMessageW(wintypes.HWND(self.h_list), LB_SETCURSEL, 0, 0)

        # SW_SHOWNOACTIVATE: appear without taking focus, which is the whole point.
        user32.ShowWindow(wintypes.HWND(self.hwnd), SW_SHOWNOACTIVATE)
        user32.UpdateWindow(wintypes.HWND(self.hwnd))
        self.ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_COMMAND:
            cid = wparam & 0xFFFF
            code = (wparam >> 16) & 0xFFFF
            if cid == ID_BUTTON and code == 0:  # BN_CLICKED
                self.click_count += 1
                self.label_text = f"clicked {self.click_count}"
                user32.SetWindowTextW(wintypes.HWND(self.h_label), self.label_text)
                return 0
        elif msg == WM_CLOSE:
            user32.DestroyWindow(wintypes.HWND(hwnd))
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(wintypes.HWND(hwnd), msg, wparam, lparam)
