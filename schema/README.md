# Question schema and concept vocabulary

Machine-readable contract for code-reading questions and the shared concept map.
This directory defines the interchange format only. It does not run code and it
does not judge question quality.

## License

The JSON Schemas and the concept vocabulary in this directory are licensed under
**MIT** (see [`../LICENSES/MIT.txt`](../LICENSES/MIT.txt)). The existing free
public question and course corpus is licensed under **CC BY-SA 4.0** (see
[`../content/README.md`](../content/README.md)); new private premium content is
marked `"LicenseRef-Proprietary"` and is not part of that free corpus.

The content `license` field is an enum, not a free string: `"CC-BY-SA-4.0"` for
the free public content, or `"LicenseRef-Proprietary"` for private premium
content. `LicenseRef-Proprietary` is an explicit, self-defined license
reference; it points to the rights-holder's separately distributed terms and
grants no rights by itself. The public site export accepts only
`"CC-BY-SA-4.0"` and rejects anything else (fail closed); see
[`../validator/EXPORT.md`](../validator/EXPORT.md).

## Files

| File | Purpose |
|---|---|
| `question.schema.json` | JSON Schema (draft 2020-12) for one question. |
| `concept-map.schema.json` | JSON Schema (draft 2020-12) for the shared concept map. |
| `concepts/python-v0.json` | Python concept map: 20 concepts and 122 misconceptions; the original 18 concepts and 106 misconceptions remain unchanged. |
| `trace.schema.json` | JSON Schema (draft 2020-12) for precomputed trace schema 0.2. |
| `tests/` | Standard-library `unittest` suite that validates the schemas, the vocabulary and an example question. |
| `../content/examples/independent-if.json` | One illustrative question in this contract (CC BY-SA 4.0). |

All content is UTF-8 JSON. Question content is **plain text**: no HTML, no
Markdown, no template syntax. Rendered output must be inserted as text. A string
that is only whitespace is allowed by the schema but rejected by the validator,
which is the semantic layer (see below).

A prose field may contain an optional matched pair of backticks as a display hint
for inline code. This is not Markdown: only textual or code nodes are produced,
an unpaired backtick stays literal text, and no field requires a hint. A fallback
renderer may show the backticks literally.

## Interchange decision

Every question and the concept map are UTF-8 **JSON**, not YAML. A browser and a
Python process can read the same representation without a conversion step or a
YAML dependency. The draft is JSON Schema **2020-12**, and references are
relative only: no schema is fetched from the network. Every defined object
disallows additional properties, and every field listed below is required unless
marked optional.

## Question object

`question.schema.json` describes a single question.

| Field | Type | Rule |
|---|---|---|
| `schema_version` | string | Constant `"0.1"`. |
| `id` | string | Matches `^[a-z][a-z0-9-]{2,79}$`. |
| `language` | string | Constant `"python"`. |
| `locale` | string | Constant `"en"`. |
| `type` | string | One of `output`, `crashing-input`, `remove-line`, `equivalence`, `complexity`, `ai-review`. |
| `level` | string | One of `beginner`, `intermediate`, `advanced`. |
| `difficulty` | number | Author's relative estimate in `0..1` (0 easiest). Not a calibrated probability. |
| `concepts` | array | Nonempty, unique concept IDs, each matching `^py\.[a-z][a-z0-9-]*$`. |
| `prompt` | string | Nonempty plain text. States all assumptions and the actual question. |
| `snippets` | array | Nonempty array of Snippet objects. The first snippet is the primary displayed code. |
| `options` | array | 5 to 8 Option objects. |
| `explanation` | string | Nonempty plain text giving the correct conclusion. |
| `verification` | object | A Verification object (below). |
| `author` | object | `{ "name": <nonempty string> }` only. |
| `license` | string | Enum `"CC-BY-SA-4.0"` or `"LicenseRef-Proprietary"`. |

Local identifiers for snippets, options and cases match
`^[a-z][a-z0-9-]{0,79}$`. They are stable opaque identifiers, so a reader can key
answers by ID rather than by position.

### Snippet

| Field | Type | Rule |
|---|---|---|
| `id` | string | Local ID, unique within `snippets`. |
| `code` | string | Nonempty. Rendered exactly as text; line numbers are display metadata and are never inserted into copied source. |
| `label` | string | Optional, nonempty. |
| `highlight_lines` | array | Optional, unique integers `>= 1`; may be empty. The validator rejects a value greater than the actual source line count. |

