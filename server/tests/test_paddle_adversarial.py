# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial offline tests for the Paddle building blocks (``server.paddle``).

These are independent negative tests written from the documented protocol only. They
try to make the pure, offline modules misbehave: forged or replayed signatures,
smuggled headers, hostile JSON, purchase decisions that must never grant access and
URLs/hosts that must never be reached. No network, no keys, no provider data: every
vector is synthetic and built in the test.

The modules are pure functions; a failure here is a contract failure, not a live
payment outcome. Nothing in this file asserts provider compatibility (no public test
vector exists) and nothing here grants or revokes anything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from server.paddle.digest import snapshot_digest
from server.paddle.mapping import decide, should_look, validate_snapshot
from server.paddle.policy import (
    API_HOSTS,
    api_url,
    check_response_url,
    environment_for_key,
    validate_config,
)
from server.paddle.signature import (
    DEFAULT_TOLERANCE_SECONDS,
    MAX_BODY_BYTES,
    MAX_H1,
    MAX_HEADER_BYTES,
    classify_dedup,
    parse_signature_header,
    verify_signature,
    verify_webhook,
)
from server.paddle.types import (
    Adjustment,
    AuthoritativeState,
    Expected,
    Item,
    PaddleConfigError,
    PaddleMalformed,
    PaddleSignatureInvalid,
    PaddleStaleTimestamp,
    PaddleTransportRejected,
    VerifiedEvent,
)

SECRET = "endpoint-secret-0123456789abcdef"
WRONG_SECRET = "another-secret-0123456789abcdef"
TS = 1_700_000_000
NOW = float(TS)
ENVIRONMENT = "sandbox"
SANDBOX_HOST = API_HOSTS["sandbox"]
LIVE_HOST = API_HOSTS["live"]

PRICE = "pri_" + "p" * 20
TXN = "txn_" + "t" * 20
ADJ = "adj_" + "a" * 20
EVT = "evt_" + "e" * 20
NTF = "ntf_" + "n" * 20


def h1_for(raw_body: bytes, ts: int, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), f"{ts}:".encode("ascii") + bytes(raw_body), hashlib.sha256).hexdigest()


def header_for(raw_body: bytes, *, ts: int = TS, secret: str = SECRET, extra_h1: tuple = ()) -> str:
    parts = [f"ts={ts}", f"h1={h1_for(raw_body, ts, secret)}"]
    parts.extend(f"h1={h}" for h in extra_h1)
    return ";".join(parts)


