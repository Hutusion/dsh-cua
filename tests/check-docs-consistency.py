#!/usr/bin/env python3
"""Assert that the docs still describe the code.

Every check below exists because the real project got it wrong at least once, and
nothing caught it.  The failure class is "documentation drift": the doc said one
thing, the code/tag/release said another, and the doc was the thing that was wrong.

Known instances this file is written against (all measured 2026-09-24..28):

  * README advertised 5 tools as "hard gate / yields to the human"; the code had
    2 `hard=True` sites.  ``skill/computer-use/SKILL.md`` was right, the README was
    wrong, and the two disagreed *inside the same repo*.  Someone recording a demo
    from the wrong half of the README would have captured nothing happening.
  * README listed `uvx dsh-cua` as the recommended install while the package was
    not on PyPI at all (JSON API -> 404).  Two of three documented install paths
    failed; the only working one was listed last.
  * `serverInfo.version` reported `1.28.1` for five releases, because the version
    was never wired through.
  * `server.json`'s description was raised to 239 characters, over the registry
    schema's `maxLength: 100`; the publish workflow failed *after* the tag existed.
  * The version number lives in four places; every release is an opportunity for
    them to disagree.
  * 19 tools were advertised and exactly one was ever actually called in a test,
    so a tool that raised on every call on Windows survived five releases.

Run:  python tests/check-docs-consistency.py [--repo <path>]
      python tests/check-docs-consistency.py --self-test

`--self-test` is the negative control: it builds a fixture repo that is wrong in a
known way and asserts the checks fail on it.  A checker nobody has seen fail is a
checker that may not work -- the same reason `check-ctypes-prototypes.py` has one.

The corpus in ``tests/mutants.json`` is the durable form of that negative
control: ``--self-test`` replays every mutant against a copy of this file
itself in a subprocess and asserts the caught/escape set equals the recorded
expectations, in BOTH directions, so the claim is recomputed on every run
instead of asserted from memory.

## Why SKIP is treated as a first-class outcome, not a pass

Several checks cannot run against an arbitrary tree: no `server.json` to measure, no
`tests/` to compare against, no numeric claim in either README.  Each of those used to
be a silent no-op -- indistinguishable in CI from a check that ran and found nothing
wrong.  That is precisely the failure class this file exists to catch, one level up:
in CI a run of 9 SKIPs is *green*, and green is what everyone reads.

So a SKIP now does two things.  It is counted and reported (a run where every check
skipped is a hard FAIL, because a tree in which nothing is checkable is not a tree
that has been verified), and under GitHub Actions it is emitted as a `::notice`
annotation, so "this run asserted nothing" lands on the pull request instead of in a
log nobody opens.

The annotation path is itself exercised by `--self-test`, for the same reason the
checks are: a reporting channel nobody has seen produce output may not produce output.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

WORD_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Tools that are advertised but that no desktop-free test can call, with the reason.
# A tool listed here is a deliberate, reviewable decision; a tool that appears as
# "never called" and is NOT listed here fails the run.
UNCALLED_OK = {
    # Requires a real interactive desktop session (a runner has none).
    "tool_click_at": "needs a real desktop: raw_event path moves the physical cursor",
    "tool_send_keys": "needs a real desktop: injects global hotkeys",
    "tool_element_action": "needs a live target window with a UIA pattern",
    "tool_element_action_at": "needs a prior skyshot against a live window",
    "tool_element_at_point": "needs a real screen point and a live window",
    "tool_read_element": "refs go stale; needs a live window across two calls",
    "tool_type_text": "needs a live editable control to read back from",
    "tool_open_application": "launches a real application (focus-taking)",
    "tool_capture_window": "needs a real window to capture pixels from",
    "tool_skyshot": "needs a live window tree",
    "tool_find_elements": "needs a live window tree",
    "tool_find_window": "needs a live window with a known title",
    "tool_get_window_rect": "needs a live window",
    "tool_list_windows": "enumerates the real desktop",
    "tool_list_displays": "enumerates real monitors",
    "tool_cursor_position": "reads the real cursor",
    "tool_coexistence_status": "reads live arbiter state (callable, but only meaningful with a desktop)",
    "tool_clipboard_write": "destroys the user's clipboard; deliberately not run unattended",
}

results: list[tuple[str, str, str]] = []  # (status, name, detail)

# "auto" emits GitHub annotations only under Actions; "always" is used by --self-test
# so the annotation path is exercised on every run rather than only in CI; "never"
# keeps local output clean.  Set by main() from --annotate.
ANNOTATE = "auto"


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def annotate(level: str, message: str, title: str = "") -> None:
    """Emit a GitHub Actions workflow command, or nothing when not applicable.

    Workflow commands are just lines of stdout outside Actions, so this is a no-op
    by policy rather than by capability -- `auto` checks the environment.
    """
    if ANNOTATE == "never":
        return
    if ANNOTATE == "auto" and os.environ.get("GITHUB_ACTIONS") != "true":
        return
    if "\n" in message:
        message = message.replace("\n", " ")
    if title:
        sys.stdout.write("::%s title=%s::%s\n" % (level, title, message))
    else:
        sys.stdout.write("::%s::%s\n" % (level, message))
    sys.stdout.flush()


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# individual checks
# --------------------------------------------------------------------------- #
def check_version_consistency(repo: str) -> None:
    """Every declared version must agree."""
    found: dict[str, str] = {}
    pyproject = os.path.join(repo, "pyproject.toml")
    if os.path.exists(pyproject):
        m = re.search(r'^version\s*=\s*"([^"]+)"', read(pyproject), re.M)
        if m:
            found["pyproject.toml"] = m.group(1)

    init = os.path.join(repo, "src", "dsh_cua", "__init__.py")
    if os.path.exists(init):
        m = re.search(r'^__version__\s*=\s*"([^"]+)"', read(init), re.M)
        if m:
            found["src/dsh_cua/__init__.py"] = m.group(1)

    srv = os.path.join(repo, "server.json")
    if os.path.exists(srv):
        for i, m in enumerate(re.finditer(r'"version"\s*:\s*"([^"]+)"', read(srv)), 1):
            found["server.json#%d" % i] = m.group(1)

    if not found:
        record("SKIP", "version consistency", "no version declarations found")
        return
    values = set(found.values())
    if len(values) == 1:
        record("PASS", "version consistency", "%s in %d places" % (values.pop(), len(found)))
    else:
        record("FAIL", "version consistency",
               "disagreement: " + ", ".join("%s=%s" % (k, v) for k, v in sorted(found.items())))


def check_server_json_description(repo: str) -> None:
    """registry schema caps description at 100 characters."""
    srv = os.path.join(repo, "server.json")
    if not os.path.exists(srv):
        record("SKIP", "server.json description length", "no server.json")
        return
    try:
        doc = json.loads(read(srv))
    except Exception as exc:  # noqa: BLE001
        record("FAIL", "server.json description length", "server.json is not valid JSON: %s" % exc)
        return
    bad = []
    for path, value in _walk_strings(doc, "description"):
        if len(value) > 100:
            bad.append("%s = %d chars" % (path, len(value)))
    if bad:
        record("FAIL", "server.json description length", "; ".join(bad) + " (registry maxLength is 100)")
    else:
        record("PASS", "server.json description length", "all descriptions <= 100 chars")


def _walk_strings(obj, key: str, prefix: str = "server.json"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, str):
                yield "%s.%s" % (prefix, k), v
            yield from _walk_strings(v, key, "%s.%s" % (prefix, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, key, "%s[%d]" % (prefix, i))


def code_tool_names(repo: str) -> set[str]:
    src = os.path.join(repo, "src")
    names: set[str] = set()
    if not os.path.isdir(src):
        return names
    for root, _dirs, files in os.walk(src):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            for m in re.finditer(r"^\s*(?:async\s+)?def\s+(tool_[A-Za-z0-9_]+)", read(os.path.join(root, fn)), re.M):
                names.add(m.group(1))
    return names


def count_registered_tools(repo: str) -> int:
    src = os.path.join(repo, "src")
    n = 0
    if not os.path.isdir(src):
        return 0
    for root, _dirs, files in os.walk(src):
        for fn in files:
            if fn.endswith(".py"):
                n += len(re.findall(r"@mcp\.tool\(", read(os.path.join(root, fn))))
    return n


# A number sitting right after one of these is a claim about a SUBSET, not the total.
# Measured false positive: `| 只读 | 观测类 12 个工具 |` in README.zh-CN.md counted as
# "the README says 12 tools" when it says 12 *observation* tools out of 19.
QUALIFIERS = ("只读", "观测", "软门", "硬门", "级别", "read-only", "observation", "gate")


def claimed_tool_counts(repo: str) -> list[tuple[str, int, int]]:
    """-> [(file, lineno, claimed_number)] for every claim about the TOTAL tool count."""
    claims = []
    for name in ("README.md", "README.zh-CN.md"):
        p = os.path.join(repo, name)
        if not os.path.exists(p):
            continue
        for i, line in enumerate(read(p).splitlines(), 1):
            for m in re.finditer(r"(\d+)\s*(?:tools?\b|个工具)", line):
                window = line[max(0, m.start() - 12):m.start()]
                if any(q in window.lower() for q in QUALIFIERS):
                    continue  # a subset claim, e.g. "12 observation tools"
                claims.append((name, i, int(m.group(1))))
            m = re.search(r"\|\s*(?:Tool count|工具数)\s*\|\s*(\d+)\s*\|", line)
            if m:
                claims.append((name, i, int(m.group(1))))
    return claims


def check_tool_count(repo: str) -> None:
    registered = count_registered_tools(repo)
    claims = claimed_tool_counts(repo)
    if not registered:
        record("SKIP", "advertised tool count == registered", "no @mcp.tool registrations found")
        return
    if not claims:
        record("SKIP", "advertised tool count == registered", "READMEs make no numeric tool claim")
        return
    wrong = [(f, ln, c) for f, ln, c in claims if c != registered]
    if wrong:
        record("FAIL", "advertised tool count == registered",
               "code registers %d; docs claim %s" % (
                   registered, "; ".join("%s:%d says %d" % (f, ln, c) for f, ln, c in wrong)))
    else:
        record("PASS", "advertised tool count == registered",
               "%d tools, claimed consistently in %d places" % (registered, len(claims)))


def count_hard_gates(repo: str) -> int:
    """Count hard-gate CALL SITES, not mentions.

    Measured false positive: `arbiter.py`'s docstring explains the modes with the
    text `hard=True  -> mutex + human-contention yield`, which a naive search for
    `hard=True` counts as a third gate.
    """
    src = os.path.join(repo, "src")
    n = 0
    if os.path.isdir(src):
        for root, _dirs, files in os.walk(src):
            for fn in files:
                if fn.endswith(".py"):
                    n += len(re.findall(r"\badmit_mutating\(\s*hard\s*=\s*True\s*\)",
                                        read(os.path.join(root, fn))))
    return n


def check_hard_gate_claim(repo: str) -> None:
    """README says how many things yield to the human; count them in the code.

    This is the check that would have caught the 5-vs-2 contradiction.
    """
    actual = count_hard_gates(repo)
    p = os.path.join(repo, "README.md")
    if not os.path.exists(p):
        record("SKIP", "hard-gate claim == hard=True sites", "no README.md")
        return
    if not actual and actual != 0:
        record("SKIP", "hard-gate claim == hard=True sites", "no hard=True sites found")
        return
    lines = read(p).splitlines()
    claims = []
    for i, line in enumerate(lines, 1):
        if "hard gate" not in line.lower():
            continue
        m = re.search(r"exactly\s+([a-z]+|\d+)", line, re.I)
        if m:
            tok = m.group(1).lower()
            num = WORD_NUM.get(tok)
            if num is None and tok.isdigit():
                num = int(tok)
            if num is not None:
                claims.append((i, num))
    if not claims:
        record("SKIP", "hard-gate claim == hard=True sites",
               "README makes no spelled-out 'exactly N' claim near 'hard gate'")
        return
    wrong = [(ln, c) for ln, c in claims if c != actual]
    if wrong:
        record("FAIL", "hard-gate claim == hard=True sites",
               "code has %d hard=True; README says %s" % (
                   actual, "; ".join("line %d claims %d" % (ln, c) for ln, c in wrong)))
    else:
        record("PASS", "hard-gate claim == hard=True sites",
               "%d hard=True sites, README agrees" % actual)


def advertised_tool_names(repo: str) -> set[str]:
    names: set[str] = set()
    for name in ("README.md", "README.zh-CN.md"):
        p = os.path.join(repo, name)
        if os.path.exists(p):
            names |= set(re.findall(r"tool_[a-z0-9_]+", read(p)))
    return names


def check_advertised_tools_exist(repo: str) -> None:
    real = code_tool_names(repo)
    advertised = advertised_tool_names(repo)
    if not real or not advertised:
        record("SKIP", "every advertised tool name exists", "nothing to compare")
        return
    ghosts = sorted(advertised - real)
    if ghosts:
        record("FAIL", "every advertised tool name exists",
               "documented but not defined: %s" % ", ".join(ghosts))
    else:
        record("PASS", "every advertised tool name exists",
               "%d advertised names all exist in code" % len(advertised))


def check_uncalled_tools(repo: str) -> None:
    """Every advertised tool must be exercised by some test, or be explicitly exempted.

    19 tools were advertised and one was called; a tool that raised on every call on
    Windows shipped in five releases because of it.
    """
    real = code_tool_names(repo)
    tests = os.path.join(repo, "tests")
    if not real or not os.path.isdir(tests):
        record("SKIP", "advertised tools are exercised by tests", "no tests dir")
        return
    called: set[str] = set()
    # Covers an MCP JSON payload ({"name": "tool_x"}) and both call shapes seen in
    # this repo: `await call_tool("tool_x")` and `server.call("tool_x", {})`.
    # Measured false positive: the receiver form was missed, so tool_clipboard_read
    # was reported as never called when tests/verify-tools-readonly.py:255 calls it.
    pat = re.compile(
        r"""["']name["']\s*:\s*["'](tool_[a-z0-9_]+)["']"""
        r"""|(?:await\s+)?[A-Za-z_][\w.]*call(?:_tool)?\(\s*["'](tool_[a-z0-9_]+)["']""",
        re.I,
    )
    for root, _dirs, files in os.walk(tests):
        for fn in files:
            if fn.endswith((".py", ".c", ".ps1")):
                for m in pat.finditer(read(os.path.join(root, fn))):
                    called.add(m.group(1) or m.group(2))
    never = sorted(t for t in real if t not in called)
    unexplained = [t for t in never if t not in UNCALLED_OK]
    if unexplained:
        record("FAIL", "advertised tools are exercised by tests",
               "never called anywhere in tests/ and not exempted: %s" % ", ".join(unexplained))
    else:
        record("PASS", "advertised tools are exercised by tests",
               "%d called, %d exempted with a stated reason" % (len(called & real), len(never)))


