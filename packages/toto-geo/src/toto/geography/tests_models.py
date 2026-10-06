"""The two types (2026-10-06): geometry on exactly two models, one link per
geometry row, and nothing of them in the admin.

    manage.py test toto.geography.tests_models
"""

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from django.apps import apps
from django.contrib import admin
from django.contrib.gis.db import models as gis
from django.db import models
from django.test import SimpleTestCase, TestCase

from toto.geography import saves, shapes
from toto.geography.models import (Address, CommunityHeadquarters, CommunityPin,
                                   CommunityZone, GeographyQuotaPolicy,
                                   GeographyUsageEvent, PersonAddress, Zone)
from toto.geography.testing import BOWTIE, SQUARE, community, member, op

COORDINATE_NAMES = {"lat", "lng", "lon", "latitude", "longitude"}


def geometry_fields():
    return [(model, field) for model in apps.get_models()
            for field in model._meta.get_fields()
            if isinstance(field, gis.GeometryField)]


class WholeProjectTests(SimpleTestCase):
    """Walks every installed model, not only this app's."""

    def test_geometry_is_on_exactly_two_models(self):
        found = sorted((model._meta.label, field.name) for model, field in geometry_fields()
                       if model._meta.app_label != "locations")
        self.assertEqual(found, [("geography.Address", "point"), ("geography.Zone", "outline")])

    def test_the_two_are_a_point_and_a_polygon_in_wgs84(self):
        point = Address._meta.get_field("point")
        outline = Zone._meta.get_field("outline")
        self.assertIs(type(point), gis.PointField)
        self.assertIs(type(outline), gis.PolygonField)
        self.assertEqual((point.srid, outline.srid), (4326, 4326))
        self.assertFalse(point.null)
        self.assertFalse(outline.null)

    def test_no_model_holds_a_line(self):
        lines = (gis.LineStringField, gis.MultiLineStringField)
        found = [(model._meta.label, field.name) for model, field in geometry_fields()
                 if isinstance(field, lines) and model._meta.app_label != "locations"]
        self.assertEqual(found, [])
        names = {model.__name__ for model in apps.get_app_config("geography").get_models()}
        self.assertEqual(names & {"Route", "RouteChain", "Territory", "MapLayer", "MapDomain"},
                         set())

    def test_no_model_keeps_a_coordinate_pair_in_numbers(self):
        numeric = (models.FloatField, models.DecimalField)
        found = sorted((model._meta.label, field.name) for model in apps.get_models()
                       if model._meta.app_label != "locations"
                       for field in model._meta.get_fields()
                       if isinstance(field, numeric) and field.name in COORDINATE_NAMES)
        self.assertEqual(found, [])

    def test_the_only_json_on_a_geography_model_is_the_usage_metadata(self):
        found = sorted((model.__name__, field.name)
                       for model in apps.get_app_config("geography").get_models()
                       for field in model._meta.get_fields()
                       if isinstance(field, models.JSONField))
        self.assertEqual(found, [("GeographyUsageEvent", "metadata")])

    def test_the_models_of_the_app(self):
        names = sorted(model.__name__ for model in apps.get_app_config("geography").get_models())
        self.assertEqual(names, ["Address", "CommunityHeadquarters", "CommunityPin",
                                 "CommunityZone", "GeographyQuotaPolicy",
                                 "GeographyUsageEvent", "PersonAddress", "PinComment",
                                 "Zone", "ZoneComment"])

    def test_the_admin_lists_no_geometry_and_no_link_to_one(self):
        registered = {model for model in admin.site._registry
                      if model._meta.app_label == "geography"}
        self.assertEqual(registered, {GeographyQuotaPolicy, GeographyUsageEvent})
        for model in registered:
            for field in model._meta.get_fields():
                self.assertNotIsInstance(field, gis.GeometryField)
                related = getattr(field, "related_model", None)
                self.assertNotIn(related, (Address, Zone, PersonAddress, CommunityHeadquarters))

    def test_the_usage_events_are_read_only_in_the_admin(self):
        usage = admin.site._registry[GeographyUsageEvent]
        self.assertFalse(usage.has_add_permission(None))
        self.assertFalse(usage.has_change_permission(None))
        self.assertFalse(usage.has_delete_permission(None))

    def test_toto_base_keeps_its_text(self):
        from toto.people.models import Person
        from toto.socialhub.models import Community

        self.assertIsInstance(Person._meta.get_field("address"), models.TextField)
        self.assertIsInstance(Person._meta.get_field("show_address"), models.BooleanField)
        self.assertIsInstance(Community._meta.get_field("seat"), models.CharField)

    def test_the_related_names_of_the_contract(self):
        self.assertEqual(PersonAddress._meta.get_field("person").remote_field.related_name,
                         "geography_address")
        self.assertEqual(
            CommunityHeadquarters._meta.get_field("community").remote_field.related_name,
            "geography_headquarters")
        for name in ("address", "zone"):
            field = CommunityHeadquarters._meta.get_field(name)
            self.assertTrue(field.one_to_one)
            self.assertTrue(field.null)