def encode(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def envelope(event_type: str, data, **override) -> dict:
    body = {
        "event_id": EVT,
        "event_type": event_type,
        "occurred_at": "2026-10-18T00:00:00Z",
        "notification_id": NTF,
        "data": data,
    }
    body.update(override)
    return body


def transaction_data(**override) -> dict:
    data = {
        "id": TXN,
        "status": "completed",
        "currency_code": "USD",
        "items": [{"price": {"id": PRICE}, "quantity": 1}],
        "details": {"totals": {"subtotal": "400", "tax": "100", "total": "500", "grand_total": "500"}},
    }
    data.update(override)
    return data


def adjustment_data(**override) -> dict:
    data = {"id": ADJ, "transaction_id": TXN, "action": "refund", "status": "approved", "type": "full"}
    data.update(override)
    return data


def verify(body: bytes, *, header=None, headers=None, now: float = NOW, secret: str = SECRET,
           environment: str = ENVIRONMENT) -> VerifiedEvent:
    if headers is None:
        headers = {"Paddle-Signature": header if header is not None else header_for(body, secret=secret)}
    return verify_webhook(body, headers, now, secret=secret, environment=environment)


def item(price_id: str = PRICE, quantity: int = 1) -> Item:
    return Item(price_id, quantity)


def adjustment(adj_id: str = ADJ, action: str = "refund", status: str = "approved", kind: str = "full",
               total_minor=None, currency_code=None) -> Adjustment:
    return Adjustment(adj_id, action, status, kind, total_minor, currency_code)


def snapshot(*, environment: str = "sandbox", transaction_id: str = TXN, status: str = "completed",
             currency_code: str = "USD", totals=None, items=None, adjustments=(), complete: bool = True,
             fetched_at: float = 1.0) -> AuthoritativeState:
    return AuthoritativeState(
        environment, transaction_id, status, currency_code,
        {"grand_total": 500} if totals is None else totals,
        (item(),) if items is None else tuple(items),
        tuple(adjustments), fetched_at, complete,
    )


def expected(*, environment: str = "sandbox", price_id: str = PRICE, quantity: int = 1,
             currency_code: str = "USD", transaction_id: str = TXN, total_minor=500) -> Expected:
    return Expected(environment, price_id, quantity, currency_code, transaction_id, total_minor)


# --------------------------------------------------------------------------- signatures
class SignatureHeaderTests(unittest.TestCase):
    """Strict parsing of the ``Paddle-Signature`` header, before any HMAC is computed."""

    def test_accepts_one_ts_and_one_to_max_h1(self):
        h1 = h1_for(b"body", TS)
        self.assertEqual(parse_signature_header(f"ts={TS};h1={h1}"), (TS, (h1,)))
        four = ";".join([f"ts={TS}"] + [f"h1={h1_for(b'body', TS, secret=SECRET + str(n))}" for n in range(MAX_H1)])
        self.assertEqual(len(parse_signature_header(four)[1]), MAX_H1)

    def test_rejects_more_than_max_h1(self):
        h1 = h1_for(b"body", TS)
        five = ";".join([f"ts={TS}"] + [f"h1={h1}"] * (MAX_H1 + 1))
        with self.assertRaises(PaddleMalformed):
            parse_signature_header(five)

    def test_rejects_empty_h1_list(self):
        with self.assertRaises(PaddleMalformed):
            parse_signature_header(f"ts={TS}")

    def test_rejects_duplicate_ts(self):
        h1 = h1_for(b"body", TS)
        with self.assertRaises(PaddleMalformed):
            parse_signature_header(f"ts={TS};ts={TS};h1={h1}")

    def test_rejects_unknown_keys(self):
        h1 = h1_for(b"body", TS)
        for header in (f"ts={TS};h1={h1};h2={h1}", f"ts={TS};h1={h1};version=1", f"ts={TS};h1={h1};"):
            with self.subTest(header=header):
                with self.assertRaises(PaddleMalformed):
                    parse_signature_header(header)

    def test_rejects_parts_without_a_value_or_separator(self):
        h1 = h1_for(b"body", TS)
        for header in (f"ts=;h1={h1}", f"ts;h1={h1}", f"ts={TS};h1", f"TS={TS};h1={h1}"):
            with self.subTest(header=header):
                with self.assertRaises(PaddleMalformed):
                    parse_signature_header(header)

    def test_rejects_non_string_and_empty_header(self):
        for value in (None, 123, b"ts=1;h1=" + b"a" * 64, "", ["ts=1"]):
            with self.subTest(value=value):
                with self.assertRaises(PaddleMalformed):
                    parse_signature_header(value)

    def test_rejects_non_ascii_header(self):
        h1 = h1_for(b"body", TS)
        with self.assertRaises(PaddleMalformed):
            parse_signature_header(f"ts={TS};h1={h1};note=åäö")

    def test_rejects_oversized_header(self):
        filler = "a" * (MAX_HEADER_BYTES + 1)
        with self.assertRaises(PaddleMalformed):
            parse_signature_header(f"ts={TS};h1={filler}")

    def test_rejects_non_canonical_or_malformed_ts(self):
        h1 = h1_for(b"body", TS)
        for ts in ("00", "01", "-1", "+1", "1.0", "1e3", "", "1 ", "0x10", "9" * 13):
            with self.subTest(ts=ts):
                with self.assertRaises(PaddleMalformed):
                    parse_signature_header(f"ts={ts};h1={h1}")

    def test_accepts_zero_timestamp_as_single_digit_group(self):
        h1 = h1_for(b"body", 0)
        self.assertEqual(parse_signature_header(f"ts=0;h1={h1}"), (0, (h1,)))

    def test_rejects_non_lowercase_or_wrong_length_hex(self):
        good = h1_for(b"body", TS)
        candidates = (good.upper(), good[:-1], good + "a", good[:-1] + "G", "z" * 64, "a" * 63, "a" * 65)
        for bad in candidates:
            with self.subTest(h1=bad):
                with self.assertRaises(PaddleMalformed):
                    parse_signature_header(f"ts={TS};h1={bad}")

    def test_rejects_whitespace_and_injection_inside_the_header(self):
        h1 = h1_for(b"body", TS)
        for header in (
            f"ts={TS}; h1={h1}",                 # space before a key
            f"ts={TS};h1={h1} ",                 # trailing space
            f"ts={TS};h1={h1}\r\nx-evil: 1",     # injected line
            f"ts={TS};h1={h1}\nx-evil=1",        # injected fake part
            f"ts={TS};h1={h1};x=1\r\n\r\n",      # smuggling attempt
        ):
            with self.subTest(header=header):
                with self.assertRaises(PaddleMalformed):
                    parse_signature_header(header)


class SignatureVerificationTests(unittest.TestCase):
    def test_valid_signature_returns_timestamp(self):
        body = b'{"ok":true}'
        self.assertEqual(verify_signature(body, header_for(body), SECRET, NOW), TS)

    def test_signature_is_over_the_exact_original_bytes(self):
        body = b'{"a": 1}\n'
        header = header_for(body)
        self.assertEqual(verify_signature(body, header, SECRET, NOW), TS)
        # Re-serialised, stripped or otherwise "equivalent" bytes are NOT the signed bytes.
        for mutated in (b'{"a":1}', b'{"a": 1}', b'{"a": 1}\n\n', b'{"a": 1} '):
            with self.subTest(mutated=mutated):
                with self.assertRaises(PaddleSignatureInvalid):
                    verify_signature(mutated, header, SECRET, NOW)

    def test_rejects_wrong_secret_and_mutated_byte(self):
        body = b'payload'
        header = header_for(body)
        with self.assertRaises(PaddleSignatureInvalid):
            verify_signature(body, header, WRONG_SECRET, NOW)
        with self.assertRaises(PaddleSignatureInvalid):
            verify_signature(b'payloac', header, SECRET, NOW)

    def test_rejects_timestamp_swapped_after_signing(self):
        body = b'payload'
        header = header_for(body, ts=TS)
        swapped = header.replace(f"ts={TS}", f"ts={TS + 1}", 1)
        with self.assertRaises(PaddleSignatureInvalid):
            verify_signature(body, swapped, SECRET, NOW + 1)

    def test_all_candidate_h1_are_compared(self):
        body = b'payload'
        good = h1_for(body, TS)
        bad = "a" * 64
        # The matching h1 in first, middle and last position must all be accepted.
        self.assertEqual(verify_signature(body, f"ts={TS};h1={good};h1={bad}", SECRET, NOW), TS)
        self.assertEqual(verify_signature(body, f"ts={TS};h1={bad};h1={good}", SECRET, NOW), TS)
        self.assertEqual(verify_signature(body, f"ts={TS};h1={bad};h1={bad};h1={bad};h1={good}", SECRET, NOW), TS)
        # A prefix of the real digest must never be accepted as a match.
        with self.assertRaises(PaddleSignatureInvalid):
            verify_signature(body, f"ts={TS};h1={good[:63]}0", SECRET, NOW)

    def test_stale_and_future_timestamps(self):
        body = b'payload'
        header = header_for(body)
        with self.assertRaises(PaddleStaleTimestamp):
            verify_signature(body, header, SECRET, NOW + DEFAULT_TOLERANCE_SECONDS + 1)
        with self.assertRaises(PaddleStaleTimestamp):
            verify_signature(body, header, SECRET, NOW - DEFAULT_TOLERANCE_SECONDS - 1)

    def test_tolerance_boundary_is_inclusive(self):
        body = b'payload'
        header = header_for(body)
        self.assertEqual(verify_signature(body, header, SECRET, NOW + DEFAULT_TOLERANCE_SECONDS), TS)
        with self.assertRaises(PaddleStaleTimestamp):
            verify_signature(body, header, SECRET, NOW + DEFAULT_TOLERANCE_SECONDS + 0.001)

    def test_custom_and_zero_tolerance(self):
        body = b'payload'
        header = header_for(body)
        self.assertEqual(verify_signature(body, header, SECRET, TS, tolerance=0), TS)
        with self.assertRaises(PaddleStaleTimestamp):
            verify_signature(body, header, SECRET, TS + 1, tolerance=0)
        self.assertEqual(verify_signature(body, header, SECRET, TS + 100, tolerance=100), TS)

    def test_rejects_bad_tolerance(self):
        body = b'payload'
        header = header_for(body)
        for tolerance in (-1, True, False, float("inf"), float("nan"), "5"):
            with self.subTest(tolerance=tolerance):
                with self.assertRaises(PaddleConfigError):
                    verify_signature(body, header, SECRET, NOW, tolerance=tolerance)

    def test_rejects_unusable_clock(self):
        body = b'payload'
        header = header_for(body)
        for now in (True, float("nan"), float("inf"), float("-inf"), "100", None):
            with self.subTest(now=now):
                with self.assertRaises(PaddleConfigError):
                    verify_signature(body, header, SECRET, now)

    def test_rejects_missing_or_weak_secret(self):
        body = b'payload'
        header = header_for(body)
        for secret in ("", "short", "x" * 15, None, 123):
            with self.subTest(secret=secret):
                with self.assertRaises(PaddleConfigError):
                    verify_signature(body, header, secret, NOW)

    def test_accepts_byte_secret_of_sufficient_length(self):
        body = b'payload'
        secret = b"endpoint-secret-0123456789"
        self.assertEqual(verify_signature(body, header_for(body, secret=secret.decode()), secret, NOW), TS)

    def test_rejects_wrong_body_type_and_oversized_body(self):
        with self.assertRaises(PaddleMalformed):
            verify_signature("not bytes", f"ts={TS};h1={'a' * 64}", SECRET, NOW)
        oversized = b"x" * (MAX_BODY_BYTES + 1)
        with self.assertRaises(PaddleMalformed):
            verify_signature(oversized, header_for(oversized), SECRET, NOW)


class WebhookHeaderTests(unittest.TestCase):
    def test_missing_header_is_rejected(self):
        body = encode(envelope("transaction.completed", transaction_data()))
        with self.assertRaises(PaddleMalformed):
            verify_webhook(body, {}, NOW, secret=SECRET, environment=ENVIRONMENT)

    def test_repeated_header_under_two_casings_is_ambiguous(self):
        body = encode(envelope("transaction.completed", transaction_data()))
        header = header_for(body)
        headers = {"Paddle-Signature": header, "paddle-signature": header}
        with self.assertRaises(PaddleMalformed):
            verify_webhook(body, headers, NOW, secret=SECRET, environment=ENVIRONMENT)

    def test_header_value_must_be_a_single_string(self):
        body = encode(envelope("transaction.completed", transaction_data()))
        header = header_for(body)
        for value in ([header], (header,)):
            with self.subTest(value=value):
                with self.assertRaises(PaddleMalformed):
                    verify_webhook(body, {"Paddle-Signature": value}, NOW, secret=SECRET, environment=ENVIRONMENT)

    def test_environment_must_be_configured_and_is_not_taken_from_the_body(self):
        body = encode(envelope("transaction.completed", transaction_data()))
        header = header_for(body)
        for environment in ("production", "", None, "SANDBOX"):
            with self.subTest(environment=environment):
                with self.assertRaises(PaddleConfigError):
                    verify_webhook(body, {"Paddle-Signature": header}, NOW, secret=SECRET, environment=environment)
        # A body that pretends to be another environment cannot change the configured one.
        lying = encode(envelope("transaction.completed", transaction_data(), environment="live"))
        event = verify(lying)
        self.assertEqual(event.environment, ENVIRONMENT)

    def test_lowercase_header_name_is_accepted(self):
        body = encode(envelope("transaction.completed", transaction_data()))
        event = verify_webhook(body, {"paddle-signature": header_for(body)}, NOW, secret=SECRET, environment=ENVIRONMENT)
        self.assertEqual(event.provider, "paddle")


# --------------------------------------------------------------------------- hostile JSON
class HostileJsonTests(unittest.TestCase):
    """Hostile bodies, signed with the real secret, must still fail closed when parsed."""

    def assertMalformed(self, body: bytes):
        with self.assertRaises(PaddleMalformed):
            verify(body)

    def test_duplicate_keys_are_rejected(self):
        self.assertMalformed(b'{"a":1,"a":2}')
        self.assertMalformed(b'{"x":{"y":1,"y":2}}')
        # The same key can also be produced through a unicode escape.
        self.assertMalformed(b'{"\\u0061":1,"a":2}')

    def test_non_finite_numbers_are_rejected(self):
        for body in (b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}', b'{"a":-1e999}'):
            with self.subTest(body=body):
                self.assertMalformed(body)

    def test_deeply_nested_json_is_rejected_not_crashing(self):
        depth = 60_000
        self.assertMalformed(b"[" * depth + b"]" * depth)

    def test_invalid_utf8_is_rejected(self):
        self.assertMalformed(b'{"x":"\xff\xfe"}')
        self.assertMalformed(b"\xff\xfe\x00")

    def test_truncated_or_trailing_garbage_is_rejected(self):
        for body in (b'{"a":', b'{"a":1', b'{"a":1} trailing', b"", b"null", b"1", b"[]", b'"x"'):
            with self.subTest(body=body):
                self.assertMalformed(body)

    def test_lone_surrogate_escape_cannot_smuggle_an_id(self):
        body = encode(envelope("transaction.completed", transaction_data(),
                               event_id="evt_" + "\ud800" * 16))
        self.assertMalformed(body)

    def test_oversized_integers_are_rejected(self):
        huge = int("9" * 40)
        self.assertMalformed(encode(envelope("transaction.completed",
                                             transaction_data(details={"totals": {"grand_total": huge}}))))
        self.assertMalformed(encode(envelope("transaction.completed",
                                             transaction_data(details={"totals": {"grand_total": "9" * 20}}))))

    def test_boolean_and_string_quantities_are_rejected(self):
        for quantity in (True, False, "1", 1.0, 0, -1, None):
            with self.subTest(quantity=quantity):
                self.assertMalformed(encode(envelope("transaction.completed", transaction_data(
                    items=[{"price": {"id": PRICE}, "quantity": quantity}]))))

    def test_malformed_transaction_shapes_are_rejected(self):
        bad_data = (
            transaction_data(id="not-a-txn"),
            transaction_data(items="x"),
            transaction_data(items=[1]),
            transaction_data(items=[{"price": {"id": "bad"}, "quantity": 1}]),
            transaction_data(currency_code="usd"),
            transaction_data(status="x" * 21),
            transaction_data(status=None),
            transaction_data(details=[]),
            transaction_data(details={"totals": []}),
            transaction_data(details={"totals": {"grand_total": 500}}),
        )
        for data in bad_data:
            with self.subTest(data=data):
                self.assertMalformed(encode(envelope("transaction.completed", data)))

    def test_malformed_envelope_shapes_are_rejected(self):
        good = transaction_data()
        for body in (
            encode({"event_id": EVT, "event_type": "transaction.completed", "notification_id": NTF,
                    "occurred_at": "2026-10-18T00:00:00Z"}),                       # no data
            encode(envelope("transaction.completed", "not-an-object")),
            encode(envelope("transaction.completed", good, event_id="evt_bad")),
            encode(envelope("transaction.completed", good, notification_id="x")),
            encode({**envelope("transaction.completed", good), "event_type": "nodot"}),
            encode({**envelope("transaction.completed", good), "event_type": "Transaction.Completed"}),
        ):
            with self.subTest(body=body[:60]):
                self.assertMalformed(body)

    def test_malformed_adjustment_shapes_are_rejected(self):
        for adj in (
            adjustment_data(action=""),
            adjustment_data(action="x" * 41),
            adjustment_data(status=""),
            adjustment_data(type=""),
            adjustment_data(id="not-adj"),
            adjustment_data(transaction_id="not-txn"),
            adjustment_data(totals={"total": 5}),
            adjustment_data(totals=[]),
        ):
            with self.subTest(adj=adj):
                self.assertMalformed(encode(envelope("adjustment.created", adj)))


class OccurredAtTests(unittest.TestCase):
    """A full RFC 3339 timestamp with a zone is required; anything else fails closed."""

    def assertMalformed(self, occurred_at):
        body = encode(envelope("transaction.completed", transaction_data(), occurred_at=occurred_at))
        with self.assertRaises(PaddleMalformed):
            verify(body)

    def test_accepts_zone_variants(self):
        for value in ("2026-10-18T00:00:00Z", "2026-10-18t00:00:00z", "2026-10-18T00:00:00+23:59",
                      "2026-10-18T00:00:00-00:30", "2026-10-18T00:00:00.123456789Z", "2026-10-18T23:59:59Z"):
            with self.subTest(value=value):
                event = verify(encode(envelope("transaction.completed", transaction_data(), occurred_at=value)))
                self.assertEqual(event.occurred_at, value)

    def test_rejects_missing_zone_and_date_only(self):
        for value in ("2026-10-18T00:00:00", "2026-10-18", "2026-10-18 00:00:00Z"):
            with self.subTest(value=value):
                self.assertMalformed(value)

    def test_rejects_hour_24_and_above(self):
        # RFC 3339 allows hours 00-23 only; a permissive parser that accepts 24:00:00
        # would let an out-of-domain timestamp through.
        for value in ("2026-10-18T24:00:00Z", "2026-10-18T24:00:00+00:00", "2026-10-18T24:30:00Z",
                      "2026-10-18T25:00:00Z", "2026-10-18T99:00:00Z"):
            with self.subTest(value=value):
                self.assertMalformed(value)

    def test_rejects_out_of_range_fields(self):
        for value in ("2026-13-40T99:99:99Z", "2026-10-18T00:00:60Z", "2026-10-18T00:00:00+24:00",
                      "2026-10-18T00:00:00+00:60", "2026-10-18T00:00:00.1234567890Z"):
            with self.subTest(value=value):
                self.assertMalformed(value)


# --------------------------------------------------------------------------- envelope
class EnvelopeTests(unittest.TestCase):
    def test_verified_transaction_event_is_minimal_and_tagged(self):
        body = encode(envelope("transaction.completed", transaction_data()))
        event = verify(body)
        self.assertIsInstance(event, VerifiedEvent)
        self.assertEqual(event.provider, "paddle")
        self.assertEqual(event.environment, ENVIRONMENT)
        self.assertEqual(event.event_id, EVT)
        self.assertEqual(event.transaction_id, TXN)
        self.assertIsNone(event.adjustment_id)
        self.assertEqual(event.payload_digest, hashlib.sha256(body).hexdigest())
        self.assertEqual(event.data["items"], [{"price_id": PRICE, "quantity": 1}])
        self.assertEqual(event.data["totals"], {"subtotal": 400, "tax": 100, "total": 500, "grand_total": 500})

    def test_unknown_totals_are_ignored_and_known_ones_coerced(self):
        data = transaction_data(details={"totals": {"subtotal": "100", "junk": "x", "currency_code": "USD"}})
        event = verify(encode(envelope("transaction.completed", data)))
        self.assertEqual(event.data["totals"], {"subtotal": 100})

    def test_missing_items_become_an_empty_list(self):
        data = transaction_data()
        data.pop("items")
        event = verify(encode(envelope("transaction.completed", data)))
        self.assertEqual(event.data["items"], [])

    def test_adjustment_event_carries_both_ids(self):
        event = verify(encode(envelope("adjustment.updated", adjustment_data(totals={"total": "100", "currency_code": "USD"}))))
        self.assertEqual(event.transaction_id, TXN)
        self.assertEqual(event.adjustment_id, ADJ)
        self.assertEqual(event.data["total_minor"], 100)
        self.assertEqual(event.data["currency_code"], "USD")

    def test_unknown_entity_is_accepted_as_a_no_op(self):
        event = verify(encode(envelope("customer.created", {"id": "ctm_" + "c" * 20})))
        self.assertIsNone(event.transaction_id)
        self.assertIsNone(event.adjustment_id)
        self.assertEqual(event.data, {})
        self.assertFalse(should_look(event))

    def test_payload_digest_is_over_the_raw_bytes_not_the_parsed_object(self):
        body = b'{"event_id": "' + EVT.encode() + b'"}'
        with self.assertRaises(PaddleMalformed):
            verify(body)  # envelope is incomplete, but the digest must be over these exact bytes
        # A correctly shaped body hashes to the raw digest (checked again for clarity).
        good = encode(envelope("transaction.completed", transaction_data()))
        self.assertEqual(verify(good).payload_digest, hashlib.sha256(good).hexdigest())


# --------------------------------------------------------------------------- decide
class DecisionTests(unittest.TestCase):
    """Only a complete, bound, price-proven snapshot of a completed transaction may grant."""

    def decide(self, state_before="pending", snap=None, exp=None):
        return decide(state_before, snap if snap is not None else snapshot(), exp if exp is not None else expected())

    def test_completed_with_matching_proof_grants(self):
        decision = self.decide()
        self.assertTrue(decision.grants)
        self.assertEqual(decision.action, "paid")

    def test_grant_requires_a_complete_bound_snapshot(self):
        self.assertFalse(self.decide(snap=snapshot(complete=False)).grants)
        self.assertFalse(self.decide(snap=snapshot(transaction_id="txn_" + "z" * 20)).grants)
        self.assertFalse(self.decide(snap=snapshot(environment="live")).grants)

    def test_grant_requires_exact_price_quantity_currency_and_amount(self):
        cases = (
            snapshot(items=[item(PRICE, 2)]),
            snapshot(items=[item("pri_" + "q" * 20, 1)]),
            snapshot(items=[item(PRICE, 1), item("pri_" + "q" * 20, 1)]),
            snapshot(currency_code="EUR"),
            snapshot(totals={"grand_total": 499}),
            snapshot(totals={}),
        )
        for snap in cases:
            with self.subTest(snap=snap):
                self.assertFalse(self.decide(snap=snap).grants)

    def test_undecided_amount_policy_never_grants(self):
        self.assertFalse(self.decide(exp=expected(total_minor=None)).grants)
        self.assertFalse(self.decide(exp=Expected("sandbox", PRICE, 1, "USD", TXN)).grants)

    def test_boolean_is_never_a_proof_value(self):
        self.assertFalse(self.decide(snap=snapshot(items=[item(PRICE, True)])).grants)
        self.assertFalse(self.decide(exp=expected(quantity=True)).grants)
        self.assertFalse(self.decide(exp=expected(total_minor=True)).grants)
        self.assertFalse(self.decide(snap=snapshot(totals={"grand_total": True})).grants)

    def test_full_approved_refund_never_grants_and_refunds_even_when_observed_first(self):
        refund = (adjustment(),)
        for state_before in ("pending", "paid"):
            with self.subTest(state_before=state_before):
                decision = self.decide(state_before=state_before, snap=snapshot(adjustments=refund))
                self.assertEqual(decision.action, "refunded")
                self.assertFalse(decision.grants)

    def test_full_refund_without_a_paid_transaction_goes_to_reconcile(self):
        decision = self.decide(snap=snapshot(status="ready", adjustments=(adjustment(),)))
        self.assertEqual(decision.action, "reconcile")
        self.assertFalse(decision.grants)

    def test_chargebacks_and_unknown_adjustments_never_grant(self):
        # Nothing in this group may grant access; each is refused as unknown/undocumented.
        for adj in (adjustment(action="chargeback"), adjustment(action="chargeback_reverse"),
                    adjustment(action="unknown_action"), adjustment(action="refund", status="made_up"),
                    adjustment(action="refund", kind="other")):
            with self.subTest(adj=adj):
                decision = self.decide(snap=snapshot(adjustments=(adj,)))
                self.assertFalse(decision.grants)
                self.assertEqual(decision.action, "reconcile")
        # A reversed or rejected refund is not terminal evidence: it may not block a valid
        # grant, but it must never turn the purchase into refunded or cancelled.
        for adj in (adjustment(action="refund", status="reversed"), adjustment(action="refund", status="rejected")):
            with self.subTest(non_terminal=adj):
                decision = self.decide(snap=snapshot(adjustments=(adj,)))
                self.assertFalse(decision.action in ("refunded", "cancelled"))

    def test_non_terminal_adjustments_do_not_block_a_valid_grant_but_never_refund(self):
        for adj in (adjustment(action="credit"), adjustment(action="refund", kind="partial"),
                    adjustment(action="refund", status="pending_approval"),
                    adjustment(action="chargeback_warning"),
                    adjustment(action="credit_reverse")):
            with self.subTest(adj=adj):
                decision = self.decide(snap=snapshot(adjustments=(adj,)))
                self.assertNotIn(decision.action, ("refunded", "cancelled"))

    def test_unknown_transaction_status_reconciles(self):
        self.assertEqual(self.decide(snap=snapshot(status="processing")).action, "reconcile")

    def test_informational_statuses_never_grant_or_cancel(self):
        for status in ("draft", "ready", "billed", "paid", "past_due"):
            with self.subTest(status=status):
                decision = self.decide(snap=snapshot(status=status))
                self.assertNotIn(decision.action, ("paid", "cancelled", "refunded"))

    def test_cancelled_only_from_pending(self):
        self.assertEqual(self.decide(snap=snapshot(status="canceled", items=[])).action, "cancelled")
        self.assertEqual(self.decide(state_before="paid", snap=snapshot(status="canceled", items=[])).action, "reconcile")

    def test_terminal_states_never_move(self):
        for state_before in ("refunded", "chargeback", "cancelled"):
            with self.subTest(state_before=state_before):
                self.assertEqual(self.decide(state_before=state_before).action, "none")

    def test_unknown_current_state_reconciles(self):
        self.assertEqual(self.decide(state_before="weird").action, "reconcile")
        self.assertEqual(self.decide(state_before=None).action, "reconcile")

    def test_already_paid_is_a_no_op_but_refund_after_paid_still_applies(self):
        self.assertEqual(self.decide(state_before="paid").action, "none")
        self.assertEqual(self.decide(state_before="paid", snap=snapshot(adjustments=(adjustment(),))).action, "refunded")

    def test_a_chargeback_outweighs_a_full_refund(self):
        snap = snapshot(adjustments=(adjustment(), adjustment(ADJ + "2", action="chargeback")))
        self.assertEqual(self.decide(snap=snap).action, "reconcile")
        self.assertFalse(self.decide(snap=snap).grants)

    def test_should_look_only_for_look_events(self):
        for event_type in ("transaction.completed", "transaction.canceled", "adjustment.created", "adjustment.updated"):
            body = encode(envelope(event_type, {
                "id": TXN, "status": "completed", "items": [],
            } if event_type.startswith("transaction") else adjustment_data()))
            self.assertTrue(should_look(verify(body)), event_type)
        for event_type in ("transaction.created", "transaction.updated", "transaction.ready", "transaction.billed",
                           "transaction.paid", "transaction.payment_failed", "transaction.past_due", "customer.created"):
            body = encode(envelope(event_type, {
                "id": TXN, "status": "completed", "items": [],
            } if event_type.startswith("transaction") else {"id": "ctm_" + "c" * 20}))
            self.assertFalse(should_look(verify(body)), event_type)


class SnapshotValidationTests(unittest.TestCase):
    def test_accepts_a_fully_formed_snapshot(self):
        snap = snapshot(adjustments=(adjustment(total_minor=100, currency_code="USD"),))
        self.assertIs(validate_snapshot(snap), snap)

    def test_empty_item_and_adjustment_tuples_are_valid(self):
        snap = snapshot(items=[], adjustments=[])
        self.assertIs(validate_snapshot(snap), snap)

    def test_rejects_corrupt_snapshots_with_a_typed_error(self):
        cases = (
            None,
            {"environment": "sandbox"},
            object(),
            snapshot(environment="prod"),
            snapshot(transaction_id=""),
            snapshot(transaction_id=123),
            snapshot(status=123),
            snapshot(complete=1),
            snapshot(currency_code=123),
            snapshot(totals=[]),
            snapshot(totals={"grand_total": True}),
            snapshot(totals={"grand_total": "500"}),
            snapshot(items=[Item(PRICE, True)]),
            snapshot(items=["x"]),
            snapshot(items=(PRICE, 1)),
            snapshot(adjustments=[adjustment(total_minor=True)]),
            snapshot(adjustments=[adjustment(currency_code=1)]),
            snapshot(adjustments=[adjustment(action=1)]),
            # The list/tuple distinction matters: a list is not the validated shape.
            AuthoritativeState("sandbox", TXN, "completed", "USD", {"grand_total": 500}, [item()], (), 1.0, True),
            AuthoritativeState("sandbox", TXN, "completed", "USD", {"grand_total": 500}, (item(),), [], 1.0, True),
        )
        for snap in cases:
            with self.subTest(snap=snap):
                with self.assertRaises(PaddleMalformed):
                    validate_snapshot(snap)


# --------------------------------------------------------------------------- digest
class DigestTests(unittest.TestCase):
    def test_digest_is_stable_and_excludes_fetch_time(self):
        a = snapshot(fetched_at=1.0)
        b = snapshot(fetched_at=999_999.0)
        self.assertEqual(snapshot_digest(a), snapshot_digest(b))
        self.assertEqual(len(snapshot_digest(a)), 64)

    def test_digest_is_order_insensitive_but_content_sensitive(self):
        one = adjustment(ADJ + "1", total_minor=100)
        two = adjustment(ADJ + "2", action="credit", status="approved", kind="full", total_minor=50)
        self.assertEqual(snapshot_digest(snapshot(adjustments=(one, two))),
                         snapshot_digest(snapshot(adjustments=(two, one))))
        self.assertEqual(snapshot_digest(snapshot(items=[item(PRICE, 1)])),
                         snapshot_digest(snapshot(items=[item(PRICE, 1)])))
        self.assertNotEqual(snapshot_digest(snapshot(status="completed")), snapshot_digest(snapshot(status="ready")))
        self.assertNotEqual(snapshot_digest(snapshot(totals={"grand_total": 500})),
                            snapshot_digest(snapshot(totals={"grand_total": 501})))
        self.assertNotEqual(snapshot_digest(snapshot(complete=True)), snapshot_digest(snapshot(complete=False)))
        self.assertNotEqual(snapshot_digest(snapshot(adjustments=(one,))),
                            snapshot_digest(snapshot(adjustments=(one, two))))

    def test_repeated_adjustment_id_is_refused_at_the_snapshot_gate(self):
        # A repeated adjustment id is ambiguous evidence (it could double-count a refund),
        # so it must be refused with a typed error at the gate, before any digest is built.
        duplicates = (
            (adjustment(ADJ, total_minor=None), adjustment(ADJ, total_minor=500)),
            (adjustment(ADJ), adjustment(ADJ)),                        # even byte-identical repeats
            (adjustment(ADJ), adjustment(ADJ, action="credit")),
        )
        for pair in duplicates:
            with self.subTest(pair=pair):
                with self.assertRaises(PaddleMalformed):
                    validate_snapshot(snapshot(adjustments=pair))


# --------------------------------------------------------------------------- dedup
class DedupTests(unittest.TestCase):
    def test_dedup_key_is_the_id_and_the_digest_is_compared_separately(self):
        self.assertEqual(classify_dedup(None, "aa"), "new")
        self.assertEqual(classify_dedup("aa", "aa"), "duplicate")
        self.assertEqual(classify_dedup("aa", "bb"), "conflict")
        # Different lengths must be a conflict, never an exception.
        self.assertEqual(classify_dedup("aa", "aaaa"), "conflict")


# --------------------------------------------------------------------------- config / hosts
class ConfigPolicyTests(unittest.TestCase):
    def test_key_prefix_chooses_the_environment(self):
        self.assertEqual(environment_for_key("pdl_live_apikey_" + "k" * 9), "live")
        self.assertEqual(environment_for_key("pdl_sdbx_apikey_" + "k" * 9), "sandbox")

    def test_short_or_unprefixed_keys_are_refused(self):
        for key in ("pdl_live_apikey_" + "k" * 8, "pdl_live_apikey_",
                    "live_apikey_" + "k" * 20, "", None, 123,
                    b"pdl_live_apikey_" + b"k" * 9):
            with self.subTest(key=key):
                with self.assertRaises(PaddleConfigError):
                    environment_for_key(key)

    def test_valid_sandbox_config_returns_the_environment(self):
        key = "pdl_sdbx_apikey_" + "k" * 20
        self.assertEqual(validate_config(paddle_env="sandbox", api_key=key, endpoint_secret="s" * 20,
                                         app_env="test", transport_is_mock=True), "sandbox")

    def test_a_mock_transport_never_serves_live(self):
        key = "pdl_live_apikey_" + "k" * 20
        with self.assertRaises(PaddleConfigError):
            validate_config(paddle_env="live", api_key=key, endpoint_secret="s" * 20,
                            app_env="test", transport_is_mock=True)

    def test_production_is_refused_for_every_arrangement(self):
        for env, prefix in (("sandbox", "pdl_sdbx_apikey_"), ("live", "pdl_live_apikey_")):
            with self.subTest(env=env):
                with self.assertRaises(PaddleConfigError):
                    validate_config(paddle_env=env, api_key=prefix + "k" * 20, endpoint_secret="s" * 20,
                                    app_env="production", transport_is_mock=False)

    def test_mismatched_environment_weak_or_reused_secret_is_refused(self):
        key = "pdl_sdbx_apikey_" + "k" * 20
        cases = (
            {"paddle_env": "live", "api_key": key},
            {"paddle_env": "nope", "api_key": key},
            {"paddle_env": "sandbox", "endpoint_secret": key},
            {"paddle_env": "sandbox", "endpoint_secret": "s" * 15},
            {"paddle_env": "sandbox", "endpoint_secret": b"s" * 20},
            {"paddle_env": "sandbox", "endpoint_secret": None},
        )
        for override in cases:
            kwargs = {"paddle_env": "sandbox", "api_key": key, "endpoint_secret": "s" * 20, "app_env": "test"}
            kwargs.update(override)
            with self.subTest(override=override):
                with self.assertRaises(PaddleConfigError):
                    validate_config(**kwargs)

    def test_errors_never_echo_the_key_or_secret(self):
        key = "pdl_sdbx_apikey_" + "SUPERSECRETKEY" * 3
        secret = "endpoint-secret-must-not-leak-0123456789"
        for kwargs in (
            {"paddle_env": "live", "api_key": key, "endpoint_secret": secret, "app_env": "test"},
            {"paddle_env": "sandbox", "api_key": key, "endpoint_secret": key, "app_env": "test"},
            {"paddle_env": "sandbox", "api_key": key, "endpoint_secret": secret, "app_env": "production"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(PaddleConfigError) as ctx:
                    validate_config(transport_is_mock=False, **kwargs)
                message = str(ctx.exception)
                self.assertNotIn(key, message)
                self.assertNotIn(secret, message)
        for key in ("pdl_live_apikey_LEAKME", None):
            with self.assertRaises(PaddleConfigError) as ctx:
                environment_for_key(key)
            self.assertNotIn("LEAKME", str(ctx.exception))


class HostPolicyTests(unittest.TestCase):
    def test_api_url_uses_the_constant_host(self):
        self.assertEqual(api_url("live", "/transactions"), f"https://{LIVE_HOST}/transactions")
        self.assertEqual(api_url("sandbox", "/transactions/" + TXN), f"https://{SANDBOX_HOST}/transactions/{TXN}")
        for path in ("transactions", "", "//evil.example/x", "/a\\b", "/a@b", "/a#b", "/a b", "/a\tb", "/a\nb"):
            with self.subTest(path=path):
                with self.assertRaises(PaddleTransportRejected):
                    api_url("live", path)
        with self.assertRaises(PaddleConfigError):
            api_url("staging", "/transactions")

    def test_response_url_must_be_the_constant_host_over_tls(self):
        for url in (f"https://{SANDBOX_HOST}/x", f"https://{SANDBOX_HOST}:443/x", f"https://{SANDBOX_HOST}/x?y=1"):
            with self.subTest(url=url):
                self.assertIsNone(check_response_url("sandbox", url))

    def test_response_url_rejects_other_schemes_hosts_and_ports(self):
        urls = (
            f"http://{SANDBOX_HOST}/x",
            f"ftp://{SANDBOX_HOST}/x",
            f"HTTPS://{LIVE_HOST}/x",                       # live host against the sandbox environment
            f"https://evil.example/x",
            f"https://{SANDBOX_HOST}.evil.example/x",
            f"https://evil.{SANDBOX_HOST}/x",
            f"https://x{SANDBOX_HOST}/x",
            f"https://{SANDBOX_HOST}./x",                   # trailing dot is a different name
            f"https://{SANDBOX_HOST}:8443/x",
            f"https://{SANDBOX_HOST}:0/x",
            f"https://127.0.0.1/x",
            f"https://[::1]/x",
            f"https://[2001:db8::1]/x",
            f"https://[api.paddle.com]/x",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(PaddleTransportRejected):
                    check_response_url("sandbox", url)

    def test_response_url_rejects_userinfo_forms(self):
        for url in (f"https://user@{SANDBOX_HOST}/x",
                    f"https://user:pass@{SANDBOX_HOST}/x",
                    f"https://@{SANDBOX_HOST}/x",
                    f"https://{SANDBOX_HOST}@evil.example/x",
                    f"https://api.paddle.com%2F@evil.example/x"):
            with self.subTest(url=url):
                with self.assertRaises(PaddleTransportRejected):
                    check_response_url("sandbox", url)

    def test_response_url_rejects_idn_and_percent_encoded_hosts(self):
        for url in ("https://\u0430pi.paddle.com/x",            # cyrillic а, not ascii
                    "https://xn--pi-6ka.paddle.com/x",          # punycode is a different name
                    "https://api%2epaddle%2ecom/x",
                    f"https://{SANDBOX_HOST}%2e%2e.attacker/x"):
            with self.subTest(url=url):
                with self.assertRaises(PaddleTransportRejected):
                    check_response_url("sandbox", url)

    def test_response_url_rejects_control_characters_and_oversize(self):
        big = f"https://{SANDBOX_HOST}/" + "a" * 2100
        for url in (f"https://{SANDBOX_HOST}/x\n", f"https://{SANDBOX_HOST}/x\x00", f"https://{SANDBOX_HOST}/x\x7f",
                    f"https://{SANDBOX_HOST}/x ", big, f"https://{SANDBOX_HOST}\\@evil.example/x", None, 123):
            with self.subTest(url=url if not isinstance(url, str) or len(url) < 40 else url[:40]):
                with self.assertRaises(PaddleTransportRejected):
                    check_response_url("sandbox", url)
        with self.assertRaises(PaddleConfigError):
            check_response_url("staging", f"https://{SANDBOX_HOST}/x")


if __name__ == "__main__":
    unittest.main()
