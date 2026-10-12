"""The charge door (``toto.quota.charge``) answering through Discipline: the
doors of the platform are not changed, and spend mana."""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal as D

from django.apps import apps

if not apps.is_installed("toto.discipline") or not apps.is_installed("toto.vault"):
    raise unittest.SkipTest("toto.discipline and toto.vault are not both installed on this host")

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import TestCase, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from toto.core.models import Platform  # noqa: E402
from toto.quota import charge  # noqa: E402
from toto.vault.models import Bucket, FileGateway, VaultDirectory, VaultFile  # noqa: E402

from . import services  # noqa: E402
from .models import ManaAccount, ManaEvent, ManaKind  # noqa: E402
from .tests import FILE  # noqa: E402

User = get_user_model()


@override_settings(DISCIPLINE_FILE=FILE, QUOTA_CHARGER="discipline",
                   MEDIA_ROOT=tempfile.mkdtemp(prefix="discipline-seam-"))
class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.keeper = User.objects.create_user("keeper", password="pw")
        cls.guest = User.objects.create_user("guest", password="pw")
        cls.bucket = Bucket.objects.create(name="Inbox", slug="inbox", owner=cls.keeper)
        cls.inbox = VaultDirectory.objects.create(name="Inbox", bucket=cls.bucket,
                                                  owner=cls.keeper)
        cls.gateway = FileGateway.objects.create(directory=cls.inbox, name="Drop box",
                                                 max_file_size=4096)       # KB

    def have(self, user, amount):
        services.account_for(user)
        ManaAccount.objects.filter(user=user).update(balance=D(str(amount)))

    def upload(self, *files):
        self.client.force_login(self.guest)
        return self.client.post(
            reverse("vault:gateway_upload", kwargs={"dir_pk": self.inbox.pk}),
            {"file": list(files)})


class ChargerTests(Fixture):
    def test_the_host_names_its_charger(self):
        self.assertEqual(charge.charger(), "discipline")
        self.assertTrue(charge.billing_enabled())
        with override_settings(QUOTA_CHARGER="none"):
            self.assertEqual(charge.charger(), "")
            self.assertIsNone(charge.price_for(self.guest, "vault"))
        with override_settings(QUOTA_CHARGER=""):
            # Unstated: the rate card where the host has one, else Discipline.
            expected = "tariffs" if apps.is_installed("toto.tariffs") else "discipline"
            self.assertEqual(charge.charger(), expected)

    def test_the_rate_card_screens_are_the_ledgers_alone(self):
        from toto.quota import rates

        self.assertFalse(rates.pricing_enabled())
        self.assertEqual(rates.rate_card(), {})
        self.assertIsNone(rates.balance_of(self.guest))
        self.assertEqual(rates.wallet_url(), "")

    def test_price_for_answers_for_an_app_with_a_priced_metric(self):
        self.assertEqual(charge.price_for(self.guest, "vault"), charge.ManaCard("vault"))
        self.assertIsNone(charge.price_for(self.guest, "subscriptions"))


