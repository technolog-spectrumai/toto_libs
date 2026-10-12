"""Discipline's pages: a person's mana and its history, the read-only lists
of sigils and auras, the admin panel where they are made and assigned, and
the two plugins (a profile's mana, the directory's Aura column)."""

from __future__ import annotations

import io
import json
import re
import tempfile
from datetime import timedelta
import unittest
from decimal import Decimal as D

from django.apps import apps

if not apps.is_installed("toto.discipline"):
    raise unittest.SkipTest("toto.discipline is not installed on this host")

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import TestCase, override_settings  # noqa: E402
from django.urls import NoReverseMatch, reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from toto.core.models import Platform  # noqa: E402
from toto.people.models import Person  # noqa: E402
from toto.socialhub.models import Community  # noqa: E402

from . import services, sigils  # noqa: E402
from .models import Aura, CommunityAura, ManaAccount, ManaEvent, Sigil, SigilEvent  # noqa: E402
from .tests import FILE  # noqa: E402

try:
    reverse("discipline:index")
except NoReverseMatch:
    raise unittest.SkipTest("this host does not mount Discipline's pages") from None

User = get_user_model()


def picture() -> SimpleUploadedFile:
    from PIL import Image

    data = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(data, "PNG")
    return SimpleUploadedFile("sigil.png", data.getvalue(), content_type="image/png")


@override_settings(DISCIPLINE_FILE=FILE, QUOTA_CHARGER="discipline",
                   MEDIA_ROOT=tempfile.mkdtemp(prefix="discipline-pages-"))
class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.mia = User.objects.create_user("mia", password="pw")
        cls.club = Community.objects.create(name="Club", slug="club")
        cls.guild = Community.objects.create(name="Guild", slug="guild")
        Person.objects.create(user=cls.mia, display_name="Mia").communities.add(cls.club)
        cls.common = Aura.objects.create(name="Common", slug="common", regen_per_hour=D("4"),
                                         cap=D("60"))
        CommunityAura.objects.create(community=cls.club, aura=cls.common)

    def as_(self, user):
        self.client.force_login(user)
        return self.client

    def audited(self, action):
        if not apps.is_installed("toto.audit"):
            return True
        from toto.audit.models import AuditRecord

        return AuditRecord.objects.filter(action=f"DISCIPLINE.{action}").exists()


