"""The ``events_read`` connector a workflow reads events through.

It runs for nobody in particular, so by default it reads what anybody may:
public events only (``public_only`` defaults to true). Windows, filters and
the four resources are asserted here, with the refusals a workflow author
meets when a value is malformed.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.events.tests_more_connectors
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.core.connectors import ConnectorExecutionError, execute_connector_type
from toto.people.models import Person

from .connectors import EventsReadConnector
from .models import Availability, EventCategory, EventInvite, ScheduledEvent

User = get_user_model()

T0 = datetime(2030, 6, 1, 9, 0, tzinfo=dt_timezone.utc)


def read(config):
    return execute_connector_type("events_read", config, {})["data"]


class ConnectorCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.talks = EventCategory.objects.create(name="Talks", description="Speakers")
        cls.ada = Person.objects.create(user=User.objects.create_user("ada", password="pw"),
                                        display_name="Ada")
        cls.bob = Person.objects.create(user=User.objects.create_user("bob", password="pw"),
                                        display_name="Bob")
        cls.keynote = ScheduledEvent.objects.create(
            title="Keynote", description="Opening talk", category=cls.talks, owner=cls.ada,
            start_time=T0, end_time=T0 + timedelta(hours=1))
        cls.keynote.organizers.add(cls.ada, cls.bob)
        cls.picnic = ScheduledEvent.objects.create(
            title="Picnic", start_time=T0 + timedelta(days=2),
            end_time=T0 + timedelta(days=2, hours=3), owner=cls.bob)
        cls.board = ScheduledEvent.objects.create(
            title="Board sitting", public=False, owner=cls.ada,
            start_time=T0 + timedelta(days=1), end_time=T0 + timedelta(days=1, hours=2))


class ScheduledEventTests(ConnectorCase):
    def titles(self, **config):
        return [e["title"] for e in read({"resource": "scheduled_event", **config})["events"]]

    def test_a_private_event_is_never_read_unless_asked_for(self):
        self.assertEqual(set(self.titles()), {"Keynote", "Picnic"})
        self.assertIn("Board sitting", self.titles(public_only=False))
        with self.assertRaises(ConnectorExecutionError):
            read({"resource": "scheduled_event", "action": "get", "id": str(self.board.pk)})

    def test_the_window_filters_bound_start_and_end(self):
        self.assertEqual(self.titles(starts_after=(T0 + timedelta(hours=1)).isoformat()),
                         ["Picnic"])
        self.assertEqual(self.titles(starts_before=T0.isoformat()), ["Keynote"])
        self.assertEqual(self.titles(ends_before=(T0 + timedelta(hours=1)).isoformat()),
                         ["Keynote"])
        self.assertEqual(self.titles(ends_after=(T0 + timedelta(days=2)).isoformat()), ["Picnic"])

    def test_a_malformed_time_names_its_field(self):
        with self.assertRaises(ConnectorExecutionError) as caught:
            read({"resource": "scheduled_event", "starts_after": "next tuesday"})
        self.assertIn("starts_after must be an ISO datetime", str(caught.exception))

    def test_owner_organiser_and_category_narrow_the_list(self):
        self.assertEqual(self.titles(owner_slug=self.bob.slug), ["Picnic"])
        self.assertEqual(self.titles(organizer_slug=self.bob.slug), ["Keynote"])
        self.assertEqual(self.titles(category="Talks"), ["Keynote"])

    def test_a_search_matches_title_description_or_category(self):
        self.assertEqual(self.titles(action="search", query="opening"), ["Keynote"])
        self.assertEqual(self.titles(action="search", query="talks"), ["Keynote"])
        self.assertEqual(self.titles(action="search"), [])

    def test_an_event_is_serialised_with_its_people(self):
        event = read({"resource": "scheduled_event", "action": "get",
                      "id": str(self.keynote.pk)})["event"]
        self.assertEqual(event["category"]["name"], "Talks")
        self.assertEqual(event["owner"]["display_name"], "Ada")
        self.assertEqual({p["display_name"] for p in event["organizers"]}, {"Ada", "Bob"})
        self.assertEqual(event["start_time"], T0.isoformat())
        self.assertIsNone(event["address"])

    def test_the_limit_caps_the_list(self):
        self.assertEqual(len(self.titles(limit=1)), 1)


class OtherResourceTests(ConnectorCase):
    def test_categories_list_search_and_get(self):
        EventCategory.objects.create(name="Assemblies")
        listed = read({"resource": "category"})["categories"]
        self.assertEqual([c["name"] for c in listed], ["Assemblies", "Talks"])
        found = read({"resource": "category", "action": "search", "query": "speak"})
        self.assertEqual([c["name"] for c in found["categories"]], ["Talks"])
        self.assertEqual(read({"resource": "category", "action": "get", "name": "Talks"})
                         ["category"]["description"], "Speakers")

    def test_invites_filter_by_person_status_and_event(self):
        EventInvite.objects.create(event=self.keynote, person=self.bob,
                                   status=EventInvite.Status.ACCEPTED)
        EventInvite.objects.create(event=self.picnic, person=self.ada)
        rows = read({"resource": "invite", "person_slug": self.bob.slug})["invites"]
        self.assertEqual([(r["event"]["title"], r["status"]) for r in rows],
                         [("Keynote", "accepted")])
        self.assertEqual(len(read({"resource": "invite", "status": "pending"})["invites"]), 1)
        self.assertEqual(len(read({"resource": "invite",
                                   "event_id": str(self.picnic.pk)})["invites"]), 1)

    def test_availability_filters_by_person_type_and_window(self):
        Availability.objects.create(person=self.ada, start_time=T0, end_time=T0 + timedelta(hours=4),
                                    availability_type=Availability.AvailabilityType.BUSY,
                                    reason="Keynote prep")
        Availability.objects.create(person=self.bob, start_time=T0 + timedelta(days=5),
                                    end_time=T0 + timedelta(days=6),
                                    availability_type=Availability.AvailabilityType.OUT_OF_OFFICE)
        rows = read({"resource": "availability", "person_slug": self.ada.slug})["availabilities"]
        self.assertEqual([r["reason"] for r in rows], ["Keynote prep"])
        self.assertTrue(rows[0]["blocks_scheduling"])
        self.assertEqual(len(read({"resource": "availability",
                                   "availability_type": "out_of_office"})["availabilities"]), 1)
        self.assertEqual(len(read({"resource": "availability",
                                   "starts_after": (T0 + timedelta(days=1)).isoformat()})
                             ["availabilities"]), 1)

    def test_an_unknown_resource_is_refused_at_validation_and_at_run_time(self):
        errors = EventsReadConnector(config={"resource": "rooms"}).validate()
        self.assertTrue(any("must be one of" in error for error in errors))
        with self.assertRaises(ConnectorExecutionError):
            read({"resource": "rooms"})

    def test_an_unknown_action_is_a_validation_error(self):
        self.assertTrue(EventsReadConnector(config={"action": "delete"}).validate())
        self.assertEqual(EventsReadConnector(config={}).validate(), [])