def check_readme_local_paths(repo: str) -> None:
    """Paths the README tells the reader to use must exist."""
    missing = []
    pat = re.compile(r"(?<![\w/])((?:skill|src|tests|examples|docs|\.github|\.dsh)/[A-Za-z0-9_./-]+)")
    for name in ("README.md", "README.zh-CN.md"):
        p = os.path.join(repo, name)
        if not os.path.exists(p):
            continue
        for i, line in enumerate(read(p).splitlines(), 1):
            for m in pat.finditer(line):
                rel = m.group(1).rstrip(".,;:)`")
                if rel.endswith("...") or "..." in rel:
                    continue
                if not os.path.exists(os.path.join(repo, rel.replace("/", os.sep))):
                    missing.append("%s:%d -> %s" % (name, i, rel))
    if missing:
        record("FAIL", "paths named in the README exist",
               "not on disk: %s" % "; ".join(sorted(set(missing))))
    else:
        record("PASS", "paths named in the README exist", "all referenced paths resolve")


def check_bilingual_agreement(repo: str) -> None:
    """The two READMEs must not contradict each other on the total tool count.

    Shares `claimed_tool_counts` on purpose: a second, hand-rolled regex here is how
    the subset-count false positive survived the first fix.
    """
    counts: dict[str, set[int]] = {}
    for name, _line, value in claimed_tool_counts(repo):
        counts.setdefault(name, set()).add(value)
    if len(counts) < 2:
        record("SKIP", "bilingual READMEs agree", "only one README present")
        return
    distinct = {frozenset(v) for v in counts.values() if v}
    if len(distinct) > 1:
        record("FAIL", "bilingual READMEs agree",
               "disagreement: " + "; ".join("%s claims %s" % (k, sorted(v)) for k, v in counts.items()))
    else:
        record("PASS", "bilingual READMEs agree",
               "same total claim %s in both" % sorted(distinct.pop()) if distinct else "no claims")


