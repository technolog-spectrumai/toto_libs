"""Your account, the recent sign-ins section (2026-09-30; on the own
profile's Security tab since 2026-10-02, stage 50): the member's own
``AUTH.*`` records of the last 30 days, read through
``toto.audit.queries.member_auth_records`` — own records only, guesses at
their name included, another member's never — paged on ``?signins_page=``.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from toto.audit import identity
from toto.audit.models import AuditRecord
from toto.audit.queries import member_auth_records
from toto.audit.services import record
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.views.account import SIGNINS_PER_PAGE

User = get_user_model()
PW = "Correct-horse-9"


class SigninsTestCase(TestCase):
    def setUp(self):
        cache.clear()
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", "ada@example.test", PW)
        self.other = User.objects.create_user("bob", "bob@example.test", PW)
        person = Person.objects.create(user=self.user, display_name="Ada")
        self.profile_url = reverse("socialhub:profile_details", args=[person.slug])
        self.client.force_login(self.user)

    def page(self, query=""):
        """The Security tab, by the way old addresses come (/account/):
        ``query`` is what such an address carried."""
        response = self.client.get(reverse("account:home") + (query or "?tab=security"),
                                   follow=True)
        self.assertEqual(response.status_code, 200)
        return response, response.context["signins_page"].rows

    def fail(self, username, address="198.51.100.9"):
        request = RequestFactory().post("/accounts/login/", REMOTE_ADDR=address,
                                        HTTP_USER_AGENT="Guesser/1.0")
        self.assertIsNone(authenticate(request, username=username, password="wrong"))


class QueryTests(SigninsTestCase):
    def test_own_records_only(self):
        from django.test import Client

        Client().force_login(self.other)
        identity.on_password_changed(self.other, sessions_ended=2)
        self.fail("bob")
        mine = member_auth_records(self.user)
        self.assertTrue(mine.filter(action="AUTH.LOGIN").exists())
        for row in mine:
            self.assertTrue(row.actor_user_id == self.user.pk
                            or row.object_id == str(self.user.pk)
                            or row.object_description.lower() in ("ada", "ada@example.test"),
                            row.action)
        self.assertFalse(mine.filter(actor_user=self.other).exists())
        self.assertFalse(mine.filter(object_id=str(self.other.pk)).exists())
        self.assertFalse(mine.filter(object_description="bob").exists())

    def test_failed_attempts_against_my_name_or_address_are_mine(self):
        self.fail("ada")
        self.fail("ADA")
        self.fail("ada@example.test")
        self.fail("adam")
        failed = member_auth_records(self.user).filter(action="AUTH.LOGIN_FAILED")
        self.assertEqual(failed.count(), 3)
        self.assertFalse(failed.filter(object_description="adam").exists())

    def test_a_pause_naming_me_is_mine_a_whole_address_pause_is_not(self):
        identity.on_signin_locked(scope="account_address", address="198.51.100.9",
                                  failures=5, minutes=15, username="ada")
        identity.on_signin_locked(scope="address", address="198.51.100.10",
                                  failures=50, minutes=60)
        locked = member_auth_records(self.user).filter(action="AUTH.LOCKED")
        self.assertEqual(locked.count(), 1)
        self.assertEqual(locked.get().metadata["scope"], "account_address")

    def test_only_the_last_thirty_days(self):
        record("auth.login", app_label="auth", object_type="auth.user",
               object_id=str(self.user.pk), description="ada", actor_user=self.user,
               timestamp=timezone.now() - timedelta(days=31))
        old = timezone.now() - timedelta(days=30, minutes=1)
        self.assertFalse(member_auth_records(self.user).filter(timestamp__lt=old).exists())
        self.assertTrue(AuditRecord.objects.filter(timestamp__lt=old).exists())

    def test_only_auth_records(self):
        record("socialhub.profile_changed", app_label="socialhub", actor_user=self.user)
        self.assertFalse(member_auth_records(self.user)
                         .exclude(action__startswith="AUTH.").exists())

    def test_nobody_has_none(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(member_auth_records(AnonymousUser()).exists())


class PageTests(SigninsTestCase):
    def test_the_section_shows_my_failed_attempts_not_bobs(self):
        self.fail("ada", address="203.0.113.5")
        self.fail("bob", address="203.0.113.6")
        response, rows = self.page()
        failed = [row for row in rows if row["action"] == "AUTH.LOGIN_FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["address"], "203.0.113.5")
        self.assertEqual(failed[0]["user_agent"], "Guesser/1.0")
        self.assertFalse(failed[0]["success"])
        self.assertContains(response, "Failed sign-in")
        self.assertContains(response, "203.0.113.5")
        self.assertNotContains(response, "203.0.113.6")

    def test_a_staff_change_hides_the_staff_members_address(self):
        admin = User.objects.create_superuser("root", "root@example.test", PW)
        request = RequestFactory().post("/admin/", REMOTE_ADDR="192.0.2.77",
                                        HTTP_USER_AGENT="AdminBrowser/9")
        record("auth.staff_granted", app_label="auth", object_type="auth.user",
               object_id=str(self.user.pk), description="ada", actor_user=admin,
               request=request)
        response, rows = self.page()
        row = next(row for row in rows if row["action"] == "AUTH.STAFF_GRANTED")
        self.assertTrue(row["by_other"])
        self.assertEqual(row["address"], "")
        self.assertNotContains(response, "192.0.2.77")
        self.assertNotContains(response, "AdminBrowser/9")

    def test_times_are_in_my_time_zone(self):
        Person.objects.filter(user=self.user).update(timezone="Pacific/Kiritimati")
        when = timezone.now().replace(second=0, microsecond=0)
        record("auth.password_changed", app_label="auth", object_type="auth.user",
               object_id=str(self.user.pk), description="ada", actor_user=self.user,
               timestamp=when)
        response, _ = self.page()
        from zoneinfo import ZoneInfo

        local = when.astimezone(ZoneInfo("Pacific/Kiritimati")).strftime("%Y-%m-%d %H:%M")
        self.assertContains(response, local)

    def test_paged_on_its_own_parameter(self):
        for _ in range(SIGNINS_PER_PAGE + 3):
            identity.on_signed_out_everywhere(self.user, sessions_ended=0)
        total = member_auth_records(self.user).count()
        response, rows = self.page()
        self.assertEqual(len(rows), SIGNINS_PER_PAGE)
        self.assertContains(response, "?signins_page=2")
        # The link is the page's own address and keeps the tab (stage 50).
        self.assertContains(
            response, f'href="{self.profile_url}?signins_page=2&amp;tab=security#signins"')
        # The Sessions list's ?page= does not move this one; either page on
        # /account/ means the Security tab.
        _, rows = self.page("?page=2")
        self.assertEqual(len(rows), SIGNINS_PER_PAGE)
        _, rows = self.page("?signins_page=2")
        self.assertEqual(len(rows), total - SIGNINS_PER_PAGE)

    def test_the_other_members_page_is_not_reachable_by_any_parameter(self):
        # No view takes a user: the section is always request.user's.
        self.fail("bob", address="203.0.113.99")
        response, _ = self.page("?tab=security&user=%d&username=bob" % self.other.pk)
        self.assertNotContains(response, "203.0.113.99")
        # /account/ carries none of it on to the page.
        landed = self.client.get(reverse("account:home")
                                 + "?tab=security&user=%d&username=bob" % self.other.pk)
        self.assertEqual(landed["Location"], self.profile_url + "?tab=security")

    def test_audit_pages_stay_staff_only(self):
        response = self.client.get(reverse("audit:index"))
        self.assertNotEqual(response.status_code, 200)


class PaginationPartialTests(TestCase):
    def test_default_parameter_is_page(self):
        from django.core.paginator import Paginator
        from django.template.loader import render_to_string

        page = Paginator(list(range(30)), 10).get_page(2)
        html = render_to_string("oya/partials/_server_pagination.html",
                                {"page_obj": page, "is_paginated": True, "extra_query": "&a=1"})
        self.assertIn("?page=1&amp;a=1", html)
        self.assertIn("?page=3&amp;a=1", html)
        html = render_to_string("oya/partials/_server_pagination.html",
                                {"page_obj": page, "is_paginated": True,
                                 "page_param": "signins_page"})
        self.assertIn("?signins_page=3", html)
        self.assertNotIn("?page=", html)

    def test_a_base_url_goes_before_each_link(self):
        """Drawn again at a door's address, a bare "?page=2" would be a GET on
        the door (2026-10-02)."""
        from django.core.paginator import Paginator
        from django.template.loader import render_to_string

        page = Paginator(list(range(30)), 10).get_page(2)
        html = render_to_string("oya/partials/_server_pagination.html",
                                {"page_obj": page, "is_paginated": True,
                                 "base_url": "/socialhub/profiles/ada/",
                                 "extra_query": "&tab=security"})
        self.assertIn('href="/socialhub/profiles/ada/?page=1&amp;tab=security"', html)
        self.assertIn('href="/socialhub/profiles/ada/?page=3&amp;tab=security"', html)
        self.assertNotIn('href="?', html)

