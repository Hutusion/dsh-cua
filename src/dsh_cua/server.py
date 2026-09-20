"""Win32 MCP Server - persistent desktop automation bridge."""
from __future__ import annotations
import base64, time, ctypes, sys, os
from typing import Any
from mcp.server.fastmcp import FastMCP

from .bridge import (
    capture_window, capture_window_image, click, cursor_pos, dpi_awareness, find_window,
    get_window_rect, list_displays, list_windows, require_verified_frame_pixels,
    send_alt_key, send_hotkey, type_text,
    client_to_screen as bridge_client_to_screen,
)
from . import arbiter, uia

def window_pid(hwnd: int) -> int:
    """Owning process of a window — used to assert the element path addresses the
    window the caller named (ZCode's `assertClickElementOwnerPid`)."""
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(ctypes.wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value

mcp = FastMCP(name="Win32 Desktop Automation", instructions="Win32 bridge for Windows desktop automation.")

@mcp.tool(description="List all visible top-level windows with title, hwnd, PID, position and size, sorted largest first. Read-only — safe while the user works.")
def tool_list_windows(filter_title: str = "") -> dict[str, Any]:
    windows = list_windows()
    if filter_title:
        ft = filter_title.lower()
        windows = [w for w in windows if ft in w["title"].lower()]
    return {"success": True, "count": len(windows), "windows": windows}

@mcp.tool(description="Find a window by title substring and return its hwnd and geometry. Read-only.")
def tool_find_window(title: str = "") -> dict[str, Any]:
    hwnd = find_window(title)
    if hwnd is None:
        return {"success": False, "error": f"No visible window with title containing '{title}'"}
    rect = get_window_rect(hwnd)
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 5)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 5)
    return {"success": True, "hwnd": hwnd, "title": buf.value, **rect}

@mcp.tool(description="Capture a window screenshot and SAVE IT TO A FILE. Provide hwnd or title substring. Crops to the window's CLIENT area by default, so image pixel (0,0) is the same point click_at calls (0,0) — no frame offset to guess. Returns bounds = the screen rect (x,y,width,height) the image maps to, plus `scale` (multiply image pixels by it to get screen pixels; 1.0 unless you asked for downscaling). Also reports dpi_verified; when false, image pixels and input coordinates cannot be safely paired. Defaults to JPEG q80 — a 1080p shot is ~6-10x smaller than PNG, which is what keeps a long session's request body under the provider's 32 MiB cap. The bytes are NOT inlined as base64 by default (that costs ~12,500 tokens per shot as text); read the saved_path with read_image instead, which attaches the same picture for ~50 tokens. Pass include_data_uri=true only when a caller genuinely cannot read a file. Read-only — no input injection, no focus change.")
def tool_capture_window(hwnd: int = 0, title: str = "", save_path: str = "", client_only: bool = True,
                        image_format: str = "jpeg", quality: int = 80, max_dim: int = 0,
                        include_data_uri: bool = False) -> dict[str, Any]:
    if hwnd == 0 and title:
        hwnd = find_window(title)
        if hwnd is None: return {"success": False, "error": f"Window not found: '{title}'"}
    if hwnd == 0: hwnd = ctypes.windll.user32.GetForegroundWindow()
    rect = get_window_rect(hwnd)
    dpi_gate = require_verified_frame_pixels("capture_window")
    t0 = time.perf_counter()
    data, method, bounds, scale = capture_window_image(
        hwnd, client_only=client_only, image_format=image_format,
        quality=quality, max_dim=max_dim)
    t1 = time.perf_counter()
    if data is None: return {"success": False, "error": "Failed to capture window"}

    # A shot nobody can look at is useless, and inline base64 is the expensive way to
    # make it lookable. So when the caller gives no path and does not ask for the data
    # URI, land it in a temp dir and hand back the path.
    ext = "jpg" if image_format == "jpeg" else "png"
    if not save_path and not include_data_uri:
        shots = os.path.join(os.environ.get("TEMP", "."), "win32_mcp_shots")
        os.makedirs(shots, exist_ok=True)
        # Nothing else prunes this directory: dsh's attachment store has no GC either,
        # so a default save path that never cleans up becomes an unmanaged pile. Drop
        # yesterday's shots on the way in — a caller that needed one has already copied
        # its bytes into an attachment by then.
        try:
            cutoff = time.time() - 86400
            for name in os.listdir(shots):
                fp = os.path.join(shots, name)
                if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
        except OSError:
            pass
        save_path = os.path.join(shots, f"win{hwnd}_{int(time.time()*1000)}.{ext}")
    saved = ""
    if save_path:
        with open(save_path, "wb") as f: f.write(data)
        saved = save_path

    out = {"success": True, "hwnd": hwnd,
           # Image dimensions and the screen rect they map to. `bounds` is what makes
           # image pixels and click_at coordinates provably the same space.
           "width": bounds[2], "height": bounds[3],
           "bounds": {"x": bounds[0], "y": bounds[1], "width": bounds[2], "height": bounds[3]},
           "client_only": client_only,
           "window_rect": {k: rect[k] for k in ("x", "y", "width", "height")},
           "client_origin_on_screen": rect["client_origin_on_screen"],
           "dpi_verified": dpi_gate is None,
           "image_format": image_format, "quality": quality if image_format == "jpeg" else None,
           "scale": round(scale, 4),
           "size_bytes": len(data), "capture_time_ms": round((t1-t0)*1000, 1),
           "capture_method": method, "saved_path": saved}
    if scale != 1.0:
        out["scale_note"] = ("image is downscaled: multiply image pixel coordinates by "
                             "`scale` before passing them to click_at")
    if include_data_uri:
        mime = "image/jpeg" if image_format == "jpeg" else "image/png"
        out["data_uri"] = f"data:{mime};base64,{base64.b64encode(data).decode('utf-8')}"
    if dpi_gate is not None:
        out["dpi_warning"] = dpi_gate["error"]
        out["dpi"] = dpi_gate["dpi"]
    return out

