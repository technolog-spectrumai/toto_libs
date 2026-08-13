"""The fee registry, the income board, and the two tab strips.

The strips are rendered directly rather than driven through a view — the
convention `toto/ravioli/tests/test_data_tab.py` set — because what matters is
which entries appear for whom, and a view would only add a login to assert it
through.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase

from toto.quota.fees import DuplicateFeeSource, FeeRegistry, FeeSource

User = get_user_model()


class FeeRegistryTests(SimpleTestCase):
    def _source(self, code="a.b", account="acct"):
        source = FeeSource()
        source.code = code
        source.account_code = account
        source.label = "A source"
        return source

    def test_a_source_registers_and_reads_back(self):
        registry = FeeRegistry()
        registry.register(self._source())

        self.assertEqual(len(registry), 1)
        self.assertEqual(registry.get("a.b").account_code, "acct")
        self.assertEqual(registry.account_codes(), ["acct"])

    def test_two_apps_cannot_claim_one_code(self):
        # Raising rather than overwriting, for the reason the levy registry
        # gives: income silently attributed to the wrong source is worse than
        # income missing.
        registry = FeeRegistry()
        registry.register(self._source())
        with self.assertRaises(DuplicateFeeSource):
            registry.register(self._source(account="other"))

    def test_registering_the_same_object_twice_is_harmless(self):
        registry = FeeRegistry()
        source = self._source()
        registry.register(source)
        registry.register(source)
        self.assertEqual(len(registry), 1)

    def test_a_source_with_no_account_is_refused(self):
        # Nothing could ever be attributed to it, so it would render as a
        # permanently-zero slice and read as "this earns nothing".
        registry = FeeRegistry()
        with self.assertRaises(DuplicateFeeSource):
            registry.register(self._source(account=""))

    def test_sources_come_back_in_a_stable_order(self):
        registry = FeeRegistry()
        registry.register(self._source(code="z.z", account="z"))
        registry.register(self._source(code="a.a", account="a"))
        self.assertEqual([s.code for s in registry], ["a.a", "z.z"])

    def test_the_real_registry_found_the_shipped_sources(self):
        """autodiscover_plugins("fees") ran, and the economy declared itself."""
        from django.apps import apps as django_apps

        from toto.quota.fees import registry

        if not django_apps.is_installed("toto.tariffs"):
            self.skipTest("no billing on this host")
        self.assertIn("tariffs.usage", [s.code for s in registry])


class EconomyTabsTests(TestCase):
    """One strip for the whole economy, and it differs by role, not by gate."""

    def setUp(self):
        self.factory = RequestFactory()
        self.staff = User.objects.create_user(
            username="tabs-staff", is_staff=True)
        self.plain = User.objects.create_user(username="tabs-plain")

    def _render(self, user, active_tab="assets"):
        request = self.factory.get("/")
        request.user = user
        return render_to_string(
            "oya/_economy_tabs.html",
            {"active_tab": active_tab, "user": user, "request": request})

    def test_the_active_tab_is_marked_for_assistive_tech(self):
        # Asked of a chip BOTH roles have: "fees" is staff-only now, so a plain
        # user renders no chip for it and the strip would carry no aria-current.
        html = self._render(self.plain, active_tab="usage")
        self.assertIn('aria-current="page"', html)

    def test_a_user_sees_their_wallet_where_staff_see_the_ledger(self):
        # Same tab, different destination: a user's "Assets" is their own money.
        self.assertIn("Wallet", self._render(self.plain))
        self.assertIn("Assets", self._render(self.staff))

    def test_income_is_not_a_chip_at_all(self):
        """The income board left the navigation.

        It stopped being one URL with two renderings: the user half — "why was I
        charged" — moved onto the metered things where the prices and the usage
        already are. What is left is the platform's own income, which is an
        operator's report rather than somewhere to navigate to, so it keeps its
        URL and loses its chip and its dashboard tile.
        """
        for who in (self.staff, self.plain):
            self.assertNotIn("Income", self._render(who))

    def test_an_anonymous_visitor_renders_without_raising(self):
        # Several /assets/ pages are still reachable anonymously, so the strip
        # renders for AnonymousUser whether or not that is desirable.
        self._render(AnonymousUser())


class UsageTabsTests(TestCase):
    """The sub-nav, after nine chips collapsed into three.

    Records, My usage, Levies, Time dials, Prices, Limits, Rate desk,
    Allowances and Metrics were nine destinations keyed by one string — the
    metric code — so answering a single question about one metered thing meant
    visiting five of them. What is left is three genuinely different objects:
    the things, the KINDS of charge, and the billing trail.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.staff = User.objects.create_user(
            username="usage-staff", is_staff=True)
        self.plain = User.objects.create_user(username="usage-plain")

    def _render(self, user, active_tab="records"):
        request = self.factory.get("/")
        request.user = user
        return render_to_string(
            "oya/_usage_tabs.html",
            {"active_tab": active_tab, "user": user, "request": request})

    def test_the_desks_are_gone_as_destinations(self):
        """Four chips all answered "where do I set what this costs"."""
        for user in (self.plain, self.staff):
            rendered = self._render(user)
            for label in ("Rate desk", "Allowances", "Limits", "Metrics",
                          "Prices", "Time dials"):
                with self.subTest(user=user.username, label=label):
                    self.assertNotIn(label, rendered)

    def test_both_roles_get_the_same_three(self):
        """The role split moved INTO the pages.

        Hiding every chip that set a number is what forced staff and members
        onto different pages for the same question. Each page does its own
        disclosure now — a member sees the numbers, staff see the editors — and
        every view keeps its own gate, so this was only ever cosmetic.
        """
        for user in (self.plain, self.staff):
            rendered = self._render(user)
            for label in ("Metered", "charged", "Records"):
                with self.subTest(user=user.username, label=label):
                    self.assertIn(label, rendered)

    def test_the_active_sub_tab_is_marked(self):
        self.assertIn('aria-current="page"', self._render(self.plain, "metered"))