class ShapeTests(SimpleTestCase):
    def test_a_point_is_two_numbers_on_the_globe(self):
        self.assertEqual(shapes.clean_pair(52.2297001234, 21.0122), (52.2297, 21.0122))
        for lat, lng in ((91, 0), (0, 181), ("52", 21), (None, None), (True, 1),
                         (float("nan"), 1), (1, float("inf"))):
            with self.subTest(lat=lat, lng=lng), self.assertRaises(shapes.BadShape):
                shapes.clean_pair(lat, lng)

    def test_an_integer_too_large_for_a_float_is_no_coordinate(self):
        """JSON puts no limit on an integer's digits and Python reads them
        all: ``float()`` of one past 1e308 is an OverflowError, which is no
        ValueError and used to leave the door as a 500."""
        huge = 10 ** 400
        for lat, lng in ((huge, 0), (0, huge), (-huge, -huge)):
            with self.subTest(lat=str(lat)[:6]), self.assertRaises(shapes.BadShape):
                shapes.clean_pair(lat, lng)
        with self.assertRaises(shapes.BadShape):
            shapes.clean_end({"lat": huge, "lng": 0})
        with self.assertRaises(shapes.BadShape):
            shapes.clean_outline([[52, 21], [52, huge], [52.1, 21.1]])

    def test_a_route_end_is_a_pair_and_nothing_else(self):
        self.assertEqual(shapes.clean_end({"lat": 1, "lng": 2}), (1.0, 2.0))
        for end in ({"lat": 1, "lng": 2, "address": 3}, {"q": "Warsaw"}, {"address": 7},
                    {"lat": 1}, [1, 2], "Warsaw", 7, None):
            with self.subTest(end=end), self.assertRaises(shapes.BadShape):
                shapes.clean_end(end)

    def test_an_outline_is_one_closed_ring(self):
        corners = shapes.clean_outline(SQUARE)
        self.assertEqual(len(corners), 4)
        self.assertEqual(shapes.clean_outline(SQUARE + [SQUARE[0]]), corners)
        polygon = shapes.polygon_of(corners)
        self.assertEqual(polygon.num_interior_rings, 0)
        self.assertEqual(shapes.corners_of(polygon), SQUARE)

    def test_bad_outlines_are_refused(self):
        for outline in (None, "x", [], SQUARE[:2], [[1, 2], [1, 2], [3, 4]], [[1, 2, 3]] * 3,
                        [[91, 0], [0, 0], [0, 1]]):
            with self.subTest(outline=outline), self.assertRaises(shapes.BadShape):
                shapes.clean_outline(outline)
        with self.assertRaises(shapes.BadShape):
            shapes.polygon_of(shapes.clean_outline(BOWTIE))
        with self.assertRaises(shapes.BadShape):     # three corners on one line
            shapes.polygon_of(shapes.clean_outline([[0, 0], [0, 1], [0, 2]]))

    def test_five_hundred_corners_and_no_more(self):
        import math

        def ring(n):
            return [[52 + 0.1 * math.sin(2 * math.pi * i / n),
                     21 + 0.1 * math.cos(2 * math.pi * i / n)] for i in range(n)]

        self.assertEqual(len(shapes.clean_outline(ring(500))), 500)
        self.assertTrue(shapes.polygon_of(shapes.clean_outline(ring(500))).valid)
        with self.assertRaises(shapes.BadShape):
            shapes.clean_outline(ring(501))


