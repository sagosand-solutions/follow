# SPDX-License-Identifier: MIT
"""Offline Paddle building blocks: signature, envelope, host/key policy and the state decision (synthetic only).

Every vector here is constructed locally with a synthetic secret. It proves self-consistency, not compatibility with
the real service (Paddle publishes no test vector).
"""
import hashlib
import hmac
import json
import unittest

from server.paddle import digest, mapping, policy, signature
from server.paddle.types import (Adjustment, AuthoritativeState, Expected, Item, PaddleConfigError, PaddleMalformed,
                                 PaddleSignatureInvalid, PaddleStaleTimestamp, PaddleTransportRejected, VerifiedEvent)

SECRET = "synthetic-endpoint-secret-0123456789"
OTHER = "another-synthetic-secret-abcdefghij"
NOW = 1_700_000_000.0
TXN, ADJ, PRICE = "txn_01abcdefghjkmnpqrstvwxyz00", "adj_01abcdefghjkmnpqrstvwxyz00", "pri_01abcdefghjkmnpqrstvwxyz00"


def sign(raw: bytes, ts: int = int(NOW), secret: str = SECRET) -> str:
    return f"ts={ts};h1=" + hmac.new(secret.encode(), f"{ts}:".encode() + raw, hashlib.sha256).hexdigest()


def envelope(event_type="transaction.completed", data=None, **over) -> bytes:
    body = {"event_id": "evt_01abcdefghjkmnpqrstvwxyz01", "event_type": event_type, "occurred_at": "2026-10-19T01:02:03.456Z",
            "notification_id": "ntf_01abcdefghjkmnpqrstvwxyz01",
            "data": data if data is not None else {"id": TXN, "status": "completed", "currency_code": "USD",
                                                   "items": [{"price": {"id": PRICE}, "quantity": 1}],
                                                   "details": {"totals": {"subtotal": "1000", "tax": "0", "total": "1000", "grand_total": "1000"}}}}
    body.update(over)
    return json.dumps(body, separators=(",", ":")).encode()


