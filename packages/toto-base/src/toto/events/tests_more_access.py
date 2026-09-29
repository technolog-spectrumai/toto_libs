"""Events beyond the calendar filter: who may make, plan, invite and answer,
what the JSON doors refuse, and the one visibility rule at its edges.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.events.tests_more_access
"""

import json
from datetime import timedelta
from unittest import skip

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person

from .access import may_read, visible_events
from .calendar import calendar_colors
from .models import Availability, EventCategory, EventInvite, ScheduledEvent

User = get_user_model()

START = timezone.now().replace(microsecond=0) + timedelta(days=2)


def person(name, *, mesh=False, **flags):
    user = User.objects.create_user(name, f"{name}@example.invalid", "pw", **flags)
    if mesh:
        user.groups.add(Group.objects.get_or_create(name="data_mesh")[0])
    return user, Person.objects.create(user=user, display_name=name.title())


def event(title, *, public=True, owner=None, start=START, hours=2):
    return ScheduledEvent.objects.create(title=title, start_time=start,
                                         end_time=start + timedelta(hours=hours),
                                         public=public, owner=owner)


class EventCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.owner_user, cls.owner = person("owner", mesh=True)
        cls.guest_user, cls.guest = person("guest", mesh=True)
        cls.stranger_user, cls.stranger = person("stranger", mesh=True)
        cls.open_day = event("Open day")
        cls.sitting = event("Board sitting", public=False, owner=cls.owner)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


class RuleTests(EventCase):
    def test_nothing_is_readable_about_no_event(self):
        self.assertFalse(may_read(self.owner_user, None))

    def test_a_given_queryset_is_narrowed_never_widened(self):
        only_private = ScheduledEvent.objects.filter(public=False)
        self.assertEqual(list(visible_events(self.stranger_user, only_private)), [])
        self.assertEqual(list(visible_events(self.owner_user, only_private)), [self.sitting])
        self.assertEqual(list(visible_events(None, only_private)), [])

    def test_an_event_reached_by_two_arms_is_listed_once(self):
        self.sitting.organizers.add(self.owner)
        EventInvite.objects.create(event=self.sitting, person=self.owner)
        self.assertEqual(list(visible_events(self.owner_user).filter(pk=self.sitting.pk)),
                         [self.sitting])

    def test_a_declined_invitation_still_lets_you_read_it(self):
        EventInvite.objects.create(event=self.sitting, person=self.guest,
                                   status=EventInvite.Status.DECLINED)
        self.assertTrue(may_read(self.guest_user, self.sitting))

    def test_staff_get_no_private_event_they_are_not_part_of(self):
        staff, _ = person("clerk", is_staff=True)
        self.assertFalse(may_read(staff, self.sitting))


