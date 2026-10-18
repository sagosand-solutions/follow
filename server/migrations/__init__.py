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
    (
        4,
        (
            """
            CREATE TABLE scoring_principals (
                id TEXT PRIMARY KEY,
                user_id TEXT UNIQUE REFERENCES users(id),
                anon_cookie_hash TEXT UNIQUE,
                created_at REAL NOT NULL,
                last_activity REAL NOT NULL,
                expires_at REAL,
                claimed_to TEXT REFERENCES scoring_principals(id),
                claim_source_id TEXT UNIQUE REFERENCES scoring_principals(id),
                state TEXT NOT NULL CHECK (state IN ('active', 'consumed', 'closed')),
                CHECK (user_id IS NULL OR (anon_cookie_hash IS NULL AND claimed_to IS NULL))
            )
            """,
            """
            CREATE TABLE question_issues (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                question_id TEXT NOT NULL,
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                catalog_version TEXT NOT NULL,
                revision TEXT NOT NULL,
                option_map TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL
            )
            """,
            "CREATE INDEX idx_question_issues_principal ON question_issues (principal_id, consumed_at)",
            """
            CREATE TABLE first_attempts (
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                question_id TEXT NOT NULL,
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                course_id TEXT NOT NULL,
                catalog_version TEXT NOT NULL,
                rules_version TEXT NOT NULL,
                revision TEXT NOT NULL,
                option_id TEXT,
                outcome TEXT NOT NULL CHECK (outcome IN ('correct', 'partial', 'not_sure', 'wrong')),
                score_units INTEGER NOT NULL CHECK (score_units IN (2, 1, 0, -1)),
                accepted_at REAL NOT NULL,
                PRIMARY KEY (principal_id, question_id)
            )
            """,
            "CREATE INDEX idx_first_attempts_family ON first_attempts (family, course_id, accepted_at)",
            "CREATE TABLE scoring_purge_gate (open INTEGER PRIMARY KEY CHECK (open = 1))",
            """
            CREATE TRIGGER first_attempts_no_delete BEFORE DELETE ON first_attempts
            WHEN NOT EXISTS (SELECT 1 FROM scoring_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'first_attempts is immutable');
            END
            """,
            """
            CREATE TRIGGER first_attempts_only_claim_moves BEFORE UPDATE ON first_attempts
            WHEN NEW.principal_id = OLD.principal_id
              OR NEW.question_id IS NOT OLD.question_id OR NEW.family IS NOT OLD.family
              OR NEW.course_id IS NOT OLD.course_id OR NEW.catalog_version IS NOT OLD.catalog_version
              OR NEW.rules_version IS NOT OLD.rules_version OR NEW.revision IS NOT OLD.revision
              OR NEW.option_id IS NOT OLD.option_id OR NEW.outcome IS NOT OLD.outcome
              OR NEW.score_units IS NOT OLD.score_units OR NEW.accepted_at IS NOT OLD.accepted_at
            BEGIN
                SELECT RAISE(ABORT, 'first_attempts is immutable');
            END
            """,
            """
            CREATE TABLE submission_receipts (
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                question_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (principal_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE leaderboard_preferences (
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                opted_in_at REAL,
                alias TEXT NOT NULL,
                nickname TEXT,
                moderation_state TEXT NOT NULL CHECK (moderation_state IN ('ok', 'hidden')),
                PRIMARY KEY (principal_id, family)
            )
            """,
        ),
    ),
    (
        5,
        (
            """
            CREATE TABLE paddle_operations (
                id TEXT PRIMARY KEY,
                purchase_id TEXT NOT NULL UNIQUE REFERENCES purchases(id),
                environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'live')),
                state TEXT NOT NULL CHECK (state IN ('planned', 'request_sent', 'bound', 'unknown', 'abandoned')),
                provider_txn_id TEXT UNIQUE,
                late_txn_id TEXT UNIQUE,
                created_at REAL NOT NULL,
                request_sent_at REAL,
                bound_at REAL,
                CHECK ((state = 'bound') = (provider_txn_id IS NOT NULL))
            )
            """,
            """
            CREATE TRIGGER paddle_operations_binding_is_immutable BEFORE UPDATE ON paddle_operations
            WHEN NEW.id IS NOT OLD.id OR NEW.purchase_id IS NOT OLD.purchase_id OR NEW.environment IS NOT OLD.environment
              OR NEW.created_at IS NOT OLD.created_at
              OR (OLD.provider_txn_id IS NOT NULL AND NEW.provider_txn_id IS NOT OLD.provider_txn_id)
              OR (OLD.late_txn_id IS NOT NULL AND NEW.late_txn_id IS NOT OLD.late_txn_id)
            BEGIN
                SELECT RAISE(ABORT, 'paddle_operations binding is immutable');
            END
            """,
            """
            CREATE TABLE paddle_txn_reservations (
                txn_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE REFERENCES paddle_operations(id),
                kind TEXT NOT NULL CHECK (kind IN ('bound', 'late'))
            )
            """,
            """
            CREATE TABLE paddle_events (
                environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'live')),
                event_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                received_at REAL NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'settled')),
                PRIMARY KEY (environment, event_id)
            )
            """,
            "CREATE INDEX idx_paddle_events_txn ON paddle_events (environment, transaction_id)",
            """
            CREATE TRIGGER paddle_events_evidence_is_immutable BEFORE UPDATE ON paddle_events
            WHEN NEW.environment IS NOT OLD.environment OR NEW.event_id IS NOT OLD.event_id
              OR NEW.transaction_id IS NOT OLD.transaction_id OR NEW.event_type IS NOT OLD.event_type
              OR NEW.payload_digest IS NOT OLD.payload_digest OR NEW.occurred_at IS NOT OLD.occurred_at
              OR NEW.received_at IS NOT OLD.received_at OR (OLD.state = 'settled' AND NEW.state = 'pending')
            BEGIN
                SELECT RAISE(ABORT, 'paddle_events evidence is immutable');
            END
            """,
            """
            CREATE TABLE paddle_reconcile (
                purchase_id TEXT PRIMARY KEY REFERENCES purchases(id),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                next_attempt_at REAL NOT NULL,
                lease_until REAL,
                owner TEXT,
                generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
                pending_seq INTEGER NOT NULL DEFAULT 0 CHECK (pending_seq >= 0),
                claimed_seq INTEGER NOT NULL DEFAULT 0 CHECK (claimed_seq >= 0),
                reason TEXT NOT NULL DEFAULT 'transport' CHECK (reason IN ('transport', 'await_event', 'reconcile', 'proof')),
                wait_deadline REAL,
                last_error TEXT CHECK (last_error IS NULL OR last_error IN ('transport', 'timeout', 'malformed', 'rejected', 'unexpected')),
                state TEXT NOT NULL CHECK (state IN ('due', 'manual'))
            )
            """,
            """
            CREATE TABLE purchase_transitions (
                id TEXT PRIMARY KEY,
                purchase_id TEXT NOT NULL REFERENCES purchases(id),
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                from_revision INTEGER NOT NULL,
                to_revision INTEGER NOT NULL,
                cause TEXT NOT NULL CHECK (cause IN ('verified_event', 'reconcile')),
                event_provider TEXT,
                event_id TEXT,
                snapshot_digest TEXT,
                created_at REAL NOT NULL,
                CHECK ((cause = 'verified_event') = (event_id IS NOT NULL)),
                CHECK ((cause = 'reconcile') = (snapshot_digest IS NOT NULL AND length(snapshot_digest) = 64))
            )
            """,
            "CREATE INDEX idx_purchase_transitions_purchase ON purchase_transitions (purchase_id, created_at)",
        ),
    ),
    (
        6,
        (
            """
            CREATE TABLE paddle_checkout_context (
                operation_id TEXT PRIMARY KEY REFERENCES paddle_operations(id) ON DELETE CASCADE,
                purchase_id TEXT NOT NULL UNIQUE REFERENCES purchases(id),
                environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'live')),
                user_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                policy_bundle_id TEXT NOT NULL,
                bundle_digest TEXT NOT NULL,
                ack_set TEXT NOT NULL,
                access_scope TEXT NOT NULL,
                amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
                product_currency TEXT NOT NULL,
                price_id TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity >= 1),
                currency_code TEXT NOT NULL,
                total_minor INTEGER,
                total_field TEXT NOT NULL CHECK (total_field IN ('total', 'grand_total', 'subtotal')),
                context_digest TEXT NOT NULL CHECK (length(context_digest) = 64),
                created_at REAL NOT NULL
            )
            """,
            "CREATE INDEX idx_paddle_checkout_context_key ON paddle_checkout_context (user_id, product_id, environment)",
            "CREATE INDEX idx_purchases_quota ON purchases (user_id, product_id, provider, state)",
            """
            CREATE TRIGGER paddle_checkout_context_is_immutable BEFORE UPDATE ON paddle_checkout_context
            BEGIN
                SELECT RAISE(ABORT, 'paddle_checkout_context is immutable');
            END
            """,
        ),
    ),
)