def check_readonly_count(repo: str) -> None:
    """The 'N read-only tools' claim must match the set the verifier actually exercises.

    Why this exists: nothing in the code binds these two facts together. The count
    appears as prose in the server description, in both READMEs, and implicitly as
    the call list in `tests/verify-tools-readonly.py` -- four places, zero links. Turn
    a read-only tool into a mutating one and every one of those prose claims silently
    becomes false, which is the exact drift class this file is built against.
    """
    verifier = os.path.join(repo, "tests", "verify-tools-readonly.py")
    if not os.path.exists(verifier):
        record("SKIP", "read-only count == the set the verifier exercises",
               "no tests/verify-tools-readonly.py")
        return
    exercised = set(re.findall(r"\b(tool_[a-z0-9_]+)\b", read(verifier)))

    claims: list[tuple[str, int, int]] = []
    for name in ("README.md", "README.zh-CN.md"):
        p = os.path.join(repo, name)
        if not os.path.exists(p):
            continue
        for i, line in enumerate(read(p).splitlines(), 1):
            for m in re.finditer(r"(\d+)\s+read-only tools?\b", line):
                claims.append((name, i, int(m.group(1))))
            # Deliberately NOT matched: the zh gate table's `| 只读 | 观测类 12 个工具 |`
            # row. That row states how many tools are in the *observation* category, which
            # is a subset label inside a gate-level table, not a claim about the read-only
            # total. Matching it produced a false positive on the self-test fixture (where
            # the two numbers differ on purpose) -- the same "subset counted as a total"
            # trap that bit `claimed_tool_counts`. Precision beats coverage here: a check
            # that fires on a correct repo gets disabled, and then it covers nothing.
    srv = os.path.join(repo, "src", "dsh_cua", "server.py")
    if os.path.exists(srv):
        for m in re.finditer(r"(\d+)\s+read-only tools?\b", read(srv)):
            claims.append(("src/dsh_cua/server.py", 0, int(m.group(1))))

    if not claims:
        record("SKIP", "read-only count == the set the verifier exercises",
               "no read-only count claim found to check")
        return
    wrong = [(f, ln, c) for f, ln, c in claims if c != len(exercised)]
    if wrong:
        record("FAIL", "read-only count == the set the verifier exercises",
               "the verifier exercises %d; docs claim %s" % (
                   len(exercised),
                   "; ".join(("%s:%d says %d" % (f, ln, c)) if ln else ("%s says %d" % (f, c))
                             for f, ln, c in wrong)))
    else:
        record("PASS", "read-only count == the set the verifier exercises",
               "%d read-only tools, claimed consistently in %d places" % (len(exercised), len(claims)))


