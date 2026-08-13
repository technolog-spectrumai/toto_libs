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
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)

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
                          "Prices", "Time dials", "How it's charged"):
                with self.subTest(user=user.username, label=label):
                    self.assertNotIn(label, rendered)

    def test_both_roles_get_the_same_destinations(self):
        """The role split moved INTO the pages.

        Hiding every chip that set a number is what forced staff and members
        onto different pages for the same question. Each page does its own
        disclosure now — a member sees the numbers, staff see the editors — and
        every view keeps its own gate, so this was only ever cosmetic.
        """
        for user in (self.plain, self.staff):
            rendered = self._render(user)
            for label in ("Metered", "Records"):
                with self.subTest(user=user.username, label=label):
                    self.assertIn(label, rendered)

    def test_tribute_is_the_one_staff_only_chip(self):
        """Not a leftover of the old split: the tribute DESK is staff-only, and
        a chip that 403s is a broken chip."""
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.portfolio"):
            self.skipTest("no tribute on this host")
        self.assertIn("Tribute", self._render(self.staff))
        self.assertNotIn("Tribute", self._render(self.plain))

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


class DemoIncomeIngressTests(TestCase):
    """`ingress_tariffs --full` leaves the fees board with something to show.

    The board reads posted ledger entries, so a seed that only wrote UsageRecord
    rows — or only moved money without recording the usage — would produce a
    page whose chart and whose tables disagree. These assert both halves.
    """

    @classmethod
    def setUpClass(cls):
        import unittest

        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.tariffs"):
            raise unittest.SkipTest("no economy on this host")

        # Seeding ENGRAVES the gas asset, a monetary act, so the host has to
        # hold an issuer key — the gate does not export one for this stanza.
        from toto.assets.testing import TEST_ISSUER_KEY

        issuer_key = override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
        issuer_key.enable()
        cls.addClassCleanup(issuer_key.disable)
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        from toto.assets.testing import ensure_local_issuer

        ensure_local_issuer()
        call_command("ingress_assets", full=True, verbosity=0)
        call_command("ingress_tariffs", full=True, verbosity=0)

    def test_the_board_has_income(self):
        from toto.quota import feeboard

        board = feeboard.income_board()
        usage = next(r for r in board.rows if r.code == "tariffs.usage")
        self.assertTrue(usage.has_income, "the fees pie would render empty")

    def test_the_pie_is_drawable(self):
        from toto.quota import feeboard

        self.assertNotEqual(feeboard.income_pie_json(feeboard.income_board()), "")

    def test_the_income_is_backed_by_posted_usage_records(self):
        """Not a transfer dressed up as revenue: real rated, posted charges."""
        from toto.tariffs.models import UsageRecord, UsageStatus

        records = UsageRecord.objects.filter(source_type="ingress.demo")
        self.assertTrue(records.exists())
        for record in records:
            with self.subTest(metric=record.metric_code):
                self.assertEqual(record.status, UsageStatus.POSTED)
                self.assertIsNotNone(record.ledger_transaction_id)

    def test_the_demo_prices_never_reach_a_real_user(self):
        """The load-bearing one.

        `get_tariff_for_user` falls back to "any ACTIVE tariff with a matching
        item", so an active demo tariff would start charging everybody for
        storage — exactly what this host's TARIFF_SEED_PRICES=False prevents.
        """
        from toto.tariffs.models import Tariff, TariffStatus

        demo = Tariff.objects.get(code="demo-usage")
        self.assertEqual(demo.status, TariffStatus.DRAFT)

    def test_re_running_does_not_inflate_the_treasury(self):
        from django.core.management import call_command

        from toto.quota import feeboard

        before = feeboard.income_board().earning_rows[0].base_units
        call_command("ingress_tariffs", full=True, verbosity=0)
        after = feeboard.income_board().earning_rows[0].base_units
        self.assertEqual(before, after)


