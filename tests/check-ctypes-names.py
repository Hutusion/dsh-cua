"""Fail when a ctypes attribute is read from the module that does not define it.

Why this exists
    0.3.2 shipped `tool_list_displays` broken on EVERY Windows machine, with
    `module 'ctypes' has no attribute 'BOOL'`. bridge.py's monitor callback took
    BOOL / HMONITOR / HDC / LPARAM off plain `ctypes`, but those names live in
    `ctypes.wintypes` — a module the same function had already imported as `_wt`, and
    used correctly for RECT two tokens later in the same decorator.

    Nothing caught it, and nothing COULD have. Every tool is Windows-only, so the CI job
    (ubuntu-latest) can only ever exercise the guard that refuses them; and the Windows
    scripts each call the three or four tools they need. A tool that always raises is
    therefore invisible until a user calls it — which is what happened.

    This check needs neither Windows nor a desktop, so it runs in CI. It resolves each
    ctypes attribute access against the real modules with hasattr at parse time, which is
    sufficient because the defect is a NAME THAT DOES NOT EXIST — the exact question
    hasattr answers. On the 0.3.2 tree it reported bridge.py:778 four times.

What it checks
    Every `*.py` in the repo. For each file it collects the local aliases bound to the
    ctypes namespaces, then walks each attribute chain the way Python would, stopping at
    the first name that does not exist:
        import ctypes                      -> ctypes
        import ctypes as _ct               -> _ct
        import ctypes.wintypes as w        -> w          (bridge.py's alias)
        from ctypes import wintypes as _wt -> _wt
    `from ctypes import name` is checked too: the name must exist on that module.
    Aliases are collected from the whole file, so an import inside a function (where the
    guard puts them) is policed like a module-level one.

Not checked
    Attributes on objects, only on the ctypes namespaces: `mi.cbSize` is the author's
    business. `build/`, `dist/` and virtualenvs are skipped — generated copies would keep
    reporting a defect that is already fixed in `src/`.

usage:  python tests/check-ctypes-names.py
        python tests/check-ctypes-names.py --self-test   # prove the checker can fail
        exit 0 = every name resolves; exit 1 = at least one cannot
"""
from __future__ import annotations

import ast
import ctypes
import inspect
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "build", "dist", "__pycache__", ".venv", "venv", ".mypy_cache"}

# The namespaces under police. Imported eagerly: ctypes.wintypes is pure Python and loads
# on POSIX too, but if a platform ever refuses it we skip rather than pretend to pass.
MODULES: dict[str, object] = {"ctypes": ctypes}
try:
    import ctypes.wintypes as _wintypes
    MODULES["ctypes.wintypes"] = _wintypes
except Exception as exc:  # noqa: BLE001
    print(f"SKIP: ctypes.wintypes is not importable here ({exc}) — nothing to police")
    sys.exit(0)


def aliases_in(tree: ast.AST) -> dict[str, str]:
    """Map local alias name -> 'ctypes' | 'ctypes.wintypes' for every import in the file."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in MODULES:
                    continue
                if a.asname:
                    # `import ctypes as _ct` -> _ct IS ctypes
                    # `import ctypes.wintypes as w` -> w IS ctypes.wintypes (bridge.py)
                    found[a.asname] = a.name
                else:
                    # `import ctypes.wintypes` binds the ROOT name ctypes; the author then
                    # reaches the submodule as `ctypes.wintypes`, which walk_chain handles.
                    found[a.name.split(".")[0]] = "ctypes"
        elif isinstance(node, ast.ImportFrom) and node.module in MODULES:
            for a in node.names:
                if a.name == "wintypes" and node.module == "ctypes":
                    found[a.asname or "wintypes"] = "ctypes.wintypes"
    return found


def chain_for(node: ast.Attribute) -> tuple[str, list[str]] | None:
    """Flatten `a.b.c` to ('a', ['b', 'c']); None when the root is not a plain name."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.reverse()
    return cur.id, parts


