"""The price hint and the usage tab, past ``tests_price_hint``: a full
discount, the per-request caches for the member's discount and the pool
assets, the degradations that must keep a toolbar rendering, the unit shown,
and — against a real seeded card — the number a discounted member reads beside
the button. Plus the usage tab a member on a mana host is sent to mana by.
"""

import unittest
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings

User = get_user_model()


class _Req:
    user = None


def render(source, request=None, **context):
    if request is not None:
        context["request"] = request
    return Template("{% load quota_tags %}" + source).render(Context(context)).strip()


MANA_CARD = {"forum.message": {"price_display": "0.1", "asset": "BLUE", "asset_id": 7}}


class DiscountRenderingTests(SimpleTestCase):
    def hint(self, card, percent, role, source='{% price_hint "forum.message" %}'):
        with mock.patch("toto.quota.rates.rate_card", return_value=card), \
             mock.patch("toto.quota.rates.member_discount", return_value=(percent, "Students")), \
             mock.patch("toto.quota.templatetags.quota_tags._mana_role", return_value=role):
            return render(source, _Req())

    def test_a_full_discount_shows_nothing_to_pay_and_the_list_price(self):
        out = self.hint(MANA_CARD, 100, "security")
        self.assertRegex(out, r'fa-droplet" aria-hidden="true"></i>−0<')
        self.assertIn("list price 0.1", out)
        self.assertIn('data-discount="100"', out)

    def test_an_odd_percent_is_shown_to_three_significant_digits(self):
        out = self.hint({"forum.message": {"price_display": "0.123456", "asset": "BLUE",
                                           "asset_id": 7}}, 33, "security")
        self.assertIn("−0.0827", out)                       # 0.08271552
        self.assertIn("list price 0.123", out)

    def test_the_pool_is_named_and_its_colour_carried(self):
        out = self.hint(MANA_CARD, 0, "compute")
        self.assertRegex(out, r'fa-droplet" aria-hidden="true"></i>−0\.1<')
        self.assertIn('data-mana-role="compute"', out)
        self.assertIn("compute mana", out)
        self.assertNotIn("BLUE", out)                      # never a ticker for mana

    def test_a_discount_on_one_code_does_not_reach_a_currency_code_beside_it(self):
        card = dict(MANA_CARD)
        card["storage.request"] = {"price_display": "0.2", "asset": "ASR", "asset_id": 9}

        def role(request, code, asset_id):
            return "security" if code == "forum.message" else ""

        with mock.patch("toto.quota.rates.rate_card", return_value=card), \
             mock.patch("toto.quota.rates.member_discount", return_value=(50, "Students")), \
             mock.patch("toto.quota.templatetags.quota_tags._mana_role", side_effect=role):
            out = render('{% price_hint "forum.message" "storage.request" %}', _Req())
        self.assertIn("−0.05", out)
        self.assertIn("0.2&nbsp;ASR", out)
        self.assertEqual(out.count("data-discount="), 1)

    def test_a_label_becomes_the_title(self):
        out = self.hint(MANA_CARD, 0, "security", '{% price_hint "forum.message" label="Send" %}')
        self.assertIn('title="Send"', out)


class PerRequestCacheTests(SimpleTestCase):
    def test_the_discount_is_asked_once_per_request(self):
        card = {"a.b": {"price_display": "1", "asset": "ASR", "asset_id": 1}}
        with mock.patch("toto.quota.rates.rate_card", return_value=card), \
             mock.patch("toto.quota.rates.member_discount", return_value=(0, "")) as asked:
            render('{% price_hint "a.b" %}{% price_hint "a.b" %}{% price_hint "a.b" %}', _Req())
        self.assertEqual(asked.call_count, 1)

    def test_a_failing_discount_lookup_quotes_the_list_price(self):
        with mock.patch("toto.quota.rates.rate_card", return_value=MANA_CARD), \
             mock.patch("toto.quota.rates.member_discount", side_effect=RuntimeError("db")), \
             mock.patch("toto.quota.templatetags.quota_tags._mana_role", return_value="security"):
            out = render('{% price_hint "forum.message" %}', _Req())
        self.assertIn("−0.1", out)
        self.assertNotIn("data-discount", out)

    def test_without_a_request_nobody_is_discounted(self):
        from toto.quota.templatetags import quota_tags

        self.assertEqual(quota_tags._member_discount(None), (0, ""))


class ManaRoleTests(SimpleTestCase):
    def setUp(self):
        from toto.quota.templatetags import quota_tags

        self.role = quota_tags._mana_role

    def test_a_row_without_an_asset_is_not_mana(self):
        self.assertEqual(self.role(_Req(), "forum.message", None), "")

    def test_a_host_without_mana_is_never_mana(self):
        real = apps.is_installed
        with mock.patch.object(apps, "is_installed",
                               side_effect=lambda name: name != "toto.mana" and real(name)):
            self.assertEqual(self.role(_Req(), "forum.message", 7), "")

    @unittest.skipUnless(apps.is_installed("toto.mana"), "no mana on this host")
    def test_a_failing_pool_lookup_is_not_mana_rather_than_a_500(self):
        with mock.patch("toto.mana.services.pools", side_effect=RuntimeError("gone")):
            self.assertEqual(self.role(_Req(), "forum.message", 7), "")

    @unittest.skipUnless(apps.is_installed("toto.mana"), "no mana on this host")
    def test_the_pools_are_read_once_per_request(self):
        pool = mock.Mock(asset_id=7, role="security")
        request = _Req()
        with mock.patch("toto.mana.services.pools", return_value={"security": pool}) as read:
            self.assertEqual(self.role(request, "a", 7), "security")
            self.assertEqual(self.role(request, "b", 8), "")
        self.assertEqual(read.call_count, 1)

    @unittest.skipUnless(apps.is_installed("toto.mana"), "no mana on this host")
    def test_without_a_request_the_pools_are_still_read(self):
        pool = mock.Mock(asset_id=7, role="storage")
        with mock.patch("toto.mana.services.pools", return_value={"storage": pool}):
            self.assertEqual(self.role(None, "a", 7), "storage")


