"""Reasons only after accepting (2026-10-02, the owner's decision on the
residual of crown 41's HUNT 2).

Crown 41 left an organiser seeing the availability of the people INVITED to
their event, reasons included. Any member could still make a year-long
event — it overlaps every period — invite Bob, and read every reason Bob had
typed for the year without Bob saying yes to anything.

Now an organiser sees an invitee's periods (kind and times) whatever they
answered, and the reason only once the invitee has ACCEPTED — and only on
the periods that overlap the event's own times. The person always sees
their own. No staff or superuser shortcut.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.events.tests_availability_reasons
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse

from toto.people.models import Person

from .access import may_see_availability, may_see_availability_reasons
from .models import Availability, EventInvite, ScheduledEvent
from .tests_more_access import START, EventCase, person

User = get_user_model()

INSIDE = "Oncology appointment"
OUTSIDE = "Court hearing"


class ReasonsCase(EventCase):
    """The owner's private retreat, three hours from START, and Bob with one
    period inside it and one ten days later."""

    def setUp(self):
        self.bob_user, self.bob = person("bob")
        self.retreat = ScheduledEvent.objects.create(
            title="Retreat", start_time=START, end_time=START + timedelta(hours=3),
            public=False, owner=self.owner)
        self.retreat.organizers.add(self.owner)
        Availability.objects.create(
            person=self.bob, start_time=START + timedelta(hours=1),
            end_time=START + timedelta(hours=2), reason=INSIDE,
            availability_type=Availability.AvailabilityType.OUT_OF_OFFICE)
        Availability.objects.create(
            person=self.bob, start_time=START + timedelta(days=10),
            end_time=START + timedelta(days=10, hours=2), reason=OUTSIDE,
            availability_type=Availability.AvailabilityType.BUSY)

    # -- the doors, used as a member uses them ------------------------------

    def invite(self, who=None, target=None, by=None):
        """Through the plan page's form, as an organiser invites."""
        self.client.force_login(by or self.owner_user)
        self.client.post(reverse("events:event_plan", args=[(target or self.retreat).pk]),
                         {"person_ids": [(who or self.bob).pk]})
        return EventInvite.objects.get(event=target or self.retreat, person=who or self.bob)

    def answer(self, invite, action, as_user=None):
        """Through the invitee's own answer page."""
        self.client.force_login(as_user or self.bob_user)
        self.client.post(reverse("events:invite_respond", args=[invite.pk]), {"action": action})
        invite.refresh_from_db()
        return invite

    def ask(self, as_user, who=None, target=None):
        self.client.force_login(as_user)
        return self.client.get(reverse("events:event_availability_api"), {
            "person_id": (who or self.bob).pk, "event_id": (target or self.retreat).pk})

    def plan(self, as_user, target=None):
        self.client.force_login(as_user)
        return self.client.get(reverse("events:event_plan", args=[(target or self.retreat).pk]))

    def bob_row(self, page):
        return {row["person"].pk: row for row in page.context["invite_rows"]}[self.bob.pk]

    # -- what a refusal and a reason-less answer look like ------------------

    def assertPeriodsWithoutReasons(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(INSIDE, response.content.decode())
        self.assertNotIn(OUTSIDE, response.content.decode())
        body = response.json()
        self.assertFalse(body["reasons_shown"])
        self.assertEqual(body["status"], "blocked")
        self.assertEqual(body["conflict"], {"type": "Out of office", "reason": None,
                                            "blocks": True})
        self.assertEqual([(a["type"], a["reason"]) for a in body["availabilities"]],
                         [("Out of office", None)])
        self.assertTrue(all(a["start"] and a["end"] for a in body["availabilities"]))

    def assertPlanWithoutReasons(self, page):
        self.assertEqual(page.status_code, 200)
        row = self.bob_row(page)
        self.assertEqual(row["avail_status"], "blocked")
        self.assertEqual(row["conflict"], {"type": "Out of office", "reason": None,
                                           "blocks": True})
        self.assertNotContains(page, INSIDE)
        self.assertNotContains(page, OUTSIDE)

    def assertRefused(self, response, page):
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(INSIDE, response.content.decode())
        self.assertEqual(page.status_code, 200)
        self.assertEqual((self.bob_row(page)["avail_status"], self.bob_row(page)["conflict"]),
                         (None, None))
        self.assertNotContains(page, INSIDE)
        self.assertNotContains(page, OUTSIDE)


class InvitationAnswerTests(ReasonsCase):
    def test_a_pending_invitee_shows_periods_without_reasons(self):
        self.assertEqual(self.invite().status, EventInvite.Status.PENDING)
        self.assertPeriodsWithoutReasons(self.ask(self.owner_user))
        self.assertPlanWithoutReasons(self.plan(self.owner_user))

    def test_an_accepted_invitee_shows_reasons_within_the_event_only(self):
        self.answer(self.invite(), "accept")
        response = self.ask(self.owner_user)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["conflict"]["reason"], INSIDE)
        self.assertEqual([a["reason"] for a in body["availabilities"]], [INSIDE])
        self.assertNotIn(OUTSIDE, response.content.decode())
        self.assertTrue(body["reasons_shown"])
        page = self.plan(self.owner_user)
        self.assertEqual(self.bob_row(page)["conflict"]["reason"], INSIDE)
        self.assertContains(page, INSIDE)
        self.assertNotContains(page, OUTSIDE)

    def test_a_declined_invitee_shows_periods_without_reasons(self):
        self.answer(self.invite(), "decline")
        self.assertPeriodsWithoutReasons(self.ask(self.owner_user))
        self.assertPlanWithoutReasons(self.plan(self.owner_user))

    def test_declining_after_accepting_takes_the_reasons_back(self):
        invite = self.answer(self.invite(), "accept")
        self.assertTrue(self.ask(self.owner_user).json()["reasons_shown"])
        self.answer(invite, "decline")
        self.assertPeriodsWithoutReasons(self.ask(self.owner_user))

    def test_accepting_one_event_opens_no_reasons_on_another(self):
        """An answer belongs to its event: the owner's second event, where
        Bob's invitation is still pending, shows his periods without them."""
        self.answer(self.invite(), "accept")
        dinner = ScheduledEvent.objects.create(
            title="Dinner", start_time=START, end_time=START + timedelta(hours=4),
            public=False, owner=self.owner)
        self.invite(target=dinner)
        self.assertFalse(may_see_availability_reasons(self.owner_user, self.bob, dinner))
        self.assertPeriodsWithoutReasons(self.ask(self.owner_user, target=dinner))