CHECKS = [
    check_version_consistency,
    check_server_json_description,
    check_tool_count,
    check_hard_gate_claim,
    check_readonly_count,
    check_advertised_tools_exist,
    check_uncalled_tools,
    check_readme_local_paths,
    check_bilingual_agreement,
]


# --------------------------------------------------------------------------- #
# self-test: build a fixture repo that is wrong in known ways
# --------------------------------------------------------------------------- #
BAD_PYPROJECT = 'version = "0.9.9"\n'
BAD_INIT = '__version__ = "0.4.0"\n'
BAD_SERVER = json.dumps({"version": "0.4.0", "description": "x" * 239}, indent=2)
BAD_README = (
    "A stdio MCP server exposing 7 tools. Every tool name is prefixed `tool_`.\n"
    "* **Physical input** (hard gate): exactly five things share your cursor.\n"
    "It exposes 5 read-only tools you can call at any time.\n"
    "Install with `uvx dsh-cua`.\n"
    "See `tests/does-not-exist.py` and `skill/computer-use/SKILL.md`.\n"
    "`tool_ghost_tool` is documented here too.\n"
)
BAD_SRC = (
    "@mcp.tool(description='a')\ndef tool_alpha() -> None: ...\n"
    "@mcp.tool(description='b')\ndef tool_beta() -> None: ...\n"
)


