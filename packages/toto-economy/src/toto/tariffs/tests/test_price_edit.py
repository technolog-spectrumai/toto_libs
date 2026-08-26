"""Editing what an action costs, and getting the new price to stay.

The reported symptom was that a new price did not survive a refresh, and the
cause was not the write path at all — it was validation failing on a field
nobody had touched. `TariffItemForm` narrowed `charged_asset` to active assets
only, so the bound value of an item priced in a retired currency was "not one of
the available choices". The form was invalid, the view re-rendered it at HTTP
200 instead of redirecting, and the price silently did not change.

On a platform with NO currencies at all — the state every install is in until
somebody engraves one — that dropdown is empty and no price can EVER be saved.
That is the condition these tests care about most, because it is the one an
operator meets first.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from toto.assets.models import AccountType, LedgerAccount, to_base_units
from toto.assets.testing import LedgerTestCase as TestCase
from toto.assets.testing import make_asset
from toto.core.models import Platform
from toto.tariffs.models import (BillingMetric, BillingUnit, Tariff,
                                 TariffItem, TariffStatus)

User = get_user_model()


class PriceEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.punter = User.objects.create_user("punter", password="pw")
        cls.asset = make_asset(name="Assarion", unit_name="ASR", decimals=9,
                               max_supply_base_units=10 ** 15, active=True)
        cls.revenue = LedgerAccount.objects.create(
            code="platform-usage-fees", name="Fees",
            account_type=AccountType.SYSTEM, active=True)
        cls.tariff = Tariff.objects.create(
            code="platform-default", name="Platform default",
            status=TariffStatus.ACTIVE)
        cls.unit = BillingUnit.objects.create(
            code="request", label="REQUEST", app_label="tariffs", active=True)
        cls.metric = BillingMetric.objects.create(
            code="storage.request", label="Upload", app_label="vault",
            default_unit=cls.unit, active=True)
        cls.item = TariffItem.objects.create(
            tariff=cls.tariff, metric=cls.metric, name="Upload",
            charged_asset=cls.asset, price_per_unit_display=Decimal("1.5"),
            unit=cls.unit, unit_quantity=1, receiving_account=cls.revenue,
            active=True)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def url(self):
        return reverse("tariffs:tariff_item_edit", args=[self.item.pk])

    def edit(self, **over):
        data = {"metric": self.metric.pk, "name": "Upload",
                "charged_asset": self.asset.pk, "price_per_unit_display": "2.5",
                "unit": self.unit.pk, "unit_quantity": "1",
                "receiving_account": self.revenue.pk,
                "minimum_charge_base_units": "0", "rounding_mode": "nearest",
                "active": "on", "metadata": "{}"}
        data.update(over)
        return self.client.post(self.url(), data)

    def stored(self):
        """Read it back from the database, not from the response."""
        return TariffItem.objects.get(pk=self.item.pk)

    # -- the happy path, asserted by re-reading ------------------------------

    def test_a_new_price_is_written_and_survives_a_refresh(self):
        response = self.edit(price_per_unit_display="2.5")
        self.assertRedirects(
            response,
            reverse("tariffs:tariff_detail", args=[self.tariff.uuid]))
        self.assertEqual(self.stored().price_per_unit_display, Decimal("2.5"))

    def test_the_billed_integer_follows_the_displayed_price(self):
        """Two numbers describe one price and only the second is ever charged.
        A display that moves without its base units is a price that lies."""
        self.edit(price_per_unit_display="2.5")
        item = self.stored()
        self.assertEqual(item.price_per_unit_base_units,
                         to_base_units(Decimal("2.5"), self.asset.decimals))

    def test_the_page_shows_the_new_price_afterwards(self):
        self.edit(price_per_unit_display="7.25")
        body = self.client.get(self.url()).content.decode()
        self.assertIn("7.25", body)

    def test_a_fractional_price_keeps_its_precision(self):
        self.edit(price_per_unit_display="0.000000001")
        self.assertEqual(self.stored().price_per_unit_base_units, 1)

    def test_a_zero_price_is_allowed(self):
        """Free is a price, and metering something at zero is how a metric is
        counted without being charged for."""
        self.edit(price_per_unit_display="0")
        self.assertEqual(self.stored().price_per_unit_display, Decimal("0"))

    # -- refusals ------------------------------------------------------------

    def test_a_negative_price_is_refused_and_changes_nothing(self):
        response = self.edit(price_per_unit_display="-3")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored().price_per_unit_display, Decimal("1.5"))

    def test_a_non_numeric_price_is_refused_and_changes_nothing(self):
        response = self.edit(price_per_unit_display="cheap")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored().price_per_unit_display, Decimal("1.5"))

    def test_a_refusal_says_so_at_the_top_of_the_form(self):
        """Not only beside the field: the failure is usually on a field nobody
        touched, and one scrolled past reads as a save that did nothing."""
        body = self.edit(price_per_unit_display="-3").content.decode()
        self.assertIn("This was not saved.", body)

    def test_confirmation_appears_only_after_it_is_written(self):
        refused = self.edit(price_per_unit_display="-3")
        self.assertNotIn(
            "updated",
            " ".join(str(m) for m in refused.context["messages"]).lower())
        landed = self.client.post(self.url(), {
            "metric": self.metric.pk, "name": "Upload",
            "charged_asset": self.asset.pk, "price_per_unit_display": "4",
            "unit": self.unit.pk, "unit_quantity": "1",
            "receiving_account": self.revenue.pk,
            "minimum_charge_base_units": "0", "rounding_mode": "nearest",
            "active": "on", "metadata": "{}"}, follow=True)
        self.assertEqual(self.stored().price_per_unit_display, Decimal("4"))
        self.assertIn("updated",
                      " ".join(str(m) for m in landed.context["messages"]).lower())

    # -- the bug itself ------------------------------------------------------

    def test_a_price_can_still_be_edited_when_its_currency_was_retired(self):
        """THE reported bug. The asset went inactive, so the item's own bound
        value stopped being an available choice and every save failed on it."""
        self.asset.active = False
        self.asset.save(update_fields=["active"])
        # An item billing in a dead currency must not stay active, so this is
        # the edit somebody would actually make: fix the price, stand it down.
        response = self.edit(price_per_unit_display="2.5", active="")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored().price_per_unit_display, Decimal("2.5"))

    def test_an_active_item_still_may_not_bill_in_a_retired_currency(self):
        """The half of the old rule worth keeping."""
        self.asset.active = False
        self.asset.save(update_fields=["active"])
        response = self.edit(price_per_unit_display="2.5", active="on")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored().price_per_unit_display, Decimal("1.5"))

    def test_with_no_currencies_at_all_it_says_what_is_actually_wrong(self):
        """The state a fresh platform is in.

        Asserted against CREATING an item, because that is where the dropdown
        is genuinely empty: an existing item keeps its own currency selectable
        (that is the fix above), so only a new one has nothing at all to choose.
        "Select a valid choice" against an empty select is a true statement
        about entirely the wrong problem.
        """
        from toto.assets.models import Asset

        Asset.objects.update(active=False)
        other = BillingMetric.objects.create(
            code="storage.transfer", label="Transfer", app_label="vault",
            default_unit=self.unit, active=True)
        response = self.client.post(
            reverse("tariffs:tariff_item_create", args=[self.tariff.uuid]),
            {"metric": other.pk, "name": "Transfer", "charged_asset": "",
             "price_per_unit_display": "1", "unit": self.unit.pk,
             "unit_quantity": "1", "receiving_account": self.revenue.pk,
             "minimum_charge_base_units": "0", "rounding_mode": "nearest",
             "active": "on", "metadata": "{}"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no currencies on this platform yet",
                      response.content.decode())
        self.assertEqual(TariffItem.objects.count(), 1)

    # -- authorisation -------------------------------------------------------

    def test_a_stranger_may_not_edit_a_price(self):
        self.client.force_login(self.punter)
        response = self.edit(price_per_unit_display="0.01")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stored().price_per_unit_display, Decimal("1.5"))