@mcp.tool(description="Send keyboard shortcut to a window. Example: keys=['ctrl','s']. For Alt+key menu navigation, include 'alt' as first key (or set use_alt_key). MUTATING: serialized across agent sessions via the admission gate, and global hotkeys additionally YIELD TO THE USER — recent mouse/keyboard activity makes them wait, then refuse with reason user-active instead of interrupting; switch to element actions or retry later. Also refuses when the window is not the foreground window, because injected keys would go to the wrong window.")
def tool_send_keys(keys: list[str] = None, hwnd: int = 0, title: str = "", use_alt_key: bool = False) -> dict[str, Any]:
    if keys is None: keys = []
    if hwnd == 0 and title:
        hwnd = find_window(title)
        if hwnd is None: return {"success": False, "error": f"Window not found: '{title}'"}
    if hwnd == 0: hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not keys: return {"success": False, "error": "No keys provided"}
    t0 = time.perf_counter()
    try:
        if use_alt_key and len(keys) >= 2:
            alt = send_alt_key(hwnd, keys[-1])
            if not alt.get("ok"):
                return {"success": False, **alt,
                        "time_ms": round((time.perf_counter()-t0)*1000, 1)}
        else:
            outcome = send_hotkey(hwnd, *keys)
            if not outcome.get("ok"):
                return {"success": False, **outcome,
                        "time_ms": round((time.perf_counter()-t0)*1000, 1)}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "hwnd": hwnd, "keys": keys, "time_ms": round((time.perf_counter()-t0)*1000, 1)}

