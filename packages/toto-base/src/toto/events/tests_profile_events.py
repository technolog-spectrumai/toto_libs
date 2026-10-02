"""A profile's Upcoming Events are the VIEWER's events (2026-10-01, 37c.25).

The "Attending" list had no visibility filter: the title, time and place of
every upcoming private event an invitee had accepted showed to any member
who opened that invitee's profile. Both lists now ask
``access.visible_events`` for the person looking — public events, and the
private ones they own, organise or are invited to.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.events.tests_profile_events
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.plugins.profile_plugins import ProfilePlugin

from .models import EventInvite, ScheduledEvent

User = get_user_model()

START = timezone.now().replace(microsecond=0) + timedelta(days=3)

DINNER = "Dinner at Elm Street 4"
FAIR = "Village fair"
PLANNING = "Planning the surprise"


def person(name):
    user = User.objects.create_user(name, f"{name}@example.invalid", "pw")
    return user, Person.objects.create(user=user, display_name=name.title())


def event(title, *, public, owner):
    return ScheduledEvent.objects.create(
        title=title, start_time=START, end_time=START + timedelta(hours=2),
        public=public, owner=owner)


class ProfileEventsCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.host_user, cls.host = person("host")
        # The profile every test opens: they accepted the private dinner.
        cls.guest_user, cls.guest = person("guest")
        cls.cousin_user, cls.cousin = person("cousin")
        cls.helper_user, cls.helper = person("helper")
        cls.stranger_user, cls.stranger = person("stranger")

        cls.dinner = event(DINNER, public=False, owner=cls.host)
        EventInvite.objects.create(event=cls.dinner, person=cls.guest,
                                   status=EventInvite.Status.ACCEPTED)
        # Invited and not yet answered: an invitation is enough to read it.
        EventInvite.objects.create(event=cls.dinner, person=cls.cousin)
        cls.dinner.organizers.add(cls.helper)

        cls.fair = event(FAIR, public=True, owner=cls.host)
        EventInvite.objects.create(event=cls.fair, person=cls.guest,
                                   status=EventInvite.Status.ACCEPTED)

        # A private event the guest organises.
        cls.planning = event(PLANNING, public=False, owner=cls.host)
        cls.planning.organizers.add(cls.guest)

    def page(self, viewer):
        """The guest's profile on its Activity tab, where the upcoming events
        are since 2026-10-02 (stage 50: the profile in tabs)."""
        self.client.force_login(viewer)
        response = self.client.get(
            reverse("socialhub:profile_details", args=[self.guest.slug]) + "?tab=activity")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="profile-panel" data-tab="activity"', response.content.decode())
        return response.content.decode()

    def lists(self, viewer):
        request = RequestFactory().get("/")
        request.user = viewer
        context = ProfilePlugin.get("upcoming_events").get_context(
            request=request, profile=self.guest)
        return ({e.title for e in context["organizing_events"]},
                {e.title for e in context["attending_events"]})


class StrangerTests(ProfileEventsCase):
    def test_a_private_event_the_invitee_accepted_is_not_shown_to_a_stranger(self):
        html = self.page(self.stranger_user)
        self.assertNotIn(DINNER, html)
        self.assertIn(FAIR, html)

    def test_a_private_event_the_profile_organises_is_not_shown_either(self):
        organizing, attending = self.lists(self.stranger_user)
        self.assertEqual(organizing, set())
        self.assertEqual(attending, {FAIR})
        self.assertNotIn(PLANNING, self.page(self.stranger_user))

    def test_a_login_without_a_person_sees_the_public_ones_only(self):
        bare = User.objects.create_user("bare", "bare@example.invalid", "pw")
        self.assertEqual(self.lists(bare), (set(), {FAIR}))


class PeopleOfTheEventTests(ProfileEventsCase):
    def test_the_owner_sees_the_private_events(self):
        self.assertEqual(self.lists(self.host_user), ({PLANNING}, {DINNER, FAIR}))
        self.assertIn(DINNER, self.page(self.host_user))

    def test_an_organiser_sees_the_event_they_organise(self):
        organizing, attending = self.lists(self.helper_user)
        self.assertEqual(attending, {DINNER, FAIR})
        self.assertEqual(organizing, set())

    def test_another_invitee_sees_it_and_only_it(self):
        organizing, attending = self.lists(self.cousin_user)
        self.assertEqual(attending, {DINNER, FAIR})
        self.assertEqual(organizing, set())

    def test_the_member_sees_all_of_their_own(self):
        self.assertEqual(self.lists(self.guest_user), ({PLANNING}, {DINNER, FAIR}))
        html = self.page(self.guest_user)
        self.assertIn(DINNER, html)
        self.assertIn(PLANNING, html)


class TabTests(ProfileEventsCase):
    """The section is on the profile's Activity tab (2026-10-02, stage 50),
    the member's own and a visitor's alike; the Overview names no event."""

    def test_the_events_are_on_the_activity_tab_alone(self):
        self.assertEqual(ProfilePlugin.get("upcoming_events").get_tab(), "activity")
        for viewer in (self.guest_user, self.stranger_user):
            with self.subTest(viewer=viewer.username):
                self.client.force_login(viewer)
                overview = self.client.get(
                    reverse("socialhub:profile_details", args=[self.guest.slug])).content.decode()
                self.assertNotIn(FAIR, overview)
                self.assertNotIn('id="upcoming-events"', overview)
                html = self.page(viewer)
                self.assertIn(FAIR, html)
                self.assertIn('id="upcoming-events"', html)
