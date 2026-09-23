"""The member's four levels: chip, pools, one pool, how it works."""

from decimal import Decimal
from io import StringIO
from itertools import count

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.assets.prepaid import get_or_create_prepaid_account
from toto.assets.services.assets import transfer_asset
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.mana import services
from toto.tax.tests.factories import GB, make_vault_file

MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
User = get_user_model()
_ref = count()


@override_settings(**MASTER)
class PageTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        call_command("ingress_mana", stdout=StringIO(), stderr=StringIO())
        self.ada = User.objects.create_user("ada", password="pw")
        self.client.force_login(self.ada)

    def get(self, name, *args, **query):
        return self.client.get(reverse(name, args=args), query)

    def spend(self, role, amount):
        pool = services.pools()[role]
        account, _ = get_or_create_prepaid_account(self.ada)
        transfer_asset(asset=pool.asset, sender_account=account,
                       receiver_account=pool.asset.reserve_account,
                       amount=Decimal(amount), reference=f"test-spend-{next(_ref)}")


class IndexTests(PageTestCase):
    def test_three_pools_in_order_with_their_levels(self):
        body = self.get("mana:index").content.decode()
        order = [body.index(f'data-role="{r}"') for r in ("security", "compute", "storage")]
        self.assertEqual(order, sorted(order))
        # The page's three, the chip's three, and the mobile dropdown's three.
        self.assertEqual(body.count('role="meter"'), 3 + 3 + 3)
        self.assertIn(">100<", body.replace(" ", "").replace("\n", ""))

    def test_a_low_pool_is_marked(self):
        self.spend("compute", "80")
        body = self.get("mana:index").content.decode()
        self.assertIn('data-band="low"', body)

    def test_anonymous_is_sent_to_log_in(self):
        self.client.logout()
        self.assertEqual(self.get("mana:index").status_code, 302)


class ColourTests(PageTestCase):
    def test_each_pool_has_its_page(self):
        for role in ("security", "compute", "storage"):
            with self.subTest(role=role):
                self.assertEqual(self.get("mana:colour", role).status_code, 200)

    def test_an_unknown_colour_is_404(self):
        self.assertEqual(self.get("mana:colour", "gold").status_code, 404)

    def test_the_detail_starts_folded(self):
        body = self.get("mana:colour", "compute").content.decode()
        self.assertIn("history: false", body)
        self.assertIn("chart: false", body)

    def test_history_shows_signed_movements(self):
        self.spend("compute", "10")
        body = self.get("mana:colour", "compute").content.decode()
        self.assertIn("+100", body)                            # the opening fill
        self.assertIn("-10", body)


class PromptTests(PageTestCase):
    def test_plaintext_is_listed_costliest_first_and_pre_ticked(self):
        small = make_vault_file(self.ada, GB // 10)
        big = make_vault_file(self.ada, GB)
        context = self.get("mana:colour", "security").context
        self.assertEqual([f["pk"] for f in context["plain_files"]], [big.pk, small.pk])
        self.assertIn(big.pk, context["preselected"])

    def test_it_opens_by_itself_when_asked(self):
        make_vault_file(self.ada, GB)
        self.assertFalse(self.get("mana:colour", "security").context["prompt_open"])
        self.assertTrue(self.get("mana:colour", "security", encrypt="1").context["prompt_open"])

    def test_it_opens_by_itself_when_the_pool_runs_low(self):
        make_vault_file(self.ada, GB)
        self.spend("security", "90")
        self.assertTrue(self.get("mana:colour", "security").context["prompt_open"])

    def test_no_plaintext_no_prompt(self):
        body = self.get("mana:colour", "security").content.decode()
        self.assertNotIn("manaPrompt(", body)


class AboutTests(PageTestCase):
    def test_a_member_sees_how_it_works_but_not_the_desk(self):
        body = self.get("mana:about").content.decode()
        self.assertIn("How mana works", body)
        self.assertNotIn('data-testid="operators"', body)

    def test_staff_get_the_way_into_the_economy(self):
        self.ada.is_staff = True
        self.ada.save(update_fields=["is_staff"])
        body = self.get("mana:about").content.decode()
        self.assertIn('data-testid="operators"', body)
        self.assertIn(reverse("assets:asset_list"), body)


class ApiTests(PageTestCase):
    def test_anonymous_gets_json_401_not_a_redirect(self):
        self.client.logout()
        response = self.get("mana:api_balances")
        # On zenobia the login middleware answers first, in JSON, for any
        # /api/ path; elsewhere the view's own 401 does. Either way: no redirect.
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")

    def test_the_payload_the_chip_reads(self):
        self.spend("storage", "30")
        response = self.get("mana:api_balances")
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        self.assertEqual(set(data["colours"]), {"security", "compute", "storage"})
        self.assertEqual(data["colours"]["storage"]["amount"], 70.0)
        self.assertEqual(data["lowest"], "storage")
        self.assertEqual(data["last"]["delta"], -30.0)


class ChipTests(PageTestCase):
    def test_every_page_carries_the_chip(self):
        body = self.client.get(reverse("core:welcome")).content.decode()
        self.assertIn('id="mana-chip"', body)
        self.assertIn('data-testid="mana-chip-mobile"', body)
        self.assertIn('id="brand-link"', body)

    def test_an_anonymous_page_does_not(self):
        self.client.logout()
        body = self.client.get(reverse("core:welcome")).content.decode()
        self.assertNotIn('id="mana-chip"', body)


class HistoryTests(PageTestCase):
    def test_movements_are_classified(self):
        from toto.assets.services.faucets import period_label

        self.spend("storage", "10")
        services.regenerate_hour()
        kinds = [row["kind"] for row in services.history(self.ada, "storage")]
        self.assertEqual(kinds[:1], ["regen"])
        self.assertIn("signup", kinds)
        self.assertIn("transfer", kinds)


class BadgeTests(PageTestCase):
    def render(self, code):
        from django.template import Context, Template
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.ada
        return Template('{% load quota_tags %}{% price_hint "' + code + '" %}').render(
            Context({"request": request}))

    def test_a_pooled_price_reads_as_mana_not_a_ticker(self):
        html = self.render("storage.request")
        self.assertIn('data-mana-role="storage"', html)
        self.assertIn("−0.5", html)
        self.assertNotIn("GREEN", html)

    def test_an_unpriced_metric_still_says_nothing(self):
        self.assertEqual(self.render("storage.egress_mb").strip(), "")


class ProfileTests(PageTestCase):
    def test_the_owner_sees_their_pools_on_their_profile(self):
        from toto.tax.tests.factories import make_person

        person = make_person(self.ada)
        body = self.client.get(reverse("socialhub:profile_details", args=[person.slug])).content.decode()
        self.assertIn('data-testid="mana-profile"', body)

    def test_nobody_else_does(self):
        from toto.tax.tests.factories import make_person

        person = make_person(self.ada)
        self.client.force_login(User.objects.create_user("bob", password="pw"))
        body = self.client.get(reverse("socialhub:profile_details", args=[person.slug])).content.decode()
        self.assertNotIn('data-testid="mana-profile"', body)
