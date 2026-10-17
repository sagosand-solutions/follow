"""Contract tests for question, concept-map and trace schemas.

Run from the repository root::

    pip install 'jsonschema>=4.18,<5'
    python3 -m unittest discover -s schema/tests -v

The suite validates the schemas, checks the seed concept map and the example
question, checks referential integrity of the seed map, executes the example
snippet and confirms that a broad set of mutated inputs is rejected. A few tests
deliberately show cases that JSON Schema does not decide; those belong to the
separate semantic validator.
"""

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCHEMA_DIR.parent
QUESTION_SCHEMA_PATH = SCHEMA_DIR / "question.schema.json"
MAP_SCHEMA_PATH = SCHEMA_DIR / "concept-map.schema.json"
TRACE_SCHEMA_PATH = SCHEMA_DIR / "trace.schema.json"
TRACE_03_SCHEMA_PATH = SCHEMA_DIR / "trace-0.3.schema.json"
COURSE_SCHEMA_PATH = SCHEMA_DIR / "course.schema.json"
CONCEPTS_PATH = SCHEMA_DIR / "concepts" / "python-v0.json"
EXAMPLE_PATH = REPO_ROOT / "content" / "examples" / "independent-if.json"

LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}


def load(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def build_validator(path):
    schema = load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def pop_key(*path):
    def mutate(obj):
        target = obj
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


def set_key(value, *path):
    def mutate(obj):
        target = obj
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def add_extra_key(*path):
    def mutate(obj):
        target = obj
        for key in path:
            target = target[key]
        target["unexpected_extra"] = "x"

    return mutate


def find_cycle(edges):
    """Return True if the prerequisite graph contains a directed cycle."""
    state = {}

    def visit(node):
        state[node] = "visiting"
        for nxt in edges.get(node, ()):
            if state.get(nxt) == "visiting":
                return True
            if state.get(nxt) is None and visit(nxt):
                return True
        state[node] = "done"
        return False

    return any(state.get(node) is None and visit(node) for node in edges)


class TestSchemaSelfValidation(unittest.TestCase):
    def test_question_schema_is_valid(self):
        build_validator(QUESTION_SCHEMA_PATH)

    def test_map_schema_is_valid(self):
        build_validator(MAP_SCHEMA_PATH)

    def test_trace_schema_is_valid(self):
        build_validator(TRACE_SCHEMA_PATH)

    def test_trace_03_schema_is_valid_and_0_2_is_untouched(self):
        build_validator(TRACE_03_SCHEMA_PATH)
        self.assertEqual(load(TRACE_SCHEMA_PATH)["properties"]["schema_version"], {"const": "0.2"})
        self.assertNotIn("frame_id", load(TRACE_SCHEMA_PATH)["$defs"]["step"]["properties"])

    def test_trace_03_contract(self):
        validator = build_validator(TRACE_03_SCHEMA_PATH)
        step = {"line": 1, "event": "line", "depth": 0, "function": "<module>", "locals": {}, "stdout_len": 0,
                "frame_id": 1, "scope": "module", "globals": {"x": "1"}, "locals_omitted": 0, "globals_omitted": 0}
        sha = "0" * 64

        def doc(version="0.3", **over):
            record = {"snippet_id": "main", "source_sha256": sha, "case_id": "case", "stdin": "", "stdin_sha256": sha,
                      "steps": [{**step, **over}], "stdout": "", "exception": None, "truncated": False}
            return {"schema_version": version, "question_id": "some-question", "traces": [record]}

        self.assertEqual(list(validator.iter_errors(doc())), [])
        self.assertTrue(list(validator.iter_errors(doc("0.2"))), "0.3 schema rejects a 0.2 envelope")
        for missing in ("frame_id", "scope", "globals", "locals_omitted", "globals_omitted"):
            bad = doc()
            del bad["traces"][0]["steps"][0][missing]
            self.assertTrue(list(validator.iter_errors(bad)), missing)
        self.assertTrue(list(validator.iter_errors(doc(frame_id=0))))
        self.assertTrue(list(validator.iter_errors(doc(scope="heap"))))
        self.assertTrue(list(validator.iter_errors(doc(globals={f"v{i}": "1" for i in range(41)}))))
        self.assertTrue(list(validator.iter_errors(doc(globals={"x": "y" * 81}))))
        self.assertTrue(list(validator.iter_errors(doc(refs={"x": "o1"}))), "no alias identity in 0.3")
        zero_two = build_validator(TRACE_SCHEMA_PATH)
        self.assertTrue(list(zero_two.iter_errors(doc("0.2"))), "0.2 schema rejects 0.3 step fields")

    def test_concept_id_prefixes_py_prod_and_sql_in_all_five_schemas(self):
        names = ("question", "concept-map", "study", "study-manifest", "exam")
        valid = ("py.values", "prod.queries", "prod.a1", "py.slice-stop-is-count", "prod.x-y", "sql.select", "sql.join-keys", "sql.a1")
        invalid = ("js.x", "sqlx.a", "sqlite.a", "prodx.a", "prod", "prod.", "py.", "sql", "sql.", "Py.values", "PROD.queries", "SQL.select", "Sql.select",
                   "prod.Queries", "sql.Select", "prod.1a", "sql.1a", "prod.a b", "sql.a b", "prod.a\n", "sql.a\n", " prod.a", " sql.a", "py:values", "sql:select", "prod..a", "sql..a", "")
        for name in names:
            schema = load(SCHEMA_DIR / f"{name}.schema.json")
            definition = schema["$defs"]["conceptId"]
            self.assertEqual(definition["pattern"], r"^(py|prod|sql)\.[a-z][a-z0-9-]*$", name)
            validator = Draft202012Validator(definition)
            for value in valid:
                with self.subTest(schema=name, value=value):
                    self.assertEqual(list(validator.iter_errors(value)), [])
            for value in invalid:
                with self.subTest(schema=name, value=value):
                    self.assertTrue(list(validator.iter_errors(value)))
            self.assertFalse(list(validator.iter_errors(1)) == [], "non-strings stay invalid")

    def test_concept_map_and_question_accept_prod_and_sql_ids_and_reject_other_prefixes(self):
        map_validator = build_validator(MAP_SCHEMA_PATH)
        question_validator = build_validator(QUESTION_SCHEMA_PATH)
        for prefix in ("prod", "sql"):
            with self.subTest(prefix=prefix):
                concept_map = copy.deepcopy(load(CONCEPTS_PATH))
                concept_map["concepts"].append({"id": f"{prefix}.reading-queries", "title": "Reading queries", "level": "beginner", "prerequisites": []})
                self.assertEqual(list(map_validator.iter_errors(concept_map)), [])
                for bad in ("js", "prodx", "sqlx", "Prod", "SQL"):
                    broken = copy.deepcopy(concept_map)
                    broken["concepts"][-1]["id"] = f"{bad}.a"
                    self.assertTrue(list(map_validator.iter_errors(broken)), bad)
                question = copy.deepcopy(load(EXAMPLE_PATH))
                question["concepts"] = [f"{prefix}.reading-queries"]
                self.assertEqual(list(question_validator.iter_errors(question)), [])
                question["concepts"] = ["js.reading-queries"]
                self.assertTrue(list(question_validator.iter_errors(question)))

    def test_schemas_are_self_contained(self):
        def collect_refs(node, found):
            if isinstance(node, dict):
                if isinstance(node.get("$ref"), str):
                    found.append(node["$ref"])
                for value in node.values():
                    collect_refs(value, found)
            elif isinstance(node, list):
                for item in node:
                    collect_refs(item, found)

        for path in (QUESTION_SCHEMA_PATH, MAP_SCHEMA_PATH, COURSE_SCHEMA_PATH):
            schema = load(path)
            self.assertNotIn("$id", schema)
            refs = []
            collect_refs(schema, refs)
            self.assertTrue(refs, f"{path.name} defines no $ref entries")
            for ref in refs:
                self.assertTrue(ref.startswith("#/$defs/"), f"non-local ref: {ref}")


class TestCourseSummarySchema(unittest.TestCase):
    def setUp(self):
        self.validator = build_validator(COURSE_SCHEMA_PATH)
        self.course = {
            "id": "python", "title": "Course", "language": "python", "levels": [
                {"level": "beginner", "title": "Beginner", "lessons": [
                    {"id": "lesson", "title": "Lesson", "builds_on": [], "questions": ["abc"]}
                ]}
            ]
        }

    def test_summary_is_optional_and_counts_unicode_codepoints(self):
        self.assertEqual(list(self.validator.iter_errors(self.course)), [])
        for summary in ("Intro text.", "x" * 200, "😀" * 200):
            with self.subTest(length=len(summary)):
                lesson = copy.deepcopy(self.course)
                lesson["levels"][0]["lessons"][0]["summary"] = summary
                self.assertEqual(list(self.validator.iter_errors(lesson)), [])
        for summary in ("x" * 201, "😀" * 201):
            with self.subTest(length=len(summary)):
                lesson = copy.deepcopy(self.course)
                lesson["levels"][0]["lessons"][0]["summary"] = summary
                self.assertTrue(list(self.validator.iter_errors(lesson)))

    def test_summary_requires_plain_single_line_text(self):
        for summary in ("", None, 1, [], {}, "x" * 201, "a\nb", "a\rb", "a\tb",
                        "a\x00b", "a\x1fb", "a\x7fb", "a\x9fb", "a\u2028b", "a\u2029b"):
            with self.subTest(summary=repr(summary)):
                course = copy.deepcopy(self.course)
                course["levels"][0]["lessons"][0]["summary"] = summary
                self.assertTrue(list(self.validator.iter_errors(course)))

    def test_forbidden_summary_characters_are_rejected_at_both_edges(self):
        forbidden = (chr(10), chr(13), chr(13) + chr(10), chr(0), chr(31),
                     chr(127), chr(159), chr(0x2028), chr(0x2029))
        for character in forbidden:
            for summary in (character + "Intro", "Intro" + character):
                with self.subTest(summary=repr(summary)):
                    course = copy.deepcopy(self.course)
                    course["levels"][0]["lessons"][0]["summary"] = summary
                    self.assertTrue(list(self.validator.iter_errors(course)))

    def test_summary_is_lesson_only(self):
        for path in ((), ("levels", 0), ("levels", 0, "lessons", 0)):
            course = copy.deepcopy(self.course)
            target = course
            for part in path:
                target = target[part]
            target["summary"] = "Lesson introduction."
            errors = list(self.validator.iter_errors(course))
            if path == ("levels", 0, "lessons", 0):
                self.assertEqual(errors, [])
            else:
                self.assertTrue(errors)


class TestSeedConceptMap(unittest.TestCase):
    def setUp(self):
        self.map = load(CONCEPTS_PATH)
        self.validator = build_validator(MAP_SCHEMA_PATH)

    def test_seed_map_validates(self):
        errors = sorted(self.validator.iter_errors(self.map), key=str)
        self.assertEqual(errors, [], "\n".join(str(e) for e in errors))

    def test_seed_map_counts(self):
        self.assertEqual(len(self.map["concepts"]), 20)
        self.assertEqual(len(self.map["misconceptions"]), 122)
        expected = {
            "py.slice-stop-is-count", "py.slice-mutates-source", "py.index-upper-bound-inclusive",
            "py.all-strings-truthy", "py.string-content-is-truth-value", "py.if-else-both-run",
            "py.global-required-for-read", "py.local-read-falls-back-global", "py.unbound-local-is-syntax-error",
            "py.get-default-for-falsy", "py.or-only-none-or-empty-string", "py.or-returns-bool", "py.or-always-fallback",
        }
        self.assertEqual({m["id"] for m in self.map["misconceptions"][43:56]}, expected)
        expected_next = {
            "py.floor-rounds-nearest", "py.floor-is-true-division", "py.floor-ignores-negative-sign",
            "py.multiplication-before-power", "py.addition-before-multiplication", "py.parentheses-do-not-override-precedence",
            "py.slice-shares-outer-container", "py.list-items-must-be-immutable", "py.comprehension-restores-outer",
            "py.comprehension-expression-uses-outer", "py.dict-iteration-sorts-keys", "py.dict-iteration-pairs",
            "py.dict-needs-keys-method", "py.keyerror-uncatchable", "py.finally-suppresses-error", "py.uncaught-skips-finally",
            "py.next-peeks-without-consuming", "py.all-iterators-allow-size-change", "py.halving-always-two-n-minus-one",
            "py.outer-loop-alone-determines-cost", "py.max-inner-bound-gives-tight-product",
        }
        self.assertEqual({m["id"] for m in self.map["misconceptions"][56:77]}, expected_next)
        material1_batch4 = {
            "py.multiplication-repeats-all-types", "py.numeric-strings-auto-coerce",
            "py.star-concatenates-operands", "py.string-repeat-forbidden",
            "py.strip-argument-is-substring", "py.strip-removes-interior", "py.strip-trims-right-only",
            "py.strip-ignores-characters-argument", "py.comprehension-filters-all-before-mapping",
            "py.comprehension-expression-before-filter", "py.comprehension-filter-becomes-result",
            "py.comprehension-ignores-element-expression", "py.and-returns-bool", "py.and-always-right",
            "py.and-false-has-no-value",
        }
        material2_batch4 = {
            "py.none-check-detects-omission", "py.all-lists-truthy", "py.iter-iterator-creates-cursor",
            "py.list-iterators-share-position", "py.next-skips-first-item", "py.iterable-iteration-copies-source",
            "py.sort-worst-case-is-every-case", "py.adjacent-scan-compares-all-pairs",
            "py.pipeline-costs-multiply", "py.send-auto-primes-generator", "py.send-returns-argument",
            "py.send-ignores-argument", "py.assigned-yield-hides-output",
        }
        self.assertEqual({m["id"] for m in self.map["misconceptions"][77:92]}, material1_batch4)
        self.assertEqual({m["id"] for m in self.map["misconceptions"][92:105]}, material2_batch4)
        self.assertEqual(self.map["misconceptions"][105]["id"], "py.minimum-needs-all-pair-comparisons")
        historical = {**self.map, "concepts": self.map["concepts"][:18]}
        original_106 = {**historical, "misconceptions": self.map["misconceptions"][:106]}
        original_106_digest = hashlib.sha256(json.dumps(original_106, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(original_106_digest, "1a6455672bfb62866cafbd5bdad1b94b66d8ee3df6a7ac170a2f4698b7a2e21f")
        preserved_77 = {**historical, "misconceptions": self.map["misconceptions"][:77]}
        preserved_77_digest = hashlib.sha256(json.dumps(preserved_77, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(preserved_77_digest, "7d30d46f56fb9a48f2e765a7b651a458e289337d82563f0699c7b32c6878d6db")
        for start, end, digest in (
            (77, 92, "17ae0cd60ca7ef114a53ccadcbadee83386bb1747b5d028b707292589fe60075"),
            (92, 105, "ecfc320ff6027214cbf9c26ca378ec9587b6536eb8482e9f53fe8ad1a4954a12"),
            (105, 106, "41fa376f9342e60e1c8fd91211e32accf4ae383895da5657d5f19c59c3dbc0c7"),
        ):
            actual = hashlib.sha256(json.dumps(self.map["misconceptions"][start:end], sort_keys=True,
                                               separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            self.assertEqual(actual, digest)
        previous = {**historical, "misconceptions": self.map["misconceptions"][:56]}
        previous_digest = hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(previous_digest, "ec02b128b352ccb0ed4319bdef9bce1534f221103b1e1bf72a53ce1c8a37f565")
        self.assertNotIn("py.or-only-empty-string", {m["id"] for m in self.map["misconceptions"]})
        # Additive vocabulary revisions must preserve all initial definitions.
        baseline = {**historical, "misconceptions": self.map["misconceptions"][:43]}
        digest = hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest, "49ccf22efef986c8d6a4a11e1f69c1c501d7d91c1c31d89edb51e48b1bb23c4f")

    def test_approved_122_extension_is_exact_and_additive(self):
        added_concepts = self.map["concepts"][18:]
        added_misconceptions = self.map["misconceptions"][106:]
        self.assertEqual({item["id"] for item in added_concepts}, {"py.modules", "py.classes"})
        self.assertEqual(
            {item["id"] for item in added_misconceptions},
            {
                "py.argument-evaluation-follows-parameters", "py.keyword-binding-is-positional",
                "py.defaulted-parameter-arguments-evaluate-first", "py.explicit-keyword-cannot-replace-default",
                "py.factory-calls-share-nonlocals", "py.nonlocal-nearest-scope-without-binding",
                "py.nonlocal-resets-on-call", "py.nonlocal-targets-module", "py.from-import-deepcopies",
                "py.repeated-import-fresh-module", "py.from-import-live-link", "py.module-constants-readonly",
                "py.class-bindings-snapshotted-at-construction", "py.class-mutable-data-copied-per-instance",
                "py.instance-assignment-writes-class", "py.instance-method-self-is-class",
            },
        )
        for records, expected in (
            (added_concepts, "c1b4f1bfe33a0a1fe0578e6253ccd308627b151571bfd9260743d6d358dc6abe"),
            (added_misconceptions, "9b44f97d852964f727a5605f2058f79c05793a63eaef0e063b768edd4355b639"),
        ):
            actual = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            self.assertEqual(actual, expected)

    def test_seed_map_referential_integrity(self):
        concepts = self.map["concepts"]
        misconceptions = self.map["misconceptions"]

        concept_ids = [c["id"] for c in concepts]
        self.assertEqual(len(concept_ids), len(set(concept_ids)))

        levels = {c["id"]: c["level"] for c in concepts}
        edges = {}
        for concept in concepts:
            edges[concept["id"]] = concept["prerequisites"]
            for prerequisite in concept["prerequisites"]:
                self.assertIn(prerequisite, levels)
                self.assertNotEqual(prerequisite, concept["id"])
                self.assertLessEqual(
                    LEVEL_ORDER[levels[prerequisite]],
                    LEVEL_ORDER[levels[concept["id"]]],
                )
        self.assertFalse(find_cycle(edges), "prerequisite graph has a cycle")

        misconception_ids = [m["id"] for m in misconceptions]
        self.assertEqual(len(misconception_ids), len(set(misconception_ids)))
        for misconception in misconceptions:
            for concept_id in misconception["concepts"]:
                self.assertIn(concept_id, levels)


class TestExampleQuestion(unittest.TestCase):
    def setUp(self):
        self.example = load(EXAMPLE_PATH)
        self.validator = build_validator(QUESTION_SCHEMA_PATH)
        self.map = load(CONCEPTS_PATH)

    def test_example_validates(self):
        errors = sorted(self.validator.iter_errors(self.example), key=str)
        self.assertEqual(errors, [], "\n".join(str(e) for e in errors))

    def test_example_references_exist_in_seed_map(self):
        levels = {c["id"] for c in self.map["concepts"]}
        misconception_concepts = {
            m["id"]: set(m["concepts"]) for m in self.map["misconceptions"]
        }
        tagged = set(self.example["concepts"])
        for concept_id in tagged:
            self.assertIn(concept_id, levels)
        for option in self.example["options"]:
            misconception_id = option["misconception"]
            if misconception_id is None:
                continue
            self.assertIn(misconception_id, misconception_concepts)
            self.assertTrue(
                misconception_concepts[misconception_id] & tagged,
                f"{misconception_id} is not tied to a tagged concept",
            )

    def test_interpreter_meets_baseline(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 11))


class TestQuestionMutations(unittest.TestCase):
    def setUp(self):
        self.example = load(EXAMPLE_PATH)
        self.validator = build_validator(QUESTION_SCHEMA_PATH)

    def base_mutations(self):
        return [
            # required fields omitted at each object level
            ("missing-schema_version", pop_key("schema_version")),
            ("missing-id", pop_key("id")),
            ("missing-language", pop_key("language")),
            ("missing-locale", pop_key("locale")),
            ("missing-type", pop_key("type")),
            ("missing-level", pop_key("level")),
            ("missing-difficulty", pop_key("difficulty")),
            ("missing-concepts", pop_key("concepts")),
            ("missing-prompt", pop_key("prompt")),
            ("missing-snippets", pop_key("snippets")),
            ("missing-options", pop_key("options")),
            ("missing-explanation", pop_key("explanation")),
            ("missing-verification", pop_key("verification")),
            ("missing-author", pop_key("author")),
            ("missing-license", pop_key("license")),
            ("missing-snippet-id", pop_key("snippets", 0, "id")),
            ("missing-snippet-code", pop_key("snippets", 0, "code")),
            ("missing-option-text", pop_key("options", 0, "text")),
            ("missing-option-score", pop_key("options", 0, "score")),
            ("missing-option-feedback", pop_key("options", 0, "feedback")),
            ("missing-option-misconception", pop_key("options", 0, "misconception")),
            ("missing-verification-status", pop_key("verification", "status")),
            ("missing-verification-cases", pop_key("verification", "cases")),
            ("missing-verification-rationale", pop_key("verification", "rationale")),
            ("missing-case-id", pop_key("verification", "cases", 0, "id")),
            ("missing-case-snippet_id", pop_key("verification", "cases", 0, "snippet_id")),
            ("missing-case-stdin", pop_key("verification", "cases", 0, "stdin")),
            ("missing-case-expected", pop_key("verification", "cases", 0, "expected")),
            ("missing-expected-stdout", pop_key("verification", "cases", 0, "expected", "stdout")),
            ("missing-expected-exit_code", pop_key("verification", "cases", 0, "expected", "exit_code")),
            ("missing-author-name", pop_key("author", "name")),
            # wrong constants
            ("wrong-schema_version", set_key("0.2", "schema_version")),
            ("wrong-language", set_key("javascript", "language")),
            ("wrong-locale", set_key("sv", "locale")),
            ("wrong-license", set_key("MIT", "license")),
            ("wrong-verification-status", set_key("passed", "verification", "status")),
            # unknown enum values
            ("unknown-type", set_key("debug", "type")),
            ("unknown-level", set_key("expert", "level")),
            # wrong types
            ("difficulty-string", set_key("0.5", "difficulty")),
            ("difficulty-bool", set_key(True, "difficulty")),
            ("concepts-string", set_key("py.conditionals", "concepts")),
            ("options-object", set_key({}, "options")),
            ("option-score-string", set_key("1", "options", 0, "score")),
            ("expected-exit_code-string", set_key("0", "verification", "cases", 0, "expected", "exit_code")),
            ("expected-stdout-number", set_key(0, "verification", "cases", 0, "expected", "stdout")),
            ("highlight-lines-string", set_key("2", "snippets", 0, "highlight_lines")),
            # boolean score and off-grid score
            ("score-boolean", set_key(True, "options", 1, "score")),
            ("score-0.25", set_key(0.25, "options", 1, "score")),
            # bad identifiers
            ("question-id-leading-digit", set_key("1-bad", "id")),
            ("question-id-too-short", set_key("ab", "id")),
            ("option-id-uppercase", set_key("Bad", "options", 0, "id")),
            ("concept-tag-uppercase", set_key(["py.Bad"], "concepts")),
            ("snippet-id-empty", set_key("", "snippets", 0, "id")),
            ("case-id-space", set_key("case bad", "verification", "cases", 0, "id")),
            # identifiers contaminated by whitespace (regex `$` bug guard)
            ("question-id-trailing-lf", set_key("independent-if-branches\n", "id")),
            ("question-id-trailing-cr", set_key("independent-if-branches\r", "id")),
            ("question-id-leading-space", set_key(" independent-if-branches", "id")),
            ("snippet-id-trailing-lf", set_key("primary\n", "snippets", 0, "id")),
            ("option-id-trailing-lf", set_key("opt-correct\n", "options", 0, "id")),
            ("case-id-trailing-lf", set_key("case-default-run\n", "verification", "cases", 0, "id")),
            ("concept-tag-trailing-lf", set_key(["py.conditionals\n"], "concepts")),
            ("concept-tag-leading-space", set_key([" py.conditionals"], "concepts")),
            # numeric ranges
            ("difficulty-negative", set_key(-0.1, "difficulty")),
            ("difficulty-above-one", set_key(1.1, "difficulty")),
            ("highlight-line-zero", set_key([0], "snippets", 0, "highlight_lines")),
            ("exit-code-too-large", set_key(256, "verification", "cases", 0, "expected", "exit_code")),
            ("exit-code-negative", set_key(-1, "verification", "cases", 0, "expected", "exit_code")),
            # option counts
            ("options-four", pop_key("options", 4)),
            ("options-empty", set_key([], "options")),
            # empty strings and arrays
            ("prompt-empty", set_key("", "prompt")),
            ("explanation-empty", set_key("", "explanation")),
            ("concepts-empty", set_key([], "concepts")),
            ("snippets-empty", set_key([], "snippets")),
            ("option-text-empty", set_key("", "options", 0, "text")),
            ("feedback-empty", set_key("", "options", 0, "feedback")),
            ("verification-cases-empty", set_key([], "verification", "cases")),
            ("verification-rationale-empty", set_key("", "verification", "rationale")),
            ("snippet-code-empty", set_key("", "snippets", 0, "code")),
            # duplicate values in unique arrays
            ("concepts-duplicate", set_key(["py.conditionals", "py.conditionals"], "concepts")),
            # nonzero exit without stderr_contains
            (
                "nonzero-exit-without-stderr",
                set_key(1, "verification", "cases", 0, "expected", "exit_code"),
            ),
            # score-dependent rules
            ("score-one-nonnull-misconception", set_key("py.independent-if-is-elif", "options", 0, "misconception")),
            ("score-one-with-partial_reason", set_key("because", "options", 0, "partial_reason")),
            ("score-zero-null-misconception", set_key(None, "options", 2, "misconception")),
            ("score-zero-with-partial_reason", set_key("because", "options", 2, "partial_reason")),
            ("score-half-missing-partial_reason", pop_key("options", 1, "partial_reason")),
            ("score-half-null-misconception", set_key(None, "options", 1, "misconception")),
            # unknown property at every object level
            ("extra-root-property", add_extra_key()),
            ("extra-snippet-property", add_extra_key("snippets", 0)),
            ("extra-option-property", add_extra_key("options", 0)),
            ("extra-verification-property", add_extra_key("verification")),
            ("extra-case-property", add_extra_key("verification", "cases", 0)),
            ("extra-expected-property", add_extra_key("verification", "cases", 0, "expected")),
            ("extra-author-property", add_extra_key("author")),
        ]

    def test_mutations_are_rejected(self):
        for label, mutate in self.base_mutations():
            with self.subTest(label=label):
                question = copy.deepcopy(self.example)
                mutate(question)
                errors = list(self.validator.iter_errors(question))
                self.assertTrue(errors, f"mutation accepted: {label}")

    def test_proprietary_license_is_accepted(self):
        question = copy.deepcopy(self.example)
        question["license"] = "LicenseRef-Proprietary"
        self.assertEqual(list(self.validator.iter_errors(question)), [])
        for bad in ("MIT", "CC-BY-SA-4.0 ", "LicenseRef-proprietary", "licenseref-proprietary", "", None):
            with self.subTest(bad=bad):
                question = copy.deepcopy(self.example)
                question["license"] = bad
                self.assertTrue(list(self.validator.iter_errors(question)), f"license accepted: {bad!r}")

    def test_too_many_options_rejected(self):
        question = copy.deepcopy(self.example)
        for index in range(4):
            question["options"].append(
                {
                    "id": f"opt-extra-{index}",
                    "text": f"Extra distractor {index}.",
                    "score": 0,
                    "feedback": f"Feedback {index}.",
                    "misconception": "py.independent-if-is-elif",
                }
            )
        self.assertEqual(len(question["options"]), 9)
        self.assertTrue(list(self.validator.iter_errors(question)))


class TestDelegatedToSemanticValidator(unittest.TestCase):
    """Cases JSON Schema intentionally does not decide."""

    def setUp(self):
        self.example = load(EXAMPLE_PATH)
        self.validator = build_validator(QUESTION_SCHEMA_PATH)

    def assert_valid(self, question):
        errors = sorted(self.validator.iter_errors(question), key=str)
        self.assertEqual(errors, [], "\n".join(str(e) for e in errors))

    def test_duplicate_embedded_ids_pass_schema(self):
        question = copy.deepcopy(self.example)
        question["options"][1]["id"] = question["options"][0]["id"]
        self.assert_valid(question)

    def test_score_counts_pass_schema(self):
        question = copy.deepcopy(self.example)
        question["options"][1]["score"] = 1
        question["options"][1]["misconception"] = None
        question["options"][1].pop("partial_reason")
        self.assert_valid(question)

    def test_whitespace_only_string_passes_schema(self):
        question = copy.deepcopy(self.example)
        question["prompt"] = "   "
        self.assert_valid(question)

    def test_highlight_line_beyond_source_passes_schema(self):
        question = copy.deepcopy(self.example)
        question["snippets"][0]["highlight_lines"] = [99]
        self.assert_valid(question)

    def test_empty_highlight_lines_pass_schema(self):
        question = copy.deepcopy(self.example)
        question["snippets"][0]["highlight_lines"] = []
        self.assert_valid(question)

    def test_duplicate_map_ids_pass_schema(self):
        mapdata = load(CONCEPTS_PATH)
        clone = copy.deepcopy(mapdata["concepts"][0])
        clone["title"] = "A different title"
        mapdata["concepts"].append(clone)
        errors = sorted(build_validator(MAP_SCHEMA_PATH).iter_errors(mapdata), key=str)
        self.assertEqual(errors, [], "\n".join(str(e) for e in errors))


class TestMapMutations(unittest.TestCase):
    def setUp(self):
        self.map = load(CONCEPTS_PATH)
        self.validator = build_validator(MAP_SCHEMA_PATH)

    def test_mutations_are_rejected(self):
        mutations = [
            ("missing-schema_version", pop_key("schema_version")),
            ("missing-language", pop_key("language")),
            ("missing-concepts", pop_key("concepts")),
            ("missing-misconceptions", pop_key("misconceptions")),
            ("wrong-schema_version", set_key("0.2", "schema_version")),
            ("wrong-language", set_key("javascript", "language")),
            ("concepts-empty", set_key([], "concepts")),
            ("misconceptions-empty", set_key([], "misconceptions")),
            ("concept-missing-id", pop_key("concepts", 0, "id")),
            ("concept-missing-title", pop_key("concepts", 0, "title")),
            ("concept-missing-level", pop_key("concepts", 0, "level")),
            ("concept-missing-prerequisites", pop_key("concepts", 0, "prerequisites")),
            ("misconception-missing-id", pop_key("misconceptions", 0, "id")),
            ("misconception-missing-title", pop_key("misconceptions", 0, "title")),
            ("misconception-missing-description", pop_key("misconceptions", 0, "description")),
            ("misconception-missing-concepts", pop_key("misconceptions", 0, "concepts")),
            ("concept-id-uppercase", set_key("py.Bad", "concepts", 0, "id")),
            ("concept-id-no-namespace", set_key("values", "concepts", 0, "id")),
            ("concept-id-trailing-lf", set_key("py.values\n", "concepts", 0, "id")),
            ("concept-id-leading-space", set_key(" py.values", "concepts", 0, "id")),
            ("misconception-id-trailing-cr", set_key("py.assignment-copies-value\r", "misconceptions", 0, "id")),
            ("concept-invalid-level", set_key("expert", "concepts", 0, "level")),
            ("concept-title-empty", set_key("", "concepts", 0, "title")),
            ("misconception-concepts-empty", set_key([], "misconceptions", 0, "concepts")),
            ("misconception-description-empty", set_key("", "misconceptions", 0, "description")),
            ("extra-root-property", add_extra_key()),
            ("extra-concept-property", add_extra_key("concepts", 0)),
            ("extra-misconception-property", add_extra_key("misconceptions", 0)),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                mapdata = copy.deepcopy(self.map)
                mutate(mapdata)
                errors = list(self.validator.iter_errors(mapdata))
                self.assertTrue(errors, f"mutation accepted: {label}")


if __name__ == "__main__":
    unittest.main()
