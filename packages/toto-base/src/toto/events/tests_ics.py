"""Importing an .ics file into the calendar (2026-10-12; ``ics.py``,
``views.import_ics``).

    manage.py test toto.events.tests_ics
"""

from datetime import datetime, timedelta, timezone as utc_zone
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person

from . import ics
from .models import ScheduledEvent

WARSAW = ZoneInfo("Europe/Warsaw")

#: The owner's example (dracena/data/cal.ics): two rides, each with a reminder.
RIDES = """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
BEGIN:VEVENT
SUMMARY:Access-A-Ride Pickup
DTSTART;TZID=America/New_York:20130802T103400
DTEND;TZID=America/New_York:20130802T110400
LOCATION:1000 Broadway Ave.\\, Brooklyn
DESCRIPTION: Access-A-Ride trip to 900 Jay St.\\, Brooklyn
STATUS:CONFIRMED
SEQUENCE:3
BEGIN:VALARM
TRIGGER:-PT10M
DESCRIPTION:Pickup Reminder
ACTION:DISPLAY
END:VALARM
END:VEVENT
BEGIN:VEVENT
SUMMARY:Access-A-Ride Pickup
DTSTART;TZID=America/New_York:20130802T200000
DTEND;TZID=America/New_York:20130802T203000
LOCATION:900 Jay St.\\, Brooklyn
DESCRIPTION: Access-A-Ride trip to 1000 Broadway Ave.\\, Brooklyn
END:VEVENT
END:VCALENDAR
"""


def calendar(*events):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(
        "BEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\nEND:VEVENT\r\n" for lines in events) + "END:VCALENDAR\r\n"


class ReaderTests(SimpleTestCase):
    def test_the_example_is_two_meetings_in_new_york(self):
        parsed = ics.parse(RIDES.encode(), WARSAW)
        self.assertEqual(len(parsed.meetings), 2)
        first = parsed.meetings[0]
        self.assertEqual(first.title, "Access-A-Ride Pickup")
        self.assertEqual(first.start, datetime(2013, 8, 2, 14, 34, tzinfo=utc_zone.utc))
        self.assertEqual(first.end - first.start, timedelta(minutes=30))
        self.assertEqual(first.place, "1000 Broadway Ave., Brooklyn")
        # The meeting's own description, not its reminder's.
        self.assertEqual(first.description, "Access-A-Ride trip to 900 Jay St., Brooklyn")
        self.assertEqual((parsed.repeating, parsed.unknown_zones, parsed.left_out), (0, 0, 0))

    def test_utc_floating_and_unknown_zones(self):
        parsed = ics.parse(calendar(
            ["SUMMARY:Z", "DTSTART:20260102T100000Z", "DTEND:20260102T110000Z"],
            ["SUMMARY:Floating", "DTSTART:20260102T100000"],
            ["SUMMARY:Nowhere", "DTSTART;TZID=Mars/Olympus:20260102T100000"]), WARSAW)
        z, floating, nowhere = parsed.meetings
        self.assertEqual(z.start, datetime(2026, 1, 2, 10, tzinfo=utc_zone.utc))
        self.assertEqual(floating.start, datetime(2026, 1, 2, 9, tzinfo=utc_zone.utc))     # Warsaw, winter
        self.assertEqual(floating.end - floating.start, timedelta(hours=1))                 # no end: an hour
        self.assertEqual(nowhere.start, floating.start)
        self.assertEqual(parsed.unknown_zones, 1)

    def test_an_all_day_meeting_a_duration_and_a_folded_line(self):
        parsed = ics.parse(calendar(
            ["SUMMARY:Holiday", "DTSTART;VALUE=DATE:20260501", "DTEND;VALUE=DATE:20260504"],
            ["SUMMARY:Long", "DTSTART:20260102T100000Z", "DURATION:PT1H30M"],
            ["SUMMARY:A very long", " title\\nsecond line", "DTSTART:20260103T100000Z"]), WARSAW)
        holiday, long, folded = parsed.meetings
        self.assertTrue(holiday.all_day)
        self.assertEqual(holiday.start, datetime(2026, 5, 1, tzinfo=WARSAW))
        self.assertEqual(holiday.end, datetime(2026, 5, 4, tzinfo=WARSAW))
        self.assertEqual(long.end - long.start, timedelta(minutes=90))
        self.assertEqual(folded.title, "A very longtitle\nsecond line")

    def test_a_series_comes_once_and_other_components_are_counted(self):
        text = calendar(["SUMMARY:Weekly", "DTSTART:20260105T090000Z", "RRULE:FREQ=WEEKLY"],
                        ["SUMMARY:No start"]).replace(
            "END:VCALENDAR", "BEGIN:VTODO\r\nSUMMARY:Buy milk\r\nEND:VTODO\r\nEND:VCALENDAR")
        parsed = ics.parse(text, WARSAW)
        self.assertEqual([m.title for m in parsed.meetings], ["Weekly"])
        self.assertTrue(parsed.meetings[0].repeats)
        self.assertEqual((parsed.repeating, parsed.left_out, parsed.without_time), (1, 1, 1))

    def test_what_is_not_a_calendar_is_refused(self):
        for data, code in ((b"", "empty"), (b"   \n", "empty"), (b"hello", "not_a_calendar"),
                           (b"x" * (ics.MAX_BYTES + 1), "too_large")):
            with self.assertRaises(ics.CalendarError) as caught:
                ics.parse(data, WARSAW)
            self.assertEqual(caught.exception.code, code)

    def test_no_more_than_the_cap(self):
        many = calendar(*[[f"SUMMARY:{n}", "DTSTART:20260102T100000Z"] for n in range(ics.MAX_EVENTS + 3)])
        parsed = ics.parse(many, WARSAW)
        self.assertEqual((len(parsed.meetings), parsed.over_the_cap), (ics.MAX_EVENTS, 3))


class ImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        User = get_user_model()
        cls.ada = User.objects.create_user("ada", password="pw")
        cls.person = Person.objects.create(user=cls.ada, display_name="Ada")
        cls.eve = User.objects.create_user("eve", password="pw")
        Person.objects.create(user=cls.eve, display_name="Eve")

    def post(self, user, data=RIDES.encode(), name="cal.ics"):
        self.client.force_login(user)
        return self.client.post(reverse("events:import_ics"),
                                {"file": SimpleUploadedFile(name, data, content_type="text/calendar")})

    def test_the_calendar_page_leads_to_the_import(self):
        self.client.force_login(self.ada)
        self.assertIn('data-testid="events-import-ics"',
                      self.client.get(reverse("events:event_list")).content.decode())
        page = self.client.get(reverse("events:import_ics"))
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-testid="events-import-file"', page.content.decode())

    def test_the_example_becomes_two_private_events_of_the_importers(self):
        response = self.post(self.ada)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"]["made"], 2)
        events = list(ScheduledEvent.objects.order_by("start_time"))
        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual((first.title, first.owner, first.public), ("Access-A-Ride Pickup", self.person, False))
        self.assertEqual(first.start_time, datetime(2013, 8, 2, 14, 34, tzinfo=utc_zone.utc))
        self.assertEqual(first.address, "1000 Broadway Ave., Brooklyn")
        self.assertEqual(list(first.organizers.all()), [self.person])
        # Private: another member does not read it.
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(reverse("events:event_detail", args=[first.pk])).status_code, 404)

    def test_importing_the_same_file_again_makes_nothing_twice(self):
        self.post(self.ada)
        again = self.post(self.ada)
        self.assertEqual((again.context["result"]["made"], again.context["result"]["already"]), (0, 2))
        self.assertEqual(ScheduledEvent.objects.count(), 2)

    def test_a_file_that_is_no_calendar_imports_nothing_and_says_so(self):
        for data in (b"hello", b""):
            response = self.post(self.ada, data)
            self.assertEqual(response.status_code, 200)
            self.assertIn('data-testid="events-import-error"', response.content.decode())
        self.client.force_login(self.ada)
        self.assertIn('data-testid="events-import-error"',
                      self.client.post(reverse("events:import_ics")).content.decode())
        self.assertFalse(ScheduledEvent.objects.exists())

    def test_signed_out_there_is_no_import(self):
        self.assertIn(self.client.get(reverse("events:import_ics")).status_code, (302, 403))
