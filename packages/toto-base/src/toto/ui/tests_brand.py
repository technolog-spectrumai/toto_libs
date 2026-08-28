"""What the chrome wears, and what decides it.

`PageProcessor` resolves one `brand` dict — name, logo, description — so the
app bar and the welcome page do not each carry a chain of `{% if %}`. These
are context assertions: no template is rendered, because the decision is the
thing under test.

The flag is the whole safety story. Every install that ever ran `init_data`
carries a seeded "Toto-Federation" attached to its platform, so a suite that
preferred a federation unasked would rename somebody's app bar on upgrade.
"""

from __future__ import annotations

import tempfile

from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from toto.core.models import Federation, Platform
from toto.ui.page import PageProcessor

MEDIA = tempfile.mkdtemp(prefix="brand-tests-")

PIXEL = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
         b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc"
         b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`"
         b"\x82")


@override_settings(MEDIA_ROOT=MEDIA)
class BrandResolutionTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(
            site_name="Zenobia", author="Tests", publication_year=2026,
            active=True)
        self.platform.logo.save("platform.png", SimpleUploadedFile(
            "platform.png", PIXEL), save=True)
        self.request = RequestFactory().get("/")
        self.request.user = AnonymousUser()

    def brand(self) -> dict:
        return PageProcessor().decorate({}, self.request)["brand"]

    def attach(self, *, name="The Holding", logo=True, description=""):
        federation = Federation.objects.create(name=name,
                                               description=description)
        if logo:
            federation.logo.save("holding.png",
                                 SimpleUploadedFile("holding.png", PIXEL),
                                 save=True)
        Platform.objects.filter(pk=self.platform.pk).update(
            federation=federation)
        return federation

    # -- the flag ---------------------------------------------------------
    @override_settings(BRAND_FROM_FEDERATION=False)
    def test_without_the_flag_the_platform_always_wins(self):
        """The upgrade promise: a seeded federation changes nothing.

        Stated explicitly rather than left to the default, because these run
        under a HOST's settings and the hosts that carry this feature turn it
        on — inheriting their value would test nothing.
        """
        self.attach()
        brand = self.brand()
        self.assertEqual(brand["name"], "Zenobia")
        self.assertIn("platform", brand["logo"])
        self.assertEqual(brand["source"], "platform")

    @override_settings(BRAND_FROM_FEDERATION=True)
    def test_with_the_flag_the_federation_brands(self):
        self.attach(description="Two companies and a chessboard.")
        brand = self.brand()
        self.assertEqual(brand["name"], "The Holding")
        self.assertIn("holding", brand["logo"])
        self.assertEqual(brand["description"],
                         "Two companies and a chessboard.")
        self.assertEqual(brand["source"], "federation")

    # -- per-field fallback ------------------------------------------------
    @override_settings(BRAND_FROM_FEDERATION=True)
    def test_a_federation_without_a_logo_keeps_the_platforms(self):
        """Resolved per FIELD: naming a holding must not blank the mark."""
        self.attach(logo=False)
        brand = self.brand()
        self.assertEqual(brand["name"], "The Holding")
        self.assertIn("platform", brand["logo"])

    @override_settings(BRAND_FROM_FEDERATION=True)
    def test_no_federation_falls_back_whole(self):
        brand = self.brand()
        self.assertEqual(brand["name"], "Zenobia")
        self.assertIn("platform", brand["logo"])
        self.assertEqual(brand["source"], "platform")

    @override_settings(BRAND_FROM_FEDERATION=True)
    def test_a_platform_without_a_logo_is_not_an_error(self):
        self.platform.logo.delete(save=True)
        self.attach(logo=False)
        self.assertIsNone(self.brand()["logo"])

    # -- the shapes templates rely on -------------------------------------
    @override_settings(BRAND_FROM_FEDERATION=True)
    def test_the_federation_logo_is_a_url_not_an_image_field(self):
        """`{{ federation.logo.url }}` renders an EMPTY src — three templates
        did exactly that. The context carries a string on purpose."""
        self.attach()
        federation = PageProcessor().decorate({}, self.request)["federation"]
        self.assertIsInstance(federation["logo"], str)
        self.assertTrue(federation["logo"].endswith(".png"))

    def test_maintenance_mode_still_has_a_brand(self):
        """No platform, no crash: the maintenance page renders the chrome."""
        brand = PageProcessor(maintenance_mode=True).decorate(
            {}, self.request)["brand"]
        self.assertEqual(brand["name"], "")
        self.assertIsNone(brand["logo"])
