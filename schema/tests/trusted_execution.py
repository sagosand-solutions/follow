"""Trusted-only local execution helper for question fixtures.

This module is intentionally NOT named ``test_*.py`` so that
``python3 -m unittest discover -s schema/tests`` never imports or runs it. The
default suite is fully static. Run this helper explicitly, only after a human has
inspected the code:

    python3 schema/tests/trusted_execution.py [QUESTION_JSON ...]

With no arguments it runs ``content/examples/independent-if.json``.

The helper runs each declared verification case on the local interpreter with a
timeout and isolated mode, then compares stdout and the exit code. It is
trusted-only: it does NOT isolate the network or the filesystem and is not a
sandbox. Untrusted content must go through the separate validator runner.
"""

import json
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 10
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTION = REPO_ROOT / "content" / "examples" / "independent-if.json"


def run_case(code, case):
    expected = case["expected"]
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code],
            input=case["stdin"].encode("utf-8"),
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [f"timed out after {TIMEOUT_SECONDS}s"]

    problems = []
    if completed.returncode != expected["exit_code"]:
        problems.append(
            f"exit code {completed.returncode} != expected {expected['exit_code']}"
        )
    if completed.stdout != expected["stdout"].encode("utf-8"):
        problems.append(f"stdout {completed.stdout!r} != expected {expected['stdout']!r}")
    if "stderr_contains" in expected:
        if expected["stderr_contains"].encode("utf-8") not in completed.stderr:
            problems.append(
                f"stderr missing {expected['stderr_contains']!r}: {completed.stderr!r}"
            )
    elif expected["exit_code"] == 0 and completed.stderr != b"":
        problems.append(f"stderr not empty: {completed.stderr!r}")
    return problems


def run_question(path):
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as handle:
        question = json.load(handle)
    snippets = {snippet["id"]: snippet["code"] for snippet in question["snippets"]}
    problems = 0
    for case in question["verification"]["cases"]:
        issues = run_case(snippets[case["snippet_id"]], case)
        print(f"[{'ok' if not issues else 'FAIL'}] {path.name} :: {case['id']}")
        for issue in issues:
            print(f"    {issue}")
            problems += 1
    return problems


def main(argv):
    targets = argv or [str(DEFAULT_QUESTION)]
    print(f"interpreter: {sys.version.split()[0]} ({sys.executable})")
    print("trusted-only: no network/filesystem isolation; inspected code only")
    failures = sum(run_question(target) for target in targets)
    if failures:
        print(f"{failures} problem(s)")
        return 1
    print("all declared cases match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
