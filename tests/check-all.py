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
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.basename(os.path.abspath(__file__))
SELF_TEST_HINT = "--self-test"
DEPTH_VAR = "DSH_CUA_CHECK_ALL_DEPTH"
TIMEOUT_S = 120


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


def run(script: str, args: list[str]) -> int:
    label = " ".join([script] + args)
    print("\n=== %s ===" % label, flush=True)
    env = dict(os.environ)
    env[DEPTH_VAR] = env.get(DEPTH_VAR, "0")
    try:
        return subprocess.run(
            [sys.executable, os.path.join(HERE, script), *args],
            cwd=os.path.dirname(HERE),
            env=env,
            timeout=TIMEOUT_S,
        ).returncode
    except subprocess.TimeoutExpired:
        print("!!! %s did not finish within %d s and was killed." % (label, TIMEOUT_S))
        return 1


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