class DoorTests(Fixture):
    def setUp(self):
        self.card = charge.price_for(self.guest, "vault")

    def test_check_funds_passes_or_refuses_as_the_door_expects(self):
        charge.check_funds(self.guest, self.card, "storage.request", 1)
        charge.check_funds(self.guest, None, "storage.request", 10_000)     # nothing priced
        with self.assertRaises(charge.InsufficientFunds) as caught:
            charge.check_funds(self.guest, self.card, "storage.request", 21)
        self.assertEqual(caught.exception.status_code, 402)
        self.assertTrue(str(caught.exception))
        with self.assertRaises(charge.InsufficientFunds):
            charge.check_funds_all(self.guest, self.card, [("storage.request", 12, ""),
                                                           ("storage.transfer_mb", 50, "MB")])

    def test_charge_spends_and_hands_back_the_event(self):
        event, ledger = charge.charge(self.guest, self.card, "storage.request", 2,
                                      source_type="vault.VaultFile", source_id="5")
        self.assertIsNone(ledger)
        self.assertEqual((event.kind, event.delta, event.source_type, event.source_id),
                         (ManaKind.SPEND, D("-1"), "vault.VaultFile", "5"))
        self.assertIsNone(charge.charge(self.guest, None, "storage.request", 2))
        self.assertIsNone(charge.charge(self.guest, self.card, "storage.egress_mb", 2))
        # Two files of one copy run are two charges, not one counted twice.
        src = {"source_type": "vault.TransferRun", "source_id": "1"}
        charge.charge(self.guest, self.card, "storage.transfer_mb", 10, unit="MB", **src)
        charge.charge(self.guest, self.card, "storage.transfer_mb", 10, unit="MB", **src)
        self.assertEqual(services.balance(self.guest), D("7"))
        # And a door that names a key is charged once however often it retries.
        for _again in range(2):
            charge.charge(self.guest, self.card, "storage.request", 2, idempotency_key="k1")
        self.assertEqual(services.balance(self.guest), D("6"))

    def test_a_charge_after_the_work_never_fails_it(self):
        self.have(self.guest, "0.2")
        event, _ledger = charge.charge(self.guest, self.card, "storage.request", 1)
        self.assertEqual((event.delta, services.balance(self.guest)), (D("-0.2"), D("0")))
        with self.assertRaises(charge.InsufficientFunds):
            charge.check_and_charge(self.guest, self.card, "storage.request", 1)

    def test_a_refund_gives_back_what_the_row_was_charged(self):
        charge.charge(self.guest, self.card, "storage.transfer_mb", 30, unit="MB",
                      source_type="vault.TransferRun", source_id="9")
        self.assertEqual(services.balance(self.guest), D("7"))
        back = charge.refund_for("vault.TransferRun", 9, "storage.transfer_mb", reason="failed")
        self.assertEqual((back.kind, back.delta), (ManaKind.REFUND, D("3")))
        self.assertIsNone(charge.refund_for("vault.TransferRun", 9, "storage.transfer_mb"))
        self.assertEqual(services.balance(self.guest), D("10"))

    def test_quote_is_plain_data(self):
        quote = charge.quote(self.guest, self.card, [("storage.request", 2, ""),
                                                     ("storage.transfer_mb", 5, "MB")])
        self.assertEqual((quote["total"], quote["balance"], quote["asset"]),
                         (D("1.5"), D("10"), "mana"))
        self.assertEqual([line["amount"] for line in quote["charges"]], [D("1"), D("0.5")])


class UploadTests(Fixture):
    def test_an_upload_spends_the_request_and_the_megabytes(self):
        response = self.upload(SimpleUploadedFile("big.bin", b"x" * (2 * 1024 * 1024)))
        self.assertEqual(response.status_code, 200, response.content[:300])
        vault_file = VaultFile.objects.get(title="big.bin")
        spent = dict(ManaEvent.objects.filter(user=self.guest, kind=ManaKind.SPEND)
                     .values_list("metric_code", "delta"))
        self.assertEqual(spent, {"storage.request": D("-0.5"), "storage.transfer_mb": D("-0.2")})
        self.assertEqual(services.balance(self.guest), D("9.3"))
        self.assertEqual(set(ManaEvent.objects.filter(kind=ManaKind.SPEND)
                             .values_list("source_type", "source_id")),
                         {("vault.VaultFile", str(vault_file.pk))})
        # The keeper of the folder pays nothing for somebody else's upload.
        self.assertEqual(services.balance(self.keeper), D("10"))

    def test_an_upload_without_the_mana_for_it_is_refused_and_stores_nothing(self):
        self.have(self.guest, "0.3")
        response = self.upload(SimpleUploadedFile("note.txt", b"hello"))
        self.assertEqual(response.status_code, 400)
        errors = response.json()["errors"]
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("note.txt: "))
        self.assertFalse(VaultFile.objects.filter(title="note.txt").exists())
        self.assertEqual(services.balance(self.guest), D("0.3"))

    def test_one_file_too_many_does_not_lose_the_others(self):
        self.have(self.guest, "0.7")
        response = self.upload(SimpleUploadedFile("a.txt", b"a"), SimpleUploadedFile("b.txt", b"b"))
        body = response.json()
        self.assertEqual([r["title"] for r in body["results"]], ["a.txt"])
        self.assertEqual(len(body["errors"]), 1)
        self.assertEqual(services.balance(self.guest), D("0.2"))

    def test_with_no_charger_the_upload_is_free(self):
        self.have(self.guest, "0")
        with override_settings(QUOTA_CHARGER="none"):
            response = self.upload(SimpleUploadedFile("free.txt", b"hello"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ManaEvent.objects.filter(kind=ManaKind.SPEND).exists())
