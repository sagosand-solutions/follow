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

`python -m server.pool` changes the pool, always in one transaction with an append-only audit row, and never goes backwards:

- `freeze --version V --reason CODE`: a ranking version stops for good; its open issues are withdrawn and its boards move to a new revision.
- `retire --question Q --release R --reason CODE --operator OP [--clone-audit REF]`: `ranked` to `retired`. Refused while an open version
  still contains the question (freeze it first); open issues for the question are withdrawn, accepted answers stay.
- `free --question Q ... --clone-audit REF --license-ok REF --exposure-audit REF`: `retired` to `free`; all three references are required.
- `check-static --public-root DIR`: fails (exit 1) if a ranked or retired question's id or content hash appears in static files.

Operator, reason, release and references are short codes, never names or e-mail addresses. Exit codes: 0 done, 1 static findings, 2 refused.

### Rolling scoring back (operator runbook)

There is no command and no database guard that stops an older binary, so rollback is a procedure: stop the service and every worker or
scheduler; block access to the scoring endpoints; check that the scoring flags are off in the configuration the OLD process will really
use; start it without scoring; confirm the scoring endpoints answer unavailable before reopening other traffic. Never run two versions
against the scoring tables at once. The database, attempts, locks and exposures are kept; rows an older binary writes get
`origin = 'legacy'` and are never ranked.

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
