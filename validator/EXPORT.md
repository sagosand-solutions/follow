# Static data export

The exporter and course schema are MIT licensed; see
[`../LICENSES/MIT.txt`](../LICENSES/MIT.txt). The exported free public questions
and course prose remain CC BY-SA 4.0 licensed; premium content marked
`"LicenseRef-Proprietary"` is never exported.

**Public export license gate (fail closed).** `public/data/questions` may contain
only content whose `license` is `"CC-BY-SA-4.0"`. A question that is otherwise
valid but carries any other value (for example `"LicenseRef-Proprietary"`) is
reported as the blocking issue `non-public-license` at `/license`. Export exits
`1` and writes no output; the same applies to `--check`. Mixed free and
non-free input is rejected as a whole, and there is no flag to allow private
content. Static question validation still accepts both license values.

After installing `validator/requirements.txt` in a virtual environment:

```sh
python3 -m validator export --check
python3 -m validator export
python3 -m validator export --root /path/to/repository --check
python3 -m unittest discover -s validator/tests -v
```

`export.main(argv) -> int` is also callable from Python. Export is **static-only**:
it never runs snippets, never enables a runner and does not certify observed
execution, human review, or publication approval. Review content separately
before publishing generated data.

## Inputs

The default root comes from the installed module's repository, not the current
working directory. `--root` selects an independent repository-shaped directory.
All paths below are relative to that root:

- `schema/question.schema.json`
- `schema/concept-map.schema.json`
- `schema/course.schema.json`
- `schema/catalog.schema.json` and `public/data/catalog.json` (when the catalog exists, see Courses below)
- `schema/concepts/<course>-v0.json` (python, production, sql)
- `public/data/questions/*.json` (one flat directory; ids are globally unique)
- `public/data/courses/<course>.json`

[Shared input bounds](INPUT_LIMITS.md) apply: 256 KiB per JSON file, 512 question
entries, 16 MiB actual question bytes and 32 nested containers. Directory scanning
stops at the first over-limit matching entry, without materializing an unbounded
glob. Limit failures are `input-limit` / exit 1 and never replace outputs.
Generated payloads/comparison reads also respect the per-file byte cap.

Strict JSON loading rejects duplicate keys and nonfinite numbers. Schemas are
local, with no network retrieval. Core structural and semantic validators check
the map and every question. The optional `schema_dir` keyword on
`validate_map`/`validate_question` selects these local schemas without changing
process-global defaults. Existing callers keep the original default behavior.

Each question filename must be `<question.id>.json`; IDs must be unique. The
question corpus cannot be absent or empty. Every question must belong to exactly
one lesson and match that course level; unknown or orphan questions fail export.

## Course contract

The course has required `id: "python"`, `language: "python"`, nonblank `title`,
and `levels` (one to three entries). A level has required `level`
(`beginner`, `intermediate`, `advanced`), nonblank `title` and nonempty `lessons`.
Levels occur once each in increasing order; unused tiers may be omitted.

A lesson has required stable `id`, nonblank `title`, `builds_on` (unique lesson
IDs, possibly empty) and `questions` (nonempty unique question IDs). Lesson IDs
are globally unique. Dependencies must exist, cannot refer to the same lesson,
and cannot form a cycle. All objects reject unknown fields. IDs use the question
contract's local/question ID rules, including rejecting trailing whitespace.
There is no optional course onboarding override in this version.

A lesson may include an optional plain-text `summary` of 1–200 Unicode code
points. It must be a single paragraph without C0/C1 controls or Unicode line
separators; whitespace-only values are invalid. Omit the field when absent.
Summaries are authored English course content, not i18n keys or Markdown, and
remain in the source course file. Export validates but does not rewrite the
course file or add summaries to the generated question index or concept map.

The first question of the first lesson in the first course level becomes
`onboarding_question`. It must exist and be beginner-level. Question order inside
the course remains editorial; it is not sorted or rewritten.

## Outputs and determinism

`public/data/index.json` contains:

```json
{
  "data_version": 2,
  "onboarding_question": "first-editorial-question",
  "questions": [
    {"id": "first-editorial-question", "type": "output", "level": "beginner", "difficulty": 0.2, "concepts": ["py.values"], "answer_profile": "standard"}
  ]
}
```

Index question entries are sorted by stable question ID and contain only the
six shown fields. `answer_profile` is always present and explicit: the question's own
profile (`standard` or `beginner-practice`) for a 0.2 document, and `standard` for a legacy 0.1
document (that is what the 0.1 contract means). Index `data_version` 2 introduced it; a client that
cannot read a version-2 index with a valid profile on every entry must not compute scoring pools
from it (see the web client), and nothing is inferred from the id, level or option count. `public/data/concepts.json` contains the **entire** canonical
map with unchanged JSON semantics. Both outputs use UTF-8, two-space indentation,
sorted object keys, literal Unicode, and one trailing newline. No timestamp,
absolute path or machine-specific metadata is generated. `data_version` versions
the format, not the content; consumers still need normal cache revalidation or a
separate release/content digest.

`--check` compares exact bytes, including key order, indentation and newline.
Missing or noncanonical generated files fail the check; nothing is written,
created or repaired. Missing required *inputs* are setup errors rather than
an empty-site export. All content validation completes before normal export
stages either output. Temporary files are staged in the output directory,
flushed, then atomically replaced **per file** and made world-readable (0644).
Input files are never rewritten. Validation or staging failures preserve existing
outputs; a failure during replacement can leave one new and one old output,
since two files are not a transactional filesystem unit. Rerun export after
resolving the I/O failure. Temporary files are cleaned on normal error paths.

