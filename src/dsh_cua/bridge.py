"""
Win32 Automation Bridge - pure Python ctypes wrapper.
Each API is available instantly with <2ms overhead.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from ctypes import (byref, c_bool, c_int, c_ubyte, c_void_p, POINTER, Structure, sizeof, windll, WINFUNCTYPE)
from typing import Optional

from . import arbiter  # coexistence gate: cross-process serialization + human-input yield

# Custom callback type for EnumWindows
EnumWindowsProc = WINFUNCTYPE(c_bool, w.HWND, w.LPARAM)

# Constants
SRCCOPY = 0x00CC0020
SW_RESTORE = 9
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
SW_SHOWNOACTIVATE = 4
GA_ROOT = 2

VK_MAP = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "escape": 0x1B, "esc": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E,
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}
for _d in range(10):
    VK_MAP[str(_d)] = 0x30 + _d

# Structures
class RECT(Structure):
    _fields_ = [("left", c_int), ("top", c_int),
                ("right", c_int), ("bottom", c_int)]

class POINT(Structure):
    _fields_ = [("x", c_int), ("y", c_int)]

# DLL bindings
user32 = windll.user32
gdi32 = windll.gdi32

user32.FindWindowW.restype = w.HWND
user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
user32.GetForegroundWindow.restype = w.HWND
user32.GetWindowTextLengthW.restype = c_int
user32.GetWindowTextLengthW.argtypes = [w.HWND]
user32.GetWindowTextW.restype = c_int
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c_int]
user32.IsWindowVisible.restype = c_bool
user32.IsWindowVisible.argtypes = [w.HWND]
user32.GetWindowRect.restype = c_bool
user32.GetWindowRect.argtypes = [w.HWND, POINTER(RECT)]
user32.GetClientRect.restype = c_bool
user32.GetClientRect.argtypes = [w.HWND, POINTER(RECT)]
user32.SetForegroundWindow.restype = c_bool
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.IsIconic.restype = c_bool
user32.IsIconic.argtypes = [w.HWND]
user32.ShowWindow.restype = c_bool
user32.ShowWindow.argtypes = [w.HWND, c_int]
user32.GetWindowThreadProcessId.restype = w.DWORD
user32.GetWindowThreadProcessId.argtypes = [w.HWND, POINTER(w.DWORD)]
user32.ClientToScreen.restype = c_bool
user32.ClientToScreen.argtypes = [w.HWND, POINTER(POINT)]
user32.SetCursorPos.restype = c_bool
user32.SetCursorPos.argtypes = [c_int, c_int]
user32.GetDC.restype = w.HDC
user32.GetDC.argtypes = [w.HWND]
user32.ReleaseDC.restype = c_bool
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.PostMessageW.restype = c_bool
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.EnumWindows.restype = c_bool
user32.EnumWindows.argtypes = [EnumWindowsProc, w.LPARAM]
user32.mouse_event.restype = None
user32.mouse_event.argtypes = [w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.LPARAM]
user32.keybd_event.restype = None
user32.keybd_event.argtypes = [c_ubyte, c_ubyte, w.DWORD, w.LPARAM]
# Coordinate-space and z-order inspection. `SetCursorPos` + `mouse_event` click
# whatever is TOPMOST at a screen point, not the hwnd that was addressed, so a
# caller must be able to ask which window actually owns a point before clicking.
user32.GetCursorPos.restype = c_bool
user32.GetCursorPos.argtypes = [POINTER(POINT)]
user32.WindowFromPoint.restype = w.HWND
user32.WindowFromPoint.argtypes = [POINT]
user32.GetAncestor.restype = w.HWND
user32.GetAncestor.argtypes = [w.HWND, w.UINT]
user32.AttachThreadInput.restype = c_bool
user32.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, c_bool]
user32.BringWindowToTop.restype = c_bool
user32.BringWindowToTop.argtypes = [w.HWND]

kernel32 = windll.kernel32
kernel32.GetCurrentThreadId.restype = w.DWORD

gdi32.BitBlt.restype = c_bool
gdi32.BitBlt.argtypes = [w.HDC, c_int, c_int, c_int, c_int, w.HDC, c_int, c_int, w.DWORD]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleBitmap.restype = w.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [w.HDC, c_int, c_int]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.DeleteDC.restype = c_bool
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.DeleteObject.restype = c_bool
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.GetDIBits.restype = c_int
gdi32.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, w.LPVOID,
                             c_void_p, w.UINT]
PW_RENDERFULLCONTENT = 0x00000002
user32.PrintWindow.restype = c_bool
user32.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]

# ---- DPI awareness ----
# Capture pixels and input coordinates are only meaningful together if the process
# is per-monitor-v2 DPI aware. A DPI-unaware process is silently virtualized to
# logical pixels — on a 150% display that is 1/1.5 of reality — so screenshots come
# back downscaled and any physical coordinate mixed in is off by exactly 1.5x.
# ZCode's CUA treats this as a hard precondition (`requireVerifiedWindowsFramePixels`:
# it refuses to mint "frame pixels" unless awareness is verified). We declare it at
# import, before any window or DC exists, which is the only moment it can be set.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
DPI_AWARENESS_UNAWARE = 1
DPI_AWARENESS_SYSTEM_AWARE = 2
DPI_AWARENESS_PER_MONITOR_AWARE = 3

user32.SetProcessDpiAwarenessContext.restype = c_bool
user32.SetProcessDpiAwarenessContext.argtypes = [c_void_p]
user32.GetThreadDpiAwarenessContext.restype = c_void_p
user32.GetThreadDpiAwarenessContext.argtypes = []
user32.AreDpiAwarenessContextsEqual.restype = c_bool
user32.AreDpiAwarenessContextsEqual.argtypes = [c_void_p, c_void_p]
user32.GetAwarenessFromDpiAwarenessContext.restype = c_int
user32.GetAwarenessFromDpiAwarenessContext.argtypes = [c_void_p]
user32.GetDpiForWindow.restype = w.UINT
user32.GetDpiForWindow.argtypes = [w.HWND]

def _declare_dpi_awareness() -> str:
    """Declare per-monitor-v2 awareness, degrading through the older APIs."""
    try:
        if user32.SetProcessDpiAwarenessContext(c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            return "SetProcessDpiAwarenessContext(per-monitor-v2)"
    except Exception:
        pass
    try:
        shcore = windll.shcore
        shcore.SetProcessDpiAwareness.restype = c_int
        shcore.SetProcessDpiAwareness.argtypes = [c_int]
        if shcore.SetProcessDpiAwareness(2) == 0:
            return "shcore.SetProcessDpiAwareness(per-monitor)"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "SetProcessDPIAware(system)"
    except Exception:
        pass
    return "failed"

_DPI_DECLARATION = _declare_dpi_awareness()

def dpi_awareness(hwnd: int = 0) -> dict:
    """Effective DPI awareness, cf. ZCode's `getNativeDpiAwareness`."""
    out = {"declared_via": _DPI_DECLARATION}
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
        out["awareness"] = user32.GetAwarenessFromDpiAwarenessContext(ctx)
        out["per_monitor_v2"] = bool(user32.AreDpiAwarenessContextsEqual(
            ctx, c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
    except Exception as e:
        out["awareness"] = None
        out["per_monitor_v2"] = False
        out["error"] = str(e)
    if hwnd:
        try:
            d = user32.GetDpiForWindow(w.HWND(hwnd))
            out["window_dpi"] = d
            out["scale"] = round(d / 96, 3)
        except Exception:
            pass
    out["screen"] = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    return out

def require_verified_frame_pixels(method: str) -> Optional[dict]:
    """Return a refusal dict unless DPI awareness is verified, else None.

    Mirrors ZCode's `requireVerifiedWindowsFramePixels`. An unverified helper cannot
    safely pair capture pixels with input coordinates, so the caller must be told
    rather than handed a subtly wrong image. `action_sent: False` is carried so a
    caller can distinguish "nothing happened" from "something happened".
    """
    info = dpi_awareness()
    if info.get("per_monitor_v2"):
        return None
    return {
        "ok": False, "reason": "dpi_awareness_unverified", "action_sent": False,
        "error": (f"{method}: this helper is not effectively per-monitor-v2 DPI aware "
                  f"(awareness={info.get('awareness')}, declared via {info.get('declared_via')}); "
                  f"capture pixels and input coordinates cannot safely be paired."),
        "dpi": info,
    }

# ---- internal helpers ----
def _resolve_key(vk: str) -> int:
    key = VK_MAP.get(vk.lower())
    if key is not None: return key
    if len(vk) == 1:
        code = ord(vk.upper())
        if 0x30 <= code <= 0x5A: return code
    raise ValueError(f"Unknown key: {vk!r}")

def _root(hwnd: int) -> int:
    """Top-level ancestor of a window, so a child (e.g. a browser's render
    surface) compares equal to the window a caller actually addressed."""
    if not hwnd: return 0
    r = user32.GetAncestor(w.HWND(hwnd), GA_ROOT)
    return int(r) if r else int(hwnd)

def window_at_point(screen_x: int, screen_y: int) -> int:
    """Top-level window that owns a screen point — i.e. who receives a click there."""
    return _root(int(user32.WindowFromPoint(POINT(screen_x, screen_y))))

def _foreground_hwnd() -> int:
    """The raw foreground window as an int, with NULL normalised to 0.

    `GetForegroundWindow` is declared with a POINTER restype (`w.HWND`, line 62), so a NULL
    return arrives as Python `None` rather than 0 — and `int(None)` raises `TypeError`. There
    are legitimate moments with no foreground window at all, and this value feeds `type_text`,
    `click_at`, `send_keys` and `send_alt_key`, so the failure reached clients as an MCP
    `isError` on a tool call instead of a clean refusal. `uia.py` had this guard; its two
    counterparts here did not (and one of them, inside `_ensure_visible`, is reached by every
    one of those four tools).
    """
    return int(user32.GetForegroundWindow() or 0)

def foreground_root() -> int:
    """Top-level window that currently has focus (keyboard input goes here)."""
    return _root(_foreground_hwnd())

def cursor_pos() -> tuple[int, int]:
    """Current cursor position. Read-only; used to prove a dry run moved nothing."""
    p = POINT(); user32.GetCursorPos(byref(p)); return (p.x, p.y)

def client_to_screen(hwnd: int, client_x: int, client_y: int):
    """Client-area coordinates -> screen coordinates, or None if the window is gone.

    The same transform `click()` uses, exposed so the element path can address the same
    point without duplicating it (and without going through a dry-run click just to read
    two numbers back out).
    """
    pt = POINT(client_x, client_y)
    if not user32.ClientToScreen(w.HWND(hwnd), byref(pt)):
        return None
    return (pt.x, pt.y)

def _ensure_visible(hwnd: int) -> dict:
    """Try to make `hwnd` the foreground window and report whether it worked.

    `SetForegroundWindow` is subject to Windows' foreground lock: called from a
    background process it usually FAILS SILENTLY and returns 0. The original code
    ignored that return value and proceeded to click, which is how a click could
    land on a different window while reporting success. So: check the result,
    retry through `AttachThreadInput`, and report honestly.

    Every foreground COMPARISON goes through `_root()`. `foreground_root()` is always a
    TOP-LEVEL window, so comparing it directly against a CHILD hwnd could never be equal — this
    function used to return `{"raised": false, "method": "failed"}` while the foreground
    genuinely WAS that child, under-reporting the raise it had just performed. (The
    `SetForegroundWindow`/`BringWindowToTop` calls below still pass the handle that was given,
    child or not: Windows resolves those to the top-level ancestor itself, and passing the child
    is what makes the reported `addressed` value the one the caller asked about.)
    """
    import time
    h = w.HWND(hwnd)
    want = _root(hwnd)
    root = w.HWND(want)
    if user32.IsIconic(root):
        user32.ShowWindow(root, SW_RESTORE)
        time.sleep(0.15)
    if foreground_root() == want:
        return {"raised": True, "method": "already-foreground", "addressed": hwnd}
    ok = bool(user32.SetForegroundWindow(h))
    time.sleep(0.05)
    if foreground_root() == want:
        return {"raised": True, "method": "SetForegroundWindow", "returned": ok,
                "addressed": hwnd}
    # Foreground lock workaround: share input state with the foreground thread so
    # this thread is allowed to change the foreground window, then detach.
    fg = _foreground_hwnd()
    fg_thread = user32.GetWindowThreadProcessId(w.HWND(fg), None) if fg else 0
    my_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(user32.AttachThreadInput(fg_thread, my_thread, True))
    try:
        ok2 = bool(user32.SetForegroundWindow(h))
        time.sleep(0.05)
        if foreground_root() != want:
            user32.BringWindowToTop(h)
            time.sleep(0.05)
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, my_thread, False)
    raised = foreground_root() == want
    return {"raised": raised, "method": "AttachThreadInput" if raised else "failed",
            "set_foreground_returned": ok, "attached": attached,
            "foreground_now": foreground_root(), "addressed": hwnd,
            "addressed_root": want}

# ---- public API ----
def find_window(title_substring: str = "") -> Optional[int]:
    result: list[int] = []
    ft = title_substring.lower()
    @EnumWindowsProc
    def _enum(hwnd: w.HWND, lparam: w.LPARAM) -> c_bool:
        if not user32.IsWindowVisible(hwnd): return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0: return True
        buf = ctypes.create_unicode_buffer(length + 5)
        user32.GetWindowTextW(hwnd, buf, length + 5)
        if ft in buf.value.lower(): result.append(hwnd)
        return True
    user32.EnumWindows(_enum, 0)
    if not result: return None
    best, best_area = result[0], 0
    for hw in result:
        r = RECT()
        if user32.GetWindowRect(hw, byref(r)):
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best_area: best_area = area; best = hw
    return best

def list_windows() -> list[dict]:
    wins: list[dict] = []
    @EnumWindowsProc
    def _enum(hwnd: w.HWND, lparam: w.LPARAM) -> c_bool:
        if not user32.IsWindowVisible(hwnd): return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0: return True
        buf = ctypes.create_unicode_buffer(length + 5)
        user32.GetWindowTextW(hwnd, buf, length + 5)
        title = buf.value
        if not title.strip(): return True
        r = RECT()
        if not user32.GetWindowRect(hwnd, byref(r)): return True
        w2, h2 = r.right - r.left, r.bottom - r.top
        if w2 <= 0 or h2 <= 0: return True
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        wins.append({"hwnd": hwnd, "title": title, "x": r.left, "y": r.top,
                     "width": w2, "height": h2, "pid": pid.value})
        return True
    user32.EnumWindows(_enum, 0)
    wins.sort(key=lambda x: x["width"] * x["height"], reverse=True)
    return wins

def get_window_rect(hwnd: int) -> dict:
    h = w.HWND(hwnd)
    r = RECT(); user32.GetWindowRect(h, byref(r))
    cr = RECT(); user32.GetClientRect(h, byref(cr))
    origin = POINT(0, 0); user32.ClientToScreen(h, byref(origin))
    return {"x": r.left, "y": r.top, "width": r.right - r.left,
            "height": r.bottom - r.top, "client_width": cr.right - cr.left,
            "client_height": cr.bottom - cr.top,
            # Where client (0,0) is on screen: the point `click()` resolves against,
            # and the origin of a client-cropped capture. Reported so a caller can
            # check the coordinate space instead of inferring it.
            "client_origin_on_screen": (origin.x, origin.y),
            "dpi": dpi_awareness(hwnd)}

# ---- capture and input ----
import struct, time
from io import BytesIO

def _capture_pixels(h, w2, h2, method):
    """Render window into a BGRA pixel buffer. method: 'printwindow' or 'bitblt'.
    Returns (pixels, buf_sz) or None on failure."""
    dc_win = user32.GetDC(h)
    if not dc_win: return None
    dc_mem = gdi32.CreateCompatibleDC(dc_win)
    bmp = gdi32.CreateCompatibleBitmap(dc_win, w2, h2)
    old = gdi32.SelectObject(dc_mem, bmp)
    ok = False
    if method == "printwindow":
        ok = bool(user32.PrintWindow(h, dc_mem, PW_RENDERFULLCONTENT))
        if not ok:
            ok = bool(user32.PrintWindow(h, dc_mem, 0))
    else:
        ok = bool(gdi32.BitBlt(dc_mem, 0, 0, w2, h2, dc_win, 0, 0, SRCCOPY))
    if not ok:
        gdi32.SelectObject(dc_mem, old); gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc_mem); user32.ReleaseDC(h, dc_win)
        return None
    class BIH(Structure):
        _fields_ = [("sz", w.DWORD), ("w", c_int), ("h", c_int),
                    ("planes", w.WORD), ("bits", w.WORD),
                    ("comp", w.DWORD), ("sizeImg", w.DWORD),
                    ("xppm", c_int), ("yppm", c_int),
                    ("clrUsed", w.DWORD), ("clrImp", w.DWORD)]
    class BI(Structure): _fields_ = [("hdr", BIH)]
    bi = BI(); bi.hdr.sz = sizeof(BIH); bi.hdr.w = w2; bi.hdr.h = -h2
    bi.hdr.planes = 1; bi.hdr.bits = 32; bi.hdr.comp = 0
    buf_sz = w2 * h2 * 4; pixels = (c_ubyte * buf_sz)()
    gdi32.GetDIBits(dc_mem, bmp, 0, h2, pixels, byref(bi), 0)
    gdi32.SelectObject(dc_mem, old); gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(dc_mem); user32.ReleaseDC(h, dc_win)
    return pixels, buf_sz