class ModelTests(TestCase):
    def test_an_event_and_an_availability_must_end_after_they_start(self):
        _, someone = person("someone")
        for row in (ScheduledEvent(title="x", start_time=START, end_time=START),
                    Availability(person=someone, start_time=START,
                                 end_time=START - timedelta(minutes=1),
                                 availability_type=Availability.AvailabilityType.BUSY)):
            with self.subTest(model=type(row).__name__):
                with self.assertRaises(ValidationError):
                    row.clean()

    def test_one_invitation_per_person_per_event(self):
        from django.db import IntegrityError

        _, someone = person("someone")
        sitting = event("Sitting")
        EventInvite.objects.create(event=sitting, person=someone)
        with self.assertRaises(IntegrityError):
            EventInvite.objects.create(event=sitting, person=someone)

    def test_the_calendar_falls_back_to_default_colours(self):
        self.assertEqual(calendar_colors(None), {"background": "#36A2EB", "text": "#000000"})
        self.assertEqual(calendar_colors({"theme": {"colors": {"accent-light": "#111",
                                                               "text-main-light": "#222"}}}),
                         {"background": "#111", "text": "#222"})


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class CreatePageTests(EventCase):
    def form(self, **overrides):
        data = {"title": "Picnic", "description": "", "start_time_0": "2030-06-01",
                "start_time_1": "12:00", "end_time_0": "2030-06-01", "end_time_1": "15:00",
                "public": "on"}
        data.update(overrides)
        return data

    def test_the_maker_owns_and_organises_what_they_make(self):
        self.client.force_login(self.guest_user)
        response = self.client.post(reverse("events:event_create"), self.form())
        made = ScheduledEvent.objects.get(title="Picnic")
        self.assertRedirects(response, reverse("events:event_detail", args=[made.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(made.owner, self.guest)
        self.assertEqual(list(made.organizers.all()), [self.guest])

    def test_an_event_that_ends_before_it_starts_is_not_made(self):
        self.client.force_login(self.guest_user)
        response = self.client.post(reverse("events:event_create"),
                                    self.form(end_time_1="11:00"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ScheduledEvent.objects.filter(title="Picnic").exists())

    def test_a_login_without_a_person_makes_an_ownerless_event(self):
        stray = User.objects.create_user("stray", password="pw")
        self.client.force_login(stray)
        self.client.post(reverse("events:event_create"), self.form(title="Stray picnic"))
        made = ScheduledEvent.objects.get(title="Stray picnic")
        self.assertIsNone(made.owner)
        self.assertFalse(made.organizers.exists())


class PlanPageTests(EventCase):
    def plan_url(self, target=None):
        return reverse("events:event_plan", args=[(target or self.sitting).pk])

    def test_the_owner_invites_and_unknown_people_are_skipped(self):
        self.client.force_login(self.owner_user)
        response = self.client.post(self.plan_url(), {"person_ids": [
            self.guest.pk, self.stranger.pk, 999999]})
        self.assertRedirects(response, self.plan_url(), fetch_redirect_response=False)
        self.assertEqual({i.person for i in self.sitting.invites.all()},
                         {self.guest, self.stranger})
        self.client.post(self.plan_url(), {"person_ids": [self.guest.pk]})
        self.assertEqual(self.sitting.invites.count(), 2)          # no second invitation

    def test_only_an_organiser_may_invite(self):
        self.client.force_login(self.guest_user)
        self.assertEqual(self.client.post(self.plan_url(),
                                          {"person_ids": [self.guest.pk]}).status_code, 403)
        self.assertFalse(self.sitting.invites.exists())
        self.sitting.organizers.add(self.guest)
        self.client.post(self.plan_url(), {"person_ids": [self.stranger.pk]})
        self.assertTrue(self.sitting.invites.filter(person=self.stranger).exists())

    def test_the_page_counts_answers_and_reads_availability(self):
        EventInvite.objects.create(event=self.sitting, person=self.guest,
                                   status=EventInvite.Status.ACCEPTED)
        EventInvite.objects.create(event=self.sitting, person=self.stranger)
        Availability.objects.create(person=self.guest, start_time=START - timedelta(hours=1),
                                    end_time=START + timedelta(hours=1),
                                    availability_type=Availability.AvailabilityType.BUSY)
        self.client.force_login(self.owner_user)
        context = self.client.get(self.plan_url()).context
        self.assertEqual((context["accepted_count"], context["pending_count"],
                          context["declined_count"]), (1, 1, 0))
        statuses = {row["person"].display_name: row["avail_status"]
                    for row in context["invite_rows"]}
        self.assertEqual(statuses, {"Guest": "blocked", "Stranger": "unknown"})
        self.assertNotIn(self.guest, list(context["uninvited"]))
        self.assertIn(self.owner, list(context["uninvited"]))
        self.assertTrue(context["user_is_organizer"])

    @skip("suspected bug: event_plan (events/views.py:242) fetches the event with "
          "get_object_or_404(ScheduledEvent...) and never asks access.visible_events, "
          "so any signed-in stranger who has the UUID reads a private event's title, "
          "times, invitees and their answers on its planning page (the detail page 404s)")
    def test_a_stranger_cannot_open_a_private_events_plan(self):
        EventInvite.objects.create(event=self.sitting, person=self.guest)
        self.client.force_login(self.stranger_user)
        self.assertEqual(self.client.get(self.plan_url()).status_code, 404)


class AvailabilityTests(EventCase):
    def status(self, who, target=None):
        self.client.force_login(self.owner_user)
        return self.client.get(reverse("events:event_availability_api"), {
            "person_id": who.pk, "event_id": (target or self.open_day).pk}).json()

    def test_a_blocking_entry_wins_over_a_soft_one(self):
        Availability.objects.create(person=self.guest, start_time=START,
                                    end_time=START + timedelta(hours=1), blocks_scheduling=False,
                                    availability_type=Availability.AvailabilityType.RESERVED)
        self.assertEqual(self.status(self.guest)["status"], "busy")
        Availability.objects.create(person=self.guest, start_time=START + timedelta(hours=1),
                                    end_time=START + timedelta(hours=3), reason="Dentist",
                                    availability_type=Availability.AvailabilityType.OUT_OF_OFFICE)
        answer = self.status(self.guest)
        self.assertEqual(answer["status"], "blocked")
        self.assertEqual(answer["conflict"], {"type": "Out of office", "reason": "Dentist",
                                              "blocks": True})
        self.assertEqual(len(answer["availabilities"]), 2)

    def test_a_non_blocking_entry_that_overlaps_reads_as_busy(self):
        Availability.objects.create(person=self.guest, start_time=START + timedelta(hours=1),
                                    end_time=START + timedelta(hours=5), blocks_scheduling=False,
                                    availability_type=Availability.AvailabilityType.BUSY,
                                    reason="Maybe")
        answer = self.status(self.guest)
        self.assertEqual(answer["status"], "busy")
        self.assertFalse(answer["conflict"]["blocks"])

    @skip("suspected bug: events/views.py _avail_status looks for an overlapping entry "
          "(`soft = overlapping.first()`) before the explicit AVAILABLE window, and a "
          "window that covers the event always overlaps it — so 'available' can never be "
          "returned: a person who marked themselves available shows as 'busy' with a "
          "conflict of type 'Available'")
    def test_a_window_marked_available_says_available(self):
        Availability.objects.create(person=self.guest, start_time=START - timedelta(days=1),
                                    end_time=START + timedelta(days=1), blocks_scheduling=False,
                                    availability_type=Availability.AvailabilityType.AVAILABLE)
        answer = self.status(self.guest)
        self.assertEqual((answer["status"], answer["conflict"]), ("available", None))

    def test_touching_edges_do_not_overlap(self):
        Availability.objects.create(person=self.guest, start_time=START - timedelta(hours=1),
                                    end_time=START,
                                    availability_type=Availability.AvailabilityType.BUSY)
        Availability.objects.create(person=self.guest, start_time=START + timedelta(hours=2),
                                    end_time=START + timedelta(hours=3),
                                    availability_type=Availability.AvailabilityType.BUSY)
        answer = self.status(self.guest)
        self.assertEqual((answer["status"], answer["conflict"], answer["availabilities"]),
                         ("unknown", None, []))

    def test_an_unknown_person_or_event_is_a_404(self):
        self.client.force_login(self.owner_user)
        url = reverse("events:event_availability_api")
        self.assertEqual(self.client.get(url, {"person_id": 999999,
                                               "event_id": self.open_day.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {
            "person_id": self.guest.pk,
            "event_id": "00000000-0000-0000-0000-000000000000"}).status_code, 404)


class InvitePageTests(EventCase):
    def setUp(self):
        self.invite = EventInvite.objects.create(event=self.sitting, person=self.guest)

    def respond(self, as_user, action):
        self.client.force_login(as_user)
        return self.client.post(reverse("events:invite_respond", args=[self.invite.pk]),
                                {"action": action})

    def test_the_invitee_accepts_or_declines(self):
        self.assertRedirects(self.respond(self.guest_user, "accept"),
                             reverse("events:my_invites"), fetch_redirect_response=False)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, EventInvite.Status.ACCEPTED)
        self.assertIsNotNone(self.invite.responded_at)
        self.respond(self.guest_user, "decline")
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, EventInvite.Status.DECLINED)

    def test_an_unknown_answer_changes_nothing(self):
        self.respond(self.guest_user, "maybe")
        self.invite.refresh_from_db()
        self.assertEqual((self.invite.status, self.invite.responded_at),
                         (EventInvite.Status.PENDING, None))

    def test_nobody_answers_for_somebody_else(self):
        for user in (self.owner_user, self.stranger_user,
                     User.objects.create_user("stray", password="pw")):
            with self.subTest(user=user.username):
                self.assertEqual(self.respond(user, "accept").status_code, 403)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, EventInvite.Status.PENDING)

    def test_my_invites_sorts_them_by_answer(self):
        EventInvite.objects.create(event=self.open_day, person=self.guest,
                                   status=EventInvite.Status.ACCEPTED)
        self.client.force_login(self.guest_user)
        context = self.client.get(reverse("events:my_invites")).context
        self.assertEqual(list(context["pending_invites"]), [self.invite])
        self.assertEqual([i.event for i in context["accepted_invites"]], [self.open_day])
        self.assertEqual(list(context["declined_invites"]), [])
        self.assertFalse(context["no_profile"])

    def test_a_login_without_a_person_has_no_invites_and_is_told_so(self):
        self.client.force_login(User.objects.create_user("stray", password="pw"))
        context = self.client.get(reverse("events:my_invites")).context
        self.assertTrue(context["no_profile"])
        self.assertEqual(list(context["pending_invites"]), [])


# ---------------------------------------------------------------------------
# The JSON doors
# ---------------------------------------------------------------------------


class CreateApiTests(EventCase):
    def create(self, payload, user=None, raw=None):
        self.client.force_login(user or self.guest_user)
        return self.client.post(reverse("events:api_list"),
                                raw if raw is not None else json.dumps(payload),
                                content_type="application/json")

    def good(self, **overrides):
        payload = {"title": "Hackday", "start_time": "2030-06-01T10:00:00",
                   "end_time": "2030-06-01T18:00:00"}
        payload.update(overrides)
        return payload

    def test_made_owned_and_organised_by_the_caller(self):
        response = self.create(self.good(public=False, capacity=12,
                                         requires_registration=True))
        self.assertEqual(response.status_code, 201)
        body = response.json()
        made = ScheduledEvent.objects.get(pk=body["id"])
        self.assertEqual((made.owner, made.public, made.capacity, made.requires_registration),
                         (self.guest, False, 12, True))
        self.assertEqual(body["organizers"], ["Guest"])
        self.assertEqual((body["accepted_count"], body["invite_count"]), (0, 0))

    def test_each_refusal_says_what_is_wrong(self):
        category = EventCategory.objects.create(name="Talks")
        cases = (
            (None, "not json", "Invalid JSON."),
            (self.good(title="   "), None, "Title is required."),
            (self.good(start_time="tomorrow"), None, "Invalid start_time or end_time (use ISO 8601)."),
            (self.good(end_time="2030-06-01T10:00:00"), None, "end_time must be after start_time."),
            (self.good(category_id=category.pk + 1000), None, "Category not found."),
            (self.good(address_id=999999), None, "Address not found."),
        )
        for payload, raw, error in cases:
            with self.subTest(error=error):
                response = self.create(payload, raw=raw)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], error)
        self.assertFalse(ScheduledEvent.objects.filter(title="Hackday").exists())

    def test_a_known_category_is_attached(self):
        category = EventCategory.objects.create(name="Talks")
        body = self.create(self.good(category_id=category.pk)).json()
        self.assertEqual((body["category"], body["category_id"]), ("Talks", category.pk))

    @skip("suspected bug: EventListApiView.post (events/api_views.py:97) passes the "
          "parsed time to make_aware, which raises for an aware datetime, so an ISO "
          "8601 time WITH an offset — the very shape this API's own GET returns — is "
          "refused with 400 'use ISO 8601'")
    def test_a_time_with_an_offset_is_accepted(self):
        response = self.create(self.good(start_time="2030-06-01T10:00:00+02:00",
                                         end_time="2030-06-01T18:00:00+02:00"))
        self.assertEqual(response.status_code, 201)


