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
| `catalog.schema.json` | JSON Schema for `public/data/catalog.json`, the public course catalog (courses by id rule, at most 8). `free_questions` is checked against the course file by the exporter; `premium.questions` is set by hand and promises no date or price, and `premium` may be left out. Optional `audience` and `path_step` (1-3, one course each) label courses for the home page. |
| `concepts/production-v0.json`, `concepts/sql-v0.json` | Public vocabularies of the free trial courses: only the concepts and misconceptions their free questions use, closed under prerequisites, copied verbatim from the private overlay maps (`py.*` entries equal python-v0). MIT, like python-v0. |
| `concepts/python-v0.json` | Python concept map: 20 concepts and 172 misconceptions; the original 18 concepts and 106 misconceptions remain unchanged. |
| `trace.schema.json` | JSON Schema (draft 2020-12) for precomputed trace schema 0.2. |
| `trace-0.3.schema.json` | Trace schema 0.3 (opt-in): adds `frame_id`, `scope`, `globals` and omitted counts per step; 0.2 is unchanged. |
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
| `schema_version` | string | `"0.1"` (the legacy standard question), `"0.2"` (explicit answer profile) or `"0.3"` (0.2 plus text artifacts, below). Any other value is refused. |
| `answer_profile` | string | **Required in 0.2 and 0.3, forbidden in 0.1.** `standard` (5 to 8 options) or `beginner-practice` (exactly 4 options). Never inferred. |
| `id` | string | Matches `^[a-z][a-z0-9-]{2,79}$`. |
| `language` | string | `"python"` (code is shown and verified). `"text"` only in 0.3, for a question that shows text artifacts and no code. |
| `locale` | string | Constant `"en"`. |
| `type` | string | One of `output`, `crashing-input`, `remove-line`, `equivalence`, `complexity`, `ai-review`. |
| `level` | string | One of `beginner`, `intermediate`, `advanced`. |
| `difficulty` | number | Author's relative estimate in `0..1` (0 easiest). Not a calibrated probability. |
| `concepts` | array | Nonempty, unique concept IDs, each matching `^(py\|prod\|sql\|git\|http\|test\|rev)\.[a-z][a-z0-9-]*$`. |
| `prompt` | string | Nonempty plain text. States all assumptions and the actual question. |
| `snippets` | array | Nonempty array of Snippet objects. The first snippet is the primary displayed code. Required in 0.1 and 0.2; in 0.3 required when `language` is `python` and forbidden when it is `text`. |
| `artifacts` | array | 0.3 only: 1 to 3 text Artifact objects (below). Required when `language` is `text`; forbidden in 0.1 and 0.2. |
| `options` | array | Option objects: 5 to 8 for `0.1` and `0.2` `standard`; exactly 4 for `0.2` `beginner-practice`. |
| `explanation` | string | Nonempty plain text giving the correct conclusion. |
| `verification` | object | A Verification object (below). |
| `author` | object | `{ "name": <nonempty string> }` only. |
| `license` | string | Enum `"CC-BY-SA-4.0"` or `"LicenseRef-Proprietary"`. |

### Answer profiles (0.2)

Version `0.1` is unchanged: five to eight options, one full, one half and at least three zero-credit, and no
`answer_profile`. Version `0.2` adds an explicit profile and nothing else:

- `standard`: the same rules as `0.1` (5 to 8 options, one 1, one 0.5, at least three 0).
- `beginner-practice`: exactly **four** options, one 1, one 0.5 and **two** 0, for a clean beginner exercise. A separate "I'm not
  sure" row in a reader is not a JSON option. Booleans are never scores.

An unknown version, an unknown or missing profile, a profile on `0.1` and every inconsistent combination are refused;
there is no fallback, and nothing is inferred from a course name, level, id prefix, option count or licence. The schema decides
structure and option count; the validator decides the score distribution, unique option ids, the misconception and
`partial_reason` binding and the snippet and case references, in the same way for both profiles. A `beginner-practice` question is
local practice only: it is never part of a server scoring pool (the scoring-catalog builder and the server both refuse it). The
exported course index (`data_version` 2) states every question's profile explicitly, and a reader computes scoring pools and points only
from a complete index; with an unknown profile it computes nothing.

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

### Text artifacts (0.3)

A text artifact is **display text that is never run**: a diff, an HTTP transcript, a test report, a policy or plain text. It is not a
snippet, never gets a trace, and is never labelled as passing a run. A question that uses artifacts is `schema_version "0.3"`, so a
reader that does not know 0.3 refuses the question instead of silently dropping the text. `0.1` and `0.2` questions are unchanged.

