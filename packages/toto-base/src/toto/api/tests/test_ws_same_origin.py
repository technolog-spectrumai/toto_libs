"""The cookie-only socket's Origin rule (2026-10-04): Django's CSRF rule for
an Origin header, on a handshake. ``toto.api.ws_origin.is_same_origin``.

    manage.py test toto.api.tests.test_ws_same_origin
"""

import unittest

from django.test import SimpleTestCase, override_settings

try:  # the socket layer is the host's choice
    from toto.api.ws_origin import TotoOriginValidator, is_same_origin
    HAVE_CHANNELS = True
except ImportError:
    HAVE_CHANNELS = False


def scope(origin, host="portal.example.org", scheme="ws", **extra):
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    headers += [(name.replace("_", "-").encode(), value.encode()) for name, value in extra.items()]
    return {"type": "websocket", "scheme": scheme, "headers": headers}


@unittest.skipUnless(HAVE_CHANNELS, "channels is not installed on this host")
@override_settings(ALLOWED_HOSTS=["portal.example.org", "localhost"], CSRF_TRUSTED_ORIGINS=[],
                   SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
class SameOriginTests(SimpleTestCase):
    def test_this_very_origin_passes(self):
        self.assertTrue(is_same_origin(scope("http://portal.example.org")))
        self.assertTrue(is_same_origin(scope("https://portal.example.org", scheme="wss")))
        self.assertTrue(is_same_origin(scope("https://portal.example.org",
                                             x_forwarded_proto="https")))
        self.assertTrue(is_same_origin(scope("http://localhost:8000", host="localhost:8000")))

    def test_no_origin_is_refused(self):
        for origin in (None, "", "null"):
            with self.subTest(origin=origin):
                self.assertFalse(is_same_origin(scope(origin)))

    def test_another_port_of_the_same_machine_is_another_origin(self):
        """What the host-name rule lets through, and this one does not."""
        other = scope("http://localhost:9999", host="localhost")
        self.assertFalse(is_same_origin(other))
        from urllib.parse import urlparse

        self.assertTrue(TotoOriginValidator(lambda *a: None).valid_origin(
            urlparse("http://localhost:9999")))

    def test_another_scheme_host_or_lookalike_is_refused(self):
        for origin in ("https://portal.example.org", "http://evil.example.com",
                       "http://portal.example.org.evil.com", "tauri://localhost",
                       "http://portal.example.org:8080"):
            with self.subTest(origin=origin):
                self.assertFalse(is_same_origin(scope(origin)))

    def test_a_host_this_platform_does_not_answer_to_names_no_origin(self):
        self.assertFalse(is_same_origin(scope("http://evil.example.com", host="evil.example.com")))

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://portal.example.org:8443",
                                             "https://*.trusted.example"])
    def test_a_trusted_origin_passes_exactly_or_by_its_pattern(self):
        self.assertTrue(is_same_origin(scope("https://portal.example.org:8443")))
        self.assertTrue(is_same_origin(scope("https://a.trusted.example")))
        self.assertFalse(is_same_origin(scope("http://a.trusted.example")))
        self.assertFalse(is_same_origin(scope("https://portal.example.org:9443")))
