"""The profile in tabs (2026-10-02, stage 50; the owner: "merge My account and
my profile - use tabs in profile view", then "the design of the profile view
is not good - use tabs - separate it logically").

Its owner gets seven tabs, one concern each — Overview, Edit profile,
Account, Security, Wallet, Activity, Your data — and anybody else the public
three — Overview, Communities, Activity: another member, staff, a superuser
with or without the Superuser plan alike, whatever the address asks for, and
nothing of the owner's account is built for them. A tab is a link
(``?tab=…``) drawn on the server, one per request; an unknown one, or one the
viewer is not shown, is the Overview. ``/account/`` leads there, carrying the
tab and the Security lists' pages and nothing else, or draws the page itself
for an account with no profile to go to — Edit profile first. Every door goes
back to its own tab; a form with errors is drawn again on its tab, its links
leading to the page and not to the door. The profile plugins sit on the tab
they name, and a tab with nothing to show is left off the strip.

    manage.py test toto.socialhub.tests_profile_tabs
"""

from __future__ import annotations

import io
import json
import re
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.safestring import mark_safe

from toto.core.models import Platform
from toto.locations.models import Address
from toto.people.models import LocationSharing, Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.profile_plugins import ProfilePlugin
from toto.socialhub.views import account as account_views
from toto.socialhub.views.account import (NO_PROFILE_TABS, OWN_TABS, TABS, VISITOR_TABS,
                                          own_page_url, page_number, tab_from)

User = get_user_model()
PW = "Correct-horse-9"

#: What only the owner's own account tabs show. None of it may be on anybody
#: else's page, whatever ``?tab=`` says.
PRIVATE = (
    'action="/account/',               # any of the account's doors
    'name="old_password"', 'name="new_email"', 'name="passphrase"',
    'id="sessions"', 'id="signins"', "this device",
    'id="keystore"', 'id="data"', 'id="erasure"', 'data-testid="erasure-confirm"',
    'id="profile-section-tabs"', 'id="where-you-live"', "address-pick-map",
    'id="references"', 'name="show_email"',
)

#: A tab on the strip: its address, key and whether it is the one shown.
TAB = re.compile(r'<a href="([^"]*)" id="profile-tab-(\w+)"\s*(aria-current="page")?')

GEOCODING_OFF = {"enabled": False}

#: A form's CSRF token, different on every page drawn.
CSRF = re.compile(r'name="csrfmiddlewaretoken" value="[^"]*"')


def strip(url, *keys, current=""):
    """The strip as ``TabsCase.tabs`` reads it: each key's address on
    ``url`` (the first key's is the page's own) and the current one."""
    return [(url if i == 0 else f"{url}?tab={key}", key,
             'aria-current="page"' if key == (current or keys[0]) else "")
            for i, key in enumerate(keys)]


@override_settings(LOCATIONS_GEOCODING=GEOCODING_OFF)
class TabsCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.ada_user = User.objects.create_user("ada", "ada@example.test", PW)
        cls.ada = Person.objects.create(user=cls.ada_user, display_name="Ada",
                                        email="ada@example.test")
        cls.guild = Community.objects.create(name="Guild of Ada")
        cls.ada.communities.add(cls.guild)
        cls.bob_user = User.objects.create_user("bob", "bob@example.test", PW)
        cls.bob = Person.objects.create(user=cls.bob_user, display_name="Bob")
        cls.staff = User.objects.create_user("staffer", "staff@example.test", PW, is_staff=True)
        cls.root = User.objects.create_superuser("root", "root@example.test", PW)
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())   # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare = User.objects.create_superuser("bare", "bare@example.test", PW)

    def setUp(self):
        self.url = reverse("socialhub:profile_details", args=[self.ada.slug])

    def get(self, user, query="", url=None):
        self.client.force_login(user)
        return self.client.get((url or self.url) + query)

    def tabs(self, response):
        return TAB.findall(response.content.decode())

    def current(self, response):
        return [key for _, key, chosen in self.tabs(response) if chosen]

    @staticmethod
    def has_wallet():
        """A member's Wallet tab shows where a plugin sits on it (the
        economy's mana and wallet)."""
        return bool(ProfilePlugin.on_tab("wallet"))

    @staticmethod
    def visitors_activity():
        """A visitor's Activity tab shows where a plugin there shows to anyone
        (the upcoming events)."""
        return any(not plugin.show_for_owner_only for plugin in ProfilePlugin.on_tab("activity"))

    def own_keys(self):
        return [tab for tab in OWN_TABS if tab != "wallet" or self.has_wallet()]

    def visitor_keys(self):
        return [tab for tab in VISITOR_TABS if tab != "activity" or self.visitors_activity()]

    def visitors(self):
        visitors = [("another member", self.bob_user), ("staff", self.staff),
                    ("a superuser on the plan", self.root)]
        if apps.is_installed("toto.subscriptions"):
            visitors.append(("a superuser without the plan", self.bare))
        return visitors


