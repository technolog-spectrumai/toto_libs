"""The outbound guard: both sinks, one function.

The baseline fence lives here too — ``test_an_unresolvable_host_is_allowed_through``
is what keeps every existing peer test green, because their fixture host
resolves nowhere.
"""

from django.test import SimpleTestCase, override_settings

from toto.vault.outbound import (
    OutboundRefused,
    assert_outbound_allowed,
    canonical_outbound_url,
)


class CanonicalUrlTests(SimpleTestCase):
    def test_userinfo_and_backslash_are_refused(self):
        """The case where urlsplit and requests disagree about the host."""
        for bad in (r"https://evil.com\@good.com",
                    "https://user:pw@host.example.org",
                    "https://user@host.example.org"):
            with self.subTest(url=bad):
                with self.assertRaises(OutboundRefused):
                    canonical_outbound_url(bad, label="Peer URL")

    def test_a_non_http_scheme_is_refused(self):
        for bad in ("file:///etc/passwd", "gopher://x/", "ftp://host/"):
            with self.subTest(url=bad):
                with self.assertRaises(OutboundRefused):
                    canonical_outbound_url(bad, label="Peer URL")

    def test_an_empty_or_hostless_url_is_refused(self):
        for bad in ("", "   ", "https://"):
            with self.subTest(url=bad):
                with self.assertRaises(OutboundRefused):
                    canonical_outbound_url(bad, label="Peer URL")

    def test_a_default_port_is_dropped_and_the_host_lowercased(self):
        self.assertEqual(
            canonical_outbound_url("https://Peer.Example.ORG:443/vault/",
                                   label="Peer URL"),
            "https://peer.example.org/vault")

    def test_a_non_default_port_is_kept(self):
        self.assertEqual(
            canonical_outbound_url("https://peer.example.org:8443",
                                   label="Peer URL"),
            "https://peer.example.org:8443")


class OutboundGuardTests(SimpleTestCase):
    def test_loopback_private_link_local_and_reserved_are_refused(self):
        for bad in ("https://127.0.0.1/",
                    "https://169.254.169.254/latest/meta-data/",
                    "https://10.0.0.5/",
                    "https://192.168.1.10/",
                    "https://172.16.4.4/",
                    "https://[::1]/",
                    "https://0.0.0.0/"):
            with self.subTest(url=bad):
                with self.assertRaises(OutboundRefused) as caught:
                    assert_outbound_allowed(bad, label="S3 endpoint")
                self.assertIn("public address", str(caught.exception))

    def test_an_unresolvable_host_is_allowed_through(self):
        """The fence around the 92 baseline tests.

        peer.example.org is the fixture host in tests_peering, tests_peer_api,
        tests_mirror and tests_transfer. It resolves nowhere, and a name that
        does not resolve cannot be connected to — so refusing it would buy no
        safety and would redden the whole suite.
        """
        self.assertEqual(
            assert_outbound_allowed("https://peer.example.org", label="Peer URL"),
            "https://peer.example.org")

    @override_settings(VAULT_OUTBOUND_ALLOWED_HOSTS=["minio.internal"])
    def test_an_explicit_allowlist_entry_permits_a_private_host(self):
        self.assertEqual(
            assert_outbound_allowed("http://minio.internal:9000",
                                    label="S3 endpoint"),
            "http://minio.internal:9000")

    @override_settings(VAULT_OUTBOUND_ALLOWED_HOSTS=["127.0.0.1"])
    def test_the_allowlist_also_covers_a_literal_private_ip(self):
        self.assertEqual(
            assert_outbound_allowed("http://127.0.0.1:9000", label="S3 endpoint"),
            "http://127.0.0.1:9000")

    def test_plain_http_is_refused_unless_allowlisted(self):
        with self.assertRaises(OutboundRefused) as caught:
            assert_outbound_allowed("http://peer.example.org", label="Peer URL")
        self.assertIn("plain http", str(caught.exception))

    @override_settings(VAULT_OUTBOUND_ALLOW_PRIVATE=True)
    def test_the_dev_hatch_permits_plain_http(self):
        self.assertEqual(
            assert_outbound_allowed("http://peer.example.org", label="Peer URL"),
            "http://peer.example.org")
