"""A place for the forum's link, with no dependence on the forum (stage 64,
2026-10-06): ``forum_thread`` is a slug in a text column, ``discussion()``
resolves it only where ``toto.forum`` is installed, and the page shows
nothing while it is absent.

    manage.py test toto.geography.tests_forum_link
"""

from pathlib import Path

from django.apps import apps
from django.db import models

from toto.geography.models import CommunityPin, CommunityZone
from toto.geography.testing import client_of
from toto.geography.locations_testing import LocationsCase


class ForumLinkTests(LocationsCase):
    def test_the_link_is_text_never_a_key(self):
        for model in (CommunityPin, CommunityZone):
            field = model._meta.get_field("forum_thread")
            self.assertIsInstance(field, models.CharField)
            self.assertIsNone(field.remote_field)

    def test_geography_does_not_import_the_forum(self):
        here = Path(__file__).resolve().parent
        for source in here.glob("*.py"):
            if source.name.startswith("tests"):
                continue
            text = source.read_text(encoding="utf-8")
            self.assertNotIn("from toto.forum", text, source.name)
            self.assertNotIn("import toto.forum", text, source.name)

    def test_without_the_forum_there_is_no_query_and_no_markup(self):
        if apps.is_installed("toto.forum"):
            self.skipTest("this host runs the forum")
        row = self.pin()
        CommunityPin.objects.filter(pk=row.pk).update(forum_thread="some-room")
        row.refresh_from_db()
        with self.assertNumQueries(0):
            self.assertIsNone(row.discussion())
        text = client_of(self.member_user).get(self.url("pin_detail", row)).content.decode()
        self.assertNotIn("discussion in the forum", text)
        self.assertNotIn("some-room", text)
        self.assertNotIn("some-room",
                         client_of(self.member_user).get(self.page_url).content.decode())
