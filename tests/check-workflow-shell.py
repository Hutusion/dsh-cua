"""Fail fast when a workflow's shell block does not even parse.

Why this exists
    A PowerShell parse error makes a step fail in **0 seconds with no output and no
    annotation** — visually indistinguishable from "the test ran and failed". That is
    exactly what happened here: a `$(...)` subexpression containing literal words
    (`$($i annotation chunk(s) ...)`) is a parse error, the whole script never ran, and
    four CI runs were spent looking for a test failure that was never reached.

    So the workflow now validates its own shell blocks before executing any of them.
    The check is cheap (parse only, nothing is run) and it turns a silent 0-second
    failure into an explicit "your YAML contains a script that cannot parse" message.

Why the reporting is loud, and why the exit code is NOT the diagnosis
    Measured 2026-09-25 on PowerShell 7.6.6: GitHub runs a `shell: pwsh` step as
    `pwsh -command ". '<temp>.ps1'"`, and in that shape EVERY non-zero exit surfaces as 1
    — `exit 2`, `exit 3`, `cmd /c exit 5` and `python sys.exit(7)` all became 1, while the
    same script under `-File` kept its code. A red run therefore tells you nothing on its
    own, and this script cannot communicate through its exit status.

    So every failure path here (a) prints an actionable message, (b) emits a `::error`
    check annotation, readable through the public check-runs API with no token, and
    (c) appends the same text to $GITHUB_STEP_SUMMARY, which the public run page renders.
    Run #5 died on the runner with "PyYAML missing" printed into a job log nobody could
    read (the log endpoint is 403 unauthenticated); that is the failure mode this
    reporting exists to prevent. The success path emits a `::notice` instead — an
    `::error` there would fail a green step.

What it checks
    Every `run:` block in `.github/workflows/*.yml`, parsed as the shell the step
    declares (`shell: pwsh`, or the runner default on Windows, which is pwsh). Blocks
    using another shell are reported as skipped rather than silently passed.

Exit codes (local use only — CI normalizes them to 1, see above)
    0  every block parses
    1  at least one block does not parse
    2  the check could not run at all (PyYAML or pwsh missing, no workflow files)

usage:  python tests/check-workflow-shell.py [workflow.yml ...]
        (defaults to every .github/workflows/*.yml)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")

# A PowerShell script that parses a file and exits non-zero on any syntax error.
#
# The first line pins stdout/stderr to UTF-8. Measured on a zh-CN machine: when pwsh's
# output is captured by a pipe it uses the ANSI code page (GBK here), so the parser's own
# error messages — the very text this tool exists to report — arrived as mojibake. The
# runner's messages are English, but a garbled diagnosis is not something to ship.
PARSER = r"""
param([Parameter(Mandatory=$true)][string]$Path)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$errors)
if ($errors -and $errors.Count -gt 0) {
  foreach ($e in $errors) {
    Write-Output ("{0}:{1}: {2}" -f $e.Extent.StartLineNumber, $e.Extent.StartColumnNumber, $e.Message)
  }
  exit 1
}
exit 0
"""


def _gha_escape(text: str) -> str:
    """Escape a workflow-command message (GitHub unescapes %0A/%0D/%25)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _annotate(kind: str, title: str, message: str) -> None:
    """Emit a check annotation when running under GitHub Actions.

    Annotations are readable through the public check-runs API, which is the only
    diagnostic channel that needs no token.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{kind} title={title}::{_gha_escape(message)}")


def _summary(lines: list[str]) -> None:
    """Append to the job summary when running under GitHub Actions.

    The job-log endpoint is 403 unauthenticated; the job summary is rendered on the
    public run page, so this is how a CI-only failure stays readable from outside.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:                       # never let reporting mask the result
        print(f"(could not write the job summary: {exc!r})")