@mcp.tool(description="Click at client-area coordinates within a window. Coordinates relative to window content area (0,0 = top-left). Tries the ELEMENT path first (resolve the point via UI Automation and invoke that element) because it needs no focus and does not care about z-order; falls back to a raw cursor click, which does. The result's `method` says which was used: 'ax_press' or 'raw_event'. MUTATING: serialized across agent sessions; the raw_event path ALSO YIELDS TO THE USER (waits for input-quiet, then refuses with user-active rather than fighting them for the cursor); the ax_press path injects no physical input. The raw path verifies the addressed window actually owns that screen point and returns success=false instead of clicking another window. Pass dry_run=true to check where the click would land without moving the cursor, clicking, or taking any gate.")
def tool_click_at(x: int = 0, y: int = 0, hwnd: int = 0, title: str = "",
                  dry_run: bool = False, prefer: str = "element") -> dict[str, Any]:
    if hwnd == 0 and title:
        hwnd = find_window(title)
        if hwnd is None: return {"success": False, "error": f"Window not found: '{title}'"}
    if hwnd == 0: hwnd = ctypes.windll.user32.GetForegroundWindow()
    t0 = time.perf_counter()

    attempts = []
    if prefer == "element":
        screen = bridge_client_to_screen(hwnd, x, y)
        if screen is None:
            attempts.append({"method": "ax_press", "ok": False,
                             "reason": "client_to_screen_failed"})
        else:
            pid = window_pid(hwnd)
            got = uia.click_element_at_point(screen[0], screen[1], expect_pid=pid,
                                             dry_run=dry_run)
            attempts.append({"method": "ax_press", "ok": bool(got.get("ok")),
                             "reason": got.get("reason"), "error": got.get("error"),
                             "screen": {"x": screen[0], "y": screen[1]},
                             "element": got.get("element")})
            if got.get("ok"):
                return {"success": True, "hwnd": hwnd, "x": x, "y": y,
                        "method": "ax_press", "focus_stolen": False, "dry_run": dry_run,
                        "element": got.get("element"),
                        "attempts": attempts,
                        "time_ms": round((time.perf_counter()-t0)*1000, 1)}
            if dry_run and got.get("reason") == "dry_run":
                # A preview is a RESULT, not a failure: reporting the element the element
                # path WOULD have pressed is the entire value of dry_run. Falling through
                # to the raw path here answered with method="raw_event" and buried the
                # element inside `attempts`, which reads as "the element path did not
                # work" when in fact it resolved the right control and stopped.
                return {"success": True, "hwnd": hwnd, "x": x, "y": y,
                        "method": "ax_press", "focus_stolen": False, "dry_run": True,
                        "action_sent": False, "would_press": True,
                        "element": got.get("element"),
                        "attempts": attempts,
                        "time_ms": round((time.perf_counter()-t0)*1000, 1)}

    outcome = click(hwnd, x, y, dry_run=dry_run)
    attempts.append({"method": "raw_event", "ok": bool(outcome.get("ok")),
                     "reason": outcome.get("reason")})
    elapsed = round((time.perf_counter()-t0)*1000, 1)
    if not outcome.get("ok"):
        return {"success": False, "method": None, "attempts": attempts,
                **outcome, "time_ms": elapsed}
    return {"success": True, "hwnd": hwnd, "x": x, "y": y, "dry_run": dry_run,
            "method": "raw_event", "focus_stolen": True,
            "attempts": attempts, "time_ms": elapsed}

@mcp.tool(description="Perform an action on a UI element by the `ref` from element_at_point — this is the focus-free path: a UI Automation pattern is delivered to the element, so the window is never raised and z-order never matters. MUTATING (soft): serialized across agent sessions; NO physical input is injected, so it can run while the user types — just do not operate the very window the user is working in. `action` must be one of the actions listed for that element (press, set_value, select, toggle, expand, collapse, scroll_into_view, focus); `set_value` also needs `text`. Refuses with the supported list when the element does not offer the action.")
async def tool_element_action(ref: str, action: str, text: str = "") -> dict[str, Any]:
    if not uia.available().get("available"):
        return {"success": False, "reason": "uia_unavailable", **uia.available()}
    res = uia.element_action(ref, action, text if text else None)
    return {"success": bool(res.get("ok")), **res}

@mcp.tool(description="Type a text string into a window character-by-character (window-targeted PostMessage, not global keystrokes). MUTATING (soft): serialized across agent sessions; no physical input is injected so it can run while the user types — but the window may be RAISED to foreground, so avoid typing into a window the user is currently working in.")
def tool_type_text(text: str = "", hwnd: int = 0, title: str = "", delay_ms: int = 10) -> dict[str, Any]:
    if not text: return {"success": False, "error": "No text provided"}
    if hwnd == 0 and title:
        hwnd = find_window(title)
        if hwnd is None: return {"success": False, "error": f"Window not found: '{title}'"}
    if hwnd == 0: hwnd = ctypes.windll.user32.GetForegroundWindow()
    t0 = time.perf_counter()
    out = type_text(hwnd, text, delay_ms/1000.0)
    if not out.get("ok"):
        return {"success": False, **out, "time_ms": round((time.perf_counter()-t0)*1000, 1)}
    return {"success": True, "hwnd": hwnd, "text_length": out.get("text_length", len(text)), "time_ms": round((time.perf_counter()-t0)*1000, 1)}

@mcp.tool(description="Get detailed position and size info for a window: absolute position, dimensions, and client area size. Read-only.")
def tool_get_window_rect(hwnd: int = 0, title: str = "") -> dict[str, Any]:
    if hwnd == 0 and title:
        hwnd = find_window(title)
        if hwnd is None: return {"success": False, "error": f"Window not found: '{title}'"}
    if hwnd == 0: hwnd = ctypes.windll.user32.GetForegroundWindow()
    return {"success": True, "hwnd": hwnd, **get_window_rect(hwnd)}

