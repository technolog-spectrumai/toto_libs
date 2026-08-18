"""The calendar's visibility boundary.

One rule, and it was wrong until 8/2026: ``public`` applied to anonymous
visitors only, so any signed-in user read every event on the platform. These
pin the fix, because it is the kind of thing that gets "simplified" back to
``objects.all()`` by somebody who notices the query is cheaper.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from toto.core.models import Platform
from toto.people.models import Person

from .models import EventInvite, ScheduledEvent

User = get_user_model()


def _person(name):
    user = User.objects.create_user(name, f"{name}@example.invalid", "pw")
    return user, Person.objects.create(user=user, display_name=name)


def _event(title, *, public, owner=None):
    start = now() + timedelta(days=1)
    return ScheduledEvent.objects.create(
        title=title, start_time=start, end_time=start + timedelta(hours=1),
        public=public, owner=owner)


class CalendarVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # PageProcessor raises Http404 without one, so every page test needs it.
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "Test", "author": "t",
                                   "publication_year": 2026})
        cls.owner_user, cls.owner = _person("owner")
        cls.other_user, cls.other = _person("other")
        cls.public_event = _event("Open day", public=True)
        cls.private_event = _event("Board sitting", public=False, owner=cls.owner)

    def _titles(self, user=None):
        if user is not None:
            self.client.force_login(user)
        response = self.client.get(reverse("events:event_list"))
        return {e.title for e in response.context["events"]}

    def test_a_signed_in_stranger_does_not_see_a_private_event(self):
        # The whole point. Before the fix this returned objects.all().
        self.assertEqual(self._titles(self.other_user), {"Open day"})

    def test_the_owner_sees_their_own_private_event(self):
        self.assertEqual(self._titles(self.owner_user),
                         {"Open day", "Board sitting"})

    def test_an_invitee_sees_the_event_they_were_invited_to(self):
        EventInvite.objects.create(event=self.private_event, person=self.other)
        self.assertIn("Board sitting", self._titles(self.other_user))

    def test_an_organizer_sees_it_too(self):
        self.private_event.organizers.add(self.other)
        self.assertIn("Board sitting", self._titles(self.other_user))

    def test_a_login_with_no_person_sees_only_public_events(self):
        stray = User.objects.create_user("stray", "s@example.invalid", "pw")
        self.assertEqual(self._titles(stray), {"Open day"})

    def test_the_default_is_still_public(self):
        # Nothing that was meant to be seen disappears: only an event somebody
        # explicitly marked private starts behaving like one.
        self.assertTrue(_event("Ordinary", public=True).public)
        self.assertTrue(ScheduledEvent._meta.get_field("public").default)
