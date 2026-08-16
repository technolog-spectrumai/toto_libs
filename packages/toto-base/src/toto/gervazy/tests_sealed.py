"""The shared seal format.

The cross-language half of these lives in the desktop app's crate
(``gervasius::gervazy::cross_language``); what can be asserted from here is
that the format is self-describing, that the old era still opens, and that a
frame this module writes matches the layout the other side parses.
"""
from django.test import SimpleTestCase

from . import sealed

SALT = bytes(range(16))
COSTS = {"memory_cost": 19456, "iterations": 2, "lanes": 1}


class SealFormatTests(SimpleTestCase):
    def test_a_frame_announces_itself(self):
        # A file on somebody's laptop has no `is_encrypted` column, so the
        # bytes must say what they are.
        frame = sealed.seal("pw", SALT, b"bytes", **COSTS)
        self.assertTrue(frame.startswith(sealed.MAGIC))
        self.assertEqual(frame[len(sealed.MAGIC)], sealed.VERSION)
        self.assertTrue(sealed.is_current(frame))
        self.assertTrue(sealed.is_sealed(frame))

    def test_the_layout_is_what_the_other_side_parses(self):
        # magic(8) || version(1) || nonce(24) || ct+tag(16+)
        frame = sealed.seal("pw", SALT, b"", **COSTS)
        self.assertEqual(len(frame), 9 + sealed.NONCE_LEN + sealed.TAG_LEN)

    def test_plaintext_is_never_mistaken_for_a_frame(self):
        for plain in (b"hello", b"", b"TOTOSEA", b"<svg/>"):
            self.assertFalse(sealed.is_current(plain), plain)
            self.assertFalse(sealed.is_sealed(plain), plain)

    def test_round_trip(self):
        frame = sealed.seal("pw", SALT, b"bytes", **COSTS)
        self.assertEqual(sealed.open_any("pw", SALT, frame, **COSTS), b"bytes")

    def test_wrong_password_and_tampering_answer_the_same(self):
        # Telling them apart would tell an attacker which one they achieved.
        frame = sealed.seal("pw", SALT, b"bytes", **COSTS)
        with self.assertRaises(sealed.SealError):
            sealed.open_any("nope", SALT, frame, **COSTS)
        torn = frame[:-1] + bytes([frame[-1] ^ 1])
        with self.assertRaises(sealed.SealError):
            sealed.open_any("pw", SALT, torn, **COSTS)

    def test_the_aad_binds_a_frame_to_its_purpose(self):
        # The property Fernet could not express, and why a legacy token could
        # be moved from one file to another undetected.
        frame = sealed.seal("pw", SALT, b"secret", aad=b"file:a", **COSTS)
        with self.assertRaises(sealed.SealError):
            sealed.open_frame("pw", SALT, frame, aad=b"file:b", **COSTS)
        self.assertEqual(
            sealed.open_frame("pw", SALT, frame, aad=b"file:a", **COSTS), b"secret")

    def test_the_costs_are_part_of_the_key(self):
        frame = sealed.seal("pw", SALT, b"bytes", **COSTS)
        with self.assertRaises(sealed.SealError):
            sealed.open_any("pw", SALT, frame, memory_cost=19456,
                            iterations=3, lanes=1)


class LegacyFernetTests(SimpleTestCase):
    """Files sealed before 8/2026 must keep opening. Nothing writes these."""

    def _legacy(self, password: str, data: bytes) -> bytes:
        import base64
        from cryptography.fernet import Fernet
        key = sealed.derive_raw_key(password, SALT, **COSTS)
        return Fernet(base64.urlsafe_b64encode(key)).encrypt(data)

    def test_open_any_reads_the_old_era(self):
        token = self._legacy("pw", b"old bytes")
        self.assertTrue(sealed.is_sealed(token))
        self.assertFalse(sealed.is_current(token))
        self.assertEqual(sealed.open_any("pw", SALT, token, **COSTS), b"old bytes")

    def test_a_wrong_password_on_a_legacy_token_fails_the_same_way(self):
        token = self._legacy("pw", b"old bytes")
        with self.assertRaises(sealed.SealError):
            sealed.open_any("nope", SALT, token, **COSTS)
