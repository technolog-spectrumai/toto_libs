"""What this platform pays in, and the one place that answers it.

The question used to have several answers depending on who asked:
``settings.GAS_ASSET`` in ``assets.prepaid``, a currency contract in
``tariffs.rate_card``, a literal ticker in ingress. None was wrong on its own;
what was wrong is that changing what the platform settles in meant finding all
of them, and nobody running the platform could change it at all.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.assets.models import Asset, SettlementAsset
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.services.settlement import (DEFAULT_UNIT, set_settlement_asset,
                                             settlement_asset, settlement_choice)
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class SettlementTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.punter = User.objects.create_user("punter", password="pw")
        # TPLN as "an active currency that is not the default": with ASR the
        # default since MANA was retired, choosing ASR would be
        # indistinguishable from choosing nothing.
        self.tpln = Asset.objects.get(unit_name="TPLN")
        self.asr = Asset.objects.get(unit_name="ASR")


class DefaultTests(SettlementTestCase):

    def test_it_settles_in_asr_when_nobody_has_chosen(self):
        self.assertEqual(settlement_asset().unit_name, DEFAULT_UNIT)

    def test_the_default_is_not_recorded_as_a_choice(self):
        """"ASR because staff chose it" and "ASR because nobody has chosen"
        are different states, and the UI has to be able to tell them apart."""
        self.assertIsNone(settlement_choice())

    def test_with_no_currencies_at_all_it_answers_none_rather_than_guessing(self):
        Asset.objects.update(active=False)
        self.assertIsNone(settlement_asset())


class ChoosingTests(SettlementTestCase):

    def test_a_choice_is_honoured(self):
        set_settlement_asset(self.tpln, actor=self.staff)
        self.assertEqual(settlement_asset().unit_name, "TPLN")

    def test_a_choice_records_who_made_it(self):
        set_settlement_asset(self.asr, actor=self.staff)
        self.assertEqual(settlement_choice().chosen_by, self.staff)

    def test_choosing_again_replaces_rather_than_accumulates(self):
        """One platform, one row — the database refuses a second."""
        set_settlement_asset(self.asr, actor=self.staff)
        set_settlement_asset(self.tpln, actor=self.staff)
        self.assertEqual(SettlementAsset.objects.count(), 1)
        self.assertEqual(settlement_asset().unit_name, "TPLN")

    def test_an_inactive_currency_is_refused(self):
        self.asr.active = False
        self.asr.save(update_fields=["active"])
        with self.assertRaises(ValidationError):
            set_settlement_asset(self.asr, actor=self.staff)

    def test_nothing_at_all_is_refused(self):
        with self.assertRaises(ValidationError):
            set_settlement_asset(None, actor=self.staff)

    def test_a_choice_that_later_goes_inactive_falls_through_to_the_default(self):
        """Settling in a retired currency is the failure this order avoids.
        Falling through beats refusing to pay anybody."""
        set_settlement_asset(self.tpln, actor=self.staff)
        self.tpln.active = False
        self.tpln.save(update_fields=["active"])
        self.assertEqual(settlement_asset().unit_name, DEFAULT_UNIT)


class OneServiceTests(SettlementTestCase):
    """Every payment path reads the same answer."""

    def test_the_rate_card_bills_in_the_settlement_asset(self):
        from toto.tariffs.rate_card import gas_asset

        set_settlement_asset(self.asr, actor=self.staff)
        self.assertEqual(gas_asset().unit_name, "ASR")
        set_settlement_asset(self.tpln, actor=self.staff)
        self.assertEqual(gas_asset().unit_name, "TPLN")

    @override_settings(GAS_ASSET="ASR")
    def test_a_stale_gas_setting_no_longer_overrides_the_choice(self):
        """`settings.GAS_ASSET` survives only as a genesis-time seed hint. A
        host that switched settlement asset still granted newcomers the old one
        while `prepaid` read that setting directly."""
        set_settlement_asset(self.tpln, actor=self.staff)
        from toto.tariffs.rate_card import gas_asset

        self.assertEqual(gas_asset().unit_name, "TPLN")


class ViewTests(SettlementTestCase):

    def url(self):
        return reverse("assets:settlement_choose")

    def test_staff_can_change_it_from_the_assets_page(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url(), {"asset": self.asr.pk})
        self.assertRedirects(response, reverse("assets:asset_list"))
        self.assertEqual(settlement_asset().unit_name, "ASR")

    def test_the_current_choice_is_shown(self):
        set_settlement_asset(self.tpln, actor=self.staff)
        self.client.force_login(self.staff)
        body = self.client.get(reverse("assets:asset_list")).content.decode()
        self.assertIn("Settles in", body)
        self.assertIn("TPLN", body)

    def test_an_ordinary_user_may_see_it_but_not_change_it(self):
        self.client.force_login(self.punter)
        body = self.client.get(reverse("assets:asset_list")).content.decode()
        self.assertIn("Settles in", body)
        self.assertNotIn(self.url(), body)

    def test_an_ordinary_user_posting_directly_is_forbidden(self):
        self.client.force_login(self.punter)
        response = self.client.post(self.url(), {"asset": self.asr.pk})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(settlement_choice())

    def test_a_get_is_refused(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    def test_a_nonsense_asset_is_refused_in_words(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url(), {"asset": "99999"}, follow=True)
        text = " ".join(str(m) for m in response.context["messages"]).lower()
        self.assertIn("choose a currency", text)