class InviteApiTests(EventCase):
    def invite(self, as_user, target, payload=None, raw=None):
        self.client.force_login(as_user)
        return self.client.post(reverse("events:api_invite", args=[target.pk]),
                                raw if raw is not None else json.dumps(payload or {}),
                                content_type="application/json")

    def test_an_organiser_invites_once(self):
        first = self.invite(self.owner_user, self.sitting, {"person_id": self.guest.pk})
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["created"])
        again = self.invite(self.owner_user, self.sitting, {"person_id": self.guest.pk})
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["created"])
        self.assertEqual(self.sitting.invites.count(), 1)

    def test_a_non_organiser_is_refused_before_anything_is_read(self):
        response = self.invite(self.guest_user, self.sitting, raw="not json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.sitting.invites.exists())

    def test_staff_may_invite_to_any_event(self):
        staff, _ = person("clerk", is_staff=True)
        self.assertEqual(self.invite(staff, self.sitting,
                                     {"person_id": self.guest.pk}).status_code, 201)

    def test_the_organisers_mistakes_are_named(self):
        self.assertEqual(self.invite(self.owner_user, self.sitting, raw="{").status_code, 400)
        self.assertEqual(self.invite(self.owner_user, self.sitting, {}).json()["error"],
                         "person_id is required.")
        self.assertEqual(self.invite(self.owner_user, self.sitting,
                                     {"person_id": 999999}).status_code, 404)
        self.client.force_login(self.owner_user)
        missing = self.client.post(reverse("events:api_invite",
                                           args=["00000000-0000-0000-0000-000000000000"]),
                                   "{}", content_type="application/json")
        self.assertEqual(missing.status_code, 404)


class RespondApiTests(EventCase):
    def setUp(self):
        self.invite = EventInvite.objects.create(event=self.sitting, person=self.guest)

    def respond(self, as_user, payload=None, raw=None, pk=None):
        self.client.force_login(as_user)
        return self.client.post(reverse("events:api_invite_respond", args=[pk or self.invite.pk]),
                                raw if raw is not None else json.dumps(payload or {}),
                                content_type="application/json")

    def test_the_invitee_answers(self):
        response = self.respond(self.guest_user, {"action": "decline"})
        self.assertEqual(response.json(), {"ok": True, "status": "declined"})
        self.invite.refresh_from_db()
        self.assertIsNotNone(self.invite.responded_at)

    def test_refusals(self):
        self.assertEqual(self.respond(self.stranger_user, {"action": "accept"}).status_code, 403)
        stray = User.objects.create_user("stray", password="pw")
        refused = self.respond(stray, {"action": "accept"})
        self.assertEqual((refused.status_code, refused.json()["error"]), (403, "No profile."))
        self.assertEqual(self.respond(self.guest_user, {"action": "maybe"}).status_code, 400)
        self.assertEqual(self.respond(self.guest_user, raw="[").status_code, 400)
        self.assertEqual(self.respond(self.guest_user, {"action": "accept"},
                                      pk="00000000-0000-0000-0000-000000000000").status_code, 404)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, EventInvite.Status.PENDING)


