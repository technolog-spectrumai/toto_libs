"""The member's mana pages beyond the first render: the Regeneration tab's
edges (no pools, the off switch, a circle that sets one pool, the superuser's
member counts, a host without circles), the JSON the header chip polls, the
pool page's refusals, the operators' links and the profile section.
"""

import json
import shutil
import tempfile
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.mana import services, views
from toto.mana.models import ManaPool
from toto.mana.tests.fixtures import economy, master, spend
from toto.people.models import Person
from toto.socialhub.models import Community

User = get_user_model()


class TempMediaMixin:
    """``make_vault_file`` writes a real file: keep it out of the shared MEDIA_ROOT."""

    @classmethod
    def setUpClass(cls):
        media = tempfile.mkdtemp(prefix="mana-test-media-")
        cls.addClassCleanup(shutil.rmtree, media, ignore_errors=True)
        setting = override_settings(MEDIA_ROOT=media)
        setting.enable()
        cls.addClassCleanup(setting.disable)
        super().setUpClass()


def circle(name, **speeds):
    return Community.objects.create(
        name=name, is_circle=True, **{f"regen_{role}": value for role, value in speeds.items()})


def join(user, *communities):
    person, _ = Person.objects.get_or_create(user=user, defaults={"display_name": user.username})
    person.communities.add(*communities)


@master
class RegenerationTabTests(TestCase):
    def setUp(self):
        economy()
        self.ada = User.objects.create_user("ada", password="pw")
        self.url = reverse("mana:regeneration")

    def page(self, user=None):
        self.client.force_login(user or self.ada)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response

    def row(self, response, role):
        return next(r for r in response.context["rows"] if r["role"] == role)

    def test_anonymous_is_sent_to_sign_in(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.url, response["Location"].split("?")[0])

    def test_a_host_with_no_pools_says_so(self):
        ManaPool.objects.all().delete()
        response = self.page()
        self.assertEqual(response.context["rows"], [])
        self.assertContains(response, "Mana is not set up on this platform yet.")

    def test_a_circle_that_sets_one_pool_leaves_the_others_at_the_default(self):
        join(self.ada, circle("archivists", storage=Decimal("20")))
        response = self.page()
        self.assertEqual((self.row(response, "storage")["rate"], self.row(response, "storage")["circle"]),
                         (Decimal("20"), "archivists"))
        self.assertEqual((self.row(response, "compute")["rate"], self.row(response, "compute")["circle"]),
                         (Decimal("4"), ""))
        body = response.content.decode()
        self.assertIn("+20", body)
        self.assertIn("—", body)                       # the pools the circle leaves alone

    def test_the_off_switch_reads_zero_whatever_the_circle_says(self):
        join(self.ada, circle("board", compute=Decimal("12")))
        ManaPool.objects.filter(role="compute").update(regen_per_hour=Decimal("0"))
        row = self.row(self.page(), "compute")
        self.assertEqual((row["rate"], row["per_day"], row["circle"]), (Decimal("0"), Decimal("0"), ""))

    def test_a_zero_speed_circle_is_named_as_the_reason(self):
        join(self.ada, circle("paused", compute=Decimal("0")))
        row = self.row(self.page(), "compute")
        self.assertEqual((row["rate"], row["circle"]), (Decimal("0"), "paused"))

    def test_the_member_list_is_their_circles_with_the_speeds_each_sets(self):
        join(self.ada, circle("board", compute=Decimal("12")))
        circle("seniors", compute=Decimal("8"))
        mine = self.page().context["mine"]
        self.assertEqual(mine, [{"name": "board", "speeds": [None, Decimal("12"), None]}])

    def test_staff_who_are_not_superusers_see_only_their_own(self):
        circle("seniors", compute=Decimal("8"))
        stan = User.objects.create_user("stan", password="pw", is_staff=True)
        response = self.page(stan)
        self.assertEqual(response.context["every"], [])
        self.assertNotContains(response, "seniors")

    def test_a_superuser_sees_how_many_members_each_circle_has(self):
        board = circle("board", compute=Decimal("12"))
        join(self.ada, board)
        join(User.objects.create_user("bob", password="pw"), board)
        circle("empty")
        root = User.objects.create_superuser("root", password="pw")
        every = self.page(root).context["every"]
        self.assertEqual([(c["name"], c["members"]) for c in every], [("board", 2), ("empty", 0)])

    def test_a_functional_community_is_never_listed(self):
        devs = Community.objects.create(name="devs")
        join(self.ada, devs)
        root = User.objects.create_superuser("root", password="pw")
        self.assertEqual(self.page(self.ada).context["mine"], [])
        self.assertEqual(self.page(root).context["every"], [])

    def test_a_host_without_circles_shows_the_speeds_alone(self):
        real = apps.is_installed
        with mock.patch.object(apps, "is_installed",
                               side_effect=lambda name: name != "toto.socialhub" and real(name)):
            self.client.force_login(self.ada)
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context["mine"], response.context["every"]), ([], []))
        self.assertEqual(len(response.context["rows"]), 3)