def cannot_run(reason: str, fix: str) -> int:
    """Report a check that could not run at all, with the fix, and return exit 2."""
    text = f"cannot run the workflow shell check: {reason}\nfix: {fix}"
    print(text)
    _annotate("error", "check-workflow-shell: cannot run", text)
    _summary(["### check-workflow-shell could not run", "",
              f"- reason: `{reason}`", f"- fix: {fix}"])
    return 2


def workflow_files(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a) if Path(a).is_absolute() else REPO / a for a in argv]
    found: list[Path] = []
    for pattern in DEFAULT_GLOBS:
        found.extend(sorted(REPO.glob(pattern)))
    return found


def iter_steps(doc: dict):
    for job_name, job in (doc.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            if "run" in step:
                yield job_name, index, step


def main() -> int:
    try:
        import yaml
    except ImportError:
        return cannot_run(
            "PyYAML is not installed",
            'install the declared test extra: python -m pip install ".[test]" '
            "(or: python -m pip install pyyaml). "
            "CI must install `.[test]`, not `.` — see pyproject.toml.",
        )

    files = workflow_files(sys.argv[1:])
    if not files:
        return cannot_run(
            "no workflow files found",
            f"run from the repository root (looked for {', '.join(DEFAULT_GLOBS)} "
            f"under {REPO})",
        )

    failures: list[tuple[str, str]] = []
    checked = skipped = 0

    with tempfile.TemporaryDirectory(prefix="wf-shell-") as tmpdir:
        tmp = Path(tmpdir)
        parser = tmp / "parse.ps1"
        parser.write_text(PARSER, encoding="utf-8")

        for path in files:
            rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for job_name, index, step in iter_steps(doc):
                shell = str(step.get("shell") or "pwsh").lower()
                label = f"{rel}:{job_name}[{index}] {step.get('name', '(unnamed step)')}"
                if shell not in ("pwsh", "powershell"):
                    print(f"  skip  {label}  (shell={shell})")
                    skipped += 1
                    continue

                script = tmp / f"step-{checked}.ps1"
                script.write_text(str(step["run"]), encoding="utf-8")
                try:
                    proc = subprocess.run(
                        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(parser), str(script)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=120,
                    )
                except FileNotFoundError:
                    return cannot_run(
                        "pwsh was not found on PATH",
                        "install PowerShell 7 (the runner image has it; a stripped local "
                        "environment may not), then re-run this check",
                    )
                except subprocess.TimeoutExpired:
                    failures.append((label, "pwsh did not finish parsing within 120s"))
                    print(f"  FAIL  {label}  (timeout)")
                    checked += 1
                    continue

                if proc.returncode == 0:
                    print(f"  OK    {label}")
                else:
                    detail = (proc.stdout or proc.stderr or "").strip()
                    print(f"  FAIL  {label}")
                    for line in detail.splitlines():
                        print(f"          {line}")
                    failures.append((label, detail or "(no parser output)"))
                checked += 1

    print(f"\nchecked {checked} shell block(s), skipped {skipped}, failed {len(failures)}")

    if failures:
        text = (f"{len(failures)} of {checked} workflow shell block(s) do not parse. A step "
                "like this fails in 0s with no output, which looks exactly like a test "
                "failure — fix the script before pushing.")
        print(text)
        _annotate("error", "check-workflow-shell",
                  text + " " + "; ".join(lbl for lbl, _ in failures))
        report = ["### check-workflow-shell: FAIL", "", text, ""]
        for label, detail in failures:
            _annotate("error", f"check-workflow-shell: {label}", detail)
            report += [f"**{label}**", "", "```text", detail, "```", ""]
        _summary(report)
        return 1

    _annotate("notice", "check-workflow-shell",
              f"{checked} shell block(s) parse; {skipped} skipped (non-pwsh shell)")
    _summary([f"### check-workflow-shell: OK", "",
              f"{checked} shell block(s) parse, {skipped} skipped (non-pwsh shell)."])
    return 0


if __name__ == "__main__":
    sys.exit(main())
