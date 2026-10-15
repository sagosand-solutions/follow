# SPDX-License-Identifier: MIT
"""Strict loading and deterministic, non-executing contract validation."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"


class SetupError(Exception):
    """Local dependency, schema or input could not be loaded."""


class ContentError(ValueError):
    """Input is not strict JSON."""


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str

    def to_dict(self):
        return asdict(self)


def load_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"nonfinite number {value}")

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"nonfinite number {value}")
        return result

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SetupError(f"{path}: cannot read UTF-8 JSON: {exc}") from exc
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant,
                          parse_float=number)
    except json.JSONDecodeError as exc:
        raise ContentError(f"{path}:{exc.lineno}:{exc.colno}: {exc.msg}") from exc
    except ValueError as exc:
        # The stdlib object hook supplies no source offset for duplicate keys.
        raise ContentError(f"{path}: JSON document: {exc}") from exc


def pointer(parts):
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _ordered(issues):
    return sorted(issues, key=lambda i: (i.path, i.code, i.message))


def _schema(data, name):
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry
    except ImportError as exc:
        raise SetupError("Install validator/requirements.txt in a virtual environment") from exc
    path = SCHEMA_DIR / name
    try:
        schema = load_json(path)
        Draft202012Validator.check_schema(schema)
        # No retrieval callback: even malicious remote references cannot fetch a URL.
        validator = Draft202012Validator(schema, registry=Registry())
        errors = list(validator.iter_errors(data))
    except SetupError:
        raise
    except Exception as exc:
        raise SetupError(f"{path}: invalid or unresolved local schema: {exc}") from exc
    issues = [Issue("schema", pointer(e.absolute_path), e.message) for e in errors]

    def inspect(value, parts=()):
        if isinstance(value, float) and not math.isfinite(value):
            issues.append(Issue("nonfinite", pointer(parts), "Numbers must be finite"))
        elif isinstance(value, dict):
            for key, item in value.items():
                inspect(item, (*parts, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                inspect(item, (*parts, index))
    inspect(data)
    return _ordered(issues)


def _blanks(value, parts=()):
    issues = []
    if isinstance(value, dict):
        for key, item in value.items():
            issues.extend(_blanks(item, (*parts, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_blanks(item, (*parts, index)))
    elif isinstance(value, str) and parts[-1:] not in (("stdin",), ("stdout",)) and not value.strip():
        issues.append(Issue("blank", pointer(parts), "Text must not be whitespace-only"))
    return issues


def _duplicates(items, path, field="id"):
    issues, seen = [], set()
    for i, item in enumerate(items):
        value = item[field]
        if value in seen:
            issues.append(Issue("duplicate", f"/{path}/{i}/{field}", f"Duplicate {field}: {value}"))
        seen.add(value)
    return issues


def validate_map(map_data):
    issues = _schema(map_data, "concept-map.schema.json")
    if issues:
        return issues
    issues = _blanks(map_data)
    concepts = {c["id"]: c for c in map_data["concepts"]}
    issues += _duplicates(map_data["concepts"], "concepts")
    issues += _duplicates(map_data["misconceptions"], "misconceptions")
    levels = {"beginner": 0, "intermediate": 1, "advanced": 2}
    for i, concept in enumerate(map_data["concepts"]):
        for j, ref in enumerate(concept["prerequisites"]):
            path = f"/concepts/{i}/prerequisites/{j}"
            if ref not in concepts:
                issues.append(Issue("unknown-concept", path, f"Unknown prerequisite: {ref}"))
            elif ref == concept["id"]:
                issues.append(Issue("self-edge", path, "Concept cannot require itself"))
            elif levels[concepts[ref]["level"]] > levels[concept["level"]]:
                issues.append(Issue("higher-level", path, "Prerequisite has a higher level"))
    # Iterative DFS avoids recursion limits on large contributor maps.
    colors = {}
    for root in sorted(concepts):
        if colors.get(root):
            continue
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                colors[node] = 2
                continue
            if colors.get(node) == 1:
                issues.append(Issue("cycle", "/concepts", f"Prerequisite cycle includes {node}"))
                continue
            if colors.get(node) == 2:
                continue
            colors[node] = 1
            stack.append((node, True))
            stack.extend((ref, False) for ref in reversed(concepts[node]["prerequisites"]) if ref in concepts)
    for i, misconception in enumerate(map_data["misconceptions"]):
        for j, ref in enumerate(misconception["concepts"]):
            if ref not in concepts:
                issues.append(Issue("unknown-concept", f"/misconceptions/{i}/concepts/{j}", f"Unknown concept: {ref}"))
    return _ordered(issues)


def validate_question(question, map_data):
    issues = _schema(question, "question.schema.json")
    map_issues = validate_map(map_data)
    if issues or map_issues:
        return _ordered(issues + [Issue("map-" + i.code, "/concept-map" + i.path, i.message) for i in map_issues])
    issues = _blanks(question)
    concepts = {c["id"] for c in map_data["concepts"]}
    misconceptions = {m["id"]: m for m in map_data["misconceptions"]}
    for i, ref in enumerate(question["concepts"]):
        if ref not in concepts:
            issues.append(Issue("unknown-concept", f"/concepts/{i}", f"Unknown concept: {ref}"))
    for field in ("snippets", "options"):
        issues += _duplicates(question[field], field)
    cases = question["verification"]["cases"]
    issues += _duplicates(cases, "verification/cases")
    scores = [o["score"] for o in question["options"]]
    if any(isinstance(s, bool) for s in scores) or scores.count(1) != 1 or scores.count(0.5) != 1 or scores.count(0) < 3:
        issues.append(Issue("scores", "/options", "Require one full, one half and at least three zero-credit options; booleans are not scores"))
    texts = set()
    for i, option in enumerate(question["options"]):
        path = f"/options/{i}"
        text = option["text"].strip()
        if text in texts:
            issues.append(Issue("duplicate-text", path + "/text", "Option text repeats after trimming"))
        texts.add(text)
        score, ref = option["score"], option["misconception"]
        if score == 1:
            if ref is not None or "partial_reason" in option:
                issues.append(Issue("full-credit", path, "Full credit requires null misconception and no partial_reason"))
        else:
            if ref is None:
                issues.append(Issue("misconception-required", path + "/misconception", "Non-full credit requires a misconception"))
            elif ref not in misconceptions:
                issues.append(Issue("unknown-misconception", path + "/misconception", f"Unknown misconception: {ref}"))
            elif not set(misconceptions[ref]["concepts"]) & set(question["concepts"]):
                issues.append(Issue("untagged-misconception", path + "/misconception", "Misconception must relate to a tagged concept"))
            if (score == 0.5) != ("partial_reason" in option):
                issues.append(Issue("partial-reason", path, "Only half credit requires partial_reason"))
    snippets = {s["id"]: s for s in question["snippets"]}
    covered = set()
    for i, snippet in enumerate(question["snippets"]):
        for j, line in enumerate(snippet.get("highlight_lines", [])):
            if line > len(snippet["code"].splitlines()):
                issues.append(Issue("line-range", f"/snippets/{i}/highlight_lines/{j}", "Line exceeds displayed source"))
    for i, case in enumerate(cases):
        path = f"/verification/cases/{i}"
        if case["snippet_id"] not in snippets:
            issues.append(Issue("unknown-snippet", path + "/snippet_id", "Case must reference a displayed snippet"))
        covered.add(case["snippet_id"])
        if case["expected"]["exit_code"] != 0 and "stderr_contains" not in case["expected"]:
            issues.append(Issue("crash-evidence", path + "/expected", "Nonzero expected exit requires stderr_contains"))
    for i, snippet in enumerate(question["snippets"]):
        if snippet["id"] not in covered:
            issues.append(Issue("unverified-snippet", f"/snippets/{i}", "Every displayed snippet needs a case"))
    return _ordered(issues)
