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


class DetailPageVisibilityTests(TestCase):
    """The door the calendar's fix never reached.

    `EventCalendarView` was taught the rule in 8/2026; `EventDetailView` had
    no `get_queryset` AT ALL until 2026-09-06, so with only `model =` set it
    fell back to `_default_manager.all()` and `LoginRequiredMixin` gated on
    authentication alone. Any signed-in user holding a pk read any event —
    title, description, times, address, organisers — `public=False` included.

    The pk is a UUID, so not enumerable; it is not a secret either. It is
    embedded in the calendar payload and in the profile widget, and a former
    invitee keeps a working link forever.
    """

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "Test", "author": "t",
                                   "publication_year": 2026})
        cls.owner_user, cls.owner = _person("d_owner")
        cls.other_user, cls.other = _person("d_other")
        cls.invitee_user, cls.invitee = _person("d_invitee")
        cls.public_event = _event("Open day", public=True)
        cls.private_event = _event("Board sitting", public=False, owner=cls.owner)

    def _get(self, event, user=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse("events:event_detail", args=[event.pk]))

    def test_a_signed_in_stranger_cannot_open_a_private_event(self):
        self.assertEqual(self._get(self.private_event, self.other_user).status_code, 404)

    def test_it_is_404_and_not_403(self):
        """A refusal that distinguishes "not allowed" from "does not exist"
        tells a stranger the row is there."""
        response = self._get(self.private_event, self.other_user)
        self.assertNotEqual(response.status_code, 403)

    def test_the_owner_still_opens_it(self):
        self.assertEqual(self._get(self.private_event, self.owner_user).status_code, 200)

    def test_an_invitee_still_opens_it(self):
        EventInvite.objects.create(event=self.private_event, person=self.invitee)
        self.assertEqual(self._get(self.private_event, self.invitee_user).status_code, 200)

    def test_an_organizer_still_opens_it(self):
        self.private_event.organizers.add(self.other)
        self.assertEqual(self._get(self.private_event, self.other_user).status_code, 200)

    def test_a_public_event_opens_for_anybody_signed_in(self):
        self.assertEqual(self._get(self.public_event, self.other_user).status_code, 200)