class HelpPartialTests(TestCase):
    """The question mark that replaced the deleted tab.

    Rendered directly, the way UsageTabsTests renders the tab strip: what
    matters is what the markup contains, and a view would only add a login.
    """

    def _render(self, **params):
        params.setdefault("title", "How you are charged")
        params.setdefault("body", "The cap is the real limit.")
        return render_to_string("oya/partials/_help.html", params)

    def test_it_renders_a_trigger_and_a_panel(self):
        html = self._render()
        self.assertIn("fa-circle-question", html)
        self.assertIn("The cap is the real limit.", html)
        self.assertIn("How you are charged", html)

    def test_the_panel_starts_hidden(self):
        """x-cloak AND x-show: without the cloak it renders open on first paint."""
        html = self._render()
        self.assertIn("x-cloak", html)
        self.assertIn('x-show="helpOpen"', html)

    def test_it_closes_three_ways(self):
        """Escape, the backdrop and a button — a modal with one exit is a trap."""
        html = self._render()
        self.assertIn("@keydown.escape.window", html)
        self.assertIn('@click="helpOpen = false"', html)
        self.assertIn("Got it", html)

    def test_the_trigger_says_what_it_opens(self):
        self.assertIn('aria-label="Help: How you are charged"', self._render())

    def test_the_second_paragraph_is_optional(self):
        without = self._render()
        self.assertNotIn("Only sometimes true.", without)
        with_extra = self._render(body_extra="Only sometimes true.")
        self.assertIn("Only sometimes true.", with_extra)

    def test_the_footer_link_is_optional(self):
        self.assertNotIn("<a href", self._render())
        linked = self._render(more_url="/quota/taxes/", more_label="Every kind")
        self.assertIn('href="/quota/taxes/"', linked)
        self.assertIn("Every kind", linked)

    def test_it_carries_both_themes(self):
        """A panel defined in one theme only is invisible in the other."""
        html = self._render()
        self.assertIn("bg-bubble-bg-dark", html)
        self.assertIn("bg-bubble-bg-light", html)


class MeteredHelpTests(TestCase):
    """The affordance is on the page, and it did NOT come back as a tab."""

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.user = User.objects.create_user(username="metered-reader")

    def _get(self):
        from django.urls import reverse

        self.client.force_login(self.user)
        return self.client.get(reverse("quota:index"))

    def test_the_metered_page_offers_the_explanation(self):
        body = self._get().content.decode()
        self.assertEqual(self._get().status_code, 200)
        self.assertIn("fa-circle-question", body)
        self.assertIn("How you are charged", body)

    def test_the_copy_is_true_where_nothing_is_priced(self):
        """This platform's actual state: a real, empty rate card.

        Copy that asserted "you are charged" would be false on every host in
        the tree today, which is why the sentence is about the CAP.
        """
        body = self._get().content.decode()
        self.assertIn("the cap is the real limit", body.lower())
        self.assertIn("An action with no price is free", body)

    def test_the_levy_sentences_only_appear_where_a_levy_engine_exists(self):
        """placidia and aurelian install no toto.tax; a levy is not a thing
        there and the modal must not describe one."""
        from unittest.mock import patch

        from toto.quota import levies

        with patch.object(levies, "levy_enabled", return_value=False):
            body = self._get().content.decode()
        self.assertNotIn("charged nightly for something you are still holding", body)

        with patch.object(levies, "levy_enabled", return_value=True):
            body = self._get().content.decode()
        self.assertIn("charged nightly for something you are still holding", body)

    def test_the_deleted_chip_did_not_come_back(self):
        """The modal is the sanctioned replacement precisely because it is not
        navigation. Re-adding the chip would undo a deliberate simplification."""
        body = self._get().content.decode()
        self.assertNotIn("How it's charged", body)

    def test_the_page_declares_the_cloak_rule(self):
        """Without it the panel — and the edit modal — flash open on load."""
        self.assertIn("[x-cloak]", self._get().content.decode())
