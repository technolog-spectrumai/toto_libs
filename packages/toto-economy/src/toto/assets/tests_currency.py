"""Currency identity: the genesis hash and what it refuses.

Run under ``toto.clearing.testing.settings`` (the economy's own runnable
settings), registered in zenobia's clean-env gate beside the clearing suite.
"""

import json

from django.test import SimpleTestCase

from toto.assets import currency_hash
from toto.assets.currency_hash import (GENESIS_CONTEXT, GENESIS_VERSION,
                                       GenesisError, build_genesis,
                                       canonical, compute_currency_hash,
                                       framed, verify_genesis_document)


def _document(**overrides):
    base = {
        "v": 1,
        "issuer_fingerprint": "a" * 64,
        "unit_name": "ASR",
        "name": "Assarion",
        "decimals": 9,
        "total_supply_base_units": 6_666_666_666_667,
        "genesis_nonce": "b" * 64,
        "issued_at": "2026-08-11T12:00:00+00:00",
    }
    base.update(overrides)
    return base


class CanonicalisationTests(SimpleTestCase):
    def test_key_order_does_not_change_the_bytes(self):
        forward = _document()
        backward = dict(reversed(list(forward.items())))
        self.assertEqual(canonical(forward), canonical(backward))

    def test_the_bytes_are_stable_across_a_json_round_trip(self):
        # A descriptor travels as JSON between repositories at very different
        # library versions; parsing and re-serialising must not move the hash.
        document = _document()
        round_tripped = json.loads(json.dumps(document))
        self.assertEqual(compute_currency_hash(document),
                         compute_currency_hash(round_tripped))

    def test_non_json_types_are_refused_not_coerced(self):
        # Deliberately stricter than the clearing wire's canonical(), which
        # uses default=str. Identity bytes must never depend on Python's
        # str() of a Decimal or a datetime.
        from decimal import Decimal

        with self.assertRaises(GenesisError):
            canonical({"supply": Decimal("5")})

    def test_unicode_in_names_is_preserved_not_escaped(self):
        one = compute_currency_hash(_document(name="Złoty"))
        other = compute_currency_hash(_document(name="Zloty"))
        self.assertNotEqual(one, other)


class FramingTests(SimpleTestCase):
    def test_the_frame_carries_context_nul_and_length(self):
        body = b"hello"
        frame = framed(body)
        self.assertTrue(frame.startswith(GENESIS_CONTEXT + b"\x00"))
        self.assertEqual(frame[len(GENESIS_CONTEXT) + 1:len(GENESIS_CONTEXT) + 5],
                         (5).to_bytes(4, "big"))
        self.assertTrue(frame.endswith(body))

    def test_a_genesis_frame_never_collides_with_a_clearing_frame(self):
        # The two subsystems share the framing DISCIPLINE, not the context:
        # a signature over a genesis document must be unverifiable as any
        # clearing message kind, and vice versa.
        from toto.clearing.services import wire

        body = canonical(_document())
        genesis_frame = framed(body)
        for kind in wire.CONTEXTS:
            self.assertNotEqual(genesis_frame, wire.framed(kind, body), kind)

    def test_the_context_is_versioned(self):
        self.assertIn(b"/v1/", GENESIS_CONTEXT)