def _pixels_blank(pixels, w2, h2):
    """Cheap blank check: sample ~32k evenly-spaced bytes; if all identical, treat as blank."""
    n = len(pixels)
    step = max(1, n // 32768)
    first = pixels[0]
    idx = 0
    while idx < n:
        if pixels[idx] != first: return False
        idx += step
    return True

def _pixels_to_png(pixels, w2, h2, crop=None):
    from PIL import Image
    img_rgba = Image.frombuffer("RGBA", (w2, h2), bytes(pixels), "raw", "BGRA", 0, 1)
    img = img_rgba.convert("RGB")
    if crop is not None:
        img = img.crop(crop)
    buf = BytesIO(); img.save(buf, format="PNG"); result = buf.getvalue(); buf.close()
    return result


def _pixels_to_image_bytes(pixels, w2, h2, crop=None,
                           image_format: str = "jpeg", quality: int = 80,
                           max_dim: int = 0):
    """Encode BGRA pixels -> (data, scales).

    JPEG exists here because a screenshot is the single most expensive thing an
    agent can put in a conversation: a 1080p PNG runs 1-4 MB, and once it is
    base64'd into a request body it counts against the provider's 32 MiB cap.
    Measured 2026-09-19: JPEG q80 is ~6-10x smaller than PNG for the same
    pixels, which is what keeps long CUA sessions under that cap.

    `scales` maps IMAGE pixels back to the pixels of the un-downscaled frame, i.e.
    the screen coordinates `click()` wants:
      {"scale": s, "scale_x": sx, "scale_y": sy, "width": w, "height": h}
    All three are exactly 1.0 when `max_dim` does not shrink the image, so the
    invariant this module is built on — image pixel (0,0) == the click (0,0) — holds
    unless a caller opts into downscaling. When it does, the axes can differ: the
    encoded size is rounded to whole pixels, so a single ratio would be off by up to
    ~1.4 px at the far edge (measured 2026-09-20 on 3841x2160 -> 1000x562). Callers
    that click from image coordinates must use scale_x/scale_y.
    """
    from PIL import Image
    img = Image.frombuffer("RGBA", (w2, h2), bytes(pixels), "raw", "BGRA", 0, 1).convert("RGB")
    if crop is not None:
        img = img.crop(crop)
    scale = 1.0
    if max_dim and max(img.size) > max_dim:
        scale = max(img.size) / float(max_dim)
        img = img.resize((max(1, int(round(img.width / scale))),
                          max(1, int(round(img.height / scale)))), Image.LANCZOS)
    buf = BytesIO()
    if image_format == "jpeg":
        img.save(buf, format="JPEG", quality=quality, optimize=True)
    else:
        img.save(buf, format="PNG")
    data = buf.getvalue(); buf.close()
    ew, eh = img.size
    scales = {"scale": round(scale, 6),
              "scale_x": round(w2 / ew, 6) if ew else 1.0,
              "scale_y": round(h2 / eh, 6) if eh else 1.0,
              "width": ew, "height": eh}
    return data, scales

def capture_window(hwnd: int, client_only: bool = True):
    """Capture a window to PNG bytes -> (png_bytes, method, bounds).

    `bounds` is the screen rectangle the image corresponds to, as (x, y, width, height).
    Cropping to the CLIENT area (the default) makes image pixel (0,0) identical to the
    (0,0) that `click()` expects, so a caller never has to know the frame offset — the
    7px-per-side trap that produced a wrong click on 2026-09-18. ZCode does the same
    when it crops a window out of a full-screen grab and returns the crop bounds
    alongside the image, so the mapping is explicit rather than assumed.

    PNG is kept as this function's format for callers that compare pixels; new code
    that only needs to *look* at the window should call `capture_window_image`, whose
    JPEG default is what keeps request bodies small.
    """
    data, method, bounds, _scale = _capture_window_encoded(hwnd, client_only, "png")
    return data, method, bounds


def capture_window_image(hwnd: int, client_only: bool = True, image_format: str = "jpeg",
                         quality: int = 80, max_dim: int = 0):
    """Capture a window with an explicit encoder -> (data, method, bounds, scales).

    `scales` is a dict (scale, scale_x, scale_y, width, height); see
    `_pixels_to_image_bytes` for why JPEG is the default and how to map image
    coordinates back to screen coordinates. scale_x/scale_y differ only when
    `max_dim` downscales, and then a single ratio is off by up to ~1.4 px.
    """
    return _capture_window_encoded(hwnd, client_only, image_format, quality, max_dim)


def _capture_window_encoded(hwnd: int, client_only: bool = True, image_format: str = "png",
                            quality: int = 80, max_dim: int = 0):
    """-> (data, method, bounds, scales). `scales` is None only on total failure."""
    h = w.HWND(hwnd)
    r = RECT()
    if not user32.GetWindowRect(h, byref(r)): return None, None, None, None
    w2 = r.right - r.left; h2 = r.bottom - r.top
    if w2 <= 0 or h2 <= 0: return None, None, None, None

    # Where the client area sits inside the captured bitmap, and where it sits on screen.
    origin = POINT(0, 0)
    have_client = bool(user32.ClientToScreen(h, byref(origin)))
    cr = RECT(); user32.GetClientRect(h, byref(cr))
    cw = cr.right - cr.left; ch = cr.bottom - cr.top
    crop = None
    bounds = (r.left, r.top, w2, h2)
    if client_only and have_client and cw > 0 and ch > 0:
        cx = origin.x - r.left; cy = origin.y - r.top
        left = max(0, min(w2, cx)); top = max(0, min(h2, cy))
        right = max(left, min(w2, cx + cw)); bottom = max(top, min(h2, cy + ch))
        if right > left and bottom > top:
            crop = (left, top, right, bottom)
            bounds = (r.left + left, r.top + top, right - left, bottom - top)

    last = (None, None, None, None)
    for method in ("printwindow", "bitblt"):
        got = _capture_pixels(h, w2, h2, method)
        if got is None: continue
        pixels, _ = got
        try:
            data, scales = _pixels_to_image_bytes(pixels, w2, h2, crop,
                                                  image_format=image_format,
                                                  quality=quality, max_dim=max_dim)
        except ImportError:
            data, scales = None, None
        if data is None: continue
        last = (data, method, bounds, scales)
        if not _pixels_blank(pixels, w2, h2):
            return last
    return last


def send_hotkey(hwnd: int, *keys: str, dry_run: bool = False,
                require_target: bool = True) -> dict:
    """Send a hotkey to a window.

    MUTATING (hard gate), same admission as a raw click: cross-process mutex first,
    then wait for input-quiet — recent human input makes it wait or refuse with
    "user-active". `keybd_event` injects into the input queue, so the keys are
    delivered to the FOREGROUND window — the passed `hwnd` does not target them.
    Same hazard as `click`, so the same guard: verify, then send, or refuse.
    """
    gate = None
    if not dry_run:
        gate = arbiter.admit_mutating(hard=True)
        if not gate.get("ok"):
            return {"ok": False, "reason": gate.get("reason"), "hwnd": hwnd,
                    "keys": list(keys), "dry_run": dry_run,
                    "arbiter": {k: v for k, v in gate.items() if k != "release"},
                    "error": gate.get("error")}
    try:
        out = _send_hotkey_impl(hwnd, *keys, dry_run=dry_run,
                                require_target=require_target, gate=gate)
    finally:
        if gate is not None:
            gate["release"]()
    if isinstance(out, dict):
        out["arbiter"] = arbiter.receipt(gate) if gate is not None else arbiter.no_gate("dry-run")
    return out


def _send_hotkey_impl(hwnd: int, *keys: str, dry_run: bool = False,
                      require_target: bool = True, gate: Optional[dict] = None) -> dict:
    h = w.HWND(hwnd)
    raise_info = {"raised": None, "method": "skipped (dry run)"}
    if not dry_run:
        raise_info = _ensure_visible(hwnd)
    if require_target and foreground_root() != _root(hwnd):
        return {"ok": False, "reason": "target-not-foreground", "hwnd": hwnd,
                "target_root": _root(hwnd), "foreground_root": foreground_root(),
                "raise": raise_info, "dry_run": dry_run,
                "error": (f"refusing to send keys: window {hwnd} is not the foreground "
                          f"window (foreground root is {foreground_root()}); the keys would "
                          f"be delivered to the wrong window. Nothing was sent.")}
    if dry_run:
        return {"ok": True, "reason": "dry-run", "hwnd": hwnd, "keys": list(keys),
                "raise": raise_info, "dry_run": True}
    # Final tight check right before the first injection.
    recent = arbiter.input_recently(arbiter.FINAL_QUIET_MS)
    if recent.get("recent"):
        return {"ok": False, "reason": "user-active", "hwnd": hwnd, "keys": list(keys),
                "raise": raise_info, "last_input_age_ms": recent.get("last_input_age_ms"),
                "error": (f"the user touched the mouse/keyboard "
                          f"{recent.get('last_input_age_ms')}ms ago — inside the "
                          f"{arbiter.FINAL_QUIET_MS}ms pre-injection window. "
                          f"Nothing was sent; retry when they are idle.")}
    mods = {"ctrl","alt","shift","control","menu"}
    mod_set = set(); main_key = ""
    for k in keys:
        kl = k.lower()
        if kl in mods: mod_set.add(kl)
        else: main_key = kl
    vk = _resolve_key(main_key)
    if "ctrl" in mod_set or "control" in mod_set:
        user32.keybd_event(VK_MAP["ctrl"], 0, 0, 0); time.sleep(0.005)
    if "alt" in mod_set or "menu" in mod_set:
        user32.keybd_event(VK_MAP["alt"], 0, 0, 0); time.sleep(0.005)
    if "shift" in mod_set:
        user32.keybd_event(VK_MAP["shift"], 0, 0, 0); time.sleep(0.005)
    user32.keybd_event(vk, 0, 0, 0); time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0); time.sleep(0.005)
    if "shift" in mod_set:
        user32.keybd_event(VK_MAP["shift"], 0, KEYEVENTF_KEYUP, 0); time.sleep(0.005)
    if "alt" in mod_set or "menu" in mod_set:
        user32.keybd_event(VK_MAP["alt"], 0, KEYEVENTF_KEYUP, 0); time.sleep(0.005)
    if "ctrl" in mod_set or "control" in mod_set:
        user32.keybd_event(VK_MAP["ctrl"], 0, KEYEVENTF_KEYUP, 0); time.sleep(0.005)
    time.sleep(0.05)
    return {"ok": True, "reason": "sent", "hwnd": hwnd, "keys": list(keys),
            "raise": raise_info}

