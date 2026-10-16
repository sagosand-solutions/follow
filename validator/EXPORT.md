# Static data export

The exporter and course schema are MIT licensed; see
[`../LICENSES/MIT.txt`](../LICENSES/MIT.txt). Authored questions and course
prose remain separately CC BY-SA 4.0 licensed.

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
- `schema/concepts/python-v0.json`
- `public/data/questions/*.json`
- `public/data/courses/python.json`

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
  "data_version": 1,
  "onboarding_question": "first-editorial-question",
  "questions": [
    {"id": "first-editorial-question", "type": "output", "level": "beginner", "difficulty": 0.2, "concepts": ["py.values"]}
  ]
}
```

Index question entries are sorted by stable question ID and contain only the
five shown fields. `public/data/concepts.json` contains the **entire** canonical
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