class CurrencyHashTests(SimpleTestCase):
    def test_the_hash_is_prefixed_and_stable(self):
        digest = compute_currency_hash(_document())
        self.assertTrue(digest.startswith("tcur1:"))
        self.assertEqual(len(digest), 6 + 64)
        self.assertEqual(digest, compute_currency_hash(_document()))

    def test_every_identity_field_moves_the_hash(self):
        baseline = compute_currency_hash(_document())
        variations = {
            "issuer_fingerprint": "c" * 64,
            "unit_name": "TPLN",
            "name": "Toto Zloty",
            "decimals": 2,
            "total_supply_base_units": 42,
            "genesis_nonce": "d" * 64,
            "issued_at": "2027-01-01T00:00:00+00:00",
        }
        for field, value in variations.items():
            with self.subTest(field=field):
                self.assertNotEqual(
                    baseline, compute_currency_hash(_document(**{field: value})))

    def test_identical_metadata_still_differs_by_nonce(self):
        # Two currencies described identically are still two currencies —
        # the nonce is what makes a re-issue a NEW identity rather than a
        # collision with the retired one.
        one = build_genesis(issuer_fingerprint="a" * 64, unit_name="ASR",
                            name="Assarion", decimals=9,
                            total_supply_base_units=10 ** 9,
                            issued_at="2026-08-11T12:00:00+00:00")
        two = build_genesis(issuer_fingerprint="a" * 64, unit_name="ASR",
                            name="Assarion", decimals=9,
                            total_supply_base_units=10 ** 9,
                            issued_at="2026-08-11T12:00:00+00:00")
        self.assertNotEqual(compute_currency_hash(one),
                            compute_currency_hash(two))

    def test_supply_is_committed_so_the_promise_is_verifiable(self):
        # The whole point of putting a fixed supply in the preimage: a holder
        # can tell from the document alone how much will ever exist.
        few = _document(total_supply_base_units=1000)
        many = _document(total_supply_base_units=2000)
        self.assertNotEqual(compute_currency_hash(few),
                            compute_currency_hash(many))


class BuildGenesisTests(SimpleTestCase):
    def test_the_builder_fills_version_and_nonce(self):
        document = build_genesis(issuer_fingerprint="a" * 64, unit_name="ASR",
                                 name="Assarion", decimals=9,
                                 total_supply_base_units=10 ** 9,
                                 issued_at="2026-08-11T12:00:00+00:00")
        self.assertEqual(document["v"], GENESIS_VERSION)
        self.assertEqual(len(document["genesis_nonce"]), 64)
        int(document["genesis_nonce"], 16)  # hex or it raises

    def test_supply_must_be_a_positive_integer_of_base_units(self):
        # A float would make the identity bytes depend on repr(); zero or
        # negative is not a currency.
        for bad in (0, -1, 1.5, "1000", True):
            with self.subTest(supply=bad):
                with self.assertRaises(GenesisError):
                    compute_currency_hash(
                        _document(total_supply_base_units=bad))

    def test_a_supply_policy_field_is_malformed_not_ignored(self):
        # Flexible supply is a future v2 document. A v1 carrying a policy is a
        # caller confusing the two, and silently dropping it would let two
        # differing documents share a hash.
        document = _document()
        document["supply_policy"] = "issuable"
        with self.assertRaises(GenesisError):
            compute_currency_hash(document)

    def test_an_unknown_version_is_refused(self):
        # v1 means fixed supply by definition. A v2 document means something
        # this build has not been taught and must not guess at.
        with self.assertRaises(GenesisError):
            compute_currency_hash(_document(v=2))

    def test_decimals_are_bounded(self):
        for bad in (-1, 20, 1.5, "9"):
            with self.subTest(decimals=bad):
                with self.assertRaises(GenesisError):
                    compute_currency_hash(_document(decimals=bad))


class SignatureTests(SimpleTestCase):
    def _keypair(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)

        private = Ed25519PrivateKey.generate()
        return private, private.public_key()

    def _pem(self, public):
        from cryptography.hazmat.primitives import serialization

        return public.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def test_a_signed_document_verifies(self):
        private, public = self._keypair()
        document = _document()
        signature = currency_hash.sign_genesis(private, document)

        self.assertTrue(verify_genesis_document(
            self._pem(public), document, signature))

    def test_tampering_with_any_field_breaks_the_signature(self):
        private, public = self._keypair()
        document = _document()
        signature = currency_hash.sign_genesis(private, document)

        tampered = _document(decimals=2)
        self.assertFalse(verify_genesis_document(
            self._pem(public), tampered, signature))

    def test_a_different_key_does_not_verify(self):
        private, _ = self._keypair()
        _, other_public = self._keypair()
        document = _document()
        signature = currency_hash.sign_genesis(private, document)

        self.assertFalse(verify_genesis_document(
            self._pem(other_public), document, signature))

    def test_garbage_inputs_return_false_rather_than_raising(self):
        # Verification runs against catalogue entries pulled from a URL an
        # operator typed; a malformed entry is refused, never a 500.
        self.assertFalse(verify_genesis_document("not a pem", _document(), "zz"))
        _, public = self._keypair()
        self.assertFalse(verify_genesis_document(self._pem(public),
                                                 _document(), "not-hex"))