@master
class BalancesApiTests(TestCase):
    def setUp(self):
        economy()
        self.ada = User.objects.create_user("ada", password="pw")
        self.url = reverse("mana:api_balances")

    def test_anonymous_gets_json_401_not_a_login_page(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("detail", response.json())

    def test_a_member_gets_three_pools_the_lowest_and_the_last_movement(self):
        spend(self.ada, "storage", "12.346")
        self.client.force_login(self.ada)
        response = self.client.get(self.url)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(set(data["colours"]), {"security", "compute", "storage"})
        self.assertEqual(data["colours"]["storage"]["amount"], 87.65)      # two places
        self.assertEqual(data["colours"]["compute"]["regen_per_hour"], 4.0)
        self.assertEqual(data["lowest"], "storage")
        self.assertEqual((data["last"]["role"], data["last"]["delta"], data["last"]["kind"]),
                         ("storage", -12.35, "transfer"))

    def test_a_host_with_no_pools_answers_empty_not_an_error(self):
        ManaPool.objects.all().delete()
        self.client.force_login(self.ada)
        data = self.client.get(self.url).json()
        self.assertEqual((data["ok"], data["colours"], data["lowest"], data["last"]),
                         (True, {}, None, None))

    def test_the_chip_paints_from_the_same_numbers(self):
        chip = json.loads(views.chip_status(services.balances_of(self.ada)))
        self.assertEqual(chip["compute"]["amount"], 100.0)
        self.assertEqual(chip["compute"]["band"], "ok")
        self.assertEqual(views.chip_status(None), "{}")


@master
class PoolPageTests(TempMediaMixin, TestCase):
    def setUp(self):
        economy()
        self.ada = User.objects.create_user("ada", password="pw")
        self.client.force_login(self.ada)

    def test_an_unknown_colour_is_404(self):
        self.assertEqual(self.client.get(reverse("mana:colour", args=["gold"])).status_code, 404)

    def test_a_pool_this_host_has_not_set_up_is_404(self):
        ManaPool.objects.filter(role="storage").delete()
        self.assertEqual(self.client.get(reverse("mana:colour", args=["storage"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("mana:colour", args=["compute"])).status_code, 200)

    def test_the_encrypt_prompt_stays_closed_for_a_healthy_pool_unless_asked(self):
        from toto.mana.tests.fixtures import seed_prices
        from toto.tax.tests.factories import GB, make_vault_file

        seed_prices()
        make_vault_file(self.ada, GB)
        url = reverse("mana:colour", args=["security"])
        self.assertFalse(self.client.get(url).context["prompt_open"])
        self.assertTrue(self.client.get(url, {"encrypt": "1"}).context["prompt_open"])

    def test_a_low_pool_opens_the_prompt_by_itself(self):
        from toto.mana.tests.fixtures import seed_prices
        from toto.tax.tests.factories import GB, make_vault_file

        seed_prices()
        make_vault_file(self.ada, GB)
        spend(self.ada, "security", "80")
        context = self.client.get(reverse("mana:colour", args=["security"])).context
        self.assertEqual(context["b"]["band"], "low")
        self.assertTrue(context["prompt_open"])

    def test_the_prompt_is_never_offered_with_no_plain_files(self):
        spend(self.ada, "security", "80")
        self.assertFalse(self.client.get(reverse("mana:colour", args=["security"])).context["prompt_open"])

    def test_the_compute_page_has_no_encrypt_prompt(self):
        context = self.client.get(reverse("mana:colour", args=["compute"])).context
        self.assertNotIn("plain_files", context)
        self.assertEqual([p["kind"] for p in context["prices"] if p["kind"] == "earn"], [])


class ChartTests(TestCase):
    def test_nothing_to_draw_is_an_empty_line(self):
        self.assertEqual(views._chart([], Decimal("100")), "")
        self.assertEqual(views._chart([{"level": Decimal(1)}], Decimal("0")), "")

    def test_a_full_and_an_empty_point_span_the_box(self):
        line = views._chart([{"level": Decimal("0")}, {"level": Decimal("100")}], Decimal("100"))
        self.assertEqual(line, "0.0,62.0 300.0,2.0")


@master
class HistoryChartTests(TestCase):
    def setUp(self):
        economy()
        self.ada = User.objects.create_user("ada", password="pw")

    def test_one_line_per_pool_over_the_days_asked(self):
        chart = json.loads(views.history_chart(self.ada, days=3))
        self.assertEqual(chart["chart_type"], "line")
        self.assertEqual(len(chart["labels"]), 4)
        self.assertEqual([d["borderColor"] for d in chart["datasets"]],
                         [views.LINE_COLOURS[r] for r in ("security", "compute", "storage")])
        self.assertEqual(chart["datasets"][1]["data"][-1], 100.0)

    def test_no_pools_is_no_chart(self):
        ManaPool.objects.all().delete()
        self.assertEqual(views.history_chart(self.ada), "")


@master
class AboutPageTests(TestCase):
    def setUp(self):
        economy()

    def test_members_get_no_operator_links(self):
        ada = User.objects.create_user("ada", password="pw")
        self.client.force_login(ada)
        response = self.client.get(reverse("mana:about"))
        self.assertEqual(response.context["staff_links"], [])
        self.assertNotContains(response, 'data-testid="operators"')

    def test_staff_get_the_links_this_host_can_reverse(self):
        stan = User.objects.create_user("stan", password="pw", is_staff=True)
        self.client.force_login(stan)
        response = self.client.get(reverse("mana:about"))
        labels = [link["label"] for link in response.context["staff_links"]]
        self.assertIn("Ledger", labels)
        self.assertIn("Tariffs", labels)
        self.assertContains(response, 'data-testid="operators"')

    def test_a_link_this_host_cannot_reverse_is_dropped_not_a_500(self):
        from django.urls import NoReverseMatch

        real = views.reverse

        def picky(name, *args, **kwargs):
            if name == "bourse:exchange_center":
                raise NoReverseMatch(name)
            return real(name, *args, **kwargs)

        stan = User.objects.create_user("stan", password="pw", is_staff=True)
        self.client.force_login(stan)
        with mock.patch.object(views, "reverse", side_effect=picky):
            response = self.client.get(reverse("mana:about"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Exchange", [link["label"] for link in response.context["staff_links"]])


@master
class ProfileSectionTests(TestCase):
    def setUp(self):
        economy()
        from toto.mana.plugins.profile_plugins import ManaProfilePlugin

        self.plugin = ManaProfilePlugin()
        self.ada = User.objects.create_user("ada", password="pw")

    def test_a_profile_with_no_account_shows_no_mana(self):
        orphan = Person.objects.create(display_name="orphan")
        self.assertFalse(self.plugin.is_visible_for_profile(profile=orphan))
        self.assertFalse(self.plugin.is_visible_for_profile(profile=None))

    def test_a_member_profile_lists_the_three_pools_in_order(self):
        profile = Person.objects.create(user=self.ada, display_name="ada")
        self.assertTrue(self.plugin.is_visible_for_profile(profile=profile))
        context = self.plugin.get_context(profile=profile)
        self.assertEqual([b["role"] for b in context["mana_balances"]],
                         ["security", "compute", "storage"])

    def test_no_pools_is_an_empty_section(self):
        ManaPool.objects.all().delete()
        profile = Person.objects.create(user=self.ada, display_name="ada")
        self.assertEqual(self.plugin.get_context(profile=profile)["mana_balances"], [])