def build_fixture(root: str) -> None:
    os.makedirs(os.path.join(root, "src", "dsh_cua"), exist_ok=True)
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    os.makedirs(os.path.join(root, "skill", "computer-use"), exist_ok=True)
    open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8").write(BAD_PYPROJECT)
    open(os.path.join(root, "src", "dsh_cua", "__init__.py"), "w", encoding="utf-8").write(BAD_INIT)
    open(os.path.join(root, "src", "dsh_cua", "server.py"), "w", encoding="utf-8").write(BAD_SRC)
    open(os.path.join(root, "src", "dsh_cua", "bridge.py"), "w", encoding="utf-8").write(
        "gate = arbiter.admit_mutating(hard=True)\n")
    open(os.path.join(root, "server.json"), "w", encoding="utf-8").write(BAD_SERVER)
    open(os.path.join(root, "README.md"), "w", encoding="utf-8").write(BAD_README)
    open(os.path.join(root, "skill", "computer-use", "SKILL.md"), "w", encoding="utf-8").write("ok\n")
    open(os.path.join(root, "tests", "someone.py"), "w", encoding="utf-8").write(
        'payload = {"name": "tool_alpha"}\n')
    # Says nothing about read-only tools -> the read-only count check must SKIP,
    # not fail. (A checker that fires on absence is a checker that gets disabled.)
    open(os.path.join(root, "tests", "verify-tools-readonly.py"), "w", encoding="utf-8").write(
        'r = server.call("tool_alpha", {})\n')


GOOD_PYPROJECT = 'version = "1.0.0"\n'
GOOD_INIT = '__version__ = "1.0.0"\n'
GOOD_SERVER = json.dumps({"version": "1.0.0", "description": "a tool" * 10}, indent=2)
GOOD_README = (
    "A stdio MCP server exposing 2 tools. Every tool name is prefixed `tool_`.\n"
    "`tool_alpha` reads; `tool_beta` writes. 2 read-only tools are safe any time.\n"
    "* **Physical input** (hard gate): exactly one thing shares your cursor.\n"
    "| Tool count | 2 | 59 | 15 | 6 |\n"
    "See `tests/one.py` and `skill/computer-use/SKILL.md`.\n"
)
GOOD_README_ZH = (
    "一个 stdio MCP 服务器，暴露 2 个工具。工具名一律带 `tool_` 前缀。\n"
    "| 工具数 | 2 | 59 | 15 | 6 |\n"
    "| 只读 | 观测类 12 个工具 | 不进门 |\n"     # precision trap: a SUBSET count
)
GOOD_SRC = (
    "@mcp.tool(description='a')\ndef tool_alpha() -> None: ...\n"
    "@mcp.tool(description='b')\ndef tool_beta() -> None: ...\n"
)


def build_good_fixture(root: str) -> None:
    """A consistent repo. The checker must stay silent on it.

    Two precision traps are deliberate:
      * `arbiter.py`'s docstring writes `hard=True` while the only CALL SITE is one.
      * the zh README says `观测类 12 个工具`, a subset count, not a total.
    """
    os.makedirs(os.path.join(root, "src", "dsh_cua"), exist_ok=True)
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    os.makedirs(os.path.join(root, "skill", "computer-use"), exist_ok=True)
    open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8").write(GOOD_PYPROJECT)
    open(os.path.join(root, "src", "dsh_cua", "__init__.py"), "w", encoding="utf-8").write(GOOD_INIT)
    open(os.path.join(root, "src", "dsh_cua", "server.py"), "w", encoding="utf-8").write(GOOD_SRC)
    open(os.path.join(root, "src", "dsh_cua", "arbiter.py"), "w", encoding="utf-8").write(
        '"""hard=True  -> mutex + human-contention yield (unused in this fixture)."""\n')
    open(os.path.join(root, "src", "dsh_cua", "bridge.py"), "w", encoding="utf-8").write(
        "gate = arbiter.admit_mutating(hard=True)\n")
    open(os.path.join(root, "server.json"), "w", encoding="utf-8").write(GOOD_SERVER)
    open(os.path.join(root, "README.md"), "w", encoding="utf-8").write(GOOD_README)
    open(os.path.join(root, "README.zh-CN.md"), "w", encoding="utf-8").write(GOOD_README_ZH)
    open(os.path.join(root, "skill", "computer-use", "SKILL.md"), "w", encoding="utf-8").write("ok\n")
    open(os.path.join(root, "tests", "one.py"), "w", encoding="utf-8").write(
        'p = {"name": "tool_alpha"}\nr = server.call("tool_beta", {})\n')
    open(os.path.join(root, "tests", "verify-tools-readonly.py"), "w", encoding="utf-8").write(
        'server.call("tool_alpha", {})\nserver.call("tool_beta", {})\n')


