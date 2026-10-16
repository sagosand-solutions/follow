import os
import sqlite3
import tempfile
import threading
import unittest

from server.db import Database
from server.models import (AuthIntent, ConflictError, EntitlementState, NotFoundError, PaymentEventStatus,
                           PolicyActivation, PolicyBundle, Product, PurchaseState, SetupError, UserStatus,
                           compute_bundle_digest, resolve_active_bundle)
from server.repositories import Repositories


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "accounts.db")
        self.now = [1_000.0]
        self.db = Database(self.path, clock=lambda: self.now[0])
        self.addCleanup(self.db.close)
        self.db.migrate()
        self.repos = Repositories(self.db)

    def add_product(self, product_id="python-lifetime"):
        self.repos.products.upsert(Product(product_id, "Python lifetime", "USD", 500, "python", True))
        return product_id


class UserTests(RepoTestCase):
    def test_create_get_and_delete(self):
        user = self.repos.users.create(user_id="u1")
        self.assertEqual(user.status, UserStatus.ACTIVE)
        self.assertIsNone(user.deletion_at)
        self.assertEqual(self.repos.users.get("u1").id, "u1")
        self.assertTrue(self.repos.users.mark_deleted("u1", when=2_000.0))
        self.assertFalse(self.repos.users.mark_deleted("u1"))
        self.assertEqual(self.repos.users.get("u1").status, UserStatus.DELETED)
        self.assertEqual(self.repos.users.get("u1").deletion_at, 2_000.0)

    def test_generated_ids_are_opaque_and_unique(self):
        a = self.repos.users.create()
        b = self.repos.users.create()
        self.assertNotEqual(a.id, b.id)


