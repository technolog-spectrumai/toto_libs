"""The company on a map: the seat, the located shareholders, the radius.

Ported with the feature from the placidia truth book. The rules are the
Business Center's own: staff write, members read, and the list is the record
— the map only draws it.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.locations.models import Address

from toto.company.models import Company, Party, ShareClass
from toto.company.services.geography import parse_radius, survey

from .factories import CompanyFactoryMixin


class LocationsTestCase(CompanyFactoryMixin, TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.staff = get_user_model().objects.create_user(
            "boss", password="x", is_staff=True)
        self.member = get_user_model().objects.create_user("clerk", password="x")
        self.company = self.make_company()
        self.ada = self.make_party(self.company, "Ada")
        self.bob = self.make_party(self.company, "Bob")

    def url(self):
        return reverse("company:locations", args=[self.company.slug])

    def seat(self, lat=52.2297, lon=21.0122):
        self.company.headquarters = Address.objects.create(
            latitude=lat, longitude=lon)
        self.company.save(update_fields=["headquarters"])

    def place(self, party, lat, lon):
        party.location = Address.objects.create(latitude=lat, longitude=lon)
        party.save(update_fields=["location"])


class PageTests(LocationsTestCase):
    def test_the_tab_is_on_the_strip_and_the_page_renders(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Headquarters")
        self.assertContains(response, "company-map-data")

    def test_login_is_required(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)

    def test_the_map_payload_carries_the_seat_and_the_pins(self):
        self.seat()
        self.place(self.ada, 50.0647, 19.9450)          # Kraków
        self.client.force_login(self.member)
        body = self.client.get(self.url()).content.decode()
        marker = '<script id="company-map-data" type="application/json">'
        start = body.index(marker) + len(marker)
        payload = json.loads(body[start:body.index("</script>", start)])
        self.assertEqual(payload["origin"]["name"], self.company.name)
        self.assertEqual([m["name"] for m in payload["markers"]], ["Ada"])
        self.assertAlmostEqual(payload["markers"][0]["km"], 252, delta=5)


class WriteTests(LocationsTestCase):
    def test_staff_set_the_headquarters(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url(), {
            "action": "headquarters",
            "hq-latitude": "52.2297", "hq-longitude": "21.0122",
            "hq-locality_name": "Warszawa",
        }, follow=True)
        self.company.refresh_from_db()
        self.assertIsNotNone(self.company.headquarters)
        self.assertEqual(self.company.headquarters.latitude, 52.2297)
        self.assertContains(response, "Headquarters saved.")

    def test_staff_place_and_clear_a_shareholder(self):
        self.client.force_login(self.staff)
        self.client.post(self.url(), {
            "action": "party", "party_id": self.ada.pk,
            "party-latitude": "50.0647", "party-longitude": "19.9450",
        })
        self.ada.refresh_from_db()
        self.assertIsNotNone(self.ada.location)

        self.client.post(self.url(), {
            "action": "party_clear", "party_id": self.ada.pk})
        self.ada.refresh_from_db()
        self.assertIsNone(self.ada.location)

    def test_a_member_may_read_but_never_write(self):
        self.client.force_login(self.member)
        response = self.client.post(self.url(), {
            "action": "headquarters",
            "hq-latitude": "1", "hq-longitude": "1"})
        self.assertEqual(response.status_code, 403)
        self.company.refresh_from_db()
        self.assertIsNone(self.company.headquarters)

    def test_one_coordinate_without_the_other_is_refused(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url(), {
            "action": "headquarters", "hq-latitude": "52.0"})
        self.assertContains(response, "Give both coordinates, or neither.")
        self.company.refresh_from_db()
        self.assertIsNone(self.company.headquarters)

    def test_a_stranger_company_party_cannot_be_placed(self):
        other = self.make_company("Other Co")
        stranger = Party.objects.create(company=other, name="Zed")
        self.client.force_login(self.staff)
        response = self.client.post(self.url(), {
            "action": "party", "party_id": stranger.pk,
            "party-latitude": "1", "party-longitude": "1"})
        self.assertEqual(response.status_code, 404)


class RadiusTests(LocationsTestCase):
    def setUp(self):
        super().setUp()
        self.seat()                                     # Warszawa
        self.place(self.ada, 50.0647, 19.9450)          # Kraków ~252 km
        self.place(self.bob, 52.4064, 16.9252)          # Poznań ~279 km

    def test_the_radius_filters_from_the_seat(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url() + "?radius=260")
        self.assertContains(response, "Ada")
        self.assertNotContains(response, "Bob")

    def test_survey_reports_distance_and_the_unlocated(self):
        cleo = self.make_party(self.company, "Cleo")
        placed, unlocated, origin = survey(
            self.company, list(self.company.parties.all()))
        self.assertEqual([row.party.name for row in placed], ["Ada", "Bob"])
        self.assertEqual([p.name for p in unlocated], ["Cleo"])
        self.assertIsNotNone(origin)

    def test_garbage_radius_means_no_filter(self):
        self.assertIsNone(parse_radius("banana"))
        self.assertIsNone(parse_radius("-3"))
        self.assertEqual(parse_radius("260"), 260.0)