class ApiVisibilityTests(TestCase):
    """The JSON twins, and the worse of the two leaks.

    `EventListApiView` was a bare `.all()` — it had never had a filter to
    drift from — so every private event on the platform was one authenticated
    GET away, in a shape built for machines to consume.

    Both callers are put in `data_mesh`, because these views are
    `MeshGatedApiView`: without the group every GET is 403 and the test would
    pass for the wrong reason, proving the mesh gate rather than the event
    filter. The mesh gate is a coarse "may this client read platform data at
    all"; what is under test is which events it gets once through.
    """

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import Group

        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "Test", "author": "t",
                                   "publication_year": 2026})
        cls.owner_user, cls.owner = _person("a_owner")
        cls.other_user, cls.other = _person("a_other")
        mesh, _ = Group.objects.get_or_create(name="data_mesh")
        cls.owner_user.groups.add(mesh)
        cls.other_user.groups.add(mesh)
        cls.public_event = _event("Open day", public=True)
        cls.private_event = _event("Board sitting", public=False, owner=cls.owner)

    def test_the_mesh_gate_is_still_in_front_of_all_of_it(self):
        """A caller outside `data_mesh` gets nothing at all, which is the
        layer these tests deliberately step past to reach the filter."""
        outsider_user, _ = _person("a_outsider")
        self.client.force_login(outsider_user)
        self.assertEqual(
            self.client.get(reverse("events:api_list")).status_code, 403)

    def test_the_list_api_hides_a_private_event_from_a_stranger(self):
        self.client.force_login(self.other_user)
        payload = self.client.get(reverse("events:api_list")).json()
        titles = {e["title"] for e in payload["events"]}
        self.assertIn("Open day", titles)
        self.assertNotIn("Board sitting", titles)

    def test_the_counts_agree_with_what_is_returned(self):
        """`total` is computed from the same list, so a filter applied to one
        and not the other would show a count nothing explains."""
        self.client.force_login(self.other_user)
        payload = self.client.get(reverse("events:api_list")).json()
        self.assertEqual(payload["total"], len(payload["events"]))

    def test_the_owner_still_sees_their_own(self):
        self.client.force_login(self.owner_user)
        payload = self.client.get(reverse("events:api_list")).json()
        self.assertIn("Board sitting", {e["title"] for e in payload["events"]})

    def test_the_detail_api_refuses_a_private_event(self):
        self.client.force_login(self.other_user)
        response = self.client.get(
            reverse("events:api_detail", args=[self.private_event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_the_detail_api_still_serves_the_owner(self):
        self.client.force_login(self.owner_user)
        response = self.client.get(
            reverse("events:api_detail", args=[self.private_event.pk]))
        self.assertEqual(response.status_code, 200)


class OneRuleTests(TestCase):
    """The list and the detail page must answer the same question.

    Asserted against the SHARED function rather than by comparing two views,
    because the drift this file exists to stop was exactly two doors holding
    two different rules.
    """

    @classmethod
    def setUpTestData(cls):
        cls.owner_user, cls.owner = _person("r_owner")
        cls.other_user, cls.other = _person("r_other")
        cls.private_event = _event("Board sitting", public=False, owner=cls.owner)

    def test_may_read_agrees_with_visible_events(self):
        from .access import may_read, visible_events

        for user in (self.owner_user, self.other_user, None):
            with self.subTest(user=getattr(user, "username", "anonymous")):
                in_queryset = visible_events(user).filter(
                    pk=self.private_event.pk).exists()
                self.assertEqual(may_read(user, self.private_event), in_queryset)

    def test_anonymous_gets_the_public_arm_only(self):
        from .access import visible_events

        self.assertEqual(
            list(visible_events(None).values_list("public", flat=True)) or [True],
            [True] * visible_events(None).count() or [True])
        self.assertFalse(
            visible_events(None).filter(pk=self.private_event.pk).exists())


class TheGridActuallyRendersTests(TestCase):
    """The partial is included, and the page still draws a calendar.

    Worth its own test because the swap to the shared partial is exactly the
    kind of change that passes every queryset assertion while rendering an
    empty box — the events all reach the context and none reach the page.
    """

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "Test", "author": "t",
                                   "publication_year": 2026})
        cls.user, cls.person = _person("g_viewer")
        _event("Open day", public=True)

    def test_the_page_carries_the_grid_and_its_payload(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("events:event_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The mount the partial makes from `id="events"`.
        self.assertIn('id="events_calendar"', body)
        # The library, exactly once — the partial guards against a second
        # <script src>, which would re-execute the bundle and race the init.
        self.assertEqual(body.count("fullcalendar.js"), 1)
        # And the event reached the page, not merely the context.
        self.assertIn("Open day", body)

    def test_the_payload_is_escaped_not_raw(self):
        """A title is user-written and lands inside a <script>. The page this
        replaced interpolated it with `|safe`."""
        from .calendar import calendar_payload

        event = _event("</script><b>x", public=True)
        payload = calendar_payload([event])
        self.assertIn("</script>", payload)      # the helper stores it verbatim
        self.client.force_login(self.user)
        body = self.client.get(reverse("events:event_list")).content.decode()
        # ...and the template must not let it close the script element.
        self.assertNotIn("</script><b>x", body)

    def test_the_payload_carries_what_the_grid_needs(self):
        from .calendar import calendar_payload
        import json

        event = _event("Open day 2", public=True)
        data = json.loads(calendar_payload([event]))
        self.assertEqual(len(data["events"]), 1)
        row = data["events"][0]
        self.assertEqual(row["title"], "Open day 2")
        for key in ("start", "end", "url"):
            self.assertTrue(row[key], f"{key} is empty")
