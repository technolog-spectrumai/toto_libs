"""The map tied to the pages of other apps (``links.py``, 2026-10-12): an
event scheduled at a registered place, a community's seat set and drawn on
its page, a person's home drawn on their profile; all of it for somebody who
has the map and may read the place, and for nobody else.

    manage.py test toto.locations.tests_links
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.events.forms import ScheduledEventForm
from toto.events.models import ScheduledEvent
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.community_plugins import CommunityPlugin
from toto.socialhub.plugins.profile_plugins import ProfilePlugin

from . import links
from .models import Address, CommunitySeat, EventPlace, Home, HomeSharing

User = get_user_model()

NO_MAP = mock.patch("toto.locations.links.map_open_to", return_value=False)


def person(name):
    user = User.objects.create_user(name, f"{name}@example.invalid", "pw")
    return user, Person.objects.create(user=user, display_name=name.title())


class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.head_user, cls.head = person("head")
        cls.member_user, cls.member = person("member")
        cls.other_user, cls.other = person("other")
        cls.club = Community.objects.create(name="Club", slug="club", head=cls.head)
        for somebody in (cls.head, cls.member):
            somebody.communities.add(cls.club)
        cls.hall = Address.objects.create(street="Elm", building="4", locality_name="Oldtown",
                                          latitude=52.2297, longitude=21.0122)
        cls.yard = Address.objects.create(street="Mill", building="9", locality_name="Oldtown",
                                          latitude=52.25, longitude=21.05)
        # Somebody's home, shared with nobody: not a place anybody else reads.
        cls.private = Address.objects.create(street="Quiet", building="1", locality_name="Oldtown",
                                             latitude=52.3, longitude=21.1)
        Home.objects.create(person=cls.other, address=cls.private, sharing=HomeSharing.OFF)

    def as_(self, user):
        self.client.force_login(user)
        return self.client


class RulesTests(Fixture):
    def test_the_registered_places_are_what_the_person_may_read(self):
        self.assertEqual(set(links.registered_places(self.member_user)), {self.hall, self.yard})
        self.assertIn(self.private, links.registered_places(self.other_user))    # their own home
        self.assertTrue(links.may_read_place(self.member_user, self.hall))
        self.assertFalse(links.may_read_place(self.member_user, self.private))
        self.assertFalse(links.may_read_place(self.member_user, None))

    def test_the_map_is_for_somebody_signed_in(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(links.map_open_to(AnonymousUser()))
        self.assertFalse(links.map_open_to(None))

    def test_a_point_is_two_floats_or_nothing(self):
        self.assertEqual(links.point_of(self.hall), (52.2297, 21.0122))
        self.assertIsNone(links.point_of(None))
        self.assertIsNone(links.point_of(Address.objects.create(locality_name="Nowhere")))


class EventPlaceTests(Fixture):
    def data(self, **more):
        start = timezone.localtime(timezone.now() + timedelta(days=3))
        end = start + timedelta(hours=2)
        data = {"title": "Dinner", "description": "",
                "start_time_0": start.strftime("%Y-%m-%d"), "start_time_1": start.strftime("%H:%M"),
                "end_time_0": end.strftime("%Y-%m-%d"), "end_time_1": end.strftime("%H:%M"),
                "address": "", "public": "on"}
        data.update(more)
        return data

    def test_the_form_lists_the_registered_places_after_the_text(self):
        form = ScheduledEventForm(user=self.member_user)
        self.assertTrue(form.has_place)
        names = list(form.fields)
        self.assertEqual(names.index("place"), names.index("address") + 1)
        self.assertEqual(set(form.fields["place"].queryset), {self.hall, self.yard})
        page = self.as_(self.member_user).get(reverse("events:event_create")).content.decode()
        self.assertIn('data-testid="event-place"', page)
        self.assertIn(str(self.hall), page)
        self.assertNotIn("Quiet", page)

    def test_an_event_scheduled_at_a_place_is_tied_to_it(self):
        client = self.as_(self.member_user)
        response = client.post(reverse("events:event_create"), self.data(place=self.hall.pk))
        event = ScheduledEvent.objects.get(title="Dinner")
        self.assertRedirects(response, reverse("events:event_detail", args=[event.pk]))
        self.assertEqual(EventPlace.objects.get(event=event).address, self.hall)
        self.assertEqual(links.event_place(event), self.hall)
        # The text names the place where the organiser typed none.
        self.assertEqual(event.address, str(self.hall))
        page = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()
        self.assertIn('data-testid="event-place-on-map"', page)
        self.assertIn(reverse("locations:address_detail", args=[self.hall.pk]), page)

    def test_what_the_organiser_typed_is_kept(self):
        self.as_(self.member_user).post(
            reverse("events:event_create"), self.data(place=self.yard.pk, address="The big room"))
        event = ScheduledEvent.objects.get(title="Dinner")
        self.assertEqual(event.address, "The big room")
        self.assertEqual(links.event_place(event), self.yard)

    def test_no_place_is_no_row_and_a_place_one_may_not_read_is_no_choice(self):
        client = self.as_(self.member_user)
        client.post(reverse("events:event_create"), self.data())
        event = ScheduledEvent.objects.get(title="Dinner")
        self.assertFalse(EventPlace.objects.filter(event=event).exists())
        response = client.post(reverse("events:event_create"),
                               self.data(title="Second", place=self.private.pk))
        self.assertEqual(response.status_code, 200)                      # the form again
        self.assertFalse(ScheduledEvent.objects.filter(title="Second").exists())
        self.assertIn("place", response.context["form"].errors)

    def test_the_place_is_shown_to_who_may_read_it(self):
        event = ScheduledEvent.objects.create(
            title="At home", owner=self.other, public=True,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=1))
        links.set_event_place(event, self.private)
        self.assertEqual(links.place_shown(self.other_user, event), self.private)
        self.assertIsNone(links.place_shown(self.member_user, event))
        page = self.as_(self.member_user).get(
            reverse("events:event_detail", args=[event.pk])).content.decode()
        self.assertNotIn('data-testid="event-place-on-map"', page)
        links.set_event_place(event, None)
        self.assertFalse(EventPlace.objects.filter(event=event).exists())

    def test_without_the_map_the_form_has_no_list_and_the_page_no_link(self):
        event = ScheduledEvent.objects.create(
            title="Fair", owner=self.member, public=True,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=1))
        links.set_event_place(event, self.hall)
        with NO_MAP:
            form = ScheduledEventForm(user=self.member_user)
            self.assertFalse(form.has_place)
            self.assertNotIn("place", form.fields)
            client = self.as_(self.member_user)
            self.assertNotIn('data-testid="event-place"',
                             client.get(reverse("events:event_create")).content.decode())
            self.assertNotIn('data-testid="event-place-on-map"', client.get(
                reverse("events:event_detail", args=[event.pk])).content.decode())
            client.post(reverse("events:event_create"), self.data(title="Plain", place=self.hall.pk))
        plain = ScheduledEvent.objects.get(title="Plain")
        self.assertFalse(EventPlace.objects.filter(event=plain).exists())


class CommunitySeatTests(Fixture):
    def page(self, user):
        return self.as_(user).get(reverse("socialhub:community_detail", args=[self.club.slug]))

    def door(self, user, **data):
        return self.as_(user).post(
            reverse("locations:set_community_seat", args=[self.club.slug]), data)

    def test_the_plugin_is_registered(self):
        self.assertIsNotNone(CommunityPlugin.get("seat_on_the_map"))

    def test_the_head_sets_the_seat_from_the_registered_places(self):
        body = self.page(self.head_user).content.decode()
        self.assertIn('data-testid="community-seat-form"', body)
        self.assertIn('data-testid="community-seat-none"', body)
        self.assertIn(str(self.hall), body)
        self.assertNotIn("Quiet", body)
        response = self.door(self.head_user, address=self.hall.pk)
        self.assertRedirects(response, reverse("socialhub:community_detail", args=[self.club.slug])
                             + "#seat-on-the-map", fetch_redirect_response=False)
        self.assertEqual(CommunitySeat.objects.get(community=self.club).address, self.hall)
        self.club.refresh_from_db()
        self.assertEqual(self.club.seat, str(self.hall))             # the text was empty
        body = self.page(self.head_user).content.decode()
        self.assertIn('data-testid="place-map"', body)
        self.assertIn('data-lat="52.229700"', body)
        self.assertIn('data-lon="21.012200"', body)
        self.assertIn("tile.openstreetmap.org", body)
        self.assertIn(reverse("locations:address_detail", args=[self.hall.pk]), body)

    def test_a_member_sees_the_seat_and_no_form(self):
        self.assertNotIn('data-testid="community-seat"', self.page(self.member_user).content.decode())
        links.set_community_seat(self.club, self.hall)
        body = self.page(self.member_user).content.decode()
        self.assertIn('data-testid="community-seat-address"', body)
        self.assertIn('data-testid="place-map"', body)
        self.assertNotIn('data-testid="community-seat-form"', body)

    def test_nobody_else_sets_it_and_only_a_registered_place_is_taken(self):
        self.assertEqual(self.door(self.member_user, address=self.hall.pk).status_code, 403)
        self.assertFalse(CommunitySeat.objects.exists())
        for wrong in (self.private.pk, 987654, "x"):
            self.door(self.head_user, address=wrong)
            self.assertFalse(CommunitySeat.objects.exists(), wrong)
        self.assertEqual(self.as_(self.head_user).get(
            reverse("locations:set_community_seat", args=[self.club.slug])).status_code, 405)

    def test_the_text_somebody_typed_is_kept_and_the_seat_can_be_taken_away(self):
        self.club.seat = "Upstairs"
        self.club.save(update_fields=["seat"])
        self.door(self.head_user, address=self.yard.pk)
        self.club.refresh_from_db()
        self.assertEqual(self.club.seat, "Upstairs")
        self.door(self.head_user, address="")
        self.assertFalse(CommunitySeat.objects.filter(community=self.club).exists())

    def test_a_seat_the_viewer_may_not_read_is_not_drawn(self):
        links.set_community_seat(self.club, self.private)          # somebody's unshared home
        body = self.page(self.member_user).content.decode()
        self.assertNotIn('data-testid="community-seat"', body)
        self.assertNotIn("Quiet", body)

    def test_without_the_map_there_is_no_section_and_no_door(self):
        links.set_community_seat(self.club, self.hall)
        with NO_MAP:
            self.assertNotIn('data-testid="community-seat"',
                             self.page(self.head_user).content.decode())
            self.assertEqual(self.door(self.head_user, address=self.yard.pk).status_code, 403)
        self.assertEqual(CommunitySeat.objects.get(community=self.club).address, self.hall)


class ProfileMapTests(Fixture):
    def page(self, viewer, whose):
        return self.as_(viewer).get(
            reverse("socialhub:profile_details", kwargs={"slug": whose.slug})).content.decode()

    def share(self, sharing):
        Home.objects.filter(person=self.other).update(sharing=sharing)

    def test_the_plugin_is_on_the_overview_for_everybody_who_may_see(self):
        plugin = ProfilePlugin.get("home_map")
        self.assertEqual((plugin.get_tab(), plugin.show_for_owner_only), ("overview", False))

    def test_somebody_who_shares_nothing_is_on_nobodys_map_but_their_own(self):
        self.assertNotIn('data-testid="profile-home-map"', self.page(self.member_user, self.other))
        own = self.page(self.other_user, self.other)
        self.assertIn('data-testid="profile-home-map"', own)
        self.assertIn('data-lat="52.300000"', own)
        self.assertIn('data-approximate="0"', own)

    def test_an_exact_sharer_is_a_pin(self):
        self.share(HomeSharing.EXACT)
        body = self.page(self.member_user, self.other)
        self.assertIn('data-testid="profile-home-map"', body)
        self.assertIn('data-lat="52.300000"', body)
        self.assertIn('data-approximate="0"', body)
        self.assertIn("Quiet 1", body)

    def test_an_approximate_sharer_is_an_area_without_the_street(self):
        Address.objects.filter(pk=self.private.pk).update(latitude=52.304321, longitude=21.107654)
        self.share(HomeSharing.APPROXIMATE)
        body = self.page(self.member_user, self.other)
        section = body[body.index('data-testid="profile-home-map"'):]
        section = section[:section.index("</section>")]
        self.assertIn('data-approximate="1"', section)
        self.assertIn('data-lat="52.300000"', section)                # two decimal places
        self.assertIn('data-lon="21.110000"', section)
        self.assertNotIn("52.3043", section)
        self.assertNotIn("Quiet", section)
        self.assertIn("Oldtown", section)

    def test_somebody_with_no_home_has_no_map(self):
        self.assertNotIn('data-testid="profile-home-map"', self.page(self.head_user, self.member))
        self.assertNotIn('data-testid="profile-home-map"', self.page(self.member_user, self.member))

    def test_without_the_map_a_profile_draws_neither_section(self):
        self.share(HomeSharing.EXACT)
        with NO_MAP:
            self.assertNotIn('data-testid="profile-home-map"',
                             self.page(self.member_user, self.other))
            own = self.page(self.other_user, self.other)
            self.assertNotIn('data-testid="profile-home-map"', own)
            self.assertNotIn('id="home-on-the-map"', own)
        self.assertIn('id="home-on-the-map"', self.page(self.other_user, self.other))
