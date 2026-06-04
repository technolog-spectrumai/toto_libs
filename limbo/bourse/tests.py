from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.assets.models import Asset, AssetHolding, LedgerAccount, LedgerTransaction, to_base_units
from toto.assets.queries import get_asset_balance_display
from toto.core.models import Platform

from .forms import ExchangeRequestCreateForm
from .models import AssetExchangeRequest, ExchangeRequestStatus
from .services import accept_exchange_request


def make_asset_direct(unit_name="TST", decimals=2, supply=10000):
    return Asset.objects.create(
        name="Test Asset",
        unit_name=unit_name,
        decimals=decimals,
        total_supply_base_units=supply,
        active=True,
    )


class AppSetupTests(TestCase):
    def test_models_importable(self):
        from toto.bourse import models  # noqa: F401

    def test_services_importable(self):
        from toto.bourse import services  # noqa: F401


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ExchangeRequestTests(TestCase):
    def setUp(self):
        User = get_user_model()
        Platform.objects.create(
            site_name="Test Platform",
            author="Tests",
            publication_year=2026,
            active=True,
        )
        self.requester = User.objects.create_user(username="requester", password="pass")
        self.counterparty = User.objects.create_user(username="counterparty", password="pass")
        self.requester_account = LedgerAccount.objects.create(
            code="requester-wallet",
            name="Requester Wallet",
            account_type="user",
            active=True,
            user=self.requester,
        )
        self.counterparty_account = LedgerAccount.objects.create(
            code="counterparty-wallet",
            name="Counterparty Wallet",
            account_type="user",
            active=True,
            user=self.counterparty,
        )
        self.offer_asset = make_asset_direct(unit_name="OFR", supply=20000)
        self.request_asset = make_asset_direct(unit_name="REQ", supply=20000)
        Asset.objects.filter(pk__in=[self.offer_asset.pk, self.request_asset.pk]).update(is_currency=True)
        self.offer_asset.refresh_from_db()
        self.request_asset.refresh_from_db()
        AssetHolding.objects.create(
            asset=self.offer_asset,
            account=self.requester_account,
            balance_base_units=to_base_units(Decimal("100.00"), self.offer_asset.decimals),
        )
        AssetHolding.objects.create(
            asset=self.request_asset,
            account=self.counterparty_account,
            balance_base_units=to_base_units(Decimal("50.00"), self.request_asset.decimals),
        )

    def test_create_form_records_requested_expected_amount(self):
        form = ExchangeRequestCreateForm(
            data={
                "requester_account": self.requester_account.pk,
                "visibility": "direct",
                "counterparty": self.counterparty.pk,
                "offer_asset": self.offer_asset.pk,
                "offer_amount": "10.00",
                "request_asset": self.request_asset.pk,
                "expected_amount": "20.00",
                "note": "proposal terms",
            },
            user=self.requester,
        )

        self.assertTrue(form.is_valid(), form.errors)
        req = form.save()

        self.assertEqual(req.requester_account, self.requester_account)
        self.assertEqual(req.offer_amount_display, Decimal("10.00"))
        self.assertEqual(req.request_amount_display, Decimal("20.00"))
        self.assertEqual(req.exchange_rate, Decimal("2.000000000000"))
        self.assertEqual(req.commission_amount_base_units, 0)

    def test_create_form_rejects_account_not_owned_by_user(self):
        form = ExchangeRequestCreateForm(
            data={
                "requester_account": self.counterparty_account.pk,
                "visibility": "direct",
                "counterparty": self.counterparty.pk,
                "offer_asset": self.offer_asset.pk,
                "offer_amount": "10.00",
                "request_asset": self.request_asset.pk,
                "expected_amount": "20.00",
            },
            user=self.requester,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("requester_account", form.errors)

    def test_create_form_allows_public_ask_without_counterparty(self):
        form = ExchangeRequestCreateForm(
            data={
                "requester_account": self.requester_account.pk,
                "visibility": "public",
                "counterparty": "",
                "offer_asset": self.offer_asset.pk,
                "offer_amount": "12.00",
                "request_asset": self.request_asset.pk,
                "expected_amount": "10.00",
            },
            user=self.requester,
        )

        self.assertTrue(form.is_valid(), form.errors)
        req = form.save()
        self.assertIsNone(req.counterparty)
        self.assertEqual(req.offer_amount_display, Decimal("12.00"))
        self.assertEqual(req.request_amount_display, Decimal("10.00"))
        self.assertEqual(req.metadata["visibility"], "public")

    def test_direct_proposal_requires_counterparty(self):
        form = ExchangeRequestCreateForm(
            data={
                "requester_account": self.requester_account.pk,
                "visibility": "direct",
                "counterparty": "",
                "offer_asset": self.offer_asset.pk,
                "offer_amount": "12.00",
                "request_asset": self.request_asset.pk,
                "expected_amount": "10.00",
            },
            user=self.requester,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("counterparty", form.errors)

    def test_create_view_renders_trader_ticket(self):
        self.client.force_login(self.requester)

        response = self.client.get(reverse("bourse:exchange_request_create"))

        self.assertContains(response, "Open a bourse proposal")
        self.assertContains(response, "Ticket")

    def test_public_ask_is_visible_and_can_be_accepted_by_other_user(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=None,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("12.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("10.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("0.833333333333"),
            commission_percent=Decimal("0"),
        )

        self.client.force_login(self.counterparty)
        response = self.client.get(reverse("bourse:exchange_center"))
        self.assertContains(response, "I will trade 12.00 OFR for 10.00 REQ")

        accepted = accept_exchange_request(
            exchange_request=req,
            counterparty_account=self.counterparty_account,
        )
        self.assertEqual(accepted.status, ExchangeRequestStatus.ACCEPTED)
        self.assertEqual(accepted.counterparty, self.counterparty)
        self.assertEqual(get_asset_balance_display(self.offer_asset, self.counterparty_account), Decimal("12.00"))
        self.assertEqual(get_asset_balance_display(self.request_asset, self.requester_account), Decimal("10.00"))

    def test_public_ask_can_be_reviewed_and_denied_by_other_user(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=None,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("12.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("10.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("0.833333333333"),
            commission_percent=Decimal("0"),
        )
        self.client.force_login(self.counterparty)

        response = self.client.get(reverse("bourse:exchange_request_accept", args=[req.pk]))
        self.assertContains(response, "Accept and settle")
        self.assertContains(response, "Deny")

        response = self.client.post(reverse("bourse:exchange_request_reject", args=[req.pk]))
        self.assertRedirects(response, reverse("bourse:exchange_center"))
        req.refresh_from_db()
        self.assertEqual(req.status, ExchangeRequestStatus.REJECTED)

    def test_accept_exchange_request_settles_assets(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=self.counterparty,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("10.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("20.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("2.000000000000"),
            commission_percent=Decimal("0"),
        )

        accepted = accept_exchange_request(
            exchange_request=req,
            counterparty_account=self.counterparty_account,
        )

        self.assertEqual(accepted.status, ExchangeRequestStatus.ACCEPTED)
        self.assertEqual(get_asset_balance_display(self.offer_asset, self.requester_account), Decimal("90.00"))
        self.assertEqual(get_asset_balance_display(self.offer_asset, self.counterparty_account), Decimal("10.00"))
        self.assertEqual(get_asset_balance_display(self.request_asset, self.requester_account), Decimal("20.00"))
        self.assertEqual(get_asset_balance_display(self.request_asset, self.counterparty_account), Decimal("30.00"))

    def test_accept_view_requires_verified_wallet_pin_session(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=self.counterparty,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("10.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("20.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("2.000000000000"),
            commission_percent=Decimal("0"),
        )
        self.client.force_login(self.counterparty)

        with patch("toto.bazaar.wallet_pin.has_wallet_pin", return_value=True):
            response = self.client.post(reverse("bourse:exchange_request_accept", args=[req.pk]), {
                "counterparty_account": self.counterparty_account.pk,
            })

        self.assertContains(response, "Wallet PIN required. Please verify your PIN.")
        req.refresh_from_db()
        self.assertEqual(req.status, ExchangeRequestStatus.PENDING)
        self.assertFalse(LedgerTransaction.objects.filter(reference=f"exchange-{req.pk}-offer").exists())

    def test_accept_view_settles_after_verified_wallet_pin_session(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=self.counterparty,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("10.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("20.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("2.000000000000"),
            commission_percent=Decimal("0"),
        )
        self.client.force_login(self.counterparty)
        from toto.bazaar.wallet_pin import mark_session_verified
        session = self.client.session
        mark_session_verified(session)
        session.save()

        with patch("toto.bazaar.wallet_pin.has_wallet_pin", return_value=True):
            response = self.client.post(reverse("bourse:exchange_request_accept", args=[req.pk]), {
                "counterparty_account": self.counterparty_account.pk,
            })

        self.assertRedirects(response, reverse("bourse:exchange_center"))
        req.refresh_from_db()
        self.assertEqual(req.status, ExchangeRequestStatus.ACCEPTED)

    def test_requester_can_cancel_pending_proposal(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=self.counterparty,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("10.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("20.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("2.000000000000"),
            commission_percent=Decimal("0"),
        )
        self.client.force_login(self.requester)

        response = self.client.get(reverse("bourse:exchange_center"))
        self.assertContains(response, "Cancel proposal")

        response = self.client.post(reverse("bourse:exchange_request_cancel", args=[req.pk]))
        self.assertRedirects(response, reverse("bourse:exchange_center"))
        req.refresh_from_db()
        self.assertEqual(req.status, ExchangeRequestStatus.CANCELLED)

    def test_fulfilled_proposal_cannot_be_cancelled(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=self.counterparty,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("10.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("20.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("2.000000000000"),
            commission_percent=Decimal("0"),
        )
        accept_exchange_request(
            exchange_request=req,
            counterparty_account=self.counterparty_account,
        )
        self.client.force_login(self.requester)

        response = self.client.post(reverse("bourse:exchange_request_cancel", args=[req.pk]))

        self.assertEqual(response.status_code, 404)
        req.refresh_from_db()
        self.assertEqual(req.status, ExchangeRequestStatus.ACCEPTED)

    def test_exchange_center_shows_recent_trade_hashes(self):
        req = AssetExchangeRequest.objects.create(
            requester=self.requester,
            counterparty=self.counterparty,
            requester_account=self.requester_account,
            offer_asset=self.offer_asset,
            request_asset=self.request_asset,
            offer_amount_base_units=to_base_units(Decimal("10.00"), self.offer_asset.decimals),
            request_amount_base_units=to_base_units(Decimal("20.00"), self.request_asset.decimals),
            commission_amount_base_units=0,
            exchange_rate=Decimal("2.000000000000"),
            commission_percent=Decimal("0"),
        )
        accept_exchange_request(
            exchange_request=req,
            counterparty_account=self.counterparty_account,
        )
        offer_tx = LedgerTransaction.objects.get(reference=f"exchange-{req.pk}-offer")
        request_tx = LedgerTransaction.objects.get(reference=f"exchange-{req.pk}-request")

        self.client.force_login(self.requester)
        response = self.client.get(reverse("bourse:exchange_center"))

        self.assertContains(response, offer_tx.reference)
        self.assertContains(response, request_tx.reference)
        self.assertContains(response, offer_tx.hash_record.hash)
        self.assertContains(response, request_tx.hash_record.hash)
