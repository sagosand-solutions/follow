# Bounded JSON inputs

The file-loading CLI and exporter share reviewed code constants in `core.py`:

| Policy | Maximum |
|---|---:|
| UTF-8 bytes per JSON file | 262,144 (256 KiB) |
| Question input entries per invocation | 512 |
| Actual question bytes per invocation | 16,777,216 (16 MiB) |
| Nested JSON containers | 32 |

There is no content field, environment setting or command-line flag that raises
or disables these limits. Changes require reviewed code changes. Each local
schema, concept map and course file has its own per-file/depth cap. Internal
schema rereads are not counted as additional questions. Repeated question paths
count again toward both question count and actual question bytes; duplicate
question IDs are still rejected independently.

`load_json` reads raw bytes, not an unbounded text stream or a stat-based size
estimate. Unbuffered reads stop at the per-file cap plus one detection byte,
including on short reads. Question budgets charge each actual chunk read, even
if a later read fails. The aggregate byte check precedes UTF-8 decoding and JSON
parsing of the crossing document. To detect overflow, actual reads may exceed
the aggregate allowed total by at most one bounded offending file; no more
question documents are read after that aggregate overflow. Budgets are local to
each CLI/export invocation, not shared across calls in one process.

A linear lexical preflight checks depth before recursive JSON parsing or schema
traversal. Objects and arrays each add one container level; a root container is
depth 1, a scalar is depth 0. Braces/brackets inside strings, escaped quotes and
escaped backslashes do not add depth. This is **not** a replacement JSON parser:
the standard library still checks JSON syntax, and the existing duplicate-key,
finite-number and strict UTF-8 policies remain. Parser RecursionError/MemoryError
conditions become controlled input-limit failures; recursion limits are not
increased.

The CLI checks the input count before reading question documents or consulting a
runner. Export directory scanning is incremental and stops at the 513th matching
`.json` entry, then sorts only an accepted bounded set. Directory entries that do
not match `.json` are not question inputs. Empty/missing export corpora remain
setup errors rather than silently exporting an empty site.

Generated index/map payloads must also fit the per-file byte cap before either
output is staged. Pretty-printing can expand an otherwise valid compact input
past the cap; export reports `input-limit` / exit 1 rather than staging either
output. `export --check` compares existing outputs using the same bounded byte
reader, without parsing those generated files. No output is written or repaired
by `--check`. Normal export can replace an old oversized output without first
reading it, provided both newly generated payloads fit the cap. Invalid/over-limit inputs prevent **all** snippet runs
and normal export replacements, preserving prior output bytes/mtimes. Existing
per-file atomicity limitations are described in [EXPORT.md](EXPORT.md).

Limit failures have stable issue code `input-limit`, an identifying file/path,
and a message naming `file-bytes`, `question-count`, `question-bytes`,
`json-depth`, or `json-parser-resource`. They return exit **1**. Already observed
I/O/setup failures retain exit **2** precedence. The CLI's JSON report envelope
is unchanged. Python callers can catch `InputLimitError` (a `ContentError`
subclass, exported by the package); its `file` attribute identifies the input.

## Security scope

These are input-processing bounds, **not a sandbox** and not a claim of bounded
total CPU/RSS/I/O time for every parser, schema, directory or filesystem workload.
They scope file-loading CLI/export inputs; APIs accepting already-created Python
objects do not protect against hostile in-process callers. A caller already
running Python can alter the program or its constants.

No snippet-execution policy, runner resource limit or isolation claim changes.
Do not execute arbitrary contributor Python, test suites or a modified validator
just because JSON inputs are bounded. A trusted-base-SHA CI workflow and a hardened
isolated runner remain separate security gates, not implemented by these caps.
The trusted-local runner remains for explicitly inspected, trusted code only.
