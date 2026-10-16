# SPDX-License-Identifier: MIT
import argparse
import json
import sys

from .core import (ContentError, InputLimitError, QuestionBudget, ROOT, SetupError,
                   check_question_count, load_json, validate_map, validate_question)
from .runner import TrustedLocalRunner, verify_question


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SetupError(message)


def main(argv=None, *, runner=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_output = any(argv[i:i + 2] == ["--format", "json"] or argv[i] == "--format=json"
                      for i in range(len(argv)))
    report = {"valid": False, "execution": "not-requested", "questions": 0,
              "issues": [], "runner": None}
    exit_code = 0

    def add(code, path, message, file=""):
        report["issues"].append({"code": code, "path": path, "message": message, "file": str(file)})

    parser = Parser(description="Statically validate questions. Execution never proves editorial correctness.")
    parser.add_argument("--concept-map", default=str(ROOT / "schema/concepts/python-v0.json"))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--runner", choices=("trusted-local",))
    parser.add_argument("--allow-trusted-code", action="store_true",
                        help="Acknowledge trusted code only: NOT a sandbox; host/network access possible")
    parser.add_argument("files", nargs="+")
    try:
        args = parser.parse_args(argv)
        json_output = args.format == "json"
        if args.verify:
            report["execution"] = "unavailable"
        check_question_count(len(args.files), args.files[0])
        budget = QuestionBudget()
        if args.verify:
            if args.runner != "trusted-local" or not args.allow_trusted_code:
                add("runner-unavailable", "", "Verification requires --runner trusted-local --allow-trusted-code; never use for untrusted contributions")
                exit_code = 2
            else:
                runner = runner if runner is not None else TrustedLocalRunner(allow_trusted_code=True)
                reason = runner.unavailable_reason() if hasattr(runner, "unavailable_reason") else None
                if reason:
                    add("runner-unavailable", "", reason)
                    exit_code = 2
                else:
                    print("WARNING: trusted code only. No network or host filesystem isolation; NEVER run untrusted contributions.", file=sys.stderr)
                    report["runner"] = {"name": getattr(runner, "name", "injected"),
                                        "python_version": sys.version.split()[0],
                                        "isolation": getattr(runner, "isolation", "unspecified")}
        map_data = load_json(args.concept_map)
        map_issues = validate_map(map_data)
        for issue in map_issues:
            add(issue.code, issue.path, issue.message, args.concept_map)
        if map_issues:
            exit_code = max(exit_code, 1)
        loaded, ids = [], set()
        for filename in args.files:
            try:
                question = load_json(filename, budget=budget)
                report["questions"] += 1
                issues = [] if map_issues else validate_question(question, map_data)
                for issue in issues:
                    add(issue.code, issue.path, issue.message, filename)
                if issues:
                    exit_code = max(exit_code, 1)
                elif not map_issues:
                    if question["id"] in ids:
                        add("duplicate-question", "/id", f"Duplicate question ID: {question['id']}", filename)
                        exit_code = max(exit_code, 1)
                    ids.add(question["id"])
                    loaded.append((filename, question))
            except InputLimitError as exc:
                add("input-limit", "", str(exc), exc.file)
                exit_code = max(exit_code, 1)
                if budget.exhausted:
                    break
            except ContentError as exc:
                add("json", "", str(exc), filename)
                exit_code = max(exit_code, 1)
            except SetupError as exc:
                add("setup", "", str(exc), filename)
                exit_code = 2
        # Global gate: no snippet runs if any input has static errors.
        if args.verify and exit_code == 0:
            report["execution"] = "passed"
            for filename, question in loaded:
                try:
                    issues, infrastructure = verify_question(question, runner)
                except Exception as exc:
                    add("runner-error", "", f"Runner failed: {exc}", filename)
                    report["execution"] = "unavailable"
                    exit_code = 2
                    break
                for issue in issues:
                    add(issue.code, issue.path, issue.message, filename)
                if issues:
                    if report["execution"] != "unavailable":
                        report["execution"] = "unavailable" if infrastructure else "failed"
                    exit_code = max(exit_code, 2 if infrastructure else 1)
    except InputLimitError as exc:
        add("input-limit", "", str(exc), exc.file)
        exit_code = max(exit_code, 1)
    except ContentError as exc:
        add("json", "", str(exc))
        exit_code = max(exit_code, 1)
    except SetupError as exc:
        add("setup", "", str(exc))
        exit_code = 2
    report["valid"] = exit_code == 0
    report["issues"].sort(key=lambda i: (i["file"], i["path"], i["code"], i["message"]))
    if json_output:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    else:
        print(f"{'VALID' if report['valid'] else 'INVALID'}: {report['questions']} question(s); execution: {report['execution']}")
        if report["execution"] == "not-requested":
            print("Execution skipped (static checks only).")
        elif report["execution"] == "unavailable":
            print("Execution unavailable or skipped; no execution approval.")
        for issue in report["issues"]:
            print(f"{issue['file']}{issue['path']}: {issue['code']}: {issue['message']}")
        print("Validation does not imply human review or publication approval.")
    return exit_code