def send_alt_key(hwnd: int, key: str) -> dict:
    """Alt-menu navigation via window-targeted messages (no physical input injection).

    MUTATING (soft gate): takes the cross-process mutex (it can raise the window via
    _ensure_visible) but skips the input-quiet wait — PostMessage does not enter the
    user's input stream."""
    gate = arbiter.admit_mutating(hard=False)
    if not gate.get("ok"):
        return {"ok": False, "reason": gate.get("reason"), "hwnd": hwnd, "key": key,
                "arbiter": arbiter.receipt(gate),
                "error": gate.get("error")}
    try:
        _send_alt_key_impl(hwnd, key)
        return {"ok": True, "reason": "sent", "hwnd": hwnd, "key": key,
                "arbiter": arbiter.receipt(gate)}
    finally:
        gate["release"]()

def _send_alt_key_impl(hwnd: int, key: str) -> None:
    h = w.HWND(hwnd); vk = _resolve_key(key); _ensure_visible(hwnd)
    time.sleep(0.03)
    lp_d = (0x20000000 | (1 << 29))
    user32.PostMessageW(h, WM_SYSKEYDOWN, vk, lp_d); time.sleep(0.05)
    lp_u = (0xC0000000 | (1 << 29) | (1 << 30))
    user32.PostMessageW(h, WM_SYSKEYUP, vk, lp_u); time.sleep(0.08)