class OneLinkPerGeometryTests(TestCase):
    """After every way a link can go, its geometry row is gone too."""

    def setUp(self):
        self.user, self.person = member("ada")
        self.head_user, self.head = member("hugo")
        self.community = community("Harbour", head=self.head)

    def assertNoOrphans(self):
        for address in Address.objects.all():
            links = (PersonAddress.objects.filter(address=address).count()
                     + CommunityHeadquarters.objects.filter(address=address).count()
                     + CommunityPin.objects.filter(address=address).count())
            self.assertEqual(links, 1, f"address {address.pk} has {links} links")
        for zone in Zone.objects.all():
            self.assertEqual(CommunityHeadquarters.objects.filter(zone=zone).count()
                             + CommunityZone.objects.filter(zone=zone).count(), 1)

    def fill(self):
        saves.save_person_point(self.user, self.person, lat=52.2, lng=21.0, name="Home",
                                note="", op=op())
        saves.save_headquarters(self.head_user, self.community, lat=54.3, lng=18.6,
                                name="HQ", note="", op=op())
        saves.save_zone(self.head_user, self.community, name="Area", description="",
                        outline=SQUARE, op=op())
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (2, 1))
        self.assertNoOrphans()

    def test_a_saved_point_is_a_geometry(self):
        self.fill()
        address = self.person.geography_address.address
        self.assertEqual((address.point.srid, address.point.y, address.point.x),
                         (4326, 52.2, 21.0))
        self.assertEqual(address.created_by, self.user)
        zone = CommunityHeadquarters.objects.get(community=self.community).zone
        self.assertEqual(zone.outline.geom_type, "Polygon")
        self.assertTrue(zone.outline.valid)

    def test_clearing_a_person_s_point_deletes_the_address(self):
        self.fill()
        self.assertTrue(saves.clear_person_point(self.user, self.person))
        self.assertEqual(Address.objects.count(), 1)
        self.assertFalse(PersonAddress.objects.exists())
        self.assertNoOrphans()
        self.assertFalse(saves.clear_person_point(self.user, self.person))

    def test_clearing_the_headquarters_keeps_the_zone_and_the_other_way(self):
        self.fill()
        self.assertTrue(saves.clear_headquarters(self.head_user, self.community))
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (1, 1))
        self.assertNoOrphans()
        self.assertTrue(saves.clear_zone(self.head_user, self.community))
        self.assertEqual(Zone.objects.count(), 0)
        self.assertFalse(CommunityHeadquarters.objects.exists(),
                         "a link with neither a point nor a zone is no row")
        self.assertNoOrphans()

    def test_deleting_the_person_deletes_their_address(self):
        self.fill()
        self.person.delete()
        self.assertEqual(Address.objects.count(), 1)
        self.assertNoOrphans()

    def test_deleting_the_account_deletes_their_address(self):
        self.fill()
        self.user.delete()
        self.assertFalse(PersonAddress.objects.exists())
        self.assertEqual(Address.objects.count(), 1)
        self.assertNoOrphans()

    def test_deleting_the_community_deletes_its_point_and_zone(self):
        self.fill()
        self.community.delete()
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (1, 0))
        self.assertNoOrphans()

    def test_deleting_a_link_row_itself_deletes_what_it_pointed_at(self):
        self.fill()
        PersonAddress.objects.all().delete()
        CommunityHeadquarters.objects.all().delete()
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (0, 0))

    def test_saving_writes_no_postal_text(self):
        self.fill()
        self.person.refresh_from_db()
        self.community.refresh_from_db()
        self.assertEqual(self.person.address or "", "")
        self.assertEqual(self.community.seat or "", "")
        self.assertEqual(self.person.geography_address.address.postal_address, "")


class BesideTheParkedMapTests(SimpleTestCase):
    """``toto.locations`` and ``toto.geography`` boot together: both have an
    Address and a Zone, and share no table, label or reverse name."""

    def test_check_and_makemigrations_with_both_installed(self):
        settings_text = textwrap.dedent('''
            from toto.registry import CORE_APPS, GEOGRAPHY_APPS, LOCATIONS_APPS
            SECRET_KEY = "test-only-not-a-secret"
            INSTALLED_APPS = [
                "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
                "django.contrib.sessions", "django.contrib.messages",
                "django.contrib.staticfiles", "django.contrib.gis",
                "rest_framework", "colorfield", "django_jsonform", "trix_editor",
                *CORE_APPS, *LOCATIONS_APPS, *GEOGRAPHY_APPS,
            ]
            MIDDLEWARE = [
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ]
            ROOT_URLCONF = "both_urls"
            TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
                          "APP_DIRS": True, "OPTIONS": {"context_processors": [
                              "django.template.context_processors.request",
                              "django.contrib.auth.context_processors.auth",
                              "django.contrib.messages.context_processors.messages"]}}]
            DATABASES = {"default": {"ENGINE": "django.contrib.gis.db.backends.spatialite",
                                     "NAME": __import__("os").environ["BOTH_DB"]}}
            SPATIALITE_LIBRARY_PATH = "mod_spatialite"
            DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
            STATIC_URL = "static/"
            HAS_GIS = True
        ''')
        with tempfile.TemporaryDirectory() as work:
            Path(work, "both_settings.py").write_text(settings_text, encoding="utf-8")
            Path(work, "both_urls.py").write_text("urlpatterns = []\n", encoding="utf-8")
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DJANGO_", "BUILD_"))}
            env["PYTHONPATH"] = os.pathsep.join([work, *[p for p in sys.path if p]])
            env["BOTH_DB"] = str(Path(work, "both.sqlite3"))
            for command in (["check"], ["makemigrations", "--check", "--dry-run",
                                        "locations", "geography"]):
                with self.subTest(command=command[0]):
                    done = subprocess.run(
                        [sys.executable, "-m", "django", *command, "--settings=both_settings"],
                        cwd=work, env=env, capture_output=True, text=True, timeout=300)
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