class OwnerTests(TabsCase):
    def test_the_owner_has_seven_tabs_the_overview_shown(self):
        response = self.get(self.ada_user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.tabs(response), strip(self.url, *self.own_keys()))
        self.assertContains(response, '<nav class="flex flex-wrap gap-2" aria-label="Profile" '
                                      'data-testid="profile-tabs">')
        self.assertContains(response, 'id="profile-panel" data-tab="overview"')
        # What others see, and the owner's communities with it.
        self.assertContains(response, "Member since")
        self.assertContains(response, 'id="communities"')
        self.assertContains(response, "Guild of Ada")
        # Nothing of any other tab: no form, no map.
        self.assertNotContains(response, 'action="/account/')
        self.assertNotContains(response, "vendor/leaflet/leaflet.js")
        self.assertNotContains(response, 'id="where-you-live"')
        self.assertNotContains(response, 'id="references"')

    def test_each_tab_draws_its_own_sections_and_no_other(self):
        sections = {
            "overview": ['id="communities"', "Member since"],
            "edit": ['id="profile"', 'action="/account/profile/"', 'name="show_email"',
                     'id="where-you-live"', 'name="tab" value="edit"',
                     "vendor/leaflet/leaflet.js", "address-pick-map"],
            "account": ['id="email"', 'id="password"', 'id="timezone"', 'id="language"',
                        'action="/account/email/"', 'action="/account/password/"',
                        'action="/account/timezone/"', 'name="tab" value="account"'],
            "security": ['id="sessions"', 'action="/account/sessions/end-others/"',
                         "this device"],
            "activity": ['id="references"', "My Reference Requests"],
            "data": ['id="data"', 'id="erasure"', 'action="/account/data-export/"',
                     'data-testid="erasure-confirm"'],
        }
        if apps.is_installed("toto.audit"):
            sections["security"].append('id="signins"')
        if apps.is_installed("toto.gervazy"):
            sections["security"] += ['id="keystore"', 'action="/account/key-store/"',
                                     reverse("gervazy:my_keys")]
        if ProfilePlugin.get("upcoming_events") is not None:
            sections["activity"].append('id="upcoming-events"')
        everyone = {marker for markers in sections.values() for marker in markers}
        for tab, markers in sections.items():
            with self.subTest(tab=tab):
                response = self.get(self.ada_user, "" if tab == "overview" else f"?tab={tab}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.current(response), [tab])
                self.assertContains(response, f'id="profile-panel" data-tab="{tab}"')
                html = response.content.decode()
                for marker in markers:
                    self.assertIn(marker, html)
                for marker in everyone - set(markers):
                    self.assertNotIn(marker, html)

    def test_the_wallet_tab_holds_the_wallet_plugins(self):
        if not self.has_wallet():
            self.skipTest("no plugin sits on the Wallet tab on this host")
        response = self.get(self.ada_user, "?tab=wallet")
        self.assertEqual(self.current(response), ["wallet"])
        self.assertContains(response, 'id="profile-panel" data-tab="wallet"')
        for marker in ('action="/account/', 'id="communities"', 'id="references"'):
            self.assertNotContains(response, marker)

    def test_a_tab_names_the_page(self):
        self.assertContains(self.get(self.ada_user, "?tab=security"),
                            "<title>Ada · Security</title>")
        self.assertContains(self.get(self.ada_user, "?tab=edit"),
                            "<title>Ada · Edit profile</title>")
        self.assertContains(self.get(self.ada_user), "<title>Ada</title>")

    def test_an_unknown_tab_is_the_overview(self):
        # "communities" is a visitor's tab: the owner has it on the Overview.
        for query in ("?tab=nonsense", "?tab=", "?tab=ACCOUNT", "?tab=account%00",
                      "?tab=communities", "?tab=overview"):
            with self.subTest(query=query):
                response = self.get(self.ada_user, query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.current(response), ["overview"])

    def test_the_tabs_are_kept_by_no_cache(self):
        for query in ("", "?tab=security", "?tab=edit"):
            with self.subTest(query=query):
                response = self.get(self.ada_user, query)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertIn("private", response["Cache-Control"])

    def test_old_section_addresses_have_their_tab(self):
        """/account/#sessions keeps its #sessions across the redirect; the
        page's script takes it to the tab the section is on."""
        response = self.get(self.ada_user)
        found = re.search(r'<script id="profile-section-tabs" type="application/json">(.*?)</script>',
                          response.content.decode())
        self.assertIsNotNone(found)
        homes = json.loads(found.group(1))
        self.assertEqual(homes["sessions"], "security")
        self.assertEqual(homes["password"], "account")
        self.assertEqual(homes["erasure"], "data")
        self.assertEqual(homes["profile"], "edit")
        self.assertEqual(homes["where-you-live"], "edit")
        self.assertEqual(homes["references"], "activity")
        self.assertTrue(set(homes.values()) <= set(OWN_TABS))

    def test_the_overview_marks_what_others_do_not_see(self):
        Person.objects.filter(pk=self.ada.pk).update(
            phone="+48 600", show_phone=True,
            address=Address.objects.create(street="Sekretna 1", locality_name="Town",
                                           latitude=52.2, longitude=21.0))
        html = self.get(self.ada_user).content.decode()
        # The e-mail address is hidden (the default), the phone shown, and the
        # address shared with nobody (the default).
        self.assertEqual(html.count("Hidden from other members."), 2)
        self.assertIn("ada@example.test", html)
        self.assertIn("+48 600", html)
        self.assertIn("Sekretna 1", html)
        Person.objects.filter(pk=self.ada.pk).update(
            location_sharing=LocationSharing.APPROXIMATE)
        html = self.get(self.ada_user).content.decode()
        self.assertEqual(html.count("Hidden from other members."), 1)
        self.assertIn("Other members see the area only: Town.", html)
        self.assertIn("Sekretna 1", html)


class VisitorTests(TabsCase):
    def test_a_visitor_has_the_public_tabs(self):
        for label, viewer in self.visitors():
            with self.subTest(viewer=label):
                response = self.get(viewer)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.tabs(response), strip(self.url, *self.visitor_keys()))

    def test_nobody_else_gets_an_account_tab_whatever_the_address_asks(self):
        for label, viewer in self.visitors():
            for query in ("", "?tab=edit", "?tab=account", "?tab=security", "?tab=wallet",
                          "?tab=data", "?tab=security&page=2&signins_page=2"):
                with self.subTest(viewer=label, query=query):
                    response = self.get(viewer, query)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.current(response), ["overview"])
                    html = response.content.decode()
                    for marker in PRIVATE:
                        self.assertNotIn(marker, html)
                    for key in ("edit", "account", "security", "wallet", "data"):
                        self.assertNotIn(f'id="profile-tab-{key}"', html)
                    # ...and the public page, as before stage 50.
                    self.assertIn("Ada", html)
                    self.assertIn("Member since", html)
                    self.assertNotIn("vendor/leaflet/leaflet.js", html)

    def test_nothing_of_the_owners_account_is_built_for_them(self):
        refuse = mock.Mock(side_effect=AssertionError("an account tab built for a visitor"))
        with mock.patch.object(account_views, "tab_context", refuse), \
                mock.patch.object(account_views, "_sessions_page", refuse), \
                mock.patch.object(account_views, "_data_export", refuse):
            for label, viewer in self.visitors():
                for tab in TABS:
                    with self.subTest(viewer=label, tab=tab):
                        self.assertEqual(self.get(viewer, f"?tab={tab}").status_code, 200)
        refuse.assert_not_called()

    def test_the_communities_tab(self):
        response = self.get(self.bob_user, "?tab=communities")
        self.assertEqual(self.current(response), ["communities"])
        self.assertContains(response, 'id="communities"')
        self.assertContains(response, "Guild of Ada")
        self.assertContains(response, "<title>Ada · Communities</title>")
        # On a visitor's Overview they have a tab of their own instead.
        self.assertNotContains(self.get(self.bob_user), 'id="communities"')

    def test_the_activity_tab_has_no_reference_requests(self):
        if not self.visitors_activity():
            self.skipTest("no Activity tab for a visitor on this host")
        response = self.get(self.bob_user, "?tab=activity")
        self.assertEqual(self.current(response), ["activity"])
        self.assertNotContains(response, 'id="references"')
        self.assertNotContains(response, "My Reference Requests")
        if ProfilePlugin.get("upcoming_events") is not None:
            self.assertContains(response, 'id="upcoming-events"')

    def test_a_visitor_sees_the_same_overview_whatever_the_tab_asked(self):
        def page(query=""):
            html = self.get(self.bob_user, query).content.decode()
            # Each form's token is masked anew for every page.
            return CSRF.sub("", html)

        plain = page()
        for tab in ("edit", "account", "security", "wallet", "data"):
            with self.subTest(tab=tab):
                # The only other difference: the address the language switch keeps.
                self.assertEqual(page(f"?tab={tab}").replace(f"?tab={tab}", ""), plain)


