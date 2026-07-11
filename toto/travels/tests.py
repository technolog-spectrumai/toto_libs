from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from toto.core.models import Platform
from toto.locations.models import Address, Route
from toto.people.models import Person
from toto.telegraph.testutils import login_mesh_member
from toto.travels.models import Travel, Visit


def _route(**kwargs):
    kwargs.setdefault("name", "Test Route")
    kwargs.setdefault("geometry", "MULTILINESTRING((0 0, 1 1))")
    return Route.objects.create(**kwargs)


def _active_platform():
    # PageProcessor.decorate() raises Http404 without an active Platform, so any
    # test that renders a page needs one.
    return Platform.objects.create(
        site_name="Toto", author="T", publication_year=2026, active=True,
    )


class TravelModelTests(TestCase):
    def test_score_out_of_range_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Travel.objects.create(
                    score=6,
                    starts_at=timezone.now(),
                    ends_at=timezone.now() + timedelta(hours=2),
                )

    def test_duration_display(self):
        start = timezone.now()
        travel = Travel.objects.create(
            starts_at=start,
            ends_at=start + timedelta(days=2, hours=3),
        )
        self.assertEqual(travel.duration_display, "2d 3h")


class VisitModelTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(display_name="Visitor One")

    def test_score_zero_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Visit.objects.create(participant=self.person, score=0)

    def test_length_of_stay_display(self):
        now = timezone.now()
        visit = Visit.objects.create(
            participant=self.person,
            visited_at=now,
            ends_at=now + timedelta(hours=5, minutes=30),
        )
        self.assertEqual(visit.length_of_stay_display, "5h 30m")


class TravelViewTests(TestCase):
    def setUp(self):
        _active_platform()
        user = login_mesh_member(self)
        self.person = Person.objects.create(user=user, display_name="Mesh Member")
        start = timezone.now()
        self.travel = Travel.objects.create(
            starts_at=start,
            ends_at=start + timedelta(hours=4),
            route=_route(notes="Steep climb near the summit."),
        )
        self.travel.participants.add(self.person)

    def test_travels_list_shows_travels_not_visits_section(self):
        res = self.client.get("/travels/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Travel log")
        # The visits list lives on its own page now.
        self.assertNotContains(res, "Visit log")

    def test_visits_list_page(self):
        res = self.client.get("/travels/visits/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Visit log")

    def test_metrics_page(self):
        res = self.client.get("/travels/metrics/")
        self.assertEqual(res.status_code, 200)

    def test_travel_review_shows_route_notes(self):
        res = self.client.get(f"/travels/travels/{self.travel.pk}/review/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Steep climb near the summit.")


class VisitReviewTests(TestCase):
    def setUp(self):
        _active_platform()
        user = login_mesh_member(self)
        self.person = Person.objects.create(user=user, display_name="Reviewer")
        self.address = Address.objects.create(
            locality_name="Paris",
            geometry="POINT(2.35 48.85)",
            note="Ring the second buzzer.",
        )

    def test_visit_review_shows_address_note(self):
        res = self.client.get(f"/travels/visits/{self.address.pk}/review/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Ring the second buzzer.")

    def test_submit_visit_review_creates_visit(self):
        res = self.client.post(
            f"/travels/visits/{self.address.pk}/review/submit/",
            {"score": "4", "review": "Lovely spot."},
        )
        self.assertEqual(res.status_code, 302)
        visit = Visit.objects.get(participant=self.person, location=self.address)
        self.assertEqual(visit.score, 4)
        self.assertEqual(visit.review, "Lovely spot.")


class LocationsTabTests(TestCase):
    def setUp(self):
        _active_platform()
        login_mesh_member(self)

    def test_locations_map_shows_travel_and_visit_tabs(self):
        res = self.client.get("/locations/")
        self.assertEqual(res.status_code, 200)
        # Both sub-tab links are mounted on the Locations tab bar.
        self.assertContains(res, 'href="/travels/"')
        self.assertContains(res, 'href="/travels/visits/"')
        self.assertContains(res, "fa-suitcase-rolling")  # unique to the Travels tab


class LocationNoteTests(TestCase):
    def setUp(self):
        _active_platform()
        login_mesh_member(self)
        self.address = Address.objects.create(locality_name="Berlin", geometry="POINT(13.4 52.5)")
        self.route = _route(name="Alpine Loop")

    def test_note_save_persists_address_note(self):
        res = self.client.post(
            f"/locations/note/address/{self.address.pk}/save/",
            {"note": "Blue door, top floor."},
        )
        self.assertEqual(res.status_code, 302)
        self.address.refresh_from_db()
        self.assertEqual(self.address.note, "Blue door, top floor.")

    def test_note_save_persists_route_notes(self):
        res = self.client.post(
            f"/locations/note/route/{self.route.pk}/save/",
            {"note": "Closed in winter."},
        )
        self.assertEqual(res.status_code, 302)
        self.route.refresh_from_db()
        self.assertEqual(self.route.notes, "Closed in winter.")

    def test_detail_page_contains_note_form(self):
        res = self.client.get(f"/locations/detail/address/{self.address.pk}/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, f"/locations/note/address/{self.address.pk}/save/")

    def test_map_payload_includes_note_key(self):
        self.address.note = "On the map."
        self.address.save(update_fields=["note"])
        res = self.client.get("/locations/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "On the map.")
