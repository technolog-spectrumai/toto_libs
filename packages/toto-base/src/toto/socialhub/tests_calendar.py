"""Smoke: the community calendar section renders and does not leak."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class CommunityCalendarSmoke(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.events.models import ScheduledEvent
        from toto.people.models import Person
        from toto.socialhub.models import Community

        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        U = get_user_model()
        cls.member_user = U.objects.create_user(username="m", password="pw")
        cls.member = Person.objects.create(user=cls.member_user, display_name="M")
        cls.outsider_user = U.objects.create_user(username="o", password="pw")
        cls.outsider = Person.objects.create(user=cls.outsider_user, display_name="O")

        cls.community = Community.objects.create(name="Guild", slug="guild")
        cls.community.members.add(cls.member)

        cls.public = ScheduledEvent.objects.create(
            title="OpenDay", owner=cls.member, public=True,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=1))
        cls.private = ScheduledEvent.objects.create(
            title="SecretPlanning", owner=cls.member, public=False,
            start_time=timezone.now() + timedelta(days=2),
            end_time=timezone.now() + timedelta(days=2, hours=1))

    def url(self):
        return reverse("socialhub:community_detail", args=["guild"])

    def test_a_member_sees_both_their_own_events(self):
        self.client.force_login(self.member_user)
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "OpenDay")
        self.assertContains(r, "SecretPlanning")
        self.assertContains(r, "community_calendar")

    def test_an_outsider_sees_the_public_one_and_NOT_the_private_one(self):
        """The leak this guards: without visible_events the section would list
        every private event any member owns, to anyone who opens the page."""
        self.client.force_login(self.outsider_user)
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "OpenDay")
        self.assertNotContains(r, "SecretPlanning")
