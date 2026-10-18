# Server foundation (accounts v1, task 018)

Configuration, SQLite storage, repositories and private content-catalog loading
for the accounts backend. **This code is a local mock/test foundation.** It makes
no external calls, needs no real provider key and must not be treated as proof of
authentication or payment correctness.

Server code is AGPL-3.0-or-later; the validator and schema packages stay MIT.
Starlette/Uvicorn are declared for the later HTTP layer (019/020); nothing in
this task imports them, so the 018 tests run with the standard library alone.

## Interfaces (frozen before implementation, for 019/020)

Timestamps are UTC epoch seconds from an injected clock. IDs are opaque random
strings; a client-supplied id, role, price or time is never authoritative.
Secrets are injected through the environment, never read from a committed file.

### `server.config`

```python
load_config(env: Mapping[str, str]) -> Settings
```

`Settings` is a frozen dataclass: `app_env`, `public_origin`, `base_path`,
`db_path`, `content_root`, `trusted_proxies`, `limits`, `enabled_adapters`,
`session_idle_seconds`, `session_absolute_seconds`, `recent_auth_seconds`, and
secret fields (`csrf_key`, `session_secret`) excluded from `repr`. `Settings.redacted()`
returns the non-secret view for logs. `load_config` fails closed (raises `SetupError`)
on an unknown `APP_ENV`, a missing/weak secret, an unsafe production origin, a
mock/test adapter in production, or unset trusted proxies in production.

### `server.db`

```python
connect(db_path, *, busy_timeout_ms=5000, read_only=False) -> sqlite3.Connection
Database(db_path, *, clock).transaction(*, immediate=False)   # context manager
apply_migrations(conn) -> int
```

`transaction` guarantees commit or rollback and raises on a failed commit. Use
`immediate=True` (`BEGIN IMMEDIATE`) for compare-and-swap writes. `connect`
enables `foreign_keys` and `busy_timeout`, and WAL unless `read_only`.

### `server.models`

Storage-neutral frozen records and enums: `User`, `Identity`, `Session`,
`AuthTransaction`, `Product`, `Purchase`, `Entitlement`, `PaymentEvent`; enums
for user status, auth intent, purchase/entitlement/payment state. Errors:
`SetupError`, `ConflictError`, `ContentError`, `StorageError`, `NotFoundError`.

### `server.repositories`

`Repositories(database)` exposes `.users`, `.identities`, `.sessions`,
`.auth_transactions`, `.products`, `.purchases`, `.entitlements`,
`.payment_events`. Highlights used by 019/020:

- `identities.add(user_id, provider, issuer, subject)` — raises `ConflictError`
  on a duplicate `(provider, issuer, subject)`.
- `auth_transactions.consume_once(token_hash)` — atomic one-time CAS; concurrent
  callers get exactly one `True`.
- `purchases.create(...)` / `purchases.compare_and_swap(purchase_id, expected_revision, ...)`
  — unique `(provider, provider_purchase_id)` and revision-guarded updates.
- `entitlements.grant(...)` / `entitlements.revoke(...)` — idempotent by
  `UNIQUE(source_purchase_id, product_id)`; a refund of one purchase never
  removes another active entitlement.
- `payment_events.record(...)` — duplicate with the same digest is a no-op;
  the same `event_id` with a different digest is a conflict.

Repositories take their clock from `Database`; tests inject a mock clock and
never sleep.

### `server.content_catalog`

```python
ContentCatalog.load(content_root, manifest_path) -> ContentCatalog
catalog.lookup(kind, id) -> ContentEntry | None
catalog.read(entry) -> bytes
```

The manifest is read only from an explicit private root and lists
`{kind, id, product_id, path, sha256}`. Loading rejects duplicate `(kind, id)`,
non-matching ids, absolute or `..`/symlink/outside-root paths and bad hashes.
`lookup` returns an internal reference only, never a public URL. `read` re-checks
containment and the SHA-256 before returning bytes (fail closed). Serving it to a
user is 020's job, behind an entitlement check on every request.

## Local development runner (mock mode)

```sh
python -m server.dev [--port 8000] [--content-root DIR --manifest FILE] \
                     [--site-root public] [--api-path /follow/api] [--site-path /follow] [--db PATH]
```

Starts Uvicorn on `127.0.0.1` with `APP_ENV=development`, no adapters and a
throwaway SQLite database (a temporary file unless `--db` is given). The CSRF and
session keys are generated at start; the runner never reads a real secret from
the environment and **refuses to start when `APP_ENV=production`** (exit 2).

The API is mounted under `/follow/api` and the static site (default `public/`)
under `/follow/` on the same origin, so the Origin and CSRF checks work in a
browser; `/` redirects to the site. Open `http://127.0.0.1:<port>/follow/` — use
`127.0.0.1`, not `localhost`: the Origin check compares the configured origin
exactly, so a mutating call from `localhost` gets 403. Provider and payment flows
are disabled (503) until real adapters exist — this runner makes no external
call. To point the site at this server, set `accountsApi` in `public/js/config.js`
to the API path (e.g. `/follow/api`); it is `null` by default (fake client).
Tests: `server/tests/test_dev.py` (production refused, loopback host, mock app
answers a GET).