class ReadApiTests(EventCase):
    def test_my_invites_lists_mine_with_the_pending_count(self):
        EventInvite.objects.create(event=self.sitting, person=self.guest)
        EventInvite.objects.create(event=self.open_day, person=self.guest,
                                   status=EventInvite.Status.ACCEPTED)
        EventInvite.objects.create(event=self.open_day, person=self.stranger)
        self.client.force_login(self.guest_user)
        body = self.client.get(reverse("events:api_my_invites")).json()
        self.assertEqual(body["pending_count"], 1)
        self.assertEqual({i["event_title"] for i in body["invites"]}, {"Board sitting", "Open day"})

    def test_my_invites_for_a_login_without_a_person_is_empty(self):
        stray = User.objects.create_user("stray", password="pw")
        stray.groups.add(Group.objects.get(name="data_mesh"))
        self.client.force_login(stray)
        self.assertEqual(self.client.get(reverse("events:api_my_invites")).json(),
                         {"invites": [], "pending_count": 0})

    def test_the_form_data_lists_categories_by_name(self):
        EventCategory.objects.create(name="Workshops")
        EventCategory.objects.create(name="Assemblies")
        self.client.force_login(self.guest_user)
        body = self.client.get(reverse("events:api_form_data")).json()
        self.assertEqual([c["name"] for c in body["categories"]], ["Assemblies", "Workshops"])
        self.assertIn("Guest", {p["name"] for p in body["people"]})

    def test_the_list_splits_upcoming_from_past(self):
        event("Last year", start=timezone.now() - timedelta(days=365))
        self.client.force_login(self.stranger_user)
        body = self.client.get(reverse("events:api_list")).json()
        self.assertEqual((body["total"], body["past_count"], body["upcoming_count"]), (2, 1, 1))
        self.assertEqual([e["title"] for e in body["upcoming"]], ["Open day"])

    def test_the_detail_counts_invitations_and_answers(self):
        EventInvite.objects.create(event=self.sitting, person=self.guest,
                                   status=EventInvite.Status.ACCEPTED)
        EventInvite.objects.create(event=self.sitting, person=self.stranger)
        self.sitting.organizers.add(self.owner)
        self.client.force_login(self.owner_user)
        body = self.client.get(reverse("events:api_detail", args=[self.sitting.pk])).json()
        self.assertEqual((body["invite_count"], body["accepted_count"]), (2, 1))
        self.assertEqual(body["organizers"], ["Owner"])
        self.assertEqual(body["owner"], "Owner")
