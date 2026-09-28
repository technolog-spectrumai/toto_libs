"""The economy gate, request by request, on zenobia's own urlconf.

A member meets 403 at every economy desk and 200 at mana. Since 2026-09-28 the
desks belong to an OPERATOR — a superuser on the Superuser plan
(`quota.rates.economy_operator`) — and plain staff meet 403 like a member.
Also every member-facing surface that used to show the economy: the
dashboard, the strip, the gas pump, the usage tab.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from toto.mana.tests.fixtures import MASTER, economy

User = get_user_model()

DESKS = ("assets:asset_list", "assets:account_list", "assets:wallet",
         "assets:faucet_list", "tariffs:tariff_list", "quota:my_usage",
         "quota:index", "bourse:exchange_center", "mint:index")


@override_settings(**MASTER)
class GateTestCase(TestCase):
    def setUp(self):
        economy()
        self.member = User.objects.create_user("ada", password="pw")
        self.staff = User.objects.create_user("clerk", password="pw", is_staff=True)
        # The operator: a superuser the plans bootstrap puts on the Superuser
        # plan, exactly as init_data does for the platform's admin.
        from io import StringIO

        from django.core.management import call_command

        self.operator = User.objects.create_superuser("root", password="pw")
        call_command("bootstrap_plans", stdout=StringIO())
        self.operator = User.objects.get(pk=self.operator.pk)

    def urls(self):
        for name in DESKS:
            try:
                yield name, reverse(name)
            except NoReverseMatch:
                continue


class DeskTests(GateTestCase):
    def test_a_member_is_refused_every_desk(self):
        self.client.force_login(self.member)
        for name, url in self.urls():
            with self.subTest(desk=name):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_json_caller_gets_json(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("assets:wallet"), HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")

    def test_the_operator_reaches_every_desk(self):
        self.client.force_login(self.operator)
        for name, url in self.urls():
            with self.subTest(desk=name):
                self.assertNotEqual(self.client.get(url).status_code, 403)

    def test_plain_staff_are_refused_every_desk_like_a_member(self):
        self.client.force_login(self.staff)
        for name, url in self.urls():
            with self.subTest(desk=name):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_member_reaches_their_mana(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("mana:index")).status_code, 200)

    def test_anonymous_still_goes_to_log_in_first(self):
        self.assertEqual(self.client.get(reverse("assets:wallet")).status_code, 302)


class SurfaceTests(GateTestCase):
    def body(self, user, name):
        self.client.force_login(user)
        return self.client.get(reverse(name)).content.decode()

    def test_the_dashboard_offers_a_member_mana_not_the_wallet(self):
        body = self.body(self.member, "core:dashboard")
        self.assertIn(reverse("mana:index"), body)
        self.assertNotIn(reverse("assets:wallet"), body)

    def test_the_operator_keeps_the_wallet_tile(self):
        self.assertIn(reverse("assets:wallet"), self.body(self.operator, "core:dashboard"))

    def test_plain_staff_see_mana_not_the_wallet(self):
        body = self.body(self.staff, "core:dashboard")
        self.assertNotIn(reverse("assets:wallet"), body)

    def test_the_strip_shows_a_member_mana_only(self):
        body = self.body(self.member, "mana:index")
        self.assertIn('aria-label="Economy navigation"', body)
        self.assertNotIn(reverse("quota:my_usage"), body)
        self.assertNotIn(reverse("tariffs:usage_list"), body)

    def test_a_member_gets_no_gas_pump(self):
        from django.test import RequestFactory

        from toto.core.plugin import FloatingPlugin

        request = RequestFactory().get("/vault/")
        request.user = self.member
        keys = [r.key for r in FloatingPlugin.render_all(request=request)]
        self.assertNotIn("gas_pump", keys)

    def test_the_usage_tab_points_a_member_at_mana(self):
        from django.template import Context, Template
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.member
        html = Template('{% load quota_tags %}{% quota_tab "vault" %}').render(
            Context({"request": request}))
        self.assertIn(reverse("mana:index"), html)
