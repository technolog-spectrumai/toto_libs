"""Who joined, who signed in and out — to the same communities only
(2026-10-04).

    manage.py test toto.notify.tests_presence
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core import live
from toto.notify import presence
from toto.notify.models import Notification
from toto.notify.testing import MEMORY_LAYER, Listener
from toto.people.models import Person
from toto.socialhub.models import Community

User = get_user_model()


@override_settings(CHANNEL_LAYERS=MEMORY_LAYER)
class Case(TestCase):
    def setUp(self):
        cache.clear()
        self.devs = Community.objects.create(name="Devs", slug="devs")
        self.ops = Community.objects.create(name="Ops", slug="ops")
        self.users, self.people = {}, {}
        for name, groups in (("ada", [self.devs]), ("bob", [self.devs, self.ops]),
                             ("cy", [self.ops]), ("eve", [])):
            user = User.objects.create_user(name, password="pw")
            person = Person.objects.create(user=user, display_name=name.title())
            person.communities.add(*groups)
            self.users[name], self.people[name] = user, person
        self.staff = User.objects.create_superuser("root", "r@example.org", "pw")
        Person.objects.create(user=self.staff, display_name="Root")
        Notification.objects.all().delete()
        self.ears = {name: Listener(live.user_group(user.pk))
                     for name, user in {**self.users, "root": self.staff}.items()}

    def heard(self):
        return {name: ear.messages() for name, ear in self.ears.items()}


class AudienceTests(Case):
    def test_it_is_exactly_the_members_of_shared_communities(self):
        names = lambda who: sorted(presence.audience(self.people[who])   # noqa: E731
                                   .values_list("username", flat=True))
        self.assertEqual(names("ada"), ["bob"])
        self.assertEqual(names("bob"), ["ada", "cy"])
        self.assertEqual(names("cy"), ["bob"])
        self.assertEqual(names("eve"), [])

    def test_an_inactive_account_is_not_in_it(self):
        self.users["bob"].is_active = False
        self.users["bob"].save()
        self.assertEqual(list(presence.audience(self.people["ada"])), [])


class SignInOutTests(Case):
    def sign_in(self, name):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.force_login(self.users[name])

    def test_a_sign_in_is_a_toast_for_the_shared_communities_and_nobody_else(self):
        self.sign_in("ada")
        heard = self.heard()
        self.assertEqual(heard["bob"], [{
            "type": "live.presence", "event": "in", "name": "Ada",
            "link": reverse("socialhub:profile_details", args=[self.people["ada"].slug])}])
        for name in ("ada", "cy", "eve", "root"):
            self.assertEqual([m for m in heard[name] if m["type"] == "live.presence"], [], name)

    def test_nothing_is_stored_for_a_sign_in_or_a_sign_out(self):
        self.sign_in("ada")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.logout()
        self.assertEqual([m["event"] for m in self.heard()["bob"]], ["in", "out"])
        self.assertFalse(Notification.objects.filter(recipient=self.users["bob"]).exists())

    def test_the_switch_off_silences_both(self):
        Person.objects.filter(pk=self.people["ada"].pk).update(show_online=False)
        self.sign_in("ada")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.logout()
        self.assertEqual(self.heard()["bob"], [])

    def test_the_switch_is_on_by_default_and_on_the_profile_form(self):
        from toto.socialhub.forms import AccountProfileForm

        self.assertTrue(Person._meta.get_field("show_online").default)
        self.assertIn("show_online", AccountProfileForm.Meta.fields)

    def test_one_sign_in_toast_per_person_per_listener_per_five_minutes(self):
        self.sign_in("ada")
        self.sign_in("ada")
        self.assertEqual(len(self.heard()["bob"]), 1)
        cache.clear()
        self.sign_in("ada")
        self.assertEqual(len(self.heard()["bob"]), 1)
        self.assertEqual(presence.THROTTLE_SECONDS, 300)

    def test_a_session_ended_from_security_is_a_sign_out(self):
        from toto.core.models import UserSession
        from toto.core.user_sessions import end_session

        self.sign_in("ada")
        self.heard()
        row = UserSession.objects.filter(user=self.users["ada"]).first()
        if row is None:
            self.skipTest("this host lists no sessions")
        with self.captureOnCommitCallbacks(execute=True):
            end_session(self.users["ada"], row.pk)
        self.assertEqual([m["event"] for m in self.heard()["bob"]], ["out"])

    def test_somebody_without_a_profile_is_announced_to_nobody(self):
        loner = User.objects.create_user("loner", password="pw")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.force_login(loner)
        self.assertTrue(all(not m for m in self.heard().values()))


class JoinedTests(Case):
    def test_joining_is_kept_in_the_bell_of_that_communitys_other_members(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.people["eve"].communities.add(self.ops)
        rows = Notification.objects.filter(kind="community.joined")
        self.assertEqual(sorted(rows.values_list("recipient__username", flat=True)),
                         ["bob", "cy"])
        row = rows.first()
        self.assertEqual((row.params["name"], row.params["community"]), ("Eve", "Ops"))
        self.assertIn(self.people["eve"].slug, row.link)
        self.assertEqual(self.heard()["cy"], [{"type": "live.notification"}])
        self.assertEqual(self.heard()["ada"], [])

    def test_from_the_communitys_side_too_and_only_once(self):
        self.devs.members.add(self.people["eve"])
        self.devs.members.add(self.people["eve"])
        self.assertEqual(sorted(Notification.objects.filter(kind="community.joined")
                                .values_list("recipient__username", flat=True)), ["ada", "bob"])
