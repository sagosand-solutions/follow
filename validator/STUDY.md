# Study notes: static contract and manifest core

This is the deliberately small first slice of the study content model. It adds
a plain-text study-note contract, static (non-executing) validation for one
note, and the deterministic core that builds the availability manifest from
explicitly approved notes.

**This is not the publication pipeline.** The module never executes code, never
fetches a URL and never writes or exports a file. Validate-all-before-write,
output caps, per-file atomicity, `--check` and the public `data/study/index.json`
staging belong to a later task that wires this core into the existing exporter.

## Files

| Path | Role |
|---|---|
| `schema/study.schema.json` | One study note (envelope, scope, limits). |
| `schema/study-manifest.schema.json` | Generated availability manifest. |
| `validator/study.py` | `validate_study`, `build_manifest` and a read-only CLI. |

## Note contract (v0.1)

Common fields: `schema_version` (`"0.1"`), `language` (`"python"`), `locale`
(`"en"`), `title` (1–120), `summary` (1–280), `sections` (1–12), `sources`
(1–20), `status` (`draft`/`reviewed`), `author` (`{name}` 1–120), `license`
(`"CC-BY-SA-4.0"`). **Exactly one** of `concept`, `lesson` or `question` is
present and is the note identity; `related` is an optional list of up to 64
existing concept IDs. `additionalProperties` is `false` at every object level.

`sections[]`: `id` (1–64, `[a-z][a-z0-9-]{0,63}`), `heading` (1–120), `body`
(1–6000), `deeper`, optional `formula` (≤1000) and optional `example`. Prose
keeps literal `<`, newlines and backticks; the renderer escapes it and the
schema does **not** forbid such markup.

`example`: `language` (`"python"`), `caption` (1–280), `code` (1–8000),
`execution_policy`. In this delivery the only policy is
`{"mode": "static-only"}`; trusted fixtures, other languages, `cases`/`setup`
and any extra field are rejected. There is **no schema default** that silently
injects an execution policy — fixtures must state `static-only` explicitly.

`sources[]`: `id`, `title` (1–200), `url` (1–2048), `locator` (1–300, document
version and section) and `accessed_on` (`YYYY-MM-DD`). URLs are validated
statically: `https` only, a real hostname, no credentials, no whitespace or
control characters, no percent-encoded control characters. No URL is ever
fetched. `accessed_on` must be a real calendar date.

The authored `status` is **not** proof of editorial approval.

## API

```python
validate_study(note, *, concept_ids, lesson_ids, question_ids, schema_dir=None) -> list[Issue]
build_manifest(notes_root, approvals, *, concept_ids, lesson_ids, question_ids, schema_dir=None) -> dict
```

`validate_study` runs the schema, whitespace-only text checks, target
existence, `related` existence, duplicate section/source ids and the URL/date
semantics. It reuses `core.Issue` and the core schema helper; nothing in
`core.py` changes for references.

`build_manifest` returns, for one invocation:

```json
{"data_version": 1, "notes": [{"scope": "concept", "target_id": "py.complexity",
  "path": "data/study/py-complexity.json", "sha256": "…"}]}
```

`notes_root` is the directory that corresponds to `data/study`. `approvals` is a
manually prepared list of `{path, sha256}` with manifest-relative
`data/study/....json` paths, derived from externally reviewed, frozen bytes —
never from the authored `status`. For each approval the core verifies a unique
canonical path, in-root resolution with no symlink in any component, a bounded
read of the exact bytes, a hash match, strict parsing of those same bytes,
schema/refs/`reviewed`, and unique `scope:target` and paths. **All entries must
be valid before anything is returned**; a failure raises `ContentError` or
`SetupError` and never yields a partial manifest. Output is sorted by
`(scope, target_id)` and hashed over bytes, not re-serialized JSON. An empty
`approvals` list yields `{"data_version": 1, "notes": []}`. A reviewed note
without an approval is never published or auto-included.

This manifest is the **public** static study manifest (`data/study/...`): it
accepts only free content. An otherwise valid, correctly approved, `reviewed`
note whose `license` is not `"CC-BY-SA-4.0"` (for example
`LicenseRef-Proprietary`) raises `ContentError` before any manifest is
returned, so a mix of free and non-free notes is rejected as a whole. Static
`validate_study` and the `python -m validator.study` CLI accept **both** license
values. A future private manifest needs its own contract, not an override here.

## Byte-to-value seam

`core.parse_json_bytes(raw, path)` was extracted from `load_json`; `load_json`
now reads bounded bytes and delegates. The manifest builder hashes exactly the
bytes it hashes with SHA-256 **and** the bytes it parses, so no second read can
observe different content. All size, depth, UTF-8, duplicate-key, finite-number
and parser-resource gates are unchanged.

## Limits

Reused reviewed constants: 262,144 bytes per JSON file, 512 manifest entries,
16,777,216 actual bytes per invocation (`core.QuestionBudget`), 32 nested JSON
containers. Budgets are fresh per call. There is no unbounded glob; only the
listed approvals are read.

## CLI

```
python -m validator.study NOTE --map MAP --course COURSE --index INDEX
```

Static validation of one note under the reference sets derived from the given
map/course/index. Exit `0` valid, `1` content error, `2` setup error. There is
no write/export function and no verify flag.

## Security scope

Static validation and a read-only manifest core are **not** a sandbox. Nothing
here executes contributor code, installs anything, or fetches the network. The
trusted-local runner and the hardened isolation work are separate gates.
