"""The visitor's address, said in one place (2026-09-30): X-Real-IP only from a
trusted proxy, REMOTE_ADDR otherwise, and never X-Forwarded-For."""

from django.test import RequestFactory, SimpleTestCase, override_settings

from toto.core import client_ip as ip


def _request(**meta):
    request = RequestFactory().get("/")
    request.META.pop("REMOTE_ADDR", None)
    request.META.update(meta)
    return request


class ClientIpTests(SimpleTestCase):
    @override_settings(TRUSTED_PROXIES=None)
    def test_loopback_is_the_only_proxy_trusted_by_default(self):
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="127.0.0.1",
                                               HTTP_X_REAL_IP="203.0.113.7")), "203.0.113.7")
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="::1",
                                               HTTP_X_REAL_IP="203.0.113.7")), "203.0.113.7")
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="172.18.0.5",
                                               HTTP_X_REAL_IP="203.0.113.7")), "172.18.0.5")

    @override_settings(TRUSTED_PROXIES=["172.16.0.0/12"])
    def test_a_named_network_is_believed_and_a_stranger_is_not(self):
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="172.18.0.5",
                                               HTTP_X_REAL_IP="203.0.113.7")), "203.0.113.7")
        # A visitor reaching Django directly cannot name somebody else.
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="198.51.100.9",
                                               HTTP_X_REAL_IP="203.0.113.7")), "198.51.100.9")
        # The list replaces the default: loopback is not trusted any more.
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="127.0.0.1",
                                               HTTP_X_REAL_IP="203.0.113.7")), "127.0.0.1")

    @override_settings(TRUSTED_PROXIES="10.0.0.1, 192.168.0.0/16")
    def test_a_comma_separated_string_is_a_list(self):
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="192.168.4.4",
                                               HTTP_X_REAL_IP="203.0.113.7")), "203.0.113.7")
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="10.0.0.1",
                                               HTTP_X_REAL_IP="203.0.113.8")), "203.0.113.8")

    @override_settings(TRUSTED_PROXIES=[], TRUST_X_REAL_IP=True)
    def test_trust_x_real_ip_believes_every_peer(self):
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="198.51.100.9",
                                               HTTP_X_REAL_IP="203.0.113.7")), "203.0.113.7")

    @override_settings(TRUSTED_PROXIES=None)
    def test_x_forwarded_for_is_never_read(self):
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="127.0.0.1",
                                               HTTP_X_FORWARDED_FOR="203.0.113.66")), "127.0.0.1")

    @override_settings(TRUSTED_PROXIES=None)
    def test_a_header_that_is_not_an_address_falls_back_to_the_peer(self):
        for junk in ("", "nobody", "203.0.113.7, 10.0.0.1", "999.1.1.1"):
            with self.subTest(junk=junk):
                self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="127.0.0.1",
                                                       HTTP_X_REAL_IP=junk)), "127.0.0.1")

    def test_no_address_at_all_is_empty(self):
        self.assertEqual(ip.client_ip(_request()), "")
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="not-an-ip")), "")
        self.assertEqual(ip.client_ip(object()), "")

    @override_settings(TRUSTED_PROXIES=None)
    def test_an_ipv4_address_carried_as_ipv6_is_the_ipv4_one(self):
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="::ffff:127.0.0.1",
                                               HTTP_X_REAL_IP="::ffff:203.0.113.7")), "203.0.113.7")
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="[2001:db8::1]")), "2001:db8::1")
        self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="fe80::1%eth0")), "fe80::1")

    def test_a_typo_in_the_list_trusts_nothing_rather_than_everything(self):
        with override_settings(TRUSTED_PROXIES=["not-a-network", "10.0.0.0/8"]), \
                self.assertLogs("toto.core.client_ip", "WARNING"):
            self.assertEqual(ip.client_ip(_request(REMOTE_ADDR="198.51.100.9",
                                                   HTTP_X_REAL_IP="203.0.113.7")), "198.51.100.9")
        self.assertFalse(ip.is_trusted_proxy("garbage"))