def click(hwnd: int, client_x: int, client_y: int,
          dry_run: bool = False, require_target: bool = True) -> dict:
    """Click at client-area coordinates, refusing to click a window that is not there.

    MUTATING (hard gate): a dry run takes no gate; a real click first takes the
    cross-process mutating mutex, then waits for the machine to be input-quiet —
    the user's recent mouse/keyboard activity makes it wait, and if the user keeps
    working it refuses with reason "user-active" instead of fighting them for the
    cursor. The gate wraps the raise too, so activation never races the user either.

    `mouse_event` emits at the cursor, so the click is received by whatever is
    TOPMOST at that screen point — the passed `hwnd` only supplies the coordinate
    transform. A click that lands elsewhere used to return success; now the target
    is verified first and a mismatch is reported instead of clicked.

    `dry_run=True` performs the whole check and reports where the click WOULD land
    without moving the cursor, clicking, or taking any gate.
    """
    gate = None
    if not dry_run:
        gate = arbiter.admit_mutating(hard=True)
        if not gate.get("ok"):
            return {"ok": False, "reason": gate.get("reason"), "hwnd": hwnd,
                    "client_x": client_x, "client_y": client_y, "dry_run": dry_run,
                    "arbiter": arbiter.receipt(gate),
                    "error": gate.get("error")}
    try:
        out = _click_impl(hwnd, client_x, client_y, dry_run=dry_run,
                          require_target=require_target, gate=gate)
    finally:
        if gate is not None:
            gate["release"]()
    # Uniform receipt on every path: a caller must be able to tell "gate taken and
    # free" from "no gate by design" from "gate skipped".
    if isinstance(out, dict):
        out["arbiter"] = arbiter.receipt(gate) if gate is not None else arbiter.no_gate("dry-run")
    return out


