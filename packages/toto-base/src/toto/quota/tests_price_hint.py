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
from django.test import SimpleTestCase, TestCase, override_settings
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


def _tariffs_shipped() -> bool:
    """Whether the economy wheel is on the path at all.

    The test below patches ``toto.tariffs.rate_card`` by dotted name, and
    ``mock.patch`` imports the module to do it — so on a host that does not
    ship toto-economy (irena since 2026-08-22; studio and aurelian before it)
    the test cannot even be set up. That is the situation the hint handles
    by being silent, and it is exercised just above; this one is about a
    tariffs app that IS installed and has no tables yet.
    """
    try:
        import toto.tariffs  # noqa: F401
    except ImportError:
        return False
    return True


class SignificantDigitsTests(SimpleTestCase):
    """Every cost is shown with three significant digits (2026-09-28): the
    forum's "−0.200000000000000000/message" was the stored Decimal, printed."""

    def test_three_significant_digits_trailing_zeros_dropped(self):
        from toto.quota.rates import significant

        for value, shown in (("0.200000000000000000", "0.2"), ("0.123456", "0.123"),
                             ("0.000123456", "0.000123"), ("0.0002", "0.0002"),
                             ("5.000000000", "5"), ("1.23456", "1.23"), ("0.1235", "0.124"),
                             ("-0.20000", "-0.2"), (0, "0"), ("0E-9", "0")):
            with self.subTest(value=value):
                self.assertEqual(significant(value), shown)

    def test_the_whole_number_part_is_never_rounded_away(self):
        from toto.quota.rates import significant

        self.assertEqual(significant("1234.5"), "1235")
        self.assertEqual(significant("120.04"), "120")
        self.assertEqual(significant("99.95"), "100")

    def test_nothing_and_non_numbers_pass_through(self):
        from toto.quota.rates import significant

        self.assertEqual((significant(None), significant("")), ("", ""))
        self.assertEqual(significant("n/a"), "n/a")

    def test_the_filter_and_the_hint_use_it(self):
        self.assertEqual(render("{{ v|sig3 }}", v="0.20000000000"), "0.2")
        card = {"forum.message": {"price_display": "0.200000000000000000", "asset": "RED"}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card):
            out = render('{% price_hint "forum.message" %}')
        self.assertIn("0.2", out)
        self.assertNotIn("0.20", out)


class PriceHintResilienceTests(SimpleTestCase):
    @unittest.skipUnless(_tariffs_shipped(), "toto.tariffs is not shipped on this host")
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


class PriceHintDiscountTests(SimpleTestCase):
    """A member's community discount (2026-09-28): the hint shows what THEY
    pay — the number the charge takes — on mana prices, and only there."""

    CARD = {"forum.message": {"price_display": "0.1", "asset": "CYAN", "asset_id": 7}}

    def render_for(self, percent, role):
        class _Req:
            user = None

        with mock.patch("toto.quota.rates.rate_card", return_value=self.CARD), \
             mock.patch("toto.quota.rates.member_discount", return_value=(percent, "Students")), \
             mock.patch("toto.quota.templatetags.quota_tags._mana_role", return_value=role):
            return Template('{% load quota_tags %}{% price_hint "forum.message" %}').render(
                Context({"request": _Req()}))

    def test_a_discounted_member_sees_the_discounted_mana_price(self):
        out = self.render_for(50, "security")
        self.assertIn("−0.05", out)
        self.assertIn('data-discount="50"', out)
        self.assertIn("Students", out)
        self.assertIn("list price 0.1", out)

    def test_no_discount_no_tag(self):
        out = self.render_for(0, "security")
        self.assertIn("−0.1", out)
        self.assertNotIn("data-discount", out)

    def test_a_currency_price_is_never_discounted(self):
        out = self.render_for(50, "")
        self.assertIn("0.1", out)
        self.assertNotIn("0.05", out)
        self.assertNotIn("data-discount", out)


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

        # Seeding ENGRAVES the gas asset, which is a monetary act, so the host
        # has to hold an issuer key for it. Imported lazily and behind the skip
        # above, so toto-base still has no dependency on the economy wheel.
        from toto.assets.testing import TEST_ISSUER_KEY

        issuer_key = override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
        issuer_key.enable()
        cls.addClassCleanup(issuer_key.disable)
        super().setUpClass()

    def setUp(self):
        from toto.assets.testing import ensure_local_issuer

        ensure_local_issuer()
        super().setUp()

    #: The metric this test prices is ITS OWN, registered for the length of the
    #: test. It named another app's four times — cyprian.pdf (gone 8/2026,
    #: cyprian migration 0003), memo.pdf (2026-09-03), sketch.save
    #: (2026-09-04), antivirus.scan (2026-10-03: "a write-door the platform
    #: cannot drop", until zenobia became storage only and dropped it). A code
    #: no metric registers cannot be priced — set_price returns False and the
    #: test fails for a reason that has nothing to do with prices, which is
    #: how it read every time. What is asserted is the wiring from the rate
    #: desk to a template; that needs A metric, not anybody's in particular,
    #: so no app's leaving can fail it again.
    PROBE = "quota.price_hint_probe"

    def test_a_price_seeded_through_the_real_rate_card_reaches_a_template(self):
        from toto.quota import rates
        from toto.quota.metrics import Metric, registry

        # The seeders build the gas asset, the default tariff and the revenue
        # account; a price needs all three to exist.
        call_command("ingress_assets", verbosity=0)
        call_command("ingress_tariffs", verbosity=0)

        probe = Metric(code=self.PROBE, label="Price hint probe", app_label="quota")
        # patch.dict hands the registry back as it was, whatever happens here.
        with mock.patch.dict(registry._metrics, {probe.code: probe}):
            # rates.set_price, not tariffs.upsert_price: the quota-side API
            # takes a metric CODE, which is the only thing a caller on this
            # side of the boundary has. upsert_price wants the registry object.
            self.assertTrue(rates.set_price(probe.code, "0.001"))
            out = render('{% price_hint "' + probe.code + '" %}')
        self.assertIn("0.001", out)
        self.assertIsNone(registry.get(probe.code))

    def test_an_unpriced_metric_stays_silent_against_a_real_card(self):
        call_command("ingress_assets", verbosity=0)
        call_command("ingress_tariffs", verbosity=0)
        self.assertEqual(render('{% price_hint "no.such.metric" %}'), "")