Source line count is computed with `splitlines`; an empty final segment after a
terminal newline is not an extra source line. Later snippets are visible
variants or examples. A `remove-line` question represents the removed line as a
second, actually displayed snippet when its behavior is tested.

### Option

| Field | Type | Rule |
|---|---|---|
| `id` | string | Local ID, unique within `options`. |
| `text` | string | Nonempty plain text. No two options may have the same text after trimming. |
| `score` | number | Exactly one of `0`, `0.5`, `1`. |
| `feedback` | string | Nonempty plain text, specific to this option. |
| `misconception` | string or null | A misconception ID, or `null`. |
| `partial_reason` | string | Optional, nonempty. |

Scoring and the score-dependent rules:

- **Exactly one** option scores `1`, **exactly one** scores `0.5`, and **at least
  three** score `0`. A question has at most eight options.
- **Score `1`** requires `misconception` to be `null` and forbids
  `partial_reason`.
- **Score `0`** requires a misconception ID and forbids `partial_reason`.
- **Score `0.5`** requires both a misconception ID and a `partial_reason`.

The schema declares the *shape* of these rules (types, allowed score values, the
`if`/`then` linkage between score and the other fields). It does not count how
many options have each score; that count is a validator check.

Every wrong option maps to a named misconception. The same misconception may
appear in more than one option if that is justified, but avoid redundant
distractors. A misconception must be associated with at least one of the
question's tagged concepts.

#### Genuine half credit

The `0.5` option must be genuinely half right, not merely partially plausible.
It exists to catch a specific misunderstanding, such as reaching the correct
result through a wrong causal story, or being right for the shown input but wrong
at an edge case. `partial_reason` must name **both** the part that is right and
the part that is wrong or incomplete, in plain language. A `0.5` option without
that explicit split is not acceptable, and the schema enforces its presence.

### Verification

`verification` declares the expectations for how the displayed code should behave
when run. It is not a self-asserted quality approval and it does not claim that
the code was executed as part of static validation.

| Field | Type | Rule |
|---|---|---|
| `status` | string | Constant `"specified"`. It declares that cases are specified, not that the question passed review. |
| `cases` | array | Nonempty, unique Case objects. |
| `rationale` | string | Nonempty. How the cases support the correct answer and what they cannot establish. |
| `limitations` | string | Optional, nonempty. Known gaps. |

Case fields:

| Field | Type | Rule |
|---|---|---|
| `id` | string | Local ID, unique within `cases`. |
| `snippet_id` | string | Refers to one displayed snippet. |
| `stdin` | string | May be empty. No argv, shell commands, external files or dependencies. |
| `expected` | object | Required `stdout` (string, may be empty) and `exit_code` (integer `0..255`); optional `stderr_contains` (nonempty literal string). |

Execution rules that the runner enforces:

- The question is run as a script where `__name__ == "__main__"`. The exact
displayed snippet is run; the validator must not silently substitute a different
proof program.
- `stdout` is compared literally, including the trailing newline, and the exit
code must match exactly.
- When `stderr_contains` is present, the literal substring must appear in
  stderr. When it is absent, a successful case (`exit_code` `0`) requires empty
  stderr. A **nonzero** expected exit code requires `stderr_contains`, for
  example `ZeroDivisionError`, so that unstable traceback paths are not
  depended on. The schema enforces this requirement.
- Termination by signal, timeout, output cap, runner failure or a memory limit
  is never treated as an expected crash. A negative observed exit code is an
  infrastructure failure even if stdout looks right.

Every displayed snippet must have at least one case (validator check).

## Concept map

`concept-map.schema.json` describes the shared vocabulary that every question is
tagged against.

| Field | Type | Rule |
|---|---|---|
| `schema_version` | string | Constant `"0.1"`. |
| `language` | string | Constant `"python"`. |
| `concepts` | array | Nonempty array of Concept objects. |
| `misconceptions` | array | Nonempty array of Misconception objects. |

Concept: `{ id, title, level, prerequisites }` where `id` matches
`^py\.[a-z][a-z0-9-]*$`, `title` is nonempty, `level` is one of the three level
values, and `prerequisites` is a unique (possibly empty) array of concept IDs.