def _click_impl(hwnd: int, client_x: int, client_y: int,
                dry_run: bool = False, require_target: bool = True,
                gate: Optional[dict] = None) -> dict:
    import time
    h = w.HWND(hwnd)
    pt = POINT(client_x, client_y)
    if not user32.ClientToScreen(h, byref(pt)):
        return {"ok": False, "reason": "client-to-screen-failed", "hwnd": hwnd,
                "client_x": client_x, "client_y": client_y,
                "error": "ClientToScreen failed; is the window still alive?"}
    screen_x, screen_y = pt.x, pt.y
    raise_info = {"raised": None, "method": "skipped (dry run)"}
    if not dry_run:
        raise_info = _ensure_visible(hwnd)
    owner = window_at_point(screen_x, screen_y)
    target_ok = owner == _root(hwnd)
    result = {
        "ok": False, "hwnd": hwnd, "client_x": client_x, "client_y": client_y,
        "screen_x": screen_x, "screen_y": screen_y,
        "window_at_point": owner, "target_root": _root(hwnd),
        "foreground_root": foreground_root(), "raise": raise_info,
        "dry_run": dry_run,
    }
    if require_target and not target_ok:
        result["reason"] = "target-not-foreground"
        result["error"] = (
            f"refusing to click: screen point ({screen_x},{screen_y}) belongs to window "
            f"{owner}, not to the addressed window {hwnd} (root {_root(hwnd)}). "
            f"The addressed window is not on top at that point; bring it forward or "
            f"re-check coordinates. Nothing was clicked."
        )
        return result
    if dry_run:
        result["ok"] = True
        result["reason"] = "dry-run"
        return result
    # Final tight check: the user may have started typing/moving during the raise.
    recent = arbiter.input_recently(arbiter.FINAL_QUIET_MS)
    if recent.get("recent"):
        result["reason"] = "user-active"
        result["last_input_age_ms"] = recent.get("last_input_age_ms")
        result["error"] = (
            f"the user touched the mouse/keyboard {recent.get('last_input_age_ms')}ms "
            f"ago — inside the {arbiter.FINAL_QUIET_MS}ms pre-injection window. "
            f"Nothing was clicked; retry when they are idle.")
        return result
    user32.SetCursorPos(screen_x, screen_y); time.sleep(0.005)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0); time.sleep(0.005)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0); time.sleep(0.02)
    result["ok"] = True
    result["reason"] = "clicked"
    return result

