"""A seeded price is seeded once; afterwards the number is staff's."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.tariffs import rate_card
from toto.tariffs.models import TariffItem


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True,
                   TARIFF_SEED_PRICES=True,
                   TARIFF_PRICES={"subscription.month": Decimal("0.5")})
class SeedOnceTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        call_command("ingress_assets", stdout=StringIO())

    def _item(self):
        from toto.quota.metrics import registry
        spec = registry.get("subscription.month")
        return TariffItem.objects.get(tariff=rate_card.default_tariff(),
                                      metric=rate_card.billing_metric_for(spec))

    def test_a_staff_edit_survives_the_next_ingress(self):
        out = StringIO()
        call_command("ingress_tariffs", stdout=out)
        item = self._item()
        self.assertEqual(item.price_per_unit_display, Decimal("0.5"))
        item.price_per_unit_display = Decimal("9")
        item.save()
        out = StringIO()
        call_command("ingress_tariffs", stdout=out)
        self.assertEqual(self._item().price_per_unit_display, Decimal("9"))
        self.assertIn("kept", out.getvalue())