@mcp.tool(description="Identify the UI element at a PHYSICAL screen point via Windows UI Automation. Returns its name, role, rect, and which actions it supports, plus an opaque `ref` for read_element. This is how you turn 'what I see in the screenshot' into something addressable without clicking: coordinates here are the same space as capture bounds (both physical pixels). Read-only — moves no cursor and changes no focus.")
async def tool_element_at_point(x: int, y: int) -> dict[str, Any]:
    if not uia.available().get("available"):
        return {"success": False, "reason": "uia_unavailable", **uia.available()}
    res = uia.element_at_point(x, y)
    if not res.get("ok"):
        return {"success": False, **res}
    return {"success": True, **res}

@mcp.tool(description="Re-read a UI element by the `ref` returned from element_at_point. Use this to check whether an element is still there before acting on it; a ref goes stale when the UI is rebuilt, and that is reported rather than guessed. Read-only.")
async def tool_read_element(ref: str) -> dict[str, Any]:
    if not uia.available().get("available"):
        return {"success": False, "reason": "uia_unavailable", **uia.available()}
    res = uia.read_element(ref)
    if not res.get("ok"):
        return {"success": False, **res}
    return {"success": True, **res}

@mcp.tool(description="Read a window's UI as a compact TEXT tree instead of a screenshot — far cheaper than an image, and it names elements a hit-test cannot reach (e.g. an input inside a Chromium page, which element_at_point reports only as the enclosing 'document'). Each line is '{index} {indent}{role} {name}{Value: ...}{(state)}'. Every shot after the first is a DIFF against the previous one: unchanged lines are omitted, '~' marks a changed line, '+' an added one, and removed indices are summarised as ranges. So a second call is usually a few lines, not the whole tree — an unchanged window costs about 100 characters. INDEXES ARE POSITIONAL: each shot numbers the tree it just walked, so a number is only valid against the shot that printed it. '~' and '+' lines carry THIS shot's index and may be acted on; unchanged lines carry no index, so reaching one needs disable_diff=true for a full render. An index read from an older shot is not a durable handle — re-check it with a fresh shot before acting if the window may have changed. The result includes shot_key (the window the diff baseline belongs to — the baseline lives server-side and outlives your session, so this is how you confirm it was taken against the window you meant). Pass include_offscreen=true to include elements that are scrolled out or hidden. NOTE: this reads element VALUES, so the text can contain whatever is on screen in that window. Read-only — no input injection, no focus change; safe to call while the user works.")
async def tool_skyshot(hwnd: int = 0, title: str = "", disable_diff: bool = False,
                       include_offscreen: bool = False, include_values: bool = True,
                       max_nodes: int = 800, max_depth: int = 30) -> dict[str, Any]:
    if not uia.available().get("available"):
        return {"success": False, "reason": "uia_unavailable", **uia.available()}
    if hwnd == 0 and title:
        hwnd = find_window(title)
        if hwnd is None: return {"success": False, "error": f"Window not found: '{title}'"}
    if hwnd == 0: hwnd = ctypes.windll.user32.GetForegroundWindow()
    res = uia.skyshot(hwnd, disable_diff=disable_diff, include_offscreen=include_offscreen,
                      include_values=include_values, max_nodes=max_nodes, max_depth=max_depth)
    return {"success": bool(res.get("ok")), **res}

@mcp.tool(description="Act on the element that line `index` of the last skyshot named. This is the focus-free path for elements element_at_point cannot reach: the tree names the element, so the action goes to it and the window is never raised. MUTATING (soft): serialized across agent sessions; no physical input injected. `action` is one of press, set_value, select, toggle, expand, collapse, scroll_into_view, focus; set_value also needs `text`. Take a fresh skyshot if the UI has changed — a stale index is refused rather than acted on.")
async def tool_element_action_at(hwnd: int, index: int, action: str, text: str = "") -> dict[str, Any]:
    if not uia.available().get("available"):
        return {"success": False, "reason": "uia_unavailable", **uia.available()}
    res = uia.element_action_at(hwnd, index, action, text if text else None)
    return {"success": bool(res.get("ok")), **res}