# ---- text-control addressing -------------------------------------------------------
#
# A `WM_CHAR` posted to a TOP-LEVEL window never reaches its child edit control. The
# frame's window procedure does not forward it, and neither does the dialog manager for
# dialog-class windows. Measured on 5/5 targets — this project's own test fixture plus
# charmap, mstsc, 7-Zip File Manager and Everything — with the character provably arriving
# at the frame and not being routed onwards. `PostMessage` is not a routed-input API.
#
# So a caller who names a WINDOW (which is all `find_window` / `list_windows` return) had
# its text posted into nothing. These helpers find the control that actually owns the text
# and read it back, so the receipt can say whether the text landed rather than only that
# something was posted.

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E

# Class-name prefixes of the classic controls that own editable text.
_TEXT_CLASS_PREFIXES = ("edit", "richedit", "textbox", "scintilla", "textedit")


class GUITHREADINFO(Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD),
                ("hwndActive", w.HWND), ("hwndFocus", w.HWND),
                ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND),
                ("rcCaret", RECT)]


user32.GetClassNameW.restype = c_int
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, c_int]
# `GetWindowThreadProcessId` is declared once, near the top of this module — not repeated here.
user32.GetGUIThreadInfo.restype = c_bool
user32.GetGUIThreadInfo.argtypes = [w.DWORD, POINTER(GUITHREADINFO)]
user32.EnumChildWindows.restype = c_bool
user32.EnumChildWindows.argtypes = [w.HWND, EnumWindowsProc, w.LPARAM]
user32.GetWindowLongW.restype = c_int
user32.GetWindowLongW.argtypes = [w.HWND, c_int]
# SendMessageTimeoutW, NOT SendMessageW. The synchronous call has no bound: a target whose
# thread is not pumping messages (hung UI, modal loop, suspended) makes it wait forever, and
# these reads sit inside `type_text`'s `try` — so a block there also means
# `finally: gate["release"]()` never runs and the cross-process mutex stays held, making every
# later mutating call from every session sit out its full MUTEX_TIMEOUT_MS and fail. Measured on
# a suspended target: this returns in 1200 ms with 0, while the synchronous version was still
# blocked when its child process was killed at 8 s. Note the two out-parameters of the API: the
# RETURN value is only a success flag (0 = the control did not answer), and the message result
# arrives in `lpdwResult` — which must be a pointer-sized buffer, since it is a DWORD_PTR.
#   c_ssize_t rather than c_longlong: the result type is pointer-sized, and c_longlong would be
# wrong on a 32-bit interpreter.
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT,
                                       POINTER(ctypes.c_size_t)]

