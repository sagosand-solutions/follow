# Precomputed traces

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

`python3 -m validator trace --check` checks that every question has exactly one
trace entry per snippet, each source hash matches, and there are no orphan trace
files. **Check mode is static-only: it never executes code** and is suitable for
CI. The CI workflow is intentionally not changed by this feature.

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
