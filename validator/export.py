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
PUBLIC_LICENSE = "CC-BY-SA-4.0"
# Concept-id prefixes each course may use (its own, plus the shared Python vocabulary).
COURSE_PREFIXES = {"python": ("py",), "production": ("prod", "py"), "sql": ("sql", "py")}
LEGACY_ENTRY = {"id": "python", "concept_map": "schema/concepts/python-v0.json"}


def derived_names(course_id):
    """Python keeps index.json/concepts.json byte for byte; other courses get a suffix."""
    return ("index.json", "concepts.json") if course_id == "python" else (f"index-{course_id}.json", f"concepts-{course_id}.json")


def _prefix_of(identifier):
    return identifier.split(".", 1)[0]


def prefix_issues(course_id, *, map_data=None, question=None):
    """Concept and misconception ids of a course vocabulary or question must use the course's allowed prefixes."""
    allowed = COURSE_PREFIXES.get(course_id, ())
    found = []
    if map_data is not None:
        for i, concept in enumerate(map_data.get("concepts", [])):
            found += [(f"/concepts/{i}/id", concept["id"])] + [(f"/concepts/{i}/prerequisites/{j}", x) for j, x in enumerate(concept.get("prerequisites", []))]
        for i, item in enumerate(map_data.get("misconceptions", [])):
            found += [(f"/misconceptions/{i}/id", item["id"])] + [(f"/misconceptions/{i}/concepts/{j}", x) for j, x in enumerate(item.get("concepts", []))]
    if question is not None:
        found += [(f"/concepts/{i}", x) for i, x in enumerate(question.get("concepts", []))]
        found += [(f"/options/{i}/misconception", o["misconception"]) for i, o in enumerate(question.get("options", [])) if o.get("misconception")]
    return [Issue("course-prefix", path, f"{identifier} does not belong to the {course_id} course (allowed prefixes: {', '.join(allowed)})")
            for path, identifier in found if isinstance(identifier, str) and _prefix_of(identifier) not in allowed]


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


def validate_course(course, questions, *, schema_dir=None, onboarding_beginner=True):
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
    if onboarding_beginner and onboarding in questions and questions[onboarding]["level"] != "beginner":
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


def _catalog_entries(root, schema_dir, add):
    """The catalog's courses, or None after reporting problems. Without public/data/catalog.json the export is the
    original single Python course (legacy mode), so older checkouts and fixtures keep working."""
    path = safe_path(root, "public/data/catalog.json")
    if not path.exists():
        return [LEGACY_ENTRY], None
    catalog = load_json(path)
    found = core._schema(catalog, "catalog.schema.json", schema_dir=schema_dir)
    seen = set()
    for i, entry in enumerate(catalog.get("courses", []) if isinstance(catalog, dict) else []):
        cid = entry.get("id") if isinstance(entry, dict) else None
        if cid in seen:
            found.append(Issue("duplicate-course", f"/courses/{i}/id", f"Repeated course id: {cid}"))
        seen.add(cid)
        if isinstance(entry, dict) and entry.get("concept_map") != f"schema/concepts/{cid}-v0.json":
            found.append(Issue("catalog-map", f"/courses/{i}/concept_map", f"Expected schema/concepts/{cid}-v0.json"))
    if not found and "python" not in seen:
        found.append(Issue("catalog-python", "/courses", "The catalog must list the python course"))
    for issue in found:
        add(path, issue)
    return (None if found else catalog["courses"]), path


