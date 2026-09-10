"""Attacking the manager's authentication.

Every test here is an attempt to get a request accepted that should not be.
"""

from __future__ import annotations

import time

from django.test import SimpleTestCase

from toto.anastasia.executor import protocol

SECRET = "shared-secret-for-tests"
OTHER = "a-different-secret"


def headers_for(body=b"{}", *, secret=SECRET, method="POST", path="/executions",
                **overrides):
    sent = protocol.sign(secret=secret, method=method, path=path, body=body)
    sent.update(overrides)
    return sent


class SigningTests(SimpleTestCase):
    def test_a_correctly_signed_request_verifies(self):
        body = protocol.encode({"capsule": "g", "operation": "render_pdf"})
        protocol.verify(secret=SECRET, method="POST", path="/executions",
                        body=body, headers=headers_for(body))

    def test_a_wrong_secret_is_refused(self):
        body = b"{}"
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=body, headers=headers_for(body, secret=OTHER))

    def test_a_tampered_body_is_refused(self):
        """The signature covers the BODY. Without that, a valid signature for
        one request could be replayed carrying a different payload."""
        signed = protocol.encode({"limits": {"ram_mb": 128}})
        tampered = protocol.encode({"limits": {"ram_mb": 999999}})
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=tampered, headers=headers_for(signed))

    def test_a_tampered_path_is_refused(self):
        body = b"{}"
        sent = headers_for(body, path="/executions")
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST",
                            path="/capsules/x/unmount", body=body, headers=sent)

    def test_a_tampered_method_is_refused(self):
        body = b"{}"
        sent = headers_for(body, method="GET")
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=body, headers=sent)

    def test_missing_headers_are_refused(self):
        body = b"{}"
        for drop in (protocol.HEADER_SIGNATURE, protocol.HEADER_TIMESTAMP,
                     protocol.HEADER_NONCE):
            with self.subTest(missing=drop):
                sent = headers_for(body)
                del sent[drop]
                with self.assertRaises(protocol.SignatureError):
                    protocol.verify(secret=SECRET, method="POST",
                                    path="/executions", body=body, headers=sent)

    def test_an_empty_secret_never_verifies(self):
        """A deployment that forgot the secret must not accidentally accept
        everything signed with an empty key."""
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret="", method="POST", path="/executions",
                            body=b"{}", headers=headers_for(b"{}"))
        with self.assertRaises(protocol.SignatureError):
            protocol.sign(secret="", method="POST", path="/x", body=b"{}")


class FreshnessTests(SimpleTestCase):
    def test_an_old_request_is_refused(self):
        body = b"{}"
        stale = str(int(time.time()) - protocol.CLOCK_SKEW_SECONDS - 60)
        sent = protocol.sign(secret=SECRET, method="POST", path="/executions",
                             body=body, timestamp=stale)
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=body, headers=sent)

    def test_a_request_from_the_future_is_refused(self):
        """Symmetric on purpose: a far-future timestamp would otherwise stay
        valid for as long as the attacker chose."""
        body = b"{}"
        ahead = str(int(time.time()) + protocol.CLOCK_SKEW_SECONDS + 60)
        sent = protocol.sign(secret=SECRET, method="POST", path="/executions",
                             body=body, timestamp=ahead)
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=body, headers=sent)

    def test_a_non_numeric_timestamp_is_refused(self):
        body = b"{}"
        sent = headers_for(body, **{protocol.HEADER_TIMESTAMP: "yesterday"})
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=body, headers=sent)

    def test_a_replayed_request_is_refused_the_second_time(self):
        body = b"{}"
        sent = headers_for(body)
        cache = protocol.NonceCache()
        protocol.verify(secret=SECRET, method="POST", path="/executions",
                        body=body, headers=sent, nonces=cache)
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=body, headers=sent, nonces=cache)

    def test_an_absurd_nonce_is_refused_before_it_is_cached(self):
        """Otherwise the replay cache is a memory-growth vector."""
        body = b"{}"
        sent = headers_for(body, **{protocol.HEADER_NONCE: "n" * 5000})
        with self.assertRaises(protocol.SignatureError):
            protocol.verify(secret=SECRET, method="POST", path="/executions",
                            body=body, headers=sent)

    def test_the_nonce_cache_forgets_what_the_clock_already_refuses(self):
        """It is bounded by the skew window, not by a count — anything older is
        refused by the timestamp check anyway, so it need not be remembered."""
        cache = protocol.NonceCache(window_seconds=100)
        now = time.time()
        self.assertTrue(cache.check_and_add("a", now))
        self.assertFalse(cache.check_and_add("a", now))
        self.assertTrue(cache.check_and_add("a", now + 200))
        self.assertEqual(len(cache._seen), 1)


class EncodingTests(SimpleTestCase):
    def test_encoding_is_deterministic(self):
        """The body is signed, so a dict that serialises two ways would produce
        two signatures for one request."""
        a = protocol.encode({"b": 1, "a": 2})
        b = protocol.encode({"a": 2, "b": 1})
        self.assertEqual(a, b)

    def test_bad_json_is_a_value_error_not_a_crash(self):
        with self.assertRaises(ValueError):
            protocol.decode(b"{not json")