def walk_chain(base: str, parts: list[str]) -> tuple[str, str] | None:
    """Resolve like Python does.

    Return (namespace, missing_attribute) for the first name that does not exist, else None.
    """
    namespace = base
    obj: object = MODULES[base]
    for attr in parts:
        if obj is MODULES["ctypes"] and attr == "wintypes":
            obj, namespace = MODULES["ctypes.wintypes"], "ctypes.wintypes"
            continue
        if not hasattr(obj, attr):
            return namespace, attr
        obj = getattr(obj, attr)
        # Keep descending only through namespaces we can reason about; a value or a struct
        # instance ends the chain, and attributes on those are out of scope.
        if not (inspect.ismodule(obj) or isinstance(obj, type)):
            return None
        namespace = f"{namespace}.{attr}"
    return None


def scan(source: str, label: str) -> list[dict]:
    """Every unresolvable ctypes name in one file, as {line, expr, namespace, attr}."""
    tree = ast.parse(source, filename=label)
    aliases = aliases_in(tree)
    problems: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            flat = chain_for(node)
            if not flat:
                continue
            base = aliases.get(flat[0])
            if base is None:
                continue
            miss = walk_chain(base, flat[1])
            if miss:
                problems.append({"line": node.lineno, "expr": ast.unparse(node),
                                 "namespace": miss[0], "attr": miss[1]})
        elif isinstance(node, ast.ImportFrom) and node.module in MODULES:
            for a in node.names:
                if a.name == "wintypes" and node.module == "ctypes":
                    continue
                if not hasattr(MODULES[node.module], a.name):
                    problems.append({"line": node.lineno,
                                     "expr": f"from {node.module} import {a.name}",
                                     "namespace": node.module, "attr": a.name})
    problems.sort(key=lambda p: (p["line"], p["expr"]))
    return problems


# Four names that cannot resolve, four that must not be flagged. The valid four matter as
# much as the broken ones: a checker that flags everything is as useless as one that flags
# nothing, and the first version of this file flagged 92 valid names (it mapped every
# `import` to plain ctypes, so bridge.py's `import ctypes.wintypes as w` was misread).
SELF_TEST = """
import ctypes as _ct
from ctypes import wintypes as _wt
import ctypes.wintypes as w


def valid():
    _ct.sizeof(_ct.c_int)
    _wt.RECT()
    _ct.wintypes.BOOL
    w.HWND


def broken():
    _ct.BOOL
    _ct.HMONITOR
    _wt.NOT_A_REAL_TYPE
    w.NOPE
"""

SELF_TEST_EXPECTED = {
    ("_ct.BOOL", "ctypes", "BOOL"),
    ("_ct.HMONITOR", "ctypes", "HMONITOR"),
    ("_wt.NOT_A_REAL_TYPE", "ctypes.wintypes", "NOT_A_REAL_TYPE"),
    ("w.NOPE", "ctypes.wintypes", "NOPE"),
}


def self_test() -> int:
    """A checker nobody has seen fail is a checker that may not work."""
    seen = {(p["expr"], p["namespace"], p["attr"]) for p in scan(SELF_TEST, "<self-test>")}
    if seen != SELF_TEST_EXPECTED:
        print("FAIL: the checker's own negative control did not behave")
        print(f"  expected: {sorted(SELF_TEST_EXPECTED)}")
        print(f"  got     : {sorted(seen)}")
        return 1
    print("self-test OK: 4 unresolvable names caught, 0 of the 4 valid ones flagged")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    targets: list[str] = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        targets += [os.path.join(root, f) for f in files if f.endswith(".py")]
    targets.sort()

    problems: list[tuple[str, dict]] = []
    for path in targets:
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"FAIL: cannot read {rel}: {exc}")
            return 1
        problems += [(rel, p) for p in scan(source, rel)]

    print(f"scanned     : {len(targets)} python files under {os.path.basename(REPO)}/")
    print(f"namespaces  : {', '.join(sorted(MODULES))}")
    if problems:
        print(f"\nFAIL: {len(problems)} ctypes name(s) cannot resolve at runtime:")
        for rel, p in problems:
            print(f"  {rel}:{p['line']}: {p['expr']} — "
                  f"{p['namespace']} has no attribute {p['attr']!r}")
        return 1
    print("\nOK: every ctypes / ctypes.wintypes name in the repo resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