class AccountAddressTests(TabsCase):
    """``/account/``: the way to one's own page, and the old addresses."""

    def location(self, query=""):
        self.client.force_login(self.ada_user)
        response = self.client.get(reverse("account:home") + query)
        self.assertEqual(response.status_code, 302)
        return response["Location"]

    def test_it_leads_to_the_own_profile_on_the_tab_asked_for(self):
        self.assertEqual(self.location(), self.url)
        for tab in ("edit", "account", "security", "wallet", "activity", "data"):
            self.assertEqual(self.location(f"?tab={tab}"), f"{self.url}?tab={tab}")
        self.assertEqual(self.location("?tab=overview"), self.url)

    def test_the_security_lists_pages_are_kept_and_mean_that_tab(self):
        self.assertEqual(self.location("?page=2"), f"{self.url}?tab=security&page=2")
        self.assertEqual(self.location("?signins_page=3"),
                         f"{self.url}?tab=security&signins_page=3")
        self.assertEqual(self.location("?tab=security&page=2&signins_page=3"),
                         f"{self.url}?tab=security&page=2&signins_page=3")
        # Another tab has no lists: its pages are dropped.
        self.assertEqual(self.location("?tab=account&page=2"), f"{self.url}?tab=account")

    def test_nothing_else_is_carried(self):
        for query in ("?next=//evil.example.com/", "?tab=//evil.example.com",
                      "?tab=https://evil.example.com/&next=/x/", "?page=abc",
                      "?page=-1", "?page=1e3", "?page=0", "?signins_page=%2F%2Fevil"):
            with self.subTest(query=query):
                self.assertEqual(self.location(query), self.url)

    def test_signed_out_it_asks_to_sign_in(self):
        response = self.client.get(reverse("account:home") + "?tab=security")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_with_no_profile_the_page_is_drawn_here_edit_profile_first(self):
        loner = User.objects.create_user("loner", "loner@example.test", PW)
        self.client.force_login(loner)
        account = reverse("account:home")
        response = self.client.get(account)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.tabs(response), strip(account, *NO_PROFILE_TABS))
        self.assertContains(response, "Your profile is created when you first save it.")
        self.assertContains(response, 'action="/account/profile/"')
        # Nothing of a profile that is not there: no map, no public tab.
        self.assertNotContains(response, 'id="where-you-live"')
        for key in ("overview", "wallet", "activity", "communities"):
            self.assertNotContains(response, f'id="profile-tab-{key}"')
        response = self.client.get(account + "?tab=account")
        self.assertContains(response, 'action="/account/password/"')
        # No language yet: its door needs a profile.
        self.assertNotContains(response, 'id="language"')
        response = self.client.get(account + "?page=2")
        self.assertEqual(self.current(response), ["security"])
        for query in ("?tab=overview", "?tab=activity", "?tab=wallet"):
            with self.subTest(query=query):
                self.assertEqual(self.current(self.client.get(account + query)), ["edit"])
        self.assertFalse(Person.objects.filter(user=loner).exists())

    def test_a_profile_whose_name_made_no_address_is_drawn_here(self):
        Person.objects.filter(pk=self.ada.pk).update(slug="")
        response = self.get(self.ada_user, url=reverse("account:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.current(response), ["overview"])
        self.assertContains(response, 'href="/account/?tab=security" id="profile-tab-security"')
        response = self.get(self.ada_user, "?tab=edit", url=reverse("account:home"))
        self.assertContains(response, 'id="where-you-live"')


class DoorTests(TabsCase):
    """Each door goes back to its own tab; a form with errors is drawn again
    on its tab, at the door's address, with links to the page."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.ada_user)

    def test_a_wrong_password_is_drawn_again_on_the_account_tab(self):
        response = self.client.post(reverse("account:password"), {
            "old_password": "not-my-password", "new_password1": "Battery-staple-41",
            "new_password2": "Battery-staple-41"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("old_password", response.context["password_form"].errors)
        # The strip leads to the page, never to the door (GET there is 405).
        self.assertEqual(self.tabs(response),
                         strip(self.url, *self.own_keys(), current="account"))
        self.assertNotContains(response, 'href="?', status_code=400)

    def test_a_profile_form_with_errors_is_drawn_on_the_edit_tab(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        not_a_picture = SimpleUploadedFile("me.png", b"<html></html>", content_type="image/png")
        response = self.client.post(reverse("account:profile"), {
            "display_name": "Mallory", "bio": "", "phone": "", "avatar": not_a_picture})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.current(response), ["edit"])
        # The form keeps what was typed; the page shows what is saved.
        self.assertContains(response, 'value="Mallory"', status_code=400)
        self.assertContains(response, "<title>Ada · Edit profile</title>", status_code=400)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.display_name, "Ada")

    def test_a_key_store_form_with_errors_pages_from_the_page(self):
        if not (apps.is_installed("toto.gervazy") and apps.is_installed("toto.audit")):
            self.skipTest("no key store or no sign-ins list here")
        from toto.audit import identity
        from toto.socialhub.views.account import SIGNINS_PER_PAGE

        for _ in range(SIGNINS_PER_PAGE + 1):
            identity.on_signed_out_everywhere(self.ada_user, sessions_ended=0)
        response = self.client.post(reverse("account:key_store"),
                                    {"passphrase": "five5", "passphrase2": "five5"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.current(response), ["security"])
        self.assertContains(
            response, f'href="{self.url}?signins_page=2&amp;tab=security#signins"',
            status_code=400)
        self.assertNotContains(response, 'href="?', status_code=400)

    def test_each_door_goes_back_to_its_tab(self):
        cases = [
            ("account:profile", {"display_name": "Ada", "bio": "", "phone": ""},
             "?tab=edit#profile"),
            ("account:timezone", {"timezone": ""}, "?tab=account#timezone"),
            ("account:sessions_end_others", {}, "?tab=security#sessions"),
            ("account:erasure_request", {}, "?tab=data#erasure"),   # unconfirmed
        ]
        for name, data, where in cases:
            with self.subTest(door=name):
                response = self.client.post(reverse(name), data)
                self.assertRedirects(response, self.url + where, fetch_redirect_response=False)

    def test_the_first_save_makes_the_profile_and_lands_on_its_edit_tab(self):
        loner = User.objects.create_user("loner", "loner@example.test", PW)
        self.client.force_login(loner)
        response = self.client.post(reverse("account:profile"),
                                    {"display_name": "Lone Wolf", "bio": "", "phone": ""})
        person = Person.objects.get(user=loner)
        self.assertRedirects(
            response, reverse("socialhub:profile_details", args=[person.slug]) + "?tab=edit#profile",
            fetch_redirect_response=False)

    def test_the_language_door_comes_back_to_the_account_tab(self):
        response = self.client.post(reverse("socialhub:set_preferred_language"),
                                    {"language": "en", "tab": "account"},
                                    HTTP_REFERER="http://testserver/")
        self.assertEqual(response["Location"], self.url + "?tab=account#language")

    def test_the_map_doors_come_back_to_where_you_live(self):
        response = self.client.post(reverse("socialhub:set_location_sharing"),
                                    {"location_sharing": "off", "tab": "edit"},
                                    HTTP_REFERER="http://testserver/")
        self.assertEqual(response["Location"], self.url + "?tab=edit#where-you-live")
        response = self.client.post(reverse("socialhub:set_my_address"),
                                    {"latitude": "52.2", "longitude": "21.0", "tab": "edit"},
                                    HTTP_REFERER="http://testserver/")
        self.assertEqual(response["Location"], self.url + "?tab=edit#where-you-live")

    def test_a_tab_not_on_the_list_leaves_the_referer_rule(self):
        for tab in ("nonsense", "communities", "//evil.example.com", "https://evil.example.com/"):
            with self.subTest(tab=tab):
                response = self.client.post(reverse("socialhub:set_preferred_language"),
                                            {"language": "en", "tab": tab},
                                            HTTP_REFERER="http://testserver/vault/")
                self.assertEqual(response["Location"], "http://testserver/vault/")
                response = self.client.post(reverse("socialhub:set_location_sharing"),
                                            {"location_sharing": "off", "tab": tab},
                                            HTTP_REFERER="https://evil.example.com/")
                self.assertEqual(response["Location"], reverse("socialhub:profile_list"))

    def test_the_doors_act_on_the_member_alone(self):
        """A tab is somebody's own page only: posting Bob's identifiers to a
        door changes Ada's account, and Bob's page gains nothing."""
        response = self.client.post(reverse("account:timezone"), {
            "timezone": "Asia/Tokyo", "user": self.bob_user.pk, "slug": self.bob.slug,
            "tab": "account"})
        self.assertRedirects(response, self.url + "?tab=account#timezone",
                             fetch_redirect_response=False)
        self.ada.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual((self.ada.timezone, self.bob.timezone), ("Asia/Tokyo", ""))


class _Probe(ProfilePlugin):
    """A plugin that says where it was drawn (safe, as a template's output is)."""

    def render_html(self, **kwargs):
        return mark_safe(f'<section data-testid="probe-{self.get_key()}"></section>')


class PluginTabTests(TabsCase):
    """``ProfilePlugin.tab``: each plugin on the tab it names."""

    def probe(self, key, tab, owner_only=False):
        cls = type(f"Probe_{key}", (_Probe,), {"key": key, "tab": tab,
                                               "show_for_owner_only": owner_only})
        ProfilePlugin.register(cls)
        self.addCleanup(ProfilePlugin.unregister, key)

    def test_the_shipped_plugins_name_their_tabs(self):
        expected = {"mana": "wallet", "wallet": "wallet",
                    "upcoming_events": "activity", "recovery_tickets": "activity"}
        for key, tab in expected.items():
            plugin = ProfilePlugin.get(key)
            if plugin is None:                # an app this host leaves out
                continue
            with self.subTest(plugin=key):
                self.assertEqual(plugin.get_tab(), tab)

    def test_a_plugin_naming_no_known_tab_is_on_the_overview(self):
        self.probe("tests_nowhere", "account")
        self.assertEqual(ProfilePlugin.get("tests_nowhere").get_tab(), "overview")
        self.assertIn(ProfilePlugin.get("tests_nowhere"), ProfilePlugin.on_tab("overview"))

    def test_each_plugin_is_drawn_on_its_tab_alone(self):
        for tab in ("overview", "wallet", "activity", "account"):
            self.probe(f"tests_probe_{tab}", tab)
        drawn = {"overview": {"tests_probe_overview", "tests_probe_account"},
                 "wallet": {"tests_probe_wallet"}, "activity": {"tests_probe_activity"}}
        probes = {"tests_probe_overview", "tests_probe_wallet", "tests_probe_activity",
                  "tests_probe_account"}
        for viewer, tabs in ((self.ada_user, ("overview", "wallet", "activity", "edit",
                                              "account", "security", "data")),
                             (self.bob_user, ("overview", "communities", "activity"))):
            for tab in tabs:
                with self.subTest(viewer=viewer.username, tab=tab):
                    html = self.get(viewer, f"?tab={tab}").content.decode()
                    shown = {key for key in probes if f'data-testid="probe-{key}"' in html}
                    self.assertEqual(shown, drawn.get(tab, set()))

    def test_the_wallet_is_never_drawn_for_a_visitor(self):
        # Not owner-only, yet on the owner's tab: nobody else is shown it.
        self.probe("tests_probe_wallet", "wallet")
        for label, viewer in self.visitors():
            with self.subTest(viewer=label):
                html = self.get(viewer, "?tab=wallet").content.decode()
                self.assertNotIn('data-testid="probe-tests_probe_wallet"', html)
                self.assertNotIn('id="profile-tab-wallet"', html)

    def test_an_owner_only_plugin_is_the_owners_alone(self):
        self.probe("tests_probe_mine", "activity", owner_only=True)
        self.assertContains(self.get(self.ada_user, "?tab=activity"),
                            'data-testid="probe-tests_probe_mine"')
        self.assertNotContains(self.get(self.bob_user, "?tab=activity"),
                               'data-testid="probe-tests_probe_mine"')

    def test_a_tab_with_nothing_to_show_is_left_off(self):
        with mock.patch.object(ProfilePlugin, "on_tab", return_value=[]):
            response = self.get(self.ada_user, "?tab=wallet")
            self.assertEqual(self.current(response), ["overview"])
            self.assertNotContains(response, 'id="profile-tab-wallet"')
            # The owner's Activity tab keeps the reference requests.
            self.assertContains(response, 'id="profile-tab-activity"')
            response = self.get(self.bob_user, "?tab=activity")
            self.assertEqual(self.current(response), ["overview"])
            self.assertEqual([key for _, key, _ in self.tabs(response)],
                             ["overview", "communities"])


class HelperTests(TestCase):
    def test_tab_from_reads_the_list_only(self):
        self.assertEqual(tab_from({"tab": "security"}), "security")
        self.assertEqual(tab_from({"tab": "communities"}), "communities")
        self.assertEqual(tab_from({"tab": "x"}), "")
        self.assertEqual(tab_from({}), "")
        self.assertEqual(tab_from({"page": "2"}), "")
        self.assertEqual(tab_from({"page": "2"}, legacy=True), "security")
        self.assertEqual(tab_from({"signins_page": "2"}, legacy=True), "security")
        self.assertEqual(tab_from({"page": "two"}, legacy=True), "")

    def test_page_number_is_digits_or_nothing(self):
        self.assertEqual([page_number(v) for v in ("3", " 12 ", "0", "-1", "1e3", "x", None,
                                                   "1" * 10, "²")],
                         ["3", "12", "", "", "", "", "", "", ""])

    def test_the_own_page_of_an_account_without_a_profile_is_account(self):
        loner = User.objects.create_user("loner", "loner@example.test", PW)
        self.assertEqual(own_page_url(loner), "/account/")
        self.assertEqual(own_page_url(loner, "edit", "profile"), "/account/#profile")
        self.assertEqual(own_page_url(loner, "security", "sessions"),
                         "/account/?tab=security#sessions")
        self.assertEqual(own_page_url(loner, "nonsense"), "/account/")
