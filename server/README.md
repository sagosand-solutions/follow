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

Starts Uvicorn on `127.0.0.1` with `APP_ENV=development`, mock adapters and a
throwaway SQLite database (a temporary file unless `--db` is given). The CSRF and
session keys are generated at start; the runner never reads a real secret from
the environment and **refuses to start when `APP_ENV=production`** (exit 2).

The API is mounted under `/follow/api` and the static site (default `public/`)
under `/follow/` on the same origin, so the Origin and CSRF checks work in a
browser; `/` redirects to the site. Provider and payment flows stay disabled
(503) until real adapters exist — this runner makes no external call. Tests:
`server/tests/test_dev.py` (production refused, mock app answers a GET).

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
