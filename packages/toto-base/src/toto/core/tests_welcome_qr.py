"""The welcome page's "scan to open on your phone" QR.

The QR itself is drawn in the browser (see ``oya/partials/_local_qr.html`` for
why the server cannot name a working address), so what is testable here is the
wiring: the partial is on the page, it is not gated behind a login, and the
vendored library it needs is actually referenced. Whether the drawn code decodes
is the library's business, and it round-trips through ``toto.core.qr.read`` in
the manual check recorded in the commit.
"""

from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform


class WelcomeQRTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)

    def _html(self):
        response = self.client.get(reverse("core:welcome"), follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_anonymous_visitors_get_the_qr(self):
        """The whole point is handing a screen to someone who is not logged in.

        Unlike the onion/tailnet QRs beside it, this encodes the address the
        visitor already used to reach the page, so gating it would withhold
        nothing and defeat the feature.
        """
        html = self._html()
        self.assertIn('id="local-qr-wrap"', html)
        self.assertIn('id="local-qr"', html)

    def test_the_vendored_library_is_referenced(self):
        # An absent qrcode.min.js is a silently empty box, so name it.
        self.assertIn("qrcodejs/qrcode.min.js", self._html())

    def test_the_address_comes_from_the_browser(self):
        """Not from the server: behind nginx, a tunnel or a port-forward the
        server's idea of its own address is routinely not one that works."""
        html = self._html()
        self.assertIn("window.location.origin", html)

    def test_it_starts_hidden_so_a_js_less_page_shows_no_empty_frame(self):
        html = self._html()
        wrap = html[html.index('id="local-qr-wrap"'):][:200]
        self.assertIn("hidden", wrap)