@mcp.tool(description="Find UI elements by role and/or name substring and return refs that can be acted on with element_action. This is how you reach an element that cannot be hit-tested — the point of naming it is that you no longer need a pixel. `role` is the lowercase role from skyshot (button, edit, document, list_item, ...). At least one filter is required: an unfiltered call would be a tree dump, so use skyshot for that. Read-only.")
async def tool_find_elements(hwnd: int = 0, title: str = "", role: str = "",
                             name_contains: str = "", automation_id: str = "",
                             include_offscreen: bool = True, max_results: int = 20) -> dict[str, Any]:
    if not uia.available().get("available"):
        return {"success": False, "reason": "uia_unavailable", **uia.available()}
    if hwnd == 0 and title:
        hwnd = find_window(title)
        if hwnd is None: return {"success": False, "error": f"Window not found: '{title}'"}
    if hwnd == 0: hwnd = ctypes.windll.user32.GetForegroundWindow()
    res = uia.find_elements(hwnd, role=role or None, name_contains=name_contains or None,
                            automation_id=automation_id or None,
                            include_offscreen=include_offscreen, max_results=max_results)
    return {"success": bool(res.get("ok")), **res}

@mcp.tool(description="Report the coexistence policy in force: whether the admission gate is enabled, the quiet/max-wait/mutex-timeout tunables, the pre-injection final-check window, named-mutex health, and how long ago the last physical input happened. Read-only — call it when an action was refused with user-active or arbiter-busy and you want the numbers behind the refusal.")
def tool_coexistence_status() -> dict[str, Any]:
    return {"success": True, **arbiter.state()}

@mcp.tool(description="Read the current clipboard text. Read-only. Non-text clipboard content reports empty text rather than failing.")
def tool_clipboard_read() -> dict[str, Any]:
    try:
        import pyperclip
    except Exception as exc:
        return {"success": False, "error": f"clipboard backend unavailable: {exc}"}
    try:
        text = pyperclip.paste()
    except Exception as exc:
        return {"success": False, "error": f"clipboard read failed: {exc}"}
    return {"success": True, "text": text if isinstance(text, str) else "",
            "length": len(text) if isinstance(text, str) else 0}

@mcp.tool(description="Replace the clipboard contents with text. MUTATING (soft): serialized across agent sessions — and it DESTROYS whatever the user last copied, so do not use it casually; announce it when you do.")
def tool_clipboard_write(text: str = "") -> dict[str, Any]:
    gate = arbiter.admit_mutating(hard=False)
    if not gate.get("ok"):
        return {"success": False, "arbiter": {k: v for k, v in gate.items() if k != "release"},
                "error": gate.get("error")}
    try:
        try:
            import pyperclip
        except Exception as exc:
            return {"success": False, "error": f"clipboard backend unavailable: {exc}"}
        try:
            pyperclip.copy(text)
        except Exception as exc:
            return {"success": False, "error": f"clipboard write failed: {exc}"}
        return {"success": True, "length": len(text)}
    finally:
        # The gate is process-wide: every other agent session's mutating call waits on
        # it. Returning without releasing wedged them all until the server restarted,
        # and because sync tools run on the event-loop thread the holder never exits,
        # so the OS never handed the mutex on as abandoned.
        gate["release"]()

@mcp.tool(description="Open a file, folder, or URI with the shell's default handler — like double-clicking it in Explorer: an .exe path, a document, or a URL. MUTATING (soft): serialized across agent sessions; the launched app may take focus.")
def tool_open_application(target: str) -> dict[str, Any]:
    if not target:
        return {"success": False, "error": "No target provided"}
    gate = arbiter.admit_mutating(hard=False)
    if not gate.get("ok"):
        return {"success": False, "arbiter": {k: v for k, v in gate.items() if k != "release"},
                "error": gate.get("error")}
    try:
        os.startfile(target)  # noqa: S606 - this tool's purpose is to launch things
        return {"success": True, "target": target}
    except Exception as exc:
        return {"success": False, "target": target, "error": f"startfile failed: {exc}"}
    finally:
        gate["release"]()

@mcp.tool(description="List monitors: index (1-based), primary flag, device name, and the monitor plus work-area rectangles in physical pixels. Read-only.")
def tool_list_displays() -> dict[str, Any]:
    displays = list_displays()
    return {"success": True, "count": len(displays), "displays": displays}

@mcp.tool(description="Current cursor position plus the screen size, both physical pixels. Read-only.")
def tool_cursor_position() -> dict[str, Any]:
    x, y = cursor_pos()
    screen = dpi_awareness(0).get("screen")
    return {"success": True, "x": x, "y": y, "screen": screen}

def main(): mcp.run(transport="stdio")
if __name__ == "__main__": main()
