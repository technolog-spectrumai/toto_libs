"""The price hint: what a button costs, said before it is clicked.

The interesting cases are all about SILENCE. `toto.quota` ships to every host,
including the two that pin no economy wheel at all, and the tag has to be safe to
write into a shared template without asking who is running it. So the tests that
matter are the ones where nothing is priced and the tag must render nothing —
not "free", not an empty badge, nothing that takes layout.
"""

from django.db import DatabaseError
from django.template import Context, Template
import unittest

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from unittest import mock


def render(source, **context):
    return Template("{% load quota_tags %}" + source).render(Context(context)).strip()


class PriceHintSilenceTests(SimpleTestCase):
    """A hint nobody can act on is noise. It has to disappear cleanly."""

    def test_nothing_at_all_when_the_host_does_not_bill(self):
        """studio and aurelian: the tariffs wheel is not even importable there."""
        with mock.patch("toto.quota.rates.rate_card", return_value={}):
            self.assertEqual(render('{% price_hint "cyprian.pdf" %}'), "")

    def test_nothing_when_the_metric_is_unpriced(self):
        with mock.patch("toto.quota.rates.rate_card", return_value={"other.thing": {}}):
            self.assertEqual(render('{% price_hint "cyprian.pdf" %}'), "")

    def test_nothing_when_the_price_is_zero(self):
        """Priced at nothing is not worth a badge saying so on every button."""
        card = {"cyprian.pdf": {"price_display": 0, "asset": "ASR"}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            self.assertEqual(render('{% price_hint "cyprian.pdf" %}'), "")

    def test_an_unknown_code_is_silent_rather_than_an_error(self):
        with mock.patch("toto.quota.rates.rate_card", return_value={}):
            self.assertEqual(render('{% price_hint "no.such.metric" %}'), "")


class PriceHintQuoteTests(SimpleTestCase):
    def test_it_names_the_price_and_the_asset(self):
        card = {"cyprian.pdf": {"price_display": "0.001", "asset": "ASR"}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            out = render('{% price_hint "cyprian.pdf" %}')
        self.assertIn("0.001", out)
        self.assertIn("ASR", out)

    def test_a_per_request_price_says_no_unit(self):
        """"0.001 ASR / request" is noise — every price is per something."""
        card = {"cyprian.pdf": {"price_display": "0.001", "asset": "ASR"}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            out = render('{% price_hint "cyprian.pdf" %}')
        self.assertNotIn("request", out)

    def test_a_metered_unit_is_named(self):
        card = {"storage.transfer_mb": {"price_display": "0.0002", "asset": "ASR"}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            out = render('{% price_hint "storage.transfer_mb" %}')
        self.assertIn("mb", out.lower())

    def test_the_rate_cards_unit_beats_the_metrics(self):
        """An admin may price per 10 MB on a metric declared in MB.

        The number shown has to be the number charged, so the rate card wins.
        """
        card = {"storage.transfer_mb": {
            "price_display": "0.002", "asset": "ASR",
            "unit_code": "gb", "unit_quantity": 10}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            out = render('{% price_hint "storage.transfer_mb" %}')
        self.assertIn("10 gb", out)

    def test_several_codes_are_all_quoted(self):
        """An upload is charged per call AND per megabyte."""
        card = {
            "storage.request": {"price_display": "0.001", "asset": "ASR"},
            "storage.transfer_mb": {"price_display": "0.0002", "asset": "ASR"},
        }
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            out = render('{% price_hint "storage.request" "storage.transfer_mb" %}')
        self.assertIn("0.001", out)
        self.assertIn("0.0002", out)

    def test_an_unpriced_code_is_dropped_from_a_multi_quote(self):
        card = {"storage.request": {"price_display": "0.001", "asset": "ASR"}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            out = render('{% price_hint "storage.request" "storage.transfer_mb" %}')
        self.assertIn("0.001", out)
        self.assertNotIn("0.0002", out)


class PriceHintResilienceTests(SimpleTestCase):
    def test_an_unmigrated_database_is_silent_rather_than_a_500(self):
        """The failure the hint made dangerous.

        `rates.rate_card` promised in its docstring to degrade to {} and did not
        guard the database — an installed tariffs app with no tables raised. That
        was survivable while two staff screens called it. It is not survivable
        now that a toolbar asks on every render, so the guard is real and this
        test is why it stays.
        """
        with mock.patch("toto.tariffs.rate_card.rate_card",
                        side_effect=DatabaseError("no such table")):
            self.assertEqual(render('{% price_hint "cyprian.pdf" %}'), "")

    def test_the_card_is_read_once_per_request_not_once_per_tag(self):
        """A toolbar carries several hints; they must not be several queries."""
        calls = []

        def _card():
            calls.append(1)
            return {"a.b": {"price_display": "1", "asset": "ASR"},
                    "c.d": {"price_display": "2", "asset": "ASR"}}

        class _Req:
            pass

        with mock.patch("toto.quota.rates.rate_card", side_effect=_card):
            Template(
                "{% load quota_tags %}"
                '{% price_hint "a.b" %}{% price_hint "c.d" %}{% price_hint "a.b" %}'
            ).render(Context({"request": _Req()}))
        self.assertEqual(len(calls), 1, "the rate card was read more than once")

    def test_it_still_works_without_a_request_in_context(self):
        """Rendered from a management command or a test — no request to cache on."""
        card = {"a.b": {"price_display": "1", "asset": "ASR"}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            self.assertIn("1", render('{% price_hint "a.b" %}'))


class PriceHintLiveTests(TestCase):
    """Against a real seeded rate card, not a mock — the wiring, end to end.

    Skipped wherever there is no economy to seed, which is most places: studio
    and aurelian pin no tariffs wheel and the library's own suite settings do not
    install one. zenobia's gate runs this module under ``zenobia.settings``,
    where the ledger is unconditional — that is where these actually execute.
    """

    @classmethod
    def setUpClass(cls):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.tariffs"):
            raise unittest.SkipTest("no economy on this host")
        super().setUpClass()

    def test_a_price_seeded_through_the_real_rate_card_reaches_a_template(self):
        from toto.quota import rates

        # The seeders build the gas asset, the default tariff and the revenue
        # account; a price needs all three to exist.
        call_command("ingress_assets", verbosity=0)
        call_command("ingress_tariffs", verbosity=0)

        # rates.set_price, not tariffs.upsert_price: the quota-side API takes a
        # metric CODE, which is the only thing a caller on this side of the
        # boundary has. upsert_price wants the registry object.
        self.assertTrue(rates.set_price("cyprian.pdf", "0.001"))

        out = render('{% price_hint "cyprian.pdf" %}')
        self.assertIn("0.001", out)

    def test_an_unpriced_metric_stays_silent_against_a_real_card(self):
        call_command("ingress_assets", verbosity=0)
        call_command("ingress_tariffs", verbosity=0)
        self.assertEqual(render('{% price_hint "no.such.metric" %}'), "")
