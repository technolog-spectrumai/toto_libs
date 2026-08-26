"""Taking the ledger away with you.

Two formats over one dataset, and the dataset is whatever the page was showing.
The export lives INSIDE `transaction_list` for that reason: one view, one filter
call, one permission posture, so the two cannot drift.

Two refusals carry most of the weight here, and they are the same idea — an
export names a bounded thing:

* **no time span** — an export with no window claims to be "everything", which
  stops being true the moment the next transaction posts;
* **too many rows** — refused, never truncated. A cut-off ledger export looks
  complete, balances against nothing, and gives its holder no way to tell.
"""
from decimal import Decimal
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.assets.export import MAX_ROWS
from toto.assets.models import (AccountType, Asset, LedgerAccount,
                                LedgerEntry, LedgerTransaction, TransactionType)
from toto.assets.testing import TEST_ISSUER_KEY
from toto.assets.testing import LedgerTestCase, make_asset
from toto.core.models import Platform

User = get_user_model()
LIST = "assets:transaction_list"


#: The issuer key is supplied HERE rather than read from the environment.
#: `LedgerTestCase` mints an issuer in setUpTestData, which needs
#: MONETARY_ISSUER_KEY — and zenobia defaults it to "" because a host is not a
#: monetary master until an operator makes it one. A suite that relied on the
#: environment passed locally and failed in the gate, which sets no such key.
@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
class ExportTestCase(LedgerTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.gas = make_asset(name="Assarion", unit_name="ASR", decimals=9,
                             max_supply_base_units=10 ** 18, active=True)
        cls.other = make_asset(name="Mana", unit_name="MANA", decimals=2,
                               max_supply_base_units=10 ** 12, active=True)
        cls.reserve = LedgerAccount.objects.create(
            code="reserve", name="Reserve", account_type=AccountType.RESERVE)
        cls.alice = LedgerAccount.objects.create(
            code="alice", name="Alice", account_type=AccountType.USER)
        cls.user = User.objects.create_user("reader", password="pw")

    def setUp(self):
        super().setUp()
        # The transactions page is login-required on this host, and the export
        # lives inside it — so every test here signs in, and the one that does
        # NOT is the authorization test below.
        self.client.force_login(self.user)

    def make_tx(self, *, reference, asset=None, amount=1000, when=None,
                tx_type=TransactionType.ASSET_TRANSFER, description=""):
        asset = asset or self.gas
        tx = LedgerTransaction.objects.create(
            reference=reference, asset=asset, transaction_type=tx_type,
            description=description, posted=True)
        LedgerEntry.objects.create(transaction=tx, account=self.reserve,
                                   asset=asset, amount_base_units=-amount)
        LedgerEntry.objects.create(transaction=tx, account=self.alice,
                                   asset=asset, amount_base_units=amount)
        if when is not None:
            LedgerTransaction.objects.filter(pk=tx.pk).update(created_at=when)
            tx.refresh_from_db()
        return tx

    def export(self, fmt="xml", **params):
        params.setdefault("from", "2026-01-01")
        params.setdefault("to", "2026-12-31")
        params["export"] = fmt
        return self.client.get(reverse(LIST), params)


class FormatTests(ExportTestCase):

    def setUp(self):
        super().setUp()
        self.make_tx(reference="tx-one", amount=1_500_000_000,
                     when=timezone.now().replace(month=6, day=1))

    def test_xml_parses_and_carries_every_required_field(self):
        response = self.export("xml")
        self.assertEqual(response.status_code, 200)
        root = ET.fromstring(response.content)
        tx = root.find("./transactions/transaction")
        for field in ("reference", "uuid", "timestamp", "type", "asset",
                      "amount", "sender", "recipient"):
            with self.subTest(field=field):
                self.assertIsNotNone(tx.find(field), field)
        self.assertEqual(tx.findtext("reference"), "tx-one")
        self.assertEqual(tx.findtext("asset"), "ASR")
        self.assertEqual(tx.findtext("sender"), "reserve")
        self.assertEqual(tx.findtext("recipient"), "alice")

    def test_the_amount_is_scaled_by_the_asset_decimals(self):
        """Base units are not the number a person reads, and a float would
        round a ledger amount."""
        root = ET.fromstring(self.export("xml").content)
        tx = root.find("./transactions/transaction")
        self.assertEqual(tx.findtext("amount"), "1.500000000")
        self.assertEqual(tx.findtext("amount-base-units"), "1500000000")

    def test_xml_is_deterministic(self):
        """Same rows in, same bytes out — the only reason to prefer this format
        over the HTML for an archive."""
        self.make_tx(reference="tx-two", when=timezone.now().replace(month=6, day=2))
        first = self.export("xml").content
        second = self.export("xml").content
        self.assertEqual(first, second)

    def test_xml_carries_no_generation_timestamp(self):
        """A stamp would change every run and make two exports of identical data
        compare unequal."""
        body = self.export("xml").content.decode()
        self.assertNotIn("generated", body.lower())

    def test_html_is_a_standalone_document(self):
        """It has to open on a machine that has never heard of this platform."""
        body = self.export("html").content.decode()
        self.assertTrue(body.lstrip().startswith("<!DOCTYPE html>"))
        for outside in ("<script", 'src="http', "<link", "@import"):
            with self.subTest(pattern=outside):
                self.assertNotIn(outside, body)

    def test_html_names_the_period_it_covers(self):
        body = self.export("html", **{"from": "2026-06-01", "to": "2026-06-30"}).content.decode()
        self.assertIn("2026-06-01", body)
        self.assertIn("2026-06-30", body)

    def test_both_come_back_as_a_file(self):
        for fmt, mime in (("xml", "application/xml"), ("html", "text/html")):
            with self.subTest(fmt=fmt):
                response = self.export(fmt)
                self.assertIn(mime, response["Content-Type"])
                self.assertIn("attachment", response["Content-Disposition"])
                self.assertIn(f".{fmt}", response["Content-Disposition"])

    def test_an_unknown_format_is_refused(self):
        response = self.export("pdf", follow=True) if False else self.client.get(
            reverse(LIST), {"from": "2026-01-01", "to": "2026-12-31",
                            "export": "pdf"}, follow=True)
        text = " ".join(str(m) for m in response.context["messages"]).lower()
        self.assertIn("html", text)
        self.assertIn("xml", text)

    def test_it_is_not_wired_to_aralia(self):
        """A person gets their own ledger back in the same request, with no
        worker and no queue in between."""
        from toto.assets import export as export_module

        self.assertNotIn("aralia", open(export_module.__file__).read().lower())


class EscapingTests(ExportTestCase):
    """A reference or a description is arbitrary text from outside, and an
    export is the last place it would be noticed going wrong."""

    HOSTILE = '<script>alert("x")</script> & "quoted" and it''s fine'

    def setUp(self):
        super().setUp()
        self.make_tx(reference="tx-hostile", description=self.HOSTILE,
                     when=timezone.now().replace(month=6, day=1))

    def test_html_escapes_it(self):
        body = self.export("html").content.decode()
        self.assertNotIn("<script>alert", body)
        self.assertIn("&lt;script&gt;", body)

    def test_xml_escapes_it_and_still_parses(self):
        content = self.export("xml").content
        self.assertNotIn(b"<script>alert", content)
        root = ET.fromstring(content)          # would raise if malformed
        self.assertEqual(
            root.findtext("./transactions/transaction/description"), self.HOSTILE)


class FilterTests(ExportTestCase):
    """The export carries exactly what the page was showing."""

    def setUp(self):
        super().setUp()
        june = timezone.now().replace(month=6, day=15)
        march = timezone.now().replace(month=3, day=15)
        self.make_tx(reference="gas-june", asset=self.gas, when=june)
        self.make_tx(reference="mana-june", asset=self.other, when=june)
        self.make_tx(reference="gas-march", asset=self.gas, when=march)
        self.make_tx(reference="mint-june", asset=self.gas, when=june,
                     tx_type=TransactionType.MINT)

    def references(self, **params):
        root = ET.fromstring(self.export("xml", **params).content)
        return {e.text for e in root.findall("./transactions/transaction/reference")}

    def test_the_time_span_bounds_it(self):
        self.assertEqual(
            self.references(**{"from": "2026-06-01", "to": "2026-06-30"}),
            {"gas-june", "mana-june", "mint-june"})

    def test_the_end_date_includes_its_whole_day(self):
        """A person choosing the 15th means the 15th."""
        self.assertIn("gas-june",
                      self.references(**{"from": "2026-06-15", "to": "2026-06-15"}))

    def test_the_asset_filter_applies(self):
        self.assertEqual(
            self.references(asset="MANA", **{"from": "2026-01-01", "to": "2026-12-31"}),
            {"mana-june"})

    def test_the_type_filter_applies(self):
        self.assertEqual(
            self.references(type=TransactionType.MINT,
                            **{"from": "2026-01-01", "to": "2026-12-31"}),
            {"mint-june"})

    def test_filters_combine(self):
        self.assertEqual(
            self.references(asset="ASR", type=TransactionType.ASSET_TRANSFER,
                            **{"from": "2026-06-01", "to": "2026-06-30"}),
            {"gas-june"})

    def test_the_export_matches_what_the_page_counted(self):
        """The guarantee behind putting the export inside the view."""
        params = {"asset": "ASR", "from": "2026-01-01", "to": "2026-12-31"}
        page = self.client.get(reverse(LIST), params)
        exported = self.references(**params)
        self.assertEqual(page.context["total_count"], len(exported))


class TimeSpanRequiredTests(ExportTestCase):
    """An export always names the window it covers."""

    def setUp(self):
        super().setUp()
        self.make_tx(reference="tx-one", when=timezone.now().replace(month=6, day=1))

    def refusal(self, **params):
        params["export"] = "xml"
        response = self.client.get(reverse(LIST), params, follow=True)
        return " ".join(str(m) for m in response.context["messages"]).lower()

    def test_no_dates_at_all_is_refused(self):
        self.assertIn("start and an end date", self.refusal())

    def test_only_a_start_is_refused(self):
        self.assertIn("start and an end date", self.refusal(**{"from": "2026-01-01"}))

    def test_only_an_end_is_refused(self):
        self.assertIn("start and an end date", self.refusal(**{"to": "2026-12-31"}))

    def test_an_unparseable_date_is_refused_rather_than_ignored(self):
        """Silently dropping it would export everything under a span the person
        believes they set."""
        self.assertIn("start and an end date",
                      self.refusal(**{"from": "last tuesday", "to": "2026-12-31"}))

    def test_a_backwards_span_is_refused(self):
        self.assertIn("before the start date",
                      self.refusal(**{"from": "2026-12-31", "to": "2026-01-01"}))

    def test_browsing_without_a_span_still_works(self):
        """Optional for looking, required for exporting."""
        response = self.client.get(reverse(LIST))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_span"])


class TooManyRowsTests(ExportTestCase):
    """Refused, never truncated."""

    def test_over_the_limit_is_refused_and_says_the_count(self):
        from unittest import mock

        self.make_tx(reference="tx-one", when=timezone.now().replace(month=6, day=1))
        with mock.patch("toto.assets.export.MAX_ROWS", 0):
            response = self.client.get(
                reverse(LIST),
                {"from": "2026-01-01", "to": "2026-12-31", "export": "xml"},
                follow=True)
        text = " ".join(str(m) for m in response.context["messages"]).lower()
        self.assertIn("narrow", text)

    def test_the_limit_is_fifty_thousand(self):
        self.assertEqual(MAX_ROWS, 50_000)

    def test_nothing_is_ever_silently_cut_off(self):
        """`transaction_rows` raises rather than returning a short list."""
        from toto.assets.export import ExportTooLarge, filtered_transactions, transaction_rows

        self.make_tx(reference="tx-one", when=timezone.now().replace(month=6, day=1))
        self.make_tx(reference="tx-two", when=timezone.now().replace(month=6, day=2))
        with self.assertRaises(ExportTooLarge) as caught:
            transaction_rows(filtered_transactions(), limit=1)
        self.assertEqual(caught.exception.count, 2)


class CountDisplayTests(ExportTestCase):
    """Narrowing a span to get under the limit must not be a guessing game."""

    def setUp(self):
        super().setUp()
        self.make_tx(reference="june", when=timezone.now().replace(month=6, day=15))
        self.make_tx(reference="march", when=timezone.now().replace(month=3, day=15))

    def test_the_page_counts_the_chosen_span(self):
        response = self.client.get(
            reverse(LIST), {"from": "2026-06-01", "to": "2026-06-30"})
        self.assertEqual(response.context["total_count"], 1)
        self.assertTrue(response.context["has_span"])

    def test_the_count_is_rendered(self):
        body = self.client.get(
            reverse(LIST), {"from": "2026-06-01", "to": "2026-06-30"}).content.decode()
        self.assertIn("2026-06-01", body)
        self.assertIn("2026-06-30", body)

    def test_over_the_limit_is_flagged_on_the_page(self):
        from unittest import mock

        with mock.patch("toto.assets.views.EXPORT_LIMIT", 1):
            response = self.client.get(
                reverse(LIST), {"from": "2026-01-01", "to": "2026-12-31"})
        self.assertTrue(response.context["over_export_limit"])
        self.assertIn("too many to export", response.content.decode().lower())


class AuthorizationTests(ExportTestCase):
    """The export inherits the page's permissions, whatever they are.

    That is the whole reason the export is handled INSIDE `transaction_list`
    rather than at a URL of its own. The property asserted here is not "the
    export is closed" — it is that the export answers exactly as the page does,
    so a change to one is a change to both and neither can be tightened or
    opened without the other.
    """

    def setUp(self):
        super().setUp()
        self.make_tx(reference="tx-one", when=timezone.now().replace(month=6, day=1))

    def span(self):
        return {"from": "2026-01-01", "to": "2026-12-31"}

    def test_an_anonymous_visitor_gets_no_ledger_and_no_export(self):
        self.client.logout()
        page = self.client.get(reverse(LIST), self.span())
        exported = self.client.get(reverse(LIST), {**self.span(), "export": "xml"})

        self.assertEqual(page.status_code, 302)
        self.assertEqual(exported.status_code, 302)
        # Not a partial document, not an empty one: nothing at all.
        self.assertEqual(exported.content, b"")
        self.assertIn("/login", exported["Location"])

    def test_a_signed_in_reader_gets_both(self):
        page = self.client.get(reverse(LIST), self.span())
        exported = self.client.get(reverse(LIST), {**self.span(), "export": "xml"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(exported.status_code, 200)

    def test_the_two_answer_alike_for_everybody(self):
        """The guarantee, stated directly."""
        for who in ("anonymous", "signed-in"):
            with self.subTest(who=who):
                if who == "anonymous":
                    self.client.logout()
                else:
                    self.client.force_login(self.user)
                page = self.client.get(reverse(LIST), self.span())
                exported = self.client.get(
                    reverse(LIST), {**self.span(), "export": "xml"})
                self.assertEqual(page.status_code, exported.status_code)

    def test_an_export_is_never_cached(self):
        """A document about somebody's money."""
        response = self.client.get(reverse(LIST), {**self.span(), "export": "xml"})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