Symlinks anywhere below the selected root are refused for all fixed input/output
paths and their parent directories, even links pointing inside the root. This
conservative policy includes dangling links and prevents following links outside
the root. The selected root itself is canonicalized. The exporter assumes no
concurrent hostile mutation of directory entries and no concurrent writers;
it is not a race-proof filesystem sandbox or a corpus locking mechanism.

## Exit codes

- **0:** all inputs valid and outputs written/current.
- **1:** invalid JSON/content/schema/semantics or missing/stale generated bytes
  with `--check`.
- **2:** CLI, I/O, dependency, unavailable/invalid local schema, empty corpus or
  other setup failure. Setup failures take precedence when aggregated.

Diagnostics include the file, issue code, JSON pointer and message. The final
status explicitly says execution was skipped and no publication approval is
implied. There is no additional runtime dependency beyond the core validator.

## Courses (catalog mode)

When `public/data/catalog.json` exists (schema `schema/catalog.schema.json`) the export covers every course it lists
(python, production, sql and any further course whose id follows the rule `[a-z0-9]+(-[a-z0-9]+)*`, at most 40 characters; at most
8 courses); without a catalog it is the original single Python course, unchanged.

- The catalog entry names the course vocabulary (`concept_map`, required, no fallback): `schema/concepts/<id>-v0.json` for python,
  production and sql, and for every other course (a beginner course) exactly `schema/concepts/python-v0.json`, the shared Python map
  (`catalog-map` otherwise; the derived `concepts-<id>.json` is then a copy of it, regenerated and checked like the others). The course file is
  `public/data/courses/<id>.json` (its `id` must equal the catalog id), and `free_questions` and `start_question` must match
  the course file (`catalog-count`, `catalog-start`). `premium.questions` is set by hand (the private repository is never read).
- Every question is validated against the vocabulary of the **one** course that lists it. A question in two courses
  (`question-in-two-courses`) or in none (`orphan-question`), a course without a vocabulary or course file, and a duplicate
  or non-python catalog are errors.
- Prefix rule (`course-prefix`): python may use `py.` only; production `prod.` and `py.`; sql `sql.` and `py.`; `git-diffs` `git.` and `py.`;
  `http-api` `http.` and `py.`; `testing` `test.` and `py.`; `review-ai-changes` `rev.`, `py.`, `git.` and `test.` (it builds on Git and Testing, as
  production and sql carry `py.` entries). Each of these courses has a vocabulary of its own, `schema/concepts/<id>-v0.json`. Any other course
  (a beginner course, on the shared Python map) `py.` only; the table is `COURSE_PREFIXES` in `validator/export.py`; for vocabulary ids, prerequisites, misconception references and a question's concepts and
  misconceptions.
- Labels for the home page, all optional: `audience` (`beginner` or `experienced`) says who a course is for, and `path_step`
  (an integer 1-3) puts a course on the beginner path. A step belongs to one course (`duplicate-path-step`). `premium` is
  optional too, so a free beginner course states no promise. Neither label changes any derived file. The real catalog marks
  python `path_step: 3` (no audience) and production and sql `experienced`.
- Lesson ids are unique across ALL courses (`lesson-in-two-courses`): the client finds a lesson's course by its id, so production and sql lessons carry their course prefix (e.g. `prod-timeouts`, `sql-null-logic`).
- The first-question-is-beginner rule applies to the python course and to every course with `audience: beginner`.
- Optional lesson fields of beginner courses (`schema/course.schema.json`): `practice_summary` (plain text, at most 80
  characters) and `introductions` (1-8 cards `{id, before_question_ids, term, explanation, example}`, term at most 60 and
  explanation and example at most 240 characters; no control character, no space at either end). The exporter adds what the
  schema cannot say: every `before_question_ids` entry is a question of the same lesson (`introduction-question`), an
  introduction id is used once per lesson (`duplicate-introduction`), and lengths and edge spaces are counted the way the
  browser counts them, in UTF-16 units and with JavaScript's idea of a space (`lesson-text`). The fields are carried
  unchanged and shown as text, never as markup.
- Derived files: python keeps `index.json` and `concepts.json` **byte for byte**; the other courses write
  `index-<id>.json` and `concepts-<id>.json`. `--check` compares all of them.
- Static only, no code is executed; `validator.trace --check` already covers every question in the flat directory.

## Text artifacts (schema 0.3)

A 0.3 question may carry `artifacts`: display-only text (see `schema/README.md`, "Text artifacts"). The export validates them with
the question and fails closed:

- `artifact-hash`: `sha256` is not the SHA-256 of the UTF-8 bytes of `text` exactly as stored.
- `artifact-binding`: `provenance.fixture_output_sha256` is not the same hash, or the bytes of the fixture output file do not hash to it, so the
  shown text is not the fixture's output. The export reads `content/fixtures/artifacts/<fixture_id>.out` (fixed directory, the id is a
  name and never a path; opened by descriptor from the root down, no symlink on the way, a regular file only, at most 20000 bytes read); a missing or oversized file is `artifact-fixture`, and a symlink
  stops the export with a setup error.
- `artifact-limits` (more than 80 lines), `artifact-text` (not encodable as UTF-8), `artifact-id` (also a snippet id), `artifact-url`
  (a URL whose host is not reserved for tests).
- A text-only question is indexed like any other (profile included) but has no snippets, so `validator trace` generates no trace
  for it and `trace --check` reports a trace file for it as an orphan. Artifacts are never executed.
