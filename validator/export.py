# SPDX-License-Identifier: MIT
"""Deterministic static-only export of a validated course and question corpus."""
import json
import os
from pathlib import Path
import tempfile

from . import core
from .cli import Parser
from .core import (ContentError, InputLimitError, Issue, QuestionBudget, SetupError,
                   check_json_size, check_question_count, load_json, read_json_bytes,
                   validate_map, validate_question)

ROOT = core.ROOT
LEVELS = {"beginner": 0, "intermediate": 1, "advanced": 2}


def safe_path(root, relative):
    """Reject symlinks below root, including dangling links and linked parents."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise SetupError(f"{relative}: path must stay below the selected root")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise SetupError(f"{path}: symlinks are not supported for export inputs or outputs")
    return path


def _question_paths(directory):
    paths = []
    # Path.glob may collect an entire directory internally; scan incrementally.
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.endswith(".json"):
                paths.append(directory / entry.name)
                check_question_count(len(paths), directory)
    if not paths:
        raise SetupError(f"{directory}: no JSON questions found; refusing an empty export")
    return sorted(paths)


def validate_course(course, questions, *, schema_dir=None):
    """Validate course shape then cross-corpus consistency; never execute snippets."""
    issues = core._schema(course, "course.schema.json", schema_dir=schema_dir)
    if issues:
        return issues
    issues = core._blanks(course)
    lessons, locations, assigned = {}, {}, set()
    seen_levels, previous = set(), -1
    for i, level in enumerate(course["levels"]):
        path = f"/levels/{i}"
        name = level["level"]
        if name in seen_levels:
            issues.append(Issue("duplicate-level", path + "/level", f"Repeated level: {name}"))
        if LEVELS[name] <= previous:
            issues.append(Issue("level-order", path + "/level", "Levels must be strictly beginner, intermediate, advanced order"))
        seen_levels.add(name)
        previous = LEVELS[name]
        for j, lesson in enumerate(level["lessons"]):
            lesson_path = f"{path}/lessons/{j}"
            identifier = lesson["id"]
            if identifier in lessons:
                issues.append(Issue("duplicate-lesson", lesson_path + "/id", f"Repeated lesson ID: {identifier}"))
            lessons[identifier], locations[identifier] = lesson, lesson_path
            for k, qid in enumerate(lesson["questions"]):
                qpath = f"{lesson_path}/questions/{k}"
                if qid not in questions:
                    issues.append(Issue("unknown-question", qpath, f"Unknown question: {qid}"))
                elif questions[qid]["level"] != name:
                    issues.append(Issue("question-level", qpath, f"Question {qid} does not belong to level {name}"))
                if qid in assigned:
                    issues.append(Issue("duplicate-assignment", qpath, f"Question assigned more than once: {qid}"))
                assigned.add(qid)
    for identifier in sorted(lessons):
        for j, dependency in enumerate(lessons[identifier]["builds_on"]):
            path = f"{locations[identifier]}/builds_on/{j}"
            if dependency not in lessons:
                issues.append(Issue("unknown-lesson", path, f"Unknown lesson: {dependency}"))
            elif dependency == identifier:
                issues.append(Issue("self-edge", path, "Lesson cannot build on itself"))
    # Iterative traversal avoids recursion depth dependence on corpus size.
    colors = {}
    for start in sorted(lessons):
        stack = [(start, False)]
        while stack:
            identifier, leaving = stack.pop()
            if leaving:
                colors[identifier] = 2
                continue
            if colors.get(identifier) == 2:
                continue
            if colors.get(identifier) == 1:
                issues.append(Issue("lesson-cycle", locations[identifier], f"Lesson dependency cycle includes {identifier}"))
                continue
            colors[identifier] = 1
            stack.append((identifier, True))
            stack.extend((ref, False) for ref in reversed(lessons[identifier]["builds_on"]) if ref in lessons)
    for qid in sorted(set(questions) - assigned):
        issues.append(Issue("orphan-question", "/levels", f"Question has no lesson: {qid}"))
    onboarding = course["levels"][0]["lessons"][0]["questions"][0]
    if onboarding in questions and questions[onboarding]["level"] != "beginner":
        issues.append(Issue("onboarding-level", "/levels/0/lessons/0/questions/0", "First course question must be beginner"))
    return core._ordered(issues)


def serialize(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _write_outputs(root, outputs):
    staged = []
    try:
        # Stage every payload before replacing any output. Replacements are atomic
        # per file, not a cross-file filesystem transaction.
        for relative, payload in outputs.items():
            target = safe_path(root, relative)
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".export-", delete=False) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, relative))
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # NamedTemporaryFile starts private; served static data must be readable.
            temporary.chmod(0o644)
        for temporary, relative in staged:
            os.replace(temporary, safe_path(root, relative))
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = Parser(description="Static-only data export. No code execution or editorial approval.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Compare canonical generated bytes without writing")
    issues = []
    exit_code = 0

    def add(file, issue):
        issues.append((str(file), issue))

    try:
        args = parser.parse_args(argv)
        root = args.root.resolve()
        if not root.is_dir():
            raise SetupError(f"{root}: repository root is not a directory")
        schema_dir = safe_path(root, "schema")
        # Validate all required paths before opening any content or output.
        for name in ("question.schema.json", "concept-map.schema.json", "course.schema.json"):
            path = safe_path(root, Path("schema") / name)
            if not path.is_file():
                raise SetupError(f"{path}: required local schema is missing")
        map_path = safe_path(root, "schema/concepts/python-v0.json")
        course_path = safe_path(root, "public/data/courses/python.json")
        question_dir = safe_path(root, "public/data/questions")
        if not question_dir.is_dir():
            raise SetupError(f"{question_dir}: required question directory is missing")
        paths = _question_paths(question_dir)
        for path in paths:
            safe_path(root, path.relative_to(root))
        for name in ("index.json", "concepts.json"):
            safe_path(root, Path("public/data") / name)
        map_data = load_json(map_path)
        map_issues = validate_map(map_data, schema_dir=schema_dir)
        for issue in map_issues:
            add(map_path, issue)
        course = load_json(course_path)
        questions, identifiers = {}, set()
        budget = QuestionBudget()
        for path in paths:
            try:
                question = load_json(path, budget=budget)
                found = validate_question(question, map_data, schema_dir=schema_dir)
                for issue in found:
                    add(path, issue)
                if found:
                    continue
                qid = question["id"]
                if path.name != qid + ".json":
                    add(path, Issue("filename", "/id", f"Expected filename {qid}.json"))
                if qid in identifiers:
                    add(path, Issue("duplicate-question", "/id", f"Repeated question ID: {qid}"))
                identifiers.add(qid)
                questions[qid] = question
            except InputLimitError as exc:
                add(exc.file, Issue("input-limit", "", str(exc)))
                if budget.exhausted:
                    break
            except ContentError as exc:
                add(path, Issue("json", "", str(exc)))
            except SetupError as exc:
                add(path, Issue("setup", "", str(exc)))
                exit_code = 2
        # Avoid semantic cascades into malformed question/map structures.
        course_issues = (core._schema(course, "course.schema.json", schema_dir=schema_dir) if issues
                         else validate_course(course, questions, schema_dir=schema_dir))
        for issue in course_issues:
            add(course_path, issue)
        if issues:
            exit_code = max(exit_code, 1)
        else:
            onboarding = course["levels"][0]["lessons"][0]["questions"][0]
            fields = ("id", "type", "level", "difficulty", "concepts")
            index = {"data_version": 1, "onboarding_question": onboarding,
                     "questions": [{field: questions[qid][field] for field in fields} for qid in sorted(questions)]}
            outputs = {"public/data/index.json": serialize(index), "public/data/concepts.json": serialize(map_data)}
            for relative, payload in outputs.items():
                check_json_size(len(payload), safe_path(root, relative), context="generated JSON after formatting")
            if args.check:
                for relative, expected in outputs.items():
                    target = safe_path(root, relative)
                    if not target.exists():
                        add(target, Issue("stale-export", "", "Generated file is missing; run export"))
                    elif read_json_bytes(target) != expected:
                        add(target, Issue("stale-export", "", "Generated bytes differ; run export"))
                exit_code = 1 if issues else 0
            else:
                _write_outputs(root, outputs)
    except InputLimitError as exc:
        add(exc.file, Issue("input-limit", "", str(exc)))
        exit_code = max(exit_code, 1)
    except ContentError as exc:
        add("", Issue("json", "", str(exc)))
        exit_code = max(exit_code, 1)
    except (SetupError, OSError, RuntimeError) as exc:
        add("", Issue("setup", "", str(exc)))
        exit_code = 2
    for file, issue in sorted(issues, key=lambda entry: (entry[0], entry[1].path, entry[1].code, entry[1].message)):
        print(f"{file}{issue.path}: {issue.code}: {issue.message}")
    print(f"Static-only export {'passed' if exit_code == 0 else 'failed'}; execution skipped; not human/publication approval.")
    return exit_code