class IdentityTests(RepoTestCase):
    def test_unique_identity_and_lookup(self):
        self.repos.users.create(user_id="u1")
        identity = self.repos.identities.add("u1", "google", "https://accounts.google.com", "sub-1")
        self.assertEqual(self.repos.identities.get("google", "https://accounts.google.com", "sub-1"), identity)
        with self.assertRaises(ConflictError):
            self.repos.identities.add("u1", "google", "https://accounts.google.com", "sub-1")

    def test_identity_for_unknown_user_is_a_foreign_key_error(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.repos.identities.add("missing", "google", "iss", "sub")

    def test_identities_list_for_user(self):
        self.repos.users.create(user_id="u1")
        self.repos.identities.add("u1", "google", "iss", "a")
        self.repos.identities.add("u1", "email", "iss", "b")
        self.assertEqual({i.provider for i in self.repos.identities.list_for_user("u1")}, {"google", "email"})


class SessionTests(RepoTestCase):
    def test_lifecycle(self):
        self.repos.users.create(user_id="u1")
        session = self.repos.sessions.create("u1", id_hash="h1", csrf_hash="c1", ttl_seconds=100, absolute_ttl_seconds=200)
        self.assertEqual(session.last_seen, 1_000.0)
        self.assertEqual(session.expires_at, 1_100.0)
        self.now[0] = 1_050.0
        touched = self.repos.sessions.touch("h1", ttl_seconds=100)
        self.assertEqual(touched.expires_at, 1_150.0)
        self.now[0] = 1_999.0
        self.assertIsNone(self.repos.sessions.touch("h1", ttl_seconds=100))  # past absolute expiry
        self.assertTrue(self.repos.sessions.revoke("h1"))
        self.assertFalse(self.repos.sessions.revoke("h1"))

    def test_revoke_all_for_user(self):
        self.repos.users.create(user_id="u1")
        for h in ("h1", "h2"):
            self.repos.sessions.create("u1", id_hash=h, csrf_hash="c", ttl_seconds=100, absolute_ttl_seconds=200)
        self.assertEqual(self.repos.sessions.revoke_all_for_user("u1"), 2)
        self.assertIsNone(self.repos.sessions.get("h1"))

    def test_touch_clamps_to_absolute_and_never_revives(self):
        self.repos.users.create(user_id="u1")
        # Idle expiry ends the session: created at 1000, idle at 1100.
        self.repos.sessions.create("u1", id_hash="idle", csrf_hash="c", ttl_seconds=100, absolute_ttl_seconds=200)
        self.now[0] = 1_100.0
        self.assertIsNone(self.repos.sessions.touch("idle", ttl_seconds=100))
        # A wide idle window must still never outlive the absolute deadline.
        self.now[0] = 1_000.0
        self.repos.sessions.create("u1", id_hash="abs", csrf_hash="c", ttl_seconds=300, absolute_ttl_seconds=200)
        self.now[0] = 1_199.0
        touched = self.repos.sessions.touch("abs", ttl_seconds=300)
        self.assertEqual(touched.expires_at, 1_200.0)  # clamped to absolute_expires_at
        self.assertEqual(touched.last_seen, 1_199.0)
        self.now[0] = 1_200.0
        self.assertIsNone(self.repos.sessions.touch("abs", ttl_seconds=300))  # at boundary
        self.now[0] = 1_201.0
        self.assertIsNone(self.repos.sessions.touch("abs", ttl_seconds=300))  # past boundary


class CompositionTests(RepoTestCase):
    class Boom(Exception):
        pass

    def test_claim_and_login_rolls_back_then_reclaims_once(self):
        self.repos.auth_transactions.create("t1", preauth_binding_hash="b", intent=AuthIntent.LOGIN,
                                            provider="google", expires_at=2_000.0)
        with self.assertRaises(CompositionTests.Boom):
            with self.db.transaction(immediate=True):
                self.assertTrue(self.repos.auth_transactions.consume_once("t1", now=1_500.0))
                user = self.repos.users.create()
                self.repos.identities.add(user.id, "google", "iss", "sub")
                self.repos.sessions.create(user.id, id_hash="h1", csrf_hash="c", ttl_seconds=100, absolute_ttl_seconds=200)
                raise CompositionTests.Boom()
        self.assertIsNone(self.repos.auth_transactions.get("t1").consumed_at)
        self.assertIsNone(self.repos.identities.get("google", "iss", "sub"))
        self.assertIsNone(self.repos.sessions.get("h1"))
        self.assertEqual(self.db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        self.assertTrue(self.repos.auth_transactions.consume_once("t1", now=1_501.0))
        self.assertFalse(self.repos.auth_transactions.consume_once("t1", now=1_502.0))

    def test_claim_and_login_commits_once(self):
        self.repos.auth_transactions.create("t2", preauth_binding_hash="b", intent=AuthIntent.LOGIN,
                                            provider="google", expires_at=2_000.0)
        with self.db.transaction(immediate=True):
            self.assertTrue(self.repos.auth_transactions.consume_once("t2", now=1_500.0))
            user = self.repos.users.create(user_id="u2")
            self.repos.identities.add("u2", "google", "iss2", "sub2")
            self.repos.sessions.create("u2", id_hash="h2", csrf_hash="c2", ttl_seconds=100, absolute_ttl_seconds=200)
        self.assertIsNotNone(self.repos.auth_transactions.get("t2").consumed_at)
        self.assertIsNotNone(self.repos.identities.get("google", "iss2", "sub2"))
        self.assertIsNotNone(self.repos.sessions.get("h2"))
        self.assertFalse(self.repos.auth_transactions.consume_once("t2", now=1_501.0))


class AuthTransactionTests(RepoTestCase):
    def make(self, token_hash="t1"):
        return self.repos.auth_transactions.create(
            token_hash, preauth_binding_hash="b", intent=AuthIntent.LOGIN, provider="google", expires_at=2_000.0)

    def test_one_time_claim_is_exclusive(self):
        self.make()
        self.assertTrue(self.repos.auth_transactions.consume_once("t1", now=1_500.0))
        self.assertFalse(self.repos.auth_transactions.consume_once("t1", now=1_501.0))

    def test_expired_token_cannot_be_consumed(self):
        self.make()
        self.assertFalse(self.repos.auth_transactions.consume_once("t1", now=2_001.0))

    def test_concurrent_claims_yield_exactly_one_success(self):
        self.make()
        results = []
        barrier = threading.Barrier(2)

        def worker():
            other = Database(self.path)
            try:
                repos = Repositories(other)
                barrier.wait()
                results.append(repos.auth_transactions.consume_once("t1", now=1_500.0))
            finally:
                other.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), [False, True])

    def test_attempts_increment(self):
        self.make()
        self.assertEqual(self.repos.auth_transactions.increment_attempts("t1"), 1)
        self.assertEqual(self.repos.auth_transactions.increment_attempts("t1"), 2)