class EventDatesTests(ReasonsCase):
    def test_a_period_outside_the_events_times_is_not_shown(self):
        self.answer(self.invite(), "accept")
        for user in (self.owner_user, self.bob_user):
            with self.subTest(user=user.username):
                response = self.ask(user)
                self.assertEqual(len(response.json()["availabilities"]), 1)
                self.assertNotIn(OUTSIDE, response.content.decode())
                self.assertNotIn("Busy", [a["type"] for a in response.json()["availabilities"]])

    def test_a_year_long_event_reads_no_reason_until_its_invitee_accepts(self):
        """The residual itself: a member makes an event spanning a year,
        which overlaps every period Bob has, and invites him."""
        year = ScheduledEvent.objects.create(
            title="My year", start_time=START - timedelta(days=30),
            end_time=START + timedelta(days=330), public=False, owner=self.stranger)
        year.organizers.add(self.stranger)
        invite = self.invite(target=year, by=self.stranger_user)
        response = self.ask(self.stranger_user, target=year)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(INSIDE, response.content.decode())
        self.assertNotIn(OUTSIDE, response.content.decode())
        self.assertEqual(len(response.json()["availabilities"]), 2)       # periods, no reasons
        self.assertEqual({a["reason"] for a in response.json()["availabilities"]}, {None})
        self.assertFalse(response.json()["reasons_shown"])
        self.assertNotContains(self.plan(self.stranger_user, target=year), INSIDE)
        # Bob says no: still nothing.
        self.answer(invite, "decline")
        self.assertNotIn(INSIDE, self.ask(self.stranger_user, target=year).content.decode())


class WhoAsksTests(ReasonsCase):
    def test_a_co_organiser_sees_what_the_owner_sees(self):
        self.retreat.organizers.add(self.guest)
        invite = self.invite()
        self.assertPeriodsWithoutReasons(self.ask(self.guest_user))
        self.answer(invite, "accept")
        self.assertEqual(self.ask(self.guest_user).json()["conflict"]["reason"], INSIDE)

    def test_a_non_organiser_sees_neither_periods_nor_reasons(self):
        """The guest is invited too, and so may read the plan, and has
        accepted — none of which makes them an organiser."""
        self.answer(self.invite(), "accept")
        self.answer(self.invite(who=self.guest), "accept", as_user=self.guest_user)
        self.assertFalse(may_see_availability(self.guest_user, self.bob, self.retreat))
        self.assertRefused(self.ask(self.guest_user), self.plan(self.guest_user))

    def test_staff_and_superusers_have_no_shortcut(self):
        self.answer(self.invite(), "accept")
        clerk_user, clerk = person("clerk", is_staff=True)
        root_user, root = person("root", is_staff=True, is_superuser=True)
        bare_root = User.objects.create_superuser("bareroot", "bare@example.invalid", "pw")
        # Invited and accepted themselves, so the private plan opens to them.
        for user, who in ((clerk_user, clerk), (root_user, root)):
            self.answer(self.invite(who=who), "accept", as_user=user)
        for user in (clerk_user, root_user):
            with self.subTest(user=user.username):
                self.assertRefused(self.ask(user), self.plan(user))
        with self.subTest(user="bareroot"):
            # No Person and no invitation: the private event is a missing one.
            self.assertEqual(self.ask(bare_root).status_code, 404)
            self.assertEqual(self.plan(bare_root).status_code, 404)
        Person.objects.create(user=bare_root, display_name="Bare root")
        self.retreat.public = True
        self.retreat.save(update_fields=["public"])
        with self.subTest(user="bareroot with a person, public event"):
            self.assertRefused(self.ask(bare_root), self.plan(bare_root))


class OwnAvailabilityTests(ReasonsCase):
    def test_the_person_sees_their_own_reasons_whatever_they_answered(self):
        self.retreat.public = True
        self.retreat.save(update_fields=["public"])
        cases = (("not invited", None), ("pending", "pending"),
                 ("declined", "decline"), ("accepted", "accept"))
        invite = None
        for label, action in cases:
            with self.subTest(label):
                if action is not None and invite is None:
                    invite = self.invite()
                if action in ("accept", "decline"):
                    self.answer(invite, action)
                response = self.ask(self.bob_user)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["conflict"]["reason"], INSIDE)
                self.assertNotIn(OUTSIDE, response.content.decode())
                self.assertTrue(response.json()["reasons_shown"])
