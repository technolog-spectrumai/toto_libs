"""Detached signatures: applied at append, verified without a key custodian."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import TestCase

from toto.ledger.services import chain, signing


def _keypair():
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private, public_pem


class SigningTests(TestCase):
    def setUp(self):
        self.private, self.public_pem = _keypair()
        self.ledger = chain.open_ledger(key="acme", name="Acme")

    def test_a_signed_block_verifies_against_its_public_key(self):
        entry = chain.append(
            ledger=self.ledger, payload={"n": 1},
            signer=signing.ed25519_signer(self.private, key_id="board-key"),
        )
        self.assertTrue(entry.signature)
        self.assertEqual(entry.signature_key_id, "board-key")
        self.assertEqual(entry.signature_algorithm, "ed25519")
        self.assertTrue(signing.verify_signature(entry, self.public_pem))

    def test_another_key_does_not_verify_it(self):
        entry = chain.append(
            ledger=self.ledger, payload={"n": 1},
            signer=signing.ed25519_signer(self.private),
        )
        _, other_pem = _keypair()
        self.assertFalse(signing.verify_signature(entry, other_pem))

    def test_an_unsigned_block_does_not_claim_a_signature(self):
        entry = chain.append(ledger=self.ledger, payload={"n": 1})
        self.assertEqual(entry.signature, "")
        self.assertFalse(signing.verify_signature(entry, self.public_pem))

    def test_signing_is_optional_and_an_unsigned_chain_still_verifies(self):
        chain.append(ledger=self.ledger, payload={"n": 1})
        chain.append(ledger=self.ledger, payload={"n": 2},
                     signer=signing.ed25519_signer(self.private))
        self.assertTrue(chain.verify(self.ledger))

    def test_a_signature_covers_the_block_not_just_its_digest(self):
        """Lifting a signature onto another block must not work.

        Signing the canonical block XML — the same text the hash is taken over
        — means the signature commits to the block's content AND its place in
        the chain, so it cannot be replayed onto a different block.
        """
        from toto.ledger.tests.test_verify import tamper

        entry = chain.append(
            ledger=self.ledger, payload={"n": 1},
            signer=signing.ed25519_signer(self.private),
        )
        other = chain.append(ledger=self.ledger, payload={"n": 2})
        tamper(other.pk, signature=entry.signature, signature_algorithm="ed25519")
        other.refresh_from_db()
        self.assertFalse(signing.verify_signature(other, self.public_pem))

    def test_an_unknown_signature_algorithm_refuses_rather_than_guessing(self):
        from toto.ledger.tests.test_verify import tamper

        entry = chain.append(
            ledger=self.ledger, payload={"n": 1},
            signer=signing.ed25519_signer(self.private),
        )
        tamper(entry.pk, signature_algorithm="rsa-pss")
        entry.refresh_from_db()
        with self.assertRaises(signing.SignatureError):
            signing.verify_signature(entry, self.public_pem)

    def test_a_non_ed25519_key_is_refused_at_signer_creation(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(signing.SignatureError):
            signing.ed25519_signer(key)