class PurchaseAndEntitlementTests(RepoTestCase):
    def test_product_catalog(self):
        self.add_product()
        self.assertEqual([p.id for p in self.repos.products.list_active()], ["python-lifetime"])
        self.assertTrue(self.repos.products.get("python-lifetime").active)

    def test_purchase_unique_binding_and_revision_guard(self):
        user = self.repos.users.create()
        self.add_product()
        purchase = self.repos.purchases.create(user.id, "python-lifetime", "mock", "prov-1")
        self.assertEqual(purchase.state, PurchaseState.PENDING)
        with self.assertRaises(ConflictError):
            self.repos.purchases.create(user.id, "python-lifetime", "mock", "prov-1")
        self.assertEqual(self.repos.purchases.get_by_provider_id("mock", "prov-1").id, purchase.id)
        self.assertTrue(self.repos.purchases.compare_and_swap(purchase.id, 0, state=PurchaseState.PAID, revision=1))
        self.assertFalse(self.repos.purchases.compare_and_swap(purchase.id, 0, state=PurchaseState.REFUNDED, revision=2))
        self.assertEqual(self.repos.purchases.get(purchase.id).state, PurchaseState.PAID)

    def test_entitlement_grant_is_idempotent_and_refund_is_scoped(self):
        user = self.repos.users.create()
        self.add_product()
        p1 = self.repos.purchases.create(user.id, "python-lifetime", "mock", "prov-1")
        p2 = self.repos.purchases.create(user.id, "python-lifetime", "mock", "prov-2")
        first = self.repos.entitlements.grant(user.id, "python-lifetime", p1.id)
        again = self.repos.entitlements.grant(user.id, "python-lifetime", p1.id)
        self.assertEqual(first.id, again.id)
        self.repos.entitlements.grant(user.id, "python-lifetime", p2.id)
        self.assertTrue(self.repos.entitlements.revoke(p1.id, "python-lifetime"))
        active = self.repos.entitlements.list_active_for_user(user.id)
        self.assertEqual([e.source_purchase_id for e in active], [p2.id])
        self.assertEqual(self.repos.entitlements.get(p1.id, "python-lifetime").state, EntitlementState.REVOKED)


class PaymentEventTests(RepoTestCase):
    def test_insert_duplicate_and_conflict(self):
        self.assertEqual(self.repos.payment_events.record("mock", "evt-1", "digest-a", purchase_id=None,
                                                          normalized_state="paid", revision=1,
                                                          status=PaymentEventStatus.APPLIED), "inserted")
        self.assertEqual(self.repos.payment_events.record("mock", "evt-1", "digest-a", purchase_id=None,
                                                          normalized_state="paid", revision=1), "duplicate")
        with self.assertRaises(ConflictError):
            self.repos.payment_events.record("mock", "evt-1", "digest-b", purchase_id=None,
                                             normalized_state="paid", revision=1)
        self.assertEqual(self.repos.payment_events.get("mock", "evt-1").payload_digest, "digest-a")


CORE_ACK = tuple(sorted(("terms", "immediate_delivery_request", "withdrawal_acknowledgement")))


class ConsentTestCase(RepoTestCase):
    def bundle(self, *, bundle_id="pb-1", product_id="python-lifetime", locale="en", version="2026-10-16",
               required=CORE_ACK, effective_at=1_000.0, digest=None):
        if digest is None:
            digest = compute_bundle_digest(product_id=product_id, access_scope="python", locale=locale,
                                           version=version, terms_sha256="a" * 64, refunds_sha256="b" * 64,
                                           privacy_sha256="c" * 64, required_acknowledgements=required,
                                           effective_at=effective_at)
        return PolicyBundle(bundle_id, product_id, "python", locale, version, "a" * 64, "b" * 64, "c" * 64,
                            tuple(required), digest, effective_at)

    def purchase(self, product_id="python-lifetime"):
        self.repos.products.upsert(Product(product_id, "Python lifetime", "USD", 500, "python", True))
        user = self.repos.users.create()
        self._prov = getattr(self, "_prov", 0) + 1
        return user, self.repos.purchases.create(user.id, product_id, "mock", f"prov-{self._prov}")


