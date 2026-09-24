"""UI Automation access — element-addressed control, the ZCode `element_at_point` idea.

WHY THIS EXISTS
    `click_at` injects at the cursor, so the click is received by whatever is topmost.
    That is why it needs a guard, and why raising the window was the only way to make it
    land: the addressed `hwnd` never actually targeted anything. Addressing an ELEMENT
    instead of a pixel removes both problems — the action goes to the element, not to
    whatever happens to be on top, so no focus has to be stolen.

WHAT IS READ-ONLY
    Everything here except the explicit element actions. Reading a UIA tree does not move
    the cursor, change focus, or emit input (proven by `verify-no-input.py`). The action
    functions are deliberately NOT wired up yet: invoking an element changes application
    state, and that needs a test run where the desktop is free.

DESIGN NOTES BORROWED FROM ZCODE (see spec/06)
    * Refs are opaque handles into a registry — the caller never sees a raw COM pointer,
      and a ref is resolved back to an element at action time.
    * The payload carries what an agent needs to decide: name, role, rect, and which
      actions the element actually supports.
    * `skyshot` is the one deliberate exception to "no tree dump", and it is a bounded
      one: the tree is rendered as compact text with a stable index per line, and every
      shot after the first is a DIFF against the previous one. ZCode's reasoning is that
      an agent needs a readable surface it can point at, while a full tree on every turn
      would be unaffordable. See the skyshot section for the format and the diff rules.

    Why a tree is needed at all: `element_at_point` can only reach what a hit-test
    returns. On a Chromium window that is the enclosing `document`, never the input
    inside it — measured, not assumed (`verify-chromium-element.py`). Without a tree
    there is no way to name such an element, so no way to act on it.

WHAT IS READ-ONLY
    Everything here except the explicit element actions. Reading a UIA tree does not move
    the cursor, change focus, or emit input (proven by `verify-no-input.py`).

COORDINATES
    UIA returns coordinates in the calling process's DPI awareness space. `bridge.py`
    declares per-monitor-v2 at import, so these are PHYSICAL pixels — the same space as
    a client-cropped capture's `bounds` and as `click_at`'s resolved point. Mixing a
    capture from a DPI-unaware process with these coordinates would be off by the scale
    factor, which is exactly what the DPI gate exists to prevent.
"""
from __future__ import annotations

import ctypes
import threading
import time
from typing import Any, Optional

from . import arbiter  # coexistence gate: element actions take the cross-process mutex (soft)

_IMPORT_ERROR: Optional[str] = None
try:
    import comtypes
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as _M  # type: ignore
    from comtypes.gen.UIAutomationClient import (  # type: ignore
        CUIAutomation, IUIAutomation, tagPOINT,
    )

    _AVAILABLE = True
except Exception as exc:  # pragma: no cover - environment dependent
    _AVAILABLE = False
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

# Pattern/control-type ids are DERIVED FROM THE TYPELIB, never hardcoded.
#
# Two traps found the hard way (see spec/07):
#   * `GetCurrentPattern(pid)` is useless for capability detection — it returns a
#     pointer for EVERY pid, so it cannot tell supported from unsupported. A first
#     version built on it reported all 13 patterns for a Chromium Document.
#   * the `UIA_IsXxxPatternAvailablePropertyId` numbers are easy to get wrong from
#     memory: `IsTextPatternAvailable` is 30040 (not 30042) and
#     `IsLegacyIAccessiblePatternAvailable` is 30090 (not 30044). Wrong ids silently
#     mis-report capabilities, so we read the constants out of the typelib instead.
def _camel_to_snake(name: str) -> str:
    import re
    out = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    # "LegacyIAccessible" -> "legacy_i_accessible"; the readable form is what an
    # agent will look for, so normalise the one name that splits badly.
    return {"legacy_i_accessible": "legacy_accessible"}.get(out, out)


def _derive_pattern_props() -> dict:
    """action name -> the UIA property that says whether the element supports it."""
    out = {}
    if not _AVAILABLE:
        return out
    prefix, suffix = "UIA_Is", "PatternAvailablePropertyId"
    for name in dir(_M):
        if name.startswith(prefix) and name.endswith(suffix):
            action = _camel_to_snake(name[len(prefix):-len(suffix)])
            out[action] = getattr(_M, name)
    return out


def _derive_control_types() -> dict:
    """control-type id -> readable role name."""
    out = {}
    if not _AVAILABLE:
        return out
    suffix = "ControlTypeId"
    for name in dir(_M):
        if name.startswith("UIA_") and name.endswith(suffix):
            role = _camel_to_snake(name[len("UIA_"):-len(suffix)])
            out[getattr(_M, name)] = role
    return out


_PATTERN_PROPS = _derive_pattern_props()
CONTROL_TYPES = _derive_control_types()

# Control types that mean "this is a bare container, not the thing the caller wanted".
# Compared by ID, taken from the typelib — NOT by role name. An earlier version tested
# `role in ("Pane", "Window")`, which never matched, because every name in CONTROL_TYPES
# is lowercase (`pane`, `window`). The warm-up branch below was therefore dead code, and
# the failure was invisible: `element_at_point` simply returned the un-warmed container.
_CONTAINER_TYPE_IDS = frozenset(
    t for t in (
        getattr(_M, "UIA_PaneControlTypeId", None),
        getattr(_M, "UIA_WindowControlTypeId", None),
    ) if t is not None
) if _AVAILABLE else frozenset()


_local = threading.local()
_registry: dict[str, dict] = {}
_ref_counter = 0
_warmed: dict[int, float] = {}
_WARM_TTL_SECONDS = 300.0


def available() -> dict:
    """Whether UIA can be used at all in this process."""
    out: dict[str, Any] = {"available": _AVAILABLE}
    if not _AVAILABLE:
        out["error"] = _IMPORT_ERROR
    return out


