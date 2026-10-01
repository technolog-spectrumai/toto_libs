"""The certificate-expiry check (2026-10-01): a TLS handshake with the
platform's own public name, notAfter read from what it presents.

The certificates here are minted on the spot with the expiry each test needs;
no test reaches the network — the handshake is replaced, except in the one
test that runs it against a server on 127.0.0.1.
"""

from __future__ import annotations

import socket
import ssl
import tempfile
import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from toto.monit import record


def fake_certificate(days_left: float, name: str = "zenobia.example.org"):
    """(DER bytes, PEM certificate, PEM key) of a self-signed certificate that
    expires ``days_left`` days from now (negative: already expired)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(dt_timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=90))
            .not_valid_after(now + timedelta(days=days_left))
            .sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return (cert.public_bytes(serialization.Encoding.DER),
            cert.public_bytes(serialization.Encoding.PEM), pem_key)


PUBLIC = override_settings(MONIT_CERT_DOMAIN="zenobia.example.org")


@PUBLIC
class ThresholdTests(SimpleTestCase):
    """WARN under 21 days, FAIL under 7, expired is FAIL."""

    def check_with(self, days_left):
        der, _, _ = fake_certificate(days_left)
        with mock.patch.object(record, "peer_certificate", return_value=der) as fetch:
            check = record.check_certificate()
        fetch.assert_called_once_with("zenobia.example.org", 443)
        return check

    def test_a_certificate_with_weeks_left_is_ok(self):
        check = self.check_with(60)
        self.assertEqual(check.status, record.OK)
        self.assertIn("days", check.summary)
        self.assertEqual(check.detail, "zenobia.example.org")

    def test_under_three_weeks_is_a_warning(self):
        self.assertEqual(self.check_with(22).status, record.OK)
        self.assertEqual(self.check_with(20.5).status, record.WARN)
        self.assertEqual(self.check_with(8).status, record.WARN)

    def test_under_a_week_is_failing(self):
        check = self.check_with(6.5)
        self.assertEqual(check.status, record.FAIL)
        self.assertIn("in 6 days", check.summary)

    def test_an_expired_certificate_is_failing_and_says_so(self):
        check = self.check_with(-2)
        self.assertEqual(check.status, record.FAIL)
        self.assertTrue(check.summary.startswith("Expired on "), check.summary)

    def test_a_name_that_does_not_answer_is_unknown_never_raised(self):
        with mock.patch.object(record, "peer_certificate",
                               side_effect=ConnectionRefusedError("refused")):
            check = record.check_certificate()
        self.assertEqual(check.status, record.UNKNOWN)
        self.assertIn("ConnectionRefusedError", check.detail)


class WhichNameTests(SimpleTestCase):
    """The profile's ssl.domain (MONIT_CERT_DOMAIN) first, then PLATFORM_DOMAIN;
    no public name means OFF and no handshake at all."""

    def target(self, **settings):
        with override_settings(**settings):
            return record.cert_target()

    def test_no_public_domain_is_not_checked(self):
        for domain in ("", "localhost", "127.0.0.1", "192.168.1.20:8443",
                       "zenobia.local", "box.internal", "intranet", "[::1]"):
            with self.subTest(domain=domain), \
                    override_settings(MONIT_CERT_DOMAIN=domain), \
                    mock.patch.object(record, "peer_certificate") as fetch:
                check = record.check_certificate()
                self.assertEqual(check.status, record.OFF)
                self.assertIn("Not checked", check.summary)
                fetch.assert_not_called()

    def test_the_ssl_domain_wins_over_the_platform_domain(self):
        self.assertEqual(self.target(MONIT_CERT_DOMAIN="tls.example.org",
                                     PLATFORM_DOMAIN="www.example.org"),
                         ("tls.example.org", 443))

    def test_set_but_empty_means_no_certificate_here(self):
        """deploy.py writes it empty for ssl.mode none: nginx serves none."""
        self.assertIsNone(self.target(MONIT_CERT_DOMAIN="",
                                      PLATFORM_DOMAIN="www.example.org"))

    def test_without_the_setting_the_platform_domain_is_read(self):
        """A host that never defines MONIT_CERT_DOMAIN (deleted inside the
        override, which restores it on the way out)."""
        from django.conf import settings

        with override_settings(PLATFORM_DOMAIN="www.example.org"):
            if hasattr(settings, "MONIT_CERT_DOMAIN"):
                del settings.MONIT_CERT_DOMAIN
            self.assertEqual(record.cert_target(), ("www.example.org", 443))

    def test_scheme_path_and_port(self):
        self.assertEqual(self.target(MONIT_CERT_DOMAIN="https://Portal.Example.org/x/"),
                         ("portal.example.org", 443))
        self.assertEqual(self.target(MONIT_CERT_DOMAIN="portal.example.org:8443"),
                         ("portal.example.org", 8443))
        self.assertIsNone(self.target(MONIT_CERT_DOMAIN="http://portal.example.org"),
                          "served in the clear: no certificate to read")

    def test_the_check_is_one_of_the_record_checks(self):
        self.assertIn(record.check_certificate, record.ALL_CHECKS)


class HandshakeTests(SimpleTestCase):
    """The real handshake, against a TLS server on 127.0.0.1 presenting a
    self-signed certificate — the kind a verifying handshake would refuse."""

    def serve_once(self, pem_cert, pem_key):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        cert_path = Path(scratch.name) / "cert.pem"
        key_path = Path(scratch.name) / "key.pem"
        cert_path.write_bytes(pem_cert)
        key_path.write_bytes(pem_key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        self.addCleanup(listener.close)

        def answer():
            try:
                conn, _ = listener.accept()
                with context.wrap_socket(conn, server_side=True):
                    pass
            except OSError:
                pass

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        return listener.getsockname()[1]

    def test_a_self_signed_certificate_is_read_not_refused(self):
        der, pem_cert, pem_key = fake_certificate(10)
        port = self.serve_once(pem_cert, pem_key)
        presented = record.peer_certificate("127.0.0.1", port)
        self.assertEqual(presented, der)
        left = record.not_after(presented) - datetime.now(dt_timezone.utc)
        self.assertAlmostEqual(left.total_seconds() / 86400, 10, delta=0.01)