class PolicyBundleTests(ConsentTestCase):
    def test_insert_is_idempotent_and_getable(self):
        bundle = self.bundle()
        self.repos.policies.insert(bundle)
        self.repos.policies.insert(bundle)  # identical repeat is a no-op
        self.assertEqual(self.repos.policies.get("pb-1"), bundle)
        self.assertEqual([b.id for b in self.repos.policies.list_for_product("python-lifetime")], ["pb-1"])

    def test_changed_content_or_digest_is_rejected(self):
        self.repos.policies.insert(self.bundle())
        with self.assertRaises(ConflictError):
            self.repos.policies.insert(self.bundle(version="2026-10-17"))  # same id, different content
        with self.assertRaises(ConflictError):
            self.repos.policies.insert(self.bundle(bundle_id="pb-2", digest="0" * 64))  # digest mismatch
        with self.assertRaises(ConflictError):
            self.repos.policies.insert(self.bundle(bundle_id="pb-3", version="2026-10-16"))  # UNIQUE(product,version,locale)

    def test_acknowledgement_validation(self):
        for required in (("terms", "immediate_delivery_request"),  # missing withdrawal
                         ("terms", "immediate_delivery_request", "withdrawal_acknowledgement", "nope"),
                         ("terms", "terms", "immediate_delivery_request", "withdrawal_acknowledgement")):
            with self.subTest(required=required):
                with self.assertRaises(ConflictError):
                    self.repos.policies.insert(self.bundle(bundle_id="x-" + str(len(required)), required=required))

    def test_ack_order_does_not_change_digest(self):
        a = compute_bundle_digest(product_id="p", access_scope="s", locale="en", version="v", terms_sha256="a" * 64,
                                  refunds_sha256="b" * 64, privacy_sha256="c" * 64,
                                  required_acknowledgements=CORE_ACK, effective_at=1.0)
        b = compute_bundle_digest(product_id="p", access_scope="s", locale="en", version="v", terms_sha256="a" * 64,
                                  refunds_sha256="b" * 64, privacy_sha256="c" * 64,
                                  required_acknowledgements=tuple(reversed(CORE_ACK)), effective_at=1.0)
        self.assertEqual(a, b)


