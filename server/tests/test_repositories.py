import os
import tempfile
import threading
import unittest

from server.db import Database
from server.models import (AuthIntent, ConflictError, EntitlementState, PaymentEventStatus, Product,
                           PurchaseState, UserStatus)
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


if __name__ == "__main__":
    unittest.main()
