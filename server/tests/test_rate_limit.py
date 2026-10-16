import unittest

from server.rate_limit import EmailStartLimiter


class EmailStartLimiterTests(unittest.TestCase):
    def test_limits_email_and_ip_without_retaining_identifiers_and_resets(self):
        now = [3600.0]
        limiter = EmailStartLimiter(b"secret" * 8, clock=lambda: now[0], per_email=2, per_ip=3, max_keys=4)
        self.assertEqual(limiter.consume("person@example.test", "192.0.2.1"), (True, 0))
        self.assertEqual(limiter.consume("person@example.test", "192.0.2.1"), (True, 0))
        self.assertEqual(limiter.consume("person@example.test", "192.0.2.1"), (False, 3600))
        self.assertNotIn("person@example.test", repr(limiter._events))
        self.assertNotIn("192.0.2.1", repr(limiter._events))
        now[0] = 7199.0
        self.assertEqual(limiter.consume("person@example.test", "192.0.2.1"), (False, 1))
        now[0] = 7200.0
        self.assertEqual(limiter.consume("person@example.test", "192.0.2.1"), (True, 0))

    def test_case_and_unicode_normalization_share_the_email_quota(self):
        now = [100.0]
        limiter = EmailStartLimiter(b"secret" * 8, clock=lambda: now[0], per_email=2, per_ip=100)
        self.assertEqual(limiter.consume("Éxample@domain.test", "192.0.2.1")[0], True)
        self.assertEqual(limiter.consume("e\u0301XAMPLE@DOMAIN.TEST", "192.0.2.2")[0], True)
        self.assertEqual(limiter.consume("ÉXAMPLE@DOMAIN.TEST", "192.0.2.3")[0], False)

    def test_ipv6_addresses_in_same_64_share_ip_quota(self):
        now = [100.0]
        limiter = EmailStartLimiter(b"secret" * 8, clock=lambda: now[0], per_email=100, per_ip=2)
        self.assertEqual(limiter.consume("a@one.test", "2001:db8:abcd:12::1")[0], True)
        self.assertEqual(limiter.consume("b@two.test", "2001:db8:abcd:12::2")[0], True)
        self.assertEqual(limiter.consume("c@three.test", "2001:db8:abcd:12::ffff")[0], False)

    def test_ipv4_mapped_ipv6_clients_receive_independent_ip_quotas(self):
        now = [100.0]
        limiter = EmailStartLimiter(b"secret" * 8, clock=lambda: now[0], per_email=100, per_ip=3)
        for client in range(1, 7):
            address = f"::ffff:198.51.100.{client}"
            for attempt in range(3):
                allowed, _ = limiter.consume(f"client{client}-{attempt}@example.test", address)
                self.assertTrue(allowed, (client, attempt))
            allowed, _ = limiter.consume(f"client{client}-blocked@example.test", address)
            self.assertFalse(allowed, client)

    def test_ipv4_mapped_address_shares_native_ipv4_quota(self):
        now = [100.0]
        limiter = EmailStartLimiter(b"secret" * 8, clock=lambda: now[0], per_email=100, per_ip=2)
        self.assertEqual(limiter.consume("one@example.test", "::ffff:198.51.100.7"), (True, 0))
        self.assertEqual(limiter.consume("two@example.test", "198.51.100.7"), (True, 0))
        self.assertEqual(limiter.consume("three@example.test", "::ffff:198.51.100.7")[0], False)

    def test_ip_limit_and_lru_capacity_are_bounded(self):
        now = [10.0]
        limiter = EmailStartLimiter(b"secret" * 8, clock=lambda: now[0], per_email=9, per_ip=2, max_keys=2)
        self.assertEqual(limiter.consume("a@example.test", "192.0.2.4")[0], True)
        self.assertEqual(limiter.consume("b@example.test", "192.0.2.4")[0], True)
        self.assertEqual(limiter.consume("c@example.test", "192.0.2.4")[0], False)
        self.assertLessEqual(len(limiter._events), 2)


if __name__ == "__main__":
    unittest.main()