class SignatureTests(unittest.TestCase):
    def test_valid_signature_returns_the_timestamp(self):
        raw = envelope()
        self.assertEqual(signature.verify_signature(raw, sign(raw), SECRET, NOW), int(NOW))

    def test_signature_is_over_the_original_bytes(self):
        raw = b'{"a": 1,\r\n "b":  "caf\xc3\xa9 \xe2\x80\x8b"}  \n'
        self.assertEqual(signature.verify_signature(raw, sign(raw), SECRET, NOW), int(NOW))
        for mutated in (raw.replace(b"1", b"2"), raw + b" ", raw.replace(b"  ", b" "), json.dumps(json.loads(raw)).encode()):
            with self.subTest(mutated=mutated[:20]), self.assertRaises(PaddleSignatureInvalid):
                signature.verify_signature(mutated, sign(raw), SECRET, NOW)

    def test_wrong_secret_and_wrong_timestamp_in_the_signed_string(self):
        raw = envelope()
        with self.assertRaises(PaddleSignatureInvalid):
            signature.verify_signature(raw, sign(raw, secret=OTHER), SECRET, NOW)
        header = sign(raw, ts=int(NOW) - 1).replace(f"ts={int(NOW) - 1}", f"ts={int(NOW)}")  # ts tampered after signing
        with self.assertRaises(PaddleSignatureInvalid):
            signature.verify_signature(raw, header, SECRET, NOW)

    def test_tolerance_applies_to_old_and_future_timestamps_but_never_replaces_dedup(self):
        raw = envelope()
        for delta in (5, -5, 0):
            signature.verify_signature(raw, sign(raw, int(NOW) - delta), SECRET, NOW)
        for delta in (6, -6, 3600):
            with self.subTest(delta=delta), self.assertRaises(PaddleStaleTimestamp):
                signature.verify_signature(raw, sign(raw, int(NOW) - delta), SECRET, NOW)
        signature.verify_signature(raw, sign(raw, int(NOW) - 60), SECRET, NOW, tolerance=120)  # configurable

    def test_bad_signature_is_reported_before_staleness(self):
        raw = envelope()
        with self.assertRaises(PaddleSignatureInvalid):
            signature.verify_signature(raw, sign(raw, int(NOW) - 3600, secret=OTHER), SECRET, NOW)

    def test_multiple_h1_values_any_may_match_and_all_are_compared(self):
        raw = envelope()
        good = sign(raw)
        bad = "h1=" + "0" * 64
        signature.verify_signature(raw, f"{good};{bad}", SECRET, NOW)
        signature.verify_signature(raw, good.replace(";h1=", f";{bad};h1="), SECRET, NOW)
        rotated = "h1=" + sign(raw, secret=OTHER).split("h1=")[1]
        signature.verify_signature(raw, f"{good};{rotated}", SECRET, NOW)
        with self.assertRaises(PaddleSignatureInvalid):
            signature.verify_signature(raw, f"ts={int(NOW)};{bad};{rotated}", SECRET, NOW)

    def test_strict_header_parsing(self):
        raw = envelope()
        h1 = sign(raw).split("h1=")[1]
        bad = ["", "ts=1", f"h1={h1}", f"ts={int(NOW)};ts={int(NOW)};h1={h1}", f"ts={int(NOW)};h1={h1};x=1",
               f"ts={int(NOW)};h1={h1.upper()}", f"ts={int(NOW)};h1={h1[:-1]}", f"ts=-1;h1={h1}", f"ts=1e9;h1={h1}",
               f"ts={int(NOW)};h1=", f"ts={int(NOW)};;h1={h1}", f"ts={int(NOW)} ;h1={h1}", f"ts={int(NOW)};h1={h1};h1={h1};h1={h1};h1={h1};h1={h1}",
               "ts=" + "9" * 13 + f";h1={h1}", "ts=١٢٣;h1=" + h1, None, 5, "x" * 2000]
        for header in bad:
            with self.subTest(header=str(header)[:40]), self.assertRaises(PaddleMalformed):
                signature.verify_signature(raw, header, SECRET, NOW)

    def test_body_and_secret_limits_and_clock(self):
        with self.assertRaises(PaddleMalformed):
            signature.verify_signature(b"x" * (signature.MAX_BODY_BYTES + 1), sign(b"x"), SECRET, NOW)
        with self.assertRaises(PaddleConfigError):     # our own configuration, not the caller's malformed request
            signature.verify_signature(envelope(), sign(envelope()), "short", NOW)
        for clock in (float("nan"), float("inf"), None, True):
            with self.subTest(clock=clock), self.assertRaises(PaddleConfigError):
                signature.verify_signature(envelope(), sign(envelope()), SECRET, clock)

    def test_comparison_is_constant_time_by_construction(self):
        import inspect
        source = inspect.getsource(signature.verify_signature)
        self.assertIn("compare_digest", source)
        self.assertNotIn("== candidate", source)


