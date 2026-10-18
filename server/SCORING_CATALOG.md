# Frozen scoring-catalog builder

`server/scoring_catalog_build.py` builds one frozen, reproducible scoring catalog
per family (`official`/`free`) from an **explicit** release selection over an
existing content manifest. It never scans directories and never guesses a course,
product, premium status or prior answer exposure from a filename.

AGPL-3.0-or-later, like the rest of `server/`. The builder is a build-time tool:
runtime loading and HTTP code must never import it, and this module never imports
runtime/HTTP code.

Each selected question is validated against the official question JSON Schema
(`schema/question.schema.json`) plus the static score rule before anything is
written. This needs the `jsonschema` library, which is pinned in
`validator/requirements.txt`; install the builder environment with
`python -m pip install -r validator/requirements.txt` (the offline builder
dependency is deliberately not added to the runtime `server/requirements.txt`).
The tool fails closed — a build error, exit `2`, never a silent skip — if the
library or the schema is missing or unreadable.

## Inputs

The content root and manifest are the existing private `ContentCatalog` inputs
(`entries` of `kind`/`id`/`product_id`/`path`/`sha256`). Two explicit JSON files
complete the release:

`release.json` — the frozen pool:

```json
{
  "schema_version": 1,
  "catalog_version": "rel-2026-10-18",
  "rules_version": "r2",
  "family": "official",
  "courses": [
    {"course_id": "python",
     "questions": [{"question_id": "py-example", "product_id": "python-lifetime",
                    "previously_exposed": false}]}
  ]
}
```

Every question, in BOTH families, must carry an explicit `previously_exposed` attestation
from content review; a `true` value is rejected (a known-exposed question may not
enter a protected pool), and a missing value is rejected rather than assumed
safe. The builder never guesses exposure from a filename or hash, so it can never
promise that an unknown leak does not exist — it only honours the reviewed
attestation. A `free`-list pool is protected too: its questions live in a private
manifest, carry the proprietary licence while they rank, and only become public
samples through the pool lifecycle (`server.pool free`) after a separate licence decision.

`exposures.json` — the reviewed exposure map (never automatic answer detection):

```json
{
  "schema_version": 1,
  "exposures": [
    {"kind": "question", "id": "py-example",
     "content_sha256": "<64 hex>", "affected_question_ids": ["py-example"]}
  ]
}
```

## CLI

Run from the repository root (the module lives in the `server` package):

```
python -m server.scoring_catalog_build \
  --content-root <private content root> \
  --manifest <manifest.json> \
  --release release.json \
  --exposures exposures.json \
  --out build/scoring-official.json
```

`--check` builds in memory and compares `--out` byte-for-byte without writing;
exit `0` means equal, `1` means drift. A build error exits `2`.

`--list-unexposed` makes the build report the manifest entries that have no
exposure row (see below); the count is always noted on stderr when non-zero.

`--question-schema` overrides the schema path. It is a **trusted build input**:
the operator must point it at the official question contract. It is not a way to
skip or weaken validation, so a build that ships an artifact should use the
bundled `schema/question.schema.json` and record the override in review.

Option ids must be unique within a question. The schema's `uniqueItems` compares
whole option objects, so it does not catch two options that differ only by
text/score while sharing one id; the builder rejects that separately, because the
scorer resolves an option by its first id match.

## Output artifact

The artifact format: `schema_version`, `catalog_version`, `rules_version`,
`family`,
`courses:[{course_id,N_c,questions:[{question_id,product_id,content_sha256}]}]`,
`exposures:[{kind,id,content_sha256,affected_question_ids}]`. Canonical UTF-8
JSON with sorted keys and sorted arrays, so input file/manifest order cannot
change the bytes.

Only `rules_version` `r2` (+2/+1/0/−1, denominator 2) may be frozen into a new
catalog. The historical `r1` (+4/+2/0/−1, denominator 4) and any unknown value
are rejected: reinterpreting r1's quarter points is forbidden. The builder does
not score.

## Fail-closed rules

- A selected question must exist in the manifest; its `product_id` binding must
  not contradict the manifest; an `official` question must carry a product, the
  proprietary license and an explicit `previously_exposed` attestation, and so must a `free`
  question (its product may be null). The public sample bank is never a pool, and a
  question attested as previously exposed is refused in either family.
- Question ids are unique across courses, `N_c` is the counted pool size, and a
  question's JSON `id` must equal the manifest id.
- Selected questions are read only through the hash-checked catalog (path/symlink
  safety included); a changed file or a wrong hash is rejected. A question that
  is not a standard-profile question (a legacy 0.1 document, or 0.2 with `answer_profile: standard`, five to eight options;
  the four-option `beginner-practice` profile, an unknown version or profile and a profile on 0.1 are refused with a clear
  error before anything is written, and before the schema, so a schema override cannot admit one) or
  does not satisfy the official schema or the static score rule (exactly one full,
  one half and at least three zero-credit options; booleans are not scores) is
  rejected before anything is written, so an invalid pool never yields an artifact.
- Every `(kind,id)` exposure row must exist in the manifest with a matching hash,
  affected ids must belong to the same pool, and every pool question needs its own
  exposure row containing its own id. An empty list is an explicit reviewed-safe
  statement; a missing row is not.
- A `course_id` must match the runtime catalog pattern `[a-z][a-z0-9-]{0,63}`, so
  the builder cannot freeze a course the runtime would refuse to load.
- The builder only governs the selected pool; other manifest material
  (`exam`/`study`/unselected questions) that has no exposure row is left to the
  runtime's default-deny. This is reported (count on stderr, ids with
  `--list-unexposed`) but never written into the artifact, and it is not a claim
  that the exposure map is a complete leak inventory.
- Writing is atomic and refuses to reuse a `catalog_version` with a different
  pool: a changed pool needs a new release version and a separate migration
  decision. A failed build leaves any previous artifact untouched.

Messages and the artifact never contain prompts, options, feedback, solutions,
source paths or raw secrets.

## Tests

`server/tests/test_scoring_catalog_build.py` runs on synthetic content only and
implements no private checkout.