def _export(root, args, add, issues_seen):
    """Validate every catalog course and write (or --check) all derived files. Returns the exit code."""
    schema_dir = safe_path(root, "schema")
    catalog_path = safe_path(root, "public/data/catalog.json")
    required = ["question.schema.json", "concept-map.schema.json", "course.schema.json"] + (["catalog.schema.json"] if catalog_path.exists() else [])
    # Validate all required paths before opening any content or output.
    for name in required:
        path = safe_path(root, Path("schema") / name)
        if not path.is_file():
            raise SetupError(f"{path}: required local schema is missing")
    question_dir = safe_path(root, "public/data/questions")
    if not question_dir.is_dir():
        raise SetupError(f"{question_dir}: required question directory is missing")
    paths = _question_paths(question_dir)
    for path in paths:
        safe_path(root, path.relative_to(root))
    entries, catalog_file = _catalog_entries(root, schema_dir, add)
    if entries is None:
        return 1
    for entry in entries:
        for name in derived_names(entry["id"]):
            safe_path(root, Path("public/data") / name)
    courses, assigned = {}, {}
    for entry in entries:
        cid = entry["id"]
        map_path = safe_path(root, entry["concept_map"])
        course_path = safe_path(root, f"public/data/courses/{cid}.json")
        map_data = load_json(map_path)
        for issue in validate_map(map_data, schema_dir=schema_dir) + prefix_issues(cid, map_data=map_data):
            add(map_path, issue)
        course = load_json(course_path)
        shape = core._schema(course, "course.schema.json", schema_dir=schema_dir)
        for issue in shape:
            add(course_path, issue)
        if not shape and course["id"] != cid:
            add(course_path, Issue("course-id", "/id", f"Course file id {course['id']!r} does not match its catalog id {cid!r}"))
        courses[cid] = {"map": map_data, "map_path": map_path, "course": course, "course_path": course_path, "ok": not shape, "questions": {}}
        if not shape:
            for lesson in (l for level in course["levels"] for l in level["lessons"]):
                for qid in lesson["questions"]:
                    if assigned.setdefault(qid, cid) != cid:
                        add(course_path, Issue("question-in-two-courses", "/levels", f"Question {qid} is already in the {assigned[qid]} course"))
    questions_by_id, exit_code = set(), 0
    budget = QuestionBudget()
    for path in paths:
        try:
            question = load_json(path, budget=budget)
            # A question is validated against the vocabulary of the course that lists it (an unlisted one against python,
            # which also reports it as an orphan below).
            cid = assigned.get(question.get("id") if isinstance(question, dict) else None, "python")
            found = validate_question(question, courses[cid]["map"], schema_dir=schema_dir) or prefix_issues(cid, question=question)
            for issue in found:
                add(path, issue)
            if found:
                continue
            if question["license"] != PUBLIC_LICENSE:
                add(path, Issue("non-public-license", "/license",
                                f"Only {PUBLIC_LICENSE} content may be exported to the public site"))
                continue
            qid = question["id"]
            if path.name != qid + ".json":
                add(path, Issue("filename", "/id", f"Expected filename {qid}.json"))
            if qid in questions_by_id:
                add(path, Issue("duplicate-question", "/id", f"Repeated question ID: {qid}"))
            questions_by_id.add(qid)
            courses[cid]["questions"][qid] = question
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
    had_issues = bool(issues_seen)
    for cid, info in courses.items():
        if not info["ok"]:
            continue
        found = (core._schema(info["course"], "course.schema.json", schema_dir=schema_dir) if had_issues
                 else validate_course(info["course"], info["questions"], schema_dir=schema_dir, onboarding_beginner=(cid == "python")))
        for issue in found:
            add(info["course_path"], issue)
    if catalog_file is not None and not issues_seen:
        for i, entry in enumerate(entries):
            info = courses[entry["id"]]
            first = info["course"]["levels"][0]["lessons"][0]["questions"][0]
            if entry["free_questions"] != len(info["questions"]):
                add(catalog_file, Issue("catalog-count", f"/courses/{i}/free_questions", f"Catalog says {entry['free_questions']} free questions; the {entry['id']} course has {len(info['questions'])}"))
            if entry["start_question"] != first:
                add(catalog_file, Issue("catalog-start", f"/courses/{i}/start_question", f"Expected the course's first question {first}"))
    if issues_seen:
        return max(exit_code, 1)
    outputs = {}
    for entry in entries:
        cid, info = entry["id"], courses[entry["id"]]
        first = info["course"]["levels"][0]["lessons"][0]["questions"][0]
        fields = ("id", "type", "level", "difficulty", "concepts")
        index = {"data_version": 1, "onboarding_question": first,
                 "questions": [{field: info["questions"][qid][field] for field in fields} for qid in sorted(info["questions"])]}
        index_name, concepts_name = derived_names(cid)
        outputs[f"public/data/{index_name}"] = serialize(index)
        outputs[f"public/data/{concepts_name}"] = serialize(info["map"])
    for relative, payload in outputs.items():
        check_json_size(len(payload), safe_path(root, relative), context="generated JSON after formatting")
    if args.check:
        for relative, expected in outputs.items():
            target = safe_path(root, relative)
            if not target.exists():
                add(target, Issue("stale-export", "", "Generated file is missing; run export"))
            elif read_json_bytes(target) != expected:
                add(target, Issue("stale-export", "", "Generated bytes differ; run export"))
        return 1 if issues_seen else exit_code
    _write_outputs(root, outputs)
    return exit_code


def main(argv=None):
    parser = Parser(description="Static-only data export. No code execution or editorial approval.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Compare canonical generated bytes without writing")
    exit_code = 0
    issues_seen = []

    def add(file, issue):
        issues_seen.append((str(file), issue))

    try:
        args = parser.parse_args(argv)
        root = args.root.resolve()
        if not root.is_dir():
            raise SetupError(f"{root}: repository root is not a directory")
        exit_code = _export(root, args, add, issues_seen)
    except InputLimitError as exc:
        add(exc.file, Issue("input-limit", "", str(exc)))
        exit_code = max(exit_code, 1)
    except ContentError as exc:
        add("", Issue("json", "", str(exc)))
        exit_code = max(exit_code, 1)
    except (SetupError, OSError, RuntimeError) as exc:
        add("", Issue("setup", "", str(exc)))
        exit_code = 2
    for file, issue in sorted(issues_seen, key=lambda entry: (entry[0], entry[1].path, entry[1].code, entry[1].message)):
        print(f"{file}{issue.path}: {issue.code}: {issue.message}")
    print(f"Static-only export {'passed' if exit_code == 0 else 'failed'}; execution skipped; not human/publication approval.")
    return exit_code


if __name__ == "__main__":
    # Support `python -m validator.export ...` by delegating to the same main as
    # `python -m validator export ...`; without this the module is a silent no-op.
    raise SystemExit(main())