| Field | Type | Rule |
|---|---|---|
| `id` | string | Local ID, unique among the question's snippets and artifacts. |
| `kind` | string | `text`, `diff`, `http-transcript`, `test-report` or `policy`. |
| `format` | string | Fixed by the kind: `unified-diff` for `diff`, `http-1.1-transcript` for `http-transcript`, `plain` otherwise. |
| `text` | string | 1 to 4000 code points and at most 80 lines. Line breaks are `\n` only; no carriage return, no control characters other than tab and newline, no U+2028/2029, no lone surrogate. |
| `sha256` | string | 64 lower-case hex: the SHA-256 of the UTF-8 bytes of `text` exactly as stored (no BOM, no normalisation, no line-ending change, no trimming). |
| `provenance` | object | Exactly `fixture_id` (local ID) and `fixture_output_sha256` (64 lower-case hex): the hash of the bytes the authoring fixture produced. |
| `label` | string | Optional, at most 80 code points; no control characters and no lone surrogate. |

No other key is allowed, so an artifact cannot carry a URL, a source, markup or anything that runs. The exporter adds what a schema
cannot say: `artifact-hash` (`sha256` is the hash of `text`), `artifact-binding` (the fixture output hash is the same bytes as the
shown text), `artifact-limits`, `artifact-text`, `artifact-id` and `artifact-url`. **URLs:** the text may name a URL only if its host
is one of the names an example may use (`example.com`, `.net`, `.org`, any `*.example`, `*.test`, `*.invalid`, `localhost`, `127.0.0.1`, or an address in
`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`; names match on whole labels, not substrings), without credentials; anything
else is `artifact-url`. This is a content rule, not a network control: nothing ever fetches a URL named in an artifact, not even a
reserved one. A relative request target (`GET /items HTTP/1.1`) or a `Host:` header is not a URL and stays allowed text.

**How verification binds.** A snippet is bound by a *case*: `snippet_id` + `stdin` + expected `stdout` and `exit_code`, run by the
runner. An artifact is bound by its *fixture*: the authoring harness (outside any reader or server path) writes the bytes the fixture
produced to `content/fixtures/artifacts/<fixture_id>.out`, and the question records their hash as `provenance.fixture_output_sha256`.
Two hash fields in a question prove nothing by themselves, so the export reads the file: it must exist (the id is a name, never a
path; the directory is fixed), and it is opened by descriptor from the repository root down, so a symlink on the way stops the export,
the opened file must be a regular file, at most 20000 bytes of it are read, and a path swapped after the start cannot redirect the
read. The root is trusted; nothing below it is. The bytes read must hash to the recorded value, which must also be the hash of the shown text (`artifact-fixture`, `artifact-binding`, `artifact-hash`). If the fixture output changes and
the question is not updated, or the other way round, the export fails. `validator.artifacts.verify_artifact_fixture(question,
artifact_id, fixture_bytes)` is the same check for the harness.

**Readers.** The browser shows each artifact as one block of literal text (never parsed as markup or markdown, no link, image or
request). `validator trace` expects no trace for a text-only question and treats a trace file for one as an orphan. The server's
scoring pool does not carry artifacts yet: the pool builder and the service refuse any 0.3 question.

**Migration.** Nothing to do for existing questions. To use artifacts, set `schema_version` to `"0.3"`, keep the `answer_profile`, add
`artifacts` (and `language: "text"` with no `snippets` for a text-only question), and record each fixture's output hash. A minimal
text-only question differs from a 0.2 one only in these fields:

```json
{
  "schema_version": "0.3",
  "answer_profile": "standard",
  "language": "text",
  "artifacts": [
    {
      "id": "change",
      "kind": "diff",
      "format": "unified-diff",
      "text": "--- a/total.py\n+++ b/total.py\n@@ -1,2 +1,2 @@\n def total(xs):\n-    return sum(xs)\n+    return sum(xs) + 1\n",
      "sha256": "<sha256 of the text above, UTF-8>",
      "provenance": { "fixture_id": "total-change", "fixture_output_sha256": "<same value>" }
    }
  ],
  "verification": { "status": "specified", "cases": [], "rationale": "The diff is the fixture's output." }
}
```

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
| `cases` | array | Unique Case objects; nonempty whenever the question has snippets (a text-only question has none). |
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

1. Choose an ID matching `^(py|prod|sql|git|http|test|rev)\.[a-z][a-z0-9-]*$` (lowercase, dot-namespaced,
   hyphen-separated). `py.` is the Python course, `prod.` the production-code course, `sql.` the SQL course, and `git.`, `http.`,
   `test.` and `rev.` the Git and diffs, HTTP and APIs, Testing and Review courses. The pattern is the same in all five schemas that
   carry concept IDs (question, concept-map, study, study-manifest, exam). No other prefix is accepted; a further course adds its own
   prefix by a schema change of its own. A namespace being valid does not make it free for everyone: the export lets a course use
   only its own namespace and the ones it was given (see `validator/EXPORT.md`), and accepting a namespace approves no concept,
   record, question or course.
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
