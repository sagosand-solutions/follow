# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned schema migrations for the accounts database.

Each entry is ``(version, statements)``. Statements are applied one by one inside
a transaction so a failed migration rolls back completely.
"""

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('active', 'deleted')),
                deletion_at REAL
            )
            """,
            """
            CREATE TABLE identities (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                issuer TEXT NOT NULL,
                subject TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE (provider, issuer, subject)
            )
            """,
            """
            CREATE TABLE sessions (
                id_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_seen REAL NOT NULL,
                expires_at REAL NOT NULL,
                auth_time REAL NOT NULL,
                absolute_expires_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE auth_transactions (
                token_hash TEXT PRIMARY KEY,
                preauth_binding_hash TEXT NOT NULL,
                intent TEXT NOT NULL CHECK (intent IN ('login', 'link')),
                provider TEXT NOT NULL,
                user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                nonce_hash TEXT,
                state_data TEXT,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                attempts INTEGER NOT NULL DEFAULT 0
            )
            """,
            "CREATE INDEX idx_auth_transactions_expires ON auth_transactions (expires_at)",
            """
            CREATE TABLE products (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
                access_scope TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1))
            )
            """,
            """
            CREATE TABLE purchases (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id),
                provider TEXT NOT NULL,
                provider_purchase_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'paid', 'refunded', 'chargeback', 'cancelled')),
                revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                UNIQUE (provider, provider_purchase_id)
            )
            """,
            "CREATE INDEX idx_purchases_user ON purchases (user_id)",
            """
            CREATE TABLE entitlements (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id),
                source_purchase_id TEXT NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
                state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
                granted_at REAL NOT NULL,
                revoked_at REAL,
                UNIQUE (source_purchase_id, product_id)
            )
            """,
            "CREATE INDEX idx_entitlements_user ON entitlements (user_id)",
            """
            CREATE TABLE payment_events (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                purchase_id TEXT REFERENCES purchases(id) ON DELETE SET NULL,
                normalized_state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('received', 'applied', 'ignored', 'conflict', 'failed')),
                received_at REAL NOT NULL,
                UNIQUE (provider, event_id)
            )
            """,
        ),
    ),
    (
        2,
        (
            """
            CREATE TABLE policy_bundles (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                access_scope TEXT NOT NULL,
                locale TEXT NOT NULL,
                version TEXT NOT NULL,
                terms_sha256 TEXT NOT NULL,
                refunds_sha256 TEXT NOT NULL,
                privacy_sha256 TEXT NOT NULL,
                required_acknowledgements TEXT NOT NULL,
                bundle_digest TEXT NOT NULL,
                effective_at REAL NOT NULL,
                UNIQUE (product_id, version, locale)
            )
            """,
            """
            CREATE TABLE purchase_consents (
                id TEXT PRIMARY KEY,
                purchase_id TEXT NOT NULL UNIQUE REFERENCES purchases(id),
                user_id TEXT NOT NULL REFERENCES users(id),
                policy_bundle_id TEXT NOT NULL REFERENCES policy_bundles(id),
                bundle_version TEXT NOT NULL,
                bundle_digest TEXT NOT NULL,
                acknowledgements TEXT NOT NULL,
                accepted_at REAL NOT NULL
            )
            """,
            """
            CREATE TRIGGER policy_bundles_no_update BEFORE UPDATE ON policy_bundles
            BEGIN
                SELECT RAISE(ABORT, 'policy_bundles is immutable');
            END
            """,
            """
            CREATE TRIGGER purchase_consents_no_update BEFORE UPDATE ON purchase_consents
            BEGIN
                SELECT RAISE(ABORT, 'purchase_consents is immutable');
            END
            """,
        ),
    ),
    (
        3,
        (
            "ALTER TABLE purchases ADD COLUMN created_at REAL NOT NULL DEFAULT 0",
            "CREATE INDEX idx_purchases_user_created ON purchases (user_id, created_at DESC, id DESC)",
        ),
    ),
)
