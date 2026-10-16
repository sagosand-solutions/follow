# Chapter exams and local attempts (schema 0.1)

Validate a chapter examination and, optionally, a local attempt from the
repository root:

```sh
python3 -m validator.exam content/exam/c01.json --map public/data/concepts.json
python3 -m validator.exam content/exam/c01.json --map public/data/concepts.json --attempt attempt.json
```

Exit codes are `0` valid, `1` invalid content **or an input-limit failure** (for
example a file above the bounded-load limit), and `2` setup or dependency failure
(for example a missing or unreadable file). The command is **static-only**: it never executes, imports
or replays contributor code, never opens a process or the network, and never
writes output files. No model answer or rubric text is printed by the CLI.

Two schemas form the contract: [`../schema/exam.schema.json`](../schema/exam.schema.json)
for the examination and [`../schema/exam-attempt.schema.json`](../schema/exam-attempt.schema.json)
for the attempt. Every object rejects unknown properties, and text limits count
Unicode code points.

## Python API

```python
from validator.exam import validate_exam, validate_attempt, validate_attempt_transition

issues = validate_exam(exam, map_data, known_study_keys={"concept:py.functions"})
issues = validate_attempt(attempt, exam)
issues = validate_attempt_transition(previous_attempt, next_attempt, exam)
```

Each function returns an ordered list of `Issue` objects (empty means valid).
`validate_exam` needs the concept map so `concepts` can be resolved.

## Examination

An examination names exactly one `source` and between six and eight explanation
`parts`. Each part carries a three-point rubric (scores `0`, `1`, `2` in order),
canonical `py.*` concepts that exist in the map, and evidence anchors into the
source. At least one part must have purpose `flow` and at least one `risk`.

`source.execution_policy.mode` is fixed to `static-only`. Trusted fixtures,
executable cases, generated traces, generated inputs, other languages,
single-select choice, two-stage responses, line selection and ordering are **not
part of 0.1** and are refused structurally rather than half-implemented.

### Source hash and lines

`source.sha256` must be the UTF-8 SHA-256 of the exact `code`, with **no
whitespace or newline normalisation**. Code uses LF line feeds only, contains at
most 100 lines (a trailing LF does not add a line), and rejects carriage
returns, `U+0085`, `U+2028`, `U+2029` and every C0 control other than TAB and LF.
An all-whitespace program is rejected.

### Evidence anchors — single-source interpretation

An examination has one source, so `evidence.snippet_id` names that source and
must equal `source.id`; `evidence.source_hash` must equal `source.sha256`. Line
bounds must satisfy `1 <= start_line <= end_line <=` the source's line count.
Later multi-snippet formats may relax `snippet_id`; 0.1 does not.

### Study references

`study_refs` are `scope:id` strings with scope `concept`, `lesson` or
`question`. Whether a reference exists is a separate, editorial question. The
Python API answers it only from the explicit `known_study_keys` set the caller
passes; with the default empty set, only an empty `study_refs` list is accepted.
The CLI always uses that empty set, so a non-empty `study_refs` is invalid on the
command line until a reviewed study manifest is wired in.

## Attempts

An attempt binds to its examination through `exam_id`, `exam_version`,
`source_hash` and `rubric_version`, and repeats the exam's parts with the same
ids in the same order. Each part is `answered` (with non-empty `text`),
`unknown`, or `unanswered`; an unanswered or unknown part has `value: null` and
is not the same as a zero score. A whitespace-only answer is invalid, not zero.

`assisted: false` forbids any `assistance_events`; `assisted: true` with no
events is allowed (assistance was available but unused). Attempts store no score
and no lock fields.

## Transitions

`validate_attempt_transition` checks one in-place save step:

- the identity fields `attempt_id`, `exam_id`, `exam_version`, `source_hash` and
  `rubric_version` never change;
- `assistance_events` are an append-only prefix — entries cannot be edited,
  reordered or removed;
- `assisted` is monotonic `false -> true`;
- `submitted` and `abandoned` are terminal: only an identical, idempotent save
  is accepted;
- new assistance events require `assisted: true` and status `in_progress`, so
  help cannot be smuggled in during submission.
