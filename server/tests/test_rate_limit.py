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

    def test_ip_limit_and_lru_capacity_are_bounded(self):
        now = [10.0]
        limiter = EmailStartLimiter(b"secret" * 8, clock=lambda: now[0], per_email=9, per_ip=2, max_keys=2)
        self.assertEqual(limiter.consume("a@example.test", "192.0.2.4")[0], True)
        self.assertEqual(limiter.consume("b@example.test", "192.0.2.4")[0], True)
        self.assertEqual(limiter.consume("c@example.test", "192.0.2.4")[0], False)
        self.assertLessEqual(len(limiter._events), 2)


if __name__ == "__main__":
    unittest.main()