Misconception: `{ id, title, description, concepts }` where `id` matches the
concept-ID pattern, `title` and `description` are nonempty, and `concepts` is a
nonempty unique array of concept IDs.

Semantic rules enforced by the validator: concept and misconception IDs are
globally unique; there are no unknown prerequisites, no self-dependencies and no
directed cycles; no concept depends on a higher level than itself; and every
misconception concept reference exists. Level ordering is
`beginner < intermediate < advanced`. Prerequisites express a learning order,
not a validated cognitive model. A question's `level` need not equal the level of
every tagged concept; advanced questions often use basic building blocks.

The misconceptions are editorial hypotheses. They are not, in this
version, assertions backed by prevalence research, and files must not claim they
are.

### Adding a concept or misconception

1. Choose an ID matching `^py\.[a-z][a-z0-9-]*$` (lowercase, dot-namespaced,
   hyphen-separated).
2. Add the entry to `concepts/python-v0.json` with a title, a level and its
   prerequisites (or `concepts` and `description` for a misconception).
3. Make sure the change is acyclic and that no concept depends on a higher
   level.
4. Tag questions with the new ID. A question that references an ID which is not
   in the shared map must not pass validation.

Do not infer IDs dynamically and do not invent an ID inside a single question.
Additions are proposed to the maintainer of the concept map, who approves them
and issues a small vocabulary revision.

## Static validation, execution and human review

These are three different things, and reports must not blur them.

- **Static validation** (JSON Schema, plus the semantic validator) checks
  structure, types, domains, identifier uniqueness, score counts and
  cross-document references. It runs no code. This project's schema does not
  enforce per-array identifier uniqueness (JSON Schema's `uniqueItems` only
  rejects an identical whole entry), correct score counts, an acyclic concept
  graph, or `highlight_lines` within the actual source. Those checks belong to
  the semantic validator and are intentionally out of scope for the schema. JSON
  Schema could express some of them with `contains`/`minContains`/`pattern`, but
  this contract keeps them in the validator instead.
- **Execution** runs the displayed snippet with the given stdin and compares
  stdout and the exit code. The local runner is **trusted-only**: it is a
  convenience for content the author already inspected and it does **not**
  isolate the network or the filesystem. A hardened sandbox is a separate
  deliverable. Passing execution shows that the declared expectations hold. It
  does **not** show that the correct option logically follows from those
  observations. Equivalence over all inputs, asymptotic complexity, pedagogical
  validity and genuine half credit are not machine-proven by a run.
- **Human review** decides whether the question is correct, well-tagged, has a
  strong set of distractors and a genuine half-credit option. Automated checks
  only prepare that review.

## Python compatibility baseline

The execution baseline is **Python 3.11 or newer**. Snippets must rely on
behavior that is stable across supported Python 3 versions. Expected answers may
not depend on hash ordering, time, environment or platform. Report the actual
interpreter version used when a question is verified. Questions whose behavior
differs between interpreter versions are out of scope until the contract gains a
versioned runtime field.

## Running the tests

The suite uses the standard library `unittest` plus the `jsonschema` package.
It is fully static: it validates structure and references and never executes
question code.

```
pip install 'jsonschema>=4.18,<5'
python3 -m unittest discover -s schema/tests -v
```

The test suite validates both schemas, checks that the seed concept map and the
example question pass, checks referential integrity of the seed map, and confirms
that a set of mutated inputs is rejected. It also documents, with explicit tests,
the cases that the schema deliberately leaves to the semantic validator.

## Trusted-only local execution

Executing a displayed snippet is a separate, explicit opt-in step and is not part
of the static test suite. Use `schema/tests/trusted_execution.py`, which runs the
declared verification cases of a question file on the local interpreter and
reports the interpreter version, actual stdout, exit code and stderr:

```
python3 schema/tests/trusted_execution.py content/examples/independent-if.json
```

This helper is trusted-only. It uses a timeout and isolated Python mode, but that
is **not** a sandbox: it does not isolate the network or the filesystem and must
only be used on code that a human has already inspected. Untrusted code must not
be executed at all until an approved hardened runner exists; for untrusted
content, only static validation is appropriate now.