class UnitTests(SimpleTestCase):
    def quote(self, row, code="storage.transfer_mb"):
        with mock.patch("toto.quota.rates.rate_card", return_value={code: row}):
            return render('{% price_hint "' + code + '" %}')

    def test_one_unit_of_a_metered_quantity_names_just_the_unit(self):
        out = self.quote({"price_display": "0.2", "asset": "ASR", "unit_code": "mb",
                          "unit_quantity": 1})
        self.assertIn("/mb", out)
        self.assertNotIn("1 mb", out)

    def test_a_per_request_row_says_no_unit_even_from_the_card(self):
        out = self.quote({"price_display": "0.2", "asset": "ASR", "unit_code": "request"})
        self.assertNotIn("/", out.split("ASR", 1)[1].split("</span>", 1)[0])

    def test_a_code_nobody_registers_is_labelled_by_its_code(self):
        out = self.quote({"price_display": "0.2", "asset": "ASR"}, code="ghost.metric")
        self.assertIn('title="ghost.metric"', out)


class LiveDiscountedHintTests(TestCase):
    """Against the seeded mana card and a real discount — no mocks."""

    @classmethod
    def setUpClass(cls):
        if not (apps.is_installed("toto.mana") and apps.is_installed("toto.subscriptions")):
            raise unittest.SkipTest("no mana or no subscriptions on this host")
        from toto.assets.testing import TEST_ISSUER_KEY

        master = override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY,
                                   ASSETS_MONETARY_MASTER=True)
        master.enable()
        cls.addClassCleanup(master.disable)
        super().setUpClass()

    def setUp(self):
        from toto.mana.tests.fixtures import economy, seed_prices
        from toto.people.models import Person
        from toto.socialhub.models import Community
        from toto.subscriptions.models import CommunityDiscount

        economy()
        seed_prices()
        students = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=students, percent=50)
        self.ada = User.objects.create_user("ada", password="pw")
        Person.objects.create(user=self.ada, display_name="ada").communities.add(students)
        self.bob = User.objects.create_user("bob", password="pw")

    def hint_for(self, user):
        request = _Req()
        request.user = user
        return render('{% price_hint "storage.request" %}', request)

    def test_a_discounted_member_reads_the_price_they_will_pay(self):
        out = self.hint_for(self.ada)
        self.assertIn('data-mana-role="storage"', out)
        self.assertIn("−0.25", out)
        self.assertIn("Students", out)

    def test_a_member_without_a_discount_reads_the_list_price(self):
        out = self.hint_for(self.bob)
        self.assertIn("−0.5", out)
        self.assertNotIn("data-discount", out)


class QuotaTabTests(TestCase):
    def tab(self, user, app_label="vault"):
        request = _Req()
        request.user = user
        return render('{% quota_tab "' + app_label + '" %}', request)

    def test_an_app_that_meters_nothing_has_no_tab(self):
        root = User.objects.create_superuser("root", password="pw")
        self.assertEqual(self.tab(root, "no_such_app"), "")

    @override_settings(ECONOMY_STAFF_ONLY=True)
    def test_a_member_on_a_mana_host_is_sent_to_mana(self):
        if not apps.is_installed("toto.mana"):
            self.skipTest("no mana on this host")
        from django.urls import reverse

        out = self.tab(User.objects.create_user("ada", password="pw"))
        self.assertIn(reverse("mana:index"), out)
        self.assertIn("Mana", out)
        self.assertIn("fa-droplet", out)

    def test_an_operator_is_sent_to_the_apps_usage(self):
        from django.urls import reverse

        with mock.patch("toto.quota.rates.economy_hidden_from", return_value=False):
            out = self.tab(User.objects.create_user("ada", password="pw"))
        self.assertIn(reverse("quota:my_usage_app", args=["vault"]), out)
        self.assertIn("Usage", out)

    def test_a_given_label_replaces_usage(self):
        with mock.patch("toto.quota.rates.economy_hidden_from", return_value=False):
            request = _Req()
            request.user = User.objects.create_user("ada", password="pw")
            out = render('{% quota_tab "vault" "Storage use" %}', request)
        self.assertIn("Storage use", out)

    def test_no_mana_page_to_send_a_member_to_is_no_tab(self):
        from django.urls import NoReverseMatch

        with mock.patch("toto.quota.rates.economy_hidden_from", return_value=True), \
             mock.patch("toto.quota.templatetags.quota_tags.reverse",
                        side_effect=NoReverseMatch("mana")):
            out = self.tab(User.objects.create_user("ada", password="pw"))
        self.assertEqual(out, "")

    def test_rendered_without_a_request_it_links_usage(self):
        from django.urls import reverse

        out = render('{% quota_tab "vault" %}')
        self.assertIn(reverse("quota:my_usage_app", args=["vault"]), out)
