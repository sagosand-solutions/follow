# Precomputed traces (schema 0.2)

Generate step traces for question snippets from the repository root:

```sh
python3 -m validator trace --runner trusted-local --allow-trusted-code
```

You may provide one or more question JSON paths instead of generating the whole
corpus. Output is written to `public/data/traces/<question-id>.json`. Generation
uses the first verification case associated with each snippet (or empty stdin if
there is none), captures the observed stdout, and checks it against that case's
expected stdout when present. Trace JSON is validated against
[`../schema/trace.schema.json`](../schema/trace.schema.json) before it is written.

Each trace entry records the exact verification `case_id`, its actual `stdin`,
and `stdin_sha256` (SHA-256 of UTF-8 stdin). The generator uses the first
verification case associated with each snippet. The checker verifies that the
case still exists for that same snippet and that its current stdin text and hash
match. Reordering cases does not change their identity. `--check` remains
static-only.

A `line` event means that `line` is the source line to run next; its locals are
observed before that line runs. `call`, `resume`, `yield`, `return`, `unwind`,
and `exception` use the source line where the event occurs. A generator's first
frame entry is `call`; a later entry is `resume` only after this frame was
observed pausing at `yield`. Normal completion is `return`; a frame exited while
an exception propagates is `unwind`. An `exception` event alone does not mean
that an exception escaped: it may be caught or suppressed by `finally`.

`stdout_len` counts Unicode code points in the captured stdout so far (Python
string length / JavaScript `Array.from` units), not UTF-8 bytes or UTF-16 code
units. If `truncated` is true, `truncated_at_step` is the number of recorded
steps; a player must state **“Trace stops here (step limit)”**. It is not a
claim that the program ended there. The step cap remains 300.

`python3 -m validator trace --check` checks every question/snippet pair, source
hash, case identity, stdin text/hash, trace schema, and orphan files. **Check
mode is static-only: it never executes code** and is suitable for CI. The CI
workflow is intentionally not changed by this feature.

## Security boundary

**Trace generation executes trusted code and is not a sandbox.** It requires both
`--runner trusted-local` and `--allow-trusted-code`; inspect and trust every
snippet before running it. The local runner has no network or host-filesystem
isolation. Never run generation on untrusted contributions, pull requests, or
code you have not personally reviewed. Generation is never run in CI. The
`--check` mode does not execute snippets.

Each snippet runs under the existing trusted-local runner limits. A timeout,
output cap, or runner failure aborts generation without writing a partial batch.
Traces are explanatory playback data, not proof of correctness or editorial
approval.
