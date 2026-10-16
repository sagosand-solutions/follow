# SPDX-License-Identifier: MIT
"""Synthetic fixtures (not instructional question prose)."""
import json
from pathlib import Path


def obj(properties, optional=()):
    return {"type": "object", "properties": properties,
            "required": [key for key in properties if key not in optional],
            "additionalProperties": False}


def array(items, minimum=1, **kw):
    return {"type": "array", "items": items, "minItems": minimum, **kw}


TEXT = {"type": "string", "minLength": 1}
LOCAL = {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,79}$"}
REF = {"type": "string", "pattern": r"^py\.[a-z][a-z0-9-]*$"}
LEVEL = {"enum": ["beginner", "intermediate", "advanced"]}
REFS = array(REF, uniqueItems=True)
BASE = {"schema_version": {"const": "0.1"}, "language": {"const": "python"}}


def schemas(directory):
    """Temporary structural schemas keep core unit tests independent of checkout state."""
    expected = obj({"stdout": {"type": "string"}, "exit_code": {"type": "integer", "minimum": 0, "maximum": 255},
                    "stderr_contains": TEXT}, ("stderr_contains",))
    question = obj({**BASE, "id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{2,79}$"},
                    "locale": {"const": "en"}, "type": {"enum": ["output", "crashing-input", "remove-line", "equivalence", "complexity", "ai-review"]},
                    "level": LEVEL, "difficulty": {"type": "number", "minimum": 0, "maximum": 1},
                    "concepts": REFS, "prompt": TEXT,
                    "snippets": array(obj({"id": LOCAL, "code": TEXT, "label": TEXT,
                                           "highlight_lines": array({"type": "integer", "minimum": 1}, 0, uniqueItems=True)}, ("label", "highlight_lines"))),
                    "options": array(obj({"id": LOCAL, "text": TEXT, "score": {"type": "number", "enum": [0, 0.5, 1]},
                                          "feedback": TEXT, "misconception": {"anyOf": [REF, {"type": "null"}]},
                                          "partial_reason": TEXT}, ("partial_reason",)), 5, maxItems=8),
                    "explanation": TEXT, "verification": obj({"status": {"const": "specified"},
                    "cases": array(obj({"id": LOCAL, "snippet_id": LOCAL, "stdin": {"type": "string"}, "expected": expected})),
                    "rationale": TEXT, "limitations": TEXT}, ("limitations",)),
                    "author": obj({"name": TEXT}), "license": {"enum": ["CC-BY-SA-4.0", "LicenseRef-Proprietary"]}})
    concept_map = obj({**BASE, "concepts": array(obj({"id": REF, "title": TEXT, "level": LEVEL,
                      "prerequisites": array(REF, 0, uniqueItems=True)})),
                      "misconceptions": array(obj({"id": REF, "title": TEXT, "description": TEXT, "concepts": REFS}))})
    for name, data in (("question.schema.json", question), ("concept-map.schema.json", concept_map)):
        Path(directory, name).write_text(json.dumps(data), encoding="utf-8")


def concept_map():
    return {"schema_version": "0.1", "language": "python",
            "concepts": [{"id": "py.values", "title": "Values", "level": "beginner", "prerequisites": []},
                         {"id": "py.functions", "title": "Functions", "level": "intermediate", "prerequisites": ["py.values"]}],
            "misconceptions": [{"id": "py.test-error", "title": "Test error", "description": "Synthetic error", "concepts": ["py.values"]},
                               {"id": "py.other-error", "title": "Other error", "description": "Synthetic other error", "concepts": ["py.functions"]}]}


def question():
    return {"schema_version": "0.1", "language": "python", "id": "test-question", "locale": "en", "type": "output",
            "level": "beginner", "difficulty": 0.2, "concepts": ["py.values"], "prompt": "Synthetic prompt",
            "snippets": [{"id": "main", "code": "print(1)"}],
            "options": [{"id": f"o{i}", "text": f"Synthetic option {i}", "score": score, "feedback": "Synthetic feedback",
                         "misconception": None if score == 1 else "py.test-error",
                         **({"partial_reason": "Synthetic correct and incorrect parts"} if score == 0.5 else {})}
                        for i, score in enumerate([1, 0.5, 0, 0, 0])],
            "explanation": "Synthetic explanation", "verification": {"status": "specified", "rationale": "Synthetic rationale",
            "cases": [{"id": "one", "snippet_id": "main", "stdin": "", "expected": {"stdout": "1\n", "exit_code": 0}}]},
            "author": {"name": "Test"}, "license": "CC-BY-SA-4.0"}
