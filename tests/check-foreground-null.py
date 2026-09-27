"""NULL foreground window must be a refusal, not an MCP isError.

A pre-release review found `tool_type_text` returning
`isError: true / TypeError: int() argument must be ... not 'NoneType'` when
`GetForegroundWindow()` returns NULL. Root cause: `bridge.py` declares that call with a POINTER
restype (`w.HWND`), so NULL arrives as Python `None`, and `int(None)` raises. `_root()` guards `0`
but nothing guarded `None`, and two call sites fed it straight into `int()` — one of them inside
`_ensure_visible`, which every one of `type_text`, `click_at`, `send_keys` and `send_alt_key` goes
through.

The reviewer could not construct the natural desktop trigger on demand, and neither could I. So
this does not try to: it replaces the `user32` handle with a stub that returns NULL, which tests
the exact code path deterministically and on any machine, including a CI runner with no desktop.

It asserts both halves — that the old form really would raise (so the bug was real), and that the
shipped form returns 0 (so it is fixed). A guard test that cannot fail is worth nothing.

usage:  python tests/check-foreground-null.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dsh_cua import bridge  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, label, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"\n         {detail}" if detail else ""))


class NullForeground:
    """Stands in for `user32` with a foreground window of NULL.

    Only `GetForegroundWindow` matters: it returns Python `None`, which is what a POINTER restype
    produces for NULL. Every other entry point returns 0/False through `__getattr__`, so the
    callers can run on to their report instead of dying on the next missing attribute — the claim
    under test is that a NULL foreground is handled, not that a real window gets raised.
    """

    def GetForegroundWindow(self):
        return None

    def __getattr__(self, name):
        return lambda *_args, **_kwargs: 0


def main() -> int:
    print("=" * 74)
    print("NULL foreground window: was a crash, must be a clean 0")
    print("=" * 74)

    # 1. The mechanism. If this does not raise, the reported bug had a different cause and the
    #    rest of this file is testing nothing.
    raised = ""
    try:
        int(None)
    except TypeError as exc:
        raised = str(exc)
    check("the old form `int(GetForegroundWindow())` really does raise on NULL",
          "NoneType" in raised, f"TypeError: {raised}")

    # 2. The fix, through the public function the four tools actually call.
    original = bridge.user32
    bridge.user32 = NullForeground()
    try:
        try:
            root = bridge.foreground_root()
            check("foreground_root() returns 0 instead of raising", root == 0,
                  f"returned {root!r}")
        except Exception as exc:  # noqa: BLE001
            check("foreground_root() returns 0 instead of raising", False,
                  f"{type(exc).__name__}: {exc}")

        # 3. The helper directly, so a future refactor that reintroduces `int(...)` here is caught
        #    even if foreground_root() is rewritten around it.
        try:
            raw = bridge._foreground_hwnd()
            check("_foreground_hwnd() normalises NULL to 0", raw == 0, f"returned {raw!r}")
        except Exception as exc:  # noqa: BLE001
            check("_foreground_hwnd() normalises NULL to 0", False,
                  f"{type(exc).__name__}: {exc}")

        # 4. The second call site: `_ensure_visible` reads the foreground itself before it does
        #    anything else. It must not raise on the way to reporting a failure.
        try:
            info = bridge._ensure_visible(0)
            ok = isinstance(info, dict) and "raised" in info
            check("_ensure_visible(0) reports rather than raising", ok, f"-> {info}")
        except Exception as exc:  # noqa: BLE001
            check("_ensure_visible(0) reports rather than raising", False,
                  f"{type(exc).__name__}: {exc}")
    finally:
        bridge.user32 = original

    # 5. And the real thing still works when there IS a foreground window.
    try:
        real = bridge.foreground_root()
        check("foreground_root() still works against the real desktop", isinstance(real, int),
              f"returned {real}")
    except Exception as exc:  # noqa: BLE001
        check("foreground_root() still works against the real desktop", False,
              f"{type(exc).__name__}: {exc}")

    failed = [r for r in RESULTS if not r[0]]
    print()
    print("=" * 74)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