def _uia() -> "IUIAutomation":
    """Per-thread UIA object.

    COM objects are apartment-bound, so the instance must not be shared across
    threads. The MCP tools that use this are declared `async def` so they run on the
    event-loop thread, but a per-thread instance keeps this correct either way.
    """
    obj = getattr(_local, "uia", None)
    if obj is None:
        comtypes.CoInitialize()
        obj = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        _local.uia = obj
    return obj


def _rect_of(el) -> tuple[int, int, int, int]:
    r = el.CurrentBoundingRectangle
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def _actions_of(el) -> list[str]:
    """Which actions this element actually supports.

    Asks the documented `IsXxxPatternAvailable` property for each pattern, so an agent
    is never told it can invoke something it cannot.
    """
    found = []
    for action, prop in _PATTERN_PROPS.items():
        try:
            if el.GetCurrentPropertyValue(prop) is True:
                found.append(action)
        except Exception:
            pass
    return sorted(found)


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def describe(el, *, with_actions: bool = True) -> dict:
    """Everything an agent needs to decide whether this is the element it wants."""
    x, y, w, h = _rect_of(el)
    ctype = _safe(lambda: el.CurrentControlType, 0)
    out = {
        "name": _safe(lambda: el.CurrentName, "") or "",
        "role": CONTROL_TYPES.get(ctype, f"type_{ctype}"),
        "control_type_id": ctype,
        "automation_id": _safe(lambda: el.CurrentAutomationId, "") or "",
        "class_name": _safe(lambda: el.CurrentClassName, "") or "",
        "rect": {"x": x, "y": y, "width": w, "height": h},
        "center": {"x": x + w // 2, "y": y + h // 2},
        "enabled": bool(_safe(lambda: el.CurrentIsEnabled, False)),
        "offscreen": bool(_safe(lambda: el.CurrentIsOffscreen, True)),
        "keyboard_focusable": bool(_safe(lambda: el.CurrentIsKeyboardFocusable, False)),
        "focused": bool(_safe(lambda: el.CurrentHasKeyboardFocus, False)),
        "pid": int(_safe(lambda: el.CurrentProcessId, 0) or 0),
        "actions": _actions_of(el) if with_actions else [],
    }
    # Report read-only-ness up front when the value pattern exists: a read-only value
    # accepts SetValue and silently ignores it, so an agent needs to know before trying.
    # The value itself is deliberately NOT included — that would pull text out of a
    # window the caller only pointed at.
    if "value" in out["actions"]:
        try:
            iface = getattr(_wrapper_for(el), "iface_value")
            out["value_read_only"] = bool(_safe(lambda: iface.CurrentIsReadOnly, False))
        except Exception:
            pass
    return out


def _register(el, payload: dict) -> str:
    """Store the element and return an opaque ref (ZCode's `toElementPayload` idea)."""
    global _ref_counter
    _ref_counter += 1
    ref = f"el{_ref_counter}"
    entry = {"element": el, "created": time.time(), "payload": payload}
    _registry[ref] = entry
    # Bound the registry so a long session cannot grow without limit.
    if len(_registry) > 500:
        for old in sorted(_registry, key=lambda k: _registry[k]["created"])[:100]:
            _registry.pop(old, None)
    return ref


def resolve(ref: str) -> dict:
    """Look a ref back up, reporting staleness instead of raising.

    A UIA element goes stale when the UI it came from is rebuilt (a re-render, a
    navigation). Rather than trusting the ref, touch a cheap property and let the
    caller know it must re-locate the element.
    """
    entry = _registry.get(ref)
    if entry is None:
        return {"ok": False, "reason": "unknown_ref", "error": f"no element registered as {ref!r}"}
    el = entry["element"]
    try:
        _rect_of(el)
    except Exception as exc:
        _registry.pop(ref, None)
        return {"ok": False, "reason": "stale_ref",
                "error": f"element {ref} no longer exists ({type(exc).__name__}); re-locate it"}
    return {"ok": True, "element": el, "payload": entry["payload"]}


def warm_up(hwnd: int, max_nodes: int = 1200) -> dict:
    """Trigger Chromium's lazy accessibility activation for a window.

    Chromium exposes its accessibility tree only after an assistive client asks for it,
    and it fills in asynchronously. A single immediate query therefore under-reports —
    on ZCode the first walk saw 20 nodes and every later walk saw 2,762. So the first
    query against a window walks the tree once, waits, and lets the next query succeed.
    """
    now = time.time()
    if hwnd in _warmed and now - _warmed[hwnd] < _WARM_TTL_SECONDS:
        return {"warmed": True, "cached": True}
    try:
        root = _uia().ElementFromHandle(hwnd)
        walker = _uia().ControlViewWalker
        count = 0
        stack = [root]
        while stack and count < max_nodes:
            el = stack.pop()
            count += 1
            try:
                child = walker.GetFirstChildElement(el)
                while child is not None:
                    stack.append(child)
                    child = walker.GetNextSiblingElement(child)
            except Exception:
                pass
        _warmed[hwnd] = time.time()
        time.sleep(0.6)
        return {"warmed": True, "cached": False, "nodes_seen": count}
    except Exception as exc:
        return {"warmed": False, "error": f"{type(exc).__name__}: {exc}"}


def element_at_point(screen_x: int, screen_y: int, *, register: bool = True,
                     auto_warm: bool = True) -> dict:
    """The element at a screen point — the primitive that makes focus-free clicking possible.

    `screen_x/screen_y` are PHYSICAL pixels (see module docstring).
    """
    if not _AVAILABLE:
        return {"ok": False, "reason": "uia_unavailable", "error": _IMPORT_ERROR}
    try:
        uia = _uia()
        pt = tagPOINT()
        pt.x = int(screen_x)
        pt.y = int(screen_y)
        el = uia.ElementFromPoint(pt)
        if el is None:
            return {"ok": False, "reason": "no_element_at_point",
                    "error": f"nothing at ({screen_x},{screen_y})"}

        payload = describe(el)
        hwnd = int(_safe(lambda: el.CurrentNativeWindowHandle, 0) or 0)

        # A Chromium window that has not been activated yet answers with a bare
        # container. Warm it up once, then ask again.
        if auto_warm and payload["control_type_id"] in _CONTAINER_TYPE_IDS and not payload["name"]:
            info = warm_up(hwnd) if hwnd else {"warmed": False}
            if info.get("warmed"):
                el = uia.ElementFromPoint(pt)
                payload = describe(el)
                payload["warmed_up"] = True

        payload["ok"] = True
        payload["point"] = {"x": int(screen_x), "y": int(screen_y)}
        payload["window_hwnd"] = hwnd
        if register:
            payload["ref"] = _register(el, payload)
        return payload
    except Exception as exc:
        return {"ok": False, "reason": "uia_error",
                "error": f"{type(exc).__name__}: {exc}"}


def read_element(ref: str) -> dict:
    """Re-read a previously located element by ref."""
    if not _AVAILABLE:
        return {"ok": False, "reason": "uia_unavailable", "error": _IMPORT_ERROR}
    got = resolve(ref)
    if not got.get("ok"):
        return got
    payload = describe(got["element"])
    payload["ok"] = True
    payload["ref"] = ref
    return payload


def focus_element(ref: str, *, dry_run: bool = True) -> dict:
    """Report whether a ref can be focused, without focusing it by default."""
    got = resolve(ref)
    if not got.get("ok"):
        return got
    el = got["element"]
    return {
        "ok": False,
        "reason": "not_implemented",
        "action_sent": False,
        "would_focus": describe(el, with_actions=False),
        "error": ("focusing an element is not enabled yet; it changes application state "
                  "and needs a test run with a free desktop"),
        "dry_run": dry_run,
    }


# ---- element actions ---------------------------------------------------------
# Acting on an ELEMENT is what makes "no focus stealing" possible: a UIA pattern is
# delivered to the element, not injected at a screen point, so the window never has to
# be brought forward. This is ZCode's level 1 (`element_press` / `element_set_value`).
#
# The pattern interfaces are NOT hand-rolled: pywinauto already wraps 19 of them
# (`iface_invoke`, `iface_value`, `iface_selection_item`, `iface_toggle`, ...). These
# functions are thin adapters over that, gated on the typelib-derived capability table
# so an unsupported action is refused with the list of what IS supported.
#
# ⚠️ pywinauto's MOUSE and KEYBOARD modules do NOT share this property — they inject via
# SendInput and therefore need the window foreground, exactly like `bridge.click`. Only
# the element patterns are focus-free. Do not "reuse" those for the pointer path.
#
# ✅ ENABLED 2026-09-18 after `verify-element-actions-selfcontained.py` passed 11/11 on a
# target the test owns: set_value verified by effect, press verified by a designed
# counter, reads focus-free, and the foreground never changed.
#
# Still true and worth knowing: activation is APP-DEPENDENT. Setting a value on Win11
# Notepad's document DID bring it forward, and a WinUI tab-close button accepted Invoke
# without doing anything. So every action result carries `effect_verified`,
# `foreground_changed` and `activated_target` — report, don't claim.
ACTIONS_ENABLED = True

# action name -> (capability key from _PATTERN_PROPS, pywinauto iface attribute, method)
ACTION_MAP = {
    "press": ("invoke", "iface_invoke", "Invoke"),
    "set_value": ("value", "iface_value", "SetValue"),
    "select": ("selection_item", "iface_selection_item", "Select"),
    "toggle": ("toggle", "iface_toggle", "Toggle"),
    "expand": ("expand_collapse", "iface_expand_collapse", "Expand"),
    "collapse": ("expand_collapse", "iface_expand_collapse", "Collapse"),
    "scroll_into_view": ("scroll_item", "iface_scroll_item", "ScrollIntoView"),
    "focus": (None, None, None),  # element.SetFocus() — no pattern involved
}


def _root_hwnd(hwnd: int) -> int:
    """Top-level ancestor of a window handle."""
    if not hwnd:
        return 0
    try:
        u = ctypes.windll.user32
        u.GetAncestor.restype = ctypes.c_void_p
        u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        root = u.GetAncestor(ctypes.c_void_p(int(hwnd)), 2)  # GA_ROOT
        return int(root or hwnd)
    except Exception:
        return int(hwnd)


def _foreground_root() -> int:
    """Top-level window that currently has focus. Local to avoid a bridge dependency;
    used only to report whether an action moved the foreground."""
    try:
        fg = ctypes.windll.user32.GetForegroundWindow()
        return _root_hwnd(int(fg)) if fg else 0
    except Exception:
        return 0


def _wrapper_for(el):
    """pywinauto's wrapper around a raw element, which carries the `iface_*` adapters.

    Imported lazily: `import pywinauto` forces the COM apartment back to STA (it prints
    `UserWarning: Revert to STA COM threading mode`). We already run STA via
    `comtypes.CoInitialize()`, so they agree today — but the coupling is worth keeping
    local to this one place rather than at module import.
    """
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo
    return UIAWrapper(UIAElementInfo(el))


# Pattern state that can be read before and after an action, so the effect can be
# CHECKED rather than assumed. `press` has no such state, which is why it is the one
# action whose effect cannot be verified — and why it reports that honestly.
#   action -> (iface attribute, property name, expected-after value or None for "changed")
EFFECT_STATE = {
    "set_value": ("iface_value", "CurrentValue", None),
    "select": ("iface_selection_item", "CurrentIsSelected", True),
    "toggle": ("iface_toggle", "CurrentToggleState", None),
    "expand": ("iface_expand_collapse", "CurrentExpandCollapseState", None),
    "collapse": ("iface_expand_collapse", "CurrentExpandCollapseState", None),
}


def element_action(ref: str, action: str, text: Optional[str] = None,
                   *, dry_run: bool = False) -> dict:
    """Perform one action on an element addressed by ref.

    MUTATING (soft gate): unless dry_run, takes the cross-process mutating mutex so
    two agent sessions never change application state at the same time. No
    input-quiet wait — a UIA pattern does not inject physical input, so it can run
    while the user is typing; the coexistence rule for these is "do not operate the
    same window the user is working in" (logical, not physical, contention).

    `action` is one of ACTION_MAP. `text` is required by `set_value`.
    Refuses, with the supported list, when the element does not offer the action.

    A pattern call being ACCEPTED is not evidence that it did anything. Two live runs
    showed calls that returned success and changed nothing (`SetValue` on a read-only
    value, `Invoke` on a tab's close button). So where the pattern exposes state it is
    read before and after, and the result carries `effect_verified`:
      True  — the state changed as expected
      False — the call was accepted but nothing changed (reported as ok=False)
      None  — no state to compare (press); the call was accepted, effect unconfirmed
    """
    gate = None
    if not dry_run:
        gate = arbiter.admit_mutating(hard=False)
        if not gate.get("ok"):
            return {"ok": False, "reason": gate.get("reason"), "action_sent": False,
                    "ref": ref, "action": action,
                    "arbiter": {k: v for k, v in gate.items() if k != "release"},
                    "error": gate.get("error")}
    try:
        out = _element_action_impl(ref, action, text, dry_run=dry_run)
    finally:
        if gate is not None:
            gate["release"]()
    if isinstance(out, dict):
        # Unconditional: a caller must be able to tell "gated and free" from "bypassed".
        out["arbiter"] = arbiter.receipt(gate)
    return out


def _element_action_impl(ref: str, action: str, text: Optional[str] = None,
                         *, dry_run: bool = False) -> dict:
    if not _AVAILABLE:
        return {"ok": False, "reason": "uia_unavailable", "error": _IMPORT_ERROR}
    if action not in ACTION_MAP:
        return {"ok": False, "reason": "unknown_action", "action_sent": False,
                "error": f"unknown action {action!r}; supported: {sorted(ACTION_MAP)}"}
    got = resolve(ref)
    if not got.get("ok"):
        return {**got, "action_sent": False}
    el = got["element"]
    payload = describe(el)

    capability, iface_attr, method = ACTION_MAP[action]
    if capability is not None and capability not in payload["actions"]:
        return {"ok": False, "reason": "action_not_supported", "action_sent": False,
                "ref": ref, "action": action, "element": payload,
                "error": (f"element {ref} ({payload['name']!r}, {payload['role']}) does not "
                          f"support {action!r}; it supports {payload['actions']}")}
    if action == "set_value" and not isinstance(text, str):
        return {"ok": False, "reason": "text_required", "action_sent": False,
                "error": "set_value needs a `text` argument"}

    would = {"ref": ref, "action": action, "method": method or "SetFocus",
             "element": {k: payload[k] for k in ("name", "role", "rect")}}
    if text is not None:
        would["text_length"] = len(text)

    if dry_run or not ACTIONS_ENABLED:
        return {"ok": False,
                "reason": "dry_run" if dry_run else "actions_disabled",
                "action_sent": False, "would_do": would,
                "error": ("preview only: nothing was sent" if dry_run else
                          "element actions are not enabled yet — they change application "
                          "state and need a test run on a free desktop "
                          "(set uia.ACTIONS_ENABLED = True once verified)")}

    # Read the pattern's state before acting, when it has any.
    state_attr, state_prop, expect = EFFECT_STATE.get(action, (None, None, None))
    before = None
    if state_attr is not None:
        try:
            iface = getattr(_wrapper_for(el), state_attr)
            before = _safe(lambda: getattr(iface, state_prop), None)
            if action == "set_value" and bool(_safe(lambda: iface.CurrentIsReadOnly, False)):
                return {"ok": False, "reason": "value_read_only", "action_sent": False,
                        "ref": ref, "action": action, "element": payload,
                        "effect_verified": False,
                        "error": (f"element {ref} ({payload['name']!r}, {payload['role']}) "
                                  f"has a read-only value; SetValue would silently do "
                                  f"nothing. Nothing was sent.")}
        except Exception:
            before = None

    fg_before = _foreground_root()
    try:
        if action == "focus":
            el.SetFocus()
        else:
            iface = getattr(_wrapper_for(el), iface_attr)
            if action == "set_value":
                iface.SetValue(text)
            else:
                getattr(iface, method)()
    except Exception as exc:
        return {"ok": False, "reason": "action_failed", "action_sent": False,
                "action": action, "ref": ref, "effect_verified": False,
                "error": f"{type(exc).__name__}: {exc}"}
    time.sleep(0.25)
    fg_after = _foreground_root()

    # Check the effect where the pattern exposes state.
    effect_verified = None
    after = None
    if state_attr is not None and before is not None:
        try:
            iface = getattr(_wrapper_for(el), state_attr)
            after = _safe(lambda: getattr(iface, state_prop), None)
            effect_verified = (after == expect) if expect is not None else (after != before)
        except Exception:
            effect_verified = None

    out = {"ok": True, "action_sent": True, "ref": ref, "action": action,
           "method": method or "SetFocus", "effect_verified": effect_verified,
           # Activation is APP-DEPENDENT: a UIA pattern is supposed to work without the
           # window being foreground, and our own Win32 target honours that, but setting
           # a value on Win11 Notepad's document did bring it forward. So measure and
           # report rather than claim, and let the caller decide what to do about it.
           "foreground_changed": fg_after != fg_before,
           "activated_target": (fg_after != fg_before
                                and fg_after == _root_hwnd(
                                    int(_safe(lambda: el.CurrentNativeWindowHandle, 0) or 0))),
           "element": would["element"]}
    if effect_verified is False:
        out["ok"] = False
        out["reason"] = "state_unchanged"
        out["error"] = (f"{action} was accepted but {state_prop} did not change "
                        f"(still {str(after)[:60]!r}); the pattern is inert for this "
                        f"element")
        if before is not None:
            out["before"] = str(before)[:80]
            out["after"] = str(after)[:80]
    elif effect_verified is None and action != "focus":
        out["effect_note"] = ("the pattern call was accepted; this action exposes no state "
                              "to compare, so the effect is unconfirmed")
    return out


def click_element_at_point(screen_x: int, screen_y: int, *,
                           expect_pid: Optional[int] = None,
                           dry_run: bool = False) -> dict:
    """ZCode's level 2: resolve a point to an element, then act on the ELEMENT.

    This is why the coordinates are only an input: the action lands on whatever element
    owns the point, so the window never has to be raised and z-order never enters into
    it. When `expect_pid` is given the element must belong to that process, which is the
    equivalent of ZCode's `assertClickElementOwnerPid`.

    `dry_run` has to be threaded through here, not only through the raw-input fallback:
    this element path is the DEFAULT, so a "preview" that silently pressed the button
    would make look-before-you-leap unusable exactly where it matters most (a
    save/discard dialog).
    """
    found = element_at_point(screen_x, screen_y)
    if not found.get("ok"):
        return {**found, "action_sent": False}
    if expect_pid is not None and found.get("pid") != expect_pid:
        return {"ok": False, "reason": "element_owner_mismatch", "action_sent": False,
                "point": found.get("point"), "element_pid": found.get("pid"),
                "expected_pid": expect_pid, "element": found,
                "error": (f"the element at ({screen_x},{screen_y}) belongs to pid "
                          f"{found.get('pid')}, not the expected pid {expect_pid}; "
                          f"nothing was sent")}
    if "invoke" not in (found.get("actions") or []):
        return {"ok": False, "reason": "not_invokable", "action_sent": False,
                "element": found, "method": None,
                "error": (f"the element at ({screen_x},{screen_y}) "
                          f"({found.get('name')!r}, {found.get('role')}) is not invokable; "
                          f"it supports {found.get('actions')}")}
    acted = element_action(found["ref"], "press", dry_run=dry_run)
    if acted.get("ok"):
        acted["method"] = "ax_press"
        acted["element"] = found
        acted["dry_run"] = dry_run
        return acted
    acted["method"] = None
    acted["element"] = found
    return acted


# ---- skyshot: a bounded text tree, with diff ---------------------------------
# ZCode's `get_skyshot` (spec/06 §2.5): hand the model a compact TEXT tree instead of a
# screenshot, and make every shot after the first a DIFF against the previous one. It
# solves two separate problems at once:
#
#   * context cost — a 2.4 MB PNG per step is unaffordable across a multi-step task
#   * reachability — `element_at_point` can only name what a hit-test returns. On a
#     Chromium window that is the enclosing `document`, never the input inside it
#     (measured, see `verify-chromium-element.py`). A tree can name it.
#
# Line format, from ZCode's `lineOf`:
#     {index} {indent}{role} {name}{attrs}{state}
#   indent = one tab per depth
#   attrs  = "Value: ..." when the element carries one; "id: ..." when it has no name
#   state  = (disabled, settable, focused), only the ones that are true
#
# Diff rules, from ZCode's `SkyshotDiffer`:
#   * a line's IDENTITY is its signature. An index is POSITIONAL: it is assigned by the
#     walk order of the shot it belongs to, so it can shift when the tree changes — it
#     is not a durable handle. What is durable is the signature, which is why the diff
#     matches on it.
#   * unchanged                  -> omitted entirely (carries no index)
#   * same signature, new text   -> "~ {depth}\t{index} {text}"   (index = this shot's)
#   * no previous signature      -> "+ {depth}\t{index} {text}"   (index = this shot's)
#   * disappeared                -> one line: "Removed element IDs: 3-7, 12"
#
# The numbers on "~" and "+" lines are the CURRENT tree's indices, so an agent may act
# on them directly. They used to come from a separate "next new element" counter, which
# meant the documented loop — read the diff, act on the number it shows — pressed
# whatever element held that positional index (measured 2026-09-20). Because indices are
# positional, an index read from an older shot must be re-checked against a fresh one
# when the tree may have changed.
#
# The signature is ZCode's `depth|role|name` PLUS the automation id when the element has
# one. ZCode's three-field form is not unique among siblings, and the collision is not
# theoretical: a window with two unnamed edits at the same depth gave both the signature
# `1|edit|`, so every shot reported a phantom "~ changed" line for whichever one lost the
# lookup. Measured, not reasoned about (`debug-firstshot2.py`). The automation id is
# Windows' stable per-control identifier, so including it separates those two without
# giving up the property that makes name-based identity worth having: inserting or
# removing an element does not renumber its siblings, so a list edit stays a small diff.
#
# Collisions are still possible — two siblings sharing role, name AND automation id, or
# all three empty. Rather than silently mis-attribute, the diff says so when it happens.
#
# ⚠️ Unlike `describe` / `element_at_point`, skyshot DOES read element values. That is the
# point of reading a UI, but it means the returned text can contain whatever is on screen
# in the addressed window — including text in fields. Callers should treat the output as
# window content, not as metadata.
SKYSHOT_DIFF_HEADER = ("The following is a diff from the previous accessibility tree "
                       "(~ changed, + added).")


def _window_identity(hwnd: int) -> dict:
    """(pid, class, title) for a top-level window — the cheapest proof that an HWND
    still refers to the window a shot was taken from. A reused HWND keeps its number
    but changes pid and/or class."""
    import ctypes.wintypes as _w
    try:
        u = ctypes.windll.user32
        pid = _w.DWORD()
        u.GetWindowThreadProcessId(_w.HWND(int(hwnd)), ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(_w.HWND(int(hwnd)), buf, 256)
        return {"pid": int(pid.value), "class_name": buf.value, "title": _window_title(hwnd)}
    except Exception:
        return {"pid": None, "class_name": "", "title": _window_title(hwnd)}


def _window_title(hwnd: int) -> str:
    try:
        u = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(ctypes.c_void_p(int(hwnd)), buf, 512)
        return buf.value
    except Exception:
        return ""


def _node_of(el, *, include_values: bool) -> dict:
    """A cheap read of one element.

    Deliberately NOT `describe()`: that probes all 32 pattern-availability properties to
    build the action list, which is 32 cross-process calls per node. A tree walk cannot
    afford that, so this reads only what a text line needs.
    """
    ctype = _safe(lambda: el.CurrentControlType, 0)
    node = {
        "role": CONTROL_TYPES.get(ctype, f"type_{ctype}"),
        "control_type_id": ctype,
        "name": _safe(lambda: el.CurrentName, "") or "",
        "automation_id": _safe(lambda: el.CurrentAutomationId, "") or "",
        "enabled": bool(_safe(lambda: el.CurrentIsEnabled, False)),
        "focused": bool(_safe(lambda: el.CurrentHasKeyboardFocus, False)),
        "offscreen": bool(_safe(lambda: el.CurrentIsOffscreen, True)),
        "value": None,
        "settable": False,
    }
    if include_values:
        prop = _PATTERN_PROPS.get("value")
        has_value = prop is not None and _safe(
            lambda: el.GetCurrentPropertyValue(prop)) is True
        if has_value:
            try:
                iface = getattr(_wrapper_for(el), "iface_value")
                node["value"] = _safe(lambda: iface.CurrentValue, None)
                node["settable"] = not bool(_safe(lambda: iface.CurrentIsReadOnly, False))
            except Exception:
                pass
    return node


_CONTROL_CHARS = {chr(c): f"\\x{c:02x}" for c in range(0x20)}
_CONTROL_CHARS["\n"] = "\\n"
_CONTROL_CHARS["\r"] = "\\r"
_CONTROL_CHARS["\t"] = "\\t"


def _escape(value) -> str:
    """Flatten control characters in an application-supplied string.

    Role, name, value and automation id all come from the TARGET application. The tree
    is a line-oriented text format whose leading number is the index an agent will act
    on, so an app that puts "\\n+ 1\\t5 button OK" in a window title can forge a line
    that looks like a real element — and with the positional index model it can also
    point at one. Escaping keeps every element on exactly one line, where its index is
    the one this walker assigned.
    """
    return "".join(_CONTROL_CHARS.get(ch, ch) for ch in str(value))


def _line_text(node: dict, depth: int) -> str:
    parts = [_escape(node["role"])]
    if node["name"]:
        parts.append(_escape(node["name"]))
    out = "\t" * depth + " ".join(parts)
    attrs = []
    if node["value"]:
        attrs.append(f"Value: {_escape(node['value'])}")
    if not node["name"] and node["automation_id"]:
        # Only when there is no name: for named elements the name is the better handle,
        # and always emitting ids roughly doubles the text of a large tree.
        attrs.append(f"id: {_escape(node['automation_id'])}")
    if attrs:
        out += " " + ", ".join(attrs)
    state = []
    if not node["enabled"]:
        state.append("disabled")
    if node["settable"]:
        state.append("settable")
    if node["focused"]:
        state.append("focused")
    if state:
        out += f" ({', '.join(state)})"
    return out


def _walk(hwnd: int, *, max_nodes: int, max_depth: int, include_offscreen: bool,
          include_values: bool) -> tuple[list[dict], bool]:
    """Walk the control-view tree, returning (records, truncated).

    ControlViewWalker rather than RawViewWalker: on Edge the control view was 100 nodes
    where the raw view was 550, and the extra 450 were separators, groups and layout
    scaffolding. The control view is the readable surface.
    """
    uia = _uia()
    walker = uia.ControlViewWalker
    records: list[dict] = []
    truncated = False
    index = 0

    def visit(el, depth: int) -> None:
        nonlocal index, truncated
        if len(records) >= max_nodes or depth > max_depth:
            truncated = True
            return
        node = _node_of(el, include_values=include_values)
        # Skip offscreen subtrees: an invisible container's children are invisible too.
        # The root is exempt — some apps report the top-level window itself as offscreen.
        if node["offscreen"] and not include_offscreen and depth > 0:
            return
        records.append({
            "index": index,
            "depth": depth,
            # Signature = ZCode's depth|role|name, plus the automation id when there is
            # one, so two unnamed siblings of the same role do not collide.
            "sig": f"{depth}|{node['role']}|{node['name']}|{node['automation_id']}",
            "text": _line_text(node, depth),
            "role": node["role"],
            "name": node["name"],
            "automation_id": node["automation_id"],
            "enabled": node["enabled"],
            "offscreen": node["offscreen"],
            "element": el,
        })
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

    visit(uia.ElementFromHandle(hwnd), 0)
    return records, truncated


def _summarize_ranges(ids: list[int]) -> str:
    """ZCode's `summarizeRanges`: [3,4,5,9] -> "3-5, 9"."""
    if not ids:
        return ""
    out = []
    start = prev = ids[0]
    for v in ids[1:]:
        if v == prev + 1:
            prev = v
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = v
    out.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(out)


class SkyshotDiffer:
    """Per-window previous-shot state. ZCode's `SkyshotDiffer`, keyed by window."""

    def __init__(self) -> None:
        self._prev: dict[str, list[dict]] = {}
        self._prev_max: dict[str, int] = {}

    def clear(self, key: Optional[str] = None) -> None:
        if key is None:
            self._prev.clear()
            self._prev_max.clear()
        else:
            self._prev.pop(key, None)
            self._prev_max.pop(key, None)

    def next_index(self, key: str) -> int:
        """The index the next new element would get for this window."""
        return self._prev_max.get(key, 0) + 1

    def render(self, key: str, records: list[dict], *, header: Optional[str] = None,
               disable_diff: bool = False) -> dict:
        lines = [{"index": r["index"], "depth": r["depth"], "sig": r["sig"],
                  "text": r["text"]} for r in records]
        prev = self._prev.get(key)
        if prev is None or disable_diff:
            self._prev[key] = lines
            self._prev_max[key] = lines[-1]["index"] if lines else 0
            body = [f"{l['index']} {l['text']}" for l in lines]
            return {"text": "\n".join(([header] if header else []) + body),
                    "is_diff": False, "lines": lines}

        # Pair previous and current lines ONE-TO-ONE by signature. A dict/set collapses
        # duplicates, which made a deleted duplicate invisible: prev=[A,A] vs cur=[A]
        # produced no "Removed" line at all (measured 2026-09-20), so the agent could
        # not tell that an element it was addressing had gone.
        prev_pool: dict[str, list] = {}
        for l in prev:
            prev_pool.setdefault(l["sig"], []).append(l)
        matched = set()
        out = [header] if header else []
        out.append(SKYSHOT_DIFF_HEADER)
        for l in lines:
            pool = prev_pool.get(l["sig"])
            # Print the CURRENT tree index, never a separate "new element" counter.
            # The index an agent reads is resolved by element_action_at against this
            # same shot's records, so a second numbering scheme here is not cosmetic:
            # it makes the documented loop (read the diff, act on the number it shows)
            # press whatever element happens to hold that positional index. Measured
            # 2026-09-20: the diff printed 1 while index 1 was a different control.
            if pool:
                p = pool.pop(0)
                matched.add(id(p))
                if p["text"] != l["text"]:
                    out.append(f"~ {l['depth']}\t{l['index']} {l['text']}")
            else:
                out.append(f"+ {l['depth']}\t{l['index']} {l['text']}")
        removed = sorted(l["index"] for l in prev if id(l) not in matched)
        if removed:
            out.append(f"Removed element IDs: {_summarize_ranges(removed)}")
        # Two elements sharing an identity cannot be told apart by a diff, so say so
        # rather than letting a "~" line be read as belonging to the wrong index.
        counts: dict[str, int] = {}
        for l in lines:
            counts[l["sig"]] = counts.get(l["sig"], 0) + 1
        ambiguous = sum(c - 1 for c in counts.values() if c > 1)
        if ambiguous:
            out.append(f"Note: {ambiguous} element(s) share an identity signature with a "
                       f"sibling, so the lines above may name the wrong index for them; "
                       f"use disable_diff=true for exact indices.")
        self._prev[key] = lines
        # Highest index this shot handed out — the only thing `next_index()` needs now
        # that the diff prints real indices instead of a separate counter.
        self._prev_max[key] = lines[-1]["index"] if lines else 0
        # `ambiguous` is machine-readable so a caller can require a full render instead
        # of trusting a diff whose lines may name the wrong index among siblings.
        return {"text": "\n".join(out), "is_diff": True, "lines": lines,
                "ambiguous": bool(ambiguous)}


_differ = SkyshotDiffer()
# Last shot per window, so a line index can be turned back into an element to act on.
# This is what closes the gap `element_at_point` leaves: an element the hit-test cannot
# name is still addressable, because the tree named it.
_shots: dict[str, dict] = {}
_SHOT_TTL_SECONDS = 600.0


def skyshot(hwnd: int, *, disable_diff: bool = False, include_offscreen: bool = False,
            include_values: bool = True, max_nodes: int = 800,
            max_depth: int = 30) -> dict:
    """Render the window's element tree as text; diff it against the previous shot.

    `disable_diff=True` forces a full render and resets the baseline — use it to get
    fresh indices, or when a diff would be ambiguous (duplicate signatures).
    """
    if not _AVAILABLE:
        return {"ok": False, "reason": "uia_unavailable", "error": _IMPORT_ERROR}
    root = _root_hwnd(hwnd)
    key = str(root)
    # Chromium answers with a bare container until something asks for the tree.
    warm_up(root)
    try:
        records, truncated = _walk(
            root, max_nodes=max_nodes, max_depth=max_depth,
            include_offscreen=include_offscreen, include_values=include_values)
    except Exception as exc:
        return {"ok": False, "reason": "uia_error",
                "error": f"{type(exc).__name__}: {exc}"}
    if not records:
        return {"ok": False, "reason": "empty_tree", "hwnd": root,
                "error": (f"the accessibility tree of window {root} is empty; it may not "
                          f"be ready yet, or the window does not expose one")}

    title = _window_title(root)
    header = f'Window: "{title}"' if title else None
    rendered = _differ.render(key, records, header=header, disable_diff=disable_diff)
    # Windows reuses HWND values, so the number alone does not identify a window. The
    # shot records the process and class it was taken from; element_action_at re-reads
    # them before acting, and refuses when they changed (see _window_identity).
    _shots[key] = {"records": records, "time": time.time(), "title": title,
                   "identity": _window_identity(root)}
    return {
        "ok": True,
        "hwnd": root,
        # The window this shot is keyed to. The diff baseline lives in this process and
        # survives across sessions, so a caller that only sees the diff text cannot
        # otherwise tell which window it was taken against — a live session asked
        # exactly that. `shot_key` makes it checkable instead of assumed.
        "shot_key": root,
        "window_title": title,
        "text": rendered["text"],
        "is_diff": rendered["is_diff"],
        "ambiguous_diff": rendered.get("ambiguous", False),
        "node_count": len(records),
        # Kept for compatibility only: the diff no longer uses a separate "new element"
        # counter (it prints this shot's real indices), and positional indices ARE
        # reused across shots, so this is not a boundary you can act above.
        "next_index": _differ.next_index(key),
        "truncated": truncated,
        "chars": len(rendered["text"]),
    }


def element_action_at(hwnd: int, index: int, action: str, text: Optional[str] = None,
                      *, dry_run: bool = False) -> dict:
    """Act on the element that line `index` of the last skyshot for `hwnd` named."""
    if not _AVAILABLE:
        return {"ok": False, "reason": "uia_unavailable", "error": _IMPORT_ERROR}
    key = str(_root_hwnd(hwnd))
    shot = _shots.get(key)
    if shot is None:
        return {"ok": False, "reason": "no_shot", "action_sent": False,
                "error": (f"no skyshot recorded for window {key}; call skyshot first, "
                          f"then act on one of the indices it printed")}
    if time.time() - shot["time"] > _SHOT_TTL_SECONDS:
        return {"ok": False, "reason": "stale_shot", "action_sent": False,
                "error": (f"the skyshot for window {key} is older than "
                          f"{int(_SHOT_TTL_SECONDS)}s; take a fresh one")}
    # Windows reuses HWND values. If the number now belongs to a different process or
    # window class, this shot's records address a window that no longer exists — acting
    # on them would press whatever now happens to hold that position. Refuse instead.
    identity = shot.get("identity")
    if identity is not None:
        current = _window_identity(int(key))
        if (current.get("pid"), current.get("class_name")) != (identity.get("pid"),
                                                               identity.get("class_name")):
            return {"ok": False, "reason": "window_reused", "action_sent": False,
                    "shot_identity": identity, "current_identity": current,
                    "error": (f"window {key} is no longer the window this skyshot was "
                              f"taken from (was pid {identity.get('pid')} / "
                              f"{identity.get('class_name')!r}, now pid "
                              f"{current.get('pid')} / {current.get('class_name')!r}); "
                              f"the shot was discarded — take a fresh one")}
    record = next((r for r in shot["records"] if r["index"] == index), None)
    if record is None:
        return {"ok": False, "reason": "no_such_index", "action_sent": False,
                "error": (f"index {index} is not in the last skyshot of window {key} "
                          f"(it had {len(shot['records'])} lines)")}
    try:
        payload = describe(record["element"])
    except Exception as exc:
        return {"ok": False, "reason": "stale_ref", "action_sent": False,
                "error": (f"the element at index {index} no longer exists "
                          f"({type(exc).__name__}); take a fresh skyshot")}
    ref = _register(record["element"], payload)
    out = element_action(ref, action, text, dry_run=dry_run)
    out["index"] = index
    out["shot_line"] = record["text"]
    return out


def find_elements(hwnd: int, *, role: Optional[str] = None,
                  name_contains: Optional[str] = None,
                  automation_id: Optional[str] = None,
                  include_offscreen: bool = True, max_results: int = 20,
                  max_nodes: int = 4000) -> dict:
    """Find elements by role / name, and return refs that can be acted on.

    This is the capability `element_at_point` cannot provide. A hit-test only answers
    "what is at this pixel", so anything not hit-testable — a page input inside a
    Chromium document, a control behind a scroll — was unreachable. Naming it by role
    and name makes it reachable, and the returned ref goes straight to `element_action`.
    """
    if not _AVAILABLE:
        return {"ok": False, "reason": "uia_unavailable", "error": _IMPORT_ERROR}
    if role is None and name_contains is None and automation_id is None:
        return {"ok": False, "reason": "no_filter",
                "error": ("give at least one of role / name_contains / automation_id — "
                          "an unfiltered search would just be a tree dump; use skyshot "
                          "for that")}
    root = _root_hwnd(hwnd)
    warm_up(root)
    try:
        # Values are not needed to match, and reading them is the expensive part.
        records, truncated = _walk(
            root, max_nodes=max_nodes, max_depth=40,
            include_offscreen=include_offscreen, include_values=False)
    except Exception as exc:
        return {"ok": False, "reason": "uia_error",
                "error": f"{type(exc).__name__}: {exc}"}

    needle = name_contains.lower() if name_contains else None
    matches = []
    for r in records:
        if role is not None and r["role"] != role.lower():
            continue
        if needle is not None and needle not in r["name"].lower():
            continue
        if automation_id is not None and r["automation_id"] != automation_id:
            continue
        matches.append(r)

    out_matches = []
    for r in matches[:max_results]:
        try:
            payload = describe(r["element"])
        except Exception:
            continue
        payload["ok"] = True
        payload["index"] = r["index"]
        payload["ref"] = _register(r["element"], payload)
        out_matches.append(payload)

    return {
        "ok": True,
        "hwnd": root,
        "window_title": _window_title(root),
        "matched": len(matches),
        "returned": len(out_matches),
        "truncated": truncated,
        "elements": out_matches,
    }
