# Feature flags

Every flag below defaults to off (or unset). The server is fail-closed: it
refuses to start on an unsafe combination. None of these flags may be enabled in
production without an explicit, separately approved release decision.

## Client flags

Build-time constants in [`public/js/config.js`](../public/js/config.js). They are
not read from the URL, browser storage, or the environment.

| Flag | Default | What it turns on | Dependencies and notes |
|---|---|---|---|
| `studyLayer` | `false` | The study layer: per-question notes and concepts from a reviewed notes manifest. | Needs reviewed notes listed in a manifest. There is no probing for missing files, so it stays off until those notes exist. |
| `scoringUi` | `false` | Server-backed scoring screens: profile (points/standing), leaderboards, and the per-question challenge link. | Needs `accountsApi` pointing at a real server and the server's `SCORING_*` flags on. No local, sample, or mock fallback: the views report "not configured" when the API is unset. |
| `accountsUi` | `false` | Account screens (sign-in, premium, checkout) and the quiet header account link. | Server-backed accounts need a real `accountsApi`; without it the screens run on the in-browser test client. |
| `accountsApi` | `null` | Base URL of the accounts/scoring API. `null` disables the HTTP client. | When `null`, the account client is the local test client and the scoring views stay off. |
| `accountsOverlay` | `null` | The development stand-in for the payment provider's checkout window (`dev-shim`). | Needs `accountsUi` on and a development environment. Only ever set in `public/js/config.js`; never from the URL or storage. |

## Server flags

Read from the process environment at startup by
[`server/config.py`](../server/config.py). A missing variable uses the default
below; an invalid value stops startup with an error. The loopback development
runner sets its own in-process values and never reads a real secret.

### Scoring

| Variable | Default | What it turns on | Dependencies and constraints |
|---|---|---|---|
| `SCORING_ENABLED` | `false` | The scoring API and its routes on the server. | Refused when `APP_ENV=production`: scoring may not run in production before a separately approved release. |
| `SCORING_ANONYMOUS_ENABLED` | `false` | Anonymous scoring profiles (no account), free content only. | Requires `SCORING_ENABLED`. |
| `SCORING_CLAIM_ENABLED` | `false` | Claiming an anonymous profile into a signed-in account. | Requires `SCORING_ENABLED`. |
| `SCORING_PUBLISH_ENABLED` | `false` | Opting into shared leaderboards (with a nickname) and reading them. | Requires `SCORING_ENABLED`. |
| `SCORING_DEV_SOURCE` | `false` | Serving the protected free pool from an in-memory map instead of the private manifest. | Requires `SCORING_ENABLED`; test/development only and refused in production. |
| `SCORING_FREE_CONTENT_ROOT` | unset | Root directory of the private free-pool content. | Set together with `SCORING_FREE_MANIFEST`; requires `SCORING_ENABLED` and excludes `SCORING_DEV_SOURCE`. Must not be a path under the public site. |
| `SCORING_FREE_MANIFEST` | unset | Hash-checked manifest listing the private free-pool files. | Set together with `SCORING_FREE_CONTENT_ROOT`; same constraints. |

### Payments

| Variable | Default | What it turns on | Dependencies and constraints |
|---|---|---|---|
| `PAYMENTS_PROVIDER` | `mock` | Selects the payment provider implementation. | One of `mock` or `paddle`. With `mock`, no other `PADDLE_*` variable may be set. |
| `PADDLE_ENV` | unset | The Paddle environment. | Only `sandbox` is supported; only valid with `PAYMENTS_PROVIDER=paddle`. |
| `PADDLE_API_KEY` | unset | Paddle API key. | Secret. Used once at startup for the environment and key-prefix check, and never stored. Only valid with `PAYMENTS_PROVIDER=paddle`. |
| `PADDLE_ENDPOINT_SECRET` | unset | Paddle webhook signature secret. | Secret, at least 16 characters. Kept redacted everywhere. Only valid with `PAYMENTS_PROVIDER=paddle`. |
| `PADDLE_PRODUCTS` | unset | JSON map of product id to price, quantity, and currency. | Strict JSON; each entry needs `price_id`, `quantity`, and `currency_code` (optional `total_minor`, `total_field`). Only valid with `PAYMENTS_PROVIDER=paddle`. |

## Production

Every flag above is off by default and must stay off in production until a
release decision explicitly turns on a specific flag set. The server enforces
the scoring and Paddle constraints at startup and refuses to bind a port on an
unsafe combination.
