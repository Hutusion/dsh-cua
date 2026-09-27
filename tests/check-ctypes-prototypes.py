"""Fail when a Win32 prototype the read-back path depends on is not declared.

Why this exists
    `check-ctypes-names.py` answers "does this ctypes NAME exist", and it says so itself: it
    polices the ctypes namespaces only, because "attributes on objects ... is the author's
    business". Every `user32.X.argtypes = ...` line in this repo is an attribute on an OBJECT, so
    it is entirely outside that checker's scope. Deleting one is invisible to the whole suite, and
    the cost of a missing prototype is that ctypes marshals the arguments with default types:
    on 64-bit that truncates a POINTER argument to 32 bits, which is a crash or silent memory
    corruption rather than an error — and only on the code path that uses it.

    This is the same class of defect as the 0.3.2 one that motivated check-ctypes-names.py: a
    path that is broken and invisible until a user happens to take it.

What it checks
    Two things, deliberately different in severity.

    1. REQUIRED (failure). A named list of Win32 entry points whose prototypes MUST be declared,
       each checked in the file that declares it. The list is the set the text-addressing and
       read-back path cannot work without, plus the two that tell a caller whether a window is
       minimized. It is a list rather than a derivation because only the author knows which calls
       need a prototype — the point is that adding a call to that path means adding a line here.

    2. UNDECLARED (report only). Every `user32.NAME(...)` call in `src/` whose NAME has no
       `argtypes` assignment anywhere in the same file. Reported, never failed, because the repo
       already contains such calls that work by luck (their handle values happen to fit in 32
       bits). The output is how a reader SEES the surface instead of trusting it.

usage:  python tests/check-ctypes-prototypes.py
        python tests/check-ctypes-prototypes.py --self-test   # prove the checker can fail
        exit 0 = every required prototype is declared; exit 1 = at least one is missing
"""
from __future__ import annotations

import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where each prototype must be declared, and why it is on the list.
REQUIRED: dict[str, dict[str, str]] = {
    "src/dsh_cua/bridge.py": {
        "GetClassNameW": "classifies a control as a text control",
        "GetWindowThreadProcessId": "resolves the thread that owns a window's focus",
        "GetGUIThreadInfo": "reads the thread's focused control (thread-focus resolution)",
        "EnumChildWindows": "walks a window's descendants for a writable text control",
        "GetWindowLongW": "reads GWL_STYLE, to recognise ES_READONLY",
        "SendMessageTimeoutW": "reads the control back, with a bound on a non-pumping target",
        "IsIconic": "reports whether the window is minimized",
    },
    "src/dsh_cua/uia.py": {
        "IsIconic": "reports whether the window is minimized, in skyshot / find_elements",
    },
}

# Calls that are known to run undeclared and are left as they were found. Named explicitly so
# the report below is a list of DECISIONS rather than a list of surprises.
KNOWN_UNDECLARED = {
    "GetForegroundWindow", "GetSystemMetrics", "SetProcessDPIAware",
    "MonitorFromWindow", "GetMonitorInfoW", "EnumDisplayMonitors",
}


def declared_prototypes(tree: ast.AST) -> set[str]:
    """Every NAME for which this file does `something.NAME.argtypes = ...`."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute) and target.attr == "argtypes"
                    and isinstance(target.value, ast.Attribute)):
                out.add(target.value.attr)
    return out


def called_user32(tree: ast.AST) -> set[str]:
    """Every NAME called as `user32.NAME(...)`."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "user32"):
            out.add(node.func.attr)
    return out


def check_source(source: str, rel: str, required: dict[str, str]) -> list[str]:
    """Missing required prototypes in one file, as readable lines."""
    tree = ast.parse(source, filename=rel)
    declared = declared_prototypes(tree)
    called = called_user32(tree)
    problems = [f"{rel}: {name} has NO `argtypes` declaration — {why}"
                for name, why in sorted(required.items()) if name not in declared]
    for name in sorted(called - declared - set(required) - KNOWN_UNDECLARED):
        problems.append(f"{rel}: {name} is called but never declared, and is not in "
                        f"KNOWN_UNDECLARED (report only, not a failure)")
    return problems


# The negative control. A checker that has never been seen to fail is a checker that may not
# work — and this one exists precisely because its predecessor could not fail on this input.
SELF_TEST = """
import ctypes
user32 = ctypes.windll.user32
user32.Kept.restype = ctypes.c_int
user32.Kept.argtypes = [ctypes.c_void_p]


def f(h):
    return user32.Kept(h), user32.Dropped(h)
"""


def self_test() -> int:
    problems = check_source(SELF_TEST, "<self-test>", {"Kept": "kept", "Dropped": "dropped"})
    if not any("Dropped has NO" in p for p in problems):
        print("FAIL: the checker did not catch a missing required prototype")
        print(f"  got: {problems}")
        return 1
    if any("Kept has NO" in p for p in problems):
        print("FAIL: the checker flagged a prototype that IS declared")
        print(f"  got: {problems}")
        return 1
    print("self-test OK: a declared prototype passes, a deleted one is caught")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    failures: list[str] = []
    reports: list[str] = []
    checked = 0
    for rel, required in REQUIRED.items():
        path = os.path.join(REPO, rel.replace("/", os.sep))
        if not os.path.exists(path):
            failures.append(f"{rel}: file does not exist — the REQUIRED list is stale")
            continue
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        checked += len(required)
        for problem in check_source(source, rel, required):
            (reports if "report only" in problem else failures).append(problem)

    print(f"required prototypes checked : {checked} across {len(REQUIRED)} file(s)")
    if reports:
        print(f"\nundeclared user32 calls (informational, {len(reports)}):")
        for line in reports:
            print(f"  {line}")
    if failures:
        print(f"\nFAIL: {len(failures)} required prototype(s) are not declared:")
        for line in failures:
            print(f"  {line}")
        return 1
    print("\nOK: every required Win32 prototype is declared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