def run_self_test() -> int:
    print("== self-test: a checker nobody has seen fail is a checker that may not work ==\n")
    tmp = tempfile.mkdtemp(prefix="docs-consistency-selftest-")
    rc = 0
    try:
        # ---- direction 1: a known-bad repo must FIRE ----
        bad = os.path.join(tmp, "bad")
        build_fixture(bad)
        results.clear()
        for fn in CHECKS:
            fn(bad)
        expected_failures = {
            "version consistency",                     # 0.9.9 vs 0.4.0
            "server.json description length",           # 239 > 100
            "advertised tool count == registered",      # claims 7, code registers 2
            "hard-gate claim == hard=True sites",       # claims five, code has 1
            "read-only count == the set the verifier exercises",  # claims 5, verifier calls 1
            "every advertised tool name exists",        # tool_ghost_tool
            "advertised tools are exercised by tests",  # tool_beta never called
            "paths named in the README exist",          # tests/does-not-exist.py
        }
        got = {name for status, name, _ in results if status == "FAIL"}
        missing = expected_failures - got
        unexpected = got - expected_failures
        print("-- direction 1: known-bad fixture (every check must fire) --")
        for status, name, detail in results:
            print("  [%-4s] %s%s" % (status, name, (" — " + detail) if detail else ""))
        print()
        if missing:
            print("SELF-TEST FAILED: did not fire on a known-bad repo: %s" % ", ".join(sorted(missing)))
            rc = 1
        if unexpected:
            print("SELF-TEST FAILED: fired unexpectedly: %s" % ", ".join(sorted(unexpected)))
            rc = 1

        # ---- direction 2: a consistent repo must stay SILENT ----
        good = os.path.join(tmp, "good")
        build_good_fixture(good)
        results.clear()
        for fn in CHECKS:
            fn(good)
        noisy = [(s, n, d) for s, n, d in results if s in ("FAIL", "WARN")]
        print("-- direction 2: consistent fixture (nothing may fire) --")
        for status, name, detail in results:
            print("  [%-4s] %s%s" % (status, name, (" — " + detail) if detail else ""))
        print()
        if noisy:
            print("SELF-TEST FAILED: false positives on a consistent repo:")
            for _s, n, d in noisy:
                print("  %s — %s" % (n, d))
            rc = 1

        # ---- direction 3: SKIP must be visible, and an empty run must not be green ----
        # The branches that decide "green but meaningless" cannot be reached by running
        # against a real repo, so they are driven directly.  Untested, they are exactly
        # the kind of code that silently stops working -- which is the whole subject of
        # this file, one level up.
        print("-- direction 3: SKIP visibility and the empty-run guard --")
        global ANNOTATE
        saved_mode = ANNOTATE
        saved_ci = os.environ.get("GITHUB_ACTIONS")
        problems = []
        try:
            # `auto` is the mode that actually runs in CI, so it is the one that matters
            # most -- and the first version of this self-test did not exercise it at all,
            # which mutation testing caught: inverting the environment test (so `auto`
            # never annotates, i.e. SKIP silently stays invisible) went undetected.
            def emit(mode):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    annotate("notice", "hello", title="t")
                return buf.getvalue()

            ANNOTATE = "always"
            if emit(ANNOTATE).strip() != "::notice title=t::hello":
                problems.append("always: annotation not emitted (got %r)" % emit(ANNOTATE))

            ANNOTATE = "never"
            if emit(ANNOTATE):
                problems.append("never: still wrote %r" % emit(ANNOTATE))

            ANNOTATE = "auto"
            os.environ.pop("GITHUB_ACTIONS", None)
            if emit(ANNOTATE):
                problems.append("auto outside Actions: wrote %r (should be silent)" % emit(ANNOTATE))
            os.environ["GITHUB_ACTIONS"] = "true"
            if emit(ANNOTATE).strip() != "::notice title=t::hello":
                problems.append("auto under Actions: did not emit (got %r) -- SKIP would be "
                                "invisible in CI, which is the whole point of this check"
                                % emit(ANNOTATE))
            ANNOTATE = "always"

            results.clear()
            for _ in range(3):
                record("SKIP", "no browser here", "nothing to compare")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_all_skip = verdict()
            if rc_all_skip == 0:
                problems.append("a run where every check skipped returned 0; it must be red")
            if "::error" not in buf.getvalue():
                problems.append("an all-SKIP run did not annotate an error")

            results.clear()
            record("PASS", "a real check", "")
            record("SKIP", "bilingual READMEs agree", "only one README present")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_partial = verdict()
            out = buf.getvalue()
            if rc_partial != 0:
                problems.append("partial coverage returned %d; it must be 0" % rc_partial)
            # Assert on the ANNOTATION LINE, not on stdout as a whole: the human-readable
            # summary also names the skipped check, so a substring test over the whole
            # output passes even when the annotation itself has been emptied out
            # (mutation testing caught exactly that).
            notices = [ln for ln in out.splitlines() if ln.startswith("::notice")]
            if len(notices) != 1:
                problems.append("expected exactly 1 notice, got %d: %r" % (len(notices), notices))
            else:
                n = notices[0]
                if "title=" not in n.split("::")[1]:
                    problems.append("notice carries no title: %r" % n)
                if "bilingual READMEs agree" not in n:
                    problems.append("notice does not say WHICH check skipped: %r" % n)
                if "1 of 2" not in n:
                    problems.append("notice does not say how many skipped: %r" % n)
        finally:
            ANNOTATE = saved_mode
            if saved_ci is None:
                os.environ.pop("GITHUB_ACTIONS", None)
            else:
                os.environ["GITHUB_ACTIONS"] = saved_ci
            results.clear()
        for p in problems:
            print("  SELF-TEST FAILED: %s" % p)
        if problems:
            rc = 1
        else:
            print("  the annotation channel emits, is suppressed on demand, and an "
                  "all-SKIP run is red.\n")

        # ---- direction 4: every data source must be individually load-bearing ----
        # REPRODUCED ESCAPE.  Found by an independent auditor on 2026-09-28 and reproduced here
        # before fixing: the bad fixture carries TWO independent version disagreements, so
        # disabling the `server.json` read entirely still left a disagreement -> direction 1
        # still fired -> `--self-test` still printed PASSED.  On a repo where ONLY server.json
        # had drifted, the mutated checker reported "all 9 checks passed" while the original
        # reported `[FAIL] version consistency`.  A fixture with REDUNDANT disagreement cannot
        # notice a data source going dark, and no amount of mutation testing against THAT
        # fixture will find it -- which is why the author's own "8/8 mutants caught" was not
        # evidence of sensitivity.  So: drift exactly one source per fixture.
        print("-- direction 4: each version source must be individually load-bearing --")
        drift_cases = [
            ("pyproject.toml", 'version = "1.0.0"'),
            (os.path.join("src", "dsh_cua", "__init__.py"), '__version__ = "1.0.0"'),
            ("server.json", '"version": "1.0.0"'),
        ]
        blind = []
        for rel, needle in drift_cases:
            fx = os.path.join(tmp, "drift_" + rel.replace(os.sep, "_").replace(".", ""))
            build_good_fixture(fx)
            fp = os.path.join(fx, rel)
            src_text = open(fp, encoding="utf-8").read()
            if needle not in src_text:
                blind.append("%s: needle %r not in the fixture (fixture changed?)" % (rel, needle))
                continue
            open(fp, "w", encoding="utf-8").write(
                src_text.replace(needle, needle.replace("1.0.0", "1.0.1"), 1))
            results.clear()
            check_version_consistency(fx)
            if not [s for s, _, _ in results if s == "FAIL"]:
                blind.append("%s drifted ALONE -> the check stayed silent" % rel)
            else:
                print("  %-34s drifted alone -> FAIL (correct)" % rel)

        # A check that stopped being called at all shrinks `results` instead of failing, so the
        # count is asserted too -- otherwise deleting a check from CHECKS is invisible.
        results.clear()
        for fn in CHECKS:
            fn(good)
        if len(results) != len(CHECKS):
            blind.append("%d of %d checks reported; a check did not run at all"
                         % (len(results), len(CHECKS)))
        results.clear()

        for b in blind:
            print("  SELF-TEST FAILED: %s" % b)
        if blind:
            rc = 1
        else:
            print("  all %d version sources are load-bearing on their own, and all %d checks ran.\n"
                  % (len(drift_cases), len(CHECKS)))

        # ---- direction 5: replay the mutant corpus (CI recomputes the claim) ----
        # tests/mutants.json is the durable form of "the self-test catches real
        # regressions": each record is a string edit modeling a known failure class,
        # together with the verdict this self-test MUST produce.  Every mutant is
        # replayed against a copy of THIS file in a subprocess, and the caught set
        # must equal the set of `expect: caught` records, in BOTH directions: a
        # `caught` mutant that escapes means sensitivity regressed; an `escape`
        # mutant that gets caught means a blind spot closed -- update
        # tests/mutants.json (expect: "caught") so the record stays true.
        print("-- direction 5: mutant replay against this file's own --self-test --")
        mpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mutants.json")
        if os.environ.get("DSH_MUTANT_REPLAY") == "1":
            # A mutated copy must not replay the corpus again: one mutation at a
            # time, or the mutant tree explodes exponentially.
            print("  skipped: this file is itself the mutant under test (DSH_MUTANT_REPLAY=1)")
            print()
        elif not os.path.isfile(mpath):
            print("  SELF-TEST FAILED: mutant corpus not found: %s" % mpath)
            rc = 1
        else:
            with open(mpath, encoding="utf-8-sig") as fh:
                corpus = json.load(fh)
            src_me = read(os.path.abspath(__file__))
            env = dict(os.environ)
            env["DSH_MUTANT_REPLAY"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            outcomes: list[tuple[str, str, str]] = []
            for mu in corpus:
                mid, expect = str(mu.get("id", "?")), str(mu.get("expect", "?"))
                if mu.get("file") != "check-docs-consistency.py":
                    outcomes.append((mid, expect, "bad-target"))
                    print("  %-6s NO-RUN: unknown target file %r" % (mid, mu.get("file")))
                    continue
                n = src_me.count(mu["old"])
                if n != 1:
                    # Asserted here rather than trusted: a mutation that does not
                    # apply would otherwise look like a pass -- tested nothing.
                    outcomes.append((mid, expect, "no-op"))
                    print("  %-6s NO-RUN: 'old' occurs %d times in the checker (must be 1)"
                          % (mid, n))
                    continue
                sane = "".join(ch if ch.isalnum() else "_" for ch in mid)
                mf = os.path.join(tmp, "mutant_%s.py" % sane)
                open(mf, "w", encoding="utf-8").write(src_me.replace(mu["old"], mu["new"], 1))
                try:
                    proc = subprocess.run(
                        [sys.executable, mf, "--self-test"],
                        capture_output=True, text=True, timeout=120,
                        encoding="utf-8", errors="replace", env=env,
                    )
                    out = (proc.stdout or "") + (proc.stderr or "")
                    actual = "caught" if (proc.returncode != 0 or "SELF-TEST FAILED" in out) else "escape"
                except subprocess.TimeoutExpired:
                    actual = "caught"  # a mutated checker that hangs is caught, loudly
                outcomes.append((mid, expect, actual))
                if actual == expect:
                    print("  %-6s expect=%-6s actual=%-6s ok" % (mid, expect, actual))
                else:
                    print("  %-6s expect=%-6s actual=%-6s MISMATCH" % (mid, expect, actual))
                    if expect == "caught":
                        print("  SELF-TEST FAILED: %s escaped -- sensitivity regressed (%s)"
                              % (mid, mu.get("why", "")))
                    else:
                        print("  SELF-TEST FAILED: %s is now caught -- a blind spot closed;"
                              " update tests/mutants.json (expect: 'caught')" % mid)
            n_caught = sum(1 for _mid, _exp, a in outcomes if a == "caught")
            print()
            print("  corpus: %d mutants, %d caught, %d escaped"
                  % (len(outcomes), n_caught, len(outcomes) - n_caught))
            if any(e != a for _mid, e, a in outcomes):
                rc = 1
            else:
                print()

        if rc == 0:
            print("SELF-TEST PASSED: %d checks fired on the bad fixture, 0 fired on the good one,\n"
                  "                  the SKIP/empty-run reporting behaves as documented, and every\n"
                  "                  version source is individually load-bearing."
                  % len(expected_failures))
        return rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
def verdict() -> int:
    """Turn the accumulated `results` into an exit code, making each outcome visible.

    Split out of main() so --self-test can drive it directly: the "everything skipped"
    guard is the newest and least-obvious branch here, and an untested branch in the
    checker is the same problem as an untested check.
    """
    failed = skipped = 0
    for status, name, detail in results:
        print("[%-4s] %s%s" % (status, name, (" — " + detail) if detail else ""))
        if status == "FAIL":
            failed += 1
            annotate("error", detail or name, title="docs consistency: %s" % name)
        elif status == "SKIP":
            skipped += 1
    print()

    # A tree where nothing is checkable is not a tree that has been verified.  Without
    # this, a renamed server.json plus a moved tests/ turns the whole run into silent
    # SKIPs -- which is the drift this file exists to catch, wearing a green checkmark.
    substantive = len(results) - skipped
    if substantive == 0 and results:
        print("every check skipped — this run asserted nothing about the repo.")
        annotate("error", "all %d checks SKIPPED; the docs were not verified at all" % len(results),
                 title="docs consistency")
        return 1

    if failed:
        print("%d check(s) failed — the docs and the code disagree." % failed)
        return 1

    if skipped:
        names = ", ".join(n for s, n, _ in results if s == "SKIP")
        print("all %d checks passed, but %d SKIPPED and asserted nothing: %s"
              % (substantive, skipped, names))
        annotate("notice", "%d of %d checks SKIPPED and asserted nothing: %s"
                 % (skipped, len(results), names), title="docs consistency: partial coverage")
        return 0

    print("all %d checks passed." % len(results))
    return 0


def main() -> int:
    global ANNOTATE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None, help="repository root (default: two levels up from this file)")
    ap.add_argument("--self-test", action="store_true", help="run the negative control and exit")
    ap.add_argument("--annotate", choices=("auto", "always", "never"), default="auto",
                    help="emit GitHub Actions annotations: auto = only when GITHUB_ACTIONS=true (default)")
    args = ap.parse_args()
    ANNOTATE = args.annotate

    if args.self_test:
        return run_self_test()

    repo = args.repo or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if not os.path.isdir(repo):
        print("repo not found: %s" % repo)
        return 2
    print("checking docs against code in %s\n" % repo)
    for fn in CHECKS:
        try:
            fn(repo)
        except Exception as exc:  # noqa: BLE001
            record("FAIL", fn.__name__, "check raised %s: %s" % (type(exc).__name__, exc))

    return verdict()


if __name__ == "__main__":
    sys.exit(main())