## Scoring (023, mock mode, default off)

Server-side grading, first-answer lock and leaderboards per `team`-internal spec
`scoring-leaderboard-v1.md` v1.1. Everything is behind flags that default to off:
`SCORING_ENABLED` (master) and `SCORING_ANONYMOUS_ENABLED`, `SCORING_CLAIM_ENABLED`,
`SCORING_PUBLISH_ENABLED` (each needs the master). `APP_ENV=production` with any of them
is a `SetupError`; with the flags off every scoring route answers 503 before it touches
the database, a cookie or a principal. Activation needs `Services.scoring`
(`ScoringServices(catalogs, question_source)`), a frozen read-only catalog artifact per
family loaded once at start (`scoring_catalog.load_catalog`); there is no build step and
no raw-content fallback at request time.

The free list is a protected pool too. Its questions come from a PRIVATE hash-checked manifest
(`SCORING_FREE_CONTENT_ROOT` and `SCORING_FREE_MANIFEST`, set together, never under the public
site), built with the same attestations as the official pool and kept under the proprietary
licence until the pool lifecycle releases them as free. An in-memory `{id: bytes}` source exists
only for tests and local development: it needs `SCORING_DEV_SOURCE=true` (which is a `SetupError`
in production and without `SCORING_ENABLED`) and the two sources exclude each other, so a missing
or changed private file stops start-up instead of falling back. Served material that reveals
ranked or retired questions (`/content/{kind}/{id}`) is released only when the reader has locked
every one of them, whichever family they belong to; paying for the product is not enough, and a
file without a reviewed exposure row stays closed.

Routes (`/scoring/anonymous`, `/scoring/questions/{id}/issue`, `/scoring/answers`,
`/scoring/me`, `/scoring/claim`, `/scoring/preferences`, `/leaderboards`): mutations need
the exact Origin and the CSRF token of the caller's own cookie. The first accepted answer
per principal and question is written with the consumed issue and the receipt in ONE
`BEGIN IMMEDIATE` transaction and can never be replaced (UNIQUE key plus triggers). Points
are exact fractions: +2/+1/0/-1 units (rules r2), course = 500 x units / N_c (r2: +2/+1/0/-1), total = mean over all K
catalog courses. A solution is returned only after commit and only when every question the
exposure map ties to that material is locked; the old `/content/{kind}/{id}` route uses the
same gate, and material with no reviewed exposure row is blocked, not assumed harmless.
Premium is access control, not DRM: shared answers, own code runs and several accounts
cannot be prevented, and the free list is explicitly not cheat-resistant.

### Question pool lifecycle and the operator tool

At start-up every scoring-catalog question is registered once as `ranked`. A question whose content changed under the same id, or whose
content equals another question's, stops the start-up: retire the old id and give the new content a new one. Only a `ranked`
question in a version that has not been frozen is issued; anything else answers `unavailable`.

Start-up also creates, once per catalog version, an immutable ranking version: the courses with their pool sizes and every member
question with its content hash, plus a digest of it all. A catalog that changed under the same version, or that holds a question
that is no longer ranked, stops the start-up; a retired question therefore means a new catalog version, never a silently smaller pool.
Only a question held with exactly this content by an open version is issued. Scores are exact: a total is 500 x T / D with
D = K x lcm(pool sizes), and the board's sort key is the zero-padded decimal of (T + D), so no floating point decides a rank.

