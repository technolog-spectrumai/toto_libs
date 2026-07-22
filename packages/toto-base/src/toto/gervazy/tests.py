"""Gervazy crypto tests — the AES-256-GCM blob helpers used for at-rest encryption."""
import ipaddress
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidTag
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from toto.gervazy.crypto import GervazyCryptoSession

User = get_user_model()


class BlobHelperTests(TestCase):
    """encrypt_blob/decrypt_blob (the raw-blob envelope, no DB row of its own)."""

    def setUp(self):
        self.user = User.objects.create_user(username="vaultowner", password="x")
        self.session, self.wk = GervazyCryptoSession.initialize_strongbox(
            self.user, "test-box", "pw"
        )

    def test_roundtrip_and_opaque_ciphertext(self):
        ct, nonce = self.session.encrypt_blob(self.wk, b"secret bytes", b"aad-ctx")
        self.assertNotIn(b"secret bytes", ct)  # ciphertext reveals no plaintext
        self.assertEqual(
            self.session.decrypt_blob(self.wk, ct, nonce, b"aad-ctx"), b"secret bytes"
        )

    def test_wrong_aad_fails(self):
        ct, nonce = self.session.encrypt_blob(self.wk, b"x", b"aad-1")
        with self.assertRaises(InvalidTag):
            self.session.decrypt_blob(self.wk, ct, nonce, b"aad-2")

    def test_tampered_ciphertext_fails(self):
        ct, nonce = self.session.encrypt_blob(self.wk, b"hello", b"")
        bad = bytearray(ct)
        bad[0] ^= 0xFF
        with self.assertRaises(InvalidTag):
            self.session.decrypt_blob(self.wk, bytes(bad), nonce, b"")

    def test_create_data_key_is_independent(self):
        wk2 = self.session.create_data_key()
        self.assertNotEqual(wk2.pk, self.wk.pk)
        ct, nonce = self.session.encrypt_blob(wk2, b"under key2", b"")
        self.assertEqual(self.session.decrypt_blob(wk2, ct, nonce, b""), b"under key2")
        # A ciphertext made under key2 must not decrypt under key1.
        with self.assertRaises(InvalidTag):
            self.session.decrypt_blob(self.wk, ct, nonce, b"")

    def test_fresh_session_decrypts_after_reload(self):
        """A new session (cold KDF) decrypts what an earlier session encrypted."""
        ct, nonce = self.session.encrypt_blob(self.wk, b"persisted", b"a")
        fresh = GervazyCryptoSession(self.session._strongbox, "pw")
        self.assertEqual(fresh.decrypt_blob(self.wk, ct, nonce, b"a"), b"persisted")


class GenSslCertCommandTests(TestCase):
    """The gen_ssl_cert management command — run from the web entrypoint to create
    the gervazy self-signed cert in-container (for ssl.mode: gervazy)."""

    def test_generates_cert_with_expected_sans(self):
        with tempfile.TemporaryDirectory() as d:
            cert = Path(d) / "localhost.crt"
            key = Path(d) / "localhost.key"
            call_command(
                "gen_ssl_cert",
                cert_path=str(cert),
                key_path=str(key),
                common_name="localhost",
                dns_names="localhost,vps-86826fd4.vps.ovh.net",
                ip_addresses="127.0.0.1,146.59.92.66",
            )
            self.assertTrue(cert.exists())
            self.assertTrue(key.exists())
            san = (
                x509.load_pem_x509_certificate(cert.read_bytes())
                .extensions.get_extension_for_class(x509.SubjectAlternativeName)
                .value
            )
            self.assertIn("vps-86826fd4.vps.ovh.net", san.get_values_for_type(x509.DNSName))
            self.assertIn(
                ipaddress.ip_address("146.59.92.66"),
                san.get_values_for_type(x509.IPAddress),
            )

    def test_idempotent_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            cert = Path(d) / "c.crt"
            key = Path(d) / "c.key"
            call_command("gen_ssl_cert", cert_path=str(cert), key_path=str(key))
            original = cert.read_bytes()
            call_command("gen_ssl_cert", cert_path=str(cert), key_path=str(key))
            self.assertEqual(cert.read_bytes(), original)  # unchanged on re-run
