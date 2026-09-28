#!/usr/bin/env python3
"""Run every desktop-free checker in this repo, then each checker's own `--self-test`.

`check-*.py` are the desktop-free assertions and `ci-desktop-free.py` is the desktop-free
main suite.  The `verify-*.py` scripts in the same directory need a live interactive
desktop -- they synthesise real input and move the cursor -- so they are deliberately NOT
run here; they are run by hand on a machine with a desktop.

Discovery is by filename rather than a hardcoded list, so a newly added checker cannot be
forgotten.  That property caused a real defect the first time it was written, and the two
guards below exist because of it:

  * This file is itself named `check-*.py`, so it discovered *itself* and re-ran itself,
    which re-ran itself -- an unbounded chain of Python interpreters, each blocking on the
    next, until the whole thing had to be killed by hand.  Hence SELF is excluded.
  * `DSH_CUA_CHECK_ALL_DEPTH` is set for children.  If it is already set, something invoked
    this script from inside its own run, and this exits instead of spawning another level.
  * Each run has a timeout.  A pre-commit hook that hangs forever is worse than one that
    fails: it blocks the commit with no diagnosis.  Measured: every checker here finishes in
    under 5 s (check-workflow-shell.py is the slow one at ~4 s, because it runs pwsh).

Each checker that advertises `--self-test` gets that run too.  The premise of those
self-tests is that a checker nobody has seen fail may not work; a self-test that is never
run inherits exactly that problem.

Child output is streamed, not captured, so nothing here depends on being able to open a
pipe to a child process.

Run:  python tests/check-all.py
Exit: 0 when every checker and every self-test passed, 1 otherwise.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.basename(os.path.abspath(__file__))
SELF_TEST_HINT = "--self-test"
DEPTH_VAR = "DSH_CUA_CHECK_ALL_DEPTH"
TIMEOUT_S = 120

# A plain (non-self-test) run that PRINTS one of these yet exits 0 is contradicting itself.
# Deliberately NOT applied to `--self-test`: its direction 1 runs a known-bad fixture and
# therefore prints [FAIL] lines ON PURPOSE (measured 2026-09-28: 9 of them in a passing run).
#
# The shapes below are MEASURED, not guessed.  A first version used `^\[FAIL\]` alone and so
# covered exactly ONE of the seven checkers -- the one mutants.json's m17 happens to target.
# The others print `  [FAIL]` (indented, via their `  [PASS/FAIL]` helper), `FAIL:`, `FAILED:`,
# or `  FAIL  `, and the synthetic probe that "verified" it printed at column 0, so it could
# never have revealed the gap (the same fixture-covers-only-its-own-shape error as 2026-09-28's
# mutant corpus).  Measured marker sites, per checker:
#     [FAIL]      check-docs-consistency.py (column 0) · ci-desktop-free.py /
#                 check-foreground-null.py / check-handshake-classifier.py (indented)
#     FAIL:       check-ctypes-names.py · check-ctypes-prototypes.py
#     `  FAIL  `  check-workflow-shell.py
#     FAILED:     check-handshake-classifier.py · ci-desktop-free.py
#     Traceback   any crash.  ci-desktop-free.py prints a formatted traceback from
#                 sys.excepthook, which is exit-status-1 by construction, so it can never
#                 reach the rc==0 branch below.
# Safety of the broad form was measured BEFORE adoption: across all 7 discovered checkers on
# Python 3.12 AND 3.10, a passing plain run prints 0 of these markers.
FAILURE_MARKER = re.compile(r"^\s*\[FAIL\]|^\s*FAIL\b|^Traceback|\bFAILED\b", re.M)


def discover() -> list[str]:
    """Every desktop-free checker, by filename.  A new `check-*.py` is picked up for free.

    `SELF` must be excluded: this file matches its own pattern (measured -- see the module
    docstring), and running it from inside itself is an unbounded chain of processes.
    """
    names = sorted(
        n for n in os.listdir(HERE)
        if n.startswith("check-") and n.endswith(".py") and n != SELF
    )
    suite = "ci-desktop-free.py"
    if os.path.exists(os.path.join(HERE, suite)):
        names.append(suite)
    return names


def advertises_self_test(script: str) -> bool:
    with open(os.path.join(HERE, script), encoding="utf-8", errors="replace") as fh:
        return SELF_TEST_HINT in fh.read()


def echo(text: str, stream=None) -> None:
    """Print a child's output without ever letting a DISPLAY problem fail the run.

    A runner that dies while reporting a result is worse than the result it was reporting:
    the crash hides every check behind it.  So encoding errors degrade to '?', they never
    raise.  (See the PYTHONIOENCODING note in run() for the failure this guards against.)
    """
    out = stream if stream is not None else sys.stdout
    try:
        out.write(text)
    except UnicodeEncodeError:
        enc = getattr(out, "encoding", None) or "utf-8"
        out.write(text.encode(enc, "replace").decode(enc, "replace"))
    out.flush()


def run(script: str, args: list[str]) -> int:
    label = " ".join([script] + args)
    print("\n=== %s ===" % label, flush=True)
    env = dict(os.environ)
    env[DEPTH_VAR] = env.get(DEPTH_VAR, "0")
    # Pin BOTH ends of the pipe to UTF-8.  A child's stdout is a pipe here, so without this
    # Python picks the *console* code page -- GBK on this machine -- and writes an em dash
    # as \xa1\xaa.  Decoding those bytes as UTF-8 turns them into U+FFFD, and re-printing
    # U+FFFD to a GBK stdout raises UnicodeEncodeError: the RUNNER crashed with a traceback
    # instead of reporting a result (own regression, shipped in the same uncommitted change
    # that added capture_output; found by running it, not by reading it, 2026-09-28).
    # Pinning also makes local output byte-identical to CI, where this never showed up.
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, script), *args],
            cwd=os.path.dirname(HERE),
            env=env,
            timeout=TIMEOUT_S,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print("!!! %s did not finish within %d s and was killed." % (label, TIMEOUT_S))
        return 1

    # Stream it through unchanged, so the log still reads exactly as before.
    echo(proc.stdout or "")
    echo(proc.stderr or "", sys.stderr)

    # Silence is not a pass.  A checker whose entry point is dead exits 0 and prints NOTHING,
    # and that is UNDETECTABLE FROM INSIDE the checker: the code that would notice is only
    # reachable through the very function that was killed.  An independent audit found it by
    # mutating `main()` to `return 0` (2026-09-28); the self-test still "passed" with zero
    # output.  So the caller demands evidence that the checker ran at all.
    if not (proc.stdout or "").strip() and not (proc.stderr or "").strip():
        print("!!! %s exited %d but printed NOTHING -- treated as NOT RUN, not as a pass."
              % (label, proc.returncode))
        return 1

    # An exit code that CONTRADICTS what the checker printed is its own failure class -- the
    # one the mutant corpus (tests/mutants.json) records as `m17`: short-circuit verdict()'s
    # failed branch to `return 0` and every [FAIL] line still prints while the process reports
    # success, so CI and this runner both call it green.  The corpus documents that hole;
    # nothing closed it, for the same structural reason as `main()` above -- a checker cannot
    # police its own exit code any more than it can reach its own entry point.  So it is closed
    # here, in the caller.
    #
    # Scoped to PLAIN runs on purpose (see FAILURE_MARKER), and measured before being trusted:
    # all 7 discovered checkers print none of these markers on a passing plain run, while a
    # passing `--self-test` prints 9.
    if not args and proc.returncode == 0 and FAILURE_MARKER.search(proc.stdout or ""):
        print("!!! %s exited 0 but printed a failure marker -- the exit code contradicts the "
              "output on screen.  Treated as FAILED." % label)
        return 1
    return proc.returncode


def main() -> int:
    depth = os.environ.get(DEPTH_VAR)
    if depth is not None:
        print("refusing to run: %s is already set to %r, so this is a nested invocation "
              "of the runner itself." % (DEPTH_VAR, depth))
        return 1
    os.environ[DEPTH_VAR] = "0"

    # check-workflow-shell.py parses the workflow YAML and therefore needs PyYAML, which
    # pyproject declares as the `test` extra.  Without it that checker dies at import, and
    # the failure has been silent in this repo's history -- so it is asserted here rather
    # than discovered later.
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("PyYAML is missing, so check-workflow-shell.py cannot run at all.")
        print('Install the test extra first:   python -m pip install ".[test]"')
        return 1

    scripts = discover()
    print("check-all: %s" % sys.executable)
    print("           python %s, %s" % (platform.python_version(), platform.platform()))
    print("           %d desktop-free checker(s), plus every advertised --self-test" % len(scripts))

    results: list[tuple[str, int]] = []
    for script in scripts:
        results.append((script, run(script, [])))
    for script in scripts:
        if advertises_self_test(script):
            results.append((script + " " + SELF_TEST_HINT, run(script, [SELF_TEST_HINT])))

    print("\n--- summary ---")
    failed = 0
    for label, code in results:
        if code:
            failed += 1
        print("  [%s] %s" % ("ok  " if code == 0 else "FAIL", label))
    print()
    if failed:
        print("%d of %d runs failed." % (failed, len(results)))
        return 1
    print("all %d runs passed." % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
