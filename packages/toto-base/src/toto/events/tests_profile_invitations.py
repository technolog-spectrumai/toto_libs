"""A person's own invitations to events on their own profile (2026-10-12,
``plugins/profile_plugins.py``, ``EventInvitationsProfilePlugin``): the ones
waiting for an answer, with Accept and Decline, and the answered ones of
events to come. For the profile's owner and nobody else."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.plugins.profile_plugins import ProfilePlugin

from .models import EventInvite, ScheduledEvent

User = get_user_model()

SOON = timezone.now().replace(microsecond=0) + timedelta(days=3)


def person(name):
    user = User.objects.create_user(name, f"{name}@example.invalid", "pw")
    return user, Person.objects.create(user=user, display_name=name.title())


def event(title, owner, *, start=SOON):
    return ScheduledEvent.objects.create(title=title, owner=owner, start_time=start,
                                         end_time=start + timedelta(hours=2), public=False)


class ProfileInvitationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.host_user, cls.host = person("host")
        cls.guest_user, cls.guest = person("guest")
        cls.other_user, cls.other = person("other")
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.dinner = event("Dinner", cls.host)
        cls.fair = event("Fair", cls.host, start=SOON + timedelta(days=1))
        cls.gone = event("Last year", cls.host, start=SOON - timedelta(days=400))
        cls.waiting = EventInvite.objects.create(event=cls.dinner, person=cls.guest)
        EventInvite.objects.create(event=cls.fair, person=cls.guest,
                                   status=EventInvite.Status.ACCEPTED)
        EventInvite.objects.create(event=cls.gone, person=cls.guest)        # never answered, over
        EventInvite.objects.create(event=cls.dinner, person=cls.other)      # somebody else's

    def page(self, viewer, profile=None):
        self.client.force_login(viewer)
        return self.client.get(reverse("socialhub:profile_details",
                                       kwargs={"slug": (profile or self.guest).slug}))

    def test_the_plugin_is_the_owners_and_on_the_overview(self):
        plugin = ProfilePlugin.get("event_invitations")
        self.assertIsNotNone(plugin)
        self.assertEqual((plugin.get_tab(), plugin.show_for_owner_only), ("overview", True))

    def test_the_owner_sees_what_waits_and_what_was_answered(self):
        response = self.page(self.guest_user)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-testid="profile-event-invitations"', body)
        section = body[body.index('data-testid="profile-event-invitations"'):]
        section = section[:section.index("</section>")]
        self.assertEqual(section.count('data-testid="profile-event-invitation"'), 1)
        self.assertIn("Dinner", section)
        self.assertIn('data-testid="profile-event-invitations-count">1<', section)
        self.assertEqual(section.count('data-testid="profile-event-answered"'), 1)
        self.assertIn("Fair", section)
        self.assertNotIn("Last year", section)                    # over: nothing left to answer
        self.assertIn(reverse("events:invite_respond", args=[self.waiting.pk]), section)
        self.assertIn(reverse("events:my_invites"), section)
        self.assertEqual(section.count('name="action" value="accept"'), 1)
        self.assertEqual(section.count('name="action" value="decline"'), 1)

    def test_nobody_else_sees_a_persons_invitations(self):
        for viewer in (self.other_user, self.host_user, self.root):
            self.assertNotIn('data-testid="profile-event-invitations"',
                             self.page(viewer).content.decode(), viewer.username)

    def test_an_answer_given_on_the_profile_comes_back_to_it(self):
        profile = reverse("socialhub:profile_details", kwargs={"slug": self.guest.slug})
        self.client.force_login(self.guest_user)
        response = self.client.post(reverse("events:invite_respond", args=[self.waiting.pk]),
                                    {"action": "accept", "next": profile})
        self.assertRedirects(response, profile, fetch_redirect_response=False)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, EventInvite.Status.ACCEPTED)
        self.assertIsNotNone(self.waiting.responded_at)
        body = self.page(self.guest_user).content.decode()
        self.assertIn('data-testid="profile-event-invitations-none"', body)
        self.assertEqual(body.count('data-testid="profile-event-answered"'), 2)

    def test_an_answer_is_not_a_way_off_the_platform_and_the_list_is_the_default(self):
        self.client.force_login(self.guest_user)
        for elsewhere in ("https://evil.example/", "//evil.example/x", ""):
            response = self.client.post(reverse("events:invite_respond", args=[self.waiting.pk]),
                                        {"action": "decline", "next": elsewhere})
            self.assertRedirects(response, reverse("events:my_invites"),
                                 fetch_redirect_response=False)

    def test_somebody_elses_invitation_is_not_answered_from_here(self):
        self.client.force_login(self.other_user)
        response = self.client.post(reverse("events:invite_respond", args=[self.waiting.pk]),
                                    {"action": "accept", "next": "/"})
        self.assertEqual(response.status_code, 403)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, EventInvite.Status.PENDING)

    def test_a_profile_with_no_invitation_says_so(self):
        body = self.page(self.host_user, self.host).content.decode()
        self.assertIn('data-testid="profile-event-invitations-none"', body)
        self.assertNotIn('data-testid="profile-event-invitations-count"', body)