class MemberTests(Fixture):
    def test_the_page_shows_the_balance_the_speed_and_where_it_comes_from(self):
        sigils.assign(sigils.create("Lantern", 2, by=self.root), self.mia, by=self.root)
        services.spend(self.mia, "storage.request", 1)
        response = self.as_(self.mia).get(reverse("discipline:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["balance"], D("9.5"))
        speed = response.context["speed"]
        self.assertEqual((speed.rate, speed.cap, speed.aura), (D("6"), D("60"), self.common))
        self.assertEqual(response.context["community"], self.club)
        body = response.content.decode()
        for mark in ('data-testid="mana-balance"', 'data-testid="mana-aura"',
                     'data-testid="mana-sigil"',
                     'data-testid="discipline-tab-mana"', 'data-testid="discipline-tab-history"'):
            self.assertIn(mark, body)
        self.assertEqual([row["price"] for row in response.context["prices"]],
                         [D("0.5"), D("0.1")])
        self.assertEqual(len(response.context["sources"]), 1)
        # Nothing of the administrators' is drawn for a member.
        for theirs in ('data-testid="mana-grant-form"', 'data-testid="discipline-tab-sigils"',
                       'data-testid="discipline-tab-auras"'):
            self.assertNotIn(theirs, body)

    def test_the_chip_is_a_bar_of_what_remains_and_the_whole_number(self):
        services.grant(self.mia, D("2.7"), by=self.root)                 # 12.7 of the aura's 60
        body = self.as_(self.mia).get(reverse("discipline:index")).content.decode()
        self.assertIn('data-testid="mana-chip" data-pct="21"', body)
        # The chip stays on the bar when the bar folds (2026-10-12): it is in
        # the group the header never hides, before the folding one.
        from toto.core.plugin import HeaderPlugin

        self.assertIs(HeaderPlugin.get("discipline_chip").pinned, True)
        pinned = body.index('id="header-pinned" class="flex shrink-0')
        self.assertLess(pinned, body.index('data-testid="mana-chip"'))
        self.assertLess(body.index('data-testid="mana-chip"'), body.index('id="header-nav"'))
        self.assertRegex(body, r'data-testid="mana-chip-amount">12</span>')
        # What remains is the palette's cyan, what is spent the grey track.
        bar = re.search(r'data-testid="mana-bar">(.*?)</span>\s*</span>', body, re.S).group(0)
        self.assertRegex(body, r'role="meter" aria-valuemin="0" aria-valuemax="60" aria-valuenow="12"[^>]*'
                               r'class="[^"]*bg-white/25[^"]*" data-testid="mana-bar"')
        self.assertIn('style="width: 21%"', bar)
        self.assertIn("darkMode ? 'bg-security-dark' : 'bg-security-light'", bar)
        # The dropdown's render has the bar too, with the cap beside the number.
        self.assertIn('data-testid="mana-bar-mobile"', body)

    def test_the_bar_is_full_above_the_cap_and_empty_at_nothing(self):
        self.assertEqual(services.chip_state(self.mia), {"amount": 10, "cap": 60, "pct": 17})
        services.grant(self.mia, 500, by=self.root)
        self.assertEqual(services.chip_state(self.mia), {"amount": 510, "cap": 60, "pct": 100})
        ManaAccount.objects.filter(user=self.mia).update(balance=0)
        self.assertEqual(services.chip_state(self.mia), {"amount": 0, "cap": 60, "pct": 0})
        # Somebody with no aura is measured against the platform's cap.
        self.assertEqual(services.chip_state(self.staff), {"amount": 10, "cap": 100, "pct": 10})

    def test_the_changes_are_the_history_tabs_and_the_mana_page_has_none(self):
        services.spend(self.mia, "storage.request", 1)
        response = self.as_(self.mia).get(reverse("discipline:index"))
        self.assertNotIn("events", response.context)
        body = response.content.decode()
        self.assertNotIn('data-testid="mana-event"', body)
        self.assertNotIn('data-testid="mana-changes"', body)

    def test_the_lists_and_the_grant_are_a_superusers(self):
        doors = [("get", reverse("discipline:sigils")), ("get", reverse("discipline:auras")),
                 ("post", reverse("discipline:grant"))]
        for user in (self.mia, self.staff):
            client = self.as_(user)
            for method, url in doors:
                with self.subTest(user=user.username, url=url):
                    response = getattr(client, method)(url, {"username": "mia", "amount": "50"})
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(services.balance(self.mia), D("10"))

    def test_nobody_reads_it_signed_out(self):
        for name in ("discipline:index", "discipline:history", "discipline:sigils"):
            self.assertIn(self.client.get(reverse(name)).status_code, (302, 403), name)


class HistoryTests(Fixture):
    def at(self, user, key, when, balance_after):
        """An event of ``user``'s, at a time of the test's choosing."""
        event = ManaEvent.objects.create(user=user, delta=1, balance_after=D(str(balance_after)),
                                         kind="grant", key=key)
        ManaEvent.objects.filter(pk=event.pk).update(created_at=when)

    def test_the_points_are_the_balance_after_the_last_change_before_each(self):
        now = timezone.now()
        ManaEvent.objects.filter(user=self.mia).update(created_at=now - timedelta(days=3))  # the first 10
        self.at(self.mia, "h1", now - timedelta(hours=5, minutes=10), 14)
        self.at(self.mia, "h2", now - timedelta(hours=2, minutes=10), 6)
        ManaAccount.objects.filter(user=self.mia).update(balance=D("6"))
        points = services.balance_history(self.mia, span=timedelta(hours=8),
                                          step=timedelta(hours=1), now=now)
        self.assertEqual(len(points), 9)                           # the start, eight steps, the last is now
        self.assertEqual(points[0], (now - timedelta(hours=8), D("10")))
        self.assertEqual([have for _at, have in points],
                         [D("10")] * 3 + [D("14")] * 3 + [D("6")] * 3)
        self.assertEqual(points[-1][0], now)
        # Before there was any change at all, there was nothing.
        self.assertEqual(services.balance_history(self.staff, span=timedelta(days=40),
                                                  step=timedelta(days=10), now=now)[0][1], 0)

    def test_the_last_point_is_now_whatever_the_step(self):
        now = timezone.now()
        points = services.balance_history(self.mia, span=timedelta(hours=1),
                                          step=timedelta(minutes=25), now=now)
        self.assertEqual([at for at, _have in points],
                         [now - timedelta(minutes=60), now - timedelta(minutes=35),
                          now - timedelta(minutes=10), now])

    def test_every_change_is_listed_under_the_line_a_page_at_a_time(self):
        from .views import CHANGES_A_PAGE

        services.grant(self.staff, 5, reason="theirs", by=self.root)
        for n in range(CHANGES_A_PAGE + 4):                       # with the first balance: 25
            services.grant(self.mia, 1, reason=f"gift {n}", by=self.root)
        client = self.as_(self.mia)
        first = client.get(reverse("discipline:history"), {"range": "week"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.context["events"]), CHANGES_A_PAGE)
        self.assertEqual(first.context["changes_total"], CHANGES_A_PAGE + 5)
        self.assertEqual({event.user_id for event in first.context["events"]}, {self.mia.pk})
        self.assertEqual(first.context["events"][0].reason, f"gift {CHANGES_A_PAGE + 3}")   # newest first
        body = first.content.decode()
        self.assertEqual(body.count('data-testid="mana-event"'), CHANGES_A_PAGE)
        self.assertIn('data-testid="mana-changes-pager"', body)
        # The pager keeps the chosen range, and the second page has the rest.
        self.assertIn('?page=2&amp;range=week', body)
        second = client.get(reverse("discipline:history"), {"range": "week", "page": 2})
        self.assertEqual(len(second.context["events"]), 5)
        self.assertEqual(second.context["range"], "week")
        self.assertEqual(second.context["events"][len(second.context["events"]) - 1].kind, "signup")
        # A page that is not there is the last one, not an error.
        self.assertEqual(client.get(reverse("discipline:history"), {"page": 99}).status_code, 200)
        # Somebody with a few changes has no pager.
        few = self.as_(self.staff).get(reverse("discipline:history")).content.decode()
        self.assertIn('data-testid="mana-event"', few)
        self.assertNotIn('data-testid="mana-changes-pager"', few)

    def test_the_tab_draws_one_line_and_offers_three_ranges_and_no_more(self):
        self.at(self.staff, "theirs", timezone.now() - timedelta(hours=1), 999)
        client = self.as_(self.mia)
        response = client.get(reverse("discipline:history"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["range"], "day")
        self.assertEqual([key for key, _label in response.context["ranges"]],
                         ["day", "week", "month"])
        chart = json.loads(response.context["chart_json"])
        self.assertEqual(chart["chart_type"], "line")
        self.assertEqual(len(chart["datasets"]), 1)                # the balance, and only it
        self.assertEqual(len(chart["labels"]), len(chart["datasets"][0]["data"]))
        self.assertEqual(len(chart["labels"]), 97)                 # a day in quarter hours, and now
        self.assertEqual(chart["datasets"][0]["data"][-1], 10.0)
        self.assertNotIn(999.0, chart["datasets"][0]["data"])      # nobody else's
        self.assertFalse(chart["options"]["plugins"]["legend"]["display"])
        from toto.core import palette

        self.assertEqual(chart["datasets"][0]["borderColor"], palette.colors()["security-light"])
        self.assertEqual(chart["options"]["interaction"], {"mode": "index", "intersect": False})
        body = response.content.decode()
        self.assertIn('id="mana_history"', body)
        self.assertIn("chart.umd.min", body)
        card = body[body.index('data-testid="mana-history"'):]
        card = card[:card.index("</form>")]
        self.assertEqual(re.findall(r'<option value="(\w+)"', card), ["day", "week", "month"])
        for chosen, points in (("week", 169), ("month", 121)):
            response = client.get(reverse("discipline:history"), {"range": chosen})
            self.assertEqual(response.context["range"], chosen)
            self.assertEqual(len(json.loads(response.context["chart_json"])["labels"]), points)
            self.assertIn(f'<option value="{chosen}" selected', response.content.decode())
        # Anything else is the last day.
        for other in ("year", "all", "9999", ""):
            self.assertEqual(client.get(reverse("discipline:history"), {"range": other})
                             .context["range"], "day")


class GrantTests(Fixture):
    """Granting is a superuser's who is on the Superuser plan: both, never
    one (2026-10-12, ``views.may_grant``)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sells_the_plan = False
        if apps.is_installed("toto.subscriptions"):
            from django.core.management import call_command

            from toto.subscriptions.plans import admin_plan

            call_command("bootstrap_plans", stdout=io.StringIO())    # root → the Superuser plan
            cls.sells_the_plan = admin_plan() is not None
        # Made after it: the account alone, on no plan.
        cls.bare_root = User.objects.create_superuser("bare", "b@e.com", "pw")

    def test_a_superuser_without_the_plan_does_not_grant(self):
        from .views import may_grant

        if not self.sells_the_plan:
            self.skipTest("this host's ladder has no plan for admins")
        self.assertTrue(may_grant(self.root))
        self.assertFalse(may_grant(self.bare_root))
        client = self.as_(self.bare_root)
        body = client.get(reverse("discipline:index")).content.decode()
        self.assertNotIn('data-testid="mana-grant-form"', body)
        self.assertIn('data-testid="mana-grant-needs-plan"', body)
        response = client.post(reverse("discipline:grant"), {"username": "mia", "amount": "50"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(services.balance(self.mia), D("10"))
        self.assertFalse(ManaEvent.objects.filter(kind="grant").exists())
        # The rest of the administrators' pages is the account's, as before.
        self.assertEqual(client.get(reverse("discipline:sigils")).status_code, 200)

    def test_the_plan_is_not_asked_for_where_none_is_sold(self):
        from unittest import mock

        from .views import may_grant

        with mock.patch("toto.discipline.views.apps.is_installed", return_value=False):
            self.assertTrue(may_grant(self.bare_root))
            for user in (self.mia, self.staff, None):
                self.assertFalse(may_grant(user))

    def test_nobody_but_a_superuser_grants_whatever_their_plan(self):
        from .views import may_grant

        for user in (self.mia, self.staff):
            self.assertFalse(may_grant(user))
        self.assertNotIn('data-testid="mana-grant-needs-plan"',
                         self.as_(self.mia).get(reverse("discipline:index")).content.decode())

    def test_a_superuser_grants_and_it_is_on_the_record(self):
        client = self.as_(self.root)
        body = client.get(reverse("discipline:index")).content.decode()
        self.assertIn('data-testid="mana-grant-form"', body)
        response = client.post(reverse("discipline:grant"), {
            "username": "MIA", "amount": "150,5", "reason": "for the archive"})
        self.assertRedirects(response, reverse("discipline:index"))
        self.assertEqual(services.balance(self.mia), D("160.5"))
        event = ManaEvent.objects.get(user=self.mia, kind="grant")
        self.assertEqual((event.by, event.reason), (self.root, "for the archive"))
        self.assertTrue(self.audited("MANA_GRANTED"))

    def test_a_grant_to_nobody_or_of_nothing_is_not_made(self):
        client = self.as_(self.root)
        for data in ({"username": "nobody", "amount": "5"}, {"username": "mia", "amount": "0"},
                     {"username": "mia", "amount": "-3"}, {"username": "mia", "amount": "lots"},
                     {"username": "mia", "amount": ""}, {"username": "", "amount": "5"}):
            with self.subTest(data=data):
                client.post(reverse("discipline:grant"), data)
                self.assertEqual(services.balance(self.mia), D("10"))
        self.assertEqual(client.get(reverse("discipline:grant")).status_code, 405)


class ListTests(Fixture):
    """The pages Sigils and Auras: tables to read, with nothing to change."""

    def test_the_sigils_are_listed_in_the_table_primitive(self):
        lantern = sigils.create("Lantern", D("2.5"), by=self.root, image=picture())
        sigils.assign(lantern, self.mia, by=self.root)
        spare = sigils.create("Spare", 1, by=self.root)
        gone = sigils.create("Gone", 1, by=self.root)
        sigils.revoke(gone, by=self.root)
        response = self.as_(self.root).get(reverse("discipline:sigils"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "oya/partials/table.html")
        self.assertTemplateUsed(response, "oya/partials/paging_script.html")
        self.assertEqual([column["label"] for column in response.context["columns"]],
                         ["Picture", "Name", "UUID", "Mana an hour", "Holder", "State", "Assigned"])
        rows = {row[1]["text"]: row for row in response.context["rows"]}
        self.assertEqual(sorted(rows), ["Gone", "Lantern", "Spare"])
        row = rows["Lantern"]
        self.assertEqual(row[0]["type"], "image")
        self.assertTrue(row[0]["url"].endswith(".png"))
        self.assertEqual([cell["text"] for cell in row[2:6]],
                         [str(lantern.uuid), "2.5", "mia", "Generating"])
        self.assertTrue(row[6]["text"])
        self.assertEqual([cell["text"] for cell in rows["Spare"][4:7]], ["", "Held by nobody", ""])
        self.assertEqual(rows["Gone"][5]["text"], "Revoked")
        self.assertEqual(rows["Spare"][0]["url"], "")
        body = response.content.decode()
        self.assertIn(str(spare.uuid), body)
        self.assertIn('<img src="', body)

    def test_the_auras_are_listed_with_their_communities(self):
        shared = Aura.objects.create(name="Shared", slug="shared", regen_per_hour=D("7"))
        CommunityAura.objects.create(community=self.guild, aura=shared)
        response = self.as_(self.root).get(reverse("discipline:auras"))
        self.assertTemplateUsed(response, "oya/partials/table.html")
        rows = {row[1]["text"]: [cell["text"] for cell in row[2:]]
                for row in response.context["rows"]}
        self.assertEqual(rows, {"Common": ["4", "60", "Club"],
                                "Shared": ["7", "100", "Guild"]})          # no cap: the platform's

    def test_nothing_on_the_lists_changes_anything(self):
        lantern = sigils.create("Lantern", 2, by=self.root)
        client = self.as_(self.root)
        for name in ("discipline:sigils", "discipline:auras"):
            body = client.get(reverse(name)).content.decode()
            page = body[body.index("<main") if "<main" in body else 0:]
            content = re.search(r'data-testid="(sigils|auras)-admin-note".*', page, re.S).group(0)
            content = content[:content.index("</section>")]
            # The table's own pager (rows a page, Prev, Next) is all there is to press.
            for control in ("<form", "<input", "<textarea", 'type="submit"', "csrfmiddlewaretoken"):
                self.assertNotIn(control, content, name)
            self.assertEqual(content.count("<select"), 1, name)
            # A POST is not a way in either: the page answers as it does a GET.
            client.post(reverse(name), {"name": "X", "regen_per_hour": "9", "username": "mia",
                                        "form": "aura", f"aura-{self.club.pk}": ""})
        self.assertEqual((Sigil.objects.count(), Aura.objects.count()), (1, 1))
        self.assertEqual(CommunityAura.objects.get(community=self.club).aura, self.common)
        lantern.refresh_from_db()
        self.assertIsNone(lantern.holder)
        # The doors that made, assigned and revoked are gone.
        for gone, argument in (("sigil_assign", lantern.uuid), ("sigil_revoke", lantern.uuid),
                               ("aura_edit", "common")):
            with self.assertRaises(NoReverseMatch):
                reverse(f"discipline:{gone}", args=[argument])
        for path in (f"/sigils/{lantern.uuid}/assign/", f"/sigils/{lantern.uuid}/revoke/",
                     "/auras/common/"):
            self.assertEqual(client.post(reverse("discipline:index").rstrip("/") + path).status_code,
                             404)

    def test_the_lists_lead_to_the_admin_panel(self):
        client = self.as_(self.root)
        try:
            sigil_admin = reverse("admin:discipline_sigil_changelist")
        except NoReverseMatch:
            self.skipTest("this host mounts no admin panel")
        self.assertIn(f'href="{sigil_admin}"', client.get(reverse("discipline:sigils")).content.decode())
        body = client.get(reverse("discipline:auras")).content.decode()
        self.assertIn(f'href="{reverse("admin:discipline_aura_changelist")}"', body)
        self.assertIn(f'href="{reverse("admin:discipline_communityaura_changelist")}"', body)


class AdminTests(Fixture):
    """Sigils and auras are made, changed and assigned in the admin panel."""

    def setUp(self):
        try:
            self.add_sigil = reverse("admin:discipline_sigil_add")
        except NoReverseMatch:
            self.skipTest("this host mounts no admin panel")
        self.client.force_login(self.root)

    def change(self, sigil):
        return reverse("admin:discipline_sigil_change", args=[sigil.pk])

    def sigil_data(self, **over):
        return {"name": "Lantern", "description": "", "regen_per_hour": "2", "holder": "",
                "note": "", **over}

    def test_a_sigil_is_made_with_its_picture(self):
        response = self.client.post(self.add_sigil, self.sigil_data(image=picture(),
                                                                    description="the first"))
        self.assertEqual(response.status_code, 302, response.content.decode()[:600])
        lantern = Sigil.objects.get(name="Lantern")
        self.assertEqual((lantern.regen_per_hour, lantern.issued_by, lantern.holder, lantern.active),
                         (D("2"), self.root, None, True))
        self.assertTrue(lantern.image.name.startswith("discipline/sigils/"))
        self.assertFalse(SigilEvent.objects.exists())
        self.assertTrue(self.audited("SIGIL_CREATED"))

    def test_choosing_a_holder_assigns_and_clearing_it_revokes(self):
        self.client.post(self.add_sigil, self.sigil_data(holder=str(self.mia.pk),
                                                         note="for the archive"))
        lantern = Sigil.objects.get(name="Lantern")
        self.assertEqual((lantern.holder, lantern.generating), (self.mia, True))
        self.assertEqual(list(SigilEvent.objects.values_list("action", "user", "by", "note")),
                         [("assigned", self.mia.pk, self.root.pk, "for the archive")])
        self.assertEqual(services.speed_for(self.mia).rate, D("6"))
        self.assertTrue(self.audited("SIGIL_ASSIGNED"))

        # To another person: the first stops holding it, in the history too.
        self.client.post(self.change(lantern), self.sigil_data(holder=str(self.staff.pk)))
        lantern.refresh_from_db()
        self.assertEqual(lantern.holder, self.staff)
        self.assertEqual(services.speed_for(self.mia).rate, D("4"))
        self.assertEqual(sorted(SigilEvent.objects.values_list("action", "user__username")),
                         [("assigned", "mia"), ("assigned", "staff"), ("revoked", "mia")])

        # A change of its words alone moves nobody.
        self.client.post(self.change(lantern), self.sigil_data(
            holder=str(self.staff.pk), name="Old lantern", regen_per_hour="3"))
        lantern.refresh_from_db()
        self.assertEqual((lantern.name, lantern.regen_per_hour, lantern.holder),
                         ("Old lantern", D("3"), self.staff))
        self.assertEqual(SigilEvent.objects.count(), 3)
        self.assertTrue(self.audited("SIGIL_CHANGED"))

        services.regenerate_hour()
        had = services.balance(self.staff)
        self.client.post(self.change(lantern), self.sigil_data(holder="", name="Old lantern",
                                                               regen_per_hour="3"))
        lantern.refresh_from_db()
        self.assertEqual((lantern.holder, lantern.active, lantern.revoked_by),
                         (None, False, self.root))
        self.assertEqual(services.balance(self.staff), had)         # what it generated stays
        self.assertTrue(self.audited("SIGIL_REVOKED"))

        # And it is assigned again from the same form.
        self.client.post(self.change(lantern), self.sigil_data(holder=str(self.mia.pk),
                                                               name="Old lantern",
                                                               regen_per_hour="3"))
        lantern.refresh_from_db()
        self.assertEqual((lantern.holder, lantern.active), (self.mia, True))

    def test_the_action_revokes_the_selected(self):
        one = sigils.create("One", 1, by=self.root)
        two = sigils.create("Two", 1, by=self.root)
        sigils.assign(one, self.mia, by=self.root)
        self.client.post(reverse("admin:discipline_sigil_changelist"), {
            "action": "revoke_selected", "_selected_action": [one.pk, two.pk]})
        self.assertEqual(list(Sigil.objects.order_by("name").values_list("active", "holder")),
                         [(False, None), (False, None)])
        self.assertEqual(services.speed_for(self.mia).rate, D("4"))

    def test_an_aura_is_made_changed_and_given_to_communities(self):
        self.client.post(reverse("admin:discipline_aura_add"), {
            "name": "Fast aura", "slug": "fast-aura", "description": "", "regen_per_hour": "9",
            "cap": ""})
        fast = Aura.objects.get(slug="fast-aura")
        self.assertEqual((fast.regen_per_hour, fast.cap), (D("9"), None))
        self.assertTrue(self.audited("AURA_CREATED"))

        # One aura for two communities; a community has one.
        response = self.client.post(reverse("admin:discipline_communityaura_add"), {
            "community": str(self.guild.pk), "aura": str(fast.pk)})
        self.assertEqual(response.status_code, 302, response.content.decode()[:600])
        link = CommunityAura.objects.get(community=self.club)
        self.client.post(reverse("admin:discipline_communityaura_change", args=[link.pk]),
                         {"aura": str(fast.pk)})
        self.assertEqual(set(CommunityAura.objects.filter(aura=fast)
                             .values_list("community__slug", flat=True)), {"club", "guild"})
        self.assertEqual(services.speed_for(self.mia).rate, D("9"))
        self.assertTrue(self.audited("AURA_SET"))
        again = self.client.post(reverse("admin:discipline_communityaura_add"), {
            "community": str(self.club.pk), "aura": str(self.common.pk)})
        self.assertEqual(again.status_code, 200)                    # refused: the form comes back
        self.assertEqual(CommunityAura.objects.filter(community=self.club).count(), 1)

        self.client.post(reverse("admin:discipline_aura_change", args=[fast.pk]), {
            "name": "Fast aura", "description": "", "regen_per_hour": "3", "cap": "25"})
        speed = services.speed_for(self.mia)
        self.assertEqual((speed.rate, speed.cap), (D("3"), D("25")))
        self.assertEqual(Aura.objects.get(pk=fast.pk).slug, "fast-aura")   # a slug is given once
        self.assertTrue(self.audited("AURA_CHANGED"))

        self.client.post(reverse("admin:discipline_communityaura_delete", args=[link.pk]),
                         {"post": "yes"})
        self.assertFalse(CommunityAura.objects.filter(community=self.club).exists())
        self.assertEqual(services.speed_for(self.mia).rate, 0)

    def test_deleting_is_recorded_and_takes_the_history_and_the_links(self):
        held = sigils.create("Held", 1, by=self.root)
        sigils.assign(held, self.mia, by=self.root)
        response = self.client.post(reverse("admin:discipline_sigil_delete", args=[held.pk]),
                                    {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Sigil.objects.filter(pk=held.pk).exists())
        self.assertFalse(SigilEvent.objects.exists())
        self.assertEqual(services.speed_for(self.mia).rate, D("4"))
        self.assertTrue(self.audited("SIGIL_DELETED"))

        response = self.client.post(reverse("admin:discipline_aura_delete", args=[self.common.pk]),
                                    {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Aura.objects.exists())
        self.assertFalse(CommunityAura.objects.exists())
        self.assertEqual(services.speed_for(self.mia).rate, 0)
        self.assertTrue(self.audited("AURA_DELETED"))
        if apps.is_installed("toto.audit"):
            from toto.audit.models import AuditRecord

            lost = AuditRecord.objects.filter(action="DISCIPLINE.AURA_SET").latest("pk")
            self.assertEqual((lost.metadata["community"], lost.metadata["before"],
                              lost.metadata["after"]), ("club", "common", None))

    def test_a_picture_larger_than_a_megabyte_is_refused(self):
        from PIL import Image

        data = io.BytesIO()
        Image.effect_noise((900, 900), 90).convert("RGB").save(data, "PNG")
        self.assertGreater(len(data.getvalue()), 1024 * 1024)
        big = SimpleUploadedFile("big.png", data.getvalue(), content_type="image/png")
        response = self.client.post(self.add_sigil, self.sigil_data(image=big))
        self.assertEqual(response.status_code, 200)                 # the form comes back
        self.assertFalse(Sigil.objects.exists())

    def test_the_admin_of_discipline_is_a_superusers_whatever_the_permissions(self):
        from django.contrib.auth.models import Permission

        self.staff.user_permissions.set(Permission.objects.filter(
            content_type__app_label="discipline"))
        staff = User.objects.get(pk=self.staff.pk)                  # a fresh permission cache
        self.assertTrue(staff.has_perm("discipline.add_sigil"))
        self.client.force_login(staff)
        lantern = sigils.create("Lantern", 2, by=self.root)
        for url in (self.add_sigil, self.change(lantern),
                    reverse("admin:discipline_sigil_changelist"),
                    reverse("admin:discipline_aura_add"),
                    reverse("admin:discipline_communityaura_add"),
                    reverse("admin:discipline_manaaccount_changelist"),
                    reverse("admin:discipline_manaevent_changelist")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.client.post(self.add_sigil, self.sigil_data(name="Theirs"))
        self.client.post(self.change(lantern), self.sigil_data(holder=str(staff.pk)))
        self.assertFalse(Sigil.objects.filter(name="Theirs").exists())
        lantern.refresh_from_db()
        self.assertIsNone(lantern.holder)

    def test_balances_and_events_are_read_and_never_written_there(self):
        account = ManaAccount.objects.get(user=self.mia)
        self.assertEqual(self.client.get(reverse("admin:discipline_manaaccount_changelist"))
                         .status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:discipline_manaaccount_add")).status_code,
                         403)
        self.client.post(reverse("admin:discipline_manaaccount_change", args=[account.pk]),
                         {"user": self.mia.pk, "balance": "9999"})
        self.assertEqual(services.balance(self.mia), D("10"))


class PluginTests(Fixture):
    def test_a_profile_shows_its_owners_mana_and_nobody_elses(self):
        sigils.assign(sigils.create("Lantern", 2, by=self.root), self.mia, by=self.root)
        try:
            page = reverse("socialhub:profile_details", kwargs={"slug": self.mia.community_profile.slug})
        except NoReverseMatch:
            self.skipTest("this host mounts no profile pages")
        body = self.as_(self.mia).get(page).content.decode()
        for mark in ('data-testid="mana-profile"', 'data-testid="mana-profile-bar"',
                     'data-testid="mana-profile-aura"', 'data-testid="mana-profile-sigil"',
                     'data-testid="mana-profile-amount">10<', 'style="width: 17%"'):
            self.assertIn(mark, body)
        self.assertIn(f'href="{reverse("discipline:index")}"', body)
        for other in (self.staff, self.root):
            self.assertNotIn('data-testid="mana-profile"',
                             self.as_(other).get(page).content.decode(), other.username)

    def test_the_directory_names_each_communitys_aura(self):
        from toto.socialhub.plugins.community_plugins import CommunityListPlugin

        self.assertIn("discipline_aura", CommunityListPlugin.registry)
        columns = CommunityListPlugin.columns_for([self.club, self.guild])
        self.assertEqual([(column["key"], str(column["title"])) for column in columns
                          if column["key"] == "discipline_aura"], [("discipline_aura", "Aura")])
        cells = next(column["cells"] for column in columns if column["key"] == "discipline_aura")
        self.assertEqual(set(cells), {self.club.pk})                # the guild has none
        try:
            page = reverse("socialhub:community_list")
        except NoReverseMatch:
            self.skipTest("this host mounts no community directory")
        response = self.as_(self.mia).get(page)
        body = response.content.decode()
        self.assertIn('data-testid="directory-column-discipline_aura"', body)
        by_slug = {community.slug: [cell["text"] for cell in community.directory_cells
                                    if cell["title"] == "Aura"]
                   for community in response.context["communities"]}
        self.assertEqual(by_slug["guild"], [""])
        self.assertTrue(by_slug["club"][0].startswith("Common"))
        self.assertIn(by_slug["club"][0], body)

    def test_a_column_that_fails_leaves_the_directory_standing(self):
        from unittest import mock

        from toto.socialhub.plugins.community_plugins import CommunityListPlugin

        from .plugins.community_plugins import AuraColumn

        with mock.patch.object(AuraColumn, "cells", side_effect=RuntimeError("no")), \
                self.assertLogs("toto.socialhub", level="ERROR"):
            columns = CommunityListPlugin.columns_for([self.club])
        self.assertNotIn("discipline_aura", [column["key"] for column in columns])


class PersonalDataTests(Fixture):
    def test_the_copy_holds_the_persons_mana_and_sigils_and_nobody_elses(self):
        from toto.core import personal_data

        lantern = sigils.create("Lantern", 2, by=self.root)
        sigils.assign(lantern, self.mia, by=self.root)
        services.grant(self.mia, 5, reason="a gift", by=self.root)
        services.grant(self.staff, 7, reason="theirs", by=self.root)
        personal_data.plugins()
        tables = {table.name: table.rows
                  for table in personal_data.PersonalDataPlugin.get("discipline").tables(self.mia)}
        self.assertEqual(tables["discipline_mana"][0]["mana"], "15.0000")
        self.assertEqual([row["kind"] for row in tables["discipline_mana_changes"]],
                         ["signup", "grant"])
        self.assertEqual(tables["discipline_mana_changes"][1]["reason"], "a gift")
        self.assertEqual([row["sigil"] for row in tables["discipline_sigils"]],
                         [str(lantern.uuid)])
        self.assertEqual([row["what"] for row in tables["discipline_sigil_history"]],
                         ["assigned"])

    def test_erasing_a_person_erases_their_mana(self):
        services.grant(self.mia, 5, by=self.root)
        pk = self.mia.pk
        self.mia.delete()
        self.assertFalse(ManaAccount.objects.filter(user_id=pk).exists())
        self.assertFalse(ManaEvent.objects.filter(user_id=pk).exists())
        # What the administrator did stays in nobody's name.
        self.assertFalse(ManaEvent.objects.filter(by=self.root).exclude(user=self.root).exists())
