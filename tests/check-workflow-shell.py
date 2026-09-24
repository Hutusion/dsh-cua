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

What it checks
    Every `run:` block in `.github/workflows/*.yml`, parsed as the shell the step
    declares (`shell: pwsh`, or the runner default on Windows, which is pwsh). Blocks
    using another shell are reported as skipped rather than silently passed.

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
PARSER = r"""
param([Parameter(Mandatory=$true)][string]$Path)
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
        print("PyYAML missing; cannot parse workflows")
        return 2

    files = workflow_files(sys.argv[1:])
    if not files:
        print("no workflow files found")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="wf-shell-"))
    parser = tmp / "parse.ps1"
    parser.write_text(PARSER, encoding="utf-8")

    failures: list[str] = []
    checked = skipped = 0

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
            proc = subprocess.run(
                ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(parser), str(script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            if proc.returncode == 0:
                print(f"  OK    {label}")
            else:
                detail = (proc.stdout or proc.stderr or "").strip()
                print(f"  FAIL  {label}")
                for line in detail.splitlines():
                    print(f"          {line}")
                failures.append(label)
            checked += 1

    print(f"\nchecked {checked} shell block(s), skipped {skipped}, failed {len(failures)}")
    if failures:
        print("A workflow shell block does not parse. It would fail in 0s with no output —")
        print("which looks exactly like a test failure. Fix the script before pushing.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