# SMTO_ABORTIFHUNG returns immediately when Windows already considers the target hung; the
# timeout bounds the case where it does not yet think so. Both are needed — a thread suspended
# in a debugger or parked in a nested modal loop is not "hung" by Windows' definition.
SMTO_ABORTIFHUNG = 0x0002
_SEND_TIMEOUT_MS = 1500


def window_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(w.HWND(hwnd), buf, 256)
    return buf.value


def _is_text_control(hwnd: int) -> bool:
    return window_class_name(hwnd).lower().startswith(_TEXT_CLASS_PREFIXES)


GWL_STYLE = -16
ES_READONLY = 0x0800
# ES_READONLY is a style bit whose MEANING is defined per window class, so only classes that
# actually define it may be judged by it: Scintilla implements read-only through
# SCI_SETREADONLY rather than a style bit, and a third-party "TextBox"/"TextEdit" class is free
# to give 0x0800 an unrelated meaning. Reading it for those classes does not merely mislabel a
# control — it picks the WRONG TARGET in `find_text_target`, silently.
_ES_READONLY_CLASSES = ("edit", "richedit")


def _is_read_only_control(hwnd: int) -> Optional[bool]:
    """True/False when this class's read-only style is knowable, None when it is not.

    None is NOT False. "Unknown" must not be read as "writable", and it must not be read as
    "read-only" either: the caller excludes only controls that are KNOWN to be read-only, because
    excluding an unknown one could discard the only writable control in the window.

    This exists because a window holding both an input box and a read-only text area (an ordinary
    layout) would otherwise resolve to the read-only one and report `no-effect` for a call that
    would have worked against the other.
    """
    if not window_class_name(hwnd).lower().startswith(_ES_READONLY_CLASSES):
        return None
    try:
        return bool(int(user32.GetWindowLongW(w.HWND(hwnd), GWL_STYLE)) & ES_READONLY)
    except Exception:
        return None


def _read_control_text(hwnd: int) -> Optional[str]:
    """A text control's own text, read across the process boundary under a TIMEOUT.

    Returns None when the control could not be read, which is a different thing from "" — a
    control that WAS read and is genuinely empty. Keeping those two apart is why this no longer
    uses the synchronous `SendMessageW`: that call returned the same "" for a destroyed handle as
    for an empty control, so "I could not read it" reached the caller as "your text did not land"
    — reported as `reason: no-effect` with an error message that blamed a read-only control.

    `WM_GETTEXT`/`WM_GETTEXTLENGTH` are among the messages Windows marshals across processes, so
    this can read another process's control. The return value is only a success flag and the
    result arrives in `lpdwResult` — measured on a real, empty, cross-process `RICHEDIT50W`:
    ret=1, lpdwResult=0. An empty control is therefore not mistaken for an unreadable one.
    """
    res = ctypes.c_size_t(0)
    if not user32.SendMessageTimeoutW(w.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0,
                                      SMTO_ABORTIFHUNG, _SEND_TIMEOUT_MS, byref(res)):
        return None
    n = int(res.value)
    if n == 0:
        return ""
    if n < 0 or n > 4_000_000:
        return None
    buf = ctypes.create_unicode_buffer(n + 1)
    if not user32.SendMessageTimeoutW(w.HWND(hwnd), WM_GETTEXT, n + 1,
                                      ctypes.cast(buf, ctypes.c_void_p).value,
                                      SMTO_ABORTIFHUNG, _SEND_TIMEOUT_MS, byref(res)):
        return None
    return buf.value


def _thread_focus(top: int) -> int:
    """The control this window's thread currently has keyboard focus on, or 0."""
    tid = user32.GetWindowThreadProcessId(w.HWND(top), None)
    if not tid:
        return 0
    gti = GUITHREADINFO()
    gti.cbSize = sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, byref(gti)):
        return 0
    return int(gti.hwndFocus or 0)


def find_text_target(top: int) -> "tuple[int, str]":
    """The control inside `top` that owns typed text, and how it was chosen.

    Returns (0, "") when the window holds no WndProc-backed text control — a browser or
    Electron window is the common case, and those are addressed as a whole.
    """
    if _is_text_control(top):
        return top, "input-is-a-text-control"
    # The thread's own focused control is where a user's keystrokes would go, so it beats
    # position when a window holds several text controls.
    focus = _thread_focus(top)
    if focus and _root(focus) == _root(top) and _is_text_control(focus):
        return focus, "thread-focus"
    found: list = []

    def cb(child, _lp):
        if _is_text_control(int(child)):
            found.append(int(child))
            if len(found) >= 8:
                return False
        return True

    user32.EnumChildWindows(w.HWND(top), EnumWindowsProc(cb), 0)
    if not found:
        return 0, ""
    # Only controls KNOWN to be read-only are excluded (`is not True`). An unrecognised class
    # returns None, and treating unknown as read-only would discard a control that may well
    # accept text — a worse error than trying the wrong one, because the receipt can only report
    # what actually happened to the target that was chosen.
    writable = [c for c in found if _is_read_only_control(c) is not True]
    if writable:
        return writable[0], "first-writable-descendant"
    return found[0], "first-descendant (all known read-only)"


