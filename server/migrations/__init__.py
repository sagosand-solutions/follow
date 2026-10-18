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
    (
        7,
        (
            # --- first_attempts.origin: fail-closed DEFAULT 'legacy', explicit by new code, immutable afterwards ---------
            # The old immutability trigger allows only a principal move, so it is dropped for the backfill and replaced
            # (below) by one that also freezes `origin`.
            "DROP TRIGGER first_attempts_only_claim_moves",
            """
            ALTER TABLE first_attempts ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy'
                CHECK (origin IN ('account', 'anonymous', 'exposed', 'legacy'))
            """,
            # Backfill: anonymous principal -> anonymous; an account with a claim marker -> legacy (claimed rows cannot be
            # told from its own); every other account -> account. Nothing is deleted, no lock disappears.
            """
            UPDATE first_attempts SET origin = CASE
                WHEN (SELECT sp.user_id FROM scoring_principals sp WHERE sp.id = first_attempts.principal_id) IS NULL THEN 'anonymous'
                WHEN (SELECT sp.claim_source_id FROM scoring_principals sp WHERE sp.id = first_attempts.principal_id) IS NOT NULL THEN 'legacy'
                ELSE 'account' END
            """,
            """
            CREATE TRIGGER first_attempts_only_claim_moves BEFORE UPDATE ON first_attempts
            WHEN NEW.principal_id = OLD.principal_id
              OR NEW.question_id IS NOT OLD.question_id OR NEW.family IS NOT OLD.family
              OR NEW.course_id IS NOT OLD.course_id OR NEW.catalog_version IS NOT OLD.catalog_version
              OR NEW.rules_version IS NOT OLD.rules_version OR NEW.revision IS NOT OLD.revision
              OR NEW.option_id IS NOT OLD.option_id OR NEW.outcome IS NOT OLD.outcome
              OR NEW.score_units IS NOT OLD.score_units OR NEW.accepted_at IS NOT OLD.accepted_at
              OR NEW.origin IS NOT OLD.origin
            BEGIN
                SELECT RAISE(ABORT, 'first_attempts is immutable');
            END
            """,
            # --- question_issues: rank eligibility and the (inert) challenge set reference ---------------------------
            "ALTER TABLE question_issues ADD COLUMN rank_eligible INTEGER NOT NULL DEFAULT 0 CHECK (rank_eligible IN (0, 1))",
            "ALTER TABLE question_issues ADD COLUMN set_id TEXT",
            # --- question_exposure: what a principal has been shown; rank_blocking only ever goes 0 -> 1 ------------
            """
            CREATE TABLE question_exposure (
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                question_id TEXT NOT NULL,
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                first_exposed_at REAL NOT NULL,
                via TEXT NOT NULL CHECK (via IN ('issue', 'content', 'claim')),
                rank_blocking INTEGER NOT NULL CHECK (rank_blocking IN (0, 1)),
                PRIMARY KEY (principal_id, question_id, family)
            )
            """,
            """
            CREATE TRIGGER question_exposure_is_monotone BEFORE UPDATE ON question_exposure
            WHEN NOT (NEW.principal_id = OLD.principal_id AND NEW.question_id = OLD.question_id AND NEW.family = OLD.family
                      AND NEW.rank_blocking >= OLD.rank_blocking AND NEW.first_exposed_at <= OLD.first_exposed_at
                      AND (NEW.via = OLD.via OR NEW.rank_blocking > OLD.rank_blocking))
            BEGIN
                SELECT RAISE(ABORT, 'question_exposure only moves 0 -> 1 and earlier');
            END
            """,
            """
            CREATE TRIGGER question_exposure_no_delete BEFORE DELETE ON question_exposure
            WHEN NOT EXISTS (SELECT 1 FROM scoring_purge_gate)
            BEGIN
                SELECT RAISE(ABORT, 'question_exposure is append-only');
            END
            """,
            # Backfill: every issue ever given is an exposure; an anonymous principal's exposure blocks ranked play.
            """
            INSERT INTO question_exposure (principal_id, question_id, family, first_exposed_at, via, rank_blocking)
            SELECT qi.principal_id, qi.question_id, qi.family, MIN(qi.issued_at), 'issue',
                   CASE WHEN sp.user_id IS NULL THEN 1 ELSE 0 END
            FROM question_issues qi JOIN scoring_principals sp ON sp.id = qi.principal_id
            GROUP BY qi.principal_id, qi.question_id, qi.family
            """,
            # A claimed source's exposures belong to the account that claimed it, as blocking ones.
            """
            INSERT INTO question_exposure (principal_id, question_id, family, first_exposed_at, via, rank_blocking)
            SELECT src.claimed_to, e.question_id, e.family, MIN(e.first_exposed_at), 'claim', 1
            FROM question_exposure e JOIN scoring_principals src ON src.id = e.principal_id
            WHERE src.claimed_to IS NOT NULL
            GROUP BY src.claimed_to, e.question_id, e.family
            ON CONFLICT (principal_id, question_id, family) DO UPDATE SET rank_blocking = 1, via = 'claim',
                first_exposed_at = MIN(question_exposure.first_exposed_at, excluded.first_exposed_at)
                WHERE question_exposure.rank_blocking = 0
            """,
            # --- publication: an anonymous opt-in is never a publication ------------------------------------------------
            """
            UPDATE leaderboard_preferences SET opted_in_at = NULL, nickname = NULL
            WHERE principal_id IN (SELECT id FROM scoring_principals WHERE user_id IS NULL)
            """,
            # --- pool lifecycle: monotone ranked -> retired -> free ---------------------------------------------------
            """
            CREATE TABLE pool_questions (
                question_id TEXT PRIMARY KEY,
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                content_sha256 TEXT NOT NULL UNIQUE CHECK (length(content_sha256) = 64),
                state TEXT NOT NULL CHECK (state IN ('ranked', 'retired', 'free')),
                release_id TEXT NOT NULL,
                since REAL NOT NULL,
                reason_code TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                clone_audit_ref TEXT,
                license_ok_ref TEXT,
                exposure_audit_ref TEXT
            )
            """,
            """
            CREATE TRIGGER pool_questions_monotone BEFORE UPDATE ON pool_questions
            WHEN NOT (NEW.question_id = OLD.question_id AND NEW.family = OLD.family AND NEW.content_sha256 = OLD.content_sha256
                      AND ((OLD.state = 'ranked' AND NEW.state = 'retired') OR (OLD.state = 'retired' AND NEW.state = 'free')))
              OR (NEW.state = 'free' AND (NEW.clone_audit_ref IS NULL OR NEW.license_ok_ref IS NULL OR NEW.exposure_audit_ref IS NULL))
            BEGIN
                SELECT RAISE(ABORT, 'pool_questions moves only ranked -> retired -> free');
            END
            """,
            "CREATE TRIGGER pool_questions_no_delete BEFORE DELETE ON pool_questions BEGIN SELECT RAISE(ABORT, 'pool_questions is permanent'); END",
            """
            CREATE TABLE pool_lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id TEXT NOT NULL REFERENCES pool_questions(question_id),
                from_state TEXT NOT NULL CHECK (from_state IN ('ranked', 'retired', 'free')),
                to_state TEXT NOT NULL CHECK (to_state IN ('ranked', 'retired', 'free')),
                release_id TEXT NOT NULL,
                effective_at REAL NOT NULL,
                operator_id TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                clone_audit_ref TEXT,
                CHECK ((from_state = 'ranked' AND to_state = 'retired') OR (from_state = 'retired' AND to_state = 'free'))
            )
            """,
            "CREATE TRIGGER pool_lifecycle_events_append_only BEFORE UPDATE ON pool_lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only'); END",
            "CREATE TRIGGER pool_lifecycle_events_no_delete BEFORE DELETE ON pool_lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only'); END",
            # --- ranking versions: immutable snapshot, open -> frozen only ------------------------------------------------
            """
            CREATE TABLE ranking_versions (
                id TEXT PRIMARY KEY,
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                catalog_version TEXT NOT NULL,
                rules_version TEXT NOT NULL,
                snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64),
                courses_k INTEGER NOT NULL CHECK (courses_k > 0),
                denominator_total TEXT NOT NULL CHECK (denominator_total NOT GLOB '*[^0-9]*' AND length(denominator_total) > 0),
                created_at REAL NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'frozen')),
                frozen_at REAL,
                freeze_reason TEXT,
                CHECK ((status = 'frozen') = (frozen_at IS NOT NULL AND freeze_reason IS NOT NULL)),
                UNIQUE (family, catalog_version, rules_version)
            )
            """,
            """
            CREATE TRIGGER ranking_versions_only_freeze BEFORE UPDATE ON ranking_versions
            WHEN NOT (OLD.status = 'open' AND NEW.status = 'frozen'
                      AND NEW.id = OLD.id AND NEW.family = OLD.family AND NEW.catalog_version = OLD.catalog_version
                      AND NEW.rules_version = OLD.rules_version AND NEW.snapshot_digest = OLD.snapshot_digest
                      AND NEW.courses_k = OLD.courses_k AND NEW.denominator_total = OLD.denominator_total
                      AND NEW.created_at = OLD.created_at)
            BEGIN
                SELECT RAISE(ABORT, 'ranking_versions only move open -> frozen');
            END
            """,
            "CREATE TRIGGER ranking_versions_no_delete BEFORE DELETE ON ranking_versions BEGIN SELECT RAISE(ABORT, 'ranking_versions is permanent'); END",
            """
            CREATE TABLE ranking_version_courses (
                version_id TEXT NOT NULL REFERENCES ranking_versions(id),
                course_id TEXT NOT NULL,
                pool_size INTEGER NOT NULL CHECK (pool_size > 0),
                PRIMARY KEY (version_id, course_id)
            )
            """,
            """
            CREATE TABLE ranking_version_members (
                version_id TEXT NOT NULL REFERENCES ranking_versions(id),
                course_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
                PRIMARY KEY (version_id, question_id)
            )
            """,
            "CREATE TRIGGER ranking_version_courses_immutable BEFORE UPDATE ON ranking_version_courses BEGIN SELECT RAISE(ABORT, 'immutable'); END",
            "CREATE TRIGGER ranking_version_courses_no_delete BEFORE DELETE ON ranking_version_courses BEGIN SELECT RAISE(ABORT, 'immutable'); END",
            "CREATE TRIGGER ranking_version_members_immutable BEFORE UPDATE ON ranking_version_members BEGIN SELECT RAISE(ABORT, 'immutable'); END",
            "CREATE TRIGGER ranking_version_members_no_delete BEFORE DELETE ON ranking_version_members BEGIN SELECT RAISE(ABORT, 'immutable'); END",
            # --- materialised, exact standings (rankable) and the private anonymous aggregate -------------------------------
            """
            CREATE TABLE course_units (
                version_id TEXT NOT NULL REFERENCES ranking_versions(id),
                window_key TEXT NOT NULL,
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                course_id TEXT NOT NULL,
                units INTEGER NOT NULL,
                answered INTEGER NOT NULL CHECK (answered >= 0),
                last_accepted_at REAL NOT NULL,
                PRIMARY KEY (version_id, window_key, principal_id, course_id)
            )
            """,
            """
            CREATE TABLE standings (
                version_id TEXT NOT NULL REFERENCES ranking_versions(id),
                scope TEXT NOT NULL,
                window_key TEXT NOT NULL,
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                numerator TEXT NOT NULL,
                score_key TEXT NOT NULL CHECK (score_key NOT GLOB '*[^0-9]*' AND length(score_key) > 0),
                answered INTEGER NOT NULL CHECK (answered >= 0),
                last_accepted_at REAL NOT NULL,
                PRIMARY KEY (version_id, scope, window_key, principal_id)
            )
            """,
            "CREATE INDEX idx_standings_board ON standings (version_id, scope, window_key, score_key DESC, answered, last_accepted_at, principal_id)",
            "CREATE INDEX idx_standings_principal ON standings (principal_id, version_id, window_key)",
            """
            CREATE TABLE anonymous_units (
                version_id TEXT NOT NULL REFERENCES ranking_versions(id),
                window_key TEXT NOT NULL,
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                course_id TEXT NOT NULL,
                units INTEGER NOT NULL,
                answered INTEGER NOT NULL CHECK (answered >= 0),
                last_accepted_at REAL NOT NULL,
                PRIMARY KEY (version_id, window_key, principal_id, course_id)
            )
            """,
            """
            CREATE TABLE board_revisions (
                version_id TEXT NOT NULL REFERENCES ranking_versions(id),
                window_key TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 0),
                PRIMARY KEY (version_id, window_key)
            )
            """,
            # --- challenge sets: contract only, solo only, nothing activated -----------------------------------------------
            """
            CREATE TABLE challenge_sets (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('solo')),
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                catalog_version TEXT NOT NULL,
                rules_version TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                seed_ref TEXT NOT NULL,
                lock_policy TEXT NOT NULL CHECK (lock_policy IN ('global_first_attempt')),
                created_at REAL NOT NULL,
                expires_at REAL
            )
            """,
            """
            CREATE TABLE challenge_set_questions (
                set_id TEXT NOT NULL REFERENCES challenge_sets(id),
                position INTEGER NOT NULL CHECK (position >= 0),
                question_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
                PRIMARY KEY (set_id, position),
                UNIQUE (set_id, question_id)
            )
            """,
            """
            CREATE TABLE set_participants (
                set_id TEXT NOT NULL REFERENCES challenge_sets(id),
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                started_at REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL CHECK (status IN ('active', 'finished', 'abandoned')),
                PRIMARY KEY (set_id, principal_id)
            )
            """,
            """
            CREATE TABLE set_attempts (
                set_id TEXT NOT NULL REFERENCES challenge_sets(id),
                principal_id TEXT NOT NULL REFERENCES scoring_principals(id),
                question_id TEXT NOT NULL,
                issue_id TEXT NOT NULL UNIQUE REFERENCES question_issues(id),
                PRIMARY KEY (set_id, principal_id, question_id)
            )
            """,
            # --- moderation: append-only audit with no personal data ----------------------------------------------------
            """
            CREATE TABLE scoring_moderation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at REAL NOT NULL,
                operator TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                family TEXT NOT NULL CHECK (family IN ('official', 'free')),
                action TEXT NOT NULL CHECK (action IN ('hide', 'unhide')),
                reason_code TEXT NOT NULL,
                dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1))
            )
            """,
            "CREATE TRIGGER scoring_moderation_audit_append_only BEFORE UPDATE ON scoring_moderation_audit BEGIN SELECT RAISE(ABORT, 'append-only'); END",
            "CREATE TRIGGER scoring_moderation_audit_no_delete BEFORE DELETE ON scoring_moderation_audit BEGIN SELECT RAISE(ABORT, 'append-only'); END",
        ),
    ),
)
