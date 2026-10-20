# Python question validator

Python 3.11+; code and synthetic unit tests are MIT licensed (see
[`../LICENSES/MIT.txt`](../LICENSES/MIT.txt)). Instructional question examples
are separately CC BY-SA 4.0 licensed.

## Install and check

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r validator/requirements.txt
python3 -m unittest discover -s validator/tests -v
python3 -m validator content/examples/independent-if.json
python3 -m validator --format json content/examples/independent-if.json
```

`jsonschema>=4.18,<5` is the only direct runtime dependency. It brings its own
transitive dependencies. Schema files and the default concept map resolve from
the package's repository, not the working directory. Explicit input paths are
relative to your working directory. There is no schema downloading or snippet
execution in default validation. Local schemas must accompany the package.

Checking questions of a course: the CLI validates against ONE concept map, the Python one by default. For another course
pass its vocabulary, for example:

```sh
python3 -m validator --concept-map schema/concepts/production-v0.json public/data/questions/prod-timeout-does-not-cancel-the-work.json
python3 -m validator --concept-map schema/concepts/sql-v0.json public/data/questions/sql-equals-null-matches-nothing.json
```

CI does not run the CLI over all questions any more: `python -m validator export --check` is the gate. It validates every
public question against the vocabulary of the course that lists it with the same `validate_question` the CLI calls, and adds
what the CLI cannot see: the filename, the public licence, duplicate and orphan questions, the course prefix rules and that
the derived files are up to date. See [EXPORT.md](EXPORT.md).

Static checks cover structure, finite numbers, scoring shape/counts, identifiers,
references, nonblank prose/source, line ranges and the concept prerequisite DAG.
Strict JSON loading rejects duplicate keys and NaN/Infinity (including overflowing
JSON exponents). Syntax failures include filename/line/column; duplicate-key and
nonfinite errors identify the JSON document because Python's object hooks do not
supply source offsets. Issue paths use RFC 6901 JSON pointers; the empty pointer
means the entire document. Output ordering is deterministic.

## Bounded file inputs

CLI/export inputs share fixed limits: 256 KiB per JSON file, 512 question inputs,
16 MiB actual question bytes per invocation, and 32 nested containers. Count,
byte and lexical depth checks happen before question parsing/execution; limit
failures are `input-limit` / exit 1. See [INPUT_LIMITS.md](INPUT_LIMITS.md) for
exact boundary semantics, scope and the explicit non-sandbox limitations.

## Execution: trusted code only

**The local runner is NOT a sandbox. Never use it for untrusted contributions,
pull requests, downloaded questions or other code you have not personally
inspected and trust. CI must remain static-only until a hardened runner exists.**
Local code can access the host filesystem and network. No AST filter can fix
that. This component does not install Docker, access a container socket, pull
images, or implement a hardened runner.

Only after inspecting your own code:

```sh
python3 -m validator --verify --runner trusted-local --allow-trusted-code \
  content/examples/independent-if.json
```

All three execution flags are required. The trust flag alone never enables
execution. Every file must pass static checks before *any* snippet is run.
Execution refuses unsupported platforms, root, or missing required local resource
controls. Each case uses a fresh temporary directory and `python -I` process,
UTF-8 stdin, no shell, no inherited user environment, a wall-clock deadline and
bounded combined output while reading. Output must be UTF-8. The process group
is killed on every exit and temporary files are removed. Trusted code must not
escape the process group; this is not protection against hostile descendants.

Defaults controlled by the validator, never by content:

- wall time: 2 seconds;
- address space: 128 MiB;
- combined raw stdout/stderr: 65,536 bytes;
- POSIX NPROC: 16, **per real user, not per invocation**;
- CPU: wall time rounded up to whole seconds; file-size limit: output cap.

POSIX limits are defense-in-depth, not isolation. Existing per-user process counts
can prevent a trusted snippet from spawning even one child. Hard system limits
can be lower than the requested limits. Python/resource failures are conservative:
a `MemoryError` diagnostic, including deliberately raised ones, is not accepted
as a successful expected crash; common resource-related OSError diagnostics are
also refused. Memory classification is best-effort, not a trustworthy peak-memory
measurement. Signal exits and all resource/infrastructure failures fail verification.

`Runner.run(code, stdin, Limits) -> RunResult` is an injectable boundary for a
future hardened adapter and test doubles. See `runner.py` for statuses and
truthful capability metadata. A hardened adapter must independently enforce no
network, no host mounts/secrets, and effective isolation. Trusted-local reports
`trusted-code-only-no-network-isolation`, never hardened capabilities.

## Reports and exit codes

JSON mode emits exactly one stdout object:

```json
{"valid": true, "execution": "not-requested", "questions": 1, "issues": [], "runner": null,
 "executed_cases": 0, "text_only_questions": 0, "artifacts": 0}
```

`questions` counts successfully parsed question documents, including structurally
invalid ones. Each issue has `file`, `code`, `path` and `message`. `execution` is
`not-requested`, `passed`, `failed`, `unavailable` (also used when requested
execution is skipped because static checks failed) or `not-applicable` (`--verify`
on input with no code to run: text artifacts are never executed, so a text-only
question never earns `passed`). `executed_cases` counts the cases that really ran,
`text_only_questions` the questions that had none to run, and `artifacts` the text
artifacts checked statically (hash, limits, URL policy; the fixture bytes are
verified by the export, not by this command). Runner metadata, when
available, has `name`, `python_version`, and `isolation`. Trust warnings go only
to stderr; snippet output never appears outside comparison results.

Exit 0: requested checks passed. Exit 1: invalid JSON/content/schema/semantics or
observed results. Exit 2: command-line, dependency, schema setup, I/O, unavailable
runner or runtime resource/infrastructure failure. Exit 2 takes precedence.

**Neither static validation nor matching observed execution proves the chosen
answer, genuine half credit, general equivalence, complexity, pedagogical quality,
human review, or publication approval.** These still require editorial review.

Unit tests use temporary synthetic structural schemas to exercise semantics
independently. Integration tests additionally use the shipped schemas, vocabulary,
and instructional example. They skip when the contract files are absent; an
alternate checkout can be selected with `VALIDATOR_INTEGRATION_ROOT`.
The example execution test is skipped unless `VALIDATOR_RUN_TRUSTED_EXAMPLE=1`
is set after inspecting the current example code. Do not enable this for PRs.
Local smoke tests execute only short self-written print/stdin/exception/timeout/
output/cleanup snippets, never adversarial probes.