class IncomeBoardTests(TestCase):
    """Meaning from the registry, money from the ledger."""

    def test_an_unbilled_host_gets_an_empty_board(self):
        from toto.quota import feeboard

        board = feeboard.income_board()
        self.assertFalse(board.rows)
        self.assertFalse(board.billing)

    def test_an_empty_board_draws_no_pie(self):
        # "" rather than an empty chart, so the template omits the canvas
        # instead of rendering a blank one.
        from toto.quota import feeboard

        self.assertEqual(feeboard.income_pie_json(feeboard.FeeBoard()), "")

    def test_the_pie_payload_matches_the_partials_contract(self):
        import json

        from toto.quota import feeboard

        board = feeboard.FeeBoard(asset_unit="ASR")
        board.rows = [
            feeboard.IncomeRow(code="a", label="Usage", description="",
                               icon="", account_code="x", settings_url="",
                               base_units=750, amount=Decimal("7.50"),
                               percent=75.0),
            feeboard.IncomeRow(code="b", label="Levy", description="",
                               icon="", account_code="y", settings_url="",
                               base_units=250, amount=Decimal("2.50"),
                               percent=25.0),
        ]
        payload = json.loads(feeboard.income_pie_json(board))

        self.assertEqual(payload["chart_type"], "pie")
        self.assertEqual(payload["labels"], ["Usage", "Levy"])
        self.assertEqual(payload["datasets"][0]["data"], [750, 250])
        self.assertEqual(len(payload["datasets"][0]["backgroundColor"]), 2)

    def test_a_source_that_earned_nothing_is_kept_but_draws_no_slice(self):
        # It still gets a card — "this is how the platform can earn" is worth
        # saying even at zero — but a 0% slice would be noise.
        from toto.quota import feeboard

        board = feeboard.FeeBoard(asset_unit="ASR")
        board.rows = [feeboard.IncomeRow(
            code="a", label="Idle", description="", icon="",
            account_code="x", settings_url="", base_units=0)]
        self.assertEqual(len(board.rows), 1)
        self.assertEqual(board.earning_rows, [])
        self.assertEqual(feeboard.income_pie_json(board), "")


class IncomeBoardLiveTests(TestCase):
    """Against a real seeded ledger, not a fixture — the wiring, end to end.

    Skipped wherever there is no economy, the shape PriceHintLiveTests uses:
    this module ships to every host and most of them pin no billing wheel.
    """

    @classmethod
    def setUpClass(cls):
        import unittest

        from django.apps import apps as django_apps
        from django.test import override_settings

        if not django_apps.is_installed("toto.tariffs"):
            raise unittest.SkipTest("no economy on this host")

        from toto.assets.testing import TEST_ISSUER_KEY

        issuer_key = override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
        issuer_key.enable()
        cls.addClassCleanup(issuer_key.disable)
        super().setUpClass()

    def setUp(self):
        from django.core.management import call_command

        from toto.assets.testing import ensure_local_issuer

        ensure_local_issuer()
        call_command("ingress_assets", verbosity=0)
        call_command("ingress_tariffs", verbosity=0)
        super().setUp()

    def test_the_board_reports_in_the_contractual_asset(self):
        from toto.quota import feeboard
        from toto.tariffs.rate_card import gas_asset

        board = feeboard.income_board()
        self.assertTrue(board.billing)
        self.assertEqual(board.asset_unit, gas_asset().unit_name)

    def test_every_registered_source_gets_a_row_even_at_zero(self):
        # A source that has earned nothing still answers "this is how the
        # platform can earn", which is half of what the page is for.
        from toto.quota.fees import registry
        from toto.quota import feeboard

        board = feeboard.income_board()
        self.assertEqual({r.code for r in board.rows},
                         {s.code for s in registry})

    def test_income_is_summed_from_the_ledger(self):
        from decimal import Decimal

        from toto.assets.services.assets import transfer_asset
        from toto.quota import feeboard
        from toto.tariffs.rate_card import gas_asset, revenue_account

        asset = gas_asset()
        board_before = feeboard.income_board()
        usage_before = next(r for r in board_before.rows
                            if r.code == "tariffs.usage").base_units

        transfer_asset(asset=asset, sender_account=asset.reserve_account,
                       receiver_account=revenue_account(),
                       amount=Decimal("5"), reference="fees-test-earn")

        board = feeboard.income_board()
        usage = next(r for r in board.rows if r.code == "tariffs.usage")
        self.assertEqual(usage.base_units - usage_before,
                         5 * 10 ** asset.decimals)
        self.assertTrue(usage.has_income)
        self.assertEqual(usage.percent, 100.0)

    def test_a_clean_platform_reports_no_drift(self):
        from toto.quota import feeboard

        self.assertEqual(feeboard.income_board().drift, [])

    def test_one_charging_currency_leaves_no_price_drift(self):
        """Nothing to report, because there is nothing to drift into.

        The rate desk denominates the WHOLE card in one asset in a single act,
        so a mixed card cannot survive an operator's next visit — and with no
        exchange rate anywhere in this ledger, a mixed card could never have
        been added up anyway.
        """
        from toto.assets.testing import make_asset
        from toto.quota import feeboard, rates
        from toto.tariffs.models import TariffItem

        banana = make_asset(unit_name="BANANA", decimals=2)
        rates.set_price("cyprian.pdf", "0.001")
        TariffItem.objects.update(charged_asset=banana)

        self.assertEqual(
            [d for d in feeboard.income_board().drift if d.kind == "price"], [])
