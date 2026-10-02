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


class CertificateRenewalTests(TestCase):
    """Stage 51 (zenobia/todo.md item 7): the self-signed cert was kept as soon
    as both files existed, so a name added to cert:, tailscale switched on, or
    825 days passed left browsers warning, and members learn to click through
    a warning that would also hide a real interception."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gervazy-cert-"))
        self.cert, self.key = self.dir / "a.crt", self.dir / "a.key"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def ensure(self, **kwargs):
        from toto.gervazy.crypto import ensure_self_signed_certificate

        return ensure_self_signed_certificate(self.cert, self.key, **kwargs)

    def sans(self):
        cert = x509.load_pem_x509_certificate(self.cert.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return (set(san.get_values_for_type(x509.DNSName)),
                {str(a) for a in san.get_values_for_type(x509.IPAddress)})

    def test_a_new_name_regenerates_it(self):
        self.assertTrue(self.ensure(dns_names=["localhost"]))
        self.assertTrue(self.ensure(dns_names=["localhost", "new.example"]))
        self.assertIn("new.example", self.sans()[0])

    def test_a_new_address_regenerates_it(self):
        self.ensure(dns_names=["localhost"], ip_addresses=["127.0.0.1"])
        self.assertTrue(self.ensure(dns_names=["localhost"],
                                    ip_addresses=["127.0.0.1", "100.64.0.7"]))
        self.assertIn("100.64.0.7", self.sans()[1])

    def test_the_same_names_keep_it(self):
        self.ensure(dns_names=["localhost", "a.example"], ip_addresses=["127.0.0.1"])
        before = self.cert.read_bytes()
        self.assertFalse(self.ensure(dns_names=["a.example", "localhost"],
                                     ip_addresses=["127.0.0.1"]))
        self.assertEqual(self.cert.read_bytes(), before)

    def test_one_near_its_end_is_renewed(self):
        from toto.gervazy.crypto import generate_self_signed_certificate

        cert_pem, key_pem = generate_self_signed_certificate(
            common_name="localhost", dns_names=["localhost"], valid_days=10)
        self.cert.write_bytes(cert_pem)
        self.key.write_bytes(key_pem)
        self.assertTrue(self.ensure(dns_names=["localhost"]))
        cert = x509.load_pem_x509_certificate(self.cert.read_bytes())
        import datetime as dt
        self.assertGreater(cert.not_valid_after_utc,
                           dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=300))

    def test_a_certificate_it_did_not_make_is_left_alone(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        import datetime as dt

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "owner.example")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(1)
                .not_valid_before(now).not_valid_after(now + dt.timedelta(days=5))
                .sign(key, hashes.SHA256()))
        self.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        self.key.write_bytes(b"owner's key")
        before = self.cert.read_bytes()
        self.assertFalse(self.ensure(dns_names=["localhost", "new.example"]))
        self.assertEqual(self.cert.read_bytes(), before)
