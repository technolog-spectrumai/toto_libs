"""Members choose whether other members see their e-mail address and phone
(2026-10-01, 37c.25).

Every signed-in member saw every other member's e-mail address — on the
profile, in the roster and in the data-mesh org chart — with no way to hide
it, and the phone number the same way. Both are now off by default
(``Person.show_email``, ``Person.show_phone``), switched on on the Edit
profile tab of the member's own profile (My account until stage 50);
the member always sees their own and an administrator (a superuser on the
Superuser plan) keeps seeing both. The org chart, which its caller's desktop
client copies on to peers, carries only what the member shows.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_contact_visibility
"""

import io
import re

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

User = get_user_model()

EMAIL = "quiet.member@example.org"
PHONE = "+48 600 700 800"
OPEN_EMAIL = "open.member@example.org"
OPEN_PHONE = "+48 111 222 333"
HIDDEN_NOTE = "Hidden from other members."


class ContactCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.quiet_user = User.objects.create_user("quiet", EMAIL, "pw")
        cls.quiet = Person.objects.create(user=cls.quiet_user, display_name="Quiet",
                                          email=EMAIL, phone=PHONE)
        cls.open_user = User.objects.create_user("open", OPEN_EMAIL, "pw")
        cls.open = Person.objects.create(user=cls.open_user, display_name="Open",
                                         email=OPEN_EMAIL, phone=OPEN_PHONE,
                                         show_email=True, show_phone=True)
        cls.other_user = User.objects.create_user("other", "other@example.org", "pw")
        cls.other = Person.objects.create(user=cls.other_user, display_name="Other")
        cls.root = User.objects.create_superuser("root", "root@example.org", "pw")
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())   # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare = User.objects.create_superuser("bare", "bare@example.org", "pw")
        cls.guild = Community.objects.create(name="Guild", slug="guild")
        cls.guild.members.add(cls.quiet, cls.open, cls.other)

    def profile(self, viewer, person):
        self.client.force_login(viewer)
        response = self.client.get(reverse("socialhub:profile_details", args=[person.slug]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def roster(self, viewer):
        self.client.force_login(viewer)
        response = self.client.get(reverse("socialhub:profile_list"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def org_chart(self, viewer):
        self.client.force_login(add_to_mesh(viewer))
        response = self.client.get(
            reverse("socialhub:api_community_org_chart", args=[self.guild.slug]))
        self.assertEqual(response.status_code, 200)
        return {node["slug"]: node for node in response.json()["nodes"]}


class DefaultTests(TestCase):
    def test_both_are_off_for_a_new_person(self):
        person = Person.objects.create(display_name="Fresh", email="fresh@example.org",
                                       phone="123")
        person.refresh_from_db()
        self.assertEqual((person.show_email, person.show_phone), (False, False))


class ProfilePageTests(ContactCase):
    def test_another_member_sees_neither_while_they_are_off(self):
        html = self.profile(self.other_user, self.quiet)
        self.assertNotIn(EMAIL, html)
        self.assertNotIn(PHONE, html)
        self.assertNotIn(HIDDEN_NOTE, html)

    def test_another_member_sees_what_the_member_shows(self):
        html = self.profile(self.other_user, self.open)
        self.assertIn(OPEN_EMAIL, html)
        self.assertIn(OPEN_PHONE, html)
        self.assertNotIn(HIDDEN_NOTE, html)

    def test_one_switch_does_not_open_the_other(self):
        Person.objects.filter(pk=self.quiet.pk).update(show_phone=True)
        html = self.profile(self.other_user, self.quiet)
        self.assertIn(PHONE, html)
        self.assertNotIn(EMAIL, html)

    def test_the_member_sees_their_own_and_is_told_others_do_not(self):
        html = self.profile(self.quiet_user, self.quiet)
        self.assertIn(EMAIL, html)
        self.assertIn(PHONE, html)
        # E-mail, phone and (since 2026-10-04) the postal address.
        self.assertEqual(html.count(HIDDEN_NOTE), 3)

    def test_an_administrator_keeps_seeing_both(self):
        html = self.profile(self.root, self.quiet)
        self.assertIn(EMAIL, html)
        self.assertIn(PHONE, html)

    def test_a_superuser_without_the_plan_does_not(self):
        if not apps.is_installed("toto.subscriptions"):
            self.skipTest("this host sells no Superuser plan")
        html = self.profile(self.bare, self.quiet)
        self.assertNotIn(EMAIL, html)
        self.assertNotIn(PHONE, html)


class RosterTests(ContactCase):
    def test_the_roster_shows_only_the_addresses_members_show(self):
        html = self.roster(self.other_user)
        self.assertIn(OPEN_EMAIL, html)
        self.assertNotIn(EMAIL, html)

    def test_the_member_finds_their_own_there(self):
        self.assertIn(EMAIL, self.roster(self.quiet_user))

    def test_an_administrator_sees_every_address(self):
        html = self.roster(self.root)
        self.assertIn(EMAIL, html)
        self.assertIn(OPEN_EMAIL, html)


class OrgChartTests(ContactCase):
    def test_a_node_carries_only_what_its_member_shows(self):
        nodes = self.org_chart(self.other_user)
        self.assertEqual((nodes[self.quiet.slug]["email"], nodes[self.quiet.slug]["phone"]),
                         ("", ""))
        self.assertEqual((nodes[self.open.slug]["email"], nodes[self.open.slug]["phone"]),
                         (OPEN_EMAIL, OPEN_PHONE))

    def test_not_even_to_the_member_or_an_administrator(self):
        """The desktop client copies this answer on to peers."""
        for viewer in (self.quiet_user, self.root):
            with self.subTest(viewer=viewer.username):
                node = self.org_chart(viewer)[self.quiet.slug]
                self.assertEqual((node["email"], node["phone"]), ("", ""))

    def test_the_page_s_own_org_chart_carries_no_contact_at_all(self):
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("socialhub:community_org_chart_data_by_slug",
                                           args=[self.guild.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(EMAIL, response.content.decode())
        self.assertNotIn(OPEN_EMAIL, response.content.decode())


class MyAccountTests(ContactCase):
    def post_profile(self, **switches):
        self.client.force_login(self.quiet_user)
        return self.client.post(reverse("account:profile"), {
            "display_name": "Quiet", "bio": "", "phone": PHONE,
            "show_online": "on", **switches})

    def test_edit_your_profile_offers_both_switches_off_with_a_sentence(self):
        self.client.force_login(self.quiet_user)
        # Edit profile, a tab of the member's own profile (stage 50).
        response = self.client.get(reverse("socialhub:profile_details", args=[self.quiet.slug])
                                   + "?tab=edit")
        html = response.content.decode()
        for name in ("show_email", "show_phone", "show_address"):
            with self.subTest(switch=name):
                tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
                self.assertIsNotNone(tag)
                self.assertIn('type="checkbox"', tag.group(0))
                self.assertNotIn("checked", tag.group(0))
        self.assertContains(response, "When this is off, only you and the administrators "
                                      "see your e-mail address.")

    def test_switching_the_address_on_shows_it_and_off_hides_it_again(self):
        response = self.post_profile(show_email="on")
        self.assertRedirects(response, reverse("socialhub:profile_details", args=[self.quiet.slug])
                             + "?tab=edit#profile", fetch_redirect_response=False)
        self.quiet.refresh_from_db()
        self.assertEqual((self.quiet.show_email, self.quiet.show_phone), (True, False))
        self.assertIn(EMAIL, self.profile(self.other_user, self.quiet))
        self.post_profile()
        self.quiet.refresh_from_db()
        self.assertFalse(self.quiet.show_email)
        self.assertNotIn(EMAIL, self.profile(self.other_user, self.quiet))

    def test_the_change_is_recorded_by_name(self):
        if not apps.is_installed("toto.audit"):
            self.skipTest("no audit chain here")
        from toto.audit.models import AuditRecord

        self.post_profile(show_phone="on")
        record = AuditRecord.objects.filter(action="SOCIALHUB.PROFILE_CHANGED").get()
        self.assertEqual(record.metadata["fields"], ["show_phone"])


class PostalAddressTests(ContactCase):
    """The postal address is text the member types, shown by its own switch
    (2026-10-04): the rule of the e-mail address and the phone number. Until
    then it was a pin on a map with a three-way sharing setting."""

    STREET = "1 Secret Lane"

    def setUp(self):
        Person.objects.filter(pk=self.quiet.pk).update(address=self.STREET + "\nHidden Town")
        self.quiet.refresh_from_db()

    def shows(self, viewer) -> bool:
        return self.STREET in self.profile(viewer, self.quiet)

    def test_it_is_off_by_default_and_hidden_from_another_member(self):
        self.assertFalse(Person._meta.get_field("show_address").default)
        self.assertFalse(self.shows(self.other_user))
        self.assertTrue(self.shows(self.quiet_user))
        self.assertTrue(self.shows(self.root))

    def test_switched_on_it_shows_to_another_member(self):
        Person.objects.filter(pk=self.quiet.pk).update(show_address=True)
        self.assertTrue(self.shows(self.other_user))

    def test_the_rule_is_the_contact_rule(self):
        from toto.socialhub.contact_access import may_see_address, shown_address

        self.assertTrue(may_see_address(self.quiet_user, self.quiet))
        self.assertFalse(may_see_address(self.other_user, self.quiet))
        self.assertFalse(may_see_address(self.quiet_user, self.quiet, passed_on=True))
        self.assertEqual(shown_address(self.other_user, self.quiet), "")

    def test_the_profile_form_saves_the_text_and_the_switch(self):
        self.client.force_login(self.quiet_user)
        response = self.client.post(reverse("account:profile"), {
            "display_name": "Quiet", "bio": "", "phone": "",
            "address": "2 New Road", "show_address": "on"})
        self.assertEqual(response.status_code, 302)
        self.quiet.refresh_from_db()
        self.assertEqual((self.quiet.address, self.quiet.show_address), ("2 New Road", True))

    def test_the_address_is_a_text_column_and_nothing_else(self):
        from django.db import models

        self.assertIsInstance(Person._meta.get_field("address"), models.TextField)
        self.assertFalse(hasattr(Person, "location_sharing"))