class WebhookTests(unittest.TestCase):
    def verify(self, raw, header=None, **kw):
        return signature.verify_webhook(raw, {"Paddle-Signature": header or sign(raw)}, NOW, secret=SECRET, environment="sandbox", **kw)

    def test_transaction_event_is_normalised_with_a_raw_body_digest(self):
        raw = envelope()
        event = self.verify(raw)
        self.assertEqual((event.provider, event.environment, event.event_type, event.transaction_id, event.adjustment_id),
                         ("paddle", "sandbox", "transaction.completed", TXN, None))
        self.assertEqual(event.payload_digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(event.data["items"], [{"price_id": PRICE, "quantity": 1}])
        self.assertEqual(event.data["totals"]["grand_total"], 1000)

    def test_adjustment_event_is_normalised(self):
        raw = envelope("adjustment.created", {"id": ADJ, "transaction_id": TXN, "action": "refund", "status": "approved", "type": "full",
                                                "totals": {"total": "1000", "currency_code": "USD"}})
        event = self.verify(raw)
        self.assertEqual((event.adjustment_id, event.transaction_id, event.data["action"], event.data["total_minor"]), (ADJ, TXN, "refund", 1000))

    def test_header_lookup_is_case_insensitive_but_ambiguity_is_refused(self):
        raw = envelope()
        signature.verify_webhook(raw, {"paddle-signature": sign(raw)}, NOW, secret=SECRET, environment="live")
        for headers in ({}, {"Paddle-Signature": sign(raw), "paddle-signature": sign(raw)}, {"Paddle-Signature": [sign(raw), sign(raw)]}):
            with self.subTest(headers=list(headers)), self.assertRaises(PaddleMalformed):
                signature.verify_webhook(raw, headers, NOW, secret=SECRET, environment="sandbox")

    def test_signature_is_checked_before_any_parsing(self):
        raw = b"not json at all"
        with self.assertRaises(PaddleSignatureInvalid):
            signature.verify_webhook(raw, {"Paddle-Signature": sign(raw, secret=OTHER)}, NOW, secret=SECRET, environment="sandbox")
        with self.assertRaises(PaddleMalformed):  # signed but not JSON
            self.verify(raw)

    def test_malformed_envelopes_are_typed_errors(self):
        cases = {
            "not_object": b"[]", "no_data": envelope(data=None).replace(b'"data":{', b'"x":{'),
            "bad_event_id": envelope(event_id="evt_x"), "bad_notification": envelope(notification_id="n"),
            "bad_type": envelope(event_type="Transaction.Completed"), "bad_time": envelope(occurred_at="yesterday"),
            "no_txn_id": envelope(data={"status": "completed"}),
            "bad_item": envelope(data={"id": TXN, "status": "completed", "items": [{"price": {"id": "x"}, "quantity": 1}]}),
            "bool_quantity": envelope(data={"id": TXN, "status": "completed", "items": [{"price": {"id": PRICE}, "quantity": True}]}),
            "float_total": envelope(data={"id": TXN, "status": "completed", "details": {"totals": {"total": 10.5}}}),
            "bad_currency": envelope(data={"id": TXN, "status": "completed", "currency_code": "usd"}),
            "bad_adjustment": envelope("adjustment.created", {"id": ADJ, "transaction_id": "x"}),
            "items_is_a_dict": envelope(data={"id": TXN, "status": "completed", "items": {"price": {"id": PRICE}}}),
            "items_is_a_string": envelope(data={"id": TXN, "status": "completed", "items": "x"}),
            "item_is_a_string": envelope(data={"id": TXN, "status": "completed", "items": ["x"]}),
            "item_is_null": envelope(data={"id": TXN, "status": "completed", "items": [None]}),
            "price_is_a_string": envelope(data={"id": TXN, "status": "completed", "items": [{"price": "pri_x", "quantity": 1}]}),
            "price_is_null": envelope(data={"id": TXN, "status": "completed", "items": [{"price": None, "quantity": 1}]}),
        }
        for name, raw in cases.items():
            with self.subTest(name), self.assertRaises(PaddleMalformed):
                self.verify(raw)

    def test_environment_must_be_configured_and_unknown_entities_carry_no_ids(self):
        raw = envelope("subscription.created", {"id": "sub_01abcdefghjkmnpqrstvwxyz00"})
        event = self.verify(raw)
        self.assertEqual((event.transaction_id, event.adjustment_id, event.data), (None, None, {}))
        with self.assertRaises(PaddleConfigError):
            signature.verify_webhook(raw, {"Paddle-Signature": sign(raw)}, NOW, secret=SECRET, environment="staging")

    def test_dedup_key_is_separate_from_the_digest(self):
        self.assertEqual(signature.classify_dedup(None, "a" * 64), "new")
        self.assertEqual(signature.classify_dedup("a" * 64, "a" * 64), "duplicate")
        self.assertEqual(signature.classify_dedup("a" * 64, "b" * 64), "conflict")
        a, b = envelope(), envelope(occurred_at="2026-10-19T01:02:04.000Z")
        ea, eb = self.verify(a), self.verify(b)
        self.assertEqual(ea.event_id, eb.event_id)
        self.assertEqual(signature.classify_dedup(ea.payload_digest, eb.payload_digest), "conflict")


class StrictInputTests(unittest.TestCase):
    """Signed-but-malformed payloads, non-canonical timestamps, bad tolerances and URL parser errors (engineer return)."""

    def verify(self, raw, **kw):
        return signature.verify_webhook(raw, {"Paddle-Signature": sign(raw)}, NOW, secret=SECRET, environment="sandbox", **kw)

    def test_signed_malformed_bodies_are_typed_errors_not_raw_exceptions(self):
        good = envelope()
        txn = '{"id":"%s","status":"completed"%%s}' % TXN
        def body(data_fragment):
            return ('{"event_id":"evt_01abcdefghjkmnpqrstvwxyz01","event_type":"transaction.completed",'
                    '"occurred_at":"2026-10-19T01:02:03Z","notification_id":"ntf_01abcdefghjkmnpqrstvwxyz01","data":%s}' % data_fragment).encode()
        cases = {
            "items_integer": body(txn % ',"items":1'), "items_list_of_int": body(txn % ',"items":[1]'),
            "items_list_of_list": body(txn % ',"items":[[]]'), "items_null_item": body(txn % ',"items":[null]'),
            "details_not_object": body(txn % ',"details":[]'), "totals_not_object": body(txn % ',"details":{"totals":5}'),
            "duplicate_keys": good.replace(b'"status":"completed"', b'"status":"completed","status":"paid"'),
            "nan": body(txn % ',"x":NaN'), "infinity": body(txn % ',"x":Infinity'), "minus_infinity": body(txn % ',"x":-Infinity'),
            "overflowing_number": body(txn % ',"x":1e999'),
            "deep_nesting": b"[" * 100_000 + b"]" * 100_000,
            "deep_object": b'{"a":' * 50_000 + b"1" + b"}" * 50_000,
            "bad_utf8": b'{"event_id":"\xff"}'.replace(b"\\xff", b"\xff"), "empty": b"", "top_level_string": b'"x"',
        }
        for name, raw in cases.items():
            with self.subTest(name), self.assertRaises(PaddleMalformed):
                self.verify(raw)

    def test_occurred_at_must_be_a_full_rfc3339_timestamp_with_a_zone(self):
        for bad in ("2026-10-19", "2026-10-19T01:02:03", "2026-10-19 01:02:03Z", "2026-10-19T01:02Z", "2026-10-19T25:00:00Z",
                    "2026-13-01T00:00:00Z", "2026-10-19T01:02:03+0100", "2026-10-19T01:02:03+00:60", "2026-10-19T01:02:03-05:99", "2026-10-19T01:02:03+24:00", "2026-10-19T01:02:03.Z", "20261019T010203Z", ""):
            with self.subTest(bad=bad), self.assertRaises(PaddleMalformed):
                self.verify(envelope(occurred_at=bad))
        for good in ("2026-10-19T01:02:03Z", "2026-10-19T01:02:03.456789123Z", "2026-10-19T01:02:03+02:00", "2026-10-19T01:02:03-23:59", "2026-10-19t01:02:03z"):
            with self.subTest(good=good):
                self.assertEqual(self.verify(envelope(occurred_at=good)).occurred_at, good)

    def test_timestamp_must_be_canonical_and_the_signed_bytes_are_exactly_the_header_string(self):
        raw = envelope()
        for ts in ("0001700000000", "00", "01", "+1700000000", " 1700000000"):
            h1 = hmac.new(SECRET.encode(), f"{int(ts) if ts.strip('+ ').isdigit() else 0}:".encode() + raw, hashlib.sha256).hexdigest()
            with self.subTest(ts=ts), self.assertRaises(PaddleMalformed):
                signature.verify_signature(raw, f"ts={ts};h1={h1}", SECRET, NOW)
        exact = hmac.new(SECRET.encode(), f"{int(NOW)}:".encode() + raw, hashlib.sha256).hexdigest()
        self.assertEqual(signature.verify_signature(raw, f"ts={int(NOW)};h1={exact}", SECRET, NOW), int(NOW))
        self.assertEqual(signature.parse_signature_header("ts=0;h1=" + "a" * 64)[0], 0)

    def test_tolerance_must_be_a_finite_non_negative_number(self):
        raw = envelope()
        for bad in (float("nan"), float("inf"), float("-inf"), -1, "5", None, True, False, [5]):
            with self.subTest(tolerance=repr(bad)), self.assertRaises(PaddleConfigError):
                signature.verify_signature(raw, sign(raw), SECRET, NOW, tolerance=bad)
        with self.assertRaises(PaddleStaleTimestamp):  # an old timestamp is NOT waved through by a bad tolerance
            signature.verify_signature(raw, sign(raw, int(NOW) - 3600), SECRET, NOW, tolerance=5)
        with self.assertRaises(PaddleStaleTimestamp):
            signature.verify_signature(raw, sign(raw, int(NOW) + 3600), SECRET, NOW, tolerance=0)
        signature.verify_signature(raw, sign(raw, int(NOW) - 3600), SECRET, NOW, tolerance=3600)
        signature.verify_signature(raw, sign(raw), SECRET, NOW, tolerance=0)

    def test_url_parser_errors_become_typed_rejections(self):
        for url in ("https://api.paddle.com:abc/x", "https://api.paddle.com:99999999/x", "https://@api.paddle.com/x", "https://:@api.paddle.com/x", "https://:pw@api.paddle.com/x", "https://user:@api.paddle.com/x", "https://[::1/x", "https://[api.paddle.com]/x",
                    "https://api.paddle.com\n/x", "https://api.paddle.com/x\t", "https://api.paddle.com/ x", "https://api.paddle.com\\@evil.example/",
                    "https://api.pad\u00e4le.com/x", "https://api.paddle.com/" + "a" * 3000, "", None, 5, b"https://api.paddle.com/"):
            with self.subTest(url=repr(url)[:40]), self.assertRaises(PaddleTransportRejected):
                policy.check_response_url("live", url)
        for env in ("staging", None, "", 5):
            with self.subTest(env=env), self.assertRaises(PaddleConfigError):
                policy.check_response_url(env, "https://api.paddle.com/x")


class PolicyTests(unittest.TestCase):
    KEY_S, KEY_L = "pdl_sdbx_apikey_" + "a" * 30, "pdl_live_apikey_" + "b" * 30

    def test_hosts_are_constants_and_urls_are_built_only_from_them(self):
        self.assertEqual(policy.api_url("live", "/transactions/txn_1"), "https://api.paddle.com/transactions/txn_1")
        self.assertEqual(policy.api_url("sandbox", "/transactions"), "https://sandbox-api.paddle.com/transactions")
        for bad in ("transactions", "//evil.example/x", "/a@evil.example", "/a#b", "/a\\b", "/a b", ""):
            with self.subTest(path=bad), self.assertRaises(PaddleTransportRejected):
                policy.api_url("live", bad)
        with self.assertRaises(PaddleConfigError):
            policy.api_url("staging", "/x")

    def test_response_url_must_be_the_constant_host_over_tls(self):
        policy.check_response_url("live", "https://api.paddle.com/transactions")
        policy.check_response_url("sandbox", "https://sandbox-api.paddle.com:443/x")
        for url in ("http://api.paddle.com/x", "https://sandbox-api.paddle.com/x", "https://api.paddle.com.evil.example/x",
                    "https://user@api.paddle.com/x", "https://api.paddle.com:8443/x", "https://evil.example/x"):
            with self.subTest(url=url), self.assertRaises(PaddleTransportRejected):
                policy.check_response_url("live", url)

    def test_key_prefix_must_match_the_environment_and_production_is_refused(self):
        ok = dict(endpoint_secret=SECRET, app_env="test")
        self.assertEqual(policy.validate_config(paddle_env="sandbox", api_key=self.KEY_S, **ok), "sandbox")
        self.assertEqual(policy.validate_config(paddle_env="live", api_key=self.KEY_L, **ok), "live")
        bad = [dict(paddle_env="live", api_key=self.KEY_S), dict(paddle_env="sandbox", api_key=self.KEY_L),
               dict(paddle_env="staging", api_key=self.KEY_S), dict(paddle_env="sandbox", api_key="pdl_sdbx_apikey_"),
               dict(paddle_env="sandbox", api_key=None), dict(paddle_env="live", api_key=self.KEY_L, transport_is_mock=True),
               dict(paddle_env="sandbox", api_key=self.KEY_S, endpoint_secret="short"),
               dict(paddle_env="sandbox", api_key=self.KEY_S, endpoint_secret=self.KEY_S),
               dict(paddle_env="sandbox", api_key=self.KEY_S, app_env="production")]
        for case in bad:
            with self.subTest(case={k: v for k, v in case.items() if k != "api_key"}), self.assertRaises(PaddleConfigError):
                policy.validate_config(**{**ok, **case})

    def test_errors_never_echo_keys_or_secrets(self):
        try:
            policy.validate_config(paddle_env="live", api_key=self.KEY_S, endpoint_secret=SECRET, app_env="test")
        except PaddleConfigError as exc:
            self.assertNotIn(self.KEY_S, str(exc))
            self.assertNotIn(SECRET, str(exc))


def snapshot(status="completed", adjustments=(), **over):
    base = dict(environment="sandbox", transaction_id=TXN, status=status, currency_code="USD",
                totals={"grand_total": 1000, "total": 1000}, items=(Item(PRICE, 1),), adjustments=tuple(adjustments), fetched_at=NOW,
                complete=True)
    base.update(over)
    return AuthoritativeState(**base)


EXPECTED = Expected("sandbox", PRICE, 1, "USD", transaction_id=TXN, total_minor=1000)


def adj(action="refund", status="approved", type="full", i=1):
    return Adjustment(f"adj_{i:026d}", action, status, type, 1000, "USD")


class DigestTests(unittest.TestCase):
    def test_the_snapshot_digest_is_canonical_and_covers_everything_a_decision_depends_on(self):
        base = snapshot(adjustments=[adj(i=1), adj("credit", "approved", "full", i=2)])
        d = digest.snapshot_digest(base)
        self.assertRegex(d, r"^[0-9a-f]{64}$")
        shuffled = snapshot(adjustments=[adj("credit", "approved", "full", i=2), adj(i=1)], totals={"total": 1000, "grand_total": 1000})
        self.assertEqual(digest.snapshot_digest(shuffled), d)                                   # order never matters
        self.assertEqual(digest.snapshot_digest(snapshot(adjustments=[adj(i=1), adj("credit", "approved", "full", i=2)], fetched_at=NOW + 99)), d)  # nor fetch time
        for name, other in {
            "status": snapshot("paid", base.adjustments), "adjustment_status": snapshot(adjustments=[adj(status="rejected", i=1), adj("credit", "approved", "full", i=2)]),
            "fewer_adjustments": snapshot(adjustments=[adj(i=1)]), "complete": snapshot(adjustments=base.adjustments, complete=False),
            "environment": snapshot(adjustments=base.adjustments, environment="live"), "currency": snapshot(adjustments=base.adjustments, currency_code="EUR"),
            "txn": snapshot(adjustments=base.adjustments, transaction_id="txn_01zzzzzzzzzzzzzzzzzzzzzzzz"),
            "amount": snapshot(adjustments=base.adjustments, totals={"grand_total": 999, "total": 1000}),
            "item": snapshot(adjustments=base.adjustments, items=(Item(PRICE, 2),)),
        }.items():
            with self.subTest(name):
                self.assertNotEqual(digest.snapshot_digest(other), d)


class MappingTests(unittest.TestCase):
    def decide(self, before, snap, expected=EXPECTED):
        return mapping.decide(before, snap, expected)

    def test_completed_with_proof_grants_exactly_once(self):
        d = self.decide("pending", snapshot())
        self.assertEqual((d.action, d.grants), ("paid", True))
        self.assertEqual(self.decide("paid", snapshot()).action, "none")

    def test_an_incomplete_or_foreign_snapshot_never_decides_anything(self):
        for snap in (snapshot(), snapshot(adjustments=[adj()]), snapshot("canceled")):
            for before in ("pending", "paid"):
                with self.subTest(status=snap.status, before=before, kind="incomplete"):
                    d = self.decide(before, snapshot(snap.status, snap.adjustments, complete=False))
                    self.assertEqual((d.action, d.grants), ("reconcile", False))
                with self.subTest(status=snap.status, before=before, kind="other transaction"):
                    d = self.decide(before, snapshot(snap.status, snap.adjustments, transaction_id="txn_01zzzzzzzzzzzzzzzzzzzzzzzz"))
                    self.assertEqual((d.action, d.grants), ("reconcile", False))
        for snap in (snapshot(), snapshot(adjustments=[adj()]), snapshot("canceled"), snapshot("completed", [adj("credit", "approved", "full")])):
            for before in ("pending", "paid"):
                with self.subTest(status=snap.status, before=before, kind="other environment"):
                    d = self.decide(before, snapshot(snap.status, snap.adjustments, environment="live"))
                    self.assertEqual((d.action, d.grants), ("reconcile", False))      # also for refund and cancel
        self.assertEqual(self.decide("pending", snapshot(), Expected("sandbox", PRICE, 1, "USD", total_minor=1000)).action, "reconcile")  # no bound txn
        self.assertEqual(self.decide("pending", snapshot(complete=None)).action, "reconcile")
        self.assertEqual(self.decide("pending", snapshot()).action, "paid")

    def test_proof_failures_never_grant(self):
        cases = {
            "environment": snapshot(environment="live"), "price": snapshot(items=(Item("pri_01zzzzzzzzzzzzzzzzzzzzzzzz", 1),)),
            "quantity": snapshot(items=(Item(PRICE, 2),)), "two_items": snapshot(items=(Item(PRICE, 1), Item(PRICE, 1))),
            "currency": snapshot(currency_code="EUR"), "amount": snapshot(totals={"grand_total": 999}),
            "no_amount": snapshot(totals={}),
        }
        for name, snap in cases.items():
            with self.subTest(name):
                d = self.decide("pending", snap)
                self.assertEqual((d.action, d.grants), ("reconcile", False))

    def test_undecided_amount_policy_blocks_every_grant(self):
        d = self.decide("pending", snapshot(), Expected("sandbox", PRICE, 1, "USD", transaction_id=TXN, total_minor=None))
        self.assertEqual((d.action, d.reason), ("reconcile", "amount policy is not decided"))

    def test_full_approved_refund_observed_before_paid_means_no_grant(self):
        d = self.decide("pending", snapshot(adjustments=[adj()]))
        self.assertEqual((d.action, d.grants), ("refunded", False))
        self.assertEqual(self.decide("paid", snapshot(adjustments=[adj()])).action, "refunded")

    def test_refund_without_a_paid_transaction_is_reconciled(self):
        self.assertEqual(self.decide("pending", snapshot("draft", [adj()])).action, "reconcile")

    def test_terminal_states_never_move_back(self):
        for before in ("refunded", "chargeback", "cancelled"):
            for snap in (snapshot(), snapshot("canceled"), snapshot(adjustments=[adj()])):
                with self.subTest(before=before, status=snap.status):
                    self.assertEqual(self.decide(before, snap).action, "none")

    def test_stale_completed_after_refund_cannot_reactivate(self):
        self.assertEqual(self.decide("refunded", snapshot()).action, "none")

    def test_cancelled_needs_the_authoritative_canceled_status(self):
        for status in ("draft", "ready", "billed", "past_due", "paid"):
            with self.subTest(status=status):
                self.assertEqual(self.decide("pending", snapshot(status)).action, "none")
        self.assertEqual(self.decide("pending", snapshot("canceled")).action, "cancelled")
        self.assertEqual(self.decide("paid", snapshot("canceled")).action, "reconcile")

    def test_paid_but_not_processed_is_information_not_a_grant(self):
        self.assertEqual(self.decide("pending", snapshot("paid")).action, "none")

    def test_chargebacks_and_unknown_adjustments_go_to_manual_reconciliation(self):
        for a in (adj("chargeback", "approved"), adj("chargeback", "pending_approval"), adj("chargeback_reverse", "approved"),
                  adj("mystery", "approved"), adj("credit", "weird"), adj("refund", "approved", "weird")):
            with self.subTest(action=a.action, status=a.status):
                d = self.decide("pending", snapshot(adjustments=[a]))
                self.assertEqual((d.action, d.grants), ("reconcile", False))

    def test_pending_rejected_partial_and_credit_adjustments_do_not_change_state(self):
        for a in (adj("refund", "pending_approval"), adj("refund", "rejected"), adj("refund", "approved", "partial"),
                  adj("credit", "approved"), adj("chargeback_warning", "approved")):
            with self.subTest(action=a.action, status=a.status, type=a.type):
                self.assertEqual(self.decide("pending", snapshot(adjustments=[a])).action, "paid")

    def test_unknown_domains_never_grant_even_after_a_benign_or_full_refund_element(self):
        unknown = [adj("refund", "weird", "partial"), adj("credit", "approved", "weird"), adj("refund", "approved", "weird"),
                   adj("credit", "weird", "full"), adj("", "approved", "full"), adj(None, "approved", "full")]
        for a in unknown:
            for before in ("pending", "paid"):
                for prefix in ([], [adj("credit", "approved", "full", i=7)], [adj()]):
                    with self.subTest(action=a.action, status=a.status, type=a.type, before=before, prefix=len(prefix)):
                        d = self.decide(before, snapshot(adjustments=prefix + [a]))
                        self.assertEqual((d.action, d.grants), ("reconcile", False))

    def test_unknown_transaction_status_is_reconciled_not_ignored(self):
        for status in ("weird", "", None, "Completed", "refunded"):
            with self.subTest(status=status):
                self.assertEqual(self.decide("pending", snapshot(status)).action, "reconcile")
                self.assertEqual(self.decide("pending", snapshot(status, [adj()])).action, "reconcile")
                self.assertEqual(self.decide("pending", snapshot(status, [adj("credit", "approved", "full")])).action, "reconcile")

    def test_numeric_proof_values_must_be_real_integers_not_booleans(self):
        # True == 1 in Python: a bool must never satisfy a quantity or an amount.
        self.assertEqual(self.decide("pending", snapshot(items=(Item(PRICE, True),))).action, "reconcile")
        self.assertEqual(self.decide("pending", snapshot(), Expected("sandbox", PRICE, True, "USD", transaction_id=TXN, total_minor=1000)).action, "reconcile")
        self.assertEqual(self.decide("pending", snapshot(totals={"grand_total": True}), Expected("sandbox", PRICE, 1, "USD", transaction_id=TXN, total_minor=1)).action, "reconcile")
        self.assertEqual(self.decide("pending", snapshot(), Expected("sandbox", PRICE, 1, "USD", transaction_id=TXN, total_minor=True)).action, "reconcile")
        self.assertEqual(self.decide("pending", snapshot(totals={"grand_total": 1000.0})).action, "reconcile")
        self.assertEqual(self.decide("pending", snapshot(items=(Item(PRICE, 1.0),))).action, "reconcile")
        self.assertEqual(self.decide("pending", snapshot()).action, "paid")

    def test_every_adjustment_is_validated_before_a_grant(self):
        d = self.decide("pending", snapshot(adjustments=[adj("credit", "approved", i=1), adj("mystery", "approved", i=2)]))
        self.assertEqual(d.action, "reconcile")

    def test_events_only_say_when_to_look(self):
        def event(kind):
            return VerifiedEvent("paddle", "sandbox", "evt_x", kind, "t", "ntf_x", "d" * 64, TXN, None, {})
        for kind in ("transaction.completed", "transaction.canceled", "adjustment.created", "adjustment.updated"):
            self.assertTrue(mapping.should_look(event(kind)))
        for kind in ("transaction.created", "transaction.updated", "transaction.ready", "transaction.billed", "transaction.paid",
                     "transaction.payment_failed", "transaction.past_due", "subscription.created", "customer.created"):
            self.assertFalse(mapping.should_look(event(kind)), kind)

    def test_no_new_state_values_and_no_io_in_the_package(self):
        from server.models import PurchaseState
        known = {s.value for s in PurchaseState}
        for before in known:
            for snap in (snapshot(), snapshot("canceled"), snapshot(adjustments=[adj()])):
                action = self.decide(before, snap).action
                self.assertIn(action, {"none", "paid", "refunded", "cancelled", "reconcile"})
                if action in ("paid", "refunded", "cancelled"):
                    self.assertIn(action, known)
        import pathlib
        text = "".join(p.read_text() for p in pathlib.Path(mapping.__file__).parent.glob("*.py"))
        for forbidden in ("import socket", "import http", "import urllib.request", "import requests", "sqlite3", "from ..app", "import os"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
