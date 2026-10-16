# Precomputed traces (schema 0.2; 0.3 opt-in)

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

## Schema 0.3 (opt-in; 0.2 stays the default)

`python3 -m validator trace --runner trusted-local --allow-trusted-code --trace-format 0.3 <question.json>`
writes schema 0.3 ([`../schema/trace-0.3.schema.json`](../schema/trace-0.3.schema.json)); without the flag the output is
byte-for-byte what 0.2 produced. `trace --check` (static, never executes code) accepts both versions per file and
validates each against its own schema; any other `schema_version` fails. Existing 0.2 traces are not regenerated.

Each step additionally records:

- `frame_id`: a deterministic counter within the trace (never a memory address). A `call` takes the next number, a
  `resume` keeps the frame's own number, `return`/`unwind` close it, and a number is never reused. This keeps two
  generator instances of the same function and depth apart. The generator holds per-frame state keyed on the frame
  object and releases it on return/unwind; a generator that never finishes stays until the end of the trace.
- `scope`: `module`, `function` or `class` (class body).
- `locals`: the variables of this frame's own scope; always `{}` in `module` scope, where the variables are globals.
- `globals`: the program's own global **data values** only, a separate namespace from `locals` (a shadowed name appears
  in both with its own value). Not included: dunder names, `__builtins__`, the instrumentation's names (the snippet runs
  in its own namespace), modules, and user functions or classes. Omitted functions/classes/modules are not counted.
- `locals_omitted` / `globals_omitted`: how many eligible names were left out by the budget.

Budget per step, shared by locals and globals (locals first, then globals; first names in definition order): at most
40 names per scope and 2 KiB of name+value bytes together, with each value rendered by the same bounded `short()` as
0.2 (80 characters, reprlib limits, no new repr/property calls). Over budget means a deterministic prefix plus the
omitted counts; the 300-step cap is unchanged. There is no alias or object-identity information: two names for one
object are two plain values, and a player must not claim they are or are not copies. "No global change" is stated only
by comparing the recorded `globals`, never inferred from a still local table.

Players read 0.2 by call depth as before and 0.3 by `frame_id`. Traces are explanatory playback data.