class ConsentTests(ConsentTestCase):
    def test_record_derives_user_and_time_and_snapshots(self):
        user, purchase = self.purchase()
        bundle = self.bundle()
        self.repos.policies.insert(bundle)
        consent = self.repos.consents.record(purchase.id, "pb-1", list(reversed(CORE_ACK)))
        self.assertEqual(consent.user_id, user.id)
        self.assertEqual(consent.accepted_at, 1_000.0)
        self.assertEqual(consent.bundle_version, "2026-10-16")
        self.assertEqual(consent.bundle_digest, bundle.bundle_digest)
        self.assertEqual(consent.acknowledgements, tuple(sorted(CORE_ACK)))
        self.assertEqual(self.repos.consents.get_for_purchase(purchase.id).id, consent.id)
        self.assertEqual(len(self.repos.consents.list_for_user(user.id)), 1)

    def test_acknowledgement_mismatch_and_unknown_ids(self):
        _, purchase = self.purchase()
        self.repos.policies.insert(self.bundle())
        for bad in (["terms"], CORE_ACK + ("privacy",), ["terms", "terms", "immediate_delivery_request",
                                                         "withdrawal_acknowledgement"], ["refunds"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ConflictError):
                    self.repos.consents.record(purchase.id, "pb-1", bad)
        with self.assertRaises(NotFoundError):
            self.repos.consents.record("missing-purchase", "pb-1", CORE_ACK)
        with self.assertRaises(NotFoundError):
            self.repos.consents.record(purchase.id, "missing-bundle", CORE_ACK)

    def test_duplicate_consent_and_other_product_bundle(self):
        _, purchase = self.purchase()
        self.repos.policies.insert(self.bundle())
        self.repos.consents.record(purchase.id, "pb-1", CORE_ACK)
        with self.assertRaises(ConflictError):
            self.repos.consents.record(purchase.id, "pb-1", CORE_ACK)
        # A bundle for another product cannot be consumed for this purchase.
        self.repos.products.upsert(Product("other-product", "Other", "USD", 500, "python", True))
        other = self.bundle(bundle_id="pb-other", product_id="other-product")
        self.repos.policies.insert(other)
        _, purchase2 = self.purchase()
        with self.assertRaises(ConflictError):
            self.repos.consents.record(purchase2.id, "pb-other", CORE_ACK)

    def test_consent_row_is_immutable(self):
        _, purchase = self.purchase()
        self.repos.policies.insert(self.bundle())
        self.repos.consents.record(purchase.id, "pb-1", CORE_ACK)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction(immediate=True) as conn:
                conn.execute("UPDATE purchase_consents SET accepted_at = 1 WHERE purchase_id = ?", (purchase.id,))

    def test_policy_bundle_row_is_immutable(self):
        self.repos.policies.insert(self.bundle())
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction(immediate=True) as conn:
                conn.execute("UPDATE policy_bundles SET version = 'x' WHERE id = 'pb-1'")

    def test_restrict_prevents_hard_delete_with_consent(self):
        _, purchase = self.purchase()
        self.repos.policies.insert(self.bundle())
        self.repos.consents.record(purchase.id, "pb-1", CORE_ACK)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction(immediate=True) as conn:
                conn.execute("DELETE FROM purchases WHERE id = ?", (purchase.id,))

    def test_atomic_purchase_and_consent_roll_back_together(self):
        user = self.repos.users.create(user_id="u-atomic")
        self.repos.products.upsert(Product("python-lifetime", "Python lifetime", "USD", 500, "python", True))
        self.repos.policies.insert(self.bundle())
        with self.assertRaises(ConflictError):
            with self.db.transaction(immediate=True):
                purchase = self.repos.purchases.create(user.id, "python-lifetime", "mock", "prov-atomic")
                self.repos.consents.record(purchase.id, "pb-1", ["terms"])  # invalid ack set
        self.assertIsNone(self.repos.purchases.get_by_provider_id("mock", "prov-atomic"))


class ActivationTests(ConsentTestCase):
    def test_resolve_active_bundle_rules(self):
        bundle = self.bundle()
        good = PolicyActivation("python-lifetime", "python", "en", "pb-1", approved=True)
        self.assertEqual(resolve_active_bundle([good], [bundle], product_id="python-lifetime", access_scope="python",
                                               locale="en", now=1_500.0, app_env="development"), bundle)
        # not approved
        self.assertIsNone(resolve_active_bundle([PolicyActivation("python-lifetime", "python", "en", "pb-1", False)],
                                                [bundle], product_id="python-lifetime", access_scope="python",
                                                locale="en", now=1_500.0, app_env="development"))
        # duplicate activations
        self.assertIsNone(resolve_active_bundle([good, good], [bundle], product_id="python-lifetime", access_scope="python",
                                               locale="en", now=1_500.0, app_env="development"))
        # product/scope/locale mismatch
        self.assertIsNone(resolve_active_bundle([good], [bundle], product_id="other", access_scope="python",
                                               locale="en", now=1_500.0, app_env="development"))
        # future effective_at
        self.assertIsNone(resolve_active_bundle([good], [bundle], product_id="python-lifetime", access_scope="python",
                                               locale="en", now=999.0, app_env="development"))

    def test_test_only_is_refused_in_production(self):
        bundle = self.bundle()
        activation = PolicyActivation("python-lifetime", "python", "en", "pb-1", approved=True, test_only=True)
        self.assertEqual(resolve_active_bundle([activation], [bundle], product_id="python-lifetime", access_scope="python",
                                               locale="en", now=1_500.0, app_env="test"), bundle)
        self.assertIsNone(resolve_active_bundle([activation], [bundle], product_id="python-lifetime", access_scope="python",
                                                locale="en", now=1_500.0, app_env="production"))

    def test_unknown_app_env_fails_closed(self):
        with self.assertRaises(SetupError):
            resolve_active_bundle([], [], product_id="p", access_scope="s", locale="en", now=1.0, app_env="staging")


if __name__ == "__main__":
    unittest.main()
