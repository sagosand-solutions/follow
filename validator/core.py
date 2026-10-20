# SPDX-License-Identifier: MIT
"""Strict loading and deterministic, non-executing contract validation."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"

# Reviewed code policy, never question-controlled and without CLI overrides.
MAX_JSON_BYTES = 262144
MAX_QUESTION_FILES = 512
MAX_QUESTION_BYTES = 16777216
MAX_JSON_DEPTH = 32


class SetupError(Exception):
    """Local dependency, schema or input could not be loaded."""


class ContentError(ValueError):
    """Input is not strict JSON."""


class InputLimitError(ContentError):
    """A file input exceeds the reviewed static processing policy."""

    def __init__(self, path, message):
        self.file = str(path)
        super().__init__(f"{path}: {message}")


def check_question_count(count, path):
    if count > MAX_QUESTION_FILES:
        raise InputLimitError(path, f"question-count: {count} inputs exceeds {MAX_QUESTION_FILES}; duplicates count")


@dataclass
class QuestionBudget:
    """Actual question bytes read in one invocation, including repeated files."""
    bytes_read: int = 0

    @property
    def exhausted(self):
        return self.bytes_read > MAX_QUESTION_BYTES

    def consume(self, size, path):
        self.bytes_read += size
        if self.exhausted:
            raise InputLimitError(path, f"question-bytes: {self.bytes_read} actual bytes exceeds {MAX_QUESTION_BYTES} per invocation")


def check_json_size(size, path, *, context="JSON input"):
    if size > MAX_JSON_BYTES:
        raise InputLimitError(path, f"file-bytes: {context} exceeds {MAX_JSON_BYTES} bytes per JSON file")


def read_json_bytes(path, *, budget=None):
    """Read no more than the file cap plus one byte, without buffered prefetch."""
    raw = bytearray()
    try:
        with Path(path).open("rb", buffering=0) as stream:
            while len(raw) <= MAX_JSON_BYTES:
                chunk = stream.read(MAX_JSON_BYTES + 1 - len(raw))
                if not chunk:
                    break
                if budget is not None:
                    budget.consume(len(chunk), path)
                raw.extend(chunk)
    except OSError as exc:
        raise SetupError(f"{path}: cannot read UTF-8 JSON: {exc}") from exc
    check_json_size(len(raw), path)
    return bytes(raw)


def _check_depth(text, path):
    # Lexical preflight only: stdlib still validates the complete JSON grammar.
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise InputLimitError(path, f"json-depth: more than {MAX_JSON_DEPTH} nested containers; root container is depth 1")
        elif character in "]}":
            depth = max(0, depth - 1)


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str

    def to_dict(self):
        return asdict(self)


def parse_json_bytes(raw, path):
    """Strictly parse already bounded-read UTF-8 JSON bytes.

    This is the single strict byte-to-value seam. Callers that must hash and
    parse the *same* content (for example the study manifest builder) hash the
    exact bytes they pass here, so no second read can observe different data.
    The bytes must already satisfy :func:`read_json_bytes`/`check_json_size`;
    the UTF-8, depth, duplicate-key, finite-number and parser-resource gates
    applied here are identical to the historical ``load_json`` behaviour.
    """
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
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise SetupError(f"{path}: cannot read UTF-8 JSON: {exc}") from exc
    _check_depth(text, path)
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant,
                          parse_float=number)
    except (RecursionError, MemoryError) as exc:
        raise InputLimitError(path, f"json-parser-resource: {type(exc).__name__}; reduce JSON input complexity") from exc
    except json.JSONDecodeError as exc:
        raise ContentError(f"{path}:{exc.lineno}:{exc.colno}: {exc.msg}") from exc
    except ValueError as exc:
        # The stdlib object hook supplies no source offset for duplicate keys.
        raise ContentError(f"{path}: JSON document: {exc}") from exc


def load_json(path, *, budget=None):
    raw = read_json_bytes(path, budget=budget)
    return parse_json_bytes(raw, path)


def pointer(parts):
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _ordered(issues):
    return sorted(issues, key=lambda i: (i.path, i.code, i.message))


def _schema(data, name, *, schema_dir=None):
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry
    except ImportError as exc:
        raise SetupError("Install validator/requirements.txt in a virtual environment") from exc
    path = (SCHEMA_DIR if schema_dir is None else Path(schema_dir)) / name
    try:
        schema = load_json(path)
        Draft202012Validator.check_schema(schema)
        # No retrieval callback: even malicious remote references cannot fetch a URL.
        validator = Draft202012Validator(schema, registry=Registry())
        errors = list(validator.iter_errors(data))
    except (SetupError, InputLimitError):
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


def validate_map(map_data, *, schema_dir=None):
    issues = _schema(map_data, "concept-map.schema.json", schema_dir=schema_dir)
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


def validate_question(question, map_data, *, schema_dir=None):
    issues = _schema(question, "question.schema.json", schema_dir=schema_dir)
    map_issues = validate_map(map_data, schema_dir=schema_dir)
    if issues or map_issues:
        return _ordered(issues + [Issue("map-" + i.code, "/concept-map" + i.path, i.message) for i in map_issues])
    issues = _blanks(question)
    concepts = {c["id"] for c in map_data["concepts"]}
    misconceptions = {m["id"]: m for m in map_data["misconceptions"]}
    for i, ref in enumerate(question["concepts"]):
        if ref not in concepts:
            issues.append(Issue("unknown-concept", f"/concepts/{i}", f"Unknown concept: {ref}"))
    for field in ("snippets", "artifacts", "options"):
        issues += _duplicates(question.get(field, []), field)
    cases = question["verification"]["cases"]
    issues += _duplicates(cases, "verification/cases")
    scores = [o["score"] for o in question["options"]]
    if question.get("answer_profile") == "beginner-practice":     # exactly four options: one full, one half, two zero-credit
        if any(isinstance(s, bool) for s in scores) or scores.count(1) != 1 or scores.count(0.5) != 1 or scores.count(0) != 2:
            issues.append(Issue("scores", "/options", "The beginner-practice profile requires exactly one full, one half and two zero-credit options; booleans are not scores"))
    elif any(isinstance(s, bool) for s in scores) or scores.count(1) != 1 or scores.count(0.5) != 1 or scores.count(0) < 3:
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
    snippets = {s["id"]: s for s in question.get("snippets", [])}
    covered = set()
    for i, snippet in enumerate(question.get("snippets", [])):
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
    for i, snippet in enumerate(question.get("snippets", [])):
        if snippet["id"] not in covered:
            issues.append(Issue("unverified-snippet", f"/snippets/{i}", "Every displayed snippet needs a case"))
    from . import artifacts
    issues += artifacts.artifact_issues(question)
    return _ordered(issues)