def type_text(hwnd: int, text: str, delay: float = 0.01) -> dict:
    """Type into a window's text control, char-by-char via PostMessage.

    MUTATING (soft gate): takes the cross-process mutex and may raise the window
    (_ensure_visible), but skips the input-quiet wait — PostMessage does not enter
    the user's input stream. Note the honest limit: the raise can still take focus
    while the user is typing; that is what a future prevent_activation gate is for.

    The text control is RESOLVED rather than assumed, and the effect is READ BACK. A
    caller can only name a top-level window (`find_window` / `list_windows` return
    top-level handles) and a `WM_CHAR` posted to one never reaches its child edit, so this
    used to post into nothing and return `ok: True`. See the block comment above.

    What `effect_verified` means here, precisely: the control's text was read before and
    after, and it CHANGED. That is not proof that these characters are the change — the window
    has just been raised and a human may be typing into it, and an application may update its
    own control — so a length that does not account for the text sent is reported in
    `effect_note` instead of being presented as success. `None` means the text could not be
    read back (no WndProc-backed control in the window, or one that did not answer within the
    timeout) and the effect is UNCONFIRMED, which is not the same as failed.
    """
    gate = arbiter.admit_mutating(hard=False)
    if not gate.get("ok"):
        return {"ok": False, "reason": gate.get("reason"), "hwnd": hwnd,
                "typed": 0, "text_length": len(text),
                "arbiter": arbiter.receipt(gate),
                "error": gate.get("error")}
    try:
        fg_before = foreground_root()
        raise_info = _ensure_visible(hwnd)
        target, resolved_by = find_text_target(hwnd)
        dest = target or hwnd
        before = _read_control_text(dest) if target else None
        for ch in text:
            user32.PostMessageW(w.HWND(dest), WM_CHAR, ord(ch), 0)
            if delay > 0: time.sleep(delay)
        after = _read_control_text(dest) if target else None
        effect_verified = None
        if before is not None and after is not None:
            effect_verified = after != before
        out = {"ok": True, "reason": "typed", "hwnd": hwnd, "target_hwnd": dest,
               "resolved_by": resolved_by or None,
               "typed": len(text), "text_length": len(text),
               "effect_verified": effect_verified,
               "foreground_changed": foreground_root() != fg_before,
               "raise": raise_info,
               "arbiter": arbiter.receipt(gate)}
        if effect_verified is False:
            out["ok"] = False
            out["reason"] = "no-effect"
            # Deliberately neutral. `after == before` is evidence that the text did not land and
            # no evidence at all about WHY; the previous wording led with "the control is
            # read-only", which was a guess presented as a diagnosis.
            out["error"] = (f"posted {len(text)} character(s) to {dest} but its text did not "
                            f"change (still {before[:60]!r}). The text did not land; this "
                            f"receipt does not say why. Known causes, in no particular order: "
                            f"the control is read-only or disabled; a maximum length or an "
                            f"input mask rejected the characters; the control was destroyed "
                            f"between the two reads; or it does not consume posted WM_CHAR.")
            out["before"] = before[:80]
            out["after"] = after[:80]
        elif effect_verified is None:
            if not target:
                out["effect_note"] = (
                    "the characters were posted to the window itself: no WndProc-backed text "
                    "control was found inside it, so there is nothing to read back and the "
                    "effect is UNCONFIRMED. Pass the control's own hwnd if you have it.")
            else:
                out["effect_note"] = (
                    f"the control {dest} did not answer WM_GETTEXT within {_SEND_TIMEOUT_MS} "
                    f"ms, so the effect is UNCONFIRMED — which is not the same as failed. It "
                    f"may be busy, hung or already destroyed.")
        else:
            # `after != before` proves the control's text CHANGED. It does not prove that OUR
            # characters are the change: `_ensure_visible` has just brought this window forward
            # and a human may be typing into it, and the application may update the control on
            # its own. Length is the cheap check available, so a mismatch is reported rather than
            # presenting a coincidental change as this call's success.
            delta = len(after) - len(before)
            if delta != len(text):
                out["effect_note"] = (
                    f"the control's text changed, but its length moved by {delta:+d} where "
                    f"{len(text)} character(s) were sent, so the change is real but it is not a "
                    f"plain insertion of this text. It may have been truncated by a maximum "
                    f"length, partly rejected by an input mask, inserted over a selection, or "
                    f"changed by something else entirely (the window was just raised, and a "
                    f"human may be typing in it). before={before[:40]!r} after={after[:40]!r}")
        return out
    finally:
        gate["release"]()


def list_displays() -> list[dict]:
    """Enumerate monitors: index (1-based, EnumDisplayMonitors order), primary flag,
    and the monitor rectangle in physical pixels (process is per-monitor-v2 aware)."""
    import ctypes as _ct
    from ctypes import wintypes as _wt

    class MONITORINFOEXW(_ct.Structure):
        _fields_ = [("cbSize", _wt.DWORD),
                    ("rcMonitor", _wt.RECT),
                    ("rcWork", _wt.RECT),
                    ("dwFlags", _wt.DWORD),
                    ("szDevice", _wt.WCHAR * 32)]

    MONITOR_DEFAULTTOPRIMARY = 1
    monitors: list[dict] = []

    @_ct.WINFUNCTYPE(_wt.BOOL, _wt.HMONITOR, _wt.HDC, _ct.POINTER(_wt.RECT), _wt.LPARAM)
    def _on_monitor(hmon, hdc, lprect, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = _ct.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, _ct.byref(mi)):
            r = mi.rcMonitor
            is_primary = bool(
                user32.MonitorFromWindow(None, MONITOR_DEFAULTTOPRIMARY) == hmon)
            monitors.append({"index": len(monitors) + 1,
                             "primary": is_primary,
                             "device": mi.szDevice,
                             "x": r.left, "y": r.top,
                             "width": r.right - r.left, "height": r.bottom - r.top,
                             "work": {"x": mi.rcWork.left, "y": mi.rcWork.top,
                                      "width": mi.rcWork.right - mi.rcWork.left,
                                      "height": mi.rcWork.bottom - mi.rcWork.top}})
        return True

    user32.EnumDisplayMonitors(None, None, _on_monitor, 0)
    return monitors