Each accepted first attempt updates its standings in the same transaction as the answer: an account's rankable attempt adds to its
per-course units and re-writes its exact `total` and `course:<id>` rows for the `all` and the ISO-week window; an anonymous attempt
only feeds a private aggregate used for a hypothetical placement, and exposed, legacy or claimed attempts never score. A replay
changes nothing, and a failure writing standings rolls the whole answer back. A board's revision rises on every change a reader can
see (a published reader's answer, publishing, unpublishing, renaming, deletion, a rebuild, a freeze), which makes older cursors stale.

Reads (`/scoring/me`, `/leaderboards`) run in one read snapshot and add, never replace, fields: `board_revision` (text), `score_display` (exact
two-decimal string, ties away from zero), `scope_score` (null when the scope holds no attempt), `moderation`, and per attempt
`rank_eligible` (true only for an account's attempt that is a member of this version). The published list is filtered before ranking
and paging; equal scores share a rank and ties are ordered by fewer answers, then earlier last answer, then an internal id that never
leaves the server. A placement is `actual` for a published account, `hypothetical` for anyone else and only when a comparable
published pool exists, otherwise `none`. A cursor is `offset.revision.signature`, bound to family, version, scope and window; a
cursor from an older board revision answers `409 stale_cursor`, a bad or foreign one `400 invalid`.

`python -m server.pool` changes the pool, always in one transaction with an append-only audit row, and never goes backwards:

- `freeze --version V --reason CODE`: a ranking version stops for good; its open issues are withdrawn and its boards move to a new revision.
- `retire --question Q --release R --reason CODE --operator OP [--clone-audit REF]`: `ranked` to `retired`. Refused while an open version
  still contains the question (freeze it first); open issues for the question are withdrawn, accepted answers stay.
- `free --question Q ... --clone-audit REF --license-ok REF --exposure-audit REF`: `retired` to `free`; all three references are required.
- `rebuild --version V`: recompute an OPEN version's standings from first attempts (a frozen version is history and is refused); every board moves to a new revision.
- `export-underlay --out FILE [--catalog FILE ...]`: write the protected pool as ids, content hashes and states only (no text), from the
  database or from frozen scoring catalogs (a catalog with an exposure gap stops the export); never overwrites; prints the digest.
- `check-static --public-root DIR [--underlay FILE (--expect-digest D | --unbound)]`: fails (exit 1) if a ranked or retired question's id or content hash appears in static files.

With `--underlay` the check needs no database. It then insists on `--expect-digest` (a RELEASE check: the digest comes from the
release decision, and the underlay must be exported from the authoritative pool, never derived from the input being checked) or
`--unbound` (integrity only, prints a notice, used by the public CI self-test with synthetic data). A missing, malformed, empty,
tampered or stale underlay is an error (exit 2), never a pass. The digest proves integrity only, not origin or currency. Public CI
holds no real pool ids, hashes or bytes; the real release check lives in the private release pipeline.

Operator, reason, release and references are short codes, never names or e-mail addresses. Exit codes: 0 done, 1 static findings, 2 refused.

### Hiding a name (operator tool) and the alias blocklist

`python -m server.moderate hide|unhide --principal ID --family F --reason CODE --operator OP [--dry-run|--write]` changes nothing
unless `--write` is given. The target is the internal principal and the family, never the alias, so renaming, opting out or opting in
again cannot lift a hide. A real change appends one audit row (time, operator, principal, family, action, reason code; no e-mail,
address or session data), moves that family's boards to a new revision in the same transaction and leaves the private history alone.
Reasons come from a fixed list and operator ids are short codes. `find --family F --alias A` is a read-only lookup of principal ids
by exact alias. There is no public admin API.

Aliases are also checked against a small versioned list (`server/blocklist/aliases-v1.json`) on whole words after NFKC, case
folding and a look-alike fold; a missing or malformed list stops start-up. It is a courtesy filter with stated limits, not a claim
of abuse safety.

### Rolling scoring back (operator runbook)

There is no command and no database guard that stops an older binary, so rollback is a procedure: stop the service and every worker or
scheduler; block access to the scoring endpoints; check that the scoring flags are off in the configuration the OLD process will really
use; start it without scoring; confirm the scoring endpoints answer unavailable before reopening other traffic. Never run two versions
against the scoring tables at once. The database, attempts, locks and exposures are kept; rows an older binary writes get
`origin = 'legacy'` and are never ranked.

### Local scoring demo (`python -m server.dev --scoring`)

Development only (the runner refuses `APP_ENV=production`, and scoring is a `SetupError` there anyway). It turns scoring on for that one
process, over a SYNTHETIC protected pool written to a temporary directory: an official pool bound to a throwaway product, and a free
pool read through its own private, hash-checked manifest (the same path a real deployment uses, not the in-memory source). Every
question says it is synthetic and no public-bank question is ever used as a protected fixture. Three seeded test readers
(`dev-reader-1..3`) get sessions for the throwaway database only, printed at start with the cookie and CSRF header to send. It does not
combine with `--content-root`/`--manifest`, makes no external call and binds loopback.

## Paddle Billing (sandbox/test preparation, default off)

Off unless `PAYMENTS_PROVIDER=paddle`. It needs `PADDLE_ENV=sandbox` (live is refused until a real transport has been
approved), `PADDLE_API_KEY` (checked once for its environment prefix, never stored), `PADDLE_ENDPOINT_SECRET` (at least 16
characters, different from the key, redacted everywhere) and `PADDLE_PRODUCTS` (strict JSON: product id to price id,
quantity 1, currency and the expected total; a missing total means nothing is ever granted). `APP_ENV=production` is
refused. The transport is injected (`Services.paddle_transport`); there is no real HTTP client yet.

`POST /checkout` keeps the mock flow unchanged and, with Paddle on, answers `{purchase_id, state: "pending", provider:
"paddle", checkout: {mode: "overlay", transaction_id}}` only when a remote transaction was created AND bound; every other
outcome is a 503 without any provider id, and a new start is a new purchase with a new consent (never an automatic retry).
`POST /payments/paddle-sandbox/webhook` verifies the signature over the original bytes (two signature fields are refused),
answers 200 for an accepted or repeated event, 409 for a reused event id with another body, 400 for anything that fails
verification, 413 for a large body and 503 when the bounded queue is full or storage fails. It stores evidence and queues
a job; it never fetches from the provider and never grants. `PaddleService.reconcile_due` (a plain function, not
scheduled yet) fetches an authoritative snapshot and is the only path that can change a purchase.
Provisional values (decision 2026-10-18): confirm before live.

## Test gates

```sh
python -m unittest discover -s server/tests
python -m unittest discover -s validator/tests
python -m unittest discover -s schema/tests
node --test web-tests
python -m validator export --check
python -m validator trace --check
git diff --check
```
