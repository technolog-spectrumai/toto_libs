"""The ledger's admin (2026-10-01).

    manage.py test toto.assets.tests_admin
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.assets.testing import TEST_ISSUER_KEY, make_asset

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
class AssetAdminTests(TestCase):
    """An asset is engraved by the mint, never typed into the admin: the add
    page refuses (it was a 500, and a row made there could not carry the
    genesis hash the table requires), while the list and an asset's page
    still open."""

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", password="pw")
        cls.asset = make_asset(unit_name="ADMN", name="Admin test", code="ADMN")

    def setUp(self):
        self.client.force_login(self.root)

    def test_the_add_page_refuses(self):
        self.assertEqual(self.client.get(reverse("admin:assets_asset_add")).status_code, 403)

    def test_the_list_offers_no_add_and_the_asset_opens(self):
        listing = self.client.get(reverse("admin:assets_asset_changelist"))
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, reverse("admin:assets_asset_add"))
        page = self.client.get(reverse("admin:assets_asset_change", args=[self.asset.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "ADMN")
