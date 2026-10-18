# Frozen scoring-catalog builder

`server/scoring_catalog_build.py` builds one frozen, reproducible scoring catalog
per family (`official`/`free`) from an **explicit** release selection over an
existing content manifest. It never scans directories and never guesses a course,
product, premium status or prior answer exposure from a filename.

AGPL-3.0-or-later, like the rest of `server/`. The builder is a build-time tool:
runtime code (`server/scoring_catalog.py` and the 023 HTTP layer) must never
import it, and this module never imports runtime/HTTP code.

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

Every `official` question must carry an explicit `previously_exposed` attestation
from content review; a `true` value is rejected (a known-exposed question may not
enter a protected pool), and a missing value is rejected rather than assumed
safe. The builder never guesses exposure from a filename or hash, so it can never
promise that an unknown leak does not exist — it only honours the reviewed
attestation. `free` questions may omit the field.

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

## Output artifact

The frozen format from the 023 interface contract: `schema_version`,
`catalog_version`, `rules_version`, `family`,
`courses:[{course_id,N_c,questions:[{question_id,product_id,content_sha256}]}]`,
`exposures:[{kind,id,content_sha256,affected_question_ids}]`. Canonical UTF-8
JSON with sorted keys and sorted arrays, so input file/manifest order cannot
change the bytes.

Only `rules_version` `r2` (+2/+1/0/−1, denominator 2) may be frozen into a new
catalog. The historical `r1` (+4/+2/0/−1, denominator 4) and any unknown value
are rejected: the 2026-10-18 decision forbids reinterpreting r1's quarter points.
The builder does not score.

## Fail-closed rules

- A selected question must exist in the manifest; its `product_id` binding must
  not contradict the manifest; an `official` question must carry a product, the
  premium license and an explicit `previously_exposed` attestation, a `free`
  question the public license. Free/public and premium are never mixed in one
  family, and a question attested as previously exposed is refused in `official`.
- Question ids are unique across courses, `N_c` is the counted pool size, and a
  question's JSON `id` must equal the manifest id.
- Selected questions are read only through the hash-checked catalog (path/symlink
  safety included); a changed file, a wrong hash or a bad question structure is
  rejected.
- Every `(kind,id)` exposure row must exist in the manifest with a matching hash,
  affected ids must belong to the same pool, and every pool question needs its own
  exposure row containing its own id. An empty list is an explicit reviewed-safe
  statement; a missing row is not.
- Writing is atomic and refuses to reuse a `catalog_version` with a different
  pool: a changed pool needs a new release version and a separate migration
  decision. A failed build leaves any previous artifact untouched.

Messages and the artifact never contain prompts, options, feedback, solutions,
source paths or raw secrets.

## Tests

`server/tests/test_scoring_catalog_build.py` runs on synthetic content only and
implements no private checkout.
